"""The appliance catalogues (appliance_catalog/): firewalls, load balancers, gateways.

Every row carries its device type ('kind'), so a make and model read off a photo also tell
what the device is. resolve(vendor, model, kind=None) turns what the user typed into the
catalogue's own spelling ("fortinet", "fortigate 200f" -> Fortinet FG-200F), or says what is
close. A model missing from the catalogue is still mapped: the catalogue fixes spelling,
it never blocks a lookup.

Licence tiers of one box share a template: hardware() gives the box ('r5000' for F5 r5600 /
r5800 / r5900, '8900' for NetScaler MPX 8905 ... 8930 and SDX 8900)."""
import re
import sys
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "appliance_catalog"))

KINDS = ("firewall", "load_balancer", "gateway")
LABEL = {"firewall": "firewall", "load_balancer": "load balancer", "gateway": "gateway"}


def normalise_kind(device):
    """'Firewall', 'load balancer', 'Load-Balancer', 'LB' -> the catalogue's kind; None stays None.
    Anything else is a clear ValueError, not a KeyError deep in the run."""
    if device is None or device == "":
        return None
    k = re.sub(r"[^a-z]+", "_", str(device).strip().lower()).strip("_")
    k = {"lb": "load_balancer", "loadbalancer": "load_balancer", "fw": "firewall", "gw": "gateway"}.get(k, k)
    if k not in KINDS:
        raise ValueError(f"unknown device type {device!r}: use one of {', '.join(KINDS)}")
    return k
_SUFFIX = ("networks", "network", "technologies", "technology", "systems", "corporation", "corp", "inc",
           "ltd", "co", "limited", "communications", "international", "electronics", "group")


def key(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def vendor_key(v):
    v = re.sub(r"\(.*?\)", "", v or "")                  # 'NetScaler (Citrix)' -> 'netscaler'
    words = re.findall(r"[a-z0-9]+", v.lower())
    while len(words) > 1 and words[-1] in _SUFFIX:
        words.pop()
    return "".join(words)


def _variants(vendor, model, same):
    """Other product IDs of the same hardware, from 'Same hardware as / variants'
    ('8900: 8905 / 8910 / 8920' on NetScaler MPX 8900 -> MPX 8905, MPX 8910 ...)."""
    if not same or ":" not in same:
        return []
    prefix = model.split(" ")[0] if " " in model else ""
    nums = re.findall(r"\b\d{4,5}[A-Z]?\b", same.split(";")[0].split(":", 1)[1])
    return [f"{prefix} {n}".strip() for n in nums]


@lru_cache(maxsize=1)
def rows():
    import firewalls
    import gateways
    import load_balancers
    import verification
    out = []
    for kind, mod in (("firewall", firewalls), ("load_balancer", load_balancers), ("gateway", gateways)):
        rs, _, _ = verification.apply(kind, mod.rows())
        for v, s, m, cat, form, st, src, same in rs:
            out.append({"kind": kind, "vendor": v, "series": s, "model": m, "category": cat, "form": form,
                        "status": st, "source": src, "same": same,
                        "variants": ", ".join(_variants(v, m, same))})
    return out


def hardware(row):
    """The box a row's template belongs to: its 'same hardware' group when it names one."""
    same = (row or {}).get("same") or ""
    g = same.split(":")[0].strip() if ":" in same else same.strip()
    return g if g and len(g) <= 14 and " / " not in g else row["model"]


@lru_cache(maxsize=1)
def vendors():
    return sorted({r["vendor"] for r in rows()}, key=str.lower)


def models_of(vendor, kind=None):
    return [r for r in rows() if r["vendor"] == vendor and (kind is None or r["kind"] == kind)]


def main_kind(vendor):
    """The device type most of a vendor's models are (Fortinet: firewall; F5: load balancer)."""
    from collections import Counter
    c = Counter(r["kind"] for r in rows() if r["vendor"] == vendor)
    return c.most_common(1)[0][0] if c else None


def find_vendor(text):
    """Catalogue vendor for what was typed ('citrix' -> 'NetScaler (Citrix)'), or None."""
    k = vendor_key(text)
    if not k:
        return None
    for v in vendors():
        if k in (vendor_key(v), key(v)) or k == key(re.sub(r".*\((.*)\)", r"\1", v)):
            return v
    for v in vendors():
        vk = vendor_key(v)
        if len(k) >= 4 and (k in vk or vk in k or k in key(v)):
            return v
    return None


# product-line words printed or typed in place of the catalogue's spelling
_PREFIX = {"fortigate": "fg", "fortiwifi": "fwf", "fortigaterugged": "fgr", "paloalto": "", "firepower": "fpr",
           "securefirewall": "csf", "bigip": "", "citrixadc": "", "netscaler": "", "acmepacket": "acmepacket",
           "smartnode": "sn", "quantumspark": "quantumspark", "neogate": "", "unifi": "",
           "at": ""}                                  # Allied Telesis prints AT-ARX200S-GTX for ARX200S-GTX
# order-code endings that are the same hardware (licence bundles, spares, power options)
_ORDER = re.compile(r"(k9|ngfwk9|asak9|ftdk9|sec|rf|ac|dc|bdl|ltm|ltmbest|best|better|good)$")


def model_keys(vendor, model):
    """Keys a typed model may match: as typed, without the vendor name, without order-code
    endings (FPR2110-NGFW-K9), with product-line words shortened (FortiGate -> FG)."""
    out = []
    mk = key(model)
    for k in (mk, mk.replace(vendor_key(vendor), "", 1) if vendor_key(vendor) else mk):
        out.append(k)
        for word, short in _PREFIX.items():
            if k.startswith(word):
                out.append(short + k[len(word):])
    for k in list(out):
        k2 = k
        while _ORDER.search(k2) and len(k2) > 4:
            k2 = _ORDER.sub("", k2)
        out.append(k2)
    return list(dict.fromkeys(k for k in out if k))


def resolve(vendor, model, kind=None):
    """{vendor, model, kind, found, row, suggestions} - the catalogue's spelling when it knows the model."""
    v = find_vendor(vendor) or (vendor or "").strip()
    keys = model_keys(v, model)
    mk = keys[0] if keys else ""
    pool = [r for r in rows() if (v not in vendors() or r["vendor"] == v) and (kind is None or r["kind"] == kind)]
    for k in keys:
        for r in pool:
            names = [r["model"]] + [x.strip() for x in (r["variants"] or "").split(",") if x.strip()]
            if any(key(n) == k for n in names):
                return {"vendor": r["vendor"], "model": r["model"], "kind": r["kind"], "found": True, "row": r,
                        "suggestions": []}
    near = sorted(((SequenceMatcher(None, mk, key(r["model"])).ratio(), r) for r in pool), key=lambda t: -t[0])
    return {"vendor": v, "model": (model or "").strip(), "kind": kind or main_kind(v), "found": False, "row": None,
            "suggestions": [f"{r['vendor']} {r['model']}" for s, r in near[:5] if s >= 0.7]}
