"""Tesseract backend — zero-ML-framework fallback.

Noticeably weaker than PP-OCRv5 on tiny/blurry scene text, but it installs
anywhere (apt/brew/choco + pytesseract) and keeps the pipeline usable when
the deep-learning backends can't be installed. The preprocessing variants
do a lot of the rescue work for it.
"""
from __future__ import annotations

import logging
from typing import List

import numpy as np

from ..config import OCRConfig
from .base import BaseEngine, RawDetection

log = logging.getLogger("switch_ocr")


class TesseractEngine(BaseEngine):
    def __init__(self, cfg: OCRConfig):
        self.cfg = cfg
        self._checked = False

    @property
    def name(self) -> str:
        return f"Tesseract (psm {self.cfg.tesseract_psm}, {self._lang()})"

    def _lang(self) -> str:
        # Map common ISO codes to tesseract's 3-letter data names.
        return {"en": "eng", "ch": "chi_sim", "fr": "fra", "de": "deu",
                "es": "spa", "it": "ita", "pt": "por", "ru": "rus",
                "ja": "jpn", "ko": "kor"}.get(self.cfg.lang, self.cfg.lang)

    def _ensure(self):
        if self._checked:
            return
        import pytesseract
        try:
            pytesseract.get_tesseract_version()
        except Exception as exc:  # binary missing
            raise RuntimeError(
                "Tesseract binary not found. Install it (e.g. "
                "`sudo apt install tesseract-ocr` / `brew install tesseract`)"
            ) from exc
        self._checked = True

    def warmup(self) -> None:
        self._ensure()

    def run(self, image_bgr: np.ndarray, variant: str = "") -> List[RawDetection]:
        """Union of two segmentation modes: sparse (11) catches scattered
        labels, block (6) reads dense plates the sparse mode fragments.
        The block pass runs only on the contrast-enhanced variant — it is
        where it helps, and on big noisy upscales it just burns minutes.
        The pipeline's merge step deduplicates the overlap."""
        self._ensure()
        import cv2

        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        psms = [self.cfg.tesseract_psm]
        if variant in ("enhanced", "zoom", ""):  # zoom crops are small: dual pass is cheap
            psms.append(6 if self.cfg.tesseract_psm != 6 else 11)
        out: List[RawDetection] = []
        for psm in psms:
            out.extend(self._run_psm(rgb, psm))
        return out

    def _run_psm(self, rgb: np.ndarray, psm: int) -> List[RawDetection]:
        import pytesseract

        config = f"--oem 1 --psm {psm}"
        try:
            data = pytesseract.image_to_data(
                rgb, lang=self._lang(), config=config,
                output_type=pytesseract.Output.DICT,
                timeout=self.cfg.tesseract_timeout,
            )
        except RuntimeError:  # pass exceeded its time budget — skip it
            log.warning("tesseract psm %d pass timed out (>%.0fs) — skipped",
                        psm, self.cfg.tesseract_timeout)
            return []

        # Group word boxes into lines (comparable to the DL engines' output).
        lines = {}
        n = len(data["text"])
        for i in range(n):
            word = (data["text"][i] or "").strip()
            conf = float(data["conf"][i])
            if not word or conf < 0:
                continue
            key = (data.get("page_num", [0] * n)[i], data["block_num"][i],
                   data["par_num"][i], data["line_num"][i])
            x, y = data["left"][i], data["top"][i]
            w, h = data["width"][i], data["height"][i]
            entry = lines.setdefault(key, {"words": [], "confs": [],
                                           "x1": x, "y1": y, "x2": x + w, "y2": y + h})
            entry["words"].append(word)
            entry["confs"].append(conf)
            entry["x1"] = min(entry["x1"], x)
            entry["y1"] = min(entry["y1"], y)
            entry["x2"] = max(entry["x2"], x + w)
            entry["y2"] = max(entry["y2"], y + h)

        out: List[RawDetection] = []
        for entry in lines.values():
            text = " ".join(entry["words"])
            conf = (sum(entry["confs"]) / len(entry["confs"])) / 100.0
            x1, y1, x2, y2 = entry["x1"], entry["y1"], entry["x2"], entry["y2"]
            poly = np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float64)
            out.append((poly, text, conf))
        return out
