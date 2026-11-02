"""Router models of the 33 vendors from models_by_vendor.json that also make routers.
Vendor names are spelled exactly as in models_by_vendor.json, so the OCR's vendor
match carries over. Same row layout and Source labels as full_models.OTHERS."""
from full_models import (BR, C, CHAS, DC, DESK, EOS, IND, INDF, KNOWN, RACK, SCRAPED, SDW, SEC, SMB, SP, WEB)

HOME = "Home / consumer router"

MORE = [
    # ---------------------------------------------------------------- enterprise / service provider
    ("ZTE", "ZXR10 M6000-S", SP, CHAS, C, WEB,
     ["ZXR10 M6000-2S", "ZXR10 M6000-3S", "ZXR10 M6000-3S Plus", "ZXR10 M6000-4SE", "ZXR10 M6000-5S",
      "ZXR10 M6000-8S", "ZXR10 M6000-8S Plus", "ZXR10 M6000-8SE", "ZXR10 M6000-16SE", "ZXR10 M6000-18S"]),
    ("ZTE", "ZXR10 ZSR V2", BR, RACK, C, WEB,
     ["ZXR10 1800-2S", "ZXR10 2800-4", "ZXR10 3800-8", "ZXR10 230", "ZXR10 260", "ZXR10 280"]),
    ("ZTE", "ZXCTN (IP RAN)", SP, RACK, C, KNOWN, ["ZXCTN 6120H", "ZXCTN 6180H", "ZXCTN 9000-2E10", "ZXCTN 9000-8EA"]),

    ("Arista Networks", "7280R3 routers", DC, RACK, C, WEB,
     ["7280PR3-48", "7280PR3-24", "7280CR3-96", "7280CR3-32P4", "7280CR3-32D4", "7280CR3-36S", "7280CR3K-36S",
      "7280SR3-40YC6", "7280SR3-48YC8", "7280SR3K-48YC8", "7280TR3-40C6", "7280DR3-24", "7280DR3A-36"]),
    ("Arista Networks", "7500R3 / 7800R3 modular", DC, CHAS, C, WEB,
     ["7504R3", "7508R3", "7512R3", "7804R3", "7808R3", "7812R3", "7816R3", "7816LR3"]),
    ("Arista Networks", "7800R4 modular", DC, CHAS, C, KNOWN, ["7804R4", "7808R4", "7812R4", "7816R4"]),
    ("Arista Networks", "Edge / SD-WAN (Velocloud-era Arista)", SDW, "Desktop / 1RU", C, KNOWN,
     ["Arista 5000 Edge", "Arista 5100 Edge", "Arista 5200 Edge"]),

    ("Ruijie Networks", "RSR routers", BR, RACK, C, WEB,
     ["RG-RSR10-01G-T", "RG-RSR20-XA", "RG-RSR30-XA", "RG-RSR77-XA"]),
    ("Ruijie Networks", "EG gateways (Reyee)", SMB, "Desktop / 1RU", C, WEB,
     ["RG-EG105G", "RG-EG105G-P", "RG-EG210G", "RG-EG210G-P-V3", "RG-EG2100-P V2", "RG-EG3000UE", "RG-EG3000XE",
      "RG-EG3230", "RG-EG3250", "RG-EG710XS"]),

    ("Allied Telesis", "AR VPN routers / UTM firewalls", SEC, "Desktop / 1RU", C, WEB,
     ["AR1050V", "AR2050V", "AR3050S", "AR4050S", "AR4050S-5G"]),
    ("Allied Telesis", "AR (older)", BR, "Desktop / 1RU", EOS, KNOWN, ["AR415S", "AR750S", "AR770S"]),

    ("Yamaha", "RTX / NVR / FWX", BR, "Desktop / 1RU", C, WEB,
     ["RTX1300", "RTX1220", "RTX840", "RTX830", "NVR700W", "NVR510"]),
    ("Yamaha", "RTX (older)", BR, "Desktop / 1RU", EOS, WEB,
     ["RTX5000", "RTX3500", "RTX1210", "RTX810", "NVR500", "FWX120"]),
    ("Yamaha", "RTX", SP, RACK, C, KNOWN, ["RTX3510"]),

    ("Versa Networks", "CSG appliances", SDW, "Desktop / 1RU", C, WEB,
     ["CSG150", "CSG350", "CSG355", "CSG365", "CSG730", "CSG750", "CSG750R", "CSG770", "CSG1300", "CSG1500",
      "CSG2500", "CSG5000"]),

    ("Edgecore Networks", "Cell site gateways (DCSG)", SP, RACK, C, WEB,
     ["AS7316-26XB (CSR320)", "AS7315-27X", "AS5915-18X"]),
    ("Edgecore Networks", "Disaggregated core / aggregation", SP, RACK, C, KNOWN,
     ["AS7946-30XB", "AS7946-74XKSB", "AS9947-72XKB"]),

    ("UfiSpace", "Open cell site / aggregation / core routers", SP, RACK, C, WEB,
     ["S9500-30XS", "S9600-72XC", "S9600-30DX", "S9700-53DX", "S9705-48D"]),
    ("UfiSpace", "Open routers", SP, RACK, C, KNOWN, ["S9500-22XST", "S9510-28DC", "S9600-32X", "S9710-76D"]),

    ("Tejas Networks", "TJ1400 IP/MPLS routers", SP, "Rack / modular chassis", C, WEB,
     ["TJ1400P", "TJ1400P 4X", "TJ1400-7", "TJ1400-11", "TJ1400-13"]),

    ("Silicom Ltd", "Edge / uCPE appliances", SDW, "Desktop / 1RU", C, WEB,
     ["Ibiza", "Cordoba", "Barcelona", "Bilbao", "Burgos", "Ceuta", "Seville", "Marbella", "Valencia", "Palma",
      "Vigo", "uCPE Small", "uCPE Medium", "uCPE Large"]),

    ("Extreme", "ExtremeRouting SLX", SP, RACK, C, WEB, ["SLX 9640", "SLX 9740-40C", "SLX 9740-80C"]),
    ("Extreme", "Routing (older)", SP, "Rack / modular chassis", EOS, KNOWN,
     ["MLXe-4", "MLXe-8", "MLXe-16", "MLXe-32", "CER 2024C", "CER 2024F", "CER 2048C", "CER 2048F", "XA1400",
      "XA1440", "XA1480"]),

    # ---------------------------------------------------------------- SMB / office / home
    ("NETGEAR", "Business VPN routers", SMB, DESK, C, WEB, ["BR500", "BR200", "PR60X"]),
    ("NETGEAR", "ProSAFE VPN firewalls (older)", SMB, "Desktop / 1RU", EOS, WEB,
     ["FVS318", "FVS318G", "FVS318N", "FVS336G", "SRX5308"]),
    ("NETGEAR", "Nighthawk / Orbi routers", HOME, DESK, C, KNOWN,
     ["RS700S", "RS300", "RAXE500", "RAX120", "RAX80", "RAX50", "RAX40", "RAX10", "R7000", "R6700", "R6400",
      "R8000", "R7800", "SXR80 (Orbi Pro)", "SXR30 (Orbi Pro)", "RBR860", "RBR760", "RBR750", "RBE970", "RBE770"]),

    ("D-Link", "DSR VPN routers", SMB, "Desktop / 1RU", C, WEB,
     ["DSR-150", "DSR-150N", "DSR-250", "DSR-250N", "DSR-250V2", "DSR-500", "DSR-500N", "DSR-1000", "DSR-1000N",
      "DSR-1000AC"]),
    ("D-Link", "Nuclias business gateways", SMB, "Desktop / 1RU", C, KNOWN, ["DBG-2000", "DBG-X1000", "DBG-X800"]),
    ("D-Link", "Home routers", HOME, DESK, C, KNOWN,
     ["DIR-X1860", "DIR-X3260", "DIR-X5460", "DIR-X6060", "DIR-842", "DIR-882", "DIR-1960", "DIR-2660", "DIR-3060",
      "M15 (EAGLE PRO AI)", "R15", "R18", "R32"]),

    ("Linksys", "Business VPN routers", SMB, "Desktop / 1RU", C, KNOWN,
     ["LRT224", "LRT214", "LRT-1030 (Cloud Router)"]),
    ("Linksys", "Home routers", HOME, DESK, C, KNOWN,
     ["E5600", "E7350", "E8450", "E9450", "EA6350", "EA7500", "EA8100", "EA8300", "EA9500", "MR7350", "MR9600",
      "MR20000", "MX4200 (Velop)", "MX5500 (Velop)", "MBE7000 (Velop)", "WRT3200ACM", "WRT32X", "WRT1900ACS"]),

    ("ASUS", "ExpertWiFi business routers", SMB, DESK, C, KNOWN,
     ["EBG15", "EBG19P", "EBR63", "EBM68", "EBA63"]),
    ("ASUS", "Home / gaming routers", HOME, DESK, C, KNOWN,
     ["RT-AX58U", "RT-AX86U", "RT-AX86U Pro", "RT-AX88U", "RT-AX88U Pro", "RT-AX92U", "RT-AX3000", "RT-AX5400",
      "RT-AX1800S", "RT-AC68U", "RT-AC86U", "RT-BE58U", "RT-BE88U", "RT-BE92U", "RT-BE96U", "GT-AX6000",
      "GT-AX11000", "GT-AX11000 Pro", "GT-AXE11000", "GT-AXE16000", "GT-BE98", "GT-BE98 Pro", "TUF-AX3000",
      "TUF-AX5400", "ZenWiFi XT8", "ZenWiFi XT9", "ZenWiFi BQ16", "ZenWiFi BT10", "ZenWiFi ET8"]),

    ("TrendNet", "Business VPN routers", SMB, "Desktop / 1RU", C, KNOWN, ["TWG-431BR", "TWG-BR1"]),
    ("TrendNet", "Home routers", HOME, DESK, C, KNOWN, ["TEW-827DRU", "TEW-829DRU", "TEW-831DR", "TEW-923DAP"]),

    ("Tenda", "Business routers", SMB, DESK, C, KNOWN, ["G0-5G-PoE", "G3", "G3 V3", "G5", "W30E"]),
    ("Tenda", "Home routers", HOME, DESK, C, KNOWN,
     ["AC6", "AC8", "AC10", "AC19", "AC23", "AX12", "AX1500", "AX1800", "RX2 Pro", "RX9 Pro", "RX12 Pro", "TX2 Pro",
      "TX9 Pro", "TX12 Pro", "BE12 Pro", "MX12 (Nova)"]),

    ("TOTOLINK", "Home / SMB routers", HOME, DESK, C, KNOWN,
     ["A720R", "A800R", "A3000RU", "A3002RU", "A3100R", "A7000R", "A8000RU", "X5000R", "X6000R", "X18", "X30",
      "X60", "N300RT", "N350RT"]),

    ("Edimax Technology Co., Ltd", "Business gateways / routers", SMB, DESK, C, KNOWN,
     ["Pro IAP-1200", "BR-6478AC V2", "BR-6208AC", "RG21S", "Gemini RG21S", "Gemini RE23S"]),

    ("Billion Electric", "BiPAC routers", SMB, DESK, C, KNOWN,
     ["BiPAC 8920NZ", "BiPAC 8900AX", "BiPAC 8900AX-2400", "BiPAC 8700AXL", "BiPAC 8206Z", "BiPAC 4500NZ",
      "BiPAC 4700AX", "BiPAC 6600NXL", "BiPAC 7800DXL"]),

    ("Araknis", "VPN routers", SMB, RACK, C, WEB,
     ["AN-110-RT-2L1W", "AN-110-RT-2L1W-WIFI", "AN-300-RT-4L2W", "AN-310-RT-4L2W"]),
    ("Araknis", "VPN routers", SMB, RACK, C, KNOWN, ["AN-210-RT-2L1W", "AN-510-RT-4L2W"]),

    ("Luxul", "Epic routers", SMB, RACK, C, WEB, ["ABR-4500", "XBR-4500"]),
    ("Luxul", "Epic routers (older)", SMB, RACK, EOS, WEB, ["ABR-5000"]),
    ("Luxul", "Epic routers (older)", SMB, RACK, EOS, KNOWN, ["XBR-4400", "XBR-2300", "ABR-4400"]),

    ("Datto", "Datto Networking Appliance / Secure Edge", SMB, DESK, C, WEB, ["DNA", "DNA-VZ4"]),
    ("Datto", "Secure routers", SMB, DESK, C, KNOWN, ["DSR-5", "DSR-10", "DSR-25"]),

    ("Nomadix", "Hospitality gateways", SMB, RACK, C, WEB, ["AG 2400", "AG 5800", "EG 6000"]),
    ("Nomadix", "Hospitality gateways (older)", SMB, RACK, EOS, WEB, ["AG 5900"]),
    ("Nomadix", "Hospitality gateways (older)", SMB, RACK, EOS, KNOWN, ["AG 2300", "AG 3100", "AG 5600"]),

    ("Planet", "VR VPN security routers", SMB, DESK, C, WEB,
     ["VR-100", "VR-300", "VR-300P", "VR-300F", "VR-300FP", "VR-300W5", "VR-300PW5", "VR-300W6", "VR-300PW6",
      "VR-300W6A", "VR-300PW6A", "VR-300FW-NR"]),
    ("Planet", "Industrial VPN gateways", IND, INDF, C, WEB, ["IVR-300", "IVR-300W", "IVR-300FP"]),
    ("Planet", "Industrial cellular gateways", IND, INDF, C, KNOWN, ["ICG-2510", "ICG-2510W-LTE", "ICG-2515-NR"]),

    # ---------------------------------------------------------------- industrial / cellular
    ("Teltonika Networks", "RUT / RUTX / RUTM / RUTC", IND, "Industrial / desktop", C, WEB,
     ["RUT200", "RUT241", "RUT956", "RUTX10", "RUTX11", "RUTX12", "RUTX14", "RUTX50", "RUTM50", "RUTM52", "RUTC50",
      "RUTC41", "RUTM16", "RUTM20"]),
    ("Teltonika Networks", "RUT / RUTX / RUTM", IND, "Industrial / desktop", C, KNOWN,
     ["RUT260", "RUT271", "RUT276", "RUT281", "RUT300", "RUT301", "RUT360", "RUT361", "RUT906", "RUT951", "RUT976",
      "RUTX08", "RUTX09", "RUTM08", "RUTM09", "RUTM10", "RUTM11", "RUTM30", "RUTM31", "RUTM51", "RUTM54"]),
    ("Teltonika Networks", "Older", IND, "Industrial / desktop", EOS, KNOWN,
     ["RUT230", "RUT240", "RUT850", "RUT900", "RUT950", "RUT955"]),

    ("MOXA", "EDR secure routers", IND, INDF, C, WEB,
     ["EDR-810-2GSFP", "EDR-810-VPN-2GSFP", "EDR-G903", "EDR-G9010-VPN-2MGSFP", "EDR-G9010-VPN-2MGSFP-HV"]),
    ("MOXA", "EDR secure routers", IND, INDF, C, KNOWN, ["EDR-8010", "EDR-G9004", "EDR-G9008"]),
    ("MOXA", "EDR (older)", IND, INDF, EOS, WEB, ["EDR-G902"]),
    ("MOXA", "TN rail routers / OnCell cellular routers", IND, INDF, C, KNOWN,
     ["TN-4900", "TN-4908", "OnCell G3150A-LTE", "OnCell G3470A-LTE", "OnCell G4302-LTE4", "OnCell 3120-LTE-1"]),

    ("Westermo", "Merlin cellular routers", IND, INDF, C, WEB, ["Merlin 4400", "Merlin 4600"]),
    ("Westermo", "Lynx L3 / RedFox / Falcon", IND, INDF, C, WEB, ["Lynx 3510", "RedFox", "Falcon"]),
    ("Westermo", "Industrial routers", IND, INDF, C, KNOWN, ["MRD-305", "MRD-355", "MRD-455", "Ibex-1000"]),

    ("Advantech Co. Ltd", "ICR industrial cellular routers", IND, INDF, C, WEB, ["ICR-2031", "ICR-3241", "ICR-4461"]),
    ("Advantech Co. Ltd", "ICR industrial cellular routers", IND, INDF, C, KNOWN,
     ["ICR-2431", "ICR-2701", "ICR-3211", "ICR-4161", "ICR-4401", "ICR-4453"]),

    ("Red Lion Controls", "Sixnet IndustrialPro SN-6000", IND, INDF, C, WEB,
     ["SN-6600-VZ", "SN-6900-AM", "SN-6900-VZ", "SN-6921-AM", "SN-6921-VZ"]),
    ("Red Lion Controls", "Sixnet SN-6000 (older)", IND, INDF, EOS, WEB, ["SN-6600-VZ-MX", "SN-6901-EU"]),
    ("Red Lion Controls", "RAM / FlexEdge", IND, INDF, C, KNOWN, ["RAM-6900", "RAM-9000", "FlexEdge DA50A", "FlexEdge DA70A"]),

    ("Phoenix Contact (including Hirschmann)", "mGuard security routers", IND, INDF, C, WEB,
     ["FL MGUARD RS2000", "FL MGUARD RS2005", "FL MGUARD RS4000", "FL MGUARD RS4004", "FL MGUARD PCI4000",
      "TC ROUTER 4202T-4G EU"]),
    ("Phoenix Contact (including Hirschmann)", "mGuard security routers", IND, INDF, C, KNOWN,
     ["FL MGUARD 1102", "FL MGUARD 1105", "FL MGUARD 2102", "FL MGUARD 2105", "FL MGUARD 4102", "FL MGUARD 4302",
      "FL MGUARD RS4000 4G", "FL MGUARD GT/GT"]),

    ("Belden", "Hirschmann EAGLE firewalls / routers", IND, INDF, C, WEB,
     ["EAGLE20-0400", "EAGLE30-0402", "EAGLE40-4F", "EAGLE40-03", "EAGLE40-07"]),

    ("Lantronix", "Cellular IoT gateways", IND, INDF, C, WEB, ["X300", "X303", "X304"]),
    ("Lantronix", "Cellular routers", IND, INDF, C, KNOWN, ["X400", "FOX3", "LM1200"]),

    # ---------------------------------------------------------------- vendors in neither list (added on request)
    ("Ciena", "Routers (5G / metro / aggregation)", SP, RACK, C, WEB,
     ["3948", "5144", "5164", "5166", "5168", "5169", "8180", "8190", "8192"]),

    ("WatchGuard", "Firebox T (tabletop)", SEC, DESK, C, WEB,
     ["T25", "T25-W", "T45", "T85", "T115", "T125", "T145", "T185"]),
    ("WatchGuard", "Firebox M (rackmount)", SEC, RACK, C, WEB,
     ["M290", "M390", "M590", "M690", "M295", "M395", "M495", "M595", "M695", "M4800", "M5800"]),
    ("WatchGuard", "Firebox (older)", SEC, "Desktop / rack", EOS, WEB,
     ["T20", "T20-W", "T40", "T40-W", "T80", "M270", "M370", "M470", "M570", "M670"]),

    ("Barracuda", "CloudGen Firewall F-Series", SEC, "Desktop / rack", C, WEB, ["F12", "F18", "F400", "F600", "F800", "F1000"]),
    ("Barracuda", "CloudGen Firewall F-Series", SEC, "Desktop / rack", C, KNOWN,
     ["F80", "F82", "F180", "F183", "F280", "F380", "F900"]),
    ("Barracuda", "CloudGen Firewall T-Series", SEC, "Desktop / rack", C, KNOWN, ["T100", "T200", "T400", "T600", "T900"]),

    ("Cradlepoint", "Branch routers", SDW, DESK, C, WEB, ["E100", "E300", "E3000", "W1850"]),
    ("Cradlepoint", "Branch routers", SDW, DESK, C, KNOWN, ["E102", "E400", "S700"]),
    ("Cradlepoint", "Mobile / ruggedized routers", IND, INDF, C, WEB, ["R1900", "R2100", "R920", "R980", "IBR1700"]),
    ("Cradlepoint", "Older", SDW, DESK, EOS, WEB, ["AER2200", "IBR900"]),

    ("Digi", "Enterprise (EX)", SDW, DESK, C, WEB, ["EX15", "EX50"]),
    ("Digi", "Industrial (IX)", IND, INDF, C, WEB, ["IX10", "IX15", "IX20", "IX30", "IX40"]),
    ("Digi", "Transportation (TX)", IND, INDF, C, WEB, ["TX40"]),
    ("Digi", "Transportation (TX) / Connect IT", IND, INDF, C, KNOWN,
     ["TX54", "TX64", "Connect IT 4", "Connect IT 16", "Connect IT 48"]),
    ("Digi", "TransPort WR (older)", IND, INDF, EOS, KNOWN, ["WR11", "WR21", "WR31", "WR44", "WR54"]),

    ("Sierra Wireless", "AirLink routers (now Semtech)", IND, INDF, C, WEB,
     ["LX40", "LX60", "MG90", "RV50X", "RV55", "RX55", "XR60", "XR80", "XR90", "EX400", "MP70", "RX400"]),
    ("Sierra Wireless", "AirLink (older)", IND, INDF, EOS, KNOWN, ["MG41", "ES450", "GX450"]),

    ("Forcepoint", "Network Security Platform (NGFW)", SEC, "Desktop / rack", C, WEB,
     ["N60", "N60L", "N61", "N120", "N120L", "N120W", "N120WL", "N125L", "N130", "N130W", "N130WL", "N352", "N355",
      "N1202", "N2201", "N2205", "N2210", "N2301", "N2305", "N2310", "N3401", "N3405", "N3410", "N3505", "N3510"]),
    ("Forcepoint", "NGFW (older)", SEC, "Desktop / rack", EOS, WEB,
     ["1101", "1105", "2101", "2105", "3301", "3305", "6205", "321", "325", "1035", "1065", "1401", "1402"]),

    ("Riverbed", "SteelHead CX (80 series)", SDW, "1RU-2RU", C, WEB, ["CX580", "CX780", "CX3080", "CX5080", "CX7080"]),
    ("Riverbed", "SteelHead CX (70 series, older)", SDW, "1RU-2RU", EOS, WEB,
     ["CX570", "CX770", "CX3070", "CX5070", "CX7070"]),
    ("Riverbed", "SteelConnect SD-WAN gateways", SDW, "Desktop / rack", EOS, WEB, ["SDI-2030", "SDI-5030"]),
    ("Riverbed", "SteelConnect SD-WAN gateways", SDW, "Desktop / rack", EOS, KNOWN, ["SDI-130", "SDI-330", "SDI-1030"]),

    ("Cato Networks", "Socket SD-WAN appliances", SDW, "Desktop / 1RU", C, WEB,
     ["X1500", "X1600", "X1600-LTE", "X1700", "X1700B"]),
]
