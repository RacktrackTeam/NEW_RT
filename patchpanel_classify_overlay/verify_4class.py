"""Stage 2 for the four-class classifier: port-type detection and vote.

Stage 1 judges a whole panel by overall appearance, which is why it confuses
a cabled RJ-45 panel with a fiber one -- the embedding averages over the
whole panel and the few pixels that actually discriminate get washed out.
This stage looks straight at the ports: a 13-class YOLO detector finds every
individual port, the port types are voted confidence-weighted, and the
stage-1 label is confirmed, corrected or left alone.

Two rules shape the reconciliation here:

KEYSTONE IS NEVER TOUCHED. A keystone panel is defined by its *frame* --
empty snap-in cutouts -- not by a connector, so the port detector has
nothing to say about it. Stage 2 neither overturns a Keystone call nor
introduces one; whatever stage 1 decided about keystone stands.

AN `empty` SLOT IS NOT AUTOMATICALLY A HOLE. The detector's `empty` class
means "nothing is plugged in here", which at panel resolution looks exactly
like a bare keystone cutout -- so believing it as a hole calls a fixed panel
with one cable unplugged a Keystone frame. Before the keystone check counts
an empty as a cutout, the empty slots are re-read: the slot check compares
each one with labelled hole/socket crops (ppc/slots.py), and the port model
reads them again at twice the resolution (ppc/ports.py recheck_empties). If
every empty slot comes back a socket, they are not frame evidence and the
panel keeps its connector label. --no-empty-recheck switches the second
check off.

THE DETECTOR IS BLIND TO COAX. It has no BNC / TNC / F_type / RF_Coax class
and labels coaxial bulkheads as RJ45. So an RJ45 verdict over a Co-axial
stage-1 guess is not evidence, it is a known failure mode, and it is
recorded rather than applied. A *fiber* verdict over a Co-axial guess is
real evidence (LC/SC/FC are genuine detector classes) and is applied.

Writes out/verified_4class.csv.
"""

import argparse
import json
import os
import re

import numpy as np
import pandas as pd

from classify_4class import CLASSES
from ppc import connector as conn
from ppc import coaxring as cr
from ppc import keystone as ks
from ppc import ports
from ppc import slots
from ppc.coaxdet import CoaxDetector
from ppc.ports import DEVICE_PORTS, TRANSCEIVER, PortVerifier, panel_style

HERE = os.path.dirname(os.path.abspath(__file__))

# The training-run filename has spaces and brackets in it, so a packaged
# copy ships the same weights as port_model.pt. Both are accepted, the
# original first, so this file is unchanged between the two.
WEIGHTS = next(
    (p for p in (os.path.join(HERE, "best_merged_ports 2 (1).pt"),
                 os.path.join(HERE, "port_model.pt"))
     if os.path.exists(p)),
    os.path.join(HERE, "port_model.pt"))

# detector class -> four-class label. 'empty' is a slot state, not a
# connector, and SFP/QSFP cages are neither of the four, so both are absent
# here on purpose: they make the ports undecidable rather than wrong.
PORT_TO_CLASS = {
    "RJ45": "RJ-45",
    "LC": "Fiber_Patchpanel",
    "SC": "Fiber_Patchpanel",
    "FC": "Fiber_Patchpanel",
}
KEYSTONE = "Keystone"
COAX = "Co-axial"

# --- first rule in reconcile_shape_first ---
COAX_RING_MIN_S1 = 0.15   # stage-1 Co-axial score; true coax median 0.66,
                          # other classes 0.01-0.08 (measured, 309 set)


def coax1_score(scores):
    """Stage 1's Co-axial score, parsed from the predictions `scores` cell
    ("RJ-45=0.12; Co-axial=0.66; ..."), or 0.0 if absent."""
    m = re.search(r"Co-axial=([\d.]+)", str(scores))
    return float(m.group(1)) if m else 0.0


def port_class(det):
    """Four-class label implied by the detected ports alone, or None.

    Votes by summed detection confidence rather than raw count: a dozen
    shaky boxes should not outvote a handful of certain ones. LC, SC and FC
    all mean the same thing at this granularity, so their weights are
    pooled instead of competing with each other.
    """
    weights = det["weights"]
    pooled, counts = {}, {}
    for pc, w in weights.items():
        cls = PORT_TO_CLASS.get(pc)
        if cls is None:
            continue
        pooled[cls] = pooled.get(cls, 0.0) + w
        counts[cls] = counts.get(cls, 0) + det["counts"].get(pc, 0)
    if not pooled:
        n_cage = sum(det["counts"].get(c, 0) for c in TRANSCEIVER)
        if n_cage:
            return None, 0, 0.0, "%d transceiver cages, not a patch panel " \
                "connector" % n_cage
        if det["n_empty"] >= 3:
            return None, 0, 0.0, "%d slots, all empty" % det["n_empty"]
        return None, 0, 0.0, "no patch-panel ports detected"

    order = sorted(pooled.items(), key=lambda kv: -kv[1])
    top, w = order[0]
    kinds = sorted((pc for pc in weights if pc in PORT_TO_CLASS
                    and PORT_TO_CLASS[pc] == top),
                   key=lambda pc: -weights[pc])
    why = "%d x %s" % (counts[top], "/".join(kinds))
    if len(order) > 1:
        why += " vs %d x %s" % (counts[order[1][0]],
                                order[1][0].replace("_", " "))
    return top, counts[top], w, why


def frame_evidence(det, min_empty=1, empty_recheck=None):
    """Is this panel a modular frame? Decided from the FRAME, not connectors.

    Under the frame-wins taxonomy, Keystone is a statement about panel
    construction -- individual snap-in cutouts -- and a connector vote
    cannot see it: a keystone frame holding 24 RJ-45 modules and a fixed
    RJ-45 panel both vote "RJ45". The detector does see the frame, though,
    through two signals:

      empty cutouts   an unpopulated snap-in position. Measured over the 230
                      crops, fixed RJ-45 panels have a median of 0 detected
                      empty slots (75th percentile 0); keystone frames have
                      a median of 8. The separation is clean enough that a
                      single empty cutout is meaningful.
      keystone_signals  uniform cutout geometry, empties beside populated
                      slots, and mixed connector types in one frame
                      (ppc/ports.py). Precise (84%) but only ~51% recall on
                      its own, so it is a second chance rather than the
                      primary test.

    The empty count is only believed after the port model has re-read those
    slots at twice the resolution (ppc/ports.py recheck_empties): an
    unoccupied RJ-45 socket is an unpopulated POSITION but not an open
    cutout, and counting it as one is what called fixed panels with a cable
    out Keystone.
    """
    vetoed, note = ks.empty_recheck_vetoes(empty_recheck)
    if det["n_empty"] >= min_empty and not vetoed:
        return True, "%d empty snap-in cutouts" % det["n_empty"]
    if det["keystone"]:
        return True, "frame geometry: %s" % det["keystone_why"]
    if vetoed:
        return False, ("%d unoccupied position%s, all re-read as sockets "
                       "rather than cutouts%s"
                       % (det["n_empty"], "" if det["n_empty"] == 1 else "s",
                          note))
    return False, "no empty cutouts, no keystone frame signals"


def reconcile_shape_first(emb_class, det, shape, ks_verdict, ks_why,
                          ks_tier=0, coax_n=0, ring=None, coax1=0.0, **kw):
    """Check 1 connector shape, then check 2 keystone-vs-fixed for RJ-45.

    The order is the one that makes physical sense: what KIND of jack is
    this, and only then, is the frame separate from the jacks? Asking
    "keystone?" of a coax bulkhead or a fiber cassette is a category error.

      0  the dedicated coax detector (ppc/coaxdet.py, out/coax_detector.pt)
         found more round metal bulkheads than RJ45/fiber ports          ->
         coax, and stop. This runs before everything else, including the
         stage-1 guess: it is real, trained evidence for the one connector
         family the 13-class port detector was never shown.
      1  the ring check (ppc/coaxring.py) found a row of same-size round
         metal rings, outnumbering every other typed/empty slot, AND
         stage 1's own Co-axial score clears COAX_RING_MIN_S1          ->
         coax, and stop. The ring count alone fires on RJ-45 jack grids and
         blank keystone frames just as often as on real coax (measured,
         40 false positives on the 309 set); the stage-1 score is what
         separates them (true-coax median 0.66 vs 0.01-0.08 elsewhere).
      2  stage 1 (the embedding) already called Co-axial or
         Fiber_Patchpanel -- trusted outright, and stop. Measured on the
         98- and 309-image coax/fiber sets: the shape/keystone checks below
         overturned more correct Co-axial/Fiber_Patchpanel calls than they
         fixed, because the port detector has no coax class and the shape
         check's thresholds (ppc/connector.py) never fire on real coax
         openings.
      3  round metal opening, single centre pin, no contact row  -> coax,
         and stop. The port detector cannot do this -- it has no coaxial
         class and reads a coax bulkhead as RJ45 -- so it is measured from
         the opening itself (ppc/connector.py).
      4  small square or duplex openings (LC/SC/FC)              -> fiber
      5  rectangular opening with a row of gold contacts         -> RJ-45,
         and only now does the keystone test run: a hole, a blank or a
         mismatched jack means keystone; a seamless strip of real sockets
         leans fixed; fully cabled and uniform is ambiguous and goes to the
         port vote.

    CROSS-CASE, deliberately resolved in favour of the connector: a keystone
    frame can hold coax inserts, so "keystone" can describe the frame even
    when the jack is round and metal. The stated decision order stops at
    coax, so that is what happens here, and the case is flagged in the
    reason string when both the shape check and the frame check fire.
    """
    n_rj45 = det["counts"].get("RJ45", 0)
    n_fiber = sum(det["counts"].get(c, 0) for c in ("LC", "SC", "FC"))
    if coax_n >= 3 and coax_n > (n_rj45 + n_fiber):
        tally = ", ".join("%d x %s" % (n, c) for c, n in
                          sorted(det["counts"].items(), key=lambda kv: -kv[1])
                          ) or "nothing"
        return COAX, "coax_detected", \
            "%d round metal bulkheads found by the coax detector; ports " \
            "said %s" % (coax_n, tally)

    if ring is not None and ring["is_coax"] and coax1 >= COAX_RING_MIN_S1:
        n_other = (sum(det["counts"].get(c, 0) for c in PORT_TO_CLASS)
                   + det["n_empty"])
        if ring["n_metal"] > n_other:
            return COAX, "coax_rings", (
                "%s -- a run of same-size round metal rings, stage 1 "
                "Co-axial %.0f%%; ports said %s"
                % (ring["why"], coax1 * 100, port_class(det)[3]))

    if emb_class in (COAX, "Fiber_Patchpanel"):
        return emb_class, "stage1_trusted", \
            "stage 1 called %s -- trusted outright, stage 2 skipped" \
            % emb_class

    # Mixed types of ports outrank the fiber call: a frame carrying two
    # kinds of connector is a modular frame, so LC adapters beside RJ-45
    # jacks is Keystone, not Fiber. This is the one place where the shape
    # check does not get the first word.
    #
    # It applies to the MIXED-FRAME verdict only (tier 2), not to every
    # positive keystone verdict. Letting tier 1 -- an empty cutout
    # anywhere -- jump the queue here sent every fiber panel to Keystone,
    # because a fiber panel has unused adapter positions like any other:
    # measured, fiber recall fell to 0% on the 211 set and 25% on the 245
    # set. A single-type fiber panel with empty slots is a fiber panel.
    if ks_verdict is True and ks_tier == 2:
        return KEYSTONE, "keystone_mixed", ks_why

    kind = shape["kind"]
    if kind == "coax":
        note = ""
        if ks_verdict is True:
            note = ("; note: the frame also looks modular, so this may be "
                    "coax inserts in a keystone frame")
        return COAX, "shape_coax", shape["why"] + note
    if kind == "fiber":
        return "Fiber_Patchpanel", "shape_fiber", shape["why"]

    # --- rectangular, so RJ-45; now keystone vs fixed ---
    if ks_verdict is True:
        return KEYSTONE, "keystone_confirmed", ks_why
    if kind == "unknown":
        # Shape undecided and no keystone evidence: fall back to the port
        # vote, which at least names the connector family.
        cls, _, why = reconcile4(emb_class, det,
                                 keystone_policy="correctable", **kw)
        if cls == KEYSTONE:
            pclass, _, _, pwhy = port_class(det)
            return (pclass or emb_class), "shape_unknown", \
                "%s; ports: %s" % (shape["why"], pwhy)
        return cls, "shape_unknown", "%s; ports say %s" % (shape["why"], why)

    verdict = "rejected keystone" if ks_verdict is False \
        else "could not confirm keystone from the front view"
    cls, _, why = reconcile4(emb_class, det,
                             keystone_policy="correctable", **kw)
    if cls == COAX and kind == "rj45" and (
            shape["gold"] > conn.GOLD_MAX or shape["circ"] < 0.75):
        return "RJ-45", "shape_overrides_coax", \
            "contact row / rectangular opening seen (gold %.3f, circ %.2f); " \
            "stage-1 coax guess rejected" % (shape["gold"], shape["circ"])
    if cls == KEYSTONE:
        pclass, _, _, pwhy = port_class(det)
        return (pclass or "RJ-45"), "keystone_unresolved", \
            "rectangular RJ-45 openings; frame check %s (%s); ports: %s" \
            % (verdict, ks_why, pwhy)
    return cls, ("keystone_rejected" if ks_verdict is False
                 else "keystone_unconfirmed"), \
        "rectangular RJ-45 openings; frame check %s (%s); ports say %s" \
        % (verdict, ks_why, why)


def reconcile_keystone_first(emb_class, det, ks_verdict, ks_why,
                             min_fiber=3, **kw):
    """Stage 1 says Keystone -> verify it visually -> else fall back to ports.

    Keystone is a claim about panel CONSTRUCTION, so it is checked with a
    construction test: modular rectangular openings on a regular pitch,
    consistent spacing between modules, empty snap-in slots, connector mix
    (ppc/keystone.py). The port detector is consulted only when that check
    fails to confirm -- a connector vote cannot tell a keystone frame full
    of RJ-45 modules from a fixed RJ-45 panel, because both vote "RJ45".

    ks_verdict is True / False / None (too few slots to judge).
    """
    # Fiber adapters outrank the frame check wherever they appear. LC/SC/FC
    # are unambiguous and Fiber_Patchpanel is its own class, so a fiber
    # panel must not be swallowed by "this is a modular frame" -- most
    # fiber panels are modular frames.
    pclass, pcount, _, pwhy = port_class(det)
    if pclass == "Fiber_Patchpanel" and pcount >= min_fiber:
        return pclass, "fiber_ports", pwhy

    if emb_class == KEYSTONE:
        if ks_verdict is True:
            return KEYSTONE, "keystone_confirmed", ks_why
        # Anything short of a positive keystone verdict -- an explicit
        # rejection OR an undecided front view -- hands over to the port
        # evidence. Keystone is kept only when the keystone features are
        # actually satisfied, never on the embedding's say-so alone.
        verdict = "rejected keystone" if ks_verdict is False \
            else "could not confirm keystone from the front view"
        cls, status, why = reconcile4(emb_class, det,
                                      keystone_policy="correctable", **kw)
        if cls == KEYSTONE:
            # The ports could not name a type either. The frame check did
            # not confirm, so keystone is not re-asserted on no evidence.
            pclass, _, _, pwhy = port_class(det)
            return (pclass or "RJ-45"), "keystone_unresolved", \
                "frame check %s (%s); ports: %s" % (verdict, ks_why, pwhy)
        return cls, ("keystone_rejected" if ks_verdict is False
                     else "keystone_unconfirmed"), \
            "frame check %s (%s); ports say %s" % (verdict, ks_why, why)

    # Stage 1 said something else. The visual check may still raise a
    # keystone frame the embedding missed.
    if ks_verdict is True:
        return KEYSTONE, "keystone_detected", ks_why
    return reconcile4(emb_class, det, keystone_policy="ports", **kw)


def reconcile4(emb_class, det, min_ports=3, min_weight=1.5, min_share=0.25,
               keystone_policy="freeze", min_fiber=3):
    """Combine the stage-1 class with the port evidence -> (class, status, why).

    The detector wins when it has solid evidence, because it is looking at
    the actual ports. The embedding is kept when the detector is silent, out
    of its depth, or backed by too thin a minority to overturn a whole panel.

    keystone_policy selects how Keystone is decided:
      frame        (default) Keystone comes from FRAME evidence -- empty
                   snap-in cutouts and keystone slot geometry -- which is
                   what the frame-wins taxonomy actually labels. Fiber
                   adapters still win over the frame signal, because a
                   fiber panel is its own class and LC/SC/FC are
                   unambiguous. This is measurably the best of the three.
      freeze       stage 1's Keystone call is final, in either direction;
                   stage 2 only speaks about panels stage 1 called
                   something else.
      correctable  stage 2 may correct a stage-1 Keystone away when it
                   finds populated ports, but can never vote one in.
    freeze and correctable are kept because they are the two readings of
    "leave keystone alone", and the report measures all three side by side.
    """
    pclass, pcount, pweight, why = port_class(det)

    if keystone_policy == "frame":
        # Fiber first: LC/SC/FC are unambiguous and Fiber_Patchpanel is its
        # own class, so a fiber panel is not reclassified as a frame merely
        # because it has unused adapter positions.
        if pclass == "Fiber_Patchpanel" and pcount >= min_fiber:
            return pclass, "fiber_ports", why
        is_frame, fwhy = frame_evidence(det)
        if is_frame:
            return KEYSTONE, "frame_detected", fwhy
        if pclass is None:
            return emb_class, "unverified", why
        # Fall through to the connector vote for a fixed panel.

    elif emb_class == KEYSTONE and keystone_policy == "freeze":
        return KEYSTONE, "keystone_untouched", \
            "stage 1 says keystone; port detector not applied"

    elif emb_class == KEYSTONE:
        # correctable: only populated, confidently typed ports move it.
        if pclass is None:
            return KEYSTONE, "keystone_untouched", \
                "%s; keystone frame stands" % why
        share = pcount / max(1, det["n_det"])
        if (pcount < min_ports or pweight < min_weight
                or share < min_share):
            return KEYSTONE, "keystone_untouched", \
                "%s -- too thin to overturn a keystone call" % why
        return pclass, "corrected", "%s -- populated, not a blank frame" % why

    if pclass is None:
        return emb_class, "unverified", why

    # Share matters as well as count. A real failure mode from the 11-class
    # pipeline: an RJ-45 panel whose occupied ports mostly read as 'empty'
    # also threw 3 stray LC boxes; three detections cleared the absolute
    # gates and a 13% minority re-labelled the whole panel as fiber.
    # Overturning stage 1 needs a real share behind it; agreeing with it
    # does not.
    share = pcount / max(1, det["n_det"])
    thin = share < min_share and pclass != emb_class
    if pcount < min_ports or pweight < min_weight or thin:
        if thin:
            why = "%s -- only %d of %d detections (%.0f%%)" % (
                why, pcount, det["n_det"], share * 100)
        if pclass == emb_class:
            return pclass, "confirmed_weak", "%s (agrees with stage 1)" % why
        return emb_class, "weak_evidence", "only %s" % why

    # The detector has no coax class and reads coax bulkheads as RJ45, so
    # this particular disagreement is a known blind spot, not evidence.
    if emb_class == COAX and pclass == "RJ-45":
        return COAX, "coax_blindspot", \
            "%s, but the detector has no coaxial class and calls coax " \
            "bulkheads RJ45 -- stage 1 kept" % why

    if pclass == emb_class:
        return pclass, "confirmed", why
    return pclass, "corrected", why


def prf(true, pred, labels):
    rows = []
    for c in labels:
        tp = sum((t == c) and (p == c) for t, p in zip(true, pred))
        fp = sum((t != c) and (p == c) for t, p in zip(true, pred))
        fn = sum((t == c) and (p != c) for t, p in zip(true, pred))
        prec = tp / (tp + fp) if tp + fp else float("nan")
        rec = tp / (tp + fn) if tp + fn else float("nan")
        f1 = (2 * prec * rec / (prec + rec)
              if prec and rec and not np.isnan(prec) and not np.isnan(rec)
              else 0.0)
        rows.append({"class": c, "support": tp + fn, "pred": tp + fp,
                     "precision": prec, "recall": rec, "f1": f1})
    return pd.DataFrame(rows)


def summarize(name, df, col):
    sub = df[df.scored]
    if not len(sub):
        print("\n%s\n  no ground truth for this set -- nothing to score"
              % name)
        return float("nan"), float("nan"), pd.DataFrame(
            columns=["class", "support", "pred", "precision", "recall", "f1"])
    true, pred = list(sub.true_class), list(sub[col])
    acc = float(np.mean([t == p for t, p in zip(true, pred)]))
    labels = sorted(set(true))
    tab = prf(true, pred, labels)
    macro = float(tab.f1.mean())
    print("\n%s" % name)
    print("  accuracy  %5.1f%%   (%d/%d)"
          % (acc * 100, round(acc * len(sub)), len(sub)))
    print("  macro F1  %5.1f%%   over %d classes present"
          % (macro * 100, len(labels)))
    return acc, macro, tab


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", default=os.path.join(
        HERE, "out", "predictions_4class.csv"))
    ap.add_argument("--weights", default=WEIGHTS)
    ap.add_argument("--imgsz", type=int, default=960)
    # Images the detector holds open at once. Purely a memory knob -- each
    # image is predicted independently, so it cannot change a detection.
    # Lower it when the slot check's DINOv2 and YOLO have to coexist.
    ap.add_argument("--chunk", type=int, default=8)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--keystone-policy", default="frame",
                    choices=["frame", "freeze", "correctable"],
                    help="frame: decide Keystone from frame geometry (empty "
                         "cutouts / keystone signals). freeze: stage 2 never "
                         "changes a stage-1 Keystone call. correctable: it "
                         "may correct Keystone away when it finds populated "
                         "ports, but never votes it in.")
    ap.add_argument("--order", default="shape-first",
                    choices=["shape-first", "keystone-first"],
                    help="shape-first: connector shape (coax / fiber / "
                         "RJ-45) decides first, then keystone-vs-fixed for "
                         "RJ-45 only. keystone-first: the previous order.")
    ap.add_argument("--keystone-verifier", default="tiered",
                    choices=["tiered", "model", "rule", "off"],
                    help="model: fitted feature model (ppc/keystone.py + "
                         "out/keystone_model.pkl). rule: hand-set weights, "
                         "no fitting. off: skip the visual check and use "
                         "--keystone-policy alone.")
    # Out-of-fold verdicts, so the end-to-end score is not inflated by the
    # keystone model having been fitted on the crops it is judging.
    ap.add_argument("--keystone-oof", default=os.path.join(
        HERE, "out", "keystone_oof.csv"))
    ap.add_argument("--no-oof", action="store_true",
                    help="use in-sample keystone verdicts (deployment mode; "
                         "the reported accuracy is then optimistic)")
    ap.add_argument("--no-empty-recheck", dest="empty_recheck",
                    action="store_false",
                    help="do not re-read the empty slots through the port "
                         "model before tier 1 believes they are open "
                         "cutouts. One extra detector pass per panel that "
                         "has an empty; without it an unoccupied socket "
                         "counts as a cutout.")
    ap.add_argument("--empty-imgsz", type=int, default=ports.EMPTY_IMGSZ,
                    help="resolution of that re-read pass (default %d, about "
                         "twice --imgsz)" % ports.EMPTY_IMGSZ)
    ap.add_argument("--out", default=os.path.join(
        HERE, "out", "verified_4class.csv"))
    ap.add_argument("--coax-weights", default=os.path.join(
        HERE, "out", "coax_detector.pt"),
                    help="single-class coax detector from "
                         "train_coax_detector.py; skipped entirely if this "
                         "file does not exist")
    ap.add_argument("--coax-imgsz", type=int, default=960)
    ap.add_argument("--coax-conf", type=float, default=0.30)
    ap.add_argument("--no-ring", action="store_true",
                    help="skip the ring check (ppc/coaxring.py) entirely -- "
                         "for A/B comparison against a run with it on")
    args = ap.parse_args()

    df = pd.read_csv(args.pred)
    df["true_class"] = df.true_class.fillna("")
    df["true_leaf"] = df.true_leaf.fillna("")
    df["scored"] = df.true_class.astype(str).str.strip().astype(bool)

    ks_model, ks_oof = None, {}
    # Loaded whenever it exists, not only when it is the active verifier,
    # so the stage-2 design comparison at the end has a real fitted-model
    # row to show rather than a copy of the hand-set one.
    mp = os.path.join(HERE, "out", "keystone_model.pkl")
    if os.path.exists(mp):
        import pickle
        with open(mp, "rb") as fh:
            ks_model = pickle.load(fh)
        print("fitted keystone model loaded (%d features)%s"
              % (len(ks_model["feats"]),
                 "" if args.keystone_verifier == "model"
                 else " -- for comparison only"))
    elif args.keystone_verifier == "model":
        print("keystone verifier: no model at %s -- run "
              "evaluate_keystone.py first; falling back to the hand-set "
              "rule" % mp)
    print("keystone verifier: %s" % args.keystone_verifier)
    if args.keystone_verifier != "off" and not args.no_oof \
            and os.path.exists(args.keystone_oof):
        o = pd.read_csv(args.keystone_oof)
        ks_oof = dict(zip(o.file, o.ks_oof.astype(bool)))
        print("keystone verdicts: out-of-fold for %d crops (honest scoring)"
              % len(ks_oof))

    v = PortVerifier(args.weights, imgsz=args.imgsz, conf=args.conf)

    # The slot check exists only to second-guess tier 1: it asks whether an
    # `empty` box is a bare hole or just an unplugged socket. With tier 1
    # switched off it can never change an answer, so it is not loaded --
    # which also keeps a second DINOv2 out of memory beside the YOLO
    # detector, a pairing that was exhausting CPU RAM on 32 GB.
    slot_gal = slot_emb = None
    sp = slots.default_gallery_path(HERE)
    if not ks.EMPTY_DECIDES:
        print("slot check: off -- tier 1 is disabled, so it cannot apply")
    elif os.path.exists(sp):
        slot_gal, slot_emb = slots.SlotGallery(sp), slots.SlotEmbedder()
        print("slot check: on (%d hole / %d socket crops)"
              % (slot_gal.n_hole, slot_gal.n_socket))
    else:
        print("slot check: off -- run build_slot_gallery.py to enable")
    if not ks.EMPTY_DECIDES:
        print("empty-slot re-read: off -- tier 1 is disabled, so it cannot "
              "apply")
    elif args.empty_recheck:
        print("empty-slot re-read: on (port model at %d px, believed when "
              "%d+ slots agree)" % (args.empty_imgsz,
                                    ks.EMPTY_RECHECK_MIN_SLOTS))
    else:
        print("empty-slot re-read: off -- an unoccupied socket will count as "
              "an open cutout")
    print("detector: %d classes (%s), imgsz=%d, conf=%.2f"
          % (len(v.names), ", ".join(sorted(v.names.values())),
             args.imgsz, args.conf))

    coax_det = None
    if os.path.exists(args.coax_weights):
        coax_det = CoaxDetector(args.coax_weights, imgsz=args.coax_imgsz,
                                conf=args.coax_conf)
        print("coax detector: on (%s, imgsz=%d, conf=%.2f)"
              % (os.path.basename(args.coax_weights), args.coax_imgsz,
                 args.coax_conf))
    else:
        print("coax detector: off -- %s not found; run "
              "train_coax_detector.py first" % args.coax_weights)

    print("verifying %d crops, %d of them scored\n"
          % (len(df), int(df.scored.sum())))

    import itertools
    paths = df.src_path.tolist()
    coax_iter = (coax_det.detect_many(paths, chunk=args.chunk) if coax_det
                else itertools.repeat(CoaxDetector._empty()))

    recs = []
    for i, (r, d, cx) in enumerate(
            zip(df.itertuples(),
                v.detect_many(paths, chunk=args.chunk), coax_iter), 1):
        # --- what is actually in the slots the panel pass called empty? ---
        # Asked before the keystone check, because tier 1 reads an `empty`
        # as an open cutout and that is only true if the slot really is a
        # hole. The port model re-reads each one at twice the resolution;
        # when they all come back as sockets, the empties are not keystone
        # evidence and the panel keeps its connector label.
        erc = None
        if (args.empty_recheck and ks.EMPTY_DECIDES
                and d["n_empty"] >= ks.MIN_EMPTY
                and d["n_det"] >= ks.MIN_SLOTS):
            erc = v.recheck_empties(r.src_path, d["boxes"], d["classes"],
                                    d["img_w"], d["img_h"],
                                    imgsz=args.empty_imgsz)

        # --- the visual keystone check, before any port reasoning ---
        kf = ks.features(r.src_path, d["boxes"], d["classes"],
                         d["img_w"], d["img_h"])
        kv, kscore, kwhy = ks.verify(kf)
        ktier, kconf = 0, "none"
        if args.keystone_verifier == "tiered":
            kv, ktier, kconf, kwhy = ks.verify_tiered(kf, empty_recheck=erc)
            kscore = float(ks.score(kf))
        elif ks_model is not None and kv is not None:
            x = np.array([[float(kf.get(c, 0.0))
                           for c in ks_model["feats"]]])
            kscore = float(ks_model["model"].predict_proba(x)[0, 1])
            kv = bool(ks_model["model"].predict(x)[0])
            kwhy = "p(keystone)=%.2f -- %s" % (kscore, kwhy)

        slot = {"verdict": None, "n_hole": 0, "n_socket": 0, "why": ""}
        if (slot_gal is not None and ktier == 1
                and 1 <= d["n_empty"] <= slots.MAX_EMPTY):
            slot = slots.check_panel(r.src_path, d["boxes"], d["classes"],
                                     d["img_w"], d["img_h"], slot_gal,
                                     slot_emb)
            if slot["verdict"] == slots.SOCKET:
                # Not a hole: the empties are sockets with no cable. That is
                # no keystone evidence, so the panel goes to the ports model
                # exactly like a fully loaded one (tier 6).
                kv, ktier, kconf = None, 6, "none"
                kwhy = slot["why"]
            elif slot["why"]:
                kwhy = "%s; %s" % (kwhy, slot["why"])

        # Alternatives recorded per row so the designs can be compared from
        # one detection pass rather than four.
        kv_rule = ks.verify(kf)[0]
        kv_model = kv_rule
        if ks_model is not None:
            _x = np.array([[float(kf.get(c, 0.0))
                            for c in ks_model["feats"]]])
            kv_model = bool(ks_model["model"].predict(_x)[0])
            if r.file in ks_oof:
                kv_model = bool(ks_oof[r.file])   # out-of-fold, honest
        kv_insample = kv_model
        # The tiered rule has nothing fitted in it, so out-of-fold
        # substitution does not apply -- only the logistic model needs it.
        if (args.keystone_verifier != "tiered" and r.file in ks_oof
                and kv is not None):
            kv = bool(ks_oof[r.file])            # out-of-fold verdict

        # --- check 1: what shape is each opening? ---
        shape = conn.shape_vote(r.src_path, d["boxes"], d["classes"],
                                d["img_w"], d["img_h"])
        coax_n = cx["n_coax"]
        ring = (None if args.no_ring else
                cr.ring_evidence(r.src_path, d["img_w"], d["img_h"]))
        coax1 = coax1_score(getattr(r, "scores", ""))

        if args.keystone_verifier == "off":
            final, status, why = reconcile4(
                r.pred_class, d, keystone_policy=args.keystone_policy)
        elif args.order == "shape-first":
            final, status, why = reconcile_shape_first(
                r.pred_class, d, shape, kv, kwhy, ks_tier=ktier,
                coax_n=coax_n, ring=ring, coax1=coax1)
        else:
            final, status, why = reconcile_keystone_first(
                r.pred_class, d, kv, kwhy)
        # Both policies are recorded on every row so the two readings of
        # "leave keystone alone" can be compared without a second YOLO pass.
        alt = {p: reconcile4(r.pred_class, d, keystone_policy=p)[0]
               for p in ("frame", "freeze", "correctable")}
        pcls, pcount, pweight, pwhy = port_class(d)
        is_frame, fwhy = frame_evidence(d, empty_recheck=erc)
        er = erc or PortVerifier._no_recheck("re-read not run")
        recs.append({
            "empty_recheck": er["verdict"] or "",
            "empty_recheck_slots": er["n_slots"],
            "empty_recheck_read": er["n_read"],
            "empty_recheck_empty": er["n_empty"],
            "empty_recheck_reads": "|".join(str(x) for x in er["reads"]),
            "empty_recheck_why": er["why"],
            # Same detections, four stage-2 designs, so they can be compared
            # without four YOLO passes.
            "ksf_oof": reconcile_keystone_first(r.pred_class, d, kv,
                                                kwhy)[0],
            "ksf_insample": reconcile_keystone_first(r.pred_class, d,
                                                     kv_insample, kwhy)[0],
            "ksf_rule": reconcile_keystone_first(r.pred_class, d, kv_rule,
                                                 kwhy)[0],
            "slot_verdict": slot["verdict"] or "",
            "slot_holes": slot["n_hole"], "slot_sockets": slot["n_socket"],
            "slot_why": slot["why"],
            "shape_kind": shape["kind"], "shape_why": shape["why"],
            "shape_circ": round(shape["circ"], 3),
            "shape_metallic": round(shape["metallic"], 3),
            "shape_gold": round(shape["gold"], 4),
            "shape_nfiber": shape["n_fiber"],
            "alt_keystone_first": reconcile_keystone_first(
                r.pred_class, d, kv, kwhy)[0],
            "ks_verdict": "" if kv is None else ("keystone" if kv
                                                 else "not-keystone"),
            "ks_tier": ktier, "ks_conf": kconf,
            "ks_score": round(kscore, 4), "ks_why": kwhy,
            "ks_empty_frac": round(kf["empty_frac"], 3),
            "ks_modularity": round(kf["modularity"], 3),
            "ks_spacing_even": round(kf["spacing_even"], 3),
            "ks_boundary": round(kf["boundary_contrast"], 3),
            "ks_rows": int(kf["n_rows"]),
            "alt_frame": alt["frame"],
            "alt_freeze": alt["freeze"],
            "alt_correctable": alt["correctable"],
            "frame_evidence": is_frame, "frame_why": fwhy,
            "port_class": pcls or "",
            "port_class_count": pcount,
            "port_class_weight": round(pweight, 3),
            "port_dominant": d["dominant"] or "",
            "port_total": d["n_det"], "port_typed": d["n_typed"],
            "port_empty": d["n_empty"],
            "port_device": d["n_device"],
            "port_meanconf": round(d["mean_conf"], 3),
            "port_green": round(d["green_frac"], 3),
            "port_mixed": d["mixed"],
            "panel_style": panel_style(d),
            "port_counts": "; ".join("%s=%d" % kv for kv in
                                     sorted(d["counts"].items(),
                                            key=lambda kv: -kv[1])),
            "port_boxes": json.dumps(d["boxes"], separators=(",", ":")),
            "port_classes": json.dumps(d["classes"], separators=(",", ":")),
            "img_w": d["img_w"], "img_h": d["img_h"],
            "coax_n": coax_n, "coax_meanconf": round(cx["mean_conf"], 3),
            "ring_n": ring["n_rings"] if ring else 0,
            "ring_metal": ring["n_metal"] if ring else 0,
            "ring_row": ring["row"] if ring else 0,
            "ring_why": ring["why"] if ring else "ring check off (--no-ring)",
            "final_class": final, "verify_status": status,
            "verify_why": why,
        })
        if i % 25 == 0 or i == len(df):
            print("  detected %d/%d" % (i, len(df)), end="\r", flush=True)
    print()

    df = pd.concat([df, pd.DataFrame(recs, index=df.index)], axis=1)
    df["stage2_correct"] = [None if not t else (p == t)
                            for p, t in zip(df.final_class, df.true_class)]
    df.to_csv(args.out, index=False)

    print("\n" + "=" * 70)
    print("STAGE COMPARISON")
    print("=" * 70)
    a1, m1, _ = summarize("Stage 1 only  (embedding)", df, "pred_class")
    a2, m2, t2 = summarize("Stage 1 + 2   (embedding + port detection)",
                           df, "final_class")
    print("\n  delta: accuracy %+.1f pp   macro F1 %+.1f pp"
          % ((a2 - a1) * 100, (m2 - m1) * 100))

    if not len(df[df.scored]):
        # No labels: everything below is a score against ground truth, so
        # print what the pipeline SAID and stop rather than emit zeros.
        print("\nNo ground truth for this set -- no per-class metrics, no"
              "\nstage comparison, no design comparison. What follows is what"
              "\nthe pipeline said, not how often it was right.")
        print("\nFINAL CLASS DISTRIBUTION")
        for k, n in df.final_class.value_counts().items():
            print("  %-18s %4d  (%4.1f%%)" % (k, n, n / len(df) * 100))
        print("\nVERIFICATION ACTIONS")
        for k, n in df.verify_status.value_counts().items():
            print("  %-22s %4d" % (k, n))
        print("\nCHECK 1 -- CONNECTOR SHAPE")
        for k, n in df.shape_kind.value_counts().items():
            print("  %-9s %4d" % (k, n))
        print("\nCHECK 2 -- KEYSTONE FRAME CHECK, tier that fired")
        for k, n in df.ks_tier.value_counts().sort_index().items():
            print("  tier %-3d %4d" % (k, n))
        print("\nEMPTY SLOTS RE-READ BY THE PORT MODEL")
        for k, n in df.empty_recheck.fillna("").value_counts().items():
            print("  %-12s %4d" % (k or "not run / unsure", n))
        print("\nPORT TYPES DETECTED")
        tally = {}
        for s_ in df.port_counts:
            for chunk in str(s_).split(";"):
                k, _, n = chunk.strip().partition("=")
                if k:
                    tally[k] = tally.get(k, 0) + int(n)
        for k, n in sorted(tally.items(), key=lambda kv: -kv[1]):
            print("  %-18s %5d" % (k, n))
        print("\nwrote %s" % args.out)
        return

    print("\nPER-CLASS after port verification")
    print("  %-18s %7s %6s %10s %8s %7s"
          % ("class", "support", "pred", "precision", "recall", "F1"))
    for r in t2.itertuples():
        def f(x):
            return "  --  " if np.isnan(x) else "%5.1f%%" % (x * 100)
        print("  %-18s %7d %6d %10s %8s %6.1f%%"
              % (r._1, r.support, r.pred, f(r.precision), f(r.recall),
                 r.f1 * 100))

    print("\nPORT TYPES DETECTED  (every box, all 230 crops)")
    tally = {}
    for s in df.port_counts:
        for chunk in str(s).split(";"):
            k, _, n = chunk.strip().partition("=")
            if k:
                tally[k] = tally.get(k, 0) + int(n)
    for k, n in sorted(tally.items(), key=lambda kv: -kv[1]):
        kind = ("connector" if k in PORT_TO_CLASS else
                "cage" if k in TRANSCEIVER else
                "device port" if k in DEVICE_PORTS else "slot state")
        print("  %-18s %5d   (%s)" % (k, n, kind))

    print("\nVERIFICATION ACTIONS")
    for k, n in df.verify_status.value_counts().items():
        s = df[(df.verify_status == k) & df.scored]
        acc = ("%5.1f%%" % ((s.final_class == s.true_class).mean() * 100)
               if len(s) else "   n/a")
        print("  %-20s %4d   final-correct %s" % (k, n, acc))

    # What the re-read bought, and what it cost. `connector` is the only
    # verdict that changes an answer: it takes the empties away from tier 1,
    # so the panel keeps its connector label instead of becoming Keystone.
    print("\nEMPTY SLOTS RE-READ BY THE PORT MODEL  (guard on tier 1, needs "
          "%d+ agreeing slots)" % ks.EMPTY_RECHECK_MIN_SLOTS)
    s = df[df.scored]
    for k, n in df.empty_recheck.fillna("").value_counts().items():
        sub = s[s.empty_recheck.fillna("") == k]
        print("  %-12s %4d   of those, truly keystone %s"
              % (k or "not run / unsure", n,
                 "%5.1f%%" % ((sub.true_class == KEYSTONE).mean() * 100)
                 if len(sub) else "  n/a"))
    vetoed = s[(s.empty_recheck.fillna("") == "connector")
               & (s.empty_recheck_slots >= ks.EMPTY_RECHECK_MIN_SLOTS)]
    print("  tier 1 overruled on %d panel%s, %d of them not keystone"
          % (len(vetoed), "" if len(vetoed) == 1 else "s",
             int((vetoed.true_class != KEYSTONE).sum())))

    print("\nCHECK 1 -- CONNECTOR SHAPE  (runs before any keystone logic)")
    s = df[df.scored]
    for k, n in df.shape_kind.value_counts().items():
        sub = s[s.shape_kind == k]
        if k == "coax":
            agree = (sub.true_class == "Co-axial").mean() * 100 \
                if len(sub) else 0.0
        elif k == "fiber":
            agree = (sub.true_class == "Fiber_Patchpanel").mean() * 100 \
                if len(sub) else 0.0
        elif k == "rj45":
            agree = (sub.true_class.isin(["RJ-45", "Keystone"])).mean() * 100 \
                if len(sub) else 0.0
        else:
            agree = float("nan")
        print("  %-9s %4d   %s"
              % (k, n, "copper-family correct %5.1f%%" % agree
                 if agree == agree else "not a shape verdict"))
    n_coax_true = int((s.true_class == "Co-axial").sum())
    n_coax_pred = int((df.shape_kind == "coax").sum())
    print("  COAX IS UNMEASURABLE HERE: the test set contains %d coaxial"
          "\n  panels, so recall cannot be computed at all. What is"
          "\n  measurable is the false-positive rate, and the check fired"
          "\n  coax on %d of %d panels known not to be coax."
          % (n_coax_true, n_coax_pred, len(s)))
    print("  median circularity %.2f (round >= %.2f), metallic %.2f "
          "(>= %.2f), gold %.3f (coax needs <= %.3f)"
          % (df.shape_circ.median(), conn.ROUND_MIN,
             df.shape_metallic.median(), conn.METALLIC_MIN,
             df.shape_gold.median(), conn.GOLD_MAX))

    print("\nSTAGE-2 DESIGNS  (same detections, scored on the same %d crops)"
          % len(df))
    print("  %-46s %9s %9s" % ("", "accuracy", "macro F1"))
    for name, col in (
            ("shape-first: connector shape, then keystone for RJ-45",
             "final_class"),
            ("keystone-first (the previous order)", "alt_keystone_first"),
            ("keystone-first, tiered rule (no fitting)", "ksf_oof"),
            ("keystone-first, fitted model (out-of-fold)", "ksf_insample"),
            ("keystone-first, hand-set weighted score", "ksf_rule"),
            ("no visual check: frame rule on empty cutouts", "alt_frame"),
            ("no visual check: keystone frozen", "alt_freeze"),
            ("no visual check: keystone correctable", "alt_correctable")):
        s = df[df.scored]
        a = (s[col] == s.true_class).mean()
        t = prf(list(s.true_class), list(s[col]), sorted(set(s.true_class)))
        print("  %-46s %8.1f%% %8.1f%%%s"
              % (name, a * 100, t.f1.mean() * 100,
                 "   <- active" if col == "ksf_oof" else ""))
    print("  The tiered rule has nothing fitted in it, so it needs no"
          "\n  holdout; the logistic model is scored out-of-fold, folds split"
          "\n  by source photograph. The last comparison applies tier 1 to"
          "\n  EVERY panel instead of only those stage 1 called keystone --"
          "\n  gating costs about a point and buys a readable decision.")

    print("\nKEYSTONE POLICY  (what stage 2 may do with a stage-1 Keystone)")
    for pol, col in (("frame", "alt_frame"), ("freeze", "alt_freeze"),
                     ("correctable", "alt_correctable")):
        s = df[df.scored]
        a = (s[col] == s.true_class).mean()
        t = prf(list(s.true_class), list(s[col]), sorted(set(s.true_class)))
        print("  %-13s accuracy %5.1f%%   macro F1 %5.1f%%%s"
              % (pol, a * 100, t.f1.mean() * 100,
                 "   <- active" if pol == args.keystone_policy else ""))

    print("\nPANEL STYLE  (construction, reported not applied)")
    for k, n in df.panel_style.value_counts().items():
        print("  %-10s %4d  (%4.1f%%)" % (k, n, n / len(df) * 100))

    ch = df[df.scored & (df.pred_class != df.final_class)]
    fixed = ch[ch.final_class == ch.true_class]
    broke = ch[ch.pred_class == ch.true_class]
    print("\n  changed %d labels: fixed %d, broke %d"
          % (len(ch), len(fixed), len(broke)))
    for r in broke.itertuples():
        print("    BROKE  %-18s -> %-18s (truth %s)  %s"
              % (r.pred_class, r.final_class, r.true_class, r.verify_why))

    still = df[df.scored & (df.final_class != df.true_class)]
    print("\n  remaining %d errors:" % len(still))
    for r in still.itertuples():
        print("    %-18s -> %-18s  [%s] %s"
              % (r.true_class, r.final_class, r.verify_status, r.verify_why))

    print("\nwrote %s" % args.out)


if __name__ == "__main__":
    main()
