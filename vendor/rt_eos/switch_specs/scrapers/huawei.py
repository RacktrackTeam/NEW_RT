"""
Huawei CloudEngine switches scraper.
Source: https://e.huawei.com/en/products/data-center-switches/
        https://e.huawei.com/en/products/enterprise-switches/

Huawei publishes specs on both .com (English) and .cn (Chinese) sites.
We target the English site. Datasheets are mostly HTML with some PDFs.
"""
from __future__ import annotations

import logging
import re
from typing import Iterator, Optional
from urllib.parse import urljoin

from .base import BaseScraper, SpecRecord
from .parsers import (
    detect_features, extract_spec_tables, find_datasheet_link,
    find_product_image, map_kv_to_record_fields, pdf_to_text, soup,
)

logger = logging.getLogger("scrapers.huawei")


# Huawei CloudEngine + S-series families
HUAWEI_FAMILIES = [
    # Data center
    ("CloudEngine 16800",
     "https://e.huawei.com/en/products/data-center-switches/ce16800-series"),
    ("CloudEngine 12800",
     "https://e.huawei.com/en/products/data-center-switches/ce12800-series"),
    ("CloudEngine 9800",
     "https://e.huawei.com/en/products/data-center-switches/ce9800-series"),
    ("CloudEngine 8800",
     "https://e.huawei.com/en/products/data-center-switches/ce8800-series"),
    ("CloudEngine 6800",
     "https://e.huawei.com/en/products/data-center-switches/ce6800-series"),
    ("CloudEngine 5800",
     "https://e.huawei.com/en/products/data-center-switches/ce5800-series"),
    # Campus
    ("S12700",
     "https://e.huawei.com/en/products/enterprise-switches/campus-switches/s12700-series"),
    ("S7700",
     "https://e.huawei.com/en/products/enterprise-switches/campus-switches/s7700-series"),
    ("S6730",
     "https://e.huawei.com/en/products/enterprise-switches/campus-switches/s6730"),
    ("S5731",
     "https://e.huawei.com/en/products/enterprise-switches/campus-switches/s5731"),
    ("S5700",
     "https://e.huawei.com/en/products/enterprise-switches/campus-switches/s5700-series"),
]


# Match Huawei SKU patterns: CE12800-32Q, S6730-H48X6C, S5731-H24T4XC
HUAWEI_SKU_PATTERNS = [
    re.compile(r"\b(CE\d{4,5}[\-A-Za-z0-9]*)\b"),
    re.compile(r"\b(S\d{4}[\-A-Za-z0-9]+)\b"),
]


class HuaweiScraper(BaseScraper):
    VENDOR = "Huawei"
    DELAY_SEC = 1.5  # Huawei tends to be sensitive to fast crawls

    def discover_models(self) -> Iterator[tuple[str, str]]:
        seen = set()
        for family, url in HUAWEI_FAMILIES:
            html = self.http.get_text(url)
            if not html:
                continue
            for pat in HUAWEI_SKU_PATTERNS:
                for sku in pat.findall(html):
                    # Filter out short prefixes (e.g. "S5731" alone)
                    if len(sku) < 6 or "-" not in sku:
                        continue
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
            nos="VRP" if sku.startswith("CE") else "VRP",
            use_case=self._use_case(family, sku),
            datasheet_url=pdf_url or url,
            image_url=find_product_image(html, base_url=url),
            **{k: v for k, v in fields.items() if v is not None},
        )
        return rec

    @staticmethod
    def _use_case(family: str, sku: str) -> str:
        # CE = CloudEngine (data center)
        if sku.startswith("CE"):
            num = int(re.findall(r"\d+", sku)[0]) if re.findall(r"\d+", sku) else 0
            if num >= 16000: return "core"
            if num >= 9800:  return "spine"
            if num >= 6800:  return "leaf"
            return "leaf"
        # S = campus
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
