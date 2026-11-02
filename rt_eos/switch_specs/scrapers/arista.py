"""
Arista scraper.
Source: https://www.arista.com/en/products/platforms
Arista publishes datasheets and quick-look spec sheets per series.
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

logger = logging.getLogger("scrapers.arista")


# Arista series with their platforms index URLs
ARISTA_SERIES = [
    ("7050X3",  "https://www.arista.com/en/products/7050x3-series"),
    ("7060X",   "https://www.arista.com/en/products/7060x-series"),
    ("7280R3",  "https://www.arista.com/en/products/7280r3-series"),
    ("7300X",   "https://www.arista.com/en/products/7300x-series"),
    ("7500R",   "https://www.arista.com/en/products/7500r-series"),
    ("7800R",   "https://www.arista.com/en/products/7800r-series"),
    ("720XP",   "https://www.arista.com/en/products/720xp-series"),
    ("750",     "https://www.arista.com/en/products/750-series"),
]


class AristaScraper(BaseScraper):
    VENDOR = "Arista"

    def discover_models(self) -> Iterator[tuple[str, str]]:
        for family, series_url in ARISTA_SERIES:
            html = self.http.get_text(series_url)
            if not html:
                continue
            # On Arista series pages, each model appears in spec tables
            s = soup(html)
            # Look for SKU patterns in the page text
            skus = set(re.findall(
                r"(DCS-7\d{3}[A-Z]+\d*[-A-Z0-9]+|CCS-\d+[A-Z0-9\-]+)", html
            ))
            for sku in skus:
                # Build datasheet URL (Arista convention varies, link to series page)
                yield f"{family}__{sku}", series_url

    def extract_model(self, name: str, url: str) -> Optional[SpecRecord]:
        family, sku = name.split("__", 1)
        html = self.http.get_text(url)
        if not html: return None

        # Try to find a per-model spec table near the SKU mention
        kv = self._kv_near_sku(html, sku)

        # Also look for datasheet PDF
        pdf_url = find_datasheet_link(html, url)
        if pdf_url:
            pdf_bytes = self.http.get(pdf_url)
            if pdf_bytes:
                pdf_text = pdf_to_text(pdf_bytes)
                kv.update(self._kv_from_pdf(pdf_text, sku))

        if not kv: return None

        fields = map_kv_to_record_fields(kv)
        features = detect_features(html.lower())

        # Model name = drop DCS- prefix for display
        model = re.sub(r"^DCS-", "", sku)

        rec = SpecRecord(
            vendor=self.VENDOR,
            family=family,
            model=model,
            sku=sku,
            features=features,
            nos="EOS",
            use_case=self._guess_use_case(model),
            datasheet_url=pdf_url or url,
            **{k: v for k, v in fields.items() if v is not None},
        )
        return rec

    def _kv_near_sku(self, html: str, sku: str) -> dict:
        """Find a spec table that contains the SKU."""
        s = soup(html)
        for table in s.find_all("table"):
            if sku not in table.get_text():
                continue
            kv = {}
            for tr in table.find_all("tr"):
                cells = tr.find_all(["th", "td"])
                if len(cells) >= 2:
                    kv[cells[0].get_text(strip=True)] = cells[1].get_text(strip=True)
            if kv: return kv
        return {}

    def _kv_from_pdf(self, text: str, sku: str) -> dict:
        """Extract key/value pairs from PDF text in a column near the SKU."""
        out = {}
        for line in text.splitlines():
            m = re.match(
                r"^\s*([A-Za-z][A-Za-z /()\-]{3,50})\s+([\d\.,]+\s*[\w%/]+.*)$",
                line,
            )
            if m:
                out.setdefault(m.group(1).strip(), m.group(2).strip())
        return out

    @staticmethod
    def _guess_use_case(model: str) -> str:
        if "7800" in model or "7500" in model or "7300" in model: return "core"
        if "7060" in model: return "spine"
        if "7280" in model: return "spine"
        if "7050" in model: return "leaf"
        if "720" in model:  return "access"
        return "leaf"
