"""
Cisco Catalyst + Nexus switches scraper.
Source: cisco.com/c/en/us/products/switches/...

Cisco is the hardest vendor:
- Massive catalog
- Many models hide spec detail behind login
- Datasheet PDFs are the primary source of truth
- SKU sprawl (Catalyst 9300-48P-A vs -E vs -L = license tiers)

This scraper focuses on getting:
- the product family pages
- per-model datasheet PDFs (public-facing)
- top-level specs from those PDFs
"""
from __future__ import annotations

import logging
import re
from typing import Iterator, Optional

from .base import BaseScraper, SpecRecord
from .parsers import (
    detect_features, find_datasheet_link, map_kv_to_record_fields,
    pdf_to_text, soup,
)

logger = logging.getLogger("scrapers.cisco")


CISCO_FAMILIES = [
    # Catalyst (campus / access)
    ("Catalyst 9200",
     "https://www.cisco.com/c/en/us/products/switches/catalyst-9200-series-switches/index.html"),
    ("Catalyst 9300",
     "https://www.cisco.com/c/en/us/products/switches/catalyst-9300-series-switches/index.html"),
    ("Catalyst 9400",
     "https://www.cisco.com/c/en/us/products/switches/catalyst-9400-series-switches/index.html"),
    ("Catalyst 9500",
     "https://www.cisco.com/c/en/us/products/switches/catalyst-9500-series-switches/index.html"),
    ("Catalyst 9600",
     "https://www.cisco.com/c/en/us/products/switches/catalyst-9600-series-switches/index.html"),
    # Nexus (data center)
    ("Nexus 9300",
     "https://www.cisco.com/c/en/us/products/switches/nexus-9000-series-switches/index.html"),
    ("Nexus 9500",
     "https://www.cisco.com/c/en/us/products/switches/nexus-9000-series-switches/index.html"),
    # Meraki cloud-managed
    ("Meraki MS",
     "https://meraki.cisco.com/products/switches/"),
]


# SKU patterns we recognize
SKU_PATTERNS = [
    re.compile(r"\bC9\d{3}[A-Z]?-\w{2,15}\b"),       # Catalyst SKUs
    re.compile(r"\bN9K-C\d{4}[A-Z\-]+\d*\w*\b"),     # Nexus 9K SKUs
    re.compile(r"\bN[235]K-C\d{4}\w*\b"),            # Nexus 2/3/5K
    re.compile(r"\bMS\d{3}[A-Z]?-\w+\b"),            # Meraki SKUs
]


class CiscoScraper(BaseScraper):
    VENDOR = "Cisco"
    # Cisco rate-limits aggressively; be polite
    DELAY_SEC = 2.0

    def discover_models(self) -> Iterator[tuple[str, str]]:
        seen = set()
        for family, url in CISCO_FAMILIES:
            html = self.http.get_text(url)
            if not html: continue

            # Find all model-line subpages
            s = soup(html)
            for a in s.find_all("a", href=True):
                href = a["href"]
                if "datasheet" in href.lower() and href.lower().endswith(".html"):
                    # datasheet listing page
                    full = href if href.startswith("http") else "https://www.cisco.com" + href
                    if full in seen: continue
                    seen.add(full)
                    # Extract SKUs from the linked page
                    yield from self._sku_pages(family, full)

            # Also pull SKUs from the family page itself
            for pat in SKU_PATTERNS:
                for sku in set(pat.findall(html)):
                    key = (family, sku)
                    if key in seen: continue
                    seen.add(key)
                    yield f"{family}__{sku}", url

    def _sku_pages(self, family: str, url: str) -> Iterator[tuple[str, str]]:
        html = self.http.get_text(url)
        if not html: return
        for pat in SKU_PATTERNS:
            for sku in set(pat.findall(html)):
                yield f"{family}__{sku}", url

    def extract_model(self, name: str, url: str) -> Optional[SpecRecord]:
        family, sku = name.split("__", 1)
        html = self.http.get_text(url)
        if not html: return None

        # Find the datasheet PDF
        pdf_url = find_datasheet_link(html, url)
        kv = {}
        text = ""
        if pdf_url:
            pdf_bytes = self.http.get(pdf_url)
            if pdf_bytes:
                text = pdf_to_text(pdf_bytes)
                kv = self._pdf_kv(text, sku)

        # Fallback: HTML spec tables
        if not kv:
            from .parsers import extract_spec_tables
            kv = extract_spec_tables(html)

        if not kv: return None

        fields = map_kv_to_record_fields(kv)
        features = detect_features((html + text).lower())

        # Cisco-specific features
        if "stackwise" in (html + text).lower():
            features.append("StackWise")
        if "cisco dna" in (html + text).lower():
            features.append("Cisco DNA")
        if "aci" in (html + text).lower():
            features.append("ACI")

        # Pretty model name: "Catalyst 9300-48P" or "Nexus 9336C-FX2"
        model = self._pretty_model(family, sku)

        rec = SpecRecord(
            vendor=self.VENDOR,
            family=family,
            model=model,
            sku=sku,
            features=list(set(features)),
            nos=self._nos(family),
            use_case=self._use_case(family, sku),
            datasheet_url=pdf_url or url,
            **{k: v for k, v in fields.items() if v is not None},
        )
        return rec

    @staticmethod
    def _pretty_model(family: str, sku: str) -> str:
        # C9300-48P -> Catalyst 9300-48P
        if sku.startswith("C9"):
            return "Catalyst " + sku[1:]
        if sku.startswith("N9K-C"):
            return "Nexus " + sku.split("-", 1)[1].lstrip("C")
        return sku

    @staticmethod
    def _nos(family: str) -> str:
        if family.startswith("Catalyst"): return "IOS-XE"
        if family.startswith("Nexus"): return "NX-OS"
        if family.startswith("Meraki"): return "Meraki Dashboard"
        return ""

    @staticmethod
    def _use_case(family: str, sku: str) -> str:
        if family.startswith("Catalyst 9200"): return "access"
        if family.startswith("Catalyst 9300"): return "access"
        if family.startswith("Catalyst 9400"): return "aggregation"
        if family.startswith("Catalyst 9500"): return "aggregation"
        if family.startswith("Catalyst 9600"): return "core"
        if "FX2" in sku or "GX" in sku or "9336" in sku or "9364" in sku: return "spine"
        if family.startswith("Nexus"): return "leaf"
        return "access"

    @staticmethod
    def _pdf_kv(text: str, sku: str) -> dict:
        out = {}
        for line in text.splitlines():
            m = re.match(
                r"^\s*([A-Za-z][A-Za-z0-9 /()\-+]{3,50})\s+([\d\.,].{0,200})$", line
            )
            if m:
                k = m.group(1).strip()
                v = m.group(2).strip()
                if len(k) < 60 and len(v) < 200:
                    out.setdefault(k, v)
        return out
