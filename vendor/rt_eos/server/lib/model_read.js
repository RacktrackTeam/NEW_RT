/**
 * model_read.js - a device's model is READ from its own text, never guessed.
 *
 * The same rules as pipeline/model_read.py, so a scan already on disk shows
 * what the reader would keep today:
 *
 *   modelFromText(rawText, make)  the clean model token in the text, or null
 *   modelInText(model, rawText)   the model (or a one-letter misread of it) is
 *                                 one to three of the text's words, and none
 *                                 of them is a name, tag, serial, location,
 *                                 port or regulatory word
 *   notAModel(token)              why a word is never a model, or null
 *
 * "sco ASR1" read on a Cisco ASR1001-X once came back as NEXUS 7010 (a
 * catalogue guess); "Name: sis-rt02" came back as the model SIS-RT02; a
 * NetBotz's "RS-232" port label came back as its model. None of those stand.
 */
'use strict';

const CONTEXT_WORDS = new Set([
  's/n', 's/n:', 'sn', 'sn:', 'n:', 'b/n', 'b/n:', 'serial', 'serial:',
  'name', 'name:', 'ame:', 'ame', 'loc', 'loc:', 'mac', 'address',
  'address:', 'ip', 'ip:', 'host', 'host:', 'hostname', 'hostname:', 'asset', 'asset:',
]);
const PORT_WORDS = new Set([
  'con', 'console', 'mgmt', 'mgm', 'usb', 'aux', 'port', 'ports', 'stat', 'status',
  'sys', 'system', 'link', 'act', 'pwr', 'power', 'fan', 'alarm', 'reset', 'lom',
  'usb/mgmt', 'usb/mgm', 'usbimgmt', 'usbmgmt', 'maj', 'min', 'sfp', 'sfp+', 'poe',
  'rs-232', 'rs232', '85-232', 'ethernet',
]);
const PORT_RE = /^(?:gi|gig|te|ten|eth|et|fa|fe|xe|hu|fo|tw|mgmt|po|ge|lom|ma)(?:[a-z]*)(?:[0-9][0-9/().-]*[a-z0-9/().-]*)$|^rs-?\d{3}$|^ge[a-z0-9]$/i;
const REGULATORY = new Set(['class', 'laser', 'lasen', 'product', 'producy', 'prooduct', 'rohs', 'ce', 'fcc', 'ul', 'warning', 'caution']);
const REGULATORY_RE = /^(?:IS[O0]\d{3,5}\S*|TIA-?\S*|EN\d{4,}\S*|IEC\d+\S*)$/i;
const HOSTNAME_RE = /^[a-z]{2,}(?:-[a-z0-9.]+)*-[a-z]+\d+[a-z]?$/i;
const ASSET_TAG_RE = /(^|[-_. ])(?:SW|PP|FW|RTR|PDU|ACT)\d{0,3}$/i;
const UNIT_TOKEN_RE = /(^|[-_. ])U\d{1,2}(?=[-_. ]|$)/i;
const LOCATION_RE = /^(?:[A-Z]{1,2}\d{1,3}-\d{1,3}|RU\d{1,2}|[A-Z]\d{2})$/i;
const SERIAL_RE = /^[A-Z]{3}\d{4,5}[A-Z0-9]{2,5}$/;
const MAC_RE = /^(?:[0-9A-F]{2}[:.-]){5}[0-9A-F]{2}$|^[0-9A-F]{4}\.[0-9A-F]{4}\.[0-9A-F]{4}$/i;
const IP_RE = /^\d{1,3}(?:\.\d{1,3}){1,3}$/;
const DIGITS_RE = /^[\d.,:/-]+$/;
const SHORT_RE = /^[A-Z]{1,2}\d{1,2}$/i;
// Reasons that bar a word from being a model alone but not from being part
// of one: "RB 3011 UiAS-RM" and "Monitor 200" are read as several words.
const SOFT = new Set(['digits', 'no letters and digits', 'short']);

const clean = (tok) => String(tok || '').replace(/^[,;:()[\]{}'"|*]+|[,;:()[\]{}'"|*]+$/g, '').replace(/^[-.]+/, '');
const norm = (s) => String(s ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '');

const SIZES = '(?:8|10|16|18|24|26|28|48|50|52)';
const HP_SERIES = '(?:1810|1820|1910|1920|1950|2510|2530|2540|2620|2810|2920|2930[FM]?|3810M?|5130|5406)';
// [vendor, shape (matches a whole run of words), weak]. A weak shape is taken
// only when the make read on the device is that vendor.
const SHAPES = [
  ['Cisco', /^ASR\d{3,4}(?:-[A-Z0-9]{1,4})?$/i, false],
  ['Cisco', /^ISR\d{3,4}(?:[A-Z]{0,2})?(?:-[A-Z0-9/]{1,8})*$/i, false],
  ['Cisco', /^(?:WS-)?C\d{4}[A-Z]{0,2}-\d{1,3}[A-Z]{0,5}(?:-[A-Z0-9]{1,4}){0,2}$/i, false],
  ['Cisco', /^N\d[KX]-C?\d{4,5}[A-Z0-9]*(?:-[A-Z0-9]{1,6})*$/i, false],
  ['Cisco', /^(?<m>NEXUS\s?\d{4})$/i, false],
  ['Cisco', /^ASA\d{4}(?:-[A-Z0-9]{1,6})*$/i, false],
  ['Cisco', /^FPR-?\d{4}(?:-[A-Z0-9]{1,6})*$/i, false],
  ['Cisco', /^PIX-?\d{3,4}[A-Z]?$/i, false],
  ['Cisco', /^M[SX]\d{2,3}(?:-\d{1,2}[A-Z]{0,3})?$/i, true],
  ['TP-Link', /^TL-[A-Z]{2,4}\d{3,5}[A-Z]{0,4}$/i, false],
  ['TP-Link', /^T\d{4}[A-Z]{0,2}-\d{1,2}[A-Z]{1,4}$/i, false],
  ['TP-Link', /^SG\d{4}[A-Z]{0,3}$/i, true],
  ['D-Link', /^D[GXE]S-\d{3,4}[A-Z]{0,2}(?:-\d{1,3}[A-Z]{0,4})?(?:\/[A-Z0-9]{1,4})?$/i, false],
  ['D-Link', /^DSR-\d{3,4}[A-Z]?$/i, false],
  ['HPE', /^J\d{4}[A-Z]$/i, false],
  ['HPE', /^JL\d{3}[A-Z]$/i, false],
  ['HPE', new RegExp(`^(?:HPE?\\s?)?${HP_SERIES}-${SIZES}G?(?:-POE\\+?|-PPOE\\+?)?$`, 'i'), false],
  ['HPE', new RegExp(`^(?:HPE?\\s?)?(?<m>${HP_SERIES})(?:-\\S*)?$`, 'i'), true],
  ['HPE', /^(?:ARUBA\s|CX\s?)(?<m>(?:6100|6200|6300|6400|8320|8325|8360|8400)[A-Z]?)$/i, false],
  ['HPE', /^ProLiant\s?(?:DL|ML|BL)\d{2,3}[A-Z0-9]*$/i, false],
  ['APC', /^AP\d{4}[A-Z]{0,2}$/i, false],
  ['APC', /^NBRK\d{4}[A-Z]?$/i, false],
  ['APC', /^(?:SU[AM]|SMT|SMX|SRT|SURT)\d{3,5}[A-Z0-9]{0,8}$/i, false],
  ['APC', /^NETBOTZ\s?(?:RACK\s)?(?:MONITOR\s)?(?<n>200|250|450|550|570|750)$/i, false],
  ['APC', /^(?:RACK\s)?MONITOR\s(?<n>200|250|450|550|570|750)$/i, true],
  ['Juniper', /^(?:EX|QFX|MX|SRX|ACX|PTX)\d{3,5}[A-Z]?(?:-[A-Z0-9]{1,6})*$/i, false],
  ['Arista', /^DCS-7\d{2,3}[A-Z0-9]*(?:-[A-Z0-9]{1,6})*$/i, false],
  ['Arista', /^7\d{3}[A-Z]{1,4}-\d{2}[A-Z0-9]*(?:-[A-Z0-9]{1,6})*$/i, true],
  ['Fortinet', /^F[GSW]-?\d{2,4}[A-Z](?:-[A-Z0-9]{1,6})*$/i, false],
  ['Fortinet', /^FORTIGATE[\s-]?(?<n>\d{2,4}[A-Z])$/i, false],
  ['Palo Alto', /^PA-\d{3,4}[A-Z]?$/i, false],
  ['MikroTik', /^(?:CRS|CCR|CSS)\d{3,4}(?:-[A-Z0-9+]{1,12})*$/i, false],
  ['MikroTik', /^RB\s?\d{3,4}\s?[A-Z]{0,6}(?:-[A-Z0-9+]{1,6})*$/i, false],
  ['Ubiquiti', /^USW-[A-Z][A-Z0-9]*(?:-[A-Z0-9]{1,12}){0,3}$/i, false],
  ['Ubiquiti', /^U(?:SG|DM|XG)-[A-Z0-9]{2,12}(?:-[A-Z0-9]{1,8}){0,2}$/i, false],
  ['NETGEAR', /^(?:GS|JGS|XS|M4300)\d{2,4}[A-Z]{0,4}(?:-[A-Z0-9]{1,8})?$/i, true],
  ['SonicWall', /^(?:TZ|NSA|NSV)-?\d{3,4}[A-Z]?$/i, true],
  ['Huawei', /^(?:S|CE)\d{4}[A-Z]?-\d{1,3}[A-Z0-9-]*$/i, true],
  ['Dell', /^(?:S|N|Z)\d{4}[A-Z]{0,3}-?(?:ON)?$/i, true],
  ['Dell', /^(?:R|T)\d{3}(?:XD|XS)?$/i, true],
  ['Synology', /^(?:DS|RS)\d{3,4}\+?(?:II|III)?$/i, true],
  ['QNAP', /^(?:TS|TVS|TES)-\d{2,4}[A-Z+]{0,6}$/i, false],
  ['Eaton', /^(?:5SC|5PX|9SX|9PX)\d{3,4}[A-Z0-9]{0,6}$/i, false],
];

const MAKE_WORDS = {
  cisco: ['cisco', 'csco', 'catalyst', 'nexus', 'meraki'],
  'tp-link': ['tplink', 'tp-link', 'jetstream', 'omada'],
  'd-link': ['dlink', 'd-link'],
  hpe: ['hpe', 'hp', 'hewlett', 'aruba', 'procurve', 'proliant', 'officeconnect'],
  apc: ['apc', 'netbotz', 'schneider', 'smart-ups', 'smartups'],
  juniper: ['juniper'],
  arista: ['arista'],
  fortinet: ['fortinet', 'fortigate', 'fortiswitch'],
  'palo alto': ['paloalto', 'palo'],
  mikrotik: ['mikrotik', 'routerboard', 'mikrorik'],
  ubiquiti: ['ubiquiti', 'unifi'],
  netgear: ['netgear', 'prosafe'],
  sonicwall: ['sonicwall'],
  huawei: ['huawei'],
  dell: ['dell', 'poweredge', 'powerswitch'],
  synology: ['synology'],
  qnap: ['qnap'],
  eaton: ['eaton'],
};
const SAME_MAKER = { hp: 'hpe', aruba: 'hpe', 'hewlett packard enterprise': 'hpe', 'hewlett-packard': 'hpe' };
const maker = (name) => { const k = String(name || '').trim().toLowerCase(); return SAME_MAKER[k] || k; };

const matchesShape = (span) => SHAPES.some(([, rx]) => rx.test(span));

function isNameLike(t) {
  if (!t) return false;
  const lowers = (t.match(/[a-z]/g) || []).length;
  const uppers = (t.match(/[A-Z]/g) || []).length;
  const hyphens = (t.match(/-/g) || []).length;
  if (HOSTNAME_RE.test(t) && (hyphens >= 2 || lowers > uppers)) return true;
  if (hyphens >= 2 && lowers > uppers) return true;
  if (UNIT_TOKEN_RE.test(t) && /[-_. ]/.test(t)) return true;
  if (ASSET_TAG_RE.test(t) && t.split(/[-_. ]/).length >= 3) return true;
  // eslint-disable-next-line no-control-regex
  if (/[^\x00-\x7f]/.test(t) && t.includes('-')) return true;
  return false;
}

/** Why a word is never a model (a short reason), or null when it may be one. */
function notAModel(token) {
  const t = clean(token);
  if (!t) return 'empty';
  const low = t.toLowerCase();
  if (PORT_WORDS.has(low) || PORT_RE.test(t)) return 'port';
  if (REGULATORY.has(low) || REGULATORY_RE.test(t)) return 'regulatory';
  if (low.startsWith('www.') || low.startsWith('http') || low.includes('.com')) return 'web address';
  if (MAC_RE.test(t)) return 'mac';
  if (IP_RE.test(t)) return 'ip';
  if (low.startsWith('s/n') || low.startsWith('sn:')) return 'serial';
  if (LOCATION_RE.test(t)) return 'location';
  if (matchesShape(t)) return null;
  if (DIGITS_RE.test(t)) return 'digits';
  if (!/[A-Za-z]/.test(t) || !/\d/.test(t)) return 'no letters and digits';
  if (SHORT_RE.test(t)) return 'short';
  if (isNameLike(t)) return 'name';
  if (t.length >= 10 && SERIAL_RE.test(t.toUpperCase()) && !t.includes('-')) return 'serial';
  return null;
}

function words(text) {
  const raw = String(text || '').split(/\s+/).filter(Boolean);
  const out = raw.map((tok, i) => {
    const t = clean(tok);
    const prev = i > 0 ? raw[i - 1].toLowerCase() : '';
    const prev2 = i > 1 ? raw[i - 2].toLowerCase() : '';
    let why = notAModel(t);
    if ((why == null || SOFT.has(why)) && (CONTEXT_WORDS.has(prev) || (['mac', 'loc:', 'loc'].includes(prev2) && ['address', 'rack'].includes(prev)))) {
      why = `after ${prev.replace(/:$/, '')}`;
    }
    return { tok: t, low: t.toLowerCase(), skip: why };
  });
  // A cable tag "sw05b Gi1-0-26 B12": the word before an interface is a name.
  for (let i = 0; i < out.length - 1; i += 1) {
    if (out[i].skip == null && out[i + 1].skip === 'port' && /^(?:gi|te|eth|et|fa|xe|hu)/.test(out[i + 1].low) && !matchesShape(out[i].tok)) {
      out[i].skip = 'cable tag';
    }
  }
  return out;
}

function* spans(ws, maxLen = 3) {
  for (let i = 0; i < ws.length; i += 1) {
    for (let k = 1; k <= maxLen && i + k <= ws.length; k += 1) {
      const part = ws.slice(i, i + k);
      if (part.some((w) => !w.tok || (w.skip && !SOFT.has(w.skip)))) break;
      if (k === 1 && part[0].skip) continue;
      yield { i, k, span: part.map((w) => w.tok).join(' ') };
    }
  }
}

function format(vendor, m) {
  const g = m.groups || {};
  if (g.n) {
    if (vendor === 'APC') return `NetBotz ${g.n}`;
    if (vendor === 'Fortinet') return `FG-${g.n.toUpperCase()}`;
  }
  if (g.m) {
    if (/^nexus/i.test(g.m)) return `Nexus ${g.m.replace(/\D/g, '')}`;
    return g.m.replace(/\s+/g, '').toUpperCase();
  }
  let s = m[0];
  if (vendor === 'HPE') s = s.replace(/^HPE?\s?/i, '');
  return s.replace(/\s+/g, '').toUpperCase();
}

function makeNear(ws, i, k, vendor) {
  const kws = MAKE_WORDS[maker(vendor)] || [vendor.toLowerCase()];
  for (let j = Math.max(0, i - 3); j < Math.min(ws.length, i + k + 3); j += 1) {
    if (j >= i && j < i + k) continue;
    const w = ws[j].low.replace(/[^a-z-]/g, '');
    if (w && (kws.includes(w) || kws.includes(w.replace(/-/g, '')))) return true;
  }
  return false;
}

const better = (a, b) => { for (let x = 0; x < a.length; x += 1) { if (a[x] !== b[x]) return a[x] > b[x]; } return false; };

/** The clean model token read in the text, as { make, model }, or null. */
function modelFromText(text, make = null) {
  const ws = words(text);
  const want = maker(make);
  let best = null;
  for (const { i, k, span } of spans(ws)) {
    SHAPES.forEach(([vendor, rx, weak], order) => {
      const m = span.match(rx);
      if (!m) return;
      const same = !!want && maker(vendor) === want;
      if (weak && !same) return;
      const score = [same ? 2 : 0, makeNear(ws, i, k, vendor) ? 1 : 0, weak ? 0 : 1, norm(span).length, -order];
      if (!best || better(score, best.score)) best = { score, make: vendor, model: format(vendor, m) };
    });
  }
  return best ? { make: best.make, model: best.model } : null;
}

function lev1(a, b) {
  if (a === b) return true;
  if (Math.abs(a.length - b.length) > 1) return false;
  if (a.length === b.length) { let d = 0; for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) d += 1; return d === 1; }
  const [s, l] = a.length < b.length ? [a, b] : [b, a];
  let i = 0;
  while (i < s.length && s[i] === l[i]) i += 1;
  return s.slice(i) === l.slice(i + 1);
}

/** The model is in the text as its own word(s), not inside a name, tag, serial, location or port. */
function modelInText(model, text) {
  if (!model) return false;
  const m = String(model).trim();
  if (notAModel(m) && !matchesShape(m)) return false;
  const want = norm(m);
  if (want.length < 4) return false;
  for (const { span } of spans(words(text))) {
    const got = norm(span);
    if (got === want || (want.length >= 5 && lev1(got, want))) return true;
  }
  return false;
}

module.exports = { modelFromText, modelInText, notAModel, matchesShape };
