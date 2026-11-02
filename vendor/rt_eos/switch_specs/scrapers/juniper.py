"""
Juniper EX/QFX switches scraper.
Source: https://www.juniper.net/us/en/products/switches.html
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

logger = logging.getLogger("scrapers.juniper")


JUNIPER_SERIES = [
    ("EX2300",  "https://www.juniper.net/us/en/products/switches/ex-series/ex2300-ethernet-switch.html"),
    ("EX3400",  "https://www.juniper.net/us/en/products/switches/ex-series/ex3400-ethernet-switch.html"),
    ("EX4100",  "https://www.juniper.net/us/en/products/switches/ex-series/ex4100-ethernet-switch.html"),
    ("EX4300",  "https://www.juniper.net/us/en/products/switches/ex-series/ex4300-ethernet-switch.html"),
    ("EX4400",  "https://www.juniper.net/us/en/products/switches/ex-series/ex4400-ethernet-switch.html"),
    ("EX4600",  "https://www.juniper.net/us/en/products/switches/ex-series/ex4600-ethernet-switch.html"),
    ("EX4650",  "https://www.juniper.net/us/en/products/switches/ex-series/ex4650-ethernet-switch.html"),
    ("QFX5100", "https://www.juniper.net/us/en/products/switches/qfx-series/qfx5100-ethernet-switch.html"),
    ("QFX5110", "https://www.juniper.net/us/en/products/switches/qfx-series/qfx5110-ethernet-switch.html"),
    ("QFX5120", "https://www.juniper.net/us/en/products/switches/qfx-series/qfx5120-ethernet-switch.html"),
    ("QFX5130", "https://www.juniper.net/us/en/products/switches/qfx-series/qfx5130-ethernet-switch.html"),
    ("QFX5200", "https://www.juniper.net/us/en/products/switches/qfx-series/qfx5200-ethernet-switch.html"),
    ("QFX5220", "https://www.juniper.net/us/en/products/switches/qfx-series/qfx5220-ethernet-switch.html"),
    ("QFX10002","https://www.juniper.net/us/en/products/switches/qfx-series/qfx10002-ethernet-switch.html"),
]


class JuniperScraper(BaseScraper):
    VENDOR = "Juniper"

    def discover_models(self) -> Iterator[tuple[str, str]]:
        for family, url in JUNIPER_SERIES:
            html = self.http.get_text(url)
            if not html: continue
            # EX2300-48P, QFX5120-48Y, etc.
            for sku in set(re.findall(
                r"\b((?:EX|QFX)\d{4,5}-[A-Z0-9]+)\b", html
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
                kv.update(self._pdf_kv(pdf_to_text(pdf_bytes), sku))

        if not kv: return None

        fields = map_kv_to_record_fields(kv)
        features = detect_features(html.lower())

        use_case = "spine" if sku.startswith(("QFX52", "QFX10", "QFX513")) else (
                   "leaf"  if sku.startswith("QFX5") else
                   "access")

        rec = SpecRecord(
            vendor=self.VENDOR,
            family=family,
            model=sku,
            sku=sku,
            features=features,
            nos="Junos",
            use_case=use_case,
            datasheet_url=pdf_url or url,
            **{k: v for k, v in fields.items() if v is not None},
        )
        return rec

    @staticmethod
    def _pdf_kv(text: str, sku: str) -> dict:
        out = {}
        for line in text.splitlines():
            m = re.match(
                r"^\s*([A-Za-z][A-Za-z /()\-+]{3,50})\s+([\d\.,].{0,200})$", line
            )
            if m:
                out.setdefault(m.group(1).strip(), m.group(2).strip())
        return out
