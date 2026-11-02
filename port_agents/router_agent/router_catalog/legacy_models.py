"""Legacy pass: older router generations of the big vendors - end-of-life but still
common in racks. Cisco series names are cisco.com's 'Routers - Retired Products' list
(router_catalog/scraped/cisco_retired.html); Cisco removes retired model pages, so the
model numbers inside them are product knowledge. Same row layout as full_models.OTHERS."""
from full_models import (BR, CHAS, DESK, EOS, EOSUP, IND, INDF, KNOWN, RACK, SDW, SEC, SMB, SP, WEB)

R = "Retired - no longer supported by Cisco (cisco.com retired list)"

LEGACY = [
    # ---------------------------------------------------------------- Cisco retired series
    ("Cisco", "830 / SOHO (retired)", SMB, DESK, EOSUP, KNOWN,
     ["CISCO831", "CISCO836", "CISCO837", "SOHO77", "SOHO78", "SOHO91", "SOHO96", "SOHO97"]),
    ("Cisco", "850 / 870 (retired)", SMB, DESK, EOSUP, KNOWN,
     ["CISCO851", "CISCO857", "CISCO871", "CISCO876", "CISCO877", "CISCO878"]),
    ("Cisco", "1700 Modular Access (retired)", BR, DESK, EOSUP, KNOWN,
     ["CISCO1701", "CISCO1711", "CISCO1712", "CISCO1721", "CISCO1751", "CISCO1760"]),
    ("Cisco", "1800 ISR (retired)", BR, "Desktop / 1RU", EOSUP, KNOWN,
     ["CISCO1801", "CISCO1802", "CISCO1803", "CISCO1811", "CISCO1812", "CISCO1841", "CISCO1861"]),
    ("Cisco", "2600 Multiservice (retired)", BR, RACK, EOSUP, KNOWN,
     ["CISCO2610", "CISCO2611", "CISCO2612", "CISCO2620", "CISCO2621", "CISCO2650", "CISCO2651", "CISCO2610XM",
      "CISCO2611XM", "CISCO2620XM", "CISCO2621XM", "CISCO2650XM", "CISCO2651XM", "CISCO2691"]),
    ("Cisco", "2800 ISR (retired)", BR, RACK, EOSUP, KNOWN, ["CISCO2801", "CISCO2811", "CISCO2821", "CISCO2851"]),
    ("Cisco", "3200 Rugged ISR (retired)", IND, INDF, EOSUP, KNOWN, ["CISCO3220", "CISCO3230", "CISCO3250", "CISCO3270"]),
    ("Cisco", "3600 Multiservice (retired)", BR, RACK, EOSUP, KNOWN, ["CISCO3620", "CISCO3640", "CISCO3660"]),
    ("Cisco", "3700 Multiservice (retired)", BR, RACK, EOSUP, KNOWN, ["CISCO3725", "CISCO3745"]),
    ("Cisco", "3800 ISR (retired)", BR, RACK, EOSUP, KNOWN, ["CISCO3825", "CISCO3845"]),
    ("Cisco", "6400 Broadband Aggregator (retired)", SP, CHAS, EOSUP, KNOWN, ["CISCO6400"]),
    ("Cisco", "7200 (retired)", SP, RACK, EOSUP, KNOWN, ["CISCO7201", "CISCO7204VXR", "CISCO7206VXR"]),
    ("Cisco", "7300 (retired)", SP, RACK, EOSUP, KNOWN, ["CISCO7301", "CISCO7304"]),
    ("Cisco", "7500 (retired)", SP, CHAS, EOSUP, KNOWN, ["CISCO7505", "CISCO7507", "CISCO7513"]),
    ("Cisco", "7600 (retired)", SP, CHAS, EOSUP, KNOWN,
     ["CISCO7603", "CISCO7603-S", "CISCO7604", "CISCO7606", "CISCO7606-S", "CISCO7609", "CISCO7609-S", "CISCO7613"]),
    ("Cisco", "10000 / 10700 (retired)", SP, CHAS, EOSUP, KNOWN, ["ESR-10008", "CISCO10720"]),
    ("Cisco", "12000 / XR 12000 (retired)", SP, CHAS, EOSUP, KNOWN,
     ["CISCO12404", "CISCO12406", "CISCO12410", "CISCO12416", "CISCO12810", "CISCO12816"]),
    ("Cisco", "ASR 901S / 921 / 941 (retired)", SP, RACK, EOSUP, KNOWN,
     ["A901S-2SG-F-D", "A901S-3SG-F-D", "A901S-4SG-F-D", "ASR-921", "ASR-941"]),
    ("Cisco", "MWR mobile wireless routers (retired)", SP, RACK, EOSUP, KNOWN, ["MWR-1941-DC", "MWR-2941-DC", "MWR-3941"]),
    ("Cisco", "SR 500 / SRP500 / SB 100 (retired)", SMB, DESK, EOSUP, KNOWN,
     ["SR520", "SRP521", "SRP526", "SRP527", "SRP541", "SRP546", "SRP547", "SB101", "SB106", "SB107"]),
    ("Cisco", "900 Industrial (retired)", IND, INDF, EOSUP, KNOWN, ["IR905", "IR910"]),
    ("Cisco Meraki", "MX (retired)", SDW, "Desktop / 1RU", EOSUP, KNOWN, ["MX60", "MX60W", "MX70", "Z1"]),

    # ---------------------------------------------------------------- Juniper
    ("Juniper", "M-series (end of life)", SP, CHAS, EOSUP, WEB, ["M20", "M40e", "M160", "M320"]),
    ("Juniper", "M-series (end of life)", SP, CHAS, EOSUP, KNOWN, ["M7i", "M10i", "M40", "M120"]),
    ("Juniper", "T-series (end of life)", SP, CHAS, EOSUP, KNOWN, ["T320", "T640", "T1600", "T4000", "TX Matrix"]),
    ("Juniper", "J-series (end of life)", BR, RACK, EOSUP, KNOWN,
     ["J2300", "J2320", "J2350", "J4300", "J4350", "J6300", "J6350"]),
    ("Juniper", "SSG / NetScreen (end of life)", SEC, "Desktop / rack", EOSUP, KNOWN,
     ["SSG5", "SSG20", "SSG140", "SSG320M", "SSG350M", "SSG520M", "SSG550M", "ISG1000", "ISG2000", "NetScreen-5GT",
      "NetScreen-25", "NetScreen-50", "NetScreen-204", "NetScreen-208"]),
    ("Juniper", "SRX data center (older)", SEC, CHAS, EOS, KNOWN, ["SRX3400", "SRX3600"]),

    # ---------------------------------------------------------------- Huawei
    ("Huawei", "AR G3 (older)", BR, "Desktop / rack", EOS, WEB, ["AR150", "AR160"]),
    ("Huawei", "AR G3 (older)", BR, "Desktop / rack", EOS, KNOWN,
     ["AR151", "AR156", "AR157", "AR161", "AR169", "AR201", "AR206", "AR207", "AR1220", "AR1220C", "AR2201-48FE",
      "AR2202-48FE", "AR2204", "AR2204-27GE", "AR2220", "AR2240"]),
    ("Huawei", "NE20E / NE5000E / NE40E (older)", SP, "Rack / modular chassis", EOS, KNOWN,
     ["NE20E-S2F", "NE20E-S8", "NE20E-S16", "NE5000E", "NE40E-X1", "NE40E-X2", "NE40E-X3", "NE40E-X8", "NE40E-X16"]),

    # ---------------------------------------------------------------- Fortinet
    ("Fortinet", "FortiGate C-series (end of life)", SEC, "Desktop / rack", EOSUP, WEB, ["FG-80C"]),
    ("Fortinet", "FortiGate A/B/C-series (end of life)", SEC, "Desktop / rack", EOSUP, KNOWN,
     ["FG-20C", "FG-40C", "FG-60C", "FG-110C", "FG-300C", "FG-600C", "FG-800C", "FG-1000C", "FG-3240C", "FG-3600C",
      "FG-50B", "FG-60B", "FG-200B", "FG-310B", "FG-311B", "FG-620B", "FG-1240B", "FG-3040B", "FG-3140B", "FG-3950B",
      "FG-100A", "FG-200A", "FG-300A", "FG-400A", "FG-500A", "FG-1000A"]),
    ("Fortinet", "FortiGate D-series (end of life)", SEC, "Desktop / rack", EOSUP, KNOWN,
     ["FG-30D", "FG-70D", "FG-92D", "FG-140D", "FG-240D", "FG-280D", "FG-1500DT", "FG-3700D", "FG-3800D", "FG-3810D"]),

    # ---------------------------------------------------------------- Palo Alto / SonicWall
    ("Palo Alto Networks", "PA-2000 / PA-4000 (end of life)", SEC, RACK, EOSUP, KNOWN,
     ["PA-2020", "PA-2050", "PA-4020", "PA-4050", "PA-4060"]),
    ("SonicWall", "NSA / PRO / TZ (end of life)", SEC, "Desktop / rack", EOSUP, KNOWN,
     ["NSA 220", "NSA 240", "NSA 250M", "NSA 2400", "NSA 3500", "NSA 4500", "NSA 5000", "NSA E5500", "NSA E6500",
      "NSA E7500", "NSA E8500", "TZ 100", "TZ 105", "TZ 170", "TZ 180", "TZ 190", "TZ 200", "TZ 205", "TZ 210", "TZ 215",
      "PRO 2040", "PRO 3060", "PRO 4060", "PRO 4100", "PRO 5060"]),

    # ---------------------------------------------------------------- MikroTik
    ("MikroTik", "RouterBOARD (discontinued)", SMB, "Desktop / 1RU", EOS, WEB,
     ["RB750G", "RB750GL", "RB751U-2HnD", "RB850Gx2", "RB1100AH", "RB2011UiAS-2HnD-IN"]),
    ("MikroTik", "RouterBOARD (discontinued)", SMB, "Desktop / 1RU", EOS, KNOWN,
     ["RB450G", "RB450Gx4", "RB750", "RB750Gr2", "RB751G-2HnD", "RB951-2n", "RB951Ui-2HnD", "RB951G-2HnD",
      "RB2011UAS-RM", "RB2011iL-RM", "RB1100", "RB1200", "CCR1009-8G-1S-1S+"]),

    # ---------------------------------------------------------------- H3C / ZTE / Ubiquiti
    ("H3C", "MSR / SR8800 (older)", BR, "Rack / modular chassis", EOS, KNOWN,
     ["MSR20-10", "MSR20-20", "MSR20-40", "MSR30-10", "MSR30-20", "MSR30-40", "MSR30-60", "MSR50-40", "MSR50-60",
      "MSR900", "MSR930", "MSR2600-10-X1", "MSR3600-28", "MSR3600-51", "MSR3610", "SR8805", "SR8808", "SR8812"]),
    ("ZTE", "ZXR10 T-series / M6000 (older)", SP, CHAS, EOS, KNOWN,
     ["ZXR10 T600", "ZXR10 T1200", "ZXR10 T8000", "ZXR10 M6000-8", "ZXR10 M6000-16"]),
    ("Ubiquiti", "UniFi Security Gateway (older)", SMB, RACK, EOS, KNOWN, ["USG-XG-8"]),
    ("DrayTek", "Vigor (older)", SMB, "Desktop / 1RU", EOS, KNOWN,
     ["Vigor2925", "Vigor2925ac", "Vigor2925n", "Vigor2912", "Vigor2952", "Vigor2133", "Vigor2820", "Vigor2830"]),
]
