"""
Parsing utilities - HTML table extraction, PDF text extraction,
and regex-based field normalization. No LLMs, no APIs.
"""
from __future__ import annotations

import io
import re
import logging
from typing import Optional

from bs4 import BeautifulSoup

logger = logging.getLogger("scrapers.parsers")

# Try to import PDF libs - optional but recommended
try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

try:
    import fitz  # pymupdf
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False


# -----------------------------------------------------------------------------
# HTML helpers
# -----------------------------------------------------------------------------
def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def extract_spec_tables(html: str) -> dict[str, str]:
    """
    Pull key/value pairs from HTML spec tables.
    Handles common patterns:
      - <table><tr><th>Key</th><td>Value</td></tr>...
      - <table><tr><td>Key</td><td>Value</td></tr>...
      - <dl><dt>Key</dt><dd>Value</dd>...
      - Two-column lists/divs
    """
    s = soup(html)
    out: dict[str, str] = {}

    for table in s.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) >= 2:
                key = _clean(cells[0].get_text())
                val = _clean(cells[1].get_text())
                if key and val and key.lower() != val.lower():
                    out.setdefault(key, val)

    for dl in s.find_all("dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        for dt, dd in zip(dts, dds):
            key = _clean(dt.get_text())
            val = _clean(dd.get_text())
            if key and val:
                out.setdefault(key, val)

    # Definition-style divs (some sites): <div class="spec-row"><span class="label">...
    for row in s.select(".spec-row, .spec, .specs-row, [class*='spec'], [class*='Spec']"):
        labels = row.select(".label, .spec-label, .key, dt")
        values = row.select(".value, .spec-value, .val, dd")
        if labels and values:
            key = _clean(labels[0].get_text())
            val = _clean(values[0].get_text())
            if key and val and key not in out:
                out[key] = val

    return out


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def find_datasheet_link(html: str, base_url: str = "") -> Optional[str]:
    """Find a PDF datasheet link on a product page."""
    s = soup(html)
    candidates = []
    for a in s.find_all("a", href=True):
        href = a["href"]
        text = a.get_text().lower()
        if ".pdf" in href.lower() and any(
            k in (href + text).lower()
            for k in ("datasheet", "data-sheet", "spec", "ds_", "ds-", "/ds/")
        ):
            candidates.append(href)
    if not candidates:
        # Any PDF as a fallback
        for a in s.find_all("a", href=True):
            if a["href"].lower().endswith(".pdf"):
                candidates.append(a["href"])
                break
    if not candidates:
        return None
    url = candidates[0]
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/") and base_url:
        from urllib.parse import urljoin
        return urljoin(base_url, url)
    return url


# Image filename heuristics - what looks like a product image vs a logo/icon
PRODUCT_IMAGE_KEYWORDS = (
    "switch", "product", "hero", "main", "front", "primary",
    "gallery", "showcase", "shot",
)

NOT_PRODUCT_KEYWORDS = (
    "logo", "icon", "favicon", "badge", "spinner", "loading",
    "placeholder", "avatar", "thumb_", "pixel", "tracker",
    "social", "share", "facebook", "twitter", "linkedin",
    "rss", "footer", "header", "nav", "menu",
    "open-graph", "opengraph", "og-image", "og_image", "sprite",
    "brand-logo", "brand_logo",
    "opacity0", "spacer", "blank.", "transparent.", "1x1", "/empty",
    "nolook", "noimage", "no-image", "lazy", "loading.gif",
)


def find_product_image(html: str, base_url: str = "") -> Optional[str]:
    """
    Extract a product image URL from a vendor product page.

    Strategy:
    1. OpenGraph / Twitter card image (most reliable when vendors set them)
    2. JSON-LD structured data (schema.org Product)
    3. <img> tags ranked by likelihood of being a product photo
       (file path, size hints in URL, alt text matching)

    Returns absolute URL, or None.
    """
    s = soup(html)

    # 1. OpenGraph / Twitter card - most vendors set this
    for meta_attr in ("og:image", "og:image:secure_url", "twitter:image",
                      "twitter:image:src"):
        # Some use 'property=', some use 'name='
        for selector in (f'meta[property="{meta_attr}"]',
                         f'meta[name="{meta_attr}"]'):
            tag = s.select_one(selector)
            if tag and tag.get("content"):
                url = _absolutize(tag["content"], base_url)
                if url and _looks_like_image(url) and not _is_logo_like(url):
                    return url

    # 2. JSON-LD structured data
    for script in s.find_all("script", type="application/ld+json"):
        try:
            import json as _json
            data = _json.loads(script.string or "{}")
            img = _extract_image_from_jsonld(data)
            if img:
                abs_img = _absolutize(img, base_url)
                if abs_img and not _is_logo_like(abs_img):
                    return abs_img
        except (ValueError, TypeError):
            continue

    # 3. <img> tags - rank by heuristics
    candidates = []
    for img in s.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original")
        if not src:
            # check srcset for highest-res variant
            srcset = img.get("srcset", "")
            if srcset:
                src = srcset.split(",")[-1].strip().split(" ")[0]
        if not src:
            continue
        url = _absolutize(src, base_url)
        if not url or not _looks_like_image(url) or _is_logo_like(url):
            continue
        score = _score_image(url, img)
        if score > 0:
            candidates.append((score, url))

    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]

    return None


def _absolutize(url: str, base_url: str) -> Optional[str]:
    """Convert relative URL to absolute. Returns None on garbage input."""
    if not url:
        return None
    url = url.strip()
    if url.startswith("data:"):
        return None  # inline base64, skip
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if base_url:
        from urllib.parse import urljoin
        return urljoin(base_url, url)
    return None


def _looks_like_image(url: str) -> bool:
    """Quick check that URL is a real image file."""
    u = url.lower().split("?")[0].split("#")[0]
    return any(u.endswith(ext) for ext in
               (".jpg", ".jpeg", ".png", ".webp", ".svg", ".gif"))


def _is_logo_like(url: str) -> bool:
    """True if the URL is a brand logo / social-card / icon rather than an
    actual product photo. Applied to og:image, JSON-LD and ranked <img>
    results so vendors that set their logo as og:image don't win."""
    u = url.lower()
    if any(kw in u for kw in NOT_PRODUCT_KEYWORDS):
        return True
    # Product photos are raster; the only SVGs we see are logos.
    if u.split("?")[0].split("#")[0].endswith(".svg"):
        return True
    return False


def _score_image(url: str, img_tag) -> int:
    """Score how likely an <img> is the product photo. 0 = skip."""
    score = 0
    u = url.lower()

    # Negative signals
    for kw in NOT_PRODUCT_KEYWORDS:
        if kw in u:
            return 0  # disqualified

    # Positive signals in URL
    for kw in PRODUCT_IMAGE_KEYWORDS:
        if kw in u:
            score += 5

    # Size hints in URL (large images more likely product photos)
    size_match = re.search(r"(\d{3,4})[x_-](\d{3,4})", u)
    if size_match:
        w, h = int(size_match.group(1)), int(size_match.group(2))
        if w >= 300 and h >= 200:
            score += 3

    # Alt text
    alt = (img_tag.get("alt") or "").lower()
    if alt:
        for kw in PRODUCT_IMAGE_KEYWORDS:
            if kw in alt:
                score += 2
        for kw in NOT_PRODUCT_KEYWORDS:
            if kw in alt:
                return 0

    # Class/id hints
    classes = " ".join(img_tag.get("class") or []).lower()
    img_id = (img_tag.get("id") or "").lower()
    combined = classes + " " + img_id
    for kw in PRODUCT_IMAGE_KEYWORDS:
        if kw in combined:
            score += 3
    for kw in NOT_PRODUCT_KEYWORDS:
        if kw in combined:
            return 0

    # Default: small positive if no signals (we have to start somewhere)
    if score == 0:
        score = 1

    return score


def _extract_image_from_jsonld(data) -> Optional[str]:
    """Recursively look for an 'image' field in JSON-LD data."""
    if isinstance(data, dict):
        # Direct image field
        img = data.get("image")
        if isinstance(img, str):
            return img
        if isinstance(img, dict):
            url = img.get("url") or img.get("@id")
            if url:
                return url
        if isinstance(img, list) and img:
            first = img[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict):
                return first.get("url") or first.get("@id")
        # Recurse
        for v in data.values():
            result = _extract_image_from_jsonld(v)
            if result:
                return result
    elif isinstance(data, list):
        for item in data:
            result = _extract_image_from_jsonld(item)
            if result:
                return result
    return None


# -----------------------------------------------------------------------------
# PDF helpers
# -----------------------------------------------------------------------------
def pdf_to_text(pdf_bytes: bytes) -> str:
    """Extract all text from a PDF. Tries pymupdf (fast) then pdfplumber."""
    if HAS_PYMUPDF:
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            return "\n".join(page.get_text() for page in doc)
        except Exception as e:
            logger.warning("pymupdf failed: %s", e)
    if HAS_PDFPLUMBER:
        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as p:
                return "\n".join(page.extract_text() or "" for page in p.pages)
        except Exception as e:
            logger.warning("pdfplumber failed: %s", e)
    return ""


def pdf_extract_tables(pdf_bytes: bytes) -> list[list[list[str]]]:
    """Extract tables from PDF (needs pdfplumber)."""
    if not HAS_PDFPLUMBER:
        return []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as p:
            tables = []
            for page in p.pages:
                for t in page.extract_tables() or []:
                    tables.append(t)
            return tables
    except Exception as e:
        logger.warning("pdfplumber tables failed: %s", e)
        return []


# -----------------------------------------------------------------------------
# Field normalization (pure regex, no LLM)
# -----------------------------------------------------------------------------
INT_RE = re.compile(r"[-+]?\d+")
FLOAT_RE = re.compile(r"[-+]?\d*\.?\d+")


def parse_int(s: Optional[str]) -> Optional[int]:
    if not s: return None
    m = INT_RE.search(s.replace(",", ""))
    return int(m.group()) if m else None


def parse_float(s: Optional[str]) -> Optional[float]:
    if not s: return None
    m = FLOAT_RE.search(s.replace(",", ""))
    return float(m.group()) if m else None


def parse_capacity_gbps(s: Optional[str]) -> Optional[float]:
    """Parse '4 Tbps', '4000 Gbps', '176Gbps' -> Gbps."""
    if not s: return None
    s = s.lower().replace(",", "")
    n = parse_float(s)
    if n is None: return None
    if "tbps" in s or "tb/s" in s:
        return n * 1000
    if "mbps" in s and "gbps" not in s:
        return n / 1000
    return n  # default Gbps


def parse_mpps(s: Optional[str]) -> Optional[float]:
    """Forwarding rate. 'mpps', 'million pps'."""
    if not s: return None
    return parse_float(s)


def parse_buffer_mb(s: Optional[str]) -> Optional[float]:
    """Parse '32 MB', '8 GB', '512KB' -> MB."""
    if not s: return None
    s = s.lower()
    n = parse_float(s)
    if n is None: return None
    if "gb" in s and "mb" not in s:
        return n * 1024
    if "kb" in s:
        return n / 1024
    return n


def parse_poe_standard(s: Optional[str]) -> Optional[str]:
    """Normalize PoE notation."""
    if not s: return None
    sl = s.lower()
    if "upoe+" in sl or "upoe plus" in sl: return "UPOE+"
    if "802.3bt" in sl or "poe++" in sl or "type 4" in sl or "90w" in sl: return "PoE++"
    if "802.3at" in sl or "poe+" in sl: return "PoE+"
    if "802.3af" in sl: return "PoE"
    if "poe" in sl and "no poe" not in sl: return "PoE"
    if "no poe" in sl or sl.strip() == "none": return "None"
    return None


def parse_layer(s: Optional[str]) -> Optional[str]:
    """Detect L2/L2+/L3."""
    if not s: return None
    sl = s.lower()
    if "layer 3" in sl or "l3" in sl: return "L3"
    if "layer 2+" in sl or "l2+" in sl: return "L2+"
    if "layer 2" in sl or "l2" in sl: return "L2"
    return None


PORT_CONFIG_RE = re.compile(
    r"(\d+)\s*[x×]\s*([\d./]+)\s*(g|gb|gbe|gbps|gigabit)?(?:base[\-\s]?[ct])?\s*(rj45|sfp\+?|sfp28|qsfp\+?|qsfp28|qsfp-?dd|osfp)?",
    re.IGNORECASE,
)


def parse_port_config(s: Optional[str]) -> tuple[Optional[int], Optional[int], Optional[str]]:
    """
    From '48x 1G RJ45 + 4x 10G SFP+' return:
        (total_ports, max_speed_gbps, normalized_config)
    """
    if not s: return None, None, None
    matches = PORT_CONFIG_RE.findall(s)
    if not matches:
        return None, None, _clean(s)
    total = 0
    max_speed = 0
    for count_s, speed_s, _unit, _media in matches:
        try:
            count = int(count_s)
        except ValueError:
            continue
        total += count
        try:
            speed = float(speed_s)
        except ValueError:
            continue
        if speed > max_speed:
            max_speed = int(speed)
    return total or None, max_speed or None, _clean(s)


FEATURE_PATTERNS = {
    "EVPN-VXLAN": re.compile(r"\bevpn[\s-]?vxlan\b", re.I),
    "VXLAN": re.compile(r"\bvxlan\b", re.I),
    "MACsec": re.compile(r"\bmacsec\b", re.I),
    "BGP": re.compile(r"\bbgp\b", re.I),
    "OSPF": re.compile(r"\bospf\b", re.I),
    "MPLS": re.compile(r"\bmpls\b", re.I),
    "Segment Routing": re.compile(r"segment\s*routing|\bsrv6\b", re.I),
    "VRRP": re.compile(r"\bvrrp\b", re.I),
    "MLAG": re.compile(r"\bml?ag\b|\bvpc\b|\bvsx\b|virtual chassis|stackwise", re.I),
    "PTP": re.compile(r"\bptp\b|ieee\s*1588", re.I),
    "VLAN": re.compile(r"\bvlan\b", re.I),
    "STP": re.compile(r"\b(stp|rstp|mstp)\b", re.I),
    "QoS": re.compile(r"\bqos\b|quality of service", re.I),
    "ACL": re.compile(r"\bacl\b|access control list", re.I),
    "RoCE": re.compile(r"\broce\b", re.I),
    "IGMP": re.compile(r"\bigmp\b", re.I),
}


def detect_features(text: str) -> list[str]:
    return [name for name, pat in FEATURE_PATTERNS.items() if pat.search(text)]


# -----------------------------------------------------------------------------
# Convenience: map raw spec dict to SpecRecord fields
# -----------------------------------------------------------------------------
KEY_ALIASES = {
    # port count
    "port_count": (
        "ports", "port count", "number of ports", "total ports",
        "ethernet ports", "interface ports", "ports total",
    ),
    "port_config": (
        "ports", "port configuration", "interfaces", "network ports",
        "ethernet ports", "ports & cabling",
    ),
    "switching_capacity_gbps": (
        "switching capacity", "switch capacity", "switching bandwidth",
        "total bandwidth", "throughput", "non-blocking throughput",
    ),
    "forwarding_rate_mpps": (
        "forwarding rate", "forwarding bandwidth", "packets per second",
        "forwarding performance", "throughput (mpps)",
    ),
    "buffer_mb": (
        "buffer", "packet buffer", "packet buffer memory",
        "shared buffer", "buffer memory",
    ),
    "latency_ns": ("latency", "port-to-port latency", "switch latency"),
    "mac_table_size": ("mac addresses", "mac table", "mac entries", "max mac"),
    "poe_standard": ("poe", "poe type", "power over ethernet"),
    "poe_budget_w": ("poe budget", "poe power", "poe power budget",
                     "max poe power", "total poe", "max poe"),
    "power_max_w": ("max power", "maximum power", "power consumption (max)"),
    "power_typical_w": ("typical power", "power consumption (typical)"),
    "layer": ("layer", "switching layer"),
    "rack_units": ("form factor", "rack units", "size", "height"),
    "nos": ("operating system", "os", "software", "network os"),
}


def map_kv_to_record_fields(kv: dict[str, str]) -> dict[str, object]:
    """
    Try to map a raw key/value table from a product page to our schema fields.
    Returns a dict of fields to apply to a SpecRecord.
    """
    norm_kv = {k.lower().strip(): v for k, v in kv.items()}
    out: dict[str, object] = {}

    for field_name, aliases in KEY_ALIASES.items():
        for alias in aliases:
            if alias in norm_kv:
                raw = norm_kv[alias]
                if field_name == "port_count":
                    out[field_name] = parse_int(raw)
                elif field_name == "switching_capacity_gbps":
                    out[field_name] = parse_capacity_gbps(raw)
                elif field_name == "forwarding_rate_mpps":
                    out[field_name] = parse_mpps(raw)
                elif field_name == "buffer_mb":
                    out[field_name] = parse_buffer_mb(raw)
                elif field_name == "latency_ns":
                    out[field_name] = parse_int(raw)
                elif field_name == "mac_table_size":
                    out[field_name] = parse_int(raw)
                elif field_name == "poe_standard":
                    out[field_name] = parse_poe_standard(raw)
                elif field_name in ("poe_budget_w", "power_typical_w", "power_max_w"):
                    out[field_name] = parse_int(raw)
                elif field_name == "layer":
                    out[field_name] = parse_layer(raw)
                elif field_name == "rack_units":
                    out[field_name] = parse_int(raw)
                elif field_name == "port_config":
                    pc, _, conf = parse_port_config(raw)
                    if pc:
                        # parse_port_config is more reliable than the simple
                        # int parse for multi-section configs like "48x 1G + 4x 10G"
                        out["port_count"] = pc
                    out["port_config"] = conf
                else:
                    out[field_name] = raw
                break
    return out
