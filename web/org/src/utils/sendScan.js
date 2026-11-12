import { apiUrl, authFetch } from './api';
import { newJobId, setPendingScan, clearPendingScan } from './pendingScan';
import { SITE_REFUSED, isSiteRefused } from '../hooks/useScanSite';

/**
 * Send the photo, the stitch set or the video to be read. Keeps what the old
 * screen learnt the hard way: the pending-scan marker so a phone that sleeps
 * mid-read gets its result back, one retry on a dropped upload, and a
 * ceiling on how long to wait.
 *
 * `pending: false` leaves the marker off: the two-rack Map sends two photos
 * at once and opens its own result, so a single resumed scan would be wrong.
 *
 * Resolves { data } when read, { quality } when the server asks whether a
 * doubtful photo should be read anyway; throws a plain sentence otherwise.
 * There is no cancel: the analysing screen has none (the owner, 29 Sep 2026).
 */

/* One attempt's signal: stops on the time limit. */
function attemptSignal(timeout) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  return { signal: ctrl.signal, done: () => clearTimeout(timer) };
}

export async function sendScan({ kind = 'photo', files, siteId, override, pending = true }) {
  const body = new FormData();
  let endpoint = '/api/analyze';
  if (kind === 'stitch') {
    endpoint = '/api/stitch';
    files.forEach((f) => body.append('images', f));
  } else if (kind === 'video') {
    endpoint = '/api/analyze-video';
    body.append('video', files[0]);
  } else {
    body.append('image', files[0]);
  }
  if (override) body.append('skipQualityCheck', '1');
  if (siteId) body.append('siteId', siteId);
  const clientJobId = newJobId();
  body.append('clientJobId', clientJobId);
  if (pending) setPendingScan(clientJobId, kind === 'photo' ? 'image' : kind);
  // The server names the rack and decides its height before it answers, so a
  // photo is given four minutes.
  const timeout = kind === 'video' ? 600000 : 240000;
  const attempt = async () => {
    const a = attemptSignal(timeout);
    try { return await authFetch(apiUrl(endpoint), { method: 'POST', body, signal: a.signal }); } finally { a.done(); }
  };
  const tooLong = (e) => e?.name === 'TimeoutError' || e?.name === 'AbortError';
  let res;
  try {
    res = await attempt();
  } catch (e) {
    if (tooLong(e)) throw new Error('The scan took too long and stopped. Try again.');
    await new Promise((r) => setTimeout(r, 1200));
    try { res = await attempt(); } catch (e2) {
      if (tooLong(e2)) throw new Error('The scan took too long and stopped. Try again.');
      throw new Error('Upload failed. Check your connection and try again.');
    }
  }
  const data = await res.json().catch(() => ({}));
  if (pending) clearPendingScan();
  if (!res.ok) {
    if (data.retryable) return { quality: data.error || 'The photo may not read well.' };
    if (isSiteRefused(res, data)) throw new Error(SITE_REFUSED);
    throw new Error(data.error || 'The rack could not be read. Try again.');
  }
  return { data };
}

export default sendScan;
