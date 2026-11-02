"""
Live extractor v2 - pure code, no LLM, no APIs.

Fetches multiple sources in parallel, merges with voting on conflicts,
uses pdfplumber's table extraction, scores confidence per field, and
stays under a hard 9-second wall clock.
"""
from __future__ import annotations

import concurrent.futures
import logging
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import requests

from live_search import guess_vendor, search_for_switch
from scrapers.base import SpecRecord, upsert_records
from scrapers.parsers import (
    HAS_PDFPLUMBER,
    detect_features, extract_spec_tables, find_product_image,
    map_kv_to_record_fields, pdf_extract_tables, pdf_to_text,
)

logger = logging.getLogger("live_extract")

FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

# Trusted vendor domains - sources from these get higher confidence
VENDOR_DOMAINS = {
    "cisco.com", "meraki.com", "arista.com", "juniper.net",
    "arubanetworks.com", "hpe.com", "dell.com",
    "nvidia.com", "mellanox.com", "extremenetworks.com",
    "huawei.com", "h3c.com", "ui.com", "ubnt.com",
    "mikrotik.com", "tp-link.com", "netgear.com",
    "zyxel.com", "dlink.com", "ruijienetworks.com",
    "fortinet.com", "fs.com", "cambiumnetworks.com",
    "edge-core.com", "lenovo.com", "moxa.com",
    "allied-telesis.com", "alliedtelesis.com",
    "trendnet.com",
}
# Extend with every domain from vendors.json so vendor inference works
# for the whole 134-entry registry, not just hardcoded names.
try:
    from vendor_registry import domains as _vr_domains
    VENDOR_DOMAINS |= _vr_domains()
except Exception:  # pragma: no cover
    pass


# Known non-switch product families that should NOT be fitted into the
# switch schema. Each is an unambiguous NIC / DPU / SmartNIC name.
_NON_SWITCH_RE = re.compile(
    r"\b(connectx[-\s]?\d+|bluefield[-\s]?\d+|smartnic|"
    r"intel\s+x(?:5[2-7]|710|722)\d*|qlogic\s+\w+\s+hba)\b",
    re.IGNORECASE,
)


@dataclass
class FieldValue:
    value: object
    source: str
    confidence: float = 1.0


@dataclass
class ExtractionResult:
    fields: dict[str, FieldValue] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)

    def add(self, key: str, value: object, source: str, confidence: float = 1.0):
        if value is None or value == "":
            return
        existing = self.fields.get(key)
        if not existing or confidence > existing.confidence:
            self.fields[key] = FieldValue(value, source, confidence)

    def to_dict(self) -> dict:
        return {k: v.value for k, v in self.fields.items()}

    def confidence_map(self) -> dict[str, float]:
        return {k: round(v.confidence, 2) for k, v in self.fields.items()}


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------
def fetch_url(url: str, timeout: int = 5) -> Optional[bytes]:
    try:
        r = requests.get(
            url, headers=FETCH_HEADERS, timeout=timeout,
            allow_redirects=True,
        )
        if r.status_code == 200:
            if len(r.content) > 25 * 1024 * 1024:  # 25MB cap
                logger.warning("Skipping huge response: %s (%d bytes)", url, len(r.content))
                return None
            return r.content
    except requests.RequestException as e:
        logger.debug("fetch %s: %s", url, e)
    return None


def fetch_parallel(urls: list[str], timeout: int = 5, max_workers: int = 4) -> dict[str, bytes]:
    pages = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_url, u, timeout): u for u in urls}
        try:
            for fut in concurrent.futures.as_completed(futures, timeout=timeout + 2):
                content = fut.result()
                if content:
                    pages[futures[fut]] = content
        except concurrent.futures.TimeoutError:
            logger.warning("Some fetches timed out")
    return pages


# ---------------------------------------------------------------------------
# Parsing one source
# ---------------------------------------------------------------------------
def parse_source(url: str, content: bytes, model_hint: str = "") -> dict[str, str]:
    if not content:
        return {}
    is_pdf = url.lower().endswith(".pdf") or content[:4] == b"%PDF"
    if is_pdf:
        return _parse_pdf(content, model_hint=model_hint)
    try:
        html = content.decode("utf-8", errors="replace")
    except Exception:
        return {}
    return extract_spec_tables(html)


# SKU-like header cells, e.g. "DES-1008C", "FortiSwitch-448E", "C9300-48P"
_SKU_LIKE = re.compile(r"^[A-Za-z]{1,}[\-_]?\d+[A-Za-z0-9\-_+]*$")


def _is_header_row(cells: list[str]) -> bool:
    """A spec table row whose value columns are model SKUs (not values)."""
    return sum(1 for c in cells[1:] if _SKU_LIKE.match(c.strip())) >= 1


def _pick_model_column(table: list[list[str]], model_hint: str) -> int | None:
    """In a multi-model comparison table, return the column index whose
    header cell matches `model_hint`. None if no header / no match."""
    if not model_hint:
        return None
    norm = re.sub(r"[^a-z0-9]", "", model_hint.lower())
    for row in (table or [])[:10]:
        if not row:
            continue
        cells = [(c or "").strip() for c in row]
        if not _is_header_row(cells):
            continue
        for i, c in enumerate(cells[1:], start=1):
            cn = re.sub(r"[^a-z0-9]", "", c.lower())
            if cn and (cn in norm or norm in cn):
                return i
    return None


def _parse_pdf(pdf_bytes: bytes, model_hint: str = "") -> dict[str, str]:
    """PDF parsing - tables first, text patterns as supplement.

    When `model_hint` matches a column header in a multi-model comparison
    table, that column's values are used (fixes the wrong-column bug where
    a DES-1008C query returned DES-1005C's switching capacity).
    """
    out: dict[str, str] = {}

    if HAS_PDFPLUMBER:
        try:
            for table in pdf_extract_tables(pdf_bytes):
                if not table or len(table) < 2:
                    continue
                col = _pick_model_column(table, model_hint)
                for row in table:
                    if not row:
                        continue
                    cells = [(c or "").strip() for c in row]
                    if len(cells) < 2 or not cells[0]:
                        continue
                    if _is_header_row(cells):
                        continue  # SKU header, not a spec
                    vidx = col if (col is not None and col < len(cells)
                                   and cells[col]) else 1
                    if vidx < len(cells) and cells[vidx]:
                        out.setdefault(cells[0], cells[vidx])
        except Exception as e:
            logger.debug("pdfplumber tables failed: %s", e)

    text = pdf_to_text(pdf_bytes)
    for line in text.splitlines():
        m = re.match(
            r"^\s*([A-Za-z][A-Za-z 0-9/()\-+]{3,50})\s*[:\.]?\s+(\d.{0,200}|[A-Z].{0,200})$",
            line,
        )
        if m:
            k = m.group(1).strip()
            v = m.group(2).strip()
            if 3 < len(k) < 60 and len(v) < 250:
                out.setdefault(k, v)

    return out


# ---------------------------------------------------------------------------
# Confidence and merging
# ---------------------------------------------------------------------------
def source_confidence(url: str) -> float:
    """0 to 1. Vendor domain PDFs are most trusted."""
    domain = urlparse(url).netloc.replace("www.", "")
    is_vendor = any(vd in domain for vd in VENDOR_DOMAINS)
    is_pdf = url.lower().endswith(".pdf")
    if is_vendor and is_pdf: return 1.0
    if is_vendor:            return 0.85
    if is_pdf:               return 0.7
    return 0.5


def merge_extractions(sources: dict[str, dict[str, str]]) -> ExtractionResult:
    """
    Multi-source merge with voting:
    - Highest confidence wins
    - Agreement across sources boosts confidence
    """
    result = ExtractionResult(sources=list(sources.keys()))
    field_votes: dict[str, list[tuple[object, str, float]]] = {}
    all_text = ""

    for url, kv in sources.items():
        if not kv:
            continue
        conf = source_confidence(url)
        mapped = map_kv_to_record_fields(kv)
        for fname, value in mapped.items():
            if value is None:
                continue
            field_votes.setdefault(fname, []).append((value, url, conf))
        all_text += " ".join(f"{k} {v}" for k, v in kv.items())

    for fname, votes in field_votes.items():
        value_counts = Counter(str(v[0]) for v in votes)

        def vote_score(v):
            val, url, conf = v
            return conf + 0.1 * (value_counts[str(val)] - 1)

        best = max(votes, key=vote_score)
        value, source, base_conf = best
        agreement = value_counts[str(value)]
        final_conf = min(1.0, base_conf + 0.1 * (agreement - 1))
        result.add(fname, value, source, final_conf)

    result.features = detect_features(all_text.lower())
    return result


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------
def live_lookup(
    query: str,
    *,
    deadline_sec: float = 9.0,
    persist: bool = True,
    n_sources: int = 3,
) -> Optional[SpecRecord]:
    """
    Live web fetch + multi-source extraction for an unknown model.
    Returns a SpecRecord (with .confidence dict attached) or None.
    """
    t0 = time.time()

    # Refuse known non-switch product lines (NICs / DPUs) up front. Without
    # this guard, a query like "ConnectX-6" gets a "switch" record with no
    # ports / no capacity, vendor inferred from whichever URL happened to
    # rank first. Better to honest-fail.
    if _NON_SWITCH_RE.search(query or ""):
        logger.info("[live_lookup] %r is a NIC/DPU, not a switch", query)
        return None

    vendor = guess_vendor(query)
    remaining = lambda: deadline_sec - (time.time() - t0)

    # ----- Step 1: search -----
    if remaining() < 4:
        logger.warning("No time for search (%.1fs left)", remaining())
        return None

    search_timeout = max(2, min(remaining() - 5, 3.5))
    logger.info("[%.1fs left] Searching for %r (vendor=%s)",
                remaining(), query, vendor)
    try:
        results = search_for_switch(
            query, vendor, max_results=n_sources * 2,
            timeout=int(search_timeout),
        )
    except Exception as e:
        logger.warning("Search failed: %s", e)
        return None

    if not results:
        logger.info("No search results")
        return None

    candidates = sorted(results,
                        key=lambda r: -source_confidence(r["url"]))[:n_sources]
    urls = [r["url"] for r in candidates]
    logger.info("[%.1fs left] Top sources: %s",
                remaining(), [urlparse(u).netloc for u in urls])

    # ----- Step 2: fetch in parallel -----
    if remaining() < 3:
        return None
    fetch_timeout = max(2, int(remaining() - 1.5))
    pages = fetch_parallel(urls, timeout=fetch_timeout)
    if not pages:
        logger.warning("No pages fetched")
        return None

    # Drop pages that look like multi-product catalogs or don't mention
    # the queried model at all — they're the reason TEG-30284 was getting
    # 12-port / 240-Gbps numbers from some other model in a TRENDnet
    # product catalog PDF.
    filtered = _filter_pages_to_model(pages, query)
    if filtered:
        logger.info("[%.1fs left] Model-match filter: %d/%d pages kept",
                    remaining(), len(filtered), len(pages))
        pages = filtered
    else:
        logger.info("[%.1fs left] Model-match filter rejected all pages — "
                    "falling back to all sources (low confidence)",
                    remaining())
    logger.info("[%.1fs left] Got %d/%d pages",
                remaining(), len(pages), len(urls))

    # ----- Step 3: parse each source -----
    if remaining() < 0.5:
        return None
    source_extractions: dict[str, dict[str, str]] = {}
    for url, content in pages.items():
        try:
            kv = parse_source(url, content, model_hint=query)
            if kv:
                source_extractions[url] = kv
        except Exception as e:
            logger.debug("Parse %s failed: %s", url, e)

    # ----- Step 4: merge with voting -----
    extraction = merge_extractions(source_extractions) if source_extractions \
        else ExtractionResult()

    # ----- Step 5: build record -----
    # Graceful degradation: even when no-LLM extraction can't parse a
    # messy vendor PDF, still return the official datasheet so "any
    # model" yields something useful instead of nothing.
    model = _best_model_name(query, source_extractions)
    datasheet = _best_datasheet_url(pages) or (urls[0] if urls else None)
    extras = _collect_extra_specs(source_extractions)
    rec = SpecRecord(
        vendor=(vendor or _vendor_from_sources(source_extractions)
                or "Unknown").title(),
        model=model,
        features=extraction.features,
        datasheet_url=datasheet,
        image_url=_extract_image(pages),
        extra_specs=extras,
        **{k: v for k, v in extraction.to_dict().items() if v is not None},
    )

    if not rec.is_minimally_valid():
        logger.warning("Final record not minimally valid (no datasheet either)")
        return None
    if not extraction.fields:
        logger.info("No specs parsed (no-LLM limit) — returning datasheet only")

    # ----- Step 6: persist for next time -----
    if persist:
        try:
            upsert_records([rec])
            logger.info("Cached %s / %s in DB", rec.vendor, rec.model)
        except Exception as e:
            logger.warning("Persist failed: %s", e)

    rec.confidence = extraction.confidence_map()  # type: ignore[attr-defined]
    elapsed = time.time() - t0
    logger.info("Live lookup complete in %.1fs", elapsed)
    return rec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_VENDOR_WORDS = {
    "cisco", "catalyst", "nexus", "meraki", "arista", "juniper", "qfx",
    "aruba", "hpe", "hp", "dell", "powerswitch", "nvidia", "mellanox",
    "spectrum", "extreme", "huawei", "h3c", "ubiquiti", "unifi", "mikrotik",
    "tp-link", "tplink", "omada", "netgear", "zyxel", "d-link", "ruijie",
    "fortinet", "fortiswitch", "switch", "switches",
}


def _looks_like_model(s: str) -> bool:
    """A real model has a digit and isn't a generic English word."""
    return bool(s) and any(c.isdigit() for c in s) and 2 < len(s) < 50


def _best_model_name(query: str, extractions: dict[str, dict[str, str]]) -> str:
    # 1. Trust the user's query first — strip vendor/category words and
    #    keep the rest if it carries a model number ("FortiSwitch 448E").
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-\+\._/]*", query)
    kept = [w for w in words if w.lower() not in _VENDOR_WORDS]
    if any(_looks_like_model(w) for w in kept):
        return " ".join(kept) if kept else query

    # 2. Otherwise accept an extracted model/SKU, but only if it actually
    #    looks like a model number (not junk like "Numbers").
    for kv in extractions.values():
        for k, v in kv.items():
            if k.lower() in ("model", "model number", "product code",
                             "part number", "sku") and _looks_like_model(v):
                return v.strip()

    # 3. Last resort: the most model-looking token, else the raw query.
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-\+\._/]{2,}", query)
    if tokens:
        return max(tokens, key=lambda t: (
            sum(c.isdigit() for c in t), "-" in t, len(t)
        ))
    return query


def _vendor_from_sources(extractions: dict[str, dict[str, str]]) -> Optional[str]:
    """Pick the vendor from the highest-confidence source whose domain
    is recognised. Confidence-weighted — a Cisco doc that just *mentions*
    a competitor no longer wins over an NVIDIA PDF in the same fetch."""
    best = (None, -1.0)
    for url in extractions:
        conf = source_confidence(url)
        if conf <= best[1]:
            continue
        domain = urlparse(url).netloc.replace("www.", "")
        for vd in VENDOR_DOMAINS:
            if vd in domain:
                best = (vd.split(".")[0], conf)
                break
    return best[0]


def _best_datasheet_url(pages: dict[str, bytes]) -> Optional[str]:
    pdfs = [u for u in pages if u.lower().endswith(".pdf")]
    if pdfs: return pdfs[0]
    vendor_urls = [
        u for u in pages
        if any(vd in urlparse(u).netloc for vd in VENDOR_DOMAINS)
    ]
    if vendor_urls: return vendor_urls[0]
    return next(iter(pages.keys()), None)


def _extract_image(pages: dict[str, bytes]) -> Optional[str]:
    """Find a product image URL across fetched HTML pages (skip PDFs),
    preferring vendor-domain pages."""
    for url, content in sorted(pages.items(),
                               key=lambda kv: -source_confidence(kv[0])):
        if url.lower().endswith(".pdf"):
            continue
        try:
            html = content.decode("utf-8", errors="replace")
            img_url = find_product_image(html, base_url=url)
            if img_url:
                logger.info("Found image: %s", img_url)
                return img_url
        except Exception as e:
            logger.debug("Image extract from %s failed: %s", url, e)
    return None


# Heuristic: a SKU-like token is uppercase letters + digits + hyphens.
_SKU_TOKEN = re.compile(r"\b[A-Z][A-Z]+[-]?\d{2,}[\w\-]*\b")


def _filter_pages_to_model(pages: dict[str, bytes], query: str) -> dict[str, bytes]:
    """Keep only pages that genuinely describe the queried model.

    Fast (no PDF library calls — raw bytes scan + URL check):
      - URL that names the model -> trusted, kept immediately
      - otherwise the bytes must contain the model SKU AND not look
        like a multi-product catalog (>12 distinct SKU patterns).
    """
    if not query:
        return pages
    # The model number, not the whole query: a page says "DGS-1210-52",
    # never "D-Link DGS-1210-52" run together (the vendor word made every
    # page drop, so the lookup fell back to unfiltered pages, low confidence).
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-\+\._/]*", query)
    model_words = [w for w in words if w.lower() not in _VENDOR_WORDS and any(c.isdigit() for c in w)]
    key = max(model_words, key=len) if model_words else query
    norm = re.sub(r"[^a-z0-9]", "", key.lower())
    if len(norm) < 4:
        return pages

    kept = {}
    for url, content in pages.items():
        url_n = re.sub(r"[^a-z0-9]", "", url.lower())
        if norm in url_n:
            kept[url] = content
            continue
        try:
            text = content[:200_000].decode("latin-1", errors="ignore").lower()
        except Exception:
            continue
        if norm not in re.sub(r"[^a-z0-9]", "", text):
            logger.debug("filter: %s drops — %r not in content", url, key)
            continue
        skus = set(_SKU_TOKEN.findall(text[:30_000].upper()))
        if len(skus) > 12:
            logger.debug("filter: %s drops — catalog (%d SKUs)", url, len(skus))
            continue
        kept[url] = content
    return kept


# Keys we deliberately drop from "extras" because they're either pure
# SKU/header noise, navigation chrome, or already covered by structured fields.
_EXTRA_SKIP = re.compile(
    r"^(home|search|menu|share|cookie|privacy|terms|sitemap|copyright|"
    r"contact|support|login|register|products?|switches?|datasheet)$",
    re.IGNORECASE,
)


def _collect_extra_specs(sources: dict[str, dict[str, str]]) -> dict[str, str]:
    """Every raw key/value pair across sources that didn't map to a schema
    field. First-source-wins on duplicate keys; HTML/PDF noise filtered."""
    extras: dict[str, str] = {}
    for kv in sources.values():
        for k, v in (kv or {}).items():
            if not k or not v:
                continue
            ks = k.strip()
            vs = v.strip() if isinstance(v, str) else str(v)
            if not ks or not vs or len(ks) > 60 or len(vs) > 600:
                continue
            if _EXTRA_SKIP.match(ks):
                continue
            # Drop if this key maps to a structured schema field already
            try:
                if map_kv_to_record_fields({ks: vs}):
                    continue
            except Exception:
                pass
            extras.setdefault(ks, vs)
    return extras
