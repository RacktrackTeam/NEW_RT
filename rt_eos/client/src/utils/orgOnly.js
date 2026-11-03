/* The /org/ build that racktrack-app serves beside its own screens (auth.sh builds it with VITE_ORG_ONLY=1).
   It holds only the organization pages: setup, organization settings, sites and people, the person's own
   settings and the platform console. Home, Scan, Map, History and the results stay in the app, and every
   page has a way back to it. */
export const ORG_ONLY = import.meta.env.VITE_ORG_ONLY === '1';

const KEY = 'rt_back_to_app';

/* The app's address to go back to: given as ?back= by the app when it opens a page here, kept for the
   session. Only an address on this same host whose path is the app's (/app/) is taken, so the link can
   never send someone to another site. */
function safeBack(raw) {
  try {
    const u = new URL(raw);
    if (u.hostname !== window.location.hostname) return null;
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
    if (!u.pathname.startsWith('/app/')) return null;
    return u.href;
  } catch {
    return null;
  }
}

export function rememberBack() {
  if (!ORG_ONLY || typeof window === 'undefined') return;
  const given = new URLSearchParams(window.location.search).get('back');
  const ok = given ? safeBack(given) : null;
  if (ok) {
    try { window.sessionStorage.setItem(KEY, ok); } catch { /* private mode: the default below is used */ }
  }
}

export function backUrl() {
  let kept = null;
  try { kept = window.sessionStorage.getItem(KEY); } catch { kept = null; }
  return (kept && safeBack(kept)) || `${window.location.protocol}//${window.location.hostname}:8700/app/#more`;
}

export function toApp() {
  window.location.assign(backUrl());
}
