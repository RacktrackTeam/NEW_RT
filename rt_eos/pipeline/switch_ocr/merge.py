"""Merging + filtering of detections coming from multiple OCR passes."""
from __future__ import annotations

from typing import List

from .types import TextDetection


def _iou(a: List[int], b: List[int]) -> float:
    """Intersection-over-union of two [x1, y1, x2, y2] boxes."""
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _contains(outer: List[int], inner: List[int], tol: float = 0.90) -> bool:
    """True when >= *tol* of *inner*'s area lies inside *outer*."""
    ix1, iy1 = max(outer[0], inner[0]), max(outer[1], inner[1])
    ix2, iy2 = min(outer[2], inner[2]), min(outer[3], inner[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return area > 0 and inter / area >= tol


def merge_detections(
    detections: List[TextDetection],
    iou_thresh: float = 0.5,
    min_confidence: float = 0.35,
    drop_empty: bool = True,
) -> List[TextDetection]:
    """Deduplicate readings of the same physical text across passes.

    Strategy: sort by a quality score (confidence, then text length as a
    tie-breaker — a fuller reading of the same label beats a truncated one),
    then greedily keep a detection unless it overlaps an already-kept one
    (IoU over *iou_thresh*, or near-total containment either way).
    """
    candidates = []
    for d in detections:
        text = d.text.strip()
        if drop_empty and not text:
            continue
        if d.confidence < min_confidence:
            continue
        if d.box[2] <= d.box[0] or d.box[3] <= d.box[1]:
            continue
        d.text = text
        candidates.append(d)

    candidates.sort(key=lambda d: (d.confidence, len(d.text)), reverse=True)

    kept: List[TextDetection] = []
    for cand in candidates:
        duplicate = False
        for existing in kept:
            if (
                _iou(cand.box, existing.box) >= iou_thresh
                or _contains(existing.box, cand.box)
                or _contains(cand.box, existing.box)
            ):
                duplicate = True
                break
        if not duplicate:
            kept.append(cand)

    # Present results in natural reading order.
    kept.sort(key=lambda d: (d.box[1], d.box[0]))
    return kept
