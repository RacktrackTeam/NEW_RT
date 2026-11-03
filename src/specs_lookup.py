"""Rack-unit height from the RackTrack specs agent (Agent/Agent_scrap - the same agent
switch_mapper, dark_mobile and RT-EOS call; the first copy found in AGENT_DIRS is used).

Called the way RT-EOS server/lib/device_lookup.js calls it: local database first (no network);
on a miss retry the agent's own closest suggestion; then the live web lookup (deadline 25 s).

Two guards on top, both from reading what the agent returns:
  * The agent's matcher keys on the part of the model from the first digit on, so "DIS-1024D"
    "matches" a stored "Dogs-1024d" with high confidence. A hit is only accepted when the full
    normalised model actually matches the one asked for.
  * Web-scraped heights take the first integer near "size"/"height", so "19-inch rack" can come
    back as 19. Only 1-12 U is trusted; 0 means the agent recorded it as a desktop unit.

Last step, when the agent has no height: switch-mapper's page finder (agent_images.py, which uses the
agent's 18 vendor scrapers, vendor web shops and browser headers that search engines and vendor sites
accept) plus a Bing web search find product pages; the height is read where the page says e.g.
"1U rack-mount" or "Form factor: 1U", only from pages that name the model, and the most common
answer wins.

The agent's database is only read (live=False). The web step calls the agent's own extractor
(live_extract.live_lookup) with persist=False, so RT-EOS's database is never written. It is called
directly because a lookalike database hit (see above) otherwise stops the agent from going to the web.
"""
import dataclasses
import pathlib
import re
import sys
import time

# The same agent lives in several RackTrack projects; the first complete copy is used.
# On another machine, point RACKTRACK_SPECS_AGENT at its Agent/Agent_scrap folder.
import os
AGENT_DIRS = [pathlib.Path(p) for p in [os.environ.get("RACKTRACK_SPECS_AGENT")] if p] + [pathlib.Path(os.environ.get("RACKTRACK_REPO", pathlib.Path(__file__).resolve().parents[1] / "rt_eos")) / "switch_specs"]
AGENT_DIR = next((d for d in AGENT_DIRS if (d / "agent.py").exists() and (d / "data" / "switches.db").exists()),
                 AGENT_DIRS[0])
LIVE_DEADLINE_S = 25      # same as RT-EOS device_lookup.js AGENT_LIVE_DEADLINE_S
SUGGESTION_MIN = 0.75     # same as RT-EOS device_lookup.js
MAX_TRUSTED_U = 12
SWITCH_MAPPER_DIRS = [pathlib.Path(p) for p in [os.environ.get("SWITCH_MAPPER_DIR")] if p] + [pathlib.Path(__file__).resolve().parents[1] / "tools" / "switch-mapper"]
SWITCH_MAPPER = next((d for d in SWITCH_MAPPER_DIRS if (d / "agent_images.py").exists()), SWITCH_MAPPER_DIRS[0])
PAGE_LIMIT = 6


def available():
    return (AGENT_DIR / "agent.py").exists() and (AGENT_DIR / "data" / "switches.db").exists()


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _import_agent():
    if str(AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(AGENT_DIR))


def _local_agent():
    _import_agent()
    from agent import SpecAgent
    return SpecAgent(live=False)     # database read only, no network, no writes


def _web_record(query):
    _import_agent()
    import live_extract
    rec = live_extract.live_lookup(query, deadline_sec=LIVE_DEADLINE_S, persist=False)
    return dataclasses.asdict(rec) if rec is not None else None


# ---------------------------------------------------------------- web pages (switch-mapper's fetcher)
_RACK_WORDS = r"(?:rack[\s-]*(?:mount(?:able|ed)?|unit|units|height|size|space)?|form[\s-]*factor|chassis|height|enclosure)"
_U_NEAR_RACK = [
    re.compile(_RACK_WORDS + r"[^.\n<]{0,40}?\b(\d{1,2})\s*(?:U|RU)\b", re.I),
    re.compile(r"\b(\d{1,2})\s*(?:U|RU)\b[^.\n<]{0,30}?" + _RACK_WORDS, re.I),
    re.compile(r"\b(\d{1,2})\s*-?\s*(?:U|RU)\s+(?:high|tall|height)\b", re.I),
]


def _switch_mapper():
    if not (SWITCH_MAPPER / "agent_images.py").exists():
        return None
    if str(SWITCH_MAPPER) not in sys.path:
        sys.path.insert(0, str(SWITCH_MAPPER))
    import agent_images
    return agent_images


def _bing_pages(ai, query, model_key, limit=PAGE_LIMIT):
    from urllib.parse import quote_plus
    html = ai._get(f"https://www.bing.com/search?q={quote_plus(query)}")
    if not html:
        return []
    urls = []
    for href, title in re.findall(r'<h2[^>]*><a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', html):
        if model_key in _norm(href) or model_key in _norm(re.sub("<[^>]+>", "", title)):
            urls.append(href.replace("&amp;", "&"))
    return list(dict.fromkeys(urls))[:limit]


def heights_in_text(text, model_key):
    """Rack heights (1-12) a page states near rack wording; [] if the page never names the model."""
    flat = re.sub(r"\s+", " ", text)
    if model_key not in _norm(flat):
        return []
    found = []
    for rx in _U_NEAR_RACK:
        found += [int(m) for m in rx.findall(flat) if 1 <= int(m) <= MAX_TRUSTED_U]
    return found


U_MM = 44.45                 # one rack unit
TOTAL_DEADLINE_S = 60        # stop fetching product pages after this long
_NUM = r"(\d{1,4}(?:[.,]\d+)?)"
_DIMS_MM = re.compile(_NUM + r"\s*(?:mm)?\s*[x×*]\s*" + _NUM + r"\s*(?:mm)?\s*[x×*]\s*" + _NUM + r"\s*mm", re.I)
_DIMS_IN = re.compile(_NUM + r"\s*(?:in|\"|”)?\s*[x×*]\s*" + _NUM + r"\s*(?:in|\"|”)?\s*[x×*]\s*" + _NUM
                      + r"\s*(?:in(?:ch(?:es)?)?\b|\"|”)", re.I)


def units_from_dimensions(text, model_key):
    """Rack units from a stated size such as "280 x 126 x 44 mm": the smallest side is the height of a rack
    device, rounded up to whole units (44 mm -> 1U, 88 mm -> 2U). [] if the text never names the model."""
    import math
    flat = re.sub(r"\s+", " ", text)
    if model_key not in _norm(flat):
        return []
    out = []
    for rx, scale in ((_DIMS_MM, 1.0), (_DIMS_IN, 25.4)):
        for m in rx.finditer(flat):
            dims = sorted(float(v.replace(",", ".")) * scale for v in m.groups())
            h = dims[0]
            if 20 <= h <= MAX_TRUSTED_U * U_MM + 5 and dims[-1] >= 100:      # plausible device, not a tiny part
                out.append(max(1, math.ceil((h - 2) / U_MM)))
    return out


def _pdf_text(url, timeout=20, max_bytes=15_000_000):
    """Text of a PDF datasheet, or "" if it can't be fetched or read."""
    import requests
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}, stream=True)
        if r.status_code != 200:
            return ""
        data = r.raw.read(max_bytes + 1, decode_content=True)
        if len(data) > max_bytes or not data.startswith(b"%PDF"):
            return ""
        import fitz
        with fitz.open(stream=data, filetype="pdf") as doc:
            return " ".join(page.get_text() for page in doc)
    except Exception:
        return ""


def _height_from_document(text, model_key):
    """(units, how) from one page or datasheet: an explicit "1U rack-mount" wins, else the stated size."""
    from collections import Counter
    hs = heights_in_text(text, model_key)
    if hs:
        return Counter(hs).most_common(1)[0][0], "states the rack height"
    ds = units_from_dimensions(text, model_key)
    if ds:
        return Counter(ds).most_common(1)[0][0], "from the stated size"
    return None, ""


def _datasheet_height(rec, model):
    """Height from the datasheet the specs agent recorded for this model, or None."""
    url = (rec or {}).get("datasheet_url")
    if not url:
        return None
    text = _pdf_text(url) if url.lower().split("?")[0].endswith(".pdf") else ""
    if not text:
        ai = _switch_mapper()
        html = ai._get(url) if ai else None
        if html:
            from bs4 import BeautifulSoup
            text = BeautifulSoup(html, "html.parser").get_text(" ")
    units, how = _height_from_document(text, _norm(model)) if text else (None, "")
    return {"units": units, "how": how, "url": url} if units else None


def _web_pages_height(make, model):
    """{'units', 'votes', 'pages'} read from product pages, or None."""
    ai = _switch_mapper()
    if ai is None:
        return None
    from collections import Counter
    from bs4 import BeautifulSoup
    mk = _norm(model)
    deadline = time.time() + TOTAL_DEADLINE_S
    pages = ai.official_pages(make, model)
    for q in (f"{make} {model} rack mount 1U", f"{make} {model} datasheet"):
        if time.time() > deadline:
            break
        pages += [u for u in _bing_pages(ai, q, mk) if u not in pages]
    votes, used = Counter(), []
    for url in pages[:PAGE_LIMIT * 2]:
        if time.time() > deadline:
            break
        if url.lower().split("?")[0].endswith(".pdf"):
            text = _pdf_text(url)
        else:
            html = ai._get(url, timeout=10)
            text = BeautifulSoup(html, "html.parser").get_text(" ") if html else ""
        u, _ = _height_from_document(text, mk) if text else (None, "")
        if u:
            votes[u] += 1                               # one vote per page
            used.append(url)
    if not votes:
        return None
    units, n = votes.most_common(1)[0]
    return {"units": units, "votes": n, "pages": used, "all_votes": dict(votes)}


def _same_model(asked, rec):
    """True when the record really is the asked-for model, not a digit-core lookalike."""
    q = _norm(asked)
    for field in ("model", "sku"):
        r = _norm(rec.get(field))
        if len(r) >= 4 and len(q) >= 4 and (q == r or q in r or r in q):
            return True
    return False


def _record(resp):
    if not resp or resp.get("type") != "spec":
        return None
    rec = resp.get("result") or (resp.get("results") or [None])[0]
    return dict(rec) if rec else None


def _similarity(a, b):
    from difflib import SequenceMatcher
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()



# ---------------------------------------------------------------- last resort: estimate from the name
NETWORK_MAKES = {"cisco", "meraki", "juniper", "aruba", "hp", "hpe", "hewlettpackard", "hewlettpackardenterprise", "dell",
                 "netgear", "tplink", "dlink", "zyxel", "ubiquiti", "mikrotik", "extreme", "extremenetworks", "brocade",
                 "ruckus", "alliedtelesis", "huawei", "h3c", "arista", "fortinet", "linksys", "edgecore", "ruijie",
                 "cambium", "planet", "trendnet", "mellanox", "nvidia", "lenovo", "alcatel", "alcatellucent", "avaya",
                 "enterasys", "foundry", "3com", "buffalo", "qnap", "teltonika", "cudy", "tenda", "mercusys", "edimax"}
# A switch model with this many ports in its name: "1820-24G", "GS1900-24", "x230-28GT", "S5735-L24T4S".
_PORTS_IN_NAME = re.compile(r"(?:^|[^0-9])(24|26|28|48|50|52)(?![0-9])(?:[a-z]|-|\b|$)", re.I)
_PORT_WORDS = re.compile(r"\b(24|48)[\s-]*ports?\b", re.I)
# Vendors that put the port count at the end of the number: Netgear GS724T / XS748T / M4300-52G, Dell N1548 / N3024.
_VENDOR_PORTS = {"netgear": re.compile(r"^(?:j?gs|xs|fs|gsm|ms)\d(24|28|48|52)", re.I),
                 "dell": re.compile(r"^n\d{1,2}(24|48)", re.I)}
_NOT_A_SWITCH = re.compile(r"patch|panel|ups|pdu|kvm|line[\s-]*card|module|\blc\b|blade", re.I)


def estimate_from_name(make, model):
    """{'units', 'reason'} when the name alone makes the height very likely, else None.
    Fixed 24-52 port switches from network vendors are 1U; nothing else is guessed."""
    if _norm(make) not in NETWORK_MAKES or _NOT_A_SWITCH.search(model or ""):
        return None
    vp = _VENDOR_PORTS.get(_norm(make))
    m = (_PORT_WORDS.search(model or "") or _PORTS_IN_NAME.search(model or "")
         or (vp.search(re.sub(r"[^a-z0-9]", "", (model or "").lower())) if vp else None))
    if not m:
        return None
    return {"units": 1, "reason": f"{m.group(1)}-port network switches are 1U"}


def lookup_height(make, model, live=True):
    """Return {'units', 'status', 'matched', 'source', 'confidence', 'note'}.

    status: 'found' (units is an int 1-12) | 'desktop' (agent says not rack-mount) |
            'no-height' (agent knows the device but not its height) | 'not-found' |
            'estimated' (no source has it; the name makes the height very likely - see estimate_from_name)
    """
    query = f"{make} {model}".strip()
    out = {"units": None, "status": "not-found", "matched": None, "source": None, "confidence": None, "note": ""}
    if not available() or not model.strip():
        est = estimate_from_name(make, model) if model.strip() else None
        if est:      # no agent on this machine: the name-based estimate still works
            out.update(units=est["units"], status="estimated", matched=query, confidence="estimate",
                       source=f"estimate from the model name ({est['reason']})",
                       note="the specs agent is not installed on this machine; check the height")
            return out
        out["note"] = "specs agent not available" if not available() else ""
        return out

    a = _local_agent()
    resp = a.answer(query)
    rec = _record(resp)
    if rec is not None and _same_model(model, rec):
        out["source"] = "specs agent · local database"
    else:
        rec = None
        sugg = (resp or {}).get("suggestions") or []
        best = max(sugg, key=lambda s: _similarity(model, s), default=None)
        if best and _similarity(model, best) >= SUGGESTION_MIN:
            r2 = a.answer(best)
            cand = _record(r2)
            if cand is not None and _same_model(best, cand):
                rec, resp, out["source"] = cand, r2, "specs agent · local database (closest name)"
        if rec is None and live:
            try:
                cand = _web_record(query)
            except Exception as e:  # network down, site blocked, parser error
                cand, out["note"] = None, f"web lookup failed: {e}"
            if cand is not None and _same_model(model, cand):
                rec, resp, out["source"] = cand, {"confidence": "live-web"}, "specs agent · live web lookup"

    if live and rec is not None and not (isinstance(rec.get("rack_units"), int) and 1 <= rec["rack_units"] <= MAX_TRUSTED_U):
        ds = _datasheet_height(rec, model)
        if ds:
            out.update(units=ds["units"], status="found",
                       matched=" ".join(x for x in (rec.get("vendor"), rec.get("model")) if x),
                       source=f"manufacturer datasheet ({ds['how']}), found by the specs agent",
                       confidence="high", note=ds["url"])
            return out
    if rec is None or not (isinstance(rec.get("rack_units"), int) and 1 <= rec["rack_units"] <= MAX_TRUSTED_U):
        if live and rec is None or (live and rec is not None and rec.get("rack_units") != 0):
            try:
                web = _web_pages_height(make, model)
            except Exception as e:  # a site change or network error must not break the page
                web, out["note"] = None, f"product-page lookup failed: {e}"
            if web:
                agree = f"{web['votes']} of {sum(web['all_votes'].values())} pages agree"
                out.update(units=web["units"], status="found", matched=query,
                           source=f"product pages via switch-mapper fetcher ({agree})",
                           confidence="high" if web["votes"] >= 2 and len(web["all_votes"]) == 1 else "medium",
                           note="; ".join(web["pages"][:3]))
                return out
    if rec is None:
        est = estimate_from_name(make, model)
        if est:
            out.update(units=est["units"], status="estimated", matched=query, confidence="estimate",
                       source=f"estimate from the model name ({est['reason']})",
                       note="no online source could be reached for this model; check the height")
            return out
        out["note"] = out["note"] or "the specs agent and product pages have no record of this model"
        return out

    out["matched"] = " ".join(x for x in (rec.get("vendor"), rec.get("model")) if x)
    out["confidence"] = (resp or {}).get("confidence")
    ru = rec.get("rack_units")
    if isinstance(ru, int) and 1 <= ru <= MAX_TRUSTED_U:
        out.update(units=ru, status="found")
    elif ru == 0:
        out.update(status="desktop", note="the specs agent lists it as a desktop (non rack-mount) unit")
    else:
        est = estimate_from_name(make, model)
        if est:
            out.update(units=est["units"], status="estimated", confidence="estimate",
                       source=f"estimate from the model name ({est['reason']}); the specs agent knows the model "
                              f"but has no height for it",
                       note="check the height")
        else:
            out.update(status="no-height", note="the specs agent knows this model but has no rack height for it"
                       + (f" (got {ru!r}, not trusted)" if ru is not None else ""))
    return out
