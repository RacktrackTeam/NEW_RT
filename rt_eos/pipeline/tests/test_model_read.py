"""A device's model is READ from its own text, never guessed (pipeline/model_read.py).

The texts are the real reads of rack B07 (RK-9B1F243E), B12 (RK-B9155BE5),
the office rack (RK-9FFD84F1) and a MikroTik rack, each with the model the
identifier offered for it before this fix.
"""

import pytest

from pipeline.model_read import model_in_text, not_a_model, read_model, settle

B07_U11 = "G3 LOC: Rack A03 RU28 ame: sis-rt01 N: FXS2010Q1U7 nvs-sgkc-sis-rt01 17807 MAJ -TE187 s-tw01cETHT-18807 STAT MIN RODUCT sco ASR1 CON CON USB/MGM CISCO"
B07_U11_B = "G3 LOC: Rack A03 RU28 ame: sis-rt01 N: FXS2010Q1U7 nvs-sgkc-sis-rt01 -101-4-01 TE187 17807 MAJ STAT MIN RODUCT Sco ASR10 CON CON USB/MGM CISCO 18807"
B07_U07 = "SG3 LOC Rack A04 RU28 Name: sis-rt02 S/N: FXS2011Q1H1 nvs-sgkc-sis-rt02 000 CLASS 1 LASEN PRODUCY Cisco ASR1001-X CON STAT GEZ GE4 CON USBIMGMT ata. CISCO"
B07_U05 = "nvs-sgkc-wan-inet-rt01 2001708 alah. CON CISCO"
B07_U03 = "GET an-rt05 Rack A03 RU18 215Q2ZM nvs-sgkc-wan-rt0łs 2001706 aoto Cisco ASR1001-X CISCO"
B07_U02 = "Cisco ASR1001-X CISCO 119 MAC Address staeh A05-184 IRGWD"
B07_U01 = "EXPANS Name SG3 LOC: Rack A05 RU18 S/N: MXQ10506SJ SC 7.2K"
B12_U08 = "88 S061-489 sw0G61 112 PXH OS nvs-sgkc-sis-sw05a Gi1-11 B11 nvs-sgkc-sis-sw05b Gi1-0-26B12 b08-reg-gwlc02 Gig0(SP) B08 P-OMS-P SS nvs-sgkc-s nvs-sgkc-f v-l---4 nvs-sgkc-sis-sw05b Ett"
B12_U07 = "nvs-sgkc-15-5 CAT8B altale nvs-sgic-ss CISCO nvs-sgtc-ss sis-sg01e2LOMB12 SWG2 SG01E2LON NS-SGKC- 2001702 SG3LOC Name: SIS-SW05B sakc-sis-rt07 Gi0-1 B10 12 S/N: FDO2203B0WU"
B12_U06 = "-ok- F14 15a SLA 174 W8 15A Y25 vn 21A nvs-sgkc-sis-waf01e2MGN nvs-sokc-sis-sw05b Gi1-0- Rack A08 RU36 swtus HP 1810-36 PeE X POE Switch Port Status 19A A08-36"
B12_U05 = "Voltage Relay Peripheral TH Senisors RS-232 POrt THTU APC PORT www.apc.com h52.80t 2412 00-8N NetBotz SG3 LOC Rack A08 RU35 Rack ISO11801/TTA-ETA 569-8 Name APCNETBOTZ Monitor 192.168.1.100 B/N: ZA1411008669 Monitor 200"
B12_U05_B = "Modbus Urs Voltage Relay Peripheral TH Serisors EXUTA 85-232 Port TH-AU APC PORT www.apc.com 017 hs2-80t 2412 008 NetBotz SG3 LOC: Rack A08 RU35 Rack IS011801/TTA-ETA 569-8 Name APCNETBOTZ Monitor 92 168.1.100 B/N: ZA1411008669 Monitor 200"
OFFICE_U20 = "Link SP-RI4B SWOE 111.111"
OFFICE_U19 = "111.111 SP.R1012.SW0 Ptp-link"
OFFICE_U17 = "Ptplink SP-RI-4015-SW0"
MIKROTIK = "TREIO DAAT NT MaroTalk routerboard CHI CTQ ma CM CTHS CTO CTH CHIO MikroTik RB 3011 UiAS-RM"


@pytest.mark.parametrize(
    "make, offered, text, want",
    [
        # Read in the text: kept, or found in place of a wrong offer.
        ("Cisco", "SIS-RT02", B07_U07, ("Cisco", "ASR1001-X")),
        ("Cisco", "C1200-16P-2G", B07_U03, ("Cisco", "ASR1001-X")),
        ("Cisco", "ASR1001-X", B07_U02, ("Cisco", "ASR1001-X")),
        ("APC", "RS-232", B12_U05, ("APC", "NetBotz 200")),
        ("APC", "IS011801", B12_U05_B, ("APC", "NetBotz 200")),
        ("HPE", "NVS-SOKC-SIS-SW05B", B12_U06, ("HPE", "1810")),
        ("MikroTik", "CRS317-1G-16S+RM", MIKROTIK, ("MikroTik", "RB3011UIAS-RM")),
        # Not in the text: no model.
        ("Cisco", "NEXUS 7010", B07_U11, ("Cisco", None)),
        ("Cisco", "NEXUS 7010", B07_U11_B, ("Cisco", None)),
        ("Cisco", None, B07_U05, ("Cisco", None)),
        (None, "MXQ10506SJ", B07_U01, (None, None)),
        (None, "NVS-SGKC-SIS-SW05B", B12_U08, (None, None)),
        ("Cisco", "C1200-16P-2G", B12_U07, ("Cisco", None)),
        (None, "B8888", "B8888", (None, None)),
        # The office rack: names only on the faceplates, the make stays.
        ("D-Link", None, OFFICE_U20, ("D-Link", None)),
        ("TP-Link", "R1012", OFFICE_U19, ("TP-Link", None)),
        ("TP-Link", None, OFFICE_U17, ("TP-Link", None)),
    ],
)
def test_settle_real_reads(make, offered, text, want):
    assert settle(make, [offered], text) == want


@pytest.mark.parametrize(
    "text, make, want",
    [
        ("D-Link DGS-1210-10 Gigabit", "D-Link", "DGS-1210-10"),
        ("tp-link JetStream TL-SG2428P 24-Port", "TP-Link", "TL-SG2428P"),
        ("CISCO Catalyst 9300 C9300-48P", "Cisco", "C9300-48P"),
        ("cisco Nexus N9K-C93180YC-EX", "Cisco", "N9K-C93180YC-EX"),
        ("Cisco ASR1002 CON AUX", "Cisco", "ASR1002"),
        ("HPE OfficeConnect 1820 J9980A", "HPE", "J9980A"),
        ("APC Switched Rack PDU AP7921B", "APC", "AP7921B"),
        ("Juniper EX4300-48T", "Juniper", "EX4300-48T"),
        ("ARISTA DCS-7050SX-64", "Arista", "DCS-7050SX-64"),
        ("FORTINET FortiGate 100F", "Fortinet", "FG-100F"),
        ("paloalto PA-3220", None, "PA-3220"),
    ],
)
def test_clean_model_tokens(text, make, want):
    assert read_model(text, make)[1] == want


@pytest.mark.parametrize(
    "tok",
    ["sis-rt02", "nvs-sgkc-sis-rt02", "an-rt05", "b08-reg-gwlc02", "Gi1-0-26", "RS-232", "CON", "USB/MGM",
     "GE0", "A08-36", "RU28", "A03", "192.168.1.100", "00:1A:2B:3C:4D:5E", "ISO11801/TTA-ETA", "2412",
     "SP-R1-U18-SW04", "S/N5RRX8Z1", "FXS2010Q1U7"],
)
def test_never_a_model(tok):
    assert not_a_model(tok)


def test_one_misread_letter_is_still_read():
    assert model_in_text("ASR1001-X", "Cisco ASR1O01-X")
    assert not model_in_text("NEXUS 7010", B07_U11)
    assert not model_in_text("SIS-RT02", B07_U07)
