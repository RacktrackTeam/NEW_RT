/**
 * Loads the ground truth files (../ground_truth/*.json) and gives every site,
 * rack, device and port group a stable id, so the mock NetBox, the mock
 * ServiceNow and the workbook filler all describe the same things.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const DIR = path.join(__dirname, '..', 'ground_truth');
const DEFAULT_FILES = ['vdo1.json', 'whatsapp_2143.json', 'whatsapp_2319.json'];

const hex32 = (s) => crypto.createHash('md5').update(String(s)).digest('hex');
const slug = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

/** The names of a device's data ports: interfaces, front ports and rear ports (power outlets are not data ports). */
function portsOf(device) {
  const out = [];
  const prefix = { interface: 'Eth', front: 'Front ', rear: 'Rear ' };
  for (const g of device.ports || []) {
    if (!prefix[g.group]) continue;
    for (let n = 1; n <= g.count; n += 1) out.push({ group: g.group, number: n, name: `${prefix[g.group]}${n}` });
  }
  return out;
}

function load(files = DEFAULT_FILES, dir = DIR) {
  return files.map((f, i) => {
    const gt = JSON.parse(fs.readFileSync(path.isAbsolute(f) ? f : path.join(dir, f), 'utf8'));
    return {
      file: path.basename(f),
      gt,
      siteId: i + 1,
      siteName: gt.site.name,
      rackLabel: gt.rack.label,
      devices: gt.devices.map((d) => ({ ...d, ports: portsOf(d) })),
    };
  });
}

module.exports = { load, hex32, slug, portsOf, DEFAULT_FILES, DIR };
