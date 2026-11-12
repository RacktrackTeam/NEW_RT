"""Ports on a router panel from the trained port model (best_merged_ports), router style.

yolo_ports.detect_ports arranges detections into one switch-like grid: ports are filled
in where a block has a gap, and ports standing apart from the block are dropped as
strays (on a switch that is the console beside the port block). A router panel is
groups: chassis jacks, one module card, another card. On a Cisco 1921 that grid step
threw away both T1/E1 jacks of a VWIC2 card and GE0/0. Here every confident detection
is a port of its own; nothing is invented, nothing apart from the main block is lost.

Same output shape as yolo_ports.detect_ports: ([{id, type, box [x,y,w,h], status,
opening_visible, confidence}], info). Network ports are numbered by column, left to
right, top to bottom within a column (1 top-left, 2 below it, 3 next column).
"""
import cv2
import numpy as np

import yolo_ports

DATA_MIN_CONF = 0.35       # network ports (below this the model's guesses sit on vents and screws)
NAMED_MIN_CONF = 0.35      # console / AUX / management / USB


SMALL_W = 900              # photos narrower than this are enlarged before detection
MANY_NAMED = 3             # this many 'consoles' on one face are serial RJ45 ports, not the console


def _raw(bgr):
    """The port model's detections; a small photo (382 px wide: jacks ~45 px) is enlarged first,
    the boxes are scaled back to the photo."""
    w = bgr.shape[1]
    if w >= SMALL_W:
        return yolo_ports.raw_detections(bgr)
    s = 1200.0 / w
    big = cv2.resize(bgr, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
    out = []
    for d in yolo_ports.raw_detections(big):
        out.append(dict(d, xyxy=[v / s for v in d["xyxy"]]))
    return out


def _tidy(dets):
    """Appliance faces: a console server or serial gateway has rows of RJ45 serial ports the
    model calls CONSOLE - with 3+ of them they are numbered ports (RJ45), not one console. A
    box spanning two detected jacks (two stacked jacks read as one) is dropped."""
    def centre(d):
        x1, y1, x2, y2 = d["xyxy"]
        return (x1 + x2) / 2, (y1 + y2) / 2

    named_console = [d for d in dets if d["label"] == "CONSOLE"]
    if len(named_console) >= MANY_NAMED:
        dets = [dict(d, label="RJ45") if d["label"] == "CONSOLE" else d for d in dets]

    # a named port (USB, console) whose centre sits inside a network jack's box is part of that jack
    data = [d for d in dets if d["label"] in yolo_ports.DATA_PORTS]
    dets = [d for d in dets if d["label"] in yolo_ports.DATA_PORTS or not any(
        o["xyxy"][0] <= centre(d)[0] <= o["xyxy"][2] and o["xyxy"][1] <= centre(d)[1] <= o["xyxy"][3] for o in data)]

    keep = []
    for d in dets:
        x1, y1, x2, y2 = d["xyxy"]
        inside = [o for o in dets if o is not d and x1 <= centre(o)[0] <= x2 and y1 <= centre(o)[1] <= y2
                  and (o["xyxy"][2] - o["xyxy"][0]) * (o["xyxy"][3] - o["xyxy"][1]) < 0.75 * (x2 - x1) * (y2 - y1)]
        if len(inside) >= 2:
            continue
        keep.append(d)
    return keep


def detect(bgr):
    dets = _tidy(_raw(bgr))
    data, named = [], []
    for d in dets:
        x1, y1, x2, y2 = d["xyxy"]
        box = [round(x1, 1), round(y1, 1), round(x2 - x1, 1), round(y2 - y1, 1)]
        kind = yolo_ports.DATA_PORTS.get(d["label"])
        if kind and d["conf"] >= DATA_MIN_CONF:
            data.append({"type": "sfp" if kind == "qsfp" else kind, "box": box, "status": d["status"],
                         "opening_visible": d["status"] == "empty", "confidence": round(d["conf"], 2)})
        elif d["label"] in yolo_ports.NAMED_PORTS and d["conf"] >= NAMED_MIN_CONF:
            named.append({"id": yolo_ports.NAMED_PORTS[d["label"]], "type": d["label"].lower(), "box": box,
                          "status": d["status"], "opening_visible": d["status"] == "empty",
                          "confidence": round(d["conf"], 2)})
    # two stacked jacks read as one tall box (a Cisco C1161's 0/0/0 over 0/0/1): split it
    if data:
        mh = float(np.median([p["box"][3] for p in data]))
        mw = float(np.median([p["box"][2] for p in data]))
        split = []
        for p in data:
            x, y, w, h = p["box"]
            if h > 1.7 * mh and h > 1.4 * w and abs(w - mw) < 0.5 * mw:
                split += [dict(p, box=[x, y, w, round(h / 2, 1)]), dict(p, box=[x, round(y + h / 2, 1), w, round(h / 2, 1)])]
            else:
                split.append(p)
        data = split
    # column order: ports whose centres are within half a port width share a column
    if data:
        pw = float(np.median([p["box"][2] for p in data]))
        cols = []
        for p in sorted(data, key=lambda p: p["box"][0] + p["box"][2] / 2):
            cx = p["box"][0] + p["box"][2] / 2
            if cols and cx - cols[-1][0] < 0.5 * pw:
                cols[-1][1].append(p)
            else:
                cols.append([cx, [p]])
        data = [p for _, col in cols for p in sorted(col, key=lambda p: p["box"][1])]
    for i, p in enumerate(data, 1):
        p["id"] = i
    ports = [{"id": p["id"], **{k: v for k, v in p.items() if k != "id"}} for p in data] + named
    info = {"detector": "merged port model (router: no grid)", "detections": len(dets),
            "rj45": sum(p["type"] == "rj45" for p in data), "sfp": sum(p["type"] == "sfp" for p in data),
            "seen": len(ports), "connected": sum(p["status"] == "connected" for p in ports)}
    return ports, info


# --------------------------------------------------------------------- module-card ports
WEAK_MIN_CONF = 0.18        # a weak detection may still be a module port if it looks like one
CLOSER_LOOK = False         # second, enlarged pass along the port band (see module_port_candidates)
JACK_LABELS = {"RJ45", "SFP", "QSFP", "LC", "SC", "FC", "CONSOLE", "AUX", "MANAGEMENT_PORT"}


def _box(d):
    x1, y1, x2, y2 = d["xyxy"]
    return [float(x1), float(y1), float(x2 - x1), float(y2 - y1)]


def _overlap(a, b):
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    return ix * iy / max(1.0, min(a[2] * a[3], b[2] * b[3]))


def module_port_candidates(bgr, known_boxes, log=None):
    """Ports on module cards that the first pass missed, for the photo being mapped.

    known_boxes: [x, y, w, h] of every port already placed (template chassis ports and the
    confident detections). Returns [{type, box, confidence, how}] of additional jacks:
      - weak detections (0.18-0.35) of a jack or cage, kept only when they are the size and
        shape of the router's real ports (0.6-1.6x the median, aspect 0.6-1.8) and sit level
        with other ports - a power socket or a screw is neither;
      - (off by default, CLOSER_LOOK) a second look: the panel band cut into overlapping
        windows, enlarged 2x and run through the model again; same checks.
    The model often calls a T1/E1 module jack 'console': such jacks count as network ports here
    (the router's own console and AUX are already placed from the template)."""
    if len(known_boxes) < 2:
        return []
    kw = float(np.median([b[2] for b in known_boxes]))
    kh = float(np.median([b[3] for b in known_boxes]))
    ys = [b[1] + b[3] / 2 for b in known_boxes]
    band = (min(b[1] for b in known_boxes) - 0.8 * kh, max(b[1] + b[3] for b in known_boxes) + 0.8 * kh)

    def plausible(b):
        w, h = b[2], b[3]
        if not (0.6 * kw <= w <= 1.6 * kw and 0.6 * kh <= h <= 1.6 * kh and 0.6 <= w / max(h, 1) <= 1.8):
            return False
        cy = b[1] + h / 2
        return band[0] <= cy <= band[1] and min(abs(cy - y) for y in ys) <= 1.6 * kh

    taken = list(known_boxes)
    out = []

    def take(b, label, conf, how):
        if label not in JACK_LABELS or not plausible(b) or any(_overlap(b, k) > 0.3 for k in taken):
            return
        taken.append(b)
        out.append({"type": "sfp" if label in ("SFP", "QSFP", "LC", "SC", "FC") else "rj45",
                    "box": [round(v, 1) for v in b], "confidence": round(conf, 2), "how": how,
                    "label": label})

    # 1. weak detections of the full photo
    for d in sorted(yolo_ports.raw_detections(bgr), key=lambda d: -d["conf"]):
        if d["conf"] >= WEAK_MIN_CONF:
            take(_box(d), d["label"], d["conf"], "weak detection")
    # 2. a closer look along the port band - off: on 19 test photos it found 1 real port and
    # 4 false ones (LED panels, labels, blank plates); the weak detections above found 9 real, 0 false
    H, W = bgr.shape[:2]
    y0, y1 = int(max(0, band[0])), int(min(H, band[1]))
    if CLOSER_LOOK and y1 - y0 >= 8:
        win = int(max(3 * (y1 - y0), 6 * kw))
        step = max(1, int(win * 0.6))
        for x0 in range(0, max(1, W - win // 3), step):
            crop = bgr[y0:y1, x0:x0 + win]
            if crop.shape[1] < 2 * kw:
                continue
            big = cv2.resize(crop, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
            for d in yolo_ports._detect(str(yolo_ports.MERGED), big, 640):
                if d["conf"] < 0.35:
                    continue
                x1, yy1, x2, yy2 = d["xyxy"]
                b = [x0 + x1 / 2, y0 + yy1 / 2, (x2 - x1) / 2, (yy2 - yy1) / 2]
                take(b, d["label"], d["conf"], "closer look")
    if out and log:
        log(f"  module ports: {len(out)} more found ({', '.join(sorted(set(o['how'] for o in out)))})")
    return out
