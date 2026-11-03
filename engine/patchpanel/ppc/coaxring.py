"""Coax check from the image: count round metal rings in a row.

The 13-class port detector has no coax class and reads BNC / F-type / N
bulkheads as RJ45, LC, SFP or nothing, and the old pixel shape check never
fires because its circularity measure picks up the centre pin instead of
the ring. This module looks for the ring itself: a Hough circle detector
over the whole crop, kept only where the circles are the same size, sit in
a row, and are metal (unsaturated, bright). A fixed RJ-45 panel, a keystone
frame and an LC/SC fiber panel have no run of same-size round rings.

Measured before use: run measure_coaxring.py on a labelled set and read the
per-class distribution of `n_rings` before trusting any threshold here.
"""

import cv2
import numpy as np

MIN_RINGS = 4            # fewer is a screw hole and two LEDs
ROW_TOL = 0.6            # same row if centres differ by < this * radius*2
MIN_ROW = 3              # at least this many rings share one row
SAT_MAX = 70.0           # median HSV saturation of the ring annulus
VAL_MIN = 70.0           # median HSV value of the ring annulus


def _load(path, img_w=0, img_h=0):
    bgr = cv2.imread(path, cv2.IMREAD_COLOR)
    if bgr is None:
        try:
            from PIL import Image
            bgr = cv2.cvtColor(np.array(Image.open(path).convert("RGB")),
                               cv2.COLOR_RGB2BGR)
        except Exception:
            return None
    if img_w and img_h and (bgr.shape[1] != img_w or bgr.shape[0] != img_h):
        bgr = cv2.resize(bgr, (img_w, img_h))
    return bgr


def _circles(gray):
    H, W = gray.shape
    short = min(H, W)
    g = cv2.createCLAHE(2.0, (8, 8)).apply(gray)
    g = cv2.medianBlur(g, 5)
    passes = []
    for rmin, rmax, dist, p2 in ((max(3, int(short * 0.015)),
                                  max(6, int(short * 0.06)),
                                  max(4, int(short * 0.03)), 24),
                                 (max(6, int(short * 0.06)),
                                  max(12, int(short * 0.20)),
                                  max(8, int(short * 0.10)), 34)):
        c = cv2.HoughCircles(g, cv2.HOUGH_GRADIENT, dp=1.2, minDist=dist,
                             param1=100, param2=p2, minRadius=rmin,
                             maxRadius=rmax)
        passes.append([] if c is None else c[0].tolist())
    found = passes[0] if len(passes[0]) >= MIN_RINGS else passes[1] or passes[0]
    if len(found) >= 3:
        med = float(np.median([c[2] for c in found]))
        found = [c for c in found if 0.6 * med <= c[2] <= 1.5 * med]
    found.sort(key=lambda c: -c[2])
    keep = []
    for x, y, r in found:
        if all((x - kx) ** 2 + (y - ky) ** 2 > (1.1 * max(r, kr)) ** 2
               for kx, ky, kr in keep):
            keep.append((x, y, r))
    return keep


def _metal(hsv, x, y, r):
    """Median saturation / value on the ring annulus (0.6r .. 1.0r)."""
    H, W = hsv.shape[:2]
    x0, y0 = max(0, int(x - r)), max(0, int(y - r))
    x1, y1 = min(W, int(x + r) + 1), min(H, int(y + r) + 1)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return 255.0, 0.0
    yy, xx = np.mgrid[y0:y1, x0:x1]
    d = np.sqrt((xx - x) ** 2 + (yy - y) ** 2)
    m = (d >= 0.6 * r) & (d <= r)
    if m.sum() < 8:
        return 255.0, 0.0
    patch = hsv[y0:y1, x0:x1]
    return (float(np.median(patch[:, :, 1][m])),
            float(np.median(patch[:, :, 2][m])))


def _longest_row(circles):
    if not circles:
        return 0
    r = float(np.median([c[2] for c in circles]))
    ys = sorted(c[1] for c in circles)
    best, start = 1, 0
    for i in range(1, len(ys)):
        if ys[i] - ys[start] > ROW_TOL * 2 * r:
            start += 1
            while ys[i] - ys[start] > ROW_TOL * 2 * r:
                start += 1
        best = max(best, i - start + 1)
    return best


def ring_evidence(image_path, img_w=0, img_h=0):
    """-> dict(n_rings, n_metal, row, radius, is_coax, why)."""
    out = {"n_rings": 0, "n_metal": 0, "row": 0, "radius": 0.0,
           "is_coax": False, "why": "image unreadable"}
    bgr = _load(image_path, img_w, img_h)
    if bgr is None:
        return out
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    circles = _circles(gray)
    metal = [c for c in circles
             if (lambda s, v: s <= SAT_MAX and v >= VAL_MIN)(
                 *_metal(hsv, *c))]
    out["n_rings"] = len(circles)
    out["n_metal"] = len(metal)
    out["row"] = _longest_row(metal)
    out["radius"] = float(np.median([c[2] for c in metal])) if metal else 0.0
    out["is_coax"] = bool(out["n_metal"] >= MIN_RINGS
                          and out["row"] >= MIN_ROW)
    out["why"] = ("%d round rings, %d of them metal, %d in one row, "
                  "radius %.0f px" % (out["n_rings"], out["n_metal"],
                                      out["row"], out["radius"]))
    return out
