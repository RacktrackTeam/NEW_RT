"""Find a model's pages on the vendor's own sites from their sitemaps - no search engine.

Search engines answer automated browsers with captchas or unrelated pages, so the vendor's own
sitemaps (robots.txt -> sitemap XML, the list of every page a site has) are read directly:
every vendor domain from the catalogue (main site, documentation and support sites), every
sitemap they list, product / document / hardware sitemaps first. Pages and PDFs whose address
names the model (or its variant / printed name) are returned, best first. Sitemaps are cached
on disk for a week, so a vendor's second model costs almost nothing.

    model_urls(vendor, names, domains, log) -> [url, ...]
"""
import os
import gzip
import hashlib
import re
import time
from pathlib import Path
from urllib.parse import urlparse, unquote

ROOT = Path(__file__).resolve().parent.parent
def _cache_folder():
    """Read at call time: a run sets PORT_AGENT_CACHE_DIR for its own duration (map_* in the pipelines)."""
    return (Path(os.environ["PORT_AGENT_CACHE_DIR"]) / "sitemaps" if os.environ.get("PORT_AGENT_CACHE_DIR")
            else ROOT / "appliance_catalog" / "scraped" / "sitemaps")
TTL = 7 * 24 * 3600
MAX_SITEMAPS = 120            # per host
TIME_BUDGET = 120             # seconds per lookup (first lookup of a vendor; cached afterwards)
HOST_BUDGET = 45              # ... and per host, so one huge main site cannot starve the docs site
GOOD = re.compile(r"product|hardware|document|docs|datasheet|data-sheet|support|guide|appliance|platform|resource|pdf|asset", re.I)
SKIP = re.compile(r"blog|news|press|video|image|media|event|career|article|webinar|story|partner|people|author|tag|category|"
                  r"customer|case-stud|forum|community|kb-|/kb/|training|course", re.I)


def _get(url, timeout=20):
    import requests
    from template_agent.sources import HEADERS
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code == 200:
            return r.content
    except requests.RequestException:
        pass
    return None


def _xml(url):
    _cache_folder().mkdir(parents=True, exist_ok=True)
    f = _cache_folder() / (hashlib.sha1(url.encode()).hexdigest()[:20] + ".xml")
    if f.exists() and time.time() - f.stat().st_mtime < TTL:
        return f.read_text(errors="replace")
    data = _get(url)
    if not data:
        f.write_text("")                     # remember the miss for a week too
        return ""
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except OSError:
            return ""
    text = data.decode("utf-8", errors="replace")
    if "<loc>" not in text:
        text = ""
    f.write_text(text)
    return text


def _roots(host):
    f = _cache_folder() / (hashlib.sha1(("robots:" + host).encode()).hexdigest()[:20] + ".txt")
    if f.exists() and time.time() - f.stat().st_mtime < TTL:
        roots = f.read_text().split()
    else:
        txt = (_get(f"https://{host}/robots.txt") or b"").decode("utf-8", errors="replace")
        roots = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", txt)
        _cache_folder().mkdir(parents=True, exist_ok=True)
        f.write_text("\n".join(roots))
    return roots or [f"https://{host}/sitemap.xml", f"https://{host}/sitemap_index.xml"]


def _hosts(domains):
    """Every host to read, documentation / support sites first (hardware guides live there)."""
    out = []
    for d in domains:
        d = d.strip().lower()
        for h in ([d] if d.count(".") >= 2 else [f"www.{d}", d]):
            if h not in out:
                out.append(h)
    return sorted(out, key=lambda h: 0 if re.match(r"(docs?|techdocs|clouddocs|documentation|support|help|campus|sc1)\.", h) else 1)


def _pattern(name):
    """The name with any (or no) separators between its characters, as a whole word."""
    chars = [re.escape(c) for c in name.lower() if c.isalnum()]
    return re.compile(r"(?<![a-z0-9])" + r"[\W_]{0,2}".join(chars) + r"(?![0-9])", re.I) if len(chars) >= 3 else None


def model_urls(vendor, names, domains, log=print, limit=8):
    """Addresses on the vendor's sites that name one of `names` (exact model first)."""
    pats = [(n, _pattern(n)) for n in names if _pattern(n)]
    # names that carry the vendor's product word: also match on the number part alone
    # ("FortiADC 4200F" -> "4200f" next to "fortiadc" anywhere in the address)
    if not pats:
        return []
    t0 = time.time()
    hits = []
    for host in _hosts(domains):
        queue, seen, th = list(_roots(host)), set(), time.time()
        while queue and len(seen) < MAX_SITEMAPS and time.time() - t0 < TIME_BUDGET and time.time() - th < HOST_BUDGET:
            sm = queue.pop(0)
            if sm in seen:
                continue
            seen.add(sm)
            xml = _xml(sm)
            for loc in re.findall(r"<loc>\s*(?:<!\[CDATA\[)?\s*([^<\s\]]+)", xml):
                loc = unquote(loc.replace("&amp;", "&"))
                if re.search(r"\.xml(\.gz)?($|\?)", loc, re.I):
                    if SKIP.search(loc):
                        continue
                    (queue.insert(0, loc) if GOOD.search(loc) else queue.append(loc))
                    continue
                path = urlparse(loc).path
                for rank, (n, p) in enumerate(pats):
                    if p.search(path):
                        hits.append((rank, loc))
                        break
    def order(h):
        rank, u = h
        path = urlparse(u).path.lower()
        kind = 0 if path.endswith(".pdf") else 1 if re.search(r"hardware|guide|datasheet|data-sheet|spec", path) else 2
        en = 0 if re.search(r"/(en|en-us|us|global)(/|$)|^/(?![a-z]{2}(-[a-z]{2})?/)", path) else 1
        return (rank, kind, en, len(u))
    out = [u for _, u in sorted(dict.fromkeys(hits), key=order)][:limit]
    log(f"  vendor sitemaps: {len(out)} page(s) naming the model ({round(time.time() - t0)} s)"
        + (": " + ", ".join(urlparse(u).path[-50:] for u in out[:3]) if out else ""))
    return out
