"""Aligning consecutive keyframes: SIFT matches -> homography on the rack plane -> one chain."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .video_io import to_work, work_scale

MIN_INLIERS = 20          # matched features needed to trust a pair
MIN_RUN_FRACTION = 0.5    # the aligned run must cover at least this share of the keyframes
MIN_RUN_FRAMES = 4


class RegistrationError(RuntimeError):
    """Neighbouring keyframes could not be aligned (too few matches) and no usable run remains."""

    def __init__(self, pair: int, inliers: int):
        super().__init__(f"Keyframes {pair - 1} and {pair} could not be aligned ({inliers} inliers).")
        self.pair, self.inliers = pair, inliers


@dataclass
class Chain:
    """The aligned run of keyframes."""

    start: int                         # first keyframe kept (index into the list that was passed in)
    stop: int                          # one past the last keyframe kept
    transforms: list[np.ndarray]       # frame -> middle frame of the run, full-resolution pixel coordinates
    inliers: list[int]                 # matched features for each kept neighbouring pair

    @property
    def complete(self) -> bool:
        return self.start == 0 and self.stop == self._total

    _total: int = 0


def _homography(src: np.ndarray, dst: np.ndarray):
    # MAGSAC++ with a tight threshold locks onto the dominant plane (the rack front).
    method = getattr(cv2, "USAC_MAGSAC", cv2.RANSAC)
    return cv2.findHomography(src, dst, method, 2.0, maxIters=5000, confidence=0.999)


def register_chain(frames: list[np.ndarray]) -> Chain:
    """Align neighbouring frames and chain them (full-resolution coordinates, relative to the middle frame).

    If some neighbouring pair cannot be aligned (for example frames that only show plain wall or floor) the
    longest run of frames that does align is kept. Raises RegistrationError if that run is too short.
    """
    n = len(frames)
    scale = work_scale(frames[0].shape)
    sift = cv2.SIFT_create(5000, contrastThreshold=0.02)
    clahe = cv2.createCLAHE(2.0, (8, 8))
    feats = [
        sift.detectAndCompute(clahe.apply(cv2.cvtColor(to_work(f, scale), cv2.COLOR_BGR2GRAY)), None)
        for f in frames
    ]
    matcher = cv2.BFMatcher()

    pair_h: list[np.ndarray | None] = [None] * n       # pair_h[j] maps frame j -> frame j-1
    pair_inl = [0] * n
    for j in range(1, n):
        (k1, d1), (k2, d2) = feats[j - 1], feats[j]
        if d1 is None or d2 is None or len(k1) < 8 or len(k2) < 8:
            continue
        good = [a for a, b in (m for m in matcher.knnMatch(d2, d1, k=2) if len(m) == 2) if a.distance < 0.72 * b.distance]
        if len(good) < 8:
            pair_inl[j] = len(good)
            continue
        p_cur = np.float32([k2[m.queryIdx].pt for m in good])
        p_prev = np.float32([k1[m.trainIdx].pt for m in good])
        H, mask = _homography(p_cur, p_prev)
        count = int(mask.sum()) if H is not None else 0
        pair_inl[j] = count
        if H is not None and count >= MIN_INLIERS:
            pair_h[j] = H

    breaks = [j for j in range(1, n) if pair_h[j] is None]
    edges = [0] + breaks + [n]
    runs = [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]
    a, b = max(runs, key=lambda r: r[1] - r[0])
    if (b - a) < max(MIN_RUN_FRAMES, MIN_RUN_FRACTION * n):
        first = breaks[0] if breaks else 1
        raise RegistrationError(first, pair_inl[first])

    chain = [np.eye(3)]
    for j in range(a + 1, b):
        chain.append(chain[-1] @ pair_h[j])
    S = np.diag([scale, scale, 1.0])
    ref_inv = np.linalg.inv(chain[len(chain) // 2])
    transforms = [np.linalg.inv(S) @ ref_inv @ t @ S for t in chain]
    result = Chain(a, b, transforms, [pair_inl[j] for j in range(a + 1, b)])
    result._total = n
    return result
