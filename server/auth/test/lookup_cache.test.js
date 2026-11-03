'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { createLookupCache } = require('../lib/lookup_cache');

// A clock we control, so TTL expiry is tested without sleeping.
function fakeClock(start = 1_000_000) {
  let t = start;
  return { now: () => t, advance: (ms) => { t += ms; } };
}

test('a second lookup is served from cache, not re-produced', async () => {
  const cache = createLookupCache();
  let calls = 0;
  const produce = async () => { calls++; return { ok: true, model: 'X' }; };

  const a = await cache.once('k', produce);
  const b = await cache.once('k', produce);

  assert.strictEqual(calls, 1, 'produce should run once');
  assert.deepStrictEqual(a, b);
});

test('concurrent identical lookups collapse onto one call', async () => {
  const cache = createLookupCache();
  let calls = 0;
  const produce = async () => {
    calls++;
    await new Promise(r => setTimeout(r, 20));
    return { ok: true };
  };

  // Four identical switches in a rack, all mounting at once.
  const all = await Promise.all([1, 2, 3, 4].map(() => cache.once('same', produce)));

  assert.strictEqual(calls, 1, 'four callers, one Python spawn');
  assert.strictEqual(all.length, 4);
  all.forEach(v => assert.strictEqual(v.ok, true));
});

test('different keys do not share an answer', async () => {
  const cache = createLookupCache();
  const a = await cache.once('vendor-a', async () => ({ ok: true, who: 'a' }));
  const b = await cache.once('vendor-b', async () => ({ ok: true, who: 'b' }));
  assert.strictEqual(a.who, 'a');
  assert.strictEqual(b.who, 'b');
});

test('a hit expires after the success TTL', async () => {
  const clock = fakeClock();
  const cache = createLookupCache({ now: clock.now, ttlOkMs: 1000, ttlMissMs: 100 });
  let calls = 0;
  const produce = async () => { calls++; return { ok: true }; };

  await cache.once('k', produce);
  clock.advance(999);
  await cache.once('k', produce);
  assert.strictEqual(calls, 1, 'still fresh');

  clock.advance(2);
  await cache.once('k', produce);
  assert.strictEqual(calls, 2, 'expired, re-produced');
});

test('a miss expires much sooner than a hit', async () => {
  const clock = fakeClock();
  const cache = createLookupCache({ now: clock.now, ttlOkMs: 100_000, ttlMissMs: 500 });
  let calls = 0;
  const produce = async () => { calls++; return { ok: false }; };

  await cache.once('k', produce);
  clock.advance(501);
  await cache.once('k', produce);

  assert.strictEqual(calls, 2,
    'a "couldn\'t find it" must not occupy the slot for the full success TTL');
});

test('a thrown produce is not cached', async () => {
  const cache = createLookupCache();
  let calls = 0;
  const boom = async () => { calls++; throw new Error('vendor site down'); };

  await assert.rejects(() => cache.once('k', boom));
  await assert.rejects(() => cache.once('k', boom));

  assert.strictEqual(calls, 2, 'a transient failure must be retryable immediately');
});

test('evicts least-recently-used past the ceiling', async () => {
  const cache = createLookupCache({ max: 3 });
  for (const k of ['a', 'b', 'c']) await cache.once(k, async () => ({ ok: true, k }));

  cache.get('a');                                        // 'a' is now most recent
  await cache.once('d', async () => ({ ok: true, k: 'd' }));

  assert.strictEqual(cache.size, 3);
  assert.strictEqual(cache.get('b'), null, 'b was least recently used');
  assert.ok(cache.get('a'), 'a survived because it was touched');
  assert.ok(cache.get('d'), 'd is the newest');
});
