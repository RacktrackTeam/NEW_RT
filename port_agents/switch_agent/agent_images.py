"""
agent_images.py - product photos through RackTrack's switch-spec agent.

Image search for the spec agent (adapted from teammate 2's, Srikanth's, product_images.py),
run against the agent in the RT-EOS repo without modifying it:

  1. official product pages: the spec record's datasheet page, the agent's vendor
     scraper's model list (18 vendors: Cisco, Dell, Aruba, Juniper, Fortinet, Huawei,
     Ubiquiti, NETGEAR, ...), and vendor web shops (D-Link)
       -> every gallery image on the page (find_product_images below)
  2. Bing image search (no key) when the official pages give nothing; every hit must
     name the model

    from agent_images import agent_product_images
    for c in agent_product_images("Dell", "PowerConnect 2824"):
        print(c.score, c.source, c.url)

Needs the RT-EOS repo (RACKTRACK_REPO); returns [] without it.
"""
from __future__ import annotations

import os
import sys

import racktrack as _rt

if str(_rt.SPEC_AGENT) not in sys.path:
    sys.path.append(str(_rt.SPEC_AGENT))     # appended, so it never shadows our own modules


import html as _html
import json
import logging
import re
from dataclasses import dataclass, asdict
from functools import lru_cache
from typing import Iterable, Optional
from urllib.parse import quote_plus, urlparse

import requests

import vendor_registry
from scrapers import REGISTRY
from scrapers.parsers import (_absolutize, _extract_image_from_jsonld, _is_logo_like,
                              _looks_like_image, _score_image, soup)

logger = logging.getLogger("product_images")

# Search engines and many vendor sites refuse the bot UA in scrapers.base.
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}
# Vendor web shops with a site search, for vendors without a scraper in
# scrapers.REGISTRY. Shopify stores answer /search/suggest.json.
VENDOR_STORES = {
    "D-Link": ["www.dlink.com.sg", "www.dlink.com.my"],
}
LOW_TRUST_HOSTS = ("pinimg", "pinterest", "shopee", "aliexpress", "alicdn", "ebay", "amazon",
                   "walmart", "facebook", "fbcdn", "youtube", "ytimg")


_RAW_IMG_RE = re.compile(r"""(?:https?:)?//[^\s"'<>()\\]+?\.(?:jpe?g|png|webp)(?:\?[^\s"'<>()\\]*)?""", re.I)


def find_product_images(html: str, base_url: str = "", model: str = "") -> list:
    """All plausible product photos on a page, best first (the agent's
    scrapers/parsers.py helpers, extended): og/twitter/JSON-LD images, every <img> (largest srcset
    variant), lightbox links and image URLs only present in inline scripts. URLs naming
    the model rank first."""
    s = soup(html)
    model_key = re.sub(r"[^a-z0-9]", "", model.lower())
    found = {}

    def add(raw, score):
        url = _absolutize(raw or "", base_url)
        if not url or not _looks_like_image(url) or _is_logo_like(url):
            return
        if model_key and model_key in re.sub(r"[^a-z0-9]", "", url.lower()):
            score += 20
        if re.search(r"(large|big|xl|1000|1200|1500|2000|original)", url.lower()):
            score += 3
        if re.search(r"(thumb|small|mini|_s\.|-s\.|50x|100x)", url.lower()):
            score -= 6
        found[url] = max(found.get(url, -99), score)

    for meta_attr in ("og:image", "og:image:secure_url", "twitter:image", "twitter:image:src"):
        for tag in s.select(f'meta[property="{meta_attr}"], meta[name="{meta_attr}"]'):
            add(tag.get("content"), 6)
    for script in s.find_all("script", type="application/ld+json"):
        try:
            add(_extract_image_from_jsonld(json.loads(script.string or "{}")), 6)
        except (ValueError, TypeError):
            continue
    for img in s.find_all("img"):
        srcset = img.get("srcset") or img.get("data-srcset") or ""
        largest = srcset.split(",")[-1].strip().split(" ")[0] if srcset else None
        for src in (largest, img.get("data-zoom-image"), img.get("data-large"),
                    img.get("data-src"), img.get("data-original"), img.get("src")):
            if src:
                add(src, _score_image(_absolutize(src, base_url) or src, img))
    for a in s.find_all("a", href=True):
        add(a["href"], 4)
    for raw in _RAW_IMG_RE.findall(html.replace("\\/", "/")):
        add(_html.unescape(raw), 1)
    return [u for u, sc in sorted(found.items(), key=lambda kv: -kv[1]) if sc > 0]


@dataclass
class ImageCandidate:
    url: str
    source: str           # official_page | image_search
    page: str = ""        # page the image was found on
    title: str = ""
    score: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _canonical_vendor(vendor: str) -> str:
    return vendor_registry.aliases().get((vendor or "").lower().strip(), vendor)


def _vendor_tokens(vendor: str) -> list[str]:
    """Strings that identify the vendor's own hosts, e.g. tp-link.com, dlink.com."""
    canon = _canonical_vendor(vendor)
    rec = vendor_registry.by_canonical().get(canon, {})
    toks = {_key(vendor), _key(canon)}
    site = (rec.get("website") or "").lower()
    if site:
        toks.add(_key(site.split(".")[0]))
    toks |= {_key(a) for a in rec.get("aliases", []) if len(_key(a)) >= 4}
    return [t for t in toks if len(t) >= 3]


def _get(url: str, timeout: int = 15) -> Optional[str]:
    try:
        r = requests.get(url, headers=BROWSER_HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code == 200 and "html" in r.headers.get("content-type", "html"):
            return r.text
        logger.info("GET %s -> %s", url, r.status_code)
    except requests.RequestException as e:
        logger.info("GET %s failed: %s", url, e)
    return None


@lru_cache(maxsize=32)
def _discovered_models(scraper_key: str) -> tuple[tuple[str, str], ...]:
    try:
        return tuple(REGISTRY[scraper_key]().discover_models())
    except Exception as e:  # a vendor site change must not break image lookup
        logger.warning("[%s] model discovery failed: %s", scraper_key, e)
        return ()


def store_pages(vendor: str, model: str) -> list[str]:
    """Product pages found through the vendor's own web shop search."""
    mk = _key(model)
    canon = _canonical_vendor(vendor)
    out = []
    for host in VENDOR_STORES.get(canon, []) + VENDOR_STORES.get(vendor, []):
        try:
            r = requests.get(f"https://{host}/search/suggest.json", headers=BROWSER_HEADERS, timeout=15,
                             params={"q": model, "resources[type]": "product", "resources[limit]": "5"})
            prods = r.json().get("resources", {}).get("results", {}).get("products", []) if r.ok else []
        except (requests.RequestException, ValueError) as e:
            logger.info("store search %s failed: %s", host, e)
            continue
        for prod in prods:
            if mk in _key(prod.get("url", "")) or mk in _key(prod.get("title", "")):
                out.append(f"https://{host}{prod['url'].split('?')[0]}")
    return out


def official_pages(vendor: str, model: str, known_pages: Iterable[str] = ()) -> list[str]:
    pages = [p for p in known_pages if p and not p.lower().endswith(".pdf")]
    mk = _key(model)
    vk = _vendor_tokens(vendor)
    for key in REGISTRY:
        if any(t in _key(key) or _key(key) in t for t in vk):
            for name, url in _discovered_models(key):
                # exact model match only: TL-SG1016 must not pick TL-SG1016PE's page
                if _key(name) == mk or _key(url.rstrip("/").rsplit("/", 1)[-1]) == mk:
                    pages.append(url)
    pages += store_pages(vendor, model)
    return list(dict.fromkeys(pages))


def image_search(query: str, limit: int = 30) -> list[ImageCandidate]:
    """Bing image results (image URL, page URL, title). Free, no API key."""
    if os.environ.get("PORT_AGENT_SEARCH_ENGINES", "0") != "1":
        return []   # search-engine scraping (Bing/Yandex through a browser) is opt-in (review #27)
    # Bing's /images/search page, fetched by a script, now ignores the query and
    # returns unrelated pictures; the async endpoint with adlt=moderate, as
    # image_search.search_reference_image calls it, returns real results
    try:
        import image_search
        page = image_search.http_get(f"https://www.bing.com/images/async?q={quote_plus(query)}"
                            f"&first=0&count=35&adlt=moderate&mmasync=1", 15).decode("utf-8", "replace")
    except Exception as e:
        logger.info("image search %r failed: %s", query, e)
        return []
    out = []
    for m in re.findall(r'\sm="(\{[^"]+\})"', page):
        try:
            d = json.loads(_html.unescape(m))
        except ValueError:
            continue
        if d.get("murl"):
            out.append(ImageCandidate(d["murl"], "image_search", d.get("purl", ""), d.get("t", "")))
        if len(out) >= limit:
            break
    return out


def _official_host(c: ImageCandidate, vendor: str) -> bool:
    """Image or page on the vendor's own domain (fs.com, tp-link.com, dlink.com ...);
    matched on the domain label, so two-letter makes like FS count too."""
    canon = _canonical_vendor(vendor)
    rec = vendor_registry.by_canonical().get(canon, {})
    labels = {_key(vendor), _key(canon)}
    site = (rec.get("website") or "").lower().replace("https://", "").replace("http://", "").replace("www.", "")
    if site:
        labels.add(_key(site.split(".")[0]))
    for u in (c.url, c.page):
        host = urlparse(u).netloc.lower().split(":")[0]
        parts = host.split(".")
        if len(parts) >= 2 and (_key(parts[-2]) in labels or (len(parts) >= 3 and _key(parts[-3]) in labels
                                                             and len(parts[-2]) <= 3)):
            return True
    return False


def _score(c: ImageCandidate, vendor_toks: list[str], model: str) -> int:
    u = c.url.lower()
    host = urlparse(c.url).netloc.lower()
    page_host = urlparse(c.page).netloc.lower()
    s = 10 if c.source == "official_page" else 0
    if any(t in _key(host) or t in _key(page_host) for t in vendor_toks):
        s += 30
    mk = _key(model)
    if mk and mk in _key(u):
        s += 20
    elif mk and mk in _key(c.title):
        s += 10
    if "front" in u or "front" in c.title.lower():
        s += 10
    if any(h in host for h in LOW_TRUST_HOSTS):
        s -= 15
    # a URL naming a different model of the same family (e.g. ...-1024d) is a wrong product
    for other in re.findall(r"[a-z]{2,4}-?\d{3,5}[a-z]{0,3}", u):
        if mk and _key(other) not in mk and mk not in _key(other) and _key(other)[:3] == mk[:3]:
            s -= 10
            break
    return s


def product_images(vendor: str, model: str, known_pages: Iterable[str] = (),
                   use_search: bool = True, limit: int = 25) -> list[ImageCandidate]:
    """Ranked image candidates for one switch model, best first."""
    vt = _vendor_tokens(vendor)
    cands: dict[str, ImageCandidate] = {}

    for page_url in official_pages(vendor, model, known_pages):
        html = _get(page_url)
        if not html:
            continue
        for url in find_product_images(html, base_url=page_url, model=model)[:40]:
            cands.setdefault(url, ImageCandidate(url, "official_page", page_url))

    if use_search:
        mk = _key(model)
        # plain "vendor model" works best; longer phrasings (e.g. "front panel")
        # can make the engine drop the model and return unrelated pictures, so
        # try several orderings until enough results actually name the model
        queries = (f"{vendor} {model} switch front panel", f"{vendor} {model}", f"{model} switch")
        n_search = 0
        for q in queries:
            for c in image_search(q):
                # keep results that name the model in the image URL, page or title, or
                # come from the vendor's own site (resource.fs.com titles its S3900
                # shots "24 Port L2+ Switch ..."); the port check rejects wrong ones
                if mk in _key(c.url) or mk in _key(c.page) or mk in _key(c.title) or _official_host(c, vendor):
                    if c.url not in cands:
                        cands[c.url] = c
                        n_search += 1
            if n_search >= 8 or len(cands) >= limit:
                break

    for c in cands.values():
        c.score = _score(c, vt, model) + (20 if _official_host(c, vendor) else 0)
    return sorted(_dedupe_sizes(cands.values()), key=lambda c: -c.score)[:limit]


def _dedupe_sizes(cands: Iterable[ImageCandidate]) -> list[ImageCandidate]:
    """CDNs list one file at many sizes (?width=400 / ?width=1500): keep the largest."""
    def width(u: str) -> int:
        m = re.search(r"[?&](?:width|w|wid)=(\d+)", u)
        return int(m.group(1)) if m else 10**6   # no size parameter = original file
    best: dict[str, ImageCandidate] = {}
    for c in cands:
        k = re.sub(r"^https?://", "", c.url.split("?")[0].lower())
        if k not in best or width(c.url) > width(best[k].url):
            best[k] = c
    return list(best.values())


def agent_product_images(vendor, model, use_search=True, limit=12):
    """Ranked image candidates for one model, with the spec record's datasheet page
    added as a known official page (what SpecAgent.product_images does in his patch)."""
    known = []
    spec = _rt.specs(vendor, model)
    if spec and spec.get("found") and spec.get("datasheet_url"):
        known.append(spec["datasheet_url"])
    try:
        return product_images(vendor, model, known_pages=known, use_search=use_search, limit=limit)
    except Exception as e:   # a vendor site change must not break the pipeline
        logger.warning("agent image lookup failed for %s %s: %s", vendor, model, e)
        return []
