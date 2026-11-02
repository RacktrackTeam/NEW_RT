"""
Fortinet FortiSwitch scraper.
Source: https://www.fortinet.com/products/switching

Fortinet publishes a comparison matrix per series and per-model datasheets.
HTML spec tables on the product overview pages are well-structured.
"""
from __future__ import annotations

import logging
import re
from typing import Iterator, Optional

from .base import BaseScraper, SpecRecord
from .parsers import (
    detect_features, extract_spec_tables, find_datasheet_link,
    find_product_image, map_kv_to_record_fields, pdf_to_text, soup,
)

logger = logging.getLogger("scrapers.fortinet")


# Fortinet FortiSwitch series
FORTINET_FAMILIES = [
    ("FortiSwitch Access",
     "https://www.fortinet.com/products/switching/access"),
    ("FortiSwitch Campus",
     "https://www.fortinet.com/products/switching/campus"),
    ("FortiSwitch Data Center",
     "https://www.fortinet.com/products/switching/data-center"),
    ("FortiSwitch Rugged",
     "https://www.fortinet.com/products/switching/rugged"),
    # General product page that lists all
    ("FortiSwitch",
     "https://www.fortinet.com/products/switching"),
]


# FortiSwitch SKU: FortiSwitch-448E-FPOE, FS-1024D, etc.
FORTINET_SKU = re.compile(
    r"\b(FortiSwitch-?\d{3,4}\w*[\-\w]*|FS-?\d{3,4}\w*[\-\w]*)\b",
    re.IGNORECASE,
)


class FortinetScraper(BaseScraper):
    VENDOR = "Fortinet"

    def discover_models(self) -> Iterator[tuple[str, str]]:
        seen = set()
        for family, url in FORTINET_FAMILIES:
            html = self.http.get_text(url)
            if not html:
                continue
            for sku in FORTINET_SKU.findall(html):
                # Normalize: strip dashes inconsistency
                normalized = self._normalize_sku(sku)
                key = (family, normalized)
                if key in seen:
                    continue
                seen.add(key)
                yield f"{family}__{normalized}", url

    @staticmethod
    def _normalize_sku(sku: str) -> str:
        # Convert "FortiSwitch 448E" to "FortiSwitch-448E"
        return re.sub(r"\s+", "-", sku.strip())

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

        # Fortinet-specific feature signals
        text_low = html.lower()
        if "fortilink" in text_low:
            features.append("FortiLink")
        if "fortigate" in text_low and "integrat" in text_low:
            features.append("FortiGate integration")
        if "security fabric" in text_low:
            features.append("Security Fabric")

        rec = SpecRecord(
            vendor=self.VENDOR,
            family=family,
            model=sku,
            sku=sku,
            features=list(set(features)),
            nos="FortiSwitchOS",
            use_case=self._use_case(family, sku),
            datasheet_url=pdf_url or url,
            image_url=find_product_image(html, base_url=url),
            **{k: v for k, v in fields.items() if v is not None},
        )
        return rec

    @staticmethod
    def _use_case(family: str, sku: str) -> str:
        if "data center" in family.lower(): return "leaf"
        if "campus" in family.lower():       return "aggregation"
        if "rugged" in family.lower():       return "access"
        # Heuristic by model number
        nums = re.findall(r"\d+", sku)
        if nums:
            num = int(nums[0])
            if num >= 3000: return "leaf"
            if num >= 1000: return "aggregation"
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
