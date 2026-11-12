/* Test helpers: a signed-in app around one screen, and a fake server. */
import { render } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { vi } from 'vitest';
import { AuthProvider } from '../AuthContext.jsx';
import { ToastProvider } from '../components/Toast.jsx';

export const USERS = {
  member: { id: 7, username: 'mandar', email: 'mandar@eos.local', role: 'member', tenant_id: 2, organization_id: 1, created_at: '2026-09-12 09:00:00', tenant: { id: 2, name: 'Rack Build Facility' }, organization: { id: 1, name: 'EOS Pilot', status: 'active' }, setup: { needsSetup: false } },
  site_manager: { id: 2, username: 'barry', email: 'barry@eos.local', role: 'site_manager', tenant_id: 2, organization_id: 1, created_at: '2026-09-12 09:00:00', tenant: { id: 2, name: 'Rack Build Facility' }, organization: { id: 1, name: 'EOS Pilot', status: 'active' }, setup: { needsSetup: false } },
  org_admin: { id: 3, username: 'aasritha', email: 'a@eos.local', role: 'org_admin', tenant_id: null, organization_id: 1, created_at: '2026-09-12 09:00:00', organization: { id: 1, name: 'EOS Pilot', status: 'active' }, setup: { needsSetup: false } },
  owner: { id: 1, username: 'owner', email: 'owner@racktrack.ai', role: 'owner', tenant_id: null, organization_id: null, created_at: '2026-09-01 09:00:00', setup: { needsSetup: false } },
};

function json(status, body) {
  return new Response(body == null ? '' : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

/**
 * Replace fetch with a fake server. handlers: { 'GET /api/home': body | fn }.
 * A function gets (url, init) and returns a body, or { status, body }.
 * The key may end in '*' to match any path that starts with it.
 * Unknown routes answer 404 { error: 'Not found' } like an unbuilt route.
 */
export function mockApi(handlers = {}, user = null) {
  const calls = [];
  const all = {
    'GET /api/auth/me': () => (user ? { ok: true, user } : { status: 401, body: { error: 'Authentication required' } }),
    'GET /api/assets/token': { token: 'x.eyJleHAiOjk5OTk5OTk5OTl9.y' },
    ...handlers,
  };
  const fn = vi.fn(async (input, init = {}) => {
    const url = typeof input === 'string' ? input : input.url;
    const method = (init.method || 'GET').toUpperCase();
    const u = new URL(url, 'http://localhost');
    const path = u.pathname;
    calls.push({ method, path, url: u.pathname + u.search, body: init.body });
    const key = Object.keys(all).find((k) => {
      const [m, p] = k.split(' ');
      if (m !== method) return false;
      return p.endsWith('*') ? path.startsWith(p.slice(0, -1)) : p === path;
    });
    if (!key) return json(404, { error: 'Not found' });
    let out = all[key];
    if (typeof out === 'function') out = await out(u.pathname + u.search, init);
    if (out && typeof out === 'object' && 'status' in out && ('body' in out || out.status >= 300)) return json(out.status, out.body);
    return json(200, out);
  });
  globalThis.fetch = fn;
  window.fetch = fn;
  fn.calls = calls;
  return fn;
}

/**
 * Render a screen inside the router, the session and the toast.
 * path: the address; pattern: the route pattern the screen is mounted on.
 */
export function renderScreen(ui, { path = '/', pattern, user = USERS.member, extra = [] } = {}) {
  window.localStorage.clear();
  if (user) window.localStorage.setItem('rt_authUser', JSON.stringify(user));
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <ToastProvider>
          <Routes>
            <Route path={pattern || path.split('?')[0]} element={ui} />
            {extra.map(([p, el]) => <Route key={p} path={p} element={el} />)}
            <Route path="*" element={<p>Somewhere else</p>} />
          </Routes>
        </ToastProvider>
      </AuthProvider>
    </MemoryRouter>,
  );
}
