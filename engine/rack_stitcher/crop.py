"""Finding the rack in the mosaic and cropping to it.

The rack cabinet is large, dark and neutral (black); the wall and floor are light and the cardboard
is brown. Two functions:

* find_rack_bbox  - bounding box of the biggest dark region (used to place the rough crop and to
                    limit where lines are searched). It also contains the open door, if any.
* find_rack_crop  - the final crop: from the cabinet's left side panel to its right side panel
                    (door excluded), from the top of the cabinet to just below the base.
"""
from __future__ import annotations

import cv2
import numpy as np

Box = tuple[int, int, int, int]  # x0, y0, x1, y1 (exclusive end)

DARK_MAX_VALUE = 85        # HSV value below this counts as cabinet-dark (0-255)
DARK_MAX_SATURATION = 100  # ... and saturation below this counts as neutral

# find_rack_crop tuning
SIDE_PLATEAU = 0.75        # share of dark pixels in a column that marks a solid side panel
GAP_JOIN = 0.03           # gaps narrower than this (fraction of width) join a rail and its side panel
MIN_PANEL = 0.01          # narrowest side panel accepted (fraction of width)
MARGIN_LEFT, MARGIN_RIGHT, MARGIN_TOP, MARGIN_BOTTOM = 0.05, 0.02, 0.01, 0.08   # of cabinet width / height


def _dark_mask(img: np.ndarray, valid: np.ndarray | None):
    """(dark mask, scale) at a working size of at most 800 px on the long side."""
    h, w = img.shape[:2]
    s = min(1.0, 800 / max(h, w))
    small = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else img
    ok = (
        cv2.resize(valid.astype(np.uint8), (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
        if valid is not None
        else np.ones(small.shape[:2], bool)
    )
    hsv = cv2.cvtColor(cv2.GaussianBlur(small, (5, 5), 0), cv2.COLOR_BGR2HSV)
    # the cabinet is dark AND neutral; brown shelves/cardboard are dark-ish but saturated
    dark = (hsv[..., 2] < DARK_MAX_VALUE) & (hsv[..., 1] < DARK_MAX_SATURATION) & ok
    return dark, s


def find_rack_bbox(img: np.ndarray, valid: np.ndarray | None = None, margin: float = 0.0) -> Box:
    """Bounding box of the largest dark region (the cabinet, door included), grown by `margin`.

    Falls back to the whole image if nothing plausible is found.
    """
    h, w = img.shape[:2]
    dark, s = _dark_mask(img, valid)
    if dark.sum() < 100:
        return 0, 0, w, h
    k = max(5, int(0.03 * max(dark.shape)))
    closed = cv2.morphologyEx(dark.astype(np.uint8), cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
    if n < 2:
        return 0, 0, w, h
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, bw, bh, area = stats[i]
    if area < 0.05 * dark.shape[0] * dark.shape[1]:
        return 0, 0, w, h
    x0, y0, x1, y1 = x / s, y / s, (x + bw) / s, (y + bh) / s
    mx, my = margin * (x1 - x0), margin * (y1 - y0)
    return int(max(0, x0 - mx)), int(max(0, y0 - my)), int(min(w, x1 + mx)), int(min(h, y1 + my))


def _runs(mask: np.ndarray):
    """Consecutive runs of equal values: (start, end, value)."""
    out, i, n = [], 0, len(mask)
    while i < n:
        j = i
        while j < n and mask[j] == mask[i]:
            j += 1
        out.append((i, j, bool(mask[i])))
        i = j
    return out


def find_rack_crop(img: np.ndarray, valid: np.ndarray | None = None, info: dict | None = None) -> Box | None:
    """Crop box for the rack alone: cabinet side panel to side panel, lid to just below the base.

    How: along the equipment rows, the share of dark pixels per image column is high on the cabinet's two
    side panels (solid black), lower across the equipment (cables, white devices) and broken up on an open
    door. Starting from the middle of the equipment and walking outwards, the first solid dark band on each
    side (a mounting rail merged with its side panel) is the cabinet side. Returns None if that structure is
    not found (the caller then falls back to find_rack_bbox).

    If `info` is a dict it receives ``top_margin`` and ``bottom_margin``: the free space above and below the
    dark cabinet region as a fraction of the image height (a value near 0 means the cabinet runs to the edge of
    the stitched area, i.e. it was cut off or filming stopped right at it).
    """
    dark, s = _dark_mask(img, valid)
    h, w = dark.shape
    bx0, by0, bx1, by1 = (int(v * s) for v in find_rack_bbox(img, valid))
    if by1 - by0 < 20 or bx1 - bx0 < 20:
        return None
    rows = slice(by0 + int(0.25 * (by1 - by0)), by0 + int(0.70 * (by1 - by0)))   # equipment band
    profile = np.convolve(dark[rows].mean(0), np.ones(5) / 5, mode="same")
    cols = np.arange(w)
    middle = np.where((profile < 0.6) & (cols > bx0) & (cols < bx1))[0]
    if len(middle) < 10:
        return None
    center = int(np.median(middle))

    solid = profile >= SIDE_PLATEAU
    for a, b, v in _runs(solid):                       # join a rail and its side panel across a small gap
        if not v and a > 0 and b < w and (b - a) <= GAP_JOIN * w:
            solid[a:b] = True
    bands = [r for r in _runs(solid) if r[2] and (r[1] - r[0]) >= max(3, MIN_PANEL * w)]
    left = [r for r in bands if r[1] <= center]
    right = [r for r in bands if r[0] >= center]
    if not left or not right:
        return None
    L, R = left[-1], right[0]
    cabinet = R[1] - L[0]
    if cabinet < 0.25 * (bx1 - bx0):                    # implausibly narrow: structure not found
        return None
    x0, x1 = max(L[0] - MARGIN_LEFT * cabinet, 0), min(R[1] + MARGIN_RIGHT * cabinet, w)

    # base: last row that is dark across (most of) the cabinet width; wheels and floor shadow are not
    frac = dark[:, int(L[0]) : int(R[1])].mean(1)
    base_rows = np.where(frac >= 0.5)[0]
    base_rows = base_rows[(base_rows >= by0) & (base_rows <= by1)]
    if len(base_rows) == 0:
        return None
    base, top = int(base_rows.max()), by0
    if info is not None:                      # how close the dark cabinet region comes to the edge of the stitched area
        info["top_margin"] = by0 / h
        info["bottom_margin"] = (h - by1) / h
    height = base - top
    y0, y1 = max(top - MARGIN_TOP * height, 0), min(base + MARGIN_BOTTOM * height, h)
    return int(x0 / s), int(y0 / s), int(x1 / s), int(y1 / s)


def trim_to_valid(valid: np.ndarray, box: Box, min_fraction: float = 0.9, max_trim: float = 0.06, central: float = 0.6) -> Box:
    """Pull each edge of `box` inward while that edge is mostly empty (no image data).

    Only the central `central` fraction of an edge is judged, so the small empty triangles that
    geometric corrections leave in the corners do not make it eat into the rack.
    """
    x0, y0, x1, y1 = box
    step = 4

    def span(a, b):                       # central part of [a, b)
        pad = int((1 - central) / 2 * (b - a))
        return a + pad, b - pad

    lim_y, lim_x = int(max_trim * (y1 - y0)), int(max_trim * (x1 - x0))
    for _ in range(lim_y // step):
        xa, xb = span(x0, x1)
        if valid[y0, xa:xb].mean() >= min_fraction:
            break
        y0 += step
    for _ in range(lim_y // step):
        xa, xb = span(x0, x1)
        if valid[y1 - 1, xa:xb].mean() >= min_fraction:
            break
        y1 -= step
    for _ in range(lim_x // step):
        ya, yb = span(y0, y1)
        if valid[ya:yb, x0].mean() >= min_fraction:
            break
        x0 += step
    for _ in range(lim_x // step):
        ya, yb = span(y0, y1)
        if valid[ya:yb, x1 - 1].mean() >= min_fraction:
            break
        x1 -= step
    return x0, y0, x1, y1
