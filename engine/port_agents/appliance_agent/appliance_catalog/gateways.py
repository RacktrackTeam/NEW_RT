"""Gateway catalogue: VoIP / media gateways, session border controllers, analog (ATA) gateways and
industrial protocol gateways. SD-WAN, cellular and branch gateways stay in router_catalog (they route)."""
from common import C, CHAS, DESK, DIN, EOS, EOSUP, KNOWN, RACK, WEB, expand

MG, SBC, ATA, IND = "VoIP / media gateway", "Session border controller", "Analog (FXS) gateway / ATA", "Protocol / IoT gateway"

GROUPS = [
    ("AudioCodes", "Mediant (gateway / E-SBC)", MG, "Desktop / 1RU", C, KNOWN,
     ["Mediant 500", "Mediant 500L", "Mediant 500Li", "Mediant 502", "Mediant 800B", "Mediant 800C", "Mediant 1000B",
      "Mediant 2600", "Mediant 2600B", "Mediant 4000", "Mediant 4000B", "Mediant 9000", "Mediant 9030", "Mediant 9080"]),
    ("AudioCodes", "Mediant (older)", MG, RACK, EOS, WEB, ["Mediant 1000", "Mediant 2000", "Mediant 3000", "Mediant 800"]),
    ("AudioCodes", "MediaPack", ATA, "Desktop / 1RU", C, KNOWN,
     ["MP-112", "MP-114", "MP-118", "MP-124", "MP-202", "MP-204", "MP-1288"]),
    ("Cisco", "VG400 series", ATA, "1RU-2RU", C, WEB, ["VG400", "VG410", "VG420"]),
    ("Cisco", "VG450", ATA, RACK, C, WEB, ["VG450"]),
    ("Cisco", "VG300 series", ATA, RACK, EOS, WEB, ["VG310", "VG320", "VG350"]),
    ("Cisco", "VG200 / VG224 (older)", ATA, RACK, EOSUP, WEB, ["VG202XM", "VG204XM", "VG224"]),
    ("Cisco", "ATA", ATA, DESK, C, KNOWN, ["ATA191", "ATA192", "ATA187", "ATA186"]),
    ("Ribbon", "SBC", SBC, RACK, C, KNOWN,
     ["SBC 1000", "SBC 2000", "SBC 5100", "SBC 5110", "SBC 5200", "SBC 5210", "SBC 5400", "SBC 7000"]),
    ("Ribbon", "EdgeMarc", SBC, "Desktop / 1RU", C, KNOWN,
     ["EdgeMarc 2900A", "EdgeMarc 4550", "EdgeMarc 4808", "EdgeMarc 6000", "EdgeMarc 7301"]),
    ("Oracle", "Acme Packet", SBC, RACK, C, WEB,
     ["Acme Packet 1100", "Acme Packet 3900", "Acme Packet 3950", "Acme Packet 4600", "Acme Packet 4900",
      "Acme Packet 6350"]),
    ("Oracle", "Acme Packet (older)", SBC, RACK, EOS, WEB, ["Acme Packet 6100", "Acme Packet 6300"]),
    ("Oracle", "Acme Packet (older)", SBC, RACK, EOSUP, KNOWN, ["Acme Packet 3810", "Acme Packet 3820", "Acme Packet 4250",
                                                               "Acme Packet 4500"]),
    ("Patton", "SmartNode", MG, "Desktop / 1RU", C, KNOWN,
     ["SN200", "SN4110", "SN4112", "SN4114", "SN4118", "SN4120", "SN4131", "SN4141", "SN4171", "SN4316", "SN4324",
      "SN4332", "SN4552", "SN4638", "SN4639", "SN4660", "SN4670", "SN4741", "SN4924", "SN4960", "SN4970",
      "SN5300", "SN5400", "SN5480", "SN5490", "SN5531", "SN5570", "SN10100", "SN10200"]),
    ("Sangoma", "Vega", MG, "Desktop / 1RU", C, KNOWN,
     ["Vega 50", "Vega 60", "Vega 60G", "Vega 100G", "Vega 200G", "Vega 400", "Vega 3000G", "Vega 5000"]),
    ("Grandstream", "GXW gateways", MG, "Desktop / 1RU", C, KNOWN,
     ["GXW4104", "GXW4108", "GXW4216", "GXW4224", "GXW4232", "GXW4248", "GXW4501", "GXW4502", "GXW4504",
      "GXW4004", "GXW4008"]),
    ("Grandstream", "HandyTone ATA", ATA, DESK, C, KNOWN, ["HT801", "HT802", "HT812", "HT814", "HT818", "HT841", "HT881"]),
    ("Yeastar", "TA / TE / TG gateways", MG, "Desktop / 1RU", C, KNOWN,
     ["TA100", "TA200", "TA400", "TA800", "TA410", "TA810", "TA1600", "TA1610", "TA2400", "TA2410", "TA3200", "TA3210",
      "TE100", "TE200", "TG100", "TG200", "TG400", "TG800", "TG1600"]),
    ("Mediatrix (Media5)", "Mediatrix gateways / Sentinel", MG, "Desktop / 1RU", C, KNOWN,
     ["Mediatrix 4102S", "Mediatrix 4104", "Mediatrix 3208", "Mediatrix 3216", "Mediatrix 3301", "Mediatrix 3308",
      "Mediatrix 3401", "Mediatrix 3632", "Mediatrix 3741", "Mediatrix C711", "Mediatrix C731", "Mediatrix S7",
      "Sentinel 100", "Sentinel 400"]),
    ("Dinstar", "DAG / MTG", MG, "Desktop / 1RU", C, KNOWN,
     ["DAG1000", "DAG2000", "DAG3000", "MTG200", "MTG600", "MTG1000", "MTG2000", "MTG3000"]),
    ("Synway", "SMG", MG, "Desktop / 1RU", C, KNOWN, ["Synway SMG (models: verify)"]),
    ("OpenVox", "VoIP gateways", MG, "Desktop / 1RU", C, KNOWN, ["OpenVox gateway (models: verify)"]),
    ("Avaya", "Branch gateways", MG, RACK, EOS, KNOWN, ["G250", "G350", "G430", "G450", "G650"]),
    ("Adtran", "Total Access 900", MG, RACK, EOS, KNOWN, ["Total Access 908e", "Total Access 916e", "Total Access 924e"]),
    ("Ingate", "SIParator", SBC, "Desktop / 1RU", C, KNOWN, ["SIParator S21", "SIParator S52", "SIParator S95",
                                                            "SIParator S97", "SIParator S98"]),
    ("Moxa", "MGate", IND, DIN, C, KNOWN,
     ["MGate MB3170", "MGate MB3180", "MGate MB3270", "MGate MB3280", "MGate MB3480", "MGate MB3660",
      "MGate 5101-PBM-MN", "MGate 5102-PBM-PN", "MGate 5103", "MGate 5105-MB-EIP", "MGate 5109", "MGate 5111",
      "MGate 5114", "MGate 5118", "MGate 5119", "MGate 5121", "MGate 5122", "MGate 5123", "MGate 5135",
      "MGate 5217", "MGate W5108"]),
    ("HMS Networks", "Anybus", IND, DIN, C, KNOWN,
     ["Anybus Communicator", "Anybus X-gateway", "Anybus PROFINET to Modbus TCP gateway"]),
]


GROUPS += [
    # added 2026-10-15 (gap check)
    ("Cisco", "SPA / ATA (small business)", ATA, DESK, EOS, WEB, ["SPA112", "SPA122", "SPA8000", "SPA2102", "SPA3102"]),
    ("Poly (Obihai)", "OBi ATAs", ATA, DESK, EOS, WEB, ["OBi202", "OBi300", "OBi302", "OBi200", "OBi212"]),
    ("beroNet", "beroNet gateways", MG, "Desktop / 1RU", C, KNOWN, ["BF400", "BF1600", "BF4000", "BF6400"]),
    ("Digium (Sangoma)", "Asterisk gateways (end of life)", MG, "1RU", EOSUP, KNOWN, ["G100", "G200", "G400", "G800"]),
    ("Huawei", "eSpace IAD", ATA, "Desktop / 1RU", EOS, KNOWN, ["IAD132E", "IAD208E", "IAD1224", "IAD196", "IAD104H"]),
    ("Teles", "vGATE / iGATE", MG, "Desktop / 1RU", EOS, KNOWN, ["vGATE Desk", "vGATE 19", "iGATE 4", "iGATE 8"]),
    ("Quintum", "Tenor (end of life)", MG, "1RU", EOSUP, KNOWN, ["Tenor AX", "Tenor DX", "Tenor CMS"]),
    ("Linksys", "ATA (end of life)", ATA, DESK, EOSUP, KNOWN, ["PAP2T", "SPA2102", "SPA3102"]),
    ("ProSoft Technology", "PLX gateways", IND, DIN, C, KNOWN, ["PLX31-EIP-MBS", "PLX31-MBTCP-MBS", "PLX32-EIP-MBTCP",
                                                                "PLX35-NB2", "PLX51-DF1-ENI"]),
    ("Red Lion", "Data Station", IND, DIN, C, KNOWN, ["DA10D", "DA30D", "DA50A", "DA70A"]),
    ("Advantech", "ECU / WISE gateways", IND, DIN, C, KNOWN, ["ECU-1251", "ECU-1152", "ECU-4553", "ECU-4784",
                                                              "WISE-6610", "WISE-710"]),
]


SDW, CELL, SMBG = "SD-WAN / edge gateway", "Cellular / industrial gateway", "Business / SMB gateway"

GROUPS += [
    # added 2026-10-16: every gateway vendor, not only VoIP and protocol gateways
    ("Fortinet", "FortiExtender", CELL, "Desktop / outdoor", C, "Vendor site (scraped)",
     ["FEX-101F", "FEX-201F", "FEX-202F", "FEX-211G", "FEX-212F", "FEX-311F", "FEX-511F", "FEX-511G", "FEX-201E",
      "FEX-211E"]),
    ("Fortinet", "FortiVoice gateways", MG, "Desktop / 1RU", C, "Vendor site (scraped)",
     ["FVG-GO04", "FVG-GO08", "FVG-GS04", "FVG-GS16", "FVG-GS24", "FVG-GT01", "FVG-GT02"]),
    ("Cisco Meraki", "MG cellular gateways", CELL, "Desktop / outdoor", C, "Vendor site (scraped)",
     ["MG21", "MG21E", "MG41", "MG41E", "MG51", "MG51E", "MG52", "MG52E"]),
    ("Teltonika Networks", "TRB gateways", CELL, "Industrial / DIN", C, WEB,
     ["TRB140", "TRB141", "TRB142", "TRB143", "TRB145", "TRB245", "TRB255", "TRB500"]),
]


def _router_rows():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "router_catalog"))
    import full_models as fm   # noqa: E402
    return fm.rows()


def router_gateways():
    """Routers their vendors sell as gateways, as listed in the router catalogue: SD-WAN edges
    (VeloCloud Edge, Versa CSG, Meraki Z, Aruba SD-Branch gateways ...), cellular / industrial
    gateways (Cradlepoint, Digi, Teltonika, MOXA OnCell, Advantech ICR ...), and business gateways
    (TP-Link Omada, UniFi gateways, D-Link Nuclias, Ruijie EG, Nomadix ...)."""
    import re
    same = {"Advantech Co. Ltd": "Advantech", "MOXA": "Moxa", "Red Lion Controls": "Red Lion"}   # one spelling per vendor
    out = []
    for v, s, m, cat, form, st, src, var in _router_rows():
        v = same.get(v, v)
        text = f"{s} {m}"
        if cat == "SD-WAN appliance":
            out.append((v, s, m, SDW, form, st, src, var))
        elif cat == "Industrial router":
            out.append((v, s, m, CELL, form, st, src, var))
        elif re.search(r"gateway|unifi|omada|nuclias", text, re.I) or (v == "Ubiquiti" and re.match(r"U(DM|CG|XG|DR|X)|EFG|USG", m)):
            out.append((v, s, m, SMBG, form, st, src, var))
    return out


def rows():
    base = expand(GROUPS)
    seen = {(r[0], r[2].upper()) for r in base}
    return base + [r for r in router_gateways() if (r[0], r[2].upper()) not in seen]
