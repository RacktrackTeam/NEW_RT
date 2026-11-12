'use strict';

// A device's own sticker names it and says its kind; a model read off the
// photo is kept only when it is a model (lib/scan_view.js).
const test = require('node:test');
const assert = require('node:assert/strict');
const { stickerName, kindOfName, saneModel } = require('../lib/scan_view');

const box = [293, 671, 1514, 794];
const line = (text, conf, y = 700, end = null) => ({ text, conf, box: [400, y, 480, y + 12], end });

test('the sticker inside the box names the device; a cable tag line does not', () => {
  const lines = [
    line('Name sis-rt02', 0.956),
    line('nvs-sgkc-sis-rt02', 0.999),
    line('Cisce ASR1001-X', 0.986),
    line('nvs-sgkc-sis-mt02 TE0 B07', 0.93, 700, { device: 'nvs-sgkc-sis-mt02', port: 'TE0', position: 'B07', portNo: 0 }),
    line('nvs-sgkc-sis-rt01', 0.999, 200), // another device's sticker, outside the box
  ];
  assert.deepEqual(stickerName(box, lines, ''), { name: 'nvs-sgkc-sis-rt02', conf: 0.999 });
  // From the device's own read text alone.
  assert.equal(stickerName(box, [], 'SG3 LOC Rack A04 RU28 Name: sis-rt02 S/N: FXS2011Q1H1 nvs-sgkc-sis-rt02 Cisco ASR1001-X').name, 'nvs-sgkc-sis-rt02');
  assert.equal(stickerName(box, [], 'nvs-sgkc-wan-inet-rt01 2001708 aloah. CON CISCO').name, 'nvs-sgkc-wan-inet-rt01');
  assert.equal(stickerName(box, [], 'CLASS TLASEN PROOUCT Cisco ASR1001-X CISCO'), null, 'a model is not a name');
  assert.equal(stickerName(null, [], ''), null);
});

test('the name says the kind', () => {
  assert.equal(kindOfName('nvs-sgkc-sis-rt02'), 'Router');
  assert.equal(kindOfName('nvs-sgkc-wan-inet-rt01'), 'Router');
  assert.equal(kindOfName('nvs-sgkc-sis-sw05b'), 'Switch');
  assert.equal(kindOfName('SP-R1-U4-PP2'), 'Patch Panel');
  assert.equal(kindOfName('SP-R1-U19-FW'), 'Firewall');
  assert.equal(kindOfName('an-rt05'), 'Router');
  assert.equal(kindOfName('ISXSGSG01'), null);
  assert.equal(kindOfName(null), null);
});

test('a model is kept only when it is one', () => {
  const rt02 = 'SG3 LOC Rack A04 RU28 Name: sis-rt02 S/N: FXS2011Q1H1 nvs-sgkc-sis-rt02 Cisco ASR1001-X GE2';
  assert.equal(saneModel('ASR1001-X', { labelText: 'nvs-sgkc-sis-rt02', rawText: rt02 }), true);
  assert.equal(saneModel('SIS-RT02', { labelText: 'nvs-sgkc-sis-rt02', rawText: rt02 }), false, 'the tail of its own name');
  assert.equal(saneModel('FXS2011Q1H1', { labelText: 'nvs-sgkc-sis-rt02', rawText: rt02 }), false, 'the serial after S/N');
  assert.equal(saneModel('NEXUS 7010', { rawText: 'ame: sis-rt01 nvs-sgkc-sis-rt01 Sco ASR10 CISCO' }), false, 'not in the text read: a guess');
  assert.equal(saneModel('C1200-16P-2G', { rawText: 'GEI an-rt05 Rack A03 RU18 Cisco ASR1001-X CISCO' }), false);
  assert.equal(saneModel('A08-36', { rawText: 'Rack A08 RU36 X POE Switch A08-36' }), false, 'a location sticker');
  assert.equal(saneModel('TL-SG2428P', { rawText: null }), true, 'no text to check against: kept');
  assert.equal(saneModel('SP-R1-U18-SW04', {}), false, 'an asset tag');
  assert.equal(saneModel(null, {}), false);
});
