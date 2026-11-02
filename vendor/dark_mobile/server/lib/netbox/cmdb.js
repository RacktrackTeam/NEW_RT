/**
 * Domain object -> ServiceNow CMDB shape. One table, one place.
 *
 * The twin of mapping.js. That file holds every piece of "what NetBox calls
 * this"; this one holds every piece of "what ServiceNow calls this", in the
 * same declarative shape, so both translations stay auditable and neither
 * hides in a writer loop.
 *
 * Three things make the CMDB side genuinely different from NetBox, and the
 * `how` on every column is what records which case a field is in:
 *
 *   FIELD    a plain column on the CI row. The ordinary case.
 *   CUSTOM   a u_ column. ServiceNow's own convention for a value the base
 *            table has no home for. Never `attributes`, which is a different
 *            thing entirely.
 *   REF      a sys_id pointing at another CI. NetBox would use a nested
 *            object here; ServiceNow wants the id of a row.
 *   REL      not on the row at all. A separate cmdb_rel_ci record with a
 *            type, which is how ServiceNow says "contains" and "connects to".
 *            NetBox expresses both of these as ordinary fields, so this is
 *            the case that a two-column mapping sheet cannot express and the
 *            reason this file carries `how` at all.
 *   NONE     we hold it, the CMDB has nowhere to put it. Recorded rather than
 *            omitted, so the gap is visible instead of looking like an
 *            oversight.
 *
 * Five of our sixteen object types (manufacturer, device type, device role,
 * site, location) are separate objects in NetBox and plain columns here. They
 * have no table of their own below; they appear as columns on the CI that
 * references them, which is the honest shape.
 */
const { EXPORT_ORDER } = require('./model');

/** How a fact is carried on the CMDB side. Printed in the mapping sheet. */
const HOW = Object.freeze({
  FIELD: 'field',
  CUSTOM: 'u_column',
  REF: 'reference',
  REL: 'relationship',
  NONE: 'not_supported',
});

/**
 * Relationship type names, as ServiceNow seeds them. The writer resolves each
 * to a sys_id once per run; the names are what a human recognises in the UI.
 */
const REL = Object.freeze({
  CONTAINS: 'Contains::Contained by',
  CONNECTS: 'Connects to::Connected by',
});

/**
 * Our stable id goes in correlation_id on every CI.
 *
 * correlation_id is ServiceNow's own field for "this row came from another
 * system". Keying on it is what makes a re-scan update the same CI instead of
 * making a second one, and it is the CMDB counterpart of the racktrack_uid
 * custom field we set in NetBox.
 */
const UID_FIELD = 'correlation_id';

/** The u_ column that ties every CI from one scan back to that scan. */
const SCAN_FIELD = 'u_racktrack_scan_id';

const s = (v) => (v === null || v === undefined ? '' : String(v).trim());

/**
 * Which CI class a device becomes.
 *
 * ServiceNow splits network hardware into sub-classes rather than keeping one
 * device table, so the role we already carry decides the table. Anything we
 * cannot place lands on cmdb_ci_netgear, which is the generic network CI and
 * is better than guessing at a more specific class that may not exist in the
 * customer's instance.
 */
const DEVICE_CLASSES = Object.freeze({
  switch: 'cmdb_ci_ip_switch',
  router: 'cmdb_ci_ip_router',
  firewall: 'cmdb_ci_ip_firewall',
  server: 'cmdb_ci_server',
  'patch-panel': 'cmdb_ci_netgear',
  'patch panel': 'cmdb_ci_netgear',
  pdu: 'cmdb_ci_pdu',
});

function deviceTable(roleName) {
  const key = s(roleName).toLowerCase();
  return DEVICE_CLASSES[key] || 'cmdb_ci_netgear';
}

/** A column declaration. `value` overrides the plain property read. */
const col = (cmdb, from, how = HOW.FIELD, extra = {}) => ({ cmdb, from, how, ...extra });

/**
 * ServiceNow's operational_status is a number, not our word. 1 is operational
 * and 6 is retired; anything we are unsure about stays operational rather than
 * silently retiring a customer's CI.
 */
const operationalStatus = (status) => (s(status) === 'offline' ? 6 : 1);

// ── The table ───────────────────────────────────────────────────────────────

const SPECS = [
  {
    field: 'racks',
    table: 'cmdb_ci_rack',
    label: 'Rack',
    columns: [
      col('name', 'name'),
      // ServiceNow's native height field on cmdb_ci_rack. Note the demo
      // scripts under servicenow/ read u_height, which is a custom column on
      // that tenant; rack_units is the base field and the one to write.
      col('rack_units', 'uHeight'),
      col('short_description', 'description'),
      col('comments', 'comments'),
      col('location', 'locationUid', HOW.FIELD, {
        note: 'site and location collapse into one text field here',
        value: (o, ctx) => ctx.placeName(o.locationUid, o.siteUid),
      }),
      col('operational_status', 'status', HOW.FIELD, { value: () => operationalStatus('active') }),
      col(SCAN_FIELD, 'rackUid', HOW.CUSTOM, { value: (o, ctx) => ctx.scanId }),
      col(UID_FIELD, 'uid', HOW.FIELD),
      col(null, null, HOW.REL, { rel: REL.CONTAINS, note: 'rack contains device' }),
      col(null, 'descUnits', HOW.NONE, { note: 'no equivalent; racks are numbered upward here' }),
      col(null, 'evidence', HOW.NONE, { note: 'see the provenance line in comments' }),
    ],
  },

  {
    field: 'devices',
    table: null, // decided per device by role, see deviceTable()
    tableFor: (o, ctx) => deviceTable(ctx.roleName(o.roleUid)),
    label: 'Device',
    columns: [
      col('name', 'name'),
      col('serial_number', 'serial'),
      col('asset_tag', 'assetTag'),
      col('model_id', 'deviceTypeUid', HOW.REF, {
        note: 'the device type, by model name',
        value: (o, ctx) => ctx.modelName(o.deviceTypeUid),
      }),
      col('manufacturer', 'deviceTypeUid', HOW.REF, {
        note: 'reached through the device type, as in NetBox',
        value: (o, ctx) => ctx.manufacturerName(o.deviceTypeUid),
      }),
      col('model_number', 'deviceTypeUid', HOW.REF, {
        value: (o, ctx) => ctx.partNumber(o.deviceTypeUid),
      }),
      col('ip_address', 'primaryIp', HOW.FIELD, {
        value: (o) => s(o.primaryIp) || s(o.customFields && o.customFields.managementIp),
      }),
      col('mac_address', 'mac'),
      col('os_version', 'customFields', HOW.FIELD, {
        note: 'firmware; NetBox has no native field and uses a custom one',
        value: (o) => s(o.customFields && (o.customFields.firmware || o.customFields.osVersion)),
      }),
      col('short_description', 'description', HOW.FIELD, {
        value: (o, ctx) => s(o.description) || ctx.describe(o.deviceTypeUid),
      }),
      col('operational_status', 'status', HOW.FIELD, { value: (o) => operationalStatus(o.status) }),
      col('u_position', 'position', HOW.CUSTOM, { note: 'which U it sits at' }),
      col('comments', 'provenance', HOW.FIELD, {
        note: 'the provenance line: what proved each value',
        value: (o, ctx) => ctx.provenanceLine(o),
      }),
      col(SCAN_FIELD, null, HOW.CUSTOM, { value: (o, ctx) => ctx.scanId }),
      col(UID_FIELD, 'uid', HOW.FIELD),
      col(null, 'rackUid', HOW.REL, { rel: REL.CONTAINS, note: 'parent rack contains this device' }),
      col(null, 'face', HOW.NONE, { note: 'front or rear of the rack; no CMDB field' }),
      col(null, 'evidence', HOW.NONE, { note: 'folded into the comments provenance line' }),
    ],
  },

  {
    field: 'interfaces',
    table: 'cmdb_ci_network_adapter',
    label: 'Interface',
    columns: [
      col('name', 'name', HOW.FIELD, { value: (o, ctx) => ctx.portName(o) }),
      col('alias', 'name'),
      col('mac_address', 'mac'),
      col('speed', 'type', HOW.FIELD, { note: 'derived from the port type', value: (o, ctx) => ctx.speedOf(o) }),
      col('short_description', 'description'),
      col('operational_status', 'enabled', HOW.FIELD, { value: (o) => (o.enabled === false ? 6 : 1) }),
      col('cmdb_ci', 'deviceUid', HOW.REF, { note: 'the CI this adapter belongs to' }),
      col(UID_FIELD, 'uid', HOW.FIELD),
      col(null, 'deviceUid', HOW.REL, { rel: REL.CONTAINS, note: 'device contains port' }),
      col(null, 'label', HOW.NONE, { note: 'the physical label; no CMDB field' }),
    ],
  },

  {
    field: 'rearPorts',
    table: 'cmdb_ci_port',
    label: 'Rear port',
    columns: [
      col('name', 'name', HOW.FIELD, { value: (o, ctx) => ctx.portName(o) }),
      col('model_number', 'type', HOW.FIELD, { note: 'the jack type, e.g. 8p8c' }),
      col('short_description', 'name', HOW.FIELD, { value: (o, ctx) => `rear port ${o.name} of ${ctx.deviceName(o.deviceUid)}` }),
      col('cmdb_ci', 'deviceUid', HOW.REF),
      col(UID_FIELD, 'uid', HOW.FIELD),
      col(null, 'deviceUid', HOW.REL, { rel: REL.CONTAINS, note: 'panel contains port' }),
      col(null, 'positions', HOW.NONE, { note: 'how many front positions map here; no CMDB field' }),
    ],
  },

  {
    field: 'frontPorts',
    table: 'cmdb_ci_port',
    label: 'Front port',
    columns: [
      col('name', 'name', HOW.FIELD, { value: (o, ctx) => ctx.portName(o) }),
      col('model_number', 'type'),
      col('short_description', 'name', HOW.FIELD, { value: (o, ctx) => `front port ${o.name} of ${ctx.deviceName(o.deviceUid)}` }),
      col('cmdb_ci', 'deviceUid', HOW.REF),
      col(UID_FIELD, 'uid', HOW.FIELD),
      col(null, 'deviceUid', HOW.REL, { rel: REL.CONTAINS, note: 'panel contains port' }),
      // The front-to-rear pairing is the whole point of a patch panel, and the
      // CMDB has no field for it. A relationship is the only honest home: it
      // keeps the trace walkable in ServiceNow instead of stopping at the panel.
      col(null, 'rearPortUid', HOW.REL, { rel: REL.CONNECTS, note: 'front port to its rear port' }),
      col(null, 'rearPortPosition', HOW.NONE, { note: 'which position on the rear port' }),
    ],
  },

  {
    field: 'powerPorts',
    table: 'cmdb_ci_network_adapter',
    label: 'Power port',
    columns: [
      col('name', 'name', HOW.FIELD, { value: (o, ctx) => ctx.portName(o) }),
      col('short_description', 'name', HOW.FIELD, { value: (o, ctx) => `power inlet ${o.name} of ${ctx.deviceName(o.deviceUid)}` }),
      col('cmdb_ci', 'deviceUid', HOW.REF, { note: 'the device that draws the power' }),
      col(UID_FIELD, 'uid', HOW.FIELD),
      col(null, 'deviceUid', HOW.REL, { rel: REL.CONTAINS, note: 'device contains its power inlet' }),
    ],
  },

  {
    field: 'powerOutlets',
    table: 'cmdb_ci_pdu',
    label: 'Power outlet',
    columns: [
      col('name', 'name', HOW.FIELD, { value: (o, ctx) => ctx.portName(o) }),
      col('short_description', 'name', HOW.FIELD, { value: (o, ctx) => `outlet ${o.name} of ${ctx.deviceName(o.deviceUid)}` }),
      col('cmdb_ci', 'deviceUid', HOW.REF, { note: 'the PDU this outlet is on' }),
      col(UID_FIELD, 'uid', HOW.FIELD),
      col(null, 'deviceUid', HOW.REL, { rel: REL.CONTAINS, note: 'PDU contains outlet' }),
    ],
  },

  {
    field: 'prefixes',
    table: 'cmdb_ci_ip_network_subnet',
    label: 'Prefix',
    columns: [
      col('name', 'prefix'),
      col('cidr', 'prefix'),
      col('short_description', 'description'),
      col(UID_FIELD, 'uid', HOW.FIELD),
      col(null, 'vlanUid', HOW.NONE, { note: 'no native VLAN table to point at' }),
    ],
  },

  {
    field: 'ipAddresses',
    table: 'cmdb_ci_allocated_ip_address',
    label: 'IP address',
    columns: [
      col('name', 'address'),
      col('ip_address', 'address', HOW.FIELD, { value: (o) => s(o.address).split('/')[0] }),
      col('ip_version', 'address', HOW.FIELD, { value: (o) => (s(o.address).includes(':') ? 6 : 4) }),
      col(UID_FIELD, 'uid', HOW.FIELD),
      col(null, 'interfaceUid', HOW.REL, { rel: REL.CONTAINS, note: 'the port holding this address' }),
    ],
  },

  {
    field: 'cables',
    table: null,
    label: 'Cable',
    // A cable is not a CI. ServiceNow has no cable class in base ITSM, and it
    // does not need one: a cable is a relationship between two ports, which is
    // exactly what cmdb_rel_ci records. The paid telecoms plugin adds a cable
    // CI with extra attributes, and nothing here depends on it.
    relationshipOnly: true,
    columns: [
      col(null, 'a', HOW.REL, { rel: REL.CONNECTS, note: 'A end, the relationship parent' }),
      col(null, 'b', HOW.REL, { rel: REL.CONNECTS, note: 'B end, the relationship child' }),
      col(null, 'type', HOW.NONE, { note: 'cat6 and friends; TNI plugin only' }),
      col(null, 'color', HOW.NONE),
      col(null, 'label', HOW.NONE),
      col(null, 'length', HOW.NONE, { note: 'TNI plugin only' }),
      col(null, 'status', HOW.NONE, { note: 'connected or planned; no CMDB equivalent' }),
      col(null, 'evidence', HOW.NONE, { note: 'what proved the cable; no CMDB equivalent' }),
    ],
  },

  // Objects that are separate in NetBox and plain columns here. No CI of their
  // own; listed so the mapping sheet can say where each one actually lands.
  {
    field: 'manufacturers', table: null, label: 'Manufacturer', foldsInto: 'devices',
    columns: [col('manufacturer', 'name', HOW.FIELD, { note: 'a column on the device CI' }),
              col(null, 'slug', HOW.NONE)],
  },
  {
    field: 'deviceTypes', table: null, label: 'Device type', foldsInto: 'devices',
    columns: [col('model_id', 'model', HOW.FIELD), col('model_number', 'slug', HOW.FIELD),
              col(null, 'uHeight', HOW.NONE, { note: 'kept on the rack CI, not the device' }),
              col(null, 'isFullDepth', HOW.NONE)],
  },
  {
    field: 'deviceRoles', table: null, label: 'Device role', foldsInto: 'devices',
    columns: [col('sys_class_name', 'name', HOW.FIELD, { note: 'the role picks the CI class itself' }),
              col(null, 'slug', HOW.NONE)],
  },
  {
    field: 'sites', table: null, label: 'Site', foldsInto: 'racks',
    columns: [col('location', 'name', HOW.FIELD, { note: 'site and location share one field' }),
              col(null, 'slug', HOW.NONE)],
  },
  {
    field: 'locations', table: null, label: 'Location', foldsInto: 'racks',
    columns: [col('location', 'name', HOW.FIELD), col(null, 'siteUid', HOW.NONE)],
  },
  {
    field: 'vlans', table: null, label: 'VLAN',
    columns: [col(null, 'vid', HOW.NONE, { note: 'base ITSM CMDB has no VLAN table' }),
              col(null, 'name', HOW.NONE), col(null, 'siteUid', HOW.NONE)],
  },
];

const BY_FIELD = Object.fromEntries(SPECS.map((sp) => [sp.field, sp]));

/**
 * Walk EXPORT_ORDER, as the NetBox writer does.
 *
 * ServiceNow is not order-sensitive the way NetBox is, but a parent CI has to
 * exist before a relationship can name it, so the same order holds and the two
 * writers stay comparable.
 */
function orderedSpecs() {
  const missing = EXPORT_ORDER.filter((f) => !BY_FIELD[f]);
  if (missing.length) {
    throw new Error(`EXPORT_ORDER names types with no CMDB mapping: ${missing.join(', ')}`);
  }
  return EXPORT_ORDER.map((f) => BY_FIELD[f]);
}

/** Every CI table this mapping can write to. */
const tables = () => [
  ...new Set(SPECS.map((sp) => sp.table).filter(Boolean).concat(Object.values(DEVICE_CLASSES))),
].sort();

/**
 * Build one CI row from a domain object.
 *
 * Only FIELD, CUSTOM and REF columns become row keys. REL columns are handled
 * by the writer, and NONE columns exist to be printed in the sheet.
 */
function rowFor(spec, o, ctx) {
  const row = {};
  for (const c of spec.columns) {
    if (!c.cmdb) continue;
    if (c.how === HOW.REL || c.how === HOW.NONE) continue;
    const raw = c.value ? c.value(o, ctx) : o[c.from];
    if (raw === undefined || raw === null || raw === '') continue;
    row[c.cmdb] = typeof raw === 'number' ? raw : s(raw);
  }
  return row;
}

/** The relationships one domain object implies. */
function relsFor(spec, o) {
  const out = [];
  for (const c of spec.columns) {
    if (c.how !== HOW.REL) continue;
    if (c.rel === REL.CONNECTS && spec.field === 'cables') continue; // the writer pairs both ends
    const target = c.from ? o[c.from] : null;
    if (!target) continue;
    out.push({ type: c.rel, from: o.uid, to: target, note: c.note || '' });
  }
  return out;
}

module.exports = {
  HOW, REL, UID_FIELD, SCAN_FIELD, DEVICE_CLASSES,
  SPECS, BY_FIELD, orderedSpecs, tables,
  deviceTable, rowFor, relsFor, operationalStatus,
};
