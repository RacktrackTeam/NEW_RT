"""Full router catalog: every router / routing-gateway model of the 21 vendors.

Rows: vendor, series, model, category, form factor, status, source, variants.
Source tells how the row is known:
  SCRAPED  read from the vendor's own site (cisco.com support pages, juniper.net, mikrotik.com,
           Fortinet product matrix) during this build
  WEB      confirmed by a web search (vendor docs or resellers) during this build
  KNOWN    product knowledge - verify before relying on it
"""
import re

from cisco_base import base_models

SCRAPED, WEB, KNOWN = "Vendor site (scraped)", "Web search (confirmed)", "Product knowledge - verify"
C, EOS, EOSUP = "Current", "End-of-sale", "End of support"
BR, SP, SEC, SMB, SDW, IND, DC = ("Enterprise / branch router", "Service-provider / core router",
                                  "Security gateway (routes)", "SMB / VPN router", "SD-WAN appliance",
                                  "Industrial router", "Data-center / core router")
RACK, DESK, CHAS, INDF = "Rack", "Desktop", "Modular chassis (rack)", "Industrial / DIN"

# ---------------------------------------------------------------- Cisco (scraped, then gaps)
CISCO_SERIES = {  # series -> (category, form factor, status override)
    "900 ISR": (BR, DESK), "1000 ISR": (BR, DESK), "1900 ISR": (BR, "Desktop / rack kit"),
    "2900 ISR": (BR, RACK), "3900 ISR": (BR, RACK), "4000 ISR": (BR, RACK),
    "8100 Secure Router": (BR, DESK), "8200 Secure Router": (BR, "Desktop / 1RU"),
    "ASR 900": (SP, RACK), "ASR 901": (SP, RACK), "ASR 901 10G": (SP, RACK), "ASR 920": (SP, RACK),
    "ASR 1000": (SP, RACK), "ASR 9000": (SP, "Rack / modular chassis"), "800 Series": (SMB, DESK),
    "8000 Series": (DC, "Rack / modular chassis"), "Catalyst 8300 Edge": (BR, RACK),
    "Catalyst 8500 Edge": (SP, RACK), "NCS 520": (SP, RACK), "NCS 540": (SP, RACK), "NCS 5000": (SP, RACK),
    "NCS 5500": (SP, "Rack / modular chassis"), "NCS 5700": (SP, RACK), "NCS 6000": (SP, CHAS),
    "Carrier Routing System": (SP, CHAS), "800 Industrial ISR": (IND, INDF), "1000 Connected Grid": (IND, INDF),
    "2000 Connected Grid": (IND, INDF), "IR1100 Catalyst Rugged": (IND, INDF), "IR1800 Catalyst Rugged": (IND, INDF),
}
STATUS_MAP = {"Available": C, "End of Sale": EOS, "End of Support": EOSUP, "": "Check"}
NOT_ROUTER = {"8010-FC", "NCS-5500-FC", "NCS-5500-SW", "8201-MSW", "C8500-12XBBOPT"}


def _cisco_name(series, b):
    if series in ("ASR 1000", "4000 ISR"):
        b = re.sub(r"(\d)-?X$", r"\1-X", b)                # ASR1001X -> ASR1001-X, ISR4451X -> ISR4451-X
        b = {"ISR4451": "ISR4451-X", "ISR4221-X": "ISR4221X"}.get(b, b)   # Cisco's own spellings
    b = re.sub(r"LTE[A-Z0-9]*$", "", b)                     # C819GWLTEMNAAK -> C819GW
    if series.startswith("NCS") or series == "8000 Series":
        b = re.sub(r"-?SYS[A-Z]*$|SAV$", "", b)
        b = re.sub(r"-(GOOGLE|MBH)$", "", b)
    return b


def cisco_rows():
    rows, seen = [], set()
    for series, (status, groups) in base_models().items():
        if series not in CISCO_SERIES:
            continue
        cat, form = CISCO_SERIES[series]
        merged = {}
        for b, ids in groups.items():
            name = _cisco_name(series, b)
            if name in NOT_ROUTER or len(name) < 4:
                continue
            merged.setdefault(name, []).extend(ids)
        for name in sorted(merged):
            if name in seen:
                continue
            seen.add(name)
            st = STATUS_MAP.get(status, status)
            if series == "4000 ISR":
                st = EOS
            rows.append(("Cisco", series, name, cat, form, st, SCRAPED, ", ".join(sorted(set(merged[name])))))
    return rows, seen


CISCO_GAPS = [   # series whose support page had no ID list
    ("8300 Secure Router", BR, RACK, C, WEB, ["C8355-G2", "C8375-E-G2"]),
    ("8400 Secure Router", BR, RACK, C, WEB, ["C8455-G2", "C8475-G2"]),
    ("8500 Secure Router", SP, RACK, C, WEB, ["C8550-G2", "C8570-G2"]),
    ("8600 Secure Router", SP, RACK, C, WEB, ["C8650-G2"]),
    ("Catalyst 8200 Edge", BR, RACK, C, KNOWN, ["C8200-1N-4T", "C8200L-1N-4T"]),
    ("Catalyst 8200 Edge uCPE", BR, RACK, EOS, KNOWN, ["C8200-UCPE-1N8"]),
    ("Catalyst 8300 Edge uCPE", BR, RACK, EOS, WEB, ["C8300-UCPE-1N20"]),
    ("Catalyst 8500L Edge", SP, RACK, C, KNOWN, ["C8500L-8S4X"]),
    ("NCS 560", SP, CHAS, C, KNOWN, ["N560-4", "N560-7"]),
    ("IR8100 Catalyst Heavy Duty", IND, INDF, C, KNOWN, ["IR8140H", "IR8140H-P"]),
    ("IR8300 Catalyst Rugged", IND, INDF, C, KNOWN, ["IR8340"]),
    ("RV Series (Small Business)", SMB, DESK, EOS, KNOWN,
     ["RV016", "RV042", "RV042G", "RV082", "RV110W", "RV130", "RV130W", "RV132W", "RV134W", "RV160", "RV160W",
      "RV260", "RV260P", "RV260W", "RV320", "RV325", "RV340", "RV340W", "RV345", "RV345P"]),
    ("cBR Converged Broadband", SP, CHAS, EOS, KNOWN, ["cBR-8"]),
]

# ---------------------------------------------------------------- other vendors
# (vendor, series, category, form factor, status, source, [models])
OTHERS = [
    ("Cisco Meraki", "MX (current)", SDW, DESK, C, WEB, ["MX67", "MX67W", "MX67C", "MX68", "MX68W", "MX68CW", "MX75"]),
    ("Cisco Meraki", "MX (current)", SDW, RACK, C, WEB, ["MX85", "MX95", "MX105", "MX250", "MX450"]),
    ("Cisco Meraki", "MX on Cisco 8000 hardware", SDW, DESK, C, SCRAPED, ["C8111-C-G2-MX", "C8121-W-G2-MX", "C8121-CW-G2-MX"]),
    ("Cisco Meraki", "MX on Cisco 8000 hardware", SDW, RACK, C, WEB, ["C8355-G2-MX"]),
    ("Cisco Meraki", "MX (older)", SDW, DESK, EOS, WEB, ["MX64", "MX64W", "MX65", "MX65W"]),
    ("Cisco Meraki", "MX (older)", SDW, RACK, EOS, WEB, ["MX80", "MX84", "MX90", "MX100", "MX400", "MX600"]),
    ("Cisco Meraki", "Z teleworker", SDW, DESK, C, WEB, ["Z3", "Z3C", "Z4", "Z4C"]),

    ("Juniper", "MX", SP, "Rack / modular chassis", C, SCRAPED,
     ["MX204", "MX301", "MX304", "MX240", "MX480", "MX960", "MX10003", "MX10004", "MX10008", "MX2008", "MX2010", "MX2020"]),
    ("Juniper", "MX (older)", SP, RACK, EOS, KNOWN, ["MX5", "MX10", "MX40", "MX80", "MX104", "MX150"]),
    ("Juniper", "PTX", DC, "Rack / modular chassis", C, WEB,
     ["PTX10001-36MR", "PTX10002-36QDD", "PTX10003", "PTX10004", "PTX10008", "PTX10016"]),
    ("Juniper", "PTX (older)", DC, CHAS, EOS, KNOWN, ["PTX1000", "PTX3000", "PTX5000"]),
    ("Juniper", "ACX", SP, RACK, C, SCRAPED,
     ["ACX710", "ACX5448", "ACX7020", "ACX7024", "ACX7024X", "ACX7100-32C", "ACX7100-48L", "ACX7332", "ACX7348", "ACX7509"]),
    ("Juniper", "ACX (older)", SP, RACK, EOS, WEB,
     ["ACX500", "ACX1000", "ACX1100", "ACX2100", "ACX2200", "ACX4000", "ACX5048", "ACX5096"]),
    ("Juniper", "SRX", SEC, RACK, C, WEB,
     ["SRX300", "SRX320", "SRX340", "SRX345", "SRX380", "SRX1600", "SRX2300", "SRX4100", "SRX4200", "SRX4300",
      "SRX4600", "SRX4700", "SRX5400", "SRX5600", "SRX5800"]),
    ("Juniper", "SRX (older)", SEC, RACK, EOS, WEB,
     ["SRX100", "SRX110", "SRX210", "SRX220", "SRX240", "SRX550", "SRX650", "SRX1500"]),
    ("Juniper", "Session Smart Router", SDW, "Desktop / 1RU", C, KNOWN,
     ["SSR120", "SSR130", "SSR1200", "SSR1300", "SSR1400", "SSR1500"]),
    ("Juniper", "Route reflector", DC, RACK, C, SCRAPED, ["JRR200"]),

    ("MikroTik", "Cloud Core Router", SP, RACK, C, SCRAPED,
     ["CCR2004-16G-2S+", "CCR2004-16G-2S+PC", "CCR2004-1G-12S+2XS", "CCR2004-1G-2XS-PCIe", "CCR2116-12G-4S+",
      "CCR2216-1G-12XS-2XQ"]),
    ("MikroTik", "RouterBOARD / hEX / L009", SMB, "Desktop / 1RU", C, SCRAPED,
     ["RB1100AHx4", "RB1100AHx4 Dude Edition", "RB4011iGS+RM", "RB5009UG+S+IN", "RB5009UPr+S+IN", "RB5009UPr+S+OUT",
      "L009UiGS-RM", "hEX", "hEX S", "hEX S (2025)", "hEX lite", "hEX PoE", "hEX PoE lite", "hEX refresh",
      "PowerBox Pro", "ROSE Data server (RDS)"]),
    ("MikroTik", "Cloud Core Router (older)", SP, RACK, EOS, KNOWN,
     ["CCR1009-7G-1C-1S+", "CCR1009-7G-1C-1S+PC", "CCR1016-12G", "CCR1016-12S-1S+", "CCR1036-12G-4S",
      "CCR1036-12G-4S-EM", "CCR1036-8G-2S+", "CCR1036-8G-2S+EM", "CCR1072-1G-8S+"]),
    ("MikroTik", "RouterBOARD (older)", SMB, "Desktop / 1RU", EOS, KNOWN,
     ["RB2011UiAS-RM", "RB3011UiAS-RM", "RB1100AHx2"]),

    ("Huawei", "NetEngine AR8000", BR, RACK, C, WEB, ["AR8140-12G10XG", "AR8140-T-12G10XG", "AR8700-8"]),
    ("Huawei", "NetEngine AR6000", BR, RACK, C, WEB,
     ["AR6120", "AR6121", "AR6140-9G-2AC", "AR6140-16G4XG", "AR6280", "AR6300"]),
    ("Huawei", "NetEngine AR5700", BR, DESK, C, WEB, ["AR5710-H8T2TS1", "AR5710-H8T2TS1-T"]),
    ("Huawei", "NetEngine AR600 / industrial", IND, INDF, C, WEB, ["AR650", "AR630", "AR6510-L"]),
    ("Huawei", "AR (older)", BR, RACK, EOS, KNOWN, ["AR1220E", "AR2220E", "AR2240C", "AR3260", "AR3670"]),
    ("Huawei", "NetEngine 8000", SP, "Rack / modular chassis", C, KNOWN,
     ["NetEngine 8000 M1A", "NetEngine 8000 M1C", "NetEngine 8000 M4", "NetEngine 8000 M6", "NetEngine 8000 M8",
      "NetEngine 8000 M14", "NetEngine 8000 X4", "NetEngine 8000 X8", "NetEngine 8000 X16", "NetEngine 8000 F1A"]),
    ("Huawei", "NE40E", SP, "Rack / modular chassis", C, KNOWN,
     ["NE40E-M2K", "NE40E-M2H", "NE40E-X3A", "NE40E-X8A", "NE40E-X16A"]),
    ("Huawei", "ATN / CX600 / NE9000", SP, "Rack / modular chassis", C, SCRAPED,
     ["ATN 905", "ATN 910", "ATN 950", "CX600", "NE9000"]),

    ("H3C", "MSR small business", SMB, DESK, C, WEB,
     ["MSR610", "MSR810-EI", "MSR830-6HI-GL", "MSR830-10HI-GL", "MSR1004S-5G-GL", "MSR1008"]),
    ("H3C", "MSR", BR, RACK, C, SCRAPED,
     ["MSR2630E-X1", "MSR3610-X1", "MSR3620", "MSR3640", "MSR3660", "MSR5620", "MSR5660", "MSR5680"]),
    ("H3C", "SR6600", SP, "Rack / modular chassis", C, SCRAPED,
     ["SR6602-I", "SR6602-X", "SR6604", "SR6608", "SR6616", "SR6600-M", "SR6616-M"]),
    ("H3C", "CR16000 / CR19000", SP, CHAS, C, SCRAPED, ["CR16000-F", "CR16000-M", "CR19000-20"]),

    ("Nokia", "7750 SR", SP, "Rack / modular chassis", C, WEB,
     ["7750 SR-1", "7750 SR-1-24D", "7750 SR-1-46S", "7750 SR-1-48D", "7750 SR-1-92S", "7750 SR-1e", "7750 SR-2e",
      "7750 SR-3e", "7750 SR-1s", "7750 SR-2s", "7750 SR-7s", "7750 SR-14s", "7750 SR-1se", "7750 SR-2se",
      "7750 SR-7", "7750 SR-12", "7750 SR-12e"]),
    ("Nokia", "7250 IXR", SP, "Rack / modular chassis", C, KNOWN,
     ["7250 IXR-e", "7250 IXR-s", "7250 IXR-R4", "7250 IXR-R6", "7250 IXR-X1", "7250 IXR-X3", "7250 IXR-6",
      "7250 IXR-10"]),
    ("Nokia", "7705 SAR", SP, "Rack / modular chassis", C, KNOWN,
     ["7705 SAR-1", "7705 SAR-8", "7705 SAR-18", "7705 SAR-A", "7705 SAR-Ax", "7705 SAR-H", "7705 SAR-Hc",
      "7705 SAR-Hm", "7705 SAR-M", "7705 SAR-W", "7705 SAR-Wx", "7705 SAR-X", "7705 SAR-10x", "7705 SAR-Mx",
      "7705 SAR-Hx"]),

    ("Ericsson", "Router 6000", SP, RACK, C, WEB, ["Router 6672", "Router 6675", "Router 6274"]),
    ("Ericsson", "Router 6000", SP, RACK, C, KNOWN, ["Router 6273", "Router 6371", "Router 6471", "Router 6676"]),

    ("Fortinet", "FortiGate desktop", SEC, DESK, C, SCRAPED,
     ["FG-30G", "FWF-30G", "FG-40F", "FWF-40F", "FG-40F-3G4G", "FG-50G", "FWF-50G", "FG-51G", "FG-60F", "FWF-60F",
      "FG-61F", "FG-70F", "FG-71F", "FG-70G", "FWF-70G", "FG-71G", "FG-80F", "FWF-80F", "FG-81F", "FG-80F-POE",
      "FG-90G", "FG-91G"]),
    ("Fortinet", "FortiGate 1RU", SEC, "1RU", C, SCRAPED,
     ["FG-100F", "FG-101F", "FG-120G", "FG-121G", "FG-200F", "FG-201F", "FG-200G", "FG-201G", "FG-400F", "FG-401F",
      "FG-400G", "FG-401G", "FG-600F", "FG-601F", "FG-700G", "FG-701G", "FG-900G", "FG-901G", "FG-1000F", "FG-1001F"]),
    ("Fortinet", "FortiGate 2RU+", SEC, "2RU-6RU", C, SCRAPED,
     ["FG-1800F", "FG-1801F", "FG-2600F", "FG-2601F", "FG-3000F", "FG-3001F", "FG-3000G", "FG-3001G", "FG-3200F",
      "FG-3201F", "FG-3500F", "FG-3501F", "FG-3500G", "FG-3501G", "FG-3700F", "FG-3701F", "FG-3800G", "FG-3801G",
      "FG-4200F", "FG-4201F", "FG-4400F", "FG-4401F", "FG-4800F", "FG-4801F", "FG-7081F", "FG-7121F"]),
    ("Fortinet", "FortiGate Rugged", IND, INDF, C, KNOWN, ["FGR-60F", "FGR-60F-3G4G", "FGR-70F", "FGR-70F-3G4G"]),
    ("Fortinet", "FortiGate E-series (older)", SEC, "Desktop / rack", EOS, KNOWN,
     ["FG-30E", "FG-50E", "FG-60E", "FG-61E", "FG-80E", "FG-81E", "FG-100E", "FG-101E", "FG-140E", "FG-200E",
      "FG-201E", "FG-300E", "FG-301E", "FG-400E", "FG-401E", "FG-500E", "FG-501E", "FG-600E", "FG-601E",
      "FG-1100E", "FG-1101E", "FG-2000E", "FG-2200E", "FG-2500E", "FG-3300E", "FG-3400E", "FG-3600E", "FG-3960E",
      "FG-3980E"]),
    ("Fortinet", "FortiGate D-series (older)", SEC, "Desktop / rack", EOSUP, KNOWN,
     ["FG-60D", "FG-90D", "FG-100D", "FG-200D", "FG-300D", "FG-500D", "FG-600D", "FG-800D", "FG-900D", "FG-1000D",
      "FG-1200D", "FG-1500D", "FG-3000D", "FG-3100D", "FG-3200D"]),

    ("Palo Alto Networks", "PA-400 / PA-400R", SEC, DESK, C, SCRAPED,
     ["PA-410", "PA-415", "PA-415-5G", "PA-440", "PA-445", "PA-450", "PA-455", "PA-455-5G", "PA-460", "PA-410R",
      "PA-410R-5G", "PA-450R", "PA-450R-5G", "PA-455R-5G"]),
    ("Palo Alto Networks", "PA-500", SEC, DESK, C, SCRAPED, ["PA-501", "PA-505", "PA-510"]),
    ("Palo Alto Networks", "PA-1400 / PA-1500", SEC, "1RU", C, WEB,
     ["PA-1410", "PA-1420", "PA-1510-POE", "PA-1520-POE", "PA-1530-POE"]),
    ("Palo Alto Networks", "PA-3400", SEC, "1RU", C, WEB, ["PA-3410", "PA-3420", "PA-3430", "PA-3440"]),
    ("Palo Alto Networks", "PA-3500", SEC, "1RU", C, SCRAPED, ["PA-3500 series (models: verify)"]),
    ("Palo Alto Networks", "PA-5400 / PA-5500", SEC, "2RU-3RU", C, WEB,
     ["PA-5410", "PA-5420", "PA-5430", "PA-5440", "PA-5445", "PA-5450", "PA-5540", "PA-5550", "PA-5560", "PA-5570",
      "PA-5580"]),
    ("Palo Alto Networks", "PA-7000 / PA-7500", SEC, CHAS, C, SCRAPED, ["PA-7050", "PA-7080", "PA-7500"]),
    ("Palo Alto Networks", "PA (older)", SEC, "Desktop / rack", EOS, SCRAPED,
     ["PA-220", "PA-220R", "PA-820", "PA-850", "PA-3220", "PA-3250", "PA-3260", "PA-5220", "PA-5250", "PA-5260",
      "PA-5280"]),
    ("Palo Alto Networks", "PA (older)", SEC, "Desktop / rack", EOSUP, KNOWN,
     ["PA-200", "PA-500", "PA-3020", "PA-3050", "PA-3060", "PA-5020", "PA-5050", "PA-5060"]),
    ("Palo Alto Networks", "Prisma SD-WAN ION", SDW, "Desktop / rack", C, SCRAPED,
     ["ION 1000", "ION 1200", "ION 1200-S", "ION 1200 4G", "ION 1200 5G", "ION 1200-S 4G", "ION 1200-S 5G",
      "ION 2000", "ION 3000", "ION 3200", "ION 3200H", "ION 3200H-C5G-WW", "ION 5200", "ION 7000", "ION 9000",
      "ION 9200"]),

    ("Check Point", "Quantum Spark", SEC, "Desktop / 1RU", C, WEB,
     ["Quantum Spark 1530", "Quantum Spark 1550", "Quantum Spark 1570", "Quantum Spark 1590", "Quantum Spark 1570R",
      "Quantum Spark 1575R", "Quantum Spark 1595R", "Quantum Spark 1600", "Quantum Spark 1800", "Quantum Spark 1900",
      "Quantum Spark 2000"]),
    ("Check Point", "Quantum / Quantum Force", SEC, "1RU-3RU", C, KNOWN,
     ["Quantum 3600", "Quantum 3800", "Quantum 6200", "Quantum 6400", "Quantum 6600", "Quantum 6700", "Quantum 6900",
      "Quantum 7000", "Quantum 7300", "Quantum 16200", "Quantum 16600HS", "Quantum 26000", "Quantum 28000"]),
    ("Check Point", "Quantum Force", SEC, "1RU-3RU", C, WEB,
     ["Quantum Force 9100", "Quantum Force 9200", "Quantum Force 9300", "Quantum Force 9400", "Quantum Force 9700",
      "Quantum Force 9800", "Quantum Force 19100", "Quantum Force 19200", "Quantum Force 29100", "Quantum Force 29200"]),
    ("Check Point", "Security Gateways (older)", SEC, "1RU-2RU", EOS, KNOWN,
     ["3100", "3200", "5100", "5200", "5400", "5600", "5800", "5900", "15400", "15600", "23500", "23800", "23900"]),

    ("SonicWall", "TZ", SEC, DESK, C, WEB,
     ["TZ270", "TZ270W", "TZ370", "TZ370W", "TZ470", "TZ470W", "TZ570", "TZ570W", "TZ570P", "TZ670"]),
    ("SonicWall", "NSa / NSsp", SEC, "1RU-2RU", C, WEB,
     ["NSa 2700", "NSa 3700", "NSa 4700", "NSa 5700", "NSa 6700", "NSsp 10700", "NSsp 11700", "NSsp 13700",
      "NSsp 15700"]),
    ("SonicWall", "Older", SEC, "Desktop / rack", EOS, WEB,
     ["TZ300", "TZ350", "TZ400", "TZ500", "TZ600", "NSa 2600", "NSa 3600", "NSa 4600", "NSa 5600", "NSa 6600",
      "NSa 2650", "NSa 3650", "NSa 4650", "NSa 5650", "NSa 6650", "NSa 9250", "NSa 9450", "NSa 9650",
      "NSsp 12400", "NSsp 12800", "SuperMassive 9200", "SuperMassive 9400", "SuperMassive 9600",
      "SuperMassive 9800"]),

    ("Sophos", "XGS desktop", SEC, DESK, C, WEB,
     ["XGS 88", "XGS 108", "XGS 128", "XGS 138", "XGS 87", "XGS 87w", "XGS 107", "XGS 107w", "XGS 116", "XGS 116w",
      "XGS 126", "XGS 126w", "XGS 136", "XGS 136w"]),
    ("Sophos", "XGS 1U", SEC, "1RU", C, WEB, ["XGS 2100", "XGS 2300", "XGS 3100", "XGS 3300", "XGS 4300", "XGS 4500"]),
    ("Sophos", "XGS 2U", SEC, "2RU", C, WEB, ["XGS 5500", "XGS 6500", "XGS 7500", "XGS 8500"]),
    ("Sophos", "XG (older)", SEC, "Desktop / rack", EOS, KNOWN,
     ["XG 86", "XG 106", "XG 115", "XG 125", "XG 135", "XG 210", "XG 230", "XG 310", "XG 330", "XG 430", "XG 450",
      "XG 550", "XG 650", "XG 750"]),

    ("Zyxel", "USG FLEX H", SEC, "Desktop / 1RU", C, WEB,
     ["USG FLEX 100H", "USG FLEX 100HP", "USG FLEX 200H", "USG FLEX 200HP", "USG FLEX 500H", "USG FLEX 700H"]),
    ("Zyxel", "USG FLEX / ATP", SEC, "Desktop / 1RU", C, WEB,
     ["USG FLEX 50", "USG FLEX 50AX", "USG FLEX 100", "USG FLEX 100AX", "USG FLEX 100W", "USG FLEX 200",
      "USG FLEX 500", "USG FLEX 700", "ATP100", "ATP100W", "ATP200", "ATP500", "ATP700", "ATP800"]),
    ("Zyxel", "ZyWALL / USG (older)", SEC, "Desktop / 1RU", EOS, KNOWN,
     ["ZyWALL VPN50", "ZyWALL VPN100", "ZyWALL VPN300", "ZyWALL VPN1000", "USG20-VPN", "USG20W-VPN", "USG40",
      "USG60", "USG110", "USG210", "USG310", "USG1100", "USG1900", "USG2200"]),

    ("Ubiquiti", "UniFi cloud gateways", SMB, "Desktop / 1RU", C, WEB,
     ["EFG (Enterprise Fortress Gateway)", "UDM-Pro-Max", "UDM-Pro", "UDM-SE", "UCG-Industrial", "UCG-Fiber",
      "UCG-Max", "UCG-Ultra", "UX7 (Express 7)", "UX (Express)", "UDR-5G-Max", "UDR7", "UDR (Dream Router)",
      "UDW (Dream Wall)"]),
    ("Ubiquiti", "UniFi next-gen gateways", SMB, "Desktop / 1RU", C, WEB,
     ["UXG-Enterprise", "UXG-Fiber", "UXG-Lite", "UXG-Max"]),
    ("Ubiquiti", "UniFi (older)", SMB, "Desktop / 1RU", EOS, KNOWN, ["UXG-Pro", "USG", "USG-PRO-4", "UDM"]),
    ("Ubiquiti", "EdgeRouter", SMB, "Desktop / 1RU", EOS, KNOWN,
     ["ER-X", "ER-X-SFP", "ER-4", "ER-6P", "ER-8", "ER-8-XG", "ER-10X", "ER-12", "ER-12P", "ERPro-8", "ERLite-3",
      "ERPoe-5", "EP-R6", "EP-R8"]),

    ("TP-Link", "Omada gateways", SMB, "Desktop / 1RU", C, WEB,
     ["ER605", "ER706W", "ER706W-4G", "ER703WP-4G-Outdoor", "ER707-M2", "ER7206", "ER7212PC", "ER7406",
      "ER7412-M2", "ER8411"]),
    ("TP-Link", "SafeStream (older)", SMB, "Desktop / 1RU", EOS, KNOWN,
     ["TL-ER5120", "TL-ER6020", "TL-ER6120", "TL-ER604W", "TL-R600VPN", "TL-R470T+", "TL-R480T+"]),

    ("Netgate", "pfSense Plus appliances", SMB, "Desktop / 1RU", C, SCRAPED, ["1100", "2100", "4200", "6100", "8200", "8300"]),
    ("Netgate", "Older appliances", SMB, "Desktop / 1RU", EOS, KNOWN,
     ["SG-1100", "SG-2100", "SG-3100", "SG-5100", "XG-7100", "1537", "1541"]),

    ("DrayTek", "Vigor", SMB, "Desktop / 1RU", C, WEB,
     ["Vigor2136", "Vigor2136ax", "Vigor2766ax", "Vigor2865", "Vigor2865ax", "Vigor2927", "Vigor2927ax",
      "Vigor2927Lax-5G", "Vigor2962", "Vigor3912S", "Vigor2767"]),
    ("DrayTek", "Vigor", SMB, "Desktop / 1RU", C, KNOWN, ["Vigor2866", "Vigor2915", "Vigor2926", "Vigor3910"]),
    ("DrayTek", "Vigor (older)", SMB, "Desktop / 1RU", EOS, KNOWN,
     ["Vigor2135ax", "Vigor2762", "Vigor2860", "Vigor2862", "Vigor2960", "Vigor3220", "Vigor3900"]),

    ("Peplink", "Balance", SDW, "Desktop / 1RU", C, WEB,
     ["Balance One", "Balance Two", "Balance 20", "Balance 20X", "Balance 30 LTE", "Balance 30 Pro", "Balance 210",
      "Balance 305", "Balance 310", "Balance 310X", "Balance 310 5G", "Balance 310 Fiber 5G", "Balance 380",
      "Balance 380X", "Balance 580", "Balance 580X", "Balance 710", "Balance 1350", "Balance 2500",
      "Balance 2500 EC", "SDX", "SDX Pro"]),

    ("Adtran", "NetVanta", BR, "Desktop / 1RU", C, WEB,
     ["NetVanta 3120", "NetVanta 3130", "NetVanta 3140", "NetVanta 3148", "NetVanta 4430"]),
    ("Adtran", "NetVanta", BR, "Desktop / 1RU", C, KNOWN,
     ["NetVanta 3430", "NetVanta 3448", "NetVanta 3450", "NetVanta 3458", "NetVanta 5305", "NetVanta 5660",
      "NetVanta 6250", "NetVanta 6310", "NetVanta 6330", "NetVanta 6355"]),

    ("HPE Aruba", "EdgeConnect SD-WAN", SDW, "Desktop / 1RU", C, WEB,
     ["EC-10104", "EC-10106", "EC-10150", "EC-XS", "EC-M-H"]),
    ("HPE Aruba", "EdgeConnect SD-WAN", SDW, "Desktop / 1RU", C, KNOWN,
     ["EC-US", "EC-S", "EC-S-P", "EC-M", "EC-M-P", "EC-L", "EC-L-P", "EC-XL", "EC-XL-P"]),
    ("HPE Aruba", "SD-Branch gateways", SDW, "Desktop / 1RU", C, KNOWN,
     ["9004", "9004-LTE", "9012", "9106", "9114", "9240"]),
]


def rows():
    out, seen = [], set()
    crow, cseen = cisco_rows()
    out += crow
    seen |= {("Cisco", m) for m in cseen}
    for series, cat, form, st, src, models in CISCO_GAPS:
        for m in models:
            if ("Cisco", m) not in seen:
                seen.add(("Cisco", m))
                out.append(("Cisco", series, m, cat, form, st, src, ""))
    from extra_vendors import EXTRA
    from legacy_models import LEGACY
    from more_vendors import MORE
    for vendor, series, cat, form, st, src, models in OTHERS + MORE + EXTRA + LEGACY:
        for m in models:
            if (vendor, m) not in seen:
                seen.add((vendor, m))
                out.append((vendor, series, m, cat, form, st, src, ""))
    from review_changes import apply
    out, rows.change_log = apply(out)      # the 2026-10-14 catalogue review
    import os
    from review2_changes import apply as apply2
    out, log2 = apply2(out) if os.environ.get("CATALOG_REVIEW2", "1") != "0" else (out, [])                # the 2026-10-21 catalogue review (Catalogue_Review_2.xlsx)
    rows.change_log = rows.change_log + log2
    return out


rows.change_log = []


if __name__ == "__main__":
    import collections
    r = rows()
    print(len(r), "models")
    print(collections.Counter(x[0] for x in r))
    print(collections.Counter(x[6] for x in r))
    print(collections.Counter(x[5] for x in r))
