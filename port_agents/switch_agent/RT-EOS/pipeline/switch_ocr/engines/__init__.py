"""OCR backend registry with auto-detection.

Priority for ``engine="auto"``: PaddleOCR (best accuracy on small/blurry
text) -> RapidOCR (same models on ONNX Runtime) -> Tesseract (universal
fallback).
"""
from __future__ import annotations

import importlib.util
import logging
from typing import List

from ..config import OCRConfig
from .base import BaseEngine, RawDetection

log = logging.getLogger("switch_ocr")

__all__ = ["BaseEngine", "RawDetection", "create_engine", "create_all_engines",
          "available_engines"]


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def available_engines() -> list:
    """Names of backends whose dependencies are importable right now."""
    found = []
    if _installed("paddleocr"):
        found.append("paddle")
    if _installed("rapidocr") or _installed("rapidocr_onnxruntime"):
        found.append("rapidocr")
    if _installed("pytesseract"):
        found.append("tesseract")
    return found


def _instantiate(cfg: OCRConfig, choice: str) -> BaseEngine:
    if choice == "paddle":
        from .paddle_engine import PaddleEngine
        return PaddleEngine(cfg)
    if choice == "rapidocr":
        from .rapidocr_engine import RapidEngine
        return RapidEngine(cfg)
    if choice == "tesseract":
        from .tesseract_engine import TesseractEngine
        return TesseractEngine(cfg)
    raise ValueError(f"Unknown engine: {choice!r}")


def create_engine(cfg: OCRConfig) -> BaseEngine:
    """Instantiate the configured (or best available) backend."""
    choice = cfg.engine
    if choice == "auto":
        avail = available_engines()
        if not avail:
            raise RuntimeError(
                "No OCR backend installed. Install one of:\n"
                "  pip install paddleocr paddlepaddle   (recommended — best accuracy)\n"
                "  pip install rapidocr onnxruntime     (lightweight alternative)\n"
                "  pip install pytesseract  + the tesseract binary (fallback)"
            )
        choice = avail[0]
        if choice != "paddle":
            log.warning(
                "PaddleOCR not installed — falling back to '%s'. For the best "
                "results on tiny/blurry text run: pip install paddleocr paddlepaddle",
                choice,
            )
    return _instantiate(cfg, choice)


def create_all_engines(cfg: OCRConfig) -> List[BaseEngine]:
    """Instantiate every installed backend for ensemble OCR.

    More engines means more independent chances to read the make/model
    correctly — each backend has different failure modes on the same
    photo. Results are pooled by the caller (raw detections merge like
    any other preprocessing pass), so this is a pure recall win at the
    cost of runtime. Falls back to a single engine when only one backend
    is importable.
    """
    avail = available_engines()
    if not avail:
        raise RuntimeError(
            "No OCR backend installed. Install one of:\n"
            "  pip install paddleocr paddlepaddle   (recommended — best accuracy)\n"
            "  pip install rapidocr onnxruntime     (lightweight alternative)\n"
            "  pip install pytesseract  + the tesseract binary (fallback)"
        )
    return [_instantiate(cfg, name) for name in avail]
