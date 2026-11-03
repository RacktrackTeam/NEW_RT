"""Typed result objects returned by the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class TextDetection:
    """One piece of text found in the image.

    Coordinates are always in the ORIGINAL image's pixel space, regardless
    of which preprocessing variant produced the hit.
    """

    text: str
    confidence: float
    #: Axis-aligned box [x1, y1, x2, y2] (ints, original image coordinates).
    box: List[int]
    #: 4-point polygon [[x, y] * 4] as detected (original image coordinates).
    polygon: List[List[int]]
    #: Which preprocessing variant produced this reading.
    variant: str = "original"

    @property
    def width(self) -> int:
        return self.box[2] - self.box[0]

    @property
    def height(self) -> int:
        return self.box[3] - self.box[1]

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "confidence": round(float(self.confidence), 4),
            "box": [int(v) for v in self.box],
            "polygon": [[int(x), int(y)] for x, y in self.polygon],
            "variant": self.variant,
        }


@dataclass
class OCRResult:
    """Everything found in one image."""

    source: str
    image_width: int
    image_height: int
    detections: List[TextDetection] = field(default_factory=list)
    #: Device identification (make + model), filled by SwitchTextReader.
    device: Optional[object] = None  # switch_ocr.identify.DeviceID
    elapsed_ms: float = 0.0
    engine: str = ""
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def full_text(self) -> str:
        """All detected text, top-to-bottom / left-to-right, one line each."""
        ordered = sorted(self.detections, key=lambda d: (d.box[1], d.box[0]))
        return "\n".join(d.text for d in ordered)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "engine": self.engine,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "error": self.error,
            "device": self.device.to_dict() if self.device else None,
            "detection_count": len(self.detections),
            "detections": [d.to_dict() for d in self.detections],
        }
