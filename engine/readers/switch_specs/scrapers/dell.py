"""
Dell PowerSwitch scraper.
Source: https://www.dell.com/en-us/shop/dell-networking-switches
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

logger = logging.getLogger("scrapers.dell")


# Dell publishes spec sheets per family - direct URLs to series pages
DELL_FAMILIES = [
    ("PowerSwitch S-series",
     "https://www.dell.com/en-us/shop/povw/networking-s-series"),
    ("PowerSwitch Z-series",
     "https://www.dell.com/en-us/shop/povw/networking-z-series"),
    ("PowerSwitch N-series",
     "https://www.dell.com/en-us/shop/povw/networking-n-series"),
]


class DellScraper(BaseScraper):
    VENDOR = "Dell"

    def discover_models(self) -> Iterator[tuple[str, str]]:
        for family, url in DELL_FAMILIES:
            html = self.http.get_text(url)
            if not html: continue
            # Look for SKU patterns: S4128F-ON, Z9332F-ON, N3248TE-ON
            for sku in set(re.findall(
                r"\b([SZN]\d{4,5}[A-Z]*(?:-ON)?)\b", html
            )):
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

        rec = SpecRecord(
            vendor=self.VENDOR,
            family=family,
            model=sku,
            sku=sku,
            features=features,
            nos="OS10 / SONiC",
            use_case=self._guess_use_case(sku),
            datasheet_url=pdf_url or url,
            **{k: v for k, v in fields.items() if v is not None},
        )
        return rec

    @staticmethod
    def _pdf_kv(text: str) -> dict:
        out = {}
        for line in text.splitlines():
            m = re.match(
                r"^\s*([A-Za-z][A-Za-z /()\-+]{3,40})\s+(\d+.*)$", line
            )
            if m:
                out.setdefault(m.group(1).strip(), m.group(2).strip())
        return out

    @staticmethod
    def _guess_use_case(sku: str) -> str:
        if sku.startswith("Z") or "9" in sku[:3]: return "spine"
        if sku.startswith("S5") or sku.startswith("S6"): return "leaf"
        if sku.startswith("S4"): return "leaf"
        if sku.startswith("N"): return "access"
        return "leaf"
