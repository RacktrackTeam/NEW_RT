"""
ports.py - find ports with RackTrack's trained models (RT-EOS/Models).

  port_type_finder.pt          where each port is and its type (RJ45, SFP, QSFP,
                               CONSOLE, MANAGEMENT_PORT, USB ...). Finds ports with a
                               cable plugged in, not only empty ones.
  port_connected_or_empty.pt   whether each port has a cable in it

Detections are put into rows and columns (a missing port in a column that has a port
in the other row is filled in), and ports are numbered: RJ45 first (1 top-left, 2 below it, 3 next column;
or row by row), then SFP/QSFP, then console and management ports by name.

Set SWITCH_FIT_MODELS to the folder holding the two .pt files, or RACKTRACK_REPO to the
RT-EOS folder (models in its Models/; no default). Needs the
ultralytics package. available() is False if either is missing.
"""
import os
from pathlib import Path

import numpy as np

# folder with port_type_finder.pt and port_connected_or_empty.pt (from RT-EOS/Models)
# only when set on purpose (SWITCH_FIT_MODELS or RACKTRACK_REPO): never picked up from a default folder
MODELS = Path(os.environ["SWITCH_FIT_MODELS"]) if os.environ.get("SWITCH_FIT_MODELS") else \
    Path(os.environ["RACKTRACK_REPO"]) / "Models" if os.environ.get("RACKTRACK_REPO") else \
    Path(os.devnull) / "no-rt-eos-models"
CONF = 0.23                      # same threshold the RackTrack pipeline uses
DATA_PORTS = {"RJ45": "rj45", "SFP": "sfp", "QSFP": "qsfp"}
NAMED_PORTS = {"CONSOLE": "Console", "MANAGEMENT_PORT": "Mgmt", "USB_A": "USB", "USB_B": "USB", "USB_C": "USB"}
_models = {}


def available():
    if not (MODELS / "port_type_finder.pt").exists():
        return False
    import importlib.util
    return importlib.util.find_spec("ultralytics") is not None


def _model(name):
    if name not in _models:
        from ultralytics import YOLO
        _models[name] = YOLO(str(MODELS / name))
    return _models[name]


def _iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    i = ix * iy
    return i / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i + 1e-9)


def _detect(name, bgr, imgsz):
    r = _model(name).predict(bgr, conf=CONF, imgsz=imgsz, verbose=False)[0]
    names = r.names
    dets = [{"xyxy": b, "label": names[int(c)], "conf": float(s)}
            for b, c, s in zip(r.boxes.xyxy.tolist(), r.boxes.cls.tolist(), r.boxes.conf.tolist())]
    dets.sort(key=lambda d: -d["conf"])
    kept = []
    for d in dets:                                   # one box per port
        if all(_iou(d["xyxy"], k["xyxy"]) < 0.5 for k in kept):
            kept.append(d)
    return kept


def raw_detections(bgr):
    """Typed port boxes with connected/empty status: [{xyxy, label, conf, status}]."""
    typed = _detect("port_type_finder.pt", bgr, 960)
    status = _detect("port_connected_or_empty.pt", bgr, 640) if (MODELS / "port_connected_or_empty.pt").exists() else []
    for d in typed:
        best = max(status, key=lambda s: _iou(d["xyxy"], s["xyxy"]), default=None)
        if best is not None and _iou(d["xyxy"], best["xyxy"]) >= 0.3:
            d["status"] = "connected" if best["label"].startswith("Connected") else "empty"
        else:
            d["status"] = "unknown"
    return typed


# ----------------------------------------------------------------------------- layout

def _rows(boxes):
    """Cluster boxes (x, y, w, h) into rows by centre height."""
    rows = []
    for b in sorted(boxes, key=lambda b: b[1] + b[3] / 2):
        cy = b[1] + b[3] / 2
        if rows and abs(cy - rows[-1]["cy"]) < 0.5 * rows[-1]["h"]:
            rows[-1]["boxes"].append(b)
            rows[-1]["cy"] = float(np.mean([q[1] + q[3] / 2 for q in rows[-1]["boxes"]]))
        else:
            rows.append({"cy": cy, "h": b[3], "boxes": [b]})
    return rows


def _grid(boxes):
    """Rows and columns of one port kind. Returns (columns, rows, w, h, pitch) where
    columns are x centres (gaps of 2-3 pitches inside a block filled in) and rows are
    y centres; or None."""
    if not boxes:
        return None
    w = float(np.median([b[2] for b in boxes]))
    h = float(np.median([b[3] for b in boxes]))
    rows = [r for r in _rows(boxes) if len(r["boxes"]) >= 1]
    # at most two rows of ports on a 1U panel; keep the two best-supported, in order
    rows = sorted(sorted(rows, key=lambda r: -len(r["boxes"]))[:2], key=lambda r: r["cy"])
    xs = sorted(b[0] + b[2] / 2 for r in rows for b in r["boxes"])
    cols = []
    for x in xs:                                     # merge the rows' x centres into columns
        if cols and x - np.mean(cols[-1]) < 0.45 * w:
            cols[-1].append(x)
        else:
            cols.append([x])
    cols = [float(np.mean(c)) for c in cols]
    d = np.diff(cols)
    pitch = float(np.median(d[d < 1.6 * w])) if np.any(d < 1.6 * w) else w * 1.1
    # a lone port at either end, set apart from the rest, is a console or management
    # port beside the block, not a data port. A column set apart with a port in both
    # rows is a block of its own (DES-1210-28P: the RJ45 uplink pairs right of the SFP
    # cages), and stays
    def rows_seen(c):
        return sum(any(abs(b[0] + b[2] / 2 - c) < 0.45 * w for b in r["boxes"]) for r in rows)

    def stray(i, j):                                 # end column i, its neighbour j
        lone = len(rows) < 2 or rows_seen(cols[i]) < 2
        return (abs(cols[i] - cols[j]) > 1.25 * pitch and lone) or \
            (len(rows) == 2 and rows_seen(cols[i]) < 2 and rows_seen(cols[j]) == 2)
    while len(cols) > 2 and stray(0, 1):
        cols = cols[1:]
    while len(cols) > 2 and stray(-1, -2):
        cols = cols[:-1]
    # no whole columns are invented: the models find ports with cables in them too, so a
    # column-wide gap with nothing detected in either row is blank panel (many switches
    # leave about one port's width between blocks), not hidden ports
    return cols, [r["cy"] for r in rows], w, h, pitch


def detect_ports(bgr, numbering="column"):
    """Ports found by the trained models, numbered. Returns (ports, info) in the same
    form as switch_fit.layout, plus 'status' (connected / empty / unknown /
    not seen) per port."""
    dets = raw_detections(bgr)
    by_kind = {}
    for d in dets:
        x1, y1, x2, y2 = d["xyxy"]
        kind = DATA_PORTS.get(d["label"])
        if kind:
            by_kind.setdefault(kind if kind != "qsfp" else "sfp", []).append((x1, y1, x2 - x1, y2 - y1, d))
    ports, info = [], {"detector": "RackTrack port models", "detections": len(dets)}
    for kind in ("rj45", "sfp"):
        boxes = by_kind.get(kind, [])
        g = _grid([b[:4] for b in boxes])
        if g is None:
            continue
        cols, rows, w, h, pitch = g
        cells = [(ci, ri) for ci in range(len(cols)) for ri in range(len(rows))] if numbering == "column" \
            else [(ci, ri) for ri in range(len(rows)) for ci in range(len(cols))]
        for ci, ri in cells:
            cx, cy = cols[ci], rows[ri]
            hit = min(boxes, key=lambda b: abs(b[0] + b[2] / 2 - cx) + abs(b[1] + b[3] / 2 - cy), default=None)
            seen = hit is not None and abs(hit[0] + hit[2] / 2 - cx) < 0.5 * w and abs(hit[1] + hit[3] / 2 - cy) < 0.5 * h
            if not seen and len(rows) == 2 and kind == "sfp":
                continue                             # SFP strips are often one row; don't invent a second
            box = [hit[0], hit[1], hit[2], hit[3]] if seen else [cx - w / 2, cy - h / 2, w, h]
            ports.append({"id": len(ports) + 1, "type": kind, "box": [round(v, 1) for v in box],
                          "status": hit[4]["status"] if seen else "not seen",
                          "opening_visible": bool(seen and hit[4]["status"] == "empty"),
                          "confidence": round(hit[4]["conf"], 2) if seen else None})
        if kind == "rj45":
            info.update(rows=len(rows), columns=len(cols), pitch_px=round(pitch, 1), blocks=[len(cols)])
    for d in dets:                                   # console, management, USB: by name
        name = NAMED_PORTS.get(d["label"])
        if name:
            x1, y1, x2, y2 = d["xyxy"]
            ports.append({"id": name, "type": d["label"].lower(), "box": [round(x1, 1), round(y1, 1), round(x2 - x1, 1), round(y2 - y1, 1)],
                          "status": d["status"], "opening_visible": d["status"] == "empty", "confidence": round(d["conf"], 2)})
    info.update(rj45=sum(p["type"] == "rj45" for p in ports), sfp=sum(p["type"] == "sfp" for p in ports),
                seen=sum(p["status"] != "not seen" for p in ports),
                connected=sum(p["status"] == "connected" for p in ports),
                candidates=len(dets))
    return ports, info


# ----------------------------------------------------------------------------- templates from vendor photos

def front_panel(bgr):
    """Crop a vendor product photo to the front panel only: the band around the detected
    port rows (a 1U face is a little taller than its two port rows), as wide as the
    switch body in that band. Drops the top surface of angled shots, the background and
    other devices. Returns (crop, (x0, y0)) or None if no ports are found."""
    dets = [d for d in raw_detections(bgr) if d["label"] in DATA_PORTS]
    if len(dets) < 4:
        return None
    ph = float(np.median([d["xyxy"][3] - d["xyxy"][1] for d in dets]))
    # stacked switches or product families in one photo: keep the switch (one or two
    # adjacent port rows) with the most ports
    rows = _rows([(d["xyxy"][0], d["xyxy"][1], d["xyxy"][2] - d["xyxy"][0], d["xyxy"][3] - d["xyxy"][1], d) for d in dets])
    best = max(([r] for r in rows), key=lambda g: len(g[0]["boxes"]))
    for a, b in zip(rows, rows[1:]):
        if b["cy"] - a["cy"] < 1.6 * ph and len(a["boxes"]) + len(b["boxes"]) > sum(len(r["boxes"]) for r in best):
            best = [a, b]
    dets = [q[4] for r in best for q in r["boxes"]]
    ys1 = [d["xyxy"][1] for d in dets]
    ys2 = [d["xyxy"][3] for d in dets]
    H, W = bgr.shape[:2]
    y0 = int(max(0, min(ys1) - 0.7 * ph))
    y1 = int(min(H, max(ys2) + 0.7 * ph))
    band = bgr[y0:y1].astype(np.int16)
    # background = the colour at the photo's left and right edges in this band
    bg = np.median(np.concatenate([band[:, :3].reshape(-1, 3), band[:, -3:].reshape(-1, 3)]), axis=0)
    body = (np.abs(band - bg).max(axis=2) > 30).mean(axis=0) > 0.5
    px0 = int(min(d["xyxy"][0] for d in dets))
    px1 = int(max(d["xyxy"][2] for d in dets))
    x0, x1 = px0, px1
    while x0 > 0 and body[x0 - 1]:                 # grow from the ports to the panel edges
        x0 -= 1
    while x1 < W - 1 and body[x1 + 1]:
        x1 += 1
    return bgr[y0:y1, x0:x1 + 1].copy(), (x0, y0)


def build_template(bgr, out_dir, vendor, model, source="", numbering="column", face_h=180):
    """template.json + front.png (front panel only) from a vendor photo, with ports
    found by the trained models. Same format as hand-made templates."""
    import cv2
    import json
    fp = front_panel(bgr)
    if fp is None:
        raise ValueError("fewer than 4 ports detected in the vendor image")
    crop, _ = fp
    s = face_h / crop.shape[0]
    crop = cv2.resize(crop, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA)
    ports, info = detect_ports(crop, numbering)
    if info["rj45"] + info["sfp"] < 4:
        raise ValueError(f"only {info['rj45']} RJ45 + {info['sfp']} SFP found on the front panel")
    rj = [p["box"] for p in ports if p["type"] == "rj45"]
    pad = int(np.median([b[3] for b in rj]) * 0.25) if rj else 0
    ignore = [[int(min(b[0] for b in rj)) - pad, int(min(b[1] for b in rj)) - pad,
               int(max(b[0] + b[2] for b in rj) - min(b[0] for b in rj)) + 2 * pad,
               int(max(b[1] + b[3] for b in rj) - min(b[1] for b in rj)) + 2 * pad]] if rj else []
    H, W = crop.shape[:2]
    meta = {
        "vendor": vendor, "model": model, "description": "",
        "summary": f"{info['rj45']}x RJ45 | {info['sfp']}x SFP | front panel cut from the vendor photo, "
                   f"ports found by the RackTrack port models",
        "image": "front.png", "image_source": source,
        "auto_generated": True, "numbering_scheme": "top-odd" if numbering == "column" else "row",
        "match_regions": [[0, 0, W, H]], "ignore_regions": ignore,
        "ports": [{"id": p["id"], "type": p["type"] if p["type"] in ("rj45", "sfp") else "console" if p["id"] == "Console" else p["type"],
                   "poe": None, "box": [int(round(v)) for v in p["box"]]} for p in ports],
    }
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_dir / "front.png"), crop)
    (out_dir / "template.json").write_text(json.dumps(meta, indent=1))
    return meta
