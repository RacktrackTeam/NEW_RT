"""Web check of the older 'Product knowledge - verify' rows (2026-10-22).

Every row still marked 'verify' after review 2 (1,242 names in 199 vendor/series groups) was searched
for online (vendor pages, data sheets, end-of-life notices, distributor listings). Applied after
review2_checked, to all four catalogues:

  CONFIRMED  found online: source becomes CHECKED, link kept ('Checked at' / Change log)
  STATUS     status corrected from the vendor's (or a support reseller's) end-of-life notice
  RENAME     the name as the vendor writes it
  NOTFOUND   not found online: the row is KEPT (user's choice) and labelled NOTFOUND_SRC
  ADD        real sibling models found while checking; copy series/category/form from LIKE
Data for CONFIRMED / STATUS / NOTFOUND is generated into verify_checked_data.py.
"""
from verify_checked_data import CONFIRMED, STATUS, NOTFOUND

C, EOS, EOSUP = "Current", "End-of-sale", "End of support"
CHECKED = "Web check 2026-10-22: confirmed online (link in 'Checked at' / Change log)"
NOTFOUND_SRC = "Not confirmed online (kept; model name unproven)"
FORTINET_F = "https://docs.fortinet.com/document/fortianalyzer/6.2.2/release-notes/867245"

RENAME = [   # vendor, old, new, url
    ("Araknis", "AN-210-RT-2L1W", "AN-110-RT-2L1W", "https://www.alltrade.co.uk/araknis"),
    ("Araknis", "AN-510-RT-4L2W", "AN-310-RT-4L2W", "https://www.alltrade.co.uk/araknis"),
    ("Arista Networks", "7816R4", "7816LR4", "https://arista.com/en/products/platforms"),
    ("Fortinet", "FG-280D", "FG-280D-POE", FORTINET_F),
    ("Keenetic", "Hero 4G", "Hero 4G+",
     "https://support.keenetic.com/hero/kn-1011/en/30227-which-router-model-do-i-need-.html"),
    ("Nokia", "7250 IXR-X1", "7250 IXR-X1b", "https://nokia.com/ip-networks/7250-interconnect-router"),
    ("Nokia", "7250 IXR-X3", "7250 IXR-X3b", "https://nokia.com/ip-networks/7250-interconnect-router"),
    ("Patton", "SN4110", "SN4112", "https://www.ebay.com/p/54840485"),
]

ADD = [   # vendor, like (existing row to copy series/category/form from), [models], status, url
    ("Array Networks", "APV3800", ["APV1900", "APV2900"], C,
     "https://www.hitachi-solutions.co.jp/array/products/apv/lineup.html"),
    ("Array Networks", "APV2600", ["APV1600", "APV3600"], EOSUP,
     "https://www.hitachi-solutions.co.jp/array/news/before2018/160916news.html"),
    ("Check Point", "Quantum 28000", ["Quantum 28600"], C,
     "https://www.checkpoint.com/kr/downloads/products/check-point-appliance-comparison-chart.pdf"),
    ("Coyote Point", "E450GX", ["E350GX", "E650GX"], EOSUP,
     "https://www.eweek.com/small-business/coyote-point-releases-app-acceleration-load-balancing-appliances-for-smbs"),
    ("Deciso", "DEC4280", ["DEC3852", "DEC3862"], C, "https://shop.opnsense.com"),
    ("UfiSpace", "S9500-22XST", ["S9600-30DX"], C,
     "https://ufispace.com/company/news/ufispace-recognized-as-intel-network-builders-winners-circle-2022-solution-partner"),
    ("Furukawa Electric", "FITELnet F220", ["FITELnet F225", "FITELnet F310"], C, "https://digitalpr.jp/r/83710"),
    ("Hillstone Networks", "A6800", ["A7600"], C,
     "https://www.helpnetsecurity.com/2022/08/26/hillstone-networks-a-series-firewalls/"),
    ("LANCOM Systems", "ISG-1000", ["ISG-5000", "ISG-8000"], C,
     "https://rs-nc.rohde-schwarz.com/fileadmin/visio-shapes/Visio-drawio-Shapes-Overview-08-2026.pdf"),
    ("LANCOM Systems", "UF-60", ["UF-560", "UF-T60"], C,
     "https://rs-nc.rohde-schwarz.com/fileadmin/visio-shapes/Visio-drawio-Shapes-Overview-08-2026.pdf"),
    ("Loadbalancer.org", "Enterprise R16", ["Enterprise R20"], C,
     "https://loadbalancer.org/blog/can-you-load-balance-multiple-applications-at-the-same-time"),
]


def apply(rows, kind, log, links=None):
    """rows: lists/tuples [vendor, series, model, category, form, status, source, same]; kind is only
    used in log text. Returns the new row list (lists)."""
    key = lambda v, m: (v, m.upper())
    conf = {key(v, m): url for v, ms, url in CONFIRMED for m in ms}
    stat = {key(v, m): (st, url) for v, ms, st, url in STATUS for m in ms}
    ren = {key(v, o): (n, url) for v, o, n, url in RENAME}
    nf = {key(v, m) for v, ms in NOTFOUND for m in ms}
    out, have = [], set()
    for r in rows:
        r = list(r)
        k = key(r[0], r[2])
        if k in ren and "verify" in r[6]:
            new, url = ren[k]
            log.append(("Renamed", r[0], f"{r[2]} -> {new}", f"web check: the vendor's name ({url})"))
            r[2], r[6] = new, CHECKED
            k = key(r[0], new)
            if links is not None:
                links[k] = url
        if k in have:                                  # renamed onto a name already listed
            continue
        if k in conf and "verify" in r[6]:
            r[6] = CHECKED
            log.append(("Confirmed", r[0], r[2], f"web check: {conf[k]}"))
            if links is not None:
                links[k] = conf[k]
        elif k in nf and "verify" in r[6]:
            r[6] = NOTFOUND_SRC
            log.append(("Not confirmed", r[0], r[2], "web check: name not found online; row kept"))
        if k in stat and r[5] != stat[k][0]:
            log.append(("Status", r[0], r[2], f"{r[5]} -> {stat[k][0]}: web check, {stat[k][1]}"))
            r[5] = stat[k][0]
        have.add(k)
        out.append(r)
    like = {key(r[0], r[2]): r for r in out}
    for vendor, base, models, st, url in ADD:
        b = like.get(key(vendor, base))
        if b is None:                                  # sibling not in this catalogue
            continue
        for m in models:
            if key(vendor, m) in have:
                continue
            have.add(key(vendor, m))
            out.append([vendor, b[1], m, b[3], b[4], st, CHECKED, ""])
            log.append(("Added", vendor, m, f"web check: real model found while checking ({url})"))
            if links is not None:
                links[key(vendor, m)] = url
    return out
