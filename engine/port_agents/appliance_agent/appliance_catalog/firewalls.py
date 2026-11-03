"""Firewall catalogue: the security gateways of router_catalog (taken over as they are) + the firewall
models and vendors that were not there.
"""
import sys
from pathlib import Path

from common import C, CHAS, DESK, EOS, EOSUP, KNOWN, RACK, ROUTER, WEB, expand

ROUTER_DIR = Path(__file__).resolve().parent.parent / "router_catalog"

NGFW, UTM, DCFW, LEG, SDW = ("Next-gen firewall", "UTM / SMB firewall", "Data-center firewall",
                             "Legacy firewall", "Security & SD-WAN appliance")
IFW, IPS = "Industrial firewall", "Intrusion prevention (IPS)"

ADD = [
    # Cisco
    ("Cisco", "Secure Firewall 1200", NGFW, "Desktop / 1RU", C, KNOWN,
     ["CSF1210CE", "CSF1210CP", "CSF1220CX", "CSF1230", "CSF1240", "CSF1250"]),
    ("Cisco", "Secure Firewall 4200", DCFW, RACK, C, KNOWN, ["FPR4215", "FPR4225", "FPR4245"]),
    ("Cisco", "Secure Firewall 6100", DCFW, RACK, C, KNOWN, ["FPR6160", "FPR6170"]),
    ("Cisco", "Firepower 1000", NGFW, DESK, C, KNOWN, ["FPR1010E"]),
    ("Cisco", "Firepower 4100 (older)", DCFW, RACK, EOS, KNOWN, ["FPR4110", "FPR4120", "FPR4140", "FPR4150"]),
    ("Cisco", "Firepower 9300", DCFW, CHAS, C, KNOWN, [("FPR9300", "chassis; blades SM-40/48/56")]),
    ("Cisco", "ASA 5500 / 5500-X (older)", LEG, RACK, EOSUP, KNOWN,
     ["ASA5505", "ASA5510", "ASA5520", "ASA5540", "ASA5550", "ASA5580", "ASA5512-X", "ASA5515-X",
      "ASA5585-X", "ASA5506W-X", "ASA5506H-X"]),
    ("Cisco", "PIX (end of life)", LEG, RACK, EOSUP, KNOWN, ["PIX 501", "PIX 506E", "PIX 515E", "PIX 525", "PIX 535"]),
    ("Cisco", "FirePOWER 7000 / 8000 (Sourcefire, end of life)", LEG, RACK, EOSUP, KNOWN,
     ["FP7010", "FP7020", "FP7030", "FP7050", "FP7110", "FP7115", "FP7120", "FP7125", "FP8120", "FP8130",
      "FP8140", "FP8250", "FP8350", "FP8360", "FP8370", "FP8390"]),
    # Check Point
    ("Check Point", "Quantum Lightspeed", DCFW, RACK, C, KNOWN, ["QLS250", "QLS450", "QLS650", "QLS800"]),
    ("Check Point", "Maestro orchestrators", DCFW, RACK, C, KNOWN, ["MHO-140", "MHO-170", "MHO-175"]),
    ("Check Point", "Appliances (older)", LEG, RACK, EOS, KNOWN,
     ["2200", "4200", "4400", "4600", "4800", "12200", "12400", "12600", "13500", "13800", "21400", "21600",
      "21700", "21800", "15000", "16000", "23000", "44000", "64000"]),
    ("Check Point", "SMB appliances (older)", UTM, DESK, EOSUP, KNOWN,
     ["600", "620", "640", "680", "700", "730", "750", "770", "790", "910", "1100", "1120", "1140", "1180",
      "1200R", "1430", "1450", "1470", "1490"]),
    # Palo Alto (older ones not in the router catalogue; duplicates are skipped)
    ("Palo Alto Networks", "PA (older)", LEG, RACK, EOSUP, KNOWN,
     ["PA-200", "PA-220", "PA-220R", "PA-500", "PA-820", "PA-850", "PA-2020", "PA-2050", "PA-3020", "PA-3050",
      "PA-3060", "PA-3220", "PA-3250", "PA-3260", "PA-4020", "PA-4050", "PA-4060", "PA-5020", "PA-5050",
      "PA-5060", "PA-5220", "PA-5250", "PA-5260", "PA-5280", "PA-5450", "PA-7050", "PA-7080"]),
    # Huawei
    ("Huawei", "HiSecEngine USG6000E", NGFW, RACK, C, KNOWN,
     ["USG6305E", "USG6310E", "USG6315E", "USG6335E", "USG6355E", "USG6365E", "USG6395E", "USG6530E",
      "USG6550E", "USG6560E", "USG6580E", "USG6620E", "USG6625E", "USG6630E", "USG6635E", "USG6650E",
      "USG6680E", "USG6712E", "USG6716E"]),
    ("Huawei", "HiSecEngine USG12000", DCFW, CHAS, C, KNOWN, ["USG12004", "USG12008"]),
    ("Huawei", "USG6000 / USG9500 / Eudemon (older)", LEG, RACK, EOS, KNOWN,
     ["USG6310", "USG6320", "USG6330", "USG6350", "USG6370", "USG6390", "USG6510", "USG6550", "USG6570",
      "USG6620", "USG6630", "USG6650", "USG6660", "USG6670", "USG6680", "USG9520", "USG9560", "USG9580",
      "Eudemon200E-X", "Eudemon1000E-X", "Eudemon8000E-X"]),
    # H3C
    ("H3C", "SecPath F1000", NGFW, RACK, C, KNOWN,
     ["F1000-AK180", "F1000-AK1110", "F1000-AK1120", "F1000-AK1130", "F1000-AK1140", "F1000-AK1150",
      "F1000-AK1160", "F1000-AK1170", "F1000-AK1180", "F1000-E", "F1000-S-G2", "F1000-C-G2", "F1000-E-G2"]),
    ("H3C", "SecPath F5000", DCFW, RACK, C, KNOWN, ["F5000-AI-20", "F5000-AI-40", "F5000-AI-60", "F5000-C", "F5000-S"]),
    # Sangfor
    ("Sangfor", "NGAF / NSF", NGFW, RACK, C, KNOWN,
     ["NSF-1000", "NSF-2000", "NSF-3000", "NSF-5000", "NSF-8000", "M5100", "M5200", "M5300", "M5400", "M5500",
      "M5600", "M5800", "M6000", "M8000", "M9000"]),
    # Meraki MX (also in the router catalogue as SD-WAN; listed here because they are the firewall)
    ("Cisco", "Meraki MX", SDW, "Desktop / rack", C, ROUTER,
     ["MX67", "MX67W", "MX67C", "MX68", "MX68W", "MX68CW", "MX75", "MX85", "MX95", "MX105", "MX250", "MX450"]),
    ("Cisco", "Meraki MX (older)", SDW, "Desktop / rack", EOS, ROUTER,
     ["MX60", "MX60W", "MX64", "MX64W", "MX65", "MX65W", "MX70", "MX80", "MX84", "MX90", "MX100", "MX400", "MX600",
      "Z1", "Z3", "Z3C", "Z4", "Z4C"]),
    # Sophos SG / UTM and Cyberoam
    ("Sophos", "SG / UTM (older)", UTM, "Desktop / rack", EOSUP, KNOWN,
     ["SG 105", "SG 115", "SG 125", "SG 135", "SG 210", "SG 230", "SG 310", "SG 330", "SG 430", "SG 450",
      "SG 550", "SG 650"]),
    ("Sophos (Cyberoam)", "Cyberoam CR (end of life)", UTM, "Desktop / rack", EOSUP, KNOWN,
     ["CR10iNG", "CR15iNG", "CR25iNG", "CR35iNG", "CR50iNG", "CR100iNG", "CR200iNG", "CR300iNG", "CR500iNG-XP",
      "CR750iNG-XP", "CR1000iNG-XP", "CR1500iNG-XP", "CR2500iNG-XP"]),
    # open-source / white-box firewall appliances
    ("Netgate", "pfSense Plus appliances", UTM, "Desktop / rack", C, KNOWN,
     ["1100", "2100", "4100", "4200", "6100", "8200", "8300", "XG-7100", "1537", "1541"]),
    ("Protectli", "Vault", UTM, DESK, C, KNOWN,
     ["FW2B", "FW4B", "FW4C", "FW6D", "VP2410", "VP2420", "VP2430", "VP4630", "VP4650", "VP4670", "VP6630",
      "VP6650", "VP6670"]),
    ("Untangle / Arista", "NG Firewall appliances (end of life)", UTM, DESK, EOS, KNOWN, ["z4", "z4w", "z6", "z12", "z20", "u25"]),
    ("Kerio", "Kerio Control boxes", UTM, DESK, EOS, KNOWN, ["NG100", "NG100W", "NG300", "NG300W", "NG500"]),
    ("Clavister", "NetWall", NGFW, "Desktop / rack", C, KNOWN, ["E10", "E20", "E80", "W20", "W30", "W40", "W50", "W60"]),
    ("Ruijie", "RG-WALL", NGFW, RACK, C, KNOWN, ["RG-WALL 1600 (models: verify)"]),
    ("NSFOCUS", "NF next-gen firewall", NGFW, RACK, C, KNOWN, ["NSFOCUS NF (models: verify)"]),
    ("Juniper", "SRX (older)", LEG, RACK, EOSUP, KNOWN, ["SRX3400", "SRX3600", "SRX1400"]),
    # added 2026-10-15 (gap check)
    ("Fortinet", "FortiGate 6000F chassis", DCFW, "3RU", C, WEB,
     ["FG-6001F", "FG-6300F", "FG-6301F", "FG-6500F", "FG-6501F"]),
    ("Fortinet", "FortiGate 7000E / 7000F chassis", DCFW, CHAS, C, WEB, ["FG-7040E", "FG-7060E", "FG-7081F", "FG-7121F"]),
    ("Cisco", "ISA 3000 (industrial)", IFW, "Industrial / DIN", C, WEB, ["ISA-3000-4C", "ISA-3000-2C2F"]),
    ("Trend Micro", "TippingPoint TPS (IPS)", IPS, RACK, C, WEB,
     ["440T", "2200T", "1100TX", "5500TX", "8200TX", "8400TX", "5600TXE", "8600TXE", "9200TXE"]),
    ("Trellix (McAfee)", "Network Security Platform (IPS)", IPS, RACK, EOS, KNOWN,
     ["NS3100", "NS3200", "NS5100", "NS5200", "NS7100", "NS7200", "NS7300", "NS7500", "NS9100", "NS9200", "NS9300", "NS9500"]),
    ("Securepoint", "RC series UTM", UTM, "Desktop / rack", C, WEB,
     ["RC100 G5", "RC200 G5", "RC300S G5", "RC350R G5", "RC400R G6", "RC1000R G5"]),
    ("D-Link", "NetDefend DFL", UTM, "Desktop / rack", EOS, WEB,
     ["DFL-160", "DFL-260E", "DFL-860E", "DFL-870", "DFL-1660", "DFL-1860", "DFL-2560", "DFL-2560G"]),
    ("SonicWall", "SOHO", UTM, DESK, EOS, KNOWN, ["SOHO 250", "SOHO 250W", "SOHO W", "SOHO TZW"]),
    ("Barracuda", "CloudGen Firewall F-Series", NGFW, "Desktop / rack", C, KNOWN, ["F93", "F193", "F18B", "F80B", "F180B", "F280B", "F380B"]),
    ("Topsec", "NGFW", NGFW, RACK, C, KNOWN, ["Topsec NGFW (models: verify)"]),
    ("DPtech", "FW1000 / FW2000", NGFW, RACK, C, KNOWN, ["DPtech FW1000 (models: verify)"]),
    ("Qi-AnXin", "NSG (Secure gateway)", NGFW, RACK, C, KNOWN, ["Qi-AnXin NSG (models: verify)"]),
    ("Venustech", "VenusGuard / USG", NGFW, RACK, C, KNOWN, ["Venustech USG-FW (models: verify)"]),
    ("genua", "genugate / genuscreen", NGFW, RACK, C, KNOWN, ["genugate (models: verify)", "genuscreen (models: verify)"]),
    ("Siemens", "SCALANCE S (industrial security)", IFW, "Industrial / DIN", C, KNOWN,
     ["SCALANCE S615", "SCALANCE SC622-2C", "SCALANCE SC632-2C", "SCALANCE SC636-2C", "SCALANCE SC642-2C",
      "SCALANCE SC646-2C"]),
    ("Tosibox", "Lock (industrial)", IFW, "Industrial / DIN", C, KNOWN, ["Lock 210", "Lock 350", "Lock 500", "Lock 650"]),
]

# router-catalogue rows that are firewalls too: industrial firewalls and UniFi security gateways
ROUTER_ALSO = [(r"^FGR-|MGUARD|^EDR-|^EAGLE", IFW), (r"^(UDM|UCG|UXG|EFG|USG(-|$)|USG Pro)", UTM)]


def router_firewalls():
    """Security-gateway rows of the router catalogue, unchanged."""
    sys.path.insert(0, str(ROUTER_DIR))
    import full_models as fm   # noqa: E402
    import re
    out = []
    for v, s, m, cat, form, st, src, var in fm.rows():
        if cat == fm.SEC:
            out.append((v, s, m, NGFW if "Desktop" not in form else UTM, form, st, src, var))
            continue
        if re.search(r"firewall", s, re.I):          # NETGEAR ProSAFE VPN firewalls, Firewalla ...
            out.append((v, s, m, UTM, form, st, src, var))
            continue
        for pat, newcat in ROUTER_ALSO:
            if re.search(pat, m, re.I):
                out.append((v, s, m, newcat, form, st, src, var))
                break
    return out


def rows():
    base = router_firewalls()
    seen = {(r[0], r[2].upper()) for r in base}
    extra = [r for r in expand(ADD) if (r[0], r[2].upper()) not in seen]
    return base + extra
