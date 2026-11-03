"""Verification pass of 2026-10-15: every 'verify' row was searched for, first on the vendor's own site,
then on the open web (data sheets, end-of-life notices, distributor listings).

Each entry carries the link it was confirmed at. apply(kind, rows) returns the corrected rows, the
change log and {(vendor, MODEL): link} for the 'Checked at' column.

  CONFIRM  (kind, vendor, source, url, [models])           source label set to SCRAPED (vendor page) / WEB
  RENAME   (kind, vendor, old, new, source, url)
  REMOVE   (kind, vendor, model, why)                       wrong names, duplicates, guesses not found anywhere
  ADD      (kind, vendor, series, category, form, status, source, url, [models])   models found missing
  STATUS   (kind, vendor, model, status, url)
"""
from common import C, EOS, EOSUP, SCRAPED, WEB

FW, LB, GW = "firewall", "load_balancer", "gateway"

CONFIRM = [
    # ------------------------------------------------------------------ gateways
    (GW, "AudioCodes", SCRAPED, "https://audiocodes.com/media/2373/mp-1xx-and-mp-124-datasheet.pdf",
     ["MP-112", "MP-114", "MP-118", "MP-124"]),
    (GW, "AudioCodes", WEB, "https://scansource.com/technologies/suppliers/audiocodes/products",
     ["Mediant 500", "Mediant 500L", "Mediant 500Li", "Mediant 800B", "Mediant 800C", "Mediant 1000B", "Mediant 2600",
      "Mediant 4000", "Mediant 4000B", "Mediant 9000", "Mediant 9030", "Mediant 9080", "MP-202", "MP-204", "MP-1288"]),
    (GW, "Ribbon", SCRAPED, "https://publicdoc.rbbn.com/display/SBXDOC101/SBC+Core+Hardware+Installation+Guides",
     ["SBC 1000", "SBC 2000", "SBC 5100", "SBC 5110", "SBC 5200", "SBC 5210", "SBC 5400", "SBC 7000",
      "EdgeMarc 4550", "EdgeMarc 4808", "EdgeMarc 7301"]),
    (GW, "Oracle", WEB, "https://serviceexpress.com/eol-eosl-database/oem/acme/",
     ["Acme Packet 3820", "Acme Packet 4250", "Acme Packet 4500"]),
    (GW, "Patton", WEB, "https://shop.ascend.de/es/products/inalp-patton-smartnode-10200-smartmedia-sn10200a-48e-r48",
     ["SN200", "SN4112", "SN4114", "SN4118", "SN4120", "SN4131", "SN4141", "SN4171", "SN4741", "SN4924", "SN4960",
      "SN5531", "SN10100", "SN10200"]),
    (GW, "Sangoma", SCRAPED, "https://sangomakb.atlassian.net/wiki/spaces/VG/pages/32375186",
     ["Vega 50", "Vega 60G", "Vega 100G", "Vega 200G", "Vega 3000G", "Vega 5000"]),
    (GW, "Grandstream", SCRAPED, "https://documentation.grandstream.com/article-categories/gxw410x-series/",
     ["GXW4104", "GXW4108", "GXW4216", "GXW4224", "GXW4232", "GXW4248", "GXW4501", "GXW4502", "GXW4504",
      "HT801", "HT802", "HT812", "HT814", "HT818", "HT841", "HT881"]),
    (GW, "Yeastar", SCRAPED, "https://www.yeastar.com/news/yeastar-introduces-high-performance-analog-voip-gateway-series/",
     ["TA100", "TA200", "TA400", "TA800", "TA1600", "TA2400", "TA3200", "TA410", "TA810", "TG100"]),
    (GW, "Yeastar", WEB, "https://www.thetelecomspot.com/collections/all?page=260", ["TA1610", "TE100", "TE200", "TG200", "TG400"]),
    (GW, "Mediatrix (Media5)", SCRAPED, "https://media5corp.com/pages/mediatrix-4102s-en", ["Mediatrix 4102S"]),
    (GW, "Mediatrix (Media5)", WEB, "https://www.voipsupply.com/manufacturer/mediatrix/media-gateways/c7-series",
     ["Mediatrix 4104", "Mediatrix 3632", "Mediatrix C711", "Mediatrix S7", "Sentinel 100"]),
    (GW, "Dinstar", WEB, "https://www.euromobile.ru/upload/iblock/674/6742870a655edaa6e1ed2e59d68723cb.pdf",
     ["DAG1000", "DAG2000", "MTG200", "MTG1000", "MTG2000", "MTG3000"]),
    (GW, "Avaya", SCRAPED, "https://documentation.avaya.com/en-us/home/bundle/communication-manager/administeringnetworkconnectivityonavayaauracmr102x/converged-networks/AvayaGateways.html",
     ["G430", "G450"]),
    (GW, "Adtran", WEB, "https://www.hummingbirdnetworks.com/adtran-voip-gateways/",
     ["Total Access 908e", "Total Access 916e", "Total Access 924e"]),
    (GW, "Ingate", SCRAPED, "https://www.ingate.com/siparators.php",
     ["SIParator S21", "SIParator S52", "SIParator S95", "SIParator S97", "SIParator S98"]),
    (GW, "Moxa", SCRAPED, "https://www.moxa.com/product/mgate_mb3180_3280_3480.htm",
     ["MGate MB3180", "MGate MB3280", "MGate MB3480", "MGate MB3660"]),
    (GW, "Moxa", WEB, "https://technolec.fw4.be/products/industrial-edge-connectivity/protocol-gateways/modbus-tcp-gateways",
     ["MGate MB3170", "MGate MB3270", "MGate 5101-PBM-MN", "MGate 5105-MB-EIP", "MGate 5109", "MGate 5114", "MGate 5118"]),
    (GW, "HMS Networks", WEB, "https://pdf.directindustry.com/pdf/hms-networks/gateway-wireless-solutions/186080-994912.html",
     ["Anybus Communicator", "Anybus X-gateway"]),
    (GW, "beroNet", SCRAPED, "https://www.beronet.com/wp-content/uploads/2019/03/beroNet_Brochure_ISDN-Gateways.pdf",
     ["BF400", "BF1600", "BF6400"]),
    (GW, "Digium (Sangoma)", WEB, "https://www.linuxjournal.com/index%2ephp/node/1040027", ["G100", "G200"]),
    (GW, "Huawei", SCRAPED, "https://support.huawei.com/enterprise/jp/enterprise-communications/espace-iad-pid-15259",
     ["IAD132E", "IAD208E", "IAD1224", "IAD196", "IAD104H"]),
    (GW, "Cisco", WEB, "https://www.netxl.com/voip-phone-adapters/cisco-ata-191-multiplatform-analogue-telephone-adapter.md",
     ["ATA191", "ATA187", "ATA186", "SPA8000", "SPA112", "SPA122"]),
    (GW, "Cisco", SCRAPED, "https://www3-realm.cisco.com/cisco/web/support/model/tsd_hardware_phoneadapter_model_spa2102.html",
     ["SPA2102", "SPA3102"]),
    (GW, "Linksys", SCRAPED, "https://www3-realm.cisco.com/cisco/web/support/model/tsd_hardware_internetphoneadapter_model_pap2t.html",
     ["PAP2T"]),
    (GW, "Poly (Obihai)", WEB, "https://helpdesk.t38fax.com/hc/en-us/sections/360011275112", ["OBi202", "OBi300", "OBi302"]),
    (GW, "ProSoft Technology", SCRAPED, "https://marketplace.odva.org/products/527-ethernet-ip-industrial-communication-gateways",
     ["PLX31-EIP-MBS"]),
    # ------------------------------------------------------------------ load balancers
    (LB, "F5", SCRAPED, "https://techdocs.f5.com/en-us/hw-platforms/platform-guide-i5000i7000i10000i11000-series/title--platform-specifications.html",
     ["i5600", "i5800", "i5820-DF", "i7600", "i7800", "i7820-DF", "i10600", "i10800", "i11600", "i11800"]),
    (LB, "F5", SCRAPED, "https://support.f5.com/kb/en-us/products/big-ip_ltm/manuals/product/platform-i800/5.html", ["i850"]),
    (LB, "F5", WEB, "https://www.shi.com/product/32741713/F5-BIG-IP-iSeries-Local-Traffic-Manager-i5800",
     ["i2600", "i2800", "i4600", "i4800"]),
    (LB, "F5", WEB, "https://www.parkplacetechnologies.com/eosl/f5-networks/",
     ["1600", "3600", "3900", "6900", "8900", "8950", "11000", "12250v", "10350v", "10200v", "7200v", "2000s",
      "4000s", "5000s", "7000s"]),
    (LB, "F5", SCRAPED, "https://clouddocs.f5.com/training/community/velos-training/html/velos_components_new.html",
     ["CX410", "CX1610", "BX110", "BX520", "C2200", "C2400", "B2100", "B2150", "B2250"]),
    (LB, "F5", WEB, "https://www.parkplacetechnologies.com/eosl/f5-networks/viprion-2400-chassis/",
     ["C4480", "C4800", "B4300", "B4450"]),
    (LB, "NetScaler (Citrix)", WEB, "https://endoflife.ai/netscaler-mpx",
     ["MPX 5500", "MPX 5550", "MPX 8005", "MPX 11500", "SDX 11500"]),
    (LB, "A10 Networks", SCRAPED, "https://www.a10networks.com/eol/",
     ["Thunder 930", "Thunder 1030S", "Thunder 1040", "Thunder 3030S", "Thunder 3040", "Thunder 3040S",
      "Thunder 3230S", "Thunder 3430", "Thunder 3430S", "Thunder 4430", "Thunder 4430S", "Thunder 5330",
      "Thunder 5430", "Thunder 5440", "Thunder 5630", "Thunder 6440", "Thunder 6630", "Thunder 6635",
      "Thunder 7440", "Thunder 7445", "Thunder 7650", "Thunder 14045", "AX 1000", "AX 2200", "AX 2500",
      "AX 2600", "AX 3000", "AX 3200", "AX 3400", "AX 5100"]),
    (LB, "Radware", WEB, "https://cdn.blueally.com/radappliances/datasheets/tech-specs/alteon-tech-spec.pdf",
     ["Alteon 4408", "Alteon 4416", "Alteon 5224", "Alteon 5412", "Alteon 6420", "Alteon 8420", "Alteon 10000"]),
    (LB, "Kemp (Progress)", WEB, "https://www.eol.network/kemp",
     ["LM-X15", "LM-X25", "LM-X40", "LM-2200", "LM-2600", "LM-3400", "LM-3600"]),
    (LB, "Array Networks", WEB, "https://www.hitachi-solutions.co.jp/array/products/apv/lineup.html",
     ["APV1800", "APV2800", "APV5800", "AVX5800", "AVX7800", "AVX9800", "AVX10650"]),
    (LB, "Barracuda", SCRAPED, "https://campus.barracuda.com/product/loadbalanceradc/doc/19333914/barracuda-load-balancer-adc-hardware-features",
     ["340", "440", "540", "640", "641", "642", "840", "841", "842"]),
    (LB, "Coyote Point", WEB, "https://docs.bmc.com/docs/discovery/contentref/coyote-point-1073952198.html", ["E350GX", "E650GX"]),
    (LB, "Brocade", WEB, "https://terabitsystems.com/blog/brocade-serveriron-adx-series-4th-generation-load-balancer-high-end-deployments",
     ["ServerIron ADX 1000", "ServerIron ADX 4000", "ServerIron ADX 10000"]),
    (LB, "Cisco", WEB, "https://community.cisco.com/t5/application-networking/ace-vs-css/td-p/1481279",
     ["ACE 4710", "CSS 11501", "CSS 11503", "CSS 11506"]),
    # ------------------------------------------------------------------ firewalls
    (FW, "Fortinet", SCRAPED, "https://docs.fortinet.com:443/document/fortianalyzer/6.2.2/release-notes/867245",
     ["FG-30E", "FG-50E", "FG-60E", "FG-80E", "FG-100E", "FG-140E", "FG-200E", "FG-300E", "FG-400E", "FG-500E",
      "FG-600E", "FG-1100E", "FG-2000E", "FG-2500E", "FG-3400E", "FG-3960E", "FG-3980E"]),
    (FW, "Fortinet", WEB, "https://datacenter360.ca/?p=23761", ["FG-2200E"]),
    (FW, "Fortinet", WEB, "https://www.parkplacetechnologies.com/eosl/family/fortigate/",
     ["FG-1200D", "FG-1000D", "FG-800D", "FG-900D", "FG-3000D", "FG-3200D", "FG-3100D", "FG-100D", "FG-200D",
      "FG-140D", "FG-1500D", "FG-3700D", "FG-20C", "FG-40C", "FG-300C", "FG-620B", "FG-1000C", "FGR-60F"]),
    (FW, "Palo Alto Networks", SCRAPED, "https://paloaltonetworks.com/services/support/end-of-life-announcements/hardware-end-of-life-dates",
     ["PA-220", "PA-820", "PA-850", "PA-3220", "PA-3250", "PA-3260", "PA-5220", "PA-5250", "PA-5260", "PA-5280",
      "PA-7050", "PA-7080"]),
    (FW, "Palo Alto Networks", WEB, "https://www.parkplacetechnologies.com/eosl/palo-alto-networks/",
     ["PA-200", "PA-500", "PA-2020", "PA-2050", "PA-3020", "PA-3050", "PA-3060", "PA-4020", "PA-4050", "PA-4060",
      "PA-5020", "PA-5050", "PA-5060"]),
    (FW, "Check Point", SCRAPED, "https://www.checkpoint.com/kr/downloads/products/check-point-appliance-comparison-chart.pdf",
     ["Quantum 3600", "Quantum 3800", "Quantum 6200", "Quantum 6400", "Quantum 6600", "Quantum 6700", "Quantum 6900",
      "Quantum 7000", "Quantum 7300", "Quantum 16200", "Quantum 16600HS", "Quantum 26000", "Quantum 28000"]),
    (FW, "Check Point", SCRAPED, "https://community.checkpoint.com/t5/General-Topics/Check-Point-Appliances-Overview/m-p/260906",
     ["MHO-140", "MHO-175"]),
    (FW, "Check Point", SCRAPED, "https://sc1.checkpoint.com/documents/Appliances/Quantum_Spark_R82.00.X/AdminGuides_Locally_Managed/EN/Content/Topics/Overview.htm",
     ["Quantum Spark 2530", "Quantum Spark 2550", "Quantum Spark 2560", "Quantum Spark 2570", "Quantum Spark 2580"]),
    (FW, "Check Point", WEB, "https://www.parkplacetechnologies.com/eosl/check-point/",
     ["2200", "4200", "4400", "4600", "4800", "12200", "12400", "12600", "13500", "13800", "21400", "21600", "21700",
      "21800", "3100", "3200", "5100", "5200", "5400", "5600", "5800", "5900", "15400", "15600", "23500", "23800",
      "23900"]),
    (FW, "Check Point", WEB, "https://www.it-server-room.com/en/check-point-firewall-equipment-eos-eol-timeline/",
     ["730", "750", "770", "790", "1430", "1450", "1470", "1200R"]),
    (FW, "Cisco", SCRAPED, "https://cisco.com/c/en/us/products/collateral/security/firewalls/secure-firewall-1200-series-ds.html",
     ["CSF1210CE", "CSF1210CP", "CSF1220CX", "CSF1230", "CSF1240", "CSF1250"]),
    (FW, "Cisco", SCRAPED, "https://cisco.com/c/en/us/products/collateral/security/firewalls/secure-firewall-4200-ds.html",
     ["FPR4215", "FPR4225", "FPR4245"]),
    (FW, "Cisco", SCRAPED, "https://cisco.com/c/en/us/products/collateral/security/firewalls/secure-firewall-6100-series/secure-firewall-6100-series-ds.html",
     ["FPR6160", "FPR6170"]),
    (FW, "Cisco", SCRAPED, "https://cisco.com/c/en/us/products/collateral/security/firepower-ngfw/firepower-41204050-fpr-9300-sm243644-series-eol.html",
     ["FPR4120", "FPR4140", "FPR4150", "FPR9300"]),
    (FW, "Cisco", SCRAPED, "https://bxin.cisco.com/c/en/us/products/collateral/security/firepower-7000-series-appliances/eos-eol-notice-c51-739207.pdf",
     ["FP7010", "FP7020", "FP7030"]),
    (FW, "Cisco", SCRAPED, "https://www.cisco.com/c/en/us/products/collateral/security/asa-5500-series-next-generation-firewalls/eol_C51-727283.html",
     ["ASA5510", "ASA5520", "ASA5540", "ASA5550", "ASA5512-X", "ASA5515-X", "ASA5525-X", "ASA5545-X", "ASA5555-X"]),
    (FW, "Cisco", WEB, "https://www.parkplacetechnologies.com/eosl/family/asa/", ["ASA5505", "ASA5580", "ASA5585-X", "ASA5506W-X"]),
    (FW, "Cisco", SCRAPED, "https://www.cisco.com/c/en/us/support/security/industrial-security-appliance-isa/series.html",
     ["ISA-3000-4C", "ISA-3000-2C2F"]),
    (FW, "Huawei", WEB, "https://network-switch.com/collections/huawei-usg-6000-series-firewalls", ["USG6305E", "USG6315E"]),
    (FW, "H3C", SCRAPED, "https://www.h3c.com/en/Products_and_Solutions/Security/Security/Products/Boundary_security/LM/F5000-AI/",
     ["F5000-AI-20", "F5000-AI-40"]),
    (FW, "Sangfor", SCRAPED, "https://sangfor.com/es/node/4693",
     ["M5100", "M5200", "M5300", "M5400", "M5500", "M5600", "M5800", "M6000"]),
    (FW, "Sophos", WEB, "https://thetechgeeks.com/blogs/sophos/sophos-utm-and-xg-firewall-retirement-calendar",
     ["XG 86", "XG 106", "XG 115", "XG 125", "XG 135", "XG 210", "XG 230", "XG 310", "XG 330", "XG 430", "XG 450",
      "XG 550", "XG 650", "XG 750", "SG 105", "SG 430", "SG 450", "SG 550"]),
    (FW, "Sophos (Cyberoam)", WEB, "https://cdn.blueally.com/cyberoamworks/datasheets/cyberoamngseriesutmtechsheet.pdf",
     ["CR10iNG", "CR15iNG", "CR1000iNG-XP", "CR1500iNG-XP", "CR2500iNG-XP"]),
    (FW, "Netgate", SCRAPED, "https://docs.netgate.com/pfsense/en/latest/solutions/search.html",
     ["1100", "2100", "4100", "4200", "6100", "8200", "8300", "1537", "1541", "XG-7100"]),
    (FW, "Protectli", SCRAPED, "https://eu.protectli.com/?p=610511",
     ["VP2410", "VP2420", "VP2430", "VP4630", "VP4650", "VP4670", "VP6630", "VP6650", "VP6670", "FW4B", "FW4C"]),
    (FW, "Untangle / Arista", WEB, "https://untangle.bit.nl/?p=220", ["z4", "z4w", "z6", "z12", "z20", "u25"]),
    (FW, "Kerio", SCRAPED, "https://manuals.gfi.com/en/kerio/control/content/hardware-appliance/setting-up-kerio-control-box-ng-series-1859.html",
     ["NG100", "NG100W", "NG300", "NG300W", "NG500"]),
    (FW, "Clavister", SCRAPED, "https://clavister.com/products/ngfw/netwall-mssp-models", ["E10", "E20", "E80", "W20", "W30"]),
    (FW, "Trellix (McAfee)", SCRAPED, "https://www.trellix.com/assets/data-sheets/trellix-intrusion-prevention-system-physical-appliance-specifications-NS9500-NS7500-NS3200.pdf",
     ["NS3200", "NS7500", "NS9500"]),
    (FW, "Trellix (McAfee)", WEB, "https://sec-certs.org/fips/id/3869", ["NS7100", "NS7200", "NS7300"]),
    (FW, "SonicWall", WEB, "https://endoflife.ai/sonicwall-firewalls",
     ["NSA 220", "NSA 240", "NSA 250M", "NSA 2400", "NSA 3500", "NSA 4500", "NSA 5000", "TZ 100", "TZ 105", "TZ 200",
      "TZ 205", "TZ 210", "TZ 215", "SOHO 250", "SOHO 250W"]),
    (FW, "Zyxel", WEB, "https://endoflife.ai/zyxel-usg",
     ["USG2200", "USG310", "USG210", "USG1100", "USG110", "USG1900", "ZyWALL VPN50", "USG20-VPN", "USG20W-VPN"]),
    (FW, "Barracuda", SCRAPED, "https://documentation.campus.barracuda.com/wiki/display/NGFEOL/Hardware+Models",
     ["F12", "F18", "F80", "F82", "F93", "F180", "F193", "F280", "F380", "F400", "F600", "F800", "F900", "F1000"]),
    (FW, "LANCOM Systems", SCRAPED, "https://www.lancom-systems.de/fileadmin/pdf/brochures-and-flyer/LANCOM-Firewalls-comparison-table.pdf",
     ["UF-60", "UF-160", "UF-260", "UF-360", "UF-760"]),
    (FW, "Hillstone Networks", SCRAPED, "https://hillstonenet.com/?p=8842", ["A6800"]),
    (FW, "Juniper", WEB, "https://www.parkplacetechnologies.com/eosl/juniper/ssg-550m-sh/",
     ["ISG1000", "ISG2000", "SSG140", "SSG320M", "SSG350M", "SSG520M", "SSG550M"]),
    (FW, "Siemens", SCRAPED, "https://cache.industry.siemens.com/dl/files/815/109754815/att_1122575/v1/PH_SCALANCE-SC-600-WBM_76.pdf",
     ["SCALANCE S615", "SCALANCE SC622-2C", "SCALANCE SC632-2C", "SCALANCE SC636-2C", "SCALANCE SC642-2C",
      "SCALANCE SC646-2C"]),
    (FW, "Tosibox", WEB, "https://industrial.rubicon.tech/collections/tosibox", ["Lock 210", "Lock 650"]),
    (FW, "Phoenix Contact (including Hirschmann)", SCRAPED, "https://phoenixcontact.com/mguard",
     ["FL MGUARD 1102", "FL MGUARD 1105", "FL MGUARD 2102", "FL MGUARD 2105", "FL MGUARD 4302"]),
    (FW, "Phoenix Contact (including Hirschmann)", WEB, "https://befr.rs-online.com/web/p/routers/0535951",
     ["FL MGUARD RS4000 4G", "FL MGUARD GT/GT"]),
    (FW, "MOXA", WEB, "https://www.worldoil.com/news/2026/9/10/moxa-edr-8010-secure-router-targets-industrial-network-cybersecurity/",
     ["EDR-8010", "EDR-G9004"]),
    (FW, "Deciso", SCRAPED, "https://shop.opnsense.com/dec800-series-opnsense-desktop-security-appliance/", ["DEC840", "DEC4280"]),
]

RENAME = [
    (GW, "Sangoma", "Vega 400", "Vega 400G", SCRAPED, "https://sangomakb.atlassian.net/wiki/x/qgHoAQ"),
    (GW, "ProSoft Technology", "PLX31-MBTCP-MBS", "PLX31-MBTCP-MBS4", WEB, "https://plc-direct.com/en-de/products/plx31-mbtcp-mbs4"),
    (LB, "A10 Networks", "AX 5200", "AX 5200-11", SCRAPED, "https://www.a10networks.com/eol/"),
]

REMOVE = [
    (GW, "Sangoma", "Vega 60", "same product as Vega 60G (no separate 'Vega 60' found)"),
    (GW, "beroNet", "BF4000", "no such model; beroNet range is BF400 / BF1600 / BF6400"),
    (GW, "HMS Networks", "Anybus PROFINET to Modbus TCP gateway", "a description, not a model name"),
    (GW, "Linksys", "SPA2102", "duplicate: listed under Cisco (Cisco support page)"),
    (GW, "Linksys", "SPA3102", "duplicate: listed under Cisco (Cisco support page)"),
    (GW, "Synway", "Synway SMG (models: verify)", "replaced by the models found"),
    (GW, "OpenVox", "OpenVox gateway (models: verify)", "replaced by the models found"),
    (LB, "A10 Networks", "Thunder 3530", "not a Thunder model; the 3530 is AX 3530 (A10 end-of-sale list)"),
    (LB, "HAProxy", "ALOHA 1G", "not HAProxy's names; hardware models are ALB 3350 / ALB 5350"),
    (LB, "HAProxy", "ALOHA 2G", "not HAProxy's names"), (LB, "HAProxy", "ALOHA 4G", "not HAProxy's names"),
    (LB, "HAProxy", "ALOHA 10G", "not HAProxy's names"),
    (LB, "Radware", "LinkProof NG 1008", "not found; LinkProof NG runs on Alteon platforms (NG 4408 found)"),
    (LB, "Radware", "LinkProof NG 2016", "not found"), (LB, "Radware", "LinkProof NG 5224", "not found"),
    (LB, "Radware", "LinkProof NG 6420", "not found"),
    (LB, "Cisco", "ACE30 module", "a Catalyst 6500 module, not an appliance"),
    (FW, "Sangfor", "NGAF M-series (models: verify)", "replaced by the models found"),
    (FW, "Sangfor", "NSF-1000", "not found; Sangfor NSF names are NSF-xxxxA-I"), (FW, "Sangfor", "NSF-2000", "not found"),
    (FW, "Sangfor", "NSF-3000", "not found"), (FW, "Sangfor", "NSF-5000", "not found"), (FW, "Sangfor", "NSF-8000", "not found"),
    (FW, "Sangfor", "M8000", "not found (NGAF range ends at M6000)"), (FW, "Sangfor", "M9000", "not found"),
    (FW, "Barracuda", "F18B", "hardware revision, not a model (F18 Revision B)"),
    (FW, "Barracuda", "F80B", "hardware revision (F80 Revision B)"), (FW, "Barracuda", "F180B", "hardware revision"),
    (FW, "Barracuda", "F280B", "hardware revision"), (FW, "Barracuda", "F380B", "hardware revision"),
    (FW, "Clavister", "W40", "not found on clavister.com"), (FW, "Clavister", "W50", "not found"),
    (FW, "Clavister", "W60", "not found"),
    (FW, "Phoenix Contact (including Hirschmann)", "FL MGUARD 4102", "no such model; the 4300 series is 4302 / 4305"),
]

STATUS = [
    (GW, "Ribbon", m, EOSUP, "https://publicdoc.rbbn.com/x/urhmEQ") for m in ("SBC 5100", "SBC 5110", "SBC 5200", "SBC 5210")
] + [
    (LB, "A10 Networks", "Thunder 7650", C, "https://www.a10networks.com/eol/"),
    (FW, "Netgate", "XG-7100", EOS, "https://docs.netgate.com/pfsense/en/latest/solutions/search.html"),
    (FW, "SonicWall", "SOHO 250", EOSUP, "https://endoflife.ai/sonicwall-firewalls/soho-250"),
    (FW, "SonicWall", "SOHO 250W", EOSUP, "https://endoflife.ai/sonicwall-firewalls/soho-250w"),
]

MG, ATA, SBC, IND = "VoIP / media gateway", "Analog (FXS) gateway / ATA", "Session border controller", "Protocol / IoT gateway"
ADC, LEG, LINK = "ADC / load balancer", "Legacy load balancer", "Link load balancer"
NGFW, UTM, DCFW, IFW, IPS = ("Next-gen firewall", "UTM / SMB firewall", "Data-center firewall", "Industrial firewall",
                             "Intrusion prevention (IPS)")
ADD = [
    # gateways
    (GW, "Ribbon", "EdgeMarc", SBC, "Desktop / 1RU", C, WEB, "https://pdf.directindustry.com/pt/pdf/ribbon-communications-operating-company-inc-4605670.html",
     ["EdgeMarc 4552", "EdgeMarc 4601", "EdgeMarc 4700", "EdgeMarc 7300"]),
    (GW, "Patton", "SmartNode", MG, "Desktop / 1RU", C, SCRAPED, "https://www.patton.com/company/newsrelease.asp?id=2752",
     ["SN4940", "SN4950", "SN4990A", "SN5301", "SN500", "SN5500", "SN5600"]),
    (GW, "Sangoma", "Vega", MG, "Desktop / 1RU", C, SCRAPED, "https://sangomakb.atlassian.net/wiki/x/qgHoAQ", ["Vega 3050G"]),
    (GW, "Grandstream", "HandyTone ATA", ATA, "Desktop", C, SCRAPED,
     "https://content.grandstream.com/hubfs/Partner_Marketing_Kit/HT%20PMK/PartnerMarketingKit_HT_Series_ATAs.pdf", ["HT813"]),
    (GW, "Mediatrix (Media5)", "Mediatrix gateways / Sentinel", MG, "Desktop / 1RU", C, WEB,
     "https://voipia.net/wp-content/uploads/2017/10/mediatrix-41XX_voipia_datasheet.pdf",
     ["Mediatrix 4102", "Mediatrix 4108", "Mediatrix 4116", "Mediatrix 4124", "Mediatrix C733"]),
    (GW, "Dinstar", "UC2000 GSM gateways", MG, "Desktop / 1RU", C, WEB, "https://w11stop.com/dinstar", ["UC2000"]),
    (GW, "Synway", "SMG", MG, "Desktop / 1RU", C, WEB, "https://www.manualslib.com/brand/synway/gateway.html",
     ["SMG1004B", "SMG1008", "SMG2030", "SMG3008B", "SMG4004", "SMG4016G"]),
    (GW, "OpenVox", "iAG / VoxStack / DGW", MG, "Desktop / 1RU", C, SCRAPED, "https://openvox.cn/products/voip-gateways/fxs-fxo-voip-gateway/",
     ["iAG804", "iAG808", "iAG840", "iAG880", "iAG200", "VS-GW1200-8S", "VS-GW1202-16S", "VS-GW1600",
      "DGW-3001", "DGW-3002", "DGW-3004"]),
    (GW, "Ingate", "SIParator", SBC, "Rack", C, WEB, "https://nojitter.com/who-ingate-systems-anyway", ["SIParator S99"]),
    (GW, "Moxa", "MGate", IND, "Industrial / DIN", C, WEB,
     "https://technolec.fw4.be/products/industrial-edge-connectivity/protocol-gateways/modbus-tcp-gateways",
     ["MGate MB3170-G2", "MGate MB3270-G2", "MGate MB3470-G2"]),
    (GW, "Quintum", "Tenor (end of life)", MG, "1RU", EOSUP, WEB, "https://www.shi.com/product/21569738/Quintum-Tenor-AFM400",
     ["Tenor AFM400"]),
    (GW, "ProSoft Technology", "PLX gateways", IND, "Industrial / DIN", C, SCRAPED,
     "https://marketplace.odva.org/products/527-ethernet-ip-industrial-communication-gateways",
     ["PLX31-EIP-MBTCP", "PLX31-MBTCP-PND"]),
    (GW, "Advantech", "ECU / WISE gateways", IND, "Industrial / DIN", C, WEB,
     "https://www.mouser.in/new/industrial-automation/network-communications/gateways/n-fb8zl", ["ECU-1270"]),
    # load balancers
    (LB, "A10 Networks", "Thunder ADC (older)", LEG, "1RU-2RU", EOS, SCRAPED, "https://www.a10networks.com/eol/",
     ["Thunder 840", "Thunder 940", "Thunder 3230", "Thunder 4435", "Thunder 4435S", "Thunder 5435", "Thunder 5435S",
      "Thunder 5845", "Thunder 6435", "Thunder 6435S"]),
    (LB, "A10 Networks", "AX Series (end of life)", LEG, "1RU-2RU", EOSUP, SCRAPED, "https://www.a10networks.com/eol/",
     ["AX 1030", "AX 2000", "AX 2100", "AX 3030", "AX 3100", "AX 3530", "AX 5630"]),
    (LB, "Kemp (Progress)", "LoadMaster", ADC, "1RU-2RU", C, WEB,
     "https://www.cdw.com/product/progress-kemp-loadmaster-xhc55-ng-load-balancing-device-hardware/8485172", ["LM-XHC55"]),
    (LB, "Kemp (Progress)", "LoadMaster (older)", LEG, "1RU", EOS, WEB, "https://eweek.com/?p=62192", ["LM-5500", "LM-3000"]),
    (LB, "Array Networks", "APV x800 / x900", ADC, "1RU-2RU", C, WEB, "https://www.hitachi-solutions.co.jp/array/products/apv/lineup.html",
     ["APV1900", "APV2900", "APV3600", "APV3650", "APV7800", "APV9800"]),
    (LB, "Array Networks", "AVX", ADC, "2RU", C, WEB, "https://cdn.blueally.com/arrayguard/datasheets/ds-avx-series.pdf", ["AVX3600"]),
    (LB, "HAProxy", "ALOHA appliances", ADC, "1RU", C, SCRAPED, "https://www.haproxy.com/products/aloha-hardware",
     ["ALB 3350", "ALB 5350"]),
    (LB, "Radware", "LinkProof NG (link load balancer)", LINK, "1RU", C, SCRAPED, "https://www.radware.com/products/linkproofng-models/",
     ["LinkProof NG 4408"]),
    (LB, "Brocade", "ServerIron ADX (end of life)", LEG, "Chassis", EOSUP, WEB,
     "https://terabitsystems.com/blog/brocade-serveriron-adx-series-4th-generation-load-balancer-high-end-deployments",
     ["ServerIron ADX 8000"]),
    # firewalls
    (FW, "Check Point", "Quantum Spark 2500", UTM, "Desktop", C, SCRAPED,
     "https://sc1.checkpoint.com/documents/Appliances/Quantum_Spark_R82.00.X/AdminGuides_Locally_Managed/EN/Content/Topics/Overview.htm",
     ["Quantum Spark 2590"]),
    (FW, "Huawei", "HiSecEngine USG6000E", NGFW, "1RU", C, WEB, "https://network-switch.com/collections/huawei-usg-6000-series-firewalls",
     ["USG6307E", "USG6311E"]),
    (FW, "H3C", "SecPath F1000-AI", NGFW, "1RU", C, SCRAPED, "https://www.h3c.com/en/d_202209/1677794_294551_0.htm",
     ["F1000-AI-03", "F1000-AI-10", "F1000-AI-20", "F1000-AI-30", "F1000-AI-50", "F1000-AI-60", "F1000-AI-70"]),
    (FW, "H3C", "SecPath F5000-AI", DCFW, "Rack", C, SCRAPED,
     "https://www.h3c.com/en/Products_and_Solutions/Security/Security/Products/Boundary_security/LM/F5000-AI/",
     ["F5000-AI-15", "F5000-AI160"]),
    (FW, "Sangfor", "NGAF / NSF", NGFW, "Desktop / rack", C, SCRAPED, "https://sangfor.com/es/node/4693",
     ["M4500", "NSF-1200A-I", "NSF-3200A-I", "NSF-7100A-I", "NSF-7200A-I", "NSF-7300A-I", "NSF-7500A-I"]),
    (FW, "Sophos (Cyberoam)", "Cyberoam CR (end of life)", UTM, "Desktop / rack", EOSUP, WEB,
     "https://scworld.com/product-test/cyberoam-technologies-pvt-ltd-cr200ing-xp", ["CR200iNG-XP"]),
    (FW, "Protectli", "Vault", UTM, "Desktop", C, SCRAPED, "https://eu.protectli.com/?p=610511",
     ["VP2440", "VP3210", "VP3230", "FW6E"]),
    (FW, "Kerio", "Kerio Control boxes", UTM, "Desktop / rack", C, SCRAPED,
     "https://manuals.gfi.com/en/kerio/control/content/hardware-appliance/setting-up-kerio-control-box-ng-series-1859.html",
     ["NG110", "NG120", "NG310", "NG320", "NG510", "NG511", "NG520", "NG521", "NG700"]),
    (FW, "Trellix (McAfee)", "Network Security Platform (IPS)", IPS, "Rack", C, SCRAPED,
     "https://www.trellix.com/en-us/assets/data-sheets/trellix-intrusion-prevention-system-physical-appliance-specifications.pdf",
     ["NS3600", "NS7600", "NS9600"]),
    (FW, "Trellix (McAfee)", "Network Security Platform (IPS)", IPS, "Rack", EOS, WEB, "https://sec-certs.org/fips/id/3870",
     ["NS7150", "NS7250", "NS7350"]),
    (FW, "Barracuda", "CloudGen Firewall F-Series", NGFW, "Rack", C, SCRAPED,
     "https://documentation.campus.barracuda.com/wiki/display/NGFEOL/Hardware+Models", ["F2000"]),
    (FW, "LANCOM Systems", "R&S Unified Firewalls", UTM, "Desktop", C, SCRAPED,
     "https://www.lancom-systems.de/fileadmin/pdf/brochures-and-flyer/LANCOM-Firewalls-comparison-table.pdf",
     ["UF-T60", "UF-60 LTE"]),
    (FW, "Hillstone Networks", "A-Series NGFW", NGFW, "Rack", C, SCRAPED, "https://hillstonenet.com/?p=8842", ["A7600"]),
    (FW, "Siemens", "SCALANCE S (industrial security)", IFW, "Industrial / DIN", C, SCRAPED,
     "https://cache.industry.siemens.com/dl/files/815/109754815/att_1122575/v1/PH_SCALANCE-SC-600-WBM_76.pdf",
     ["SCALANCE SC626-2C"]),
    (FW, "Tosibox", "Lock (industrial)", IFW, "Industrial / DIN", C, WEB, "https://industrial.rubicon.tech/collections/tosibox",
     ["Lock 150", "Lock 200", "Lock 610", "Lock 695"]),
    (FW, "Phoenix Contact (including Hirschmann)", "mGuard security routers", IFW, "Industrial / DIN", C, SCRAPED,
     "https://phoenixcontact.com/mguard", ["FL MGUARD 4305"]),
    (FW, "Deciso", "OPNsense appliances", UTM, "Desktop / rack", C, SCRAPED, "https://shop.opnsense.com/dec700-series-opnsense-desktop-security-appliance/",
     ["DEC740", "DEC750", "DEC850", "DEC2680", "DEC2687", "DEC3842", "DEC3852", "DEC3862", "DEC3920", "DEC3940",
      "DEC3960", "DEC4240"]),
]


def apply(kind, rows):
    log, links = [], {}
    import os
    review2_on = os.environ.get("CATALOG_REVIEW2", "1") != "0"   # 0 = as before review 2 (backup copy)
    from review2 import VENDOR                         # entries below may use a vendor's older spelling
    key = (lambda v, m: (VENDOR.get(v, v), m.upper())) if review2_on else (lambda v, m: (v, m.upper()))
    idx = {key(r[0], r[2]): i for i, r in enumerate(rows)}
    rows = [list(r) for r in rows]
    gone = set()

    for k, vendor, model, why in REMOVE:
        i = idx.get(key(vendor, model))
        if k == kind and i is not None:
            gone.add(i)
            log.append(("Removed", vendor, model, why))
    for k, vendor, old, new, src, url in RENAME:
        i = idx.get(key(vendor, old))
        if k == kind and i is not None:
            rows[i][2], rows[i][6] = new, src
            links[key(vendor, new)] = url
            log.append(("Renamed", vendor, f"{old} -> {new}", url))
    for k, vendor, src, url, models in CONFIRM:
        if k != kind:
            continue
        for m in models:
            i = idx.get(key(vendor, m))
            if i is None or i in gone:
                continue
            was = rows[i][6]
            if "verify" in was or src == SCRAPED:
                rows[i][6] = src
            links[key(vendor, m)] = url
            if "verify" in was:
                log.append(("Confirmed", vendor, m, url))
    for k, vendor, model, status, url in STATUS:
        i = idx.get(key(vendor, model))
        if k == kind and i is not None and rows[i][5] != status:
            log.append(("Status", vendor, model, f"{rows[i][5]} -> {status}  ({url})"))
            rows[i][5] = status
    out = [tuple(r) for i, r in enumerate(rows) if i not in gone]
    have = {key(r[0], r[2]) for r in out}
    for k, vendor, series, cat, form, status, src, url, models in ADD:
        if k != kind:
            continue
        for m in models:
            m, same = (m, "") if isinstance(m, str) else m          # ('SLC 8000', 'same hardware / variants')
            if key(vendor, m) in have:
                continue
            have.add(key(vendor, m))
            out.append((vendor, series, m, cat, form, status, src, same))
            links[key(vendor, m)] = url
            log.append(("Added", vendor, m, url))
    import review2                                     # second catalogue review (2026-10-21), applied last
    if review2_on:
        out = review2.apply(kind, out, log, links)
    return out, log, links


# ---------------------------------------------------------------------- second round (same day)
CONFIRM += [
    (FW, "Cisco", SCRAPED, "https://www.cisco.com/c/en/us/products/collateral/security/firepower-1000-series/datasheet-c78-742469.html",
     ["FPR1010", "FPR1010E", "FPR1120", "FPR1140", "FPR1150"]),
    (FW, "Cisco", SCRAPED, "https://www.cisco.com/c/en/us/products/collateral/security/firepower-2100-series/datasheet-c78-742473.html",
     ["FPR2110", "FPR2120", "FPR2130", "FPR2140"]),
    (FW, "Cisco", SCRAPED, "https://www.cisco.com/c/en/us/products/collateral/security/firewalls/secure-firewall-3100-series-ds.html",
     ["FPR3105", "FPR3110", "FPR3120", "FPR3130", "FPR3140"]),
    (FW, "Cisco", SCRAPED, "https://www.cisco.com/c/en/us/products/collateral/security/firepower-4100-series/datasheet-c78-742474.html",
     ["FPR4110", "FPR4112", "FPR4115", "FPR4125", "FPR4145"]),
    (FW, "Cisco", SCRAPED, "https://www.cisco.com/c/en/us/products/collateral/security/firepower-7000-series-appliances/eos-eol-notice-c51-741685.html",
     ["FP7050", "FP7110", "FP7115", "FP7120", "FP7125", "FP8120", "FP8130", "FP8140", "FP8250", "FP8350", "FP8360",
      "FP8370", "FP8390"]),
    (FW, "Fortinet", SCRAPED, "https://docs.fortinet.com/document/fortianalyzer/6.0.4/release-notes/867245",
     ["FG-3240C", "FG-3600C"]),
    (FW, "Fortinet", WEB, "https://www.parkplacetechnologies.com/eosl/fortinet",
     ["FG-61E", "FG-101E", "FG-201E", "FG-60D", "FG-300D", "FG-60C", "FG-110C", "FG-600C", "FG-800C", "FG-1240B"]),
    (FW, "SonicWall", SCRAPED, "https://www.sonicwall.com/support/product-notification/kA1VN0000000R3R0AU",
     ["NSA E6500", "NSA E8500"]),
    (FW, "Huawei", WEB, "https://www.tokopedia.com/dserver-indonesia/huawei-usg6000e-series-firewalls-matrix", ["USG6555E"]),
    (FW, "Sophos", SCRAPED, "https://www.sophos.com/pt-br/products/unified-threat-management/tech-specs",
     ["SG 115", "SG 125", "SG 135", "SG 210", "SG 230"]),
    (FW, "Hillstone Networks", SCRAPED, "https://www.hillstonenet.com/?p=13319", ["A5200"]),
    (FW, "Juniper", WEB, "https://www.parkplacetechnologies.com/eosl/family/ssg/", ["SSG5", "SSG20"]),
    (LB, "F5", WEB, "https://www.parkplacetechnologies.com/eosl/family/big-ip/",
     ["1500", "3400", "6400", "6800", "8400", "8800", "11050", "2200s", "4200v", "5050s", "5200v", "5250v", "10000s",
      "10050s", "i15800"]),
    (GW, "Patton", WEB, "https://dev.linkshop.gr/patton.html", ["SN4316", "SN4324", "SN4332", "SN4660", "SN5490"]),
    (GW, "Moxa", SCRAPED, "https://moxa.com/Products/INDUSTRIAL-EDGE-CONNECTIVITY/Protocol-Gateways/PROFINET-Gateways/MGate-5102-PBM-PN-Series",
     ["MGate 5102-PBM-PN"]),
    (GW, "Moxa", SCRAPED, "https://www.moxa.com/Event/Tech/modbus-protocol-gateway/downloads/mgate-quick-card-en.pdf",
     ["MGate W5108", "MGate 5111"]),
    (GW, "Moxa", WEB, "https://www.manuauto.com/Protocol-Gateways-Products",
     ["MGate 5103", "MGate 5119", "MGate 5121", "MGate 5122", "MGate 5123", "MGate 5135"]),
    (GW, "Mediatrix (Media5)", SCRAPED, "https://documentation.media5corp.com/display/DGWLATEST/Using+the+Sentinel+as+a+Gateway",
     ["Sentinel 400"]),
    (GW, "Mediatrix (Media5)", WEB, "https://www.thetelecomspot.com/collections/media5", ["Mediatrix C731"]),
    (GW, "Avaya", SCRAPED, "https://support.avaya.com/css/public/documents/101046576", ["G650"]),
]
RENAME += [
    (GW, "Moxa", "MGate 5217", "MGate 5217I", WEB, "https://www.manuauto.com/Protocol-Gateways-Products"),
]
STATUS += [
    (GW, "Avaya", "G650", EOSUP, "https://support.avaya.com/css/public/documents/101046576"),
]
ADD += [
    (FW, "Fortinet", "FortiGate A/B/C-series (end of life)", "Legacy firewall", "Desktop / rack", EOSUP, WEB,
     "https://www.parkplacetechnologies.com/eosl/fortinet", ["FG-80C"]),
    (FW, "Hillstone Networks", "A-Series NGFW", NGFW, "Rack", C, SCRAPED,
     "https://www.hillstonenet.com/blog/empowering-security-with-hillstone-a-series-ngfw-new-models-available-a2715-a2815-a3615-and-a3815/",
     ["A2715", "A2815", "A3615", "A3815"]),
    (FW, "Hillstone Networks", "A-Series NGFW", NGFW, "Desktop / rack", C, WEB,
     "https://www.infotech.com/software-reviews/vendor-technology-notes/can-hillstone-networks-position-its-stoneos-to-take-firewalls-beyond-the-next-generation",
     ["A200", "A1100", "A5800"]),
    (GW, "Patton", "SmartNode", SBC, "1RU", C, SCRAPED, "https://www.patton.com/manuals/SN5530-UM.pdf", ["SN5530"]),
    (GW, "Moxa", "MGate", IND, "Industrial / DIN", C, SCRAPED,
     "https://www.moxa.com/Event/Tech/modbus-protocol-gateway/downloads/mgate-quick-card-en.pdf", ["MGate 5435", "MGate W5208"]),
]


# ---------------------------------------------------------------------- third round (same day)
CONFIRM += [
    (FW, "Huawei", WEB, "https://www.router-switch.com/faq/huawei-eom-eos-firewalls.html",
     ["USG6310", "USG6320", "USG6330", "USG6350", "USG6370", "USG6390", "USG6620"]),
    (FW, "Huawei", SCRAPED, "https://e.huawei.com/eu/products/security/usg6500e-desktop",
     ["USG6510E", "USG6530E", "USG6525E", "USG6585E"]),
    (FW, "Huawei", WEB, "https://network-switch.com/es/products/network-firewall-huawei-usg6355e-ac-next-generation-firewall",
     ["USG6355E"]),
    (FW, "Check Point", WEB, "https://endoflife.ai/checkpoint-appliances",
     ["600", "620", "640", "680", "1120", "1140", "1180", "910", "1490", "64000", "MHO-170", "QLS250"]),
    (FW, "Check Point", WEB, "https://pplware.sapo.pt/?p=382128", ["700"]),
    (FW, "SonicWall", SCRAPED, "https://www.sonicwall.com/support/product-notification/end-of-support-notification-of-soho-sohow-tz-300p-nsa-9250-nsa-9450-and-nsa-9650/kA1VN0000014jIw0AI",
     ["SOHO W"]),
    (FW, "Cisco", SCRAPED, "https://bxin.cisco.com/c/en/us/products/collateral/security/asa-5500-series-next-generation-firewalls/eos-eol-notice-c51-744797.html",
     ["ASA5506-X", "ASA5508-X", "ASA5516-X", "ASA5506H-X"]),
    (LB, "Kemp (Progress)", SCRAPED, "https://kemptechnologies.com/files/assets/data-sheets/LM-8020.pdf", ["LM-8020"]),
    (LB, "Kemp (Progress)", WEB, "https://www.thomas-krenn.com/de/wiki/Kemp_Loadbalancer_Uebersicht",
     ["LM-2000", "LM-4000", "LM-5300"]),
]
STATUS += [
    (FW, "SonicWall", "SOHO W", EOSUP, "https://www.sonicwall.com/support/product-notification/end-of-support-notification-of-soho-sohow-tz-300p-nsa-9250-nsa-9450-and-nsa-9650/kA1VN0000014jIw0AI"),
    (FW, "Cisco", "ASA5506-X", EOS, "https://bxin.cisco.com/c/en/us/products/collateral/security/asa-5500-series-next-generation-firewalls/eos-eol-notice-c51-744797.html"),
]
ADD += [
    (FW, "Huawei", "HiSecEngine USG6000E", NGFW, "1RU", C, SCRAPED, "https://e.huawei.com/de/products/security/usg6500e",
     ["USG6565E", "USG6575E-B"]),
    (FW, "Huawei", "HiSecEngine USG6000E", NGFW, "Desktop / 1RU", C, WEB,
     "https://network-switch.com/es/products/network-firewall-huawei-usg6309e-ac-next-generation-firewalls", ["USG6309E"]),
]


# ---------------------------------------------------------------------- fourth round: series placeholders filled
REMOVE += [
    (FW, v, m, "replaced by the models found") for v, m in (
        ("Topsec", "Topsec NGFW (models: verify)"), ("DPtech", "DPtech FW1000 (models: verify)"),
        ("genua", "genugate (models: verify)"), ("genua", "genuscreen (models: verify)"),
        ("Ruijie", "RG-WALL 1600 (models: verify)"), ("NSFOCUS", "NSFOCUS NF (models: verify)"))
] + [
    (LB, v, m, "replaced by the models found") for v, m in (
        ("Piolink", "PAS-K (models: verify)"), ("Sangfor", "Sangfor AD (models: verify)"),
        ("Elfiq", "Elfiq link balancer (models: verify)"), ("Ecessa", "Ecessa PowerLink (models: verify)"),
        ("SKUDONET (Zevenet)", "SKUDONET appliance (models: verify)"), ("DPtech", "DPtech ADX (models: verify)"))
]
RENAME += [
    (FW, "Qi-AnXin", "Qi-AnXin NSG (models: verify)", "QAX Legendsec NGFW (model numbers not published in English)",
     "Product knowledge - verify", "https://en.qianxin.com/product/detail/167"),
    (FW, "Venustech", "Venustech USG-FW (models: verify)", "Venusense NGFW (model numbers not published in English)",
     "Product knowledge - verify", "https://www.venusense.com/type/NGFW/"),
]
ADD += [
    (FW, "DPtech", "FW1000", NGFW, "Rack", C, SCRAPED, "http://dptech.com/en/upload/201712/DPtech_FW1000_Series_Product_Data_Sheet.pdf",
     ["FW1000-B", "FW1000-GA", "FW1000-GA-E", "FW1000-GC", "FW1000-GC-N", "FW1000-GM-A", "FW1000-GM-E", "FW1000-GS-N",
      "FW1000-MA-N", "FW1000-ME-N", "FW1000-MS", "FW1000-MS-N", "FW1000-TA-A", "FW1000-TM", "FW1000-TM-E",
      "FW1000-TS-A", "FW1000-TS-E"]),
    (FW, "DPtech", "FW1000", DCFW, "Rack", C, SCRAPED, "https://www.dptech.com/uploadfile/2025/0730/20250730054519674.pdf",
     ["FW1000-TA-A10"]),
    (FW, "Huawei", "HiSecEngine USG6000F", NGFW, "1RU-2RU", C, SCRAPED,
     "https://data.nag.wiki/Huawei/Datasheet/USG6600F&USG6700F/HUAWEI%20HiSecEngine%20USG6600F&USG6700F%20Series%20Firewalls%20Datasheet%20(R024C00).pdf",
     ["USG6510F-D", "USG6615F", "USG6625F", "USG6635F", "USG6655F", "USG6685F", "USG6710F", "USG6715F", "USG6725F"]),
    (FW, "Huawei", "HiSecEngine USG6000F", NGFW, "1RU", C, SCRAPED,
     "https://support.huawei.cn/enterprise/en/security/usg6585f-pid-257418503", ["USG6585F"]),
    (FW, "Huawei", "HiSecEngine USG6000G (2026)", NGFW, "2RU", C, SCRAPED,
     "https://e.huawei.com/en/news/2026/solutions/enterprise-network/usg6000g-series-firewalls", ["USG6885G"]),
    (FW, "NSFOCUS", "NF next-gen firewall", NGFW, "Desktop / 1RU", C, SCRAPED,
     "https://nsfocusglobal.com/wp-content/uploads/2024/02/NGFW-datasheet_20240229.pdf",
     ["NFNX3-HDB680", "NFNX3-HDB1080", "NFNX3-HDB1180", "NFNX3-HDB1480", "NFNX3-HDB1780", "NFNX3-HDB3080",
      "NFNX3-HDB3280", "NFNX5-HD5280", "NFNX5-HD6480", "NFNX5-T6280"]),
    (FW, "NSFOCUS", "NF next-gen firewall", NGFW, "1RU", C, WEB, "http://productspec.ul.com/document.php?id=NWGQ.E341914",
     ["NF NX3-G4100H", "NF NX3-G4100S", "NF NX5-T6100L"]),
    (FW, "Ruijie", "RG-WALL 1600 Z series", NGFW, "1RU", C, SCRAPED,
     "https://www.ruijie.com/en-global/products/security/next-generation-firewall/rg-wall-1600-z3200-s",
     ["RG-WALL 1600-Z3200-S", "RG-WALL 1600-Z5100-S"]),
    (FW, "Ruijie", "RG-WALL 1600 Z series", NGFW, "1RU-2RU", C, WEB, "https://baike.baidu.com/en/item/Ruijie%20Firewall/4554313",
     ["RG-WALL 1600-Z3100", "RG-WALL 1600-Z3500", "RG-WALL 1600-Z5000-D05", "RG-WALL 1600-Z5000-G10",
      "RG-WALL 1600-Z8600-G20", "RG-WALL 1600-Z8620", "RG-WALL 1600-Z8680"]),
    (FW, "genua", "genuscreen", NGFW, "Desktop / rack", C, SCRAPED,
     "https://www.genua.eu/it-security-solutions/firewall-vpn-appliance-genuscreen/hardware-variants",
     ["genuscreen XS", "genuscreen S", "genuscreen M", "genuscreen L", "genuscreen XL"]),
    (FW, "genua", "genugate", NGFW, "Rack", C, SCRAPED,
     "https://www.genua.eu/it-security-solutions/high-resistance-firewall-genugate/hardware-variants",
     ["genugate S", "genugate M", "genugate L"]),
    (FW, "Topsec", "NGFW4000-UF", NGFW, "1RU", C, WEB, "https://product.yesky.com/product/858/858637/",
     ["NGFW4000-UF TG-A2408", "NGFW4000-UF TG-41204", "NGFW4000-UF TG-41406", "NGFW4000-UF TG-41410",
      "NGFW4000-UF TG-41427", "NGFW4000-UF TG-51130"]),
    (FW, "Venustech", "Venusense industrial firewall", IFW, "Industrial / DIN", C, SCRAPED,
     "https://www.venusense.com/type/IFW/", ["Venusense IFW-3000"]),
    # load balancers
    (LB, "Piolink", "PAS-K", ADC, "1RU-2RU", C, SCRAPED, "https://commoncriteriaportal.org/files/epfiles/PAS-K%20V2.2%20ST_Lite_en.pdf",
     ["PAS-K1716", "PAS-K1800", "PAS-K2424", "PAS-K2824", "PAS-K3200", "PAS-K3200X", "PAS-K3600", "PAS-K4024",
      "PAS-K4224", "PAS-K4300", "PAS-K4424", "PAS-K4824", "PAS-K5200", "PAS-K5400", "PAS-K5600", "PAS-K8220",
      "PAS-K8620"]),
    (LB, "Sangfor", "Application Delivery (AD)", ADC, "1RU-2RU", C, SCRAPED,
     "https://www.ipv6ready.org/db/index.php/public/search/?o=7&do=1&lim=25&p=2", ["AD-1000"]),
    (LB, "Sangfor", "Application Delivery (AD)", ADC, "2RU", C, WEB,
     "https://smartcomputerindo.com/produk/application_delivery_network_ad1000j442", ["AD-1000-J442"]),
    (LB, "DPtech", "ADX3000", ADC, "Rack", C, SCRAPED,
     "https://dptech.com/index.php?a=show&c=index&catid=1152&id=33&m=content", ["ADX3000", "ADX3000-TM"]),
    (LB, "Elfiq", "Link balancers", LINK, "1RU", C, SCRAPED, "http://www.elfiq.com/",
     ["LBX400", "LBX700", "LBX900", "LBX1200", "LBX2600", "LBX5500"]),
    (LB, "Elfiq", "Link balancers (older)", LINK, "1RU", EOS, WEB, "https://www.ebay.com/itm/232880758555",
     ["LB-600E", "LB-800E"]),
    (LB, "Ecessa", "PowerLink / ClariLink / Edge", LINK, "1RU", C, SCRAPED,
     "https://www.ecessa.com/wp-content/uploads/2022/08/PowerLink_DataSheet_2023.11.pdf",
     ["PowerLink 150", "PowerLink 350", "PowerLink 750", "PowerLink 1250", "PowerLink 4000", "PowerLink 10G",
      "PowerLink 20G", "ClariLink 10G", "ClariLink 20G", "Ecessa Edge"]),
    (LB, "Ecessa", "PowerLink (older)", LINK, "1RU", EOS, SCRAPED,
     "http://www.ecessa.com/wp-content/uploads/2016/02/PowerLink_60_175_600_1200_4000_Datasheet_2.2016.pdf",
     ["PowerLink 60", "PowerLink 175", "PowerLink 600", "PowerLink 1200", "ClariLink 175"]),
    (LB, "SKUDONET (Zevenet)", "Hardware appliances", ADC, "1RU-2RU", C, SCRAPED,
     "https://www.skudonet.com/portfolio/enterprise/hardware/zna6708-hardware-appliance/",
     ["ZNA6508", "ZNA6708", "SNA7108", "SNA8214"]),
]


# ---------------------------------------------------------------------- 2026-10-16: Lantronix (a user photo)
ADD += [
    (GW, "Lantronix", "SLC 8000 console servers", "Console / serial device server", "1RU", C, WEB,
     "https://www.shi.com/product/34068079/Lantronix-SLC-8000-Console-server",
     [("SLC 8000", "SLC8008 / SLC8016 / SLC8032 / SLC8048: 8-48 RS-232 ports")]),
    (GW, "Lantronix", "LM console servers", "Console / serial device server", "Desktop / 1RU", C, WEB,
     "https://www.shi.com/product/46064709/Lantronix-LM83X-Console-server",
     [("LM80", "8 ports"), ("LM83X", "16 / 24 / 40 ports"), "LM4"]),
    (GW, "Lantronix", "EDS3000PR device servers", "Console / serial device server", "1RU", C, WEB,
     "https://www.levata.com/lantronix-eds3016pr1ns-console-device-servers", ["EDS3008PR", "EDS3016PR", "EDS3032PR"]),
    (GW, "Lantronix", "UDS device servers", "Console / serial device server", "Desktop", C, WEB,
     "https://sales.c3.la/brands/lantronix/", ["UDS1100", "UDS2100"]),
]
ADD += [
    (FW, "GajShield", "NU series data security firewalls", UTM, "Desktop / rack", C, SCRAPED, "https://gajshield.com/all-products",
     ["GS15nu", "GS15nu V2", "GS15nu V3", "GS20nu", "GS20nu V2", "GS40nu", "GS80nu", "GS120nu", "GS160nu", "GS165nu V2",
      "GS240nu", "GS590nu", "GS890nu", "GS1390nu", "GS1490nu"]),
]

# ---------------------------------------------------------------------- 2026-10-19: more load-balancer vendors
ADD += [
    (LB, "Mushroom Networks", "Truffle bonding / load-balancing appliances", LINK, "Desktop / 1RU", C, SCRAPED,
     "https://mushroomnetworks.com/brochure-truffle-e/",
     ["Truffle Lite", "Truffle Lite 2", "Truffle E", "Truffle EC", "Truffle EF", "Truffle EX", "Truffle EXX"]),
    (LB, "Mushroom Networks", "Truffle (older)", LINK, "Desktop / 1RU", EOS, WEB,
     "https://convergedigest.com/mushroom-launches-broadband-bonding/?amp=1", ["BBNA6401", "BBNA6411"]),
    (LB, "Linksys", "Dual-WAN load-balancing routers", "Multi-WAN load balance router", "Desktop", EOS, WEB,
     "https://www.smallnetbuilder.com/lanwan/lanwan-reviews/linksys-lrt224-dual-wan-gigabit-vpn-router-reviewed/", ["LRT224"]),
    (LB, "Grandstream", "GWN multi-WAN routers", "Multi-WAN load balance router", "Desktop / 1RU", C, SCRAPED,
     "https://www.grandstream.com/de/products/networking-solutions/routers/product/gwn7001_gwn7002_gwn7003",
     ["GWN7001", "GWN7002", "GWN7003"]),
    (LB, "Cisco", "LocalDirector (end of life)", LEG, "1RU", EOSUP, SCRAPED,
     "https://www.cisco.com/c/en/us/support/application-networking-services/localdirector-417g/model.html",
     ["LocalDirector 410", "LocalDirector 416", "LocalDirector 417G"]),
    (LB, "Topsec", "TopApp load balance system", ADC, "1RU-2RU", C, WEB,
     "https://www.ipv6ready.org/db/index.php/public/logo/02-C-001521", ["TopApp LB"]),
    (LB, "D-Link", "Load-balancing routers (older)", "Multi-WAN load balance router", "Desktop", EOSUP, WEB,
     "https://maisonbisson.com/post/dual-wan-or-multi-wan-load-balancing-routers/", ["DI-LB604"]),
]

# ---------------------------------------------------------------------- 2026-10-19: gaps found against the NetBox device-type library
NB = "NetBox device-type library (community)"
NBU = "https://github.com/netbox-community/devicetype-library/tree/master/device-types/"
VPN_GW, WHITE, DDOS, OT = "VPN / crypto gateway", "White-box firewall appliance", "DDoS protection", "OT security sensor"
OOB, IOT = "Console / out-of-band gateway", "IoT / LoRaWAN gateway"
ADD += [
    # firewalls: new vendors
    (FW, "FireBrick", "FireBrick firewall / routers", NGFW, "1RU", C, WEB, NBU + "FireBrick", ["FB2900", "FB6202"]),
    (FW, "Ideco", "Ideco UTM appliances", UTM, "1RU", C, WEB, NBU + "Ideco", ["Ideco LX-plus"]),
    (FW, "Infotecs", "ViPNet Coordinator", VPN_GW, "1RU", C, WEB, NBU + "Infotecs",
     ["ViPNet Coordinator HW1000C", "ViPNet Coordinator HW1000-Q7"]),
    (FW, "SecurityCode", "Continent IPC", VPN_GW, "Desktop / 1RU", C, WEB, NBU + "SecurityCode", ["IPC-10", "IPC-50", "IPC-R50"]),
    (FW, "Secunet", "SINA Box", VPN_GW, "1RU", C, WEB, NBU + "Secunet",
     ["SINA L2 Box S 10G", "SINA L3 Box S 2G", "SINA L3 Box S 800M"]),
    (FW, "Rohde & Schwarz", "SITLine ETH / TrustedVPN", VPN_GW, "1RU", C, WEB, NBU + "Rohde%20%26%20Schwarz",
     ["SITLine ETH-S-1G", "SITLine ETH-S-10G", "SITLine ETH-L-4x1G", "SITLine ETH-L-10G", "SITLine ETH-L-4x10G",
      "SITLine ETH-XL-4x1-10G", "SITLine ETH-XL-8x1-10G", "SITLine ETH-XL-1x100G", "SITLine ETH-XL-2x100G",
      "SITLine ETH4G", "SITLine ETH40G", "TrustedVPN L", "TrustedVPN XL"]),
    (FW, "CryptoPro", "NGate TLS gateways", VPN_GW, "1RU", C, WEB, NBU + "CryptoPro", ["NGate 320", "NGate 600"]),
    (FW, "Arbor (NETSCOUT)", "TMS / Sightline", DDOS, "1RU-2RU", C, WEB, NBU + "Arbor", ["TMS-HD1000"]),
    (FW, "Nozomi Networks", "Guardian sensors", OT, "1RU", C, WEB, NBU + "Nozomi%20Networks", ["NSG-M-750", "NSG-M-1000"]),
    (FW, "PC Engines", "APU boards (firewall hardware)", WHITE, "Desktop", EOS, WEB, NBU + "PC%20Engines", ["APU", "APU2", "APU4", "APU6"]),
    (FW, "Qotom", "Mini PC firewall appliances", WHITE, "Desktop", C, WEB, NBU + "Qotom", ["Q330G4"]),
    (FW, "Lanner", "Network appliances (firewall OEM)", WHITE, "1RU", C, WEB, NBU + "Lanner", ["NCA-533"]),
    # firewalls: models missing from known vendors
    (FW, "Fortinet", "FortiGate (more models)", NGFW, "Desktop / 1RU", EOS, WEB, NBU + "Fortinet",
     ["FG-51E", "FG-80D", "FG-90E", "FG-91E", "FG-3301E", "FGR-70G-5G-Dual"]),
    (FW, "Cisco", "Secure Firewall 200", NGFW, "Desktop", C, WEB, NBU + "Cisco", ["CSF220"]),
    (FW, "Palo Alto Networks", "PA-500", UTM, "Desktop", C, WEB, NBU + "Palo%20Alto%20Networks", ["PA-545-POE", "PA-555-POE"]),
    (FW, "SonicWall", "TZ", UTM, "Desktop", C, WEB, NBU + "SonicWall", ["TZ250", "TZ250W"]),
    (FW, "Sophos", "XGS desktop", UTM, "Desktop", C, WEB, NBU + "Sophos", ["XGS 118"]),
    (FW, "Stormshield", "SN range", NGFW, "Rack", C, WEB, NBU + "Stormshield", ["SN2200"]),
    # gateways: new vendors
    (GW, "Opengear", "Out-of-band gateways / console servers", OOB, "1RU", C, WEB, NBU + "Opengear",
     ["ACM5003-M-F-E", "ACM7004-2-L", "ACM7004-2-LMP", "ACM7008-2-L", "ACM7008-2-M", "CM7116-2", "CM7132-2", "CM7148-2",
      "CM7196-2", "CM8116", "CM8132", "CM8148", "CM8196-10G", "IM4216-2", "IM4232-2", "IM4248-2", "IM7216-2", "IM7232-2",
      "IM7248-2", "OM1208-8E-L", "OM2216", "OM2216-L"]),
    (GW, "ZPE Systems", "Nodegrid", OOB, "Desktop / 1RU", C, WEB, NBU + "ZPE",
     ["Nodegrid BSR", "Nodegrid GSR", "Nodegrid NSC-T48R", "Nodegrid NSR", "Nodegrid NSR Lite"]),
    (GW, "Avocent (Vertiv)", "ACS console servers", OOB, "1RU", C, WEB, NBU + "Avocent",
     ["ACS 6016", "ACS 6048", "ACS 8008", "ACS 8016", "ACS 8032", "ACS 8048", "ACS 5004", "ACS 5008", "ACS 5016", "ACS 5032",
      "ACS 5048", "ACS16", "ACS48", "Cyclades TS-1000"]),
    (GW, "WTI", "Console servers", OOB, "1RU", C, WEB, NBU + "WTI",
     ["CPM-800-1", "CPM-800-2", "CPM-1600-1", "DSM-8NM", "DSM-24NM", "DSM-40NM"]),
    (GW, "Digi", "Console servers", OOB, "1RU", EOS, WEB, NBU + "Digi", ["CM 16", "CM 32", "CM 48", "PortServer TS 8", "PortServer TS 16"]),
    (GW, "Lantronix", "ETS terminal servers (older)", OOB, "1RU", EOS, WEB, NBU + "Lantronix", ["ETS16PR", "ETS32PR"]),
    (GW, "Kerlink", "Wirnet LoRaWAN gateways", IOT, "Outdoor / indoor", C, WEB, NBU + "Kerlink",
     ["Wirnet Station", "Wirnet iStation", "Wirnet iFemtoCell"]),
    (GW, "Eltex", "SMG / TAU / TOPGATE voice gateways", MG, "Desktop / 1RU", C, WEB, NBU + "Eltex",
     ["SMG-200", "SMG-1016M", "TAU-72.IP", "TOPGATE-4E1-2FG"]),
    (GW, "Allot", "Service Gateway", "Service / DPI gateway", "1RU-2RU", C, WEB, NBU + "Allot", ["SSG-500"]),
    (GW, "BiZone", "SD-WAN customer edge", "SD-WAN / edge gateway", "Desktop / 1RU", C, WEB, NBU + "BiZone",
     ["CE20n-3t", "CE20n-3t2m", "CE50n-4t1s", "CE100n-4t1s", "CE300n-4t2s2xs", "CE500n-4t4xs", "CE1000n-8t2n",
      "CE1000n-8t2n-4xs", "CE1000n-8t2n-8xs", "CE3000d-2t4xs"]),
    (GW, "Teltonika Networks", "TRB gateways", "Cellular / industrial gateway", "Industrial / DIN", C, WEB, NBU + "Teltonika", ["TRB501"]),
    (GW, "Fortinet", "FortiExtender (older)", "Cellular / industrial gateway", "Desktop", EOS, WEB, NBU + "Fortinet", ["FEX-40D"]),
]

# ---------------------------------------------------------------------- Qi-AnXin and Venustech models (Chinese-language sources, names as printed)
REMOVE += [
    (FW, "Qi-AnXin", "Qi-AnXin NSG (models: verify)", "replaced by the models found"),
    (FW, "Venustech", "Venustech USG-FW (models: verify)", "replaced by the models found"),
]
ADD += [
    (FW, "Qi-AnXin", "QAX Legendsec NSG / SecGate 3600", NGFW, "1RU-3RU", C, SCRAPED,
     "https://www.legendsec.com/newsec.php?up=2&cid=2",
     ["NSG3000-TE35M-Q", "NSG3200", "NSG3300", "NSG5000-TG35M-QYD", "NSG5000-TG65M-Q", "NSG7000-TX35M-Q",
      "NSG7000-TX55M-Q", "NSG7000-TX65M-QW", "NSG9000-TZ01M-Q", "NSG9000-TZ50M-Q"]),
    (FW, "Venustech", "Venusense USG firewalls", NGFW, "1RU-2RU", C, WEB, "https://detail.zol.com.cn/firewall/venustech/",
     ["USG-FW-310B", "USG-FW-310-T-NF-C600", "USG-FW-3600D", "USG-FW-4000D", "USG-FW-4000E", "USG-FW-4010D",
      "USG-FW-4000-T-NF2100", "USG-FW-8000D", "USG-FW-12600GP", "USG-610A", "USG-810A"]),
]
REMOVE += [(GW, "Alcatel-Lucent Enterprise", "OmniAccess SD-WAN (models: verify)", "no SD-WAN model list found; gateways added below")]
ADD += [(GW, "Alcatel-Lucent Enterprise", "OmniAccess gateways / controllers", "Business / SMB gateway", "1RU", EOS, WEB,
         "https://shop.avoira.com/catalog/product/view/id/9450968/category/3", ["OAW-4010", "OAW-4030"])]

# ---------------------------------------------------------------------- 2026-10-19: devices in the user's test folders
PHOTO = "Seen on a photographed unit (test set)"
ADD += [
    (FW, "Arista Networks", "Edge Threat Management NG Firewall Q series", UTM, "Desktop / 1RU", EOS, SCRAPED,
     "https://www.arista.com/en/support/advisories-notices/end-of-sale/23673-end-of-sale-of-the-arista-edge-threat-management-ng-firewall-q-series-appliances",
     ["Q4", "Q8", "Q8W", "Q12", "Q20"]),
    (FW, "AhnLab", "TrusGuard XTG", NGFW, "Desktop / rack", C, SCRAPED, "https://download.ahnlab.com/kr/site/brochure/Brochure_TrusGuard.pdf",
     ["XTG40", "XTG50", "XTG70", "XTG100", "XTG300", "XTG500", "XTG1000", "XTG2000", "XTG5000", "XTG10000", "XTG20000"]),
    (FW, "Allied Telesis", "ARX200S UTM firewall routers", UTM, "Desktop", C, SCRAPED,
     "https://www.alliedtelesis.com/us/en/products/security-appliances/arx200s-gtx", ["ARX200S-GT", "ARX200S-GTX"]),
    (GW, "Quantum Networks", "SD-WAN / security gateways", "SD-WAN / edge gateway", "Desktop / 1RU", C, PHOTO,
     "https://www.zengroup.co.in/quantum-networks", ["QN-S-50", "QN-S-200"]),
    (GW, "AKOM Technologies", "PRI gateways", MG, "Desktop", C, PHOTO, "https://m.indiamart.com/akomtechnologies/profile.html",
     ["TGL2001", "TGL2002"]),
    (GW, "Dinstar", "DAG / MTG", MG, "Desktop / 1RU", C, WEB, "https://www.freeswitch.org/confluence/display/FREESWITCH/Gateway+Dinstar",
     ["DAG2000-32S", "DAG2000-16O", "DAG1000-4S4O", "DAG1000-4O"]),
    (GW, "Dinstar", "DAG / MTG", MG, "1RU", C, PHOTO, "", ["DAG2500-96S", "MTG2000B"]),
    (LB, "F5", "3-DNS controller (end of life)", LEG, "1RU-2RU", EOSUP, PHOTO, "", ["3-DNS"]),
]
