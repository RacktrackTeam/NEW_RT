import { describe, test, expect } from 'vitest';
import { safeRedirect } from './safeRedirect.js';

describe('safeRedirect', () => {
  test('keeps ordinary in-app paths', () => {
    expect(safeRedirect('/scan', '/')).toBe('/scan');
    expect(safeRedirect('/results/RK-00A187E2', '/')).toBe('/results/RK-00A187E2');
    expect(safeRedirect('/results/RK-1?group=g1', '/')).toBe('/results/RK-1?group=g1');
    expect(safeRedirect('/', '/scan')).toBe('/');
  });

  test('rejects the protocol-relative form that made this exploitable', () => {
    // The live vector: sign out, visit a path starting with two slashes, and
    // react-router treats what follows as a host. The user logs in for real
    // and is handed to the attacker.
    expect(safeRedirect('//evil.com', '/scan')).toBe('/scan');
    expect(safeRedirect('//evil.com/login', '/scan')).toBe('/scan');
    expect(safeRedirect('///evil.com', '/scan')).toBe('/scan');
  });

  test('rejects backslash variants, which browsers normalise to slashes', () => {
    expect(safeRedirect('/\\evil.com', '/scan')).toBe('/scan');
    expect(safeRedirect('/\\\\evil.com', '/scan')).toBe('/scan');
  });

  test('rejects absolute URLs and bare hosts', () => {
    expect(safeRedirect('https://evil.com', '/scan')).toBe('/scan');
    expect(safeRedirect('http://evil.com', '/scan')).toBe('/scan');
    expect(safeRedirect('javascript:alert(1)', '/scan')).toBe('/scan');
    expect(safeRedirect('evil.com', '/scan')).toBe('/scan');
  });

  test('rejects leading whitespace used to smuggle a scheme past a naive check', () => {
    expect(safeRedirect('  //evil.com', '/scan')).toBe('/scan');
    expect(safeRedirect('\t/\\evil.com', '/scan')).toBe('/scan');
  });

  test('falls back when there is no usable target', () => {
    expect(safeRedirect(undefined, '/scan')).toBe('/scan');
    expect(safeRedirect(null, '/scan')).toBe('/scan');
    expect(safeRedirect('', '/scan')).toBe('/scan');
    expect(safeRedirect(42, '/scan')).toBe('/scan');
    expect(safeRedirect({ toString: () => '/scan' }, '/')).toBe('/');
  });
});
