"""
Extreme Networks switches scraper.
Source: https://www.extremenetworks.com/products/switching-routing/
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

logger = logging.getLogger("scrapers.extreme")


# Extreme's main product families - direct URLs to family pages
EXTREME_FAMILIES = [
    ("ExtremeSwitching 5000",
     "https://www.extremenetworks.com/products/extremeswitching/5000-series/"),
    ("ExtremeSwitching 5300",
     "https://www.extremenetworks.com/products/extremeswitching/5300-series/"),
    ("ExtremeSwitching 5400",
     "https://www.extremenetworks.com/products/extremeswitching/5400-series/"),
    ("ExtremeSwitching 5500",
     "https://www.extremenetworks.com/products/extremeswitching/5500-series/"),
    ("ExtremeSwitching 5700",
     "https://www.extremenetworks.com/products/extremeswitching/5700-series/"),
    ("ExtremeSwitching 5800",
     "https://www.extremenetworks.com/products/extremeswitching/5800-series/"),
    ("ExtremeSwitching 7000",
     "https://www.extremenetworks.com/products/extremeswitching/7000-series/"),
    ("ExtremeSwitching 7500",
     "https://www.extremenetworks.com/products/extremeswitching/7500-series/"),
    ("ExtremeSwitching 8000",
     "https://www.extremenetworks.com/products/extremeswitching/8000-series/"),
    ("ExtremeSwitching 8500",
     "https://www.extremenetworks.com/products/extremeswitching/8500-series/"),
    # Older but still in production
    ("ExtremeSwitching X440-G2",
     "https://www.extremenetworks.com/products/extremeswitching/x440-g2-series/"),
    ("ExtremeSwitching X465",
     "https://www.extremenetworks.com/products/extremeswitching/x465-series/"),
    ("ExtremeSwitching X590",
     "https://www.extremenetworks.com/products/extremeswitching/x590-series/"),
    ("ExtremeSwitching X695",
     "https://www.extremenetworks.com/products/extremeswitching/x695-series/"),
    ("ExtremeSwitching X870",
     "https://www.extremenetworks.com/products/extremeswitching/x870-series/"),
]


class ExtremeScraper(BaseScraper):
    VENDOR = "Extreme Networks"

    def discover_models(self) -> Iterator[tuple[str, str]]:
        for family, url in EXTREME_FAMILIES:
            html = self.http.get_text(url)
            if not html:
                continue
            # Match SKUs like "5520-48W", "X465-48P", "VSP 7400-48Y"
            patterns = [
                r"\b(5\d{3}-\d+[A-Z]*[\w-]*)\b",          # 5520-48W
                r"\b(X\d{3,4}[-A-Za-z0-9]*)\b",           # X465-48W
                r"\b(7\d{3}[-A-Za-z0-9]*)\b",             # 7520
                r"\b(8\d{3}[-A-Za-z0-9]*)\b",             # 8720
            ]
            seen = set()
            for pat in patterns:
                for sku in re.findall(pat, html):
                    if sku in seen:
                        continue
                    seen.add(sku)
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
                kv.update(self._pdf_kv(pdf_to_text(pdf_bytes), sku))

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
            nos="EXOS / VOSS / Fabric Engine",
            use_case=self._use_case(family, sku),
            datasheet_url=pdf_url or url,
            image_url=find_product_image(html, base_url=url),
            **{k: v for k, v in fields.items() if v is not None},
        )
        return rec

    @staticmethod
    def _use_case(family: str, sku: str) -> str:
        # Heuristic based on series number
        nums = re.findall(r"\d+", sku)
        if not nums:
            return "access"
        num = int(nums[0])
        if num >= 8000:
            return "spine"
        if num >= 7000:
            return "core"
        if num >= 5500:
            return "aggregation"
        return "access"

    @staticmethod
    def _pdf_kv(text: str, sku: str) -> dict:
        out = {}
        for line in text.splitlines():
            m = re.match(
                r"^\s*([A-Za-z][A-Za-z0-9 /()\-+]{3,50})\s+([\d\.,].{0,200})$",
                line,
            )
            if m:
                out.setdefault(m.group(1).strip(), m.group(2).strip())
        return out
