"""
HPE Aruba CX switches scraper.
Source: https://www.arubanetworks.com/products/switches/
"""
from __future__ import annotations

import logging
import re
from typing import Iterator, Optional
from urllib.parse import urljoin

from .base import BaseScraper, SpecRecord
from .parsers import (
    detect_features, extract_spec_tables, find_datasheet_link,
    map_kv_to_record_fields, pdf_to_text, soup,
)

logger = logging.getLogger("scrapers.aruba")


ARUBA_SERIES = [
    ("CX 6000",  "https://www.arubanetworks.com/products/switches/access/6000-series/"),
    ("CX 6100",  "https://www.arubanetworks.com/products/switches/access/6100-series/"),
    ("CX 6200F", "https://www.arubanetworks.com/products/switches/access/6200-series/"),
    ("CX 6300M", "https://www.arubanetworks.com/products/switches/access/6300-series/"),
    ("CX 6400",  "https://www.arubanetworks.com/products/switches/access/6400-series/"),
    ("CX 8320",  "https://www.arubanetworks.com/products/switches/core-aggregation/8320-series/"),
    ("CX 8325",  "https://www.arubanetworks.com/products/switches/core-aggregation/8325-series/"),
    ("CX 8360",  "https://www.arubanetworks.com/products/switches/core-aggregation/8360-series/"),
    ("CX 8400",  "https://www.arubanetworks.com/products/switches/core-aggregation/8400-series/"),
    ("CX 9300",  "https://www.arubanetworks.com/products/switches/core-aggregation/9300-series/"),
    ("CX 10000", "https://www.arubanetworks.com/products/switches/core-aggregation/10000-series/"),
]


class ArubaScraper(BaseScraper):
    VENDOR = "HPE Aruba"

    def discover_models(self) -> Iterator[tuple[str, str]]:
        for family, url in ARUBA_SERIES:
            html = self.http.get_text(url)
            if not html: continue
            # Aruba SKUs use letter+number codes like JL678A, R9A28A; model numbers
            # like 6300M-48G-PoE4+. Find both.
            models = set(re.findall(
                r"\b(?:CX\s*)?(\d{4,5}[A-Z]?[\-A-Za-z0-9+]*)",
                html,
            ))
            for m in models:
                # filter for likely model strings (must contain a digit and -)
                if "-" not in m or len(m) < 5: continue
                yield f"{family}__{m}", url

    def extract_model(self, name: str, url: str) -> Optional[SpecRecord]:
        family, model = name.split("__", 1)
        html = self.http.get_text(url)
        if not html: return None

        kv = extract_spec_tables(html)
        pdf_url = find_datasheet_link(html, url)
        if pdf_url:
            pdf_bytes = self.http.get(pdf_url)
            if pdf_bytes:
                kv.update(self._pdf_kv(pdf_to_text(pdf_bytes), model))

        if not kv: return None

        fields = map_kv_to_record_fields(kv)
        features = detect_features(html.lower())

        rec = SpecRecord(
            vendor=self.VENDOR,
            family=family,
            model=model,
            features=features,
            nos="ArubaOS-CX",
            use_case=self._use_case(family),
            datasheet_url=pdf_url or url,
            **{k: v for k, v in fields.items() if v is not None},
        )
        return rec

    @staticmethod
    def _use_case(family: str) -> str:
        n = re.findall(r"\d+", family)
        if not n: return "access"
        num = int(n[0])
        if num >= 9300: return "spine"
        if num >= 8000: return "leaf"
        return "access"

    @staticmethod
    def _pdf_kv(text: str, model: str) -> dict:
        out = {}
        in_model_section = True   # default to true unless model is mentioned multiple times
        for line in text.splitlines():
            m = re.match(
                r"^\s*([A-Za-z][A-Za-z /()\-+]{3,50})\s+([\d\.,].{0,200})$", line
            )
            if m and in_model_section:
                out.setdefault(m.group(1).strip(), m.group(2).strip())
        return out
