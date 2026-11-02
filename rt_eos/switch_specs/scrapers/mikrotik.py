"""
MikroTik scraper - https://mikrotik.com/products/group/switches
MikroTik has very consistent spec tables on each product page.
"""
from __future__ import annotations

import logging
import re
from typing import Iterator, Optional
from urllib.parse import urljoin

from .base import BaseScraper, SpecRecord
from .parsers import (
    detect_features, extract_spec_tables, map_kv_to_record_fields, soup,
)

logger = logging.getLogger("scrapers.mikrotik")


class MikroTikScraper(BaseScraper):
    VENDOR = "MikroTik"
    BASE = "https://mikrotik.com"
    CATALOG_URLS = [
        "https://mikrotik.com/products/group/switches",
    ]

    def discover_models(self) -> Iterator[tuple[str, str]]:
        for catalog in self.CATALOG_URLS:
            html = self.http.get_text(catalog)
            if not html: continue
            s = soup(html)
            seen = set()
            for a in s.find_all("a", href=True):
                href = a["href"]
                if "/product/" not in href:
                    continue
                full = urljoin(self.BASE, href)
                if full in seen: continue
                seen.add(full)
                # Slug → model name
                name = href.rsplit("/", 1)[-1].upper().replace("_", "-")
                yield name, full

    def extract_model(self, name: str, url: str) -> Optional[SpecRecord]:
        html = self.http.get_text(url)
        if not html: return None

        kv = extract_spec_tables(html)
        if not kv: return None

        fields = map_kv_to_record_fields(kv)
        text_blob = html.lower()
        features = detect_features(text_blob)

        # MikroTik puts the product code in the title or breadcrumb
        s = soup(html)
        title = s.find(["h1", "h2"])
        model = name
        if title:
            t = title.get_text(strip=True)
            m = re.search(r"(CRS\d+[\-A-Za-z0-9\+]+|CSS\d+[\-A-Za-z0-9\+]+)", t)
            if m: model = m.group(1)

        # MikroTik has explicit "Product Code"
        if "product code" in {k.lower() for k in kv}:
            for k, v in kv.items():
                if k.lower() == "product code":
                    model = v
                    break

        family = "CRS3xx" if model.startswith("CRS3") else (
            "CRS5xx" if model.startswith("CRS5") else (
            "CSS"     if model.startswith("CSS")  else "CRS"
        ))

        rec = SpecRecord(
            vendor=self.VENDOR,
            family=family,
            model=model,
            sku=model,
            features=features,
            nos="RouterOS / SwitchOS",
            datasheet_url=url,
            **{k: v for k, v in fields.items() if v is not None},
        )
        return rec
