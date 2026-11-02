"""Changes from the second catalogue review (Catalogue_Review_2.xlsx, 2026-10-21): firewall, gateway and
load-balancer side. The router side, and every row these catalogues take over from the router catalogue,
is changed in router_catalog/review2_changes.py.

apply(kind, rows, log, links) runs last in verification.apply:
  1. one spelling per vendor (the router catalogue's: MOXA, Advantech Co. Ltd, Red Lion Controls ...),
     duplicates that the respelling creates dropped
  2. REMOVE, EDIT (any column, including the model name), then ADD (review2_data.ADD + VENDOR_ADD)

Kept against the review's suggestion (the user's call, see the Notes sheets):
  - the 86 multi-WAN routers in the load-balancer catalogue (TP-Link ER / R, DrayTek, Cisco RV ...):
    the user asked for them there, and the load-balancer test photos include them
  - the 509 router rows in the gateway catalogue: kept, marked 'Also in: Routers', Notes corrected
"""
import sys
from pathlib import Path

from review2_data import ADD as SHEET_ADD
from review2_labels import C, CHECK, EOS, EOSUP, FW, GW, LB, REVIEW2, REVIEW2_KNOWN

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "router_catalog"))
from review2_changes import VENDOR  # noqa: E402  (one spelling map for all four catalogues)

NGFW, DCFW, UTM, VPN, OT = ("Next-gen firewall", "Data-center firewall", "UTM / SMB firewall", "VPN / crypto gateway",
                            "OT security sensor")
SBC, PROTO, CONSOLE = "Session border controller", "Protocol / IoT gateway", "Console / out-of-band gateway"
ADC = "ADC / load balancer"

REMOVE = [   # kind, vendor, model, why
    (FW, "SonicWall", "TZ250", "no such model; the SOHO 250 / NSA 250M are listed (web search: no hits)"),
    (FW, "SonicWall", "TZ250W", "no such model; the SOHO 250W is listed"),
    (FW, "Stormshield", "SN2200", "same product as SN-L-Series-2200 (stormshield.com) - kept there as a variant"),
    (FW, "Check Point", "15000", "series name; 15400 / 15600 are listed"),
    (FW, "Check Point", "16000", "series name; 16200 / 16600HS are listed"),
    (FW, "Check Point", "23000", "series name; 23500 / 23800 / 23900 are listed"),
    (FW, "Check Point", "600", "series name; 620 / 640 / 680 are listed"),
    (FW, "Check Point", "700", "series name; 730 / 750 / 770 / 790 are listed"),
    (FW, "Check Point", "1100", "series name; 1120 / 1140 / 1180 are listed"),
    (GW, "Alcatel-Lucent Enterprise", "OAW-4010", "a WLAN controller (same hardware as Aruba 7010), not a gateway"),
    (GW, "Alcatel-Lucent Enterprise", "OAW-4030", "a WLAN controller (same hardware as Aruba 7030), not a gateway"),
    (LB, "Loadbalancer.org", "Enterprise", "the family name; Enterprise R16 / MAX / 10G / 40G are the models"),
]

_FPR41 = ("FPR4112", "FPR4115", "FPR4125", "FPR4145")
_R10 = ("r10600", "r10800", "r10900", "r12600-DS", "r12800-DS", "r12900-DS")
EDIT = [   # kind, vendor, model, {column: value}, why
    *[(FW, "Cisco", m, dict(category=DCFW), "Firepower 4100: data-center firewall") for m in _FPR41],
    (FW, "Cisco", "FPR1010E", dict(series="Secure Firewall 1000 (Firepower 1000)"), "one series scheme per family"),
    (FW, "Cisco", "ASA5505", dict(form="Desktop"), "the 5505 is a desktop box"),
    (FW, "Cisco", "Z3", dict(status=EOS), "replaced by Z4; router file agrees"),
    (FW, "Cisco", "Z3C", dict(status=EOS), "replaced by Z4C; router file agrees"),
    (FW, "Cisco", "Z4", dict(status=C), "the current Meraki teleworker gateway; router file agrees"),
    (FW, "Cisco", "Z4C", dict(status=C), "the current Meraki teleworker gateway; router file agrees"),
    (FW, "Netgate", "1537", dict(model="XG-1537", status=EOS), "vendor name XG-1537, discontinued; router file agrees"),
    (FW, "Netgate", "1541", dict(model="XG-1541", status=EOS), "vendor name XG-1541, discontinued; router file agrees"),
    *[(FW, "Arista Networks", m, dict(series="Untangle NG Firewall appliances (end of life)"),
       "Untangle and Arista are one vendor label now") for m in ("z4", "z4w", "z6", "z12", "z20", "u25")],
    (FW, "H3C", "F5000-AI160", dict(model="F5000-AI-160", source=REVIEW2_KNOWN),
     "name looks truncated; the family is F5000-AI-15 / -20 / -40 / -60 (verify)"),
    (FW, "NSFOCUS", "NF NX3-G4100H", dict(model="NFNX3-G4100H"), "one spelling for the NF family"),
    (FW, "NSFOCUS", "NF NX3-G4100S", dict(model="NFNX3-G4100S"), "one spelling for the NF family"),
    (FW, "NSFOCUS", "NF NX5-T6100L", dict(model="NFNX5-T6100L"), "one spelling for the NF family"),
    (FW, "Stormshield", "SN-L-Series-2200", dict(same="also written SN2200"), "SN2200 merged here"),
    # gateways
    *[(GW, "AudioCodes", m, dict(category=SBC), "AudioCodes' SBC line - same category as the Ribbon / Oracle SBCs")
      for m in ("Mediant 2600", "Mediant 2600B", "Mediant 4000", "Mediant 4000B", "Mediant 9000", "Mediant 9030",
                "Mediant 9080")],
    (GW, "AudioCodes", "Mediant 502", dict(source=REVIEW2_KNOWN), "not a standard AudioCodes name - verify"),
    (GW, "AudioCodes", "Mediant 2600B", dict(source=REVIEW2_KNOWN), "not a standard AudioCodes name - verify"),
    (GW, "Ribbon", "EdgeMarc 4550", dict(source=REVIEW2_KNOWN), "not in Ribbon's EdgeMarc data sheets - verify"),
    (GW, "Avaya", "G430", dict(status=C, source=REVIEW2_KNOWN), "still in Avaya CM 10.x gateway docs (verify orderable)"),
    (GW, "Avaya", "G450", dict(status=C, source=REVIEW2_KNOWN), "still in Avaya CM 10.x gateway docs (verify orderable)"),
    (GW, "HMS Networks", "Anybus Communicator", dict(model="AB7007", series="Anybus Communicator", source=REVIEW2_KNOWN),
     "family name - one model (the review's example)"),
    (GW, "HMS Networks", "Anybus X-gateway", dict(model="AB7832", series="Anybus X-gateway", source=REVIEW2_KNOWN),
     "family name - one model (the review's example)"),
    *[(GW, "Teltonika Networks", m, dict(status=EOS), "on Teltonika's EOL list; TRB246 / 247 / 256 replaced them")
      for m in ("TRB245", "TRB255", "TRB500")],
    *[(GW, "Avocent (Vertiv)", m, dict(status=EOSUP, source=REVIEW2_KNOWN), "ACS 5000 is discontinued; ACS 8000 is current")
      for m in ("ACS 5004", "ACS 5008", "ACS 5016", "ACS 5032", "ACS 5048")],
    *[(GW, "Avocent (Vertiv)", m, dict(status=EOSUP), "Cyclades-era ACS line; ACS 6000 support ended 2023 and ACS 8000 "
       "is current (vertiv.com ACS6000 EOL notice)") for m in ("ACS16", "ACS48")],
    (GW, "Avocent (Vertiv)", "Cyclades TS-1000", dict(status=EOSUP), "far older Cyclades line"),
    # load balancers
    *[(LB, "F5", m, dict(form="Chassis blade"), "VELOS blades for the CX410 / CX1610 chassis") for m in ("BX110", "BX520")],
    (LB, "A10 Networks", "Thunder 7650", dict(series="Thunder ADC", category=ADC), "current model - not in the 'older' series"),
    *[(LB, "F5", m, dict(form="2RU"), "r10000 / r12000-DS are 2RU in F5's data sheet (review open check)") for m in _R10],
]

# vendors the review asks for with example models only (sheet 'Vendors to add'); the rest of that sheet
# is covered by the models-to-add sheets. WINS, Huawei AntiDDoS, Claroty, Dragos and Tenable OT have no
# model names in the review and are not added.
VENDOR_ADD = [
    (FW, "Seqrite (Quick Heal)", "UTM Terminator", UTM, "Desktop / rack", C, REVIEW2_KNOWN, "Indian UTM vendor",
     ["T2S-20", "T2S-30", "T2S-60", "T2S-120", "T2S-250", "T2S-500", "T2S-1000"]),
    (FW, "Endian", "UTM appliances", UTM, "Desktop / rack", C, REVIEW2_KNOWN, "European UTM appliances",
     ["UTM Mini 25", "Mercury 50", "Mercury 100", "Macro X2", "Macro X3"]),
    (FW, "Smoothwall", "UTM appliances", UTM, "Desktop / rack", C, REVIEW2_KNOWN, "UK schools / public-sector UTM",
     ["S4", "S6", "S8", "S10", "S14"]),
    (FW, "Calyptix", "AccessEnforcer", UTM, "Desktop / rack", C, REVIEW2_KNOWN, "US SMB UTM", ["AE800", "AE1200", "AE2500"]),
    (FW, "SECUI", "MF2 next-gen firewall", NGFW, "Rack", C, REVIEW2_KNOWN, "Korean NGFW",
     ["MF2 100", "MF2 200", "MF2 500", "MF2 1000", "MF2 2000", "MF2 5000"]),
    (FW, "Cisco", "Cyber Vision (OT sensor)", OT, "Industrial / DIN", C, REVIEW2_KNOWN,
     "OT sensor category completed (review: Cisco Cyber Vision IC3000)", ["IC3000"]),
]

COL = {"series": 1, "model": 2, "category": 3, "form": 4, "status": 5, "source": 6, "same": 7}


def apply(kind, rows, log, links):
    key = lambda v, m: (v, m.upper())
    out, seen = [], set()
    for r in rows:                                     # 1. one spelling per vendor
        r = list(r)
        if r[0] in VENDOR:
            r[0] = VENDOR[r[0]]
        if key(r[0], r[2]) in seen:
            continue
        seen.add(key(r[0], r[2]))
        out.append(r)
    for k, v in sorted({(a, b) for a, b in VENDOR.items() if any(r[0] == b for r in out)}):
        log.append(("Vendor", v, "(all rows)", f"'{k}' -> '{v}': one spelling in every catalogue (review 2)"))

    idx = {key(r[0], r[2]): i for i, r in enumerate(out)}
    gone = set()
    for k, vendor, model, why in REMOVE:               # 2. removals
        i = idx.get(key(vendor, model))
        if k == kind and i is not None:
            gone.add(i)
            log.append(("Removed", vendor, model, why + " (review 2)"))
        elif k == kind:
            log.append(("Not found", vendor, model, "named in review 2 but not in the catalogue"))
    for k, vendor, model, ch, why in EDIT:             # 3. field changes
        if k != kind:
            continue
        i = idx.get(key(vendor, model))
        if i is None or i in gone:
            log.append(("Not found", vendor, model, "named in review 2 but not in the catalogue"))
            continue
        for f, val in ch.items():
            old = out[i][COL[f]]
            if old != val:
                out[i][COL[f]] = val
                log.append(("Model" if f == "model" else f.capitalize(), vendor, model,
                            f"{old or '(empty)'} -> {val}: {why} (review 2)"))
    out = [tuple(r) for i, r in enumerate(out) if i not in gone]
    have = {key(r[0], r[2]) for r in out}
    for k, vendor, series, cat, form, status, src, note, models in SHEET_ADD + VENDOR_ADD:   # 4. additions
        if k != kind:
            continue
        vendor = VENDOR.get(vendor, vendor)
        for m in models:
            if key(vendor, m) in have:
                continue
            have.add(key(vendor, m))
            out.append((vendor, series, m, cat, form, status, src, ""))
            links.setdefault(key(vendor, m), "")
            log.append(("Added", vendor, m, f"{note} (review 2)"))
    import review2_checked                             # web check of the review's product-knowledge names
    out = review2_checked.apply(out, kind, log, links)
    import verify_checked                              # web check of the older 'verify' rows
    out = verify_checked.apply(out, kind, log, links)
    import gap_check                                   # links for rows the router gap check found
    return [tuple(r) for r in gap_check.apply(out, kind, log, links, add=False)]
