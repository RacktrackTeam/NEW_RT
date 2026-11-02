import { describe, test, expect } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { renderScreen, mockApi, USERS } from '../test/render.jsx';
import SetupPage from './SetupPage.jsx';

const GATED = { ...USERS.org_admin, setup: { needsSetup: true } };

const snap = (over = {}) => ({
  tenant: { id: 2, name: 'Rack Build Facility', organization_id: 1 },
  datacentre: {}, spaces: [], approver: null, rules: null, profile: { facility: {}, contacts: [] }, completeness: null,
  ...over,
});
const DONE_SNAP = snap({
  datacentre: { address: '12 Harbour Road, Rotterdam, Netherlands', timezone: 'Europe/Amsterdam' },
  approver: { user_id: 2, username: 'barry', email: 'barry@eos.local' },
  rules: { accepted_at: '2026-09-20 10:00:00' },
  profile: { facility: { address_line1: '12 Harbour Road', city: 'Rotterdam', country: 'NL' }, contacts: [] },
});
const PROFILE = { profile: { name: 'EOS Pilot', timezone: 'Europe/London', country: '', primary_contact_name: '', primary_contact_email: '' } };
const FULL_PROFILE = { profile: { name: 'EOS Pilot', timezone: 'Europe/London', country: 'GB', primary_contact_name: 'Barry', primary_contact_email: 'barry@eos.local' } };

const api = (extra = {}, user = USERS.org_admin, site = snap(), profile = PROFILE) => mockApi({
  'GET /api/setup/state': { tenants: [{ id: 2, name: 'Rack Build Facility' }] },
  'GET /api/setup/2': site,
  'GET /api/setup/org/1/profile': profile,
  'PUT /api/setup/org/1/profile': (url, init) => ({ profile: { ...profile.profile, ...JSON.parse(init.body) } }),
  'GET /api/orgs/1/members': { members: [] },
  ...extra,
}, user);

const bodies = (fn, method, path) => fn.calls.filter((c) => c.method === method && c.path === path).map((c) => JSON.parse(c.body));
const stepTitle = () => screen.getByRole('dialog').querySelector('h2').textContent;

describe('SetupPage', () => {
  test('gated: opens on the first step with work left, Next checks it, and there is no way out but sign out', async () => {
    const fn = api({}, GATED);
    renderScreen(<SetupPage />, { path: '/setup', user: GATED });
    await waitFor(() => expect(stepTitle()).toBe('Organization'));
    expect(screen.getAllByRole('button', { name: 'Sign out' }).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: 'Close' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Back', hidden: false })?.disabled).toBe(true);
    expect(document.body.textContent).not.toMatch(/Step \d|Required|Optional/);

    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('Fill in the marked fields to continue.')).toBeTruthy();
    expect(screen.getAllByRole('alert').some((a) => a.textContent === 'Pick a country')).toBe(true);
    expect(stepTitle()).toBe('Organization');

    fireEvent.change(screen.getByLabelText(/^Country/), { target: { value: 'GB' } });
    await waitFor(() => expect(bodies(fn, 'PUT', '/api/setup/org/1/profile')).toContainEqual({ country: 'GB' }));
    fireEvent.change(screen.getByLabelText(/Primary contact name/), { target: { value: 'Barry' } });
    fireEvent.change(screen.getByLabelText(/Primary contact email/), { target: { value: 'barry@eos.local' } });
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(stepTitle()).toBe('Sites'));
    expect(bodies(fn, 'PUT', '/api/setup/org/1/profile')).toContainEqual({ primary_contact_name: 'Barry', primary_contact_email: 'barry@eos.local' });
  });

  test('sites: the address saves as it goes and the SPOC gets a site manager account', async () => {
    const fn = api({
      'PUT /api/setup/2/profile/facility': (url, init) => ({ data: JSON.parse(init.body) }),
      'PUT /api/setup/2/datacentre': (url, init) => ({ datacentre: JSON.parse(init.body) }),
      'POST /api/sites/2/members': { ok: true, member: { id: 30, username: 'ravi.k' } },
      'PUT /api/setup/2/approver': { approver: { user_id: 30, username: 'ravi.k', email: 'ravi@eos.local' } },
    }, USERS.org_admin, snap(), FULL_PROFILE);
    renderScreen(<SetupPage />, { path: '/setup?step=sites', pattern: '/setup', user: USERS.org_admin });
    await waitFor(() => expect(stepTitle()).toBe('Sites'));
    const street = await screen.findByLabelText(/Street address/);
    fireEvent.change(street, { target: { value: '12 Harbour Road' } });
    fireEvent.blur(street);
    fireEvent.change(screen.getByLabelText(/^Country/), { target: { value: 'NL' } });
    await waitFor(() => expect(bodies(fn, 'PUT', '/api/setup/2/datacentre').length).toBeGreaterThan(0));
    const dcBodies = bodies(fn, 'PUT', '/api/setup/2/datacentre');
    // The country starts from the organization's.
    expect(dcBodies[0].address).toBe('12 Harbour Road, United Kingdom');
    expect(dcBodies[0].timezone).toBe('Europe/London');
    expect(dcBodies.at(-1).address).toBe('12 Harbour Road, Netherlands');

    // Next without a SPOC stays and says why.
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('Give this site a SPOC: make an account or pick someone.')).toBeTruthy();
    expect(stepTitle()).toBe('Sites');

    fireEvent.change(screen.getByLabelText(/^Email/), { target: { value: 'ravi@eos.local' } });
    fireEvent.change(screen.getByLabelText(/^Username/), { target: { value: 'ravi.k' } });
    fireEvent.change(screen.getByLabelText(/Temporary password/), { target: { value: 'Spoc@12345' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create the account' }));
    await waitFor(() => expect(bodies(fn, 'PUT', '/api/setup/2/approver')).toEqual([{ user_id: 30 }]));
    expect(bodies(fn, 'POST', '/api/sites/2/members')).toEqual([{ username: 'ravi.k', email: 'ravi@eos.local', password: 'Spoc@12345', role: 'site_manager' }]);
    expect(await screen.findByText('ravi.k')).toBeTruthy();
  });

  test('sites: an existing person at the site can be made the SPOC', async () => {
    const fn = api({
      'GET /api/orgs/1/members': { members: [{ id: 2, username: 'barry', email: 'barry@eos.local', role: 'site_manager', tenant_id: 2, active: 1 }] },
      'PUT /api/setup/2/approver': { approver: { user_id: 2, username: 'barry' } },
    }, USERS.org_admin, snap(), FULL_PROFILE);
    renderScreen(<SetupPage />, { path: '/setup?step=sites', pattern: '/setup', user: USERS.org_admin });
    fireEvent.click(await screen.findByRole('tab', { name: 'Someone with an account' }));
    fireEvent.change(screen.getByLabelText(/^Person/), { target: { value: '2' } });
    await waitFor(() => expect(bodies(fn, 'PUT', '/api/setup/2/approver')).toEqual([{ user_id: 2 }]));
  });

  test('rules: one tick accepts for every site', async () => {
    const fn = api({ 'PUT /api/setup/2/rules': { rules: { accepted_at: '2026-09-28 10:00:00' } } }, USERS.org_admin, { ...DONE_SNAP, rules: null }, FULL_PROFILE);
    renderScreen(<SetupPage />, { path: '/setup?step=rules', pattern: '/setup', user: USERS.org_admin });
    await waitFor(() => expect(stepTitle()).toBe('Rules'));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect((await screen.findAllByText('Tick I accept to continue.')).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByLabelText(/I accept/));
    await waitFor(() => expect(bodies(fn, 'PUT', '/api/setup/2/rules')).toEqual([{ accepted: true }]));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(stepTitle()).toBe('Review'));
  });

  test('review lists what is still needed and jumps to its step', async () => {
    api({}, USERS.org_admin, snap(), FULL_PROFILE);
    renderScreen(<SetupPage />, { path: '/setup?step=review', pattern: '/setup', user: USERS.org_admin });
    await waitFor(() => expect(stepTitle()).toBe('Review'));
    expect(screen.getByText('Still needed')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Rules accepted' }));
    await waitFor(() => expect(stepTitle()).toBe('Rules'));
  });

  test('a finished setup ends with Done, which re-reads the session and goes back to settings', async () => {
    const fn = api({}, USERS.org_admin, DONE_SNAP, FULL_PROFILE);
    renderScreen(<SetupPage />, { path: '/setup?step=review', pattern: '/setup', user: USERS.org_admin, extra: [['/settings', <p key="s">Settings page</p>]] });
    await waitFor(() => expect(stepTitle()).toBe('Review'));
    expect(screen.getByText('Everything needed is done.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(stepTitle()).toBe('Thank you'));
    const before = fn.calls.filter((c) => c.path === '/api/auth/me').length;
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(await screen.findByText('Settings page')).toBeTruthy();
    expect(fn.calls.filter((c) => c.path === '/api/auth/me').length).toBeGreaterThan(before);
  });

  test('not gated: Close goes back to settings', async () => {
    api({}, USERS.org_admin, DONE_SNAP, FULL_PROFILE);
    renderScreen(<SetupPage />, { path: '/setup', user: USERS.org_admin, extra: [['/settings', <p key="s">Settings page</p>]] });
    await waitFor(() => expect(stepTitle()).toBe('Review'));
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(await screen.findByText('Settings page')).toBeTruthy();
  });

  test('a load failure says so', async () => {
    mockApi({ 'GET /api/setup/state': { status: 500, body: { error: 'Database is busy.' } } }, USERS.org_admin);
    renderScreen(<SetupPage />, { path: '/setup', user: USERS.org_admin });
    expect(await screen.findByText('Setup could not be loaded. Database is busy.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });
});
