"""Ports the port model missed, found again - without false boxes.

The model sometimes misses jacks it should see: dark jacks on a dark panel (Barracuda Load Balancer
540: 0 of 4 found), jacks at an angle (Quantum QN-S-50: 2 of 5), a block of cages beside the ones it
found. Two recoveries, used by all three agents through yolo_ports.raw_detections:

1. Brightened pass: when fewer than 4 network ports are found, the model is run again on a
   brightened copy; ports found there that are not already boxed are added.
2. Look-alike pass: a port the model found with good confidence is searched for along its own row;
   a match is added only when it passes every check below.

Checks a recovered box must pass (the reason no LED, label, logo or duplicate gets a box):
  - row:       its centre within a quarter of a port height of a row holding 2+ found ports
               (LEDs, labels and logos sit above or below the port row);
  - neighbour: directly beside a found or recovered port of that row, at the row's port spacing
               (0.8-1.35 pitch) - it only fills gaps and extends a row one port at a time;
  - size:      the found port's size, +-10 %;
  - look:      normalised correlation >= LOOK_MIN with a port found at >= REF_MIN confidence;
  - duplicate: its centre more than 0.6 of a port from every box already there.
A recovered box is never used to look for further ports. PORT_RECOVERY=0 turns both passes off.
"""
import os

import cv2
import numpy as np

LOOK_MIN = 0.68          # correlation with a found port (QN-S-50's missed jacks score 0.69-0.80)
REF_MIN = 0.45           # only confident detections (in a row of 2+ found ports) are used as the port to look for
FEW_PORTS = 4            # brightened pass below this many network ports
DATA_LABELS = {"RJ45", "SFP", "QSFP", "FC", "LC", "SC"}


def enabled():
    return os.environ.get("PORT_RECOVERY", "1") != "0"


def _centre(b):
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def _near(b, boxes, w, h, frac=0.6):
    cx, cy = _centre(b)
    return any(abs(cx - _centre(q)[0]) < frac * w and abs(cy - _centre(q)[1]) < frac * h for q in boxes)


def brightened(bgr, detect, dets):
    """Ports found on a brightened copy that are not already boxed (only when few were found)."""
    data = [d for d in dets if d["label"] in DATA_LABELS and d["conf"] >= 0.35]
    if len(data) >= FEW_PORTS:
        return []
    bright = np.clip(255.0 * (bgr / 255.0) ** 0.6, 0, 255).astype(np.uint8)
    extra = []
    for d in detect(bright):
        if d["label"] not in DATA_LABELS or d["conf"] < 0.35:
            continue
        w, h = d["xyxy"][2] - d["xyxy"][0], d["xyxy"][3] - d["xyxy"][1]
        if _near(d["xyxy"], [q["xyxy"] for q in dets + extra], w, h):
            continue
        extra.append(dict(d, recovered="brightened"))
    return extra


def _rows(data):
    """Rows of found ports: [{cy, h, boxes}] with 2+ ports each, boxes sorted left to right."""
    rows = []
    for d in sorted(data, key=lambda d: _centre(d["xyxy"])[1]):
        cy = _centre(d["xyxy"])[1]
        h = d["xyxy"][3] - d["xyxy"][1]
        if rows and abs(cy - rows[-1]["cy"]) < 0.3 * rows[-1]["h"]:
            rows[-1]["dets"].append(d)
            rows[-1]["cy"] = float(np.mean([_centre(q["xyxy"])[1] for q in rows[-1]["dets"]]))
        else:
            rows.append({"cy": cy, "h": h, "dets": [d]})
    return [r for r in rows if len(r["dets"]) >= 2]


def lookalikes(bgr, dets):
    """Ports beside the found ones in their own rows that look like them (see the module's checks)."""
    data = [d for d in dets if d["label"] in DATA_LABELS]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    H, W = gray.shape
    taken = [d["xyxy"] for d in dets]
    extra = []
    for row in _rows(data):
        refs = [d for d in row["dets"] if d["conf"] >= REF_MIN]
        if not refs:
            continue
        ref = max(refs, key=lambda d: d["conf"])
        x1, y1, x2, y2 = [int(round(v)) for v in ref["xyxy"]]
        w, h = x2 - x1, y2 - y1
        if w < 6 or h < 6:
            continue
        patch = gray[y1:y2, x1:x2]
        xs = sorted(_centre(d["xyxy"])[0] for d in row["dets"])
        gaps = [b - a for a, b in zip(xs, xs[1:]) if b - a > 0.5 * w]
        pitch = float(min(gaps)) if gaps else 1.1 * w
        pitch = min(max(pitch, 0.9 * w), 1.6 * w)
        band0, band1 = int(max(0, row["cy"] - 0.75 * h)), int(min(H, row["cy"] + 0.75 * h))
        band = gray[band0:band1]
        cands = []
        for s in (0.9, 1.0, 1.1):
            p = cv2.resize(patch, None, fx=s, fy=s)
            if p.shape[0] > band.shape[0] or p.shape[1] > band.shape[1] or p.std() < 8:
                continue
            r = cv2.matchTemplate(band, p, cv2.TM_CCOEFF_NORMED)
            for yy, xx in zip(*np.where(r >= LOOK_MIN)):
                cands.append((float(r[yy, xx]), [float(xx), float(yy + band0), float(xx + p.shape[1]), float(yy + band0 + p.shape[0])]))
        cands.sort(key=lambda c: -c[0])
        row_boxes = [d["xyxy"] for d in row["dets"]]
        grew = True
        while grew:                                   # extend outwards and into gaps, one port at a time
            grew = False
            for score, b in cands:
                cx, cy = _centre(b)
                if abs(cy - row["cy"]) > 0.25 * h:
                    continue
                bw, bh = b[2] - b[0], b[3] - b[1]
                if not (0.88 * w <= bw <= 1.12 * w and 0.88 * h <= bh <= 1.12 * h):
                    continue
                if _near(b, taken + [e["xyxy"] for e in extra], w, h):
                    continue
                dists = [abs(cx - _centre(q)[0]) for q in row_boxes]
                if not dists or not (0.8 * pitch <= min(dists) <= 1.35 * pitch):
                    continue
                extra.append({"xyxy": b, "label": ref["label"], "conf": round(min(ref["conf"], score), 3),
                              "recovered": "look-alike"})
                row_boxes.append(b)
                grew = True
                break
    return extra


WEAK_MIN = 0.35         # agents drop network ports below this; a weaker one may still be confirmed
WEAK_LOOK_MIN = 0.6     # ... when it also looks like a confident port (QN-S-50 WAN 0.67; a logo 0.25)


def _look(gray, ref, box):
    """Normalised correlation of box with ref (box resized to ref's size)."""
    x1, y1, x2, y2 = [int(round(v)) for v in ref]
    X1, Y1, X2, Y2 = [int(round(v)) for v in box]
    p, q = gray[y1:y2, x1:x2], gray[Y1:Y2, X1:X2]
    if p.size == 0 or q.size == 0 or p.std() < 8:
        return -1.0
    q = cv2.resize(q, (p.shape[1], p.shape[0]))
    return float(cv2.matchTemplate(q, p, cv2.TM_CCOEFF_NORMED).max())


def confirm_weak(bgr, dets, extra):
    """A port the model found only weakly (below WEAK_MIN) counts when it passes the row, neighbour and
    size checks against confident and recovered ports and looks like a confident port (WEAK_LOOK_MIN) -
    the QN-S-50's blue WAN jack at 0.29 beside its LAN jacks. Its confidence is raised to WEAK_MIN and it
    is marked recovered 'confirmed'."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    refs = [d for d in dets if d["label"] in DATA_LABELS and d["conf"] >= REF_MIN and not d.get("recovered")]
    strong = [d for d in dets if d["label"] in DATA_LABELS and d["conf"] >= WEAK_MIN] + extra
    out = []
    for row in _rows(strong):
        ws = [d["xyxy"][2] - d["xyxy"][0] for d in row["dets"]]
        hs = [d["xyxy"][3] - d["xyxy"][1] for d in row["dets"]]
        w, h = float(np.median(ws)), float(np.median(hs))
        xs = sorted(_centre(d["xyxy"])[0] for d in row["dets"])
        gaps = [b - a for a, b in zip(xs, xs[1:]) if b - a > 0.5 * w]
        pitch = min(max(float(min(gaps)) if gaps else 1.1 * w, 0.9 * w), 1.6 * w)
        boxes = [d["xyxy"] for d in row["dets"]]
        for d in dets:
            if d["label"] not in DATA_LABELS or d["conf"] >= WEAK_MIN or d.get("recovered"):
                continue
            b = d["xyxy"]
            cx, cy = _centre(b)
            bw, bh = b[2] - b[0], b[3] - b[1]
            if abs(cy - row["cy"]) > 0.25 * h or not (0.85 * w <= bw <= 1.15 * w and 0.85 * h <= bh <= 1.15 * h):
                continue
            if _near(b, boxes, w, h):
                continue
            dist = min(abs(cx - _centre(q)[0]) for q in boxes)
            if not refs or max(_look(gray, r["xyxy"], b) for r in refs) < WEAK_LOOK_MIN:
                continue
            if 0.8 * pitch <= dist <= 1.35 * pitch:
                d.update(conf=WEAK_MIN, recovered="confirmed")
                out.append(d)
                boxes.append(b)
    return out


def recover(bgr, dets, detect):
    """The detections plus the ports recovered from them (each marked 'recovered')."""
    if not enabled() or bgr is None:
        return dets
    dets = [dict(d) for d in dets]
    extra = brightened(bgr, detect, dets)
    extra += lookalikes(bgr, dets + extra)
    confirm_weak(bgr, dets, extra)
    return dets + extra
