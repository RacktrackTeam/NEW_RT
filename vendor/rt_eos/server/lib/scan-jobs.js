// Lightweight "claim ticket" tracker for in-flight scans.
//
// Why this exists: /api/analyze runs the CV pipeline and returns the result
// as the HTTP response. On mobile, if the app is backgrounded mid-scan, iOS
// suspends the WebView and that response is lost — the user comes back to the
// upload screen and the finished scan is orphaned (the server still wrote it
// to outputs/<rackId>/, but the client never learned the rackId).
//
// The client now sends a random `clientJobId` with the scan and remembers it.
// The server records jobId -> { status, rackId } here. When the app resumes it
// polls GET /api/analyze/result/:jobId and, once the job is done, navigates
// straight to the results it would have seen. No change to the CV pipeline.
//
// In-memory map is the source of truth; a small JSON file makes it survive a
// server (nodemon) restart during the short resume window. Entries are pruned
// after TTL so the file can't grow unbounded.

const fs   = require('fs');
const path = require('path');

// Beside the auth database: see lib/data_paths.js.
const STORE = require('./data_paths').SCAN_JOBS_FILE;
const TTL_MS = 15 * 60 * 1000;   // keep a finished job claimable for 15 min

let jobs = {};   // { [jobId]: { status:'running'|'done'|'error', rackId, error, at } }

function load() {
  try {
    if (fs.existsSync(STORE)) jobs = JSON.parse(fs.readFileSync(STORE, 'utf8')) || {};
  } catch { jobs = {}; }
  prune();
}
function persist() {
  try {
    fs.mkdirSync(path.dirname(STORE), { recursive: true });
    fs.writeFileSync(STORE, JSON.stringify(jobs));
  } catch { /* best-effort; in-memory still works */ }
}
function prune() {
  const now = Date.now();
  let changed = false;
  for (const [id, j] of Object.entries(jobs)) {
    if (!j || (now - (j.at || 0)) > TTL_MS) { delete jobs[id]; changed = true; }
  }
  if (changed) persist();
}

function isValidId(id) {
  return typeof id === 'string' && /^[A-Za-z0-9_-]{8,128}$/.test(id);
}

function start(id) {
  if (!isValidId(id)) return;
  jobs[id] = { status: 'running', rackId: null, error: null, at: Date.now() };
  persist();
}
function done(id, rackId) {
  if (!isValidId(id)) return;
  jobs[id] = { status: 'done', rackId: rackId || null, error: null, at: Date.now() };
  persist();
}
function fail(id, error) {
  if (!isValidId(id)) return;
  jobs[id] = { status: 'error', rackId: null, error: String(error || 'failed').slice(0, 300), at: Date.now() };
  persist();
}
function get(id) {
  if (!isValidId(id)) return null;
  prune();
  return jobs[id] || null;
}

load();

module.exports = { start, done, fail, get, isValidId };
