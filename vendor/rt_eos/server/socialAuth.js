/**
 * Social sign-in — "Continue with Google / Apple / Facebook".
 *
 * SCOPE: signing IN, and accepting an invite. Deliberately NOT creating a new
 * organization. Self-signup mints an org, demands a company name and parks the
 * account in 'pending' until the platform owner approves it (auth.js) — none of
 * which a one-tap button can shortcut, so /signup keeps its existing form.
 *
 * FLOW (identical for all three providers, web and native)
 *   1. client   → GET /api/auth/oauth/:provider/start
 *   2. server   → 302 to the provider, carrying a single-use `state` nonce
 *   3. provider → GET (or POST, for Apple) /api/auth/oauth/:provider/callback
 *   4. server   → redeems the code, resolves the user, mints the same 30-day
 *                 JWT that password login issues
 *   5. server   → 302 to the app with the session in the URL *fragment*
 *
 * The fragment matters: everything after '#' is stripped by the browser before
 * the request is sent, so the token never reaches an access log, a proxy, or a
 * Referer header the way a query parameter would.
 *
 * HOW AN IDENTITY BECOMES A SESSION
 *   a. (provider, subject) already linked  → sign in. Always tried first, so a
 *      user who later changes their Google address keeps their account.
 *   b. verified email matches an account   → link on the fly, then sign in.
 *      Only for providers that actually assert verification; see the comment
 *      on `trustsEmail` in lib/oauthProviders.js for why Facebook doesn't.
 *   c. invite flow, invite email == social email → create the account.
 *   d. otherwise → refuse. There is no path here that creates an account
 *      outside an invite.
 */

const crypto = require('crypto');
const express = require('express');
const bcrypt = require('bcryptjs');
const audit = require('./audit');
const { logger } = require('./lib/observability');
const { getProvider, enabledProviders, makePkce, makeNonce } = require('./lib/oauthProviders');
const { db, publicUser, assignPublicId, USERNAME_RE,
        issueTokenPair, setAuthCookies } = require('./auth');

const STATE_TTL_MS = 10 * 60 * 1000;   // a consent screen nobody finishes in 10 min is abandoned

// ── Schema ───────────────────────────────────────────────────
// Same hand-rolled idempotent style as auth.js — runs at require() time.
db.exec(`
  CREATE TABLE IF NOT EXISTS social_identities (
    provider   TEXT    NOT NULL,
    subject    TEXT    NOT NULL,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    email      TEXT,
    linked_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (provider, subject)
  );
  CREATE INDEX IF NOT EXISTS idx_social_identities_user ON social_identities(user_id);

  CREATE TABLE IF NOT EXISTS oauth_states (
    state       TEXT    PRIMARY KEY,
    provider    TEXT    NOT NULL,
    mode        TEXT    NOT NULL,   -- 'login' | 'invite'
    invite_code TEXT,
    platform    TEXT    NOT NULL,   -- 'web' | 'native'
    expires_at  INTEGER NOT NULL
  );
`);

// PKCE verifier, OIDC nonce, and the browser-binding secret, added after the
// table shipped. Same idempotent style as auth.js.
(function migrateOauthStates() {
  const cols = db.prepare('PRAGMA table_info(oauth_states)').all().map(c => c.name);
  const add = (col, ddl) => { if (!cols.includes(col)) db.exec(`ALTER TABLE oauth_states ADD COLUMN ${ddl}`); };
  add('code_verifier', 'code_verifier TEXT');
  add('nonce',         'nonce TEXT');
  add('browser_key',   'browser_key TEXT');
})();

// users.password_set (0 = joined through a provider, has no password) is added
// by the migration block in auth.js, alongside the other user columns.

// ── Config ───────────────────────────────────────────────────
// Where the providers send the browser back. Must be a public HTTPS origin and
// must match the redirect URI registered in each provider console byte for
// byte — a trailing slash difference is a redirect_uri_mismatch.
function redirectBase() {
  return (process.env.OAUTH_REDIRECT_BASE || '').replace(/\/+$/, '');
}
function redirectUriFor(provider) {
  return `${redirectBase()}/api/auth/oauth/${provider}/callback`;
}
// Where the finished session is handed back to.
function webOrigin() {
  return (process.env.OAUTH_WEB_ORIGIN || redirectBase()).replace(/\/+$/, '');
}
function nativeScheme() {
  return process.env.OAUTH_NATIVE_SCHEME || 'com.racktrack.app';
}

// ── Helpers ──────────────────────────────────────────────────
// Read one cookie off the raw header. Express sets cookies natively via
// res.cookie(), but reading them needs cookie-parser — a whole dependency and
// a global middleware for the single cookie this module uses. `res.clearCookie`
// is likewise core, so nothing else is missing.
function readCookie(req, name) {
  const header = req.headers?.cookie;
  if (!header) return null;
  for (const part of header.split(';')) {
    const eq = part.indexOf('=');
    if (eq === -1) continue;
    if (part.slice(0, eq).trim() === name) {
      try { return decodeURIComponent(part.slice(eq + 1).trim()); }
      catch { return part.slice(eq + 1).trim(); }
    }
  }
  return null;
}

const BIND_COOKIE = 'rt_oauth_bind';
const BIND_COOKIE_PATH = '/api/auth/oauth';

function landingUrl(platform, params) {
  // base64url so a JSON user object survives the trip without any interaction
  // between JSON's characters and URL parsing.
  const frag = new URLSearchParams(params).toString();
  return platform === 'native'
    ? `${nativeScheme()}://auth/callback#${frag}`
    : `${webOrigin()}/auth/callback#${frag}`;
}

function encodeUser(user) {
  return Buffer.from(JSON.stringify(publicUser(user)), 'utf8').toString('base64url');
}

// A social account has no username, but users.username is UNIQUE NOT NULL and
// shows up all over the UI. Derive one from the email local-part, pad it to the
// 3-char minimum, strip anything the USERNAME_RE won't accept, then de-dupe.
function deriveUsername(email, name) {
  const seed = String(email || name || 'user').split('@')[0];
  let base = seed.replace(/[^a-zA-Z0-9_.-]/g, '').replace(/^[._-]+|[._-]+$/g, '').slice(0, 24);
  if (base.length < 3) base = `user${base}`;
  base = base.slice(0, 24);

  const taken = (u) => !!db.prepare('SELECT 1 FROM users WHERE username = ? COLLATE NOCASE').get(u);
  if (USERNAME_RE.test(base) && !taken(base)) return base;
  for (let i = 2; i <= 99; i++) {
    const cand = `${base}${i}`;
    if (USERNAME_RE.test(cand) && !taken(cand)) return cand;
  }
  // Pathological collision — fall back to something that cannot collide.
  for (;;) {
    const cand = `${base.slice(0, 20)}-${crypto.randomBytes(3).toString('hex')}`;
    if (!taken(cand)) return cand;
  }
}

// See the password_set comment above.
function unusablePasswordHash() {
  return bcrypt.hashSync(crypto.randomBytes(32).toString('hex'), 10);
}

function findLink(provider, subject) {
  return db.prepare('SELECT * FROM social_identities WHERE provider = ? AND subject = ?')
           .get(provider, subject);
}

function linkIdentity(provider, subject, userId, email) {
  db.prepare(`INSERT INTO social_identities (provider, subject, user_id, email)
              VALUES (?, ?, ?, ?)
              ON CONFLICT(provider, subject) DO UPDATE SET user_id = excluded.user_id,
                                                           email   = excluded.email`)
    .run(provider, subject, userId, email || null);
}

function userByEmail(email) {
  return db.prepare('SELECT * FROM users WHERE email = ? COLLATE NOCASE').get(email);
}

// Thrown by the resolvers; the callback turns these into a redirect carrying a
// stable machine code plus prose the sign-in page can show verbatim.
class SocialAuthError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}

// ── Resolution: sign in an existing account ──────────────────
function resolveLogin(identity, provider) {
  const link = findLink(identity.provider, identity.subject);
  if (link) {
    const user = db.prepare('SELECT * FROM users WHERE id = ?').get(link.user_id);
    if (user) {
      if (user.active === 0) {
        throw new SocialAuthError('deactivated',
          'This account has been deactivated. Contact your administrator.');
      }
      return { user, linked: false };
    }
    // The user row was deleted but the link outlived it. Drop the orphan and
    // fall through to the email path rather than 500ing on a null user.
    db.prepare('DELETE FROM social_identities WHERE provider = ? AND subject = ?')
      .run(identity.provider, identity.subject);
  }

  if (!identity.email) {
    throw new SocialAuthError('no_email',
      `${provider.label} didn't share an email address, so we can't tell which account this is. `
      + 'Sign in with your username and password instead.');
  }

  // Auto-linking by email is only safe when the provider actually asserts the
  // address is verified — otherwise anyone who registers a social account
  // against a colleague's email inherits their RackTrack account.
  if (!provider.trustsEmail || !identity.emailVerified) {
    throw new SocialAuthError('link_required',
      `We can't match your ${provider.label} account to a RackTrack account automatically. `
      + 'Sign in with your username and password once, then link it from your profile.');
  }

  const user = userByEmail(identity.email);
  if (!user) {
    throw new SocialAuthError('no_account',
      `No RackTrack account uses ${identity.email}. Ask your organization admin for an invite link.`);
  }
  if (user.active === 0) {
    throw new SocialAuthError('deactivated',
      'This account has been deactivated. Contact your administrator.');
  }
  linkIdentity(identity.provider, identity.subject, user.id, identity.email);
  return { user, linked: true };
}

// ── Resolution: accept an invite ─────────────────────────────
function resolveInvite(identity, provider, code) {
  const inv = db.prepare('SELECT * FROM invites WHERE code = ?').get(code);
  if (!inv) throw new SocialAuthError('invite_invalid', 'Invite not found.');
  if (inv.accepted_at) throw new SocialAuthError('invite_used', 'This invite has already been used.');
  if (inv.expires_at && Date.now() > inv.expires_at) {
    throw new SocialAuthError('invite_expired', 'This invite has expired.');
  }

  if (!identity.email) {
    throw new SocialAuthError('no_email',
      `${provider.label} didn't share an email address, so we can't confirm this invite is yours. `
      + 'Use the username and password form below instead.');
  }

  // THE security anchor for this path, and the reason Facebook is safe here
  // even though it isn't trusted for auto-linking: an admin sent the invite to
  // a specific address, so requiring the social account to assert that same
  // address means the org already vouched for whoever controls it.
  if (identity.email.toLowerCase() !== String(inv.email).toLowerCase()) {
    const relay = identity.isPrivateEmail
      ? ' You chose "Hide My Email", which gives us a private relay address instead of your real one.'
      : '';
    throw new SocialAuthError('email_mismatch',
      `This invite was sent to ${inv.email}, but your ${provider.label} account uses `
      + `${identity.email}.${relay} Use the form below instead.`);
  }

  // Idempotency: the invite link opened twice, or an account already exists for
  // that address. Link and sign in rather than colliding on users.email UNIQUE.
  const existing = userByEmail(inv.email);
  if (existing) {
    if (existing.active === 0) {
      throw new SocialAuthError('deactivated',
        'This account has been deactivated. Contact your administrator.');
    }
    linkIdentity(identity.provider, identity.subject, existing.id, identity.email);
    db.prepare("UPDATE invites SET accepted_at = datetime('now') WHERE code = ?").run(code);
    return { user: existing, created: false };
  }

  const username = deriveUsername(inv.email, identity.name);
  const created = db.transaction(() => {
    const r = db.prepare(`
      INSERT INTO users (email, username, password_hash, email_verified,
                         role, organization_id, tenant_id, active, password_set)
      VALUES (?, ?, ?, 1, ?, ?, ?, 1, 0)
    `).run(String(inv.email).toLowerCase(), username, unusablePasswordHash(),
           inv.role, inv.organization_id, inv.tenant_id);
    db.prepare("UPDATE invites SET accepted_at = datetime('now') WHERE code = ?").run(code);
    linkIdentity(identity.provider, identity.subject, r.lastInsertRowid, identity.email);
    return r.lastInsertRowid;
  })();

  try { assignPublicId(created, inv.role); } catch { /* non-fatal, publicUser retries */ }
  return { user: db.prepare('SELECT * FROM users WHERE id = ?').get(created), created: true };
}

// ── Routes ───────────────────────────────────────────────────
function registerRoutes(app) {

  // Which buttons to draw. Server-side config means enabling a provider is an
  // env change and a restart, not a mobile app rebuild and store resubmission.
  app.get('/api/auth/providers', (req, res) => {
    res.json({ ok: true, providers: enabledProviders() });
  });

  // ── Step 1: hand the browser off to the provider ───────────
  app.get('/api/auth/oauth/:provider/start', (req, res) => {
    const name = String(req.params.provider || '').toLowerCase();
    const provider = getProvider(name);
    if (!provider) return res.status(404).send('That sign-in provider is not enabled.');
    if (!redirectBase()) {
      logger.error('[social] OAUTH_REDIRECT_BASE is not set — cannot build a redirect URI');
      return res.status(500).send('Social sign-in is not configured on this server.');
    }

    const platform = req.query.platform === 'native' ? 'native' : 'web';
    const mode = req.query.mode === 'invite' ? 'invite' : 'login';
    const inviteCode = mode === 'invite' ? String(req.query.invite || '') : null;

    // Validate the invite BEFORE bouncing the user out to a consent screen —
    // failing afterwards means they authorize an app and then get told no.
    if (mode === 'invite') {
      const inv = db.prepare('SELECT * FROM invites WHERE code = ?').get(inviteCode);
      const dead = !inv || inv.accepted_at || (inv.expires_at && Date.now() > inv.expires_at);
      if (dead) {
        return res.redirect(302, landingUrl(platform, {
          error_code: 'invite_invalid',
          error: 'This invite is no longer valid.',
        }));
      }
    }

    // Opportunistic sweep — no scheduler needed for a table this small.
    db.prepare('DELETE FROM oauth_states WHERE expires_at < ?').run(Date.now());

    const state = crypto.randomBytes(24).toString('base64url');
    const pkce = provider.supportsPkce ? makePkce() : null;
    const nonce = provider.supportsNonce ? makeNonce() : null;

    // Browser binding. `state` alone proves the callback corresponds to a
    // request WE started; it does not prove it reached the browser that started
    // it. Without this, an attacker who obtains a valid callback URL — from a
    // shoulder-surfed screen, a shared log, a chat paste — can open it in their
    // own browser and receive the victim's session. The other half of the pair
    // lives in an httpOnly cookie the attacker cannot have, so a callback
    // arriving without it is refused.
    //
    // Web only: a native flow hands off to an in-app browser tab whose cookie
    // jar is separate from the WebView's, so the cookie could not come back.
    // There the deep link into the app is itself the binding — the redirect
    // targets a scheme only our installed app is registered to receive.
    let browserKey = null;
    if (platform === 'web') {
      browserKey = crypto.randomBytes(24).toString('base64url');

      // SameSite has to match how the provider returns the result. Lax is sent
      // on a cross-site top-level GET (Google, Facebook) but NOT on a
      // cross-site POST — so for Apple's form_post the cookie must be None,
      // which browsers only honour when it is also Secure.
      const crossSitePost = !!provider.usesFormPost;

      // Secure is derived rather than hardcoded: a hardcoded `true` is dropped
      // by the browser on plain http, which would break local development
      // against a tunnel-less server. Behind Caddy the connection to us is
      // http, so the forwarded header is what tells us the real scheme.
      const isHttps = req.secure || req.headers['x-forwarded-proto'] === 'https';

      res.cookie(BIND_COOKIE, browserKey, {
        httpOnly: true,
        secure: isHttps || crossSitePost,   // SameSite=None is void without Secure
        sameSite: crossSitePost ? 'none' : 'lax',
        maxAge: STATE_TTL_MS,
        path: BIND_COOKIE_PATH,
      });
    }

    db.prepare(`INSERT INTO oauth_states
                  (state, provider, mode, invite_code, platform, expires_at,
                   code_verifier, nonce, browser_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`)
      .run(state, name, mode, inviteCode, platform, Date.now() + STATE_TTL_MS,
           pkce?.verifier || null, nonce, browserKey);

    res.redirect(302, provider.authorizeUrl(state, redirectUriFor(name), {
      challenge: pkce?.challenge, nonce,
    }));
  });

  // ── Step 2: the provider comes back ────────────────────────
  // GET for Google and Facebook. Apple POSTs a form body, because requesting
  // any scope obliges it to use response_mode=form_post — hence the urlencoded
  // parser mounted here rather than globally.
  const callback = async (req, res) => {
    const name = String(req.params.provider || '').toLowerCase();
    const src = req.method === 'POST' ? (req.body || {}) : req.query;
    const { code, state } = src;
    // Fallback platform for the error redirect if we can't resolve the state.
    let platform = 'web';

    // Single-use, like the state row it pairs with. Cleared on every exit path
    // — success or failure — so a stale binding can never be replayed against a
    // later flow.
    res.clearCookie(BIND_COOKIE, { path: BIND_COOKIE_PATH });

    try {
      const provider = getProvider(name);
      if (!provider) throw new SocialAuthError('provider_disabled', 'That sign-in provider is not enabled.');

      if (!state) throw new SocialAuthError('bad_state', 'Sign-in session was lost. Please try again.');
      const row = db.prepare('SELECT * FROM oauth_states WHERE state = ?').get(String(state));
      // Single use: consumed whether or not the rest succeeds, so a replayed
      // callback can't mint a second session.
      db.prepare('DELETE FROM oauth_states WHERE state = ?').run(String(state));
      if (!row) throw new SocialAuthError('bad_state', 'Sign-in session was lost or already used. Please try again.');
      platform = row.platform;
      if (Date.now() > row.expires_at) {
        throw new SocialAuthError('expired', 'Sign-in took too long. Please try again.');
      }
      if (row.provider !== name) {
        throw new SocialAuthError('bad_state', 'Sign-in session did not match. Please try again.');
      }

      // Browser binding — see the comment where browser_key is issued. Compared
      // in constant time so the check can't be turned into an oracle.
      if (row.browser_key) {
        const presented = String(readCookie(req, BIND_COOKIE) || '');
        const expected = String(row.browser_key);
        const ok = presented.length === expected.length
          && crypto.timingSafeEqual(Buffer.from(presented), Buffer.from(expected));
        if (!ok) {
          throw new SocialAuthError('bad_state',
            'This sign-in was started in a different browser. Please try again here.');
        }
      }

      // The user tapped Cancel on the consent screen.
      if (!code) {
        const denied = src.error === 'access_denied' || src.error === 'user_cancelled_authorize';
        throw new SocialAuthError(denied ? 'cancelled' : 'no_code',
          denied ? `${provider.label} sign-in was cancelled.`
                 : `${provider.label} did not return an authorization code.`);
      }

      const identity = await provider.exchange(String(code), redirectUriFor(name), {
        verifier: row.code_verifier || undefined,
        nonce: row.nonce || undefined,
      });

      const result = row.mode === 'invite'
        ? resolveInvite(identity, provider, row.invite_code)
        : resolveLogin(identity, provider);

      const user = result.user;
      audit.log({
        req, user,
        action: row.mode === 'invite' ? 'auth.social.invite_accept' : 'auth.social.login',
        status: 'ok', targetType: 'user', targetId: user.id,
        payload: { provider: name, created: !!result.created, linked: !!result.linked },
      });

      // Web lands back on our own origin, so the session can be planted as
      // httpOnly cookies on this very redirect — the token never enters the
      // URL, and therefore never reaches browser history, a proxy log or the
      // Referer of whatever the page loads next.
      //
      // Native cannot: the redirect leaves through the OS to
      // com.racktrack.app://auth/callback, where no cookie jar of ours is
      // reachable. It keeps the fragment token, matching the Bearer flow the
      // native build still uses.
      const { accessJwt, refreshPlain } = issueTokenPair(user, req);
      if (platform === 'native') {
        return res.redirect(302, landingUrl(platform, {
          token: accessJwt,
          user: encodeUser(user),
        }));
      }
      setAuthCookies(res, accessJwt, refreshPlain);
      return res.redirect(302, landingUrl(platform, { user: encodeUser(user) }));

    } catch (err) {
      const code_ = err instanceof SocialAuthError ? err.code : 'exchange_failed';
      // Provider/network failures carry diagnostic detail that belongs in the
      // log, not on a sign-in screen — show something actionable instead.
      const message = err instanceof SocialAuthError
        ? err.message
        : 'Could not complete sign-in. Please try again.';
      if (!(err instanceof SocialAuthError)) {
        logger.error(`[social] ${name} callback failed: ${err.message}`);
      }
      audit.log({ req, action: 'auth.social.login', status: 'fail',
        error: code_, payload: { provider: name } });
      return res.redirect(302, landingUrl(platform, { error_code: code_, error: message }));
    }
  };

  app.get('/api/auth/oauth/:provider/callback', callback);
  app.post('/api/auth/oauth/:provider/callback', express.urlencoded({ extended: false }), callback);
}

module.exports = { registerRoutes };
