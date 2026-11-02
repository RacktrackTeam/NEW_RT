"""RapidOCR backend — PP-OCR models running on ONNX Runtime.

Same model family as the Paddle backend but with a much lighter dependency
footprint (no paddlepaddle). Slightly behind PaddleOCR's newest models in
accuracy; a good middle ground for constrained deployments.
"""
from __future__ import annotations

import logging
import threading
from typing import List

import numpy as np

from ..config import OCRConfig
from .base import BaseEngine, RawDetection

log = logging.getLogger("switch_ocr")


class RapidEngine(BaseEngine):
    def __init__(self, cfg: OCRConfig):
        self.cfg = cfg
        self._ocr = None
        self._modern = True
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "RapidOCR/onnxruntime"

    def _get(self):
        if self._ocr is not None:
            return self._ocr
        with self._lock:
            if self._ocr is not None:
                return self._ocr
            try:  # rapidocr >= 2.0
                from rapidocr import RapidOCR
                self._modern = True
            except ImportError:  # legacy package name
                from rapidocr_onnxruntime import RapidOCR
                self._modern = False
            log.info("Loading RapidOCR ...")
            cfg = self.cfg
            # CRITICAL: raise the detector's input-size cap. RapidOCR's
            # default (~736 px) would undo the pipeline's upscaling and make
            # tiny model labels undetectable again.
            if self._modern:
                params = {
                    "Det.limit_side_len": cfg.det_limit_side_len,
                    "Det.limit_type": cfg.det_limit_type,
                    "Det.thresh": cfg.det_thresh,
                    "Det.box_thresh": cfg.det_box_thresh,
                    "Det.unclip_ratio": cfg.det_unclip_ratio,
                }
                try:
                    self._ocr = RapidOCR(params=params)
                except Exception as exc:
                    log.warning("RapidOCR rejected tuned params (%s); using defaults "
                                "— tiny text recall will suffer", exc)
                    self._ocr = RapidOCR()
            else:
                try:
                    self._ocr = RapidOCR(
                        det_limit_side_len=cfg.det_limit_side_len,
                        det_limit_type=cfg.det_limit_type,
                        det_thresh=cfg.det_thresh,
                        det_box_thresh=cfg.det_box_thresh,
                        det_unclip_ratio=cfg.det_unclip_ratio,
                    )
                except TypeError as exc:
                    log.warning("RapidOCR rejected tuned params (%s); using defaults "
                                "— tiny text recall will suffer", exc)
                    self._ocr = RapidOCR()
            return self._ocr

    def warmup(self) -> None:
        self._get()

    def run(self, image_bgr: np.ndarray, variant: str = "") -> List[RawDetection]:
        ocr = self._get()
        with self._lock:
            result = ocr(image_bgr)

        out: List[RawDetection] = []
        # rapidocr 2.x returns an object with .boxes/.txts/.scores
        if hasattr(result, "boxes") and hasattr(result, "txts"):
            boxes = result.boxes if result.boxes is not None else []
            txts = result.txts or []
            scores = result.scores or []
            for i, text in enumerate(txts):
                poly = np.asarray(boxes[i], dtype=np.float64).reshape(-1, 2) \
                    if i < len(boxes) else None
                if poly is None or poly.size == 0:
                    continue
                score = float(scores[i]) if i < len(scores) else 0.0
                out.append((poly, str(text), score))
            return out

        # rapidocr_onnxruntime 1.x returns (list_of_[box, text, score], elapse)
        if isinstance(result, tuple):
            result = result[0]
        for item in result or []:
            box, text, score = item[0], item[1], float(item[2])
            poly = np.asarray(box, dtype=np.float64).reshape(-1, 2)
            out.append((poly, str(text), score))
        return out
