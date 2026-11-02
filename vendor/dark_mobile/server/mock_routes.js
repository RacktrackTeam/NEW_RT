/**
 * mock_routes.js -- Integrated mock data-source endpoints.
 *
 * Simulates 5 external APIs (ServiceNow, NetBox, SolarWinds Orion,
 * CA/DX Spectrum, Generic REST) so the app works without real PDIs.
 * Previously ran as a standalone server on :4001; now mounted directly
 * in the main Express app on :3001.
 */

'use strict';

const express = require('express');
const path    = require('path');
const fs      = require('fs');

const router = express.Router();

// -- Load dummy data --
const DATA_DIR = path.join(__dirname, 'data', 'mock');
const snData      = JSON.parse(fs.readFileSync(path.join(DATA_DIR, 'servicenow-data.json'), 'utf8'));
const nbData      = JSON.parse(fs.readFileSync(path.join(DATA_DIR, 'netbox-data.json'),     'utf8'));
const orionData   = JSON.parse(fs.readFileSync(path.join(DATA_DIR, 'orion-data.json'),      'utf8'));
const specData    = JSON.parse(fs.readFileSync(path.join(DATA_DIR, 'spectrum-data.json'),   'utf8'));
const restData    = JSON.parse(fs.readFileSync(path.join(DATA_DIR, 'generic-rest-data.json'), 'utf8'));

// -- Auth helpers --

const SN_USERS = {
  sn_admin: {
    password:   'MockSN@2024!',
    name:       'System Admin',
    role:       'System Administrator',
    inc_groups: null,
    dev_ids:    null,
    rack_ids:   null,
  },
  sn_netops: {
    password:   'NetOps@2024!',
    name:       'Network Operations',
    role:       'Network Engineer',
    inc_groups: ['grp001'],
    dev_ids:    ['ci001aaa0001bbbb0001cccc0001dddd01',
                 'ci005aaa0005bbbb0005cccc0005dddd05',
                 'ci002aaa0002bbbb0002cccc0002dddd02'],
    rack_ids:   ['rack001aaa001bbbb001cccc001dddd001',
                 'rack002aaa002bbbb002cccc002dddd002'],
  },
  sn_dcops: {
    password:   'DCOps@2024!',
    name:       'DC Operations',
    role:       'Data Center Operator',
    inc_groups: ['grp003'],
    dev_ids:    [],
    rack_ids:   ['rack003aaa003bbbb003cccc003dddd003'],
  },
  sn_secops: {
    password:   'SecOps@2024!',
    name:       'Security Operations',
    role:       'Security Engineer',
    inc_groups: ['grp002'],
    dev_ids:    ['ci003aaa0003bbbb0003cccc0003dddd03'],
    rack_ids:   ['rack001aaa001bbbb001cccc001dddd001'],
  },
  sn_noc: {
    password:   'NOC@2024!',
    name:       'NOC Analyst',
    role:       'NOC Analyst',
    inc_groups: null,
    dev_ids:    null,
    rack_ids:   null,
  },
};

function checkSnAuth(req, res) {
  const auth = req.headers['authorization'] || '';
  if (!auth.startsWith('Basic ')) {
    res.status(401).json({ error: { message: 'Unauthorized', detail: 'Basic auth required' } });
    return null;
  }
  const [user, pass] = Buffer.from(auth.slice(6), 'base64').toString().split(':');
  const profile = SN_USERS[user];
  // `if (profile && ...)` short-circuited for an UNKNOWN user — the password was
  // never checked — and the old `return profile ? user : 'sn_admin'` then
  // promoted that unknown user to admin. `curl -u bogus:wrongpw` returned 200
  // and the full incident table. This mirrors checkOrionAuth/checkSpectrumAuth,
  // which both already fail closed with `!profile ||`; this checker was the
  // only one of the five missing it.
  if (!profile || profile.password !== pass) {
    res.status(401).json({ error: { message: 'Invalid credentials' } });
    return null;
  }
  return user;
}

const NB_USERS = {
  'nb-a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6': { name: 'NB Admin',       role: 'Administrator',       dev_ids: null,        rack_ids: null },
  'nb-netops-111111111111111':            { name: 'Network Ops',     role: 'Network Engineer',    dev_ids: [1, 3, 4],   rack_ids: [1, 2] },
  'nb-secops-222222222222222':            { name: 'Security Ops',    role: 'Security Engineer',   dev_ids: [2],         rack_ids: [1] },
  'nb-dcops-3333333333333333':            { name: 'DC Operations',   role: 'DC Operator',         dev_ids: [5],         rack_ids: [3] },
  'nb-readonly-44444444444444':           { name: 'NOC Analyst',     role: 'NOC Analyst',         dev_ids: null,        rack_ids: null },
};
function checkNbAuth(req, res) {
  const auth = req.headers['authorization'] || '';
  if (!auth.startsWith('Token ')) {
    res.status(403).json({ detail: 'Authentication credentials were not provided.' });
    return null;
  }
  const token = auth.slice(6);
  if (!NB_USERS[token]) {
    res.status(403).json({ detail: 'Invalid token.' });
    return null;
  }
  return token;
}

const ORION_USERS = {
  orion_admin:  { password: 'OrionMock@2024!',  name: 'Orion Admin',    role: 'Administrator',    node_ids: null },
  orion_netops: { password: 'NetOps@Orion24!',  name: 'Network Ops',    role: 'Network Engineer', node_ids: [1, 3] },
  orion_secops: { password: 'SecOps@Orion24!',  name: 'Security Ops',   role: 'Security Engineer',node_ids: [2] },
  orion_dcops:  { password: 'DCOps@Orion24!',   name: 'DC Operations',  role: 'DC Operator',      node_ids: [4] },
  orion_noc:    { password: 'NOC@Orion2024!',   name: 'NOC Analyst',    role: 'NOC Analyst',      node_ids: null },
};
function checkOrionAuth(req, res) {
  const auth = req.headers['authorization'] || '';
  if (!auth.startsWith('Basic ')) {
    res.status(401).json({ error: 'Unauthorized' });
    return null;
  }
  const [user, pass] = Buffer.from(auth.slice(6), 'base64').toString().split(':');
  const profile = ORION_USERS[user];
  if (!profile || profile.password !== pass) {
    res.status(401).json({ error: 'Invalid credentials' });
    return null;
  }
  return user;
}

const SPECTRUM_USERS = {
  spectrum_admin:  { password: 'SpectrumMock@2024!', name: 'Admin',        role: 'Administrator',    dev_mhs: null },
  spectrum_netops: { password: 'NetOps@Spec24!',     name: 'Network Ops',  role: 'Network Engineer', dev_mhs: ['0x10001', '0x10003'] },
  spectrum_secops: { password: 'SecOps@Spec24!',     name: 'Security Ops', role: 'Security Engineer',dev_mhs: ['0x10002'] },
  spectrum_dcops:  { password: 'DCOps@Spec24!',      name: 'DC Ops',       role: 'DC Operator',      dev_mhs: ['0x10004'] },
  spectrum_noc:    { password: 'NOC@Spec2024!',      name: 'NOC Analyst',  role: 'NOC Analyst',      dev_mhs: null },
};
function checkSpectrumAuth(req, res) {
  const auth = req.headers['authorization'] || '';
  if (!auth.startsWith('Basic ')) {
    res.status(401).json({ error: 'Unauthorized' });
    return null;
  }
  const [user, pass] = Buffer.from(auth.slice(6), 'base64').toString().split(':');
  const profile = SPECTRUM_USERS[user];
  if (!profile || profile.password !== pass) {
    res.status(401).json({ error: 'Invalid credentials' });
    return null;
  }
  return user;
}

const REST_USERS = {
  'rt-z9y8x7w6v5u4t3s2r1q0p9o8n7m6l5k4': { name: 'REST Admin',     role: 'Administrator',    dev_ids: null,                                    rack_ids: null,                         alert_ids: null },
  'rt-netops-aabbcc11223344':             { name: 'Network Ops',     role: 'Network Engineer', dev_ids: ['dev-sw-u06','dev-sw-u10','dev-pp-u18'], rack_ids: ['rack-a01','rack-b02'],      alert_ids: ['alert-002'] },
  'rt-secops-aabbcc55667788':             { name: 'Security Ops',    role: 'Security Engineer',dev_ids: ['dev-fw-u01'],                           rack_ids: ['rack-a01'],                 alert_ids: [] },
  'rt-dcops-aabbcc99aabbcc':              { name: 'DC Operations',   role: 'DC Operator',      dev_ids: ['dev-ups-u40'],                          rack_ids: ['rack-c03'],                 alert_ids: ['alert-001'] },
  'rt-readonly-aabbccddeeff0':            { name: 'NOC Analyst',     role: 'NOC Analyst',      dev_ids: null,                                    rack_ids: null,                         alert_ids: null },
};
function checkRestAuth(req, res) {
  const auth = req.headers['authorization'] || '';
  if (!auth.startsWith('Bearer ') && !auth.startsWith('Token ')) {
    res.status(401).json({ error: 'Unauthorized', message: 'Bearer or Token required' });
    return null;
  }
  const token = auth.replace(/^(Bearer|Token)\s+/, '');
  if (!REST_USERS[token]) {
    res.status(401).json({ error: 'Invalid token' });
    return null;
  }
  return token;
}

// === ACCOUNT 1 -- ServiceNow API (/api/now/...) ===

router.post('/login', (req, res) => {
  const { user_name, user_password } = req.body || {};
  const profile = SN_USERS[user_name];
  if (profile && profile.password === user_password) {
    return res.json({ result: { session: 'mock-session-token', user: user_name } });
  }
  res.status(401).json({ error: 'Invalid credentials' });
});

router.get('/api/now/table/incident', (req, res) => {
  const snUser = checkSnAuth(req, res);
  if (!snUser) return;
  const profile = SN_USERS[snUser];
  const query   = req.query.sysparm_query || '';
  const limit   = parseInt(req.query.sysparm_limit  || '100');
  const offset  = parseInt(req.query.sysparm_offset || '0');
  const displayValue = req.query.sysparm_display_value === 'true';

  let results = [...snData.incidents];

  if (profile.inc_groups) {
    results = results.filter(i => profile.inc_groups.includes(i.assignment_group?.value));
  }
  if (query.includes('number=')) {
    const m = query.match(/number=([^&^]+)/);
    if (m) results = results.filter(i => i.number === m[1]);
  }
  if (query.includes('state=')) {
    const m = query.match(/state=([^&^]+)/);
    if (m) results = results.filter(i => i.state === m[1]);
  }
  const stateInMatch = query.match(/stateIN([^&^]+)/);
  if (stateInMatch) {
    const allowed = stateInMatch[1].split(',');
    results = results.filter(i => allowed.includes(i.state));
  }

  if (displayValue) {
    results = results.map(r => {
      const flat = { ...r };
      for (const key of ['cmdb_ci', 'assignment_group', 'assigned_to']) {
        if (flat[key] && typeof flat[key] === 'object' && 'display_value' in flat[key]) {
          flat[key] = flat[key].display_value;
        }
      }
      return flat;
    });
  }

  res.json({ result: results.slice(offset, offset + limit) });
});

router.get('/api/now/table/sc_request', (req, res) => {
  if (!checkSnAuth(req, res)) return;
  res.json({ result: snData.sc_requests });
});

router.get('/api/now/table/sc_request/:sys_id', (req, res) => {
  if (!checkSnAuth(req, res)) return;
  const found = snData.sc_requests.find(r => r.sys_id === req.params.sys_id);
  if (!found) return res.status(404).json({ error: { message: 'Record not found' } });
  res.json({ result: found });
});

router.post('/api/now/table/sc_request', (req, res) => {
  if (!checkSnAuth(req, res)) return;
  const newReq = {
    sys_id:            `req-${Date.now()}-mock`,
    number:            `REQ${String(Date.now()).slice(-7)}`,
    short_description: req.body?.short_description || 'CMDB sync request',
    state:             '1',
    approval:          'requested',
    opened_at:         new Date().toISOString(),
    ...req.body,
  };
  snData.sc_requests.push(newReq);
  res.status(201).json({ result: newReq });
});

router.patch('/api/now/table/:table/:sys_id', (req, res) => {
  if (!checkSnAuth(req, res)) return;
  res.json({ result: { sys_id: req.params.sys_id, ...req.body, sys_updated_on: new Date().toISOString() } });
});

router.get('/api/now/table/cmdb_ci_rack', (req, res) => {
  const snUser = checkSnAuth(req, res);
  if (!snUser) return;
  const profile = SN_USERS[snUser];
  const query   = req.query.sysparm_query || '';
  let results   = [...snData.cmdb_ci_rack];

  if (profile.rack_ids) {
    results = results.filter(r => profile.rack_ids.includes(r.sys_id));
  }
  if (query.includes('u_racktrack_scan_id=')) {
    const m = query.match(/u_racktrack_scan_id=([^&^]+)/);
    if (m) results = results.filter(r => r.u_racktrack_scan_id === m[1]);
  }
  res.json({ result: results });
});

router.get('/api/now/table/cmdb_ci_netgear', (req, res) => {
  const snUser = checkSnAuth(req, res);
  if (!snUser) return;
  const profile = SN_USERS[snUser];
  let results   = [...snData.cmdb_ci_netgear];

  if (profile.dev_ids) {
    results = results.filter(d => profile.dev_ids.includes(d.sys_id));
  }
  res.json({ result: results });
});

router.get('/api/now/table/cmdb_rel_ci', (req, res) => {
  if (!checkSnAuth(req, res)) return;
  const query = req.query.sysparm_query || '';
  let results = [...snData.cmdb_rel_ci];
  if (query.includes('child=')) {
    const m = query.match(/child=([^&^]+)/);
    if (m) results = results.filter(r => r.child.value === m[1]);
  }
  res.json({ result: results });
});

router.get('/api/now/table/cmdb_ci/:sys_id', (req, res) => {
  if (!checkSnAuth(req, res)) return;
  const sid = req.params.sys_id;
  const ci  = snData.cmdb_ci_netgear.find(c => c.sys_id === sid)
           || snData.cmdb_ci_rack.find(c => c.sys_id === sid);
  if (!ci) return res.status(404).json({ error: { message: 'Not found' } });
  res.json({ result: ci });
});

router.get('/api/now/table/:table/:sys_id', (req, res) => {
  if (!checkSnAuth(req, res)) return;
  const { table, sys_id } = req.params;
  const all = [
    ...snData.cmdb_ci_netgear,
    ...snData.cmdb_ci_rack,
    ...snData.incidents,
    ...snData.sc_requests,
    ...snData.cmdb_rel_ci,
  ];
  const found = all.find(r => r.sys_id === sys_id);
  if (!found) {
    return res.json({ result: { sys_id, sys_class_name: table, name: `mock-${sys_id.slice(0,8)}` } });
  }
  res.json({ result: found });
});

router.get('/api/now/table/:table', (req, res) => {
  if (!checkSnAuth(req, res)) return;
  const tableMap = {
    cmdb_ci_rack:    snData.cmdb_ci_rack,
    cmdb_ci_netgear: snData.cmdb_ci_netgear,
    incident:        snData.incidents,
    sc_request:      snData.sc_requests,
    cmdb_rel_ci:     snData.cmdb_rel_ci,
  };
  const rows = tableMap[req.params.table] || [];
  res.json({ result: rows });
});

// === ACCOUNT 2 -- NetBox API (/api/dcim/...) ===

router.get('/api/status/', (_req, res) => {
  res.json({
    netbox_version: '3.7.0-mock',
    python_version:  '3.11.0',
    plugins:         {},
    rq_workers_running: 1,
  });
});

router.get('/api/dcim/racks/', (req, res) => {
  const nbToken = checkNbAuth(req, res);
  if (!nbToken) return;
  const profile = NB_USERS[nbToken];
  let racks = [...nbData.racks];
  if (profile.rack_ids) racks = racks.filter(r => profile.rack_ids.includes(r.id));
  res.json({ count: racks.length, next: null, previous: null, results: racks });
});

router.get('/api/dcim/racks/:id/', (req, res) => {
  const nbToken = checkNbAuth(req, res);
  if (!nbToken) return;
  const profile = NB_USERS[nbToken];
  const id = Number(req.params.id);
  if (profile.rack_ids && !profile.rack_ids.includes(id)) return res.status(404).json({ detail: 'Not found.' });
  const rack = nbData.racks.find(r => r.id === id);
  if (!rack) return res.status(404).json({ detail: 'Not found.' });
  res.json(rack);
});

router.get('/api/dcim/devices/', (req, res) => {
  const nbToken = checkNbAuth(req, res);
  if (!nbToken) return;
  const profile = NB_USERS[nbToken];
  const { rack_id, site_id } = req.query;
  let devs = [...nbData.devices];
  if (profile.dev_ids)  devs = devs.filter(d => profile.dev_ids.includes(d.id));
  if (rack_id)          devs = devs.filter(d => d.rack?.id === Number(rack_id));
  if (site_id)          devs = devs.filter(d => d.site?.id === Number(site_id));
  res.json({ count: devs.length, next: null, previous: null, results: devs });
});

router.get('/api/dcim/devices/:id/', (req, res) => {
  const nbToken = checkNbAuth(req, res);
  if (!nbToken) return;
  const profile = NB_USERS[nbToken];
  const id = Number(req.params.id);
  if (profile.dev_ids && !profile.dev_ids.includes(id)) return res.status(404).json({ detail: 'Not found.' });
  const dev = nbData.devices.find(d => d.id === id);
  if (!dev) return res.status(404).json({ detail: 'Not found.' });
  res.json(dev);
});

router.get('/api/dcim/interfaces/', (req, res) => {
  const nbToken = checkNbAuth(req, res);
  if (!nbToken) return;
  const profile = NB_USERS[nbToken];
  const { device_id } = req.query;
  let ifaces = [...nbData.interfaces];
  if (profile.dev_ids) ifaces = ifaces.filter(i => profile.dev_ids.includes(i.device?.id));
  if (device_id)       ifaces = ifaces.filter(i => i.device?.id === Number(device_id));
  res.json({ count: ifaces.length, next: null, previous: null, results: ifaces });
});

router.get('/api/dcim/cables/', (req, res) => {
  if (!checkNbAuth(req, res)) return;
  res.json({ count: nbData.cables.length, next: null, previous: null, results: nbData.cables });
});

router.get('/api/users/tokens/', (req, res) => {
  if (!checkNbAuth(req, res)) return;
  res.json({ count: 1, results: [{ id: 1, display: 'Mock Token', key: 'nb-a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6' }] });
});

// === ACCOUNT 3 -- SolarWinds Orion (/SolarWinds/...) ===

router.get('/SolarWinds/InformationService/v3/Json/Query', (req, res) => {
  const orionUser = checkOrionAuth(req, res);
  if (!orionUser) return;
  const profile = ORION_USERS[orionUser];
  const query   = (req.query.query || '').toLowerCase();
  let results   = [];

  if (query.includes('orion.nodes')) {
    results = orionData.nodes;
    if (profile.node_ids) results = results.filter(n => profile.node_ids.includes(n.NodeID));
  } else if (query.includes('orion.npm.interfaces')) {
    results = orionData.interfaces;
    if (profile.node_ids) results = results.filter(i => profile.node_ids.includes(i.NodeID));
  } else if (query.includes('orion.alertactive')) {
    results = orionData.alerts;
    if (profile.node_ids) results = results.filter(a => profile.node_ids.includes(a.NodeID));
  }

  res.json({ results });
});

router.get('/SolarWinds/InformationService/v3/Json/Invoke/Orion.System/GetHostName', (req, res) => {
  if (!checkOrionAuth(req, res)) return;
  res.json({ result: 'orion-mock-server' });
});

router.get('/SolarWinds/InformationService/v3/Json/swis://localhost/Orion/Orion.Nodes/:id', (req, res) => {
  if (!checkOrionAuth(req, res)) return;
  const node = orionData.nodes.find(n => n.NodeID === Number(req.params.id));
  if (!node) return res.status(404).json({ error: 'Not found' });
  res.json(node);
});

// === ACCOUNT 4 -- CA/DX Spectrum (/spectrum/...) ===

router.get('/spectrum/restful/landscapes', (req, res) => {
  if (!checkSpectrumAuth(req, res)) return;
  res.set('Content-Type', 'application/json');
  res.json({ 'landscapes': specData.landscapes });
});

router.get('/spectrum/restful/devices', (req, res) => {
  const spUser = checkSpectrumAuth(req, res);
  if (!spUser) return;
  const profile   = SPECTRUM_USERS[spUser];
  const landscape = req.query.landscape;
  let devs = [...specData.devices];
  if (profile.dev_mhs) devs = devs.filter(d => profile.dev_mhs.includes(d.mh));
  if (landscape)       devs = devs.filter(d => d.landscapeHandle === landscape);
  res.json({ 'ns1:model-response-list': { 'ns1:model-responses': devs.map(d => ({ 'ns1:model': d })) } });
});

router.get('/spectrum/restful/devices/:mh', (req, res) => {
  const spUser = checkSpectrumAuth(req, res);
  if (!spUser) return;
  const profile = SPECTRUM_USERS[spUser];
  if (profile.dev_mhs && !profile.dev_mhs.includes(req.params.mh)) return res.status(404).json({ error: 'Model not found' });
  const dev = specData.devices.find(d => d.mh === req.params.mh);
  if (!dev) return res.status(404).json({ error: 'Model not found' });
  res.json({ 'ns1:model': dev });
});

router.get('/spectrum/restful/alarms', (req, res) => {
  const spUser = checkSpectrumAuth(req, res);
  if (!spUser) return;
  const profile = SPECTRUM_USERS[spUser];
  let alarms = [...specData.alarms];
  if (profile.dev_mhs) alarms = alarms.filter(a => profile.dev_mhs.includes(a.modelHandle));
  res.json({ 'ns1:alarm-response-list': { 'ns1:alarm-responses': alarms } });
});

// === ACCOUNT 5 -- Generic REST (/api/v1/...) ===

router.get('/api/v1/racks', (req, res) => {
  const restToken = checkRestAuth(req, res);
  if (!restToken) return;
  const profile = REST_USERS[restToken];
  let racks = [...restData.racks];
  if (profile.rack_ids) racks = racks.filter(r => profile.rack_ids.includes(r.id));
  res.json({ ok: true, count: racks.length, data: racks });
});

router.get('/api/v1/racks/:id', (req, res) => {
  const restToken = checkRestAuth(req, res);
  if (!restToken) return;
  const profile = REST_USERS[restToken];
  if (profile.rack_ids && !profile.rack_ids.includes(req.params.id)) return res.status(404).json({ ok: false, error: 'Not found' });
  const rack = restData.racks.find(r => r.id === req.params.id);
  if (!rack) return res.status(404).json({ ok: false, error: 'Not found' });
  res.json({ ok: true, data: rack });
});

router.get('/api/v1/devices', (req, res) => {
  const restToken = checkRestAuth(req, res);
  if (!restToken) return;
  const profile   = REST_USERS[restToken];
  const { rack, type } = req.query;
  let devs = [...restData.devices];
  if (profile.dev_ids) devs = devs.filter(d => profile.dev_ids.includes(d.id));
  if (rack)            devs = devs.filter(d => d.rack === rack);
  if (type)            devs = devs.filter(d => d.type.toLowerCase() === type.toLowerCase());
  res.json({ ok: true, count: devs.length, data: devs });
});

router.get('/api/v1/devices/:id', (req, res) => {
  const restToken = checkRestAuth(req, res);
  if (!restToken) return;
  const profile = REST_USERS[restToken];
  if (profile.dev_ids && !profile.dev_ids.includes(req.params.id)) return res.status(404).json({ ok: false, error: 'Not found' });
  const dev = restData.devices.find(d => d.id === req.params.id);
  if (!dev) return res.status(404).json({ ok: false, error: 'Not found' });
  res.json({ ok: true, data: dev });
});

router.get('/api/v1/cables', (req, res) => {
  if (!checkRestAuth(req, res)) return;
  res.json({ ok: true, count: restData.cables.length, data: restData.cables });
});

router.get('/api/v1/alerts', (req, res) => {
  const restToken = checkRestAuth(req, res);
  if (!restToken) return;
  const profile = REST_USERS[restToken];
  let alerts = [...restData.alerts];
  if (profile.alert_ids) alerts = alerts.filter(a => profile.alert_ids.includes(a.id));
  res.json({ ok: true, count: alerts.length, data: alerts });
});

router.get('/api/v1/health', (_req, res) => {
  res.json({ ok: true, service: 'RackTrack Generic REST Mock', version: '1.0.0' });
});

module.exports = router;
