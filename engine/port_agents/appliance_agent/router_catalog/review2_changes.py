"""Changes from the second catalogue review (Catalogue_Review_2.xlsx, 2026-10-21).

Router side of the review: sheets 'Router - corrections', 'Router - still open (rev 1)' and the
'Cross-catalogue' points that touch the router file. The firewall, gateway and load-balancer sides
are in appliance_catalog/review2.py. Rows the appliance catalogues take over from this one (security
gateways, SD-WAN / industrial gateways, multi-WAN routers) change with it, so a fix made here
reaches every catalogue that holds the row.

Not applied (named by the review only by reference): the 625 product-knowledge candidates and 16
vendors in Router_Catalog_Review.xlsx, which was not available.
"""
from full_models import BR, C, CHAS, DESK, EOS, IND, INDF, RACK, SDW, SEC, SMB, SP

REVIEW2 = "Reviewer round 2: confirmed (vendor page)"
REVIEW2_KNOWN = "Reviewer round 2: product knowledge - verify"
MIRROR = "Mirrored from the firewall catalogue (review round 2)"
HOME, NID = "Home / consumer router", "Carrier Ethernet demarcation (NID)"

# ---------------------------------------------------------------- one spelling per vendor, in every catalogue
VENDOR = {
    "Moxa": "MOXA",
    "Advantech": "Advantech Co. Ltd",
    "Red Lion": "Red Lion Controls",
    "Ribbon (ECI)": "Ribbon",
    "Phoenix Contact (including Hirschmann)": "Phoenix Contact",      # Hirschmann is Belden's, listed under Belden
    "Untangle / Arista": "Arista Networks",
    "VMware VeloCloud": "VeloCloud (Arista; formerly VMware / Broadcom)",
}
VELO = VENDOR["VMware VeloCloud"]

# ---------------------------------------------------------------- rows removed
REMOVE = {
    ("Palo Alto Networks", "PA-530"): "no PA-530 in Palo Alto's PA-500 list (review 2, docs.paloaltonetworks.com)",
    ("Sophos", "XGS 138w"): "Sophos lists XGS 138 without a w variant (review 2, sophos.com)",
    ("Ekinops / OneAccess", "ONE100"): "not an exact name; ONE100X is (review 2)",
    ("Ekinops / OneAccess", "ONE200"): "not an exact name; ONE20X is (review 2)",
    ("MikroTik", "ROSE Data server (RDS)"): "a storage product, not a router (review 1, still open)",
    ("Silicom Ltd", "uCPE Small"): "a size class, not a model (review 1)",
    ("Silicom Ltd", "uCPE Medium"): "a size class, not a model (review 1)",
    ("Silicom Ltd", "uCPE Large"): "a size class, not a model (review 1)",
    ("ASUS", "EBA63"): "an access point (review 1)",
    ("TrendNet", "TEW-923DAP"): "an access point (review 1)",
    ("Edimax Technology Co., Ltd", "Gemini RG21S"): "duplicate of RG21S (review 1)",
    ("Datto", "DSR-5"): "DSR is D-Link's naming; no Datto DSR found (review 1)",
    ("Datto", "DSR-10"): "DSR is D-Link's naming (review 1)",
    ("Datto", "DSR-25"): "DSR is D-Link's naming (review 1)",
    ("Westermo", "Lynx 3510"): "a managed switch family (review 1)",
    ("Westermo", "Ibex-1000"): "not a router (review 1: verify or remove)",
    ("Lantronix", "X400"): "not found (review 1)",
    ("Lantronix", "LM1200"): "not found (review 1)",
    ("Lantronix", "FOX3"): "a telematics gateway, not a router (review 1)",
    ("Cradlepoint", "W1850"): "a 5G adapter, not a router (review 1)",
    ("Digi", "Connect IT 4"): "a console server - moved to the gateway catalogue (review 1)",
    ("Digi", "Connect IT 16"): "a console server - moved to the gateway catalogue (review 1)",
    ("Digi", "Connect IT 48"): "a console server - moved to the gateway catalogue (review 1)",
    ("Synology", "BC500"): "an IP camera (review 1)",
    ("Alcatel-Lucent Enterprise", "OmniAccess SD-WAN (models: verify)"):
        "placeholder; the ESR model names the review suggests (5710 / 5720 / 5725 / 5740) were not found online",
    ("Phoenix Contact", "FL MGUARD 4102"): "no such model; the 4300 series is 4302 / 4305 (firewall verification, "
                                           "2026-10-15) - removed here too so the catalogues agree",
}

# ---------------------------------------------------------------- placeholders / families -> the models they stand for
REPLACE = {   # (vendor, model) -> (new models, status or None, source, why)
    ("Sangfor", "NGAF M-series (models: verify)"):
        (["M4500", "M5100", "M5200", "M5300", "M5400", "M5500", "M5600", "M5800", "M6000", "NSF-1200A-I",
          "NSF-3200A-I", "NSF-7100A-I", "NSF-7200A-I", "NSF-7300A-I", "NSF-7500A-I"], None, REVIEW2,
         "placeholder replaced by the NGAF models of the firewall file (scraped from sangfor.com)"),
    ("Cisco", "C890"): (["C896VA", "C897VA", "C897VAM", "C897VAW"], None, REVIEW2_KNOWN,
                        "C890 is the series name, not a product ID"),
    ("Huawei", "AR650"): (["AR651", "AR651W", "AR651C", "AR651F-Lite", "AR651K", "AR657W"], None, REVIEW2_KNOWN,
                          "series name - one row per model"),
    ("Huawei", "CX600"): (["CX600-X1", "CX600-X2", "CX600-X3", "CX600-X8", "CX600-X16"], None, REVIEW2_KNOWN,
                          "family - one row per chassis"),
    ("Huawei", "NE9000"): (["NE9000-8", "NE9000-20"], None, REVIEW2_KNOWN, "family - one row per chassis"),
    ("Huawei", "NE5000E"): (["NE5000E-20"], None, REVIEW2_KNOWN, "family - the chassis"),
}

# ---------------------------------------------------------------- field changes: (vendor, model) -> {field: value}, why
_FPR21 = ("FPR2110", "FPR2120", "FPR2130", "FPR2140")
_FPR31 = ("FPR3105", "FPR3110", "FPR3120", "FPR3130", "FPR3140")
_FPR41 = ("FPR4112", "FPR4115", "FPR4125", "FPR4145")
_XGS2 = ("XGS 88", "XGS 88w", "XGS 108", "XGS 108w", "XGS 128", "XGS 128w", "XGS 138")
_XGS1 = ("XGS 87", "XGS 87w", "XGS 107", "XGS 107w", "XGS 116", "XGS 116w", "XGS 126", "XGS 126w", "XGS 136", "XGS 136w")
EDIT = {
    ("Cisco Meraki", "Z3"): (dict(status=EOS), "Z3 was replaced by Z4; firewall file agrees"),
    ("Cisco Meraki", "Z3C"): (dict(status=EOS), "Z3C was replaced by Z4C; firewall file agrees"),
    ("Netgate", "1537"): (dict(model="XG-1537", status=EOS), "vendor name XG-1537; End-of-sale in both files"),
    ("Netgate", "1541"): (dict(model="XG-1541", status=EOS), "vendor name XG-1541; End-of-sale in both files"),
    **{("Cisco", m): (dict(status=EOS, form="1RU", series="Firepower 2100 (older)"),
                      "Firepower 2100 end-of-sale 27 May 2025 (cisco.com); 1RU rack unit") for m in _FPR21},
    **{("Cisco", m): (dict(form="1RU", series="Secure Firewall 3100"), "1RU rack unit") for m in _FPR31},
    **{("Cisco", m): (dict(status=EOS, form="1RU-2RU", series="Firepower 4100 (older)"),
                      "Firepower 4100 end-of-sale, last order 6 Jan 2026 (cisco.com EoS notice)") for m in _FPR41},
    **{("Cisco", m): (dict(series="Secure Firewall 1000 (Firepower 1000)"), "one series scheme per family")
       for m in ("FPR1010", "FPR1120", "FPR1140", "FPR1150")},
    ("Cisco", "CGR-2010"): (dict(series="2000 Series Connected Grid"), "series label (review 1)"),
    ("Yamaha", "RTX3510"): (dict(category=BR), "an enterprise / branch router (review 1)"),
    ("MOXA", "EDR-G903"): (dict(status=EOS), "EDR-G9010 is the successor (review 1)"),
    ("Fortinet", "FG-80C"): (dict(series="FortiGate A/B/C-series (end of life)"), "series label (review 1)"),
    ("MikroTik", "CCR1009-8G-1S-1S+"): (dict(series="Cloud Core Router (older)", category=SP, form=RACK),
                                        "a Cloud Core Router (review 1)"),
    **{("Juniper", m): (dict(form=DESK), "a desktop unit (review 1 + 2)")
       for m in ("SRX300", "SRX320", "SRX100", "SRX110", "SRX210", "SRX220")},
    ("Juniper", "SRX240"): (dict(form="Desktop / 1RU"), "a 1RU desktop unit (review 1 + 2: not a rack chassis)"),
    ("RAD", "ETX-1p"): (dict(category=NID), "an Ethernet access / demarcation device, not a router"),
    ("RAD", "ETX-2i"): (dict(category=NID), "an Ethernet access / demarcation device, not a router"),
    ("RAD", "SecFlow-1v"): (dict(category=IND, form=INDF), "an industrial router"),
    ("NetComm", "NF18ACV"): (dict(category=HOME, form=DESK), "a consumer VDSL / fibre Wi-Fi gateway, not industrial"),
    ("Ciena", "8192"): (dict(source=REVIEW2_KNOWN), "not in the 8100 list the review knows - verify"),
    **{("Check Point", m): (dict(source=REVIEW2), "listed in the R82 Spark admin guide")
       for m in ("Quantum Spark 2530", "Quantum Spark 2550", "Quantum Spark 2560", "Quantum Spark 2570",
                 "Quantum Spark 2580")},
    ("Stormshield", "SN-L-Series-2200"): (dict(variants="also written SN2200"), "same product as SN2200"),
    **{("Sophos", m): (dict(series="XGS desktop (2nd gen)"), "one series label for the 2nd-gen desktops") for m in _XGS2},
    **{("Sophos", m): (dict(series="XGS desktop (1st gen)"), "one series label for the 1st-gen desktops") for m in _XGS1},
}

# ---------------------------------------------------------------- models added
ADD = [   # vendor, series, category, form, status, source, models, why
    ("Check Point", "Quantum Spark 2500", SEC, "1RU", C, REVIEW2, ["Quantum Spark 2590"], "R82 Spark admin guide"),
    ("Palo Alto Networks", "PA-500", SEC, DESK, C, REVIEW2, ["PA-520-5G", "PA-545-POE", "PA-555-POE"],
     "PA-500 list in PAN-OS 12.1 hardware features"),
    ("Sophos", "XGS desktop (2nd gen)", SEC, DESK, C, REVIEW2, ["XGS 118", "XGS 118w"], "sophos.com comparison"),
    ("Teltonika Networks", "RUT routers", IND, INDF, C, REVIEW2,
     ["RUT140", "RUT142", "RUT145", "RUT202", "RUT204", "RUT206", "RUT261", "RUT286", "RUT901", "RUT981", "RUT986",
      "RUTM55", "RUTM56", "RUTM59", "RUTC16", "RUTC40", "RUTC42", "RUTC54", "ATRM50"], "wiki.teltonika-networks.com"),
    ("Teltonika Networks", "OTD outdoor routers", IND, "Outdoor", C, REVIEW2, ["OTD140", "OTD144", "OTD500"],
     "wiki.teltonika-networks.com"),
    ("Ekinops / OneAccess", "OneOS routers", BR, "Desktop / 1RU", C, REVIEW2,
     ["ONE20X", "ONE100X", "ONE270", "ONE420", "ONE421", "ONE425", "ONE521", "ONE526", "ONE531", "ONE545", "ONE700",
      "ONE1520", "ONE1526", "ONE1560", "ONE2510", "ONE2511", "ONE2515", "ONE2520", "ONE3540"],
     "OneAccess product overview (SUA Telenet) + reseller listings"),
    ("RAD", "SecFlow industrial routers", IND, INDF, C, REVIEW2_KNOWN, ["SecFlow-2", "SecFlow-4"], "missing SecFlow models"),
    ("NetComm", "Industrial routers", IND, INDF, C, REVIEW2_KNOWN, ["NTC-400", "NTC-500"], "NetComm's industrial line"),
    ("Cisco", "Carrier Routing System", SP, CHAS, EOS, REVIEW2_KNOWN, ["CRS-4/S", "CRS-8/S", "CRS-16/S", "CRS-3", "CRS-X"],
     "the other CRS chassis (review 1)"),
    ("Ciena", "Routers (5G / metro / aggregation)", SP, RACK, C, REVIEW2_KNOWN, ["8110", "8112", "8114", "8140"],
     "the 8100 family the review lists"),
    # changes made in the firewall file only: mirrored so both files agree (review 2, Cross-catalogue)
    ("Fortinet", "FortiGate Rugged", IND, INDF, C, MIRROR, ["FGR-70G-5G-Dual"], "a current 2024 rugged 5G model"),
    ("Netgate", "pfSense Plus appliances", SMB, "Desktop / rack", C, MIRROR, ["4100"], "in the firewall file only"),
    ("Protectli", "Vault", SMB, DESK, C, MIRROR, ["VP2430", "VP2440", "VP3210", "VP3230", "FW2B", "FW6E"],
     "in the firewall file only"),
]

FIELDS = {"series": 1, "model": 2, "category": 3, "form": 4, "status": 5, "source": 6, "variants": 7}


def canon(vendor):
    return VENDOR.get(vendor, vendor)


def apply(rows):
    """rows: (vendor, series, model, category, form, status, source, variants) after review 1.
    Returns (rows, change log [(action, vendor, model, detail)])."""
    log, out, seen = [], [], set()
    renamed = set()
    remove, replace, edit = dict(REMOVE), dict(REPLACE), dict(EDIT)     # copies: rows() runs more than once
    for r in rows:
        r = list(r)
        if r[0] in VENDOR:
            renamed.add((r[0], VENDOR[r[0]]))
            r[0] = VENDOR[r[0]]
            if r[0] == VELO and not r[1].startswith("VeloCloud"):
                r[1] = f"VeloCloud {r[1]}"
        k = (r[0], r[2])
        if k in remove:
            log.append(("Removed", r[0], r[2], remove.pop(k) + " [review 2]"))
            continue
        if k in replace:
            models, st, src, why = replace.pop(k)
            for m in models:
                if (r[0], m) not in seen:
                    seen.add((r[0], m))
                    out.append((r[0], r[1], m, r[3], r[4], st or r[5], src, ""))
                    log.append(("Replaced", r[0], m, f"replaces '{r[2]}': {why} [review 2]"))
            continue
        if k in edit:
            ch, why = edit.pop(k)
            for f, v in ch.items():
                old = r[FIELDS[f]]
                if old != v:
                    r[FIELDS[f]] = v
                    log.append(("Model" if f == "model" else f.capitalize(), r[0], k[1],
                                f"{old or '(empty)'} -> {v}: {why} [review 2]"))
        if (r[0], r[2]) in seen:
            continue
        seen.add((r[0], r[2]))
        out.append(tuple(r))
    for old, new in sorted(renamed):
        log.append(("Vendor", new, "(all rows)", f"'{old}' -> '{new}': one spelling in every catalogue [review 2]"))
    for vendor, series, cat, form, st, src, models, why in ADD:
        for m in models:
            if (vendor, m) in seen:
                continue
            seen.add((vendor, m))
            out.append((vendor, series, m, cat, form, st, src, ""))
            log.append(("Added", vendor, m, f"{why} [review 2]"))
    for (v, m) in list(remove) + list(replace) + list(edit):
        log.append(("Not found", v, m, "named in review 2 but not in the catalogue"))
    import review2_checked                             # web check of the review's product-knowledge names
    out = [tuple(r) for r in review2_checked.apply(out, "router", log)]
    import verify_checked                              # web check of the older 'verify' rows
    out = [tuple(r) for r in verify_checked.apply(out, "router", log)]
    import gap_check                                   # models / vendors missing from the catalogue
    out = [tuple(r) for r in gap_check.apply(out, "router", log)]
    return out, log
