"""Read one zone close-up: the labels on the fibres and the ports they sit in.

A zone is a run of ports on one device, from port `from_no` to port `to_no`,
photographed from 20 to 30 cm so every label is sharp. This module:

  1. finds the ports in the close-up with the port models the rack scan uses
     (config.json models.ports_typed for the type, models.ports_status for
     cable or no cable), through pipeline.port_pattern;
  2. reads every piece of text with the label reader the rack scan uses
     (pipeline.ocr_labels, EasyOCR);
  3. numbers the ports left to right, top row first, starting at `from_no`;
  4. pairs each label with the nearest port, one label to one port.

The numbering and pairing (order_ports, pair_labels, number_zone) are pure
functions of boxes, so they are tested with synthetic boxes and a synthetic
image in pipeline/tests/test_zone_read.py; the models only supply the boxes.

Output (all boxes are [x, y, w, h] in close-up pixels):

    {
      "ok": true,
      "image_size": {"w": 4032, "h": 3024},
      "labels": [{"text", "confidence", "portNo" | null, "box",
                  "between": [port, port] (only on a label left between two ports)}],
      "ports":  [{"portNo", "box", "state": "connected" | "empty", "sure",
                  "cap_state", "cap_reason"}],
      "portsFrom": "model" | "labels" | "none",
      "extraPorts": 0
    }

A close-up is where a dust cap can be seen, so every port also carries the
dust-cap read of pipeline/cap_check.py and, on empty ports, the model team's
classifier (pipeline/cap_model.py, cap_source "model") (cap_state connected | empty | capped |
uncertain | too_small, and cap_reason in plain words) when that read ran.

When the port model finds no port at all, the labels themselves are numbered
in reading order (portsFrom "labels"), so a close-up of a panel the model does
not know still gives every label a port number. That is a fallback, and the
answer says so.

Run as a worker command (pipeline/worker.py "zone_read") or on its own:

    python -m pipeline.zone_read --image close.jpg --from 1 --to 12 --config config.json
"""

from __future__ import annotations

import os
import argparse
import json
import re
import statistics
import sys

# A label a person reads is at least two characters, and a bare port number
# printed on the faceplate ("7", "12", "048") is not a cable label.
_SILKSCREEN_NUMBER = re.compile(r"^\d{1,3}$")

# How far a label may sit from the port it belongs to, in port heights. A flag
# label on a fibre hangs a little below the connector; a label further away
# than this belongs to no port in the picture.
MAX_PAIR_DISTANCE = 4.0

# A label hanging between two ports goes to the nearer only when it is
# clearly nearer: its gap to that port under this share of its gap to the
# other, measured along the row (across the rows for two ports one above the
# other). Otherwise it is left unplaced and said to be between the two.
BETWEEN_RATIO = 0.6


def two_nearest(ports, lab, limit):
    """The label's two nearest ports within `limit` as (d, pi) pairs, nearest first."""
    lx, ly = _center(lab["box"])
    near = []
    for pi, port in enumerate(ports):
        px, py = _center(port["box"])
        d = ((lx - px) ** 2 + (ly - py) ** 2) ** 0.5
        if d <= limit:
            near.append((d, pi))
    near.sort()
    return near[:2]


def is_between(ports, lab, unit, ratio=BETWEEN_RATIO):
    """(pi1, pi2) when the label sits between its two nearest ports with no
    clear winner, else None. The gap is measured along the row when the two
    ports share a row, across the rows when one is above the other, so a
    label hanging straight under a port is that port's however far it hangs."""
    near = two_nearest(ports, lab, MAX_PAIR_DISTANCE * unit)
    if len(near) < 2:
        return None
    (_d1, p1), (_d2, p2) = near
    lx, ly = _center(lab["box"])
    c1, c2 = _center(ports[p1]["box"]), _center(ports[p2]["box"])
    same_row = abs(c1[1] - c2[1]) <= 0.6 * unit
    axis = 0 if same_row else 1
    g1 = abs((lx, ly)[axis] - c1[axis])
    g2 = abs((lx, ly)[axis] - c2[axis])
    if g2 <= 0:
        return (p1, p2)
    return (p1, p2) if g1 >= ratio * g2 else None


def _xyxy(box):
    x1, y1, x2, y2 = (float(v) for v in box)
    return x1, y1, x2, y2


def _center(box):
    x1, y1, x2, y2 = _xyxy(box)
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0


def _xywh(box):
    x1, y1, x2, y2 = _xyxy(box)
    return [int(round(x1)), int(round(y1)), int(round(x2 - x1)), int(round(y2 - y1))]


def order_ports(ports):
    """Ports in reading order: rows top first, each row left to right.

    ports: dicts with "box" [x1, y1, x2, y2]. A new row starts when a port's
    centre sits more than 0.6 of the median port height below the row being
    built. Returns a new list; the dicts are not changed.
    """
    if not ports:
        return []
    heights = [max(1.0, _xyxy(p["box"])[3] - _xyxy(p["box"])[1]) for p in ports]
    tol = 0.6 * statistics.median(heights)
    by_y = sorted(ports, key=lambda p: _center(p["box"])[1])
    rows = []
    for p in by_y:
        cy = _center(p["box"])[1]
        if rows:
            row = rows[-1]
            mean = sum(_center(q["box"])[1] for q in row) / len(row)
            if abs(cy - mean) <= tol:
                row.append(p)
                continue
        rows.append([p])
    out = []
    for row in rows:
        out.extend(sorted(row, key=lambda p: _center(p["box"])[0]))
    return out


def pair_labels(ports, labels, max_distance=MAX_PAIR_DISTANCE, between=None):
    """Pair each label with the nearest port, one to one.

    ports, labels: dicts with "box" [x1, y1, x2, y2]. The closest pairs are
    taken first; a port takes one label and a label one port. A pair further
    apart than max_distance port heights is not made. A label that sits
    between two ports with no clear winner (is_between) is paired last: with
    the one of its two ports still free, and when both are free with
    neither; it is then written into `between` (a dict, when given) as
    {label index: (port index, port index)}. Returns {label index: port index}.
    """
    if not ports or not labels:
        return {}
    heights = [max(1.0, _xyxy(p["box"])[3] - _xyxy(p["box"])[1]) for p in ports]
    unit = statistics.median(heights)
    limit = max_distance * unit
    unsure = {}
    for li, lab in enumerate(labels):
        b = is_between(ports, lab, unit)
        if b is not None:
            unsure[li] = b
    pairs = []
    for li, lab in enumerate(labels):
        if li in unsure:
            continue
        lx, ly = _center(lab["box"])
        for pi, port in enumerate(ports):
            px, py = _center(port["box"])
            d = ((lx - px) ** 2 + (ly - py) ** 2) ** 0.5
            if d <= limit:
                pairs.append((d, li, pi))
    pairs.sort()
    used_l, used_p, out = set(), set(), {}
    for _d, li, pi in pairs:
        if li in used_l or pi in used_p:
            continue
        used_l.add(li)
        used_p.add(pi)
        out[li] = pi
    for li, (p1, p2) in unsure.items():
        free = [p for p in (p1, p2) if p not in used_p]
        if len(free) == 1:
            used_p.add(free[0])
            out[li] = free[0]
        elif len(free) == 2 and between is not None:
            between[li] = (p1, p2)
    return out


def keep_label(text):
    """False for text that is not a cable label (a port number on the faceplate)."""
    t = (text or "").strip()
    return len(t) >= 2 and not _SILKSCREEN_NUMBER.match(t)


def _state(status, has_label):
    """The contract's two states. A port with a label on it has a cable in it."""
    if status == "connected":
        return "connected", True
    if status == "empty":
        return ("connected", False) if has_label else ("empty", True)
    return ("connected", False) if has_label else ("empty", False)


def number_zone(ports, labels, from_no, to_no):
    """Number the ports of one zone and give each label its port number.

    ports:  [{"box": [x1,y1,x2,y2], "status": "connected"|"empty"|"unknown"}]
    labels: [{"box": [x1,y1,x2,y2], "text": str, "conf": float}]
    Returns the "labels", "ports", "portsFrom" and "extraPorts" of the answer.
    """
    from_no = int(from_no)
    to_no = int(to_no)
    want = max(0, to_no - from_no + 1)
    labels = [lab for lab in labels if keep_label(lab.get("text"))]
    ordered = order_ports(ports)
    extra = max(0, len(ordered) - want)
    ordered = ordered[:want]

    if ordered:
        unsure = {}
        pairing = pair_labels(ordered, labels, between=unsure)
        port_of = {li: from_no + pi for li, pi in pairing.items()}
        between_of = {li: sorted((from_no + a, from_no + b)) for li, (a, b) in unsure.items()}
        labelled = set(pairing.values())
        out_ports = []
        for i, p in enumerate(ordered):
            state, sure = _state(p.get("status"), i in labelled)
            cap = {k: p[k] for k in ("cap_state", "cap_reason") if p.get(k)}
            out_ports.append({"portNo": from_no + i, "box": _xywh(p["box"]), "state": state, "sure": sure, **cap})
        source = "model"
    else:
        # No port found: number the labels themselves in reading order.
        ordered_labels = order_ports(labels)[:want]
        index = {id(lab): i for i, lab in enumerate(ordered_labels)}
        port_of = {li: from_no + index[id(lab)] for li, lab in enumerate(labels) if id(lab) in index}
        out_ports = []
        between_of = {}
        source = "labels" if ordered_labels else "none"

    out_labels = []
    for li, lab in enumerate(labels):
        out_labels.append(
            {
                "text": str(lab.get("text") or "").strip(),
                "confidence": round(float(lab.get("conf", lab.get("confidence", 0.0)) or 0.0), 3),
                "portNo": port_of.get(li),
                "box": _xywh(lab["box"]),
                **({"between": between_of[li]} if li in between_of else {}),
                **({"end": lab["end"]} if lab.get("end") else {}),
            }
        )
    out_labels.sort(key=lambda lab: (lab["portNo"] is None, lab["portNo"] or 0, lab["box"][0]))
    return {"labels": out_labels, "ports": out_ports, "portsFrom": source, "extraPorts": extra}


# ── The models (only these two functions touch weights) ───────────────


def detect_ports(img, config):
    """Port boxes in the close-up with the rack scan's port models."""
    from pipeline.detection import load_model
    from pipeline.port_pattern import classify_ports_by_pattern

    models = config.get("models", {})
    det = config.get("detection", {})
    type_model = load_model(models["ports_typed"])
    status_model = load_model(models["ports_status"]) if models.get("ports_status") else None
    sfp_rel = models.get("sfp_type")
    sfp_model = load_model(sfp_rel) if sfp_rel and os.path.exists(sfp_rel) else None
    got = classify_ports_by_pattern(
        img, type_model, conf=float(det.get("ports_conf", 0.23)), status_model=status_model,
        sfp_model=sfp_model, sfp_conf=float(det.get("sfp_conf", 0.30)),
    )
    found = [p for key in ("main_ports", "sfp_ports", "console_ports", "other_ports") for p in (got.get(key) or [])]
    # Is an empty port open or under a dust cap (pipeline/cap_check.py). A
    # failure leaves cap_state off the ports; the close-up is still read.
    try:
        from pipeline.cap_check import check_caps
        from pipeline.cap_model import apply_cap_model

        check_caps(img, found)
        # Then the model team's classifier on every empty port, when its
        # weights are there (pipeline/cap_model.py).
        apply_cap_model(img, found, config)
    except Exception as e:
        sys.stderr.write(f"zone_read: cap check skipped: {type(e).__name__}: {e}\n")
    out = []
    for p in found:
        port = {"box": p["box"], "status": p.get("status", "unknown"), "type": p.get("class_name")}
        for k in ("cap_state", "cap_reason"):
            if p.get(k):
                port[k] = p[k]
        out.append(port)
    return out


def read_text(image_path):
    """Every piece of text in the close-up, as [{"box": xyxy, "text", "conf"}].

    Two readers: the upright reader the rack scan uses, and the cable flag
    reader (pipeline/cable_flags.py), which reads the tags hanging on the
    cables at 90 degrees and gives each line its device, port and position
    ("end"). A flag line that the upright reader also caught is kept once,
    the flag reader's version first, since it carries the parse.
    """
    from pipeline.ocr_labels import extract_labels

    out = []
    try:
        from pipeline.cable_flags import read_flags, same_print

        flags = read_flags(image_path)
        for ln in flags.get("lines", []):
            if not ln.get("end"):
                continue
            out.append({"box": list(ln["box"]), "text": ln["text"], "conf": ln["conf"], "raw": ln.get("raw"), "end": ln["end"]})
    except Exception as e:  # the close-up is still read the old way
        sys.stderr.write(f"zone_read: cable flags skipped: {type(e).__name__}: {e}\n")
        same_print = None

    got = extract_labels(image_path, min_conf=0.2)
    for lab in got.get("labels", []):
        b = lab["bbox"]
        item = {
            "box": [b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]],
            "text": lab["text"],
            "conf": lab["conf"],
        }
        if same_print is not None and any(same_print(item, o) for o in out):
            continue
        out.append(item)
    return out


def read_zone(image_path, from_no, to_no, config_path="config.json", detect=None, reader=None):
    """Read one close-up. `detect` and `reader` replace the models (tests)."""
    import cv2

    img = cv2.imread(image_path)
    if img is None:
        return {"ok": False, "error": "The close-up could not be read as an image."}
    h, w = img.shape[:2]
    if detect is None:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
        ports = detect_ports(img, config)
    else:
        ports = detect(img)
    labels = reader(image_path) if reader is not None else read_text(image_path)
    out = number_zone(ports, labels, from_no, to_no)
    return {"ok": True, "image_size": {"w": w, "h": h}, **out}


def main():
    ap = argparse.ArgumentParser(description="Read one zone close-up.")
    ap.add_argument("--image", required=True)
    ap.add_argument("--from", dest="from_no", type=int, required=True)
    ap.add_argument("--to", dest="to_no", type=int, required=True)
    ap.add_argument("--config", default="config.json")
    args = ap.parse_args()
    try:
        out = read_zone(args.image, args.from_no, args.to_no, args.config)
    except Exception as e:  # always answer JSON so the server can parse it
        out = {"ok": False, "error": str(e)}
    sys.stdout.write(json.dumps(out))
    sys.stdout.flush()
    sys.exit(0 if out.get("ok") else 1)


if __name__ == "__main__":
    main()
