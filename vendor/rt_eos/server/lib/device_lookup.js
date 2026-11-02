/**
 * Public facts about a device model: its specifications, looked up from the
 * vendor's public pages, never from the device (RT-EOS has no network access
 * to any device).
 *
 *   specs(make, model)     -> { specs, specsStatus }
 *
 * It answers at once. A model asked for the first time answers "looking"
 * and the lookup runs in the background; the next ask (the device card asks
 * again every few seconds) gets "found" or "not_found". Answers are kept in
 * memory (lib/lookup_cache.js, restored from RackTrack) and on disk under
 * cache/lookups (lib/store.js), so a restart does not ask the web again.
 *
 * Source, restored from RackTrack (commit 625ebdce):
 *   specs     switch_specs/cli.py: a local switch knowledge base built
 *             from seed_data.json, with a web lookup of the vendor's
 *             datasheet for a model it does not hold; a garbled model read
 *             off a label is first tried against the base's own suggestions.
 *
 * Online lookups can be switched off per organization (lib/org_settings.js
 * onlineLookups). Callers pass { online: false } and then no request leaves
 * the server: specifications come only from answers already held and from
 * the local knowledge base (cli.py --no-live, no web). Each answer carries
 * lookupsOff: true then, so the device card can say so.
 *
 * Tests replace the runners with setRunner(kind, fn): specs, specsLocal.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { spawn } = require('child_process');
const store = require('./store');
const { pythonCmd } = require('./python');
const { createLookupCache } = require('./lookup_cache');
const { logger } = require('./observability');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const AGENT_DIR = path.join(REPO_ROOT, 'switch_specs');
// The web lookup gets 25 s (the agent's own default of 8.5 s made it flaky:
// a search takes 2 to 5 s, leaving too little to read the pages).
const AGENT_LIVE_DEADLINE_S = 25;
const AGENT_TIMEOUT_MS = 45_000;
const DISK_TTL_FOUND_MS = 30 * 24 * 60 * 60_000;
// A miss is asked again after an hour: a web lookup that failed on a slow
// page must not be remembered for a day.
const DISK_TTL_MISS_MS = 60 * 60_000;

/**
 * A rack asset label read as a model ("SP-R1-U18-SW04"): a unit token between
 * separators, or the site's device-name shape. Never looked up, and never
 * kept as a model (a lookup once cached such a label as a D-Link model).
 */
function isAssetTag(model) {
  const s = String(model || '').trim();
  if (!s) return false;
  if (/(^|[-_ ])U\d{1,2}(?=[-_ ]|$)/i.test(s) && /[-_ ]/.test(s)) return true;
  // A rack token (R1, R02) plus a unit token anywhere, even run together
  // by the reader ("SP-R1-U1ESW04").
  if (/(^|[-_ ])R\d{1,2}(?=[-_ ])/i.test(s) && /(^|[-_ ])U\d{1,2}/i.test(s)) return true;
  return /(^|[-_ ])(SW|PP|FW|RTR|PDU|ACT)\d{1,3}$/i.test(s) && s.split(/[-_ ]/).length >= 3;
}

const memory = createLookupCache({ ttlOkMs: DISK_TTL_FOUND_MS, ttlMissMs: DISK_TTL_MISS_MS });
const inflight = new Map();

// ── Python runners ────────────────────────────────────────────────────
function runPython(args, { cwd, timeoutMs }) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(pythonCmd(), args, { cwd, env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' }, windowsHide: true });
    } catch (err) {
      resolve({ error: err.message, stdout: '' });
      return;
    }
    let stdout = '';
    let stderr = '';
    const killer = setTimeout(() => { try { child.kill('SIGKILL'); } catch { /* gone */ } }, timeoutMs);
    child.stdout.on('data', (c) => { stdout += c.toString(); });
    child.stderr.on('data', (c) => { stderr += c.toString(); });
    child.on('error', (err) => { clearTimeout(killer); resolve({ error: err.message, stdout }); });
    child.on('close', (code) => { clearTimeout(killer); resolve({ code, stdout, stderr: stderr.slice(-500) }); });
  });
}

function lastJson(stdout) {
  const t = String(stdout || '').trim();
  try { return JSON.parse(t); } catch { /* one object per line */ }
  const line = t.split('\n').filter(Boolean).pop();
  try { return line ? JSON.parse(line) : null; } catch { return null; }
}

async function runAgent(args) {
  if (!fs.existsSync(path.join(AGENT_DIR, 'cli.py'))) return { ok: false, error: 'The specification agent is not installed.' };
  const r = await runPython(['-u', 'cli.py', '--json', '--deadline', String(AGENT_LIVE_DEADLINE_S), ...args], { cwd: AGENT_DIR, timeoutMs: AGENT_TIMEOUT_MS });
  return lastJson(r.stdout) || { ok: false, error: r.error || 'The specification agent gave no answer.' };
}

// How alike two model strings are, 0..1: the family stem sits at the front
// of a vendor SKU, so a shared prefix counts most. From RackTrack's /api/specs.
function modelSimilarity(a, b) {
  const n = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  const x = n(a);
  const y = n(b);
  if (!x || !y) return 0;
  let pre = 0;
  const minLen = Math.min(x.length, y.length);
  while (pre < minLen && x[pre] === y[pre]) pre += 1;
  let common = 0;
  let i = 0;
  for (const ch of y) {
    const idx = x.indexOf(ch, i);
    if (idx >= 0) { common += 1; i = idx + 1; }
  }
  return 0.6 * (pre / minLen) + 0.4 * (common / Math.max(x.length, y.length));
}

/** A model read off a label may be garbled: the knowledge base first, then its closest suggestion, then the web. */
async function agentSpecs(make, model) {
  const q = `${make || ''} ${model}`.trim();
  const probe = await runAgent(['--no-live', q]);
  if (probe.ok) return probe;
  const suggestions = probe?.response?.suggestions || [];
  let best = null;
  let bestScore = -1;
  for (const s of suggestions) {
    const sc = modelSimilarity(s, model);
    if (sc > bestScore) { bestScore = sc; best = s; }
  }
  // Only a near-identical name in the knowledge base stands in for a
  // misread one (DGS-1210-S2 for DGS-1210-52); a merely similar model is
  // a different product, never offered as this one.
  if (best && bestScore >= 0.75 && !isAssetTag(best) && best.toLowerCase() !== String(model).toLowerCase()) {
    const retry = await runAgent(['--no-live', `${make || ''} ${best}`.trim()]);
    if (retry.ok) return retry;
  }
  return runAgent([q]);
}

// "24x 1G RJ45 + 2x 10G SFP+" -> [ { type: 'RJ45', count: 24 }, { type: 'SFP', count: 2 } ]
function portTypesFrom(config) {
  const out = [];
  // Each "N x words" runs up to the next "N x" (a datasheet's line has no
  // separators: "48 x 1000Base-T 4 x 1000M SFP"). The same ports listed twice
  // ("48 x 1000Base-T" and "48 x 10/100/1000BASE-T ports") count once.
  const seen = new Set();
  for (const m of String(config || '').matchAll(/(\d+)\s*x\s*(.+?)(?=\s*[+,;]|\s+\d+\s*x\s|$)/gi)) {
    const words = m[2].toUpperCase();
    const type = /QSFP/.test(words) ? 'QSFP' : /SFP/.test(words) ? 'SFP' : /RJ-?45|BASE-T|COPPER/.test(words) ? 'RJ45'
      : /\bLC\b/.test(words) ? 'LC' : /\bSC\b/.test(words) ? 'SC' : null;
    if (!type) continue;
    const n = Number(m[1]);
    if (seen.has(`${type}:${n}`)) continue;
    seen.add(`${type}:${n}`);
    const hit = out.find((t) => t.type === type);
    if (hit) hit.count += n;
    else out.push({ type, count: n });
  }
  return out;
}

// Every figure of the agent's record, under the words RackTrack's switch
// screen showed them (the /api/specs contract of commit 625ebdce): the
// structured columns first, then the datasheet's own extras verbatim.
const SPEC_KEY_LABELS = {
  family: 'Family', sku: 'SKU',
  port_count: 'Ports', port_config: 'Port config', uplink_config: 'Uplinks',
  port_speed_max_gbps: 'Max port speed (Gbps)',
  switching_capacity_gbps: 'Switching capacity (Gbps)',
  forwarding_rate_mpps: 'Forwarding rate (Mpps)',
  buffer_mb: 'Buffer (MB)', latency_ns: 'Latency (ns)',
  mac_table_size: 'MAC table',
  poe_standard: 'PoE', poe_budget_w: 'PoE budget (W)',
  power_typical_w: 'Power typical (W)', power_max_w: 'Power max (W)',
  layer: 'Layer', features: 'Features', rack_units: 'Rack units',
  nos: 'Network OS', status: 'Status', use_case: 'Typical use',
};

function specTable(rec) {
  const table = {};
  for (const [key, label] of Object.entries(SPEC_KEY_LABELS)) {
    const v = rec[key];
    if (v == null || v === '') continue;
    table[label] = Array.isArray(v) ? v.join(', ') : String(v);
  }
  const extras = rec.extra_specs && typeof rec.extra_specs === 'object' ? rec.extra_specs : null;
  if (extras) {
    for (const [k, v] of Object.entries(extras)) {
      if (v == null || v === '' || table[k]) continue;
      table[k] = Array.isArray(v) ? v.join(', ') : String(v);
    }
  }
  return table;
}

function specsFromAgent(res) {
  if (!res || !res.ok) return null;
  const rec = res.response?.result || (res.response?.results || [])[0] || null;
  if (!rec) return null;
  const power = rec.power_typical_w || rec.power_max_w
    ? [rec.power_typical_w ? `${rec.power_typical_w} W typical` : null, rec.power_max_w ? `${rec.power_max_w} W max` : null].filter(Boolean).join(', ')
    : null;
  const portTypes = portTypesFrom(rec.port_config);
  const typed = portTypes.reduce((n, t) => n + t.count, 0);
  // A page read live may count the same ports twice; the port line is the record.
  const portCount = typed > 0 && (rec.port_count == null || rec.port_count > typed) ? typed : (rec.port_count ?? null);
  return {
    heightU: rec.rack_units ?? null,
    portCount,
    portTypes,
    power,
    datasheetUrl: rec.datasheet_url || null,
    productUrl: rec.datasheet_url || rec.image_url || null,
    vendor: rec.vendor || null,
    model: rec.model || null,
    table: specTable(rec),
  };
}

/** The local knowledge base only: never the web. Used when online lookups are off. */
async function localSpecs(make, model) {
  const q = `${make || ''} ${model}`.trim();
  const res = await runAgent(['--no-live', q]);
  const specs = specsFromAgent(res);
  return specs ? { ok: true, specs } : { ok: false, note: 'This model is not in the local knowledge base, and online lookups are off.' };
}

async function realSpecs(make, model) {
  const res = await agentSpecs(make, model);
  const specs = specsFromAgent(res);
  return specs ? { ok: true, specs } : { ok: false, note: res?.response?.message || res?.error || null };
}

const REAL = { specs: realSpecs, specsLocal: localSpecs };
const runners = { ...REAL };

/**
 * Specifications and optics are for switching gear only (the owner, 29 Sep
 * 2026): the kinds the Switches tab lists. A patch panel, PDU, UPS, server
 * or shelf gets none, and no lookup is started for it.
 */
const SWITCH_GEAR = new Set(['Switch', 'Router', 'Firewall', 'Gateway']);
const isSwitchGear = (type) => SWITCH_GEAR.has(String(type || ''));

/** Tests: replace a runner. fn(make, model) -> { ok, specs, note }. */
function setRunner(kind, fn) {
  runners[kind] = fn || REAL[kind];
  memory.clear();
  inflight.clear();
}

// ── Cache ─────────────────────────────────────────────────────────────
// v4: the web lookup has time to answer and asset labels are never looked
// up; answers kept before that (misses on a short deadline, labels cached as
// models) are asked again.
function keyOf(kind, make, model) {
  return `${kind}.v4::${String(make || '').trim().toLowerCase()}::${String(model || '').trim().toLowerCase()}`;
}
function diskPath(key) {
  const [kind] = key.split('::');
  return path.join(store.cachePath('lookups'), `${kind}-${crypto.createHash('sha1').update(key).digest('hex').slice(0, 20)}.json`);
}
function fromDisk(key) {
  const doc = store.readJson(diskPath(key), null);
  if (!doc || doc.key !== key || !doc.value) return null;
  const ttl = doc.value.ok ? DISK_TTL_FOUND_MS : DISK_TTL_MISS_MS;
  if (Date.now() - Date.parse(doc.at) > ttl) return null;
  return doc.value;
}

/** An answer already held (memory or disk), without starting anything. */
function held(kind, make, model) {
  const key = keyOf(kind, make, model);
  const hit = memory.get(key) || fromDisk(key);
  if (hit) memory.set(key, hit);
  return hit || null;
}

/** The answer if known; else starts the lookup and returns null ("looking"). */
function lookup(kind, make, model) {
  const key = keyOf(kind, make, model);
  const hit = memory.get(key) || fromDisk(key);
  if (hit) { memory.set(key, hit); return hit; }
  if (!inflight.has(key)) {
    const p = (async () => {
      let value;
      try { value = await runners[kind](make, model); } catch (err) { value = { ok: false, note: null, error: err.message }; }
      if (!value || typeof value !== 'object') value = { ok: false };
      // A lookup that did not run at all is not remembered.
      if (value.error) {
        logger.warn({ event: 'lookup.failed', kind, err: value.error }, 'device lookup failed');
        return value;
      }
      memory.set(key, value);
      try { store.writeJsonAtomic(diskPath(key), { key, at: new Date().toISOString(), value }); } catch { /* memory still holds it */ }
      return value;
    })().finally(() => inflight.delete(key));
    inflight.set(key, p);
  }
  return null;
}

/** Wait for a lookup in flight (tests, and a caller that can wait). */
function settle(kind, make, model) {
  return inflight.get(keyOf(kind, make, model)) || Promise.resolve(null);
}

// ── The answer in the contract's shape ────────────────────────────────
function specs(make, model, { online = true } = {}) {
  const off = online ? {} : { lookupsOff: true };
  if (!String(model || '').trim()) return { specs: null, specsStatus: 'no_model', ...off };
  if (isAssetTag(model)) return { specs: null, specsStatus: 'not_found', note: 'That is a rack label, not a model. Enter the model from the switch faceplate.', ...off };
  if (!online) {
    // Held answers are shown; anything new comes from the local base only.
    const h = held('specs', make, model);
    if (h && h.ok && h.specs) return { specs: h.specs, specsStatus: 'found', ...off };
    const v = lookup('specsLocal', make, model);
    if (!v) return { specs: null, specsStatus: 'looking', ...off };
    return v.ok && v.specs ? { specs: v.specs, specsStatus: 'found', ...off } : { specs: null, specsStatus: 'not_found', note: v.note || null, ...off };
  }
  const v = lookup('specs', make, model);
  if (!v) return { specs: null, specsStatus: 'looking' };
  return v.ok && v.specs ? { specs: v.specs, specsStatus: 'found' } : { specs: null, specsStatus: 'not_found', note: v.note || null };
}

module.exports = { SPEC_KEY_LABELS, specTable, specs, setRunner, settle, portTypesFrom, specsFromAgent, modelSimilarity, isSwitchGear, SWITCH_GEAR, isAssetTag };
