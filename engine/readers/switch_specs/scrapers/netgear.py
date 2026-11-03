"""
Netgear business switches scraper.
Source: https://www.netgear.com/business/wired/switches/
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

logger = logging.getLogger("scrapers.netgear")


class NetgearScraper(BaseScraper):
    VENDOR = "Netgear"
    BASE = "https://www.netgear.com"
    CATALOG_URLS = [
        "https://www.netgear.com/business/wired/switches/smart/",
        "https://www.netgear.com/business/wired/switches/fully-managed/",
        "https://www.netgear.com/business/wired/switches/av-line-m4250/",
        "https://www.netgear.com/business/wired/switches/unmanaged/",
    ]

    def discover_models(self) -> Iterator[tuple[str, str]]:
        seen = set()
        for catalog in self.CATALOG_URLS:
            html = self.http.get_text(catalog)
            if not html: continue
            s = soup(html)
            for a in s.find_all("a", href=True):
                href = a["href"]
                # Netgear product pages: /business/wired/switches/<series>/<sku>/
                if not re.search(r"/switches/[\w-]+/[\w-]+/?$", href, re.I):
                    continue
                full = urljoin(self.BASE, href)
                if full in seen: continue
                seen.add(full)
                slug = href.rstrip("/").rsplit("/", 1)[-1].upper()
                yield slug, full

    def extract_model(self, name: str, url: str) -> Optional[SpecRecord]:
        html = self.http.get_text(url)
        if not html: return None

        kv = extract_spec_tables(html)
        # Netgear often pushes detailed specs into the datasheet PDF
        if len([v for v in kv.values() if v]) < 5:
            pdf_url = find_datasheet_link(html, url)
            if pdf_url:
                pdf_bytes = self.http.get(pdf_url)
                if pdf_bytes:
                    text = pdf_to_text(pdf_bytes)
                    kv.update(self._kv_from_pdf_text(text))

        if not kv: return None

        fields = map_kv_to_record_fields(kv)
        text_blob = html.lower()
        features = detect_features(text_blob)

        # Find marketing model name (e.g. M4300-28G-PoE+) and SKU
        s = soup(html)
        h1 = s.find(["h1", "h2"])
        model = name
        if h1:
            t = h1.get_text(strip=True)
            m = re.search(r"(M\d{4}[-A-Za-z0-9+]+|GS\d+\w*|XSM\w+)", t)
            if m: model = m.group(1)

        family = self._family(model)

        rec = SpecRecord(
            vendor=self.VENDOR,
            family=family,
            model=model,
            sku=name,
            features=features,
            nos=self._nos(family),
            datasheet_url=url,
            **{k: v for k, v in fields.items() if v is not None},
        )
        return rec

    @staticmethod
    def _family(model: str) -> str:
        if model.startswith("M4500"): return "M4500"
        if model.startswith("M4350"): return "M4350"
        if model.startswith("M4300"): return "M4300"
        if model.startswith("M4250"): return "M4250 AV"
        if model.startswith("GS"): return "Smart Managed"
        return "Netgear"

    @staticmethod
    def _nos(family: str) -> str:
        if family.startswith("M4"): return family
        return "Smart Managed"

    @staticmethod
    def _kv_from_pdf_text(text: str) -> dict:
        out = {}
        for line in text.splitlines():
            # Look for "Label : value" or "Label    value" patterns
            m = re.match(
                r"^\s*([A-Za-z][A-Za-z /()\-+]{3,40})\s*[:\.]\s+(.{1,200})$",
                line,
            )
            if m:
                k = m.group(1).strip().rstrip(":")
                v = m.group(2).strip()
                if len(k) < 50 and len(v) < 200:
                    out.setdefault(k, v)
        return out
