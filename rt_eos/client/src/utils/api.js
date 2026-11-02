import { getItem, setItem, removeItem } from './safeStorage';

// In dev, VITE_API_BASE is empty → Vite's proxy routes /api, /outputs, /uploads.
// In the APK build, we set VITE_API_BASE=https://<public-host> so the WebView
// can reach the real backend instead of its own (non-existent) origin.
let API_BASE = import.meta.env.VITE_API_BASE || '';
const APP_KEY  = import.meta.env.VITE_APP_KEY  || '';

// Self-heal a common misbuild: the web dist gets built with a LOCALHOST API
// base (e.g. from a leftover .env.production.local pointing at localhost:3001),
// then served publicly through a tunnel. On the https tunnel page, every call
// to http://localhost:3001 is blocked as mixed content → "Load failed"
// everywhere. If we detect a localhost base but the page is actually served
// from a real host, drop to same-origin (relative) so the app talks to whatever
// host is serving it. Native (capacitor://localhost) and real dev on localhost
// are untouched - they only trigger when the SERVING host is also localhost.
try {
  if (typeof window !== 'undefined' && /^https?:\/\/(localhost|127\.0\.0\.1)/i.test(API_BASE)) {
    const host = window.location.hostname;
    if (host && host !== 'localhost' && host !== '127.0.0.1') {
      API_BASE = '';   // same origin as the served page
    }
  }
} catch { /* non-browser */ }

// Append ?app_key=... to a URL so the server's app-key gate accepts it.
// The key rides in the query string rather than a header because <img>/<video>/
// <a download> URLs can't carry custom headers. Appending on every URL means a
// single code path works for both fetches and tag-driven media loads.
function withAppKey(url) {
  if (!APP_KEY) return url;
  const sep = url.includes('?') ? '&' : '?';
  return url + sep + 'app_key=' + encodeURIComponent(APP_KEY);
}

// ── Asset tokens ─────────────────────────────────────────────────────
// Rack photographs are served from /outputs and /uploads, and an <img> tag
// cannot send an Authorization header - which is why those paths used to be
// open to anyone holding a rack id. The server now accepts a short-lived asset
// token in the query string instead, so it rides the same appending pattern as
// the app key above and every image URL is covered by one code path.
// Scan files, reports and exports are opened by <img> and plain links too, so
// they carry the same capability. The /outputs and /uploads mounts stay
// covered while older scans still point at them.
const ASSET_PATH_RE = /^\/(outputs|uploads)\/|^\/api\/scans\/[^/]+\/(files\/|report\.)|^\/api\/checks\/[^/]+\/(report\.|export\.)/i;
let assetToken = getItem('rt_assetToken');

// The token is captured into a module variable, and apiUrl() is not reactive - // nothing re-renders when a new one lands. So a component that rendered its
// <img> before the mint (a cold start, or the morning after when the stored
// token has expired) emitted a src the server now 404s, and it stayed broken
// until that component happened to remount. Bumping a generation counter and
// telling the app about it lets those images re-render once, on the render
// after the token arrives.
let assetTokenGen = 0;
export function assetTokenGeneration() { return assetTokenGen; }

// Guards the logout race: a sign-out during an in-flight mint used to be
// overwritten by the response, re-arming a 12-hour capability on a device that
// had just signed out - the exact "departed employee keeps access" case.
let assetTokenEpoch = 0;

/** Called after sign-in to mint the capability <img> tags travel with. */
export async function refreshAssetToken() {
  const epoch = ++assetTokenEpoch;
  try {
    const res = await authFetch(apiUrl('/api/assets/token'));
    if (!res.ok) return null;
    const { token } = await res.json();
    // Someone signed out (or signed in as someone else) while this was in
    // flight. Drop the result on the floor rather than resurrecting it.
    if (epoch !== assetTokenEpoch) return null;
    if (token) {
      assetToken = token;
      assetTokenGen += 1;
      setItem('rt_assetToken', token);
      try { window.dispatchEvent(new Event('rt:asset-token')); } catch { /* non-browser */ }
    }
    return token || null;
  } catch {
    return null;   // images degrade to a broken thumbnail, not a broken app
  }
}

// Read `exp` out of the stored capability without verifying it - the server is
// the only party whose opinion of validity counts. This is used purely to
// decide when to ask for a new one.
function assetTokenExpiry(tok) {
  try {
    const [, body] = String(tok).split('.');
    const { exp } = JSON.parse(atob(body.replace(/-/g, '+').replace(/_/g, '/')));
    return typeof exp === 'number' ? exp * 1000 : 0;
  } catch { return 0; }
}

// The capability lives 12 hours; the auth token lives 30 days. refreshAssetToken
// used to run only at AuthContext mount, so a phone that kept the app resident
// and resumed it the next day carried a dead token: every fetch still worked
// (those send a Bearer header) while every <img> 404'd, which reads as "the
// scan images stopped loading" with no other symptom. Re-mint whenever the app
// comes back to the foreground and the token is gone, expired, or close to it.
const ASSET_REFRESH_MARGIN_MS = 30 * 60 * 1000;
let assetRefreshInFlight = null;

export function ensureFreshAssetToken({ force = false } = {}) {
  // "Am I signed in?" is answered by the cached user, not by a stored token.
  // Web sessions are httpOnly cookies now, so rt_authToken is absent there - // gating on it meant no asset token was ever minted on the web build, and
  // every rack photograph 404'd while the rest of the app worked normally.
  // rt_authUser is written on every sign-in path (see AuthContext) and cleared
  // on sign-out, so it tracks the session on both web and native.
  if (!getItem('rt_authUser')) return Promise.resolve(null);   // signed out
  if (!force && assetToken && Date.now() < assetTokenExpiry(assetToken) - ASSET_REFRESH_MARGIN_MS) {
    return Promise.resolve(assetToken);       // still comfortably valid
  }
  // Collapse concurrent callers (resume + a failed image + a remount) onto one
  // request, so a stale token doesn't produce a burst of identical mints.
  if (!assetRefreshInFlight) {
    assetRefreshInFlight = refreshAssetToken().finally(() => { assetRefreshInFlight = null; });
  }
  return assetRefreshInFlight;
}

/** Wire the foreground/resume triggers. Safe to call more than once. */
export function installAssetTokenRefresh() {
  if (typeof window === 'undefined' || window.__rtAssetRefreshWired) return;
  window.__rtAssetRefreshWired = true;
  const onWake = () => { if (!document.hidden) ensureFreshAssetToken(); };
  document.addEventListener('visibilitychange', onWake);
  window.addEventListener('focus', onWake);
  window.addEventListener('online', onWake);
}

export function clearAssetToken() {
  assetTokenEpoch += 1;   // invalidates any mint already in flight
  assetToken = null;
  assetTokenGen += 1;
  removeItem('rt_assetToken');
  try { window.dispatchEvent(new Event('rt:asset-token')); } catch { /* non-browser */ }
}

function withAssetToken(path, url) {
  if (!assetToken || !ASSET_PATH_RE.test(path)) return url;
  const sep = url.includes('?') ? '&' : '?';
  return url + sep + 't=' + encodeURIComponent(assetToken);
}

/** Add (or refresh) a cache-buster on a URL apiUrl() has already built.
 *
 * The port-locate images keep the same filename across selections, so without
 * a unique query the browser serves the previous port's picture and the
 * highlight looks stuck. The obvious `url + '?t=' + Date.now()` is wrong twice
 * over once assets carry a capability token:
 *
 *   1. apiUrl() has already put a `?` in the URL, so a second one lands INSIDE
 *      the query string. `&t=<token>?t=1753...` parses as a token with the
 *      buster glued to its end - the signature check fails and the server
 *      answers 404. The image silently breaks while the rest of the page,
 *      which sends a Bearer header instead, keeps working.
 *   2. `t` is the token's own parameter name, so even with a correct `&` the
 *      two collide and Express hands the verifier an array.
 *
 * Hence a separate name, the right separator, and no rebuilding of the URL by
 * hand - never `split('?')[0]`, which throws the token away with the query.
 */
export function bustUrl(url) {
  if (!url) return url;
  const [base, query = ''] = String(url).split(/\?(.*)/s);
  const params = query.split('&').filter((p) => p && !p.startsWith('cb='));
  params.push('cb=' + Date.now());
  return base + '?' + params.join('&');
}

// The public origin an off-device link (an invite) should
// point at. In the native app window.location.origin is capacitor://localhost
// (iOS) or http://localhost (Android) - useless the moment the link leaves the
// phone. Use the configured backend host, which serves the web app (including
// the accept-invite page). On the web, API_BASE is empty and the served origin
// already IS the public one.
export function publicOrigin() {
  if (API_BASE) return API_BASE.replace(/\/+$/, '');
  try { return window.location.origin; } catch { return ''; }
}

export function apiUrl(path) {
  if (!path) return path;
  if (/^https?:\/\//i.test(path)) return withAppKey(path); // already absolute
  return withAssetToken(path, withAppKey(API_BASE + path));
}

// Free ngrok tunnels serve an HTML interstitial ("You are about to visit...")
// instead of the real response to clients that look like a browser - which a
// Capacitor WebView does. Any request carrying this header skips it. Scoped to
// our own backend so third-party fetches are untouched (a custom header on a
// cross-origin request forces a CORS preflight).
//
// Harmless once off ngrok: the header is simply ignored by any other host.
export function installFetchInterceptor() {
  if (typeof window === 'undefined' || window.__rtFetchPatched) return;
  window.__rtFetchPatched = true;

  const nativeFetch = window.fetch.bind(window);

  const isOurBackend = (url) => {
    const u = String(url);
    if (!/^https?:\/\//i.test(u)) return true;        // relative → same origin
    return API_BASE ? u.startsWith(API_BASE) : false; // absolute → only our API
  };

  window.fetch = (input, init = {}) => {
    const url = input instanceof Request ? input.url : input;
    if (!isOurBackend(url)) return nativeFetch(input, init);

    // A Request's headers live on the Request, not on init.
    if (input instanceof Request) {
      const headers = new Headers(input.headers);
      headers.set('ngrok-skip-browser-warning', 'true');
      return nativeFetch(new Request(input, { headers }), init);
    }
    const headers = new Headers(init.headers || {});
    headers.set('ngrok-skip-browser-warning', 'true');
    return nativeFetch(input, { ...init, headers });
  };
}

// Collapses a burst of simultaneous 401s onto ONE refresh call. Without this,
// a screen that fires eight requests the moment the 15-minute access token
// expires would send eight refreshes; each rotates the refresh token, so seven
// of them present a token that was just rotated away - which the server is
// right to read as theft, and the user gets signed out for using the app
// normally.
let refreshPromise = null;

function refreshSession() {
  if (!refreshPromise) {
    refreshPromise = fetch(apiUrl('/api/auth/refresh'), { method: 'POST', credentials: 'include' })
      .then((res) => res.ok)
      .catch(() => false)
      .finally(() => { refreshPromise = null; });
  }
  return refreshPromise;
}

// Wrapper around fetch for anything that must be attributed to the signed-in
// user.
//
// Web sessions ride httpOnly cookies, so `credentials: 'include'` is the whole
// mechanism and there is no token for a script to read. The native app has no
// usable cookie jar for capacitor://localhost, so it still carries a Bearer
// token; sending both is harmless and lets one code path serve both.
//
// On a 401 it attempts a single silent refresh and retries once. Only if the
// refresh itself fails does it dispatch 'rt:auth-expired' - otherwise an
// ordinary 15-minute expiry would sign the user out mid-task.
export function authFetch(input, init = {}) {
  const token = getItem('rt_authToken');   // native only; null on web
  const headers = new Headers(init.headers || {});
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  const doFetch = () => fetch(input, { ...init, headers, credentials: 'include' });

  const expired = () => {
    try { window.dispatchEvent(new Event('rt:auth-expired')); } catch { /* non-browser */ }
  };

  return doFetch().then((res) => {
    if (res.status !== 401) return res;
    return refreshSession().then((refreshed) => {
      if (!refreshed) { expired(); return res; }
      return doFetch().then((retryRes) => {
        if (retryRes.status === 401) expired();
        return retryRes;
      });
    });
  });
}
