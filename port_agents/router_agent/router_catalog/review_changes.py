"""Changes from the catalogue review of 2026-10-14 (Router_Catalog_Coverage.html summary).

Only what the review names explicitly is applied here. Its row-level file
(Router_Catalog_Review.xlsx: 64 corrections, 91 confirmed models, 625 candidates, 20 open
checks) was not available; the rest is applied when it is. Every change is listed in the
Excel's "Change log" sheet.
"""
from full_models import (BR, C, CHAS, DESK, EOS, IND, INDF, KNOWN, RACK, SDW, SEC, SMB, SP, WEB)

REVIEW = "Reviewer-confirmed (vendor page)"     # models the reviewer checked on the vendor's site
REVIEW_KNOWN = "Reviewer: product knowledge - verify"

# ---------------------------------------------------------------- 1. names that do not exist / not routers
REMOVE = {
    ("Cisco", "ISR4211"): "name does not exist (review)",
    ("Cisco", "CISCO1981"): "name does not exist (review)",
    ("Nokia", "7705 SAR-1"): "name does not exist (review)",
    ("Nokia", "7705 SAR-10x"): "name does not exist (review)",
    ("Nokia", "7705 SAR-Mx"): "name does not exist (review)",
    ("Nokia", "7705 SAR-Hx"): "name does not exist (review)",
    ("Arista Networks", "Arista 5000 Edge"): "name does not exist (review)",
    ("Arista Networks", "Arista 5100 Edge"): "name does not exist (review)",
    ("Arista Networks", "Arista 5200 Edge"): "name does not exist (review)",
    ("Linksys", "LRT-1030 (Cloud Router)"): "name does not exist (review)",
    ("Linksys", "MR20000"): "name does not exist (review)",
    ("Sierra Wireless", "MG41"): "a Cisco Meraki product, not Sierra Wireless (review)",
    ("AVM", "FRITZ!Box 7630 AX"): "name does not exist (review)",
    ("Teltonika Networks", "RUTM31"): "name does not exist (review)",
    # the review also flags access points / console servers / a switch / a camera / an adapter
    # without naming the rows here; these two are certain from the product names
    ("Edimax Technology Co., Ltd", "Pro IAP-1200"): "an access point, not a router",
    ("Edimax Technology Co., Ltd", "Gemini RE23S"): "a range extender, not a router",
}

# ---------------------------------------------------------------- 2. status / label fixes
STATUS = {
    ("Cisco", "IR8140H"): C, ("Cisco", "IR8140H-P"): C,
    ("Cisco", "CRS1-8"): EOS,
    ("Teltonika Networks", "RUT300"): EOS, ("Teltonika Networks", "RUT360"): EOS,
    **{("Sophos", m): EOS for m in ("XGS 87", "XGS 87w", "XGS 107", "XGS 107w", "XGS 116", "XGS 116w",
                                    "XGS 126", "XGS 126w", "XGS 136", "XGS 136w")},
}
CATEGORY = {   # (vendor, model) -> (series, category, form factor)
    ("Huawei", "AR650"): ("NetEngine AR600", BR, DESK),
    ("Huawei", "AR6510-L"): ("NetEngine AR6500", BR, RACK),
    ("Cisco", "IR8140H"): ("IR8100 Catalyst Heavy Duty", IND, INDF),
    ("Cisco", "IR8140H-P"): ("IR8100 Catalyst Heavy Duty", IND, INDF),
}
# placeholders replaced by the models they stand for
REPLACE = {
    ("Palo Alto Networks", "PA-3500 series (models: verify)"): ["PA-3510", "PA-3520", "PA-3530", "PA-3540"],
    ("Sangfor", "NGAF series (models: verify)"): ["NGAF M-series (models: verify)"],
}

# ---------------------------------------------------------------- 3. models the review confirmed
ADD = [
    ("SonicWall", "Gen 8 TZ", SEC, DESK, C, REVIEW, ["TZ80", "TZ280", "TZ380", "TZ480", "TZ580", "TZ680"]),
    ("SonicWall", "Gen 8 NSa", SEC, "1RU", C, REVIEW, ["NSa 2800", "NSa 3800", "NSa 4800", "NSa 5800"]),
    ("Check Point", "Quantum Spark", SEC, DESK, C, REVIEW,
     ["Quantum Spark 1535", "Quantum Spark 1555", "Quantum Spark 1575", "Quantum Spark 1595"]),
    ("Check Point", "Quantum Spark 2500", SEC, "1RU", C, REVIEW_KNOWN,
     ["Quantum Spark 2530", "Quantum Spark 2550", "Quantum Spark 2560", "Quantum Spark 2570", "Quantum Spark 2580"]),
    ("Palo Alto Networks", "PA-500", SEC, DESK, C, REVIEW, ["PA-520", "PA-530", "PA-540", "PA-550", "PA-560"]),
    ("Teltonika Networks", "RUTX rack-mount", IND, RACK, C, REVIEW, ["RUTXR1"]),
    ("Nokia", "7705 SAR", SP, "Rack / modular chassis", C, REVIEW, ["7705 SAR-F", "7705 SAR-Hmc"]),
    ("Arista Networks", "AWE edge routers", SDW, "Desktop / 1RU", C, REVIEW, ["AWE-7220R", "AWE-7230R", "AWE-7250R"]),
    ("VMware VeloCloud", "Edge 4100 / 5100", SDW, RACK, C, REVIEW, ["Edge 4100", "Edge 5100"]),
    ("Sophos", "XGS desktop (2nd gen)", SEC, DESK, C, REVIEW, ["XGS 88w", "XGS 108w", "XGS 128w", "XGS 138w"]),
    ("Cradlepoint", "Routers", SDW, DESK, C, REVIEW, ["R2400", "S400"]),
]

# ---------------------------------------------------------------- 4. tier-1 vendors the review asks for
# model names below are product knowledge: marked to verify, as the review itself does for its candidates
ADD_VENDORS = [
    ("HPE", "FlexNetwork MSR", BR, "Desktop / 1RU", C, REVIEW_KNOWN,
     ["MSR954", "MSR958", "MSR1002-4", "MSR2003", "MSR3012", "MSR3024", "MSR3044", "MSR3064", "MSR4060", "MSR4080"]),
    ("Alcatel-Lucent Enterprise", "OmniAccess SD-WAN / ESR", SDW, "Desktop / 1RU", C, REVIEW_KNOWN,
     ["OmniAccess SD-WAN (models: verify)"]),
    ("Ekinops / OneAccess", "OneOS routers", BR, "Desktop / 1RU", C, REVIEW_KNOWN,
     ["ONE100", "ONE200", "ONE400", "ONE540", "ONE1540", "ONE2540"]),
    ("Robustel", "Industrial routers", IND, INDF, C, REVIEW_KNOWN,
     ["R1510", "R1520", "R2000", "R2010", "R2110", "R3000", "R5020"]),
    ("InHand Networks", "Industrial routers", IND, INDF, C, REVIEW_KNOWN, ["IR305", "IR315", "IR615", "IR915", "ER805"]),
    ("Milesight", "Industrial routers", IND, INDF, C, REVIEW_KNOWN, ["UR32", "UR35", "UR41", "UR75"]),
    ("Grandstream", "GWN routers", SMB, DESK, C, REVIEW_KNOWN, ["GWN7001", "GWN7002", "GWN7003", "GWN7052", "GWN7062"]),
    ("AudioCodes", "Mediant MSBR", BR, "Desktop / 1RU", C, REVIEW_KNOWN,
     ["Mediant 500 MSBR", "Mediant 500L MSBR", "Mediant 800 MSBR"]),
    ("RAD", "Service routers", SP, "Desktop / 1RU", C, REVIEW_KNOWN, ["SecFlow-1v", "ETX-1p", "ETX-2i"]),
    ("NetComm", "Routers", IND, INDF, C, REVIEW_KNOWN, ["NTC-140W", "NTC-220", "NF18ACV"]),
    ("Ewon", "Industrial remote-access routers", IND, INDF, C, REVIEW_KNOWN, ["Cosy 131", "Flexy 205"]),
    ("Cisco", "Secure Firewall", SEC, "Desktop / 1RU", C, REVIEW_KNOWN,
     ["FPR1010", "FPR1120", "FPR1140", "FPR1150", "FPR2110", "FPR2120", "FPR2130", "FPR2140", "FPR3105", "FPR3110",
      "FPR3120", "FPR3130", "FPR3140", "FPR4112", "FPR4115", "FPR4125", "FPR4145"]),
    ("Cisco", "ASA 5500-X", SEC, "Desktop / 1RU", EOS, REVIEW_KNOWN,
     ["ASA5506-X", "ASA5508-X", "ASA5516-X", "ASA5525-X", "ASA5545-X", "ASA5555-X"]),
    ("Huawei", "HiSecEngine USG", SEC, "Desktop / 1RU", C, REVIEW_KNOWN,
     ["USG6510E", "USG6525E", "USG6555E", "USG6585E", "USG6615E", "USG6655E", "USG6680E"]),
]


def apply(rows):
    """rows: full_models.rows() tuples (vendor, series, model, category, form, status, source, variants).
    Returns (new rows, change log [(action, vendor, model, detail)])."""
    log, out = [], []
    have = {(r[0], r[2]) for r in rows}
    for r in rows:
        k = (r[0], r[2])
        if k in REMOVE:
            log.append(("Removed", r[0], r[2], REMOVE[k]))
            continue
        if k in REPLACE:
            for m in REPLACE[k]:
                out.append((r[0], r[1], m, r[3], r[4], r[5], REVIEW if "verify" not in m else r[6], ""))
                log.append(("Placeholder filled", r[0], m, f"replaces '{r[2]}' (review)"))
            continue
        r = list(r)
        if k in STATUS and r[5] != STATUS[k]:
            log.append(("Status", r[0], r[2], f"{r[5]} -> {STATUS[k]} (review)"))
            r[5] = STATUS[k]
        if k in CATEGORY:
            s, cat, form = CATEGORY[k]
            if (r[1], r[3], r[4]) != (s, cat, form):
                log.append(("Category", r[0], r[2], f"{r[3]} -> {cat} (review)"))
                r[1], r[3], r[4] = s, cat, form
        out.append(tuple(r))
    for vendor, series, cat, form, st, src, models in ADD + ADD_VENDORS:
        for m in models:
            if (vendor, m) in have:
                continue
            have.add((vendor, m))
            out.append((vendor, series, m, cat, form, st, src, ""))
            log.append(("Added", vendor, m, "confirmed by the review on the vendor page" if src == REVIEW
                        else "named by the review; model name to verify"))
    missing = [k for k in list(REMOVE) + list(STATUS) + list(CATEGORY) + list(REPLACE) if k not in have and k[1]]
    for v, m in missing:
        log.append(("Not found", v, m, "named in the review but not in the catalogue"))
    return out, log
