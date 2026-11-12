"""
Lenovo ThinkSystem switches scraper.
Source: https://lenovopress.lenovo.com/networking
        https://www.lenovo.com/us/en/p/servers-storage/networking/

Lenovo ThinkSystem networking lives in two places. The Lenovo Press portal
(lenovopress.lenovo.com) is the canonical source for technical specs - they
publish per-product "Product Guides" that are well-structured PDFs.
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

logger = logging.getLogger("scrapers.lenovo")


LENOVO_FAMILIES = [
    # ThinkSystem ToR switches
    ("ThinkSystem NE0152T",
     "https://lenovopress.lenovo.com/lp1605-lenovo-thinksystem-ne0152t-rackswitch"),
    ("ThinkSystem NE1032",
     "https://lenovopress.lenovo.com/lp0605-lenovo-thinksystem-ne1032-rackswitch"),
    ("ThinkSystem NE1032T",
     "https://lenovopress.lenovo.com/lp0606-lenovo-thinksystem-ne1032t-rackswitch"),
    ("ThinkSystem NE1072T",
     "https://lenovopress.lenovo.com/lp0607-lenovo-thinksystem-ne1072t-rackswitch"),
    ("ThinkSystem NE2572",
     "https://lenovopress.lenovo.com/lp0608-lenovo-thinksystem-ne2572-rackswitch"),
    ("ThinkSystem NE10032",
     "https://lenovopress.lenovo.com/lp0609-lenovo-thinksystem-ne10032-rackswitch"),
    # Catalog
    ("ThinkAgile network",
     "https://lenovopress.lenovo.com/networking"),
]


# Lenovo SKU: NE1032, NE10032, NE2572, NE0152T - plus part numbers like 7Z51 / 7159
LENOVO_SKU = re.compile(
    r"\b(NE\d{4,5}T?(?:O)?)\b"
)


class LenovoScraper(BaseScraper):
    VENDOR = "Lenovo"
    DELAY_SEC = 1.0

    def discover_models(self) -> Iterator[tuple[str, str]]:
        seen = set()
        for family, url in LENOVO_FAMILIES:
            html = self.http.get_text(url)
            if not html:
                continue
            for sku in LENOVO_SKU.findall(html):
                if sku in seen:
                    continue
                seen.add(sku)
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
        if "cnos" in text_low: features.append("CNOS")
        if "enos" in text_low: features.append("ENOS")

        rec = SpecRecord(
            vendor=self.VENDOR,
            family=family,
            model=sku,
            sku=sku,
            features=list(set(features)),
            nos="CNOS / ENOS",
            use_case=self._use_case(sku),
            datasheet_url=pdf_url or url,
            image_url=find_product_image(html, base_url=url),
            **{k: v for k, v in fields.items() if v is not None},
        )
        return rec

    @staticmethod
    def _use_case(sku: str) -> str:
        # NE10032 = 32x 100G spine; NE2572 = 25G leaf; NE1032 = 10G ToR
        nums = re.findall(r"\d+", sku)
        if not nums: return "leaf"
        num = int(nums[0])
        if num >= 10000: return "spine"
        if num >= 2500:  return "leaf"
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
