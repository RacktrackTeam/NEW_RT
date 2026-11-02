/**
 * What the manager's workbook says beyond the cables (lib/workbook.js), in
 * the shapes the checks already work with. A doc from before the manager's
 * workbook has no racks, devices or ports, and every helper then gives
 * nothing extra, so the checks behave as they did.
 *
 *   rackNameOf(doc, rackId)    the name a rack goes by in the checks (Rack_Name, else the ID)
 *   inventoryDevices(doc)      the active devices: { name, aliases, rack, unit, height, role, passive }
 *   rackAliases(doc)           the active racks: { name, aliases, height }
 *   placementRows(doc)         rows shaped like cables (a_device, a_rack, a_unit) for the helpers
 *                              that read only where things stand (rack naming, rack height)
 *   expectedPorts(doc, name, rack, aliases)  Map(port number -> what the workbook expects there)
 */
'use strict';

const { portNumber } = require('./scan_view');
const { portFamily } = require('./workbook');

const key = (s) => String(s ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '');
const active = (x) => !x.record_status || key(x.record_status) === 'ACTIVE';
const num = (v) => { const m = String(v ?? '').match(/\d+/); return m ? Number(m[0]) : null; };

// Things in a rack with no ports that the photo's device model has no class
// for: it reads them as an unidentified unit, so they are never "not seen".
const PASSIVE = /cable\s*manag|blank|brush|filler|shelf|tray/i;

const MEMO = new WeakMap();
function memo(doc, name, build) {
  if (!doc || typeof doc !== 'object') return build();
  if (!MEMO.has(doc)) MEMO.set(doc, {});
  const m = MEMO.get(doc);
  if (!(name in m)) m[name] = build();
  return m[name];
}

function rackNameOf(doc, rackId) {
  if (!rackId) return '';
  const r = (doc?.racks || []).find((x) => key(x.rack_id) === key(rackId));
  return (r && (r.rack_name || r.rack_id)) || String(rackId).trim();
}

function rackAliases(doc) {
  return memo(doc, 'racks', () => (doc?.racks || []).filter(active).map((r) => ({
    name: r.rack_name || r.rack_id,
    aliases: [...new Set([r.rack_id, r.expected_label].filter((a) => a && key(a) !== key(r.rack_name || r.rack_id)))],
    height: num(r.height_u),
  })));
}

function inventoryDevices(doc) {
  return memo(doc, 'devices', () => (doc?.devices || []).filter(active).filter((d) => d.device_name).map((d) => ({
    name: d.device_name,
    aliases: [...new Set([d.device_id, d.expected_label].filter((a) => a && key(a) !== key(d.device_name)))],
    rack: rackNameOf(doc, d.rack_id),
    unit: num(d.ru_start),
    height: num(d.ru_height),
    role: d.role || '',
    passive: PASSIVE.test(d.role || ''),
  })));
}

/**
 * Rows shaped like cables for the helpers that only read where devices and
 * racks stand: one per inventory device at its lowest unit, one at its top
 * unit, one per other name it goes by, and one per rack at its height (a
 * row with a rack and a unit but no device).
 */
function placementRows(doc) {
  return memo(doc, 'rows', () => {
    const rows = [];
    for (const d of inventoryDevices(doc)) {
      if (!d.rack) continue;
      rows.push({ a_device: d.name, a_rack: d.rack, a_unit: d.unit != null ? String(d.unit) : '', a_port: '', inventory: true });
      if (d.unit != null && d.height > 1) rows.push({ a_device: d.name, a_rack: d.rack, a_unit: String(d.unit + d.height - 1), a_port: '', inventory: true });
      for (const a of d.aliases) rows.push({ a_device: a, a_rack: d.rack, a_unit: '', a_port: '', inventory: true });
    }
    for (const r of rackAliases(doc)) {
      if (r.height) rows.push({ a_device: '', a_rack: r.name, a_unit: String(r.height), a_port: '', inventory: true });
    }
    return rows;
  });
}

/**
 * What the workbook expects on each port of one device in one rack:
 * Map(portNo -> { occupied, dustCap, portType, opticType, label, cable, end, panel })
 * from PORT_INVENTORY, then each cable end on the device (its own fields win
 * where the inventory said nothing), then each cable whose path passes the
 * device as its patch panel (A_Patch_Panel_Name and A_Panel_Position):
 * occupied there. portAt(code) places a printed position code ("14A") on the
 * device; without it a code is not placed.
 */
function expectedPorts(doc, name, rack, aliases = [], portAt = null) {
  const out = new Map();
  if (!doc || !name) return out;
  const names = new Set([name, ...aliases].map(key));
  const inRack = (r) => !r || !rack || key(r) === key(rack);
  const put = (n, fields) => {
    if (n == null) return;
    const cur = out.get(n) || {};
    for (const [k, v] of Object.entries(fields)) if (v != null && v !== '' && (cur[k] == null || cur[k] === '')) cur[k] = v;
    out.set(n, cur);
  };
  const place = (port) => {
    const s = String(port ?? '').trim();
    if (!s) return null;
    if (/^\d{1,3}\s*[A-Za-z]$/.test(s)) return portAt ? portAt(s) : null;
    return portNumber(s);
  };
  const devIds = new Set((doc.devices || []).filter((d) => names.has(key(d.device_name)) || names.has(key(d.device_id))).map((d) => key(d.device_id)).filter(Boolean));
  for (const p of doc.ports || []) {
    if (!active(p) || !(devIds.has(key(p.device_id)) || names.has(key(p.device_id)))) continue;
    if (p.rack_id && !inRack(rackNameOf(doc, p.rack_id)) && !inRack(p.rack_id)) continue;
    put(num(p.port_number) ?? place(p.port_name), {
      occupied: p.expected_occupied, dustCap: p.expected_dust_cap, portType: p.port_type, opticType: p.optic_type, label: p.expected_label, alias: p.alias,
    });
  }
  for (const c of doc.cables || []) {
    for (const e of ['a', 'z']) {
      const end = c[e] || {};
      if (names.has(key(c[`${e}_device`])) && inRack(c[`${e}_rack`])) {
        put(num(end.port_number) ?? place(c[`${e}_port`]), {
          occupied: end.expected_occupied, dustCap: end.expected_dust_cap, portType: end.port_type, opticType: end.optic_type,
          label: c[`label_${e}`], cable: c, end: e,
        });
      }
      if (end.panel_name && names.has(key(end.panel_name)) && inRack(c[`${e}_rack`])) {
        put(place(end.panel_position), { occupied: 'Yes', cable: c, end: e, panel: true });
      }
    }
  }
  return out;
}

/** The family a photo port reads as (SFP, QSFP, OSFP, RJ45), from the SFP model first, then the port type. */
function photoFamily(p) {
  return portFamily(p?.sfpType) || portFamily(p?.fixedType) || portFamily(p?.type);
}

module.exports = { PASSIVE, rackNameOf, rackAliases, inventoryDevices, placementRows, expectedPorts, photoFamily, key };
