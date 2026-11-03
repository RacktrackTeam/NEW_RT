import { describe, test, expect, vi, afterEach } from 'vitest';
import { screen, fireEvent, waitFor, within } from '@testing-library/react';
import HomePage from './HomePage.jsx';
import HistoryPage, { shapeHistory } from './HistoryPage.jsx';
import YouPage, { placesFor, menuFor, datasetValue } from './YouPage.jsx';
import HelpPage from './HelpPage.jsx';
import ContactPage from './ContactPage.jsx';
import ConsolePage, { eventLine } from './ConsolePage.jsx';
import { mockApi, renderScreen, USERS } from '../test/render.jsx';

const today = new Date().toISOString();
const SCANS = { scans: [
  { rackId: 'RK-1', rackName: 'R01-A12', timestamp: today, image: '/api/scans/RK-1/files/thumb.jpg', qualityWarning: 'blurry' },
  { rackId: 'RK-2', rackName: 'R02-B07', timestamp: '2026-01-02T10:00:00Z', image: '/api/scans/RK-2/files/thumb.jpg' },
] };

afterEach(() => { window.matchMedia = undefined; });


describe('Home', () => {
  test('the logo and organization (no site line), a photo of a whole rack, a title, a line, and one button', async () => {
    mockApi({ 'GET /api/home': { role: 'member', site: { id: 2, name: 'Rack Build Facility' }, recent: [{ scanId: 'RK-1', rackLabel: 'B12' }], worklist: { count: 27 } } }, USERS.member);
    renderScreen(<HomePage />, { path: '/', user: USERS.member });
    expect(screen.getByRole('img', { name: 'RackTrack' }).getAttribute('src')).toBe('/logo.jpg');
    expect(screen.getAllByText('EOS Pilot').length).toBeGreaterThan(0);
    expect(screen.queryByText('Rack Build Facility')).toBeNull();
    expect(screen.getByRole('img', { name: 'A rack of switches and patch panels, fully cabled' }).getAttribute('src')).toBe('/home-rack.jpg');
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Check a rack in one photo');
    // Scan a rack, and Map two racks (shown on a laptop only, by CSS).
    expect(screen.getAllByRole('link').map((l) => [l.textContent, l.getAttribute('href')])).toEqual([['Scan a rack', '/scan'], ['Map two racks', '/map']]);
    expect(screen.queryByText('B12')).toBeNull();
    expect(document.querySelectorAll('main img')).toHaveLength(2);
  });

  test('an admin sees the organization only, no site text under it', () => {
    mockApi({ 'GET /api/home': { site: null, recent: [] } }, USERS.org_admin);
    renderScreen(<HomePage />, { path: '/', user: USERS.org_admin });
    expect(screen.getAllByText('EOS Pilot').length).toBeGreaterThan(0);
    expect(screen.queryByText('Every site')).toBeNull();
  });
});

describe('History', () => {
  test('groups by day, searches, and opens a rack', async () => {
    mockApi({ 'GET /api/scans': SCANS }, USERS.member);
    renderScreen(<HistoryPage />, { path: '/history' });
    expect(await screen.findByText('Today')).toBeTruthy();
    expect(screen.getByRole('link', { name: /R02-B07/ }).getAttribute('href')).toBe('/results/RK-2');
    fireEvent.change(screen.getByLabelText('Search by rack name'), { target: { value: 'b07' } });
    expect(screen.queryByText('R01-A12')).toBeNull();
    fireEvent.change(screen.getByLabelText('Search by rack name'), { target: { value: 'zzz' } });
    expect(screen.getByText('No racks match.')).toBeTruthy();
  });
  test('two racks mapped together show once, with both pictures, and open the two-rack page', async () => {
    const at = today;
    const scans = { scans: [
      { rackId: 'RK-A', rackName: 'B12', timestamp: today, image: '/api/scans/RK-A/files/thumb.jpg', pair: { with: 'RK-Z', side: 'a', at } },
      { rackId: 'RK-Z', rackName: 'B07', timestamp: today, image: '/api/scans/RK-Z/files/thumb.jpg', pair: { with: 'RK-A', side: 'z', at } },
      { rackId: 'RK-3', rackName: 'C01', timestamp: today, image: '/api/scans/RK-3/files/thumb.jpg', pair: null },
    ] };
    mockApi({ 'GET /api/scans': scans }, USERS.member);
    renderScreen(<HistoryPage />, { path: '/history' });
    const link = await screen.findByRole('link', { name: /B12 and B07/ });
    expect(link.getAttribute('href')).toBe('/map/RK-A/RK-Z');
    expect(link.querySelectorAll('img')).toHaveLength(2);
    expect(screen.getAllByRole('link')).toHaveLength(2);
    expect(screen.getByRole('link', { name: /C01/ }).getAttribute('href')).toBe('/results/RK-3');
    fireEvent.change(screen.getByLabelText('Search by rack name'), { target: { value: 'b07' } });
    expect(screen.getByRole('link', { name: /B12 and B07/ })).toBeTruthy();
    expect(screen.queryByText('C01')).toBeNull();
    fireEvent.change(screen.getByLabelText('Search by rack name'), { target: { value: 'b12' } });
    expect(screen.getByRole('link', { name: /B12 and B07/ })).toBeTruthy();
    expect(shapeHistory(scans.scans).map((s) => (s.kind === 'pair' ? 'pair' : s.rackId)).sort()).toEqual(['RK-3', 'pair']);
    expect(shapeHistory([scans.scans[1]]).map((s) => s.rackId)).toEqual(['RK-Z']);
  });
  test('empty history offers the scan', async () => {
    mockApi({ 'GET /api/scans': { scans: [] } }, USERS.member);
    renderScreen(<HistoryPage />, { path: '/history' });
    expect(await screen.findByText('No scans yet. Every rack you scan is kept here.')).toBeTruthy();
  });
  test('order and age', () => {
    const now = Date.parse(today);
    expect(shapeHistory(SCANS.scans, { sort: 'old' }).map((s) => s.rackId)).toEqual(['RK-2', 'RK-1']);
    expect(shapeHistory(SCANS.scans, { age: 7, now }).map((s) => s.rackId)).toEqual(['RK-1']);
    expect(shapeHistory(SCANS.scans, { sort: 'name' }).map((s) => s.rackId)).toEqual(['RK-1', 'RK-2']);
  });
});

describe('You', () => {
  test('each role gets its own rows in plain groups, and the menu keeps only the account actions', () => {
    const labels = (role) => placesFor(role).flat().map((i) => i[0]);
    expect(labels('member')).toEqual(['History', 'Help', 'Contact support']);
    expect(labels('site_manager')).toEqual(['History', 'Excel dataset', 'Site and people', 'Help', 'Contact support']);
    expect(labels('site_manager')).not.toContain('Organization settings');
    expect(labels('org_admin')).toContain('Organization settings');
    expect(labels('owner')).toContain('Console');
    expect(menuFor().map((m) => m[0])).toEqual(['Change picture', 'Change password', 'Sign out']);
    expect(datasetValue({ import: null })).toBe('Not imported');
    expect(datasetValue({ import: { id: 1 }, counts: { cables: 5 } })).toBe('5 cables');
    expect(datasetValue(null)).toBeNull();
  });
  test('a profile header, rows with no section titles, and the Excel dataset row with its cable count', async () => {
    mockApi({ 'GET /api/matrix': { import: { id: 'a1', cableCount: 384 }, counts: { cables: 384 } } }, USERS.site_manager);
    renderScreen(<YouPage />, { path: '/you', user: USERS.site_manager });
    expect(screen.getAllByRole('heading', { level: 1 }).map((h) => h.textContent)).toEqual(['You']);
    expect(screen.getByText('Barry')).toBeTruthy();
    expect(screen.getByText('Site manager, EOS Pilot, Rack Build Facility')).toBeTruthy();
    for (const word of ['Work', 'Manage']) expect(screen.queryByText(word)).toBeNull();
    expect(screen.queryByRole('heading', { level: 2 })).toBeNull();
    const dataset = screen.getByRole('link', { name: /Excel dataset/ });
    expect(dataset.getAttribute('href')).toBe('/matrix');
    await waitFor(() => expect(dataset.textContent).toContain('384 cables'));
    expect(screen.getByRole('link', { name: /Site and people/ }).getAttribute('href')).toBe('/site');
    expect(screen.getByRole('link', { name: /Contact support/ }).getAttribute('href')).toBe('/contact');
    expect(screen.queryByText(/Worklist/)).toBeNull();
    expect(document.querySelector('svg:not(.appIcon)')).toBeNull();
  });
  test('with no dataset the row says Not imported', async () => {
    const api = mockApi({ 'GET /api/matrix': { import: null, counts: { cables: 0 } } }, USERS.site_manager);
    renderScreen(<YouPage />, { path: '/you', user: USERS.site_manager });
    await waitFor(() => expect(screen.getByRole('link', { name: /Excel dataset/ }).textContent).toContain('Not imported'));
    expect(api.calls.some((c) => c.path === '/api/matrix')).toBe(true);
  });
  test('a technician sees no dataset row and nothing is fetched for it', () => {
    const api = mockApi({}, USERS.member);
    renderScreen(<YouPage />, { path: '/you', user: USERS.member });
    expect(screen.queryByRole('link', { name: /Excel dataset/ })).toBeNull();
    expect(api.calls.some((c) => c.path === '/api/matrix')).toBe(false);
  });
  test('the three dots open the account actions; Sign out asks first', async () => {
    mockApi({ 'POST /api/auth/logout': { ok: true } }, USERS.site_manager);
    renderScreen(<YouPage />, { path: '/you', user: USERS.site_manager, extra: [['/login', <p key="l">Sign in screen</p>]] });
    expect(screen.queryByText('Change password')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    const menu = screen.getByRole('dialog', { name: 'More' });
    expect(within(menu).getAllByRole('button').map((m) => m.textContent)).toEqual(['Change picture', 'Change password', 'Sign out']);
    fireEvent.click(within(menu).getByRole('button', { name: 'Sign out' }));
    expect(screen.queryByRole('dialog', { name: 'More' })).toBeNull();
    const dialog = screen.getByRole('dialog', { name: 'Sign out of RackTrack?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Sign out' }));
    expect(await screen.findByText('Sign in screen')).toBeTruthy();
  });
  test('the menu closes on Escape', () => {
    mockApi({}, USERS.member);
    renderScreen(<YouPage />, { path: '/you', user: USERS.member });
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    expect(screen.getByRole('dialog', { name: 'More' })).toBeTruthy();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'More' })).toBeNull();
  });
  test('the picture sheet uploads or removes a real photo only', async () => {
    const withPhoto = { ...USERS.member, avatarPhoto: 'data:image/jpeg;base64,AAAA' };
    const api = mockApi({ 'GET /api/scans': SCANS, 'POST /api/auth/avatar-photo': { user: USERS.member } }, withPhoto);
    renderScreen(<YouPage />, { path: '/you', user: withPhoto });
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    fireEvent.click(within(screen.getByRole('dialog', { name: 'More' })).getByRole('button', { name: 'Change picture' }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove photo' }));
    await waitFor(() => expect(api.calls.some((c) => c.path === '/api/auth/avatar-photo' && JSON.parse(c.body).photo === null)).toBe(true));
    expect(document.querySelector('svg:not(.appIcon)')).toBeNull();
  });
});

describe('Help and Contact', () => {
  test('help is folded questions that open to short lines, with the way to support', () => {
    mockApi({}, USERS.member);
    renderScreen(<HelpPage />, { path: '/help' });
    const q = screen.getByRole('button', { name: 'Checking against your Excel' });
    expect(q.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(q);
    expect(screen.getByText(/Every scan is then checked against it/)).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Contact support' }).getAttribute('href')).toBe('/contact');
  });
  test('contact sends the message', async () => {
    const api = mockApi({ 'POST /api/support/contact': { ok: true } }, USERS.member);
    renderScreen(<ContactPage />, { path: '/contact' });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    expect(screen.getByRole('alert').textContent).toBe('Write a few words about what happened.');
    fireEvent.change(screen.getByLabelText(/Message/), { target: { value: 'The label check shows a wrong port.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    expect(await screen.findByText(/Your message is with support/)).toBeTruthy();
    const body = JSON.parse(api.calls.find((c) => c.path === '/api/support/contact').body);
    expect(body.message).toBe('The label check shows a wrong port.');
    expect(typeof body.context).toBe('string');
  });
});

describe('Console', () => {
  const DASH = {
    totals: { scansToday: 42, scansFail: 3, activeToday: 9 },
    feedback: { right: 90, wrong: 10, accuracy: 90 },
    recent: [{ ts: today, username: 'mandar', action: 'scan.create', target_id: 'RK-1', status: 'ok', org: 'EOS Pilot' }, { ts: today, username: 'priya.s', action: 'scan.create', target_id: 'RK-2', status: 'fail', org: 'EOS Pilot' }],
    topUsers: [{ username: 'mandar', scans: 12 }], byOrg: [], bySite: [], errors: [],
  };
  test('today in numbers, real accuracy only, and live events', async () => {
    mockApi({ 'GET /api/admin/dashboard': DASH }, USERS.owner);
    renderScreen(<ConsolePage />, { path: '/console', user: USERS.owner });
    expect(await screen.findByText('42')).toBeTruthy();
    expect(screen.getByText('90%')).toBeTruthy();
    expect(screen.getAllByText('Not measured yet').length).toBe(4);
    expect(screen.getByText('mandar read RK-1')).toBeTruthy();
    expect(screen.getByText('Failed')).toBeTruthy();
  });
  test('the log lists lines and clears after asking', async () => {
    const api = mockApi({ 'GET /api/logs': { total: 1, logs: [{ id: 1, ts: today, level_label: 'error', msg: 'pipeline failed' }] }, 'POST /api/logs/clear': { removed: 1 } }, USERS.owner);
    renderScreen(<ConsolePage />, { path: '/console', user: USERS.owner });
    fireEvent.click(screen.getByRole('tab', { name: 'Log' }));
    expect(await screen.findByText('pipeline failed')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Clear the log' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Clear the log' }));
    await waitFor(() => expect(api.calls.some((c) => c.path === '/api/logs/clear')).toBe(true));
  });
  test('event lines are plain words', () => {
    expect(eventLine({ username: 'barry', action: 'auth.login' })).toBe('barry signed in');
  });
});

vi.mock('@capacitor/core', () => ({ Capacitor: { isNativePlatform: () => false, getPlatform: () => 'web' } }));
