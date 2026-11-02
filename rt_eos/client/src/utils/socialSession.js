/**
 * Shared decoding for the session the server hands back after a social sign-in.
 *
 * The server finishes the OAuth dance by redirecting to
 *   web     https://<app>/auth/callback#token=...&user=...
 *   native  com.racktrack.app://auth/callback#token=...&user=...
 *
 * Everything rides in the URL FRAGMENT rather than the query string, because a
 * fragment is stripped by the browser before the request goes out - so the
 * token never lands in an access log, a proxy, or a Referer header.
 *
 * Two callers need to read it and they arrive very differently: the web build
 * gets it as the URL of a page load, the native build gets it as an appUrlOpen
 * event on an app that is already running. Hence one parser, used by both.
 */

// The user object is base64url so a JSON payload survives URL parsing intact.
function decodeUser(b64) {
  const padded = b64.replace(/-/g, '+').replace(/_/g, '/')
    .padEnd(b64.length + ((4 - (b64.length % 4)) % 4), '=');
  const bin = atob(padded);
  // Go through TextDecoder rather than the old unescape(atob()) trick so a
  // non-ASCII name or organization survives.
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes));
}

/**
 * @param {string} hashOrUrl  a location.hash ("#token=..."), or a whole deep-link URL
 * @returns {{ok: true, token, user} | {ok: false, code, message} | null}
 *          null means "this isn't a social callback" - the caller should ignore it.
 */
export function parseAuthFragment(hashOrUrl) {
  if (!hashOrUrl) return null;
  const hash = hashOrUrl.includes('#') ? hashOrUrl.slice(hashOrUrl.indexOf('#') + 1) : '';
  if (!hash) return null;

  const p = new URLSearchParams(hash);
  const err = p.get('error_code');
  if (err) {
    return { ok: false, code: err, message: p.get('error') || 'Sign-in failed. Please try again.' };
  }

  // `user` is what identifies this as a social callback; `token` is optional.
  // On web the server now plants the session as httpOnly cookies on the
  // redirect itself and deliberately leaves the token out of the URL, so
  // requiring one here sent every browser sign-in back to /login. Native still
  // sends it, because a custom-scheme deep link reaches no cookie jar of ours.
  const token = p.get('token');
  const user = p.get('user');
  if (!user) return null;

  try {
    return { ok: true, token: token || null, user: decodeUser(user) };
  } catch {
    // A truncated or mangled redirect. Better to send the user back to the
    // sign-in form than to store half a session.
    return { ok: false, code: 'bad_payload', message: 'Sign-in response was incomplete. Please try again.' };
  }
}

export function isAuthCallbackUrl(url) {
  return typeof url === 'string' && url.includes('/auth/callback');
}
