"""Auto mode port detection: every port on a device cut, with its type, from the 13-class port detector.

Stage 1 of the team's port detection pipeline (deliveries/port_detection/port_detection.py, 29 Oct 2026), as a function: the merged port model
(the same weights the switch agent ships) run at its training size, then the pipeline's own clean-up - class-agnostic
NMS at IoU 0.45 (ports sit edge to edge, so the default 0.70 keeps two boxes on one jack), a confidence floor of 0.25,
the "empty" class dropped (it marks a vacant cage and fires on LEDs, vents and lettering), and containment dedupe
(a box that swallows a smaller one is the same detection even at low IoU; a box over two or more is a spanning box).

Used when no vendor layout fits (the agent's auto mode): the ports it finds replace the agent's grid, so the app
shows every port with its type - RJ45, SFP, QSFP, LC / SC / FC, console, management, USB, aux.

    detect(bgr)                      -> [{"type": "RJ45", "conf": 0.91, "box": [x1, y1, x2, y2]}, ...]
    ports_from(dets)                 -> the app's port list: numbered data ports, named console / management / USB
    draw(bgr, dets)                  -> the cut with every port outlined, runs of one type labelled
"""
import pathlib
import threading

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODEL = ROOT / "models" / "ports" / "best_merged_ports.pt"
IMGSZ = 960                         # the training size
CONF_RAW = 0.10                     # what the model is asked for; everything reported clears CONF
CONF = 0.25
NMS_IOU = 0.45
AGNOSTIC_NMS = True
CONTAIN_RATIO = 0.80                # a box that swallows this much of a smaller box duplicates it
DROP_CLASSES = {"empty"}
MIN_PORTS = 2                       # fewer finds than this is no layout

DATA_PORTS = ("RJ45", "SFP", "QSFP", "LC", "SC", "FC")                 # numbered: RJ45 first, then the cages and fibre
NAMED_PORTS = {"CONSOLE": "Console", "MANAGEMENT_PORT": "Mgmt", "USB_A": "USB", "USB_B": "USB", "USB_C": "USB", "AUX": "Aux"}
SFP_LIKE = {"SFP", "QSFP", "LC", "SC", "FC"}                           # drawn and cabled like SFP

PORT_COLORS = {
    "RJ45": "#2a78d6", "SFP": "#d6399b", "QSFP": "#17a98a", "OSFP": "#7b52cc",
    "CONSOLE": "#29a3c4", "USB_A": "#e34948", "USB_B": "#b06b1f",
    "USB_C": "#8a6bd1", "LC": "#5f8f2a", "SC": "#a08a2a", "FC": "#2a8f7a",
    "AUX": "#c2561f", "MANAGEMENT_PORT": "#7a8794",
}
FONT = cv2.FONT_HERSHEY_SIMPLEX

STATUS_MODEL = ROOT / "engine" / "port_agents" / "switch_agent" / "port_status.pt"    # the switch agent's connected / empty model, read only
STATUS_IOU = 0.3

_model = [None]
_status = [None]
_lock = threading.Lock()


def available():
    import importlib.util
    return MODEL.exists() and importlib.util.find_spec("ultralytics") is not None


def _get_model():
    if _model[0] is None:
        from ultralytics import YOLO
        _model[0] = YOLO(str(MODEL))
    return _model[0]


def _get_status_model():
    if _status[0] is None and STATUS_MODEL.exists():
        from ultralytics import YOLO
        _status[0] = YOLO(str(STATUS_MODEL))
    return _status[0]


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy; u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def statuses(bgr, ports):
    """connected / empty per port from the status model (the box overlapping the port most, IoU 0.3, or whose centre
    lies inside it); ports it does not see keep status None. RT-EOS's own port status, read later, overrides this."""
    m = _get_status_model()
    if m is None:
        return ports
    with _lock:
        r = m.predict(bgr, imgsz=IMGSZ, conf=0.25, device="cpu", verbose=False)[0]
    boxes = [{"box": [float(v) for v in b], "conf": float(c), "name": r.names[int(k)]}
             for b, c, k in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist(), r.boxes.cls.tolist())]
    for p in ports:
        xs = [q[0] for q in p["polygon"]]; ys = [q[1] for q in p["polygon"]]; box = [min(xs), min(ys), max(xs), max(ys)]
        best = max(boxes, key=lambda s: _iou(box, s["box"]), default=None)
        if best is None or _iou(box, best["box"]) < STATUS_IOU:
            inside = [s for s in boxes if box[0] <= (s["box"][0] + s["box"][2]) / 2 <= box[2] and box[1] <= (s["box"][1] + s["box"][3]) / 2 <= box[3]]
            best = max(inside, key=lambda s: s["conf"], default=None)
        p["status"] = ("connected" if "onnect" in best["name"] else "empty") if best else None
    return ports


def contained_pairs(boxes):
    """Indices to drop because they duplicate another box by containment: a box that swallows two or more smaller
    boxes is a spanning box; of a simple nested pair the less confident one goes."""
    n = len(boxes)
    drop, swallows = set(), {i: 0 for i in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
            ax1, ay1, ax2, ay2, ac = boxes[i]
            bx1, by1, bx2, by2, bc = boxes[j]
            iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
            ih = max(0.0, min(ay2, by2) - max(ay1, by1))
            inter = iw * ih
            if inter <= 0:
                continue
            aa = (ax2 - ax1) * (ay2 - ay1)
            bb = (bx2 - bx1) * (by2 - by1)
            if inter / min(aa, bb) < CONTAIN_RATIO:
                continue
            swallows[i if aa >= bb else j] += 1
            drop.add(i if ac < bc else j)
    drop |= {i for i, k in swallows.items() if k >= 2}
    return drop


def detect(bgr):
    """Every port on the picture: [{"type", "conf", "box": [x1, y1, x2, y2]}], the pipeline's clean-up applied."""
    with _lock:
        r = _get_model().predict(bgr, imgsz=IMGSZ, conf=CONF_RAW, device="cpu", iou=NMS_IOU,
                                 agnostic_nms=AGNOSTIC_NMS, verbose=False)[0]
    names = r.names
    n = len(r.boxes)
    cls = [names[int(r.boxes.cls[j])] for j in range(n)]
    confs = [float(r.boxes.conf[j]) for j in range(n)]
    xyxy = [[float(v) for v in r.boxes.xyxy[j].tolist()] for j in range(n)]
    passing = [j for j in range(n) if cls[j] not in DROP_CLASSES and confs[j] >= CONF]
    sup = contained_pairs([xyxy[j] + [confs[j]] for j in passing])
    kept = [passing[k] for k in range(len(passing)) if k not in sup]
    return [{"type": cls[j], "conf": round(confs[j], 3), "box": [round(v, 1) for v in xyxy[j]]} for j in kept]


def _columns(dets):
    """Ports in column order - 1 top left, 2 below it, 3 top of the next column - as the switch ports are numbered."""
    if not dets:
        return []
    w = float(np.median([d["box"][2] - d["box"][0] for d in dets]))
    cols = []
    for d in sorted(dets, key=lambda d: (d["box"][0] + d["box"][2]) / 2):
        cx = (d["box"][0] + d["box"][2]) / 2
        for c in cols:
            if abs(cx - c["x"]) < 0.5 * w:
                c["items"].append(d); c["x"] = (c["x"] * (len(c["items"]) - 1) + cx) / len(c["items"]); break
        else:
            cols.append({"x": cx, "items": [d]})
    out = []
    for c in cols:
        out += sorted(c["items"], key=lambda d: d["box"][1])
    return out


def ports_from(dets):
    """The app's port list from the detections: data ports numbered in column order (RJ45 first, then SFP / QSFP /
    fibre), console / management / USB / aux by name; type = the detector's class, lower case."""
    ports = []
    rj = _columns([d for d in dets if d["type"] == "RJ45"])
    cages = _columns([d for d in dets if d["type"] in DATA_PORTS and d["type"] != "RJ45"])
    for d in rj + cages:
        x1, y1, x2, y2 = d["box"]
        ports.append({"id": len(ports) + 1, "type": d["type"].lower(), "confidence": d["conf"],
                      "polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]})
    seen = {}
    for d in sorted((d for d in dets if d["type"] in NAMED_PORTS), key=lambda d: d["box"][0]):
        name = NAMED_PORTS[d["type"]]; seen[name] = seen.get(name, 0) + 1
        x1, y1, x2, y2 = d["box"]
        ports.append({"id": name if seen[name] == 1 else f"{name} {seen[name]}", "type": d["type"].lower(), "confidence": d["conf"],
                      "polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]})
    return ports


def _bgr(hex_color):
    h = hex_color.lstrip("#")
    return (int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16))


def _group_runs(dets):
    """Adjacent same-type boxes on one row, as one labelled run."""
    groups = []
    for d in sorted(dets, key=lambda d: (d["type"], d["box"][1], d["box"][0])):
        x1, y1, x2, y2 = d["box"]; dh, dw = y2 - y1, x2 - x1
        for g in groups:
            if g["type"] != d["type"]:
                continue
            ov = min(g["y2"], y2) - max(g["y1"], y1)
            if ov <= 0.4 * min(g["y2"] - g["y1"], dh):
                continue
            if x1 - g["x2"] > 1.3 * dw:
                continue
            g["x1"], g["y1"], g["x2"], g["y2"] = min(g["x1"], x1), min(g["y1"], y1), max(g["x2"], x2), max(g["y2"], y2)
            g["n"] += 1
            break
        else:
            groups.append({"type": d["type"], "n": 1, "x1": x1, "y1": y1, "x2": x2, "y2": y2})
    return groups


def _label(img, text, x, y_top, y_bot, col, scale, thick):
    (tw, th), _ = cv2.getTextSize(text, FONT, scale, thick)
    pad = max(3, int(th * 0.35))
    y = int(y_top - pad)
    if y - th < 0:
        y = int(min(img.shape[0] - 2, y_bot + th + pad))
    x = int(max(1, min(x, img.shape[1] - tw - 2)))
    cv2.putText(img, text, (x, y), FONT, scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
    cv2.putText(img, text, (x, y), FONT, scale, col, thick, cv2.LINE_AA)


def draw(bgr, dets):
    """The cut with every port outlined in its type's colour and each run of one type labelled ("RJ45 x24")."""
    img = bgr.copy(); h, w = img.shape[:2]
    t = 2 if max(w, h) >= 900 else 1
    for d in dets:
        x1, y1, x2, y2 = map(int, d["box"])
        cv2.rectangle(img, (x1, y1), (x2, y2), _bgr(PORT_COLORS.get(d["type"], "#ffffff")), t)
    scale = max(0.34, min(0.62, w / 2600)); thick = 1 if w < 1400 else 2
    for g in _group_runs(dets):
        text = g["type"] if g["n"] == 1 else f'{g["type"]} x{g["n"]}'
        _label(img, text, g["x1"], g["y1"], g["y2"], _bgr(PORT_COLORS.get(g["type"], "#ffffff")), scale, thick)
    return img


def auto_layout(photo, out_dir=None):
    """Detect, number and draw on one cut photo: {"ports", "dets", "by_type", "picture"} or None when fewer than
    MIN_PORTS ports are found (or the model is not available)."""
    if not available():
        return None
    bgr = cv2.imread(str(photo))
    if bgr is None:
        return None
    dets = detect(bgr)
    if len(dets) < MIN_PORTS:
        return None
    ports = statuses(bgr, ports_from(dets))
    pic = None
    if out_dir is not None:
        pic = pathlib.Path(out_dir) / "auto_ports.jpg"
        cv2.imwrite(str(pic), draw(bgr, dets), [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    by_type = {}
    for d in dets:
        by_type[d["type"].lower()] = by_type.get(d["type"].lower(), 0) + 1
    return {"ports": ports, "dets": dets, "by_type": by_type, "picture": str(pic) if pic else None}
