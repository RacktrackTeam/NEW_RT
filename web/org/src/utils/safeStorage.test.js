// Regression tests for the storage guard.
//
// The bug these exist for: an unguarded localStorage call inside a React
// effect white-screened the entire product on any browser with site data
// blocked, and again on any device whose origin quota was full. Both failure
// modes are invisible in normal development - localStorage works fine on a
// developer's machine - so nothing but a test that *makes* storage throw can
// keep the guard honest.

import { describe, test, expect, afterEach } from 'vitest';
import { getItem, setItem, removeItem, getJSON } from './safeStorage';

const realLocal = window.localStorage;
const realSession = window.sessionStorage;

/** Install a stand-in for window.localStorage / sessionStorage. */
function install(fake, which = 'localStorage') {
  Object.defineProperty(window, which, { value: fake, configurable: true, writable: true });
}

/** Storage that throws on every operation - "Block All Cookies" in Safari. */
function blockedStorage() {
  const err = new DOMException('The operation is insecure.', 'SecurityError');
  return {
    getItem() { throw err; },
    setItem() { throw err; },
    removeItem() { throw err; },
  };
}

/**
 * Storage with a byte budget, like a real origin near its quota. Writes over
 * `budget` characters throw QuotaExceededError; everything else persists.
 */
function quotaStorage(budget) {
  const data = new Map();
  return {
    data,
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem(k, v) {
      if (String(v).length > budget) {
        throw new DOMException('quota exceeded', 'QuotaExceededError');
      }
      data.set(k, String(v));
    },
    removeItem: (k) => void data.delete(k),
  };
}

afterEach(() => {
  install(realLocal, 'localStorage');
  install(realSession, 'sessionStorage');
  realLocal.clear();
});

describe('a storage that throws on every call', () => {
  test('every entry point returns instead of throwing', () => {
    install(blockedStorage());

    // The whole contract in one place: nothing here may escape to a caller,
    // because every one of these used to run inside an effect.
    expect(() => getItem('k')).not.toThrow();
    expect(getItem('k')).toBe(null);
    expect(setItem('k', 'v')).toBe(false);
    expect(removeItem('k')).toBe(false);
    expect(getJSON('k', { fallback: true })).toEqual({ fallback: true });
  });

  test('a blocked sessionStorage is guarded on the same terms', () => {
    // The `storage` argument is a separate code path through pick(); a guard
    // that only covered localStorage would still crash the session-scoped
    // callers.
    install(blockedStorage(), 'sessionStorage');
    expect(getItem('k', 'session')).toBe(null);
    expect(setItem('k', 'v', 'session')).toBe(false);
  });
});

describe('reads', () => {
  test('getItem/setItem/removeItem round-trip through real storage', () => {
    expect(setItem('k', 'v')).toBe(true);
    expect(getItem('k')).toBe('v');
    expect(removeItem('k')).toBe(true);
    expect(getItem('k')).toBe(null);
  });

  test('getJSON returns the fallback for malformed JSON rather than throwing', () => {
    // Half-written values are real: a tab killed mid-write, or an older build
    // that stored a bare string under the same key.
    window.localStorage.setItem('k', '{"a":1,');
    expect(getJSON('k', 'fallback')).toBe('fallback');
  });

  test('getJSON distinguishes a missing key from a stored null', () => {
    expect(getJSON('absent', 'fallback')).toBe('fallback');
    setItem('present', 'null');
    expect(getJSON('present', 'fallback')).toBe(null);
  });

  test('getJSON parses a stored JSON value', () => {
    setItem('k', JSON.stringify({ a: [1, 2], b: 'x' }));
    expect(getJSON('k')).toEqual({ a: [1, 2], b: 'x' });
  });
});

describe('a full quota', () => {
  test('setItem reports the failure and leaves the stored value intact', () => {
    const store = quotaStorage(12);
    install(store);
    store.data.set('k', 'earlier');
    expect(setItem('k', 'x'.repeat(100))).toBe(false);
    expect(store.data.get('k')).toBe('earlier');
  });
});
