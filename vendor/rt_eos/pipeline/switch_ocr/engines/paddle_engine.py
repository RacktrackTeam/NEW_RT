"""PaddleOCR (PP-OCRv5) backend — the recommended engine.

PP-OCRv5's DB++ detector and SVTR-based recognizer are state-of-the-art
among free models for exactly the hard cases this project targets: tiny,
blurred, low-contrast scene text. Runs well on CPU (mobile tier).
"""
from __future__ import annotations

import logging
import threading
from typing import List

import numpy as np

from ..config import OCRConfig
from .base import BaseEngine, RawDetection

log = logging.getLogger("switch_ocr")

_MOBILE_DET = "PP-OCRv5_mobile_det"
_MOBILE_REC = {"en": "en_PP-OCRv5_mobile_rec"}
_SERVER_DET = "PP-OCRv5_server_det"
_SERVER_REC: dict = {}  # let PaddleOCR pick the server rec by lang/version


class PaddleEngine(BaseEngine):
    """Lazily-initialised, thread-safe PaddleOCR runner."""

    def __init__(self, cfg: OCRConfig):
        self.cfg = cfg
        self._ocr = None
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return (f"PaddleOCR/{self.cfg.ocr_version}-{self.cfg.model_tier} "
                f"({self.cfg.lang}, {self.cfg.device})")

    # ------------------------------------------------------------------ #
    def _build_kwargs(self) -> dict:
        cfg = self.cfg
        kwargs = dict(
            lang=cfg.lang,
            ocr_version=cfg.ocr_version,
            device=cfg.device,
            cpu_threads=cfg.cpu_threads,
            enable_mkldnn=cfg.enable_mkldnn,
            # Equipment photos are not documents: page-level orientation
            # classification and unwarping would distort them and waste time.
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=cfg.use_textline_orientation,
            # Detection sensitivity — tuned for faint/small text.
            text_det_limit_side_len=cfg.det_limit_side_len,
            text_det_limit_type=cfg.det_limit_type,
            text_det_thresh=cfg.det_thresh,
            text_det_box_thresh=cfg.det_box_thresh,
            text_det_unclip_ratio=cfg.det_unclip_ratio,
            text_recognition_batch_size=cfg.rec_batch_size,
            # We filter by confidence later, with full visibility.
            text_rec_score_thresh=0.0,
        )
        if cfg.model_tier == "server":
            kwargs["text_detection_model_name"] = _SERVER_DET
            rec = _SERVER_REC.get(cfg.lang)
            if rec:
                kwargs["text_recognition_model_name"] = rec
        else:
            kwargs["text_detection_model_name"] = _MOBILE_DET
            rec = _MOBILE_REC.get(cfg.lang)
            if rec:
                kwargs["text_recognition_model_name"] = rec
        return kwargs

    def _get_ocr(self):
        if self._ocr is not None:
            return self._ocr
        with self._lock:
            if self._ocr is not None:
                return self._ocr
            from paddleocr import PaddleOCR  # heavy import, done once

            kwargs = self._build_kwargs()
            log.info("Loading %s (first run downloads models) ...", self.name)
            try:
                self._ocr = PaddleOCR(**kwargs)
            except TypeError as exc:
                # Forward-compat: if a kwarg is renamed in a future PaddleOCR
                # release, retry with the universally-stable core set.
                log.warning("PaddleOCR rejected a parameter (%s); retrying with core set", exc)
                core = {k: kwargs[k] for k in
                        ("lang", "ocr_version", "device", "use_textline_orientation")}
                self._ocr = PaddleOCR(**core)
            log.info("Engine ready: %s", self.name)
            return self._ocr

    def warmup(self) -> None:
        self._get_ocr()

    # ------------------------------------------------------------------ #
    def run(self, image_bgr: np.ndarray, variant: str = "") -> List[RawDetection]:
        ocr = self._get_ocr()
        with self._lock:  # Paddle predictors are not re-entrant
            results = ocr.predict(image_bgr)

        out: List[RawDetection] = []
        for res in results or []:
            texts = _field(res, "rec_texts") or []
            scores = _field(res, "rec_scores")
            scores = list(scores) if scores is not None else []
            polys = _field(res, "rec_polys")
            if polys is None:
                polys = _field(res, "dt_polys")
            if polys is None:
                polys = []
            for i, text in enumerate(texts):
                score = float(scores[i]) if i < len(scores) else 0.0
                poly = np.asarray(polys[i], dtype=np.float64) if i < len(polys) else None
                if poly is None or poly.size == 0:
                    continue
                out.append((poly.reshape(-1, 2), str(text), score))
        return out


def _field(res, name):
    """Read a field from a PaddleOCR result across API styles (attr/mapping/.json)."""
    val = getattr(res, name, None)
    if val is not None:
        return val
    try:
        return res[name]
    except Exception:
        pass
    try:
        inner = res.json
        if isinstance(inner, dict):
            return inner.get("res", inner).get(name)
    except Exception:
        pass
    return None
