/**
 * The shorthand a technician types to find a cable in the Excel dataset,
 * without a photo: the rack, unit, device and port as they are said on
 * site, in any of these forms
 *
 *   r1/u13/sw1/7     sw1 p7     sw01 port 7     pp1 10a     10a     u13
 *   r1 u15           b12 sw5 26     gi1/0/26     pdu 3      fw
 *
 * Racks are the sheet's own rack names (R1 is SP-HYB-RM01-R01-R1, B12 is
 * B12); a device is its kind and number (sw1 is SP-R1-U13-SW01 and
 * nvs-sgkc-sis-sw01d alike); a port is its number, with a panel's letter
 * (10a) or an interface path (gi1/0/26). Every part given must land on the
 * same end of the cable. Text that is not shorthand is not parsed, and the
 * plain search over the labels and names still applies.
 *
 * Taken from the port-lookup tool a teammate wrote against the NetBox
 * exports (30 Sep 2026); here the record is the Excel.
 */
'use strict';

const RACK_WORDS = new Set(['R', 'RACK', 'RK']);
const UNIT_WORDS = new Set(['U', 'UNIT', 'RU']);
const DEVICE_WORDS = {
  S: 'SW', SW: 'SW', SWITCH: 'SW', PP: 'PP', PATCH: 'PP', PANEL: 'PP', PATCHPANEL: 'PP',
  FW: 'FW', FIREWALL: 'FW', RTR: 'RTR', ROUTER: 'RTR', PDU: 'PDU', SRV: 'SRV', SERVER: 'SRV', CM: 'CM', CABLEMANAGER: 'CM',
};
const PORT_WORDS = new Set(['P', 'PORT', 'PORTS', 'GI', 'GE', 'GIG', 'GIGABITETHERNET', 'ETH', 'ETHERNET', 'E', 'ET', 'TE', 'XE', 'FA', 'HU', 'FO', 'SFP', 'OUT', 'OUTLET', 'POWER', 'PWR', 'MGMT']);
const KIND_IN_NAME = { SW: 'SW', PP: 'PP', FW: 'FW', RTR: '(?:RTR|ROUTER|RT)', PDU: 'PDU', SRV: '(?:SRV|SVR|SERVER)', CM: 'CM' };

const compact = (s) => String(s ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '');

/** "r1/u13/sw1/7" -> ['R', '1', 'U', '13', 'SW', '1', '7']; "patch panel" -> ['PATCHPANEL']. */
function tokens(q) {
  const out = [];
  for (const word of String(q || '').toUpperCase().match(/[A-Z0-9]+/g) || []) {
    for (const t of word.match(/[A-Z]+|\d+/g) || []) {
      const prev = out[out.length - 1];
      if (/^[A-Z]+$/.test(t) && prev && /^[A-Z]+$/.test(prev) && DEVICE_WORDS[prev + t]) out[out.length - 1] = prev + t;
      else out.push(t);
    }
  }
  return out;
}

/**
 * -> { rack, unit, device: { kind, n } | null, port: { number, code, path } | null } or null
 * when the text is not shorthand. racks: the sheet's rack names, so "b12"
 * and "r1" are read as racks. numbered: the kinds the sheet numbers (SW01,
 * PP2); after a kind it does not number (FW, ROUTER, PDU) a number is the
 * port, so "pdu 3" is outlet 3 and "sw1 7" is switch 1 port 7.
 */
function parse(q, racks = [], numbered = null) {
  const t = tokens(q);
  if (!t.length) return null;
  const rackKeys = racks.map((r) => compact(r)).filter(Boolean);
  const isRack = (tok) => rackKeys.some((k) => k === tok || k.endsWith(tok) && tok.length >= 2 && /\d/.test(tok));
  const num = (j) => j < t.length && /^\d+$/.test(t[j]);
  const p = { rack: null, unit: null, device: null, port: null };
  let i = 0;
  if (RACK_WORDS.has(t[i]) && num(i + 1)) { p.rack = `R${parseInt(t[i + 1], 10)}`; i += 2; }
  else if (t[i] && t[i + 1] && /^[A-Z]+$/.test(t[i]) && num(i + 1) && isRack(t[i] + t[i + 1]) && !DEVICE_WORDS[t[i]] && !UNIT_WORDS.has(t[i])) { p.rack = t[i] + t[i + 1]; i += 2; }
  if (UNIT_WORDS.has(t[i]) && num(i + 1)) { p.unit = parseInt(t[i + 1], 10); i += 2; }
  if (t[i] && DEVICE_WORDS[t[i]]) {
    const kind = DEVICE_WORDS[t[i]];
    i += 1;
    let n = null;
    if (num(i) && (!numbered || numbered.has(kind))) { n = parseInt(t[i], 10); i += 1; }
    p.device = { kind, n };
  }
  // The port: an optional word (p, port, gi, eth), a number or an interface
  // path (1/0/26), and a panel's row letter (10a).
  const start = i;
  while (t[i] && PORT_WORDS.has(t[i])) i += 1;
  const path = [];
  while (num(i)) { path.push(parseInt(t[i], 10)); i += 1; }
  let code = null;
  if (path.length && t[i] && /^[AB]$/.test(t[i])) { code = t[i]; i += 1; }
  if (path.length) p.port = { number: path[path.length - 1], code, path, words: t.slice(start, i).join('') };
  else if (i > start) return null; // "sw1 port" with no number
  if (i !== t.length) return null;
  if (!p.rack && p.unit == null && !p.device && !p.port) return null;
  return p;
}

const unitOf = (u) => { const m = String(u ?? '').match(/\d+/); return m ? Number(m[0]) : null; };
const portNumber = (port) => { const m = String(port ?? '').match(/(\d+)(?!.*\d)/); return m ? Number(m[1]) : null; };

function rackFits(want, rack) {
  if (!rack) return false;
  const r = compact(rack);
  const w = compact(want);
  if (r === w) return true;
  // R1 is the tail of SP-HYB-RM01-R01-R1; R01 too.
  const m = w.match(/^([A-Z]+)0*(\d+)$/);
  if (!m) return r.endsWith(w);
  return new RegExp(`(^|[^A-Z0-9])${m[1]}0*${m[2]}$`).test(String(rack).toUpperCase());
}

function deviceFits(want, name) {
  if (!name) return false;
  const kind = KIND_IN_NAME[want.kind];
  if (!kind) return false;
  const n = want.n == null ? '\\d*' : `0*${want.n}`;
  // SW01, sw05b, U13-SW01: the kind and number as one token of the name, a single letter may follow.
  return new RegExp(`(^|[-_ ./])${kind}${n}[A-Z]?($|[-_ ./])`, 'i').test(String(name));
}

function unitFits(want, unit, name) {
  if (unitOf(unit) === want) return true;
  return new RegExp(`(^|[-_ ./])U0*${want}($|[-_ ./])`, 'i').test(String(name || ''));
}

function portFits(want, port) {
  if (port == null || port === '') return false;
  const c = compact(port);
  if (want.code) return c === `${want.number}${want.code}` || c.endsWith(`${want.number}${want.code}`) && /[A-Z]/.test(c[0]);
  if (want.path.length > 1) return c.endsWith(want.path.join('')) && (compact(want.words) === c || c.endsWith(compact(want.words)));
  return portNumber(port) === want.number && !/^\d+[AB]$/.test(c);
}

/** True when one end of the cable carries every part of the shorthand. */
function cableFits(c, p) {
  for (const e of ['a', 'z']) {
    const dev = c[`${e}_device`];
    if (p.rack && !rackFits(p.rack, c[`${e}_rack`])) continue;
    if (p.unit != null && !unitFits(p.unit, c[`${e}_unit`], dev)) continue;
    if (p.device && !deviceFits(p.device, dev)) continue;
    if (p.port && !portFits(p.port, c[`${e}_port`])) continue;
    return true;
  }
  return false;
}

/** The device kinds the sheet numbers: SW when a name carries SW01, PP when one carries PP2. */
function numberedKinds(list) {
  const names = [...new Set(list.flatMap((c) => [c.a_device, c.z_device]).filter(Boolean))];
  const out = new Set();
  for (const [kind, re] of Object.entries(KIND_IN_NAME)) {
    const rx = new RegExp(`(^|[-_ ./])${re}\\d+[A-Z]?($|[-_ ./])`, 'i');
    if (names.some((n) => rx.test(n))) out.add(kind);
  }
  return out;
}

/** The cables the text finds: by plain text over labels, devices and ports, and by the shorthand. */
function find(list, q) {
  const needle = String(q || '').trim().toLowerCase();
  if (!needle) return list;
  const plain = (c) => [c.cable_label, c.a_device, c.a_port, c.z_device, c.z_port].some((v) => String(v || '').toLowerCase().includes(needle));
  const racks = [...new Set(list.flatMap((c) => [c.a_rack, c.z_rack]).filter(Boolean))];
  const p = parse(needle, racks, numberedKinds(list));
  return list.filter((c) => plain(c) || (p && cableFits(c, p)));
}

module.exports = { tokens, parse, cableFits, find, numberedKinds, rackFits, deviceFits, portFits };
