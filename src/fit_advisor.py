"""Fit advisor: "where in this rack can my new device go?"

Self-contained (standard library only, no network). Given the per-row occupancy
that the pipeline already produces and a device make/model, it:

1. looks up the device's height in rack units (U) from an offline curated catalog
   (`lookup_device`),
2. finds every run of contiguous free rows that can hold it and ranks them by
   datacenter practice (`suggest_placements`),
3. summarises free space (`free_space_summary`),
4. and wraps all of that in one call for the UI (`advise`).

Rows are dicts ordered TOP of the rack to BOTTOM:
    {"unit": "u12", "box": (x1, y1, x2, y2), "status": "occupied"|"unoccupied", "p_occupied": float}
Labels follow rack convention: u01 is the BOTTOM row.
"""
from __future__ import annotations

import difflib
import re

__all__ = [
    "CATALOG",
    "lookup_device",
    "suggest_placements",
    "free_space_summary",
    "advise",
    "placement_preference",
]

# ═══════════════════════════════════════════════════════════════════════════
# Catalog
# ═══════════════════════════════════════════════════════════════════════════
# Each entry: make, model (display name), units (rack-unit height; 0 = mounts
# vertically in the side channel), category, aliases (other names that should
# match exactly), notes. Heights are taken from vendor spec sheets; models whose
# height we could not pin down were deliberately left out.

CATEGORIES = {
    "server", "gpu-server", "storage", "nas", "switch", "router", "firewall",
    "wireless-controller", "chassis", "ups", "pdu", "patch-panel",
    "cable-management", "blanking-panel", "shelf", "kvm",
}

CATALOG: list[dict] = []


def _fam(make, category, units, models, notes="", alias=None):
    """Add a family of models sharing make/category/height.

    `models` items are a model string or a tuple (model, alias, alias, ...).
    `alias` is an optional function model -> list of extra aliases.
    """
    for m in models:
        model, *aliases = (m,) if isinstance(m, str) else m
        if alias:
            aliases += [a for a in alias(model) if a and a != model]
        CATALOG.append({
            "make": make, "model": model, "units": units, "category": category,
            "aliases": tuple(aliases), "notes": notes,
        })


def _cisco_alias(model):
    """'C9300-48P' -> '9300-48P', 'N9K-C93180YC-FX' -> '93180YC-FX', 'WS-C2960X..' -> '2960X..'."""
    return [re.sub(r"^(?:WS-|N9K-)?C(?=\d)", "", model)]


def _strip_prefix(prefix):
    return lambda model: [model[len(prefix):]] if model.startswith(prefix) else []


# ── Dell ───────────────────────────────────────────────────────────────────
_fam("Dell", "server", 1, [
    "PowerEdge R230", "PowerEdge R330", "PowerEdge R430", "PowerEdge R630",
    "PowerEdge R240", "PowerEdge R340", "PowerEdge R440", "PowerEdge R640",
    "PowerEdge R6415", "PowerEdge R6515", "PowerEdge R6525",
    "PowerEdge R250", "PowerEdge R350", "PowerEdge R450", "PowerEdge R650",
    "PowerEdge R650xs", "PowerEdge R660", "PowerEdge R660xs",
    "PowerEdge R6615", "PowerEdge R6625",
], notes="1U rack server")
_fam("Dell", "server", 2, [
    "PowerEdge R730", "PowerEdge R730xd", "PowerEdge R540", "PowerEdge R740",
    "PowerEdge R740xd", "PowerEdge R740xd2", "PowerEdge R7415", "PowerEdge R7425",
    "PowerEdge R7515", "PowerEdge R7525", "PowerEdge R550", "PowerEdge R750",
    "PowerEdge R750xs", "PowerEdge R760", "PowerEdge R760xs", "PowerEdge R760xd2",
    "PowerEdge R7615", "PowerEdge R7625", "PowerEdge R840", "PowerEdge R860",
], notes="2U rack server")
_fam("Dell", "server", 3, ["PowerEdge R940"], notes="3U four-socket server")
_fam("Dell", "server", 4, ["PowerEdge R940xa", "PowerEdge R960"], notes="4U four-socket server")
_fam("Dell", "gpu-server", 2, ["PowerEdge R750xa", "PowerEdge R760xa"], notes="2U GPU server")
_fam("Dell", "gpu-server", 6, ["PowerEdge XE9680"], notes="6U 8-GPU server")
_fam("Dell", "storage", 2, ["PowerVault ME4012", "PowerVault ME4024",
                             "PowerVault ME5012", "PowerVault ME5024"], notes="2U storage array")
_fam("Dell", "storage", 5, ["PowerVault ME4084", "PowerVault ME5084"], notes="5U 84-drive enclosure")
_fam("Dell", "switch", 1, ["S4148F-ON", "S5248F-ON", "S5232F-ON", "N3248TE-ON"],
     notes="1U data-centre switch")

# ── HPE (incl. Aruba) ──────────────────────────────────────────────────────
def _gens(name, gens):
    return (f"ProLiant {name}",) + tuple(f"{name} {g}" for g in gens)


_fam("HPE", "server", 1, [
    _gens("DL20", ["Gen9", "Gen10", "Gen10 Plus", "Gen11"]),
    _gens("DL160", ["Gen9", "Gen10"]),
    _gens("DL320", ["Gen11"]),
    _gens("DL325", ["Gen10", "Gen10 Plus", "Gen10 Plus v2", "Gen11"]),
    _gens("DL360", ["Gen9", "Gen10", "Gen10 Plus", "Gen11"]),
    _gens("DL365", ["Gen10 Plus", "Gen11"]),
], notes="1U in every listed generation")
_fam("HPE", "server", 2, [
    _gens("DL180", ["Gen10"]),
    _gens("DL380", ["Gen9", "Gen10", "Gen10 Plus", "Gen11"]),
    _gens("DL385", ["Gen10", "Gen10 Plus", "Gen10 Plus v2", "Gen11"]),
    _gens("DL560", ["Gen10", "Gen11"]),
], notes="2U in every listed generation")
_fam("HPE", "server", 4, [_gens("DL580", ["Gen9", "Gen10"])], notes="4U four-socket server")
_fam("HPE", "storage", 2, ["MSA 2060", "MSA 2062"], notes="2U storage array (SFF or LFF)")
_fam("HPE", "switch", 1, [
    ("Aruba 2930F", "2930F", "JL262A"), ("Aruba 6200F", "6200F"), ("Aruba 6300M", "6300M"),
    ("Aruba CX 8325", "8325"), ("Aruba CX 8360", "8360"),
], notes="1U switch")

# ── Lenovo ThinkSystem ─────────────────────────────────────────────────────
_fam("Lenovo", "server", 1, [
    "ThinkSystem SR250", "ThinkSystem SR250 V2", "ThinkSystem SR250 V3",
    "ThinkSystem SR530", "ThinkSystem SR570", "ThinkSystem SR630",
    "ThinkSystem SR630 V2", "ThinkSystem SR630 V3", "ThinkSystem SR635",
    "ThinkSystem SR635 V3", "ThinkSystem SR645", "ThinkSystem SR645 V3",
], notes="1U rack server")
_fam("Lenovo", "server", 2, [
    "ThinkSystem SR550", "ThinkSystem SR590", "ThinkSystem SR650",
    "ThinkSystem SR650 V2", "ThinkSystem SR650 V3", "ThinkSystem SR655",
    "ThinkSystem SR655 V3", "ThinkSystem SR665", "ThinkSystem SR665 V3",
    "ThinkSystem SR850", "ThinkSystem SR850 V2", "ThinkSystem SR850 V3",
], notes="2U rack server")
_fam("Lenovo", "server", 4, [
    "ThinkSystem SR860", "ThinkSystem SR860 V2", "ThinkSystem SR860 V3", "ThinkSystem SR950",
], notes="4U four/eight-socket server")

# ── Supermicro ─────────────────────────────────────────────────────────────
_fam("Supermicro", "server", 1, [
    "SYS-1029P-WTR", "SYS-1029U-TR4", "SYS-6019P-WTR", "SYS-5019C-M", "SYS-5019D-FN8TP",
    "SYS-110P-WTR", "SYS-120U-TNR", "SYS-121H-TNR", "AS-1014S-WTRT", "AS-1114S-WTRT",
], notes="1U SuperServer")
_fam("Supermicro", "server", 2, [
    "SYS-6029P-TR", "SYS-6029U-TR4", "SYS-220U-TNR", "SYS-221H-TNR", "AS-2014S-TR",
], notes="2U SuperServer")
_fam("Supermicro", "gpu-server", 4, ["SYS-4029GP-TRT"], notes="4U GPU server")
_fam("Supermicro", "storage", 4, ["SSG-6049P-E1CR36L"], notes="4U 36-bay storage server")

# ── Cisco ──────────────────────────────────────────────────────────────────
_fam("Cisco", "switch", 1, [
    # Catalyst 9200 / 9200L
    "C9200-24T", "C9200-24P", "C9200-48T", "C9200-48P",
    "C9200L-24T-4G", "C9200L-24P-4G", "C9200L-48T-4G", "C9200L-48P-4G", "C9200L-48P-4X",
    # Catalyst 9300 / 9300L / 9300X
    "C9300-24T", "C9300-24P", "C9300-24U", "C9300-24UX", "C9300-48T", "C9300-48P",
    "C9300-48U", "C9300-48UN", "C9300-48UXM", "C9300L-24T-4G", "C9300L-48P-4X", "C9300X-48HX",
    # Catalyst 9500
    "C9500-16X", "C9500-40X", "C9500-24Y4C", "C9500-48Y4C", "C9500-32C", "C9500-32QC",
    # Legacy Catalyst
    "WS-C2960X-24PS-L", "WS-C2960X-48FPD-L", "WS-C3850-24T", "WS-C3850-48P",
    # Nexus 9300 fixed
    "N9K-C93180YC-EX", "N9K-C93180YC-FX", "N9K-C93180YC-FX3", "N9K-C93108TC-FX",
    "N9K-C93108TC-FX3P", "N9K-C9332C", "N9K-C9336C-FX2", "N9K-C9316D-GX",
    # UCS fabric interconnects
    ("UCS 6332", "UCS-FI-6332"), ("UCS 6454", "UCS-FI-6454"), ("UCS 6536", "UCS-FI-6536"),
], notes="1RU switch", alias=_cisco_alias)
_fam("Cisco", "switch", 2, ["N9K-C9364C"], notes="2RU spine switch", alias=_cisco_alias)
_fam("Cisco", "chassis", 4, ["C9404R"], notes="4-slot Catalyst 9400 chassis, 4RU", alias=_cisco_alias)
_fam("Cisco", "chassis", 10, ["C9407R"], notes="7-slot Catalyst 9400 chassis, 10RU", alias=_cisco_alias)
_fam("Cisco", "chassis", 13, ["C9410R"], notes="10-slot Catalyst 9400 chassis, 13RU", alias=_cisco_alias)
_fam("Cisco", "chassis", 8, ["C9606R"], notes="Catalyst 9600 chassis, 8RU", alias=_cisco_alias)
_fam("Cisco", "chassis", 7, ["N9K-C9504"], notes="Nexus 9504 chassis, 7RU", alias=_cisco_alias)
_fam("Cisco", "chassis", 13, ["N9K-C9508"], notes="Nexus 9508 chassis, 13RU", alias=_cisco_alias)
_fam("Cisco", "wireless-controller", 1, ["C9800-40"], notes="1RU WLC", alias=_cisco_alias)
_fam("Cisco", "wireless-controller", 2, ["C9800-80"], notes="2RU WLC", alias=_cisco_alias)
_fam("Cisco", "router", 1, [
    ("ISR4321", "ISR4321/K9"), ("ISR4331", "ISR4331/K9"), ("ISR4431", "ISR4431/K9"),
    "C8200-1N-4T", "C8300-1N1S-6T", "C8300-1N1S-4T2X", "C8500-12X", "C8500-12X4QC",
    "ASR1001-X", "ASR1001-HX",
], notes="1RU router", alias=_cisco_alias)
_fam("Cisco", "router", 2, [
    ("ISR4351", "ISR4351/K9"), ("ISR4451-X", "ISR4451-X/K9", "ISR4451"), ("ISR4461", "ISR4461/K9"),
    "C8300-2N2S-6T", "C8300-2N2S-4T2X", "ASR1002-X", "ASR1002-HX",
], notes="2RU router", alias=_cisco_alias)
_fam("Cisco", "chassis", 4, ["ASR1004"], notes="ASR 1000 modular chassis, 4RU")
_fam("Cisco", "chassis", 6, ["ASR1006", "ASR1006-X"], notes="ASR 1000 modular chassis, 6RU")
_fam("Cisco", "chassis", 9, ["ASR1009-X"], notes="ASR 1000 modular chassis, 9RU")
_fam("Cisco", "chassis", 13, ["ASR1013"], notes="ASR 1000 modular chassis, 13RU")
_fam("Cisco", "server", 1, [
    ("UCS C220", "C220 M4", "C220 M5", "C220 M6", "C220 M7"), ("UCS C225 M6", "C225 M6"),
], notes="1RU rack server (all listed generations)")
_fam("Cisco", "server", 2, [
    ("UCS C240", "C240 M4", "C240 M5", "C240 M6", "C240 M7"), ("UCS C245 M6", "C245 M6"),
], notes="2RU rack server (all listed generations)")
_fam("Cisco", "server", 4, [("UCS C480 M5", "C480 M5")], notes="4RU server")
_fam("Cisco", "chassis", 6, [("UCS 5108", "UCSB-5108-AC2")], notes="UCS B-series blade chassis, 6RU")
_fam("Cisco", "chassis", 7, [("UCS X9508", "UCSX-9508")], notes="UCS X-series modular chassis, 7RU")
_fam("Cisco", "firewall", 1, [
    "Firepower 1120", "Firepower 1140", "Firepower 1150",
    "Firepower 2110", "Firepower 2120", "Firepower 2130", "Firepower 2140",
    "Firepower 3110", "Firepower 3120", "Firepower 3130", "Firepower 3140",
    "Firepower 4112", "Firepower 4115", "Firepower 4125", "Firepower 4145",
    "ASA5525-X", "ASA5545-X", "ASA5555-X",
], notes="1RU firewall",
    alias=lambda m: [m.replace("Firepower ", "FPR-"), m.replace("Firepower ", "FPR")]
    if m.startswith("Firepower") else [m.replace("ASA", "ASA ")])
_fam("Cisco", "chassis", 3, [("Firepower 9300", "FPR-9300", "FPR9300")], notes="3RU security chassis")

# ── Juniper ────────────────────────────────────────────────────────────────
_fam("Juniper", "switch", 1, [
    "EX2300-24T", "EX2300-24P", "EX2300-48T", "EX2300-48P",
    "EX3400-24T", "EX3400-48T", "EX3400-48P", "EX4100-48P",
    "EX4300-48T", "EX4300-48P", "EX4400-24T", "EX4400-48T", "EX4400-48P", "EX4650-48Y",
    "QFX5100-48S", "QFX5110-48S", "QFX5120-48Y", "QFX5120-32C", "QFX5200-32C", "QFX5130-32CD",
], notes="1U switch")
_fam("Juniper", "switch", 2, ["QFX10002-36Q", "QFX10002-72Q"], notes="2U spine switch")
_fam("Juniper", "chassis", 13, ["QFX10008"], notes="13U modular switch chassis")
_fam("Juniper", "firewall", 1, ["SRX340", "SRX345", "SRX380", "SRX1500", "SRX4100", "SRX4200", "SRX4600"],
     notes="1U services gateway")
_fam("Juniper", "chassis", 5, ["SRX5400"], notes="5U services gateway chassis")
_fam("Juniper", "chassis", 8, ["SRX5600"], notes="8U services gateway chassis")
_fam("Juniper", "chassis", 14, ["SRX5800"], notes="14U services gateway chassis")
_fam("Juniper", "router", 1, ["MX204"], notes="1U router")
_fam("Juniper", "router", 2, ["MX304"], notes="2U router")
_fam("Juniper", "router", 3, ["MX10003"], notes="3U router")
_fam("Juniper", "chassis", 5, ["MX240"], notes="5U router chassis")
_fam("Juniper", "chassis", 8, ["MX480"], notes="8U router chassis")
_fam("Juniper", "chassis", 16, ["MX960"], notes="16U router chassis")
_fam("Juniper", "chassis", 13, ["MX10008"], notes="13U router chassis")

# ── Arista ─────────────────────────────────────────────────────────────────
_fam("Arista", "switch", 1, [
    "DCS-7050SX3-48YC8", "DCS-7050SX3-48YC12", "DCS-7050CX3-32S", "DCS-7050CX3M-32S",
    "DCS-7050TX3-48C8", "DCS-7050QX-32S", "DCS-7050SX-64", "DCS-7050TX-64",
    "DCS-7280SR-48C6", "DCS-7280SR2-48YC6", "DCS-7280SR3-48YC8", "DCS-7280TR-48C6",
    "DCS-7280QR-C36", "DCS-7280CR3-32P4", "DCS-7280CR3K-32P4", "DCS-7280CR3-32D4",
    "DCS-7010T-48", "DCS-7020TR-48", "CCS-720XP-48ZC2", "CCS-720XP-24ZY4",
], notes="1RU switch", alias=lambda m: [re.sub(r"^(?:DCS|CCS)-", "", m)])

# ── Fortinet ───────────────────────────────────────────────────────────────
_fam("Fortinet", "firewall", 1, [
    "FG-100E", "FG-101E", "FG-100F", "FG-101F", "FG-120G", "FG-200E", "FG-201E",
    "FG-200F", "FG-201F", "FG-300E", "FG-301E", "FG-400E", "FG-401E", "FG-400F",
    "FG-401F", "FG-500E", "FG-501E", "FG-600E", "FG-601E", "FG-600F", "FG-601F",
], notes="1RU FortiGate", alias=lambda m: ["FortiGate " + m[3:]])
_fam("Fortinet", "switch", 1, [("FortiSwitch 124F", "FS-124F"), ("FortiSwitch 148F", "FS-148F"),
                                ("FortiSwitch 248E-POE", "FS-248E-POE"), ("FortiSwitch 448E", "FS-448E")],
     notes="1RU FortiSwitch")

# ── Palo Alto Networks ─────────────────────────────────────────────────────
_fam("Palo Alto", "firewall", 1, [
    "PA-820", "PA-850", "PA-1410", "PA-1420", "PA-3220", "PA-3250", "PA-3260",
    "PA-3410", "PA-3420", "PA-3430", "PA-3440",
], notes="1RU firewall", alias=_strip_prefix("PA-"))
_fam("Palo Alto", "firewall", 3, ["PA-5220", "PA-5250", "PA-5260", "PA-5280"],
     notes="3RU firewall", alias=_strip_prefix("PA-"))
_fam("Palo Alto", "chassis", 9, ["PA-7050"], notes="9RU chassis firewall", alias=_strip_prefix("PA-"))
_fam("Palo Alto", "chassis", 19, ["PA-7080"], notes="19RU chassis firewall", alias=_strip_prefix("PA-"))

# ── Ubiquiti UniFi ─────────────────────────────────────────────────────────
_fam("Ubiquiti", "switch", 1, [
    "USW-24", "USW-48", "USW-24-PoE", "USW-48-PoE",
    ("USW-Pro-24", "Switch Pro 24"), ("USW-Pro-48", "Switch Pro 48"),
    ("USW-Pro-24-PoE", "Switch Pro 24 PoE"), ("USW-Pro-48-PoE", "Switch Pro 48 PoE"),
    ("USW-Pro-Max-24", "Pro Max 24"), ("USW-Pro-Max-48", "Pro Max 48"),
    ("USW-Pro-Max-24-PoE", "Pro Max 24 PoE"), ("USW-Pro-Max-48-PoE", "Pro Max 48 PoE"),
    ("USW-Aggregation", "Switch Aggregation"), ("USW-Pro-Aggregation", "Switch Pro Aggregation"),
    ("USW-EnterpriseXG-24", "Enterprise XG 24"), ("USW-Enterprise-24-PoE", "Enterprise 24 PoE"),
    ("USW-Enterprise-48-PoE", "Enterprise 48 PoE"),
], notes="1U UniFi switch")
_fam("Ubiquiti", "router", 1, [
    ("UDM-Pro", "UDM Pro", "Dream Machine Pro"), ("UDM-SE", "UDM SE", "Dream Machine SE"),
    ("UDM-Pro-Max", "UDM Pro Max", "Dream Machine Pro Max"), ("UXG-Pro", "Next-Gen Gateway Pro"),
], notes="1U UniFi gateway")
_fam("Ubiquiti", "nas", 1, [("UNVR", "Network Video Recorder"), ("UNAS-Pro", "UNAS Pro")],
     notes="1U UniFi recorder / NAS")
_fam("Ubiquiti", "nas", 2, [("UNVR-Pro", "UNVR Pro", "Network Video Recorder Pro")], notes="2U UniFi recorder")
_fam("Ubiquiti", "pdu", 1, [("USP-PDU-Pro", "PDU Pro")], notes="1U horizontal PDU")

# ── MikroTik ───────────────────────────────────────────────────────────────
_fam("MikroTik", "router", 1, [
    "CCR1009-7G-1C-1S+", "CCR1016-12G", "CCR1036-12G-4S", "CCR1036-8G-2S+",
    "CCR1072-1G-8S+", "CCR2004-16G-2S+", "CCR2004-1G-12S+2XS", "CCR2116-12G-4S+",
    "CCR2216-1G-12XS-2XQ", "RB1100AHx4",
], notes="1U Cloud Core Router / RouterBOARD")
_fam("MikroTik", "switch", 1, [
    "CRS326-24G-2S+RM", "CRS328-24P-4S+RM", "CRS354-48G-4S+2Q+RM", "CRS354-48P-4S+2Q+RM",
    "CRS317-1G-16S+RM", "CRS326-24S+2Q+RM", "CRS312-4C+8XG-RM", "CRS518-16XS-2XQ-RM",
], notes="1U Cloud Router Switch (RM = rack-mount)")

# ── Netgear ────────────────────────────────────────────────────────────────
_fam("Netgear", "switch", 1, [
    "GS724T", "GS724TP", "GS728TP", "GS748T", "GS752TP", "GS752TPP", "JGS524",
    "XS716T", "XS728T", "XS748T", "M4300-28G", "M4300-52G",
], notes="1U rack-mount switch")

# ── Synology ───────────────────────────────────────────────────────────────
_fam("Synology", "nas", 1, ["RS422+", "RS819", "RS820+", "RS820RP+", "RS822+", "RS1619xs+", "FS2500"],
     notes="1U RackStation / FlashStation")
_fam("Synology", "nas", 2, ["RS1221+", "RS1221RP+", "RS2418+", "RS2418RP+", "RS2421+", "RS2423+",
                            "RS2423RP+", "RS3618xs", "RS3621xs+", "RS3621RPxs", "SA3400", "SA3600", "FS3400"],
     notes="2U RackStation / SA / FS")
_fam("Synology", "nas", 3, ["RS4017xs+", "RS4021xs+"], notes="3U 16-bay RackStation")
_fam("Synology", "storage", 4, ["HD6500"], notes="4U 60-bay")
_fam("Synology", "storage", 1, ["RX418"], notes="1U expansion unit")
_fam("Synology", "storage", 2, ["RX1217", "RX1222sas"], notes="2U expansion unit")

# ── QNAP ───────────────────────────────────────────────────────────────────
_qrp = lambda m: [m + "-RP"] if not m.endswith("-RP") else [m[:-3]]
_fam("QNAP", "nas", 1, ["TS-432PXU", "TS-463XU", "TS-464U", "TS-977XU"], notes="1U rack NAS", alias=_qrp)
_fam("QNAP", "nas", 2, ["TS-832PXU", "TS-1232PXU", "TS-863XU", "TS-1263XU", "TS-873AU", "TS-1273AU",
                        "TS-864eU", "TS-h1277AXU-RP", "TS-h2490FU"], notes="2U rack NAS", alias=_qrp)
_fam("QNAP", "nas", 3, ["TS-1673AU-RP", "TS-h1677AXU-RP", "TS-h1886XU-RP"], notes="3U rack NAS", alias=_qrp)

# ── NetApp ─────────────────────────────────────────────────────────────────
_aff = lambda m: [m.split(" ", 1)[1]] if " " in m else []
_fam("NetApp", "storage", 2, [
    "AFF A150", "AFF A250", "AFF C250", "FAS2720", "FAS2750", "FAS2820", "FAS500f",
    "DS212C", "DS224C", "NS224", "E2812", "E2824", "EF300", "EF600",
], notes="2U controller / shelf", alias=_aff)
_fam("NetApp", "storage", 4, ["AFF A400", "AFF A800", "AFF A700s", "FAS8300", "DS460C", "E2860"],
     notes="4U controller / shelf", alias=_aff)
_fam("NetApp", "storage", 8, ["AFF A700", "AFF A900"], notes="8U controller chassis", alias=_aff)

# ── UPS ────────────────────────────────────────────────────────────────────
_fam("APC", "ups", 2, [
    ("SMT750RM2U", "SMT750RM2UC"), ("SMT1000RM2U", "SMT1000RM2UC"), ("SMT1500RM2U", "SMT1500RM2UC"),
    ("SMT2200RM2U", "SMT2200RM2UC"), ("SMT3000RM2U", "SMT3000RM2UC"),
    "SMT750RMI2U", "SMT1000RMI2U", "SMT1500RMI2U", "SMT2200RMI2U", "SMT3000RMI2U",
    "SMX1500RM2U", "SMX2200RM2U", "SMX3000RMLV2U",
    ("SRT1000RMXLI", "SRT1000RMXLA"), ("SRT1500RMXLI", "SRT1500RMXLA"),
    ("SRT2200RMXLI", "SRT2200RMXLA"), ("SRT3000RMXLI", "SRT3000RMXLA", "SRT3000RMXLT"),
    "SMC1000I-2U", "SMC1500I-2U",
], notes="2U rack-mount Smart-UPS")
_fam("Eaton", "ups", 2, ["5PX1500RT", "5PX2200RT", "5PX3000RT", "9PX1500RT", "9PX2000RT", "9PX3000RT"],
     notes="2U rack/tower UPS")
_fam("Eaton", "ups", 1, ["5P1500R", "5P1550R"], notes="1U rack UPS")
_fam("Tripp Lite", "ups", 2, ["SMART1500RM2U", "SMART3000RM2U", "SU1500RTXLCD2U"], notes="2U rack UPS")
_fam("CyberPower", "ups", 1, ["OR500LCDRM1U", "OR700LCDRM1U", "OR1500LCDRM1U"], notes="1U rack UPS")
_fam("CyberPower", "ups", 2, [
    "OR1000LCDRT2U", "OR2200LCDRT2U", "PR750RT2U", "PR1000RT2U", "PR1500RT2U", "PR2200RT2U",
    "PR3000RT2U", "OL1000RTXL2U", "OL1500RTXL2U", "OL2000RTXL2U", "OL3000RTXL2U",
], notes="2U rack/tower UPS")

# ── PDUs ───────────────────────────────────────────────────────────────────
_fam("APC", "pdu", 1, ["AP7900B", "AP7901B", "AP7920B", "AP7921B", "AP9559", "AP9562", "AP6015A"],
     notes="1U horizontal rack PDU")
_fam("APC", "pdu", 0, ["AP7930", "AP7931", "AP7941B", "AP8941"],
     notes="Zero-U vertical PDU: mounts in the rack's side channel, uses no rack units")
_fam("Tripp Lite", "pdu", 1, ["PDUMH15", "PDUMH20", "PDU1215"], notes="1U horizontal PDU")

# ── Passive / accessories ──────────────────────────────────────────────────
_fam("Generic", "patch-panel", 1, [
    ("24-port patch panel", "24-port patch panel 1U", "24 port cat6 patch panel", "24 port cat5e patch panel"),
    ("48-port patch panel 1U", "48-port high density patch panel", "48 port 1U patch panel"),
], notes="1U patch panel")
_fam("Generic", "patch-panel", 2, [("48-port patch panel", "48-port patch panel 2U")],
     notes="Standard-density 48-port panel is 2U; high-density 1U versions exist (say '48-port patch panel 1U')")
_fam("Panduit", "patch-panel", 1, ["CP24BLY"], notes="24-port 1U patch panel")
_fam("Panduit", "patch-panel", 2, ["CP48BLY"], notes="48-port 2U patch panel")
_fam("Generic", "cable-management", 1, [("horizontal cable manager 1U", "cable management 1U", "brush panel 1U")],
     notes="1U horizontal cable manager")
_fam("Generic", "cable-management", 2, [("horizontal cable manager 2U", "cable management 2U")],
     notes="2U horizontal cable manager")
_fam("Generic", "blanking-panel", 1, [("blanking panel 1U", "blank panel 1U", "filler panel 1U")], notes="")
_fam("Generic", "blanking-panel", 2, [("blanking panel 2U", "blank panel 2U", "filler panel 2U")], notes="")
_fam("Generic", "blanking-panel", 4, [("blanking panel 4U", "blank panel 4U", "filler panel 4U")], notes="")
_fam("APC", "blanking-panel", 1, ["AR8101BLK", "AR8136BLK", "AR8136BLK200"], notes="1U blanking panel")
_fam("APC", "blanking-panel", 2, ["AR8102BLK"], notes="2U blanking panel")
_fam("APC", "blanking-panel", 4, ["AR8104BLK"], notes="4U blanking panel")
_fam("Generic", "shelf", 1, [("rack shelf 1U", "1U shelf", "cantilever shelf 1U")], notes="1U fixed shelf")
_fam("Generic", "shelf", 2, [("rack shelf 2U", "2U shelf", "cantilever shelf 2U")], notes="2U fixed shelf")
_fam("Generic", "kvm", 1, [("1U LCD KVM console", "rack console 1U", "KVM drawer 1U")], notes="1U console drawer")


# ═══════════════════════════════════════════════════════════════════════════
# Name normalisation and index
# ═══════════════════════════════════════════════════════════════════════════
# Words that carry no model identity: vendor names, product-family names and
# generic nouns. They are removed from both catalog names and queries, so
# "Dell PowerEdge R-750", "poweredge r750" and "R750" all reduce to "r750".
_STOP = {
    # vendors
    "dell", "emc", "dellemc", "hpe", "hp", "hewlett", "packard", "aruba",
    "lenovo", "supermicro", "cisco", "systems", "juniper", "networks", "arista",
    "fortinet", "palo", "alto", "paloalto", "ubiquiti", "ui", "unifi", "mikrotik", "netgear",
    "synology", "qnap", "netapp", "apc", "schneider", "electric", "eaton", "cyberpower",
    "raritan", "tripp", "lite", "tripplite", "panduit", "generic",
    # product families / prefixes
    "poweredge", "pe", "powervault", "proliant", "thinksystem", "superserver", "sys",
    "catalyst", "nexus", "n9k", "ws", "ucs", "fortigate", "fg", "smart", "ups", "smartups",
    "rackstation", "routerboard", "dcs", "ccs", "prosafe", "insight",
    # generic nouns
    "server", "switch", "router", "firewall", "rack", "rackmount", "mount", "appliance",
    "series", "model", "nas", "device", "unit",
}
# Family names people glue onto the model ("poweredger750" -> "r750"). Kept to an
# explicit list: some stop words are real parts of SKUs ("SMART1500RM2U").
_STOP_PREFIXES = ["poweredge", "powervault", "proliant", "thinksystem", "superserver",
                  "catalyst", "nexus", "fortigate", "rackstation"]
_LONG_STOPS = [w for w in _STOP if len(w) >= 5]

# ── F5, SonicWall (added: common appliances the online lookup could not reach) ──
_fam("F5", "chassis", 1, [("BIG-IP i2600", "i2600"), ("BIG-IP i2800", "i2800"), ("BIG-IP i4600", "i4600"),
                          ("BIG-IP i4800", "i4800"), ("BIG-IP i5600", "i5600"), ("BIG-IP i5800", "i5800"),
                          ("BIG-IP i5820", "i5820"), ("BIG-IP i7600", "i7600"), ("BIG-IP i7800", "i7800"),
                          ("BIG-IP i7820", "i7820"), ("BIG-IP i10600", "i10600"), ("BIG-IP i10800", "i10800")],
     notes="1U BIG-IP iSeries appliance")
_fam("SonicWall", "firewall", 1, [("NSa 2650", "NSA2650"), ("NSa 3650", "NSA3650"), ("NSa 4650", "NSA4650"),
                                  ("NSa 5650", "NSA5650"), ("NSa 2700", "NSA2700"), ("NSa 3700", "NSA3700"),
                                  ("NSa 4700", "NSA4700"), ("NSa 5700", "NSA5700"), ("NSa 6700", "NSA6700")],
     notes="1U firewall")

_MAKE_ALIASES = {
    "dell": "Dell", "dellemc": "Dell", "emc": "Dell",
    "hpe": "HPE", "hp": "HPE", "hewlettpackardenterprise": "HPE", "hewlettpackard": "HPE", "aruba": "HPE",
    "lenovo": "Lenovo", "supermicro": "Supermicro", "cisco": "Cisco", "ciscosystems": "Cisco",
    "juniper": "Juniper", "junipernetworks": "Juniper", "arista": "Arista", "aristanetworks": "Arista",
    "fortinet": "Fortinet", "fortigate": "Fortinet",
    "paloalto": "Palo Alto", "paloaltonetworks": "Palo Alto", "pan": "Palo Alto",
    "ubiquiti": "Ubiquiti", "ui": "Ubiquiti", "unifi": "Ubiquiti",
    "mikrotik": "MikroTik", "netgear": "Netgear", "synology": "Synology", "qnap": "QNAP",
    "netapp": "NetApp", "apc": "APC", "schneider": "APC", "schneiderelectric": "APC",
    "eaton": "Eaton", "tripplite": "Eaton", "cyberpower": "CyberPower", "panduit": "Panduit",
}
# Makes that should be treated as one vendor when filtering.
_MAKE_GROUP = {"Tripp Lite": "Eaton"}


def _tokens(text: str) -> list[str]:
    text = (text or "").lower().replace("+", " plus ")
    # "Enterprise" is a model word for Ubiquiti, so drop it only inside HPE's full name.
    text = re.sub(r"hewlett[\s-]*packard[\s-]*enterprise", "hpe", text)
    return [t for t in re.split(r"[^a-z0-9]+", text) if t]


def _strip_stop_prefix(tok: str) -> str:
    changed = True
    while changed:
        changed = False
        for w in _STOP_PREFIXES:
            if tok.startswith(w) and len(tok) > len(w) and any(c.isdigit() for c in tok[len(w):]):
                tok, changed = tok[len(w):], True
                break
    return tok


def _key_tokens(text: str, typo_tolerant: bool = False) -> tuple[list[str], float]:
    """Tokens with stop words removed. Returns (tokens, worst typo similarity used)."""
    out, worst = [], 1.0
    for t in _tokens(text):
        if t in _STOP:
            continue
        if typo_tolerant and len(t) >= 5 and not any(c.isdigit() for c in t):
            close = difflib.get_close_matches(t, _LONG_STOPS, n=1, cutoff=0.8)
            if close:
                worst = min(worst, difflib.SequenceMatcher(None, t, close[0]).ratio())
                continue
        out.append(_strip_stop_prefix(t))
    return out, worst


def _norm(text: str) -> str:
    return "".join(_key_tokens(text)[0])


def _sorted_norm(text: str) -> str:
    """Order-insensitive key: 'patch panel 48 port' == '48-port patch panel'."""
    parts = []
    for t in _key_tokens(text)[0]:
        parts += re.findall(r"[a-z]+|\d+", t)
    return " ".join(sorted(parts))


def _canon_make(make: str) -> str | None:
    k = "".join(_tokens(make))
    m = _MAKE_ALIASES.get(k)
    if m is None:
        for c in CATALOG:
            if "".join(_tokens(c["make"])) == k and k:
                m = c["make"]
                break
    return _MAKE_GROUP.get(m, m) if m else None


def _entry_make(entry) -> str:
    return _MAKE_GROUP.get(entry["make"], entry["make"])


def _build_index():
    exact, unordered = {}, {}
    for i, e in enumerate(CATALOG):
        for name in (e["model"],) + e["aliases"]:
            for idx, key in ((exact, _norm(name)), (unordered, _sorted_norm(name))):
                if key:
                    idx.setdefault(key, [])
                    if i not in idx[key]:
                        idx[key].append(i)
    return exact, unordered


_EXACT, _UNORDERED = _build_index()


def _result(entry, source):
    return {"make": entry["make"], "model": entry["model"], "units": entry["units"],
            "category": entry["category"], "source": source, "notes": entry["notes"]}


def _pick(indices, make):
    """Choose among catalog indices sharing a key; prefer the user's make. None if ambiguous."""
    cands = [CATALOG[i] for i in indices]
    if make:  # a recognised make only matches its own products (or generic items)
        cands = [c for c in cands if _entry_make(c) in (make, "Generic")]
    if not cands or len({c["units"] for c in cands}) > 1:
        return None
    return cands[0]


# Explicit height in the text: "2U", "1RU", "4 U", "3 RU". Not when glued to a
# model code ("C9300-48U" is a PoE flavour, not a 48U device).
_HEIGHT_RE = re.compile(r"(?<![\w-])(\d{1,2})\s*(?:u|ru|he)\b", re.I)
# Vendor SKU suffixes that encode height: "...RM2U", "...RT2U", "...XL2U", "...RT1UG2".
_HEIGHT_SUFFIX_RE = re.compile(r"(?:rm|rt|xl|rmi|rti)([1-9])u", re.I)
MAX_PARSED_U = 24


def _parse_height(text: str) -> int | None:
    for rx in (_HEIGHT_RE, _HEIGHT_SUFFIX_RE):
        m = rx.search(text or "")
        if m and 1 <= int(m.group(1)) <= MAX_PARSED_U:
            return int(m.group(1))
    return None


FUZZY_CUTOFF = 0.85
_AMBIGUITY_MARGIN = 0.02


def _digits(s: str) -> list[str]:
    return re.findall(r"\d+", s)


def _digits_compatible(q: list[str], c: list[str]) -> bool:
    """Numbers must agree; a trailing SKU number on one side is fine ('ISR4331/K9')."""
    n = min(len(q), len(c))
    return n > 0 and q[:n] == c[:n]


def _family_match(text: str, make: str | None):
    q = _norm(text)
    if len(q) < 5:
        return None
    best = None
    for key, indices in _EXACT.items():
        if (len(key) >= 4 and len(key) < len(q) and q.startswith(key) and re.search(r"\d", key)
                and re.search(r"[a-z]", key) and not (key[-1].isdigit() and q[len(key)].isdigit())):
            if best is None or len(key) > len(best[0]):
                best = (key, indices)
    return _pick(best[1], make) if best else None


def _family_result(make, model, mk):
    """4. family: a catalog model is the start of what was typed ("Aruba 2930F" for "2930F-24G")."""
    fam = _family_match(model, mk) or _family_match(f"{make} {model}", mk)
    return _result(fam, "catalog:family") if fam else None


def lookup_device(make: str, model: str) -> dict | None:
    """Rack-unit height of a device from the offline catalog.

    Returns {"make", "model", "units", "category", "source", "notes"} or None.
    `source` is "catalog:exact", "parsed-from-name", "catalog:family" or "catalog:fuzzy(0.91)".
    Order: normalised exact match, then an explicit height in the text, then a
    conservative fuzzy match (similarity >= FUZZY_CUTOFF, numbers must agree,
    and the best hit must not be a near-tie with a different height).
    """
    make, model = (make or "").strip(), (model or "").strip()
    if not make and not model:
        return None
    mk = _canon_make(make)

    # 1. exact (model alone, then make + model), order-sensitive then order-insensitive
    for text in (model, f"{make} {model}"):
        for idx, key in ((_EXACT, _norm(text)), (_UNORDERED, _sorted_norm(text))):
            if key and key in idx:
                entry = _pick(idx[key], mk)
                if entry:
                    return _result(entry, "catalog:exact")

    # 2. explicit height in the user's text
    units = _parse_height(model) or _parse_height(make)
    if units:
        return {"make": make, "model": model, "units": units, "category": "unknown",
                "source": "parsed-from-name", "notes": f"Height {units}U read from the name you entered"}

    # 3. conservative fuzzy match
    q_tokens, typo_sim = _key_tokens(model or make, typo_tolerant=True)
    q = "".join(q_tokens)
    if len(q) < 3:
        return _family_result(make, model, mk)
    q_digits = _digits(q)
    scored = []
    for key, indices in _EXACT.items():
        if not _digits_compatible(q_digits, _digits(key)):
            continue
        sm = difflib.SequenceMatcher(None, q, key)
        if sm.real_quick_ratio() < FUZZY_CUTOFF or sm.quick_ratio() < FUZZY_CUTOFF:
            continue
        r = sm.ratio() * typo_sim
        if r >= FUZZY_CUTOFF:
            for i in indices:
                e = CATALOG[i]
                if mk and _entry_make(e) not in (mk, "Generic"):
                    continue
                scored.append((r, i))
    if not scored:
        return _family_result(make, model, mk)
    scored.sort(key=lambda t: -t[0])
    best_r, best_i = scored[0]
    best = CATALOG[best_i]
    for r, i in scored[1:]:
        if best_r - r > _AMBIGUITY_MARGIN:
            break
        if CATALOG[i]["units"] != best["units"]:
            return None  # two equally good guesses disagree on height: don't guess
    return _result(best, f"catalog:fuzzy({best_r:.2f})")


# ═══════════════════════════════════════════════════════════════════════════
# Rack geometry
# ═══════════════════════════════════════════════════════════════════════════
def _positions(rows) -> list[int]:
    """Rack position of each row (1 = bottom), from its 'uNN' label or list order."""
    n = len(rows)
    out = []
    for i, r in enumerate(rows):
        m = re.search(r"\d+", str(r.get("unit", "")))
        out.append(int(m.group()) if m else n - i)
    return out


def _is_free(row) -> bool:
    return row.get("status") == "unoccupied"


def _free_runs(rows) -> list[list[int]]:
    """Runs of contiguous free rows as lists of row indices (top to bottom).

    Rows are contiguous when they are neighbours in the list AND their rack
    positions differ by exactly one (a missed detection breaks the run).
    """
    pos = _positions(rows)
    runs, cur = [], []
    for i, r in enumerate(rows):
        if _is_free(r) and cur and pos[cur[-1]] - pos[i] == 1:
            cur.append(i)
        else:
            if cur:
                runs.append(cur)
            cur = [i] if _is_free(r) else []
    if cur:
        runs.append(cur)
    return runs


def _range_label(positions) -> str:
    lo, hi = min(positions), max(positions)
    return f"U{lo:02d}" if lo == hi else f"U{lo:02d}-U{hi:02d}"


def _bbox_union(boxes):
    boxes = [tuple(b) for b in boxes if b is not None]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def free_space_summary(rows) -> dict:
    """Totals plus every run of contiguous free rows (top to bottom)."""
    pos = _positions(rows)
    total = len(rows)
    free = sum(1 for r in rows if _is_free(r))
    runs = [{"units": [rows[i]["unit"] for i in run],
             "range_label": _range_label([pos[i] for i in run]),
             "size": len(run)} for run in _free_runs(rows)]
    return {
        "total_units": total,
        "occupied_units": total - free,
        "free_units": free,
        "free_pct": round(100.0 * free / total, 1) if total else 0.0,
        "largest_contiguous_free": max((r["size"] for r in runs), default=0),
        "free_runs": runs,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Placement ranking
# ═══════════════════════════════════════════════════════════════════════════
HEAVY = {"ups", "storage", "gpu-server", "chassis"}
LIGHT = {"switch", "patch-panel", "cable-management", "router", "firewall", "wireless-controller"}


def placement_preference(category: str | None, units: int) -> str:
    """'bottom' | 'top' | 'middle' | 'any' for a device, by datacenter practice."""
    if category in HEAVY:
        return "bottom"
    if category in ("server", "nas"):
        return "bottom" if units >= 2 else "middle"
    if category in LIGHT:
        return "top"
    if category in (None, "unknown"):
        return "bottom" if units >= 2 else "any"
    return "any"  # pdu, shelf, blanking panel, kvm


# Weights of the three factors. Position matters most (weight low, keep cabling
# high); then "best fit" (use the smallest gap that works, keep big gaps whole);
# then avoiding a stranded 1U sliver that nothing will ever fill.
W_POSITION, W_FIT, W_NO_ORPHAN = 0.6, 0.2, 0.2
UNCERTAIN_P = 0.35  # a free row with p_occupied above this is a close call
UNCERTAIN_PENALTY = 0.05


def _position_score(frac: float, prefer: str) -> float:
    """frac: 0 = bottom of the rack, 1 = top."""
    return {"bottom": 1 - frac, "top": frac, "middle": 1 - 2 * abs(frac - 0.5)}.get(prefer, 0.5)


def _position_phrase(frac: float, prefer: str, ps: float) -> str:
    where = "low" if frac < 1 / 3 else "high" if frac > 2 / 3 else "mid-rack"
    where = f"{where} in the rack" if where != "mid-rack" else where
    if prefer == "bottom":
        return (f"{where.capitalize()}, where heavy gear belongs for stability" if ps >= 0.67
                else f"{where.capitalize()} (heavy gear is better lower down)")
    if prefer == "top":
        return (f"{where.capitalize()}, where light network gear usually sits near the cabling" if ps >= 0.67
                else f"{where.capitalize()} (network gear usually goes higher)")
    if prefer == "middle":
        return (f"{where.capitalize()}, leaving the bottom for heavy gear and the top for network gear"
                if ps >= 0.5 else f"{where.capitalize()}")
    return where.capitalize()


def _score(need, gap, above, below, frac, prefer, n_uncertain):
    """Score one placement; higher is better. Returns (score, parts)."""
    ps = _position_score(frac, prefer)
    fit = need / gap                                   # 1.0 = fills the gap exactly
    orphans = (above == 1) + (below == 1)              # stranded 1U slivers left behind
    no_orphan = 1.0 - 0.5 * orphans
    penalty = min(3, n_uncertain) * UNCERTAIN_PENALTY
    score = W_POSITION * ps + W_FIT * fit + W_NO_ORPHAN * no_orphan - penalty
    return score, {"position": ps, "fit": fit, "orphans": orphans}


def _fit_phrase(need, gap, above, below):
    if gap == need:
        return f"fills a {gap}U gap exactly"
    left = []
    if above:
        left.append(f"{above}U above")
    if below:
        left.append(f"{below}U below")
    s = f"uses {need}U of a {gap}U gap"
    if 1 in (above, below):
        orphan_side = " and ".join(x for x in ("above" if above == 1 else "", "below" if below == 1 else "") if x)
        return f"{s}, but strands a 1U sliver {orphan_side}"
    return f"{s}, leaving {' and '.join(left)} free in one piece" if len(left) == 1 else f"{s}, leaving {' and '.join(left)}"


def suggest_placements(rows, units_needed: int, prefer: str = "bottom") -> list[dict]:
    """Up to 5 ranked placements for a device `units_needed` U tall.

    prefer: 'bottom' (heavy gear), 'top' (light network gear), 'middle' or 'any'.
    Each suggestion: {"units": [top..bottom labels], "range_label": "U04-U05",
    "bbox": union of the rows' boxes, "score": float, "reason": str}.
    """
    if isinstance(units_needed, bool) or not isinstance(units_needed, int):
        raise TypeError("units_needed must be an int")
    if units_needed < 0:
        raise ValueError("units_needed must be >= 0")
    if units_needed == 0 or not rows:
        return []
    pos = _positions(rows)
    lo, hi = min(pos), max(pos)
    out = []
    for run in _free_runs(rows):
        gap = len(run)
        for k in range(gap - units_needed + 1):
            idx = run[k:k + units_needed]
            above, below = k, gap - units_needed - k
            p = [pos[i] for i in idx]
            frac = ((sum(p) / len(p)) - lo) / (hi - lo) if hi > lo else 0.5
            unsure = [rows[i]["unit"] for i in idx if float(rows[i].get("p_occupied", 0.0)) >= UNCERTAIN_P]
            score, parts = _score(units_needed, gap, above, below, frac, prefer, len(unsure))

            pos_txt = _position_phrase(frac, prefer, parts["position"])
            fit_txt = _fit_phrase(units_needed, gap, above, below)
            pos_weight = W_POSITION * parts["position"] if prefer != "any" else -1
            fit_weight = W_FIT * parts["fit"] + W_NO_ORPHAN * (1 - 0.5 * parts["orphans"])
            if pos_weight >= fit_weight:
                reason = f"{pos_txt}; {fit_txt}"
            else:
                reason = f"{fit_txt[0].upper()}{fit_txt[1:]}; {pos_txt[0].lower()}{pos_txt[1:]}"
            if unsure:
                reason += f". Check {', '.join(u.upper() for u in unsure)} by eye: the detector was unsure it is empty"
            out.append({
                "units": [rows[i]["unit"] for i in idx],
                "range_label": _range_label(p),
                "bbox": _bbox_union(rows[i].get("box") for i in idx),
                "score": round(score, 3),
                "reason": reason + ".",
                "_tiebreak": min(p) if prefer != "top" else -max(p),
            })
    out.sort(key=lambda s: (-s["score"], s["_tiebreak"]))
    for s in out:
        del s["_tiebreak"]
    return out[:5]


# ═══════════════════════════════════════════════════════════════════════════
# One-call API for the UI
# ═══════════════════════════════════════════════════════════════════════════
def advise(rows, make: str, model: str, units_override: int | None = None) -> dict:
    """Look up the device, rank placements, summarise free space, and write one message.

    Returns {"device", "units_needed", "prefer", "suggestions", "free_space",
             "needs_units_input", "message"}.
    """
    if units_override is not None and (isinstance(units_override, bool) or not isinstance(units_override, int)
                                       or units_override < 0):
        raise ValueError("units_override must be a non-negative int")
    device = lookup_device(make, model)
    summary = free_space_summary(rows)
    name = f"{device['make']} {device['model']}" if device and device["source"] != "parsed-from-name" \
        else f"{make or ''} {model or ''}".strip() or "This device"
    units = units_override if units_override is not None else (device["units"] if device else None)
    result = {"device": device, "units_needed": units, "prefer": None, "suggestions": [],
              "free_space": summary, "needs_units_input": False, "message": ""}

    if units is None:
        result["needs_units_input"] = True
        result["message"] = (f"'{name}' is not in the catalog. Ask the user how many rack units (U) "
                             f"tall it is, e.g. 1U or 2U.")
        return result
    if units == 0:
        result["message"] = (f"{name} mounts vertically in the rack's side channel (0U), "
                             f"so it doesn't need a free rack unit.")
        return result

    category = device["category"] if device else None
    prefer = placement_preference(category, units)
    result["prefer"] = prefer
    sugg = suggest_placements(rows, units, prefer)
    result["suggestions"] = sugg
    hint = ""
    if device and device["source"].startswith("catalog:fuzzy"):
        hint = " (closest catalog match; confirm the model)"
    elif device and device["source"] == "catalog:family":
        hint = f" (same as the {device['model']} family; confirm the model)"
    if sugg:
        best = sugg[0]
        result["message"] = (f"{name} is {units}U{hint}. Best spot: {best['range_label']}. "
                             f"{best['reason']} {len(sugg)} option(s) found.")
    elif summary["free_units"] < units:
        result["message"] = (f"{name} needs {units}U but only {summary['free_units']}U is free in the whole "
                             f"rack, so there isn't enough space.")
    else:
        runs = summary["free_runs"]
        largest = max(runs, key=lambda r: r["size"]) if runs else None
        where = f" ({largest['range_label']})" if largest else ""
        result["message"] = (f"{name} needs {units}U. There is {summary['free_units']}U free in total, but it "
                             f"is fragmented: the largest contiguous run is {summary['largest_contiguous_free']}U"
                             f"{where}.")
    return result
