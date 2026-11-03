"""
vendor_images.py - find product photos of a switch model on the vendor's own website.

No API key needed. For each vendor in vendors.json:
  1. read the site's sitemaps (from robots.txt, or /sitemap.xml) and find page URLs that
     contain the model number
  2. open those product pages and collect their images: og:image, JSON-LD images and
     <img> tags whose file name or alt text mentions the model
Optional: an image-search API restricted to the vendor's domain (SERPAPI_KEY, or
GOOGLE_API_KEY + GOOGLE_CSE_ID) when the sitemap route finds nothing.

Some vendor sites block automated requests (they answer 403 to scripts). For those the
search API or a URL you supply (--vendor-image) is the way in.
"""
import gzip
import json
import os
import re
import time
import urllib.parse
import urllib.request
from html import unescape

from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"          # saved templates, one folder per model
VENDOR_PHOTOS = HERE / "vendor_photos"  # saved vendor product photos (+ web/ download cache)
VENDORS_JSON = HERE / "vendors.json"    # vendor websites for the image search
WEB = HERE / "ui"                       # the UI page
OUTPUT = HERE / "output"                # results; created on first use
UI_RUNS = OUTPUT / "ui_runs"            # one folder per UI run
SHOWCASE = HERE / "showcase"            # UI showcase examples

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
VENDORS = json.loads(VENDORS_JSON.read_text())


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def http_get(url, timeout=20, max_bytes=40_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(max_bytes)
        if r.headers.get("Content-Encoding") == "gzip" or url.endswith(".gz") or data[:2] == b"\x1f\x8b":
            try:
                data = gzip.decompress(data)
            except OSError:
                pass
        return data, r.geturl()


# ----------------------------------------------------------------------------- vendor lookup

def find_vendor(name_or_text):
    """Match free text (OCR word or user input) to a vendor entry."""
    t = name_or_text.lower().replace(" ", "")
    for v in VENDORS:
        if norm(v["name"]) == norm(name_or_text):
            return v
    for v in VENDORS:
        for a in v["aliases"]:
            a2 = a.replace(" ", "")
            if len(a2) >= 4 and a2 in t:
                return v
            if len(a2) < 4 and t == a2:
                return v
    return None


def vendor_from_model(model):
    m = model.upper()
    hits = [v for v in VENDORS for p in v["model_prefixes"] if re.match(p, m)]
    return hits[0] if len(hits) == 1 else None      # only trust an unambiguous prefix


# ----------------------------------------------------------------------------- sitemaps

def _sitemap_roots(vendor):
    roots = list(vendor.get("sitemaps") or [])
    for d in vendor["domains"]:
        host = d if d.count(".") > 1 else "www." + d
        if not roots:
            try:
                txt, _ = http_get(f"https://{host}/robots.txt", max_bytes=200_000)
                roots += re.findall(r"(?im)^sitemap:\s*(\S+)", txt.decode("utf-8", "ignore"))
            except Exception:
                pass
        roots += [f"https://{host}/sitemap.xml", f"https://{host}/sitemap_index.xml"]
    seen, out = set(), []
    for r in roots:
        if r not in seen:
            seen.add(r); out.append(r)
    return out


def model_keys(model):
    """Full normalised model, then progressively shorter (TL-SG3424P-AB -> tlsg3424p, ...)."""
    parts = re.split(r"[-_/ ]", model.lower())
    keys = []
    for k in range(len(parts), 0, -1):
        key = norm("".join(parts[:k]))
        if len(key) >= 5 and re.search(r"\d", key) and key not in keys:
            keys.append(key)
    return keys


def sitemap_pages(vendor, model, budget_s=60, max_fetch=40, log=print):
    keys = model_keys(model)
    if not keys:
        return []
    t0 = time.time()
    queue = _sitemap_roots(vendor)
    seen, fetched, hits = set(queue), 0, {}
    while queue and fetched < max_fetch and time.time() - t0 < budget_s:
        url = queue.pop(0)
        try:
            data, _ = http_get(url)
        except Exception:
            continue
        fetched += 1
        xml = data.decode("utf-8", "ignore")
        locs = [unescape(l.strip()) for l in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", xml)]
        if "<sitemapindex" in xml:
            # visit product / english sitemaps first
            def prio(u):
                ul = u.lower()
                return (0 if "product" in ul else 1, 0 if re.search(r"/(en|us|en-us|global)/|[-_](en|us)\b", ul) else 1)
            for l in sorted(locs, key=prio):
                if l not in seen:
                    seen.add(l); queue.append(l)
            continue
        for l in locs:
            nl = norm(urllib.parse.urlparse(l).path)
            for rank, k in enumerate(keys):
                if k in nl:
                    hits[l] = min(hits.get(l, 99), rank)
                    break
    side = ("support", "download", "datasheet", "resources", "preview", "news", "blog", "faq",
            "document", "firmware", "manual", "compare", "case-stud", "video")
    pages = sorted(hits, key=lambda u: (any(w in u.lower() for w in side), hits[u],
                                        0 if re.search(r"/(en|us|en-us|en-global|global)/", u.lower()) else 1,
                                        len(u)))
    log(f"  sitemaps: {fetched} read, {len(pages)} pages mention the model")
    return pages[:4]


# ----------------------------------------------------------------------------- page images

def page_images(page_url, model):
    try:
        data, final = http_get(page_url, max_bytes=5_000_000)
    except Exception:
        return []
    html = data.decode("utf-8", "ignore")
    try:
        import device_spec
        device_spec.note_page(page_url, html)      # the spec step reads it too, with no search
    except ImportError:
        pass
    key = model_keys(model)
    imgs = []
    for m in re.finditer(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]*>', html, re.I):
        c = re.search(r'content=["\']([^"\']+)', m.group(0))
        if c:
            imgs.append((0, c.group(1)))
    for m in re.finditer(r'"image"\s*:\s*(\[[^\]]*\]|"[^"]+")', html):
        for u in re.findall(r'"(https?://[^"]+)"', m.group(1)):
            imgs.append((1, u))
    for m in re.finditer(r"<img\b[^>]*>", html, re.I):
        tag = m.group(0)
        srcs = re.findall(r'(?:src|data-src|data-original|data-lazy-src)=["\']([^"\']+)', tag, re.I)
        srcs += [s.split()[0] for ss in re.findall(r'(?:srcset|data-srcset)=["\']([^"\']+)', tag, re.I)
                 for s in ss.split(",") if s.strip()]
        alt = " ".join(re.findall(r'(?:alt|title)=["\']([^"\']*)', tag, re.I))
        for s in srcs:
            n = norm(s + alt)
            if any(k in n for k in key):
                imgs.append((2, s))
    out, seen = [], set()
    for _, u in sorted(imgs, key=lambda x: x[0]):
        u = urllib.parse.urljoin(final, unescape(u))
        if not re.search(r"\.(jpe?g|png|webp)(\?|$)", u, re.I) and "image" not in u.lower():
            continue
        if any(w in u.lower() for w in ("logo", "icon", "sprite", "banner", "thumb")):
            continue
        if u not in seen:
            seen.add(u); out.append(u)
    return out


# ----------------------------------------------------------------------------- site-specific resolvers

def resolve_tplink(model, log=print):
    """TP-Link's sitemaps mostly list support pages; its site search finds product pages."""
    urls = []
    try:
        html, _ = http_get(f"https://www.tp-link.com/us/search/?q={urllib.parse.quote(model)}&t=product")
        html = html.decode("utf-8", "ignore")
        pages = sorted(set(re.findall(r'href="(/[a-z]{2}/[^"#]*/' + re.escape(model.lower()) + r'/)"', html)))
        for p in [p for p in pages if "/support/" not in p][:2]:
            page, _ = http_get("https://www.tp-link.com" + p)
            page = page.decode("utf-8", "ignore")
            key = norm(model)
            imgs = [u for u in re.findall(r'https?://static\.tp-link\.com/[^"\'\s>]+\.(?:jpg|png)', page)
                    if key in norm(u) and "thumb" not in u.lower()]
            for u in sorted(imgs, key=lambda u: (0 if ("large" in u.lower() or "_01" in u) else 1)):
                if u not in urls:
                    urls.append(u)
    except Exception as e:
        log(f"  TP-Link lookup failed: {e}")
    return urls


SITE_RESOLVERS = {"TP-Link": resolve_tplink}


# ----------------------------------------------------------------------------- search API

def search_api_images(vendor_name, domains, model):
    q = f"{vendor_name} {model} front"
    urls = []
    site = domains[0] if domains else None
    if os.environ.get("SERPAPI_KEY"):
        try:
            p = {"engine": "google_images", "q": (f"site:{site} " if site else "") + q,
                 "api_key": os.environ["SERPAPI_KEY"]}
            d = json.loads(http_get("https://serpapi.com/search.json?" + urllib.parse.urlencode(p))[0])
            urls += [r["original"] for r in d.get("images_results", [])[:10] if r.get("original")]
        except Exception as e:
            print(f"  SerpAPI failed: {e}")
    if os.environ.get("GOOGLE_API_KEY") and os.environ.get("GOOGLE_CSE_ID"):
        try:
            p = {"q": q, "searchType": "image", "num": 10, "imgSize": "large",
                 "key": os.environ["GOOGLE_API_KEY"], "cx": os.environ["GOOGLE_CSE_ID"]}
            if site:
                p["siteSearch"] = site
            d = json.loads(http_get("https://www.googleapis.com/customsearch/v1?" + urllib.parse.urlencode(p))[0])
            urls += [it["link"] for it in d.get("items", [])]
        except Exception as e:
            print(f"  Google search API failed: {e}")
    return urls


# ----------------------------------------------------------------------------- Wikimedia Commons

def commons_images(vendor_name, model, limit=8):
    """Free fallback with no key: photos on Wikimedia Commons whose title or description
    mentions the model. Coverage is patchy, but it includes vendors whose own sites
    block scripts (Cisco, HPE, Juniper ...)."""
    keys = model_keys(model)
    if not keys:
        return []
    q = {"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
         "gsrsearch": f"{vendor_name} {model}", "gsrlimit": 20, "prop": "imageinfo",
         "iiprop": "url|size|mime", "iiurlwidth": 2000}
    try:
        d = json.loads(http_get("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(q))[0])
    except Exception:
        return []
    out = []
    for page in (d.get("query", {}).get("pages", {}) or {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        if info.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
            continue
        if not any(k in norm(page.get("title", "")) for k in keys):
            continue          # search is fuzzy; keep files that name the model
        out.append(info.get("thumburl") or info.get("url"))
    return [u for u in out if u][:limit]


# ----------------------------------------------------------------------------- entry point

def find_images(vendor_name, model, log=print, budget_s=60):
    """Returns (urls, how). Tries the vendor's site first, then a search API (if a key
    is set), then Wikimedia Commons."""
    v = find_vendor(vendor_name)
    if v is None:
        log(f"  '{vendor_name}' is not in vendors.json; trying the search API and Wikimedia Commons")
        api = search_api_images(vendor_name, [], model)
        if api:
            return api, "search API"
        return commons_images(vendor_name, model), "Wikimedia Commons"
    urls = []
    if v["name"] in SITE_RESOLVERS:
        urls = SITE_RESOLVERS[v["name"]](model, log=log)
        if urls:
            return urls, f"{v['name']} website"
    for page in sitemap_pages(v, model, budget_s=budget_s, log=log):
        imgs = page_images(page, model)
        log(f"  {page} -> {len(imgs)} images")
        urls += [u for u in imgs if u not in urls]
        if len(urls) >= 8:
            break
    if urls:
        return urls, f"{v['name']} website"
    api = search_api_images(v["name"], v["domains"], model)
    if api:
        return api, "search API"
    return commons_images(v["name"], model), "Wikimedia Commons"
