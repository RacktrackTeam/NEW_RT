import { describe, test, expect, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import SignInPage from './SignInPage.jsx';
import AcceptInvitePage from './AcceptInvitePage.jsx';
import { nextPasswordRule } from '../components/PasswordFields.jsx';
import ForgotPasswordPage from './ForgotPasswordPage.jsx';
import PendingPage from './PendingPage.jsx';
import { mockApi, renderScreen, USERS } from '../test/render.jsx';

describe('Sign in', () => {
  beforeEach(() => { mockApi({}, null); });

  test('asks for both fields before calling the server', () => {
    const api = mockApi({}, null);
    renderScreen(<SignInPage />, { path: '/login', user: null });
    fireEvent.click(screen.getByRole('button', { name: 'Get started' }));
    expect(screen.getByRole('alert').textContent).toBe('Enter your username and password.');
    expect(api.calls.some((c) => c.path === '/api/auth/login')).toBe(false);
  });

  test('signs in and lands on Home', async () => {
    const api = mockApi({ 'POST /api/auth/login': { ok: true, user: USERS.member } }, null);
    renderScreen(<SignInPage />, { path: '/login', user: null, extra: [['/', <p key="h">Home screen</p>]] });
    fireEvent.change(screen.getByLabelText(/Username or email/), { target: { value: ' mandar ' } });
    fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: 'Mandar@12345' } });
    fireEvent.click(screen.getByRole('button', { name: 'Get started' }));
    await screen.findByText('Home screen');
    const call = api.calls.find((c) => c.path === '/api/auth/login');
    expect(JSON.parse(call.body)).toEqual({ username: 'mandar', password: 'Mandar@12345' });
  });

  test('shows the server sentence when refused', async () => {
    mockApi({ 'POST /api/auth/login': { status: 401, body: { error: 'Invalid username or password' } } }, null);
    renderScreen(<SignInPage />, { path: '/login', user: null });
    fireEvent.change(screen.getByLabelText(/Username or email/), { target: { value: 'x' } });
    fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: 'y' } });
    fireEvent.click(screen.getByRole('button', { name: 'Get started' }));
    expect((await screen.findByRole('alert')).textContent).toBe('Invalid username or password');
  });

  test('shows and hides the password, and has no sign-up', () => {
    renderScreen(<SignInPage />, { path: '/login', user: null });
    expect(screen.getByRole('heading', { name: 'RackTrack' })).toBeTruthy();
    const pw = screen.getByLabelText(/^Password/);
    expect(pw.type).toBe('password');
    fireEvent.click(screen.getByRole('button', { name: 'Show password' }));
    expect(pw.type).toBe('text');
    expect(screen.getByRole('button', { name: 'Hide password' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Forgot password?' }).getAttribute('href')).toBe('/forgot-password');
    expect(screen.getByText(/No account\? Contact your administrator\./)).toBeTruthy();
    expect(screen.queryByText(/sign up/i)).toBeNull();
    expect(screen.getByRole('link', { name: 'support@racktrack.ai' }).getAttribute('href')).toMatch(/^mailto:support@racktrack.ai/);
  });
});

describe('Accept invite', () => {
  test('says who invited you and joins', async () => {
    const api = mockApi({
      'GET /api/invites/abc': { ok: true, invite: { email: 'mandar@company.com', role: 'member', organization: 'EOS Pilot', site: 'Rack Build Facility' } },
      'POST /api/invites/abc/accept': { ok: true, user: USERS.member },
    }, null);
    const assign = window.location.assign;
    let went = null;
    Object.defineProperty(window, 'location', { value: { ...window.location, assign: (u) => { went = u; } }, writable: true });
    renderScreen(<AcceptInvitePage />, { path: '/invite/abc', pattern: '/invite/:code', user: null });
    await screen.findByText('EOS Pilot');
    expect(screen.getByText(/as Technician/)).toBeTruthy();
    expect(screen.getByLabelText('Email').value).toBe('mandar@company.com');
    expect(screen.getByLabelText(/Choose a username/).value).toBe('mandar');
    fireEvent.change(screen.getByLabelText(/Create a password/), { target: { value: 'Short1!' } });
    fireEvent.click(screen.getByRole('button', { name: 'Join EOS Pilot' }));
    expect(screen.getByRole('alert').textContent).toBe('Need 8 characters.');
    fireEvent.change(screen.getByLabelText(/Create a password/), { target: { value: 'Longer@123' } });
    fireEvent.click(screen.getByRole('button', { name: 'Join EOS Pilot' }));
    await waitFor(() => expect(went).toBe('/'));
    expect(JSON.parse(api.calls.find((c) => c.path === '/api/invites/abc/accept').body)).toEqual({ username: 'mandar', password: 'Longer@123' });
    window.location.assign = assign;
  });

  test('a dead invite says so', async () => {
    mockApi({ 'GET /api/invites/old': { status: 410, body: { ok: false, error: 'This invite has expired' } } }, null);
    renderScreen(<AcceptInvitePage />, { path: '/invite/old', pattern: '/invite/:code', user: null });
    expect(await screen.findByText('Invite unavailable')).toBeTruthy();
    expect(screen.getByText(/This invite has expired/)).toBeTruthy();
  });

  test('the password rule names the next thing missing', () => {
    expect(nextPasswordRule('abcdefgh')).toBe('Need an uppercase');
    expect(nextPasswordRule('Abcdefgh')).toBe('Need a digit');
    expect(nextPasswordRule('Abcdefg1')).toBe('Need a special character');
    expect(nextPasswordRule('Abcdef1!')).toBeNull();
  });
});

describe('RackTrack frame', () => {
  test('sign in and reset say RackTrack, never the pilot name', async () => {
    mockApi({}, null);
    const { unmount } = renderScreen(<SignInPage />, { path: '/login', user: null });
    expect(document.body.textContent).not.toMatch(/EOS/);
    unmount();
    renderScreen(<ForgotPasswordPage />, { path: '/forgot-password', user: null });
    expect(screen.getAllByText('RackTrack').length).toBeGreaterThan(0);
    expect(screen.getByText('Remembered it?')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Sign in' }).getAttribute('href')).toBe('/login');
    expect(document.querySelector('video')).toBeNull();
    expect(document.body.textContent).not.toMatch(/EOS/);
  });

  test('the invite offers no Google, Apple or Facebook sign-in (removed by the owner, 2 Nov 2026)', async () => {
    mockApi({
      'GET /api/invites/abc': { ok: true, invite: { email: 'a@b.co', role: 'member', organization: 'Org' } },
      'GET /api/auth/providers': { providers: [{ name: 'google', label: 'Google' }, { name: 'apple', label: 'Apple' }] },
    }, null);
    renderScreen(<AcceptInvitePage />, { path: '/invite/abc', pattern: '/invite/:code', user: null });
    expect((await screen.findAllByText(/Org/)).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: /Apple|Google|Facebook/ })).toBeNull();
  });
});

describe('Forgot password', () => {
  test('email, then code, then the choice', async () => {
    const api = mockApi({
      'POST /api/auth/forgot-password': { ok: true, email: 'mandar@eos.local' },
      'POST /api/auth/verify-reset-code': { ok: true },
      'POST /api/auth/login-with-code': { ok: true, user: USERS.member },
    }, null);
    renderScreen(<ForgotPasswordPage />, { path: '/forgot-password', user: null, extra: [['/', <p key="h">Home screen</p>]] });
    fireEvent.change(screen.getByLabelText(/Email/), { target: { value: 'mandar@eos.local' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send reset code' }));
    await screen.findByText(/a code is\s+on the way/);
    fireEvent.change(screen.getByLabelText('Digit 1'), { target: { value: '123456' } });
    fireEvent.click(screen.getByRole('button', { name: 'Verify code' }));
    await screen.findByText('Code verified');
    fireEvent.click(screen.getByRole('button', { name: 'No, take me to the app' }));
    await screen.findByText('Home screen');
    expect(JSON.parse(api.calls.find((c) => c.path === '/api/auth/verify-reset-code').body)).toEqual({ email: 'mandar@eos.local', code: '123456' });
  });

  test('a new password must match twice', async () => {
    mockApi({ 'POST /api/auth/forgot-password': { ok: true }, 'POST /api/auth/verify-reset-code': { ok: true } }, null);
    renderScreen(<ForgotPasswordPage />, { path: '/forgot-password', user: null });
    fireEvent.change(screen.getByLabelText(/Email/), { target: { value: 'a@b.co' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send reset code' }));
    await screen.findByText(/on the way/);
    fireEvent.change(screen.getByLabelText('Digit 1'), { target: { value: '111111' } });
    fireEvent.click(screen.getByRole('button', { name: 'Verify code' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Yes, change password' }));
    fireEvent.change(screen.getByLabelText(/^New password/), { target: { value: 'Longer@123' } });
    fireEvent.change(screen.getByLabelText(/Confirm new password/), { target: { value: 'Longer@124' } });
    fireEvent.click(screen.getByRole('button', { name: 'Reset password' }));
    expect(screen.getByRole('alert').textContent).toBe('Passwords do not match.');
  });
});

describe('Waiting for approval', () => {
  test('explains the state and opens the app once approved', async () => {
    const pending = { ...USERS.org_admin, organization: { id: 1, name: 'EOS Pilot', status: 'pending' } };
    let status = 'pending';
    mockApi({ 'GET /api/auth/me': () => ({ ok: true, user: { ...pending, organization: { ...pending.organization, status } } }) }, pending);
    renderScreen(<PendingPage />, { path: '/pending', user: pending, extra: [['/', <p key="h">Home screen</p>]] });
    expect(await screen.findByText('Waiting for approval')).toBeTruthy();
    status = 'active';
    fireEvent.click(screen.getByRole('button', { name: 'Check now' }));
    await screen.findByText('Home screen');
  });
});
