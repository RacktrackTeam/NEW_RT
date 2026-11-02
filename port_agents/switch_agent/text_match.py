"""OCR-tolerant text matching, the model-name port parser and a built-in model catalog.

Adapted from teammate 1's (Jaya) switch_identify.py. Model numbers are compared on
their letters and digits only, with an edit distance that charges little for the
character swaps OCR makes on small faceplate print (0/O, 1/I/L, 5/S, 8/B, G/E ...).
"""
import re

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


# Character groups OCR confuses at small sizes and low contrast.
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


# Port counts spelled in a model name. Copper tokens: 24P / 24T / 48GT / 24TC.
# Fibre tokens: 4S / 4S+ / 4X / 4Y / 2Q / 4SFP. A bare number is a series digit, not
# a port count; a digit may follow (S5735-L24T4S: 24T then 4S), another letter may not.
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
