"""
Ubiquiti UniFi switches scraper.
Source: https://techspecs.ui.com/unifi/switching (clean spec pages)
"""
from __future__ import annotations

import logging
import re
from typing import Iterator, Optional

from .base import BaseScraper, SpecRecord
from .parsers import (
    detect_features, extract_spec_tables, map_kv_to_record_fields,
    parse_port_config, soup,
)

logger = logging.getLogger("scrapers.ubiquiti")


class UbiquitiScraper(BaseScraper):
    VENDOR = "Ubiquiti"
    CATALOG_URLS = [
        "https://techspecs.ui.com/unifi/switching",
        "https://store.ui.com/us/en/category/all-switching",
    ]

    def discover_models(self) -> Iterator[tuple[str, str]]:
        for catalog in self.CATALOG_URLS:
            html = self.http.get_text(catalog)
            if not html: continue
            s = soup(html)
            seen = set()
            # Match product cards / links to /products/<model>
            for a in s.find_all("a", href=True):
                href = a["href"]
                if "/products/" not in href and "/unifi/switching/" not in href:
                    continue
                if href in seen: continue
                seen.add(href)
                url = href if href.startswith("http") else (
                    "https://techspecs.ui.com" + href if "techspecs" in catalog
                    else "https://store.ui.com" + href
                )
                name = a.get_text(strip=True) or href.rsplit("/", 1)[-1]
                if len(name) < 3 or len(name) > 100:
                    name = href.rsplit("/", 1)[-1].replace("-", " ").upper()
                yield name, url

    def extract_model(self, name: str, url: str) -> Optional[SpecRecord]:
        html = self.http.get_text(url)
        if not html: return None

        kv = extract_spec_tables(html)
        if not kv:
            # techspecs.ui.com loads spec table via JS; fall back to <script> JSON
            kv = self._extract_from_json(html)
        if not kv: return None

        fields = map_kv_to_record_fields(kv)
        text_blob = html.lower()
        features = detect_features(text_blob)

        # Model name guess: look for SKU pattern in title or path
        sku = self._find_sku(html, url, name)
        model = sku or name

        rec = SpecRecord(
            vendor=self.VENDOR,
            family="UniFi Switch",
            model=model,
            sku=sku,
            features=features,
            nos="UniFi OS",
            datasheet_url=url,
            **{k: v for k, v in fields.items() if v is not None},
        )
        return rec

    def _extract_from_json(self, html: str) -> dict:
        """techspecs.ui.com embeds spec data in JSON."""
        out: dict[str, str] = {}
        for m in re.finditer(r'"(\w[\w\s]*)"\s*:\s*"([^"]{1,200})"', html):
            k, v = m.group(1), m.group(2)
            if any(kw in k.lower() for kw in (
                "port", "capacity", "buffer", "poe", "power", "mac"
            )):
                out[k] = v
        return out

    def _find_sku(self, html: str, url: str, name: str) -> Optional[str]:
        # USW-...  patterns
        for source in (name, url):
            m = re.search(r"USW-[A-Za-z0-9\-]+", source)
            if m: return m.group(0)
        return None
