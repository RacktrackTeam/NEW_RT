// Remembers a scan that's being analyzed, so the app can reclaim it if iOS
// suspends the WebView mid-scan (app-switch, phone lock). The scan itself
// still finishes on the server; we just need to find our way back to it.
//
// Flow: ScanPage generates a random id, sends it with the upload, and drops a
// marker here BEFORE awaiting. If the request dies because the app was
// backgrounded, the marker survives. On resume, PendingScanResumer (App.jsx)
// polls the server for that id and navigates straight to the results.
import { apiUrl, authFetch } from './api';
import { getItem, setItem, removeItem } from './safeStorage';

const KEY = 'racktrack:pendingScan';
const TTL_MS = 15 * 60 * 1000;   // matches the server-side job TTL

export function newJobId() {
  try { if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID().replace(/-/g, ''); } catch { /* best effort */ }
  return 'j' + Date.now().toString(36) + Math.random().toString(36).slice(2, 12);
}

export function setPendingScan(id, kind = 'image') {
  try { setItem(KEY, JSON.stringify({ id, kind, startedAt: Date.now() })); } catch { /* best effort */ }
}

export function getPendingScan() {
  try {
    const raw = getItem(KEY);
    if (!raw) return null;
    const p = JSON.parse(raw);
    if (!p || !p.id) return null;
    if (Date.now() - (p.startedAt || 0) > TTL_MS) { clearPendingScan(); return null; }
    return p;
  } catch { return null; }
}

export function clearPendingScan() {
  try { removeItem(KEY); } catch { /* best effort */ }
}

// Ask the server what became of a scan id: { status:'running'|'done'|'error'|'missing', rackId }
export async function fetchScanJob(id) {
  try {
    const r = await authFetch(apiUrl(`/api/analyze/result/${encodeURIComponent(id)}`));
    if (!r.ok) return { status: 'missing' };
    return await r.json();
  } catch {
    return { status: 'unknown' };   // transient network error - caller retries
  }
}
