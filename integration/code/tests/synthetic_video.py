"""Render test videos with a KNOWN camera path from a real rack image.

The scene is a rack image padded with plain wall (above) and floor (below). Each frame is a window of the
scene (position, zoom, roll, blur, brightness) so the stitcher's output can be compared with the truth and
each capture fault can be injected one at a time.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

OUT_W, OUT_H = 1600, 900


def make_scene(rack_image: np.ndarray) -> tuple[np.ndarray, int, int]:
    """Pad the rack image with wall above and floor below. Returns (scene, rack_top, rack_bottom)."""
    h, w = rack_image.shape[:2]
    pad = int(0.14 * h)
    rng = np.random.default_rng(1)

    def plain(rows, colour):
        block = np.full((rows, w, 3), colour, np.float32)
        block += cv2.GaussianBlur(rng.normal(0, 7, (rows, w, 3)).astype(np.float32), (0, 0), 6)   # faint texture
        return np.clip(block, 0, 255).astype(np.uint8)

    scene = np.vstack([plain(pad, (222, 222, 216)), rack_image, plain(pad, (196, 192, 186))])
    return scene, pad, pad + h


def render(
    scene: np.ndarray,
    path: Path,
    *,
    seconds: float = 20.0,
    fps: float = 30.0,
    y_start: float = 0.0,
    y_end: float | None = None,
    window_w: int = 1500,
    zoom: tuple[float, float] = (1.0, 1.0),
    sway_px: float = 0.0,
    roll_deg: tuple[float, float] = (0.0, 0.0),
    blur_px: int = 0,
    flicker: float = 0.0,
    flicker_period_s: float = 1.0,
    shake_share: float = 0.0,
    shake_blur: int = 41,
    noise: float = 2.0,
    out_size: tuple[int, int] = (OUT_W, OUT_H),
    skip: tuple[float, float] | None = None,
) -> Path:
    """Write an mp4 of a camera moving down the scene. `skip=(a, b)` removes the part of the path between
    a and b (fractions), which makes an abrupt jump (frames missing). `flicker` alternates the brightness
    between 1-f and 1+f every `flicker_period_s` (auto-exposure hunting); `shake_share` blurs that share of
    the frames heavily (camera shake)."""
    sh, sw = scene.shape[:2]
    ow, oh = out_size
    win_h = window_w * oh / ow
    n = int(seconds * fps)
    ts = np.linspace(0, 1, n)
    if skip:
        ts = ts[(ts < skip[0]) | (ts > skip[1])]
    y_end = sh - win_h * max(zoom) if y_end is None else y_end
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (ow, oh))
    rng = np.random.default_rng(7)
    cx0 = sw / 2
    for idx, t in enumerate(ts):
        z = zoom[0] + (zoom[1] - zoom[0]) * t
        wh = win_h * z
        cx = cx0 + sway_px * np.sin(2 * np.pi * 1.5 * t)
        cy = y_start + t * (y_end - y_start) + wh / 2
        k = ow / (window_w * z)
        M = cv2.getRotationMatrix2D((cx, cy), roll_deg[0] + (roll_deg[1] - roll_deg[0]) * t, k)
        M[:, 2] += (ow / 2 - cx, oh / 2 - cy)
        frame = cv2.warpAffine(scene, M, (ow, oh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        length = blur_px
        if shake_share and rng.random() < shake_share:
            length = max(length, shake_blur)
        if length > 1:
            kernel = np.zeros((length, length), np.float32)
            kernel[:, length // 2] = 1.0 / length                        # vertical motion blur
            frame = cv2.filter2D(frame, -1, kernel)
        frame = frame.astype(np.float32)
        if flicker:
            frame *= (1 + flicker) if int(t * seconds / flicker_period_s) % 2 else (1 - flicker)
        frame += rng.normal(0, noise, frame.shape)
        writer.write(np.clip(frame, 0, 255).astype(np.uint8))
    writer.release()
    return path
