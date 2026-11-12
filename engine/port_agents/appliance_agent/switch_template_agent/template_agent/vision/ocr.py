"""OCR backends: local EasyOCR, or Claude vision. Both return OcrResult."""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from functools import lru_cache

import cv2
import numpy as np
from pydantic import BaseModel


@dataclass
class OcrToken:
    text: str
    conf: float
    box: np.ndarray  # 4x2 polygon in original-image pixels


@dataclass
class OcrResult:
    backend: str
    tokens: list[OcrToken] = field(default_factory=list)
    vendor: str | None = None   # only set by backends that read make/model directly
    model: str | None = None

    @property
    def texts(self) -> list[str]:
        out = [t.text for t in self.tokens]
        if self.model:
            out = [self.model, *out]
        return out


def _reading_order(tokens: list[OcrToken]) -> list[OcrToken]:
    if not tokens:
        return tokens
    h = np.median([np.ptp(t.box[:, 1]) for t in tokens]) or 1
    return sorted(tokens, key=lambda t: (round(t.box[:, 1].mean() / h), t.box[:, 0].min()))


# ---------------------------------------------------------------- EasyOCR
@lru_cache(maxsize=1)
def _easyocr_reader():
    import easyocr
    return easyocr.Reader(["en"], gpu=False, verbose=False)


def _variants(img: np.ndarray, target_height: int):
    scale = max(1.0, target_height / img.shape[0])
    big = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_LANCZOS4)
    g = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(cv2.cvtColor(big, cv2.COLOR_BGR2GRAY))
    sharp = cv2.addWeighted(g, 1.8, cv2.GaussianBlur(g, (0, 0), 3), -0.8, 0)
    # plain, sharpened, and inverted (white-on-dark vs dark-on-white labels)
    return scale, (g, sharp, 255 - sharp)


def easyocr_read(img: np.ndarray, target_height: int = 600, min_conf: float = 0.2,
                 label_zone: float = 0.4) -> OcrResult:
    """Rack photos are ~120-200 px tall, so upscale, boost contrast and try several
    variants. The make/model is printed at the left end of the panel, so that zone
    is read again at higher magnification."""
    reader = _easyocr_reader()
    tokens: list[OcrToken] = []
    w = img.shape[1]
    zones = [(img, 0, target_height)]
    if label_zone:
        zones.append((img[:, : int(w * label_zone)], 0, int(target_height * 1.35)))
    for region, x_off, th in zones:
        scale, variants = _variants(region, th)
        for v in variants:
            for box, text, conf in reader.readtext(v, text_threshold=0.5, low_text=0.25,
                                                   link_threshold=0.3):
                if conf < min_conf or not text.strip():
                    continue
                box = np.asarray(box, dtype=np.float32) / scale + [x_off, 0]
                # drop duplicates found by several variants / zones
                c = box.mean(0)
                dup = next((t for t in tokens if np.linalg.norm(t.box.mean(0) - c) < 6
                            and t.text.upper() == text.upper()), None)
                if dup is None:
                    tokens.append(OcrToken(text.strip(), float(conf), box))
                elif conf > dup.conf:
                    dup.conf = float(conf)
    return OcrResult("easyocr", _reading_order(tokens))


# ---------------------------------------------------------------- Claude vision
class _SwitchLabel(BaseModel):
    vendor: str
    model: str
    other_text: list[str]
    confidence: float