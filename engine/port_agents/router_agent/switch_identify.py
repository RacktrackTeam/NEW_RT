#!/usr/bin/env python3
"""Identify the vendor and model of network switches in photos, retrieve each
model's reference photo, and write an HTML report showing them side by side.

Everything in one run, no paths baked in.

    python switch_identify.py                       # photos in ./, report alongside
    python switch_identify.py path/to/photos
    python switch_identify.py photos -o report.html
    python switch_identify.py one_switch.jpg        # a single file works too

Reference photos come from the vendor workbooks when a catalog is available:

    python build_switch_catalog.py path/to/switch_excels -o switch_catalog.json
    python switch_identify.py photos --images-from switch_catalog.json

A catalog named switch_catalog.json beside the photos or beside this script is
picked up automatically. Models the workbooks do not list are looked up online
instead, which matters because racks are full of discontinued kit that current
product sheets no longer carry. Pass --no-web to stay offline.

Requires: easyocr, opencv-python, numpy  (pip install easyocr opencv-python numpy)

How it works
------------
1. Each photo is rendered a dozen ways (upscaled, contrast-stretched, inverted,
   left/right panel crops) because faceplate silkscreen is tiny, low-contrast and
   usually light-on-dark; no single preprocessing setting reads it all.
2. The brand name is located, then the area around it is re-read at up to 12x
   magnification -- the model number is printed far smaller than the logo and is
   almost never legible in a full-frame pass.
3. Everything read is matched against a vendor/model catalog using an edit
   distance that charges little for glyph pairs OCR routinely swaps (0/O, 1/I/L,
   5/S, C/G) and full price for everything else.
4. Marketing text on the faceplate ("Gigabit", "28-Port", "PoE") corroborates or
   rules out candidates, which is what resolves the single characters OCR cannot.
5. The identified model's reference photo is pulled from its workbook, or failing
   that from a web search filtered to results carrying that model number.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp")


# =========================================================================== #
# Catalog
# =========================================================================== #

# Vendor name -> spellings that may appear on a faceplate (logos, silkscreen).
#
# Deliberately empty: the catalog file is the single source of truth for both
# vendors and models. load_catalog() fills this from Switch_Vendors_and_Models.xlsx
# (sheet Switches: the models with 'Used by agent' = Yes; sheet Vendors: the OCR
# spellings). Add a make or an alias there, not here - anything added here would be a
# second place to look and would drift out of step with the file.
VENDOR_ALIASES = {}

# Known models. Order within a vendor does not matter; longer strings win ties.
MODELS = {
    "TP-Link": [
        "TL-SG105", "TL-SG108", "TL-SG108E", "TL-SG108PE", "TL-SG116",
        "TL-SG1005D", "TL-SG1008D", "TL-SG1008P", "TL-SG1016", "TL-SG1016D",
        "TL-SG1016DE", "TL-SG1016PE", "TL-SG1024", "TL-SG1024D", "TL-SG1024DE",
        "TL-SG1210MPE", "TL-SG1218MPE", "TL-SG1428PE",
        "TL-SG2008", "TL-SG2210P", "TL-SG2218", "TL-SG2424", "TL-SG2424P",
        "TL-SG2428P", "TL-SG2452",
        "TL-SG3210", "TL-SG3424", "TL-SG3424P", "TL-SG3428", "TL-SG3428X",
        "TL-SG3428XMP", "TL-SG3452", "TL-SG3452X",
        "TL-SF1005D", "TL-SF1008D", "TL-SF1016", "TL-SF1016D", "TL-SF1016DS",
        "TL-SF1024", "TL-SF1024D", "TL-SF1048", "TL-SL1218MP", "TL-SL2428P",
        "T1500G-10PS", "T1600G-28TS", "T1600G-52TS", "T2600G-28TS", "T2600G-52TS",
    ],
    "D-Link": [
        "DGS-1005A", "DGS-1008A", "DGS-1008D", "DGS-1016A", "DGS-1016C",
        "DGS-1016D", "DGS-1024A", "DGS-1024C", "DGS-1024D", "DGS-1048",
        "DGS-1100-08", "DGS-1100-16", "DGS-1100-24", "DGS-1210-10P",
        "DGS-1210-16", "DGS-1210-24", "DGS-1210-28", "DGS-1210-28P",
        "DGS-1210-52", "DGS-1510-20", "DGS-1510-28", "DGS-1510-52",
        "DGS-3120-24TC", "DGS-3420-28TC", "DGS-3620-28TC",
        "DES-1005A", "DES-1008A", "DES-1016A", "DES-1016D", "DES-1024A",
        "DES-1024D", "DES-1050G", "DES-1210-10P", "DES-1210-28",
        "DES-1210-28P", "DES-1210-52", "DES-3200-28", "DES-3526",
        "DXS-1210-12TC", "DXS-1210-28S",
    ],
    "Cisco": [
        "SG110-16", "SG110-24", "SG110D-08", "SG112-24", "SG200-26", "SG200-50",
        "SG220-26", "SG250-24", "SG250-48", "SG300-10", "SG300-28", "SG300-52",
        "SG350-10", "SG350-28", "SG350-52", "SG500-28", "SG550X-24",
        "SF110-16", "SF110-24", "SF200-24", "SF300-24", "SF350-48",
        "CBS110-24T", "CBS250-24T", "CBS250-48P", "CBS350-24T", "CBS350-48P",
        "WS-C2960-24TT-L", "WS-C2960-48TT-L", "WS-C2960S-24TS-L",
        "WS-C2960X-24PS-L", "WS-C2960X-48FPD-L", "WS-C3560-24PS",
        "WS-C3750-48TS", "WS-C3850-24T", "C9200-24T", "C9200L-48P-4G",
        "C9300-24T", "C9300-48P",
    ],
    "Dell": [
        "PowerConnect 2808", "PowerConnect 2816", "PowerConnect 2824",
        "PowerConnect 2848", "PowerConnect 3524", "PowerConnect 3548",
        "PowerConnect 5524", "PowerConnect 6224", "PowerConnect 6248",
        "N1524", "N1548", "N2024", "N2048", "N3024", "N3048",
        "X1008", "X1018", "X1026", "X1052", "S3048-ON", "S4048-ON",
    ],
    "NETGEAR": [
        "GS105", "GS108", "GS110TP", "GS116", "GS305", "GS308", "GS316",
        "GS724T", "GS748T", "GS752TP", "GS724TP", "JGS516", "JGS524",
        "FS116", "FS726T", "M4300-28G", "M4100-26G", "XS708T",
    ],
    "HPE/Aruba": [
        "J9085A", "J9279A", "J9450A", "J9772A", "J9773A", "J9776A", "J9850A",
        "ProCurve 2510-24", "ProCurve 2520-24", "ProCurve 2610-24",
        "ProCurve 2810-24G", "ProCurve 2824", "ProCurve 2848",
        "Aruba 2530-24G", "Aruba 2540-24G", "Aruba 2930F-24G",
        "1920-24G", "1950-24G", "1820-24G", "1810-24G",
    ],
    "Juniper": [
        "EX2200-24T", "EX2200-48T", "EX2300-24T", "EX2300-48P", "EX3300-24T",
        "EX3300-48T", "EX3400-24T", "EX4200-48T", "EX4300-48T",
    ],
    "Ubiquiti": [
        "US-8-60W", "US-8-150W", "US-16-150W", "US-24", "US-24-250W",
        "US-48", "US-48-500W", "USW-24-POE", "USW-48-POE", "USW-Pro-24-PoE",
        "USW-Lite-16-PoE", "USW-Flex", "ES-24-250W",
    ],
    "MikroTik": [
        "CRS112-8G-4S", "CRS326-24G-2S", "CRS328-24P-4S", "CRS309-1G-8S",
        "CSS326-24G-2S", "CSS610-8G-2S", "RB260GS", "RB260GSP", "RB2011UiAS",
    ],
    "Zyxel": [
        "GS1200-8", "GS1900-8", "GS1900-24", "GS1900-24E", "GS1900-48",
        "GS1920-24", "GS2210-24", "XGS1210-12", "XGS1930-28", "ES1100-24",
    ],
    "Huawei": [
        "S1700-24", "S1720-28GWR", "S5700-28C-EI", "S5700-48TP-SI",
        "S5720-28X-SI", "S5720-52X-SI", "S5730-48C-SI", "S6720-30C-EI",
    ],
    "Extreme": [
        "X440-24t", "X440-48p", "X450-24t", "X460-48p", "X620-16x",
        "Summit X440", "Summit X450",
    ],
    "Allied Telesis": ["AT-GS950-24", "AT-GS950-48", "AT-8000S-24", "AT-x230-28GT"],
    "Linksys": ["SRW224G4", "SRW248G4", "LGS124", "LGS326", "SE4008"],
    "Planet": ["GSW-2404", "GSD-2022P", "SGS-5220-24T"],
    "3Com": ["3C16471", "3C16475", "Baseline 2824", "Baseline 2928"],
}

# Prefix patterns that catch model numbers absent from the catalog above.
FAMILY_PATTERNS = [
    (r"TL(?:SG|SF|SL)\d{3,5}[A-Z]{0,4}", "TP-Link"),
    (r"T[12]\d{3}G\d{2}[A-Z]{1,3}", "TP-Link"),
    (r"(?:DGS|DES|DXS|DIS)\d{4}[A-Z]?\d{0,3}[A-Z]{0,3}", "D-Link"),
    (r"WSC\d{4}[A-Z]{0,2}\d{2}[A-Z]{1,4}", "Cisco"),
    (r"(?:SG|SF|CBS)\d{3}[A-Z]?\d{2}[A-Z]{0,2}", "Cisco"),
    (r"POWERCONNECT\d{4}", "Dell"),
    (r"(?:GS|FS|JGS|XS)\d{3,4}[A-Z]{0,3}", "NETGEAR"),
    (r"EX\d{4}\d{0,2}[A-Z]{0,2}", "Juniper"),
    (r"(?:CRS|CSS)\d{3}\d{0,2}[A-Z0-9]{0,8}", "MikroTik"),
    (r"US[W]?\d{2}\d{0,3}[A-Z]{0,4}", "Ubiquiti"),
    (r"S\d{4}\d{0,2}[A-Z]{0,4}", "Huawei"),
]

# Short tokens are matched exactly - fuzzy-matching a three-letter word against a
# page of OCR noise finds it everywhere.
DESCRIPTOR_HINTS = [
    (r"(\d{1,2})PORT", "port_count"),
    (r"POE", "poe"),
    (r"L2", "l2"),
    (r"SFP", "sfp"),
    (r"SMART", "smart"),
]

# Longer words survive a fuzzy, confusion-aware lookup, which is what makes them
# usable when the marketing line is half-legible ("GIQABIT" -> gigabit).
FUZZY_DESCRIPTOR_HINTS = [
    ("GIGABIT", "gigabit"),
    ("MANAGED", "managed"),
    ("WEBSMART", "smart"),
    ("JETSTREAM", "jetstream"),
]
FUZZY_DESCRIPTOR_MIN = 0.78
# Above this the reading is trusted enough to rule candidates out, not just nudge
# them: a switch whose faceplate clearly says "Gigabit" is not a Fast Ethernet
# model, whatever the blurred model number looks like.
FUZZY_DESCRIPTOR_STRONG = 0.85

ALL_MODELS = [(m, v) for v, ms in MODELS.items() for m in ms]

# The catalog above is a built-in starting point, not a closed list.  It is what
# makes the fuzzy match work at all - OCR returns "ILSgi0io" and only a known
# vocabulary turns that back into TL-SG1016 - but no built-in list covers every
# switch in every rack, so it can be extended or replaced from a JSON file.
CATALOG_FILENAME = "switch_catalog.json"
SWITCH_EXCEL = Path("switch_catalog") / "Switch_Vendors_and_Models.xlsx"   # below an agent's folder


def read_switch_excel(path):
    """{vendor: {"aliases": [...], "models": [...]}} from the switch catalogue workbook: the models of
    sheet 'Switches' whose 'Used by agent' is Yes (in sheet order) and the 'OCR spellings (used by
    agent)' of sheet 'Vendors' (comma separated)."""
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        out = {}

        def table(name):
            rows = wb[name].iter_rows(values_only=True)
            head = [str(h or "").strip() for h in next(rows)]
            return head, rows

        head, rows = table("Vendors")
        iv, ia = head.index("Vendor"), head.index("OCR spellings (used by agent)")
        for r in rows:
            vendor = str(r[iv] or "").strip()
            if vendor:
                aliases = [a.strip() for a in str(r[ia] or "").split(",") if a.strip()]
                out[vendor] = {"aliases": aliases, "models": []}
        head, rows = table("Switches")
        iv, im, iu = head.index("Vendor"), head.index("Model"), head.index("Used by agent")
        for r in rows:
            vendor, model = str(r[iv] or "").strip(), str(r[im] or "").strip()
            if vendor and model and str(r[iu] or "").strip().lower().startswith("yes"):
                ms = out.setdefault(vendor, {"aliases": [], "models": []})["models"]
                if model not in ms:
                    ms.append(model)
        return out
    finally:
        wb.close()


def rebuild_model_index():
    global ALL_MODELS, _BLOCK_INDEX
    ALL_MODELS = [(m, v) for v, ms in MODELS.items() for m in ms]
    _BLOCK_INDEX = None  # catalog changed; the blocking index is stale


def catalog_as_dict():
    """The active catalog, in the shape load_catalog() accepts."""
    return {
        "vendors": {v: list(a) for v, a in VENDOR_ALIASES.items()},
        "models": {v: list(m) for v, m in MODELS.items()},
        "family_patterns": [[p, v] for p, v in FAMILY_PATTERNS],
    }


def _normalise_catalog(data):
    """Accept either the hand-edited shape or build_switch_catalog.py's output.

    The builder writes one flat list of model records, each naming its vendor,
    and vendors as objects carrying their aliases.  Both are folded into the
    vendor -> [model] form the matcher uses.
    """
    vendors, models = {}, {}

    # models_by_vendor.json is a bare {vendor: ...} map with no wrapper keys, where each
    # vendor is either a plain list of models or {"aliases": [...], "models": [...]}.
    # Recognise both so that one file can carry the whole catalog, aliases included.
    if not any(k in data for k in ("vendors", "models", "family_patterns")) and data \
            and all(isinstance(v, (list, dict)) for v in data.values()):
        flat_v, flat_m = {}, {}
        for vendor, entry in data.items():
            if isinstance(entry, dict):
                flat_m[vendor] = list(entry.get("models") or [])
                al = list(entry.get("aliases") or [])
                if al:
                    flat_v[vendor] = al
            else:
                flat_m[vendor] = list(entry or [])
        data = {"vendors": flat_v, "models": flat_m}

    for vendor, info in (data.get("vendors") or {}).items():
        if isinstance(info, dict):
            vendors[vendor] = list(info.get("aliases") or [vendor])
        else:
            vendors[vendor] = list(info or [])

    raw = data.get("models")
    if isinstance(raw, list):
        for rec in raw:
            vendor = (rec or {}).get("vendor")
            if not vendor:
                continue
            for name in expand_models(rec.get("model") or ""):
                models.setdefault(vendor, []).append(name)
    elif isinstance(raw, dict):
        for vendor, names in raw.items():
            models.setdefault(vendor, []).extend(names or [])

    return {"vendors": vendors, "models": models,
            "family_patterns": data.get("family_patterns") or []}


def load_catalog(path, replace=False):
    """Merge (or replace) the built-in vendor/model catalog from a JSON file.

    Expected shape - every key optional:

        {
          "vendors": {"Acme": ["ACME NETWORKS", "ACMESWITCH"]},
          "models":  {"Acme": ["AC-1024G", "AC-1048G"]},
          "family_patterns": [["AC\\\\d{4}[A-Z]{0,2}", "Acme"]]
        }

    The file produced by build_switch_catalog.py is also accepted, so the models
    harvested from the vendor workbooks can extend what is recognised.

    Merging is the default so a site can add its own kit without losing the
    built-ins; `replace` swaps the catalog out entirely. An .xlsx path is read
    with read_switch_excel().
    """
    if str(path).lower().endswith(".xlsx"):
        data = read_switch_excel(path)
    else:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("%s: expected a JSON object at the top level" % path)
    data = _normalise_catalog(data)

    if replace:
        VENDOR_ALIASES.clear()
        MODELS.clear()
        del FAMILY_PATTERNS[:]

    added_v = added_m = 0
    for vendor, aliases in (data.get("vendors") or {}).items():
        existing = VENDOR_ALIASES.setdefault(vendor, [])
        for alias in aliases:
            if alias not in existing:
                existing.append(alias)
                added_v += 1

    for vendor, models in (data.get("models") or {}).items():
        existing = MODELS.setdefault(vendor, [])
        # A vendor can be listed under "models" without appearing under
        # "vendors"; make sure it is still matchable by name.
        VENDOR_ALIASES.setdefault(vendor, [])
        for model in models:
            if model not in existing:
                existing.append(model)
                added_m += 1

    for entry in (data.get("family_patterns") or []):
        pattern, vendor = entry[0], entry[1]
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError("%s: bad family pattern %r (%s)" % (path, pattern, exc))
        if (pattern, vendor) not in FAMILY_PATTERNS:
            FAMILY_PATTERNS.append((pattern, vendor))

    rebuild_model_index()
    return added_v, added_m


def find_catalog(*directories):
    """Look for a catalog file next to the images or next to this script."""
    for directory in directories:
        if not directory:
            continue
        candidate = Path(directory) / CATALOG_FILENAME
        if candidate.is_file():
            return candidate
    return None


# =========================================================================== #
# Confusion-aware matching
# =========================================================================== #

# Glyph groups an OCR engine confuses at small point sizes / low contrast.
# C sits with the round glyphs because a low-resolution C routinely closes up
# into an O or a 0.
_CONFUSION_GROUPS = [
    "0OQDC", "1IL|T7J", "5S", "8B", "2Z", "6G", "CGO", "9GQ",
    "4A", "3E", "UV", "MN", "KX", "PR", "FE", "YV",
    # Closed round digits: a blurred 8 loses its waist and reads as 0 or 6,
    # which is what separates a TL-SG2428P from a TL-SG2424P on a rack photo.
    "08B", "68", "39",
    # D-Link prints DGS/DES in a bold italic where a motion-blurred G loses its
    # bar and reads as E - the recognizer returns "DES-1016D" off a DGS-1016D
    # strip a human reads correctly.
    "EG",
]
_CONFUSABLE = {(a, b) for g in _CONFUSION_GROUPS for a in g for b in g if a != b}

SUB_CONFUSABLE = 0.30   # cheap: a known glyph swap
SUB_OTHER = 1.0         # full price: a genuinely different character
INDEL = 1.0

# A vendor read straight off the logo is trusted enough to narrow the model
# search to that vendor's catalog at a lower bar.
VENDOR_MIN = 0.82
MODEL_MIN_GATED = 0.70
MODEL_MIN_OPEN = 0.80
# A cross-vendor match only overrides the logo if it is near perfect.
MODEL_MIN_CROSS = 0.95
# Shorter than this and a blurry faceplate cannot identify a model reliably.
MIN_MODEL_LEN = 6

# Families whose model prefix encodes the port speed.
_FAST_ETHERNET = re.compile(r"^(DES-|TL-SF|TL-SL|FS\d|SF\d{3}|ES1100|AT-8000|S1700)")
# A trailing P / PE / MP marks the PoE variant of an otherwise identical model.
_POE_MODEL = re.compile(r"(P|PE|MP|MPE|PWR)$")


def squash(text):
    """Uppercase and drop everything that is not a letter or digit.

    Separators (-, space, .) are the first thing OCR loses or invents, so model
    identity is compared on the alphanumeric skeleton only.
    """
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def _sub_cost(a, b):
    if a == b:
        return 0.0
    return SUB_CONFUSABLE if (a, b) in _CONFUSABLE else SUB_OTHER


def weighted_distance(a, b):
    """Levenshtein distance with the confusion-aware substitution cost."""
    if not a:
        return len(b) * INDEL
    if not b:
        return len(a) * INDEL
    prev = [j * INDEL for j in range(len(b) + 1)]
    for i, ca in enumerate(a, 1):
        cur = [i * INDEL]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + INDEL,
                           cur[j - 1] + INDEL,
                           prev[j - 1] + _sub_cost(ca, cb)))
        prev = cur
    return prev[-1]


def similarity(a, b):
    """1.0 == identical, 0.0 == nothing in common. Normalised by the longer string."""
    if not a and not b:
        return 1.0
    return max(0.0, 1.0 - weighted_distance(a, b) / max(len(a), len(b)))


def best_substring_similarity(needle, haystack, slack=2):
    """Best score for `needle` against any window of `haystack` of a similar length.

    OCR glues the model to neighbouring words ("TLSG1016JETSTREAM"), so the
    catalog entry is matched against windows rather than the whole blob.
    """
    if not needle or not haystack:
        return 0.0, ""
    if needle in haystack:
        return 1.0, needle
    n = len(needle)
    best, best_window = 0.0, ""
    for width in range(max(1, n - slack), n + slack + 1):
        for start in range(0, len(haystack) - width + 1):
            window = haystack[start:start + width]
            score = similarity(needle, window)
            if score > best:
                best, best_window = score, window
    return best, best_window


def _sort_key_spatial(det):
    box = det.get("box")
    if not box:
        return (1, 0.0, 0.0)
    # Round y into bands so items on one printed line stay together.
    return (0, round(min(p[1] for p in box) / 12.0), min(p[0] for p in box))


def build_haystacks(doc):
    """Return [(label, squashed_text, weight)] reconstructions of the page.

    A model number is sometimes one detection ("TL-SG3424") and sometimes two
    ("PowerConnect" above "2824"), so several orderings are offered.
    """
    stage1 = [d for d in doc.get("detections", []) if d.get("text")]
    stage2 = [d for d in doc.get("roi_detections", []) if d.get("text")]

    hay = []
    for d in stage1:
        hay.append(("full:%s" % d["text"], squash(d["text"]), 1.0))
    for d in stage2:
        hay.append(("roi:%s" % d["text"], squash(d["text"]), 1.0))

    # Spatial reading order glues neighbours together, which is how a two-part
    # model name becomes matchable.
    spatial = sorted(stage1, key=_sort_key_spatial)
    hay.append(("reading-order", squash("".join(d["text"] for d in spatial)), 0.95))

    by_conf = sorted(stage1, key=lambda d: -d.get("conf", 0))
    hay.append(("confidence-order", squash("".join(d["text"] for d in by_conf)), 0.9))

    # The magnified-crop stage emits hundreds of sightings per photo; the window
    # search over a concatenation of all of them is quadratic and the tail is
    # pure noise, so the blob is built from the most confident few dozen only.
    roi_conf = sorted(stage2, key=lambda d: -d.get("conf", 0))[:40]
    hay.append(("roi-blob", squash("".join(d["text"] for d in roi_conf)), 0.9))

    # Adjacent pairs, so a split model matches without the rest of the faceplate
    # text diluting the window search.
    for a, b in zip(spatial, spatial[1:]):
        hay.append(("pair", squash(a["text"] + b["text"]), 0.95))

    # Dedupe identical reconstructions - the ROI stage re-reads the same strip
    # many ways and often returns the same string from each.
    seen, out = set(), []
    for label, text, weight in hay:
        if len(text) < 3 or (text, round(weight, 2)) in seen:
            continue
        seen.add((text, round(weight, 2)))
        out.append((label, text, weight))
    return out


SHORT_ALIAS = 4          # aliases this short ('RAD', 'EDS', 'A10', 'MOXA') match whole words only
_SERIAL_WORDS = re.compile(r"\b(S/?N|SER(IAL)?|MAC|P/?N|PART|IMEI|EAN|UPC|REV|HW|FW|BATCH|LOT)\b", re.I)
_MAC = re.compile(r"[0-9A-F]{2}[:\-.][0-9A-F]{2}[:\-.][0-9A-F]{2}|\b[0-9A-F]{4}:[0-9A-F]{4,}|\bMAC\s?ADD", re.I)


def is_serial_like(text):
    """Text from a serial / MAC / part-number sticker: no vendor is read from it. On a Sophos
    firewall a sticker gave MOXA at 0.9 over the SOPHOS printed on the panel."""
    # a serial is one unbroken run of letters and digits (FCW2134L0AB12); a model number has a dash
    # or a space ('Catalyst 4948-10GE' carries Cisco's brand word and must stay)
    runs = re.findall(r"[A-Za-z0-9]+", text or "")
    return bool(_SERIAL_WORDS.search(text or "") or _MAC.search(text or "")
                or any(len(r) >= 10 and sum(c.isdigit() for c in r) >= 5 and any(c.isalpha() for c in r)
                       for r in runs))


def vendor_haystacks(doc):
    """The reconstructions vendor detection reads: those of build_haystacks() without the
    serial / MAC / part-number stickers, so a sticker's letters cannot spell a brand."""
    keep = lambda ds: [d for d in ds if d.get("text") and not is_serial_like(d["text"])]
    return build_haystacks({"detections": keep(doc.get("detections", [])),
                            "roi_detections": keep(doc.get("roi_detections", []))})


def _word_score(alias_sq, haystacks):
    """Best similarity of a short alias against whole printed words (never a window inside
    a longer string or a concatenation: 'RAO' is not RAD, 'A1C' is not A10)."""
    best, evidence, support = 0.0, "", 0
    for label, _text, weight in haystacks:
        if not (label.startswith("full:") or label.startswith("roi:")):
            continue
        for word in re.split(r"[^A-Za-z0-9]+", label.split(":", 1)[1]):
            w = squash(word)
            if not w or abs(len(w) - len(alias_sq)) > 1:
                continue
            score = similarity(alias_sq, w) * weight
            if score >= 0.95:
                support += 1
            if score > best:
                best, evidence = score, w
    return best, evidence, support


def detect_vendor(haystacks, min_score=VENDOR_MIN, heights=None):
    """Score every vendor alias against every reconstruction; return ranked hits.
    Aliases of up to SHORT_ALIAS characters must match a whole printed word at 0.95+ (three
    or four letters turn up by chance inside any sticker or concatenation). heights: text ->
    printed height in px, so a near-tie goes to the larger printed brand (logos are big)."""
    hits = []
    heights = heights or {}
    for vendor, aliases in VENDOR_ALIASES.items():
        best, evidence, support = 0.0, "", 0
        for alias in [vendor] + aliases:
            alias_sq = squash(alias)
            if len(alias_sq) < 3:
                continue
            if len(alias_sq) <= SHORT_ALIAS:
                score, window, sup = _word_score(alias_sq, haystacks)
                support += sup
                if score >= 0.95 and score > best:
                    best, evidence = score, window
                continue
            for _label, text, weight in haystacks:
                score, window = best_substring_similarity(alias_sq, text)
                score *= weight
                if score >= min_score:
                    support += 1
                if score > best:
                    best, evidence = score, window
        if best >= min_score:
            hits.append({"vendor": vendor, "score": round(best, 3), "evidence": evidence, "support": support,
                         "height": max((h for t, h in heights.items() if evidence and evidence in squash(t)), default=0)})
    # More independent sightings breaks a tie between two near-equal scores.
    hits.sort(key=lambda h: (-h["score"], -h["support"], -len(h["evidence"])))
    # a near-tie (within 0.05) goes to the brand printed larger
    if len(hits) > 1 and hits[0]["score"] - hits[1]["score"] <= 0.05 and hits[1]["height"] > 1.3 * hits[0]["height"] > 0:
        hits[0], hits[1] = hits[1], hits[0]
    return hits


def _text_heights(doc):
    """Printed height (px) of each detected text."""
    out = {}
    for d in doc.get("detections", []):          # full-frame boxes only: crop boxes are magnified
        box = d.get("box")
        if d.get("text") and box:
            ys = [p[1] for p in box]
            out[d["text"]] = max(out.get(d["text"], 0), max(ys) - min(ys))
    return out


# --------------------------------------------------------------------------- #
# Blocking index
#
# Scoring every catalog entry against every reconstruction is quadratic in the
# catalog size: ~250 models already costs a minute per photo, so a catalog of a
# hundred vendors (tens of thousands of models) would take hours.  Instead the
# catalog is indexed by character trigrams and only entries that share enough of
# them with the page are scored properly.
#
# Exact trigrams are useless here - OCR noise destroys them - so both sides are
# first folded into confusion classes.  "ILSGI0IO" and "TLSG1016" both fold to
# "1156101x" and share five of six trigrams, while an unrelated model shares
# none.  The fold is deliberately lossy: it decides what is worth scoring, and
# the real confusion-aware distance still decides what matches.
# --------------------------------------------------------------------------- #

_CANON_GROUPS = ["0OQDC", "1ILTJ7", "5S", "8B", "2Z", "6G9", "4A", "3EF",
                 "UVY", "MN", "KX", "PR"]
_CANON = {c: g[0] for g in _CANON_GROUPS for c in g}

NGRAM = 3
# Keep a candidate if it shares this fraction of its trigrams with the page, and
# always keep at least this many best-scoring candidates regardless.
BLOCK_MIN_RATIO = 0.34
BLOCK_KEEP = 400
# Below this many models a full scan is cheap enough that blocking only adds risk.
BLOCK_THRESHOLD = 600

_BLOCK_INDEX = None


def canon(text):
    """Fold a squashed string into OCR confusion classes."""
    return "".join(_CANON.get(c, c) for c in text)


def _ngrams(text, n=NGRAM):
    if len(text) < n:
        return {text} if text else set()
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def _build_block_index():
    """trigram -> set of positions in ALL_MODELS."""
    index = {}
    grams = []
    for i, (model, _vendor) in enumerate(ALL_MODELS):
        g = _ngrams(canon(squash(model)))
        grams.append(g)
        for gram in g:
            index.setdefault(gram, set()).add(i)
    return index, grams


def _shortlist(haystacks, keep=BLOCK_KEEP):
    """Catalog entries worth scoring against this page."""
    global _BLOCK_INDEX
    if _BLOCK_INDEX is None or _BLOCK_INDEX[2] != len(ALL_MODELS):
        index, grams = _build_block_index()
        _BLOCK_INDEX = (index, grams, len(ALL_MODELS))
    index, grams, _n = _BLOCK_INDEX

    page = set()
    for _label, text, _w in haystacks:
        page |= _ngrams(canon(text))

    hits = {}
    for gram in page:
        for i in index.get(gram, ()):
            hits[i] = hits.get(i, 0) + 1
    if not hits:
        return list(range(len(ALL_MODELS)))

    scored = [(n / max(len(grams[i]), 1), i) for i, n in hits.items()]
    scored.sort(reverse=True)
    kept = [i for ratio, i in scored if ratio >= BLOCK_MIN_RATIO]
    if len(kept) < keep:
        # Top up with the next best so a borderline true match is not dropped.
        kept = [i for _r, i in scored[:keep]]
    return kept


# A vendor's own list used to be a few dozen strings, so the gated search scanned all
# of it. Against the switch catalogue it is not: Cisco alone carries 541 models, and
# scoring every one against every reconstruction of the page costs ~40 s. The same
# trigram prefilter used on the open catalog applies, with the same top-up so a
# borderline true match is still scored.
VENDOR_POOL_MAX = 150


def _shortlist_pool(pool, haystacks, keep=VENDOR_POOL_MAX):
    """Entries of an arbitrary (model, vendor) list worth scoring against this page."""
    page = set()
    for _label, text, _w in haystacks:
        page |= _ngrams(canon(text))
    if not page:
        return pool
    scored = []
    for i, (model, _vendor) in enumerate(pool):
        g = _ngrams(canon(squash(model)))
        if not g:
            continue
        scored.append((len(page & g) / len(g), i))
    if not scored:
        return pool
    scored.sort(reverse=True)
    kept = [i for ratio, i in scored if ratio >= BLOCK_MIN_RATIO]
    if len(kept) < keep:
        kept = [i for _r, i in scored[:keep]]
    return [pool[i] for i in kept]


# Sightings at or above this level count as independent corroboration.
SIGHTING_MIN = 0.60
# How much repeated corroboration can add to a candidate's rank.  Small on
# purpose: it exists to break ties between near-equal catalog siblings, where
# the one the crops keep half-spelling is the one actually printed on the metal.
CONSENSUS_WEIGHT = 0.08


def _score_model(model, haystacks):
    model_sq = squash(model)
    best, evidence, source, support = 0.0, "", "", 0
    coverage = 0.0
    sightings = []
    for label, text, weight in haystacks:
        score, window = best_substring_similarity(model_sq, text)
        score *= weight
        if score >= 0.80:
            support += 1
        if score >= SIGHTING_MIN:
            sightings.append(score)
        if score > best:
            best, evidence, source = score, window, label
            # How much of the read text this match actually accounts for. The
            # window search is what lets a model match inside a glued blob
            # ("TLSG1016JETSTREAM"), but it also lets a short catalog name match
            # part of a standalone model number and ignore the rest - see
            # _prefer_full_coverage.
            coverage = len(window) / max(len(text), 1)
    # Mean of the strongest sightings: one lucky window scores the same as one
    # honest read, but five independent half-reads of the same silkscreen do not
    # happen by accident.
    sightings.sort(reverse=True)
    top = sightings[:5]
    consensus = (sum(top) / 5.0) * CONSENSUS_WEIGHT if top else 0.0
    return best, evidence, source, support, consensus, round(coverage, 3)


# A single OCR box that is nothing but a model number ("TL-602476P") should be
# explained end to end. Below this, the winner has left part of that box
# unaccounted for and a rival that explains all of it is worth preferring.
COVERAGE_MIN = 0.80
COVERAGE_FULL = 0.95
# How far below the acceptance floor a full-coverage rival may sit and still be
# allowed to challenge. A partial match scores higher than a full one precisely
# because it is normalised over fewer characters, so the true reading is often
# just under the bar.
COVERAGE_CHALLENGE = 0.12


def _is_model_shaped(text):
    """A reading that is a model number on its own, not a model glued to words.

    Letters and digits only (already squashed), both present, and short enough
    that a product name has not run into it.
    """
    return bool(text) and 5 <= len(text) <= 16 and re.search(r"[A-Z]", text) \
        and re.search(r"\d", text)


def _prefer_full_coverage(candidates, haystacks):
    """Promote a rival that explains the whole reading over a winner that does not.

    OCR turned a TL-SG2428P into "TL-602476P". Scored by window, TL-SG1024 fits
    the first six characters at 0.71 and pays nothing for the "76P" it leaves
    behind, while the true TL-SG2428P explains all nine at 0.63 and loses. The
    shorter name wins for being shorter, which is backwards: the reading is a
    standalone model number, so the candidate that accounts for all of it is the
    better answer whenever the two are close.

    Only fires when the winner leaves part of a model-shaped box unexplained, so
    a candidate that already reads end to end is never disturbed.
    """
    if len(candidates) < 2:
        return candidates
    top = candidates[0]
    if top.get("coverage", 1.0) >= COVERAGE_MIN:
        return candidates
    # the box the winner matched inside has to look like a bare model number
    text = next((t for label, t, _w in haystacks if label == top.get("source")), "")
    if not _is_model_shaped(text):
        return candidates
    for c in candidates[1:]:
        if c is top or c.get("coverage", 0.0) < COVERAGE_FULL:
            continue
        if top["score"] - c["score"] > COVERAGE_CHALLENGE:
            break          # ranked by score: nothing further can qualify
        c["rank"] = top["rank"] + 0.001
        c["corroboration"] = list(c["corroboration"]) + [
            "explains all of '%s'; %s accounts for only '%s'"
            % (text, top["model"], top["evidence"])]
        break
    return candidates


def _corroboration(model, descriptors):
    """Nudge a candidate using what the faceplate says about the hardware.

    OCR routinely fails on exactly the one character that separates two real
    products (DES- vs DGS-, -2424P vs -2428P), but the marketing line underneath
    is set in larger type and reads cleanly. These only ever break near-ties.
    """
    bonus, reasons = 0.0, []
    squashed = squash(model)

    if descriptors.get("gigabit") and _FAST_ETHERNET.match(model):
        bonus -= 0.05
        reasons.append("faceplate says Gigabit but this is a Fast Ethernet family")
    elif descriptors.get("gigabit"):
        bonus += 0.015
        reasons.append("Gigabit family agrees with the faceplate")

    ports = descriptors.get("port_counts") or []
    if ports:
        matched = [p for p in ports if p in squashed]
        if matched:
            bonus += 0.04
            reasons.append("port count %s appears in the model number"
                           % "/".join(matched))
        else:
            bonus -= 0.02

    if descriptors.get("poe"):
        if _POE_MODEL.search(squashed):
            bonus += 0.025
            reasons.append("PoE marking matches the PoE model variant")
        else:
            bonus -= 0.015

    return bonus, reasons


def _prefer_poe_sibling(candidates, descriptors):
    """Promote X-P over X when the faceplate shows PoE.

    Length normalisation structurally penalises the PoE variant: it is one
    character longer, and that character is the trailing P that OCR is least
    likely to recover. So DES-1210-28 always outscores DES-1210-28P even on a PoE
    unit. When the photo shows PoE markings, the longer sibling is the better
    answer.
    """
    if not descriptors.get("poe") or len(candidates) < 2:
        return candidates
    top = candidates[0]
    base = squash(top["model"])
    if _POE_MODEL.search(base):
        return candidates  # already the PoE variant
    for c in candidates[1:]:
        sq = squash(c["model"])
        if sq.startswith(base) and _POE_MODEL.search(sq) and len(sq) - len(base) <= 2:
            c["rank"] = top["rank"] + 0.001
            c["corroboration"] = list(c["corroboration"]) + [
                "PoE markings on the faceplate favour this over %s" % top["model"]]
            break
    return candidates


def detect_models(haystacks, vendor=None, descriptors=None):
    """Score the catalog against the page.

    When the logo gave us a vendor, only that vendor's models are considered and
    the bar is lowered - the search space is ~40 strings instead of ~400, so a
    weaker match is still safe. Otherwise the whole catalog is searched at a
    stricter threshold.
    """
    descriptors = descriptors or {}
    if vendor and vendor in MODELS:
        # The logo narrowed this to one vendor's list. That is usually small enough to
        # scan in full, but a workbook-sized catalog puts hundreds of models under one
        # make, so a long list gets the same trigram prefilter as the open catalog.
        pool = [(m, vendor) for m in MODELS[vendor]]
        if len(pool) > VENDOR_POOL_MAX:
            pool = _shortlist_pool(pool, haystacks)
        floor, gated = MODEL_MIN_GATED, True
    elif len(ALL_MODELS) > BLOCK_THRESHOLD:
        pool = [ALL_MODELS[i] for i in _shortlist(haystacks)]
        floor, gated = MODEL_MIN_OPEN, False
    else:
        pool, floor, gated = ALL_MODELS, MODEL_MIN_OPEN, False

    # Port speed is printed in large type and is a property of the hardware, so a
    # confident "Gigabit" reading excludes the Fast Ethernet families outright
    # rather than merely scoring them down.
    exclude_fast = bool(descriptors.get("gigabit_strong"))

    out = []
    for model, model_vendor in pool:
        if len(squash(model)) < MIN_MODEL_LEN:
            continue  # too short to identify reliably from a blurry faceplate
        if exclude_fast and _FAST_ETHERNET.match(model):
            continue
        score, evidence, source, support, consensus, coverage = _score_model(model, haystacks)
        # A full-coverage reading is kept a little below the floor so it can
        # challenge a partial winner in _prefer_full_coverage; it is dropped
        # again below if no such challenge happens.
        if score < floor - (COVERAGE_CHALLENGE if coverage >= COVERAGE_FULL else 0):
            continue
        bonus, reasons = _corroboration(model, descriptors)
        out.append({
            "model": model, "vendor": model_vendor, "score": round(score, 3),
            "rank": round(score + bonus + consensus, 4), "corroboration": reasons,
            "evidence": evidence, "source": source, "support": support,
            "consensus": round(consensus, 4), "coverage": coverage,
            "exact": score >= 0.999, "gated": gated, "floor": floor,
        })

    out.sort(key=lambda c: (-c["rank"], -c["support"], -len(squash(c["model"]))))
    out = _prefer_poe_sibling(out, descriptors)
    out = _prefer_full_coverage(out, haystacks)
    # Longer catalog strings are more specific, so they break ties upward.
    out.sort(key=lambda c: (-c["rank"], -c["support"], -len(squash(c["model"]))))
    # sub-floor entries only existed to let a full-coverage reading challenge a
    # partial winner; one that did not win is dropped again
    return [c for i, c in enumerate(out) if i == 0 or c["score"] >= c["floor"]]


def extract_family_candidates(haystacks):
    """Pull model-shaped tokens out of the text even if no catalog entry matches."""
    found, seen = [], set()
    for _label, text, _w in haystacks:
        for pattern, vendor in FAMILY_PATTERNS:
            for m in re.finditer(pattern, text):
                if m.group(0) not in seen:
                    seen.add(m.group(0))
                    found.append({"raw": m.group(0), "vendor": vendor})
    found.sort(key=lambda f: -len(f["raw"]))
    kept = []
    for f in found:
        if not any(f["raw"] in k["raw"] for k in kept):
            kept.append(f)
    return kept


# Last-resort model shape for a vendor with no FAMILY_PATTERNS entry: a few
# letters, some digits, maybe a trailing suffix. Loose on purpose - this only
# runs when nothing vendor-specific matched anything at all.
_GENERIC_MODEL_RE = re.compile(r"[A-Z]{2,6}\d{2,5}[A-Z0-9]{0,8}")

# Words that OCR reads near the logo but that are never the vendor name.
_NOT_A_VENDOR = {
    "GIGABIT", "MANAGED", "UNMANAGED", "SMART", "WEBSMART", "JETSTREAM",
    "SWITCH", "SWITCHES", "NETWORK", "NETWORKS", "ETHERNET", "PORT", "PORTS",
    "POE", "POE+", "SFP", "UPLINK", "RACKMOUNT", "RACK", "MOUNT", "SERIES",
    "MODEL", "MADE", "CHINA", "WARNING", "CAUTION", "RESET", "CONSOLE",
    "POWER", "STATUS", "LINK", "SPEED", "FULL", "HALF", "DUPLEX",
}


# Digits that OCR returns as letters on a silkscreened panel.
_DIGITY = str.maketrans({"I": "1", "L": "1", "T": "1", "O": "0", "Q": "0", "D": "0",
                         "S": "5", "B": "8", "G": "6", "Z": "2"})


def _is_port_numbering(token):
    """Is this token the row of port numbers printed along the panel?

    Switches silkscreen "1 2 3 ... 23 24" above the jacks, and OCR returns it as one
    run with the digits half-read as letters - "TTIOIO101112" is 1,1,1,0,1,0 then
    10,11,12. It is model-shaped to any regex, so a model read off a panel has to be
    told apart from the numbering printed beside it: a model number does not count
    upwards."""
    digits = token.upper().translate(_DIGITY)
    if not digits.isdigit() or len(digits) < 4:
        return False
    for width in (1, 2):
        nums = [int(digits[i:i + width]) for i in range(0, len(digits) - width + 1, width)]
        run = 1
        for a, b in zip(nums, nums[1:]):
            run = run + 1 if b == a + 1 else 1
            if run >= 3:
                return True
    return False


def extract_generic_model_guess(haystacks, exclude_raws=()):
    """Loosest possible model-shaped token, for a vendor with no catalog and no
    FAMILY_PATTERNS entry. Only meant to be used when everything else found
    nothing - it is a guess, not a match, and callers must flag it as such."""
    exclude = {squash(r) for r in exclude_raws}
    found, seen = [], set()
    for _label, text, weight in haystacks:
        for m in _GENERIC_MODEL_RE.finditer(text):
            raw = m.group(0)
            if raw in seen or raw in exclude or len(raw) < 5:
                continue
            if _is_port_numbering(raw):
                continue
            seen.add(raw)
            found.append({"raw": raw, "weight": weight, "support": text.count(raw)})
    found.sort(key=lambda f: (-f["weight"], -f["support"], -len(f["raw"])))
    return found[0]["raw"] if found else None


def extract_vendor_guess(doc):
    """Most plausible company-name-shaped word read anywhere on the panel, for
    a vendor with no entry in VENDOR_ALIASES. Checks whole tokens first, then
    same-line adjacent pairs - a short two-part brand ("Wi-Tek") often lands
    as two separate OCR boxes, each too short alone to look like a name."""
    dets = [d for d in doc.get("detections", []) if d.get("text")]
    dets += [d for d in doc.get("roi_detections", []) if d.get("text")]

    def clean(text):
        w = re.sub(r"[^A-Za-z]", "", text)
        return "" if not w or w.upper() in _NOT_A_VENDOR else w

    candidates = []  # (conf, word)
    for d in dets:
        w = clean(d["text"])
        if len(w) >= 4:
            candidates.append((d.get("conf", 0.0), w))

    spatial = sorted(dets, key=_sort_key_spatial)
    for a, b in zip(spatial, spatial[1:]):
        if _sort_key_spatial(a)[:2] != _sort_key_spatial(b)[:2]:
            continue  # not the same printed line
        wa, wb = clean(a["text"]), clean(b["text"])
        if wa and wb and (len(wa) < 4 or len(wb) < 4) and len(wa) + len(wb) >= 4:
            candidates.append((min(a.get("conf", 0.0), b.get("conf", 0.0)), wa + wb))

    if not candidates:
        return None, 0.0
    conf, word = max(candidates, key=lambda c: c[0])
    return word[:1].upper() + word[1:].lower(), conf


def extract_descriptors(haystacks):
    """Marketing text ('24-PORT GIGABIT MANAGED') used as corroboration."""
    joined = " ".join(t for _l, t, _w in haystacks)
    out = {}
    for pattern, key in DESCRIPTOR_HINTS:
        if key == "port_count":
            # A PoE unit prints two counts ("28-Port ... with 24-Port PoE+"), and
            # either may be the one that appears in the model number.
            counts = sorted({m.group(1) for m in re.finditer(pattern, joined)})
            if counts:
                out["port_counts"] = counts
            continue
        if re.search(pattern, joined):
            out[key] = True

    for word, key in FUZZY_DESCRIPTOR_HINTS:
        if out.get(key):
            continue
        score, window = best_substring_similarity(word, joined)
        if score >= FUZZY_DESCRIPTOR_MIN:
            out[key] = True
            out.setdefault("_fuzzy", {})[key] = (round(score, 2), window)
            if score >= FUZZY_DESCRIPTOR_STRONG:
                out[key + "_strong"] = True
    return out


# --------------------------------------------------------------------------- #
# Port spec from the model name (pipeline step 3)
#
# Many model numbers spell their port layout out: CRS328-24P-4S+RM is 24 RJ45 +
# 4 SFP, C1000-24T-4G-L is 24 + 4, S5735-L24T4S-A is 24 + 4.  Knowing the
# expected counts is what lets a later template/spec check reject a vendor photo
# of the wrong unit.  Names that do not spell the split out (TL-SG3424, GS724T)
# yield nothing - no count is ever assumed.
# --------------------------------------------------------------------------- #

# Copper tokens: 24P / 24T / 48GT / 24TC...  Fibre tokens: 4S / 4S+ / 4X / 4Y /
# 2Q / 4SFP.  Only letters that name a port type count; a bare number is a
# series digit, not a port count.  The trailing guard allows a digit so that
# glued splits parse (S5735-L24T4S: 24T then 4S) but refuses another letter,
# which would make the token something else (28TC is TC, not T).
_COPPER_TOKEN = re.compile(r"(?<![0-9])(\d{1,2})(?:G?)(P|T|GT|TP|TC|PC)(?![A-Z])")
_FIBRE_TOKEN = re.compile(r"(?<![0-9])(\d{1,2})(S\+?|X|XS|Y|Q|SFP\+?)(?![A-Z])")


def parse_port_spec(model):
    """Expected port counts spelled out by the model name, or None.

    Returns {"rj45": n, "sfp": m} with either key optional.  Deliberately
    conservative: a wrong expectation is worse than none, because downstream it
    is used to *reject* templates.
    """
    if not model:
        return None
    # Work on the hyphen-separated tail segments; the leading segment is the
    # series name, whose digits (CRS328, S5735) are not port counts.
    text = re.sub(r"\s+", "-", model.upper())
    segments = text.split("-")[1:]
    if not segments:
        # Single-token names such as S5735L24T4SA still spell counts inline.
        segments = [text]

    rj45 = sfp = 0
    for seg in segments:
        for m in _COPPER_TOKEN.finditer(seg):
            rj45 += int(m.group(1))
        for m in _FIBRE_TOKEN.finditer(seg):
            sfp += int(m.group(1))

    spec = {}
    # A lone copper-looking token is as likely a TOTAL as an RJ45 count
    # (DGS-1210-28P is 24 copper + 4 combo, not 28 copper), so copper is only
    # trusted when the name also spells the fibre side - a genuine split.
    if 0 < rj45 <= 96 and 0 < sfp <= 32:
        spec["rj45"] = rj45
    if 0 < sfp <= 32:
        spec["sfp"] = sfp
    return spec or None


def _confidence_label(score):
    if score >= 0.93:
        return "high"
    if score >= 0.82:
        return "medium"
    return "low" if score > 0 else "none"


def _common_prefix(a, b):
    """Length of the shared leading run of two model numbers, ignoring separators."""
    n = 0
    for ca, cb in zip(squash(a), squash(b)):
        if ca != cb:
            break
        n += 1
    return n


def _same_family(a, b):
    """True when two catalog entries differ only in a trailing revision suffix."""
    base_a = re.sub(r"[A-Z]+$", "", squash(a))
    base_b = re.sub(r"[A-Z]+$", "", squash(b))
    return bool(base_a) and base_a == base_b


def identify(doc):
    """Combine vendor, catalog and faceplate evidence into a single verdict."""
    haystacks = build_haystacks(doc)
    descriptors = extract_descriptors(haystacks)

    vendor_hits = detect_vendor(vendor_haystacks(doc), heights=_text_heights(doc))
    vendor_from_logo = vendor_hits[0]["vendor"] if vendor_hits else None

    model_hits = detect_models(haystacks, vendor=vendor_from_logo,
                               descriptors=descriptors)
    # A gated search that finds nothing may mean the logo was misread - but a
    # weak match from some other vendor's catalog is far more likely to be noise
    # than a real correction, so only a near-perfect one is allowed through.
    if vendor_from_logo and not model_hits:
        model_hits = [c for c in detect_models(haystacks, descriptors=descriptors)
                      if c["score"] >= MODEL_MIN_CROSS]

    family_hits = extract_family_candidates(haystacks)
    unexplained = [f for f in family_hits
                   if not any(similarity(squash(c["model"]), f["raw"]) >= 0.78
                              for c in model_hits)]

    model = model_vendor = None
    model_score, model_evidence, model_source = 0.0, "", ""
    in_catalog = False
    if model_hits:
        top = model_hits[0]
        model, model_vendor = top["model"], top["vendor"]
        model_score, model_evidence = top["score"], top["evidence"]
        model_source, in_catalog = top["source"], True
    elif unexplained:
        top = unexplained[0]
        model, model_vendor = top["raw"], top["vendor"]
        model_score, model_evidence = 0.60, top["raw"]
        model_source = "family-pattern"

    # The model number identifies the maker more reliably than a blurred logo.
    vendor = vendor_from_logo or model_vendor
    conflict = bool(vendor_from_logo and model_vendor
                    and vendor_from_logo != model_vendor)
    if conflict and model_score >= 0.95:
        vendor = model_vendor

    vendor_score = vendor_hits[0]["score"] if vendor_hits else (
        model_score if model_vendor else 0.0)
    if vendor_from_logo and model_vendor and vendor_from_logo == model_vendor:
        vendor_score = min(1.0, vendor_score + 0.05)  # logo and model agree

    # Runners-up within noise of the winner that share most of their characters
    # mean the photo did not actually resolve the difference - say so rather than
    # presenting a coin flip as a result.
    # A near-exact reading has already resolved the difference, so only flag the
    # ambiguity when the winner itself was a stretch.
    variants = [] if model_score >= 0.93 else [
        c for c in model_hits[1:]
        if model_score - c["score"] <= 0.06
        and (_same_family(c["model"], model or "")
             or _common_prefix(c["model"], model or "") >= 6
             # Near-twins one glyph apart (DGS-1016D / DES-1016D): the pixels
             # that would separate them are exactly the ones OCR got wrong.
             or weighted_distance(squash(c["model"]), squash(model or "")) <= 1.2)]

    # Racks are photographed as stacks, so a second model number in the frame is
    # usually a second switch rather than a competing reading of the first.
    secondary, seen = [], {model}
    for c in model_hits[1:]:
        if c["model"] in seen or _same_family(c["model"], model or ""):
            continue
        if c["vendor"] != vendor:
            continue
        if c["score"] >= 0.84 and c["support"] >= 2:
            secondary.append(c)
            seen.add(c["model"])

    # Last resort: a vendor this tool has never seen has no alias to match and
    # no FAMILY_PATTERNS entry, so everything above comes back empty. Rather
    # than forcing a person to type it in, take the best raw OCR reading as an
    # unconfirmed guess - clearly flagged, never persisted to the catalog
    # unless a later exact web hit confirms it (remember_model's own gate).
    vendor_unconfirmed = model_unconfirmed = False
    vendor_guess_conf = 0.0
    if not vendor:
        guess, vendor_guess_conf = extract_vendor_guess(doc)
        if guess:
            vendor = guess
            vendor_unconfirmed = True
    if not model:
        guess = extract_generic_model_guess(haystacks, exclude_raws=[f["raw"] for f in family_hits])
        if guess:
            model = guess
            model_vendor = model_vendor or vendor
            model_evidence = guess
            model_source = "raw-ocr-guess"
            model_unconfirmed = True

    return {
        "vendor": vendor,
        "vendor_score": round(vendor_score, 3) if vendor and not vendor_unconfirmed else 0.0,
        "vendor_confidence": ("guess" if vendor_unconfirmed else
                              (_confidence_label(vendor_score) if vendor else "none")),
        "vendor_evidence": (vendor_hits[0]["evidence"] if vendor_hits else
                            ("(from model)" if model_vendor and not vendor_unconfirmed else
                             "raw OCR reading, not matched to a known vendor (conf %.2f)"
                             % vendor_guess_conf if vendor_unconfirmed else "")),
        "vendor_from_logo": bool(vendor_from_logo),
        # another brand read almost as well: the caller should treat the vendor as doubtful
        "vendor_runner_up": (vendor_hits[1]["vendor"] if len(vendor_hits) > 1
                             and vendor_hits[0]["score"] - vendor_hits[1]["score"] <= 0.05 else None),
        "vendor_unconfirmed": vendor_unconfirmed,
        "model": model,
        "model_score": round(model_score, 3) if model and not model_unconfirmed else 0.0,
        "model_confidence": "guess" if model_unconfirmed else (_confidence_label(model_score) if model else "none"),
        "model_evidence": model_evidence,
        "model_source": model_source,
        "model_unconfirmed": model_unconfirmed,
        "in_catalog": in_catalog,
        "port_spec": parse_port_spec(model),
        "vendor_model_conflict": conflict,
        "suffix_ambiguous": bool(variants),
        "variants": variants[:3],
        "corroboration": model_hits[0]["corroboration"] if model_hits else [],
        "alternatives": model_hits[1:6],
        "secondary_devices": secondary[:3],
        "family_tokens": [f["raw"] for f in family_hits][:6],
        "descriptors": descriptors,
    }


# =========================================================================== #
# OCR
# =========================================================================== #

# Model numbers use this alphabet; restricting it stops the recognizer from
# emitting punctuation noise on the targeted crops.
MODEL_ALLOWLIST = ("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                   "0123456789-+/ ")

_READER = None


def get_reader(gpu=False, languages=("en",)):
    global _READER
    if _READER is None:
        import easyocr
        _READER = easyocr.Reader(list(languages), gpu=gpu, verbose=False)
    return _READER


def _upscale(img, factor):
    import cv2
    if abs(factor - 1.0) < 1e-6:
        return img
    return cv2.resize(img, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)


def _clahe(gray, clip=3.0, grid=8):
    import cv2
    return cv2.createCLAHE(clipLimit=clip, tileGridSize=(grid, grid)).apply(gray)


def _unsharp(img, amount=1.5, radius=3):
    import cv2
    return cv2.addWeighted(img, 1 + amount,
                           cv2.GaussianBlur(img, (0, 0), radius), -amount, 0)


def _scale_for(img, target_height):
    """Pick an upscale factor that brings a strip to a readable height."""
    return max(1.0, min(12.0, target_height / max(img.shape[0], 1)))


def _gray(img):
    import cv2
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img


def build_variants(img):
    """Return [(name, image)] renderings of the whole photo."""
    import cv2
    h, w = img.shape[:2]
    gray = _gray(img)
    base = _scale_for(img, 480)

    variants = [("original", img), ("upscaled", _upscale(img, base))]

    cl = _clahe(_upscale(gray, base))
    variants.append(("gray_clahe", cl))
    # Faceplate text is usually light on a dark chassis; inverting puts it in the
    # dark-on-light polarity the recognizer was trained on.
    variants.append(("gray_clahe_inv", cv2.bitwise_not(cl)))
    variants.append(("sharpened", _unsharp(_upscale(gray, base * 1.4))))
    variants.append(("denoised", cv2.fastNlMeansDenoising(cl, None, 10, 7, 21)))

    # Vendor logo and model number sit on the left of a rack-mount faceplate.
    left = img[:, :max(8, int(w * 0.45))]
    left_gray = _clahe(_upscale(_gray(left), _scale_for(left, 700)))
    variants.append(("left_panel", left_gray))
    variants.append(("left_panel_inv", cv2.bitwise_not(left_gray)))
    variants.append(("left_panel_sharp", _unsharp(left_gray, amount=1.2)))

    # Some units print the full product description to the right of the ports.
    right = img[:, int(w * 0.55):]
    right_gray = _clahe(_upscale(_gray(right), _scale_for(right, 600)))
    variants.append(("right_panel", right_gray))
    variants.append(("right_panel_inv", cv2.bitwise_not(right_gray)))
    return variants


_LOGO_HINT = re.compile(
    r"(LINK|CISCO|DELL|NETGEAR|ARUBA|PROCURVE|JUNIPER|UBIQUITI|UNIFI|MIKROTIK"
    r"|ZYXEL|HUAWEI|EXTREME|LINKSYS|ARISTA|BROCADE|3COM|QNAP|PLANET|TP|JETSTREAM"
    r"|OMADA|POWERCONNECT|CATALYST|PROSAFE)", re.I)


def logo_rois(img, detections):
    """Crops likely to contain the model number, anchored on the brand name.

    Vendors print the model either immediately below the logo (D-Link, Dell,
    Cisco) or on the same baseline just to its right (TP-Link), so both
    neighbourhoods are returned, plus fallbacks that need no logo at all.
    """
    h, w = img.shape[:2]
    anchors = []
    for d in detections:
        if not d.get("box") or not _LOGO_HINT.search(d["text"]):
            continue
        xs = [p[0] for p in d["box"]]
        ys = [p[1] for p in d["box"]]
        sx, sy = d.get("scale_x", 1.0) or 1.0, d.get("scale_y", 1.0) or 1.0
        ox, oy = d.get("offset_x", 0.0), d.get("offset_y", 0.0)
        # Map the box back onto the original photo's coordinate system.
        anchors.append((min(xs) / sx + ox, min(ys) / sy + oy,
                        max(xs) / sx + ox, max(ys) / sy + oy, d["conf"]))
    anchors.sort(key=lambda a: -a[4])

    rois = []
    for i, (x0, y0, x1, y1, _c) in enumerate(anchors[:2]):
        bw, bh = max(x1 - x0, 4), max(y1 - y0, 4)
        # Below the logo: where D-Link / Dell / TP-Link print the model.  The
        # model line is set to roughly the logo's own width, so a crop much wider
        # than the logo drags in the LED array and port numbers - that noise both
        # distracts the detector and skews the magnification, which is what costs
        # whole characters ("TL-SG2428P" coming back as "IL-S6242P").
        rois.append(("logo%d_below" % i,
                     (x0 - bw * 0.35, y0 + bh * 0.4, x1 + bw * 0.6, y1 + bh * 3.0)))
        # A wider, shorter version for faceplates that set the model in one long
        # line under the brand.
        rois.append(("logo%d_under" % i,
                     (x0 - bw * 0.5, y0 + bh * 0.8, x1 + bw * 1.8, y1 + bh * 4.5)))
        # Same baseline, to the right: where some TP-Link units put it.
        rois.append(("logo%d_right" % i,
                     (x0 - bw * 0.2, y0 - bh * 0.8, x1 + bw * 3.0, y1 + bh * 1.5)))

    # D-Link in particular prints the model on the bottom edge of the faceplate.
    rois.append(("band_lower_left", (0.0, h * 0.50, w * 0.42, h * 1.0)))
    rois.append(("band_upper_left", (0.0, 0.0, w * 0.42, h * 0.55)))
    rois.append(("band_left_full", (0.0, 0.0, w * 0.30, h * 1.0)))

    out = []
    for name, (x0, y0, x1, y1) in rois:
        x0 = int(max(0, min(w - 2, x0)))
        y0 = int(max(0, min(h - 2, y0)))
        x1 = int(max(x0 + 2, min(w, x1)))
        y1 = int(max(y0 + 2, min(h, y1)))
        crop = img[y0:y1, x0:x1]
        if crop.size and crop.shape[0] >= 4 and crop.shape[1] >= 8:
            out.append((name, crop))
    return out


def roi_renders(crop):
    """Aggressive renderings of a small crop, at several magnifications.

    The recognizer is sensitive to glyph height, and the right magnification for
    a six-pixel-tall silkscreen is not knowable in advance, so a few are tried.
    """
    import cv2
    gray = _gray(crop)
    renders = []
    # (target height, resampler, CLAHE clip) - the middle size gets both
    # resamplers and both contrast settings because that is where a sweep put
    # every successful reading; the outer sizes are cheap insurance.  Lanczos is
    # the primary enlarger: on type this small, bicubic's softer edges cost whole
    # characters, but the two disagree often enough to keep one bicubic pass.
    recipes = [
        (200, cv2.INTER_LANCZOS4, "L", 4.0, "b"),
        (400, cv2.INTER_LANCZOS4, "L", 2.0, "a"),
        (400, cv2.INTER_LANCZOS4, "L", 4.0, "b"),
        (400, cv2.INTER_CUBIC, "C", 2.0, "a"),
        (400, cv2.INTER_CUBIC, "C", 4.0, "b"),
        (650, cv2.INTER_LANCZOS4, "L", 2.0, "a"),
    ]
    for target, interp, tag, clip, ctag in recipes:
        scale = _scale_for(gray, target)
        if scale <= 1.0 and target > 200:
            continue
        g = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=interp)
        cl = _clahe(g, clip=clip)
        renders.append(("x%d%s%s" % (target, tag, ctag), cl))
        renders.append(("x%d%s%s_inv" % (target, tag, ctag), cv2.bitwise_not(cl)))

    mid = _upscale(gray, _scale_for(gray, 400))
    sharp = _unsharp(_clahe(mid, clip=4.0), amount=1.3)
    renders.append(("sharp", sharp))
    renders.append(("sharp_inv", cv2.bitwise_not(sharp)))
    # Flattening the illumination helps grey-on-grey legends.
    norm = cv2.normalize(mid, None, 0, 255, cv2.NORM_MINMAX)
    renders.append(("norm", norm))
    renders.append(("norm_inv", cv2.bitwise_not(norm)))
    return renders


def _run(reader, image, allowlist=None, text_threshold=0.5, low_text=0.3):
    kwargs = dict(detail=1, paragraph=False, text_threshold=text_threshold,
                  low_text=low_text, link_threshold=0.4, mag_ratio=1.5)
    if allowlist:
        kwargs["allowlist"] = allowlist
    try:
        return reader.readtext(image, **kwargs)
    except Exception:
        return []


def _merge(target, text, conf, source, box=None, scale=None, offset=None):
    text = (text or "").strip()
    if not text:
        return
    key = text.upper()
    entry = target.setdefault(key, {
        "text": text, "conf": 0.0, "sources": [], "box": None,
        "scale_x": 1.0, "scale_y": 1.0, "offset_x": 0.0, "offset_y": 0.0})
    if conf > entry["conf"]:
        entry["conf"], entry["text"] = float(conf), text
        if box is not None:
            entry["box"] = [[float(p[0]), float(p[1])] for p in box]
            entry["scale_x"], entry["scale_y"] = scale or (1.0, 1.0)
            entry["offset_x"], entry["offset_y"] = offset or (0.0, 0.0)
    if source not in entry["sources"]:
        entry["sources"].append(source)


def read_image(path, gpu=False, deep=True):
    """OCR a photo in two stages; return (stage1, stage2, source_names)."""
    import cv2
    import numpy as np

    reader = get_reader(gpu=gpu)
    # imdecode rather than imread: imread cannot open paths with non-ASCII
    # characters on Windows, which filenames in the wild frequently have.
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
    if img is None:
        raise IOError("could not read %s" % path)
    h, w = img.shape[:2]

    # --- stage 1: whole frame, several renderings ---------------------------
    stage1, sources = {}, []
    for name, variant in build_variants(img):
        sources.append(name)
        vh, vw = variant.shape[:2]
        # Variants are scaled and some are cropped; record the mapping so boxes
        # can be projected back onto the original photo.
        if name.startswith("left_panel"):
            sx = sy = vw / max(w * 0.45, 1)
            off = (0.0, 0.0)
        elif name.startswith("right_panel"):
            sx = sy = vw / max(w * 0.45, 1)
            off = (w * 0.55, 0.0)
        else:
            sx, sy, off = vw / w, vh / h, (0.0, 0.0)
        for box, text, conf in _run(reader, variant, text_threshold=0.55):
            _merge(stage1, text, conf, name, box, (sx, sy), off)

    # --- stage 2: magnified crops around the brand name ---------------------
    stage2 = {}
    if deep:
        for roi_name, crop in logo_rois(img, list(stage1.values())):
            for render_name, render in roi_renders(crop):
                source = "%s/%s" % (roi_name, render_name)
                sources.append(source)
                # Low thresholds: these crops are known to contain faint text, so
                # recall matters more than precision - the catalog filters later.
                for _b, text, conf in _run(reader, render,
                                           allowlist=MODEL_ALLOWLIST,
                                           text_threshold=0.25, low_text=0.2):
                    _merge(stage2, text, conf, source)

    def finish(d):
        out = sorted(d.values(), key=lambda x: -x["conf"])
        for e in out:
            e["conf"] = round(e["conf"], 3)
        return out

    return finish(stage1), finish(stage2), sources


# =========================================================================== #
# Reference images
#
# Once a switch is identified, the report shows what that model actually looks
# like.  The vendor workbooks are the authority and are tried first; anything
# they do not list is looked up online, because a rack is full of discontinued
# kit that current product sheets no longer carry.
# =========================================================================== #

class ReferenceLibrary(object):
    """Reference photos for a model, from the vendor workbooks.

    Built from the catalog file produced by build_switch_catalog.py, which
    records where each picture lives inside its workbook rather than copying it
    out -- the workbooks run to gigabytes and only a handful are ever needed.
    """

    def __init__(self, catalog_path=None, excel_dir=None):
        self.by_model = {}      # squashed model -> record
        self.excel_dir = Path(excel_dir).expanduser() if excel_dir else None
        self.loaded = False
        if not catalog_path:
            return
        path = Path(catalog_path).expanduser()
        if not path.exists():
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (ValueError, OSError):
            return
        if self.excel_dir is None and data.get("generated_from"):
            self.excel_dir = Path(data["generated_from"])
        for rec in data.get("models") or []:
            if not rec.get("images"):
                continue
            for name in expand_models(rec.get("model") or ""):
                self.by_model.setdefault(squash(name), rec)
        self.loaded = True

    def lookup(self, model):
        """Workbook picture for this exact model, or None.

        Matched on the exact model number only.  A near neighbour is a different
        product, and showing one switch's photo beside another's reading would
        quietly turn an uncertain identification into a confident-looking one.
        """
        rec = self.by_model.get(squash(model or ""))
        if not rec or not self.excel_dir:
            return None
        book = self.excel_dir / rec.get("source", "")
        if not book.exists():
            return None
        try:
            with zipfile.ZipFile(book) as zf:
                data = zf.read(rec["images"][0])
        except (KeyError, OSError, zipfile.BadZipFile):
            return None
        return {"bytes": data, "detail": "%s : %s" % (book.name, rec["images"][0]),
                "record": rec}


_SKU_SPLIT = re.compile(r"\s*/\s*")


def expand_models(cell):
    """Expand a workbook cell that packs several SKUs into one string.

    Vendor sheets abbreviate families: "C9300-48T/48P/48U" is three products and
    "CBS350-24T-4G/24P-4G" is two, so each is listed in full.
    """
    cell = re.sub(r"\s+", " ", (cell or "").strip())
    if not cell:
        return []
    parts = [p for p in _SKU_SPLIT.split(cell) if p]
    if len(parts) == 1:
        return [cell]
    base = parts[0]
    out = [base]
    # Everything up to the first hyphen is the stem the shorthand hangs off.
    stem = base[:base.index("-") + 1] if "-" in base else ""
    for part in parts[1:]:
        candidate = (stem + part) if (stem and not part.upper()
                                      .startswith(stem.upper())) else part
        if candidate and candidate not in out:
            out.append(candidate)
    return out


_UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                   " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,*/*;q=0.8",
}


def _safe_url(url):
    """Percent-encode a URL so non-ASCII characters survive the request.

    Image results routinely carry accented or CJK characters in their path, and
    http.client encodes the request line as ASCII, so these raise rather than
    404 -- an unhandled crash in the middle of a long run.
    """
    parts = urllib.parse.urlsplit(url)
    try:
        netloc = parts.netloc.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        netloc = parts.netloc
    return urllib.parse.urlunsplit((
        parts.scheme, netloc,
        urllib.parse.quote(parts.path, safe="/%@:"),
        urllib.parse.quote(parts.query, safe="=&%?+/:,"),
        ""))


def _http_get(url, timeout=25):
    req = urllib.request.Request(_safe_url(url), headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# A rack switch photographed straight on is a wide, short strip; the same unit
# shot at an angle is much closer to square.  Aspect ratio is therefore a direct
# test of "is this a front-panel view", which is the only view worth putting
# beside a faceplate photo.
STRAIGHT_MIN_ASPECT = 3.2
GOOD_ENOUGH_ASPECT = 4.5
MAX_CANDIDATES = 10


def _content_box(arr):
    """Bounding box of the subject, ignoring a flat background.

    Product photos are routinely padded onto a square white canvas, so the file's
    own proportions say nothing about the shape of the thing in it: the official
    TP-Link shot of a 1U switch is a 4:3 image of a 5.4:1 switch.  Measuring the
    subject instead is what makes "is this a front-panel view" answerable.
    """
    import cv2
    import numpy as np
    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY) if arr.ndim == 3 else arr
    h, w = gray.shape[:2]
    if h < 8 or w < 8:
        return None
    border = np.concatenate([gray[0], gray[-1], gray[:, 0], gray[:, -1]])
    mask = np.abs(gray.astype(np.int16) - int(np.median(border))) > 18
    ys, xs = np.where(mask)
    if len(xs) < 32:
        return None
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _aspect(data):
    """(subject width / height, subject width) for encoded bytes, or (0, 0)."""
    try:
        import cv2
        import numpy as np
        arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            return 0.0, 0
        box = _content_box(arr)
        if not box:
            h, w = arr.shape[:2]
            return (w / float(h), w) if h else (0.0, 0)
        x0, y0, x1, y1 = box
        return (x1 - x0 + 1) / float(y1 - y0 + 1), x1 - x0 + 1
    except Exception:
        return 0.0, 0


def search_reference_image(vendor, model, timeout=25, keep=1):
    """Find a straight-on product photo online for a model the workbooks lack.

    Two filters, both necessary:

    * the model number must appear in the result's title or URL - image search
      happily returns another product from the same brand, and a confident photo
      of the wrong switch is worse than no photo;
    * the image must be wide and short - a front-panel view.  Most product shots
      are three-quarter renders, which are useless for comparing against a
      faceplate.
    """
    if os.environ.get("PORT_AGENT_SEARCH_ENGINES", "0") != "1":
        return None   # search-engine scraping (Bing/Yandex through a browser) is opt-in (review #27)
    query = "%s %s switch front panel" % (vendor or "", model)
    url = ("https://www.bing.com/images/async?q=%s&first=0&count=35"
           "&adlt=moderate&mmasync=1" % urllib.parse.quote(query))
    try:
        page = _http_get(url, timeout).decode("utf-8", "replace")
    except Exception:
        # A reference photo is a nicety; nothing here is worth failing a run over.
        return None

    target = squash(model)
    found = []
    tried = 0
    for attr in re.findall(r'\sm="([^"]*)"', page):
        if tried >= MAX_CANDIDATES:
            break
        if "murl" not in attr:
            continue
        try:
            item = json.loads(html.unescape(attr))
        except ValueError:
            continue
        murl, title = item.get("murl") or "", item.get("t") or ""
        if not murl or target not in squash(title + murl):
            continue
        try:
            data = _http_get(murl, timeout)
        except Exception:
            continue  # dead host, bad certificate, unencodable URL - try the next
        if len(data) < 2000:
            continue  # a placeholder or tracking pixel, not a product shot
        tried += 1

        ratio, width = _aspect(data)
        if ratio < STRAIGHT_MIN_ASPECT:
            continue
        found.append({"bytes": data, "url": murl, "title": title,
                      "aspect": round(ratio, 2), "width": width})
        if keep == 1 and ratio >= GOOD_ENOUGH_ASPECT:
            break  # clearly a front-panel strip, no need to keep downloading
    # Wider is more side-on; among those, prefer the larger file. But the widest image
    # can be a band cropped out of the panel, so with keep > 1 the runners-up go along
    # and the caller's pixel matching decides.
    found.sort(key=lambda c: (c["aspect"], c["width"]), reverse=True)
    if keep == 1:
        return found[0] if found else None
    return found[:keep]


def shrink(data, max_width=620, trim=True):
    """Crop the padding off a reference image and downscale it for the report.

    Trimming matters as much as scaling: a switch occupying a third of a square
    white canvas is unreadable beside the test photo, and the padding is what
    makes it small.
    """
    try:
        import cv2
        import numpy as np
        arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            return data, "image/png"
        if trim:
            box = _content_box(arr)
            if box:
                x0, y0, x1, y1 = box
                pad = max(2, int(0.02 * (x1 - x0)))
                h0, w0 = arr.shape[:2]
                arr = arr[max(0, y0 - pad):min(h0, y1 + pad + 1),
                          max(0, x0 - pad):min(w0, x1 + pad + 1)]
                if arr.size == 0:
                    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        h, w = arr.shape[:2]
        if w > max_width:
            arr = cv2.resize(arr, (max_width, max(1, int(h * max_width / w))),
                             interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", arr, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        if ok:
            return buf.tobytes(), "image/jpeg"
    except Exception:
        pass
    return data, "image/png"


def resolve_reference(library, result, cache_dir, allow_web=True):
    """Reference photo for an identified switch: workbook first, then the web."""
    out = {"data": None, "mime": "image/jpeg", "origin": None, "detail": "",
           "record": None}
    vendor, model = result.get("vendor"), result.get("model")
    if not model:
        return out

    found = library.lookup(model) if library.loaded else None
    if found:
        data, mime = shrink(found["bytes"])
        out.update(data=data, mime=mime, origin="workbook",
                   detail=found["detail"], record=found["record"])
        return out

    if not allow_web:
        return out

    cached = cache_dir / (re.sub(r"[^A-Za-z0-9._-]+", "_",
                                 "%s_%s" % (vendor or "x", model))[:120] + ".jpg")
    if cached.exists():
        out.update(data=cached.read_bytes(), mime="image/jpeg", origin="web",
                   detail="cached: %s" % cached.name)
        return out

    hit = search_reference_image(vendor, model)
    if hit:
        data, mime = shrink(hit["bytes"])
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(data)
        except OSError:
            pass
        out.update(data=data, mime=mime, origin="web", detail=hit["url"],
                   aspect=hit.get("aspect"))
    return out


# =========================================================================== #
# Report
# =========================================================================== #

CSS = """
:root{
  --bg:#f6f7f9; --panel:#fff; --ink:#15181d; --muted:#656d7a; --line:#e2e5ea;
  --accent:#2f5fd0; --high:#1b7f4d; --high-bg:#e6f4ec;
  --med:#a2650a; --med-bg:#fdf1dc; --low:#a53030; --low-bg:#fbe9e9; --chip:#eef1f6;
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#111317; --panel:#1a1d23; --ink:#e8eaee; --muted:#99a1ad; --line:#2b3039;
    --accent:#7aa2f7; --high:#5ed89b; --high-bg:#16301f;
    --med:#e3b341; --med-bg:#33280d; --low:#f08a8a; --low-bg:#3a1c1c; --chip:#242933;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1500px;margin:0 auto;padding:28px 20px 64px}
h1{font-size:23px;margin:0 0 4px;letter-spacing:-.01em}
.sub{color:var(--muted);margin:0 0 20px;font-size:13px}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:22px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:10px 16px;min-width:120px}
.stat b{display:block;font-size:21px;line-height:1.2}
.stat span{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em}
.tablewrap{overflow-x:auto;background:var(--panel);border:1px solid var(--line);
  border-radius:12px}
table{border-collapse:collapse;width:100%;min-width:900px}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.07em;
  color:var(--muted);padding:12px 14px;border-bottom:1px solid var(--line);
  background:var(--chip);position:sticky;top:0;z-index:1}
td{padding:14px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:none}
.col-img{width:34%}.col-txt{width:20%}.col-res{width:24%}.col-ref{width:22%}
figure{margin:0}
figure img{width:100%;height:auto;display:block;border-radius:8px;
  border:1px solid var(--line);background:#000}
figcaption{color:var(--muted);font-size:11px;margin-top:6px;word-break:break-all;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.vendor{font-size:19px;font-weight:650;letter-spacing:-.01em}
.model{font-size:16px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  margin-top:2px;word-break:break-all}
.none{color:var(--muted);font-style:italic;font-weight:400;font-size:14px}
.badge{display:inline-block;font-size:10px;font-weight:700;text-transform:uppercase;
  letter-spacing:.06em;padding:2px 7px;border-radius:999px;margin-right:5px;
  vertical-align:middle}
.b-high{color:var(--high);background:var(--high-bg)}
.b-med{color:var(--med);background:var(--med-bg)}
.b-low{color:var(--low);background:var(--low-bg)}
.b-none{color:var(--muted);background:var(--chip)}
.meta{margin-top:10px;font-size:11.5px;color:var(--muted);line-height:1.7}
.meta code,.ocr code{background:var(--chip);padding:1px 5px;border-radius:4px;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--ink)}
.note{margin-top:8px;font-size:11.5px;padding:7px 9px;border-radius:7px;
  background:var(--med-bg);color:var(--med)}
.ocr{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.5px;
  max-height:280px;overflow:auto;line-height:1.65}
.ocr div{display:flex;gap:7px;padding:1px 0;border-bottom:1px dotted var(--line)}
.ocr .c{color:var(--muted);flex:0 0 32px;text-align:right;font-variant-numeric:tabular-nums}
.ocr .t{word-break:break-all}
.ocr .hit{color:var(--accent);font-weight:700}
details{margin-top:8px}
summary{cursor:pointer;color:var(--accent);font-size:11.5px}
.foot{margin-top:26px;color:var(--muted);font-size:11.5px;line-height:1.7}
.col-ref figure img{background:#fff}
.src{display:inline-block;font-size:10px;font-weight:700;text-transform:uppercase;
  letter-spacing:.05em;padding:2px 7px;border-radius:999px;background:var(--chip);
  color:var(--muted);margin-bottom:7px}
.src.workbook{color:var(--high);background:var(--high-bg)}
.src.web{color:var(--accent)}

/* Click any photo to open it full screen. Faceplate text is the whole point of
   this report and it is unreadable at thumbnail size. */
figure img{cursor:zoom-in}
#lb{position:fixed;inset:0;z-index:100;display:none;background:rgba(8,10,14,.93);
  padding:0}
#lb.open{display:block}
#lb .pane{position:absolute;inset:0 0 52px 0;overflow:auto;text-align:center}
#lb img{display:block;margin:auto;max-width:100%;max-height:100%;
  object-fit:contain;cursor:zoom-in}
#lb.actual .pane{text-align:left}
#lb.actual img{max-width:none;max-height:none;margin:0;cursor:zoom-out}
#lb .bar{position:absolute;left:0;right:0;bottom:0;height:52px;display:flex;
  align-items:center;gap:14px;padding:0 16px;background:rgba(0,0,0,.55);
  color:#e8eaee;font-size:12px}
#lb .bar .name{flex:1;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#lb button{background:rgba(255,255,255,.12);color:#e8eaee;border:0;border-radius:6px;
  padding:7px 13px;font:inherit;cursor:pointer}
#lb button:hover{background:rgba(255,255,255,.22)}
"""

# Kept out of the page template so its braces and percent signs survive the
# string formatting that builds the report.
LIGHTBOX = """
<div id="lb" aria-hidden="true">
  <div class="pane"><img alt=""></div>
  <div class="bar">
    <span class="name"></span>
    <button type="button" data-act="size">Actual size</button>
    <button type="button" data-act="close">Close &nbsp;Esc</button>
  </div>
</div>
<script>
(function(){
  var lb = document.getElementById('lb'),
      pane = lb.querySelector('.pane'),
      img = lb.querySelector('img'),
      name = lb.querySelector('.name'),
      sizeBtn = lb.querySelector('[data-act="size"]');

  function open(src, caption){
    img.src = src;
    name.textContent = caption || '';
    lb.classList.remove('actual');
    sizeBtn.textContent = 'Actual size';
    lb.classList.add('open');
    lb.setAttribute('aria-hidden', 'false');
    pane.scrollTop = pane.scrollLeft = 0;
    document.body.style.overflow = 'hidden';
  }
  function close(){
    lb.classList.remove('open');
    lb.setAttribute('aria-hidden', 'true');
    img.removeAttribute('src');
    document.body.style.overflow = '';
  }
  function toggleSize(){
    var actual = lb.classList.toggle('actual');
    sizeBtn.textContent = actual ? 'Fit to screen' : 'Actual size';
  }

  document.addEventListener('click', function(e){
    var picture = e.target.closest('figure img');
    if (picture && !lb.contains(picture)){
      var fig = picture.closest('figure'),
          cap = fig && fig.querySelector('figcaption');
      open(picture.src, (cap && cap.textContent) || picture.alt);
      return;
    }
    var act = e.target.closest('[data-act]');
    if (act){ act.dataset.act === 'size' ? toggleSize() : close(); return; }
    if (e.target === img){ toggleSize(); return; }
    // A click on the backdrop rather than on the photo means "done".
    if (lb.classList.contains('open') && lb.contains(e.target)) close();
  });

  document.addEventListener('keydown', function(e){
    if (!lb.classList.contains('open')) return;
    if (e.key === 'Escape') close();
    else if (e.key === 'Enter' || e.key === ' '){ e.preventDefault(); toggleSize(); }
  });
})();
</script>
"""


def _data_uri(path, mime=None):
    if isinstance(path, (bytes, bytearray)):
        return "data:%s;base64,%s" % (mime or "image/jpeg",
                                      base64.b64encode(bytes(path)).decode("ascii"))
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    with open(path, "rb") as fh:
        return "data:%s;base64,%s" % (mime, base64.b64encode(fh.read()).decode("ascii"))


def _reference_cell(ref, result):
    """The retrieved photo of the identified model, or why there isn't one."""
    if not ref.get("data"):
        if not result.get("model"):
            reason = "no model identified, so nothing to look up"
        else:
            reason = ("this exact model is not in the vendor workbooks, and no "
                      "online result carried both this model number and a "
                      "front-panel view")
        return '<div class="meta none">%s</div>' % html.escape(reason)

    label = {"workbook": "from vendor workbook",
             "web": "from web search"}.get(ref["origin"], ref["origin"] or "")
    extra = ""
    rec = ref.get("record") or {}
    bits = []
    if rec.get("name"):
        bits.append(html.escape(rec["name"][:90]))
    if rec.get("total_ports"):
        bits.append("ports: <code>%s</code>" % html.escape(str(rec["total_ports"])))
    if bits:
        extra = '<div class="meta">%s</div>' % "<br>".join(bits)
    return ('<span class="src %s">%s</span>'
            '<figure><img src="%s" alt="reference" loading="lazy">'
            '<figcaption>%s</figcaption></figure>%s'
            % (ref["origin"] or "", html.escape(label),
               _data_uri(ref["data"], ref["mime"]),
               html.escape(ref["detail"][:110]), extra))


def _badge(level):
    cls = {"high": "b-high", "medium": "b-med", "low": "b-low"}.get(level, "b-none")
    return '<span class="badge %s">%s</span>' % (cls, html.escape(level))


def _interesting(detections):
    """Drop the single-character noise that port numbers and LEDs generate.

    A faceplate is covered in digits ("1 2 3 ... 24") which the recognizer picks
    up at high confidence; they crowd out the few strings that carry the brand
    and model.
    """
    return [d for d in detections if len(d.get("text", "").strip()) >= 2]


def _is_hit(text, highlight):
    sq = squash(text)
    return bool(highlight and sq and (sq in highlight or highlight in sq))


def _ocr_lines(detections, limit, highlight):
    rows = []
    for d in detections[:limit]:
        rows.append('<div><span class="c">%.2f</span><span class="t%s">%s</span></div>'
                    % (d.get("conf", 0.0),
                       " hit" if _is_hit(d.get("text", ""), highlight) else "",
                       html.escape(d.get("text", ""))))
    return "".join(rows) or '<div class="t none">no text recognised</div>'


def _result_cell(r):
    vendor = r["vendor"] or "unidentified"
    model = r["model"] or "no model resolved"
    parts = [
        '<div class="vendor%s">%s %s</div>' % (
            "" if r["vendor"] else " none", _badge(r["vendor_confidence"]),
            html.escape(vendor)),
        '<div class="model%s">%s %s</div>' % (
            "" if r["model"] else " none", _badge(r["model_confidence"]),
            html.escape(model)),
    ]

    meta = []
    if r["vendor"]:
        meta.append("vendor match <code>%s</code> &middot; %.2f %s" % (
            html.escape(r["vendor_evidence"]), r["vendor_score"],
            "(logo)" if r["vendor_from_logo"] else "(inferred from model)"))
    if r["model"]:
        meta.append("model match <code>%s</code> &middot; %.2f" % (
            html.escape(r["model_evidence"] or "-"), r["model_score"]))
        meta.append("read from <code>%s</code>" % html.escape(r["model_source"] or "-"))
        if not r["in_catalog"]:
            meta.append("not in catalog &mdash; reported from model-number pattern")
    if r.get("port_spec"):
        spec = r["port_spec"]
        meta.append("expected ports from the name: <code>%s</code>" % html.escape(
            " + ".join("%d %s" % (spec[k], {"rj45": "RJ45", "sfp": "SFP"}[k])
                       for k in ("rj45", "sfp") if k in spec)))
    for reason in r.get("corroboration", []):
        meta.append("&check; %s" % html.escape(reason))
    if meta:
        parts.append('<div class="meta">%s</div>' % "<br>".join(meta))

    notes = []
    if r.get("suffix_ambiguous"):
        notes.append("The characters separating this from its siblings are not "
                     "legible; the reading is equally consistent with "
                     + html.escape(", ".join(v["model"] for v in r["variants"])))
    if r.get("vendor_model_conflict"):
        notes.append("Logo and model number point at different vendors.")
    if r.get("secondary_devices"):
        notes.append("Another device appears in this photo: " + html.escape(
            ", ".join("%s %s" % (s["vendor"], s["model"])
                      for s in r["secondary_devices"])))
    if not r["model"]:
        notes.append("Faceplate text was too small or too low-contrast to resolve a "
                     "model number.")
    parts += ['<div class="note">%s</div>' % n for n in notes]

    if r.get("alternatives"):
        rows = "".join("<div><code>%s</code> %s &middot; %.2f</div>"
                       % (html.escape(a["model"]), html.escape(a["vendor"]), a["score"])
                       for a in r["alternatives"])
        parts.append('<details><summary>%d other catalog candidate(s)</summary>'
                     '<div class="meta">%s</div></details>'
                     % (len(r["alternatives"]), rows))

    desc = ", ".join(
        "%s%s" % (k, "" if v is True else "=%s" % ("/".join(v) if isinstance(v, list) else v))
        for k, v in (r.get("descriptors") or {}).items()
        if not k.startswith("_") and not k.endswith("_strong"))
    if desc:
        parts.append('<div class="meta">faceplate descriptors: <code>%s</code></div>'
                     % html.escape(desc))
    return "".join(parts)


def render(rows, source_label, max_lines=28):
    n = len(rows)
    n_vendor = sum(1 for r in rows if r["result"]["vendor"])
    n_model = sum(1 for r in rows if r["result"]["model"])
    n_ref = sum(1 for r in rows if r.get("ref", {}).get("data"))

    body = []
    for row in rows:
        r = row["result"]
        highlight = squash(r.get("model_evidence") or "")
        dets = row["doc"].get("detections", []) + row["doc"].get("roi_detections", [])
        dets = sorted(_interesting(dets), key=lambda d: -d.get("conf", 0))
        # Always show the string the model was read from, even when its confidence
        # puts it far down the list - that is the one a reader wants to check.
        if highlight:
            hit = [d for d in dets if _is_hit(d.get("text", ""), highlight)]
            dets = hit + [d for d in dets if d not in hit]
        extra = max(0, len(dets) - max_lines)
        body.append(
            '<tr><td class="col-img"><figure><img src="%s" alt="%s" loading="lazy">'
            '<figcaption>%s</figcaption></figure></td>'
            '<td class="col-txt"><div class="ocr">%s</div>%s</td>'
            '<td class="col-res">%s</td>'
            '<td class="col-ref">%s</td></tr>'
            % (row["data_uri"], html.escape(row["file"]), html.escape(row["file"]),
               _ocr_lines(dets, max_lines, highlight),
               ('<div class="meta">+%d more lower-confidence strings</div>' % extra)
               if extra else "",
               _result_cell(r),
               _reference_cell(row.get("ref") or {}, r)))

    return """<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Switch identification &mdash; test photo vs reference</title>
<style>%s</style>
<div class="wrap">
<h1>Switch identification &mdash; test photo vs reference photo</h1>
<p class="sub">Vendor and model read by OCR over multiple renderings of each photo,
matched against the catalog with a confusion-aware edit distance, then paired with
that model's reference photo from the vendor workbooks or the web.
Source: <code>%s</code></p>
<div class="stats">
  <div class="stat"><b>%d</b><span>images</span></div>
  <div class="stat"><b>%d</b><span>vendor identified</span></div>
  <div class="stat"><b>%d</b><span>model identified</span></div>
  <div class="stat"><b>%d</b><span>reference image</span></div>
</div>
<div class="tablewrap"><table>
<thead><tr><th class="col-img">Test image</th>
<th class="col-txt">Detected text (confidence)</th>
<th class="col-res">Vendor &amp; model</th>
<th class="col-ref">Reference image</th></tr></thead>
<tbody>
%s
</tbody></table></div>
<p class="foot">
Confidence badges reflect the match score between the OCR reading and the catalog
entry, not a guarantee of correctness. <b>high</b> &ge; 0.93, <b>medium</b> &ge; 0.82,
<b>low</b> below that. Strings in blue are the ones the model number was read from.
Where a trailing revision letter (<code>-1024C</code> vs <code>-1024D</code>) could
not be resolved from the pixels, the alternatives are listed rather than guessed.
Reference photos are matched on the exact model number only &mdash; a near
neighbour is a different product, so the workbook photo is never substituted for
one. Web results are kept only when the model number appears in the result's
title or URL.
<br>Click any photo to open it full screen; click it again (or press Enter) for
actual size, Esc to close.
</p>
</div>
%s
""" % (CSS, html.escape(source_label), n, n_vendor, n_model, n_ref,
       "\n".join(body), LIGHTBOX)


# =========================================================================== #
# Driver
# =========================================================================== #

def collect_images(target):
    """Accept a single image or a directory of them; return sorted paths."""
    target = Path(target).expanduser()
    if target.is_file():
        return [target]
    if not target.is_dir():
        raise SystemExit("no such file or directory: %s" % target)
    files = sorted(p for p in target.iterdir()
                   if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS)
    if not files:
        raise SystemExit("no images (%s) found in %s"
                         % (", ".join(IMAGE_EXTENSIONS), target))
    return files


def load_cache(path):
    if path and Path(path).exists():
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (ValueError, OSError):
            print("  (ignoring unreadable cache at %s)" % path)
    return {}


def save_cache(path, cache):
    """Write via a temporary file so an interrupted run cannot corrupt the cache."""
    if not path:
        return
    tmp = Path(str(path) + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass


def fingerprint(path):
    """Cheap identity for a file, so an edited image is re-read automatically."""
    st = os.stat(path)
    return "%d:%d" % (st.st_size, int(st.st_mtime))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="OCR switch photos, identify vendor/model, write an HTML report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Requires: pip install easyocr opencv-python numpy")
    ap.add_argument("images", nargs="?", default=".",
                    help="image file or directory of images (default: current directory)")
    ap.add_argument("-o", "--out", default=None,
                    help="output HTML path (default: <images>/switch_identify_report.html)")
    ap.add_argument("--cache", default=None,
                    help="JSON file of OCR results; reused if present, written after "
                         "the run, so re-reports are instant (default: alongside -o)")
    ap.add_argument("--no-cache", action="store_true", help="do not read or write a cache")
    ap.add_argument("--refresh", action="store_true", help="re-OCR even if cached")
    ap.add_argument("--fast", action="store_true",
                    help="skip the magnified logo-anchored pass (much quicker, "
                         "finds far fewer model numbers)")
    ap.add_argument("--gpu", action="store_true", help="use GPU if torch sees one")
    ap.add_argument("--max-lines", type=int, default=28,
                    help="OCR strings shown per image (default: 28)")
    ap.add_argument("--json", default=None, help="also write the results as JSON")
    ap.add_argument("--catalog", default=None,
                    help="JSON file of extra vendors/models to recognise; merged "
                         "with the built-in catalog. Picked up automatically if a "
                         "file named %s sits beside the images or this script."
                         % CATALOG_FILENAME)
    ap.add_argument("--catalog-only", action="store_true",
                    help="use only --catalog, ignoring the built-in catalog")
    ap.add_argument("--dump-catalog", metavar="FILE", default=None,
                    help="write the built-in catalog to FILE as JSON and exit; "
                         "edit it and pass it back with --catalog")
    ap.add_argument("--images-from", metavar="CATALOG", default=None,
                    help="catalog built by build_switch_catalog.py, used to pull "
                         "each identified model's reference photo out of its "
                         "vendor workbook (default: %s beside the images or this "
                         "script, if present)" % CATALOG_FILENAME)
    ap.add_argument("--excel-dir", default=None,
                    help="folder holding the vendor workbooks (default: the path "
                         "recorded in the catalog)")
    ap.add_argument("--no-web", action="store_true",
                    help="never look up a reference photo online; show only what "
                         "the workbooks contain")
    args = ap.parse_args(argv)

    if args.dump_catalog:
        target = Path(args.dump_catalog).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(catalog_as_dict(), fh, indent=2, ensure_ascii=False)
        print("wrote %s (%d vendors, %d models)"
              % (target, len(MODELS), sum(len(m) for m in MODELS.values())))
        return 0

    if args.catalog_only and not args.catalog:
        raise SystemExit("--catalog-only needs --catalog")

    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
    except ImportError as exc:
        raise SystemExit("missing dependency (%s)\n  pip install easyocr opencv-python numpy"
                         % exc)

    files = collect_images(args.images)
    base_dir = Path(args.images).expanduser()
    base_dir = base_dir if base_dir.is_dir() else base_dir.parent

    if args.catalog:
        try:
            added_v, added_m = load_catalog(args.catalog, replace=args.catalog_only)
        except (OSError, ValueError) as exc:
            raise SystemExit("could not read --catalog %s: %s" % (args.catalog, exc))
        print("catalog: +%d alias(es), +%d model(s) from %s  (%d vendors, %d models)"
              % (added_v, added_m, args.catalog, len(MODELS), len(ALL_MODELS)))
    else:
        print("catalog: built-in (%d vendors, %d models)" % (len(MODELS), len(ALL_MODELS)))

    out_path = Path(args.out).expanduser() if args.out else base_dir / "switch_identify_report.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cache_path = None
    if not args.no_cache:
        cache_path = Path(args.cache).expanduser() if args.cache else \
            out_path.with_suffix(".ocr.json")
    cache = {} if (args.refresh or not cache_path) else load_cache(cache_path)

    # Reference photos: the workbook catalog if we can find one, web otherwise.
    ref_catalog = args.images_from or find_catalog(base_dir, Path(__file__).parent)
    library = ReferenceLibrary(ref_catalog, args.excel_dir)
    web_cache = out_path.parent / "reference_images"
    if library.loaded:
        print("reference photos: %d model(s) from %s"
              % (len(library.by_model), ref_catalog))
    elif ref_catalog:
        print("reference photos: could not read %s" % ref_catalog)
    else:
        print("reference photos: no workbook catalog given%s"
              % ("" if args.no_web else ", using web lookup only"))

    print("%d image(s) in %s" % (len(files), base_dir))
    if cache:
        print("reusing %d cached OCR result(s) from %s" % (len(cache), cache_path))

    rows = []
    for i, path in enumerate(files, 1):
        key = path.name
        stamp = fingerprint(path)
        doc = cache.get(key)
        # Re-read when the cache is empty for this file or the file itself changed.
        if doc and not args.refresh and doc.get("fingerprint") == stamp:
            print("  [%2d/%d] %-44s cached" % (i, len(files), key[:44]))
        else:
            t0 = time.time()
            try:
                stage1, stage2, sources = read_image(path, gpu=args.gpu,
                                                     deep=not args.fast)
                err = None
            except Exception as exc:
                stage1, stage2, sources, err = [], [], [], str(exc)
            doc = {"file": key, "fingerprint": stamp, "detections": stage1,
                   "roi_detections": stage2, "sources": sources, "error": err,
                   "seconds": round(time.time() - t0, 1)}
            cache[key] = doc
            print("  [%2d/%d] %-44s %3d + %3d strings  %5.1fs%s"
                  % (i, len(files), key[:44], len(stage1), len(stage2),
                     time.time() - t0, "  ERROR: %s" % err if err else ""))
            # Persist after every image so a long run survives an interruption.
            save_cache(cache_path, cache)

        result = identify(doc)
        ref = resolve_reference(library, result, web_cache, not args.no_web)
        rows.append({"file": key, "doc": doc, "data_uri": _data_uri(path),
                     "result": result, "ref": ref})

    # Images removed since the last run should not linger in the cache.
    names = {p.name for p in files}
    for stale in [k for k in cache if k not in names]:
        del cache[stale]
    save_cache(cache_path, cache)

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(render(rows, str(base_dir), max_lines=args.max_lines))

    if args.json:
        payload = [dict(file=r["file"], reference=r["ref"]["origin"],
                        reference_detail=r["ref"]["detail"], **r["result"])
                   for r in rows]
        with open(Path(args.json).expanduser(), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)

    print()
    for r in rows:
        res = r["result"]
        print("  %-42s %-12s %-18s v=%.2f m=%.2f  ref=%s"
              % (r["file"][:42], res["vendor"] or "-", res["model"] or "-",
                 res["vendor_score"], res["model_score"],
                 r["ref"]["origin"] or "none"))
    print("\nwrote %s (%.0f KB)" % (out_path, out_path.stat().st_size / 1024.0))
    if cache_path:
        print("OCR cache: %s  (delete it or pass --refresh to re-read the images)"
              % cache_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
