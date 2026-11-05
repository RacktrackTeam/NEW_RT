"""Rack-unit ladder - the SAME logic RT-EOS uses to identify units.

Copied verbatim from RT-EOS `pipeline/detection.py` (`shrink_for_inference`,
`_unit_rows_from_result`, `_drop_duplicate_units`, `_cap_at_rails`,
`ladder_from_unit_boxes`, `build_unit_grid_from_model` and their constants), so
that this project reads a rack's rows exactly as the production pipeline does.
Keep it in sync with RT-EOS when that file changes; `pipeline/tests/
test_unit_grid_model.py` there pins every rule below.

Input is a BGR numpy image (cv2 convention), as in RT-EOS. Output rows are
full rack width, contiguous, labelled u01 at the BOTTOM.
"""
import os

import cv2
import numpy as np
from ultralytics import YOLO

_MODEL_CACHE = {}


def load_model(model_path: str):
    """Load a YOLO model; cached so a persistent worker pays the load cost once."""
    cached = _MODEL_CACHE.get(model_path)
    if cached is not None:
        return cached
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model file not found: {model_path}")
    model = YOLO(model_path)
    _MODEL_CACHE[model_path] = model
    return model


# ── Rack-level inference size ──────────────────────────────────
#
# A phone photo is 4032x3024 or larger, and YOLO shrinks it to 640 in one linear
# step. The rack-level models (devices, units) are therefore run on a copy first
# shrunk by a whole factor with area averaging - long side at most about 1000px -
# and their boxes are multiplied back to the original.
INFERENCE_LONG_SIDE = 1000


def shrink_for_inference(img, target=INFERENCE_LONG_SIDE):
    """A copy with the long side divided by a whole factor, and the factor.

    3000x2000 becomes 1000x666 with factor 3. Anything under twice the target is
    returned as it is with factor 1, so a normal photo is not touched.
    """
    h, w = img.shape[:2]
    factor = max(h, w) // target
    if factor < 2:
        return img, 1
    small = cv2.resize(
        img, (max(1, w // factor), max(1, h // factor)), interpolation=cv2.INTER_AREA
    )
    return small, factor


# ── Unit grid: YOLO unit model + contiguous post-processing ────

# The units model's own class names (weights/rack_unit_finder.pt).
UNIT_CLASS = "rackunit"
RACK_CLASS = "rack"
RAIL_CLASS = "rail"

# A detection this much taller than the median is not one unit. The Rack class
# is a whole-frame box and is excluded by name, but a RackUnit that swallowed
# several rows would otherwise set the pitch for everything below it.
_UNIT_TALL_FACTOR = 2.5
# Two boxes covering this much of the shorter one are the same unit found twice.
_UNIT_DUP_OVERLAP = 0.6
# A hole smaller than this fraction of a unit is a detection edge, not a row.
_UNIT_HOLE_MIN = 0.5
# Rack frame visible beyond the outermost unit by this much is another row.
_UNIT_EXTEND_MIN = 0.6
_UNIT_EXTEND_MAX = 2            # rows added past the model's outermost box at each end: a dark PDU at the floor, never the plinth and the floor (testers, 4 Nov 2026)

# The tallest rack anybody builds is 58U (a 48U cabinet plus its extras), so a
# ladder taller than this is a failed estimate rather than a tall rack.
MAX_RACK_UNITS = 58


def _unit_rows_from_result(result, names):
    """Split one prediction into (unit rows, rack boxes, rail boxes).

    Everything here is in the image's pixel space. Classes are matched by NAME,
    not index, so retraining that reorders the classes cannot silently turn the
    rack frame into a unit.
    """
    units, racks, rails = [], [], []
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return units, racks, rails
    for b in boxes:
        name = str(names.get(int(b.cls[0].item()), "")).strip().lower()
        x1, y1, x2, y2 = (float(v) for v in b.xyxy[0].tolist())
        conf = float(b.conf[0].item())
        if name == UNIT_CLASS:
            units.append({"box": [x1, y1, x2, y2], "confidence": conf})
        elif name == RACK_CLASS:
            racks.append([x1, y1, x2, y2])
        elif name == RAIL_CLASS:
            rails.append([x1, y1, x2, y2])
    return units, racks, rails


def _drop_duplicate_units(units):
    """Two detections on one physical row collapse to the more confident one.

    Measured on the vertical axis only. A unit is a full width band, so two
    bands that share most of their height are the same row however their left
    and right edges landed.
    """
    kept = []
    for u in sorted(units, key=lambda d: d["confidence"], reverse=True):
        y1, y2 = u["box"][1], u["box"][3]
        h = max(1.0, y2 - y1)
        dup = False
        for k in kept:
            ky1, ky2 = k["box"][1], k["box"][3]
            overlap = min(y2, ky2) - max(y1, ky1)
            if overlap > 0 and overlap / min(h, max(1.0, ky2 - ky1)) > _UNIT_DUP_OVERLAP:
                dup = True
                break
        if not dup:
            kept.append(u)
    return sorted(kept, key=lambda d: d["box"][1])


def build_unit_grid_from_model(img, unit_model_path, conf=0.25, imgsz=768):
    """The rack's units as the units model read them, rather than a uniform
    grid inferred from how tall the equipment happens to be.

    What the model gives and what this does with it:
      - RackUnit boxes, which overlap each other by a few pixels and very
        occasionally miss a row. Duplicates are collapsed, the overlaps are
        split down the middle so the ladder is contiguous WITHOUT forcing every
        row to the same height, and a hole worth a whole row is filled in.
      - A Rack box. It sets the left and right edge and lets the ladder be
        extended when the frame plainly continues past the last unit seen.
      - rail boxes, which are not rows and are dropped (but cap the ladder).

    Returns unit dicts labelled u01 at the BOTTOM, which is the rack convention
    the rest of the pipeline follows. Returns [] when the model finds nothing.
    """
    if not unit_model_path or img is None or img.size == 0:
        return []

    model = load_model(unit_model_path)
    small, factor = shrink_for_inference(img)
    results = model.predict(small, conf=conf, imgsz=imgsz, verbose=False)
    if not results:
        return []
    names = getattr(model, "names", {}) or {}
    units, racks, rails = _unit_rows_from_result(results[0], names)
    if factor != 1:
        # Back to the original image's pixels before any ladder rule runs.
        for u in units:
            u["box"] = [v * factor for v in u["box"]]
        racks = [[v * factor for v in r] for r in racks]
        rails = [[v * factor for v in r] for r in rails]
    if not units:
        return []

    return ladder_from_unit_boxes(units, racks, img.shape[0], img.shape[1], rails)


def _cap_at_rails(rack_top, rack_bot, first_top, last_bot, rails):
    """Pull the frame's top and bottom in to the cross rails, where there are any.

    The Rack box is the whole cabinet when the photo shows one: roof panel,
    plinth, castors. The model also finds the horizontal cross rails that close
    the mounting space at the top and the bottom, and nothing mounts past them.
    Only a rail wider than it is tall counts: an upright rail runs the whole
    height and says nothing about where the rows stop.
    """
    for x1, y1, x2, y2 in rails or []:
        if (x2 - x1) <= 2 * (y2 - y1):
            continue
        centre = (y1 + y2) / 2.0
        if centre <= first_top:
            rack_top = max(rack_top, y2)
        elif centre >= last_bot:
            rack_bot = min(rack_bot, y1)
    return rack_top, rack_bot


def ladder_from_unit_boxes(units, racks, img_h, img_w, rails=None):
    """Turn the model's raw boxes into the contiguous, numbered ladder.

    Split out from the prediction so it can be tested without loading weights:
    every rule that decides where a unit boundary falls lives here.
    """
    if not units:
        return []
    units = _drop_duplicate_units(units)
    heights = [u["box"][3] - u["box"][1] for u in units]
    median_h = float(np.median(heights))
    if median_h <= 0:
        return []

    # A box several rows tall is not a row. Drop it rather than let it set the
    # pitch, but only when there is a majority to judge it against.
    if len(units) >= 3:
        units = [u for u in units if (u["box"][3] - u["box"][1]) <= median_h * _UNIT_TALL_FACTOR]
        if not units:
            return []
        median_h = float(np.median([u["box"][3] - u["box"][1] for u in units]))

    if racks:
        left_x = int(max(0, min(r[0] for r in racks)))
        right_x = int(min(img_w - 1, max(r[2] for r in racks)))
        rack_top = float(min(r[1] for r in racks))
        rack_bot = float(max(r[3] for r in racks))
    else:
        left_x = int(max(0, min(u["box"][0] for u in units)))
        right_x = int(min(img_w - 1, max(u["box"][2] for u in units)))
        rack_top, rack_bot = units[0]["box"][1], units[-1]["box"][3]
    if right_x <= left_x:
        left_x, right_x = 0, img_w - 1

    # Contiguous without being uniform: where two rows overlap, the boundary is
    # the middle of the overlap, so both keep the height the model gave them.
    edges = [[u["box"][1], u["box"][3]] for u in units]
    for i in range(len(edges) - 1):
        lower_top, upper_bot = edges[i + 1][0], edges[i][1]
        if lower_top < upper_bot:
            mid = (lower_top + upper_bot) / 2.0
            edges[i][1] = mid
            edges[i + 1][0] = mid

    # A gap worth at least half a row is a row the model missed. Split it into
    # however many rows it measures, evenly.
    filled = []
    for i, (y1, y2) in enumerate(edges):
        filled.append([y1, y2])
        if i + 1 < len(edges):
            gap = edges[i + 1][0] - y2
            if gap >= median_h * _UNIT_HOLE_MIN:
                missing = max(1, int(round(gap / median_h)))
                step = gap / missing
                for k in range(missing):
                    filled.append([y2 + k * step, y2 + (k + 1) * step])

    # The frame plainly carrying on past the outermost row means rows the model
    # did not see - a dark PDU at the floor, a UPS at the top. Never past a
    # cross rail, though: beyond it is the cabinet, not the rack.
    rack_top, rack_bot = _cap_at_rails(rack_top, rack_bot, filled[0][0], filled[-1][1], rails)
    above = filled[0][0] - rack_top
    if above >= median_h * _UNIT_EXTEND_MIN:
        for k in range(min(_UNIT_EXTEND_MAX, max(1, int(round(above / median_h))))):
            top = filled[0][0] - median_h
            filled.insert(0, [max(rack_top, top), filled[0][0]])
    below = rack_bot - filled[-1][1]
    if below >= median_h * _UNIT_EXTEND_MIN:
        for k in range(min(_UNIT_EXTEND_MAX, max(1, int(round(below / median_h))))):
            filled.append([filled[-1][1], min(rack_bot, filled[-1][1] + median_h)])

    count = len(filled)
    out = []
    for i, (y1, y2) in enumerate(filled):
        y1i, y2i = int(round(max(0, y1))), int(round(min(img_h, y2)))
        if y2i - y1i <= 0:
            continue
        out.append(
            {
                # u01 is the BOTTOM row, so the topmost box carries the highest
                # number. This is the one place the model's order and the rack's
                # numbering disagree, and it has to be resolved here.
                "label": f"u{count - i:02d}",
                "box": [left_x, y1i, right_x, y2i],
                "center": [(left_x + right_x) // 2, (y1i + y2i) // 2],
                "center_y": float((y1i + y2i) / 2),
            }
        )
    return out
