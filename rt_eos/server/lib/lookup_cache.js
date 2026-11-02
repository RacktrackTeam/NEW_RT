'use strict';

// Memo for the specification lookup (/api/devices/specs). It spawns Python
// and can go to the live web, and the answer does not change minute to
// minute: a model's spec sheet is fixed. Yet every page load re-paid the
// full cost, because the client's cache is a module-level Map that a reload
// wipes.
//
// Measured on a fast laptop: a model the spec agent's DB knows answers in
// ~0.03s, but a garbled OCR model misses the DB and falls through to two live
// web lookups at ~7s each. On a 2-vCPU box all of that is worse. Caching it
// is the difference between a page that loads and a page you wait for.
//
// In-memory on purpose: re-warming after a restart is cheap, and it sidesteps
// invalidating a file on disk entirely. The contents are public facts about
// hardware - no tenant data - so one cache serves every caller.

const DEFAULTS = {
  ttlOkMs:   24 * 60 * 60_000,   // 24h for a real answer
  // Negative results are cached too - a model the vendor doesn't publish
  // should not re-scrape the web on every visit - but for much less time, so
  // a newly added model isn't shut out for a day.
  ttlMissMs: 30 * 60_000,
  max:       500,
};

function createLookupCache(opts = {}) {
  const { ttlOkMs, ttlMissMs, max } = { ...DEFAULTS, ...opts };
  // Insertion-ordered, so the first key is always the least recently used.
  const entries = new Map();      // key -> { at, value }
  const inflight = new Map();     // key -> Promise
  const now = () => (opts.now ? opts.now() : Date.now());

  function get(key) {
    const hit = entries.get(key);
    if (!hit) return null;
    const ttl = hit.value && hit.value.ok ? ttlOkMs : ttlMissMs;
    if (now() - hit.at > ttl) { entries.delete(key); return null; }
    entries.delete(key);         // refresh recency
    entries.set(key, hit);
    return hit.value;
  }

  function set(key, value) {
    entries.delete(key);
    entries.set(key, { at: now(), value });
    while (entries.size > max) {
      entries.delete(entries.keys().next().value);   // oldest first
    }
  }

  // Collapses concurrent identical lookups onto one call. Without this, a rack
  // of four identical switches fires four simultaneous scrapes of the same
  // vendor page - which on a 2-core box turns a 7s wait into a 30s one.
  //
  // A rejected produce() is NOT cached: a thrown error is a transient failure,
  // and freezing it for the miss TTL would keep a recovered vendor unreachable.
  function once(key, produce) {
    const cached = get(key);
    if (cached) return Promise.resolve(cached);
    if (inflight.has(key)) return inflight.get(key);
    const p = (async () => {
      const value = await produce();
      set(key, value);
      return value;
    })().finally(() => inflight.delete(key));
    inflight.set(key, p);
    return p;
  }

  return {
    get, set, once,
    get size() { return entries.size; },
    clear() { entries.clear(); inflight.clear(); },
  };
}

module.exports = { createLookupCache, DEFAULTS };
