"""
Vendor registry loader — one source of truth from vendors.json.

agent.py, live_search.py and live_extract.py all extend their built-in
alias/domain tables with what this module returns, so adding a vendor
to vendors.json automatically makes it recognised everywhere.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger("vendor_registry")

_REGISTRY_PATH = Path(__file__).parent / "vendors.json"


def _load() -> list[dict]:
    if not _REGISTRY_PATH.exists():
        return []
    try:
        return json.loads(_REGISTRY_PATH.read_text()).get("vendors", [])
    except Exception as e:  # pragma: no cover
        logger.warning("vendors.json unreadable: %s", e)
        return []


_VENDORS = _load()


def aliases() -> dict[str, str]:
    """alias_text(lowercase) -> canonical display name."""
    out: dict[str, str] = {}
    for v in _VENDORS:
        name = v.get("name")
        if not name:
            continue
        # the canonical name itself is a valid alias
        for a in [name] + (v.get("aliases") or []):
            a_low = a.strip().lower()
            if a_low and a_low not in out:
                out[a_low] = name
    return out


def domains() -> set[str]:
    """Set of registered vendor domains (host portion, no scheme)."""
    out: set[str] = set()
    for v in _VENDORS:
        w = v.get("website")
        if not w:
            continue
        host = urlparse(w if "://" in w else f"https://{w}").netloc or w
        host = host.replace("www.", "").lower()
        if host:
            out.add(host)
    return out


def login_gated() -> dict[str, str]:
    """Vendors whose release notes live behind a login portal — map them
    to their portal URL. Derived from the registry's category + notes."""
    portals = {
        "Cisco":      "https://software.cisco.com",
        "Juniper Networks": "https://support.juniper.net/support/downloads/",
        "Arista Networks":  "https://www.arista.com/en/support/software-download",
        "HPE Aruba Networking": "https://asp.arubanetworks.com",
        "Dell Technologies":    "https://www.dell.com/support",
        "Huawei":               "https://support.huawei.com",
        "H3C":                  "https://www.h3c.com/en/Support",
        "Extreme Networks":     "https://extremeportal.force.com",
        "RUCKUS Networks":      "https://support.ruckuswireless.com",
        "Alcatel-Lucent Enterprise": "https://myportal.al-enterprise.com",
        "Nokia Networks":       "https://customer.nokia.com",
        "ZTE":                  "https://support.zte.com.cn",
        "Fujitsu":              "https://support.ts.fujitsu.com",
        "NEC":                  "https://www.nec.com/en/global/support/",
        "Ciena":                "https://www.ciena.com/services-support",
        "Lenovo":               "https://datacentersupport.lenovo.com",
        "Tejas Networks":       "https://www.tejasnetworks.com/support",
    }
    # restrict to vendors actually in the registry
    known = {v["name"] for v in _VENDORS}
    return {n: u for n, u in portals.items() if n in known}


def by_canonical() -> dict[str, dict]:
    """Lookup table keyed by canonical name → full registry entry."""
    return {v["name"]: v for v in _VENDORS if v.get("name")}


def count() -> int:
    return len(_VENDORS)
