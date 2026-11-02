/**
 * Work out what is behind an address, without being told and without a login.
 *
 * RackTrack already knows how to talk to a NetBox, a ServiceNow and a plain
 * REST CMDB. What it could not do was work out WHICH of them a customer had
 * typed: the Data sources form made you pick the product from a list first, and
 * picking wrong gave you a connection error that named nothing useful.
 *
 * Ported from the on-device CMDB lookup a colleague wrote. His reasoning is
 * kept, because the reasoning is the valuable part:
 *
 *   - ServiceNow names itself, twice, and neither tell needs a password: its
 *     hostname, and the challenge it sends back to an unauthenticated request,
 *     WWW-Authenticate: Basic realm="Service-now".
 *   - NetBox has /api/status/ and guards it. A 200 carrying a netbox-version is
 *     a NetBox; so is a 401 or a 403, because only something that HAS that
 *     endpoint bothers to protect it.
 *   - Anything else is treated as a plain REST CMDB, and that guess is tried
 *     LAST on purpose: a generic REST connector accepts almost any JSON API, so
 *     trying it earlier would swallow a NetBox or a ServiceNow that was simply
 *     misread.
 *
 * A guess here is only a guess. It is offered to the person as a suggestion,
 * and signing in is what confirms or corrects it.
 */
const { safeFetch, urlCandidates, AddressRefused } = require('../../safe_address');

/** ServiceNow puts this in the realm of its Basic challenge. */
const REALM = 'Service-now';
const SERVICENOW_HOSTS = ['.service-now.com', '.servicenow.com'];

/** How long to wait on a system that may be asleep, per attempt. */
const TIMEOUT_MS = 6000;

/**
 * The order to fall back through when the first guess is wrong. `rest` is last
 * because it accepts almost anything; see the note at the top.
 */
const ORDER = ['netbox', 'servicenow', 'rest'];

const withTimeout = async (url, opts, guard) => {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    return await safeFetch(url, { ...opts, signal: ctrl.signal }, guard);
  } finally {
    clearTimeout(timer);
  }
};

/**
 * Is a ServiceNow behind this URL? Neither tell needs a login.
 *
 * Returns { match, answered }. `answered` matters as much as `match`: a host
 * that never replied has not told us it is a plain REST CMDB, it has told us
 * nothing, and reporting "REST CMDB" for an address that is simply down would
 * send somebody to check credentials on a system that was never there.
 */
async function looksLikeServiceNow(url, guard = {}) {
  const host = new URL(url).hostname.toLowerCase();
  if (SERVICENOW_HOSTS.some((s) => host.endsWith(s))) return { match: true, answered: true };
  let res;
  try {
    res = await withTimeout(`${url}/api/now/table/sys_user?sysparm_limit=1`,
      { headers: { Accept: 'application/json' } }, guard);
  } catch (err) {
    if (err instanceof AddressRefused) throw err;
    return { match: false, answered: false };
  }
  if (String(res.headers.get('www-authenticate') || '').includes(REALM)) {
    return { match: true, answered: true };
  }
  let body;
  try { body = await res.json(); } catch { return { match: false, answered: true }; }
  const match = Boolean(body && typeof body === 'object' && ('result' in body || 'error' in body));
  return { match, answered: true };
}

/** Is a NetBox behind this URL? A guarded /api/status/ is the tell. */
async function looksLikeNetBox(url, guard = {}) {
  let res;
  try {
    res = await withTimeout(`${url}/api/status/`,
      { headers: { Accept: 'application/json' } }, guard);
  } catch (err) {
    if (err instanceof AddressRefused) throw err;
    return { match: false, answered: false };
  }
  // Guarded, which is what NetBox does. Something that does not have this
  // endpoint answers 404, not 401.
  if (res.status === 401 || res.status === 403) return { match: true, answered: true };
  if (res.status !== 200) return { match: false, answered: true };
  try {
    const body = await res.json();
    return { match: Boolean(body && typeof body === 'object' && 'netbox-version' in body),
             answered: true };
  } catch { return { match: false, answered: true }; }
}

/**
 * What is behind one exact URL.
 *
 * Returns { type, url, why }. `type` is one of the connector types the registry
 * knows, and `why` is a sentence a person can read, because a detection nobody
 * can question is worse than one they can.
 */
async function probe(url, guard = {}) {
  const sn = await looksLikeServiceNow(url, guard);
  if (sn.match) return { type: 'servicenow', url, why: 'It answers as a ServiceNow instance.' };
  const nb = await looksLikeNetBox(url, guard);
  if (nb.match) return { type: 'netbox', url, why: 'It has a NetBox status endpoint.' };
  // Nothing replied to either question. That is not "a REST CMDB we could not
  // identify", it is an address that is not answering, and saying the first
  // would send somebody to check credentials on a system that was never there.
  if (!sn.answered && !nb.answered) {
    const err = new Error(`Nothing answered at ${url}.`);
    err.code = 'no_answer';
    throw err;
  }
  return { type: 'rest', url, why: 'Nothing identifies it, so it is treated as a REST CMDB.' };
}

/**
 * What is behind an address somebody typed.
 *
 * The scheme is chosen by urlCandidates, the safe way round, and each candidate
 * is tried in turn - which matters for a lab address that is plain http. The
 * first candidate that answers at all decides; a candidate that cannot be
 * reached moves on to the next.
 *
 * Throws AddressRefused when the address is one we must not connect to at all.
 * That refusal is not a failed guess and must not be swallowed: it is the whole
 * point of asking before connecting.
 */
async function detect(typed, { allowPrivate = false, allowed = null, allowPlainHttp = false } = {}) {
  const guard = { allowPrivate, allowed };
  const candidates = urlCandidates(typed, { allowPlainHttp });
  let lastError = null;
  for (const url of candidates) {
    try {
      return await probe(url, guard);
    } catch (err) {
      if (err instanceof AddressRefused) throw err;
      lastError = err;
    }
  }
  const tried = candidates.length === 1 ? candidates[0] : candidates.join(' and ');
  const err = new Error(`Nothing answered at ${tried}.`);
  err.code = 'no_answer';
  err.cause = lastError;
  throw err;
}

module.exports = { detect, probe, looksLikeNetBox, looksLikeServiceNow, ORDER, REALM, TIMEOUT_MS };
