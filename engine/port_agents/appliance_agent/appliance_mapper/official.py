"""Template images from the vendor's own site, for every vendor - found before any web image.

Why: the open web search behind the template agent is throttled and often returns the
vendor's home page instead of the model's page, so resellers' photos won. A site-restricted
search ("site:mikrotik.com RB3011UiAS-RM") through the real browser finds the model's own
pages reliably: the product page, its tech specs, the hardware reference, the data sheet.

    official_images(vendor, model, variant, series) -> [(image url, image, where, exact)]

1. pages: the vendor's official domains (template agent) x the names the model goes by
   (exact variant first, the catalogue model, the name printed on the panel - "FortiGate
   200F" for FG-200F), as site-restricted searches; only pages on the vendor's domains are
   kept, and only those whose address or title names the model or its series. Fortinet
   data sheets are also tried at their fixed address (fortigate-200f-series.pdf). Cisco's
   series data sheets come from appliance_mapper.pipeline.datasheet_images.
2. images on those pages: an image counts when its file name, alt text or caption names
   the model (or the variant); on a page made for this model (its address names it) the
   page's large images count too. PDFs: the embedded images of the pages that mention the
   model, panel-shaped ones only.
Pages and searches are cached on disk (appliance_catalog/scraped/official/).
"""
import os
import hashlib
import html as _html
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
# PORT_AGENT_CACHE_DIR moves every page / search cache out of the package (a writable folder)
def _cache_folder():
    """Read at call time: a run sets PORT_AGENT_CACHE_DIR for its own duration (map_* in the pipelines)."""
    return (Path(os.environ["PORT_AGENT_CACHE_DIR"]) / "appliance_official" if os.environ.get("PORT_AGENT_CACHE_DIR")
            else ROOT / "appliance_catalog" / "scraped" / "official")
MAX_PAGES = 6
MAX_IMAGES = 10

# every vendor's own sites, from the appliance catalogue (vendor_info.py: the domains listed
# per vendor and device type), searched before the domains the template agent guesses
def _domains():
    import sys
    sys.path.insert(0, str(ROOT / "appliance_catalog"))
    import vendor_info as vi
    out = {}
    for table in (vi.FIREWALL, vi.LOAD_BALANCER, vi.GATEWAY):
        for v, d in table.items():
            out.setdefault(v, [])
            out[v] += [x for x in d.get("domains", []) if x not in out[v]]
    return out


DOMAINS = _domains()
try:                                    # vendors taken over from the router catalogue
    from router_mapper.official import DOMAINS as _RD
    for _v, _d in _RD.items():
        DOMAINS.setdefault(_v, list(_d))
except Exception:
    pass
# pages that discuss a model but do not show it officially
NOT_PRODUCT = re.compile(r"(^|\.)(community|live|forum|forums|kb|blog|answers)\.|/t5/|/s/article|/forum|/community|"
                         r"/discussions?/|/blog/|/news/|/press", re.I)


def _key(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


PAGE_DAYS, SEARCH_DAYS = 30, 14     # cached pages / search results older than this are fetched again


def _cache(name, fn, max_days):
    """fn()'s answer, cached on disk. Nothing empty is cached (a timeout must not block that page
    for good) and an entry older than max_days, or an empty one left by older code, is fetched again."""
    import time
    _cache_folder().mkdir(parents=True, exist_ok=True)
    f = _cache_folder() / (hashlib.sha1(name.encode()).hexdigest()[:16] + ".json")
    if f.exists() and time.time() - f.stat().st_mtime < max_days * 86400:
        v = json.loads(f.read_text())
        if v and v != {"html": ""}:
            return v
    v = fn()
    if v and v != {"html": ""}:
        f.write_text(json.dumps(v))
    return v


def _search(q):
    from template_agent.sources import web_search
    return _cache("q:" + q, lambda: [[u, t] for u, t in web_search(q)[:10]], SEARCH_DAYS)


def _page(url):
    from template_agent.sources import _browser_get, _get
    html = _cache("p:" + url, lambda: {"html": (_get(url) or _browser_get(url, settle_ms=3000) or "")},
                  PAGE_DAYS)["html"]
    try:
        import device_spec
        device_spec.note_page(url, html)      # the spec step reads it too, with no search
    except ImportError:
        pass
    return html


def _bytes(url):
    import requests
    from template_agent.sources import HEADERS, _browser_bytes
    data = None
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        if r.status_code == 200 and len(r.content) > 5000:
            data = r.content
    except requests.RequestException:
        pass
    data = data or _browser_bytes(url)
    if data and data[:4] == b"%PDF":
        try:
            import device_spec
            device_spec.note_page(url, data)      # the spec step reads it too, with no search
        except ImportError:
            pass
    return data


def names_for(vendor, model, variant):
    """The names to look for, most specific first."""
    from appliance_mapper.ocr import printed_names
    out = [n for n in (variant, model) if n]
    out += printed_names(vendor, model)
    return list(dict.fromkeys(out))


def _mentions(text, names):
    """The first name the text names as a whole name (separators may differ; Cisco-style
    memory X ignored), or None."""
    from template_agent.sources import model_mention
    t = re.sub(r"\s*-\s*", "-", unquote(text or "")).replace("+", " ")
    t = re.sub(r"(?i)(?<=\d)X(?=-?\d)", "", t)
    for n in names:
        if model_mention(t, n) == "exact":
            return n
    return None


def family_names(vendor, model):
    """Names a vendor files a model's pages under when not under the model itself: its hardware
    family from the catalogue (r5800 -> r5000), and the model without its last letter
    (Mediant 800C -> Mediant 800)."""
    out = []
    try:
        from appliance_mapper import catalog
        c = catalog.resolve(vendor, model)
        if c["found"]:
            hw = catalog.hardware(c["row"])
            if hw and hw != model:
                out.append(hw if re.search(r"[a-z]", hw, re.I) else f"{model.split()[0]} {hw}" if " " in model else hw)
    except Exception:
        pass
    m = re.match(r"^(.*\d)[A-Z]{1,2}$", model)
    if m and len(re.sub(r"\W", "", m.group(1))) >= 4:
        out.append(m.group(1))
    return [n for n in dict.fromkeys(out) if n and n != model]


def official_pages(vendor, model, variant, series, log):
    from template_agent.sources import is_official, official_domains
    names = names_for(vendor, model, variant)
    doms_all = list(dict.fromkeys(DOMAINS.get(vendor, []) + list(official_domains(vendor))))
    # 1. the vendor's own sitemaps: no search engine (they answer scripts with captchas)
    from appliance_mapper import sitemaps
    found = sitemaps.model_urls(vendor, names, doms_all, log)
    exact = bool(found)
    if not found:
        fam = family_names(vendor, model)
        if fam:
            found = sitemaps.model_urls(vendor, fam, doms_all, log)
    site_pages = [(u, (names[0] if exact else None)) for u in found]
    doms = doms_all[:3]
    series_key = _key(re.sub(r"\(.*?\)|older|series", "", series or ""))
    pages = []
    for d in doms:
        for n in names[:3]:
            for q in (f"site:{d} {n}", f"site:{d} {n} datasheet", f"site:{d} {n} hardware"):
                for u, t in _search(q):
                    if not (is_official(u, vendor) or any(urlparse(u).netloc.endswith(x) for x in DOMAINS.get(vendor, []))):
                        continue
                    if NOT_PRODUCT.search(urlparse(u).netloc + urlparse(u).path):
                        continue
                    path = urlparse(u).path
                    hit = _mentions(path + " " + t, names)
                    fam = len(series_key) >= 4 and series_key in _key(path + " " + t)
                    if hit or fam:
                        pages.append((u, hit))
            if len(pages) >= MAX_PAGES:
                break
    # Fortinet publishes one data sheet PDF per series at a fixed address
    m = re.match(r"^(?:FG|FWF)-(\d+)([A-Z])", model)
    if vendor == "Fortinet" and m:
        num = int(m.group(1))
        for n in dict.fromkeys([num, num - num % 10 if num >= 100 else num]):
            pages.append((f"https://www.fortinet.com/content/dam/fortinet/assets/data-sheets/"
                          f"fortigate-{n}{m.group(2).lower()}-series.pdf", None))
    if vendor == "Fortinet":
        pages = fortinet_doc_pages(model, log) + pages
        if re.match(r"^(FortiADC|FAD)", model, re.I):
            pages.append(("https://www.fortinet.com/content/dam/fortinet/assets/data-sheets/FortiADC.pdf", None))
    pages = list(dict.fromkeys(site_pages + pages))[:MAX_PAGES + 4]
    if pages:
        log(f"  vendor site: {len(pages)} page(s): " + ", ".join(urlparse(u).path[-50:] for u, _ in pages[:4]))
    return pages, names


# Fortinet keeps a quick-start / hardware guide per model in its document library, with front and
# back panel figures in the guide's PDF - reachable with no search engine (search engines answer
# automated browsers with captchas or unrelated pages)
_FORTI_PRODUCT = [(r"^(FG|FWF|FGR|FortiGate|FortiWiFi)", "fortigate"), (r"^(FortiADC|FAD)", "fortiadc"),
                  (r"^(FEX|FortiExtender)", "fortiextender"), (r"^(FVG|FortiVoice)", "fortivoice-enterprise")]


def fortinet_doc_pages(model, log):
    """[(guide PDF url, None)] for a Fortinet model from docs.fortinet.com/product/<product>/hardware."""
    prod = next((p for pat, p in _FORTI_PRODUCT if re.match(pat, model, re.I)), None)
    if not prod:
        return []
    num = re.search(r"(\d{2,4}[A-Z]{1,2}(?:-[A-Z0-9]+)?)\b", model.upper())
    if not num:
        return []
    token = num.group(1).lower()
    index = _page(f"https://docs.fortinet.com/product/{prod}/hardware")
    guides = [u for u in dict.fromkeys(re.findall(r'href="(/document/[^"]+/hardware/[^"]+)"', index))
              if re.search(r"(?<![0-9a-z])" + re.escape(token) + r"(?![0-9])", u.lower())]
    out = []
    for g in guides[:3]:
        h = _page("https://docs.fortinet.com" + g)
        out += [(u, None) for u in dict.fromkeys(re.findall(r'(https?://[^"\s]+\.pdf)', h))][:2]
    if out:
        log(f"  Fortinet document library: {len(out)} hardware guide(s) for {model}")
    return out


def _names_other(text, others):
    k = _key(unquote(text or ""))
    return next((o for o in others if o in k), None)


def _html_images(url, page_names_model, names, others=()):
    h = _page(url)
    out = []
    for m in re.finditer(r"<img\b[^>]*>", h):
        tag = m.group(0)
        src = re.search(r'(?:data-src|src)="([^"]+)"', tag)
        if not src or src.group(1).startswith("data:"):
            continue
        alt = " ".join(re.findall(r'(?:alt|title)="([^"]*)"', tag))
        caption = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _html.unescape(h[m.end():m.end() + 250])))[:120]
        u = urljoin(url, _html.unescape(src.group(1)))
        if re.search(r"(logo|icon|sprite|banner|flag|avatar|badge|social)", u, re.I) or u.endswith(".svg"):
            continue
        hit = _mentions(urlparse(u).path + " " + alt + " " + caption[:60], names)
        if not hit and _names_other(urlparse(u).path + " " + alt, others):
            continue                                  # an image of a sibling model (DSR-1000N on a DSR-1000AC page)
        if hit or page_names_model:
            out.append((u, hit, (alt or caption)[:80]))
    return out


def _linked_pdfs(url, names, limit=3):
    """PDF links on a vendor page that look like the model's data sheet or hardware guide."""
    h = _page(url)
    keys = [_key(n) for n in names if len(_key(n)) >= 4]
    out = []
    for href in re.findall(r'href="([^"]+\.pdf[^"]*)"', h, re.I):
        u = urljoin(url, _html.unescape(href))
        k = _key(unquote(urlparse(u).path))
        if any(x in k for x in keys) or re.search(r"datasheet|data-sheet|hardware|install|quick.?start|qsg|spec", u, re.I):
            out.append(u)
    return list(dict.fromkeys(out))[:limit]


def _pdf_images(url, names):
    """Panel-shaped images embedded in a PDF's pages that mention the model."""
    import fitz
    data = _bytes(url)
    if not data or not data.startswith(b"%PDF"):
        return []
    from template_agent.vision.vendor import decode_image
    out = []
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        return []
    for pno in range(min(len(doc), 12)):
        pg = doc[pno]
        if not _mentions(pg.get_text()[:20000], names):
            continue
        for x in pg.get_images(full=True):
            try:
                pix = doc.extract_image(x[0])
            except Exception:
                continue
            w, h = pix.get("width", 0), pix.get("height", 0)
            if w < 500 or not 2.2 <= w / max(h, 1) <= 16:
                continue
            img = decode_image(pix["image"])
            if img is not None:
                out.append((f"{url}#page{pno + 1}-img{x[0]}", img, f"{urlparse(url).path.rsplit('/', 1)[-1]} p{pno + 1}"))
    return out[:6]


def official_images(vendor, model, variant, series, log, others=()):
    """[(src, image, where, exact, named)] from the vendor's own pages, the exact variant's first.
    named: the image itself names the model (file, alt text, caption) or comes from its data sheet."""
    from template_agent.vision.vendor import decode_image
    pages, names = official_pages(vendor, model, variant, series, log)
    found, seen = [], set()
    for url, page_hit in pages:
        if len(found) >= MAX_IMAGES:
            break
        if url.lower().split("?")[0].endswith(".pdf"):
            for src, img, where in _pdf_images(url, names):
                found.append((src, img, f"vendor data sheet {where}", False, True))
            continue
        for pdf in _linked_pdfs(url, names + family_names(vendor, model)):
            if len(found) >= MAX_IMAGES:
                break
            for src, img, where in _pdf_images(pdf, names + family_names(vendor, model)):
                found.append((src, img, f"vendor document {where}", False, True))
        # a page found under the model's hardware family (F5 'platform-guide-r5000...' for an r5800)
        # names its figures after the family
        fam = family_names(vendor, model)
        on_family = page_hit is None and any(_key(f) in _key(urlparse(url).path) for f in fam)
        cands = _html_images(url, bool(page_hit) or on_family, names + (fam if on_family else []), others)
        # named images first, then the dedicated page's other images, largest first is unknown: keep order
        cands.sort(key=lambda c: c[1] is None)
        for u, hit, where in cands[:8]:
            if u in seen:
                continue
            seen.add(u)
            data = _bytes(u)
            img = decode_image(data) if data else None
            if img is None or img.shape[1] < 400:
                continue
            found.append((u, img, f"vendor page {urlparse(url).netloc}: {where}", bool(variant) and hit == variant,
                          hit is not None))
            if len(found) >= MAX_IMAGES:
                break
    if found:
        log(f"  vendor site: {len(found)} image(s) of the model")
    return found
