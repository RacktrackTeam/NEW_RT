"""
Base scraper framework - shared HTTP client, caching, retries, parsing helpers.
No external APIs. Pure parsing.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterator, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger("scrapers")

ROOT = Path(__file__).parent.parent
CACHE_DIR = ROOT / "data_cache"
DB_PATH = ROOT / "data" / "switches.db"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; SwitchSpecAgent/1.0; "
        "+https://example.com/bot)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/pdf,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

# Polite default: at least 1s between requests to the same domain
DEFAULT_DELAY_SEC = 1.0


# -----------------------------------------------------------------------------
# Spec record
# -----------------------------------------------------------------------------
@dataclass
class SpecRecord:
    """One switch spec, matching the DB schema. Unknown fields = None."""
    vendor: str
    model: str
    family: Optional[str] = None
    sku: Optional[str] = None
    port_count: Optional[int] = None
    port_speed_max_gbps: Optional[int] = None
    port_config: Optional[str] = None
    uplink_config: Optional[str] = None
    switching_capacity_gbps: Optional[float] = None
    forwarding_rate_mpps: Optional[float] = None
    buffer_mb: Optional[float] = None
    latency_ns: Optional[int] = None
    mac_table_size: Optional[int] = None
    poe_standard: Optional[str] = None
    poe_budget_w: Optional[int] = None
    power_typical_w: Optional[int] = None
    power_max_w: Optional[int] = None
    layer: Optional[str] = None
    features: list[str] = field(default_factory=list)
    rack_units: Optional[int] = None
    nos: Optional[str] = None
    status: str = "active"
    use_case: Optional[str] = None
    datasheet_url: Optional[str] = None
    image_url: Optional[str] = None
    # Every raw key/value pair extracted from the source that didn't map
    # to a schema field. Lets the UI show the rest of the datasheet
    # inline instead of just linking out to it.
    extra_specs: dict = field(default_factory=dict)

    def to_db_dict(self) -> dict:
        d = asdict(self)
        d["features"] = json.dumps(self.features) if self.features else None
        d["extra_specs"] = (json.dumps(self.extra_specs)
                            if self.extra_specs else None)
        return d

    def is_minimally_valid(self) -> bool:
        """At minimum: vendor + model + something else useful."""
        if not self.vendor or not self.model:
            return False
        useful = [
            self.port_count, self.port_config,
            self.switching_capacity_gbps, self.datasheet_url,
        ]
        return any(v for v in useful)


# -----------------------------------------------------------------------------
# HTTP client with on-disk caching
# -----------------------------------------------------------------------------
class HttpClient:
    """
    Persistent HTTP cache so reruns don't re-fetch.
    Cache key = SHA1(url). Files written under data_cache/<vendor>/.
    """
    def __init__(self, vendor: str, delay: float = DEFAULT_DELAY_SEC):
        self.vendor = vendor
        self.delay = delay
        self._last_fetch: dict[str, float] = {}
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.cache_dir = CACHE_DIR / vendor.replace(" ", "_").lower()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _key(self, url: str) -> Path:
        h = hashlib.sha1(url.encode()).hexdigest()
        ext = ".pdf" if url.lower().endswith(".pdf") else ".html"
        return self.cache_dir / f"{h}{ext}"

    def get(self, url: str, *, force: bool = False, timeout: int = 20) -> Optional[bytes]:
        cache = self._key(url)
        if not force and cache.exists():
            return cache.read_bytes()

        # Polite delay per domain
        domain = urlparse(url).netloc
        last = self._last_fetch.get(domain, 0)
        wait = self.delay - (time.time() - last)
        if wait > 0:
            time.sleep(wait)

        try:
            r = self.session.get(url, timeout=timeout, allow_redirects=True)
        except requests.RequestException as e:
            logger.warning("GET %s failed: %s", url, e)
            return None
        self._last_fetch[domain] = time.time()

        if r.status_code != 200:
            logger.warning("GET %s -> %d", url, r.status_code)
            return None
        cache.write_bytes(r.content)
        return r.content

    def get_text(self, url: str, **kw) -> Optional[str]:
        b = self.get(url, **kw)
        return b.decode("utf-8", errors="replace") if b else None


# -----------------------------------------------------------------------------
# Base scraper class
# -----------------------------------------------------------------------------
class BaseScraper:
    """
    Subclass and implement:
        VENDOR = "..."
        CATALOG_URLS = ["https://..."]
        def discover_models(self) -> Iterator[tuple[str, str]]:
            # yield (model_name, product_url)
        def extract_model(self, name: str, url: str) -> Optional[SpecRecord]:
            # return populated SpecRecord
    """
    VENDOR: str = ""
    CATALOG_URLS: list[str] = []
    DELAY_SEC: float = DEFAULT_DELAY_SEC

    def __init__(self):
        self.http = HttpClient(self.VENDOR, delay=self.DELAY_SEC)
        self.records: list[SpecRecord] = []

    def discover_models(self) -> Iterator[tuple[str, str]]:
        raise NotImplementedError

    def extract_model(self, name: str, url: str) -> Optional[SpecRecord]:
        raise NotImplementedError

    def run(self, limit: Optional[int] = None) -> list[SpecRecord]:
        logger.info("[%s] starting", self.VENDOR)
        count = 0
        for name, url in self.discover_models():
            try:
                rec = self.extract_model(name, url)
            except Exception as e:
                logger.warning("[%s] %s failed: %s", self.VENDOR, name, e)
                continue
            if rec and rec.is_minimally_valid():
                self.records.append(rec)
                logger.info("[%s] %s OK", self.VENDOR, rec.model)
            else:
                logger.info("[%s] %s skipped (no useful data)", self.VENDOR, name)
            count += 1
            if limit and count >= limit:
                break
        logger.info("[%s] done: %d records", self.VENDOR, len(self.records))
        return self.records


# -----------------------------------------------------------------------------
# DB writer (upsert)
# -----------------------------------------------------------------------------
def upsert_records(records: list[SpecRecord]) -> int:
    """Insert or update by (vendor, model). Returns count written."""
    if not records:
        return 0
    DB_PATH.parent.mkdir(exist_ok=True)
    if not DB_PATH.exists():
        schema_sql = (ROOT / "schema.sql").read_text()
        con = sqlite3.connect(DB_PATH)
        con.executescript(schema_sql)
        con.close()

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    written = 0
    for rec in records:
        d = rec.to_db_dict()
        existing = con.execute(
            "SELECT id FROM switches WHERE vendor=? AND model=?",
            (d["vendor"], d["model"])
        ).fetchone()
        if existing:
            # Only overwrite columns the scraper actually produced a value
            # for. A partial re-scrape must not blank existing fields
            # (e.g. clobber a good image_url with NULL).
            upd = {k: v for k, v in d.items()
                   if k not in ("vendor", "model") and v not in (None, "")}
            if upd:
                sets = ",".join(f"{k}=?" for k in upd)
                con.execute(
                    f"UPDATE switches SET {sets} WHERE id=?",
                    list(upd.values()) + [existing["id"]]
                )
        else:
            keys = ",".join(d.keys())
            qs = ",".join("?" * len(d))
            con.execute(f"INSERT INTO switches ({keys}) VALUES ({qs})", list(d.values()))
        written += 1
    con.commit()
    con.close()
    return written
