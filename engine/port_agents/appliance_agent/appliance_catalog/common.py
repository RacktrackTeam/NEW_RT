"""Shared labels for the firewall / load-balancer / gateway catalogues (same scheme as router_catalog).

Row: vendor, series, model, category, form factor, status, source, same hardware as / variants.
"""
SCRAPED, WEB, KNOWN = "Vendor site (scraped)", "Web search (confirmed)", "Product knowledge - verify"
ROUTER = "Router catalogue (vendor site / web)"      # rows taken over from router_catalog
C, EOS, EOSUP = "Current", "End-of-sale", "End of support"
RACK, DESK, CHAS, BLADE, DIN = "Rack", "Desktop", "Modular chassis (rack)", "Chassis blade", "Industrial / DIN"

SOURCES = (SCRAPED, WEB, ROUTER, KNOWN)


def expand(groups):
    """groups: (vendor, series, category, form, status, source, models). A model may be
    'name' or ('name', 'same hardware as / variants')."""
    out, seen = [], set()
    for vendor, series, cat, form, st, src, models in groups:
        for m in models:
            name, same = (m, "") if isinstance(m, str) else m
            if (vendor, name.upper()) in seen:
                continue
            seen.add((vendor, name.upper()))
            out.append((vendor, series, name, cat, form, st, src, same))
    return out


def ports_face(form, vendor, info):
    """Which face the network ports are on: rack units and chassis on the front; desktop units
    vary by vendor (most firewall desktops have them on the back)."""
    f = form.lower()
    if "desktop" in f and "rack" not in f and "ru" not in f:
        return info.get(vendor, {}).get("desktop_ports", "Check")
    if f.startswith(("desktop / rack", "desktop / 1ru")):
        return "Rack models: front; desktop models: " + info.get(vendor, {}).get("desktop_ports", "check")
    if "din" in f:
        return "Front / top"
    return "Front"
