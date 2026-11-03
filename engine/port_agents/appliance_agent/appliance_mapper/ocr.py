"""Read an appliance's make and model off the photo - the switch mapper's OCR, appliances only.

Same two stages as the switch and router pipelines:
  panel_ocr.read_panel   RapidOCR over the whole frame (both polarities), then the
                         area around the brand name re-read at several magnifications
  switch_identify        vendor from the logo and its aliases, model by OCR-confusion-
                         aware matching (5/S, 0/O, 1/I, 8/B ...), vendor-gated
switch_identify is loaded with the three appliance catalogues together (firewalls, load
balancers, gateways; in this process only), so the model read also tells the device type.
The names as printed on the panel are added ("FortiGate 200F" for FG-200F, "Firepower 2110"
for FPR2110, "Secure Firewall 1210CE" for CSF1210CE), each mapped back to the catalogue model.
Vendors that print only their logo (F5, NetScaler, Kemp) give the vendor; the model then
comes from the vendor's saved templates (pipeline.visual_identify) or is typed.
"""
import json
import os
import re
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from appliance_mapper import catalog  # noqa: E402

ENHANCED = os.environ.get("OCR_ENHANCED", "0") == "1"     # off: measured 2026-10-16, no gain, one new error, 2x slower
OCR_BUDGET_S = float(os.environ.get("APPLIANCE_MAPPER_OCR_BUDGET", "12" if ENHANCED else "8"))
MODEL_MIN_SCORE = 0.75     # a catalogue match below this is not trusted ('CISCOSSL' -> CISCO857 scored 0.72)

# faceplate words per vendor beyond its name (logos, sub-brands, product lines)
VENDOR_WORDS = {
    "Cisco": ["CISCO", "FIREPOWER", "SECURE FIREWALL", "ASA"], "Fortinet": ["FORTINET", "FORTIGATE", "FORTIWIFI", "FORTIADC"],
    "Palo Alto Networks": ["PALO ALTO", "PALOALTO"], "Check Point": ["CHECK POINT", "CHECKPOINT"],
    "SonicWall": ["SONICWALL", "SONICWALL NSA"], "Sophos": ["SOPHOS"], "Sophos (Cyberoam)": ["CYBEROAM"],
    "WatchGuard": ["WATCHGUARD", "FIREBOX"], "Juniper": ["JUNIPER", "JUNIPER NETWORKS", "NETSCREEN"],
    "Forcepoint": ["FORCEPOINT", "STONESOFT"], "Barracuda": ["BARRACUDA"], "Stormshield": ["STORMSHIELD"],
    "Zyxel": ["ZYXEL", "ZYWALL"], "Hillstone Networks": ["HILLSTONE"], "Huawei": ["HUAWEI", "HISECENGINE"],
    "H3C": ["H3C", "SECPATH"], "Sangfor": ["SANGFOR"], "Netgate": ["NETGATE", "PFSENSE"], "Deciso": ["OPNSENSE", "DECISO"],
    "Trend Micro": ["TIPPINGPOINT", "TREND MICRO"], "Trellix (McAfee)": ["TRELLIX", "MCAFEE"],
    "Ubiquiti": ["UBIQUITI", "UNIFI"], "MOXA": ["MOXA", "MGATE"], "Siemens": ["SIEMENS", "SCALANCE"],
    "Phoenix Contact": ["PHOENIX CONTACT", "MGUARD"], "Belden": ["HIRSCHMANN", "BELDEN"],
    "F5": ["F5", "BIG-IP", "VIPRION", "VELOS"], "NetScaler (Citrix)": ["NETSCALER", "CITRIX"],
    "A10 Networks": ["A10", "THUNDER"], "Radware": ["RADWARE", "ALTEON", "LINKPROOF"], "Kemp (Progress)": ["KEMP", "LOADMASTER"],
    "Array Networks": ["ARRAY NETWORKS", "ARRAY"], "Loadbalancer.org": ["LOADBALANCER.ORG"], "Piolink": ["PIOLINK"],
    "HAProxy": ["HAPROXY", "ALOHA"], "Peplink": ["PEPLINK", "PEPWAVE"], "Elfiq": ["ELFIQ"], "Ecessa": ["ECESSA"],
    "AudioCodes": ["AUDIOCODES", "MEDIANT", "MEDIAPACK"], "Ribbon": ["RIBBON", "SONUS", "EDGEMARC"],
    "Oracle": ["ORACLE", "ACME PACKET"], "Patton": ["PATTON", "SMARTNODE"], "Sangoma": ["SANGOMA", "VEGA"],
    "Grandstream": ["GRANDSTREAM"], "Yeastar": ["YEASTAR", "NEOGATE"], "Mediatrix (Media5)": ["MEDIATRIX", "MEDIA5"],
    "Dinstar": ["DINSTAR"], "Avaya": ["AVAYA"], "Adtran": ["ADTRAN", "TOTAL ACCESS"], "Ingate": ["INGATE", "SIPARATOR"],
    "HMS Networks": ["HMS", "ANYBUS"], "beroNet": ["BERONET"], "Poly (Obihai)": ["OBIHAI", "POLY"],
    "ProSoft Technology": ["PROSOFT"], "Red Lion Controls": ["RED LION"], "Advantech Co. Ltd": ["ADVANTECH"], "D-Link": ["D-LINK", "NETDEFEND"],
    "LANCOM Systems": ["LANCOM"], "Allied Telesis": ["ALLIED TELESIS"], "Kerio": ["KERIO"], "Clavister": ["CLAVISTER"],
    "Securepoint": ["SECUREPOINT"], "Ruijie": ["RUIJIE", "RG-WALL"], "NSFOCUS": ["NSFOCUS"], "Topsec": ["TOPSEC"],
    "DPtech": ["DPTECH"], "Tosibox": ["TOSIBOX"], "genua": ["GENUA", "GENUSCREEN", "GENUGATE"],
    "Lantronix": ["LANTRONIX"], "GajShield": ["GAJSHIELD"], "Quantum Networks": ["QUANTUM NETWORKS"], "AKOM Technologies": ["AKOM"], "AhnLab": ["AHNLAB", "TRUSGUARD"], "Arista Networks": ["ARISTA"], "Mushroom Networks": ["MUSHROOM", "TRUFFLE"],
}


def _merge_router_words():
    try:
        from router_mapper.ocr import VENDOR_WORDS as RW
    except Exception:
        return
    for v, words in RW.items():
        VENDOR_WORDS.setdefault(v, [])
        VENDOR_WORDS[v] += [w for w in words if w not in VENDOR_WORDS[v]]


_merge_router_words()


def printed_names(vendor, model):
    """Other ways the panel spells this model."""
    out = []
    m = re.match(r"^(FG|FWF|FGR)-(\w+)$", model)
    if m:
        out.append({"FG": "FortiGate ", "FWF": "FortiWiFi ", "FGR": "FortiGate Rugged "}[m.group(1)] + m.group(2))
    if vendor == "Cisco":
        m = re.match(r"^FPR(\d{4})$", model)
        if m:
            n = m.group(1)
            out += [f"FPR-{n}", f"Firepower {n}", f"Secure Firewall {n}"]
        m = re.match(r"^CSF(\d{4}\w*)$", model)
        if m:
            out += [f"Secure Firewall {m.group(1)}", f"CSF-{m.group(1)}"]
        m = re.match(r"^ASA(\d{4}\w?)(-X)?$", model)
        if m:
            out.append(f"ASA {m.group(1)}{m.group(2) or ''}")
        m = re.match(r"^VG(\d{3})", model)
        if m:
            out.append(f"Cisco VG{m.group(1)}")
    if vendor == "Check Point":
        m = re.match(r"^(?:Quantum(?: Force| Spark)? )?(\d{3,5}\w*)$", model)
        if m and len(m.group(1)) >= 4:
            out.append(m.group(1))
    if vendor == "WatchGuard" and re.match(r"^[MT]\d", model):
        out.append(f"Firebox {model}")
    if vendor == "Patton":
        m = re.match(r"^SN(\d{3,5}\w*)$", model)
        if m:
            out.append(f"SmartNode {m.group(1)}")
    if vendor == "Kemp (Progress)" and model.startswith("LM-"):
        out.append("LoadMaster " + model[3:])
    if vendor == "F5" and re.match(r"^[ir]\d{3,5}", model):
        out.append(f"BIG-IP {model}")
    m = re.match(r"^TL-(ER|R)(\d+\w*\+?)$", model) if vendor == "TP-Link" else None
    if m:
        out.append(f"TL-{m.group(1)}{m.group(2)}".replace("-", ""))
    if vendor == "Allied Telesis" and not model.upper().startswith("AT-"):
        out.append("AT-" + model)
    if vendor == "Radware" and model.startswith("Alteon "):
        out.append(model.replace("Alteon ", "", 1))
    return [o for o in out if o != model]


@lru_cache(maxsize=4)
def ocr_catalog(kind=None):
    """({vendor: {aliases, models}}, {squashed name: (vendor, catalog model)}); kind limits it to
    one device type's catalogue (the type chosen in the UI), None = all three."""
    import switch_identify as si
    try:                                          # switch vendors' OCR spellings (Switch_Vendors_and_Models.xlsx)
        switch_cat = si.read_switch_excel(ROOT / si.SWITCH_EXCEL)
    except (OSError, ValueError, KeyError):
        switch_cat = {}
    by_key = {catalog.vendor_key(v): e for v, e in switch_cat.items()}
    cat, names = {}, {}
    for r in catalog.rows():
        if kind and r["kind"] != kind:
            continue
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


def load(kind=None):
    """Load the appliance catalogues (or one device type's) into switch_identify, replacing its
    switch models."""
    global _loaded
    if _loaded == (kind or "all"):
        return
    import tempfile
    import switch_identify as si
    cat, _ = ocr_catalog(kind)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(cat, fh)
    si.load_catalog(fh.name, replace=True)
    os.unlink(fh.name)
    # appliance family patterns (switch_identify's are switch families)
    si.FAMILY_PATTERNS[:] = [(p, v) for p, v in [
        (r"FG\d{2,4}[A-Z]{1,2}", "Fortinet"), (r"FORTIGATE\d{2,4}[A-Z]{1,2}", "Fortinet"), (r"FORTIADC\d{3,4}[A-Z]", "Fortinet"),
        (r"PA\d{3,4}R?", "Palo Alto Networks"), (r"FPR\d{4}", "Cisco"), (r"FIREPOWER\d{4}", "Cisco"),
        (r"ASA\d{4}[A-Z]?X?", "Cisco"), (r"CSF\d{4}[A-Z]{0,2}", "Cisco"), (r"VG\d{3}", "Cisco"),
        (r"SRX\d{3,4}", "Juniper"), (r"SSG\d{1,3}M?", "Juniper"), (r"NSA\d{3,4}", "SonicWall"), (r"TZ\d{3}[A-Z]?", "SonicWall"),
        (r"XGS\d{2,4}W?", "Sophos"), (r"XG\d{2,3}W?", "Sophos"), (r"SG\d{3}W?", "Sophos"), (r"CR\d{2,4}ING", "Sophos (Cyberoam)"),
        (r"USG\d{4}[A-Z]?", "Huawei"), (r"SN\d{3}[A-Z0-9]{0,3}", "Stormshield"), (r"THUNDER\d{3,5}[A-Z]?", "A10 Networks"),
        (r"MPX\d{4,5}", "NetScaler (Citrix)"), (r"SDX\d{4,5}", "NetScaler (Citrix)"), (r"MEDIANT\d{3,4}[A-Z]?", "AudioCodes"),
        (r"MP\d{3,4}", "AudioCodes"), (r"SMARTNODE\d{3,5}", "Patton"), (r"GXW\d{4}", "Grandstream"), (r"HT8\d{2}", "Grandstream"),
        (r"MGATE\w{4,10}", "MOXA"), (r"APV\d{4,5}", "Array Networks"), (r"LMX\d{1,2}", "Kemp (Progress)")]]
    si.MIN_MODEL_LEN = 4          # appliance models can be short: T45, M470, 340 (vendor-gated)
    si.rebuild_model_index()
    _loaded = kind or "all"


def _other_number(read, name):
    """True when the read text and the catalogue name carry different numbers of the same length
    (11000 vs 17000, 2110 vs 2140): a digit read as another digit is not a misreading the matcher
    may correct. Letter/digit confusions (SRX34S, XG13 cut short) give numbers of other lengths."""
    a, b = re.sub(r"\D", "", read), re.sub(r"\D", "", name)
    return bool(a) and len(a) == len(b) and a != b and len(a) >= 2


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


def identify(photo, log=print, kind=None):
    """{vendor, model, found, how, evidence, raw} read from the photo (see _identify). A vendor is only
    kept when its name is read on the photo, or its model is printed there as is: a vendor taken from
    the nearest model of any vendor's list on garbled text ('UDMPO' -> Sierra Wireless MP70) is not
    used (review 2, #4)."""
    out = _identify(photo, log, kind)
    raw = out.get("raw") or {}
    if out.get("vendor") and not raw.get("vendor_from_logo") and (raw.get("model_score") or 0) < 0.95:
        out["vendor_doubt"] = out["vendor"]
        out["how"] = (f"vendor {out['vendor']} not written on the photo (taken from the reading "
                      f"'{raw.get('model_evidence') or '?'}' only); not used")
        out.update(vendor=None, model=None, found=False)
    return out


def _identify(photo, log=print, kind=None):
    """{vendor, model, found, how, evidence, raw} read from the photo. model is None unless
    it is a confident catalog match (the switch pipeline's rule: no guessed names)."""
    import panel_ocr
    import switch_identify as si
    load(kind)
    _, names = ocr_catalog(kind)
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
    ev = si.squash(res.get("model_evidence") or "")
    if model and ev and _other_number(ev, si.squash(model)):
        # the panel reads 'MPX11000' cleanly and the nearest catalogue name is MPX 17000: one digit
        # read as another digit is a different product, never an OCR slip - the model as printed
        # is kept (looked up as typed, not in the catalogue) instead of the wrong catalogue model
        out["model_read"] = res.get("model_evidence")
        out["how"] = (f"read '{res.get('model_evidence')}' - not in the catalogue; the nearest name {model} has a "
                      f"different number, so it is not used")
        return out
    text = [t for t, *_ in panel_ocr.tokens(doc)]
    if model and res.get("in_catalog"):
        vname = names.get(si.squash(model), (vendor, model, True))[0]
        cat, _ = ocr_catalog(kind)
        entry = cat.get(vname) or {}
        why = doubtful(res.get("model_evidence") or "", model, entry.get("models", []), text, entry.get("aliases", []))
        if why:
            out["how"] = f"model not trusted: {why}"
            out["doubt"] = why
            return out
    if kind and not (model and res.get("in_catalog") and res.get("model_score", 0) >= MODEL_MIN_SCORE):
        # nothing in the chosen device type: the same reading against all three types, only to
        # tell the user which type the unit is (the run stays in the chosen type)
        load(None)
        r2 = si.identify(doc)
        _, all_names = ocr_catalog(None)
        if r2.get("model") and r2.get("in_catalog") and r2.get("model_score", 0) >= MODEL_MIN_SCORE:
            v2, m2, _ = all_names.get(si.squash(r2["model"]), (r2.get("vendor"), r2["model"], True))
            out["other_type"] = {"vendor": v2, "model": m2}
    if out.get("vendor") and not (model and res.get("in_catalog") and res.get("model_score", 0) >= MODEL_MIN_SCORE):
        cat_u, _ = ocr_catalog(kind)
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
