"""Engine interface shared by all OCR backends."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Tuple

import numpy as np

#: (polygon Nx2 float array, text, confidence 0..1)
RawDetection = Tuple[np.ndarray, str, float]


class BaseEngine(ABC):
    """One OCR backend. Implementations must be thread-safe in :meth:`run`."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable engine identifier (goes into results/logs)."""

    @abstractmethod
    def run(self, image_bgr: np.ndarray, variant: str = "") -> List[RawDetection]:
        """OCR one BGR image and return raw line-level detections.

        ``variant`` is the preprocessing-variant name ("original",
        "enhanced", "upscaled") — engines may use it to pick
        cost/quality trade-offs, or ignore it.
        """

    def warmup(self) -> None:  # pragma: no cover - trivial default
        """Load models eagerly (default: no-op; first run() loads lazily)."""
