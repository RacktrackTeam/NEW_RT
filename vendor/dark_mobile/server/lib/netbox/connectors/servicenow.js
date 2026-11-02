/**
 * ServiceNow CMDB connector.
 *
 * ServiceNow keeps configuration items in CMDB tables (cmdb_ci_ip_switch and
 * friends). This walks a scan and writes a CI per object through the Table
 * API, matching on a correlation id so a re-scan updates the same CI rather
 * than making a second one.
 *
 * What goes where is not decided here. Every "what ServiceNow calls this"
 * lives in cmdb.js, the twin of mapping.js, so this file stays a plain loop
 * and the translation stays in one auditable table. This connector used to
 * carry nine hard-coded device fields and skip racks, panels, ports and
 * cables entirely; the mapping table is what let that scope close.
 *
 * The planning half is a pure function so the whole shape of a write can be
 * tested without an instance. The HTTP half uses the same fetch as every
 * other connector.
 */
const cmdb = require('../cmdb');

// ── Planning ────────────────────────────────────────────────────────────────

/**
 * Everything a snapshot needs to translate itself: name lookups across the
 * objects that are separate in NetBox and plain columns in ServiceNow.
 */
function contextFor(snapshot) {
  const byUid = (list) => new Map((list || []).map((o) => [o.uid, o]));
  const types = byUid(snapshot.deviceTypes);
  const mfrs = byUid(snapshot.manufacturers);
  const roles = byUid(snapshot.deviceRoles);
  const sites = byUid(snapshot.sites);
  const places = byUid(snapshot.locations);
  const devices = byUid(snapshot.devices);

  const typeOf = (uid) => types.get(uid) || null;

  return {
    scanId: snapshot.rackUid || '',

    roleName: (uid) => (roles.get(uid) || {}).name || '',
    modelName: (uid) => (typeOf(uid) || {}).model || '',
    partNumber: (uid) => (typeOf(uid) || {}).slug || '',
    manufacturerName: (uid) => {
      const t = typeOf(uid);
      return t ? (mfrs.get(t.manufacturerUid) || {}).name || '' : '';
    },
    describe: (uid) => {
      const t = typeOf(uid);
      if (!t) return '';
      const mfr = (mfrs.get(t.manufacturerUid) || {}).name || '';
      return [mfr, t.model].filter(Boolean).join(' ');
    },

    /** Site and location collapse into one text field on the CI. */
    placeName: (locationUid, siteUid) => {
      const place = places.get(locationUid);
      const site = sites.get(place ? place.siteUid : siteUid);
      return [site && site.name, place && place.name].filter(Boolean).join(' / ');
    },

    deviceName: (uid) => (devices.get(uid) || {}).name || '',
    /** Ports are named per device, so a bare "1" is not ambiguous estate-wide. */
    portName: (o) => {
      const dev = (devices.get(o.deviceUid) || {}).name || '';
      return dev ? `${dev}:${o.name}` : o.name;
    },

    /** 1000base-t and friends carry their speed in the name. */
    speedOf: (o) => {
      const m = /(\d+)\s*g?base/i.exec(String(o.type || ''));
      if (!m) return '';
      const n = Number(m[1]);
      return String(/g base|gbase/i.test(o.type) && n < 100 ? n * 1000 : n);
    },

    /**
     * What proved each value, in a field a person browsing the CI can read.
     * comments is a standard CMDB field, so this needs no customisation of
     * the customer's instance.
     */
    provenanceLine: (o) => {
      const bits = [`evidence=${o.evidence || 'unknown'}`];
      for (const [k, v] of Object.entries(o.provenance || {})) {
        if (v !== undefined && v !== null && v !== '') bits.push(`${k}=${String(v).slice(0, 120)}`);
      }
      return bits.join('; ');
    },
  };
}

/**
 * The whole write, as data: every CI row and every relationship, in order.
 *
 * Nothing here talks to an instance, so a test can assert the exact shape of
 * what a scan would do to a customer's CMDB.
 */
function plan(snapshot) {
  const ctx = contextFor(snapshot);
  const rows = [];
  const rels = [];

  for (const spec of cmdb.orderedSpecs()) {
    const items = snapshot[spec.field] || [];
    if (!items.length) continue;

    // Cables are relationships, never CI rows. Both ends, or it is not a cable.
    if (spec.relationshipOnly) {
      for (const cable of items) {
        if (!cable.a || !cable.b) continue;
        rels.push({ type: cmdb.REL.CONNECTS, from: cable.a.uid, to: cable.b.uid,
                    note: 'cable', uid: cable.uid });
      }
      continue;
    }

    // Objects that are columns on another CI rather than a CI of their own.
    if (!spec.table && !spec.tableFor) continue;

    for (const o of items) {
      const table = spec.tableFor ? spec.tableFor(o, ctx) : spec.table;
      rows.push({ table, uid: o.uid, label: spec.label, row: cmdb.rowFor(spec, o, ctx) });
      for (const r of cmdb.relsFor(spec, o)) rels.push({ ...r, uid: o.uid });
    }
  }

  return { rows, rels, ctx };
}

/**
 * The device rows alone.
 *
 * Kept because the rest of the app and its tests have always asked this
 * question, and because devices are still the rows a customer looks at first.
 */
function rowsFrom(snapshot) {
  return plan(snapshot).rows
    .filter((r) => r.label === 'Device')
    .map((r) => r.row);
}

/** One device to one CI row. Kept for callers that shape a single device. */
function toCiRow(device, typeByUid, mfrByUid) {
  const snap = {
    devices: [device],
    deviceTypes: [...(typeByUid ? typeByUid.values() : [])],
    manufacturers: [...(mfrByUid ? mfrByUid.entries() : [])].map(([uid, name]) =>
      (typeof name === 'string' ? { uid, name } : name)),
  };
  return rowsFrom(snap)[0] || {};
}

// ── HTTP ────────────────────────────────────────────────────────────────────

function auth(cfg) {
  return {
    'Content-Type': 'application/json',
    Accept: 'application/json',
    Authorization: 'Basic ' + Buffer.from(`${cfg.username || ''}:${cfg.password || ''}`).toString('base64'),
  };
}

async function req(url, method, headers, body, timeoutMs = 15000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined, signal: ctrl.signal });
    const text = await res.text();
    let parsed; try { parsed = text ? JSON.parse(text) : {}; } catch { parsed = text.slice(0, 300); }
    return { status: res.status, ok: res.ok, body: parsed };
  } finally {
    clearTimeout(timer);
  }
}

module.exports = {
  type: 'servicenow',
  label: 'ServiceNow CMDB',
  fields: [
    { key: 'instanceUrl', label: 'Instance URL', required: true, placeholder: 'https://acme.service-now.com' },
    { key: 'username', label: 'Username', required: true },
    { key: 'password', label: 'Password', required: true, secret: true },
    { key: 'table', label: 'Fallback CI table', default: 'cmdb_ci_ip_switch',
      help: 'Used only for a device whose role does not name a CI class of its own' },
  ],

  validate(cfg) {
    if (!cfg.instanceUrl || !/^https?:\/\//i.test(cfg.instanceUrl)) {
      return { ok: false, error: 'A full https instance URL is required.' };
    }
    if (!cfg.username || !cfg.password) return { ok: false, error: 'A username and password are required.' };
    return { ok: true };
  },

  async test(cfg) {
    const table = cfg.table || 'cmdb_ci_ip_switch';
    const url = `${cfg.instanceUrl.replace(/\/+$/, '')}/api/now/table/${table}?sysparm_limit=1`;
    try {
      const res = await req(url, 'GET', auth(cfg));
      if (res.ok) return { ok: true, message: `Reachable, and the ${table} table answered.` };
      return { ok: false, message: `ServiceNow replied ${res.status}. Check the URL, login, and table name.` };
    } catch (e) {
      return { ok: false, message: `Could not reach it: ${e.message || e}` };
    }
  },

  async export(snapshot, cfg, { apply }) {
    const { rows, rels } = plan(snapshot);
    const changes = rows.map((r) => ({ type: r.label, name: r.row.name || r.uid, action: 'create', table: r.table }));

    if (!apply) {
      return { ok: true, dryRun: true, type: 'servicenow', target: cfg.instanceUrl,
               counts: { create: rows.length, relationships: rels.length },
               tables: [...new Set(rows.map((r) => r.table))].sort(),
               changes, warnings: [] };
    }

    const base = `${cfg.instanceUrl.replace(/\/+$/, '')}/api/now/table`;
    const headers = auth(cfg);
    const warnings = [];
    const sysIdByUid = new Map();
    let created = 0; let updated = 0; let linked = 0;

    for (const { table, uid, row } of rows) {
      // Idempotent: find an existing CI by correlation_id, then PATCH or POST.
      let existing = null;
      try {
        const q = await req(`${base}/${table}?sysparm_query=${cmdb.UID_FIELD}=${encodeURIComponent(uid)}&sysparm_limit=1`, 'GET', headers);
        existing = q.ok && q.body && q.body.result && q.body.result[0];
      } catch (e) { warnings.push(`lookup failed for ${row.name || uid}: ${e.message}`); break; }

      try {
        if (existing && existing.sys_id) {
          const r = await req(`${base}/${table}/${existing.sys_id}`, 'PATCH', headers, row);
          if (r.ok) { updated += 1; sysIdByUid.set(uid, existing.sys_id); }
          else warnings.push(`${row.name || uid}: update ${r.status}`);
        } else {
          const r = await req(`${base}/${table}`, 'POST', headers, row);
          if (r.ok) {
            created += 1;
            const made = r.body && r.body.result;
            if (made && made.sys_id) sysIdByUid.set(uid, made.sys_id);
          } else warnings.push(`${row.name || uid}: create ${r.status}`);
        }
      } catch (e) { warnings.push(`${row.name || uid}: ${e.message}`); break; }
    }

    // Relationships last: both CIs have to exist before one can name the other.
    const relTypeIds = new Map();
    for (const rel of rels) {
      const parent = sysIdByUid.get(rel.from);
      const child = sysIdByUid.get(rel.to);
      if (!parent || !child) continue; // an end we did not write; not an error

      let typeId = relTypeIds.get(rel.type);
      if (!typeId) {
        try {
          const q = await req(`${base}/cmdb_rel_type?sysparm_query=name=${encodeURIComponent(rel.type)}&sysparm_limit=1`, 'GET', headers);
          typeId = q.ok && q.body && q.body.result && q.body.result[0] && q.body.result[0].sys_id;
          if (typeId) relTypeIds.set(rel.type, typeId);
        } catch { /* fall through to the warning below */ }
      }
      if (!typeId) { warnings.push(`relationship type not found: ${rel.type}`); continue; }

      try {
        const q = await req(`${base}/cmdb_rel_ci?sysparm_query=parent=${parent}^child=${child}^type=${typeId}&sysparm_limit=1`, 'GET', headers);
        if (q.ok && q.body && q.body.result && q.body.result[0]) continue; // already linked
        const r = await req(`${base}/cmdb_rel_ci`, 'POST', headers, { parent, child, type: typeId });
        if (r.ok) linked += 1; else warnings.push(`link ${rel.from} to ${rel.to}: ${r.status}`);
      } catch (e) { warnings.push(`link ${rel.from} to ${rel.to}: ${e.message}`); }
    }

    return { ok: warnings.length === 0, dryRun: false, type: 'servicenow', target: cfg.instanceUrl,
             counts: { create: created, update: updated, relationships: linked },
             tables: [...new Set(rows.map((r) => r.table))].sort(),
             changes, warnings };
  },

  // exported for tests
  _plan: plan,
  _rowsFrom: rowsFrom,
  _toCiRow: toCiRow,
  _contextFor: contextFor,
};
