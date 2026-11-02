/**
 * The sign-in and organizations service for racktrack-app.
 *
 *   node auth_service.js            (started by ../../auth.sh)
 *
 * This is RT-EOS's own server (app.js) with only the parts that are about
 * people: sign-in (password, invite link, reset code, Google / Apple /
 * Facebook when their keys are set), the platform owner's approval of new
 * organizations, organizations, sites, people and roles, invites, the
 * organization settings and the first-run setup. The scan and pipeline
 * routes of app.js stay off: the video engine (server/app.py) owns scans
 * and trusts the session this service signs (same JWT_SECRET, same cookie).
 *
 * Every block below is copied from app.js unchanged in what it does: the
 * security headers, CORS, the CSRF origin check, the credential rate limit,
 * auth.registerRoutes, socialAuth, and the setup, home, org_data and
 * scan-sites routers. What is new: the RT-EOS client built with the base
 * path /org/ is served at /org/, and nothing else is mounted.
 *
 * Settings come from the file RT_ENV_FILE names (auth.sh points it at
 * data/auth/.env): PORT, JWT_SECRET, RACKTRACK_AUTH_DB, CORS_ALLOWED_ORIGINS,
 * and optionally SMTP_* and the OAuth keys. With no SMTP set, mail is written
 * to the outbox folder (RT_MAIL_OUTBOX) instead of being sent.
 */
'use strict';

const path = require('path');
const fs = require('fs');

require('./lib/load_env');

const express = require('express');
const cors = require('cors');
const cookieParser = require('cookie-parser');
const helmet = require('helmet');
const jwt = require('jsonwebtoken');
const auth = require('./auth');
const audit = require('./audit');
const o11y = require('./lib/observability');
const { uploadLimiter } = require('./lib/rate_limit');

const { logger } = o11y;
const app = express();
const PORT = Number(process.env.PORT) || 3302;
const PROJECT_ROOT = path.join(__dirname, '..');

app.use(o11y.requestId);
app.use(o11y.httpLogger);

app.use(helmet({
  contentSecurityPolicy: false,
  crossOriginEmbedderPolicy: false,
  crossOriginResourcePolicy: { policy: 'cross-origin' },
}));

// CORS as in app.js: credentials only for the origins listed in
// CORS_ALLOWED_ORIGINS (here the engine's address, whose screens call this
// service for sign-in) and the native WebView origins.
const corsOrigins = (process.env.CORS_ALLOWED_ORIGINS || '')
  .split(',').map((s) => s.trim()).filter(Boolean);
const corsIsDev = (process.env.NODE_ENV || 'development') !== 'production';
const nativeOrigins = new Set(['capacitor://localhost', 'ionic://localhost', 'https://localhost', 'http://localhost']);
app.use(cors((req, cb) => {
  const origin = req.headers.origin;
  const isNative = !!origin && nativeOrigins.has(origin);
  const isConfigured = !!origin && corsOrigins.includes(origin);
  const allowed = !origin || isNative || isConfigured || (corsOrigins.length === 0 && corsIsDev);
  cb(null, { origin: allowed ? (origin || true) : false, credentials: isConfigured || isNative });
}));
app.use('/api/setup/org', express.json({ limit: '320kb' }));
app.use(express.json());
app.use(cookieParser());

// CSRF: the same origin check as app.js.
function csrfOriginCheck(req, res, next) {
  if (req.method === 'GET' || req.method === 'HEAD' || req.method === 'OPTIONS') return next();
  if (!req.cookies?.rt_access && !req.cookies?.rt_refresh) return next();
  const isTrusted = (originStr) => {
    if (!originStr) return false;
    if (corsOrigins.includes(originStr)) return true;
    if (corsOrigins.length === 0 && corsIsDev) return true;
    try { return new URL(originStr).host === req.headers.host; } catch { return false; }
  };
  const origin = req.headers.origin;
  if (origin) return isTrusted(origin) ? next() : res.status(403).json({ error: 'Origin not allowed' });
  const referer = req.headers.referer;
  if (referer) {
    try { if (isTrusted(new URL(referer).origin)) return next(); } catch { /* reject below */ }
    return res.status(403).json({ error: 'Origin not allowed' });
  }
  return res.status(403).json({ error: 'Origin required' });
}
app.use(csrfOriginCheck);

app.get('/api/health', (req, res) => {
  res.json({ status: 'ok', service: 'RackTrack sign-in' });
});
app.get('/healthz', o11y.healthHandler);

// The asset token the RT-EOS client asks for after sign-in (app.js). The
// engine does not read it; it is kept so the client's start-up call succeeds.
app.get('/api/assets/token', auth.requireAuth, (req, res) => {
  const u = req.user;
  const secret = process.env.JWT_SECRET;
  if (!secret) return res.status(503).json({ error: 'Asset tokens unavailable' });
  const token = jwt.sign({
    scope: 'asset', role: u.role, tenantId: u.tenant_id ?? null, organizationId: u.organization_id ?? null,
  }, secret, { expiresIn: 12 * 60 * 60 });
  res.json({ token, expiresIn: 12 * 60 * 60 });
});

// Credential endpoints are throttled per account (app.js authLimit).
const authLimit = uploadLimiter({
  rate: 10, burst: 20,
  keyFn: (req) => {
    const id = String(req.body?.username || req.body?.email || '').trim().toLowerCase();
    return id ? `auth:${id}` : `authip:${req.ip || req.socket?.remoteAddress || 'unknown'}`;
  },
});
app.use(['/api/auth/login', '/api/auth/signup', '/api/auth/forgot-password',
  '/api/auth/reset-password', '/api/auth/login-with-code',
  '/api/auth/verify-reset-code', '/api/auth/verify', '/api/auth/resend-code'], authLimit);
auth.registerRoutes(app);
require('./socialAuth').registerRoutes(app);

app.use('/api/setup', auth.requireAuth, require('./routes/setup'));
app.use('/api/scan-sites', auth.requireAuth, require('./routes/scan_sites'));
app.use('/api', require('./routes/home')({ requireAuth: auth.requireAuth }));
app.use('/api', require('./routes/org_data')({ requireAuth: auth.requireAuth, audit }));

// Anything else under /api is not this service's.
app.use('/api', (req, res) => res.status(404).json({ error: 'Not found' }));

// The RT-EOS screens, built once with the base path /org/ (auth.sh builds
// them when the folder is missing). Client routes fall back to index.html; a
// missing file with an extension is a 404, as in app.js.
const clientDist = process.env.RT_ORG_CLIENT_DIST || path.join(PROJECT_ROOT, 'client', 'dist-org');
if (fs.existsSync(clientDist)) {
  app.use('/org', express.static(clientDist));
  app.get(/^\/org(\/.*)?$/, (req, res, next) => {
    if (path.extname(req.path)) return next();
    res.sendFile(path.join(clientDist, 'index.html'));
  });
} else {
  logger.warn({ event: 'org_client.missing', dir: clientDist }, 'the /org/ screens are not built');
}
// The client's own pictures are asked for at the root (/logo.jpg).
if (fs.existsSync(clientDist)) {
  app.use(express.static(clientDist, { index: false, fallthrough: true }));
}
app.get('/', (req, res) => res.redirect('/org/'));

app.use(o11y.errorHandler);

if (require.main === module) {
  const host = process.env.RT_AUTH_HOST || '127.0.0.1';
  const server = app.listen(PORT, host, () => {
    logger.info({ event: 'server.listening', port: PORT, host }, `sign-in service on http://${host}:${PORT}`);
  });
  const stop = () => { server.close(() => process.exit(0)); setTimeout(() => process.exit(0), 3000).unref(); };
  process.on('SIGINT', stop);
  process.on('SIGTERM', stop);
}

module.exports = app;
