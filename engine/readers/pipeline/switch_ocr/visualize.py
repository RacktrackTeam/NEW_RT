"""Draw detections onto a copy of the original image."""
from __future__ import annotations

from typing import List

import cv2
import numpy as np

from .types import TextDetection

_BOX = (80, 220, 60)       # green boxes
_LABEL_BG = (30, 30, 30)   # dark label background
_LABEL_FG = (255, 255, 255)


def annotate(image_bgr: np.ndarray, detections: List[TextDetection]) -> np.ndarray:
    """Return a new image with polygons, text and confidence drawn on."""
    canvas = image_bgr.copy()
    h, w = canvas.shape[:2]
    thickness = max(1, round(min(h, w) / 500))
    font_scale = max(0.4, min(h, w) / 1200)

    for det in detections:
        pts = np.asarray(det.polygon, dtype=np.int32).reshape(-1, 1, 2)
        cv2.polylines(canvas, [pts], isClosed=True, color=_BOX, thickness=thickness)

        label = f"{det.text} {det.confidence:.2f}"
        (tw, th), baseline = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness
        )
        x1, y1 = det.box[0], det.box[1]
        ty = y1 - 4
        if ty - th - baseline < 0:  # label would leave the frame -> put below
            ty = det.box[3] + th + baseline + 4
        tx = min(x1, max(0, w - tw - 2))
        cv2.rectangle(
            canvas,
            (tx, ty - th - baseline),
            (tx + tw + 2, ty + baseline // 2),
            _LABEL_BG,
            -1,
        )
        cv2.putText(
            canvas, label, (tx + 1, ty), cv2.FONT_HERSHEY_SIMPLEX,
            font_scale, _LABEL_FG, thickness, cv2.LINE_AA,
        )
    return canvas
