"""The specification of an identified device: its ports by type, rack units and data sheet link,
looked up at run time for the make / model an agent identified (switch, router, firewall, load
balancer, gateway). One copy per agent folder, all identical (like switch_identify.py).

    lookup(vendor, model, device, log=print) -> spec dict, always (found False when none)
    compare(spec, ports) -> how the spec's port counts agree with the ports mapped on the photo
    add_spec(result, device, log=print) -> the result with its 'spec' field

Where the spec comes from, first answer wins:
  1. switches only: the switch catalogue's port counts (read only), then RackTrack's spec
     database (switches.db, read-only)
  2. the model's pages on the vendor's own site, found without any search key:
     a. pages and data sheets the template step opened in this run (note_page), and
     b. vendor rules: fixed addresses (MikroTik product page, Ubiquiti store / tech specs,
        Fortinet and Palo Alto data-sheet PDFs) and the vendor's own site search (TP-Link,
        NETGEAR, D-Link)
  3. optional, only when a key is set: a data-sheet search through the Brave Search API
     (BRAVE_SEARCH_API_KEY, or GOOGLE_CSE_KEY + GOOGLE_CSE_CX; environment or search_keys.env).
The port rows of the pages' spec tables are read (HTML tables, definition lists, PDF tables and
lines; a series data sheet in the model's column). Pages that never name the model are skipped.
Nothing is written into the agent folder: pages and answers are cached in the run's cache
(PORT_AGENT_CACHE_DIR), else in cache/device_spec beside this file. PORT_AGENT_SPEC=0 turns the
lookup off (the result then says so).

Port types use the result's names: rj45, sfp (SFP / SFP+ / SFP28 cages), qsfp, combo (RJ45 / SFP
shared), console, usb, management_port; 'other' counts serial / DSL / module slots.
"""
import concurrent.futures
import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

HERE = Path(__file__).resolve().parent
SPEC_DBS = [HERE / "spec_data" / "switches.db",                                  # package
            Path.home() / "Downloads" / "RT-EOS" / "switch_specs" / "data" / "switches.db"]
# the agent's own key file; on the development machine the switch agent (port_overlay 2) also reads
# the router / appliance agents' file
KEY_FILES = [HERE / "search_keys.env", HERE.parent / "port_overlay" / "search_keys.env"]
DEADLINE = 40            # seconds for one lookup (search, pages, parsing)
MAX_PAGES = 5
PARSE_VERSION = 4        # cached pages hold parsed rows; a parser change bumps this
NOUN = {"switch": "switch", "router": "router", "firewall": "firewall", "load_balancer": "load balancer",
        "gateway": "gateway"}
HEADERS = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
           "Accept-Language": "en-US,en;q=0.9"}
JUNK = ("ebay.", "amazon.", "reddit.", "youtube.", "facebook.", "twitter.", "pinterest.", "aliexpress.",
        "alibaba.", "linkedin.", "wikipedia.")


def _cache_dir():
    root = os.environ.get("PORT_AGENT_CACHE_DIR")
    d = Path(root) / "device_spec" if root else HERE / "cache" / "device_spec"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cached(name, fn):
    f = _cache_dir() / (hashlib.sha1(name.encode()).hexdigest()[:20] + ".json")
    if f.exists():
        try:
            return json.loads(f.read_text())
        except ValueError:
            pass
    v = fn()
    if v:
        f.write_text(json.dumps(v))
    return v


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


# ------------------------------------------------------------------ search
def _keys():
    """Search keys from the environment, else from search_keys.env (read here only: the
    environment is not changed, so the template search behaves as before)."""
    keys = {k: os.environ[k] for k in ("BRAVE_SEARCH_API_KEY", "GOOGLE_CSE_KEY", "GOOGLE_CSE_CX")
            if os.environ.get(k)}
    for f in KEY_FILES:
        if f.exists():
            for line in f.read_text().splitlines():
                k, _, v = line.partition("=")
                if k.strip() and v.strip() and not line.lstrip().startswith("#"):
                    keys.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return keys


def can_search():
    k = _keys()
    return bool(k.get("BRAVE_SEARCH_API_KEY") or (k.get("GOOGLE_CSE_KEY") and k.get("GOOGLE_CSE_CX")))


def web_search(query):
    """[(url, title)] for a query: Brave, else Google; [] when neither key is set."""
    def run():
        k = _keys()
        try:
            if k.get("BRAVE_SEARCH_API_KEY"):
                r = requests.get("https://api.search.brave.com/res/v1/web/search",
                                 params={"q": query, "count": 20},
                                 headers={"X-Subscription-Token": k["BRAVE_SEARCH_API_KEY"],
                                          "Accept": "application/json"}, timeout=15)
                if r.ok:
                    return [[x.get("url", ""), re.sub(r"<[^>]+>", "", x.get("title", ""))]
                            for x in (r.json().get("web") or {}).get("results", [])
                            if x.get("url", "").startswith("http")]
            if k.get("GOOGLE_CSE_KEY") and k.get("GOOGLE_CSE_CX"):
                r = requests.get("https://www.googleapis.com/customsearch/v1",
                                 params={"q": query, "key": k["GOOGLE_CSE_KEY"], "cx": k["GOOGLE_CSE_CX"]},
                                 timeout=15)
                if r.ok:
                    return [[x.get("link", ""), x.get("title", "")] for x in r.json().get("items", [])]
        except (requests.RequestException, ValueError):
            pass
        return []
    return [tuple(x) for x in _cached("q:" + query, run) or []]


def _vendor_word(vendor):
    """The brand word a vendor's domain carries ("Palo Alto Networks" -> paloalto)."""
    words = re.findall(r"[a-z0-9]+", (vendor or "").lower())
    drop = {"networks", "network", "inc", "technologies", "technology", "systems", "communications", "co", "ltd"}
    kept = [w for w in words if w not in drop] or words
    return "".join(kept)


OWN_DOMAINS = {"ubiquiti": ("ui.com", "ubnt.com"), "f5": ("f5.com",), "hpe": ("arubanetworks.com",),
               "aruba": ("arubanetworks.com", "hpe.com"), "progresskemp": ("kemptechnologies.com", "progress.com")}


def _official(url, vendor):
    host = urlparse(url).netloc.lower()
    w = _vendor_word(vendor)
    if any(host == d or host.endswith("." + d) for d in OWN_DOMAINS.get(w, ())):
        return True
    first = re.findall(r"[a-z0-9]+", (vendor or "").lower())[:1]
    return bool(w) and (w in _norm(host) or (first and len(first[0]) >= 4 and first[0] in host))


def _names_model(text, model):
    return _norm(model) in _norm(text)


def _rank(url, title, vendor, model):
    u, t = url.lower(), (title or "").lower()
    s = 0
    if _official(url, vendor):
        s += 20
    if _names_model(urlparse(url).path, model) or _names_model(title, model):
        s += 10
    if u.split("?")[0].endswith(".pdf"):
        s += 5
    if re.search(r"data-?sheet|datasheet|spec|technical", u + " " + t):
        s += 5
    if any(j in urlparse(u).netloc for j in JUNK):
        s -= 40
    return s


# ------------------------------------------------------------------ pages the template step opened
SEEN = {}                # url -> its HTML text or PDF bytes (None: fetch it), in the order opened
MAX_SEEN = 80              # pages kept per run (HTML over 3 MB is kept as its address only)


def note_page(url, content=None):
    """Called by the template step for each vendor page / data sheet it opens, so the spec can be
    read from it with no search (and from its copy when the site blocks plain requests)."""
    try:
        if not url or len(SEEN) >= MAX_SEEN:
            return
        if isinstance(content, (bytes, bytearray)) and (content[:4] != b"%PDF" or len(content) > 25_000_000):
            content = None
        if not isinstance(content, (str, bytes, bytearray)) or not content or (isinstance(content, str)
                                                                            and len(content) > 3_000_000):
            content = None
        if content is not None or url not in SEEN:
            SEEN[url] = content
    except Exception:          # noqa: BLE001 - never disturbs the caller
        pass


# ------------------------------------------------------------------ pages -> rows
def _parse(content, content_type=""):
    if isinstance(content, (bytes, bytearray)) and (content[:4] == b"%PDF" or "pdf" in content_type):
        return {"kind": "pdf", "rows": _pdf_rows(bytes(content))}
    text = content.decode("utf-8", "replace") if isinstance(content, (bytes, bytearray)) else content
    return {"kind": "html", "rows": _html_rows(text)}


def _fetch(url, content=None):
    def run():
        if content is not None:
            return _parse(content)
        try:
            r = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        except requests.RequestException:
            return None
        if r.status_code != 200 or len(r.content) > 25_000_000:
            return None
        return _parse(r.content, r.headers.get("content-type", ""))
    return _cached(f"p{PARSE_VERSION}:" + url, run)


def _clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _table_rows(table):
    """[(key, value, [header cells])] of one table (list of rows of cell texts). A table with one
    column per model keeps all value cells with the header row, for the model column to be picked."""
    out, header = [], None
    for cells in table:
        cells = [_clean(c) for c in cells if c is not None]
        if len(cells) < 2 or not any(cells):
            continue
        if sum(1 for c in cells[1:] if re.search(r"[a-z]", c, re.I) and re.search(r"\d", c)
               and len(c) < 30 and " " not in c.strip()) >= 2 and not re.search(r"\d\s*[x×]", " ".join(cells)):
            header = cells
            continue
        out.append((cells[0], cells[1:], header))
    return out


def _html_rows(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "svg"]):
        t.decompose()
    rows = []
    for table in soup.find_all("table"):
        rows += _table_rows([[c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
                             for tr in table.find_all("tr")])
    for dl in soup.find_all("dl"):
        for dt in dl.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd:
                rows.append((_clean(dt.get_text(" ")), [_clean(dd.get_text(" "))], None))
    rows += _line_rows(soup.get_text("\n").splitlines())
    # spec tables kept as data in the page (Ubiquiti tech specs: {"value": "9", "feature": {"label": "GbE RJ45"}})
    for value, label in re.findall(r'"value":"((?:[^"\\]|\\.){1,120})","feature":\{[^{}]{0,300}?"label":"([^"]{1,60})"', html):
        rows.append((label, [_clean(value.replace("\\n", " "))], None))
    return [[k, v, h] for k, v, h in rows]


def _line_rows(lines):
    """Rows from loose text lines: 'key: value' lines, other lines as text, and a label line
    followed by a count line ("SFP ports" / "1", "I/O" / "(4) 10/100/1000") as a key / value pair."""
    lines = [l for l in (_clean(x) for x in lines) if l]
    rows = []
    for i, line in enumerate(lines):
        if not 1 < len(line) < 200:
            continue
        k, sep, v = line.partition(":")
        rows.append((k, [v], None) if sep and len(k) < 60 and v.strip() else ("", [line], None))
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if (not sep and len(line) <= 60 and 0 < len(nxt) <= 40 and PORTISH.search(line)
                and not COUNT.search(line) and not re.match(r"\d+\.\s", line) and re.match(r"\(?\d", nxt)):
            rows.append((line, [nxt], None))
    return rows


def _pdf_rows(data):
    try:
        import fitz
    except ImportError:
        return []
    rows = []
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:          # noqa: BLE001 - an unreadable PDF is just no rows
        return []
    for page in list(doc)[:20]:
        try:
            for t in page.find_tables().tables:
                rows += _table_rows([[c or "" for c in r] for r in t.extract()])
        except Exception:      # noqa: BLE001
            pass
        rows += _line_rows(page.get_text().splitlines())
    return [[k, v, h] for k, v, h in rows]


# ------------------------------------------------------------------ rows -> ports
PORTISH = re.compile(r"port|interface|i/o|connector|ethernet|\blan\b|\bwan\b|\bdmz\b|sfp|qsfp|rj-?45|console|"
                     r"\busb\b|uplink|downlink|base-?t|gigabit|\bge\b|\bfe\b|copper|mgmt|management|combo|10/100",
                     re.I)
NOT_PORTS = re.compile(r"poe|speed|throughput|capacity|bandwidth|power|budget|buffer|latency|mac\b|vlan|jumbo|"
                       r"rate|led|indicator|standard|protocol|feature|security|vpn|session|support|per port|"
                       r"table|queue|memory|flash|storage|temperature|dimension|weight|mtbf|certif|complian|"
                       r"aggregat|\blag\b|trunk|mirror|cable|distance|transceiver|optic|module type|wavelength|"
                       r"isolation|surge|protection|warranty|price|accessor|included|kit|licen|bypass|"
                       r"up to|maximum|max\.?\b|optional|expansion slot|sold separately", re.I)
COUNT = re.compile(r"(?:^|[\s,;(])(\d{1,3})\s*[x×]\s*(?=\S)|\((\d{1,3})\)\s*(?=\S)|"
                   r"(?:^|[\s,;])(\d{1,3})\s*(?:-|\s)?(?:port|ports)\b|"
                   r"(?:^|[\s,;])(\d{1,3})\s+(?=(?:10/100|gigabit|gbe|ge\b|fe\b|fast ethernet|sfp|qsfp|rj-?45|"
                   r"combo|wan|lan|dmz|console|usb|1g|2\.5g|10g|25g|40g|100g|10gbe|multi-?gig|ethernet))|"
                   r"(?:^|[\s,;])(\d{1,3})\s+(?=[a-z]+\s+(?:sfp|qsfp|rj-?45|combo|console|usb)\S*\s+(?:port|slot)s?\b)",
                   re.I)


def _kind(text):
    t = text.lower()
    if "combo" in t:
        return "combo"
    if "qsfp" in t:
        return "qsfp"
    if "sfp" in t or "mini-gbic" in t or "minigbic" in t or "fiber" in t or "fibre" in t:
        return "sfp"
    if "console" in t or re.search(r"rj-?45 serial|serial \(?rj-?45", t):
        return "console"
    if re.search(r"\busb\b|usb[- ]?c|usb\s*\d", t):
        return "usb"
    if re.search(r"management|\bmgmt\b|out-of-band|\boob\b", t):
        return "management_port"
    if re.search(r"serial|t1/e1|\bt1\b|\be1\b|\bdsl|vdsl|adsl|fxs|fxo|isdn|\bslot|\bnim\b|module|expansion|"
                 r"sim\b|antenna|\bdc\b|power", t):
        return "other"
    if re.search(r"rj-?45|base-?t|10/100|gigabit|gbe|\bge\b|\bfe\b|fast ethernet|ethernet|copper|lan|wan|dmz|"
                 r"multi-?gig|\bports?\b|\d\s*g\b", t):
        return "rj45"
    return None


def _role(text):
    t = text.lower()
    if re.search(r"\bwan\b", t) and re.search(r"\blan\b", t):
        return "wan/lan"                       # ports that can be either (TP-Link "4 LAN/WAN ports")
    for role, pat in (("wan", r"\bwan\b"), ("dmz", r"\bdmz\b"), ("ha", r"\bha\b|high availability"),
                      ("uplink", r"uplink"), ("stacking", r"stack"), ("lan", r"\blan\b|internal|switch ports?"),
                      ("poe", r"\bpoe\b")):
        if re.search(pat, t):
            return role
    return ""


def _speed(text):
    t = text.lower()
    for s, pat in (("100g", r"100\s*g"), ("40g", r"40\s*g"), ("25g", r"25\s*g|sfp28"), ("10g", r"10\s*g|sfp\+|10gbe"),
                   ("5g", r"\b5\s*g"), ("2.5g", r"2\.5\s*g"), ("1g", r"1000|gigabit|\bge\b|\b1\s*g\b|gbe"),
                   ("100m", r"10/100\b|fast ethernet|\bfe\b")):
        if re.search(pat, t):
            return s
    return None


def _row_ports(key, value):
    """[(kind, role, speed, count, text)] a spec row describes. A value is cut at every count, so
    "24 10/100/1000 RJ45 Ports 4 Combo SFP Slots 1 Console Port" gives three entries and
    "10x GE RJ45 (including 7x Internal, 2x WAN, 1x DMZ)" a total plus its roles."""
    if not PORTISH.search(key + " " + value) or NOT_PORTS.search(key):
        return []
    out = []
    v = value.strip()
    nums = re.findall(r"\d{1,3}", v) if re.fullmatch(r"[\d\s/()]+", v) else []
    parts = re.split(r"\s*/\s*(?=[A-Za-z])", key)
    if key and len(nums) > 1 and len(parts) == len(nums):                    # "WAN / DMZ ports | 2 / 1"
        for part, n in zip(parts, nums):
            k = _kind(part) or _kind(key)
            if k and int(n):
                out.append((k, _role(part), _speed(key), int(n), f"{key}: {v}"))
        return out
    if re.fullmatch(r"\(?\d{1,3}\)?(\s*ports?)?(\s*\([^)]*\))?", v, re.I) and key:   # "GE RJ45 ports | 8"
        k = _kind(key)
        if k:
            out.append((k, _role(key), _speed(key), int(re.search(r"\d+", v).group()), f"{key}: {v}"))
        return out
    ms = list(COUNT.finditer(v))
    tail = re.fullmatch(r"(.{2,60}?)\s*\((\d{1,3})\)", v)
    if not ms and tail:                         # "I/O | 10/100/1000 (8)", "RJ-45 console port (1)"
        k = _kind(tail.group(1)) or _kind(key)
        if k and 0 < int(tail.group(2)) <= 128:
            out.append((k, _role(key + " " + tail.group(1)), _speed(tail.group(1)) or _speed(key),
                        int(tail.group(2)), f"{key}: {v}" if key else v))
        return out
    if not ms:          # "Management I/O | 10/100 out-of-band management port": one port, named singly
        k = _kind(v) or _kind(key)
        if k in ("console", "usb", "management_port") and len(v) < 80 and re.search(
                r"(console|usb|management|mgmt|out-of-band|serial)[^.,;•]{0,25}\bport\b(?!s)", key + " " + v, re.I):
            out.append((k, "", None, 1, f"{key}: {v}" if key else v))
        return out
    for i, m in enumerate(ms):
        n = int(next(g for g in m.groups() if g))
        desc = re.split(r"[;,]|\binclud", v[m.end():ms[i + 1].start() if i + 1 < len(ms) else len(v)])[0]
        before = v[max(0, m.start() - 12):m.start()]
        if (NOT_PORTS.search(desc) and not re.search(r"port|slot|interface", desc, re.I)) \
                or re.search(r"up to|max", before, re.I):
            continue
        head = " ".join(desc.split()[:5])       # the type follows the count ("24-Port Gigabit ... with 2 SFP")
        k = _kind(head) or _kind(key)
        if k in ("rj45", "sfp") and re.search(r"management|\bmgmt\b|out-of-band", key, re.I):
            if n > 2:
                continue                       # "Management interface: (4) Ethernet ports" - managed through them
            k = "management_port"
        if not k or not 0 < n <= 128:
            continue
        out.append((k, _role(key + " " + desc), _speed(desc) or _speed(key), n,
                    f"{key}: {m.group(0).strip()} {desc.strip()}" if key else f"{m.group(0).strip()} {desc.strip()}"))
    return out


def _combine(entries):
    """{kind: count} from row entries: one row per (kind, role, speed) (the largest, so a port
    described twice is counted once); unknown speed vs the sum of the known speeds counts the
    larger. Rows without a role are the total when they reach the sum of the roles (10x GE RJ45
    = 2 WAN + 1 DMZ + 7 internal), else one more group (2x FortiLink beside WAN / DMZ / internal)."""
    best = {}
    for k, role, sp, n, _ in entries:
        best[(k, role, sp)] = max(best.get((k, role, sp), 0), n)
    out = {}
    for k in {e[0] for e in best}:
        roles = {}
        for role in {r for kk, r, _ in best if kk == k}:
            known = sum(n for (kk, r, sp), n in best.items() if kk == k and r == role and sp)
            roles[role] = max(known, best.get((k, role, None), 0))
        generic = roles.pop("", 0)
        poe = roles.pop("poe", 0)                    # PoE ports are a subset of the ports
        named = sum(roles.values())
        out[k] = max(generic if generic >= named else named + generic, poe)
    return out


def _model_column(header, model):
    if not header:
        return None
    n = _norm(model)
    hits = [i for i, c in enumerate(header[1:]) if _norm(c) and (_norm(c) == n or _norm(c) in n or n in _norm(c))]
    exact = [i for i in hits if _norm(header[i + 1]) == n]
    return (exact or hits or [None])[0]


def ports_from_rows(rows, model):
    """({kind: count}, [the rows used]) from one page's rows. A table with a column per model is
    read in the model's column only; a page with spec-table rows ignores loose text lines."""
    table, loose = [], []
    for key, values, header in rows:
        if header:
            col = _model_column(header, model)
            if col is None or col >= len(values):
                continue
            value = values[col]
        else:
            value = " ".join(values)
        found = _row_ports(key, value)
        (table if key else loose).extend(found)
    data = ("rj45", "sfp", "qsfp", "combo")
    # loose text lines add only single ports (console, USB, management) once a table gave the network ports
    entries = (table + [e for e in loose if e[0] not in data]) if any(e[0] in data for e in table) else table + loose
    return _combine(entries), [e[4] for e in entries][:12]


def _rack_units(rows):
    """Rack units from a form-factor row, else from text such as "1U rackmount"; 'desktop' for a
    desktop unit."""
    for key, values, _ in rows:
        text = key + " " + " ".join(values)
        if re.search(r"rack|form factor|height|mount|chassis|dimension", key, re.I):
            m = re.search(r"\b(\d(?:\.\d)?)\s*-?\s*(?:ru|u)\b", text, re.I)
            if m:
                return float(m.group(1)) if "." in m.group(1) else int(m.group(1))
            if re.search(r"desktop", text, re.I):
                return "desktop"
    for key, values, _ in rows:
        m = re.search(r"\b(\d(?:\.\d)?)\s*-?\s*(?:ru|u)\b\s*(?:rack|high|height|form|chassis|1\.75)", " ".join(values), re.I)
        if m:
            return float(m.group(1)) if "." in m.group(1) else int(m.group(1))
    return None


# ------------------------------------------------------------------ switch catalogue (offline)
SWITCH_EXCEL = HERE / "switch_catalog" / "Switch_Vendors_and_Models.xlsx"
_EXCEL = {}


def workbook_ports(text):
    """{kind: count} from the vendor workbooks' port text, written type first:
    "SFP+ - 4 ETHERNET - 24 RJ45 SERIAL CONSOLE Port - 1", "SFP28 (25 Gbps) - 48 QSFP28 (100 Gbps) - 8"."""
    entries = []
    for m in re.finditer(r"(.+?)\s*[-:]\s*(\d{1,3})(?=[\s(]|$)", text or ""):
        k = _kind(m.group(1))
        if k and int(m.group(2)):
            entries.append((k, m.group(1).strip(), _speed(m.group(1)), int(m.group(2)), m.group(0).strip()))
    out = {}
    for k, _, _, n, _ in entries:                     # each item is its own group: they add up
        out[k] = out.get(k, 0) + n
    return out, [e[4] for e in entries]


def _from_switch_excel(vendor, model):
    """The switch catalogue row of the model (read only): ports by type from its workbook port
    text, else only its total port count."""
    if not SWITCH_EXCEL.exists():
        return None
    if not _EXCEL:
        import openpyxl
        wb = openpyxl.load_workbook(SWITCH_EXCEL, read_only=True)
        rows = wb["Switches"].iter_rows(values_only=True)
        head = next(rows)
        col = {h: i for i, h in enumerate(head)}
        for r in rows:
            if r[col["Model"]]:
                _EXCEL.setdefault(_norm(r[col["Model"]]), []).append(
                    (str(r[col["Vendor"]] or ""), r[col["Total ports"]], r[col["Ports (workbook)"]]))
        wb.close()
    cands = _EXCEL.get(_norm(model)) or []
    v = _vendor_word(vendor)
    cands = [c for c in cands if not v or v in _norm(c[0]) or _vendor_word(c[0]) in _norm(vendor)] or cands
    for vend, total, text in cands:
        ports, used = workbook_ports(text)
        if any(ports.get(k) for k in ("rj45", "sfp", "qsfp", "combo")):
            return {"ports": ports, "rows": used, "total_ports": total, "source": "switch catalogue (vendor workbook)",
                    "confidence": 0.8}
    for vend, total, text in cands:
        if isinstance(total, (int, float)) and total > 0:
            return {"ports": {}, "rows": [f"Total ports: {int(total)}"], "total_ports": int(total),
                    "source": "switch catalogue (total port count only)", "confidence": 0.6}
    return None


def port_config(ports):
    names = {"rj45": "RJ45", "sfp": "SFP", "qsfp": "QSFP", "combo": "combo RJ45/SFP", "console": "console",
             "usb": "USB", "management_port": "management", "other": "other (serial / DSL / slots)"}
    return " + ".join(f"{ports[k]}x {names[k]}" for k in names if ports.get(k))


# ------------------------------------------------------------------ lookup
def _from_db(vendor, model):
    for db in SPEC_DBS:
        if not db.exists():
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            rows = con.execute("SELECT * FROM switches").fetchall()
            con.close()
        except sqlite3.Error:
            continue
        n = _norm(model)
        for r in rows:
            if n and n in (_norm(r["model"]), _norm(r["sku"])):
                ports, used = ports_from_rows([["Ports", [r["port_config"] or ""], None],
                                               ["Uplinks", [r["uplink_config"] or ""], None]], model)
                if ports:
                    return {"ports": ports, "rows": used, "rack_units": r["rack_units"],
                            "datasheet_url": r["datasheet_url"], "source": "RackTrack spec database",
                            "confidence": 0.9}
        return None
    return None


# ------------------------------------------------------------------ vendor rules (no search key)
UBIQUITI_TECHSPECS = ("unifi/switching", "unifi/cloud-gateways", "uisp/wired", "unifi/integrations",
                      "uisp/fiber", "unifi/power-tech")
SITE_SEARCH = {          # the vendor's own site search, answered as plain HTML
    "tplink": "https://www.tp-link.com/us/search/?q={q}&t=product",
    "netgear": "https://www.netgear.com/search/?q={q}",
    "dlink": "https://www.dlink.com/en/search?q={q}",
}


def _site_search(vendor_key, model):
    """Product pages the vendor's own site search lists for the model (the last part of the
    address is the model, as TP-Link /er7206/, NETGEAR /gs724t/, D-Link /dgs-1210-28mp-28-port-...)."""
    from urllib.parse import quote_plus, urljoin
    url = SITE_SEARCH[vendor_key].format(q=quote_plus(model))

    def run():
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
        except requests.RequestException:
            return []
        if r.status_code != 200:
            return []
        n = _norm(model)
        out = []
        for h in re.findall(r'href="([^"#?]+)"', r.text):
            last = [x for x in urlparse(h).path.split("/") if x][-1:] or [""]
            if re.search(r"support|download|compare|accessor|faq|document|licen|subscription|\bkb\.", h, re.I) \
                    or urlparse(urljoin(r.url, h)).netloc != urlparse(r.url).netloc:
                continue                       # product pages on the vendor's main site only
            ln = _norm(last[0])
            if ln == n or re.fullmatch(re.escape(n) + r"v\d+", ln) or (ln.startswith(n) and re.match(
                    re.escape(model.lower().replace(" ", "-")) + r"-", last[0].lower())):
                out.append(urljoin(r.url, h))
        if not out:             # a series page (D-Link des-1210-series-...): its table has a column per model
            fam = re.match(r"[a-z]+-?\d+", model.lower())
            out = [urljoin(r.url, h) for h in re.findall(r'href="([^"#?]+)"', r.text)
                   if fam and "series" in h.lower() and fam.group(0) in h.lower()
                   and not re.search(r"support|download", h, re.I)]
        return list(dict.fromkeys(out))[:2]
    return _cached("s2:" + url, run) or []


def vendor_pages(vendor, model):
    """The model's pages on the vendor's own site, from rules that need no search engine."""
    v, m = _vendor_word(vendor), (model or "").strip()
    slug = re.sub(r"[^a-z0-9+]+", "-", m.lower()).strip("-")
    out = []
    if v == "mikrotik":
        out.append("https://mikrotik.com/product/" + re.sub(r"\s+", "", m).replace("+", "plus"))
    elif v in ("ubiquiti", "ui", "ubnt"):
        s = slug.replace("+", "")
        out += [f"https://store.ui.com/us/en/products/{s}"] + [f"https://techspecs.ui.com/{c}/{s}"
                                                               for c in UBIQUITI_TECHSPECS]
    elif v == "fortinet":
        f = re.search(r"(?:FG|FWF|FortiGate|FortiWiFi)[-\s]*(\d+)([A-Z])", m, re.I)
        if f:
            x = f"{f.group(1)}{f.group(2).lower()}"
            base = "https://www.fortinet.com/content/dam/fortinet/assets/data-sheets/"
            out += [f"{base}fortigate-fortiwifi-{x}-series.pdf", f"{base}fortigate-{x}-series.pdf"]
    elif v == "paloalto":
        f = re.match(r"PA-?(\d+)$", m, re.I)
        if f:
            out.append(f"https://www.paloaltonetworks.com/content/dam/pan/en_US/assets/pdf/datasheets/"
                       f"pa-{f.group(1)}/pa-{f.group(1)}.pdf")
    key = {"tplink": "tplink", "netgear": "netgear", "dlink": "dlink"}.get(v)
    if key:
        out += _site_search(key, m)
    return out


def lookup(vendor, model, device="switch", log=print, deadline=DEADLINE):
    """The spec of vendor / model (always a dict; found False with a reason when there is none)."""
    base = {"found": False, "vendor": vendor, "model": model, "device": device}
    if os.environ.get("PORT_AGENT_SPEC", "1") == "0":
        return {**base, "reason": "spec lookup turned off (PORT_AGENT_SPEC=0)"}
    if not model:
        return {**base, "reason": "no model identified on the photo (pass the model to look its spec up)"}
    t0 = time.time()
    hit = total_only = None
    if device == "switch":
        hit = _from_switch_excel(vendor, model)
        if hit and not hit["ports"]:
            hit, total_only = None, hit            # a total alone: the web may give the types
        hit = hit or _from_db(vendor, model)
    if not hit:
        hit = _from_web(vendor, model, device, log, t0 + deadline)
    hit = hit or total_only
    if not hit:
        return {**base, "reason": "no page of the model with port details found on the vendor's site"
                + ("" if can_search() else " (a search key, optional, would also search the web)")}
    ports = hit["ports"]
    out = {**base, "found": True, "ports": {k: ports[k] for k in sorted(ports)},
           "data_ports": sum(ports.get(k, 0) for k in ("rj45", "sfp", "qsfp", "combo")) or None,
           "total_ports": hit.get("total_ports") or sum(ports.values()),
           "port_config": port_config(ports) or f"{hit['total_ports']} ports (types not listed)",
           "rack_units": hit.get("rack_units"), "datasheet_url": hit.get("datasheet_url"), "source": hit["source"],
           "confidence": hit["confidence"], "spec_rows": hit["rows"]}
    if hit.get("agreeing_sources"):
        out["agreeing_sources"] = hit["agreeing_sources"]
    log(f"  spec: {out['port_config']}  ({out['source']}, {time.time() - t0:.1f} s)")
    return out


def _candidates(vendor, model, device, stop):
    """Pages to read, best first: those the template step opened that name the model, the vendor
    rules' pages, then (only with a search key) search results."""
    seen = [u for u, c in SEEN.items() if _names_model(urlparse(u).path, model)
            or (isinstance(c, str) and _names_model(c[:400_000], model))
            or (isinstance(c, (bytes, bytearray)) and _norm(model) in _norm(c[:3_000_000].decode("latin-1")))]
    seen.sort(key=lambda u: -_rank(u, "", vendor, model))
    urls = seen[:MAX_PAGES] + vendor_pages(vendor, model)
    if can_search():
        noun = NOUN.get(device, device or "")
        hits = {}
        for q in (f"{vendor} {model} datasheet", f"{vendor} {model} {noun} specifications"):
            for url, title in web_search(q):
                hits.setdefault(url, title)
            if sum(1 for u, t in hits.items() if _rank(u, t, vendor, model) >= 25) >= 2 or time.time() > stop:
                break
        ranked = sorted(hits, key=lambda u: -_rank(u, hits[u], vendor, model))
        urls += [u for u in ranked if _rank(u, hits[u], vendor, model) > 0][:MAX_PAGES]
    return list(dict.fromkeys(urls))[:MAX_PAGES + 8]


def _from_web(vendor, model, device, log, stop):
    ranked = _candidates(vendor, model, device, stop)
    if not ranked:
        return None
    pages = {}
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_PAGES)
    futs = {ex.submit(_fetch, u, SEEN.get(u)): u for u in ranked}
    try:
        for f in concurrent.futures.as_completed(futs, timeout=max(5, stop - time.time())):
            try:
                if f.result():
                    pages[futs[f]] = f.result()
            except Exception:          # noqa: BLE001 - a page that fails is skipped
                pass
    except concurrent.futures.TimeoutError:
        log("  spec: some data-sheet pages did not load in time")
    ex.shutdown(wait=False, cancel_futures=True)      # the run does not wait for slow pages
    found = []
    for url, page in pages.items():
        rows = page["rows"]
        text = " ".join(k + " " + " ".join(v) for k, v, _ in rows[:4000])
        if not _names_model(text, model) and not _names_model(url, model):
            continue                   # a page that never names the model describes another one
        ports, used = ports_from_rows(rows, model)
        if not any(ports.get(k) for k in ("rj45", "sfp", "qsfp", "combo")):
            continue
        official = _official(url, vendor)
        conf = (0.9 if official and page["kind"] == "pdf" else 0.85 if official else
                0.65 if page["kind"] == "pdf" else 0.55)
        if not _names_model(urlparse(url).path, model):
            conf -= 0.1                # a series page or data sheet: the model's column / rows were read
        src = ("vendor data sheet (PDF)" if official and page["kind"] == "pdf" else
               "vendor web page" if official else "web data sheet (PDF, not the vendor's site)"
               if page["kind"] == "pdf" else "web page (not the vendor's site)")
        found.append({"ports": ports, "rows": used, "rack_units": _rack_units(rows), "datasheet_url": url,
                      "source": src, "confidence": round(conf, 2), "_rank": ranked.index(url)})
    if not found:
        return None
    key = lambda h: tuple(h["ports"].get(k, 0) for k in ("rj45", "sfp", "qsfp", "combo"))
    for h in found:                    # sources giving the same network ports confirm each other
        same = [o["datasheet_url"] for o in found if o is not h and key(o) == key(h)]
        h["agreeing_sources"] = same
        h["confidence"] = round(min(0.98, h["confidence"] + 0.05 * len(same)), 2)
    best = max(found, key=lambda h: (h["confidence"], -h["_rank"]))
    if best["rack_units"] is None:
        best["rack_units"] = next((h["rack_units"] for h in found if h["rack_units"] is not None), None)
    best.pop("_rank")
    return best


# ------------------------------------------------------------------ against the photo
def compare(spec, ports):
    """How the spec's network ports compare with the ports mapped on the photo."""
    if not spec.get("found"):
        return None
    got = {}
    for p in ports or []:
        t = str(p.get("type") or "").lower()
        k = "sfp" if "sfp" in t else "rj45" if t in ("rj45", "ethernet", "wan", "lan") else None
        if k:
            got[k] = got.get(k, 0) + 1
    rj, sfp = got.get("rj45", 0), got.get("sfp", 0)
    sp = spec["ports"]
    if not spec.get("data_ports"):             # only a total (it may include console / USB)
        total = spec.get("total_ports") or 0
        ok = total - 3 <= rj + sfp <= total
        out = {"agrees": ok, "photo": {"rj45": rj, "sfp": sfp}, "spec": {"total_ports": total}}
        if not ok:
            out["note"] = f"the spec lists {total} ports in all; the photo was mapped with {rj} RJ45 and {sfp} SFP"
        return out
    want_rj, want_sfp = sp.get("rj45", 0), sp.get("sfp", 0) + sp.get("qsfp", 0)
    # a combo port is an RJ45 jack and an SFP cage sharing one port (both are on the panel); an
    # RJ45 management or console port may be mapped as RJ45
    combo, mgmt = sp.get("combo", 0), sp.get("management_port", 0) + sp.get("console", 0)
    ok = (want_rj <= rj <= want_rj + combo + mgmt and want_sfp <= sfp <= want_sfp + combo
          and want_rj + want_sfp <= rj + sfp <= want_rj + want_sfp + 2 * combo + mgmt)
    out = {"agrees": ok, "photo": {"rj45": rj, "sfp": sfp},
           "spec": {"rj45": want_rj, "sfp": want_sfp, **({"combo": combo} if combo else {})}}
    if not ok:
        cages = (f"{want_sfp} SFP" if not sp.get("qsfp") else
                 f"{want_sfp} SFP / QSFP ({sp.get('sfp', 0)} SFP + {sp['qsfp']} QSFP)")
        out["note"] = (f"the spec lists {want_rj} RJ45 and {cages}"
                       + (f" (+{combo} combo)" if combo else "")
                       + f"; the photo was mapped with {rj} RJ45 and {sfp} SFP")
    return out


def add_spec(result, device, log=print, read_model=None):
    """result['spec'] for the result's vendor / model (never raises; None for a run that failed).
    read_model: a model read clearly on the photo that the result leaves out (auto mode, no
    template fitted); its spec is looked up and says so in 'model_from'."""
    if result.get("error"):
        result["spec"] = None
        return result
    try:
        model = result.get("model") or read_model
        spec = lookup(result.get("vendor"), model, device or result.get("device") or "switch", log)
        if model and not result.get("model"):
            spec["model_from"] = "read on the photo; the result has no model because no template fitted"
        check = compare(spec, result.get("ports"))
        if check:
            spec["matches_photo"] = check
            if not check["agrees"]:
                log(f"  spec check: {check['note']}")
    except Exception as e:     # noqa: BLE001 - the spec never costs the caller its result
        spec = {"found": False, "vendor": result.get("vendor"), "model": result.get("model"),
                "reason": f"spec lookup failed: {type(e).__name__}: {e}"}
    result["spec"] = spec
    return result


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Look up a device's spec (ports by type) as the agents do.")
    ap.add_argument("vendor"); ap.add_argument("model")
    ap.add_argument("--device", default="switch", choices=list(NOUN))
    a = ap.parse_args()
    print(json.dumps(lookup(a.vendor, a.model, a.device), indent=1))
