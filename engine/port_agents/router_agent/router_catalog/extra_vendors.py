"""Third round: the remaining router vendors (SD-WAN, European, Japanese, firewall,
Asian SP, industrial/cellular, home). Same row layout and Source labels as
full_models.OTHERS. Vendors with no model list found online and none known well
enough (Netskope SD-WAN hardware, DZS, Kontron, DPTech, DriveNets, Inspur) are left out."""
from full_models import (BR, C, CHAS, DESK, EOS, IND, INDF, KNOWN, RACK, SDW, SEC, SMB, SP, WEB)

HOME = "Home / consumer router"

EXTRA = [
    # ---------------------------------------------------------------- SD-WAN / branch appliances
    ("VMware VeloCloud", "Edge 700 series", SDW, "Desktop / 1RU", C, WEB, ["Edge 710-W", "Edge 710-5G", "Edge 720", "Edge 740"]),
    ("VMware VeloCloud", "Edge 600 / 3000 series", SDW, "Desktop / 1RU", EOS, WEB, ["Edge 510N", "Edge 610C", "Edge 610N", "Edge 610LTE", "Edge 620", "Edge 3400"]),
    ("VMware VeloCloud", "Edge (older)", SDW, "Desktop / 1RU", EOS, KNOWN,
     ["Edge 510", "Edge 510LTE", "Edge 520", "Edge 520v", "Edge 540", "Edge 610", "Edge 640", "Edge 680", "Edge 840",
      "Edge 2000", "Edge 3800", "Edge 3810"]),
    ("Aryaka", "ANAP appliances", SDW, "Desktop / 1RU", C, WEB, ["ANAP 1000", "ANAP 2000", "ANAP 2600", "ANAP 3000", "ANAP 4000", "ANAP 10000"]),
    ("Zscaler", "Zero Trust Branch", SDW, "Desktop / 1RU", C, WEB, ["ZT400", "ZT600", "ZT800"]),
    ("Citrix", "SD-WAN Standard Edition", SDW, "Desktop / rack", EOS, WEB,
     ["SD-WAN 110", "SD-WAN 210", "SD-WAN 400", "SD-WAN 410", "SD-WAN 1000", "SD-WAN 1100", "SD-WAN 2000",
      "SD-WAN 2100", "SD-WAN 4000", "SD-WAN 4100", "SD-WAN 5100", "SD-WAN 6100"]),

    # ---------------------------------------------------------------- European enterprise routers
    ("LANCOM Systems", "SD-WAN routers / gateways", BR, "Desktop / 1RU", C, WEB,
     ["1781EF", "1781VAW", "1790VA", "1790EF", "1793VAW", "1800EF", "1800EFW", "1800VA", "1800VAW", "1803VA", "1803VAW",
      "1900EF", "1926VAG", "883 VoIP"]),
    ("LANCOM Systems", "Central-site gateways", BR, RACK, C, WEB, ["ISG-5000", "ISG-8000"]),
    ("LANCOM Systems", "Central-site gateways", BR, RACK, C, KNOWN, ["ISG-1000", "ISG-4000"]),
    ("LANCOM Systems", "R&S Unified Firewalls", SEC, "Desktop / 1RU", C, KNOWN,
     ["UF-60", "UF-160", "UF-260", "UF-360", "UF-760", "UF-910", "UF-1060"]),

    ("bintec elmeg", "RS series VPN routers", BR, "Desktop / rack", C, WEB,
     ["RS123", "RS123w", "RS123-4G", "RS353j", "RS353jw", "RS353j-4G", "RS353jv", "RS353jwv", "RS353jv-4G", "be.IP plus"]),
    ("bintec elmeg", "RXL / be.IP", BR, RACK, C, KNOWN, ["RXL12100", "RXL12500", "be.IP smart"]),

    ("Viprinet", "Multichannel VPN routers", SDW, "Desktop / rack", C, WEB, ["300", "310", "2610", "2620", "5010"]),
    ("Viprinet", "Multichannel VPN routers", SDW, "Desktop / rack", C, KNOWN, ["510", "512"]),

    ("INSYS icom", "Industrial routers", IND, INDF, C, KNOWN, ["MRX2", "MRX3", "MRX5", "MRO-L200", "SCR-WAN"]),

    # ---------------------------------------------------------------- Japanese routers
    ("NEC", "UNIVERGE IX", BR, "Desktop / 1RU", EOS, WEB, ["IX2105", "IX2106", "IX2207", "IX2215", "IX2310", "IX3015", "IX3110"]),
    ("NEC", "UNIVERGE IX", BR, "Desktop / 1RU", C, WEB, ["IX3315"]),
    ("NEC", "UNIVERGE IX-R", BR, "Desktop / 1RU", C, WEB, ["IX-R2510", "IX-R2530", "IX-R2610"]),
    ("NEC", "UNIVERGE IX", BR, "Desktop / 1RU", C, KNOWN, ["IX2107", "IX2235", "IX1020"]),

    ("Fujitsu", "Si-R G series", BR, "Desktop / 1RU", EOS, WEB, ["Si-R G100", "Si-R G200"]),
    ("Fujitsu", "Si-R G series", BR, "Desktop / 1RU", C, KNOWN,
     ["Si-R G110", "Si-R G120", "Si-R G121", "Si-R G210", "Si-R G211", "Si-R G220", "Si-R G300", "Si-R G310",
      "Si-R GX500", "Si-R GX510"]),
    ("Fujitsu", "Si-R (older)", BR, "Desktop / 1RU", EOS, WEB,
     ["Si-R30", "Si-R130", "Si-R170", "Si-R220", "Si-R260", "Si-R300", "Si-R330", "Si-R870"]),

    ("Furukawa Electric", "FITELnet", BR, "Desktop / 1RU", C, KNOWN, ["FITELnet F70", "FITELnet F71", "FITELnet F220", "FITELnet F221", "FITELnet F2200", "FITELnet F3000"]),

    # ---------------------------------------------------------------- firewalls that route
    ("Hillstone Networks", "A-Series NGFW", SEC, "Desktop / rack", C, WEB,
     ["A200", "A1100", "A2715", "A2815", "A3615", "A3815", "A7600"]),
    ("Hillstone Networks", "A-Series NGFW", SEC, "Desktop / rack", C, KNOWN,
     ["A1000", "A1600", "A2000", "A2600", "A3000", "A3600", "A3700", "A3800", "A5100", "A5200", "A5500", "A5600",
      "A6600", "A6800", "A7000"]),
    ("Hillstone Networks", "E-Series NGFW", SEC, "Desktop / 1RU", C, WEB, ["E1100W", "E1600", "E1606", "E1700"]),
    ("Hillstone Networks", "X-Series DC firewall", SEC, CHAS, C, WEB, ["X25812"]),

    ("Stormshield", "SN range", SEC, "Desktop / rack", C, WEB,
     ["SN160", "SN160W", "SN210", "SN210W", "SN310", "SN-XS-Series-170", "SN-S-Series-220", "SN-S-Series-320", "SN510",
      "SN-M-Series-520", "SN710", "SN910", "SN-M-Series-720", "SN-M-Series-920", "SN1100", "SN2100", "SN3100",
      "SN-L-Series-2200", "SN-L-Series-3200", "SN6100", "SN-XL-Series-5200", "SN-XL-Series-6200", "SNi10", "SNi20",
      "SNi40", "SNxr1200"]),
    ("Stormshield", "SN range (older)", SEC, "Desktop / rack", EOS, WEB,
     ["SN150", "SN200", "SN300", "SN500", "SN700", "SN900", "SN2000", "SN3000", "SN6000"]),

    ("Sangfor", "NGAF / SD-WAN appliances", SEC, "Desktop / rack", C, KNOWN, ["NGAF series (models: verify)"]),

    ("Deciso", "OPNsense appliances", SEC, "Desktop / rack", C, WEB, ["DEC740", "DEC750", "DEC850"]),
    ("Deciso", "OPNsense appliances", SEC, "Desktop / rack", C, KNOWN, ["DEC840", "DEC2750", "DEC3850", "DEC4280"]),
    ("Protectli", "Vault", SMB, DESK, C, WEB, ["VP2420"]),
    ("Protectli", "Vault", SMB, DESK, C, KNOWN, ["FW4B", "FW4C", "FW6D", "VP2410", "VP4630", "VP4650", "VP4670", "VP6630", "VP6650", "VP6670"]),
    ("Firewalla", "Firewall routers", SMB, DESK, C, KNOWN, ["Firewalla Gold", "Firewalla Gold Plus", "Firewalla Gold SE", "Firewalla Gold Pro", "Firewalla Purple", "Firewalla Purple SE", "Firewalla Blue Plus"]),

    # ---------------------------------------------------------------- Asian / SP routers
    ("Maipu", "MP routers", BR, "Desktop / rack", C, WEB,
     ["MP1800X-50", "MP1800X-51", "MP1900X-12-AC", "MP1900X-22-AC", "MP2900X-04", "MP2900X-14", "MP2900X-24",
      "MP7500X-04", "MP7500X-10", "MP1700X-40"]),
    ("Maipu", "MP routers (older)", BR, RACK, EOS, WEB, ["MP3800"]),
    ("Raisecom", "Routers", BR, "Desktop / 1RU", C, WEB, ["MSG1200"]),
    ("FiberHome", "CiTRANS R800 IP RAN routers", SP, "Rack / modular chassis", C, WEB, ["CiTRANS R820", "CiTRANS R820-7", "CiTRANS R835E", "CiTRANS R845", "CiTRANS R860"]),
    ("FiberHome", "CiTRANS R800 IP RAN routers", SP, "Rack / modular chassis", C, KNOWN, ["CiTRANS R880"]),
    ("Ribbon (ECI)", "Neptune packet / router", SP, "Rack / modular chassis", C, KNOWN, ["NPT-1050", "NPT-1200", "NPT-1800", "NPT-1300"]),

    # ---------------------------------------------------------------- industrial / cellular
    ("Siemens", "SCALANCE M / MUM routers", IND, INDF, C, WEB, ["SCALANCE MUM856-1", "SCALANCE MUM853-1", "SCALANCE M804PB"]),
    ("Siemens", "SCALANCE M routers", IND, INDF, C, KNOWN,
     ["SCALANCE M812-1", "SCALANCE M816-1", "SCALANCE M826-2", "SCALANCE M874-2", "SCALANCE M874-3", "SCALANCE M876-3",
      "SCALANCE M876-4"]),
    ("Siemens", "SCALANCE S / SC security routers", IND, INDF, C, WEB, ["SCALANCE S615"]),
    ("Siemens", "SCALANCE SC security routers", IND, INDF, C, KNOWN,
     ["SCALANCE SC622-2C", "SCALANCE SC632-2C", "SCALANCE SC636-2C", "SCALANCE SC642-2C", "SCALANCE SC646-2C"]),

    ("MultiTech", "rCell / Conduit", IND, INDF, C, WEB, ["rCell 100 (MTR-LNA7)", "rCell 100 (MTR-L4G1)", "rCell 500 (MTR5)", "Conduit (MTCDT)", "Conduit 300 (MTCDT3AC)"]),
    ("MultiTech", "rCell", IND, INDF, C, KNOWN, ["rCell 300"]),
    ("Inseego", "Wavemaker 5G routers", SMB, DESK, C, WEB, ["FX2000", "FX2000e", "FX3100", "FX3110", "FX4100"]),
    ("Inseego", "Skyus / Wavemaker", IND, INDF, C, KNOWN, ["Skyus 500", "Skyus DS2", "FW2010", "FG2000"]),

    # ---------------------------------------------------------------- home / consumer (main models only)
    ("AVM", "FRITZ!Box", HOME, DESK, C, WEB, ["FRITZ!Box 7690", "FRITZ!Box 7630 AX", "FRITZ!Box 7632", "FRITZ!Box 7590 AX", "FRITZ!Box 7590"]),
    ("AVM", "FRITZ!Box", HOME, DESK, C, KNOWN,
     ["FRITZ!Box 7530 AX", "FRITZ!Box 7530", "FRITZ!Box 7520", "FRITZ!Box 6690 Cable", "FRITZ!Box 6670 Cable",
      "FRITZ!Box 6660 Cable", "FRITZ!Box 6591 Cable", "FRITZ!Box 5690 Pro", "FRITZ!Box 5590 Fiber", "FRITZ!Box 5530 Fiber",
      "FRITZ!Box 6860 5G", "FRITZ!Box 6850 5G", "FRITZ!Box 6850 LTE", "FRITZ!Box 4060", "FRITZ!Box 4040"]),
    ("Keenetic", "Routers", HOME, DESK, C, KNOWN,
     ["Peak", "Ultra", "Giga", "Hopper", "Hopper DSL", "Titan", "Viva", "Speedster", "Explorer", "Hero 4G", "Skipper DSL"]),
    ("GL.iNet", "Routers", HOME, DESK, C, KNOWN,
     ["GL-MT6000 (Flint 2)", "GL-AX1800 (Flint)", "GL-BE9300 (Flint 3)", "GL-MT3000 (Beryl AX)", "GL-MT2500 (Brume 2)",
      "GL-AXT1800 (Slate AX)", "GL-BE3600 (Slate 7)", "GL-X3000 (Spitz AX)", "GL-XE3000 (Puli AX)", "GL-SFT1200 (Opal)",
      "GL-MT300N-V2 (Mango)", "GL-AR300M16 (Shadow)"]),
    ("Synology", "Routers", HOME, DESK, C, KNOWN, ["RT6600ax", "WRX560", "RT2600ac", "MR2200ac", "BC500"]),
    ("Eero", "Mesh routers", HOME, DESK, C, KNOWN, ["eero 6", "eero 6+", "eero Pro 6", "eero Pro 6E", "eero 7", "eero Pro 7", "eero Max 7", "eero PoE 6", "eero PoE Gateway"]),
    ("Google", "Nest Wifi", HOME, DESK, C, KNOWN, ["Nest Wifi Pro", "Nest Wifi", "Google Wifi"]),
    ("Xiaomi", "Routers", HOME, DESK, C, KNOWN, ["Router AX3000T", "Router AX3600", "Router AX6000", "Router AX9000", "Router BE3600", "Router BE5000", "Router BE7000", "Mesh System AX3000"]),
]
