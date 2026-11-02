"""
Free web search - no API keys, no quotas.

Strategy:
1. DuckDuckGo HTML endpoint (html.duckduckgo.com) - returns search results
   as HTML, parseable with BeautifulSoup. No quota, no key.
2. Direct vendor site search for known vendors.

Returns a list of (title, url, snippet) tuples.
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import quote, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("live_search")

# Headers that DuckDuckGo accepts
DDG_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.5",
}

# Known vendor domains - used to recognize vendor-source results and prioritize
VENDOR_DOMAINS = {
    "cisco": ["cisco.com", "meraki.com"],
    "arista": ["arista.com"],
    "juniper": ["juniper.net"],
    "aruba": ["arubanetworks.com", "hpe.com"],
    "dell": ["dell.com"],
    "nvidia": ["nvidia.com", "mellanox.com"],
    "extreme": ["extremenetworks.com"],
    "huawei": ["huawei.com"],
    "h3c": ["h3c.com"],
    "ubiquiti": ["ui.com", "ubnt.com"],
    "mikrotik": ["mikrotik.com"],
    "tp-link": ["tp-link.com"],
    "netgear": ["netgear.com"],
    "trendnet": ["trendnet.com"],
    "zyxel": ["zyxel.com"],
    "d-link": ["dlink.com"],
    "ruijie": ["ruijienetworks.com"],
    "fortinet": ["fortinet.com"],
    "fs": ["fs.com"],
    "cambium": ["cambiumnetworks.com"],
    "edgecore": ["edge-core.com"],
}

# Auto-extend with every vendor + domain from vendors.json.
try:
    from vendor_registry import by_canonical as _vr_by_name
    for _entry in _vr_by_name().values():
        _site = (_entry.get("website") or "").replace("https://", "").replace("http://", "").replace("www.", "")
        if _site:
            for _a in [_entry.get("name", "")] + (_entry.get("aliases") or []):
                _key = _a.strip().lower()
                if _key and _key not in VENDOR_DOMAINS:
                    VENDOR_DOMAINS[_key] = [_site]
except Exception:  # pragma: no cover
    pass


SEARCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


def _engine_startpage(query: str, timeout: int) -> list[dict]:
    try:
        r = requests.get("https://www.startpage.com/sp/search",
                          params={"query": query}, headers=SEARCH_HEADERS,
                          timeout=timeout)
    except requests.RequestException:
        return []
    if r.status_code != 200:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for a in soup.select("a.result-link, a.w-gl__result-title"):
        href = a.get("href", "")
        if href.startswith("http") and "startpage.com" not in href:
            out.append({"title": a.get_text(strip=True), "url": href,
                        "snippet": ""})
    return out


def _engine_mojeek(query: str, timeout: int) -> list[dict]:
    try:
        r = requests.get("https://www.mojeek.com/search",
                          params={"q": query}, headers=SEARCH_HEADERS,
                          timeout=timeout)
    except requests.RequestException:
        return []
    if r.status_code != 200:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for a in soup.select("a.ob, ul.results-standard li a[href]"):
        href = a.get("href", "")
        if href.startswith("http") and "mojeek.com" not in href:
            out.append({"title": a.get_text(strip=True), "url": href,
                        "snippet": ""})
    return out


def _engine_ddg(query: str, timeout: int) -> list[dict]:
    """Legacy DuckDuckGo HTML — often 202-throttled, tried last."""
    try:
        r = requests.post("https://html.duckduckgo.com/html/",
                          data={"q": query, "kl": "us-en"},
                          headers=DDG_HEADERS, timeout=timeout)
    except requests.RequestException:
        return []
    if r.status_code != 200:
        return []
    return _parse_ddg(r.text, 10)


# Order matters: Startpage gives the best (vendor-PDF) hits, Mojeek is
# the most scraper-tolerant, DDG is a long shot kept as a last resort.
SEARCH_ENGINES = (_engine_startpage, _engine_mojeek, _engine_ddg)


def search_duckduckgo(query: str, max_results: int = 10, timeout: int = 6) -> list[dict]:
    """Free, no-API web search. Tries multiple engines until one yields
    results. (Name kept for backward compatibility.)"""
    for engine in SEARCH_ENGINES:
        try:
            res = engine(query, timeout)
        except Exception as e:  # never let one engine kill the lookup
            logger.warning("%s failed: %s", engine.__name__, e)
            res = []
        if res:
            seen, out = set(), []
            for r in res:
                if r["url"] in seen:
                    continue
                seen.add(r["url"])
                out.append(r)
                if len(out) >= max_results:
                    break
            logger.info("search hit via %s (%d results)",
                        engine.__name__, len(out))
            return out
    logger.info("no search engine returned results for %r", query)
    return []


search_web = search_duckduckgo  # clearer alias


def _parse_ddg(html: str, max_results: int) -> list[dict]:
    """Parse DDG HTML results page."""
    soup = BeautifulSoup(html, "html.parser")
    results = []

    # DDG html format: results live in .result divs
    for result in soup.select(".result, .web-result, .result__body")[: max_results * 2]:
        link = result.select_one("a.result__a, h2 a, .result__url")
        if not link:
            continue
        href = link.get("href", "")
        if href.startswith("//duckduckgo.com/l/?uddg="):
            # DDG redirect wrapper - extract real URL
            m = re.search(r"uddg=([^&]+)", href)
            if m:
                from urllib.parse import unquote
                href = unquote(m.group(1))
        if not href.startswith("http"):
            continue

        title = link.get_text(strip=True)
        snippet_el = result.select_one(".result__snippet, .snippet")
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""

        results.append({"title": title, "url": href, "snippet": snippet})
        if len(results) >= max_results:
            break

    return results


def search_for_switch(
    query: str,
    vendor: Optional[str] = None,
    max_results: int = 10,
    timeout: int = 6,
) -> list[dict]:
    """
    Search for a switch model. Returns results ranked by likelihood
    of being a useful spec/datasheet source.

    Strategy:
    1. Build targeted queries (model + "datasheet", + "specs", + filetype:pdf)
    2. Search via DuckDuckGo
    3. Rank: vendor-domain hits > datasheet/spec keyword hits > others
    """
    queries = _build_queries(query, vendor)
    seen_urls = set()
    all_results = []

    for q in queries:
        for r in search_duckduckgo(q, max_results=max_results, timeout=timeout):
            if r["url"] in seen_urls:
                continue
            seen_urls.add(r["url"])
            all_results.append(r)
        # Stop as soon as one query gives us enough — extra queries
        # mostly cost time (live lookup is on a hard deadline).
        if len(all_results) >= max_results:
            break

    return _rank_results(all_results, query, vendor)[:max_results]


def _build_queries(query: str, vendor: Optional[str]) -> list[str]:
    """Build targeted searches, best-first (we usually only run the first)."""
    if vendor:
        return [f"{vendor} {query} datasheet pdf",
                f"{vendor} {query} specifications"]
    return [f"{query} switch datasheet pdf",
            f"{query} switch specifications"]


def _rank_results(results: list[dict], query: str, vendor: Optional[str]) -> list[dict]:
    """Rank results by likely usefulness."""
    vendor_domains = []
    if vendor:
        vendor_domains = VENDOR_DOMAINS.get(vendor.lower(), [])

    def score(r):
        s = 0
        url = r["url"].lower()
        title = r["title"].lower()
        snippet = r.get("snippet", "").lower()

        # PDF datasheets are usually best
        if url.endswith(".pdf"): s += 10
        if "datasheet" in url or "datasheet" in title: s += 8
        if "data-sheet" in url or "data_sheet" in url: s += 8
        if "spec" in url or "specs" in title: s += 4

        # Vendor domain match
        domain = urlparse(url).netloc.replace("www.", "")
        if any(d in domain for d in vendor_domains): s += 15

        # Penalize obvious junk
        if any(junk in domain for junk in
               ("reddit.com", "youtube.com", "facebook.com", "twitter.com",
                "ebay.com", "amazon.com")):
            s -= 20

        # Bonus if model number appears in title
        for token in query.split():
            if len(token) > 3 and token.lower() in title:
                s += 2

        return s

    return sorted(results, key=score, reverse=True)


def guess_vendor(query: str) -> Optional[str]:
    """Pull a vendor name out of the query if possible."""
    q = query.lower()
    aliases = {
        "cisco": "cisco", "catalyst": "cisco", "nexus": "cisco", "meraki": "cisco",
        "arista": "arista",
        "juniper": "juniper", "qfx": "juniper", "ex4": "juniper", "ex3": "juniper", "ex2": "juniper",
        "aruba": "aruba", "hpe": "aruba",
        "dell": "dell", "powerswitch": "dell",
        "nvidia": "nvidia", "mellanox": "nvidia", "spectrum": "nvidia",
        "connectx": "nvidia", "bluefield": "nvidia", "quantum": "nvidia",
        "extreme": "extreme", "extremeswitching": "extreme",
        "huawei": "huawei", "cloudengine": "huawei",
        "h3c": "h3c",
        "ubiquiti": "ubiquiti", "ubnt": "ubiquiti", "unifi": "ubiquiti", "usw": "ubiquiti",
        "mikrotik": "mikrotik", "crs": "mikrotik",
        "tp-link": "tp-link", "tplink": "tp-link", "omada": "tp-link",
        "netgear": "netgear",
        "trendnet": "trendnet", "teg-": "trendnet", "tpe-": "trendnet",
        "zyxel": "zyxel",
        "d-link": "d-link", "dlink": "d-link",
        "ruijie": "ruijie",
        "fortinet": "fortinet", "fortiswitch": "fortinet",
        "fs.com": "fs", "fs ": "fs",
        "cambium": "cambium",
        "edgecore": "edgecore", "edge-core": "edgecore",
    }
    # Merge in every alias from vendors.json (134 vendors) so guess_vendor
    # recognises the long tail (Westermo, Hirschmann, Pluribus, Fujitsu, NEC,
    # Tejas, Sophos, Tenda, WatchGuard, ...) — not just the hardcoded ~20.
    try:
        from vendor_registry import aliases as _vr_aliases
        for _alias, _name in _vr_aliases().items():
            aliases.setdefault(_alias, _name.lower())
    except Exception:  # pragma: no cover
        pass
    # Longest alias first so "tp-link" beats "tp"
    for kw in sorted(aliases, key=len, reverse=True):
        if kw in q:
            return aliases[kw]
    return None
