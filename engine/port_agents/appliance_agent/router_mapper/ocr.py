"""Read a router's make and model off the photo - the switch mapper's OCR, routers only.

Same two stages as the switch pipeline:
  panel_ocr.read_panel   RapidOCR over the whole frame (both polarities), then the
                         area around the brand name re-read at several magnifications
                         (model numbers are printed far smaller than the logo)
  switch_identify        vendor from the logo and its aliases, model by OCR-confusion-
                         aware matching (5/S, 0/O, 1/I, 8/B ...), vendor-gated, with
                         faceplate-text corroboration
The only difference is the catalog: switch_identify is loaded with the ROUTER catalog
(replacing its built-in switch models, in this process only), so a router photo can only
match a router. The catalog holds every model, every Cisco product ID, and the names as
printed on the panel ("FortiGate 200F" for FG-200F, "EdgeRouter 6P" for ER-6P,
"CISCO 4321" for ISR4321), each mapped back to the catalog model.
"""
import json
import os
import re
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from router_mapper import catalog  # noqa: E402

ENHANCED = os.environ.get("OCR_ENHANCED", "0") == "1"     # off: measured 2026-10-16, no gain, one new error, 2x slower
OCR_BUDGET_S = float(os.environ.get("ROUTER_MAPPER_OCR_BUDGET", "12" if ENHANCED else "8"))
MODEL_MIN_SCORE = 0.75     # a catalogue match below this is not trusted ('CISCOSSL' -> CISCO857 scored 0.72)

# faceplate words per vendor beyond its name (logos, sub-brands, product lines)
VENDOR_WORDS = {
    "Cisco": ["CISCO", "CISCO SYSTEMS"], "Cisco Meraki": ["MERAKI"],
    "MikroTik": ["MIKROTIK", "ROUTERBOARD", "CLOUD CORE ROUTER"],
    "Ubiquiti": ["UBIQUITI", "UBNT", "EDGEROUTER", "EDGEMAX", "UNIFI"],
    "TP-Link": ["TP-LINK", "TPLINK", "OMADA", "SAFESTREAM"],
    "Fortinet": ["FORTINET", "FORTIGATE", "FORTIWIFI"], "Juniper": ["JUNIPER", "JUNIPER NETWORKS"],
    "DrayTek": ["DRAYTEK", "VIGOR"], "D-Link": ["D-LINK", "DLINK"], "Huawei": ["HUAWEI", "NETENGINE"],
    "H3C": ["H3C"], "Palo Alto Networks": ["PALO ALTO", "PALOALTO"], "SonicWall": ["SONICWALL"],
    "Sophos": ["SOPHOS"], "WatchGuard": ["WATCHGUARD", "FIREBOX"], "Netgate": ["NETGATE", "PFSENSE"],
    "Peplink": ["PEPLINK", "PEPWAVE"], "Zyxel": ["ZYXEL", "ZYWALL"], "Teltonika Networks": ["TELTONIKA"],
    "Check Point": ["CHECK POINT", "CHECKPOINT"], "NETGEAR": ["NETGEAR", "PROSAFE"], "Linksys": ["LINKSYS"],
    "ASUS": ["ASUS"], "Barracuda": ["BARRACUDA"], "Forcepoint": ["FORCEPOINT"], "Cradlepoint": ["CRADLEPOINT"],
    "Ruijie Networks": ["RUIJIE", "REYEE"], "Allied Telesis": ["ALLIED TELESIS"], "Yamaha": ["YAMAHA"],
    "LANCOM Systems": ["LANCOM"], "bintec elmeg": ["BINTEC", "ELMEG"], "Nokia": ["NOKIA"],
    "Ericsson": ["ERICSSON"], "Arista Networks": ["ARISTA"], "Extreme": ["EXTREME NETWORKS"],
    "ZTE": ["ZTE"], "Adtran": ["ADTRAN", "NETVANTA"], "HPE Aruba": ["ARUBA", "HPE"], "Silicom Ltd": ["SILICOM"],
    "Siemens": ["SIEMENS", "SCALANCE"], "MOXA": ["MOXA"], "Advantech Co. Ltd": ["ADVANTECH"],
    "Planet": ["PLANET"], "Araknis": ["ARAKNIS"], "Luxul": ["LUXUL"], "Stormshield": ["STORMSHIELD"],
    "Hillstone Networks": ["HILLSTONE"], "NEC": ["NEC", "UNIVERGE"], "Fujitsu": ["FUJITSU"],
    "Versa Networks": ["VERSA"], "VeloCloud (Arista; formerly VMware / Broadcom)": ["VELOCLOUD", "VMWARE"], "Ciena": ["CIENA"],
}


def printed_names(vendor, model):
    """Other ways the panel spells this model."""
    out = []
    m = re.match(r"^(FG|FWF|FGR)-(\w+)$", model)
    if m:
        out.append({"FG": "FortiGate ", "FWF": "FortiWiFi ", "FGR": "FortiGate Rugged "}[m.group(1)] + m.group(2))
    m = re.match(r"^ER-?(\w[\w-]*)$", model) if vendor == "Ubiquiti" else None
    if m:
        out.append("EdgeRouter " + m.group(1).replace("-XG", " Infinity").replace("-", " "))
    if vendor == "Ubiquiti":
        out += {"ERLite-3": ["EdgeRouter Lite"], "ERPro-8": ["EdgeRouter PRO"], "ER-8-XG": ["EdgeRouter Infinity"],
                "ERPoe-5": ["EdgeRouter PoE"]}.get(model, [])
    m = re.match(r"^ISR(4\d{3}(?:-X)?)$", model) if vendor == "Cisco" else None
    if m:
        out += [f"CISCO {m.group(1)}", f"ISR {m.group(1)}"]
    m = re.match(r"^CISCO(\d{3,5}\w*)$", model) if vendor == "Cisco" else None
    if m:
        out.append(f"Cisco {m.group(1)}")
    if vendor == "MikroTik" and model.startswith("RB"):
        out.append(model.replace("RB", "RouterBOARD ", 1))
    return out


@lru_cache(maxsize=1)
def ocr_catalog():
    """({vendor: {aliases, models}}, {squashed name: (vendor, catalog model)})."""
    import switch_identify as si
    try:                                          # switch vendors' OCR spellings (Switch_Vendors_and_Models.xlsx)
        switch_cat = si.read_switch_excel(ROOT / si.SWITCH_EXCEL)
    except (OSError, ValueError, KeyError):
        switch_cat = {}
    by_key = {catalog.vendor_key(v): e for v, e in switch_cat.items()}
    cat, names = {}, {}
    for r in catalog.rows():
        v, m = r["vendor"], r["model"]
        if "verify)" in m:                        # "PA-3500 series (models: verify)" is not a name
            continue
        e = cat.setdefault(v, {"aliases": [], "models": []})
        ids = [m] + [x.strip() for x in (r["variants"] or "").split(",") if x.strip()]
        for n in ids + printed_names(v, m):
            if n not in e["models"]:
                e["models"].append(n)
            # third field: a product ID of its own (C1161-8PLTEP) - not just the model as printed
            names.setdefault(si.squash(n), (v, m, n in ids))
    for v, e in cat.items():
        sw = by_key.get(catalog.vendor_key(v))
        al = [v] + VENDOR_WORDS.get(v, []) + (list(sw.get("aliases") or []) if isinstance(sw, dict) else [])
        e["aliases"] = list(dict.fromkeys(a for a in al if len(si.squash(a)) >= 3))
    return cat, names


_loaded = False


def load():
    """Load the router catalog into switch_identify, replacing its switch models."""
    global _loaded
    if _loaded:
        return
    import tempfile
    import switch_identify as si
    cat, _ = ocr_catalog()
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(cat, fh)
    si.load_catalog(fh.name, replace=True)
    os.unlink(fh.name)
    # router family patterns (switch_identify's are switch families)
    si.FAMILY_PATTERNS[:] = [(p, v) for p, v in [
        (r"CISCO\d{4}[A-Z]{0,2}", "Cisco"), (r"ISR\d{4}[A-Z]{0,2}", "Cisco"), (r"C1\d{3}[A-Z]?\d?P[A-Z]{0,6}", "Cisco"),
        (r"ASR\d{3,4}[A-Z]{0,2}", "Cisco"), (r"CCR\d{4}[A-Z0-9]{0,12}", "MikroTik"), (r"RB\d{3,4}[A-Z0-9]{0,8}", "MikroTik"),
        (r"SRX\d{3,4}", "Juniper"), (r"MX\d{3,5}", "Juniper"), (r"FG\d{2,4}[A-Z]{1,2}", "Fortinet"),
        (r"PA\d{3,4}", "Palo Alto Networks"), (r"VIGOR\d{4}[A-Z]{0,3}", "DrayTek"), (r"ER\d{3,4}[A-Z0-9]{0,3}", "TP-Link"),
        (r"DSR\d{3,4}[A-Z]{0,3}", "D-Link"), (r"AR\d{3,4}[A-Z]{0,2}", "Huawei"), (r"MSR\d{2,4}", "H3C")]]
    si.MIN_MODEL_LEN = 5          # router models are often short: MX204, ER605, RB750
    si.rebuild_model_index()
    _loaded = True


# brand words too generic to anchor a zoom on (they also appear as port or product words)
_NOT_ANCHORS = {"ASA", "SMART", "THUNDER", "QUANTUM", "VEGA", "ARRAY", "POLY", "TOTAL ACCESS", "SECURE FIREWALL",
                "EDGE", "GATEWAY", "SWITCH", "ROUTER", "CONSOLE"}


@lru_cache(maxsize=1)
def logo_words():
    """Every catalogue vendor's name and logo / product-line words, for anchoring the zoom."""
    cat, _ = ocr_catalog()
    words = set()
    for v, e in cat.items():
        for a in [v] + list(e.get("aliases") or []):
            a = re.sub(r"\(.*?\)", "", a).strip()
            if a and a.upper() not in _NOT_ANCHORS and (len(a) >= 3 or a.upper() in ("F5", "TP", "HP")):
                words.add(a)
    return sorted(words)


# letters OCR often returns for digits: a reading 'DSNIODOAC' still carries DSR-1000AC's four digits
_DIGIT_LIKE = set("0123456789ODQILSBZG")


def doubtful(evidence, name, vendor_names, text, aliases=()):
    """Why a catalogue match must not be trusted, or None. Measured on 50 router photos:
      - a series label read as a model ('CISCO 1900 SERIES' -> CISCO1905, '2800 Series' -> 2801):
        the reading is the start of the name, cut short, and the panel says SERIES;
      - a product line read as a model ('EDGEROUTER' -> ER-X on an EdgeRouter PRO): the reading is
        the start of several of the vendor's model names, so it names none of them;
      - most of the model number missing ('CISCO4AS' -> ISR4451-X on a 2811)."""
    e, n = _sq(evidence), _sq(name)
    if not e or not n:
        return None
    if n.startswith(e) and len(e) < len(n):
        if any("SERIES" in t.upper() for t in text):
            return f"'{evidence}' is a series label (the panel says SERIES), not a model"
        rivals = [v for v in vendor_names if _sq(v).startswith(e) and _sq(v) != n]
        if rivals:
            return f"'{evidence}' is only the start of {len(rivals) + 1} model names ({name}, {rivals[0]} ...)"
    digits = re.sub(r"\D", "", n)
    rest = e
    for a in sorted((_sq(a) for a in aliases), key=len, reverse=True):     # the vendor name read with it
        if a and rest.startswith(a) and not n.startswith(a):
            rest = rest[len(a):]
            break
    k = 0
    while k < min(len(rest), len(n)) and rest[k] == n[k] and not n[k].isdigit():  # the model's own letters (DSR, CCR)
        k += 1
    if len(digits) >= 3 and sum(c in _DIGIT_LIKE for c in rest[k:]) < len(digits) - 1:
        return f"'{evidence}' carries too little of {name}'s number"
    return None


def _sq(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def unique_number(evidence, models, canon):
    """The one catalogue model the reading's model number belongs to, or None. OCR often garbles
    a product-line word ('FortiADC 320F' read as 'FGn00320F') but reads the number right; when
    that number (3+ digits, up to 3 letters) ends the names of exactly one of the vendor's models,
    the model is that one. Never on a number shared by two models (FG-100F / FWF-100F ...).
    canon: name -> its catalogue model (a model's printed name and ID count once)."""
    m = re.search(r"(\d{3,5}[A-Z]{0,3})$", _sq(evidence))
    if not m:
        return None
    num = m.group(1).lstrip("0")             # 'FGn00320F': the garbled letters before 320F read as zeros
    if not re.match(r"\d{3}", num):
        return None
    hits = {canon(n) for n in models if re.search(r"(?<![0-9])" + num + r"$", _sq(n))}
    return hits.pop() if len(hits) == 1 else None


def identify(photo, log=print):
    """{vendor, model, found, how, evidence, raw} read from the photo (see _identify). A vendor is only
    kept when its name is read on the photo, or its model is printed there as is: a vendor taken from
    the nearest model of any vendor's list on garbled text ('UDMPO' -> Sierra Wireless MP70) is not
    used (review 2, #4)."""
    out = _identify(photo, log)
    raw = out.get("raw") or {}
    if out.get("vendor") and not raw.get("vendor_from_logo") and (raw.get("model_score") or 0) < 0.95:
        out["vendor_doubt"] = out["vendor"]
        out["how"] = (f"vendor {out['vendor']} not written on the photo (taken from the reading "
                      f"'{raw.get('model_evidence') or '?'}' only); not used")
        out.update(vendor=None, model=None, found=False)
    return out


def _identify(photo, log=print):
    """{vendor, model, found, how, evidence, raw} read from the photo. model is None unless
    it is a confident catalog match (the switch pipeline's rule: no guessed names)."""
    import panel_ocr
    import switch_identify as si
    load()
    _, names = ocr_catalog()
    if ENHANCED:
        # zoom anchored on every catalogue vendor's logo words (the switch list has none of
        # Fortinet, Sophos, F5 ...) and sharpened / black-and-white re-reads of each crop
        doc = panel_ocr.read_panel(photo, budget_s=OCR_BUDGET_S, logo_words=logo_words(), extra_renders=True)
    else:
        doc = panel_ocr.read_panel(photo, budget_s=OCR_BUDGET_S)
    res = si.identify(doc)
    vendor, model = res.get("vendor"), res.get("model")
    out = {"vendor": None, "model": None, "found": False, "how": "", "raw": {
        k: res.get(k) for k in ("vendor", "vendor_score", "vendor_evidence", "model", "model_score", "model_evidence",
                                "model_source", "in_catalog", "alternatives", "vendor_unconfirmed", "model_unconfirmed", "vendor_from_logo",
                                "vendor_runner_up")},
           "text": [t for t, *_ in panel_ocr.tokens(doc)][:80]}
    if vendor and not res.get("vendor_unconfirmed"):
        out["vendor"] = vendor
    text = [t for t, *_ in panel_ocr.tokens(doc)]
    if model and res.get("in_catalog"):
        vname = names.get(si.squash(model), (vendor, model, True))[0]
        cat, _ = ocr_catalog()
        entry = cat.get(vname) or {}
        why = doubtful(res.get("model_evidence") or "", model, entry.get("models", []), text, entry.get("aliases", []))
        if why:
            out["how"] = f"model not trusted: {why}"
            out["doubt"] = why
            return out
    if out.get("vendor") and not (model and res.get("in_catalog") and res.get("model_score", 0) >= MODEL_MIN_SCORE):
        cat_u, _ = ocr_catalog()
        hit = unique_number(res.get("model_evidence") or "", (cat_u.get(out["vendor"]) or {}).get("models", []),
                            lambda n: names.get(si.squash(n), (None, n, True))[1])
        if hit and not any("SERIES" in t.upper() for t in text):
            v, m, _ = names.get(si.squash(hit), (out["vendor"], hit, True))
            out.update(vendor=v, model=m, found=True,
                       how=f"read '{res.get('model_evidence')}': its number is unique to {m} (name garbled, number read)")
            return out
    if model and res.get("in_catalog") and res.get("model_score", 0) >= MODEL_MIN_SCORE:
        v, m, is_id = names.get(si.squash(model), (vendor, model, True))
        if is_id and si.squash(model) != si.squash(m):
            out["variant"] = model          # the exact product ID read (C1161-8PLTEP for base C1161-8P)
        out.update(vendor=v, model=m, found=True,
                   how=f"read '{res.get('model_evidence')}' -> {m} (score {res.get('model_score')})")
    elif model:
        out["how"] = f"model reading '{model}' not trusted (score {res.get('model_score')}, " \
                     f"{'in' if res.get('in_catalog') else 'not in'} the catalog)"
    return out
