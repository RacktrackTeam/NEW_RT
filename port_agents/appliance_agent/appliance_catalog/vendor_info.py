"""Per vendor: is the make/model printed on the unit (can OCR read it?) and where templates come from.

printed  - Yes: model text on the ported face (OCR reads vendor + model, as for routers)
           Partial: vendor logo / series on the face, model only on an LCD, a pull-out tag or the
                    rear label - OCR gives the vendor, the model comes from the port layout
                    (compare the photo with the vendor's templates) or is typed
           No: white-box hardware (OEM server / appliance) - typed, or matched by port layout
where    - where the text is
sources  - where front-panel templates come from, best first
domains  - vendor domains for the site-restricted search (router_mapper/official.py DOMAINS)
desktop_ports - which face the ports are on for the vendor's desktop models

These are product knowledge from vendor photos and documents seen while building the catalogue;
check each against the first real photo of the vendor.
"""
DS, HW, QSG, PP = "data sheet PDF (front / back figures)", "hardware / installation guide (front panel figure, ports named)", \
    "quick start guide (port diagram)", "product page images"

FIREWALL = {
    "Fortinet": dict(printed="Yes", where="'FortiGate 200F' printed on the front (rack) / top or front (desktop)",
                     sources=[DS, QSG, PP], domains=["fortinet.com", "docs.fortinet.com"], desktop_ports="Back"),
    "Palo Alto Networks": dict(printed="Yes", where="'PA-3220' printed front left; desktop on the front",
                               sources=[HW, DS, PP], domains=["paloaltonetworks.com", "docs.paloaltonetworks.com"],
                               # PA-400 hardware reference: data ports, MGT, console and USB on the front
                               # (review 2); PA-500 is the same compact form (review open check)
                               desktop_ports="Front"),
    "Check Point": dict(printed="Yes", where="model number on the front bezel ('6200', 'Quantum Force 9100'); Spark on top",
                        sources=[HW, DS, PP], domains=["checkpoint.com", "sc1.checkpoint.com"], desktop_ports="Back"),
    "SonicWall": dict(printed="Yes", where="'NSa 2700' / 'TZ370' printed on the front", sources=[DS, QSG, PP],
                      domains=["sonicwall.com"], desktop_ports="Back"),
    "Sophos": dict(printed="Yes", where="'XGS 2100' printed on the front", sources=[DS, HW, PP],
                   domains=["sophos.com", "docs.sophos.com"], desktop_ports="Back"),
    "WatchGuard": dict(printed="Yes", where="'Firebox M470' / 'T45' printed on the front", sources=[HW, DS, PP],
                       domains=["watchguard.com"], desktop_ports="Back"),
    "Juniper": dict(printed="Yes", where="'SRX345' printed on the front", sources=[HW, DS, PP],
                    domains=["juniper.net"], desktop_ports="Front"),
    "Cisco": dict(printed="Yes", where="'Firepower 2110' / 'ASA 5525-X' / 'Secure Firewall 3110' on the front; Meraki: model small on the front",
                  sources=[HW, DS, PP], domains=["cisco.com", "documentation.meraki.com", "meraki.cisco.com"],
                  desktop_ports="Back"),
    "Forcepoint": dict(printed="Partial", where="Forcepoint logo on the front; model on the rear / bottom label",
                       sources=[HW, DS], domains=["forcepoint.com", "help.forcepoint.com"], desktop_ports="Back (check)"),
    "Barracuda": dict(printed="Partial", where="Barracuda logo; model (F180, F380) on a front badge on some, label on others",
                      sources=[DS, HW, PP], domains=["barracuda.com", "campus.barracuda.com"], desktop_ports="Back"),
    "Stormshield": dict(printed="Yes", where="'SN310' printed on the front", sources=[DS, PP],
                        domains=["stormshield.com", "documentation.stormshield.eu"], desktop_ports="Back (check)"),
    "Zyxel": dict(printed="Yes", where="'USG FLEX 200' printed on the front", sources=[DS, QSG, PP],
                  domains=["zyxel.com"], desktop_ports="Front"),
    "Hillstone Networks": dict(printed="Yes", where="model on the front ('SG-6000-A2600')", sources=[DS, HW],
                               domains=["hillstonenet.com"], desktop_ports="Front"),
    "Huawei": dict(printed="Yes", where="'USG6525E' label on the front", sources=[HW, DS],
                   domains=["e.huawei.com", "support.huawei.com"], desktop_ports="Front"),
    "H3C": dict(printed="Yes", where="model on the front ('F1000-AK180')", sources=[HW, DS], domains=["h3c.com"]),
    "Sangfor": dict(printed="Partial", where="Sangfor logo; model on the rear label", sources=[DS, PP], domains=["sangfor.com"]),
    "LANCOM Systems": dict(printed="Yes", where="model on the front", sources=[DS, PP], domains=["lancom-systems.com"]),
    "Allied Telesis": dict(printed="Yes", where="model on the front", sources=[DS, HW], domains=["alliedtelesis.com"]),
    "Belden": dict(printed="Yes", where="'EAGLE40' on the front", sources=[DS], domains=["hirschmann.com", "belden.com"]),
    "Deciso": dict(printed="Partial", where="OPNsense logo; model on the bottom label", sources=[PP, DS],
                   domains=["shop.opnsense.com", "deciso.com"], desktop_ports="Front"),
    "Netgate": dict(printed="Partial", where="Netgate logo; model on the bottom label", sources=[PP, HW],
                    domains=["netgate.com", "docs.netgate.com"], desktop_ports="Front"),
    "Firewalla": dict(printed="No", where="logo only", sources=[PP], domains=["firewalla.com"], desktop_ports="Back"),
    "NETGEAR": dict(printed="Yes", where="model on the front", sources=[DS, PP], domains=["netgear.com"], desktop_ports="Back"),
    "Kerio": dict(printed="Partial", where="logo; model on label", sources=[PP, DS], domains=["gfi.com", "kerio.com"], desktop_ports="Back"),
    "Untangle / Arista": dict(printed="Partial", where="logo; model on label", sources=[PP], domains=["untangle.com", "edge.arista.com"]),
    "Sophos (Cyberoam)": dict(printed="Yes", where="'CR50iNG' printed on the front", sources=[DS, PP],
                              domains=["sophos.com", "cyberoam.com"], desktop_ports="Front"),
    "Protectli": dict(printed="No", where="no model text on the ported face", sources=[PP], domains=["protectli.com"],
                      desktop_ports="Front / back (both)"),
    "Clavister": dict(printed="Yes", where="model on the front", sources=[DS, PP], domains=["clavister.com"]),
    "Ruijie": dict(printed="Yes", where="model on the front", sources=[DS, HW], domains=["ruijienetworks.com"]),
    "NSFOCUS": dict(printed="Partial", where="logo; model on label", sources=[DS, HW], domains=["nsfocusglobal.com"]),
    "Array Networks": dict(printed="Yes", where="model on the front", sources=[DS, PP], domains=["arraynetworks.com"]),
    "GajShield": dict(printed="Yes", where="GajShield logo + model on the front", sources=[DS, PP], domains=["gajshield.com"]),
    "Arista Networks": dict(printed="Yes", where="'ARISTA Q12' on the front", sources=[DS, PP], domains=["arista.com", "edge.arista.com"]),
    "AhnLab": dict(printed="Yes", where="'AhnLab XTG100' on the front", sources=[DS, PP], domains=["ahnlab.com"]),
    "Trend Micro": dict(printed="Yes", where="'TippingPoint' + model on the front", sources=[HW, DS],
                        domains=["trendmicro.com", "docs.trendmicro.com"]),
    "Trellix (McAfee)": dict(printed="Partial", where="logo; model on label", sources=[HW, DS], domains=["trellix.com", "docs.trellix.com"]),
    "Securepoint": dict(printed="Partial", where="logo; model on label", sources=[PP, DS], domains=["securepoint.de"], desktop_ports="Front"),
    "D-Link": dict(printed="Yes", where="'NetDefend DFL-860E' on the front", sources=[DS, PP], domains=["dlink.com"], desktop_ports="Front"),
    "Topsec": dict(printed="Partial", where="logo; model on label", sources=[DS], domains=["topsec.com.cn"]),
    "DPtech": dict(printed="Partial", where="logo; model on label", sources=[DS], domains=["dptech.com"]),
    "Qi-AnXin": dict(printed="Partial", where="QAX logo; model on label", sources=[DS], domains=["en.qianxin.com", "qianxin.com"]),
    "Venustech": dict(printed="Partial", where="Venusense logo; model on label", sources=[DS], domains=["venusense.com", "venustech.com.cn"]),
    "genua": dict(printed="Partial", where="logo; model on label", sources=[DS, PP], domains=["genua.de"]),
    "Siemens": dict(printed="Yes", where="'SCALANCE SC646-2C' on the front", sources=[HW, DS], domains=["siemens.com", "support.industry.siemens.com"]),
    "Tosibox": dict(printed="Yes", where="'Lock 500' on the top / front", sources=[DS, PP], domains=["tosibox.com"]),
    "Ubiquiti": dict(printed="Partial", where="logo + small screen; model on label", sources=[PP, DS, QSG],
                     domains=["ui.com", "help.ui.com", "store.ui.com", "techspecs.ui.com"], desktop_ports="Back"),
    "Phoenix Contact (including Hirschmann)": dict(printed="Yes", where="model on the front label", sources=[DS, HW], domains=["phoenixcontact.com"]),
    "MOXA": dict(printed="Yes", where="'EDR-G9010' on the front", sources=[DS, QSG], domains=["moxa.com"]),
}

LOAD_BALANCER = {
    "F5": dict(printed="Partial", where="F5 logo + LCD on the front; the model is shown on the LCD menu / rear label, "
                                        "not printed on the bezel. Licence tiers (i5600 / i5800) are the same box",
               sources=[HW, DS, PP], domains=["f5.com", "techdocs.f5.com", "clouddocs.f5.com", "my.f5.com"]),
    "NetScaler (Citrix)": dict(printed="Partial", where="'NetScaler' / 'Citrix' logo on the bezel, LCD on MPX/SDX; "
                                                        "model family on some bezels, the exact model on the rear label",
                               sources=[HW, DS], domains=["docs.netscaler.com", "netscaler.com", "docs.citrix.com"]),
    "A10 Networks": dict(printed="Yes", where="'Thunder 3350S' printed front left", sources=[HW, DS, PP],
                         domains=["a10networks.com", "documentation.a10networks.com"]),
    "Radware": dict(printed="Yes", where="'Alteon' + model ('D-6024') on the front", sources=[DS, HW],
                    domains=["radware.com", "support.radware.com"]),
    "Kemp (Progress)": dict(printed="Partial", where="Kemp / LoadMaster logo; OEM hardware, model on the rear label",
                            sources=[DS, PP], domains=["kemptechnologies.com", "docs.progress.com", "progress.com"]),
    "Array Networks": dict(printed="Yes", where="'APV' + model on the front", sources=[DS, PP], domains=["arraynetworks.com"]),
    "Fortinet": dict(printed="Yes", where="'FortiADC 200F' printed on the front", sources=[DS, QSG],
                     domains=["fortinet.com", "docs.fortinet.com"]),
    "Barracuda": dict(printed="Partial", where="Barracuda logo; model on label", sources=[DS, PP],
                      domains=["barracuda.com", "campus.barracuda.com"]),
    "Loadbalancer.org": dict(printed="No", where="white-box server (logo only)", sources=[PP, DS], domains=["loadbalancer.org"]),
    "Cisco": dict(printed="Yes", where="'ACE 4710' on the front", sources=[HW, DS], domains=["cisco.com"]),
    "Brocade": dict(printed="Yes", where="'ServerIron ADX' on the front", sources=[HW], domains=["broadcom.com"]),
    "Piolink": dict(printed="Yes", where="'PAS-K' + model on the front", sources=[DS, PP], domains=["piolink.com"]),
    "HAProxy": dict(printed="Partial", where="ALOHA logo; model on label", sources=[PP, DS], domains=["haproxy.com"]),
    "Sangfor": dict(printed="Partial", where="Sangfor logo; model on the rear label", sources=[DS, PP], domains=["sangfor.com"]),
    "Mushroom Networks": dict(printed="Partial", where="Mushroom Networks logo; model on label", sources=[DS, PP], domains=["mushroomnetworks.com"]),
    "Linksys": dict(printed="Yes", where="model on the front", sources=[DS, PP], domains=["linksys.com"]),
    "Grandstream": dict(printed="Yes", where="'GWN7003' on the front", sources=[DS, QSG, PP], domains=["grandstream.com"]),
    "Topsec": dict(printed="Partial", where="logo; model on label", sources=[DS], domains=["topsec.com.cn"]),
    "D-Link": dict(printed="Yes", where="model on the front", sources=[DS, PP], domains=["dlink.com"]),
    "Edgenexus": dict(printed="Partial", where="logo; model on label", sources=[PP, DS], domains=["edgenexus.io"]),
    "Elfiq": dict(printed="Yes", where="logo + model on the front", sources=[DS], domains=["elfiq.com"]),
    "Ecessa": dict(printed="Partial", where="logo; model on label", sources=[DS], domains=["ecessa.com"]),
    "Coyote Point": dict(printed="Yes", where="'Equalizer' + model on the front", sources=[DS], domains=["coyotepoint.com"]),
    "SKUDONET (Zevenet)": dict(printed="No", where="white-box server", sources=[PP], domains=["skudonet.com", "zevenet.com"]),
    "Foundry / Brocade": dict(printed="Yes", where="'ServerIron' on the front", sources=[HW], domains=["broadcom.com"]),
    "Nortel": dict(printed="Yes", where="'Alteon' + model on the front", sources=[HW], domains=["radware.com"]),
    "Juniper": dict(printed="Yes", where="'DX' + model on the front", sources=[HW], domains=["juniper.net"]),
    "DPtech": dict(printed="Partial", where="logo; model on label", sources=[DS], domains=["dptech.com"]),
    "Peplink": dict(printed="Yes", where="'Balance 380' on the front", sources=[DS, PP], domains=["peplink.com", "download.peplink.com"]),
}

GATEWAY = {
    "AudioCodes": dict(printed="Yes", where="'Mediant 800' / 'MP-124' printed on the front", sources=[HW, DS, PP],
                       domains=["audiocodes.com"]),
    "Cisco": dict(printed="Yes", where="'Cisco VG310' / 'VG450' printed on the front; ATA on top",
                  sources=[HW, DS], domains=["cisco.com"]),
    "Ribbon": dict(printed="Yes", where="'SBC 5400' / 'EdgeMarc' on the front", sources=[HW, DS],
                   domains=["ribboncommunications.com", "doc.rbbn.com"]),
    "Oracle": dict(printed="Yes", where="'Acme Packet 4600' on the front", sources=[HW, DS],
                   domains=["oracle.com", "docs.oracle.com"]),
    "Patton": dict(printed="Yes", where="'SmartNode 4670' printed on the front", sources=[DS, QSG], domains=["patton.com"]),
    "Sangoma": dict(printed="Yes", where="'Vega 3000G' on the front", sources=[DS, PP], domains=["sangoma.com", "wiki.sangoma.com"]),
    "Grandstream": dict(printed="Yes", where="'GXW4216' on the front; HT on top", sources=[DS, QSG, PP],
                        domains=["grandstream.com"]),
    "Yeastar": dict(printed="Yes", where="'TA1600' on the front", sources=[DS, QSG], domains=["yeastar.com"]),
    "Mediatrix (Media5)": dict(printed="Yes", where="model on the front", sources=[DS, HW], domains=["media5corp.com"]),
    "Dinstar": dict(printed="Partial", where="logo; model on the rear label on some", sources=[DS, PP], domains=["dinstar.com"]),
    "Synway": dict(printed="Partial", where="logo; model on label", sources=[DS], domains=["synway.net"]),
    "OpenVox": dict(printed="Partial", where="logo; model on label", sources=[DS, PP], domains=["openvox.cn"]),
    "Avaya": dict(printed="Yes", where="'G450' on the front", sources=[HW, DS], domains=["avaya.com", "support.avaya.com"]),
    "Adtran": dict(printed="Yes", where="'Total Access 908e' on the front", sources=[HW, DS], domains=["adtran.com", "portal.adtran.com"]),
    "Ingate": dict(printed="Yes", where="'SIParator' + model on the front", sources=[DS], domains=["ingate.com"]),
    "Quantum Networks": dict(printed="Yes", where="'QUANTUM NETWORKS' logo + 'QN-S-200' on the front", sources=[PP], domains=["zengroup.co.in"]),
    "AKOM Technologies": dict(printed="Yes", where="'AKOM' logo + 'TGL2001' on the front", sources=[PP], domains=["akomtechnologies.com"]),
    "Moxa": dict(printed="Yes", where="'MGate MB3180' on the front", sources=[DS, QSG, PP], domains=["moxa.com"]),
    "HMS Networks": dict(printed="Yes", where="'Anybus' + product on the front label", sources=[DS, PP],
                         domains=["hms-networks.com", "anybus.com"]),
    "Poly (Obihai)": dict(printed="Yes", where="'OBi302' on the top", sources=[PP, QSG], domains=["poly.com", "hp.com"]),
    "beroNet": dict(printed="Yes", where="'beroNet' + model on the front", sources=[DS, PP], domains=["beronet.com"]),
    "Digium (Sangoma)": dict(printed="Yes", where="'Digium G400' on the front", sources=[DS], domains=["sangoma.com"]),
    "Huawei": dict(printed="Yes", where="'IAD' + model on the front", sources=[HW, DS], domains=["huawei.com", "support.huawei.com"]),
    "Teles": dict(printed="Yes", where="model on the front", sources=[DS], domains=["teles.com"]),
    "Quintum": dict(printed="Yes", where="'Tenor' + model on the front", sources=[DS], domains=["quintum.com"]),
    "Linksys": dict(printed="Yes", where="model on the top", sources=[PP], domains=["linksys.com"]),
    "ProSoft Technology": dict(printed="Yes", where="'PLX31' on the front label", sources=[DS, QSG], domains=["prosoft-technology.com"]),
    "Red Lion": dict(printed="Yes", where="'DA30D' on the front", sources=[DS, PP], domains=["redlion.net"]),
    "Advantech": dict(printed="Yes", where="'ECU-1251' on the front", sources=[DS, PP], domains=["advantech.com"]),
    "Lantronix": dict(printed="Partial", where="LANTRONIX logo on the front; model on the label (often not on the face)",
                      sources=[DS, QSG, PP], domains=["lantronix.com", "docs.lantronix.com"]),
}

GENERAL_BASIS = [
    ("Model printed (Yes)", "OCR reads vendor and model off the photo, as for routers: the same panel_ocr + "
                            "switch_identify step loaded with this catalogue."),
    ("Model not printed (Partial / No)", "OCR gives the vendor (logo). The model is found by the port layout: the photo's "
                                         "ports (count of RJ45 / SFP / QSFP, rows, management ports) are compared with "
                                         "the saved templates of that vendor's models; or the model is typed / taken "
                                         "from the inventory (asset tag, hostname)."),
    ("Same hardware", "Licence tiers that are the same box (F5 i5600 / i5800, r5600 / r5800 / r5900; NetScaler "
                      "MPX 8905-8930) share one template: column 'Same hardware as'. A photo can only tell the "
                      "hardware family; the tier is a licence."),
    ("Template sources", "1. Hardware / installation guide: front-panel figure with every port named (best: gives "
                         "port names). 2. Data sheet PDF: front and back figures. 3. Quick start guide. 4. Product page "
                         "images. 5. Template store / open web. Searched with site: restricted to the vendor domains "
                         "listed per vendor, as the router agent does."),
    ("Ports on the back", "Most desktop firewalls (FortiGate 40F-90G, PA-400, Spark, TZ, T-series, XGS desktop) "
                          "and many small gateways have their ports on the back: only a photo of the back can be "
                          "mapped; a front photo is 'cannot map'. Column 'Ports on'."),
    ("Chassis", "VIPRION / VELOS / NetScaler SDX 22000 / Palo Alto PA-7000 / SRX5000 / Acme 6300 are chassis "
                "with blades: one template per blade, the chassis template holds the slots (like modular routers)."),
]


def fill_missing(table, vendors):
    """Vendors taken over from the router catalogue (gateways, multi-WAN routers) without an entry
    here: their domains from router_mapper.official.DOMAINS; 'printed' left to check."""
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    try:
        from router_mapper.official import DOMAINS as RD
    except Exception:
        RD = {}
    for v in vendors:
        if v not in table:
            table[v] = dict(printed="Check", where="vendor name usually on the front; check the first photo",
                            sources=[DS, PP], domains=RD.get(v, []))
    return table


def _one_spelling():
    """Vendor names as the catalogues now spell them (review 2: one spelling per vendor in every catalogue)."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "router_catalog"))
    from review2_changes import VENDOR
    for table in (FIREWALL, LOAD_BALANCER, GATEWAY):
        for old, new in VENDOR.items():
            if old in table:
                entry = table.pop(old)
                if new in table:                           # an entry under the new name wins; its sites are added
                    table[new]["domains"] = list(dict.fromkeys(table[new].get("domains", []) + entry.get("domains", [])))
                else:
                    table[new] = entry


import os as _os
if _os.environ.get("CATALOG_REVIEW2", "1") != "0":
    _one_spelling()
else:                                                      # as before review 2 (backup copy)
    FIREWALL["Palo Alto Networks"]["desktop_ports"] = "Back (check)"
