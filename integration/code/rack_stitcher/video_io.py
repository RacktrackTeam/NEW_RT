"""Reading frames, scoring sharpness and choosing keyframes.

Two passes keep memory low: pass 1 scores every frame (nothing stored), pass 2 keeps only the
chosen keyframes. A 900-frame 1080p video therefore needs ~40 frames in RAM, not ~900.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np

WORK_SIDE = 1280  # long side used for feature matching and sharpness scoring
MIN_FRAMES = 8


class VideoReadError(RuntimeError):
    """The video could not be opened or has too few frames."""


@dataclass
class VideoScan:
    """Result of pass 1."""

    scores: np.ndarray                      # sharpness of every frame (from start_frame on)
    times: np.ndarray                       # timestamp of every frame in seconds, starting at 0
    original_shape: tuple[int, int]         # (height, width) as stored in the file
    frame_shape: tuple[int, int]            # (height, width) after the max_frame_side cap


def _cap_scale(shape: tuple[int, int], max_side: int | None) -> float:
    h, w = shape[:2]
    if not max_side or max(h, w) <= max_side:
        return 1.0
    return max_side / max(h, w)


def iter_frames(path: Path, start: int = 0, max_side: int | None = None, with_time: bool = False) -> Iterator:
    """Yield BGR frames from `start`, optionally capped to `max_side` on the long side.

    With ``with_time=True`` yields ``(frame, seconds)``; seconds come from the container timestamps, so
    they stay correct for variable-frame-rate phone videos.
    """
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise VideoReadError(f"Cannot open video: {path}")
    try:
        index, scale = 0, None
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if index >= start:
                stamp = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
                if scale is None:
                    scale = _cap_scale(frame.shape, max_side)
                if scale < 1.0:
                    frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                yield (frame, stamp) if with_time else frame
            index += 1
    finally:
        cap.release()


def work_scale(shape: tuple[int, int]) -> float:
    """Scale factor from full-resolution frames to the working resolution."""
    return min(1.0, WORK_SIDE / max(shape[:2]))


def to_work(frame: np.ndarray, scale: float) -> np.ndarray:
    return cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else frame


def sharpness(frame: np.ndarray, scale: float) -> float:
    """Mean gradient magnitude of the central region: low for motion-blurred frames."""
    g = cv2.cvtColor(to_work(frame, scale), cv2.COLOR_BGR2GRAY).astype(np.float32)
    h, w = g.shape
    g = g[h // 5 : -h // 5, w // 5 : -w // 5]
    gx, gy = cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1)
    return float(np.mean(np.sqrt(gx * gx + gy * gy)))


def scan_video(path: Path, start: int, max_side: int | None) -> VideoScan:
    """Pass 1: sharpness and timestamp of every frame."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise VideoReadError(f"Cannot open video: {path}")
    original = (int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)), int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)))
    cap.release()

    scores, stamps, shape, scale = [], [], None, 1.0
    for frame, stamp in iter_frames(path, start, max_side, with_time=True):
        if shape is None:
            shape = frame.shape[:2]
            scale = work_scale(shape)
            original = original if all(original) else shape
        scores.append(sharpness(frame, scale))
        stamps.append(stamp)
    if len(scores) < MIN_FRAMES:
        raise VideoReadError(f"Video has too few usable frames ({len(scores)}): {path}")
    times = np.asarray(stamps) - stamps[0]
    if not np.all(np.diff(times) >= 0) or times[-1] <= 0:      # broken timestamps: fall back to uniform spacing
        fps = cv2.VideoCapture(str(path)).get(cv2.CAP_PROP_FPS) or 30.0
        times = np.arange(len(scores)) / fps
    return VideoScan(np.asarray(scores), times, original, shape)


def select_keyframes(scores: np.ndarray, window: int) -> list[int]:
    """Sharpest frame in each consecutive window of `window` frames."""
    picks, i = [], 0
    while i < len(scores):
        j = min(i + window, len(scores))
        picks.append(i + int(np.argmax(scores[i:j])))
        i = j
    return picks


def read_keyframes(path: Path, indices: list[int], start: int, max_side: int | None) -> list[np.ndarray]:
    """Pass 2: load only the chosen frames (indices are relative to `start`)."""
    want = set(indices)
    frames = [f for n, f in enumerate(iter_frames(path, start, max_side)) if n in want]
    if len(frames) != len(want):
        raise VideoReadError("Could not re-read all keyframes (video changed or is not seekable).")
    return frames
