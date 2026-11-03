"""Check 1: what SHAPE is each opening? Separates coax from RJ-45 from fiber.

This runs before any keystone reasoning, because the keystone question
("is the frame separate from the jacks?") only makes sense once you know
what kind of jack you are looking at.

  coax     a round, metal opening with a single centre pin or hole and no
           row of gold contacts. Cords are thick and round, ending in a
           metal barrel -- two lugs on the outside (BNC) or threaded
           (F-type).
  RJ-45    a rectangular plastic opening with a latch notch and a row of
           gold contacts inside. Cords end in a clear plastic latch plug.
  fiber    small square or duplex openings (LC/SC/FC).

WHY THIS IS MEASURED FROM PIXELS AND NOT FROM THE DETECTOR

The 13-class port detector has no coaxial class at all. It reads a coax
bulkhead as RJ45, which is the single blind spot that has been flagged
throughout this pipeline. So coax cannot come from the detector's labels;
it has to come from the opening itself. Fiber is different -- LC, SC and FC
are real detector classes and reliable -- so fiber is taken from the
detector and only the coax-vs-RJ45 split is done here.

  roundness       circularity 4*pi*A/P^2 of the dark opening inside each
                  box. A circle is 1.0; a rectangle with a latch notch
                  lands near 0.6-0.75.
  metallic        low colour saturation plus strong specular highlights --
                  a machined barrel, not moulded plastic.
  gold_contacts   share of pixels in the brass/gold hue band, bright and
                  saturated, which is the contact row inside an RJ-45.

CALIBRATION HONESTY: the current test set contains ZERO coaxial panels, so
the coax recall of this check cannot be measured here at all. What CAN be
measured, and is reported, is its false-positive rate against 211 panels
known not to be coax. Thresholds are therefore set conservatively: the cost
of missing a coax panel is a wrong label, but the cost of firing on an
RJ-45 panel is a wrong label on a class that is 60% of the data.
"""

import cv2
import numpy as np

FIBER_CLASSES = {"LC", "SC", "FC"}
RJ45_CLASS = "RJ45"
EMPTY = "empty"

# Conservative because coax is unmeasurable on this set -- see the module
# docstring. A panel must look round AND metallic AND show no contact row.
ROUND_MIN = 0.86         # circularity; a circle is 1.0
METALLIC_MIN = 0.55      # low saturation + specular highlights
GOLD_MAX = 0.015         # a contact row above this means RJ-45, not coax
MIN_PORTS = 4            # below this there is nothing to vote over
FIBER_SHARE = 0.34       # share of typed ports that must be LC/SC/FC
# A "fiber" verdict is checked against the opening too: the detector reads
# coax bulkheads as LC as well as RJ45 (measured on the 98-image set).
COAX_CHECK_FIBER = True


def _opening_stats(bgr, box):
    """(circularity, metallic, gold share) for one port opening."""
    H, W = bgr.shape[:2]
    x0, y0 = max(0, int(box[0])), max(0, int(box[1]))
    x1, y1 = min(W, int(box[2])), min(H, int(box[3]))
    if x1 - x0 < 6 or y1 - y0 < 6:
        return None
    patch = bgr[y0:y1, x0:x1]
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0].astype(np.float32), \
        hsv[:, :, 1].astype(np.float32), hsv[:, :, 2].astype(np.float32)

    # --- shape of the dark opening ---
    g = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    g = cv2.GaussianBlur(g, (3, 3), 0)
    thr = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV
                        + cv2.THRESH_OTSU)[1]
    cnts = cv2.findContours(thr, cv2.RETR_EXTERNAL,
                            cv2.CHAIN_APPROX_SIMPLE)[0]
    circ = 0.0
    if cnts:
        c = max(cnts, key=cv2.contourArea)
        a, p = cv2.contourArea(c), cv2.arcLength(c, True)
        if p > 1e-6 and a > 0.08 * patch.shape[0] * patch.shape[1]:
            circ = float(np.clip(4.0 * np.pi * a / (p * p), 0.0, 1.0))

    # --- metal: desaturated body with specular highlights ---
    low_sat = float((s < 60).mean())
    spec = float((v > 215).mean())
    metallic = float(np.clip(0.65 * low_sat + 3.0 * min(spec, 0.12), 0, 1))

    # --- gold contact row: brass hue, bright and saturated ---
    # OpenCV hue is 0-180, so brass/gold sits around 12-28.
    gold = float(((h > 12) & (h < 30) & (s > 80) & (v > 110)).mean())

    return circ, metallic, gold


def _load(image_path, img_w, img_h):
    bgr = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if bgr is None:
        return None
    if img_w and img_h and (bgr.shape[1] != img_w or bgr.shape[0] != img_h):
        bgr = cv2.resize(bgr, (img_w, img_h))
    return bgr


def _median_stats(bgr, boxes, idx):
    """(circ, metallic, gold) medians over the given boxes, or None."""
    st = [s for s in (_opening_stats(bgr, boxes[i]) for i in idx)
          if s is not None]
    if len(st) < 3:
        return None
    arr = np.asarray(st)
    return tuple(float(np.median(arr[:, k])) for k in range(3))


def shape_vote(image_path, boxes, classes, img_w=0, img_h=0):
    """Panel-level connector shape -> dict with kind and the evidence.

    kind is "coax", "fiber", "rj45" or "unknown". Voting is over the
    individual openings, by median, so one blown-out or cable-covered port
    cannot swing the panel.
    """
    out = {"kind": "unknown", "circ": 0.0, "metallic": 0.0, "gold": 0.0,
           "n_ports": 0, "n_fiber": 0, "fiber_share": 0.0, "why": ""}
    cls = list(classes)
    typed = [c for c in cls if c == RJ45_CLASS or c in FIBER_CLASSES]
    out["n_ports"] = len(typed)
    out["n_fiber"] = sum(c in FIBER_CLASSES for c in cls)

    # --- fiber comes from the detector: LC/SC/FC are real classes here ---
    if typed:
        out["fiber_share"] = out["n_fiber"] / len(typed)
    if out["n_fiber"] >= 3 and out["fiber_share"] >= FIBER_SHARE:
        fidx = [i for i, c in enumerate(cls) if c in FIBER_CLASSES]
        if COAX_CHECK_FIBER:
            bgr = _load(image_path, img_w, img_h)
            ms = _median_stats(bgr, boxes, fidx) if bgr is not None else None
            if ms is not None:
                out["circ"], out["metallic"], out["gold"] = ms
                if (out["circ"] >= ROUND_MIN
                        and out["metallic"] >= METALLIC_MIN
                        and out["gold"] <= GOLD_MAX):
                    out["kind"] = "coax"
                    out["why"] = (
                        "detector said LC/SC, but the openings are round "
                        "metal with no contact row (circularity %.2f, "
                        "metallic %.2f, gold %.3f) -- coax bulkheads read "
                        "as fiber" % ms)
                    return out
        out["kind"] = "fiber"
        out["why"] = ("%d LC/SC/FC adapters, %.0f%% of the typed ports -- "
                      "small square/duplex openings, so fiber"
                      % (out["n_fiber"], out["fiber_share"] * 100))
        return out

    # --- coax vs RJ-45 is decided from the opening itself ---
    idx = [i for i, c in enumerate(cls) if c == RJ45_CLASS]
    if len(idx) < MIN_PORTS:
        out["why"] = ("only %d rectangular openings -- too few to judge "
                      "shape" % len(idx))
        return out

    bgr = _load(image_path, img_w, img_h)
    if bgr is None:
        out["why"] = "image unreadable"
        return out

    stats = [s for s in (_opening_stats(bgr, boxes[i]) for i in idx)
             if s is not None]
    if len(stats) < MIN_PORTS:
        out["why"] = "openings too small to measure"
        return out
    arr = np.asarray(stats)
    out["circ"] = float(np.median(arr[:, 0]))
    out["metallic"] = float(np.median(arr[:, 1]))
    out["gold"] = float(np.median(arr[:, 2]))

    if (out["circ"] >= ROUND_MIN and out["metallic"] >= METALLIC_MIN
            and out["gold"] <= GOLD_MAX):
        out["kind"] = "coax"
        out["why"] = ("round metal openings, no contact row "
                      "(circularity %.2f, metallic %.2f, gold %.3f)"
                      % (out["circ"], out["metallic"], out["gold"]))
        return out

    out["kind"] = "rj45"
    bits = []
    if out["circ"] < ROUND_MIN:
        bits.append("rectangular opening (circularity %.2f)" % out["circ"])
    if out["gold"] > GOLD_MAX:
        bits.append("a row of gold contacts (%.3f)" % out["gold"])
    if out["metallic"] < METALLIC_MIN:
        bits.append("moulded plastic rather than metal (%.2f)"
                    % out["metallic"])
    out["why"] = "; ".join(bits) or "rectangular plastic opening"
    return out
