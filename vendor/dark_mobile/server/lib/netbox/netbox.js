/**
 * NetBox REST client.
 *
 * Uses the fetch built into Node 18+, so there is no HTTP dependency to install
 * or keep patched. Everything here is GET, POST or PATCH.
 *
 * There is deliberately NO delete method on this class. See writer.js rule 3.
 */

/**
 * The custom field that makes re-scanning safe. NetBox has no upsert, so
 * without a stable id of our own, pushing the same rack twice creates a
 * second copy of every object in it.
 */
const UID_FIELD = 'racktrack_uid';

/**
 * The field that says "this record was the customer's before RackTrack touched
 * it", written where the uid is written.
 *
 * A rack or a device RackTrack created is RackTrack's to keep correct. One a
 * person bound to the customer's own row is not: its name, its site, its height
 * and its model are the customer's, and only the RackTrack id is ever written on
 * it. That protection used to be inferred from a JSON file beside the code, so
 * losing the file, correcting the binding or taking it back let the writer rename
 * and re-site the customer's rack on the next compare. The fact belongs on the
 * record, next to the uid it protects, where nothing local can lose it.
 *
 * Only racks and devices carry it: they are the two types adopt() refuses to
 * claim on a collision, and the only two a person binds by hand.
 */
const BOUND_FIELD = 'racktrack_bound';
const BOUND_TYPES = Object.freeze(['dcim.rack', 'dcim.device']);

/**
 * The filter a preload uses to sweep one rack's objects: NetBox's contains
 * form, `cf_racktrack_uid__ic=<text>`, which answers with every uid holding
 * that text, ignoring case. The exact form and the starts-with form are not
 * recorded as coverage: neither promises anything about a uid it did not name.
 */
const CONTAINS_FILTER = `cf_${UID_FIELD}__ic`;

const containsNeedle = (params) => {
  const v = (params || {})[CONTAINS_FILTER];
  return typeof v === 'string' && v ? v.toLowerCase() : null;
};

/** Whether a preload of this endpoint already asked NetBox about this uid. */
const isCovered = (needles, uid) => {
  if (!needles || !needles.size) return false;
  const u = String(uid ?? '').toLowerCase();
  for (const n of needles) if (u.includes(n)) return true;
  return false;
};

class NetBoxError extends Error {
  constructor(status, detail, path = '') {
    super(`NetBox HTTP ${status} on ${path}: ${JSON.stringify(detail)}`);
    this.name = 'NetBoxError';
    this.status = status;
    this.detail = detail;
    this.path = path;
  }
}

class NetBox {
  constructor(url, token, timeoutMs = 15000) {
    this.url = String(url || '').replace(/\/+$/, '');
    this.token = token || '';
    this.timeoutMs = timeoutMs;
  }

  headers() {
    const h = { Accept: 'application/json', 'Content-Type': 'application/json' };
    // Tolerate a token pasted with its scheme already on it ("Bearer nbt_...").
    const raw = String(this.token || '').replace(/^\s*(Bearer|Token)\s+/i, '').trim();
    if (raw) {
      // Newer NetBox issues nbt_ tokens and authenticates them with the Bearer
      // scheme; older NetBox uses the DRF "Token" scheme. Sending the wrong one
      // is a 403 even when the token is perfectly valid, so pick by prefix.
      const scheme = raw.startsWith('nbt_') ? 'Bearer' : 'Token';
      h.Authorization = `${scheme} ${raw}`;
    }
    return h;
  }

  async request(method, path, body = null, params = null) {
    let url = `${this.url}${path}`;
    if (params && Object.keys(params).length) {
      url += `?${new URLSearchParams(params).toString()}`;
    }
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), this.timeoutMs);
    let res;
    try {
      res = await fetch(url, {
        method,
        headers: this.headers(),
        body: body === null ? undefined : JSON.stringify(body),
        signal: ctrl.signal,
      });
    } catch (err) {
      throw new NetBoxError(0, `unreachable: ${err.message}`, path);
    } finally {
      clearTimeout(timer);
    }

    const text = await res.text();
    let parsed;
    try { parsed = text ? JSON.parse(text) : {}; } catch { parsed = text.slice(0, 500); }
    // NetBox's own error bodies are specific and name the offending field —
    // pass them through untouched rather than flattening to a status code.
    if (!res.ok) throw new NetBoxError(res.status, parsed, path);
    return parsed;
  }

  get(path, params)        { return this.request('GET', path, null, params); }
  post(path, body)         { return this.request('POST', path, body); }
  patch(path, id, body)    { return this.request('PATCH', `${path}${id}/`, body); }

  /** Walk every page. NetBox caps page size, so a full rack needs this. */
  async paginate(path, params = {}) {
    const out = [];
    let page = await this.request('GET', path, null, { ...params, limit: 200 });
    out.push(...(page.results || []));
    while (page.next) {
      const rel = page.next.startsWith(this.url) ? page.next.slice(this.url.length) : page.next;
      page = await this.request('GET', rel);
      out.push(...(page.results || []));
    }
    return out;
  }

  status() { return this.get('/api/status/'); }

  /**
   * The whole idempotency story. Returns the existing object, or null.
   * Filtering on a custom field uses NetBox's `cf_<name>` query parameter.
   *
   * The server's match is not trusted: a text custom field whose filter
   * logic is "loose" (NetBox's default) matches by substring, so a lookup
   * for if:R1:SW01:1 would also return :10 through :19. Only rows whose uid
   * is exactly the one asked for count.
   */
  /**
   * Fetch, in a few pages, every object on an endpoint whose racktrack_uid
   * matches a filter, and remember them by uid so findByUid answers from
   * memory. Preview used to ask NetBox one GET per object — six hundred round
   * trips for a full rack, each one a tunnel hop — and a person watched a
   * spinner for minutes to be told nothing had changed. One paginated list
   * per object type answers the same question in a dozen requests.
   *
   * A preload that fails is not an error: findByUid simply goes back to asking
   * one at a time, which is slower and still right.
   */
  async preloadByUid(endpoint, params = {}) {
    if (!this._uidCache) this._uidCache = new Map();
    let rows;
    // null means the preload failed, so nothing is known; 0 means NetBox
    // answered and there is genuinely nothing there. Callers that skip work
    // on an empty answer must not skip it on a failure.
    try { rows = await this.paginate(endpoint, params); }
    catch { return null; }
    const byUid = this._uidCache.get(endpoint) || new Map();
    for (const r of rows) {
      const uid = (r.custom_fields || {})[UID_FIELD];
      if (!uid) continue;
      // Two objects with one uid is the same refusal findByUid makes below;
      // remembered as a marker so the lookup still refuses rather than guesses.
      // A row seen again under the same NetBox id is the same object, read
      // later: a write keeps one client for the comparison before it, the push
      // and the comparison after it, and each of them preloads the same rows.
      // Counting those as two objects made every known uid refuse from the
      // second preload on. The map is kept, not reset, because a keyed rack
      // preloads each endpoint twice, by its key and by its hash, and the
      // second must not wipe what the first found.
      const prev = byUid.get(uid);
      const another = prev && prev !== 'ambiguous' && Number(prev.id) !== Number(r.id);
      byUid.set(uid, prev === 'ambiguous' || another ? 'ambiguous' : r);
    }
    this._uidCache.set(endpoint, byUid);
    // Remember what this preload actually covered. A contains filter asked
    // NetBox for every uid holding that text, so a uid holding it that did not
    // come back is genuinely absent and findByUid can say so without asking
    // again. That is the whole saving on a rack NetBox has never seen: every
    // object misses the cache, and without this each miss costs a round trip.
    // Only the contains form is recorded, because only it makes the promise:
    // an exact filter says nothing about any uid but the one it named.
    const needle = containsNeedle(params);
    if (needle) {
      if (!this._uidCovered) this._uidCovered = new Map();
      const covered = this._uidCovered.get(endpoint) || new Set();
      covered.add(needle);
      this._uidCovered.set(endpoint, covered);
    }
    return rows.length;
  }

  /**
   * `fresh` asks NetBox itself and ignores everything remembered. Use it
   * wherever the answer decides a write: a preload is a snapshot of a moment,
   * and between that moment and a patch another writer may have minted the
   * uid. Reading is free to be fast; writing is not.
   */
  async findByUid(endpoint, uid, { fresh = false } = {}) {
    const cached = fresh ? null : this._uidCache?.get(endpoint);
    if (cached && cached.has(uid)) {
      const hit = cached.get(uid);
      if (hit === 'ambiguous') {
        throw new NetBoxError(409,
          `more than one object shares ${UID_FIELD}=${uid}, so this refuses to guess which to update`, endpoint);
      }
      return hit;
    }
    // A preload that ran and did not see this uid has answered "not there"
    // for everything it covered. A uid outside what it covered, such as a
    // manufacturer every rack shares, was never asked about and still goes to
    // NetBox one at a time.
    if (!fresh && isCovered(this._uidCovered?.get(endpoint), uid)) return null;
    const res = await this.get(endpoint, { [`cf_${UID_FIELD}`]: uid });
    const hits = (res.results || []).filter((h) => (h.custom_fields || {})[UID_FIELD] === uid);
    if (hits.length > 1) {
      throw new NetBoxError(409,
        `${hits.length} objects share ${UID_FIELD}=${uid}, so this refuses to guess which to update`,
        endpoint);
    }
    return hits[0] || null;
  }

  async customField(name = UID_FIELD) {
    const res = await this.get('/api/extras/custom-fields/', { name });
    return (res.results || []).find((f) => f.name === name) || null;
  }

  /**
   * Create or widen the racktrack_bound field on racks and devices.
   *
   * Separate from the uid field because it covers two types rather than sixteen,
   * and because a push must be able to say out loud that it could not be made:
   * without it a bind cannot be marked as a bind, and an unmarked bind is one
   * the next compare will happily rename.
   */
  async ensureBoundField() {
    const existing = await this.customField(BOUND_FIELD);
    if (!existing) {
      const created = await this.post('/api/extras/custom-fields/', {
        object_types: [...BOUND_TYPES],
        type: 'text',
        name: BOUND_FIELD,
        label: 'RackTrack bound record',
        description: 'Set by RackTrack when a person bound this existing record to a scan. '
                   + 'While it is set, RackTrack writes only its own id on this record and '
                   + 'reports every other difference instead. Do not edit by hand.',
        required: false,
        filter_logic: 'exact',
      });
      return { action: 'created', field: created };
    }
    const have = new Set(existing.object_types || []);
    const missing = BOUND_TYPES.filter((t) => !have.has(t));
    if (missing.length) {
      const widened = await this.patch('/api/extras/custom-fields/', existing.id, {
        object_types: [...new Set([...have, ...BOUND_TYPES])].sort(),
      });
      return { action: 'widened', added: missing, field: widened };
    }
    return { action: 'present', field: existing };
  }

  /**
   * Create or widen the racktrack_uid custom field.
   *
   * Must run before any push. If the field does not exist, every lookup
   * silently matches nothing and every push creates duplicates — the exact
   * failure this field exists to prevent. So it is never skipped quietly.
   */
  async ensureCustomField(objectTypes) {
    const existing = await this.customField();
    if (!existing) {
      const created = await this.post('/api/extras/custom-fields/', {
        object_types: [...objectTypes].sort(),
        type: 'text',
        name: UID_FIELD,
        label: 'RackTrack UID',
        description: 'Stable RackTrack id. Used to update rather than duplicate '
                   + 'on re-scan. Do not edit by hand.',
        required: false,
        // Loose (the default) filters text by substring, which makes a
        // lookup for port 1 also return ports 10 to 19. Uids are ids.
        filter_logic: 'exact',
      });
      return { action: 'created', field: created };
    }
    // The field exists but may not cover every type this snapshot touches,
    // and one created by hand or by an older version may still filter loosely.
    const have = new Set(existing.object_types || []);
    const missing = [...objectTypes].filter((t) => !have.has(t));
    const logic = existing.filter_logic && typeof existing.filter_logic === 'object'
      ? existing.filter_logic.value : existing.filter_logic;
    const body = {};
    if (missing.length) body.object_types = [...new Set([...have, ...objectTypes])].sort();
    if (logic !== 'exact') body.filter_logic = 'exact';
    if (Object.keys(body).length) {
      const widened = await this.patch('/api/extras/custom-fields/', existing.id, body);
      return { action: 'widened', added: missing.sort(), field: widened };
    }
    return { action: 'present', field: existing };
  }
}

module.exports = { NetBox, NetBoxError, UID_FIELD, BOUND_FIELD, BOUND_TYPES };
