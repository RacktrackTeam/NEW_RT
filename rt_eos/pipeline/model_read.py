"""
model_read.py - the model of a device is READ from its own text, never guessed.

The faceplate reader (switch-ocr's identify_device) and the old regex parser
both hand back a model for a device. Some of those models are not in the text
at all: the identifier snaps a fragment to the nearest model in its catalogue
("sco ASR1" became NEXUS 7010, "2001706" became C1200-16P-2G), and others are
text that is not a model (the device's own name "sis-rt02", a cable tag
"nvs-sgkc-sis-sw05b Gi1-0-26", a port label "RS-232", a serial after S/N).

This module settles the model from the device's own read text:

  read_model(text, make)    a clean model token in the text, by vendor shape
  model_in_text(model, text) the model (or a one-character misread of it) is
                            one of the text's words, not part of a name, tag,
                            serial, location, port or regulatory line
  settle(make, candidates, text)  the make and model to keep

A catalogue is used only to CONFIRM a token that is in the text, never to
offer one that is not. When no clean model is in the text, the model is None
and the device shows "model not read".

server/lib/model_read.js mirrors these rules for scans already on disk.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Words that are never a model
# --------------------------------------------------------------------------

# A token right after one of these is a serial, a name, a location or an
# address, never the model. ("ame:" is "Name:" with its first letter cut.)
_CONTEXT_WORDS = {
    "s/n", "s/n:", "sn", "sn:", "n:", "b/n", "b/n:", "serial", "serial:",
    "name", "name:", "ame:", "ame", "loc", "loc:", "mac", "address",
    "address:", "ip", "ip:", "host", "host:", "hostname", "hostname:", "asset", "asset:",
}

# Port and interface labels printed on a faceplate.
_PORT_WORDS = {
    "con", "console", "mgmt", "mgm", "usb", "aux", "port", "ports", "stat", "status",
    "sys", "system", "link", "act", "pwr", "power", "fan", "alarm", "reset", "lom",
    "usb/mgmt", "usb/mgm", "usbimgmt", "usbmgmt", "maj", "min", "sfp", "sfp+", "poe",
    "rs-232", "rs232", "85-232", "ethernet",
}
_PORT_RE = re.compile(
    r"^(?:gi|gig|te|ten|eth|et|fa|fe|xe|hu|fo|tw|mgmt|po|ge|lom|ma)"
    r"(?:[a-z]*)(?:[0-9][0-9/().\-]*[a-z0-9/().\-]*)$"
    r"|^rs-?\d{3}$|^ge[a-z0-9]$",
    re.IGNORECASE,
)

# Regulatory and standards text.
_REGULATORY = {"class", "laser", "lasen", "product", "producy", "prooduct", "rohs", "ce", "fcc", "ul", "warning", "caution"}
_REGULATORY_RE = re.compile(r"^(?:IS[O0]\d{3,5}\S*|TIA-?\S*|EN\d{4,}\S*|IEC\d+\S*)$", re.IGNORECASE)

# A device name or hostname: nvs-sgkc-sis-rt02, sis-rt02, an-rt05, b08-reg-gwlc02.
_HOSTNAME_RE = re.compile(r"^[a-z]{2,}(?:-[a-z0-9.]+)*-[a-z]+\d+[a-z]?$", re.IGNORECASE)
# A rack asset label: SP-R1-U18-SW04, SP-RI-4015-SW0.
_ASSET_TAG_RE = re.compile(r"(^|[-_. ])(?:SW|PP|FW|RTR|PDU|ACT)\d{0,3}$", re.IGNORECASE)
_UNIT_TOKEN_RE = re.compile(r"(^|[-_. ])U\d{1,2}(?=[-_. ]|$)", re.IGNORECASE)
# A location sticker: A08-36, A05-184, RU28, A03.
_LOCATION_RE = re.compile(r"^(?:[A-Z]{1,2}\d{1,3}-\d{1,3}|RU\d{1,2}|[A-Z]\d{2})$", re.IGNORECASE)
# A serial: three letters, four or five digits, then letters and digits (FXS2010Q1U7, FDO2203B0WU).
_SERIAL_RE = re.compile(r"^[A-Z]{3}\d{4,5}[A-Z0-9]{2,5}$")
_MAC_RE = re.compile(r"^(?:[0-9A-F]{2}[:.\-]){5}[0-9A-F]{2}$|^[0-9A-F]{4}\.[0-9A-F]{4}\.[0-9A-F]{4}$", re.IGNORECASE)
_IP_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){1,3}$")
_DIGITS_RE = re.compile(r"^[\d.,:/\-]+$")
_SHORT_RE = re.compile(r"^[A-Z]{1,2}\d{1,2}$", re.IGNORECASE)

_STRIP = ",;:()[]{}'\"|*"

# Reasons that bar a word from being a model ALONE but not from being part of
# one: "RB 3011 UiAS-RM" and "Monitor 200" are read as several words.
_SOFT = {"digits", "no letters and digits", "short"}


def _clean(tok: str) -> str:
    t = tok.strip(_STRIP)
    # A stray leading dash from a split read ("-TE187").
    return t.lstrip("-.")


def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


# --------------------------------------------------------------------------
# Model shapes per vendor
# --------------------------------------------------------------------------
# (vendor, pattern, weak). The pattern must match a WHOLE span of the text's
# words (one to three words joined by a space). A weak shape is too plain to
# name its vendor by itself (a bare "1810"), so it is taken only when the
# make read on the device is that vendor. A named group "m" is what is kept.
_SIZES = r"(?:8|10|16|18|24|26|28|48|50|52)"
MODEL_SHAPES: list[tuple[str, re.Pattern, bool]] = [
    # Cisco
    ("Cisco", re.compile(r"ASR\d{3,4}(?:-[A-Z0-9]{1,4})?", re.I), False),
    ("Cisco", re.compile(r"ISR\d{3,4}(?:[A-Z]{0,2})?(?:-[A-Z0-9/]{1,8})*", re.I), False),
    ("Cisco", re.compile(r"(?:WS-)?C\d{4}[A-Z]{0,2}-\d{1,3}[A-Z]{0,5}(?:-[A-Z0-9]{1,4}){0,2}", re.I), False),
    ("Cisco", re.compile(r"N\d[KX]-C?\d{4,5}[A-Z0-9]*(?:-[A-Z0-9]{1,6})*", re.I), False),
    ("Cisco", re.compile(r"(?P<m>NEXUS\s?\d{4})", re.I), False),
    ("Cisco", re.compile(r"ASA\d{4}(?:-[A-Z0-9]{1,6})*", re.I), False),
    ("Cisco", re.compile(r"FPR-?\d{4}(?:-[A-Z0-9]{1,6})*", re.I), False),
    ("Cisco", re.compile(r"PIX-?\d{3,4}[A-Z]?", re.I), False),
    ("Cisco", re.compile(r"M[SX]\d{2,3}(?:-\d{1,2}[A-Z]{0,3})?", re.I), True),
    # TP-Link
    ("TP-Link", re.compile(r"TL-[A-Z]{2,4}\d{3,5}[A-Z]{0,4}", re.I), False),
    ("TP-Link", re.compile(r"T\d{4}[A-Z]{0,2}-\d{1,2}[A-Z]{1,4}", re.I), False),
    ("TP-Link", re.compile(r"SG\d{4}[A-Z]{0,3}", re.I), True),
    # D-Link
    ("D-Link", re.compile(r"D[GXE]S-\d{3,4}[A-Z]{0,2}(?:-\d{1,3}[A-Z]{0,4})?(?:/[A-Z0-9]{1,4})?", re.I), False),
    ("D-Link", re.compile(r"DSR-\d{3,4}[A-Z]?", re.I), False),
    # HP / HPE / Aruba
    ("HPE", re.compile(r"J\d{4}[A-Z]", re.I), False),
    ("HPE", re.compile(r"JL\d{3}[A-Z]", re.I), False),
    ("HPE", re.compile(r"(?:HPE?\s?)?(?:1810|1820|1910|1920|1950|2510|2530|2540|2620|2810|2920|2930[FM]?|3810M?|5130|5406)-" + _SIZES + r"G?(?:-POE\+?|-PPOE\+?)?", re.I), False),
    ("HPE", re.compile(r"(?:HPE?\s?)?(?P<m>1810|1820|1910|1920|1950|2510|2530|2540|2620|2810|2920|2930[FM]?|3810M?|5130|5406)(?:-\S*)?", re.I), True),
    ("HPE", re.compile(r"(?:ARUBA\s|CX\s?)(?P<m>(?:6100|6200|6300|6400|8320|8325|8360|8400)[A-Z]?)", re.I), False),
    ("HPE", re.compile(r"ProLiant\s?(?:DL|ML|BL)\d{2,3}[A-Z0-9]*", re.I), False),
    # APC
    ("APC", re.compile(r"AP\d{4}[A-Z]{0,2}", re.I), False),
    ("APC", re.compile(r"NBRK\d{4}[A-Z]?", re.I), False),
    ("APC", re.compile(r"(?:SU[AM]|SMT|SMX|SRT|SURT)\d{3,5}[A-Z0-9]{0,8}", re.I), False),
    ("APC", re.compile(r"NETBOTZ\s?(?:RACK\s)?(?:MONITOR\s)?(?P<n>200|250|450|550|570|750)", re.I), False),
    ("APC", re.compile(r"(?:RACK\s)?MONITOR\s(?P<n>200|250|450|550|570|750)", re.I), True),
    # Juniper
    ("Juniper", re.compile(r"(?:EX|QFX|MX|SRX|ACX|PTX)\d{3,5}[A-Z]?(?:-[A-Z0-9]{1,6})*", re.I), False),
    # Arista
    ("Arista", re.compile(r"DCS-7\d{2,3}[A-Z0-9]*(?:-[A-Z0-9]{1,6})*", re.I), False),
    ("Arista", re.compile(r"7\d{3}[A-Z]{1,4}-\d{2}[A-Z0-9]*(?:-[A-Z0-9]{1,6})*", re.I), True),
    # Fortinet
    ("Fortinet", re.compile(r"F[GSW]-?\d{2,4}[A-Z](?:-[A-Z0-9]{1,6})*", re.I), False),
    ("Fortinet", re.compile(r"FORTIGATE[\s-]?(?P<n>\d{2,4}[A-Z])", re.I), False),
    # Palo Alto
    ("Palo Alto", re.compile(r"PA-\d{3,4}[A-Z]?", re.I), False),
    # MikroTik (RB 3011 UiAS-RM is read as three words)
    ("MikroTik", re.compile(r"(?:CRS|CCR|CSS)\d{3,4}(?:-[A-Z0-9+]{1,12})*", re.I), False),
    ("MikroTik", re.compile(r"RB\s?\d{3,4}\s?[A-Z]{0,6}(?:-[A-Z0-9+]{1,6})*", re.I), False),
    # Ubiquiti
    ("Ubiquiti", re.compile(r"USW-[A-Z][A-Z0-9]*(?:-[A-Z0-9]{1,12}){0,3}", re.I), False),
    ("Ubiquiti", re.compile(r"U(?:SG|DM|XG)-[A-Z0-9]{2,12}(?:-[A-Z0-9]{1,8}){0,2}", re.I), False),
    # NETGEAR
    ("NETGEAR", re.compile(r"(?:GS|JGS|XS|M4300)\d{2,4}[A-Z]{0,4}(?:-[A-Z0-9]{1,8})?", re.I), True),
    # SonicWall
    ("SonicWall", re.compile(r"(?:TZ|NSA|NSV)-?\d{3,4}[A-Z]?", re.I), True),
    # Huawei
    ("Huawei", re.compile(r"(?:S|CE)\d{4}[A-Z]?-\d{1,3}[A-Z0-9-]*", re.I), True),
    # Dell
    ("Dell", re.compile(r"(?:S|N|Z)\d{4}[A-Z]{0,3}-?(?:ON)?", re.I), True),
    ("Dell", re.compile(r"(?:R|T)\d{3}(?:XD|XS)?", re.I), True),
    # Synology / QNAP
    ("Synology", re.compile(r"(?:DS|RS)\d{3,4}\+?(?:II|III)?", re.I), True),
    ("QNAP", re.compile(r"(?:TS|TVS|TES)-\d{2,4}[A-Z+]{0,6}", re.I), False),
    # Eaton
    ("Eaton", re.compile(r"(?:5SC|5PX|9SX|9PX)\d{3,4}[A-Z0-9]{0,6}", re.I), False),
]

# Names a make is written as, for the make-is-near-the-model check and for
# "is this make read only off a name or a tag".
MAKE_WORDS: dict[str, tuple[str, ...]] = {
    "cisco": ("cisco", "csco", "catalyst", "nexus", "meraki"),
    "tp-link": ("tplink", "tp-link", "jetstream", "omada"),
    "d-link": ("dlink", "d-link"),
    "hpe": ("hpe", "hp", "hewlett", "aruba", "procurve", "proliant", "officeconnect"),
    "hp": ("hpe", "hp", "hewlett", "procurve", "proliant", "officeconnect"),
    "aruba": ("aruba", "hpe", "hp"),
    "apc": ("apc", "netbotz", "schneider", "smart-ups", "smartups"),
    "juniper": ("juniper",),
    "arista": ("arista",),
    "fortinet": ("fortinet", "fortigate", "fortiswitch"),
    "palo alto": ("paloalto", "palo"),
    "mikrotik": ("mikrotik", "routerboard", "mikrorik"),
    "ubiquiti": ("ubiquiti", "unifi"),
    "netgear": ("netgear", "prosafe"),
    "sonicwall": ("sonicwall",),
    "huawei": ("huawei",),
    "dell": ("dell", "poweredge", "powerswitch"),
    "synology": ("synology",),
    "qnap": ("qnap",),
    "eaton": ("eaton",),
    "hikvision": ("hikvision",),
}

# Makes that are the same maker for this purpose.
_SAME_MAKER = {"hp": "hpe", "aruba": "hpe", "hewlett packard enterprise": "hpe", "hewlett-packard": "hpe"}


def _maker(name: str | None) -> str:
    k = str(name or "").strip().lower()
    return _SAME_MAKER.get(k, k)


# --------------------------------------------------------------------------
# Words of the text
# --------------------------------------------------------------------------


def _is_name_like(t: str) -> bool:
    """A device name, hostname or rack asset label (not a model)."""
    if not t:
        return False
    if _HOSTNAME_RE.match(t):
        lowers = sum(c.islower() for c in t)
        uppers = sum(c.isupper() for c in t)
        # TL-SG2428P has the same shape as an-rt05; a name is written in lower
        # case or has three or more parts.
        if t.count("-") >= 2 or lowers > uppers:
            return True
    if t.count("-") >= 2 and sum(c.islower() for c in t) > sum(c.isupper() for c in t):
        return True
    if _UNIT_TOKEN_RE.search(t) and re.search(r"[-_. ]", t):
        return True
    if _ASSET_TAG_RE.search(t) and len(re.split(r"[-_. ]", t)) >= 3:
        return True
    # Any non-ASCII letter (nvs-sgkc-wan-rt0łs): a misread name, never a model.
    if any(ord(c) > 127 for c in t) and "-" in t:
        return True
    return False


def _matches_shape(span: str) -> bool:
    return any(rx.fullmatch(span) for _, rx, _ in MODEL_SHAPES)


def not_a_model(token: str) -> str | None:
    """Why a token is not a model (a short reason), or None when it may be one."""
    t = _clean(str(token or ""))
    if not t:
        return "empty"
    low = t.lower()
    if low in _PORT_WORDS or _PORT_RE.match(t):
        return "port"
    if low in _REGULATORY or _REGULATORY_RE.match(t):
        return "regulatory"
    if low.startswith("www.") or low.startswith("http") or ".com" in low:
        return "web address"
    if _MAC_RE.match(t):
        return "mac"
    if _IP_RE.match(t):
        return "ip"
    if low.startswith("s/n") or low.startswith("sn:"):
        return "serial"
    if _LOCATION_RE.match(t):
        return "location"
    # The shapes below can look like a model too; a clean model shape wins.
    if _matches_shape(t):
        return None
    if _DIGITS_RE.match(t):
        return "digits"
    if not re.search(r"[A-Za-z]", t) or not re.search(r"\d", t):
        return "no letters and digits"
    if _SHORT_RE.match(t):
        return "short"
    if _is_name_like(t):
        return "name"
    if len(t) >= 10 and _SERIAL_RE.match(t.upper()) and "-" not in t:
        return "serial"
    return None


def _words(text: str) -> list[dict]:
    """The text's words, each marked when it can never be (part of) a model."""
    raw = str(text or "").split()
    out = []
    for i, tok in enumerate(raw):
        t = _clean(tok)
        prev = raw[i - 1].lower() if i > 0 else ""
        prev2 = raw[i - 2].lower() if i > 1 else ""
        why = not_a_model(t)
        # The word after S/N, Name, LOC, Rack, MAC: a serial, a name, a place.
        if (why is None or why in _SOFT) and (prev in _CONTEXT_WORDS or (prev2 in {"mac", "loc:", "loc"} and prev in {"address", "rack"})):
            why = "after " + prev.rstrip(":")
        out.append({"tok": t, "low": t.lower(), "skip": why})
    # A cable tag "sw05b Gi1-0-26 B12": the word before a port label is a name.
    for i in range(len(out) - 1):
        if out[i]["skip"] is None and out[i + 1]["skip"] == "port" and _PORT_RE.match(out[i + 1]["tok"] or "") \
                and re.match(r"^(?:gi|te|eth|et|fa|xe|hu)", out[i + 1]["low"]) and not _matches_shape(out[i]["tok"]):
            out[i]["skip"] = "cable tag"
    return out


def _spans(words: list[dict], max_len: int = 3):
    """Runs of one to three words that may be a model: no word in the run is
    a name, tag, serial, location, port or regulatory word, and a single word
    is not a bare number or a bare word."""
    n = len(words)
    for i in range(n):
        for k in range(1, max_len + 1):
            if i + k > n:
                break
            part = words[i:i + k]
            if any(not w["tok"] or (w["skip"] and w["skip"] not in _SOFT) for w in part):
                break
            if k == 1 and part[0]["skip"]:
                continue
            yield i, k, " ".join(w["tok"] for w in part)


def _format(vendor: str, m: re.Match) -> str:
    g = m.groupdict()
    if g.get("n"):
        if vendor == "APC":
            return f"NetBotz {g['n']}"
        if vendor == "Fortinet":
            return f"FG-{g['n'].upper()}"
    if g.get("m"):
        s = g["m"]
        if s.upper().startswith("NEXUS"):
            return "Nexus " + re.sub(r"\D", "", s)
        return re.sub(r"\s+", "", s).upper()
    s = m.group(0)
    if vendor == "HPE":
        s = re.sub(r"^HPE?\s?", "", s, flags=re.I)
    return re.sub(r"\s+", "", s).upper()


def _make_near(words: list[dict], i: int, k: int, vendor: str) -> bool:
    kws = MAKE_WORDS.get(_maker(vendor), (vendor.lower(),))
    lo, hi = max(0, i - 3), min(len(words), i + k + 3)
    for j in range(lo, hi):
        if i <= j < i + k:
            continue
        w = re.sub(r"[^a-z\-]", "", words[j]["low"])
        if w and (w in kws or w.replace("-", "") in kws):
            return True
    return False


def read_model(text: str, make: str | None = None) -> tuple[str | None, str | None]:
    """(vendor, model) of the clean model token in the text, or (None, None).

    A token next to the make word, or of the make's own shape, is preferred.
    """
    words = _words(text)
    want = _maker(make)
    best = None
    for i, k, span in _spans(words):
        for order, (vendor, rx, weak) in enumerate(MODEL_SHAPES):
            m = rx.fullmatch(span)
            if not m:
                continue
            same = bool(want) and _maker(vendor) == want
            if weak and not same:
                continue
            score = (
                2 if same else 0,
                1 if _make_near(words, i, k, vendor) else 0,
                0 if weak else 1,
                len(_norm(span)),
                -order,
            )
            if best is None or score > best[0]:
                best = (score, vendor, _format(vendor, m))
    if not best:
        return None, None
    return best[1], best[2]


def _lev1(a: str, b: str) -> bool:
    """True when a and b differ by at most one edit."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(x != y for x, y in zip(a, b)) == 1
    if la > lb:
        a, b, la, lb = b, a, lb, la
    i = 0
    while i < la and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def model_in_text(model: str | None, text: str) -> bool:
    """The model is one, two or three of the text's words (one misread letter
    allowed on a model of five characters or more), and none of those words is
    a name, tag, serial, location, port or regulatory word."""
    if not model:
        return False
    if not_a_model(model) and not _matches_shape(str(model).strip()):
        return False
    want = _norm(model)
    if len(want) < 4:
        return False
    for _, _, span in _spans(_words(text)):
        got = _norm(span)
        if got == want or (len(want) >= 5 and _lev1(got, want)):
            return True
    return False


def make_from_name_only(make: str | None, text: str) -> bool:
    """True when every place the make's word appears is inside a name or a tag
    (nvs-sgkc-f5-ltm, APCNETBOTZ after Name), so the make was not read off the
    device's badge."""
    if not make:
        return False
    kws = MAKE_WORDS.get(_maker(make))
    if not kws:
        return False
    words = _words(text)
    seen_free = seen_any = False
    for w in words:
        low = re.sub(r"[^a-z0-9\-]", "", w["low"])
        hit = any(low == k or low.replace("-", "") == k.replace("-", "") or (len(k) >= 4 and k in low) for k in kws)
        if not hit:
            continue
        seen_any = True
        if w["skip"] not in ("name", "cable tag") and not str(w["skip"] or "").startswith("after name"):
            seen_free = True
    return seen_any and not seen_free


def settle(make: str | None, candidates: list[str | None], text: str) -> tuple[str | None, str | None]:
    """The make and model to keep for one device.

    candidates: models offered by the identifier and the regex parser, best
    first. A clean model token read from the text wins; otherwise the first
    candidate that is in the text and is not a name, tag, serial, location or
    port. No model is invented.
    """
    if make and make_from_name_only(make, text):
        make = None
    vendor, model = read_model(text, make)
    if model:
        return (make or vendor), model
    for c in candidates:
        if not c or not model_in_text(c, text):
            continue
        # A word of no known model shape is taken only for a device whose make
        # was read: a lone "B8888" with no maker is a tag, not a model.
        if not make and not _matches_shape(str(c).strip()):
            continue
        return make, str(c).strip().upper()
    return make, None
