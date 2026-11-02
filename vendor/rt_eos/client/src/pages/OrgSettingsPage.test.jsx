import { describe, test, expect } from 'vitest';
import { screen, fireEvent, waitFor, within } from '@testing-library/react';
import { renderScreen, mockApi, USERS } from '../test/render.jsx';
import OrgSettingsPage from './OrgSettingsPage.jsx';

const SNAP = {
  tenant: { id: 2, name: 'Rack Build Facility', organization_id: 1 },
  datacentre: { address: '12 Harbour Road, Rotterdam', timezone: 'Europe/Amsterdam' },
  spaces: [], approver: { user_id: 2, username: 'barry' }, rules: { accepted_at: '2026-09-20' }, profile: {}, completeness: null,
};
const PROFILE = { profile: { name: 'EOS Pilot', timezone: 'Europe/London', country: 'GB', primary_contact_name: 'Barry', primary_contact_email: 'barry@eos.local' } };

const base = (extra = {}) => ({
  'GET /api/setup/state': { tenants: [{ id: 2, name: 'Rack Build Facility' }] },
  'GET /api/setup/2': SNAP,
  'GET /api/setup/org/1/profile': PROFILE,
  'PUT /api/setup/org/1/profile': (url, init) => ({ profile: { ...PROFILE.profile, ...JSON.parse(init.body) } }),
  ...extra,
});
const OWNER = {
  'GET /api/dashboard/owner': { organizations: [{ id: 1, name: 'EOS Pilot', status: 'active', sites: 1, users: 4 }, { id: 4, name: 'Acme', status: 'pending', sites: 0, users: 1 }] },
  'GET /api/orgs/pending': { pending: [{ id: 4, name: 'Acme', admin_username: 'acme.admin', admin_email: 'admin@acme.test' }] },
};

const bodies = (api, method, path) => api.calls.filter((c) => c.method === method && c.path === path).map((c) => JSON.parse(c.body));


async function openFolds() {
  await screen.findAllByRole('button', { name: /^Organization/ });
  for (const b of screen.queryAllByRole('button', { expanded: false })) {
    if (/^Organization/.test(b.textContent)) fireEvent.click(b);
  }
}

describe('OrgSettingsPage', () => {
  test('an org admin sees the name read-only, a site list, and the plain line when pilot data is not built', async () => {
    const api = mockApi(base(), USERS.org_admin);
    renderScreen(<OrgSettingsPage />, { path: '/settings', user: USERS.org_admin });
    await openFolds();
    const name = await screen.findByLabelText('Name');
    await waitFor(() => expect(name.value).toBe('EOS Pilot'));
    expect(name.readOnly).toBe(true);
    expect(await screen.findByText('Pilot data settings are not available on this server yet.')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Edit' }).getAttribute('href')).toBe('/setup?step=sites');
    expect(screen.getByRole('link', { name: /Rack Build Facility/ }).getAttribute('href')).toBe('/site/2');
    expect(screen.queryByRole('region', { name: 'Organizations' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Open first-run setup' }).getAttribute('href')).toBe('/setup');

    fireEvent.change(screen.getByLabelText(/Time zone/), { target: { value: 'Europe/Paris' } });
    await waitFor(() => expect(bodies(api, 'PUT', '/api/setup/org/1/profile')).toContainEqual({ timezone: 'Europe/Paris' }));
    const mail = screen.getByLabelText('Primary contact email');
    fireEvent.change(mail, { target: { value: 'not an email' } });
    fireEvent.blur(mail);
    expect(screen.getByText('Not a valid email address')).toBeTruthy();
    fireEvent.change(mail, { target: { value: 'ops@eos.local' } });
    fireEvent.blur(mail);
    await waitFor(() => expect(bodies(api, 'PUT', '/api/setup/org/1/profile')).toContainEqual({ primary_contact_email: 'ops@eos.local' }));
  });

  test('pilot data: the switch and the days save the whole setting', async () => {
    const api = mockApi(base({
      'GET /api/orgs/1/data-settings': { neverTrain: true, retentionDays: 90 },
      'PUT /api/orgs/1/data-settings': { ok: true },
    }), USERS.org_admin);
    renderScreen(<OrgSettingsPage />, { path: '/settings', user: USERS.org_admin });
    const [sw, online] = await screen.findAllByRole('switch');
    expect(sw.getAttribute('aria-checked')).toBe('true');
    expect(online.getAttribute('aria-checked')).toBe('true');
    expect(screen.getByText('Photos and readings from EOS Pilot are never used to train models.')).toBeTruthy();
    expect(screen.getByText('Look up specifications online')).toBeTruthy();
    fireEvent.click(sw);
    await waitFor(() => expect(bodies(api, 'PUT', '/api/orgs/1/data-settings')).toContainEqual({ neverTrain: false, retentionDays: 90, onlineLookups: true }));
    const days = screen.getByLabelText(/Keep pilot data for/);
    expect(days.value).toBe('90');
    fireEvent.change(days, { target: { value: '0' } });
    fireEvent.blur(days);
    expect(screen.getByText(/Enter whole days from 1 to 3650/)).toBeTruthy();
    fireEvent.change(days, { target: { value: '30' } });
    fireEvent.blur(days);
    await waitFor(() => expect(bodies(api, 'PUT', '/api/orgs/1/data-settings')).toContainEqual({ neverTrain: false, retentionDays: 30, onlineLookups: true }));
    fireEvent.change(days, { target: { value: '' } });
    fireEvent.blur(days);
    await waitFor(() => expect(bodies(api, 'PUT', '/api/orgs/1/data-settings')).toContainEqual({ neverTrain: false, retentionDays: null, onlineLookups: true }));
    // Online lookups off: nothing leaves the server for datasheets.
    fireEvent.click(online);
    await waitFor(() => expect(bodies(api, 'PUT', '/api/orgs/1/data-settings')).toContainEqual({ neverTrain: false, retentionDays: null, onlineLookups: false }));
  });

  test('delete all pilot data needs the exact name typed', async () => {
    const api = mockApi(base({
      'GET /api/orgs/1/data-settings': { neverTrain: false, retentionDays: null },
      'POST /api/orgs/1/delete-pilot-data': { deletedScans: 46, deletedFiles: 1284 },
    }), USERS.org_admin);
    renderScreen(<OrgSettingsPage />, { path: '/settings', user: USERS.org_admin });
    fireEvent.click(await screen.findByRole('button', { name: 'Delete all pilot data' }));
    const sheet = await screen.findByRole('dialog', { name: 'Delete all pilot data?' });
    const go = within(sheet).getByRole('button', { name: 'Delete everything' });
    expect(go.disabled).toBe(true);
    fireEvent.change(within(sheet).getByLabelText(/Type EOS Pilot to confirm/), { target: { value: 'eos pilot' } });
    expect(go.disabled).toBe(true);
    fireEvent.change(within(sheet).getByLabelText(/Type EOS Pilot to confirm/), { target: { value: 'EOS Pilot' } });
    expect(go.disabled).toBe(false);
    fireEvent.click(go);
    expect(await screen.findByText('Deleted 46 scans and 1284 files.')).toBeTruthy();
    expect(bodies(api, 'POST', '/api/orgs/1/delete-pilot-data')).toEqual([{ confirm: 'EOS Pilot' }]);
  });

  test('add a site posts its name', async () => {
    const api = mockApi(base({
      'POST /api/orgs/1/sites': { site: { id: 3, name: 'Hall 2' } },
      'GET /api/setup/3': { ...SNAP, tenant: { id: 3, name: 'Hall 2', organization_id: 1 }, datacentre: {} },
    }), USERS.org_admin);
    renderScreen(<OrgSettingsPage />, { path: '/settings', user: USERS.org_admin });
    await openFolds();
    fireEvent.click(await screen.findByRole('button', { name: 'Add a site' }));
    const sheet = await screen.findByRole('dialog', { name: 'Add a site' });
    fireEvent.change(within(sheet).getByLabelText(/Name/), { target: { value: 'Hall 2' } });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Add site' }));
    await waitFor(() => expect(bodies(api, 'POST', '/api/orgs/1/sites')).toEqual([{ name: 'Hall 2' }]));
    expect(await screen.findByRole('link', { name: /Hall 2/ })).toBeTruthy();
  });

  test('the owner renames, approves and adds organizations', async () => {
    const api = mockApi(base({
      ...OWNER,
      'PATCH /api/orgs/1': { organization: { id: 1, name: 'EOS' } },
      'POST /api/orgs/4/approve': { ok: true },
      'POST /api/orgs': { organization: { id: 9 } },
    }), USERS.owner);
    renderScreen(<OrgSettingsPage />, { path: '/settings', user: USERS.owner });
    await openFolds();
    const orgs = await screen.findByRole('region', { name: 'Organizations' });
    expect(await within(orgs).findByText('Acme')).toBeTruthy();
    expect(within(orgs).getByText('Asked by acme.admin, admin@acme.test')).toBeTruthy();
    expect(within(orgs).getByText('1 site, 4 people')).toBeTruthy();

    const name = await screen.findByLabelText(/^Name/);
    await waitFor(() => expect(name.value).toBe('EOS Pilot'));
    expect(name.readOnly).toBe(false);
    fireEvent.change(name, { target: { value: 'EOS' } });
    fireEvent.blur(name);
    await waitFor(() => expect(bodies(api, 'PATCH', '/api/orgs/1')).toEqual([{ name: 'EOS' }]));

    fireEvent.click(within(orgs).getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/api/orgs/4/approve')).toBe(true));

    fireEvent.click(within(orgs).getByRole('button', { name: 'Add an organization' }));
    const sheet = await screen.findByRole('dialog', { name: 'Add an organization' });
    fireEvent.change(within(sheet).getByLabelText(/Organization name/), { target: { value: 'Northwind' } });
    fireEvent.change(within(sheet).getByLabelText(/Admin username/), { target: { value: 'nw.admin' } });
    fireEvent.change(within(sheet).getByLabelText(/Admin email/), { target: { value: 'Admin@Northwind.test' } });
    fireEvent.change(within(sheet).getByLabelText(/Admin password/), { target: { value: 'North@12345' } });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Add organization' }));
    await waitFor(() => expect(bodies(api, 'POST', '/api/orgs')).toEqual([{ name: 'Northwind', adminUsername: 'nw.admin', adminEmail: 'admin@northwind.test', adminPassword: 'North@12345' }]));
  });

  test('the owner removes an organization only after typing its name', async () => {
    const api = mockApi(base({ ...OWNER, 'DELETE /api/orgs/4': { removed: { id: 4 } } }), USERS.owner);
    renderScreen(<OrgSettingsPage />, { path: '/settings', user: USERS.owner });
    await openFolds();
    fireEvent.click(await screen.findByRole('button', { name: 'Options for Acme' }));
    const sheet = await screen.findByRole('dialog', { name: 'Acme' });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Remove' }));
    const go = within(sheet).getByRole('button', { name: 'Remove organization' });
    expect(go.disabled).toBe(true);
    fireEvent.change(within(sheet).getByLabelText(/Type Acme to confirm/), { target: { value: 'Acme' } });
    fireEvent.click(go);
    await waitFor(() => expect(api.calls.some((c) => c.method === 'DELETE' && c.path === '/api/orgs/4')).toBe(true));
  });

  test('a load failure says so and offers another try', async () => {
    mockApi({ 'GET /api/setup/state': { status: 500, body: { error: 'Database is busy.' } } }, USERS.org_admin);
    renderScreen(<OrgSettingsPage />, { path: '/settings', user: USERS.org_admin });
    expect(await screen.findByText('Organization settings could not be loaded. Database is busy.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });
});
