"""Stage 2: port-level verification with a YOLO port detector.

The embedding stage judges a whole panel by overall appearance, which is why
it confuses a cabled RJ-45 panel with SC_UPC. This stage looks at the ports
themselves: it detects every individual port, votes over port types, and
corrects the panel label when the ports disagree with the embedding.

Detector classes (13): RJ45, LC, SC, FC, SFP, QSFP, empty, and the device
ports AUX / CONSOLE / MANAGEMENT_PORT / USB_A / USB_B / USB_C.

KNOWN BLIND SPOT: the detector has no coaxial classes (no BNC, TNC, F_type,
RF_Coax, ST). It labels coax bulkheads as RJ45. So it cannot confirm a coax
panel, and will actively push a true coax panel toward RJ-45. Coax
predictions are therefore flagged rather than silently trusted.

SECOND BLIND SPOT, and what `recheck_empties` is for: the `empty` class means
"nothing plugged in here", which is not the same as "this is an open cutout".
At panel resolution an unoccupied RJ-45 socket and a bare keystone hole are
the same dark rectangle, and anything that treats `empty` as a hole will call
a fixed panel with one cable unplugged a keystone frame. So the empty slots
are handed back to the detector at twice the resolution, where a socket shows
its contact row; only when none of them comes back a socket do they count as
cutouts. ~0.3 s per panel that has an empty.
"""

import numpy as np
from PIL import Image

# detector class -> (family, leaf) ; leaf None = family known, needs a tiebreak
PORT_MAP = {
    "RJ45": ("Copper_Ethernet", "RJ-45"),
    "LC": ("Fiber_Optic", None),      # LC_UPC vs LC_APC -> decided by colour
    "SC": ("Fiber_Optic", None),      # SC_UPC vs SC_APC -> decided by colour
    "FC": ("Fiber_Optic", "FC"),
}
DEVICE_PORTS = {"AUX", "CONSOLE", "MANAGEMENT_PORT",
                "USB_A", "USB_B", "USB_C"}
TRANSCEIVER = {"SFP", "QSFP"}
COAX_LEAVES = {"BNC", "TNC", "F_type", "RF_Coax", "ST_Ports"}

UNCERTAIN = "Uncertain"
KEYSTONE = "Keystone"
BLANK = "Blank_panel"

# --- second look at the slots the panel pass called `empty` ---------------
#
# `empty` is a statement about OCCUPANCY, not about what the opening is. On a
# whole-panel pass a port is ~30 px wide, and at that size an RJ-45 socket
# with no cable in it and a bare keystone cutout are the same dark
# rectangle, so the detector answers `empty` for both. Anything downstream
# that reads `empty` as "open cutout" then calls a fixed panel with one cable
# unplugged a keystone frame.
#
# So the same model is asked again at twice the resolution, where an
# unoccupied socket shows its jack body, latch notch and contact row -- all
# things the model was trained on -- and comes back RJ45, while a bare cutout
# has none of them and stays `empty`.
#
# MEASURED, and the reason this is a whole-panel pass rather than a crop:
# cropping each slot and upscaling it (pad 0.6-3.0, imgsz 256-640) returns
# NOTHING for two slots in three -- the detector has never seen a single port
# filling the frame, so taking it out of its panel takes it out of the
# model's range. The hi-res panel pass reads 24 of 27 slots on the same
# panels and, unlike every crop setting tried, never once called a true
# keystone cutout a socket. The sweep is in the report.
EMPTY_IMGSZ = 1920      # ~2x the panel pass, so a port is ~60 px not ~30
EMPTY_CONF = 0.10       # lower bar: the question is only what is in the slot
EMPTY_COVER = 0.20      # min IoU with the slot before a box is "that slot"
EMPTY_MAX_SLOTS = 48    # cost guard; above this the panel is left alone


def green_fraction(img, boxes):
    """Share of saturated-green pixels inside detected port boxes.

    APC fiber adapters are green; UPC are beige/blue/grey. Colour survives
    low resolution far better than the angled-ferrule geometry that
    nominally distinguishes them, so this is the reliable split.
    """
    if not len(boxes):
        return 0.0
    tot = grn = 0
    arr = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
    H, W = arr.shape[:2]
    for x1, y1, x2, y2 in boxes:
        x1, y1 = max(0, int(x1)), max(0, int(y1))
        x2, y2 = min(W, int(x2)), min(H, int(y2))
        if x2 <= x1 or y2 <= y1:
            continue
        p = arr[y1:y2, x1:x2].reshape(-1, 3)
        mx, mn = p.max(1), p.min(1)
        v = mx
        s = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
        r, g, b = p[:, 0], p[:, 1], p[:, 2]
        d = np.maximum(mx - mn, 1e-6)
        h = np.where(mx == r, ((g - b) / d) % 6,
                     np.where(mx == g, (b - r) / d + 2,
                              (r - g) / d + 4)) * 60
        grn += int(((h > 75) & (h < 165) & (s > 0.25) & (v > 0.15)).sum())
        tot += len(p)
    return grn / tot if tot else 0.0


def keystone_signals(cls, xy):
    """Decide whether the panel is keystone-style from the detected slots.

    Keystone = identical snap-in cutouts in a plain frame, each jack a
    separate module. The observable consequences in detector output are:

      * uniform slot geometry -- every cutout the same size, so box widths
        and heights have low spread;
      * open / blank slots sitting alongside populated ones, because
        modules are added as needed rather than fitted at manufacture;
      * mixed connector types in one frame, which only a modular panel can
        do (a fixed-media panel is a single type by construction).

    Mixed types is the strongest single tell, so it alone is sufficient.
    """
    n = len(cls)
    if n < 4:
        return False, 0.0, "too few slots"

    w = np.array([b[2] - b[0] for b in xy], dtype=float)
    h = np.array([b[3] - b[1] for b in xy], dtype=float)
    cv_w = w.std() / max(w.mean(), 1e-6)
    cv_h = h.std() / max(h.mean(), 1e-6)
    uniform = cv_w < 0.30 and cv_h < 0.35

    n_empty = sum(c == "empty" for c in cls)
    typed = [c for c in cls if c in PORT_MAP or c in TRANSCEIVER]
    kinds = {}
    for c in typed:
        kinds[c] = kinds.get(c, 0) + 1
    ordered = sorted(kinds.values(), reverse=True)
    mixed_types = len(ordered) >= 2 and ordered[1] >= max(2,
                                                          0.25 * ordered[0])
    empty_mixed = n_empty >= 3 and len(typed) >= 3

    score = (0.55 * mixed_types + 0.30 * empty_mixed + 0.15 * uniform)
    bits = []
    if mixed_types:
        bits.append("%d connector types" % len(ordered))
    if empty_mixed:
        bits.append("%d empty + %d populated" % (n_empty, len(typed)))
    if uniform:
        bits.append("uniform cutouts (cv %.2f/%.2f)" % (cv_w, cv_h))
    is_key = mixed_types or (empty_mixed and uniform)
    return is_key, score, ", ".join(bits) or "no keystone signals"


class PortVerifier:
    def __init__(self, weights, imgsz=960, conf=0.25, device="cpu"):
        from ultralytics import YOLO
        self.model = YOLO(weights)
        self.imgsz, self.conf, self.device = imgsz, conf, device
        self.names = self.model.names

    def detect_many(self, paths, chunk=8):
        """Detect over many images without exhausting CPU RAM.

        Two traps this avoids. Calling predict() per image in a loop
        accumulates Results on the predictor until the process runs out of
        memory. But handing predict() the whole list is no better: it opens
        every image up front. So we go chunk by chunk and collect in
        between. Missing files yield an empty summary instead of killing
        the batch.
        """
        import gc
        import os
        paths = list(paths)
        for s in range(0, len(paths), chunk):
            batch = paths[s:s + chunk]
            live = [p for p in batch if os.path.exists(p)]
            res_by_path = {}
            if live:
                for p, res in zip(live, self.model.predict(
                        live, imgsz=self.imgsz, conf=self.conf,
                        device=self.device, stream=True, verbose=False)):
                    res_by_path[p] = self._summarize(res, p)
                    del res
            for p in batch:
                yield res_by_path.get(p, self._empty())
            res_by_path.clear()
            gc.collect()

    @staticmethod
    def _empty():
        return {"boxes": [], "classes": [], "img_w": 0, "img_h": 0,
                "n_det": 0, "counts": {}, "weights": {}, "dominant": None,
                "dom_weight": 0.0, "dom_count": 0, "runner_up": None,
                "mixed": False, "n_empty": 0, "n_typed": 0, "n_device": 0,
                "green_frac": 0.0, "mean_conf": 0.0, "keystone": False,
                "keystone_score": 0.0, "keystone_why": "image unavailable"}

    def detect(self, path):
        res = self.model.predict(path, imgsz=self.imgsz, conf=self.conf,
                                 device=self.device, verbose=False)[0]
        return self._summarize(res, path)

    @staticmethod
    def _no_recheck(why):
        return {"verdict": None, "n_slots": 0, "n_read": 0, "n_empty": 0,
                "reads": [], "counts": {}, "dominant": None, "why": why}

    def recheck_empties(self, path, boxes, classes, img_w=0, img_h=0,
                        imgsz=EMPTY_IMGSZ, conf=EMPTY_CONF,
                        cover=EMPTY_COVER, max_slots=EMPTY_MAX_SLOTS):
        """Re-read the slots the panel pass called `empty`, at twice the size.

        One extra detector pass over the whole panel at `imgsz`, then each
        `empty` box from the first pass is matched to whatever the hi-res
        pass put in that position.

        Returns dict(verdict, reads, counts, dominant, n_slots, n_read,
        n_empty, why), where `reads` is the class each empty slot came back
        as (None = nothing above `conf` landed on it) and:

          verdict "connector"  every empty slot re-read as the same real
                               connector -- they are unoccupied sockets, not
                               open cutouts, so this is a fixed panel of that
                               connector type
                  "empty"      at least one slot is still `empty` at twice
                               the resolution: a genuine open cutout
                  None         not applicable, or the slots did not agree;
                               the caller should keep whatever it had

        None rather than a guess is the point: a slot that reads as nothing
        at all is not evidence of a socket.
        """
        import cv2
        idx = [i for i, c in enumerate(classes) if c == "empty"]
        if not idx:
            return self._no_recheck("no empty slots to re-read")
        if len(idx) > max_slots:
            return self._no_recheck("%d empty slots -- above the %d-slot "
                                    "re-read limit" % (len(idx), max_slots))
        bgr = cv2.imread(path, cv2.IMREAD_COLOR)
        if bgr is None:
            return self._no_recheck("image unreadable")
        # The boxes were measured on the image at the size the first pass
        # saw, so the re-read has to use those same coordinates.
        if img_w and img_h and (bgr.shape[1] != img_w
                                or bgr.shape[0] != img_h):
            bgr = cv2.resize(bgr, (img_w, img_h))

        res = self.model.predict(bgr, imgsz=imgsz, conf=conf,
                                 device=self.device, verbose=False)[0]
        det = [(self.names[int(c)], f, b) for c, f, b in
               zip(res.boxes.cls.tolist(), res.boxes.conf.tolist(),
                   res.boxes.xyxy.tolist())]
        del res
        reads = [self._best_read(det, [float(v) for v in boxes[i]], cover)
                 for i in idx]

        counts = {}
        for c in reads:
            if c:
                counts[c] = counts.get(c, 0) + 1
        n_read = sum(counts.values())
        n_empty = counts.get("empty", 0)
        typed = {c: n for c, n in counts.items() if c in PORT_MAP}
        dom = max(typed, key=lambda c: typed[c]) if typed else None

        out = {"verdict": None, "n_slots": len(reads), "n_read": n_read,
               "n_empty": n_empty, "reads": reads, "counts": counts,
               "dominant": dom, "why": ""}
        tally = ", ".join("%d x %s" % (n, c) for c, n in
                          sorted(counts.items(), key=lambda kv: -kv[1])) \
            or "nothing"
        blank = len(reads) - n_read
        if n_empty:
            out["verdict"] = "empty"
            out["why"] = ("%d of %d empty slots are still empty at %d px "
                          "(%s) -- a real open cutout"
                          % (n_empty, len(reads), imgsz, tally))
        elif dom and typed[dom] == len(reads):
            out["verdict"] = "connector"
            out["why"] = ("all %d empty slots re-read as %s at %d px -- "
                          "unoccupied sockets, not open cutouts"
                          % (len(reads), dom, imgsz))
        elif dom:
            out["why"] = ("re-read is mixed or incomplete (%s; %d slot%s "
                          "returned nothing)"
                          % (tally, blank, "" if blank == 1 else "s"))
        else:
            out["why"] = ("the %d px re-read found nothing in %d slot%s -- "
                          "no evidence either way"
                          % (imgsz, len(reads), "" if len(reads) == 1 else "s"))
        return out

    @staticmethod
    def _best_read(det, slot, cover):
        """The hi-res class sitting on this slot, or None.

        Matched by overlap rather than by centre distance: a neighbouring
        port's box can contain the slot's centre when the pitch is tight,
        and confidence only breaks ties among boxes that actually sit on it.
        """
        sx0, sy0, sx1, sy1 = slot
        s_area = max(1e-6, (sx1 - sx0) * (sy1 - sy0))
        # The floor sits in the initial key, so a box only wins by beating
        # both `cover` and whatever is already best.
        best, best_key = None, (cover, 0.0)
        for name, f, b in det:
            ix = max(0.0, min(b[2], sx1) - max(b[0], sx0))
            iy = max(0.0, min(b[3], sy1) - max(b[1], sy0))
            inter = ix * iy
            union = (b[2] - b[0]) * (b[3] - b[1]) + s_area - inter
            iou = inter / (union + 1e-9)
            if (iou, f) > best_key:
                best, best_key = name, (iou, f)
        return best

    @staticmethod
    def _dedupe(xy, cls, cf, thresh=0.5):
        """One box per physical port, across classes.

        YOLO runs NMS per class, so a single port detected as both RJ45 and
        'empty' survives as two overlapping boxes. Measured on the test set:
        98 such pairs across 55 of 230 panels, almost all RJ45+empty. They
        make the overlay draw two rectangles on one port and let two
        template ports match what is really one opening, which breaks the
        one-port-one-box guarantee the overlay is supposed to give.
        Highest confidence wins.
        """
        order = sorted(range(len(xy)), key=lambda i: -cf[i])
        keep = []
        for i in order:
            a = xy[i]
            dup = False
            for j in keep:
                b = xy[j]
                ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
                iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
                inter = ix * iy
                union = ((a[2] - a[0]) * (a[3] - a[1])
                         + (b[2] - b[0]) * (b[3] - b[1]) - inter)
                if inter / (union + 1e-9) > thresh:
                    dup = True
                    break
            if not dup:
                keep.append(i)
        keep.sort()
        return ([xy[i] for i in keep], [cls[i] for i in keep],
                [cf[i] for i in keep])

    def _summarize(self, res, path):
        cls = [self.names[int(c)] for c in res.boxes.cls.tolist()]
        cf = res.boxes.conf.tolist()
        xy = res.boxes.xyxy.tolist()
        xy, cls, cf = self._dedupe(xy, cls, cf)

        # Weight by confidence, not raw count: a dozen shaky boxes should
        # not outvote a handful of certain ones.
        weight, count = {}, {}
        for c, f in zip(cls, cf):
            weight[c] = weight.get(c, 0.0) + f
            count[c] = count.get(c, 0) + 1

        # 'empty' means an unpopulated slot. It makes the panel blank only
        # if nothing else was found -- a fiber panel with 18 empty slots and
        # 6 LC adapters is still a fiber panel.
        typed = {c: w for c, w in weight.items()
                 if c in PORT_MAP or c in TRANSCEIVER}
        order = sorted(typed.items(), key=lambda kv: -kv[1])
        dom = order[0][0] if order else None
        runner = order[1][0] if len(order) > 1 else None
        # 0.25, not 0.40: a genuinely mixed keystone panel can be lopsided
        # (e.g. 3 LC + 1 RJ45). A stray single detection on a 24-port panel
        # is still only ~4%, so this stays well clear of false positives.
        mixed = bool(runner and order[1][1] >= 0.25 * order[0][1])

        is_key, key_score, key_why = keystone_signals(cls, xy)

        gf = 0.0
        if dom in ("LC", "SC"):
            with Image.open(path) as im:
                gf = green_fraction(im, [b for b, c in zip(xy, cls)
                                         if c == dom])
        # Box geometry is kept, not just the tally: port centres are the
        # correspondence points used to fit a homography between a reference
        # panel and this one. Rounded to int to keep the CSV small.
        boxes = [[int(round(c)) for c in b] for b in xy]

        return {
            "boxes": boxes, "classes": cls,
            "img_w": int(res.orig_shape[1]), "img_h": int(res.orig_shape[0]),
            "n_det": len(cls), "counts": count, "weights": weight,
            "dominant": dom, "dom_weight": order[0][1] if order else 0.0,
            "dom_count": count.get(dom, 0),
            "runner_up": runner, "mixed": mixed,
            "n_empty": count.get("empty", 0),
            "n_typed": sum(count.get(c, 0) for c in typed),
            "n_device": sum(count.get(d, 0) for d in DEVICE_PORTS),
            "green_frac": gf,
            "mean_conf": float(np.mean(cf)) if cf else 0.0,
            "keystone": is_key, "keystone_score": key_score,
            "keystone_why": key_why,
        }

    def port_leaf(self, d, green_thresh=0.12):
        """Leaf implied by the ports alone, or None if undecidable."""
        dom = d["dominant"]
        if dom is None:
            if d["n_empty"] >= 3 and d["n_device"] == 0:
                # Every slot read `empty` on the panel pass. If the zoomed
                # re-read finds a socket in each of them, the panel is an
                # unoccupied panel OF THAT TYPE, not a blank frame -- an
                # unplugged RJ-45 panel is still an RJ-45 panel.
                rc = d.get("empty_recheck") or {}
                leaf = PORT_MAP.get(rc.get("dominant") or "", (None, None))[1]
                if rc.get("verdict") == "connector" and leaf:
                    return leaf, "%d unoccupied slots, %s" % (d["n_empty"],
                                                              rc["why"])
                return BLANK, "all %d slots empty" % d["n_empty"]
            return None, "no ports detected"
        if dom in TRANSCEIVER:
            return "SFP_Cage", "%s transceiver cages" % dom
        fam, leaf = PORT_MAP[dom]
        if leaf:
            return leaf, "%d x %s ports" % (d["dom_count"], dom)
        apc = d["green_frac"] >= green_thresh
        return ("%s_%s" % (dom, "APC" if apc else "UPC"),
                "%d x %s, green %.0f%% -> %s"
                % (d["dom_count"], dom, d["green_frac"] * 100,
                   "APC" if apc else "UPC"))


def panel_style(d):
    """Keystone / Fixed / Unknown -- construction, independent of connector."""
    if d["n_det"] < 4:
        return "Unknown"
    return KEYSTONE if d["keystone"] else "Fixed"


def reconcile(emb_leaf, emb_status, det, verifier,
              min_ports=3, min_weight=1.5, min_share=0.25,
              empty_policy="keystone"):
    """Combine the embedding guess with the port evidence.

    Returns (final_leaf, status, reason). The detector wins when it has
    solid evidence, because it is looking at the actual ports; the embedding
    is kept when the detector is silent or out of its depth; and when
    neither can decide, the answer is Uncertain rather than a guess.

    Keystone never replaces a determinable connector type -- a keystone
    frame full of RJ-45 jacks is still an RJ-45 panel. It only becomes the
    label when no connector type can be read off the panel at all.
    """
    pleaf, why = verifier.port_leaf(det)

    # --- neither stage has usable evidence -> say so ---
    if pleaf is None:
        if emb_status == "unsure":
            return UNCERTAIN, "uncertain", \
                "no ports detected and no gallery match"
        return emb_leaf, "unverified", why

    if pleaf == BLANK:
        # Every slot is empty, so the connector type is genuinely
        # unreadable from the image. Which label is "right" is a labeling
        # convention, not a detection question, so it is a policy knob:
        #   keystone       -- an unpopulated modular frame (most accurate)
        #   blank          -- Blank_panel
        #   keep-embedding -- trust the embedding's guess from panel context
        if empty_policy == "keep-embedding":
            return emb_leaf, "empty_kept", "%s; kept embedding guess" % why
        if empty_policy == "blank":
            return BLANK, "corrected", why
        if det["keystone"] or det["n_empty"] >= 6:
            return KEYSTONE, "corrected", "%s, modular frame" % why
        return BLANK, "corrected", why

    # --- the panel is unoccupied, but the slots are sockets ---
    # port_leaf has already named the connector from the re-read. The gates
    # below weigh TYPED detections against each other, and there are none
    # here by construction -- every box came back `empty` on the panel pass
    # -- so running this through them would throw the answer away and hand
    # the panel to the keystone branch. An unplugged RJ-45 panel is an RJ-45
    # panel whatever the embedding guessed.
    if (det["dominant"] is None
            and (det.get("empty_recheck") or {}).get("verdict") ==
            "connector"):
        return pleaf, "sockets_only", why

    # --- detector found ports but none dominates convincingly ---
    #
    # Share matters as well as count. A real example: an RJ-45 panel whose
    # occupied ports mostly read as 'empty' also produced 3 stray LC boxes.
    # Three detections cleared the absolute count and weight gates, so a
    # 13% minority re-labelled the whole panel as fiber -- and because no
    # LC reference exists, the overlay then vanished too. Overturning the
    # embedding needs a real share of the detections behind it; merely
    # agreeing with it does not.
    share = det["dom_count"] / max(1, det["n_det"])
    thin = share < min_share and pleaf != emb_leaf
    if det["dom_count"] < min_ports or det["dom_weight"] < min_weight or thin:
        if thin:
            why = "%s -- only %d of %d detections (%.0f%%)" % (
                why, det["dom_count"], det["n_det"], share * 100)
        # Weak evidence that AGREES with the embedding is corroboration,
        # not a reason to abstain. Only bail out when the two disagree.
        if pleaf == emb_leaf:
            return pleaf, "confirmed_weak", "%s (agrees with embedding)" % why
        if det["keystone"]:
            return KEYSTONE, "keystone", \
                "mixed/undetermined modules: %s" % det["keystone_why"]
        if emb_status == "unsure":
            return UNCERTAIN, "uncertain", "weak port evidence (%s)" % why
        return emb_leaf, "weak_evidence", "only %s" % why

    # The detector is blind to coax, so an RJ45 verdict over a coax guess is
    # not trustworthy on its own -- it calls coax bulkheads RJ45 too.
    if emb_leaf in COAX_LEAVES and pleaf == "RJ-45":
        if det["mean_conf"] >= 0.65 and det["dom_count"] >= 8:
            return pleaf, "corrected", \
                "%s (detector has no coax class; strong RJ45 evidence)" % why
        return UNCERTAIN, "coax_unverifiable", \
            "embedding says %s but detector has no coax class" % emb_leaf

    if det["mixed"]:
        return pleaf, "mixed", "%s (+%s)" % (why, det["runner_up"])
    if pleaf == emb_leaf:
        return pleaf, "confirmed", why
    if pleaf.split("_")[0] == str(emb_leaf).split("_")[0]:
        return pleaf, "refined", why
    return pleaf, "corrected", why
