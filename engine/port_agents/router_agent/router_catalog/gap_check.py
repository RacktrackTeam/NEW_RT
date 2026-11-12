"""Gap check of the router catalogue (2026-10-22).

The second review said the first review's file listed 625 candidate models and 16 vendors that were
missing; that file never arrived, so the gaps were looked for directly: the families the review named
(Nokia 7950 XRS / 7730 SXR, HPE HSR, Peplink MAX, Arista 7280R / R2 / 7500R, Huawei NE40E), thin
series of the big vendors, router makers absent from all four catalogues, and a diff against the
NetBox device-type library (github.com/netbox-community/devicetype-library). Every model below was
found online; the link is kept in the Change log ('Checked at' in the appliance catalogues).

  F  vendor, series, category, form, status, [models], url
  L  vendor, like, [models], status, url   (siblings: series / category / form copied from LIKE)
Models already listed are not added again; a listed row still 'Not confirmed online' is confirmed.
Applied last by review2_changes.apply (and for links by appliance_catalog/review2.apply).
"""
SP, SEC, SMB, SDW, IND, DC, BR, HOME = "Service-provider / core router", "Security gateway (routes)", "SMB / VPN router", "SD-WAN appliance", "Industrial router", "Data-center / core router", "Enterprise / branch router", "Home / consumer router"
C, EOS, EOSUP = "Current", "End-of-sale", "End of support"
RACKM, CHAS, RACK, DESK, INDF = "Rack / modular chassis", "Modular chassis (rack)", "Rack", "Desktop", "Industrial / DIN"
F = [
("Nokia", "7950 XRS", SP, RACKM, C, ["7950 XRS-16c", "7950 XRS-20"], "https://documentation.nokia.com/nsp/24-8/User_Guide/ai8ha35ok8.html"),
("Nokia", "7950 XRS", SP, RACKM, C, ["7950 XRS-40"], "https://carritech.com/?p=2409"),
("HPE", "FlexNetwork HSR6800", SP, CHAS, C, ["HSR6802", "HSR6804", "HSR6808"], "https://www.hpe.com/us/en/collaterals/collateral.c04111425.html"),
("Peplink", "MAX mobile routers", SDW, "Desktop / vehicle", C,
 ["MAX 700", "MAX HD2", "MAX HD2 IP67", "MAX HD2 Mini", "MAX HD2 MBX 5G", "MAX HD2 MBX", "MAX HD Dome", "MAX HD Dome Pro",
  "MAX Dome Pro Duo", "MAX Dome Pro LR", "MAX HD4", "MAX HD4 IP67", "MAX HD4 MBX", "MAX BR1 Mini", "MAX BR1 Mini 5G",
  "MAX BR1 Mini Core", "MAX BR1 Mini M2M", "MAX BR1 Pro LTE", "MAX BR1 Pro 5G", "MAX Transit", "MAX Transit 5G",
  "MAX Transit Mini", "MAX Transit Pro E", "MAX Transit Duo Pro E", "MAX Transit Core", "MAX Transit Duo Pro", "MAX BR2 Micro LTE"],
 "https://satellitephonestore.com/uploads/documentation/7241/Peplink_MAX_User_Manual_Sept_202520235.pdf"),
("Arista Networks", "7280R / 7280R2 routers (older)", DC, RACK, EOS, ["7280SR-48C6", "7280CR2-60", "7280QR-C36"], "https://www.networktigers.com/collections/arista-7280r-series"),
("Arista Networks", "7280R / 7280R2 routers (older)", DC, RACK, EOS, ["7280SR2-48YC6"], "https://www.arista.com/en/support/advisories-notices/end-of-sale/15852-end-of-sale-of-the-arista-dcs-7280sr2-48yc6-series"),
("Arista Networks", "7500R / 7500R2 modular (older)", DC, CHAS, EOS, ["7504R", "7508R", "7512R", "7516R"], "https://www.arista.com/en/support/advisories-notices/end-of-sale/18885-end-of-sale-of-the-arista-7500r-and-7500r2-series-modules-and-bundles"),
("Nokia", "7730 SXR", SP, RACKM, C, ["7730 SXR-1x-44S", "7730 SXR-1-32D", "7730 SXR-1d-32D", "7730 SXR-R6d", "7730 SXR-R6dl"], "https://documentation.nokia.com/srlinux/26-7/books/product-overview/hardware-overview.html"),
("Siemens", "RUGGEDCOM RX1500 / RX5000 routers", IND, "Rack / DIN", C, ["RUGGEDCOM RX1500", "RUGGEDCOM RX1501", "RUGGEDCOM RX1510", "RUGGEDCOM RX1511", "RUGGEDCOM RX1512", "RUGGEDCOM RX1536", "RUGGEDCOM RX5000"], "https://cache.industry.siemens.com/dl/files/804/109766804/att_1054367/v1/RUGGEDCOM_Multi_Service_Platform.pdf"),
("NetModule", "NB industrial / vehicle routers", IND, INDF, C, ["NB800", "NB1600", "NB1601", "NB1800", "NB2800", "NB3700", "NB3701", "NB3710", "NB3800"], "https://cpe.gcve.eu/vendors/b67e51e9-b6fc-5a0d-8e3a-6444efc29430"),
("Cradlepoint", "Mobile / ruggedized routers", IND, INDF, C, ["IBR600C", "IBR650C"], "https://firstsourcewireless.com/en-gb/collections/cradlepoint-wireless-router"),
("Sierra Wireless", "AirLink routers (now Semtech)", IND, INDF, C, ["LX40", "LX60", "MG90", "MP70", "RV50X", "RV55", "XR80", "XR90", "EX400", "RX400", "RX55", "XR60"], "https://source.sierrawireless.com/resources/airlink/hardware_reference_docs/airlink-router-datasheets/"),
("Eltex", "ESR service routers", SMB, "Desktop / 1RU", C, ["ESR-10", "ESR-12V", "ESR-12VF", "ESR-14VF", "ESR-15", "ESR-20", "ESR-21", "ESR-30", "ESR-100", "ESR-200"], "https://eltex-co.com/upload/iblock/4b0/1685ct68se2peg4wxcgo8g1mef5f29jn/ESR_10_12V_12VF_14VF_15_20_21_30_100_200_datasheet_1.18.1_en.pdf"),
("Eltex", "ESR service routers", SP, RACK, C, ["ESR-1000", "ESR-1200", "ESR-1500", "ESR-1511", "ESR-1700", "ESR-3100"], "https://eltex-co.com/upload/iblock/5c9/q72428izmx046yjdees3woe3ibhwngp4/ESR-Series_Release notes_1.13.0_en.pdf"),
("Teldat", "RS / Atlas branch routers", BR, DESK, C, ["RS123", "RS123w", "RS123w-4G"], "https://pdf.directindustry.com/pdf/teldat/teldat-rs-123/216961-1115957.html"),
("Teldat", "RS / Atlas branch routers", BR, DESK, EOS, ["Atlas-60"], "https://pim.teldat.com/files/pdf/d3d9446802a44259755d38e6d163e820.pdf"),
("Teldat", "Regesta routers", BR, DESK, C, ["Regesta Smart", "Regesta Smart PRO"], "https://www.manualslib.com/brand/teldat"),
("Digi", "Cellular routers (EX / IX / TX)", IND, INDF, C, ["EX12", "EX15", "EX50", "IX10", "IX15", "IX20", "IX30", "IX40", "TX40", "TX54", "TX64", "TX64 Rail", "6310-DX"], "https://lte.callmc.com/product-tag/digi-international/"),
("Ericsson", "Router 6000", SP, RACK, C, ["Router 6672", "Router 6675", "Router 6274"], "https://www.ericsson.com/en/press-releases/2015/2/new-ericsson-router-6000-series-couples-radio-and-ip-transport-for-5g-future"),
("Ericsson", "Router 6000", SP, RACK, C, ["Router 6671"], "https://www.ericsson.com/en/portfolio/networks/ericsson-cloud-ran/cloud-ran-transport/router-6000-series"),
("TP-Link", "Omada gateways", SMB, "Desktop / rack", C, ["ER605", "ER7206", "ER8411", "ER7212PC", "ER707-M2", "ER706W", "ER706W-4G"], "https://store.omadanetworks.com/collections/all-gateways"),
("Extreme", "ExtremeRouting SLX", DC, RACK, C, ["SLX 9540", "SLX 9640", "SLX 9740"], "https://extremenetworks.com/solution/border-routing"),
("Sagemcom", "F@st home gateways", HOME, DESK, C, ["F@st 5359"], "https://techinfodepot.shoutwiki.com/wiki/Sagemcom_F@ST_5359"),
("Sagemcom", "F@st home gateways", HOME, DESK, C, ["F@st 5670", "F@st 5688ax"], "https://www.businesswirechina.com/en/news/36221.html"),
("Cudy", "Routers", HOME, DESK, C, ["WR3000", "WR3000E", "WR3000H", "WR3000S", "WR3000P", "TR3000", "TR3600", "X6"], "https://www.cudy.com/pages/download-center/wr3000s-1-0"),
("Cudy", "Routers", HOME, DESK, C, ["WR3600H", "LT400", "LT400V", "LT400E", "LT500", "LT500D", "P5"], "https://www.pccomponentes.com/brands/cudy/routers"),
("Mercusys", "Routers / Halo mesh", HOME, DESK, C, ["MR90X", "MR80X", "MR70X", "MR50G", "MR30G", "MB130-4G", "Halo H30G", "Halo H50G", "Halo H60X", "Halo H70X", "Halo H80X", "Halo H90X"], "https://techinfodepot.shoutwiki.com/wiki/Mercusys"),
("Welotec", "TK industrial routers", IND, INDF, C, ["TK800", "TK600"], "https://tk800.docs.welotec.com/latest/Introduction.html"),
("Welotec", "TK industrial routers", IND, INDF, C, ["TK525L", "TK525L-W", "TK535L1"], "https://trends.directindustry.com/welotec-gmbh/project-87657-1136537.html"),
("Four-Faith", "Industrial routers", IND, INDF, C, ["F-NR100", "F-NR200", "F3426", "F3836", "F-R100"], "https://pdf.directindustry.com/pdf/xiamen-four-faith-communication-technology-co-ltd-114843.html"),
("Cambium Networks", "cnPilot home / small business routers", HOME, DESK, "Check", ["cnPilot R190V", "cnPilot R190W", "cnPilot R195W", "cnPilot R195P", "cnPilot R200", "cnPilot R200P", "cnPilot R201", "cnPilot R201P", "cnPilot R201W"], "https://www.cambiumnetworks.com/wp-content/uploads/2021/07/cnPilot-Home-Router_User-Guide_System-Release-4.7.2.pdf"),
("Datacom", "DM2500 routers", BR, RACK, C, ["DM2500 4GT", "DM2500 4GT LTE"], "https://datacom.com.br/en/produtos/roteadores/dm2500-4gt"),
("IIJ", "SEIL routers", BR, DESK, C, ["SEIL/X4", "SA-W2L"], "https://cloud.watch.impress.co.jp/docs/news/1176972.html"),
("IIJ", "SEIL routers", BR, DESK, EOS, ["SEIL/X1", "SEIL/X2", "SEIL/B1", "SEIL/BPV4"], "https://jvn.jp/en//jp/JVN86171513"),
("Century Systems", "FutureNet NXR routers", BR, DESK, C, ["FutureNet NXR-130", "FutureNet NXR-230", "FutureNet NXR-G100"], "https://manageengine.com.au/network-monitoring/device-discovery/century-systems-performance-monitoring.html"),
("BDCOM", "BSR / BIG enterprise gateways", SMB, "Desktop / 1RU", C, ["BSR2900-10B", "BSR2900-20C", "BSR2900-24G", "BSR2900-40C"], "https://bdcom.cn/edge_router/80.html"),
("BDCOM", "BSR / BIG enterprise gateways", SMB, "Desktop / 1RU", C, ["BIG2000-20", "BIG2000-20C", "BIG2000-40"], "https://bdcom.cn/converged_gateway/76.html"),
("Buffalo", "AirStation / BroadStation routers", HOME, DESK, C, ["WXR-5700AX7S", "WSR-3200AX4S"], "https://www.buffalo.jp/press/detail/20201125-01.html"),
("Buffalo", "AirStation / BroadStation routers", SMB, DESK, EOS, ["VR-S1000"], "https://www.gdm.or.jp/pressrelease/2013/1204/53236"),
("Vantiva (Technicolor)", "Broadband gateways", HOME, DESK, C, ["CGA4236"], "https://www.normann-engineering.com/en/products/1299/vantiva-cga-4236/"),
("Vantiva (Technicolor)", "Broadband gateways", HOME, DESK, EOS, ["DGA0122"], "https://www.provu.co.uk/technicolor-dga0122.html"),
("Hitron", "Cable gateways", HOME, DESK, C, ["CGNM-3552", "CODA-4582", "CHITA", "CODA-5619"], "https://us.hitrontech.com/?p=1058"),
("Calix", "GigaSpire BLAST", HOME, DESK, C, ["GigaSpire BLAST u4"], "https://www.businesswire.com/news/home/20200924005225/en/Calix-Launches-the-First-Carrier-Class-Wi-Fi-6-System-Subscribers-Will-Welcome-into-the-Home-Showcasing-the-CSPs-Brand-and-Virtual-Storefront"),
("Calix", "GigaSpire BLAST", HOME, DESK, C, ["GigaSpire BLAST u6.1 (GS4220E)"], "https://lightwaveonline.com/broadband/article/55031779/calix-launches-carrier-class-wi-fi-6-system"),
("Calix", "GigaSpire BLAST", HOME, DESK, C, ["GigaSpire BLAST u6x"], "https://www.businesswire.com/news/home/20210511005732/en/The-Worlds-Leading-Portfolio-of-Broadband-Premises-Systems-From-Calix-Is-About-To-Get-Bigger-and-Better"),
("Weidmüller", "IE-SR industrial routers", IND, INDF, C, ["IE-SR-4TX-LTE/4G-EU", "IE-SR-4TX-LTE/4G-USEMEA"], "https://www.lemu.dk/en/product/router-ie-sr-4tx-lte-4g-eu-id-7886327722"),
("Weidmüller", "IE-SR industrial routers", IND, INDF, EOS, ["IE-SR-2GT-LAN", "IE-SR-6GT-LAN", "IE-SR-2GT-LTE/4G-US"], "https://www.radwell.com/Buy/WEIDMULLER/WEIDMULLER/IE-SR-2GT-LAN"),
("Actiontec", "Broadband gateways", HOME, DESK, C, ["C3000A", "T3260", "T3200M"], "https://speedguide.net/routers/actiontec-t3260-bonded-vdsl-wireless-ac-gateway-4704"),
("Actiontec", "Broadband gateways", HOME, DESK, EOS, ["GT784WN"], "https://www.speedguide.net/routers/actiontec-gt784wn-wireless-n-adsl-router-1968"),
("Comtrend", "DSL gateways", HOME, DESK, C, ["VR-3071"], "https://comtrend.com/dbase/upload-img/download/DS_VR-3071v2_V3.0.pdf"),
("Comtrend", "DSL gateways", HOME, DESK, EOS, ["AR-5387un"], "https://openwrt.org/toh/comtrend/ar-5387un"),
("Comtrend", "DSL gateways", HOME, DESK, C, ["NexusLink 3120"], "https://kb.nexicom.net/knowledge-base-2/tag/wireless/"),
("Starlink", "Routers", HOME, DESK, C, ["Router Gen 3", "Router Mini"], "https://www.speedcast.com/blog-hub/2025/starlink-router-options/"),
("Starlink", "Routers", HOME, DESK, EOS, ["Router Gen 2"], "https://www.speedcast.com/blog-hub/2025/starlink-router-options/"),
("ALAXALA", "AX620R routers", BR, DESK, EOS, ["AX620R-2105", "AX620R-2106", "AX620R-2215", "AX620R-3110", "AX620R-3315"], "https://www.ALAXALA.com/jp/products/archive/datasheet_pamphlet/pdf/ax620r_ds_op_ver10_0.pdf"),
("QTECH", "QSR routers", BR, RACK, C, ["QSR-1920-12-AC", "QSR-1920-22-AC"], "https://www.comnews.ru/content/224240/2023-02-02/2023-w05/obzor-marshrutizatorov-dlya-korporativnykh-setey-kompanii-qtech"),
("QTECH", "QSR routers", BR, RACK, C, ["QSR-3920-08"], "https://xcom-shop.ru/qtech_qsr-3920-08_909865.html"),
("Ruijie Networks", "EG gateways (Reyee)", SMB, "Desktop / 1RU", C, ["RG-EG105G", "RG-EG105G-P", "RG-EG105GW", "RG-EG210G-P", "RG-EG310GH-E", "RG-EG305GH-P-E"], "https://www.broadbandbuyer.com/Advice/4971-ruijie-reyee-desktop-routers-comparison-chart/"),
("Hikvision", "Wireless routers", HOME, DESK, C, ["DS-3WR15X", "DS-3WR12C"], "https://www.hikvision.com/content/dam/hikvision/en/brochures-download/product-leaflet/Wireless-Router-Leaflet.pdf"),
("Hikvision", "Wireless routers", HOME, DESK, C, ["DS-3WR3N"], "https://saruk.co.ke/product/Hikvision~DS-3WR3N~300Mbps~Wireless~Router"),
("WAVLINK", "Routers", HOME, DESK, C, ["WL-WN586X3", "WL-WN533A8"], "https://openwrt.org/toh/hwdata/wavlink/start"),
("I-O DATA", "Wi-Fi routers", HOME, DESK, C, ["WN-DAX3000QR"], "https://24wireless.info/iodata-wn-dax3000qr"),
("I-O DATA", "Wi-Fi routers", HOME, DESK, C, ["WN-7T94XR"], "https://gmobb.jp/service/docomohikari/bbr-spec/WN-7T94XR/"),
("ELECOM", "Wi-Fi routers", HOME, DESK, C, ["WRC-X3200GST3-B"], "https://gmobb.jp/service/docomohikari/bbr-spec/WRC-X3200GST3-B/"),
("Huawei", "NetEngine 8000", SP, RACKM, C, ["NetEngine 8000 F8", "NetEngine 8000 M1D-B"], "https://e.huawei.com/ma/products/routers/ne8000"),
("Huawei", "NetEngine 8000", SP, RACKM, C, ["NetEngine 8000 F1A-C", "NetEngine 8000 F2C"], "https://e.huawei.com/cz/products/routers/ne8000"),
("Juniper", "MX", SP, CHAS, C, ["MX10004", "MX10008", "MX10016"], "https://brightstarsystems.com/product-category/juniper/routers-switches/mx-series/mx10004-mx10008-mx10016/"),
("Juniper", "MX", SP, CHAS, C, ["MX2008", "MX2010", "MX2020", "MX304"], "https://www.networktigers.com/collections/juniper-mx-routers-refurbished-used"),
("ZTE", "ZXR10 M6000-S", SP, CHAS, C, ["ZXR10 M6000-3S", "ZXR10 M6000-3S Plus", "ZXR10 M6000-4SE", "ZXR10 M6000-5S", "ZXR10 M6000-8S", "ZXR10 M6000-8S Plus", "ZXR10 M6000-8SE", "ZXR10 M6000-16SE", "ZXR10 M6000-18S", "ZXR10 M6000-2S6", "ZXR10 M6000-2S8", "ZXR10 M6000-2S16"], "https://www.zte.com.cn/z/c8a6jw.html"),
("H3C", "CR16000 / CR19000", SP, CHAS, C, ["CR16003E-F", "CR16005E-F", "CR16010E-F"], "https://www.h3c.com/en/d_202310/1955119_294551_0.htm"),
("H3C", "CR16000 / CR19000", SP, CHAS, C, ["CR19000-20"], "https://www.h3c.com/en/d_202111/1493327_294551_0.htm"),
("FiberHome", "CiTRANS R800 / R8000 IP routers", SP, RACKM, C, ["CiTRANS R810", "CiTRANS R820-2", "CiTRANS R820-3", "CiTRANS R820-4", "CiTRANS R835E", "CiTRANS R845", "CiTRANS R865", "CiTRANS R8000-3", "CiTRANS R8000-5", "CiTRANS R8000-10"], "https://cpm.fiberhome.com/operator/product/products/56.aspx.htm"),
("FiberHome", "CiTRANS R800 / R8000 IP routers", SP, RACKM, C, ["CiTRANS R8000-10E", "CiTRANS R8000-20E"], "https://en.fiberhome.com/IPRAN/20240125/46171.html"),
("NETGEAR", "Nighthawk / Orbi routers", HOME, DESK, C, ["RS600", "RS500", "RBE971", "RBK852", "RBE871"], "https://www.ldlc.com/fr-be/informatique/reseau/modem-routeur/c4311/page2/+fc1274-1.html"),
("NETGEAR", "Nighthawk / Orbi routers", HOME, DESK, C, ["RAXE300", "RAX120"], "https://www.netgear.com/ca-en/home/wifi/routers/?link=faq"),
("TP-Link", "Archer / Deco home routers", HOME, DESK, C, ["Archer BE800", "Archer BE550", "Archer AXE75", "Archer AX73", "Archer AX55", "Archer C6", "Deco BE85", "Deco X55"], "https://www.tp-link.com/sg/alexa-compatibility/product-list/"),
("Ubiquiti", "UniFi cloud gateways", SMB, "Desktop / rack", C, ["UDM-SE", "UDM-Pro-Max", "UDM-Pro", "UCG-Ultra", "UCG-Max", "UXG-Lite", "UXG-Max", "UXG-Pro", "UDR7"], "https://www.alltrade.co.uk/shop/category/networking-routers-ubiquiti-unifi"),
("Zyxel", "USG FLEX H", SEC, "Desktop / rack", C, ["USG FLEX 100H", "USG FLEX 200H", "USG FLEX 200HP", "USG FLEX 700H"], "https://www.zyxel.com/global/en/products/firewall-ngfw/usg-flex-h-series/faqs"),
("MikroTik", "Cloud Core Router", SP, RACK, C, ["CCR2116-12G-4S+", "CCR2216-1G-12XS-2XQ", "CCR2004-16G-2S+"], "https://www.shop.wirelessnetware.ca/4__mikrotik?page=3"),
("MikroTik", "RouterBOARD / hEX / L009", SMB, DESK, C, ["RB5009UPr+S+IN", "RB5009UPr+S+OUT", "L009UiGS-RM", "hEX refresh (E50UG)", "hEX S"], "https://www.shop.wirelessnetware.ca/4__mikrotik?page=3"),
("MikroTik", "RouterBOARD / hEX / L009", HOME, DESK, C, ["hAP ax3", "hAP ax2"], "https://www.pccomponentes.com/brands/mikrotik"),
("DrayTek", "Vigor routers", SMB, DESK, C, ["Vigor2136", "Vigor2766", "Vigor2927", "Vigor3912S", "Vigor2865", "Vigor2962"], "https://draytek.nl/storage/DrayTek-databook_2025.pdf"),
("Huawei", "NetEngine AR6000", BR, RACK, C, ["AR6280"], "https://network-switch.com/products/routers-huawei-ar6280-enterprise-branch-network-router"),
("Huawei", "NetEngine AR6300", BR, RACK, C, ["AR6300"], "https://e.huawei.com/cn/products/routers/netengine-ar6300"),
("Huawei", "NetEngine AR700", BR, DESK, C, ["AR720", "AR730"], "https://www.mediamarkt.es/eu/product/_router-de-sobremesa-02354gbg-001-huawei-1000-mbps-not-available-165267971.html"),
]

NB = "https://github.com/netbox-community/devicetype-library/tree/master/device-types/"
L = [   # vendor, like (copy series / category / form), [models], status, url  -- siblings found in the NetBox device-type library
("Cisco", "ASR1002", ["ASR1013"], EOS, NB + "Cisco"),
("Cisco", "N540-12Z20G", ["N540-ACC-SYS"], C, NB + "Cisco"),
("Cisco", "C1121-4P", ["C1121-4PLTEP"], C, NB + "Cisco"),
("Cisco", "RV042", ["RV220W"], EOSUP, NB + "Cisco"),
("Cisco", "ASA5525-X", ["ASA5585-SSP-20"], EOSUP, NB + "Cisco"),
("Juniper", "SRX210", ["SRX210HE2"], EOSUP, NB + "Juniper"),
("Juniper", "SRX110", ["SRX110H2-VA"], EOSUP, NB + "Juniper"),
("Juniper", "PTX10003", ["PTX10003-80C"], C, NB + "Juniper"),
("Juniper", "MX80", ["MX80-48T"], EOSUP, NB + "Juniper"),
("Adtran", "NetVanta 3120", ["NetVanta 4660"], C, NB + "Adtran"),
("Allied Telesis", "AR2050V", ["AR2010V"], C, NB + "Allied Telesis"),
("D-Link", "DIR-X1860", ["DIR-320", "DIR-815"], EOSUP, NB + "D-Link"),
("AVM", "FRITZ!Box 7530", ["FRITZ!Box 7490", "FRITZ!Box 7510", "FRITZ!Box 6890 LTE"], C, NB + "AVM"),
("Deciso", "DEC740", ["DEC2610", "DEC2630", "DEC2640", "DEC2650", "DEC2685", "DEC2752", "DEC3840", "DEC3860", "DEC4020", "DEC4040", "DEC4610", "DEC4620", "DEC4630", "DEC4640"], C, NB + "Deciso"),
("RAD", "ETX-2i", ["ETX-2i-10G", "ETX-203AM"], C, NB + "RAD"),
("Riverbed", "CX580", ["CX555", "CX755", "CX5055", "CX7055"], EOS, NB + "Riverbed"),
("UfiSpace", "S9600-30DX", ["S9610-36D", "S9610-46DX", "S9700-23D"], C, NB + "UfiSpace"),
]

CHECKED = "Gap check 2026-10-22: added, confirmed online (link in 'Checked at' / Change log)"


def apply(rows, kind, log, links=None, add=True):
    """rows: lists/tuples [vendor, series, model, category, form, status, source, same]. add=False
    (appliance catalogues) only confirms and links rows that are there. Returns the new row list."""
    import re
    key = lambda v, m: (v, re.sub(r"[^A-Z0-9]", "", str(m).upper()))
    out = [list(r) for r in rows]
    idx = {key(r[0], r[2]): r for r in out}
    entries = [(v, s, c, f, st, ms, url) for v, s, c, f, st, ms, url in F]
    for v, like, ms, st, url in L:
        b = idx.get(key(v, like))
        if b is not None:
            entries.append((v, b[1], b[3], b[4], st, ms, url))
    for v, series, cat, form, st, models, url in entries:
        for m in models:
            k = key(v, m)
            r = idx.get(k)
            if r is not None:
                if r[6].startswith("Not confirmed") or "verify" in r[6]:
                    r[6] = CHECKED
                    log.append(("Confirmed", v, r[2], f"gap check: {url}"))
                if links is not None and not links.get((r[0], r[2].upper())):
                    links[(r[0], r[2].upper())] = url
                continue
            if not add:
                continue
            r = [v, series, m, cat, form, st, CHECKED, ""]
            out.append(r)
            idx[k] = r
            log.append(("Added", v, m, f"gap check: missing model found online ({url})"))
    return out
