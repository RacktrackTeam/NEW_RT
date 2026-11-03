"""Making the rack rectilinear.

Stitching leaves the rack front slightly keystoned and rotated. Two corrections:

1. straighten_verticals - detect the near-vertical edges (rails, cabinet sides), find their
   vanishing point, and apply a homography that sends it to infinity (verticals become vertical).
2. level_horizontals    - a vertical-only fix leaves horizontal edges tilted (the axes are no longer
   at 90 degrees), so a second y-shear levels them while keeping verticals vertical.
"""
from __future__ import annotations

import cv2
import numpy as np

from .crop import find_rack_bbox

LSD_SIDE = 1400            # long side used for line detection (levelling)
VP_MIN_SEGMENT = 60        # shortest near-vertical segment (px, at long side 1000) used for the vanishing point
VP_MIN_INLIER_LINES = 3    # lines that must agree on the vanishing point
VP_MIN_SUPPORT = 0.15      # share of total line length that must agree
PARALLEL_FLOOR_DEG = 0.5   # lines that agree on one direction to within this need no perspective correction
PARALLEL_RATIO = 1.6       # ... or whose spread is not worse than this times the vanishing-point model's
PARALLEL_BAND_DEG = 0.8    # pre-check: if this share of line length lies within +-this of one direction, edges are parallel
PARALLEL_SHARE = 0.5


def _weighted_median(values, weights):
    order = np.argsort(values)
    v, w = np.asarray(values)[order], np.asarray(weights)[order]
    c = np.cumsum(w)
    return float(v[np.searchsorted(c, c[-1] / 2)])


def _segments(gray: np.ndarray, min_len: float):
    """Line segments (x1,y1,x2,y2) found on a downscaled copy, with the scale used."""
    s = LSD_SIDE / max(gray.shape)
    small = cv2.resize(gray, None, fx=s, fy=s) if s < 1 else gray
    s = small.shape[1] / gray.shape[1]
    lines = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD).detect(small)[0]
    if lines is None:
        return np.empty((0, 4)), s
    lines = lines.reshape(-1, 4)
    keep = np.hypot(lines[:, 2] - lines[:, 0], lines[:, 3] - lines[:, 1]) >= min_len
    return lines[keep], s


def horizontal_tilt(img: np.ndarray, valid: np.ndarray) -> float | None:
    """Length-weighted median angle (degrees) of near-horizontal edges on the rack interior.

    Returns None when too few edges are found to measure it (unknown is not the same as level).
    """
    x0, y0, x1, y1 = find_rack_bbox(img, valid)
    w = x1 - x0
    roi = img[y0:y1, x0 + int(0.15 * w) : x1 - int(0.15 * w)]
    if roi.size == 0:
        return None
    segs, _ = _segments(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), 60)
    angles, weights = [], []
    for x_a, y_a, x_b, y_b in segs:
        a = (np.degrees(np.arctan2(y_b - y_a, x_b - x_a)) + 90) % 180 - 90
        if abs(a) < 12:
            angles.append(a)
            weights.append(np.hypot(x_b - x_a, y_b - y_a))
    return _weighted_median(angles, weights) if len(angles) >= 8 else None


def _shear_y(img, valid, m):
    """y' = y - m*(x - cx): horizontals rotate by -atan(m), verticals stay vertical."""
    h, w = img.shape[:2]
    M = np.array([[1, 0, 0], [-m, 1, m * w / 2.0]])
    out = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC)
    ok = cv2.warpAffine(valid.astype(np.uint8), M, (w, h), flags=cv2.INTER_NEAREST) > 0
    return out, ok


def level_horizontals(img, valid, tol_deg: float = 0.3, max_passes: int = 6):
    """Shear until near-horizontal edges are level.

    Returns (img, valid, applied_deg, residual_deg); residual_deg is None if the tilt could not be measured
    (the image is then returned unchanged).
    """
    m, residual = 0.0, horizontal_tilt(img, valid)
    cur_img, cur_valid = img, valid
    for _ in range(max_passes):
        if residual is None or abs(residual) < tol_deg:
            break
        m += np.tan(np.radians(residual))
        cur_img, cur_valid = _shear_y(img, valid, m)  # always warp from the original: one interpolation
        residual = horizontal_tilt(cur_img, cur_valid)
        if residual is None:                          # lost the reference while correcting: undo
            return img, valid, 0.0, None
    return cur_img, cur_valid, float(np.degrees(np.arctan(m))), residual


def straighten_verticals(
    img,
    valid,
    *,
    restrict_to_rack: bool = True,
    min_segment: float = VP_MIN_SEGMENT,
    min_inlier_lines: int = VP_MIN_INLIER_LINES,
    min_support: float = VP_MIN_SUPPORT,
    iterations: int = 3000,
):
    """Send the vertical vanishing point to infinity. Returns (img, valid, info or None).

    `info` is None when no reliable vanishing point was found (the image is returned unchanged). When the edges
    are already parallel (a front-on capture) there is nothing to correct: the image is returned unchanged with
    ``info["status"] == "parallel"``. Otherwise ``info["status"] == "corrected"``.
    Line detection runs at a fixed working scale (long side 1000 px) so the thresholds below
    (inlier distance 6, vanishing point at least 2000) are resolution-independent.

    restrict_to_rack=False uses lines from the whole image (the original report pipeline);
    True keeps only lines on the detected rack, which is more robust on a loosely cropped image.
    """
    h, w = img.shape[:2]
    s = 1000.0 / max(h, w)
    gray = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), None, fx=s, fy=s)
    raw = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD).detect(gray)[0]
    if raw is None:
        return img, valid, None
    segs = raw.reshape(-1, 4)
    length = np.hypot(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1])
    angle = (np.degrees(np.arctan2(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1])) + 90) % 180 - 90
    # keep long, near-vertical segments whose midpoint lies on the rack (not on the wall)
    keep = (length >= min_segment) & (np.abs(angle) < 12)
    if restrict_to_rack:
        bx0, by0, bx1, by1 = (np.array(find_rack_bbox(img, valid)) * s)
        mx, my = (segs[:, 0] + segs[:, 2]) / 2, (segs[:, 1] + segs[:, 3]) / 2
        keep &= (mx > bx0) & (mx < bx1) & (my > by0) & (my < by1)
    segs, weight = segs[keep], length[keep]
    if len(segs) < (8 if restrict_to_rack else 2):
        return img, valid, None

    # Pre-check: most of the edge length pointing the same way means the edges are already parallel
    # (a front-on capture): nothing to correct, and no finite vanishing point exists to find.
    ang_all = angle[keep]
    order = np.argsort(ang_all)
    centre = ang_all[order][np.searchsorted(np.cumsum(weight[order]), weight.sum() / 2)]
    near = np.abs(((ang_all - centre + 90) % 180) - 90) <= PARALLEL_BAND_DEG
    parallel_share = float(weight[near].sum() / weight.sum())
    if len(segs) >= 8 and parallel_share >= PARALLEL_SHARE:
        return img, valid, {"status": "parallel", "parallel_share": parallel_share}

    p1 = np.c_[segs[:, 0], segs[:, 1], np.ones(len(segs))]
    p2 = np.c_[segs[:, 2], segs[:, 3], np.ones(len(segs))]
    L = np.cross(p1, p2)
    L /= np.linalg.norm(L[:, :2], axis=1, keepdims=True)

    rng = np.random.default_rng(0)
    best_score, best_v = 0.0, None
    for _ in range(iterations):
        i, j = rng.choice(len(L), 2, replace=False)
        v = np.cross(L[i], L[j])
        if abs(v[2]) < 1e-9:
            continue
        v = v / v[2]
        if abs(v[1]) < 2000.0:           # vanishing point must be far above/below the image
            continue
        score = weight[np.abs(L @ v) < 6.0].sum()
        if score > best_score:
            best_score, best_v = score, v
    if best_v is None or best_score / weight.sum() < min_support:
        return img, valid, None
    inl = np.abs(L @ best_v) < 6.0
    if inl.sum() < min_inlier_lines:
        return img, valid, None
    vp = np.linalg.lstsq(L[inl, :2], -L[inl, 2], rcond=None)[0] / s     # back to full-resolution pixels
    vx, vy = vp[0] - w / 2.0, vp[1] - h / 2.0                          # relative to the image centre
    if abs(vy) < 1e-6:
        return img, valid, None

    # Do the edges really converge? Compare how well ONE common direction (parallel lines) explains the
    # measured angles with how well a vanishing point does. Without this check a front-on rack (parallel
    # edges) would get a made-up keystone correction from noise.
    ang_in = angle[keep][inl]
    w_in = weight[inl]
    mid_x = ((segs[:, 0] + segs[:, 2]) / 2)[inl]
    mid_y = ((segs[:, 1] + segs[:, 3]) / 2)[inl]
    wrap = lambda a: (a + 90) % 180 - 90
    pred = wrap(np.degrees(np.arctan2(vp[0] * s - mid_x, vp[1] * s - mid_y)))
    rms = lambda r: float(np.sqrt(np.sum(w_in * r * r) / np.sum(w_in)))
    order = np.argsort(ang_in)
    median = ang_in[order][np.searchsorted(np.cumsum(w_in[order]), w_in.sum() / 2)]
    rms_parallel, rms_vp = rms(wrap(ang_in - median)), rms(wrap(ang_in - pred))
    stats = {"parallel_share": parallel_share, "rms_parallel_deg": rms_parallel, "rms_vp_deg": rms_vp, "vanishing_point": (float(vp[0]), float(vp[1])), "support": float(best_score / weight.sum())}
    if rms_parallel <= max(PARALLEL_FLOOR_DEG, PARALLEL_RATIO * rms_vp):
        return img, valid, {"status": "parallel", **stats}

    Hs = np.array([[1, -vx / vy, 0], [0, 1, 0], [0, 0, 1.0]])
    Hp = np.array([[1, 0, 0], [0, 1, 0], [0, -1 / vy, 1.0]])
    T0 = np.array([[1, 0, -w / 2.0], [0, 1, -h / 2.0], [0, 0, 1.0]])
    H = np.linalg.inv(T0) @ Hp @ Hs @ T0
    c = H @ np.array([w / 2.0, h / 2.0, 1.0])
    c /= c[2]
    H = np.array([[1, 0, w / 2.0 - c[0]], [0, 1, h / 2.0 - c[1]], [0, 0, 1.0]]) @ H   # keep centre and scale
    corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
    moved = cv2.perspectiveTransform(corners, H).reshape(-1, 2)
    lo, hi = moved.min(0), moved.max(0)
    out_size = (int(hi[0] - lo[0]), int(hi[1] - lo[1]))
    if min(out_size) < 1 or out_size[0] > 2.5 * w or out_size[1] > 2.5 * h:      # degenerate correction
        return img, valid, None
    H = np.array([[1, 0, -lo[0]], [0, 1, -lo[1]], [0, 0, 1.0]]) @ H
    out = cv2.warpPerspective(img, H, out_size, flags=cv2.INTER_CUBIC)
    ok = cv2.warpPerspective(valid.astype(np.uint8), H, out_size, flags=cv2.INTER_NEAREST) > 0
    return out, ok, {"status": "corrected", **stats}
