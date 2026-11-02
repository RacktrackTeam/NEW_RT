"""
TP-Link Omada switches scraper.
Source: https://www.tp-link.com/us/business-networking/switch/
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

logger = logging.getLogger("scrapers.tplink")


class TPLinkScraper(BaseScraper):
    VENDOR = "TP-Link"
    BASE = "https://www.tp-link.com"
    # The product catalog is JS-rendered (no links in static HTML), so we
    # enumerate every switch from the sitemap instead — reliable, $0, no JS.
    SITEMAP = "https://www.tp-link.com/us/sitemap.xml"

    def discover_models(self) -> Iterator[tuple[str, str]]:
        xml = self.http.get_text(self.SITEMAP)
        if not xml:
            logger.warning("[TP-Link] sitemap unavailable")
            return
        seen = set()
        for loc in re.findall(r"<loc>([^<]+)</loc>", xml):
            if "/business-networking/" not in loc:
                continue
            try:
                category, slug = loc.rstrip("/").rsplit("/", 2)[-2:]
            except ValueError:
                continue
            # Switch product pages: a "*switch*" category + a model slug
            # with at least one digit (filters out accessories/APs).
            if "switch" not in category.lower():
                continue
            if not re.search(r"[a-z].*\d", slug, re.I):
                continue
            if loc in seen:
                continue
            seen.add(loc)
            yield slug.upper(), loc

    def extract_model(self, name: str, url: str) -> Optional[SpecRecord]:
        # TP-Link has a /spec/ subpage with the structured table
        spec_url = url.rstrip("/") + "/spec/"
        html = self.http.get_text(spec_url) or self.http.get_text(url)
        if not html: return None

        kv = extract_spec_tables(html)
        if not kv: return None

        fields = map_kv_to_record_fields(kv)
        text_blob = html.lower()
        features = detect_features(text_blob)

        s = soup(html)
        model = name
        title = s.find(["h1"])
        if title:
            m = re.search(r"(TL-[A-Z0-9]+)", title.get_text(), re.I)
            if m: model = m.group(1).upper()

        family = "Omada SDN" if "omada" in url.lower() else "JetStream"
        nos = "Omada SDN" if family == "Omada SDN" else "JetStream"

        rec = SpecRecord(
            vendor=self.VENDOR,
            family=family,
            model=model,
            sku=model,
            features=features,
            nos=nos,
            datasheet_url=url,
            **{k: v for k, v in fields.items() if v is not None},
        )
        return rec
