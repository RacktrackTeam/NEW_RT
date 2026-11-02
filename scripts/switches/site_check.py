"""Online check of the switch list, pass 1: the vendor's own site, through its sitemaps (2026-10-23).

    python3 scripts/switches/site_check.py [out.json]        # default data/catalogues/switch_site_checks.json

For every vendor in Switch_Vendors_and_Models.xlsx: take its sitemaps from vendors.json, else try robots.txt and
/sitemap.xml on each of its domains; collect the page URLs (sitemap indexes followed, at most MAX_SUB files per
vendor); a model is confirmed when its name is a path token of a URL on the vendor's domain. A URL under an
end-of-life / discontinued / legacy path gives 'End-of-sale', any other product page 'Current'.
Output: {"checked": date, "stats": {vendor: {...}}, "found": {vendor: {model: {"url":..., "status":...}}}}.
The workbook builder reads this file and fills Status / Source / Checked at.
"""
import gzip
import json
import re
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[2]
SW = ROOT / "data" / "catalogues"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else SW / "switch_site_checks.json"
HDR = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) RackTrack catalogue check"}
SKIP = re.compile(r"blog|news|press|/kb/|/faq|community|career|legal|privacy|image|video|hreflang|deals|/sr-|event|webinar|case-stud|partner", re.I)
EOL = re.compile(r"end-of-life|end-of-sale|eol|eos|discontinued|legacy|retired|archive", re.I)
MAX_SUB, TIMEOUT, PAUSE = 80, 20, 0.2


def fetch(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=TIMEOUT) as r:
            data = r.read()
        if url.endswith(".gz") or data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        return data
    except Exception as ex:                                   # noqa: BLE001 - any failure is a miss, not a crash
        print("  fail", url, type(ex).__name__, file=sys.stderr)
        return None


def locs(data):
    """<loc> entries, split into (sitemap files, page urls); regex on purpose, no XML parser on foreign input."""
    out = [u.decode("utf-8", "ignore").strip() for u in re.findall(rb"<loc>\s*([^<\s]+)\s*</loc>", data)]
    is_index = b"<sitemapindex" in data[:2000]
    return (out, []) if is_index else ([], out)


def seeds_for(domains, sitemaps):
    seeds = list(sitemaps)
    for d in domains:
        base = f"https://www.{d}" if not d.startswith("www.") and d.count(".") == 1 else f"https://{d}"
        rb = fetch(base + "/robots.txt")
        if rb:
            seeds += re.findall(rb"(?im)^sitemap:\s*(\S+)", rb) and [m.decode() for m in re.findall(rb"(?im)^sitemap:\s*(\S+)", rb)]
        seeds += [base + "/sitemap.xml", base + "/sitemap_index.xml"]
    seen, uniq = set(), []
    for s in seeds:
        if s not in seen:
            seen.add(s); uniq.append(s)
    return uniq


def crawl(seeds, domains):
    pages, seen, queue, n = [], set(), list(seeds), 0
    while queue and n < MAX_SUB:
        u = queue.pop(0)
        if u in seen or not any(d in urlparse(u).netloc for d in domains):
            continue
        seen.add(u); n += 1
        data = fetch(u)
        if not data:
            continue
        idx, pg = locs(data)
        pages += [p for p in pg if any(d in urlparse(p).netloc for d in domains)]
        queue += [i for i in idx if not SKIP.search(i)]
        time.sleep(PAUSE)
    return pages


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def main():
    wb = load_workbook(SW / "Switch_Vendors_and_Models.xlsx", read_only=True, data_only=True)
    models = {}
    for v, m, *_ in list(wb["Switches"].iter_rows(values_only=True))[1:]:
        models.setdefault(v, []).append(str(m))
    vend = {r[0]: r for r in list(wb["Vendors"].iter_rows(values_only=True))[1:]}
    result = {"checked": time.strftime("%Y-%m-%d"), "stats": {}, "found": {}}
    if OUT.exists():
        result = json.load(open(OUT))
    for vendor, names in models.items():
        if vendor in result["stats"]:
            continue
        row = vend.get(vendor)
        domains = [d.strip() for d in str(row[3] or "").split(",") if d.strip()] if row else []
        sitemaps = [s.strip() for s in str(row[5] or "").split(",") if s.strip()] if row else []
        print("==", vendor, domains, file=sys.stderr)
        if not domains:
            result["stats"][vendor] = {"urls": 0, "models": len(names), "confirmed": 0, "note": "no domain"}
            continue
        pages = crawl(seeds_for(domains, sitemaps), domains)
        tokens = {}
        for p in pages:
            path = urlparse(p).path.lower()
            for t in re.split(r"[/._\-+ ]+", path):
                if len(t) >= 4:
                    tokens.setdefault(norm(t), p)
        found = {}
        for m in names:
            k = norm(m)
            if len(k) < 4 or k.isdigit():
                continue
            url = tokens.get(k)
            if not url and len(k) >= 6:
                for t, p in tokens.items():
                    if t.startswith(k) and len(t) <= len(k) + 3:
                        url = p
                        break
            if url:
                found[m] = {"url": url, "status": "End-of-sale" if EOL.search(urlparse(url).path) else "Current"}
        result["found"][vendor] = found
        result["stats"][vendor] = {"urls": len(pages), "models": len(names), "confirmed": len(found)}
        print("  ", result["stats"][vendor], file=sys.stderr)
        json.dump(result, open(OUT, "w"), indent=1)
    json.dump(result, open(OUT, "w"), indent=1)
    tot = sum(s["confirmed"] for s in result["stats"].values())
    print(f"{OUT.name}: {tot} models confirmed on vendor sites, {len(result['stats'])} vendors")


if __name__ == "__main__":
    main()
