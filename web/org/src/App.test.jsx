import { describe, test, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import App, { orgNotActive } from './App.jsx';
import { mockApi, USERS } from './test/render.jsx';

vi.mock('@capacitor/core', () => ({ Capacitor: { isNativePlatform: () => false, getPlatform: () => 'web' } }));

function open(path, user) {
  window.localStorage.clear();
  if (user) window.localStorage.setItem('rt_authUser', JSON.stringify(user));
  window.history.pushState({}, '', path);
  mockApi({ 'GET /api/scans': { scans: [] } }, user);
  return render(<App />);
}

describe('routes and guards', () => {
  test('signed out goes to sign in', async () => {
    open('/history', null);
    expect(await screen.findByRole('button', { name: 'Get started' })).toBeTruthy();
  });
  test('a technician cannot open organization settings or the console', async () => {
    open('/settings', USERS.member);
    expect(await screen.findByRole('link', { name: 'Scan a rack' })).toBeTruthy();
    expect(window.location.pathname).toBe('/');
  });
  test('old addresses land on the new screens', async () => {
    open('/profile', USERS.member);
    expect(await screen.findByRole('button', { name: 'More' })).toBeTruthy();
    expect(window.location.pathname).toBe('/you');
  });
  test('an organization that is not active waits', () => {
    expect(orgNotActive({ role: 'member', organization: { status: 'pending' } })).toBe(true);
    expect(orgNotActive({ role: 'owner' })).toBe(false);
  });
});
