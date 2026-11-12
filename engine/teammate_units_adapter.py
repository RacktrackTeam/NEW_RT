"""Adapters that run the teammate's unchanged code (rack_units.py, more_datailed_1.py) and return
rows in the unit_grid format, so scripts/units/eval_unit_logic.py can grade them like every other approach."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))   # src/: rack_units.py and more_datailed_1.py (teammate code, unchanged)
import rack_units as R            # noqa: E402
import more_datailed_1 as MD      # noqa: E402


def _rows(boxes):
    """boxes top to bottom: [(x1, y1, x2, y2)] -> unit_grid rows, u01 at the bottom."""
    n = len(boxes)
    out = []
    for i, (x1, y1, x2, y2) in enumerate(boxes):
        out.append({"label": f"u{n - i:02d}", "box": [int(x1), int(round(y1)), int(x2), int(round(y2))],
                    "center": [int((x1 + x2) / 2), int((y1 + y2) / 2)], "center_y": float((y1 + y2) / 2)})
    return out


def full_map(path):
    """more_datailed_1.build_map: every U slot from the rack's top to its bottom."""
    out, _ = MD.build_map(str(path))
    boxes = sorted((s["bbox"] for s in out["slots"]), key=lambda b: b[1])
    rows = _rows(boxes)
    info = {"ok": bool(rows), "stage": "mate_map", "pitch": out["u_pitch_px"],
            "x_left": boxes[0][0] if boxes else None, "x_right": boxes[0][2] if boxes else None,
            "note": f"top from {out['rack']['top_limit_from']}, bottom from {out['rack']['bottom_limit_from']}"}
    return rows, info


def segments(path):
    """rack_units.analyse: devices and empty slots, each split into its units (frames and irregular blocks dropped)."""
    res, d = R.analyse(str(path))
    U = res.u_pitch_px
    boxes = []
    for s in res.segments:
        if s["kind"] not in ("device", "empty"):
            continue
        n = max(1, s["units"])
        x1, _, x2, _ = s["bbox"]
        for k in range(n):
            t = s["top"] + (s["bottom"] - s["top"]) * k / n
            b = s["top"] + (s["bottom"] - s["top"]) * (k + 1) / n
            boxes.append((x1, t, x2, b))
    boxes.sort(key=lambda b: b[1])
    rows = _rows(boxes)
    info = {"ok": bool(rows), "stage": "mate_seg", "pitch": U,
            "x_left": boxes[0][0] if boxes else None, "x_right": boxes[0][2] if boxes else None}
    return rows, info
