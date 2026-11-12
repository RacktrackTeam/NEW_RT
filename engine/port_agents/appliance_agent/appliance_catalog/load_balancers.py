"""Load balancer / application delivery controller catalogue (hardware only; virtual editions are not
photographed). Licence tiers of one box carry the box in 'same hardware as'."""
from common import BLADE, C, CHAS, EOS, EOSUP, KNOWN, RACK, WEB, expand

ADC, CHA, LEG = "ADC / load balancer", "ADC chassis", "Legacy load balancer"
R1, R2 = "1RU", "2RU"

GROUPS = [
    # F5 - rSeries (current), iSeries, older; one box per family
    ("F5", "BIG-IP rSeries", ADC, R1, C, WEB,
     [("r2600", "r2000"), ("r2800", "r2000"), ("r4600", "r4000"), ("r4800", "r4000"),
      ("r5600", "r5000"), ("r5800", "r5000"), ("r5900", "r5000"),
      ("r10600", "r10000"), ("r10800", "r10000"), ("r10900", "r10000"),
      ("r12600-DS", "r12000-DS"), ("r12800-DS", "r12000-DS"), ("r12900-DS", "r12000-DS")]),
    ("F5", "BIG-IP iSeries", ADC, R1, EOS, KNOWN,
     [("i850", ""), ("i2600", "i2000"), ("i2800", "i2000"), ("i4600", "i4000"), ("i4800", "i4000"),
      ("i5600", "i5000"), ("i5800", "i5000"), ("i5820-DF", "i5000"), ("i7600", "i7000"), ("i7800", "i7000"),
      ("i7820-DF", "i7000"), ("i10600", "i10000"), ("i10800", "i10000"), ("i11600", "i11000"),
      ("i11800", "i11000"), ("i15600", "i15000"), ("i15800", "i15000")]),
    ("F5", "BIG-IP (older)", LEG, "1RU-2RU", EOSUP, KNOWN,
     ["1500", "1600", "3400", "3600", "3900", "6400", "6800", "6900", "8400", "8800", "8900", "8950", "11000",
      "11050", ("2000s", "2000"), ("2200s", "2000"), ("4000s", "4000"), ("4200v", "4000"), ("5000s", "5000"),
      ("5050s", "5000"), ("5200v", "5000"), ("5250v", "5000"), ("7000s", "7000"), ("7050s", "7000"),
      ("7200v", "7000"), ("7250v", "7000"), ("10000s", "10000"), ("10050s", "10000"), ("10200v", "10000"),
      ("10250v", "10000"), ("10350v", "10000"), "12250v"]),
    ("F5", "VIPRION chassis", CHA, CHAS, EOS, KNOWN, ["C2200", "C2400", "C4400", "C4480", "C4800"]),
    ("F5", "VIPRION blades", CHA, BLADE, EOS, KNOWN,
     ["B2100", "B2150", "B2250", "B4100", "B4200", "B4300", "B4340N", "B4450"]),
    ("F5", "VELOS", CHA, CHAS, C, KNOWN, ["CX410", "CX1610", "BX110", "BX520"]),
    # NetScaler (Citrix ADC); MPX and SDX share the hardware of a family
    ("NetScaler (Citrix)", "MPX / SDX current", ADC, R1, C, WEB,
     [("MPX 5900", "5900: 5901 / 5905 / 5910"), ("MPX 8900", "8900: 8905 / 8910 / 8920 / 8930"),
      ("SDX 8900", "8900"), ("MPX 9100", "9100: 9110 / 9120 / 9130 / 9140"), ("SDX 9100", "9100"),
      ("MPX 14000", "14000: 14020 / 14030 / 14040 / 14060 / 14080 / 14100; -40G / -40S / -40C"),
      ("SDX 14000", "14000"), ("MPX 15000", "15000: 15020 / 15030 / 15040 / 15060 / 15080 / 15100 / 15120; -50G"),
      ("SDX 15000", "15000"), ("MPX 16000", "16000: 16040 / 16060 / 16080 / 16100 / 16120"), ("SDX 16000", "16000"),
      ("MPX 26000", "26000: 26100 / 26160 / 26200; -50S / -100G"), ("SDX 26000", "26000"),
      ("MPX 25000", "25000: 25100 / 25160 / 25200; -40G"), ("SDX 25000", "25000")]),
    ("NetScaler (Citrix)", "MPX / SDX (older)", LEG, "1RU-2RU", EOSUP, KNOWN,
     [("MPX 5500", ""), ("MPX 5550", "5550 / 5650"), ("MPX 7500", "7500 / 9500"), ("MPX 8005", "8005 / 8015 / 8200 / 8400 / 8600 / 8800"),
      ("MPX 10500", "10500 / 12500 / 15500"), ("MPX 11500", "11500 / 13500 / 14500 / 16500 / 18500 / 20500"),
      ("SDX 11500", "11500 / 13500 / 14500 / 16500 / 18500 / 20500"), ("MPX 17000", "17000 / 17550 / 19550 / 20550 / 21550"),
      ("MPX 22040", "22040 / 22060 / 22080 / 22100 / 22120"), ("SDX 22040", "22040-22120"), ("MPX 24100", "24100 / 24150")]),
    # A10 Networks
    ("A10 Networks", "Thunder ADC", ADC, R1, C, WEB,
     ["Thunder 1060S", "Thunder 3350", "Thunder 3350-E", "Thunder 3350S", "Thunder 3360S", "Thunder 3745",
      "Thunder 6655S", "Thunder 7460S", "Thunder 7460S-MAX", "Thunder 7655S"]),
    ("A10 Networks", "Thunder ADC", ADC, R1, C, WEB, ["Thunder 4440", "Thunder 4440S", "Thunder 5840", "Thunder 5840S",
                                                       "Thunder 6430", "Thunder 6430S"]),
    ("A10 Networks", "Thunder ADC (older)", LEG, "1RU-2RU", EOS, KNOWN,
     ["Thunder 930", "Thunder 1030S", "Thunder 1040", "Thunder 1040S", "Thunder 3030S", "Thunder 3040", "Thunder 3040S",
      "Thunder 3230S", "Thunder 3330", "Thunder 3430", "Thunder 3430S", "Thunder 3530", "Thunder 4430", "Thunder 4430S",
      "Thunder 5330", "Thunder 5430", "Thunder 5440", "Thunder 5630", "Thunder 6440", "Thunder 6630", "Thunder 6635",
      "Thunder 7440", "Thunder 7445", "Thunder 7650", "Thunder 14045"]),
    ("A10 Networks", "AX Series (end of life)", LEG, "1RU-2RU", EOSUP, KNOWN,
     ["AX 1000", "AX 2200", "AX 2500", "AX 2600", "AX 3000", "AX 3200", "AX 3400", "AX 5100", "AX 5200"]),
    # Radware Alteon
    ("Radware", "Alteon D-line", ADC, "1RU-2RU", C, WEB,
     ["Alteon D-5208", "Alteon D-5424", "Alteon D-5820", "Alteon D-6024", "Alteon D-6420", "Alteon D-7220",
      "Alteon D-7612", "Alteon D-8420", "Alteon D-9800"]),
    ("Radware", "Alteon (older)", LEG, "1RU-2RU", EOS, KNOWN,
     ["Alteon 4408", "Alteon 4416", "Alteon 5224", "Alteon 5412", "Alteon 5820", "Alteon 6024", "Alteon 6420",
      "Alteon 8420", "Alteon 10000"]),
    # Kemp
    ("Kemp (Progress)", "LoadMaster X", ADC, R1, C, WEB, ["LM-X1", "LM-X3"]),
    ("Kemp (Progress)", "LoadMaster X", ADC, R1, C, KNOWN, ["LM-X15", "LM-X25", "LM-X40"]),
    ("Kemp (Progress)", "LoadMaster (older)", LEG, R1, EOS, KNOWN,
     ["LM-2000", "LM-2200", "LM-2400", "LM-2600", "LM-3400", "LM-3600", "LM-4000", "LM-5300", "LM-5400", "LM-5600",
      "LM-8020", "LM-8020M", "LM-R320"]),
    # Array Networks
    ("Array Networks", "APV", ADC, "1RU-2RU", C, KNOWN,
     ["APV1800", "APV2800", "APV3800", "APV6800", "APV8800", "APV11800"]),
    ("Array Networks", "AVX", ADC, R2, C, KNOWN, ["AVX5800", "AVX7800", "AVX9800", "AVX10650"]),
    ("Array Networks", "APV (older)", LEG, "1RU-2RU", EOS, KNOWN,
     ["APV1200", "APV2600", "APV4600", "APV5800", "APV6600", "APV8600", "APV10650"]),
    # Fortinet
    ("Fortinet", "FortiADC F-series", ADC, "Desktop / 1RU", C, WEB,
     ["FortiADC 100F", "FortiADC 120F", "FortiADC 200F", "FortiADC 220F", "FortiADC 300F", "FortiADC 320F",
      "FortiADC 400F", "FortiADC 420F", "FortiADC 1000F", "FortiADC 1200F", "FortiADC 2000F", "FortiADC 2200F",
      "FortiADC 4000F", "FortiADC 4200F", "FortiADC 5000F"]),
    ("Fortinet", "FortiADC D-series (older)", LEG, R1, EOS, WEB,
     ["FortiADC 200D", "FortiADC 300D", "FortiADC 400D", "FortiADC 700D", "FortiADC 1500D", "FortiADC 2000D",
      "FortiADC 4000D"]),
    # Barracuda
    ("Barracuda", "Load Balancer ADC", ADC, "1RU-2RU", C, KNOWN,
     ["340", "440", "540", "640", "641", "642", "840", "841", "842", "940", "941", "942"]),
    # others
    ("Loadbalancer.org", "Enterprise appliances", ADC, R1, C, KNOWN,
     ["Enterprise R16", "Enterprise", "Enterprise MAX", "Enterprise 10G", "Enterprise 40G"]),
    ("Piolink", "PAS-K", ADC, R1, C, KNOWN, ["PAS-K (models: verify)"]),
    ("HAProxy", "ALOHA appliances", ADC, R1, C, KNOWN, ["ALOHA 1G", "ALOHA 2G", "ALOHA 4G", "ALOHA 10G"]),
    ("Sangfor", "Application Delivery (AD)", ADC, R1, C, KNOWN, ["Sangfor AD (models: verify)"]),
    ("Cisco", "ACE / CSS (end of life)", LEG, R1, EOSUP, KNOWN,
     ["ACE 4710", "CSS 11501", "CSS 11503", "CSS 11506", "ACE30 module"]),
    ("Brocade", "ServerIron ADX (end of life)", LEG, "1RU-chassis", EOSUP, KNOWN,
     ["ServerIron ADX 1000", "ServerIron ADX 4000", "ServerIron ADX 10000"]),
]


GROUPS += [
    # added 2026-10-15 (gap check)
    ("Edgenexus", "ALB-X appliances", ADC, R1, C, WEB, ["ALB-X H1", "ALB-X H2", "ALB-X H3-10"]),
    ("Radware", "LinkProof NG (link load balancer)", "Link load balancer", R1, C, KNOWN,
     ["LinkProof NG 1008", "LinkProof NG 2016", "LinkProof NG 5224", "LinkProof NG 6420"]),
    ("Elfiq", "Link balancers", "Link load balancer", R1, EOS, KNOWN, ["Elfiq link balancer (models: verify)"]),
    ("Ecessa", "WANworX / PowerLink", "Link load balancer", R1, EOS, KNOWN, ["Ecessa PowerLink (models: verify)"]),
    ("Coyote Point", "Equalizer (end of life)", LEG, R1, EOSUP, KNOWN,
     ["E250GX", "E350GX", "E450GX", "E650GX", "E670GX", "E970GX"]),
    ("SKUDONET (Zevenet)", "Hardware appliances", ADC, R1, C, KNOWN, ["SKUDONET appliance (models: verify)"]),
    ("Foundry / Brocade", "ServerIron (end of life)", LEG, "1RU-chassis", EOSUP, KNOWN,
     ["ServerIron 450", "ServerIron 850", "ServerIron GT C", "ServerIron GT E", "ServerIron 4G-SSL"]),
    ("Nortel", "Alteon (pre-Radware, end of life)", LEG, R1, EOSUP, KNOWN,
     ["Alteon 2208", "Alteon 2216", "Alteon 2424", "Alteon 3408", "Alteon 2424-SSL"]),
    ("Juniper", "DX (end of life)", LEG, R1, EOSUP, KNOWN, ["DX 3200", "DX 3600"]),
    ("DPtech", "ADX", ADC, R1, C, KNOWN, ["DPtech ADX (models: verify)"]),
]


MULTI_WAN = "Multi-WAN load balance router"


def _router_rows():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "router_catalog"))
    import full_models as fm   # noqa: E402
    return fm.rows()


def router_link_balancers():
    """Routers their vendors sell as load balancers (multi-WAN link load balancing), as listed in
    the router catalogue: Peplink Balance, TP-Link's Load Balance / SafeStream and Omada multi-WAN
    routers (TL-ER5120, TL-R480T+), DrayTek Vigor (dual / multi-WAN load balancing on every model),
    Cisco RV dual-WAN routers."""
    out = []
    for v, s, m, cat, form, st, src, var in _router_rows():
        if (v == "Peplink" and m.startswith("Balance")) \
                or (v == "TP-Link" and (s.startswith(("SafeStream", "Omada")) or m.startswith(("TL-ER", "TL-R")))) \
                or v == "DrayTek" \
                or (v == "Cisco" and m in CISCO_DUAL_WAN):
            out.append((v, s, m, "Link load balancer" if v == "Peplink" else MULTI_WAN, form, st, src, var))
    return out


CISCO_DUAL_WAN = {"RV016", "RV042", "RV042G", "RV082", "RV320", "RV325", "RV340", "RV340W", "RV345", "RV345P",
                  "RV260", "RV260P", "RV260W", "RV160", "RV160W"}

GROUPS += [
    # added 2026-10-16: every load-balancer vendor, not only data-centre ADCs
    ("TP-Link", "Load Balance routers", MULTI_WAN, "Desktop / 1RU", C, WEB, ["TL-R480T+", "TL-ER5120"]),
    ("H3C", "SecPath L5000 / L1000", ADC, "1RU-2RU", C, "Vendor site (scraped)",
     ["L5000-AD830", "L5000-AD820", "L5000-E", "L5030", "L5060", "L5080", "L5000-AK535", "L5060-G", "L5000-CN530",
      "L1000-AK310", "L1000-AK315", "L1000-AK325", "L1000-AK390", "L1030", "L1050", "L1070", "L1090"]),
    ("FatPipe", "WARP / MPVPN / IPVPN", "Link load balancer", "1RU", C, WEB, ["FatPipe WARP", "FatPipe MPVPN", "FatPipe IPVPN"]),
]


def rows():
    base = expand(GROUPS)
    seen = {(r[0], r[2].upper()) for r in base}
    return base + [r for r in router_link_balancers() if (r[0], r[2].upper()) not in seen]
