"""
Ruijie Networks switches scraper.
Source: https://www.ruijienetworks.com/products/switches

Ruijie publishes English product pages. Their RG- prefix is consistent.
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

logger = logging.getLogger("scrapers.ruijie")


RUIJIE_FAMILIES = [
    # Data center
    ("RG-S6500",
     "https://www.ruijienetworks.com/products/switches/RG-S6500-Series"),
    ("RG-S6520",
     "https://www.ruijienetworks.com/products/switches/RG-S6520-Series"),
    ("RG-S6920",
     "https://www.ruijienetworks.com/products/switches/RG-S6920-Series"),
    ("RG-N18000",
     "https://www.ruijienetworks.com/products/switches/RG-N18000-Series"),
    # Aggregation / core
    ("RG-S5750",
     "https://www.ruijienetworks.com/products/switches/RG-S5750-Series"),
    ("RG-S5760",
     "https://www.ruijienetworks.com/products/switches/RG-S5760-Series"),
    # Access
    ("RG-S2900",
     "https://www.ruijienetworks.com/products/switches/RG-S2900-Series"),
    ("RG-S5300",
     "https://www.ruijienetworks.com/products/switches/RG-S5300-Series"),
    ("RG-S5310",
     "https://www.ruijienetworks.com/products/switches/RG-S5310-Series"),
]


RUIJIE_SKU = re.compile(r"\b(RG-[SN]\d{4,5}[\-A-Za-z0-9]+)\b")


class RuijieScraper(BaseScraper):
    VENDOR = "Ruijie"
    DELAY_SEC = 1.5

    def discover_models(self) -> Iterator[tuple[str, str]]:
        seen = set()
        for family, url in RUIJIE_FAMILIES:
            html = self.http.get_text(url)
            if not html:
                continue
            for sku in RUIJIE_SKU.findall(html):
                if len(sku) < 10 or len(sku) > 40:
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
        if "vsu" in html.lower():
            features.append("VSU")  # Ruijie's stacking tech

        rec = SpecRecord(
            vendor=self.VENDOR,
            family=family,
            model=sku,
            sku=sku,
            features=list(set(features)),
            nos="RGOS",
            use_case=self._use_case(family),
            datasheet_url=pdf_url or url,
            image_url=find_product_image(html, base_url=url),
            **{k: v for k, v in fields.items() if v is not None},
        )
        return rec

    @staticmethod
    def _use_case(family: str) -> str:
        if "N18000" in family or "S6920" in family: return "spine"
        if "S6500" in family or "S6520" in family:  return "leaf"
        if "S5750" in family or "S5760" in family:  return "aggregation"
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
