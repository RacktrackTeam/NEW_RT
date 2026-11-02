/**
 * A small ServiceNow, for tests: the Table API (/api/now/table/<table>) over
 * the ground truths, in the shapes vendor/dark_mobile/servicenow/diff_cmdb.py
 * reads (rack found by name or u_racktrack_scan_id, its devices through
 * cmdb_rel_ci "rack Contains child", ports from cmdb_ci_network_adapter and,
 * for patch panels, cmdb_ci_port).
 *
 *   cmdb_ci_rack                  name, rack_units, location, u_racktrack_scan_id
 *   device classes                cmdb_ci_ip_switch (Switch), cmdb_ci_netgear (Patch Panel),
 *                                 cmdb_ci_server, cmdb_ci_firewall_network, cmdb_ci_router,
 *                                 cmdb_ci_pdu, cmdb_ci_storage_device, cmdb_ci_hardware (anything else)
 *                                 name, model_id, manufacturer, serial_number, asset_tag,
 *                                 u_position, u_height, short_description (role), location
 *   cmdb_ci                       all of them
 *   cmdb_rel_ci                   parent {value} contains child {value}
 *   cmdb_ci_network_adapter, cmdb_ci_port    ports (name, cmdb_ci, u_port_number, u_port_type)
 *
 * Query: sysparm_query (= != LIKE STARTSWITH, joined with ^), sysparm_limit,
 * sysparm_offset, sysparm_fields, sysparm_display_value=true (references as
 * names; otherwise { value, link }). Test helpers: PATCH /api/now/table/<t>/<sys_id>,
 * DELETE the same, POST /__mock/reset.
 *
 *   node mock/servicenow.js <port>
 */
'use strict';

const express = require('express');
const { load, hex32 } = require('./truths');

const CLASS_OF = {
  Switch: 'cmdb_ci_ip_switch', 'Patch Panel': 'cmdb_ci_netgear', Server: 'cmdb_ci_server', Firewall: 'cmdb_ci_firewall_network',
  Router: 'cmdb_ci_router', PDU: 'cmdb_ci_pdu', Storage: 'cmdb_ci_storage_device',
};
const DEVICE_CLASSES = [...new Set([...Object.values(CLASS_OF), 'cmdb_ci_hardware'])];
const PORT_TYPE = { interface: 'RJ45', front: 'RJ45', rear: 'RJ45' };

function build(truths) {
  const t = { cmdb_ci_rack: [], cmdb_rel_ci: [], cmdb_ci_network_adapter: [], cmdb_ci_port: [] };
  for (const c of DEVICE_CLASSES) t[c] = [];
  for (const tr of truths) {
    const rackId = hex32(`rack:${tr.file}`);
    t.cmdb_ci_rack.push({ sys_id: rackId, sys_class_name: 'cmdb_ci_rack', name: tr.rackLabel, rack_units: String(tr.gt.rack.height_ru), location: tr.siteName, u_racktrack_scan_id: '' });
    for (const d of tr.devices) {
      const cls = CLASS_OF[d.kind] || 'cmdb_ci_hardware';
      const sys = hex32(`dev:${tr.file}:${d.name}`);
      t[cls].push({
        sys_id: sys, sys_class_name: cls, name: d.name, model_id: d.model || '', manufacturer: d.manufacturer || '',
        serial_number: d.serial || '', asset_tag: d.asset_tag || '', u_position: d.position ? String(d.position) : '', u_height: String(d.height || 1),
        short_description: d.role || '', location: tr.siteName, rack_face: d.face || '',
      });
      t.cmdb_rel_ci.push({ sys_id: hex32(`rel:${sys}`), parent: { value: rackId }, child: { value: sys }, type: 'Contains::Contained by' });
      d.ports.forEach((p) => {
        const row = { sys_id: hex32(`port:${sys}:${p.name}`), name: p.name, cmdb_ci: { value: sys }, u_port_number: String(p.number), u_port_type: PORT_TYPE[p.group], u_port_group: p.group, device_name: d.name };
        (cls === 'cmdb_ci_netgear' ? t.cmdb_ci_port : t.cmdb_ci_network_adapter).push(row);
      });
    }
  }
  return t;
}

const REFS = { model_id: 'model_id', manufacturer: 'manufacturer', location: 'location', cmdb_ci: 'cmdb_ci', parent: 'parent', child: 'child' };

function matcher(query) {
  const conds = String(query || '').split('^').filter((c) => c && !/^ORDERBY/.test(c) && !/^OR/.test(c)).map((c) => {
    const m = /^([A-Za-z_.]+?)(!=|=|LIKE|STARTSWITH|ENDSWITH)(.*)$/.exec(c);
    return m ? { f: m[1], op: m[2], v: m[3] } : null;
  }).filter(Boolean);
  return (row) => conds.every(({ f, op, v }) => {
    const raw = row[f.split('.')[0]];
    const val = String((raw && typeof raw === 'object' ? raw.value : raw) ?? '');
    const lower = val.toLowerCase(); const w = v.toLowerCase();
    if (op === '=') return val === v;
    if (op === '!=') return val !== v;
    if (op === 'LIKE') return lower.includes(w);
    if (op === 'STARTSWITH') return lower.startsWith(w);
    return lower.endsWith(w);
  });
}

function createApp(truths = load()) {
  const app = express();
  app.use(express.json());
  let tables = build(truths);
  app.locals.tables = () => tables;
  const all = () => DEVICE_CLASSES.flatMap((c) => tables[c]).concat(tables.cmdb_ci_rack);

  const shape = (rows, q, tableName) => {
    const display = String(q.sysparm_display_value) === 'true';
    const fields = q.sysparm_fields ? String(q.sysparm_fields).split(',') : null;
    return rows.map((r) => {
      const o = {};
      for (const [k, v] of Object.entries(r)) {
        if (fields && !fields.includes(k)) continue;
        if (k in REFS && typeof v === 'string' && v !== '') o[k] = display ? v : { value: v, link: `/api/now/table/cmdb_ci/${hex32(v)}` };
        else if (k in REFS && typeof v === 'string') o[k] = '';
        else if (v && typeof v === 'object' && display) o[k] = (tables[tableName] && refName(v.value)) || v.value;
        else o[k] = v;
      }
      return o;
    });
  };
  const refName = (sys) => { const hit = all().find((x) => x.sys_id === sys); return hit ? hit.name : null; };

  app.get('/api/now/table/:table', (req, res) => {
    const name = req.params.table;
    const rows = name === 'cmdb_ci' ? all() : tables[name];
    if (!rows) return res.status(404).json({ error: { message: 'Invalid table', detail: name }, status: 'failure' });
    const hits = rows.filter(matcher(req.query.sysparm_query));
    const limit = Number(req.query.sysparm_limit) || 10000; const offset = Number(req.query.sysparm_offset) || 0;
    const page = hits.slice(offset, offset + limit);
    res.set('X-Total-Count', String(hits.length)).json({ result: shape(page, req.query, name) });
  });
  app.get('/api/now/table/:table/:sys_id', (req, res) => {
    const rows = req.params.table === 'cmdb_ci' ? all() : tables[req.params.table];
    const hit = (rows || []).find((r) => r.sys_id === req.params.sys_id);
    if (!hit) return res.status(404).json({ error: { message: 'No Record found' }, status: 'failure' });
    res.json({ result: shape([hit], req.query, req.params.table)[0] });
  });
  app.patch('/api/now/table/:table/:sys_id', (req, res) => {
    const hit = (tables[req.params.table] || []).find((r) => r.sys_id === req.params.sys_id);
    if (!hit) return res.status(404).json({ error: { message: 'No Record found' }, status: 'failure' });
    Object.assign(hit, req.body);
    res.json({ result: hit });
  });
  app.delete('/api/now/table/:table/:sys_id', (req, res) => {
    const list = tables[req.params.table];
    if (!list) return res.status(404).end();
    const i = list.findIndex((r) => r.sys_id === req.params.sys_id);
    if (i < 0) return res.status(404).end();
    list.splice(i, 1);
    tables.cmdb_rel_ci = tables.cmdb_rel_ci.filter((r) => r.child.value !== req.params.sys_id);
    res.status(204).end();
  });
  app.post('/__mock/reset', (_req, res) => { tables = build(truths); res.json({ ok: true }); });
  return app;
}

function start(port, truths) {
  return new Promise((resolve) => { const server = createApp(truths).listen(port, '127.0.0.1', () => resolve(server)); });
}

if (require.main === module) {
  const port = Number(process.argv[2]);
  if (!port) { console.error('usage: node mock/servicenow.js <port>'); process.exit(2); }
  start(port).then(() => console.log(`mock ServiceNow on http://127.0.0.1:${port}`));
}

module.exports = { createApp, start, build, CLASS_OF, DEVICE_CLASSES };
