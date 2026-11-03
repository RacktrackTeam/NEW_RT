"""
Edgecore Networks switches scraper.
Source: https://www.edge-core.com/cs.php?categories=switches

Edgecore makes white-box hardware (Broadcom Tomahawk/Trident-based) that runs
disaggregated NOSes - SONiC, Cumulus, IPInfusion OcNOS, ICOS, etc.
The hardware specs live at edge-core.com, NOS is separate.
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

logger = logging.getLogger("scrapers.edgecore")


EDGECORE_FAMILIES = [
    # Data center 400G
    ("DCS800",  "https://www.edge-core.com/productsList.php?cls=1&cls2=180"),
    # Data center 100G
    ("DCS500",  "https://www.edge-core.com/productsList.php?cls=1&cls2=181"),
    # Data center 25G/40G
    ("DCS200",  "https://www.edge-core.com/productsList.php?cls=1&cls2=14"),
    # Open Modular
    ("OMP800",  "https://www.edge-core.com/productsList.php?cls=1&cls2=146"),
    # Campus
    ("ECS4000", "https://www.edge-core.com/productsList.php?cls=2&cls2=20"),
    ("ECS5000", "https://www.edge-core.com/productsList.php?cls=2&cls2=24"),
    # Carrier
    ("AS5800",  "https://www.edge-core.com/productsList.php?cls=4&cls2=58"),
    # Catalog index
    ("All",     "https://www.edge-core.com/cs.php?categories=switches"),
]


# Edgecore SKU patterns: AS7726-32X, DCS810-32D, ECS4510-28T, AS9716-32D
EDGECORE_SKU = re.compile(
    r"\b((?:AS|ECS|DCS|OMP)\d{4}[-\w]+)\b"
)


class EdgecoreScraper(BaseScraper):
    VENDOR = "Edgecore"
    DELAY_SEC = 1.0

    def discover_models(self) -> Iterator[tuple[str, str]]:
        seen = set()
        for family, url in EDGECORE_FAMILIES:
            html = self.http.get_text(url)
            if not html:
                continue
            for sku in EDGECORE_SKU.findall(html):
                if len(sku) < 8 or len(sku) > 30:
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
        text_low = html.lower()
        if "sonic" in text_low: features.append("SONiC compatible")
        if "cumulus" in text_low: features.append("Cumulus compatible")
        if "ocnos" in text_low: features.append("OcNOS compatible")

        rec = SpecRecord(
            vendor=self.VENDOR,
            family=family,
            model=sku,
            sku=sku,
            features=list(set(features)),
            nos="Open / SONiC / Cumulus",
            use_case=self._use_case(sku),
            datasheet_url=pdf_url or url,
            image_url=find_product_image(html, base_url=url),
            **{k: v for k, v in fields.items() if v is not None},
        )
        return rec

    @staticmethod
    def _use_case(sku: str) -> str:
        nums = re.findall(r"\d+", sku)
        if not nums: return "access"
        num = int(nums[0])
        if num >= 9000:                return "spine"
        if num >= 7000:                return "leaf"
        if sku.startswith("DCS"):      return "leaf"
        if sku.startswith("OMP"):      return "modular"
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
