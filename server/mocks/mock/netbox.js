/**
 * A small NetBox, for tests: serves the ground truths on the REST paths the
 * importer reads (the envelope, filters and paging follow NetBox and the
 * mock in vendor/dark_mobile/server/mock_routes.js).
 *
 *   GET /api/status/
 *   GET /api/dcim/sites/            filters: id, name, slug
 *   GET /api/dcim/racks/            filters: id, site_id, name, facility_id
 *   GET /api/dcim/device-types/     filters: id, model
 *   GET /api/dcim/devices/          filters: id, site_id, rack_id, name, serial, asset_tag
 *   GET /api/dcim/interfaces/       filters: device_id, rack_id, site_id
 *   GET /api/dcim/front-ports/ and /rear-ports/   the same filters
 *   GET /api/dcim/cables/           (none unless a test adds one to state.cables)
 *
 * For tests that plant a difference: PATCH and DELETE on /api/dcim/devices/:id/
 * and POST /__mock/reset. They change only this process's memory.
 *
 *   node mock/netbox.js <port>
 */
'use strict';

const express = require('express');
const { load } = require('./truths');

const TYPE_OF = { interface: '1000base-t', front: '8p8c', rear: '8p8c' };

function build(truths) {
  const state = { sites: [], racks: [], deviceTypes: [], roles: [], manufacturers: [], devices: [], ports: [], cables: [] };
  let id = { site: 0, rack: 0, type: 0, role: 0, man: 0, dev: 0, port: 0 };
  const manByName = new Map(); const typeByKey = new Map(); const roleByName = new Map();
  const manufacturer = (name) => {
    if (!manByName.has(name)) { const m = { id: ++id.man, name, slug: name.toLowerCase().replace(/[^a-z0-9]+/g, '-') }; manByName.set(name, m); state.manufacturers.push(m); }
    return manByName.get(name);
  };
  const role = (name) => {
    if (!roleByName.has(name)) { const r = { id: ++id.role, name, slug: name.toLowerCase().replace(/[^a-z0-9]+/g, '-') }; roleByName.set(name, r); state.roles.push(r); }
    return roleByName.get(name);
  };
  const dtype = (d) => {
    const man = manufacturer(d.manufacturer || 'Unknown');
    const model = d.model || 'Unknown model'; // NetBox needs a manufacturer and a model on every device type
    const k = `${man.name}|${model}|${d.height}`;
    if (!typeByKey.has(k)) {
      const t = { id: ++id.type, manufacturer: { id: man.id, name: man.name, slug: man.slug }, model, slug: model.toLowerCase().replace(/[^a-z0-9]+/g, '-'), u_height: d.height || 1 };
      typeByKey.set(k, t); state.deviceTypes.push(t);
    }
    return typeByKey.get(k);
  };
  for (const t of truths) {
    const site = { id: ++id.site, name: t.siteName, slug: t.siteName.toLowerCase().replace(/[^a-z0-9]+/g, '-'), status: { value: 'active', label: 'Active' }, region: t.gt.site.region ? { name: t.gt.site.region } : null, latitude: t.gt.site.lat ?? null, longitude: t.gt.site.lng ?? null };
    state.sites.push(site);
    const rack = { id: ++id.rack, name: t.rackLabel, facility_id: t.gt.rack.facility_id || null, site: { id: site.id, name: site.name }, u_height: t.gt.rack.height_ru, status: { value: 'active', label: 'Active' } };
    state.racks.push(rack);
    for (const d of t.devices) {
      const ty = dtype(d); const r = role(d.role || 'Unknown');
      const dev = {
        id: ++id.dev, name: d.name, device_type: ty, role: { id: r.id, name: r.name, slug: r.slug }, site: { id: site.id, name: site.name },
        rack: d.position ? { id: rack.id, name: rack.name } : null, position: d.position ? Number(d.position) : null,
        face: d.position && d.face ? { value: d.face.toLowerCase(), label: d.face } : null,
        serial: d.serial || '', asset_tag: d.asset_tag || null, status: { value: 'active', label: 'Active' }, custom_fields: {},
      };
      // A device with no position still belongs to the site; keep its rack so a rack filter finds it.
      if (!d.position) dev.rack = { id: rack.id, name: rack.name };
      state.devices.push(dev);
      for (const p of d.ports) {
        state.ports.push({ kind: p.group, id: ++id.port, name: p.name, label: '', type: { value: TYPE_OF[p.group], label: TYPE_OF[p.group] }, speed: null, device: { id: dev.id, name: dev.name }, device_row: dev });
      }
    }
  }
  return state;
}

function createApp(truths = load()) {
  const initial = truths;
  const app = express();
  app.use(express.json());
  let state = build(initial);
  app.locals.state = () => state;

  const page = (req, rows) => {
    const limit = Math.max(1, Math.min(Number(req.query.limit) || 50, 1000));
    const offset = Math.max(0, Number(req.query.offset) || 0);
    const slice = rows.slice(offset, offset + limit);
    const base = `${req.protocol}://${req.get('host')}${req.path}`;
    const q = (o) => { const p = new URLSearchParams({ ...Object.fromEntries(Object.entries(req.query).filter(([k]) => !['limit', 'offset'].includes(k))), limit, offset: o }); return `${base}?${p}`; };
    return { count: rows.length, next: offset + limit < rows.length ? q(offset + limit) : null, previous: offset > 0 ? q(Math.max(0, offset - limit)) : null, results: slice };
  };
  const many = (v) => (v === undefined ? null : [].concat(v).map(String));
  const eq = (field, get) => (req) => { const w = many(req.query[field]); return w ? (r) => w.includes(String(get(r) ?? '')) : null; };
  const filtered = (list, req, filters) => { const fs = filters.map((f) => f(req)).filter(Boolean); return list.filter((r) => fs.every((f) => f(r))); };

  app.get('/api/status/', (_req, res) => res.json({ 'netbox-version': '4.0.0-mock', 'django-version': 'mock', plugins: {} }));
  app.get('/api/dcim/sites/', (req, res) => res.json(page(req, filtered(state.sites, req, [eq('id', (r) => r.id), eq('name', (r) => r.name), eq('slug', (r) => r.slug)]))));
  app.get('/api/dcim/racks/', (req, res) => res.json(page(req, filtered(state.racks, req, [eq('id', (r) => r.id), eq('site_id', (r) => r.site.id), eq('name', (r) => r.name), eq('facility_id', (r) => r.facility_id)]))));
  app.get('/api/dcim/device-types/', (req, res) => res.json(page(req, filtered(state.deviceTypes, req, [eq('id', (r) => r.id), eq('model', (r) => r.model)]))));
  app.get('/api/dcim/devices/', (req, res) => res.json(page(req, filtered(state.devices, req, [eq('id', (r) => r.id), eq('site_id', (r) => r.site.id), eq('rack_id', (r) => r.rack?.id), eq('name', (r) => r.name), eq('serial', (r) => r.serial), eq('asset_tag', (r) => r.asset_tag)]))));
  const portRoute = (path, kind) => app.get(path, (req, res) => {
    const rows = state.ports.filter((p) => p.kind === kind).map(({ device_row, kind: _k, ...p }) => ({ ...p, _dev: device_row }));
    const out = filtered(rows, req, [eq('device_id', (r) => r.device.id), eq('rack_id', (r) => r._dev.rack?.id), eq('site_id', (r) => r._dev.site.id)]);
    res.json(page(req, out.map(({ _dev, ...p }) => p)));
  });
  portRoute('/api/dcim/interfaces/', 'interface');
  portRoute('/api/dcim/front-ports/', 'front');
  portRoute('/api/dcim/rear-ports/', 'rear');
  app.get('/api/dcim/cables/', (req, res) => res.json(page(req, state.cables)));

  app.patch('/api/dcim/devices/:id/', (req, res) => {
    const d = state.devices.find((x) => x.id === Number(req.params.id));
    if (!d) return res.status(404).json({ detail: 'Not found.' });
    for (const k of ['name', 'serial', 'asset_tag', 'position']) if (k in req.body) d[k] = req.body[k];
    if (req.body.model) d.device_type = { ...d.device_type, model: req.body.model };
    if (req.body.manufacturer) d.device_type = { ...d.device_type, manufacturer: { ...d.device_type.manufacturer, name: req.body.manufacturer } };
    res.json(d);
  });
  app.delete('/api/dcim/devices/:id/', (req, res) => {
    const n = state.devices.length;
    state.devices = state.devices.filter((x) => x.id !== Number(req.params.id));
    state.ports = state.ports.filter((p) => p.device.id !== Number(req.params.id));
    res.status(state.devices.length < n ? 204 : 404).end();
  });
  app.post('/__mock/reset', (_req, res) => { state = build(initial); res.json({ ok: true }); });
  return app;
}

function start(port, truths) {
  return new Promise((resolve) => { const server = createApp(truths).listen(port, '127.0.0.1', () => resolve(server)); });
}

if (require.main === module) {
  const port = Number(process.argv[2]);
  if (!port) { console.error('usage: node mock/netbox.js <port>'); process.exit(2); }
  start(port).then(() => console.log(`mock NetBox on http://127.0.0.1:${port}`));
}

module.exports = { createApp, start, build };
