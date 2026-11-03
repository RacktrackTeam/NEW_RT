'use strict';

// A device's model is READ from its own text, never guessed (lib/model_read.js,
// the same rules as pipeline/model_read.py). The texts are the real reads of
// rack B07 (RK-9B1F243E), B12 (RK-B9155BE5) and the office rack (RK-9FFD84F1).
const test = require('node:test');
const assert = require('node:assert/strict');
const { modelFromText, modelInText, notAModel } = require('../lib/model_read');
const { readModelOf, saneModel } = require('../lib/scan_view');

const B07_U11 = 'G3 LOC: Rack A03 RU28 ame: sis-rt01 N: FXS2010Q1U7 nvs-sgkc-sis-rt01 17807 MAJ -TE187 s-tw01cETHT-18807 STAT MIN RODUCT sco ASR1 CON CON USB/MGM CISCO';
const B07_U07 = 'SG3 LOC Rack A04 RU28 Name: sis-rt02 S/N: FXS2011Q1H1 nvs-sgkc-sis-rt02 000 CLASS 1 LASEN PRODUCY Cisco ASR1001-X CON STAT GEZ GE4 CON USBIMGMT ata. CISCO';
const B07_U03 = 'GET an-rt05 Rack A03 RU18 215Q2ZM nvs-sgkc-wan-rt0łs 2001706 aoto Cisco ASR1001-X CISCO';
const B07_U02 = 'Cisco ASR1001-X CISCO 119 MAC Address staeh A05-184 IRGWD';
const B12_U08 = '88 S061-489 sw0G61 112 PXH OS nvs-sgkc-sis-sw05a Gi1-11 B11 nvs-sgkc-sis-sw05b Gi1-0-26B12 b08-reg-gwlc02 Gig0(SP) B08 P-OMS-P SS nvs-sgkc-s nvs-sgkc-f v-l---4 nvs-sgkc-sis-sw05b Ett';
const B12_U07 = 'nvs-sgkc-15-5 CAT8B altale nvs-sgic-ss CISCO nvs-sgtc-ss sis-sg01e2LOMB12 SWG2 SG01E2LON NS-SGKC- 2001702 SG3LOC Name: SIS-SW05B sakc-sis-rt07 Gi0-1 B10 12 S/N: FDO2203B0WU';
const B12_U06 = '-ok- F14 15a SLA 174 W8 15A Y25 vn 21A nvs-sgkc-sis-waf01e2MGN nvs-sokc-sis-sw05b Gi1-0- Rack A08 RU36 swtus HP 1810-36 PeE X POE Switch Port Status 19A A08-36';
const B12_U05 = 'Voltage Relay Peripheral TH Senisors RS-232 POrt THTU APC PORT www.apc.com h52.80t 2412 00-8N NetBotz SG3 LOC Rack A08 RU35 Rack ISO11801/TTA-ETA 569-8 Name APCNETBOTZ Monitor 192.168.1.100 B/N: ZA1411008669 Monitor 200';
const OFFICE_U19 = '111.111 SP.R1012.SW0 Ptp-link';

test('the model is the clean model token in the text', () => {
  assert.deepEqual(modelFromText(B07_U07, 'Cisco'), { make: 'Cisco', model: 'ASR1001-X' });
  assert.deepEqual(modelFromText(B07_U03, 'Cisco'), { make: 'Cisco', model: 'ASR1001-X' });
  assert.deepEqual(modelFromText(B07_U02, 'Cisco'), { make: 'Cisco', model: 'ASR1001-X' });
  assert.deepEqual(modelFromText(B12_U05, 'APC'), { make: 'APC', model: 'NetBotz 200' });
  assert.deepEqual(modelFromText(B12_U06, 'HPE'), { make: 'HPE', model: '1810' });
  assert.equal(modelFromText('D-Link DGS-1210-10 Gigabit', 'D-Link').model, 'DGS-1210-10');
  assert.equal(modelFromText('tp-link JetStream TL-SG2428P 24-Port', 'TP-Link').model, 'TL-SG2428P');
  assert.equal(modelFromText('CISCO Catalyst 9300 C9300-48P', 'Cisco').model, 'C9300-48P');
  assert.equal(modelFromText('cisco Nexus N9K-C93180YC-EX', 'Cisco').model, 'N9K-C93180YC-EX');
  assert.equal(modelFromText('MikroTik RB 3011 UiAS-RM', 'MikroTik').model, 'RB3011UIAS-RM');
  assert.equal(modelFromText('Juniper EX4300-48T', 'Juniper').model, 'EX4300-48T');
  assert.equal(modelFromText('paloalto PA-3220', null).model, 'PA-3220');
});

test('no clean model in the text: no model, never a guess', () => {
  assert.equal(modelFromText(B07_U11, 'Cisco'), null, 'only "sco ASR1" was read');
  assert.equal(modelFromText(B12_U08, null), null, 'cable tags only');
  assert.equal(modelFromText(B12_U07, 'Cisco'), null, 'a name and a serial only');
  assert.equal(modelFromText(OFFICE_U19, 'TP-Link'), null, 'an asset label only');
  assert.equal(modelFromText('nvs-sgkc-wan-inet-rt01 2001708 alah. CON CISCO', 'Cisco'), null);
  assert.equal(modelFromText('B8888', null), null);
});

test('names, tags, serials, locations, ports and regulatory words are never a model', () => {
  for (const t of ['sis-rt02', 'nvs-sgkc-sis-rt02', 'an-rt05', 'b08-reg-gwlc02', 'Gi1-0-26', 'RS-232', 'CON', 'USB/MGM', 'MGMT', 'GE0',
    'A08-36', 'RU28', 'A03', '192.168.1.100', '00:1A:2B:3C:4D:5E', 'ISO11801/TTA-ETA', 'IS011801', '2412', 'SP-R1-U18-SW04', 'S/N5RRX8Z1', 'FXS2010Q1U7']) {
    assert.ok(notAModel(t), `${t} is not a model`);
  }
  for (const t of ['ASR1001-X', 'TL-SG2428P', 'DGS-1210-10', 'C9300-48P', 'J9980A', 'AP7921B']) assert.equal(notAModel(t), null, t);
});

test('a stored model stands only when it is in the text as its own word', () => {
  assert.equal(modelInText('NEXUS 7010', B07_U11), false, 'a catalogue guess');
  assert.equal(modelInText('C1200-16P-2G', B07_U03), false, 'a catalogue guess');
  assert.equal(modelInText('SIS-RT02', B07_U07), false, 'its own name');
  assert.equal(modelInText('NVS-SGKC-SIS-SW05B', B12_U08), false, 'a cable tag');
  assert.equal(modelInText('NVS-SOKC-SIS-SW05B', B12_U06), false, 'a cable tag');
  assert.equal(modelInText('RS-232', B12_U05), false, 'a port label');
  assert.equal(modelInText('R1012', OFFICE_U19), false, 'part of an asset label');
  assert.equal(modelInText('ASR1001-X', B07_U02), true);
  assert.equal(modelInText('ASR1001X', 'Cisco ASR1O01-X'), true, 'one misread letter');
});

test('scans on disk show the model read, not the one stored', () => {
  const show = (stored, rawText, make, labelText = null) => readModelOf(stored, { rawText, make, labelText }).model;
  assert.equal(show('NEXUS 7010', B07_U11, 'Cisco'), null);
  assert.equal(show('C1200-16P-2G', B07_U03, 'Cisco'), 'ASR1001-X');
  assert.equal(show('SIS-RT02', B07_U07, 'Cisco', 'nvs-sgkc-sis-rt02'), 'ASR1001-X');
  assert.equal(show('ASR1001-X', B07_U02, 'Cisco'), 'ASR1001-X');
  assert.equal(show('NVS-SGKC-SIS-SW05B', B12_U08, null), null);
  assert.equal(show('C1200-16P-2G', B12_U07, 'Cisco'), null);
  assert.equal(show('NVS-SOKC-SIS-SW05B', B12_U06, 'HPE'), '1810');
  assert.equal(show('RS-232', B12_U05, 'APC'), 'NetBotz 200');
  assert.equal(show('R1012', OFFICE_U19, 'TP-Link'), null);
  assert.equal(saneModel('RS-232', { rawText: B12_U05 }), false);
  assert.equal(show('B8888', 'B8888', null), null, 'no maker and no model shape: a tag');
  assert.equal(show('MXQ10506SJ', 'EXPANS Name SG3 LOC: Rack A05 RU18 S/N: MXQ10506SJ SC 7.2K', null), null, 'the serial');
});
