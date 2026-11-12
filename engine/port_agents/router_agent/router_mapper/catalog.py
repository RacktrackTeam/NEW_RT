"""The router catalog (router_catalog/full_models.py): 94 vendors, ~2,300 models.

resolve(vendor, model) turns what the user typed into the catalog's own spelling
("juniper networks", "srx 345" -> Juniper SRX345), or says what is close when the
model is not in the catalog. A model missing from the catalog is still mapped: the
catalog only fixes spelling, it never blocks a lookup."""
import re
import sys
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "router_catalog"))

_SUFFIX = ("networks", "network", "technologies", "technology", "systems", "corporation", "corp", "inc",
           "ltd", "co", "limited", "communications", "international", "electronics", "group")


def key(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def vendor_key(v):
    words = re.findall(r"[a-z0-9]+", (v or "").lower())
    while len(words) > 1 and words[-1] in _SUFFIX:
        words.pop()
    return "".join(words)


@lru_cache(maxsize=1)
def rows():
    import full_models
    return [dict(zip(("vendor", "series", "model", "category", "form", "status", "source", "variants"), r))
            for r in full_models.rows()]


@lru_cache(maxsize=1)
def vendors():
    return sorted({r["vendor"] for r in rows()}, key=str.lower)


def models_of(vendor):
    return [r for r in rows() if r["vendor"] == vendor]


def find_vendor(text):
    """Catalog vendor for what was typed ('juniper networks' -> 'Juniper'), or None."""
    k = vendor_key(text)
    if not k:
        return None
    for v in vendors():
        vk = vendor_key(v)
        if k == vk or k == key(v):
            return v
    for v in vendors():                       # 'cisco meraki' vs 'meraki', 'hpe aruba' vs 'aruba'
        vk = vendor_key(v)
        if len(k) >= 4 and (k in vk or vk in k):
            return v
    return None


# product-line words printed or typed in place of the catalog's short prefix
_PREFIX = {"fortigate": "fg", "fortiwifi": "fwf", "fortigaterugged": "fgr", "paloalto": "", "unifi": "",
           "edgerouter": "er", "cloudcorerouter": "", "routerboard": "rb"}
# order-code endings that are the same hardware (licence bundles, spares, refurbished)
_ORDER = re.compile(r"(k9|sec|sk9|seck9|rf|ws)$")


def model_keys(vendor, model):
    """Keys a typed model may match: as typed, without the vendor name in front, without
    order-code endings (ISR4331/K9), with product-line words shortened (FortiGate -> FG)."""
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
    vk = vendor_key(vendor)
    out += [vk + k for k in list(out) if vk and not k.startswith(vk)]   # '2911' -> 'cisco2911'
    return list(dict.fromkeys(k for k in out if k))


def resolve(vendor, model):
    """{vendor, model, found, row, suggestions} - the catalog's spelling when it knows the model."""
    v = find_vendor(vendor) or (vendor or "").strip()
    keys = model_keys(v, model)
    mk = keys[0] if keys else ""
    pool = models_of(v) if v in vendors() else rows()
    for k in keys:
        for r in pool:
            names = [r["model"]] + [x.strip() for x in (r["variants"] or "").split(",") if x.strip()]
            if any(key(n) == k for n in names):
                return {"vendor": r["vendor"], "model": r["model"], "found": True, "row": r, "suggestions": []}
    near = sorted(((SequenceMatcher(None, mk, key(r["model"])).ratio(), r) for r in pool),
                  key=lambda t: -t[0])
    return {"vendor": v, "model": (model or "").strip(), "found": False, "row": None,
            "suggestions": [f"{r['vendor']} {r['model']}" for s, r in near[:5] if s >= 0.7]}
