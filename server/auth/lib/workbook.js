/**
 * The manager's source-of-truth workbook (RackTrack_A_Z_Source_of_Truth_Upload_Matrix.xlsx,
 * a copy in reference/): the only Excel layout RT-EOS imports.
 *
 *   A_Z_CONNECTION_MATRIX   one row per expected cable, both ends in full (the main import)
 *   RACK_INVENTORY          the racks: name, height, the label on the rack
 *   DEVICE_INVENTORY        the devices: rack, RU start and height, the label on the device
 *   PORT_INVENTORY          the ports: expected occupied, dust cap, port type, optic, label
 *   LABEL_RULES             how printed labels read
 *   LOOKUPS                 the allowed values
 *   README, FIELD_DICTIONARY  read by people; not imported
 *
 * parse(read) takes the reader's answer (pipeline/matrix_read.py: every sheet,
 * every row as text) and returns what RT-EOS keeps:
 *   { ok, error, sheets, cables, racks, devices, ports, labelRules, lookups, problems, counts }
 *
 * A cable keeps the fields every check already reads (cable_label, a_device,
 * a_port, a_rack, a_unit, z_..., cable_type, connector, cable_color, notes,
 * label_a, label_z, label_alias) plus the manager's own facts the new checks
 * use, and its whole row as `raw` (each column by its header). Only a cable
 * whose Record_Status is Active and whose Expected_State is Connected is
 * checked against a photo (`checked`); the others are kept and shown.
 *
 * Column names are matched with case, spaces and punctuation ignored, so
 * "A_Device_Name", "A Device Name" and "a-device-name" are the same column.
 * A pure module: nothing here touches the disk.
 */
'use strict';

const MAIN = 'A_Z_CONNECTION_MATRIX';
const SHEETS = {
  [MAIN]: 'cables',
  RACK_INVENTORY: 'racks',
  DEVICE_INVENTORY: 'devices',
  PORT_INVENTORY: 'ports',
  LABEL_RULES: 'labelRules',
  LOOKUPS: 'lookups',
  README: null,
  FIELD_DICTIONARY: null,
};

const YES_NO = ['Yes', 'No', 'N/A'];
// The template's own lists (its LOOKUPS sheet), used when a workbook has none.
const DEFAULT_LOOKUPS = {
  Record_Status: ['Active', 'Planned', 'Decommissioned', 'Superseded'],
  Port_Type: ['OSFP', 'QSFP-DD', 'QSFP28', 'QSFP+', 'SFP28', 'SFP+', 'SFP', 'RJ45'],
  Connector_Type: ['LC', 'SC', 'MPO-8', 'MPO-12', 'MPO-16', 'MPO-24', 'RJ45'],
  Fiber_Mode: ['SMF', 'MMF-OM3', 'MMF-OM4', 'MMF-OM5'],
  Criticality: ['Low', 'Medium', 'High', 'Critical'],
  Expected_State: ['Connected', 'Planned', 'Disconnected', 'Spare', 'Decommissioned'],
};
const PROBLEMS_KEPT = 500;

const key = (h) => String(h == null ? '' : h).toLowerCase().replace(/[^a-z0-9]/g, '');
const text = (v) => String(v == null ? '' : v).trim();
const same = (a, b) => key(a) !== '' && key(a) === key(b);

/** An Excel date kept as its day number (46294) as an ISO date; any other text as it is. */
function dateText(v) {
  const s = text(v);
  if (/^\d{5}(\.\d+)?$/.test(s)) {
    const d = new Date(Date.UTC(1899, 11, 30) + Math.floor(Number(s)) * 86400000);
    return d.toISOString().slice(0, 10);
  }
  return s.replace(/T00:00:00$/, '');
}

/** Yes / No / N/A in any case, else the text as written. */
function yesNo(v) {
  const s = text(v);
  const hit = YES_NO.find((x) => key(x) === key(s));
  return hit || s;
}

/** A sheet's rows as objects keyed by header, with the sheet's own row numbers. */
function rowsOf(sheet) {
  const headers = (sheet.headers || []).map(text);
  const out = [];
  (sheet.rows || []).forEach((r, i) => {
    if (!(r || []).some((c) => text(c))) return;
    const raw = {};
    headers.forEach((h, j) => { if (h) raw[h] = text(r[j]); });
    for (const h of Object.keys(raw)) if (/date$/i.test(h)) raw[h] = dateText(raw[h]);
    out.push({ rowNo: (sheet.header_row ?? 0) + 2 + i, raw });
  });
  return out;
}

/** A getter over one row's raw cells by column name, case and punctuation ignored. */
function getter(raw) {
  const byKey = new Map(Object.entries(raw).map(([h, v]) => [key(h), v]));
  return (...names) => {
    for (const n of names) {
      const v = byKey.get(key(n));
      if (v != null && v !== '') return v;
    }
    return '';
  };
}

const unitText = (v) => { const m = text(v).match(/\d+/); return m ? m[0] : ''; };

/** The family of a port type, so a photo's "QSFP" fits the sheet's "QSFP28". */
function portFamily(t) {
  const s = key(t);
  if (!s) return null;
  if (s.includes('osfp')) return 'OSFP';
  if (s.includes('qsfp')) return 'QSFP';
  if (s.includes('sfp')) return 'SFP';
  if (s.includes('rj45') || s.includes('8p8c') || s.includes('copper') || s.includes('ethernet')) return 'RJ45';
  return null;
}

function endOf(get, e) {
  const E = e.toUpperCase();
  const f = (name) => get(`${E}_${name}`);
  return {
    site: f('Site'), building: f('Building'), floor: f('Floor'), room: f('Room_Hall'), row: f('Row'),
    rack_id: f('Rack_ID'), rack_name: f('Rack_Name'), rack_zone: f('Rack_Zone'),
    device_id: f('Device_ID'), device_name: f('Device_Name'), device_role: f('Device_Role'),
    manufacturer: f('Manufacturer'), model: f('Model'), serial: f('Serial_Number'),
    ru_start: unitText(f('RU_Start')), ru_height: unitText(f('RU_Height')),
    panel_id: f('Patch_Panel_ID'), panel_name: f('Patch_Panel_Name'), panel_ru: unitText(f('Panel_RU')), panel_position: f('Panel_Position'),
    port_id: f('Port_ID'), port_name: f('Port_Name'), port_number: f('Port_Number'), port_type: f('Port_Type'), port_speed: f('Port_Speed'),
    connector_type: f('Connector_Type'), optic_type: f('Optic_Type'), optic_part: f('Optic_Part_Number'), optic_serial: f('Optic_Serial'),
    expected_occupied: yesNo(f('Expected_Occupied')), expected_dust_cap: yesNo(f('Expected_Dust_Cap')),
    label_text: f('Label_Expected_Text'), label_position: f('Label_Position'), photo_zone: f('Expected_Photo_Zone'),
  };
}

/** The record status as the template spells it; blank is Active. */
function statusOf(v) {
  const s = text(v);
  if (!s) return 'Active';
  return DEFAULT_LOOKUPS.Record_Status.find((x) => same(x, s)) || s;
}

function cableOf({ rowNo, raw }) {
  const get = getter(raw);
  const a = endOf(get, 'a');
  const z = endOf(get, 'z');
  const status = statusOf(get('Record_Status'));
  const state = text(get('Expected_State')) ? (DEFAULT_LOOKUPS.Expected_State.find((x) => same(x, get('Expected_State'))) || text(get('Expected_State'))) : 'Connected';
  const media = [get('Media_Type'), get('Fiber_Mode')].filter(Boolean);
  const c = {
    row_no: rowNo,
    // The fields every check reads.
    cable_label: get('Cable_Label_Primary', 'Cable_ID'),
    a_device: a.device_name || a.device_id,
    a_port: a.port_name || a.port_number || a.port_id,
    a_rack: a.rack_name || a.rack_id,
    a_unit: a.ru_start,
    z_device: z.device_name || z.device_id,
    z_port: z.port_name || z.port_number || z.port_id,
    z_rack: z.rack_name || z.rack_id,
    z_unit: z.ru_start,
    cable_type: media.length > 1 && same(media[0], 'Fiber') ? media[1] : media.join(' '),
    connector: a.connector_type || z.connector_type,
    cable_color: get('Cable_Color'),
    notes: get('Notes'),
    label_a: get('Cable_Label_A_End') || a.label_text,
    label_z: get('Cable_Label_Z_End') || z.label_text,
    label_alias: get('Cable_Label_Alias'),
    // The manager's facts.
    record_status: status,
    expected_state: state,
    checked: same(status, 'Active') && same(state, 'Connected'),
    connection_id: get('Connection_ID'),
    cable_id: get('Cable_ID'),
    circuit_id: get('Circuit_Link_ID'),
    media_type: get('Media_Type'),
    fiber_mode: get('Fiber_Mode'),
    polarity: get('Polarity'),
    length_m: get('Cable_Length_m'),
    source_revision: get('Source_Revision'),
    change_ticket: get('Change_Ticket_ID'),
    a: a,
    z: z,
    raw,
  };
  return c;
}

function rackOf({ rowNo, raw }) {
  const get = getter(raw);
  return {
    row_no: rowNo,
    record_status: statusOf(get('Record_Status')),
    rack_id: get('Rack_ID'),
    rack_name: get('Rack_Name') || get('Rack_ID'),
    site: get('Site'), building: get('Building'), floor: get('Floor'), room: get('Room_Hall'), row: get('Row'),
    height_u: unitText(get('Rack_Height_RU')),
    expected_label: get('Expected_Rack_Label'),
    zone: get('Zone'),
    raw,
  };
}

function deviceOf({ rowNo, raw }) {
  const get = getter(raw);
  return {
    row_no: rowNo,
    record_status: statusOf(get('Record_Status')),
    site: get('Site'),
    rack_id: get('Rack_ID'),
    device_id: get('Device_ID'),
    device_name: get('Device_Name') || get('Device_ID'),
    role: get('Device_Role'),
    manufacturer: get('Manufacturer'), model: get('Model'), serial: get('Serial_Number'), asset_tag: get('Asset_Tag'),
    ru_start: unitText(get('RU_Start')), ru_height: unitText(get('RU_Height')),
    face: get('Face'),
    expected_label: get('Expected_Device_Label'),
    photo_zone: get('Photo_Zone'),
    raw,
  };
}

function portOf({ rowNo, raw }) {
  const get = getter(raw);
  return {
    row_no: rowNo,
    record_status: statusOf(get('Record_Status')),
    rack_id: get('Rack_ID'),
    device_id: get('Device_ID'),
    port_id: get('Port_ID'),
    port_name: get('Port_Name'),
    port_number: get('Port_Number'),
    port_type: get('Port_Type'),
    port_speed: get('Port_Speed'),
    connector_type: get('Connector_Type'),
    optic_type: get('Expected_Optic_Type'),
    expected_occupied: yesNo(get('Expected_Occupied')),
    expected_dust_cap: yesNo(get('Expected_Dust_Cap')),
    expected_label: get('Expected_Label_Text'),
    alias: get('Label_Alias'),
    photo_zone: get('Photo_Zone'),
    position_row: get('Port_Position_Row'),
    position_column: get('Port_Position_Column'),
    raw,
  };
}

function labelRuleOf({ rowNo, raw }) {
  const get = getter(raw);
  return {
    row_no: rowNo,
    rule_id: get('Rule_ID'),
    object_type: get('Object_Type'),
    applies_to: get('Applies_To'),
    pattern: get('Expected_Pattern'),
    example: get('Example'),
    case_sensitive: yesNo(get('Case_Sensitive')),
    allow_spaces: yesNo(get('Allow_Spaces')),
    separators: get('Allowed_Separators'),
    normalisation: get('OCR_Normalization'),
    alias_handling: get('Alias_Handling'),
    required: get('Required'),
    notes: get('Notes'),
  };
}

/** LOOKUPS: one list per column, read down the column. */
function lookupsOf(sheet) {
  const headers = (sheet.headers || []).map(text);
  const out = {};
  headers.forEach((h, j) => {
    if (!h) return;
    out[h] = (sheet.rows || []).map((r) => text(r[j])).filter(Boolean);
  });
  return out;
}

/**
 * @param {{ sheets: { name, header_row, headers, rows }[], error?: string }} read
 */
function parse(read) {
  const sheetsIn = read?.sheets || [];
  const find = (name) => sheetsIn.find((s) => same(s.name, name));
  const main = find(MAIN);
  if (!main) {
    return {
      ok: false,
      error: `This is not the manager's workbook: it has no ${MAIN} sheet. Download the template from the Excel page and fill it in.`,
    };
  }
  const problems = [];
  let problemCount = 0;
  let errorCount = 0;
  const note = (level, sheet, rowNo, reason) => {
    problemCount += 1;
    if (level === 'error') errorCount += 1;
    if (problems.length < PROBLEMS_KEPT) problems.push({ level, sheet, row_no: rowNo, reason });
  };

  const cables = rowsOf(main).map(cableOf);
  const racks = find('RACK_INVENTORY') ? rowsOf(find('RACK_INVENTORY')).map(rackOf) : [];
  const devices = find('DEVICE_INVENTORY') ? rowsOf(find('DEVICE_INVENTORY')).map(deviceOf) : [];
  const ports = find('PORT_INVENTORY') ? rowsOf(find('PORT_INVENTORY')).map(portOf) : [];
  const labelRules = find('LABEL_RULES') ? rowsOf(find('LABEL_RULES')).map(labelRuleOf).filter((r) => r.rule_id || r.object_type) : [];
  const lookupsRead = find('LOOKUPS') ? lookupsOf(find('LOOKUPS')) : {};
  const lookups = { ...DEFAULT_LOOKUPS };
  for (const [k, list] of Object.entries(lookupsRead)) if (list.length) lookups[k] = list;

  for (const name of Object.keys(SHEETS)) {
    if (SHEETS[name] && name !== MAIN && !find(name)) note('warning', name, null, `The ${name} sheet is missing. The import goes on without it.`);
  }

  // ── Cables ────────────────────────────────────────────────────────
  const seen = { connection: new Map(), cable: new Map(), label: new Map() };
  const once = (map, value, rowNo, what) => {
    if (!value) return;
    const k = key(value);
    if (map.has(k)) note('error', MAIN, rowNo, `${what} ${value} is also on row ${map.get(k)}. An ID is never used for two cables.`);
    else map.set(k, rowNo);
  };
  const rackIds = new Set(racks.map((r) => key(r.rack_id)).filter(Boolean));
  const deviceIds = new Set(devices.map((d) => key(d.device_id)).filter(Boolean));
  const portIds = new Set(ports.map((p) => key(p.port_id)).filter(Boolean));
  const inList = (list, v) => !v || (list || []).some((x) => same(x, v));
  for (const c of cables) {
    const r = c.row_no;
    if (/illustrative sample/i.test(c.notes)) note('warning', MAIN, r, 'This is the template\'s sample row. Replace it with the real design or delete it.');
    for (const e of ['a', 'z']) {
      const E = e.toUpperCase();
      if (!c[`${e}_device`]) note('error', MAIN, r, `The ${E}-end has no device (${E}_Device_Name or ${E}_Device_ID), so this end cannot be checked.`);
      if (!c[`${e}_port`]) note('error', MAIN, r, `The ${E}-end has no port (${E}_Port_Name, ${E}_Port_Number or ${E}_Port_ID), so this end cannot be checked.`);
      if (!c[`${e}_rack`]) note('warning', MAIN, r, `The ${E}-end has no rack (${E}_Rack_ID or ${E}_Rack_Name).`);
      const end = c[e];
      if (end.rack_id && racks.length && !rackIds.has(key(end.rack_id))) note('warning', MAIN, r, `${E}_Rack_ID ${end.rack_id} is not in RACK_INVENTORY.`);
      if (end.device_id && devices.length && !deviceIds.has(key(end.device_id))) note('warning', MAIN, r, `${E}_Device_ID ${end.device_id} is not in DEVICE_INVENTORY.`);
      if (end.port_id && ports.length && !portIds.has(key(end.port_id))) note('warning', MAIN, r, `${E}_Port_ID ${end.port_id} is not in PORT_INVENTORY.`);
      if (end.port_type && !inList(lookups.Port_Type, end.port_type)) note('warning', MAIN, r, `${E}_Port_Type "${end.port_type}" is not one of the LOOKUPS values.`);
      if (end.connector_type && !inList(lookups.Connector_Type, end.connector_type)) note('warning', MAIN, r, `${E}_Connector_Type "${end.connector_type}" is not one of the LOOKUPS values.`);
      for (const f of ['expected_occupied', 'expected_dust_cap']) {
        if (end[f] && !YES_NO.includes(end[f])) note('warning', MAIN, r, `${E}_${f === 'expected_occupied' ? 'Expected_Occupied' : 'Expected_Dust_Cap'} should be Yes, No or N/A, not "${end[f]}".`);
      }
    }
    if (!c.cable_label) note('error', MAIN, r, 'The cable has no label (Cable_Label_Primary or Cable_ID).');
    if (!c.connection_id) note('warning', MAIN, r, 'Connection_ID is empty. The template needs it on every row.');
    if (!c.cable_id) note('warning', MAIN, r, 'Cable_ID is empty. The template needs it on every row.');
    if (!c.source_revision) note('warning', MAIN, r, 'Source_Revision is empty. The template needs it on every row.');
    if (!inList(lookups.Record_Status, c.record_status)) note('warning', MAIN, r, `Record_Status "${c.record_status}" is not one of the LOOKUPS values.`);
    if (!inList(lookups.Expected_State, c.expected_state)) note('warning', MAIN, r, `Expected_State "${c.expected_state}" is not one of the LOOKUPS values.`);
    if (c.fiber_mode && !inList(lookups.Fiber_Mode, c.fiber_mode)) note('warning', MAIN, r, `Fiber_Mode "${c.fiber_mode}" is not one of the LOOKUPS values.`);
    const crit = c.raw && getter(c.raw)('Criticality');
    if (crit && !inList(lookups.Criticality, crit)) note('warning', MAIN, r, `Criticality "${crit}" is not one of the LOOKUPS values.`);
    if (same(c.record_status, 'Active')) {
      once(seen.connection, c.connection_id, r, 'Connection_ID');
      once(seen.cable, c.cable_id, r, 'Cable_ID');
      once(seen.label, c.cable_label, r, 'Cable label');
    }
  }

  // ── Inventories ───────────────────────────────────────────────────
  const unique = (list, field, sheet, what) => {
    const m = new Map();
    for (const x of list) {
      if (!x[field]) { note('warning', sheet, x.row_no, `${what} is empty.`); continue; }
      if (!same(x.record_status, 'Active')) continue;
      const k = key(x[field]);
      if (m.has(k)) note('error', sheet, x.row_no, `${what} ${x[field]} is also on row ${m.get(k)}.`);
      else m.set(k, x.row_no);
    }
  };
  unique(racks, 'rack_id', 'RACK_INVENTORY', 'Rack_ID');
  unique(devices, 'device_id', 'DEVICE_INVENTORY', 'Device_ID');
  unique(ports, 'port_id', 'PORT_INVENTORY', 'Port_ID');
  for (const d of devices) {
    if (d.rack_id && racks.length && !rackIds.has(key(d.rack_id))) note('warning', 'DEVICE_INVENTORY', d.row_no, `Rack_ID ${d.rack_id} is not in RACK_INVENTORY.`);
    if (!d.ru_start) note('warning', 'DEVICE_INVENTORY', d.row_no, `${d.device_name || 'This device'} has no RU_Start, so its unit cannot be checked.`);
  }
  for (const p of ports) {
    if (p.device_id && devices.length && !deviceIds.has(key(p.device_id))) note('warning', 'PORT_INVENTORY', p.row_no, `Device_ID ${p.device_id} is not in DEVICE_INVENTORY.`);
    if (p.port_type && !inList(lookups.Port_Type, p.port_type)) note('warning', 'PORT_INVENTORY', p.row_no, `Port_Type "${p.port_type}" is not one of the LOOKUPS values.`);
    for (const f of ['expected_occupied', 'expected_dust_cap']) {
      if (p[f] && !YES_NO.includes(p[f])) note('warning', 'PORT_INVENTORY', p.row_no, `${f === 'expected_occupied' ? 'Expected_Occupied' : 'Expected_Dust_Cap'} should be Yes, No or N/A, not "${p[f]}".`);
    }
  }
  // Two devices of one rack in the same unit.
  const byRack = new Map();
  for (const d of devices) {
    if (!same(d.record_status, 'Active') || !d.ru_start || !d.rack_id) continue;
    const k = key(d.rack_id);
    if (!byRack.has(k)) byRack.set(k, []);
    byRack.get(k).push(d);
  }
  for (const list of byRack.values()) {
    list.sort((x, y) => Number(x.ru_start) - Number(y.ru_start));
    for (let i = 1; i < list.length; i += 1) {
      const prev = list[i - 1];
      const top = Number(prev.ru_start) + Math.max(1, Number(prev.ru_height) || 1) - 1;
      if (Number(list[i].ru_start) <= top) note('warning', 'DEVICE_INVENTORY', list[i].row_no, `${list[i].device_name} at U${list[i].ru_start} overlaps ${prev.device_name} (U${prev.ru_start} to U${top}).`);
    }
  }

  const checked = cables.filter((c) => c.checked).length;
  return {
    ok: true,
    error: null,
    sheets: sheetsIn.map((s) => {
      const role = Object.entries(SHEETS).find(([n]) => same(n, s.name));
      return { name: s.name, used: !!(role && role[1]), rows: rowsOf(s).length };
    }),
    cables, racks, devices, ports, labelRules, lookups,
    problems,
    counts: {
      cables: cables.length, checked, notChecked: cables.length - checked,
      racks: racks.length, devices: devices.length, ports: ports.length, labelRules: labelRules.length,
      problems: problemCount, errors: errorCount, warnings: problemCount - errorCount,
    },
  };
}

module.exports = { parse, portFamily, key, dateText, yesNo, MAIN, SHEETS, DEFAULT_LOOKUPS };
