"""
H3C switches scraper.
Source: https://www.h3c.com/en/Products___Solutions/Technology/Switches/

H3C publishes English-language product pages for its S-series switches.
Some technical details only live in the Chinese site (h3c.com.cn) - we use
the English site for stable structure.
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

logger = logging.getLogger("scrapers.h3c")


H3C_FAMILIES = [
    # Data center
    ("S12500X-AF",
     "https://www.h3c.com/en/Products___Solutions/Technology/Switches/Data_Center_Switches/Products/S12500_Series_Switches/"),
    ("S9820",
     "https://www.h3c.com/en/Products___Solutions/Technology/Switches/Data_Center_Switches/Products/S9820_Series_Switches/"),
    ("S6850",
     "https://www.h3c.com/en/Products___Solutions/Technology/Switches/Data_Center_Switches/Products/S6850_Series_Switches/"),
    ("S6800",
     "https://www.h3c.com/en/Products___Solutions/Technology/Switches/Data_Center_Switches/Products/S6800_Series_Switches/"),
    ("S6520X",
     "https://www.h3c.com/en/Products___Solutions/Technology/Switches/Data_Center_Switches/Products/S6520X_Series_Switches/"),
    # Campus
    ("S12500G-AF",
     "https://www.h3c.com/en/Products___Solutions/Technology/Switches/Campus_Switches/Products/S12500G_Series_Switches/"),
    ("S7500E",
     "https://www.h3c.com/en/Products___Solutions/Technology/Switches/Campus_Switches/Products/S7500E_Series_Switches/"),
    ("S5500V2",
     "https://www.h3c.com/en/Products___Solutions/Technology/Switches/Campus_Switches/Products/S5500V2_Series_Switches/"),
    ("S5130",
     "https://www.h3c.com/en/Products___Solutions/Technology/Switches/Campus_Switches/Products/S5130_Series_Switches/"),
    ("S5120V3",
     "https://www.h3c.com/en/Products___Solutions/Technology/Switches/Campus_Switches/Products/S5120V3_Series_Switches/"),
]


# H3C SKU pattern: LS-S6850-56HF-H1, S12500X-AF, S5130S-28F-EI
H3C_SKU_PATTERNS = [
    re.compile(r"\b(LS-S\d{3,5}[XGV]?-[A-Za-z0-9-]+)\b"),
    re.compile(r"\b(S\d{4}[XEV]?[-A-Za-z0-9]+)\b"),
    re.compile(r"\b(S\d{5}[XGCV]?[-A-Za-z0-9]+)\b"),
]


class H3CScraper(BaseScraper):
    VENDOR = "H3C"
    DELAY_SEC = 1.5

    def discover_models(self) -> Iterator[tuple[str, str]]:
        seen = set()
        for family, url in H3C_FAMILIES:
            html = self.http.get_text(url)
            if not html:
                continue
            for pat in H3C_SKU_PATTERNS:
                for sku in pat.findall(html):
                    # H3C SKUs are usually 8-30 chars - filter outliers
                    if len(sku) < 7 or len(sku) > 40:
                        continue
                    # Need a hyphen to be a real SKU vs a family name
                    if "-" not in sku:
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
        # H3C-specific
        if "irf" in html.lower():
            features.append("IRF")  # H3C's stacking tech

        rec = SpecRecord(
            vendor=self.VENDOR,
            family=family,
            model=sku,
            sku=sku,
            features=list(set(features)),
            nos="Comware",
            use_case=self._use_case(family),
            datasheet_url=pdf_url or url,
            image_url=find_product_image(html, base_url=url),
            **{k: v for k, v in fields.items() if v is not None},
        )
        return rec

    @staticmethod
    def _use_case(family: str) -> str:
        if family.startswith("S12500"): return "core"
        if family.startswith("S9"):     return "spine"
        if family.startswith("S6"):     return "leaf"
        if family.startswith("S7"):     return "aggregation"
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
