"""Web check of every 'Reviewer round 2: product knowledge - verify' row (2026-10-22).

Each model the second review added or renamed from product knowledge was searched for online
(vendor pages, data sheets, end-of-life notices, distributor listings). Applied last, to all four
catalogues, by router_catalog/review2_changes.py and appliance_catalog/review2.py:

  CONFIRMED  found online: source becomes CHECKED, link kept ('Checked at' / Change log)
  STATUS     status corrected from the vendor's end-of-life notice
  RENAME     the name as the vendor writes it
  DROP       not found anywhere (or not a product name): removed, so no guessed name stays
  ADD        real models found while checking, missing from the catalogues
"""
C, EOS, EOSUP, CHECK = "Current", "End-of-sale", "End of support", "Check"
CHECKED = "Reviewer round 2: confirmed online (link in 'Checked at' / Change log)"

CONFIRMED = [   # vendor, [models], url
    ("H3C", ["F1000-AK108", "F1000-AK109", "F1000-AK110", "F1000-AK115", "F1000-AK120", "F1000-AK125", "F1000-AK130",
             "F1000-AK135", "F1000-AK140", "F1000-AK145", "F1000-AK150", "F1000-AK155", "F1000-AK160", "F1000-AK170"],
     "https://www.h3c.com/cn/d_202208/1675235_30005_0.htm"),
    ("Cisco", ["C896VA", "C897VA", "C897VAW"], "https://www.touchpoint.com.au/shop/isr-890-cisco-890-integrated-services-router/"),
    ("Huawei", ["AR651", "AR651W", "AR651C", "AR657W"], "https://e.huawei.com/th/products/routers/netengine-ar650"),
    ("Huawei", ["AR651F-Lite"], "https://toko.nale.co.id/produk/huawei-enterprise-router-ar651f-lite/"),
    ("Huawei", ["CX600-X3", "CX600-X8", "CX600-X16"], "https://carrier.huawei.com/tr/products/fixed-network/data-communication/router/cx600"),
    ("Huawei", ["NE9000-8", "NE9000-20"], "https://carrier.huawei.com/en/products/fixed-network/data-communication/router/ne9000"),
    ("Huawei", ["NE5000E-20"], "https://carrier.huawei.com/en/products/fixed-network/carrier-ip/router/ne5000e"),
    ("Ciena", ["8110", "8112", "8114", "8140"], "https://opticalconnectionsnews.com/2022/10/ciena-expands-8100-router-range/"),
    ("Ciena", ["8192"], "https://www.ciena.com/products/8192"),
    ("RAD", ["SecFlow-2", "SecFlow-4"], "https://alliancecorporation.ca/wp-content/uploads/2018/07/SecFlow-4.pdf"),
    ("RAD", ["ETX-203AX", "ETX-220A"], "https://www.rad.com/wp-content/uploads/2024/10/etx-2_ds_0.pdf"),
    ("NetComm", ["NTC-400", "NTC-500"], "https://www.esis.com.au/new-ntc-500-industrial-iot-router-maximising-5g-connectivity/"),
    ("Cisco", ["CRS-4/S", "CRS-8/S", "CRS-16/S"], "https://www.cisco.com/c/en/us/products/collateral/routers/carrier-routing-system/eos-eol-notice-c51-744822.html"),
    # firewalls
    ("H3C", ["F5000-AI-160"], "https://www.h3c.com/en/Products_and_Solutions/Security/Security/Products/Boundary_security/LM/F5000-AI/"),
    ("Fortinet", ["FG-7030E"], "https://docs.fortinet.com/document/fortigate-7000/hardware/fortigate-7030e-system-guide/757726/chassis-hardware-information"),
    ("Fortinet", ["FG-60E-POE", "FG-80E-POE", "FG-81E-POE", "FG-140E-POE"], "https://www.parkplacetechnologies.com/?p=27063"),
    ("Fortinet", ["FG-100EF"], "https://www.parkplacetechnologies.com/fr/eosl/fortinet/fortigate-100ef-usg/"),
    ("Fortinet", ["FWF-61F"], "https://www.shi.com/product/40516723/Fortinet-FortiWiFi-61F"),
    ("Fortinet", ["FG-81F-POE"], "https://datacenter360.ca/fortinet/fortigate/fortigate-81f-poe-firewall/"),
    ("Fortinet", ["FG-80F-BYPASS"], "https://datacenter360.ca/fortinet/fortigate/fortigate-80f-bypass-firewall/"),
    ("Fortinet", ["FGR-30D", "FGR-35D"], "https://www.shi.com/product/40145188/Fortinet-FortiGate-Rugged-30D"),
    ("Fortinet", ["FGR-90D"], "https://www.touchpoint.com.au/shop/fgr-90d-fortigate-90d-ruggedized-w-5-x-ge-rj45-switch-ports-including-1x-pair-ge-bypass-rj45-ports-2-x-ge-sfp-slots-2x-db9-serial-console-dual-power-input/"),
    ("Hillstone Networks", ["X9180"], "https://hillstonenet.com/wp-content/uploads/Hillstone_X-Series_X9180_V5.5R10_EN.pdf"),
    ("H3C", ["F100-C-G5", "F100-S-G5", "F100-M-G5", "F100-A-G5", "F100-E-G5"],
     "https://www.h3c.com/cn/Products_And_Solution/Proactive_Security/Product_Series/Border_Security/FW_VPN/F100/F100_G5/"),
    ("H3C", ["F100-C-G3", "F100-A-G3", "F100-C-A3"], "https://network-switch.com/collections/h3c-f-100-series-smb-next-gen-firewalls"),
    ("Hillstone Networks", ["E2300", "E2800"], "https://hillstonenet.com/wp-content/uploads/Hillstone_E-2000_Series_EN.pdf"),
    ("Hillstone Networks", ["E6168", "E6360"], "https://www.hillstonenet.com/wp-content/uploads/Hillstone_E-6000_V5.5R5_EN.pdf"),
    ("Hillstone Networks", ["X7180", "X8180"], "https://www.hillstonenet.com/wp-content/uploads/Hillstone_X-Series_V5.5R5_EN.pdf"),
    ("Hillstone Networks", ["X10800"], "https://www.hillstonenet.com/resources/hillstone-x10800-data-center-firewalls/"),
    ("WatchGuard", ["T10", "T15", "T30", "T35", "T50", "T55", "T70", "M200", "M300", "M400", "M440", "M500", "M4600", "M5600"],
     "https://www.watchguard.com/wgrd-trust-center/end-of-life-policy"),
    ("WatchGuard", ["XTM 25", "XTM 26", "XTM 33", "XTM 330", "XTM 505", "XTM 510", "XTM 520", "XTM 530", "XTM 810",
                    "XTM 820", "XTM 830", "XTM 1050", "XTM 2050"], "https://watchguard.com/node/93306"),
    ("WatchGuard", ["T45-PoE", "T45-W-PoE"], "https://www.watchguard.com/help/docs/help-center/en-US/content/en-us/Hardware-Guides/firebox-t45-PoE-hardware-guide.html"),
    ("WatchGuard", ["T85-PoE", "T115-W"], "https://www.streamnetworks.co.uk/hardware/watchguard-t45w-poe/"),
    ("WatchGuard", ["T125-W"], "https://www.watchguard.com/help/docs/help-center/en-US/content/en-us/Hardware-Guides/firebox-t125-hardware-guide.html"),
    ("WatchGuard", ["T145-W"], "https://www.watchguard.com/help/docs/help-center/en-US/content/en-us/Hardware-Guides/firebox-t145-hardware-guide.html"),
    ("SonicWall", ["TZ300W", "TZ300P", "TZ350W", "TZ400W", "TZ500W", "TZ600P"],
     "https://www.sonicwall.com/support/knowledge-base/what-are-the-gen6-tz-series-features-and-capabilities/170505346045054/"),
    ("SonicWall", ["TZ 215W", "TZ 205W", "TZ 105W"], "https://csrc.nist.gov/projects/cryptographic-module-validation-program/certificate/2190"),
    ("SonicWall", ["TZ 100W"], "https://endoflife.ai/score/sonicwall-firewalls/TZ100W"),
    ("SonicWall", ["SMA 6200", "SMA 6210", "SMA 7200", "SMA 7210"],
     "https://www.sonicwall.com/support/technical-documentation/docs/sma_1000-12-5-admin_guide/Content/Introduction/sonicwall-sma-appliance-models.htm"),
    ("SonicWall", ["SMA 210", "SMA 410", "SRA 1600", "SRA 4600", "SRA 4200"], "https://www.theregister.com/2021/07/15/sonicwall_secure_access/"),
    ("Ivanti (Pulse Secure)", ["PSA 300", "PSA 3000", "PSA 5000", "PSA 7000c", "PSA 7000f"],
     "https://www.macnica.co.jp/en/business/security/manufacturers/pulsesecure/spec.html"),
    ("Ivanti (Pulse Secure)", ["MAG 2600", "MAG 4610", "MAG 6610", "MAG 6611"], "https://www.macnica.co.jp/en/business/security/2016/news_01.html"),
    ("Ivanti (Pulse Secure)", ["SA 2500", "SA 4500", "SA 6500"], "https://www.parkplacetechnologies.com/end-of-service-life/juniper/sa2500/"),
    ("Barracuda", ["F183R"], "https://documentation.campus.barracuda.com/wiki/spaces/NGFEOL/pages/5507004"),
    ("Zyxel", ["USG FLEX 50H", "USG FLEX 50HP"], "https://www.shi.com/product/49745230/Zyxel-USG-Flex-H-Series-50HP"),
    ("Zyxel", ["ZyWALL 110", "ZyWALL 310", "ZyWALL 1100"], "https://endoflife.ai/zyxel-usg/ZyWALL-1100"),
    ("Zyxel", ["USG 100", "USG 200", "USG 300", "USG 1000", "USG 2000"], "https://katalog.atcomp.cz/katalog/50100111/ZYWALL_USG.pdf"),
    ("Juniper", ["SRX550M"], "https://www.parkplacetechnologies.com/eosl/juniper/srx550-m-sys-je-ac/"),
    ("Cisco", ["ISA-3000-4C-FTD", "ISA-3000-2C2F-FTD"], "https://www.avendor.com/products/isa-3000-4-copper-ports-ftd-unified-image"),
    ("Trend Micro", ["660N", "1400N"], "https://success.trendmicro.com/en-US/solution/KA-0016971"),
    ("Trend Micro", ["2500N", "5100N", "6100N"], "https://sec-certs.org/fips/id/1709"),
    ("Hillstone Networks", ["S1060", "S2160", "S3560", "S5560"], "https://www.hillstonenet.com/wp-content/uploads/Hillstone_S-Series_V3.4_EN.pdf"),
    ("IBM", ["XGS 7100"], "https://www.ibm.com/support/pages/node/158467"),
    ("IBM", ["XGS 3100", "XGS 4100", "XGS 5100"], "https://www.peerspot.com/products/ibm-security-network-ips/archived_reviews"),
    ("Radware", ["DefensePro 20", "DefensePro 60", "DefensePro 200", "DefensePro 400", "DefensePro x420", "DefensePro x4420"],
     "https://cdn.blueally.com/radappliances/datasheets/tech-specs/defensepro-tech-spec.pdf"),
    ("A10 Networks", ["Thunder TPS 4435", "Thunder TPS 5435", "Thunder TPS 6435", "Thunder TPS 14045"], "https://sec-certs.org/cc/id/SERTIT-062"),
    ("A10 Networks", ["Thunder TPS 3030S"], "https://www.ithome.com.tw/review/91225"),
    ("Fortinet", ["FortiDDoS 200F", "FortiDDoS 1500F", "FortiDDoS 2000F"], "https://www.fortinet.com/br/products/ddos/fortiddos"),
    ("Fortinet", ["FortiDDoS 200B", "FortiDDoS 400B", "FortiDDoS 800B", "FortiDDoS 1200B", "FortiDDoS 2000B", "FortiDDoS 3000B"],
     "https://docs.fortinet.com/document/fortimanager/6.2.0/release-notes/987297"),
    ("Arbor (NETSCOUT)", ["APS 2600", "APS 2800"],
     "https://store.area9.com.au/products/arbor-networks-aps-2800-40g-ac-lab-2ru-appliance-dual-ac-power-licensed-for-40-gbps-of-inspect-1455281"),
    ("Arbor (NETSCOUT)", ["TMS 2800"], "https://cis.bakotech.com/uploads/ckeditor/files/arbor_threat_mitigation_system.pdf"),
    ("Arbor (NETSCOUT)", ["TMS 4000"], "https://www.scribd.com/document/775875930/PocketGuide"),
    ("Seqrite (Quick Heal)", ["T2S-30", "T2S-60"], "https://www.seqrite.com/unified-threat-management-faq"),
    ("Endian", ["Mercury 50"], "https://admin-magazine.com/Articles/New-versions-of-the-Endian-and-Sophos-UTM-solutions"),
    ("Smoothwall", ["S4", "S8", "S10", "S14"], "https://kb.smoothwall.com/hc/en-us/articles/360008340759-Smoothwall-Hardware-Appliance-Specifications"),
    ("Calyptix", ["AE800", "AE1200"], "https://www.calyptix.com/?p=4184"),
    ("SECUI", ["MF2 1000"], "https://www.ebay.de/itm/277478769520"),
    ("Cisco", ["IC3000"], "https://www.cisco.com/c/en/us/td/docs/routers/ic3000/release-notes/IC3000-Release-Note-1-2-1.html"),
    # load balancers
    ("Barracuda", ["230", "330", "430"], "https://cdn.blueally.com/barraguard/datasheets/products/barracuda_link_balancer_ds_us.pdf"),
    ("Barracuda", ["240"], "https://cdn.blueally.com/barraguard/datasheets/products/barracuda_load_balancer_adc_ds_us.pdf"),
    ("Radware", ["OnDemand Switch 1", "OnDemand Switch 2", "OnDemand Switch 3"],
     "https://www.radware.com/documents/press-releases/radware-takes-ondemand-switch™-platforms-to-the-next-level"),
    ("Cisco", ["CSS 11150"], "https://www.cisco.com/c/en/us/support/application-networking-services/css-11150-content-services-switch/model.html"),
    ("Cisco", ["CSS 11050"], "https://www3-realm.cisco.com/en/US/products/hw/contnetw/ps789/index.html"),
    ("NetScaler (Citrix)", ["NetScaler 7000", "NetScaler 9010", "NetScaler 10010", "NetScaler 12000"],
     "https://www.macnica.co.jp/en/business/list_closed/citrix/"),
    ("Fortinet", ["FortiADC 60F"], "https://www.touchpoint.com.au/shop/fad-60f-fortiadc-application-delivery-controller-w-5-x-ge-rj45-ports-64gb-ssd/"),
    ("F5", ["1000"], "https://www.eol.network/f5/big-ip-legacy/1000"),
    ("F5", ["2400", "5100"], "https://www.gotoknow.org/posts/327500"),
    ("Array Networks", ["APV5200"], "https://www.itreseller.ch/Artikel/25644/Effizientes_Load_Balancing_von_Array.html"),
    ("Piolink", ["PAS-K1016"], "https://detail.zol.com.cn/load_leveling/beijing/3.html"),
    ("Peplink", ["EPX"], "https://cdn.shopify.com/s/files/1/0740/6134/9161/files/peplink_epx_datasheet.pdf"),
    # gateways
    ("AudioCodes", ["Mediant 2600B"], "https://www.shi.com/product/51390308/AudioCodes-Mediant-2600B"),
    ("Ribbon", ["EdgeMarc 2900e"], "https://ipphone-warehouse.com/ribbon-edgemarc-2900e-70-calls-enterprise-sbc-p/2900e-100-0070.htm"),
    ("Ribbon", ["EdgeMarc 4806", "EdgeMarc 4800"],
     "https://learn.rbbn.com/hubfs/Corporate%20Marketing%20(TOP%20LEVEL)/Data%20Sheets/DS%20EdgeMarc%204806%204808%20Multi-Service%20Gateways.pdf"),
    ("Ribbon", ["EdgeMarc 4550"], "https://help.8x8.com/docs/edgemarc-edgewater-router-installing-upgrading-and-configuration"),
    ("Avaya", ["G430", "G450"], "https://documentation.avaya.com/en-us/home/bundle/communication-manager/avayacommunicationmanagerhardwaredescriptionr102x/branch-gateways-andintegratedgateways/AvayaG450MediaGateway.html"),
    ("HMS Networks", ["AB7007"], "https://www.digikey.com/es/product-highlight/h/hms-industrial-networks/anybus-serial-to-industrial-protocol-converters"),
    ("Avocent (Vertiv)", ["ACS 5004", "ACS 5008", "ACS 5016", "ACS 5032", "ACS 5048"],
     "https://www.vertivco.com/en-us/products-catalog/monitoring-control-and-management/itmanagement/avocent-acs-5000-serial-consoles/"),
    ("Digi", ["Connect IT 4", "Connect IT 16", "Connect IT 48", "Connect IT Mini"],
     "https://www.nasdaq.com/press-release/digi-international-introduces-digi-connect-it-family-of-console-access-servers-for"),
    ("Raritan (Legrand)", ["DSX2-4", "DSX2-8", "DSX2-16", "DSX2-32", "DSX2-48"],
     "https://www.raritan.com/assets/ram/downloads/documents/Raritan-KVM-EOL-Announce-DSX2-20250602.pdf"),
    ("Raritan (Legrand)", ["DSX-16", "DSX-32"], "https://www.raritan.com/support/product/dominion-sx/sx-version-3.1.6"),
    ("Perle", ["IOLAN SCG18"], "https://shop.ascend.de/en/products/perle-iolan-console-server-scg18-04033920"),
    ("Perle", ["IOLAN SCG34"], "https://www.shi.com/product/36439757/Perle-IOLAN-SCG34-RU"),
    ("Perle", ["IOLAN SCG50"], "https://www.shi.com/product/36439749/Perle-IOLAN-SCG50-R-Console-server"),
    ("Perle", ["IOLAN STS8", "IOLAN STS16", "IOLAN STS24"], "https://perlesystems.fr/products/iolan-sts-terminal-server.shtml"),
    ("Black Box", ["LES1516A", "LES1532A", "LES1548A"], "https://www.farnell.com/datasheets/3542618.pdf"),
    ("Black Box", ["LES1608A"], "https://datasheets.globalspec.com/ds/Black-Box/les1608a/49b3e0db-81f6-4b60-86eb-5fdec73c0345"),
    ("Black Box", ["LES1508A"], "https://www.manualslib.com/manual/899666/Black-Box-Les1508a.html"),
    ("Opengear", ["OM2224-24E", "OM2248-L"], "https://opengear.com/?p=3555"),
    ("ZPE Systems", ["Nodegrid Mini SR", "Nodegrid Hive SR", "Nodegrid Net SR", "NSCP-T48R"], "https://zpesystems.com/products/"),
    ("Cisco", ["VG202", "VG204", "VG248"], "https://www.cisco.com/c/en/us/products/collateral/unified-communications/vg-series-gateways/end_of_life_notice_c51-728269.pdf"),
    ("Cisco", ["ATA190"], "https://www.cisco.com/c/en/us/products/collateral/unified-communications/ata-190-series-analog-telephone-adapters/eos-eol-notice-c51-741354.html"),
    ("Avaya", ["G700"], "https://support.avaya.com/css/public/documents/101046670"),
    ("Innovaphone", ["IP0011", "IP411", "IP811", "IP1130", "IP3011", "IP6010"],
     "https://www.innovaphone.com//content/downloads/Uebersicht_VoIP_Gateways_EN.pdf"),
    ("Dialogic", ["DMG1000", "DMG2000", "DMG4000"], "https://info.scansource.com/solutions/suppliers/dialogic/products/media-gateways-dmg"),
    ("MultiTech", ["MVP210", "MVP410", "MVP810"], "https://shoshin.co.jp/c/mt/brands/multivoip.html"),
    ("Mitel", ["3300 CX", "3300 CXi", "3300 CXi II", "3300 MXe", "3300 MXe III", "3300 AX"],
     "https://www.techtarget.com/searchunifiedcommunications/feature/Mitel-VoIP-3300-series-controllers-Product-overview"),
    ("Milesight", ["UG56", "UG63", "UG65", "UG67"], "https://www.milesight.com/docs/en/mini-gateway/ug63/overview.html"),
    ("RAK Wireless", ["RAK7268", "RAK7268C", "RAK7289", "RAK7289C"], "https://docs.rakwireless.com/product-categories/wisgate/"),
    ("Tektelic", ["Kona Micro", "Kona Macro", "Kona Mega", "Kona Enterprise"], "https://knowledgehub.tektelic.com/lorawan-gateways"),
    ("Cisco", ["IXM-LPWA-800", "IXM-LPWA-900"], "https://www.cisco.com/c/en/us/products/collateral/se/internet-of-things/datasheet-c78-737307.html"),
    ("Kerlink", ["Wirnet iBTS", "Wirnet iZeptoCell"], "https://docs.kerlink.com/wirnet-productline"),
    ("Sandvine", ["PTS 22000", "PTS 24000", "PTS 32000"],
     "https://www.sandvine.com/hubfs/Sandvine_Redesign_2019/Downloads/Datasheets/sandvine-ds-policy-traffic-switch.pdf"),
    ("Siemens", ["IE/PB Link PN IO", "IE/AS-i Link PN IO"], "https://mall.industry.siemens.com/mall/en/us/Catalog/Product/6GK14115AB10"),
]

STATUS = [   # vendor, [models], status, url  (end-of-life notices)
    ("NetComm", ["NTC-400"], EOS, "https://store.dorks.com.au/products/netcomm-ntc-400-4g-lte-cat6-industrial-router-2583797"),
    ("Fortinet", ["FGR-90D"], EOSUP, "https://avfirewalls.com/fortigaterugged-90d.asp"),
    ("Hillstone Networks", ["X9180"], EOS, "https://eol.network/hillstone/x-series-dc-firewall/X9180-Data-Center-Firewall"),
    ("Hillstone Networks", ["S2160", "S3560"], EOS, "https://www.hillstonenet.com/wp-content/uploads/Hillstone_S-Series_NIPS_5.5_EN.pdf"),
    ("WatchGuard", ["T35", "T55", "T70", "M440"], EOSUP, "https://www.watchguard.com/wgrd-trust-center/end-of-life-policy"),
    ("SonicWall", ["TZ 215W", "TZ 205W", "TZ 105W", "TZ 100W"], EOSUP, "https://endoflife.ai/score/sonicwall-firewalls/TZ100W"),
    ("SonicWall", ["SMA 7200"], EOSUP, "https://www.sonicwall.com/support/product-lifecycle-tables/sonicwall-secure-mobile-access/hardware"),
    ("SonicWall", ["SRA 1600", "SRA 4600", "SRA 4200"], EOSUP, "https://www.theregister.com/2021/07/15/sonicwall_secure_access/"),
    ("Ivanti (Pulse Secure)", ["MAG 2600", "MAG 4610", "MAG 6610", "MAG 6611", "SA 2500", "SA 4500", "SA 6500"], EOSUP,
     "https://www.macnica.co.jp/en/business/security/2016/news_01.html"),
    ("Trend Micro", ["660N", "1400N", "2500N", "5100N", "6100N"], EOSUP, "https://success.trendmicro.com/en-US/solution/KA-0016971"),
    ("Fortinet", ["FortiDDoS 200B", "FortiDDoS 400B", "FortiDDoS 800B", "FortiDDoS 1200B", "FortiDDoS 2000B", "FortiDDoS 3000B"],
     EOSUP, "https://docs.fortinet.com/document/fortimanager/6.2.0/release-notes/987297"),
    ("Fortinet", ["FortiDDoS 200F", "FortiDDoS 1500F", "FortiDDoS 2000F"], C, "https://www.fortinet.com/br/products/ddos/fortiddos"),
    ("Calyptix", ["AE1200"], EOS, "https://www.calyptix.com/?p=4184"),
    ("Raritan (Legrand)", ["DSX2-4", "DSX2-8", "DSX2-16", "DSX2-32", "DSX2-48"], EOS,
     "https://www.raritan.com/assets/ram/downloads/documents/Raritan-KVM-EOL-Announce-DSX2-20250602.pdf"),
    ("Cisco", ["VG202", "VG204", "VG248"], EOSUP,
     "https://www.cisco.com/c/en/us/products/collateral/unified-communications/vg-series-gateways/end_of_life_notice_c51-728269.pdf"),
    ("Innovaphone", ["IP3011", "IP6010"], EOS, "https://www.innovaphone.com/en/hardware/gateways/ip411.html"),
    ("MultiTech", ["MVP210", "MVP410", "MVP810"], EOS, "https://shoshin.co.jp/c/mt/brands/multivoip.html"),
]

RENAME = [   # vendor, old, new, url
    ("Cisco", "C897VAM", "C897VA-M", "https://au.stockinthechannel.com/Product/Cisco-C897VA-M-K9-wired-router-Gigabit-Ethernet-Black/8733"),
    ("Huawei", "CX600-X1", "CX600-X1-M", "https://cp.yiqi.com/items/10067699be.html"),
    ("Huawei", "CX600-X2", "CX600-X2-M", "https://cp.yiqi.com/items/10067699be.html"),
    ("Raritan (Legrand)", "DSX-48", "DSXA-48", "https://raritan.com/jp/support/eol-eos-list"),
    ("Opengear", "OM2248", "OM2248-10G", "https://opengear.com/?p=3555"),
    ("Opengear", "CM8116-L", "CM8116", "https://opengear.com/?p=3555"),
    ("Opengear", "ACM7004-5", "ACM7004-5-L", "https://opengear.com/?p=3555"),
    ("Opengear", "IM7208-2", "IM7208", "https://opengear.com/?p=3555"),
    ("Opengear", "IM7216-2-24E", "IM7216", "https://opengear.com/?p=3555"),
    ("NewRock", "MX8", "MX8A", "https://bcstrategies.com/New-Rock-Technologies-MX-Gateways-and-ATAs-BroadSoft-Certified"),
    ("NewRock", "MX60", "MX60E", "https://b3networks.helpjuice.com/newrock-voip-gateway"),
    ("NewRock", "MX120", "MX120G", "https://b3networks.helpjuice.com/newrock-voip-gateway"),
    ("NewRock", "MX100", "MX100-AG", "https://infotel.ua/en/vendor-post/newrock"),
    ("Matrix Comsec", "SETU VFXTH", "SETU VFXTH0016",
     "https://www.matrixcomsec.com/products/product/matrix-voip-fxo-fxs-gateway-with-16-voip-and-16-fxs-ports-setu-vfxth0016/"),
    ("Matrix Comsec", "SETU VTEP", "SETU VTEP 1P", "https://www.matrixcomsec.com/products/?p=12030"),
    ("Hilscher", "NT 50", "NT 50-DP-EN", "https://www.mouser.in/en/c/embedded-solutions/ethernet-communication-modules/gateways/?m=Hilscher"),
    ("Hilscher", "NT 100", "NT 100-RE-DP", "https://www.mouser.in/en/c/embedded-solutions/ethernet-communication-modules/gateways/?m=Hilscher"),
    ("Hilscher", "NT 151-RE", "NT 151-RE-RE",
     "https://www.hilscher.com/na/products/gateways-switches-for-industrial-communication/gateways-for-industrial-ethernet-fieldbus-serial/nt-151-re-re"),
]

DROP = [   # vendor, [models], why  (searched for, not found)
    ("Huawei", ["AR651K", "AR6510-L26T2X"], "not found online (AR650 / AR6510-L pages list other models)"),
    ("Alcatel-Lucent Enterprise", ["OmniAccess 5710 ESR", "OmniAccess 5720 ESR", "OmniAccess 5725 ESR", "OmniAccess 5740 ESR"],
     "not found online; ALE's ESR model list could not be confirmed"),
    ("Cisco", ["CRS-3", "CRS-X"], "hardware generations (fabric / line cards), not chassis models"),
    ("Huawei", ["USG6710E", "USG6720E", "USG6730E"], "not found; Huawei's USG6700E page lists USG6712E / USG6716E only"),
    ("H3C", ["F100-E-G3", "F100-M-G3", "F100-S-G3", "F100-A-A3", "F100-E-A3"], "not found online"),
    ("H3C", ["F1000-AK1010", "F1000-AK1020", "F1000-AK1030", "F1000-AK1040", "F1000-AK1050"], "none of these names found online"),
    ("Hillstone Networks", ["E3960", "E5168", "E5560", "E5960", "X20812"], "not found; Hillstone's lists use other numbers"),
    ("Sangfor", ["M6300", "M6500", "M6600", "M6800", "M7000", "M7500", "M8000", "M9000"],
     "not found; the NGAF range found on sangfor.com ends at M6000"),
    ("WatchGuard", ["T45-W"], "not found (T45, T45-PoE, T45-W-PoE exist)"),
    ("Huawei", ["NIP6320", "NIP6330", "NIP6350", "NIP6380", "NIP6600", "NIP6650", "NIP6680"],
     "model names not confirmed online (only the NIP6300 / NIP6600 families)"),
    ("A10 Networks", ["Thunder TPS 840", "Thunder TPS 1040", "Thunder TPS 7445"], "not found online"),
    ("Arbor (NETSCOUT)", ["TMS 2300"], "not found online"),
    ("NSFOCUS", ["ADS NX3", "ADS NX5"], "family names; the NX3 / NX5 models are listed instead"),
    ("Seqrite (Quick Heal)", ["T2S-20", "T2S-120", "T2S-250", "T2S-500", "T2S-1000"], "not in Seqrite's model list"),
    ("Endian", ["UTM Mini 25", "Mercury 100", "Macro X2", "Macro X3"], "not found; Endian's models are listed instead"),
    ("Smoothwall", ["S6"], "Smoothwall never released an S6 (Smoothwall knowledge base)"),
    ("Calyptix", ["AE2500"], "not found; Calyptix's models are listed instead"),
    ("SECUI", ["MF2 100", "MF2 200", "MF2 500", "MF2 2000", "MF2 5000"], "not found online (MF2 1000 is)"),
    ("Barracuda", ["530", "630"], "not found (Link Balancer 230 / 330 / 430 are)"),
    ("Radware", ["AppDirector 1000", "AppDirector 2000", "AppDirector 3000"],
     "not hardware names: AppDirector is the software on the OnDemand Switch platforms"),
    ("Kemp (Progress)", ["LM-X3000"], "no such model (LM-3000 and LM-X3 exist)"),
    ("Array Networks", ["APV2200", "APV3200", "APV6200"], "not found online"),
    ("Piolink", ["PAS-K2016"], "not found online"),
    ("AudioCodes", ["Mediant 502"], "not found online"),
    ("Ribbon", ["EdgeMarc 4810"], "not found online"),
    ("HMS Networks", ["AB7832"], "not found online"),
    ("Raritan (Legrand)", ["DSX2-8M", "DSX2-16M"], "not found online"),
    ("Perle", ["IOLAN SCG-L", "IOLAN SCG-R"], "not found online"),
    ("Black Box", ["LES1616A", "LES1632A", "LES1648A"], "not found online"),
    ("Avaya", ["SBCE 310", "SBCE 360"], "not found online (SBCE runs on Dell servers)"),
    ("MultiTech", ["MVP2410"], "not found online"),
    ("Matrix Comsec", ["SETU ATA", "SETU VGFX"], "not found in Matrix Comsec's catalogue"),
    ("Sandvine", ["PL8720", "PL10000", "PL20000"], "not found online"),
]

ADD = [   # kind, vendor, series, category, form, status, [models], url
    ("router", "Huawei", "NetEngine AR600", "Enterprise / branch router", "Desktop", C, ["AR651W-8P", "AR651EW"],
     "https://e.huawei.com/de/products/routers/netengine-ar650"),
    ("firewall", "Hillstone Networks", "E-Series NGFW", "Next-gen firewall", "1RU-2RU", CHECK,
     ["E2860", "E2868", "E6160", "E6368"], "https://www.hillstonenet.com/wp-content/uploads/Hillstone_E-6000_V5.5R4_EN.pdf"),
    ("firewall", "Hillstone Networks", "E-Series NGFW", "Next-gen firewall", "1RU-2RU", CHECK,
     ["E3662", "E3965", "E5568"], "https://eol.network/hillstone/e-series%20ngfw"),
    ("firewall", "Fortinet", "FortiDDoS", "DDoS protection", "2RU", C, ["FortiDDoS 3000F"],
     "https://docs.fortinet.com/document/fortiddos-f/6.6.1/handbook/348476"),
    ("firewall", "Arbor (NETSCOUT)", "APS / TMS", "DDoS protection", "2RU", CHECK, ["TMS 2600"],
     "https://cis.bakotech.com/uploads/ckeditor/files/arbor_threat_mitigation_system.pdf"),
    ("firewall", "NSFOCUS", "ADS (anti-DDoS)", "DDoS protection", "1RU-2RU", CHECK,
     ["NX3-800E", "NX3-2020E", "NX3-HD2500", "NX5-4020E", "NX5-6025E", "NX5-8000", "NX5-10000", "NX5-HD4500", "NX5-HD6500"],
     "https://nsfocusglobal.com/wp-content/uploads/2021/08/PVD-ADS-V4.5R90F02-Relase-Notes.pdf"),
    ("firewall", "Seqrite (Quick Heal)", "UTM Terminator", "UTM / SMB firewall", "Desktop / rack", C,
     ["T2S-10", "T2M-100", "T2M-250"], "https://www.seqrite.com/unified-threat-management-faq"),
    ("firewall", "Endian", "UTM appliances", "UTM / SMB firewall", "Desktop / rack", C, ["UTM Mini 21"],
     "https://endian.com/endian-utm-mini-21"),
    ("firewall", "Endian", "UTM appliances", "UTM / SMB firewall", "1RU", C, ["UTM Macro 22"], "https://endian.com/endian-utm-macro-22"),
    ("firewall", "Endian", "UTM appliances", "UTM / SMB firewall", "Desktop / rack", C, ["UTM Mercury"],
     "https://cms.endian.com/media/download/endian_utm_mercury_2021_datasheet_en.pdf"),
    ("firewall", "Smoothwall", "UTM appliances", "UTM / SMB firewall", "1RU", C, ["S3", "S5", "S9"],
     "https://kb.smoothwall.com/hc/en-us/articles/360008340759-Smoothwall-Hardware-Appliance-Specifications"),
    ("firewall", "Calyptix", "AccessEnforcer", "UTM / SMB firewall", "Desktop / rack", C, ["AE1400", "AE2400", "AE3400", "AE4400"],
     "https://www.calyptix.com/?p=4184"),
    ("load_balancer", "Array Networks", "APV (older)", "Legacy load balancer", "1RU-2RU", EOS,
     ["APV2600", "APV5600", "APV10600", "APV10650"],
     "https://www.dbta.com/Editorial/News-Flashes/Array-Networks-Ships-New-Application-Delivery-Controller-Line-89568.aspx"),
    ("load_balancer", "Piolink", "PAS-K (older)", "Legacy load balancer", "1RU", CHECK, ["PAS-K2824"],
     "https://detail.zol.com.cn/load_leveling/beijing/3.html"),
    ("gateway", "HMS Networks", "Anybus X-gateway", "Protocol / IoT gateway", "Industrial / DIN", C, ["AB7800"],
     "https://hms-networks.com/p/ab7800-f-anybus-x-gateway-profibus-master-ethernet-ip-adapter"),
    ("gateway", "Innovaphone", "IP gateways", "VoIP / media gateway", "Desktop / 1RU", C, ["IP6013", "IP0013"],
     "https://www.innovaphone.com/en/hardware/gateways/ip411.html"),
    ("gateway", "Opengear", "Out-of-band gateways / console servers", "Console / out-of-band gateway", "1RU", C,
     ["OM2216", "OM2248-10G-L", "CM8132", "CM8148", "IM7232", "IM7248"], "https://opengear.com/?p=3555"),
    ("gateway", "Matrix Comsec", "SETU VoIP gateways", "VoIP / media gateway", "Desktop / 1RU", C,
     ["SETU VFXTH0024", "SETU VFXTH0032", "SETU VFXTH0808", "SETU VFXTH3200"], "https://www.matrixcomsec.com/products/?p=11980"),
    ("gateway", "Matrix Comsec", "SETU VoIP gateways", "VoIP / media gateway", "Desktop / 1RU", C,
     ["SETU VTEP 2P", "SETU VTEP 3P", "SETU VTEP 4P"], "https://www.matrixcomsec.com/products/?p=12030"),
    ("gateway", "Hilscher", "netTAP protocol gateways", "Protocol / IoT gateway", "Industrial / DIN", C,
     ["NT 50-RS-EN", "NT 100-RE-EN"], "https://www.mouser.in/en/c/embedded-solutions/ethernet-communication-modules/gateways/?m=Hilscher"),
    ("gateway", "RAK Wireless", "WisGate LoRaWAN gateways", "IoT / LoRaWAN gateway", "Industrial / outdoor", C,
     ["RAK7268V2", "RAK7268CV2", "RAK7289V2", "RAK7289CV2"], "https://docs.rakwireless.com/tags/wis-gate-edge-series"),
]
_ADD_ALL = [x for x in ADD]


def apply(rows, kind, log, links=None):
    """rows: lists [vendor, series, model, category, form, status, source, variants/same] (changed in place
    order kept); kind: 'router' / 'firewall' / 'load_balancer' / 'gateway'. Returns the new row list."""
    key = lambda v, m: (v, m.upper())
    conf = {key(v, m): url for v, ms, url in CONFIRMED for m in ms}
    stat = {key(v, m): (st, url) for v, ms, st, url in STATUS for m in ms}
    ren = {key(v, o): (n, url) for v, o, n, url in RENAME}
    drop = {key(v, m): why for v, ms, why in DROP for m in ms}
    out, have = [], set()
    for r in rows:
        r = list(r)
        k = key(r[0], r[2])
        if k in drop:
            log.append(("Removed", r[0], r[2], f"web check: {drop[k]} (review 2)"))
            continue
        if k in ren:
            new, url = ren[k]
            log.append(("Renamed", r[0], f"{r[2]} -> {new}", f"web check: the vendor's name ({url})"))
            r[2], r[6] = new, CHECKED
            k = key(r[0], new)
            if links is not None:
                links[k] = url
        if k in have:                                  # renamed onto a name already listed
            continue
        if k in conf and "verify" in r[6]:
            r[6] = CHECKED
            log.append(("Confirmed", r[0], r[2], f"web check: {conf[k]} (review 2)"))
            if links is not None:
                links[k] = conf[k]
        if k in stat and r[5] != stat[k][0]:
            log.append(("Status", r[0], r[2], f"{r[5]} -> {stat[k][0]}: web check, {stat[k][1]} (review 2)"))
            r[5] = stat[k][0]
        have.add(k)
        out.append(r)
    for k_, vendor, series, cat, form, st, models, url in ADD:
        if k_ != kind:
            continue
        for m in models:
            if key(vendor, m) in have:
                continue
            have.add(key(vendor, m))
            out.append([vendor, series, m, cat, form, st, CHECKED, ""])
            log.append(("Added", vendor, m, f"web check: real model found while checking ({url}) (review 2)"))
            if links is not None:
                links[key(vendor, m)] = url
    return out
