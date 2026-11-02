/**
 * Where to land after a successful sign-in / signup / password reset,
 * restricted to an in-app path.
 *
 * The route guards in App.jsx stash `location.pathname + location.search`
 * verbatim so a deep link survives the bounce through login. That value is
 * whatever was in the address bar, and react-router (<= 7.17.0, which is what
 * ships here - GHSA-2j2x-hqr9-3h42) re-interprets a leading double slash as a
 * protocol-relative URL. So signing out and visiting a path that begins with
 * two slashes puts that string in state.from, and the post-login navigate
 * sends the browser to the attacker's origin. The user authenticates on the
 * real site and lands somewhere else - a working open redirect, and a
 * convincing one precisely because the login itself was genuine.
 *
 * Accept one leading slash, and nothing a browser could read as a scheme or an
 * authority. Backslash is folded because browsers normalise it to a forward
 * slash in URLs, so a slash-backslash prefix is equivalent to two slashes.
 *
 * @param {unknown} target   candidate path, typically location.state?.from
 * @param {string}  fallback where to go when the candidate is not a safe path
 * @returns {string}
 */
export function safeRedirect(target, fallback) {
  if (typeof target !== 'string') return fallback;
  const t = target.trim();
  if (!t.startsWith('/')) return fallback;   // absolute URL, or a bare host
  if (/^\/[/\\]/.test(t)) return fallback;   // authority-style prefix
  return t;
}

export default safeRedirect;
