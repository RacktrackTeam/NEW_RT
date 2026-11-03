"""Compositing: every output pixel comes from one frame; seams follow the cheapest path.

Averaging overlapping frames ghosts anything off the rack plane (cables). Instead, each new frame
is joined to the mosaic along a minimum-difference seam (dynamic programming) with a narrow
feather, so cables stay sharp.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

DIVERGENCE_PIXELS = 500_000_000   # a canvas this big means the chain of homographies went wrong
DEFAULT_BUDGET_PIXELS = 40_000_000  # working canvas size we are willing to allocate (about 200 bytes/pixel peak)
GAIN_LIMITS = (0.85, 1.18)         # exposure correction between neighbouring frames is clipped to this range
FEATHER_PX = 8.0
EDGE_TRIM = 15  # drop this many pixels at each frame border (lens vignetting / warp edges)


@dataclass
class ComposeStats:
    """What happened while joining the frames (feeds the capture-quality warnings)."""

    max_gain_deviation: float = 0.0   # largest brightness correction needed between neighbours (0.2 = 20%)
    clipped_pairs: int = 0            # neighbour pairs whose brightness mismatch exceeded the correction limit
    canvas_scale: float = 1.0         # <1 if frames were shrunk to fit the memory budget


class CompositionError(RuntimeError):
    """The aligned frames do not form a sensible canvas."""


def canvas_geometry(frame_shape: tuple[int, int], transforms: list[np.ndarray]):
    """Output size and the offset homography that moves everything into positive coordinates."""
    h, w = frame_shape[:2]
    corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
    pts = np.concatenate([cv2.perspectiveTransform(corners, t) for t in transforms]).reshape(-1, 2)
    if not np.isfinite(pts).all():
        raise CompositionError("Non-finite canvas bounds (alignment diverged).")
    lo, hi = np.floor(pts.min(0)), np.ceil(pts.max(0))
    size = (int(hi[0] - lo[0]), int(hi[1] - lo[1]))
    if size[0] < 1 or size[1] < 1 or size[0] * size[1] > DIVERGENCE_PIXELS:
        raise CompositionError(f"Implausible canvas size {size[0]}x{size[1]} (alignment diverged).")
    offset = np.array([[1, 0, -lo[0]], [0, 1, -lo[1]], [0, 0, 1.0]])
    return size, offset


def _seam_alpha(mosaic, new, covered, new_mask, size):
    """Blend weight of the new frame (0..1) from a minimum-cost seam through the overlap."""
    overlap = covered & new_mask
    ys = np.where(overlap.any(1))[0]
    xs = np.where(overlap.any(0))[0]
    y0, y1, x0, x1 = ys[0], ys[-1] + 1, xs[0], xs[-1] + 1
    sub = overlap[y0:y1, x0:x1]
    h, w = sub.shape

    diff = np.abs(cv2.GaussianBlur(mosaic, (0, 0), 1.5) - cv2.GaussianBlur(new, (0, 0), 1.5)).sum(2)
    col_has = sub.any(0)
    top = np.where(col_has, sub.argmax(0), 0)
    bottom = np.where(col_has, h - 1 - sub[::-1].argmax(0), 0)
    mid = (top + bottom) / 2.0
    # prefer the middle of the overlap (frame centres are sharper / less distorted)
    cost = diff[y0:y1, x0:x1] + 0.2 * np.abs(np.arange(h)[:, None] - mid[None, :])
    cost[~sub] = 1e9

    acc = cost.copy()
    back = np.zeros((h, w), np.int16)
    for x in range(1, w):
        prev = acc[:, x - 1]
        best, arg = prev.copy(), np.zeros(h, np.int16)
        for dy in (-2, -1, 1, 2):
            shifted = np.full(h, 1e18)
            if dy > 0:
                shifted[dy:] = prev[:-dy]
            else:
                shifted[: h + dy] = prev[-dy:]
            better = shifted < best
            best, arg = np.where(better, shifted, best), np.where(better, dy, arg)
        acc[:, x], back[:, x] = cost[:, x] + best, arg

    seam = np.zeros(w, int)
    y = int(acc[:, -1].argmin())
    for x in range(w - 1, -1, -1):
        seam[x] = y
        y -= int(back[y, x])

    # frames advance downward: below the seam use the new frame, above keep the mosaic
    signed = np.full(size[::-1], -1000.0, np.float32)
    signed[y0:y1, x0:x1] = np.arange(h)[:, None] - seam[None, :]
    signed[y1:, :] = 1000.0
    alpha = np.clip(signed / FEATHER_PX + 0.5, 0, 1)
    alpha[~new_mask] = 0
    alpha[new_mask & ~covered] = 1
    return alpha


def fit_budget(frames: list[np.ndarray], transforms: list[np.ndarray], budget: int = DEFAULT_BUDGET_PIXELS):
    """Shrink frames (and their transforms) so the mosaic canvas stays within `budget` pixels.

    Returns (frames, transforms, scale); scale is 1.0 when nothing had to be shrunk.
    """
    size, _ = canvas_geometry(frames[0].shape, transforms)
    area = size[0] * size[1]
    if area <= budget:
        return frames, transforms, 1.0
    s = math.sqrt(budget / area)
    S = np.diag([s, s, 1.0])
    shrunk = [cv2.resize(f, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) for f in frames]
    return shrunk, [S @ t @ np.linalg.inv(S) for t in transforms], s


def compose(frames: list[np.ndarray], transforms: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray, ComposeStats]:
    """Return (mosaic uint8 BGR, valid mask bool, stats)."""
    stats = ComposeStats()
    h, w = frames[0].shape[:2]
    size, offset = canvas_geometry(frames[0].shape, transforms)
    keep = cv2.erode(np.ones((h, w), np.uint8), np.ones((EDGE_TRIM, EDGE_TRIM)))

    def warp(k):
        M = offset @ transforms[k]
        img = cv2.warpPerspective(frames[k], M, size, flags=cv2.INTER_CUBIC)
        return img.astype(np.float32), cv2.warpPerspective(keep, M, size, flags=cv2.INTER_NEAREST) > 0

    mosaic, covered = warp(0)
    for k in range(1, len(frames)):
        new, new_mask = warp(k)
        overlap = covered & new_mask
        if overlap.sum() < 500:  # no overlap: just paste the uncovered part
            fresh = new_mask & ~covered
            mosaic[fresh] = new[fresh]
            covered |= new_mask
            continue
        gain = (mosaic[overlap].mean(0) + 1) / (new[overlap].mean(0) + 1)  # exposure matching
        stats.max_gain_deviation = max(stats.max_gain_deviation, float(np.abs(gain - 1).max()))
        stats.clipped_pairs += int((gain < GAIN_LIMITS[0]).any() or (gain > GAIN_LIMITS[1]).any())
        new *= np.clip(gain, *GAIN_LIMITS)
        alpha = _seam_alpha(mosaic, new, covered, new_mask, size)
        mosaic = mosaic * (1 - alpha[..., None]) + new * alpha[..., None]
        covered |= new_mask

    out = np.clip(mosaic, 0, 255).astype(np.uint8)
    out[~covered] = 0
    return out, covered, stats
