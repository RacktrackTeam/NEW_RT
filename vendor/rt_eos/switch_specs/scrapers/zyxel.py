"""
Zyxel switches scraper.
Source: https://www.zyxel.com/global/en/products/switch

Zyxel's product pages are clean HTML. Most data is in spec tables.
"""
from __future__ import annotations

import logging
import re
from typing import Iterator, Optional

from .base import BaseScraper, SpecRecord
from .parsers import (
    detect_features, extract_spec_tables, find_datasheet_link,
    find_product_image, map_kv_to_record_fields, pdf_to_text,
)

logger = logging.getLogger("scrapers.zyxel")


ZYXEL_FAMILIES = [
    # Aggregation L3
    ("XGS4600",  "https://www.zyxel.com/global/en/products/switch/l3-aggregation-switch-xgs4600-series"),
    ("XGS3700",  "https://www.zyxel.com/global/en/products/switch/l2-aggregation-switch-xgs3700-series"),
    # Access L3
    ("XS3800",   "https://www.zyxel.com/global/en/products/switch/12-port-multi-gigabit-switch-xs3800-28"),
    ("XS1930",   "https://www.zyxel.com/global/en/products/switch/12-port-10g-smart-managed-switch-xs1930-series"),
    # Access L2
    ("GS1920",   "https://www.zyxel.com/global/en/products/switch/24-48-port-gbe-smart-managed-switch-gs1920-series-v2"),
    ("GS1900",   "https://www.zyxel.com/global/en/products/switch/8-24-48-port-gbe-smart-managed-switch-gs1900-series-v2"),
    ("XMG1930",  "https://www.zyxel.com/global/en/products/switch/8-16-24-port-2-5g-multi-gigabit-smart-managed-switch-xmg1930-series"),
    # Index
    ("All",      "https://www.zyxel.com/global/en/products/switch"),
]


# Zyxel SKU pattern: XGS4600-32F, GS1920-24HPv2, XS1930-12HP
ZYXEL_SKU = re.compile(
    r"\b((?:XGS|XS|GS|XMG|MGS)\d{4}-\d+\w*(?:v\d+)?)\b",
    re.IGNORECASE,
)


class ZyxelScraper(BaseScraper):
    VENDOR = "Zyxel"
    DELAY_SEC = 1.0

    def discover_models(self) -> Iterator[tuple[str, str]]:
        seen = set()
        for family, url in ZYXEL_FAMILIES:
            html = self.http.get_text(url)
            if not html:
                continue
            for sku in ZYXEL_SKU.findall(html):
                # Normalize case
                sku = sku.upper().replace("V2", "v2").replace("V3", "v3")
                # Restore lowercase v
                sku = re.sub(r"V(\d)$", r"v\1", sku)
                key = (family, sku)
                if key in seen:
                    continue
                seen.add(key)
                yield f"{family}__{sku}", url

    def extract_model(self, name: str, url: str) -> Optional[SpecRecord]:
        family, sku = name.split("__", 1)
        html = self.http.get_text(url)
        if not html:
            return None

        kv = extract_spec_tables(html)
        pdf_url = find_datasheet_link(html, url)
        if pdf_url:
            pdf_bytes = self.http.get(pdf_url)
            if pdf_bytes:
                kv.update(self._pdf_kv(pdf_to_text(pdf_bytes)))

        if not kv:
            return None

        fields = map_kv_to_record_fields(kv)
        features = detect_features(html.lower())

        rec = SpecRecord(
            vendor=self.VENDOR,
            family=family,
            model=sku,
            sku=sku,
            features=features,
            nos="ZyNOS",
            use_case=self._use_case(family),
            datasheet_url=pdf_url or url,
            image_url=find_product_image(html, base_url=url),
            **{k: v for k, v in fields.items() if v is not None},
        )
        return rec

    @staticmethod
    def _use_case(family: str) -> str:
        if family.startswith("XGS46"): return "aggregation"
        if family.startswith("XGS37"): return "aggregation"
        if family.startswith("XS"):    return "aggregation"
        return "access"

    @staticmethod
    def _pdf_kv(text: str) -> dict:
        out = {}
        for line in text.splitlines():
            m = re.match(
                r"^\s*([A-Za-z][A-Za-z0-9 /()\-+]{3,50})\s+([\d\.,].{0,200})$",
                line,
            )
            if m:
                out.setdefault(m.group(1).strip(), m.group(2).strip())
        return out
