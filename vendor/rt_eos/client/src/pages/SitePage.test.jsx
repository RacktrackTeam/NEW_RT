import { describe, test, expect, beforeEach, vi } from 'vitest';
import { screen, fireEvent, waitFor, within } from '@testing-library/react';
import { renderScreen, mockApi, USERS } from '../test/render.jsx';
import SitePage, { allowed } from './SitePage.jsx';

const SCAN_SITES = {
  ok: true,
  preselect: 2,
  sites: [{ id: 2, name: 'Rack Build Facility', racks: [{ rackId: 'RK-4F6115CF', name: 'R01-A12' }, { rackId: 'RK-9FFD84F1', name: null }] }],
};
const SCANS = { scans: [{ rackId: 'RK-4F6115CF', rackName: 'R01-A12', timestamp: '2026-09-28T10:42:00Z', image: '/api/scans/RK-4F6115CF/files/thumb.jpg' }] };
const MEMBERS = {
  ok: true,
  members: [
    { id: 2, username: 'barry', email: 'barry@eos.local', role: 'site_manager', tenant_id: 2, active: 1, last_scan: null },
    { id: 7, username: 'mandar', email: 'mandar@eos.local', role: 'member', tenant_id: 2, active: 1, last_scan: '2026-09-28 10:42:00' },
    { id: 8, username: 'priya.s', email: 'priya@eos.local', role: 'site_manager', tenant_id: 2, active: 1, last_scan: null },
    { id: 9, username: 'elsewhere', email: 'e@eos.local', role: 'member', tenant_id: 5, active: 1 },
  ],
};

const fallbackApi = (extra = {}, user = USERS.site_manager) => mockApi({
  'GET /api/scan-sites': SCAN_SITES,
  'GET /api/scans': SCANS,
  'GET /api/orgs/1/members': MEMBERS,
  ...extra,
}, user);

const bodyOf = (api, method, path) => {
  const c = api.calls.find((x) => x.method === method && x.path === path);
  return c ? JSON.parse(c.body) : null;
};

beforeEach(() => {
  Object.defineProperty(navigator, 'clipboard', { value: { writeText: vi.fn(async () => {}) }, configurable: true });
});

describe('SitePage', () => {
  test('without the overview route it builds the site from the scan list and the members', async () => {
    fallbackApi();
    renderScreen(<SitePage />, { path: '/site', user: USERS.site_manager });
    expect(await screen.findByRole('heading', { name: 'Rack Build Facility' })).toBeTruthy();
    expect(screen.getByText('EOS Pilot. 3 people, 2 racks.')).toBeTruthy();
    const rack = screen.getByRole('link', { name: /R01-A12/ });
    expect(rack.getAttribute('href')).toBe('/results/RK-4F6115CF');
    expect(screen.getByText('mandar')).toBeTruthy();
    expect(screen.getByText(/^Technician, Last scan/)).toBeTruthy();
    expect(screen.queryByText('elsewhere')).toBeNull();
  });

  test('with the overview it draws racks only the sheet knows and the Check word', async () => {
    mockApi({
      'GET /api/sites/2/overview': {
        site: { id: 2, name: 'Rack Build Facility' },
        racks: [
          { rackLabel: 'R01-A12', scanId: 'RK-1', thumbUrl: '/api/scans/RK-1/files/thumb.jpg', takenAt: '2026-09-28T10:42:00Z', status: 'check', problems: 3 },
          { rackLabel: 'R03-A01', scanId: null },
        ],
        people: [{ id: 7, name: 'mandar', username: 'mandar', role: 'member' }, { id: 12, name: 'j.okafor', username: 'j.okafor', role: 'member', pending: true }],
      },
      'GET /api/orgs/1/members': MEMBERS,
    }, USERS.site_manager);
    renderScreen(<SitePage />, { path: '/site/2', pattern: '/site/:siteId', user: USERS.site_manager });
    expect(await screen.findByText('R03-A01')).toBeTruthy();
    expect(screen.getByText('Not scanned')).toBeTruthy();
    expect(screen.getByText('From the sheet')).toBeTruthy();
    expect(screen.getByText('Check')).toBeTruthy();
    expect(screen.getByText('3 need a look')).toBeTruthy();
    expect(screen.getByText('Technician, Invite sent, not joined yet')).toBeTruthy();
    // Someone who has not joined has no account to manage yet.
    expect(screen.queryByRole('button', { name: 'Options for j.okafor' })).toBeNull();
  });

  test('a site manager invites a technician and gets a link to copy', async () => {
    const api = fallbackApi({ 'POST /api/sites/2/invites': { invite: { code: 'abc', path: '/invite/abc' } } });
    renderScreen(<SitePage />, { path: '/site', user: USERS.site_manager });
    await screen.findByRole('heading', { name: 'Rack Build Facility' });
    fireEvent.click(screen.getAllByRole('button', { name: 'Invite a technician' })[0]);
    const sheet = await screen.findByRole('dialog');
    // A site manager can only invite technicians, so there is no role to pick.
    expect(within(sheet).queryByLabelText('Role')).toBeNull();
    fireEvent.click(within(sheet).getByRole('button', { name: 'Create invite link' }));
    expect(within(sheet).getByText('Enter their email')).toBeTruthy();
    fireEvent.change(within(sheet).getByLabelText(/Email/), { target: { value: 'New.Tech@eos.local' } });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Create invite link' }));
    const link = await within(sheet).findByLabelText('Invite link');
    expect(link.value).toMatch(/\/invite\/abc$/);
    expect(bodyOf(api, 'POST', '/api/sites/2/invites')).toEqual({ email: 'new.tech@eos.local', role: 'member' });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Copy link' }));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith(link.value));
  });

  test('an admin makes the account now, choosing the role', async () => {
    const api = fallbackApi({ 'POST /api/sites/2/members': { ok: true, member: { id: 20 } } }, USERS.org_admin);
    renderScreen(<SitePage />, { path: '/site/2', pattern: '/site/:siteId', user: USERS.org_admin });
    await screen.findByRole('heading', { name: 'Rack Build Facility' });
    fireEvent.click(screen.getAllByRole('button', { name: 'Invite a technician' })[0]);
    const sheet = await screen.findByRole('dialog');
    fireEvent.click(within(sheet).getByRole('button', { name: 'Or create the account now' }));
    fireEvent.change(within(sheet).getByLabelText(/Username/), { target: { value: 'j.okafor' } });
    fireEvent.change(within(sheet).getByLabelText(/Email/), { target: { value: 'j@eos.local' } });
    fireEvent.change(within(sheet).getByLabelText(/Temporary password/), { target: { value: 'Temp@12345' } });
    fireEvent.change(within(sheet).getByLabelText('Role'), { target: { value: 'site_manager' } });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Create account' }));
    await waitFor(() => expect(bodyOf(api, 'POST', '/api/sites/2/members')).toEqual({ username: 'j.okafor', email: 'j@eos.local', password: 'Temp@12345', role: 'site_manager' }));
  });

  test('a site manager manages technicians only, and only their password and state', async () => {
    const api = fallbackApi({ 'PATCH /api/orgs/1/members/7': { ok: true, member: {} }, 'DELETE /api/orgs/1/members/7': { ok: true } });
    renderScreen(<SitePage />, { path: '/site', user: USERS.site_manager });
    await screen.findByText('mandar');
    // Not themselves, not another site manager.
    expect(screen.queryByRole('button', { name: 'Options for barry' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Options for priya.s' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Options for mandar' }));
    let sheet = await screen.findByRole('dialog');
    fireEvent.click(within(sheet).getByRole('button', { name: /Edit/ }));
    expect(within(sheet).queryByLabelText(/Username/)).toBeNull();
    expect(within(sheet).queryByLabelText('Role')).toBeNull();
    fireEvent.change(within(sheet).getByLabelText('New password'), { target: { value: 'Fresh@12345' } });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(bodyOf(api, 'PATCH', '/api/orgs/1/members/7')).toEqual({ password: 'Fresh@12345' }));

    fireEvent.click(await screen.findByRole('button', { name: 'Options for mandar' }));
    sheet = await screen.findByRole('dialog');
    fireEvent.click(within(sheet).getByRole('button', { name: /Remove/ }));
    expect(within(sheet).getByText(/Remove mandar\? The account is deleted/)).toBeTruthy();
    fireEvent.click(within(sheet).getByRole('button', { name: 'Remove mandar' }));
    await waitFor(() => expect(api.calls.some((c) => c.method === 'DELETE' && c.path === '/api/orgs/1/members/7')).toBe(true));
  });

  test('deactivate sends active false', async () => {
    const api = fallbackApi({ 'PATCH /api/orgs/1/members/7': { ok: true, member: {} } });
    renderScreen(<SitePage />, { path: '/site', user: USERS.site_manager });
    fireEvent.click(await screen.findByRole('button', { name: 'Options for mandar' }));
    const sheet = await screen.findByRole('dialog');
    fireEvent.click(within(sheet).getByRole('button', { name: /Deactivate/ }));
    await waitFor(() => expect(bodyOf(api, 'PATCH', '/api/orgs/1/members/7')).toEqual({ active: false }));
  });

  test('the owner without a site picks one first', async () => {
    mockApi({ 'GET /api/scan-sites': { sites: [] }, 'GET /api/setup/state': { tenants: [{ id: 2, name: 'Rack Build Facility' }, { id: 3, name: 'Hall 2' }] } }, USERS.owner);
    renderScreen(<SitePage />, { path: '/site', user: USERS.owner });
    const pick = await screen.findByLabelText('Site');
    expect(screen.getByText('Pick a site to see its racks and people.')).toBeTruthy();
    expect(pick.querySelectorAll('option').length).toBe(3);
  });

  test('a server error says what to do', async () => {
    mockApi({ 'GET /api/sites/2/overview': { status: 500, body: { error: 'Something broke.' } } }, USERS.site_manager);
    renderScreen(<SitePage />, { path: '/site', user: USERS.site_manager });
    expect((await screen.findByRole('alert')).textContent).toBe('The site could not be loaded. Something broke.');
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });

  test('allowed follows the server rules', () => {
    expect(allowed(USERS.org_admin, { id: 50, role: 'org_admin' }).edit).toBe(false);
    expect(allowed(USERS.owner, { id: 50, role: 'org_admin' }).remove).toBe(true);
    expect(allowed(USERS.owner, { id: 50, role: 'org_admin' }).role).toBe(false);
    expect(allowed(USERS.org_admin, { id: 3, role: 'org_admin' }).edit).toBe(false);
    expect(allowed(USERS.org_admin, { id: 8, role: 'site_manager' }).role).toBe(true);
  });
});
