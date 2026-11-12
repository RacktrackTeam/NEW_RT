"""
Where template images come from: candidate image URLs for any vendor + model.

No per-vendor code and no logins. Two tiers, in order:

  OFFICIAL - the vendor's own websites
    The vendor's domains are found from its name: domains built from the name
    that answer and mention the vendor, plus search results on a domain that
    carries the name. Any host whose domain carries the vendor's name counts
    as official, so regional and country sites (vendor.com.sg, vendor.de,
    vendorcn.com) are included. Cached on disk.
      1. Sitemaps (robots.txt -> sitemap XML): product pages whose URL names
         the model. Cached on disk for a week.
      2. Official pages a web search finds for the model.
      3. The official web shops' own product search on every official host
         seen (standard Shopify endpoint; hosts without one are skipped).

  INTERNET - used when the official sites give no image naming the model
  (site blocks robots, page gone for an end-of-life model, unknown vendor)
      4. Image search for "vendor model" on any website. A search API is used
         when its key is set (BRAVE_SEARCH_API_KEY, or GOOGLE_CSE_KEY +
         GOOGLE_CSE_CX). Bing and Yandex are searched through a real browser
         (installed Edge or Chrome, headless, via Playwright), which gets the same
         results a person does; their results are merged. Plain HTTP is the last
         resort (search engines throttle it).
      5. Web search for pages naming the model, then the images on them.
    Every internet hit must name the model in its image URL, page URL or
    title.

Candidates are ranked by where they came from. Picking the actual front-panel
view is done visually in template_agent.selector.
"""
from __future__ import annotations

import base64
import html as _html
import gzip
import os
import hashlib
import json
import logging
import atexit
import re
import threading
import time
from pathlib import Path
from dataclasses import asdict, dataclass
from functools import lru_cache
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

import requests

from .devices import device as _device

logger = logging.getLogger("template_agent.sources")

def _cache_dir():
    """Read at call time: a run sets PORT_AGENT_CACHE_DIR for its own duration (map_* in the pipelines)."""
    return (Path(os.environ["PORT_AGENT_CACHE_DIR"]) / "template_agent" if os.environ.get("PORT_AGENT_CACHE_DIR")
            else Path(__file__).parent / "cache")


def _load_key_file():
    """Search API keys from search_keys.env (project root or this package's parent), KEY=VALUE per
    line, so every run and both web UIs pick them up without setting environment variables."""
    for f in (Path(__file__).resolve().parents[2] / "search_keys.env", Path(__file__).resolve().parents[1] / "search_keys.env"):
        if f.exists():
            for line in f.read_text().splitlines():
                k, _, v = line.partition("=")
                if k.strip() and v.strip() and not line.lstrip().startswith("#"):
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_key_file()
BRAVE_CACHE_TTL = 30 * 24 * 3600     # Brave answers kept a month: a repeated query never uses the quota


def _brave(endpoint: str, params: dict) -> dict | None:
    """One Brave Search API call (web or images), answered from the disk cache when it can be."""
    key = os.environ.get("BRAVE_SEARCH_API_KEY")
    if not key:
        return None
    import hashlib
    import json as _json
    d = _cache_dir() / "brave"
    d.mkdir(parents=True, exist_ok=True)
    f = d / (hashlib.sha1((endpoint + _json.dumps(params, sort_keys=True)).encode()).hexdigest()[:24] + ".json")
    if f.exists() and time.time() - f.stat().st_mtime < BRAVE_CACHE_TTL:
        try:
            return _json.loads(f.read_text())
        except ValueError:
            pass
    try:
        r = requests.get(f"https://api.search.brave.com/res/v1/{endpoint}/search", params=params,
                         headers={"X-Subscription-Token": key, "Accept": "application/json"}, timeout=15)
    except requests.RequestException as e:
        logger.info("Brave %s search failed: %s", endpoint, e)
        return None
    if r.status_code == 429:
        logger.warning("Brave Search API: rate limit or monthly quota reached")
        return None
    if not r.ok:
        logger.warning("Brave Search API answered %s: %s", r.status_code, r.text[:200])
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    f.write_text(_json.dumps(data))
    time.sleep(1.05)                  # the free plan allows about one request per second
    return data

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}
_IMG_EXT = re.compile(r"\.(?:jpe?g|png|webp)(?:$|[?#])", re.I)
_RAW_IMG = re.compile(r"""(?:https?:)?//[^\s"'<>()\\]+?\.(?:jpe?g|png|webp)(?:\?[^\s"'<>()\\]*)?""", re.I)
LOW_TRUST = ("pinimg", "pinterest", "facebook", "fbcdn", "youtube", "ytimg", "wikipedia",
             "vecteezy", "alamy", "shutterstock", "istockphoto", "freepik", "dreamstime")
_NOT_PRODUCT = ("logo", "icon", "favicon", "sprite", "banner", "badge", "avatar", "flag",
                "social", "facebook", "twitter", "linkedin", "youtube", "placeholder", "loading",
                "spacer", "1x1", "pixel", "qrcode", "qr-code", "award", "manifest", "apple-touch")


@dataclass
class ImageCandidate:
    url: str
    page: str            # page the image was found on (or the search hit's page)
    source: str          # sitemap_page | official_search | official_store | image_search | web_page
    title: str = ""
    score: int = 0
    tier: str = "official"   # official | internet

    def to_dict(self) -> dict:
        return asdict(self)


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _registrable(host: str) -> str:
    parts = host.lower().strip(".").split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "net", "org", "ac") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


# words that often trail a company name but not its domain ("Juniper Networks" -> juniper.net)
_COMPANY_SUFFIX = ("networks", "network", "technologies", "technology", "systems", "corporation",
                   "corp", "inc", "group", "electronics", "communications", "international")


def _brand_keys(vendor: str) -> set[str]:
    """Normalized names a vendor's own domains are expected to carry."""
    keys = {_key(vendor)}
    for name in list(keys):
        stripped = name
        for suf in _COMPANY_SUFFIX:
            if stripped.endswith(suf) and len(stripped) - len(suf) >= 3:
                stripped = stripped[: -len(suf)]
        keys.add(stripped)
    words = [_key(w) for w in re.split(r"[\s/,]+", vendor) if _key(w)]
    if words and len(words[0]) >= 4:
        keys.add(words[0])
    return {k for k in keys if len(k) >= 2}


def _discover_domains(vendor: str) -> tuple[str, ...]:
    """The vendor's domains, found from its name: domains built from the name
    that answer and mention the vendor, plus search results on such domains."""
    cache = {}
    try:
        cache = json.loads((_cache_dir() / "vendor_domains.json").read_text())
    except (OSError, ValueError):
        pass
    ck = _key(vendor)
    if ck in cache:
        return tuple(cache[ck])

    brands = _brand_keys(vendor)
    hyphen = re.sub(r"[^a-z0-9]+", "-", vendor.lower()).strip("-")
    names = sorted(brands | {hyphen}, key=len)
    found: set[str] = set()
    probe_names = list(dict.fromkeys(names + [b + suf for b in sorted(brands, key=len)
                                               for suf in ("tech", "networks", "global") if len(b) >= 4]))
    for name in probe_names:
        for tld in ("com", "net"):
            for url in (f"https://www.{name}.{tld}/", f"https://{name}.{tld}/"):
                try:
                    r = requests.get(url, headers=HEADERS, timeout=8, allow_redirects=True)
                except requests.RequestException:
                    continue
                if r.status_code != 200:
                    # many vendor sites refuse plain HTTP clients (bot protection): ask the browser
                    page = _browser_get(url, settle_ms=1500) if r.status_code in (401, 403, 429, 503) else None
                    if page and any(b in _key(page[:300_000]) for b in brands):
                        found.add(_registrable(urlparse(url).netloc))
                        break
                    continue
                # keep the domain the site actually lives on (d-link.com -> dlink.com),
                # and only if it carries the vendor's name and its page mentions the vendor
                final = _registrable(urlparse(r.url).netloc)
                if (_is_brand_label(_key(final.split(".")[0]), brands)
                        and any(b in _key(r.text[:300_000]) for b in brands)):
                    found.add(final)
                break
    for q in (f"{vendor} official website", f"{vendor} network switches"):
        for url, title in web_search(q):
            reg = _registrable(urlparse(url).netloc)
            label = _key(reg.split(".")[0])
            # the vendor's name in the domain, or a short domain that is the vendor's first
            # word ("TE Connectivity" -> te.com) when the result's title names the vendor
            first = _key(re.split(r"[\s,]+", vendor.strip())[0])
            if _is_brand_label(label, brands) or (
                    label == first and _key(vendor) in _key(title)):
                found.add(reg)
        if found:
            break
    doms = tuple(sorted(found))
    cache[ck] = list(doms)
    try:
        (_cache_dir() / "vendor_domains.json").parent.mkdir(parents=True, exist_ok=True)
        (_cache_dir() / "vendor_domains.json").write_text(json.dumps(cache, indent=1))
    except OSError:
        pass
    return doms


# domain = brand + one of these (engeniustech.com, dlinkglobal.com, engeniuscdn.com)
_BRAND_DOMAIN_SUFFIXES = ("global", "intl", "usa", "networks", "network", "tech", "technology",
                          "cdn", "static", "media", "assets", "img", "images")


def _is_brand_label(label: str, brands: set[str]) -> bool:
    """A domain label that is the vendor's name, optionally plus a country code
    or a common company word (tendacn, dlinkglobal, junipernetworks)."""
    for b in brands:
        if label == b:
            return True
        if len(b) >= 4 and label.startswith(b):
            rest = label[len(b):]
            if len(rest) == 2 or rest in _BRAND_DOMAIN_SUFFIXES:
                return True
    return False


@lru_cache(maxsize=None)
def official_domains(vendor: str) -> tuple[str, ...]:
    """The vendor's own domains, discovered from its name (cached)."""
    return _discover_domains(vendor)


def is_official(url: str, vendor: str) -> bool:
    """On one of the vendor's domains, or any site whose domain carries the
    vendor's name (regional sites such as vendor.com.sg or vendor.de)."""
    host = urlparse(url if "//" in url else "https://" + url).netloc.lower().split(":")[0]
    if not host:
        return False
    reg = _registrable(host)
    doms = official_domains(vendor)
    label = _key(reg.split(".")[0])
    return (reg in doms or label in {_key(d.split(".")[0]) for d in doms}
            or _is_brand_label(label, _brand_keys(vendor)))


def _get(url: str, timeout: int = 15) -> str | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code == 200 and "html" in r.headers.get("content-type", "text/html"):
            return r.text
    except requests.RequestException as e:
        logger.info("GET %s failed: %s", url, e)
    return None


def page_images(page_html: str, base_url: str) -> list[str]:
    """Every product-looking image URL on a page (img src / data-src / srcset,
    og:image, image links, and URLs only present in inline scripts)."""
    try:
        import device_spec
        device_spec.note_page(base_url, page_html)      # the spec step reads it too, with no search
    except ImportError:
        pass
    found: list[str] = []
    attr_urls = re.findall(r"""(?:src|data-src|data-original|data-zoom-image|data-large|href|content)\s*=\s*["']([^"']+)["']""",
                           page_html, re.I)
    for srcset in re.findall(r"""srcset\s*=\s*["']([^"']+)["']""", page_html, re.I):
        attr_urls.append(srcset.split(",")[-1].strip().split(" ")[0])  # largest variant
    for raw in attr_urls + _RAW_IMG.findall(page_html.replace("\\/", "/")):
        u = _html.unescape(raw.strip())
        if u.startswith("//"):
            u = "https:" + u
        u = urljoin(base_url, u)
        if not _IMG_EXT.search(u) or any(k in u.lower() for k in _NOT_PRODUCT):
            continue
        if u not in found:
            found.append(u)
    return found


SITEMAP_TTL = 7 * 24 * 3600
MAX_SITEMAPS_PER_DOMAIN = 60


def _get_xml(url: str) -> str | None:
    """Sitemap XML (plain or .gz), cached on disk."""
    (_cache_dir() / "sitemaps").mkdir(parents=True, exist_ok=True)
    f = (_cache_dir() / "sitemaps") / (hashlib.sha1(url.encode()).hexdigest() + ".xml")
    if f.exists() and time.time() - f.stat().st_mtime < SITEMAP_TTL:
        return f.read_text(encoding="utf-8", errors="replace")
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
    except requests.RequestException as e:
        logger.info("sitemap %s failed: %s", url, e)
        return None
    if r.status_code != 200:
        return None
    data = r.content
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except OSError:
            return None
    text = data.decode("utf-8", errors="replace")
    if "<loc>" not in text:
        return None
    f.write_text(text, encoding="utf-8")
    return text


def _sitemap_roots(domain: str) -> list[str]:
    roots = []
    for host in (f"www.{domain}", domain):
        txt = _get(f"https://{host}/robots.txt") or ""
        roots += re.findall(r"(?im)^\s*sitemap:\s*(\S+)", txt)
        if roots:
            break
    return roots or [f"https://www.{domain}/sitemap.xml", f"https://www.{domain}/sitemap_index.xml"]


def _locale_rank(url: str) -> int:
    """Prefer English / global pages over other locales."""
    path = urlparse(url).path.lower()
    if re.search(r"/(en|us|en-us|en-gb|uk|global|int)(/|$)", path):
        return 0
    if not re.match(r"^/[a-z]{2}(-[a-z]{2})?/", path):
        return 1
    return 2


def _sitemap_pages(vendor: str, model: str, max_pages: int = 4,
                   hint: str = r"product|support|switch") -> list[str]:
    """Official product pages whose URL names the model, found via sitemaps."""
    mk = _key(model)
    hits: list[str] = []
    for domain in official_domains(vendor):
        queue = _sitemap_roots(domain)
        seen: set[str] = set()
        while queue and len(seen) < MAX_SITEMAPS_PER_DOMAIN:
            sm = queue.pop(0)
            if sm in seen:
                continue
            seen.add(sm)
            xml = _get_xml(sm)
            if not xml:
                continue
            for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml):
                loc = _html.unescape(loc)
                if re.search(r"\.xml(\.gz)?($|\?)", loc, re.I):
                    # product sitemaps first; skip blog/news/media sitemaps
                    if re.search(r"(blog|news|press|video|image|media|event|career|article)", loc, re.I):
                        continue
                    (queue.insert(0, loc) if re.search(hint, loc, re.I)
                     else queue.append(loc))
                elif mk and names_model(urlparse(loc).path, model) and is_official(loc, vendor):
                    hits.append(loc)
    # exact slug before longer ones (GS724T before GS724TP), product pages before
    # support/download pages (those rarely show the product), English first
    def rank(u):
        path = urlparse(u).path.lower()
        slug = _key(path.rstrip("/").rsplit("/", 1)[-1])
        support = bool(re.search(r"/(support|download|downloads|faq|document|docs)/", path))
        return (0 if slug == mk else 1, support, _locale_rank(u), len(u))
    return sorted(dict.fromkeys(hits), key=rank)[:max_pages]


def query_variants(vendor: str, model: str, extra: str = "", noun: str = "switch") -> list[str]:
    """Phrasings of "vendor model" to try in turn. Search engines can read a hyphen
    as an operator and drop the rest of a word ("D-Link" -> "D", "DES-1210-28P" ->
    "DES"), and which phrasing they understand varies, so several are tried; results
    are always filtered on the model name anyway."""
    # a long company name dilutes the query; its first word is usually the brand
    # ("Alcatel-Lucent Enterprise" -> "Alcatel", "Schweitzer Engineering Laboratories" -> "Schweitzer")
    short_vendor = re.split(r"[\s,]+", vendor.strip())[0]
    short_vendor = re.split(r"-", short_vendor)[0] if len(short_vendor) > 10 else short_vendor
    joined_vendor = re.sub(r"[^A-Za-z0-9]", "", short_vendor)    # D-Link -> DLink
    spaced_model = re.sub(r"[^A-Za-z0-9]+", " ", model).strip()  # DES-1210-28P -> DES 1210 28P
    tail = f" {extra}" if extra else ""
    out = [f"{vendor} {model}{tail}", f"{short_vendor} {model}{tail}", f"{joined_vendor} {model}{tail}",
           f"{joined_vendor} {spaced_model}{tail}", f'"{model}" {joined_vendor}{tail}',
           f'"{model}" {noun}{tail}']                                  # the model alone, as a product
    return list(dict.fromkeys(q.strip() for q in out))


def _official_search_pages(vendor: str, model: str, max_pages: int = 4, noun: str = "switch") -> list[str]:
    """Official pages (any of the vendor's hosts, incl. regional) a web search finds for the model."""
    pages = []
    for q in query_variants(vendor, model, noun=noun):
        for url, title in web_search(q):
            if is_official(url, vendor) and (names_model(urlparse(url).path, model) or names_model(title, model)):
                pages.append(url)
        if pages:
            break
    return list(dict.fromkeys(pages))[:max_pages]


def _store_pages(host: str, model: str) -> list[str]:
    """Product pages from a web shop's own search (standard Shopify endpoint;
    a host without it just returns nothing)."""
    try:
        r = requests.get(f"https://{host}/search/suggest.json", headers=HEADERS, timeout=10,
                         params={"q": model, "resources[type]": "product", "resources[limit]": "5"})
        prods = r.json().get("resources", {}).get("results", {}).get("products", []) if r.ok else []
    except (requests.RequestException, ValueError):
        return []
    return [f"https://{host}{p['url'].split('?')[0]}" for p in prods
            if p.get("url") and (names_model(p["url"], model) or names_model(p.get("title", ""), model))]


# ------------------------------------------------------------------ internet search
# ------------------------------------------------------------------ real browser
# Search engines answer a plain HTTP client as a bot (after a few requests they
# drop the model number and return unrelated results). A real browser - the
# installed Edge or Chrome, run headless - gets the same results a person gets.
_BROWSER_CHANNELS = ("msedge", "chrome", None)   # None = Playwright's own Chromium, if installed


class _BrowserWorker:
    """One headless browser owned by a dedicated thread.

    Playwright's sync API cannot run inside an asyncio event loop (web apps such as
    Streamlit or FastAPI run their code in one) and its objects are bound to the
    thread that created them. A private thread with no event loop sidesteps both:
    callers from any thread hand it a URL and wait for the page HTML.

    Long sessions are expected (a server, a batch over many vendors), so the worker
    heals itself: a failed page load restarts the browser, and loads to the same
    search engine are spaced out so the engine does not start throttling."""

    MIN_GAP_S = 1.5          # between two loads of the same host
    PAGE_TIMEOUT_MS = 30_000

    def __init__(self):
        import queue
        self.jobs: "queue.Queue" = queue.Queue()
        self.ready = threading.Event()
        self.ok = False
        self._last_hit: dict[str, float] = {}
        threading.Thread(target=self._run, name="template-agent-browser", daemon=True).start()
        self.ready.wait(60)

    def _launch(self, pw):
        for channel in _BROWSER_CHANNELS:
            try:
                browser = (pw.chromium.launch(channel=channel, headless=True) if channel
                           else pw.chromium.launch(headless=True))
                logger.info("searching through a real browser (%s)", channel or "chromium")
                return browser, browser.new_page(locale="en-US")
            except Exception:
                continue
        return None, None

    def _run(self):
        try:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
        except Exception as e:
            logger.warning("browser search unavailable (%s); using plain HTTP", e)
            self.ready.set()
            return
        browser, page = self._launch(pw)
        if browser is None:
            logger.warning("no Edge/Chrome/Chromium found; using plain HTTP for search")
            pw.stop()
            self.ready.set()
            return
        self.ok = True
        self.ready.set()
        while True:
            url, settle_ms, box, done = self.jobs.get()
            if url is None:
                break
            host = urlparse(url).netloc
            wait = self.MIN_GAP_S - (time.time() - self._last_hit.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            if settle_ms < 0:
                # raw bytes (an image) through the browser's own session: sites that refuse
                # plain HTTP clients (cisco.com answers 403) serve the browser
                try:
                    resp = page.context.request.get(url, timeout=self.PAGE_TIMEOUT_MS,
                                                    headers={"Referer": f"https://{host}/"})
                    box.append(resp.body() if resp.ok else None)
                except Exception as e:
                    logger.info("browser fetch failed (%s): %s", host, str(e).splitlines()[0][:120])
                self._last_hit[host] = time.time()
                done.set()
                continue
            for attempt in (1, 2):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=self.PAGE_TIMEOUT_MS)
                    page.wait_for_timeout(settle_ms)
                    box.append(self._content(page))
                    break
                except Exception as e:
                    logger.warning("browser load failed (%s): %s - restarting the browser",
                                   host, str(e).splitlines()[0][:120])
                    try:
                        browser.close()
                    except Exception:
                        pass
                    browser, page = self._launch(pw)
                    if browser is None:
                        self.ok = False
                        break
            self._last_hit[host] = time.time()
            done.set()
            if not self.ok:
                logger.warning("browser could not be restarted; using plain HTTP from now on")
                break
        try:
            browser.close()
        except Exception:
            pass
        pw.stop()

    @staticmethod
    def _content(page) -> str:
        """Page HTML. Search engines sometimes keep navigating after the first load
        (redirects, client-side routing); wait for it to settle and read again."""
        for _ in range(4):
            try:
                return page.content()
            except Exception as e:
                if "navigating" not in str(e):
                    raise
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10_000)
                except Exception:
                    pass
                page.wait_for_timeout(1000)
        return page.content()

    def get(self, url: str, settle_ms: int) -> str | None:
        if not self.ok:
            return None
        box: list = []
        done = threading.Event()
        self.jobs.put((url, settle_ms, box, done))
        if not done.wait(120):
            logger.warning("browser did not answer within 120 s for %s", url)
        return box[0] if box else None

    def close(self):
        if self.ok:
            self.jobs.put((None, 0, None, None))


_worker: _BrowserWorker | None = None
_worker_lock = threading.Lock()


def _browser_bytes(url: str) -> bytes | None:
    """A file (an image) fetched through the real browser's session; None if no browser or refused."""
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = _BrowserWorker()
            atexit.register(_worker.close)
    return _worker.get(url, -1)


def _browser_get(url: str, settle_ms: int = 1500) -> str | None:
    """Page HTML fetched by a real browser; None if no browser is available."""
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = _BrowserWorker()
            atexit.register(_worker.close)
    return _worker.get(url, settle_ms)


def _bing_images(query: str, limit: int = 35) -> list[ImageCandidate]:
    if os.environ.get("PORT_AGENT_SEARCH_ENGINES", "0") != "1":
        return []   # search-engine scraping (Bing/Yandex through a browser) is opt-in (review #27)
    url = f"https://www.bing.com/images/search?q={quote_plus(query)}&form=HDRSC2"
    page = _browser_get(url) or _get(url)
    out = []
    for m in re.findall(r'\sm="(\{[^"]+\})"', page or ""):
        try:
            d = json.loads(_html.unescape(m))
        except ValueError:
            continue
        if d.get("murl"):
            out.append(ImageCandidate(d["murl"], d.get("purl", ""), "image_search", d.get("t", ""),
                                    tier="internet"))
        if len(out) >= limit:
            break
    return out


def _brave_images(query: str) -> list[ImageCandidate]:
    data = _brave("images", {"q": query, "count": 30, "safesearch": "strict"})
    rows = (data or {}).get("results", [])
    return [ImageCandidate((x.get("properties") or {}).get("url") or x.get("url", ""), x.get("url", ""),
                         "image_search", x.get("title", ""), tier="internet") for x in rows]


GOOGLE_DAILY_LIMIT = int(os.environ.get("GOOGLE_CSE_DAILY_LIMIT", "100"))   # the free allowance


def _google(params: dict) -> dict | None:
    """One Google Programmable Search (Custom Search JSON API) call, answered from the disk cache
    when it can be; stops at the daily free allowance (100) and says so."""
    key, cx = os.environ.get("GOOGLE_CSE_KEY"), os.environ.get("GOOGLE_CSE_CX")
    if not (key and cx):
        return None
    import hashlib
    import json as _json
    d = _cache_dir() / "google"
    d.mkdir(parents=True, exist_ok=True)
    f = d / (hashlib.sha1(_json.dumps(params, sort_keys=True).encode()).hexdigest()[:24] + ".json")
    if f.exists() and time.time() - f.stat().st_mtime < BRAVE_CACHE_TTL:
        try:
            return _json.loads(f.read_text())
        except ValueError:
            pass
    counter = d / f"count-{time.strftime('%Y-%m-%d')}.txt"
    used = int(counter.read_text() or 0) if counter.exists() else 0
    if used >= GOOGLE_DAILY_LIMIT:
        logger.warning("Google search: today's %d free searches used; vendor sitemaps and saved templates only", GOOGLE_DAILY_LIMIT)
        return None
    try:
        r = requests.get("https://www.googleapis.com/customsearch/v1", params={"key": key, "cx": cx, **params}, timeout=15)
    except requests.RequestException as e:
        logger.info("Google search failed: %s", e)
        return None
    counter.write_text(str(used + 1))
    if not r.ok:
        logger.warning("Google Custom Search answered %s: %s", r.status_code, r.text[:200])
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    f.write_text(_json.dumps(data))
    return data


def _google_images(query: str) -> list[ImageCandidate]:
    data = _google({"q": query, "searchType": "image", "num": 10})
    rows = (data or {}).get("items", [])
    return [ImageCandidate(x.get("link", ""), (x.get("image") or {}).get("contextLink", ""),
                         "image_search", x.get("title", ""), tier="internet") for x in rows]


_PAGE_IMG = re.compile(r"""https?://[^"'\s<>\\]+?\.(?:jpe?g|png|webp)(?:\?[^"'\s<>\\]*)?""", re.I)


_YANDEX_ITEM = re.compile(
    r'"alt":"(?P<alt>[^"]*)"[^{}]{0,400}?"origUrl":"(?P<img>[^"]+)","snippet":\{"title":"(?P<title>[^"]*)",'
    r'"domain":"[^"]*","url":"(?P<page>[^"]+)"')


def _yandex_images(query: str, limit: int = 60) -> list[ImageCandidate]:
    """Yandex image results, read through the real browser (its index differs from
    Bing's, so it often has what Bing misses). Each result carries the original image
    URL, its title and the page it is on; titles and pages often name the model when
    the image file name does not."""
    if os.environ.get("PORT_AGENT_SEARCH_ENGINES", "0") != "1":
        return []   # search-engine scraping (Bing/Yandex through a browser) is opt-in (review #27)
    page = _browser_get(f"https://yandex.com/images/search?text={quote_plus(query)}", settle_ms=2500)
    if not page:
        return []
    text = _html.unescape(page).replace("\\u002F", "/").replace("\\/", "/")
    out, seen = [], set()
    for m in _YANDEX_ITEM.finditer(text):
        img = m["img"]
        if img in seen or "yandex" in urlparse(img).netloc.lower():
            continue
        seen.add(img)
        title = m["title"] if len(m["title"]) >= len(m["alt"]) else m["alt"]
        out.append(ImageCandidate(img, m["page"], "image_search", title, tier="internet"))
        if len(out) >= limit:
            return out
    # page layout changed: fall back to bare image URLs
    for u in _PAGE_IMG.findall(text):
        host = urlparse(u).netloc.lower()
        if "yandex" in host or "yastatic" in host or u in seen:
            continue
        seen.add(u)
        out.append(ImageCandidate(u, "", "image_search", "", tier="internet"))
        if len(out) >= limit:
            break
    return out


@lru_cache(maxsize=512)
def _image_search_cached(query: str) -> tuple:
    api = _brave_images(query) + _google_images(query)
    if os.environ.get("BRAVE_SEARCH_API_KEY") or os.environ.get("GOOGLE_CSE_KEY"):
        return tuple(api)             # with an API key the browser engines (captchas, junk answers) are skipped
    return tuple(api + _bing_images(query) + _yandex_images(query))


def image_search(query: str) -> list[ImageCandidate]:
    """Image hits from every available engine: API engines when keys are set, then
    Bing and Yandex through the browser. Engines are merged because each has bad
    moments (a misread query, a throttled answer) and different coverage."""
    from copy import copy
    return [copy(c) for c in _image_search_cached(query)]   # copies: callers set scores


def _bing_result_url(href: str) -> str | None:
    """Bing wraps result links (bing.com/ck/a?...&u=a1<base64url>)."""
    href = _html.unescape(href)
    if "bing.com/ck/a" not in href:
        return href
    v = parse_qs(urlparse(href).query).get("u", [""])[0]
    v = v[2:] if v.startswith("a1") else v
    try:
        return base64.urlsafe_b64decode(v + "=" * (-len(v) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return None


@lru_cache(maxsize=512)
def web_search(query: str) -> list[tuple[str, str]]:
    """(url, title) of web results: the Brave Search API when its key is set, else Bing through
    the browser (often answered with captchas or unrelated pages for automated use)."""
    data = _brave("web", {"q": query, "count": 20})
    if data is not None:
        return [(x.get("url", ""), re.sub(r"<[^>]+>", "", _html.unescape(x.get("title", ""))))
                for x in (data.get("web") or {}).get("results", []) if x.get("url", "").startswith("http")]
    data = _google({"q": query, "num": 10})
    if data is not None:
        return [(x.get("link", ""), x.get("title", "")) for x in data.get("items", []) if x.get("link", "").startswith("http")]
    if os.environ.get("PORT_AGENT_SEARCH_ENGINES", "0") != "1":
        return []   # Bing web results through a browser are opt-in (review #27)
    url = f"https://www.bing.com/search?q={quote_plus(query)}&setlang=en"
    page = _browser_get(url) or _get(url) or ""
    out = []
    for href, title in re.findall(r'<li class="b_algo".*?<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                                  page, re.S):
        u = _bing_result_url(href)
        if u and u.startswith("http"):
            out.append((u, re.sub(r"<[^>]+>", "", _html.unescape(title))))
    return out


# ------------------------------------------------------------------ ranking
def _model_pattern(model: str) -> re.Pattern:
    """The model name with any (or no) separators between its characters."""
    chars = [re.escape(c) for c in model.lower() if c.isalnum()]
    return re.compile("(?<![a-z0-9])" + r"[\W_]?".join(chars), re.I)


def model_mention(text: str, model: str) -> str:
    """exact | variant | none - 'TL-SG1016_UN.jpg' is exact for TL-SG1016,
    'TL-SG1016D-01.jpg' and 'TL-SG1016PE' are other models (variants)."""
    found = "none"
    for m in _model_pattern(model).finditer(text or ""):
        nxt = text[m.end():m.end() + 1]
        if not nxt or not nxt.isalnum():
            return "exact"
        found = "variant"
    return found


def names_model(text: str, model: str) -> bool:
    """`text` names the model as a whole name (separators may differ): 'TL-SG1016_UN.jpg'
    and the longer 'TL-SG1016D' do; 'CRS328' does NOT name 'RS328' (a different name)."""
    from urllib.parse import unquote
    return bool(model) and model_mention(unquote(text or "").replace("+", " "), model) != "none"


def on_model_page(img: ImageCandidate, model: str) -> bool:
    """Found on an official page whose address names the model (the vendor's own
    product page): its images are that model's images, whatever their file names
    (many vendors number their files, e.g. 1493_hi_res.png)."""
    return img.tier == "official" and names_model(urlparse(img.page).path, model)


def _score(img: ImageCandidate, vendor: str, model: str) -> int:
    mk = _key(model)
    s = {"sitemap_page": 15, "official_search": 15, "official_store": 15,
         "image_search": 0, "web_page": 0}[img.source]
    named = bool(mk) and (names_model(urlparse(img.url).path, model) or on_model_page(img, model))
    # an official host alone is weak evidence: menus and banners are official too
    if is_official(img.url, vendor):
        s += 40 if named else 5
    elif is_official(img.page, vendor):
        s += 20 if named else 5
    if named:
        s += 20
    elif mk and (names_model(img.title, model) or names_model(urlparse(img.page).path, model)):
        s += 8
    u = img.url.lower()
    # the exact model beats a longer model name that merely contains it
    mention = model_mention(urlparse(img.url).path, model)
    if mention == "none":
        mention = model_mention(img.title, model)
    if mention == "exact":
        s += 15
    elif mention == "variant":
        s -= 25
    if "front" in u:
        s += 10
    if re.search(r"(large|big|xl|original|1000|1200|1500|2000)", u):
        s += 3
    if re.search(r"(thumb|small|mini|_s\.|-s\.|50x|100x)", u):
        s -= 6
    if any(h in urlparse(img.url).netloc.lower() for h in LOW_TRUST):
        s -= 15
    return s


def _dedupe_sizes(imgs: list[ImageCandidate]) -> list[ImageCandidate]:
    """CDNs list one file at many sizes (?width=400 / ?width=1500): keep the largest."""
    def width(u: str) -> int:
        m = re.search(r"[?&](?:width|w|wid)=(\d+)", u)
        return int(m.group(1)) if m else 10**6
    best: dict[str, ImageCandidate] = {}
    for i in imgs:
        k = re.sub(r"^https?://", "", i.url.split("?")[0].lower())
        if k not in best or width(i.url) > width(best[k].url) or i.score > best[k].score:
            best[k] = i
    return list(best.values())


# ------------------------------------------------------------------ "did you mean"
_MODEL_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-_ ]?[A-Za-z0-9+]+){1,5}")


def suggest_models(vendor: str, model: str, limit: int = 3, same_numbers: bool = True,
                   noun: str = "switch") -> list[str]:
    """Real model names close to `model`, read from search results (image titles,
    file names, pages) - for a model that nothing online names: a typo, an OCR
    misread, or a variant that does not exist. Only names of the same family
    (same leading letters + digits) and close in spelling are suggested."""
    from difflib import SequenceMatcher
    mk = _key(model)
    family = re.match(r"[a-z]+\d{2,}", mk)
    if not family:
        return []
    counts: dict[str, int] = {}
    shown: dict[str, str] = {}
    for q in query_variants(vendor, model, noun=noun)[:2]:
        for hit in image_search(q):
            text = " ".join([hit.title or "", urlparse(hit.url).path, urlparse(hit.page).path])
            for tok in _MODEL_TOKEN.findall(text.replace("%2B", "+")):
                # "4S+RM" appears in addresses as "4S-RM" or "4S-PLUS-RM": one model
                tok = re.sub(r"(?i)[-_ ]plus(?=[-_ ]|$)", "+", tok)
                tok = re.sub(r"[-_ ]*\+[-_ ]*", "+", tok)          # "2S-+-RM" -> "2S+RM"
                tok = re.sub(r"(?<=[A-Za-z])[-_]\d+$", "", tok)    # file suffix "RM-2" -> "RM"
                k = _key(tok)
                if k == mk or not k.startswith(family.group(0)) or not (0.6 * len(mk) <= len(k) <= 1.4 * len(mk)):
                    continue
                if SequenceMatcher(None, k, mk).ratio() < 0.75:
                    continue
                counts[k] = counts.get(k, 0) + 1
                # keep the most readable spelling seen: one written with "+" (as on the
                # product) over an address-style one, then the one with separators
                spelled = tok.upper().replace("_", "-").replace(" ", "-")
                if k not in shown or (spelled.count("+"), spelled.count("-")) > (shown[k].count("+"), shown[k].count("-")):
                    shown[k] = spelled
    ranked = sorted(counts, key=lambda k: (-counts[k], -SequenceMatcher(None, k, mk).ratio()))
    # same numbers only: different numbers are a different model (2S vs 4S), not a typo
    nums = tuple(re.findall(r"\d+", model))
    return [shown[k] for k in ranked if counts[k] >= 2
            and (not same_numbers or tuple(re.findall(r"\d+", shown[k])) == nums)][:limit]


# ------------------------------------------------------------------ public
def find_template_images(vendor: str, model: str, limit: int = 25, use_search: bool = True,
                         min_official: int = 1, device: str = "switch") -> list[ImageCandidate]:
    """Ranked candidate images for one model, official sites first. Never raises.

    The internet tier runs when the official tier found fewer than
    `min_official` images that name the model (or always, if use_search and
    min_official is large). device: 'switch' or 'router' (template_agent.devices)."""
    try:
        return _get_product_images(vendor, model, limit, use_search, min_official, _device(device))
    except Exception:
        logger.exception("template image lookup failed for %s %s", vendor, model)
        return []


def _official_images(vendor: str, model: str, use_search: bool = True, dev=None) -> dict[str, ImageCandidate]:
    dev = _device(dev)
    imgs: dict[str, ImageCandidate] = {}

    def take(page: str, source: str) -> str | None:
        page_html = _get(page)
        for u in page_images(page_html or "", page):
            if names_model(urlparse(u).path, model) or is_official(u, vendor):
                imgs.setdefault(u, ImageCandidate(u, page, source))
        return page_html

    # 1. official sitemaps
    for page in _sitemap_pages(vendor, model, hint=dev.sitemap_hint):
        take(page, "sitemap_page")
    # 2. official pages a web search finds (also reveals regional sites)
    hosts = {f"www.{d}" for d in official_domains(vendor)}
    if use_search:
        for page in _official_search_pages(vendor, model, noun=dev.noun):
            take(page, "official_search")
            hosts.add(urlparse(page).netloc)
    # 3. web-shop product search on every official host seen
    for host in sorted(hosts):
        for page in _store_pages(host, model)[:2]:
            take(page, "official_store")
    return imgs


def _internet_images(vendor: str, model: str, want: int = 10, noun: str = "switch") -> dict[str, ImageCandidate]:
    mk = _key(model)
    imgs: dict[str, ImageCandidate] = {}
    # plain "vendor model" first: longer phrasings can make engines drop the model number
    # the model family: the name without a trailing letter suffix (ECS1528T -> ECS1528);
    # listings often use it, so it is a fallback when few hits name the exact model
    family = re.sub(r"[a-z]+$", "", mk) if re.search(r"\d[a-z]{1,3}$", mk) else ""
    near: dict[str, ImageCandidate] = {}
    for q in query_variants(vendor, model, noun=noun):
        for hit in image_search(q):
            text = _key(hit.url) + " " + _key(hit.page) + " " + _key(hit.title)
            if hit.url and (names_model(urlparse(hit.url).path, model) or names_model(hit.title, model)
                            or names_model(urlparse(hit.page).path, model)):
                imgs.setdefault(hit.url, hit)
            elif hit.url and family and len(family) >= 4 and family in text:
                near.setdefault(hit.url, hit)
        if len(imgs) >= want:
            return imgs
    if len(imgs) < 3:
        for u, hit in near.items():
            imgs.setdefault(u, hit)
    # pages about the model -> pictures on those pages
    for q in query_variants(vendor, model, "datasheet", noun=noun)[:2]:
        for url, title in web_search(q)[:8]:
            if not (names_model(urlparse(url).path, model) or names_model(title, model)) \
                    or any(h in url for h in LOW_TRUST):
                continue
            for u in page_images(_get(url) or "", url)[:40]:
                if names_model(urlparse(u).path, model):
                    imgs.setdefault(u, ImageCandidate(u, url, "web_page", title, tier="internet"))
            if len(imgs) >= want:
                return imgs
    return imgs


def _get_product_images(vendor, model, limit, use_search, min_official, dev):
    imgs = _official_images(vendor, model, use_search, dev)
    # official images of this model: named for it, or on the vendor's own page for it
    n_named = sum(1 for i in imgs.values() if names_model(urlparse(i.url).path, model) or on_model_page(i, model))
    if use_search and n_named < min_official:
        logger.info("%s %s: %d official image(s) of the model -> searching the internet",
                    vendor, model, n_named)
        for u, i in _internet_images(vendor, model, noun=dev.noun).items():
            imgs.setdefault(u, i)
    elif n_named:
        logger.info("%s %s: %d official image(s) of the model; the official site is enough",
                    vendor, model, n_named)
    for i in imgs.values():
        i.score = _score(i, vendor, model)
    return sorted(_dedupe_sizes(list(imgs.values())), key=lambda i: -i.score)[:limit]
