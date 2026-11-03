"""
NVIDIA Spectrum / Mellanox switches scraper.
Source: https://www.nvidia.com/en-us/networking/ethernet-switching/
"""
from __future__ import annotations

import logging
import re
from typing import Iterator, Optional

from .base import BaseScraper, SpecRecord
from .parsers import (
    detect_features, extract_spec_tables, find_datasheet_link,
    map_kv_to_record_fields, pdf_to_text, soup,
)

logger = logging.getLogger("scrapers.nvidia")


NVIDIA_SERIES = [
    ("Spectrum SN2000",
     "https://www.nvidia.com/en-us/networking/ethernet-switching/spectrum-sn2000/"),
    ("Spectrum-2 SN3000",
     "https://www.nvidia.com/en-us/networking/ethernet-switching/spectrum-2-sn3000/"),
    ("Spectrum-3 SN4000",
     "https://www.nvidia.com/en-us/networking/ethernet-switching/spectrum-3-sn4000/"),
    ("Spectrum-4 SN5000",
     "https://www.nvidia.com/en-us/networking/ethernet-switching/spectrum-x/"),
]


class NvidiaScraper(BaseScraper):
    VENDOR = "NVIDIA"

    def discover_models(self) -> Iterator[tuple[str, str]]:
        for family, url in NVIDIA_SERIES:
            html = self.http.get_text(url)
            if not html: continue
            # NVIDIA SKUs: SN2010, SN2410, SN3700, SN4700, SN5600 etc.
            for sku in set(re.findall(r"\b(SN\d{4}\w*)\b", html)):
                yield f"{family}__{sku}", url

    def extract_model(self, name: str, url: str) -> Optional[SpecRecord]:
        family, sku = name.split("__", 1)
        html = self.http.get_text(url)
        if not html: return None

        kv = extract_spec_tables(html)
        pdf_url = find_datasheet_link(html, url)
        if pdf_url:
            pdf_bytes = self.http.get(pdf_url)
            if pdf_bytes:
                kv.update(self._pdf_kv(pdf_to_text(pdf_bytes)))

        if not kv: return None

        fields = map_kv_to_record_fields(kv)
        features = detect_features(html.lower())
        # NVIDIA-specific feature detection
        if "roce" in html.lower(): features.append("RoCE")
        if "adaptive routing" in html.lower(): features.append("Adaptive routing")
        if "spectrum-x" in html.lower() or "spectrumx" in html.lower():
            features.append("Spectrum-X")

        rec = SpecRecord(
            vendor=self.VENDOR,
            family=family,
            model=sku,
            sku=f"M{sku}",  # MSN2010 etc.
            features=list(set(features)),
            nos="Cumulus Linux / SONiC",
            use_case="leaf" if sku.startswith(("SN20", "SN24")) else "spine",
            datasheet_url=pdf_url or url,
            **{k: v for k, v in fields.items() if v is not None},
        )
        return rec

    @staticmethod
    def _pdf_kv(text: str) -> dict:
        out = {}
        for line in text.splitlines():
            m = re.match(r"^\s*([A-Za-z][A-Za-z /()\-]{3,50})\s*[:\.]?\s+(\d.*)$", line)
            if m:
                out.setdefault(m.group(1).strip(), m.group(2).strip())
        return out
