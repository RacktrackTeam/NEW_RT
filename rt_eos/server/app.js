const path     = require('path');
const fs       = require('fs');

// The one secrets file, <repo>/.env, before anything reads process.env.
require('./lib/load_env');

const express  = require('express');
const cors     = require('cors');
const cookieParser = require('cookie-parser');
const multer   = require('multer');
const crypto   = require('crypto');
const { spawn: spawnChild } = require('child_process');
const sharp    = require('sharp');
const { v4: uuidv4 } = require('uuid');
const { WorkerPool, isFatalWorkerError } = require('./worker-pool');
const auth = require('./auth');
const audit = require('./audit');
const tenant = require('./lib/tenant');
const estate = require('./lib/estate');
const rackNaming = require('./lib/rack_naming');
const busy = require('./lib/busy');
const rackHeight = require('./lib/rack_height');
const scanSite = require('./lib/scan_site');
const rackAccess = require('./lib/rack_access');
const ocrCache = require('./lib/ocr_cache');
const imageIntake = require('./lib/image_intake');
const orphanGC = require('./lib/orphan_gc');
const store = require('./lib/store');
const orgSettings = require('./lib/org_settings');
const jwt = require('jsonwebtoken');
const scanJobs = require('./lib/scan-jobs');
const helmet = require('helmet');
const { uploadLimiter } = require('./lib/rate_limit');
// Central observability - must be required before anything that wants to
// log structured events. Provides logger + metrics + middleware + helpers.
const o11y = require('./lib/observability');
const { logger, withSpan, recordEvent } = o11y;
const { pythonCmd: resolvePython } = require('./lib/python');

// Best-effort: extract userId from a Bearer token if present and valid.
// Returns null when no token, an invalid token, or the auth module's secret
// path can't be read. Routes that *require* auth still use auth.requireAuth.
function softAuthUserId(req) {
  return softAuthPayload(req)?.sub || null;
}

// Resolve the signing key EXACTLY as auth.js does: JWT_SECRET from .env, else
// the key generated into the data folder. Reading only one of the two used to
// make auth.js sign with one key while this verified with the other: every
// verify failed, softAuthPayload returned null, and app.param('rackId') waved
// every request past the tenant check.
function jwtSecret() {
  if (process.env.JWT_SECRET) return process.env.JWT_SECRET;
  // The same fallback file auth.js signs with, beside the auth database.
  const secretPath = require('./lib/data_paths').FALLBACK_SIGNING_KEY_FILE;
  if (!fs.existsSync(secretPath)) return null;
  return fs.readFileSync(secretPath, 'utf8').trim();
}

// Same as above but returns the whole JWT payload (so callers can also
// read tenantId for tenant-scoped reads on otherwise public routes).
function softAuthPayload(req) {
  // Reads the cookie as well as the header. Its contract is "no payload means
  // treat as anonymous", so the moment browsers stopped sending Authorization
  // every signed-in user would have silently looked anonymous on the ~25
  // endpoints that scope their reads through this - no error, just other
  // tenants' data quietly missing.
  //
  // Verification is delegated to auth.verifyAccessToken rather than done here.
  // The old local jwt.verify pinned no algorithm and asserted no issuer or
  // audience, so it accepted tokens requireAuth would have rejected; and it
  // re-read the secret from disk, returning null for every request whenever
  // JWT_SECRET came from the environment instead of the generated file.
  const bearer = (req.headers.authorization || '').match(/^Bearer\s+(.+)$/i)?.[1];
  const token = req.cookies?.rt_access || bearer;
  return token ? auth.verifyAccessToken(token) : null;
}

// ── Asset links ──────────────────────────────────────────────────────
// An <img> cannot send an Authorization header, so rack photographs need a
// capability that rides in the query string. This carries the caller's role
// and Site rather than a specific rack, so one token covers every image the
// user is entitled to - and it is re-checked against canAccessRack on each
// request, so it grants no more than the session it came from.
//
// Long enough that images do not break mid-session, but still expiring,
// unlike the rack id it replaces.
const ASSET_TOKEN_TTL_SEC = 12 * 60 * 60;

function signAssetToken(auth) {
  const secret = jwtSecret();
  if (!secret || !auth) return null;
  return jwt.sign({
    scope: 'asset',
    role: auth.role,
    tenantId: auth.tenantId ?? auth.tenant_id ?? null,
    organizationId: auth.organizationId ?? auth.organization_id ?? null,
  }, secret, { expiresIn: ASSET_TOKEN_TTL_SEC });
}

/** The principal an asset token stands for, or null. Scope-checked, so a
 *  session JWT cannot be used as an asset token or vice versa. */
function verifyAssetToken(token) {
  if (!token) return null;
  try {
    const secret = jwtSecret();
    if (!secret) return null;
    const p = jwt.verify(token, secret);
    return p && p.scope === 'asset' ? p : null;
  } catch { return null; }
}

const app  = express();
const PORT = process.env.PORT || 3001;

const PROJECT_ROOT = path.join(__dirname, '..');
const CONFIG_PATH  = path.join(PROJECT_ROOT, 'config.json');

// Every file a scan writes lives in the data tree, and lib/store.js is the one
// module that builds a path into it (see the layout written there).
store.init();
// In-flight uploads and temporary stitched images. A file here is removed as
// soon as photo.jpg exists for its scan; the daily job clears anything older
// than a day that a crash left behind.
const uploadsDir = store.cachePath('uploads');
ocrCache.init(store.cachePath('ocr'));

// A scan's engine folder: the working files the engine writes (what used to be
// the whole per-rack folder). For an id the index does not hold this
// is a path that never exists, so every "is it there" check answers no.
const _NO_SCAN_DIR = path.join(require('os').tmpdir(), 'rt-eos-no-such-scan');
function engineDir(rackId) {
  try { return store.file(rackId, 'engine'); }
  catch { return path.join(_NO_SCAN_DIR, String(rackId || '').replace(/[^A-Za-z0-9-]/g, '').slice(0, 40) || 'none'); }
}
// The scan's photo (photo.jpg), or null when there is none.
function photoPathOf(rackId) {
  try {
    const p = store.file(rackId, 'photo');
    return fs.existsSync(p) ? p : null;
  } catch { return null; }
}
// URL of one of a scan's files, served by GET /api/scans/:scanId/files/...
function scanFileUrl(rackId, name) {
  return `/api/scans/${encodeURIComponent(rackId)}/files/${name}`;
}
// URL of an image the engine drew (under engine/), relative to engine/.
function engineImageUrl(rackId, rel) {
  return scanFileUrl(rackId, `engine/${String(rel).replace(/\\/g, '/')}`);
}

// Windows sometimes keeps a lingering handle on files sharp just wrote,
// causing transient EPERM on unlink. Retry briefly, then give up - a
// leftover tmp file is harmless.
function safeUnlink(p) {
  if (!p || !fs.existsSync(p)) return;
  for (let i = 0; i < 5; i++) {
    try { fs.unlinkSync(p); return; }
    catch (err) {
      if (err.code !== 'EPERM' && err.code !== 'EBUSY') return;
      // tight synchronous retry - usually clears within ~50ms
      const until = Date.now() + 50;
      while (Date.now() < until) {}
    }
  }
}

// Resolve a pipeline image filename to a real path inside the engine folder.
// Prefers the images/ subfolder, falls back to the folder itself.
function rackImagePath(rackDir, fname) {
  const inSub = path.join(rackDir, 'images', fname);
  if (fs.existsSync(inSub)) return inSub;
  return path.join(rackDir, fname);
}
// Same idea but returns the path relative to the engine folder, for
// engineImageUrl().
function rackImageUrlPath(rackDir, fname) {
  return fs.existsSync(path.join(rackDir, 'images', fname)) ? `images/${fname}` : fname;
}
// Per-port artifacts (5/6 PNG copies) - new layout: <rack>/ports/<base>.png
function rackPortPath(rackDir, baseName) {
  const dir = path.join(rackDir, 'ports');
  fs.mkdirSync(dir, { recursive: true });
  const newPath = path.join(dir, baseName);
  const legacy  = path.join(rackDir, `port_${baseName}`);
  if (!fs.existsSync(newPath) && fs.existsSync(legacy)) return legacy;
  return newPath;
}
function rackPortRelative(rackDir, baseName) {
  // What we store in the JSONL log so the result can find the file later.
  // Always returns the new-layout name; reading code resolves either path.
  return path.join('ports', baseName).replace(/\\/g, '/');
}

// ── Tenant guard for any route that takes :rackId ────────────────────
// Express's `app.param` callback fires whenever a route definition has
// `:rackId` in its path, just before the handler runs. This means EVERY
// scan and OCR endpoint that takes a rackId is automatically
// gated by tenant ownership - no per-route wiring needed.
//
// Behavior:
//   * Authenticated request whose tenant doesn't own this rack → 404
//     (404 not 403 - don't reveal that the rack exists in another tenant)
//   * Unauthenticated request → falls through (preserves legacy
//     dev/test access; the routes themselves can require auth if they want)
// Role-aware rack access (single source of truth for owner/admin/member
// visibility). Owner → every rack; org admin → racks owned by a Site in their
// org; member → their own Site. Unauthenticated callers are left to the
// route's own logic. Returns true if access is allowed.
function canAccessRack(auth, rackId) {
  // COMPATIBILITY: shipped app builds (<= 17) call /api/select and the
  // /api/feedback/* routes with a plain fetch() that sends no Authorization
  // header, so `auth` is null for them. Failing closed here returned
  // "Rack not found" on Find Port and silently broke every feedback action for
  // everyone still on those builds.
  //
  // NOW CLOSED. The relaxation above was temporary, to keep app builds <= 17
  // working after they had already shipped. Two things make it safe to close:
  // every client call to these routes goes through authFetch, and the tunnel
  // moved to a new domain, so builds <= 20 point at a host that no longer
  // exists and cannot reach this server at all. Any client that can talk to us
  // is build 21+, and build 21 sends credentials.
  // Delegates to lib/rack_access so this and the app.param guard below are one
  // policy rather than two copies that disagree - which is precisely what an
  // audit found them doing.
  return rackAccess.canAccessRack(auth, rackId, tenant);
}

// Fail CLOSED throughout. This used to `return next()` for a member whose
// tenantId is NULL - handing any such principal every rack on the platform -
// while canAccessRack refused the identical case. One policy, two guards, two
// answers. Both now come from lib/rack_access.
app.param('rackId', rackAccess.rackOwnershipParam({
  tenant,
  logger,
  getPrincipal: softAuthPayload,
}));

// ── Observability middleware (must be installed before any routes) ───
// Order matters: requestId first (so other middleware can read req.id) →
// httpLogger (logs each request, inherits requestId) → httpMetrics
// (records duration histogram) → cors/json/static → routes.
app.use(o11y.requestId);
app.use(o11y.httpLogger);
app.use(o11y.httpMetrics);

// Security headers. CSP is left to the client's own build (the SPA ships
// separately); CORP is set to cross-origin so the web/native client can load
// scan images (GET /api/scans/:id/files/...) across origins. This still gives us
// HSTS, X-Content-Type-Options, X-Frame-Options, Referrer-Policy, and drops
// the x-powered-by banner.
app.use(helmet({
  contentSecurityPolicy: false,
  crossOriginEmbedderPolicy: false,
  crossOriginResourcePolicy: { policy: 'cross-origin' },
}));

// CORS: allow-list from CORS_ALLOWED_ORIGINS (comma-separated). In dev, an
// empty list falls back to reflecting any origin so localhost:5173 etc. just
// work. In prod, an empty list means same-origin only (no CORS headers).
const _corsOrigins = (process.env.CORS_ALLOWED_ORIGINS || '')
  .split(',').map(s => s.trim()).filter(Boolean);
const _corsIsDev = (process.env.NODE_ENV || 'development') !== 'production';
// The Capacitor (iOS/Android) app runs from a FIXED local WebView origin and
// calls this API cross-origin, so unless these are allowed the packaged app
// gets no Access-Control-Allow-Origin and EVERY request dies as "Load failed"
// - the exact symptom testers hit at login, while the web app (same-origin) is
// unaffected. Allowing them is safe: auth is a Bearer token the client reads
// from its OWN origin's localStorage (never a cookie), and fetch never uses
// credentials:'include', so CORS carries no ambient credential a malicious
// local page could ride. iOS = capacitor://localhost, Android (default https
// scheme) = https://localhost, plus http/ionic variants for older configs.
const _nativeOrigins = new Set([
  'capacitor://localhost',
  'ionic://localhost',
  'https://localhost',
  'http://localhost',
]);
// Credentials are granted PER ORIGIN, not globally, and only to origins an
// operator listed explicitly in CORS_ALLOWED_ORIGINS.
//
// The safety argument in the comment above - that allowing http(s)://localhost
// costs nothing - holds only while no ambient credential rides along. Now that
// browser sessions are cookies, a blanket `credentials: true` would hand every
// page running on a user's own machine (any local dev server, any other app
// serving on localhost) the ability to make cookie-bearing calls to this API
// and read the replies. The native app authenticates with a Bearer token and
// needs no credentials at all, so it keeps CORS access without them.
app.use(cors((req, cb) => {
  const origin = req.headers.origin;
  const isNative     = !!origin && _nativeOrigins.has(origin);
  const isConfigured = !!origin && _corsOrigins.includes(origin);
  const allowed = !origin || isNative || isConfigured
    || (_corsOrigins.length === 0 && _corsIsDev);
  cb(null, {
    origin: allowed ? (origin || true) : false,
    // Native counts as credentialed too.
    //
    // _nativeOrigins is a fixed, hardcoded set - the WebView origins our own
    // packaged app runs from - not something an operator or a caller can
    // widen, so this is not the blanket `credentials: true` the note above
    // warns against.
    //
    // It has to be here because the client now sends `credentials:'include'`
    // on every request, and a browser DISCARDS the response to such a request
    // unless the server answers Access-Control-Allow-Credentials: true. The
    // app was allowed through CORS, served correctly, and then had every reply
    // thrown away by its own WebView - indistinguishable from the API being
    // down, while the same server answered the web app (same-origin, and
    // configured) perfectly.
    credentials: isConfigured || isNative,
  });
}));
// The organisation profile carries a small logo as a data: URI (200 KB
// decoded, ~275 KB as base64). The default 100 kb JSON limit below would
// refuse it with a 413 before the setup router ever saw it, so that one path
// is parsed here with a larger limit; the parser below then skips a body
// already read.
app.use('/api/setup/org', express.json({ limit: '320kb' }));
app.use(express.json());
// Must precede anything that reads req.cookies - requireAuth, softAuthPayload
// and the CSRF check below all do.
app.use(cookieParser());

// CSRF: a cookie is sent by the browser automatically, so a form on someone
// else's site could trigger a state-changing call as the signed-in user. The
// Bearer path has never had this problem - an attacker cannot set a header on
// a cross-site request - so the check only engages when a request actually
// carries one of our auth cookies. That keeps the native app, curl and
// server-to-server callers (which authenticate by Bearer or app key, and have
// no ambient credential to abuse) working unchanged.
//
// SameSite=lax already blocks the classic cross-site POST; this is the second
// line, and the one that still holds if SameSite is ever loosened.
function csrfOriginCheck(req, res, next) {
  if (req.method === 'GET' || req.method === 'HEAD' || req.method === 'OPTIONS') return next();
  if (!req.cookies?.rt_access && !req.cookies?.rt_refresh) return next();

  const isTrusted = (originStr) => {
    if (!originStr) return false;
    if (_corsOrigins.includes(originStr)) return true;
    if (_corsOrigins.length === 0 && _corsIsDev) return true;
    try { return new URL(originStr).host === req.headers.host; } catch { return false; }
  };

  const origin = req.headers.origin;
  if (origin) {
    return isTrusted(origin) ? next() : res.status(403).json({ error: 'Origin not allowed' });
  }
  const referer = req.headers.referer;
  if (referer) {
    try {
      if (isTrusted(new URL(referer).origin)) return next();
    } catch { /* unparseable - fall through to reject */ }
    return res.status(403).json({ error: 'Origin not allowed' });
  }
  // A browser always sends one of the two on a mutating request. Neither
  // present, yet a session cookie attached, is not a shape a browser produces.
  return res.status(403).json({ error: 'Origin required' });
}
app.use(csrfOriginCheck);
// Rack imagery is readable only by someone who may open the scan.
//
// A rack id used to be a permanent bearer credential for its photos, and ids
// travel: they sit in SPA URLs, browser history and proxy logs. Two ways in,
// both of them checked (GET /api/scans/:scanId/files/... below):
//   * an asset token - a session-derived capability the client appends to
//     <img> URLs, because an image tag cannot send an Authorization header
//   * a normal session (cookie or Bearer)
//
// The file path is decoded and normalised BEFORE anything is decided, so a
// percent-encoded or backslash spelling cannot walk out of the scan folder.
function normalizedAssetPath(p) {
  let decoded = String(p || '');
  // Decode repeatedly: a single pass leaves %252e as %2e, which the filesystem
  // layer would then decode again.
  for (let i = 0; i < 3; i++) {
    let next;
    try { next = decodeURIComponent(decoded); } catch { return null; }
    if (next === decoded) break;
    decoded = next;
  }
  if (decoded.includes('\0')) return null;
  // Windows accepts backslash as a separator; posix normalize does not collapse
  // it, so fold it before normalising or `..\` walks out of the tree.
  const unified = decoded.replace(/\\/g, '/');
  const norm = path.posix.normalize(unified);
  // After normalising, a leading `..` means the path escapes the mount root.
  if (norm.startsWith('../') || norm === '..') return null;
  return norm;
}

// ── A scan's files ───────────────────────────────────────────────────
// GET /api/scans/:scanId/files/photo.jpg        the photo as taken
// GET /api/scans/:scanId/files/annotated.jpg    the drawn result
// GET /api/scans/:scanId/files/thumb.jpg        360 px wide, made on first request
// GET /api/scans/:scanId/files/crops/<name>     device, port and label crops
// GET /api/scans/:scanId/files/engine/<path>    an image the engine drew
// GET /api/scans/:scanId/files/zones/<id>.jpg   a zone close-up (routes/physical.js)
//
// This replaces the old /outputs and /uploads static mounts. The scan id is
// resolved through the store's index, never by walking a folder, and the
// same rule as the scan itself decides who may read it: a session (cookie or
// Bearer) or an asset token (?t=, because an <img> cannot send a header) whose
// holder may open the scan. Anyone else is told the file is not there. Only
// images are served; a scan's JSON is reachable only through the scan routes.
const THUMB_WIDTH = 360;
const _thumbBuilds = new Map();

async function ensureThumb(scanId) {
  const thumb = store.file(scanId, 'thumb');
  if (fs.existsSync(thumb)) return thumb;
  if (_thumbBuilds.has(scanId)) return _thumbBuilds.get(scanId);
  const run = (async () => {
    const photo = photoPathOf(scanId);
    if (!photo) return null;
    const tmp = `${thumb}.${process.pid}.tmp`;
    await sharp(photo).rotate().resize({ width: THUMB_WIDTH, withoutEnlargement: true })
      .jpeg({ quality: 80 }).toFile(tmp);
    fs.renameSync(tmp, thumb);
    return thumb;
  })().finally(() => { _thumbBuilds.delete(scanId); });
  _thumbBuilds.set(scanId, run);
  return run;
}

// annotated.jpg: the engine's drawing of the rack (devices and units), as a
// JPEG at quality 85. Written after every analysis; made here too when a scan
// is asked for before that step ran.
async function writeAnnotated(scanId) {
  const dir = engineDir(scanId);
  const src = ['2_devices_only.png', '3_units_and_devices.png']
    .map((f) => rackImagePath(dir, f)).find((p) => fs.existsSync(p));
  if (!src) return null;
  const dest = store.file(scanId, 'annotated');
  const tmp = `${dest}.${process.pid}.tmp`;
  await sharp(src).flatten({ background: '#ffffff' }).jpeg({ quality: 85 }).toFile(tmp);
  fs.renameSync(tmp, dest);
  return dest;
}

const SCAN_IMAGE_RE = /\.(jpe?g|png|webp)$/i;
app.get(/^\/api\/scans\/([^/]+)\/files\/(.+)$/, async (req, res) => {
  const notFound = () => res.status(404).json({ error: 'Not found' });
  let scanId;
  try { scanId = decodeURIComponent(req.params[0]); } catch { return notFound(); }
  scanId = String(scanId).toUpperCase();
  if (!rackAccess.isValidRackId(scanId)) return notFound();
  const rel = normalizedAssetPath(req.params[1]);
  if (!rel || !SCAN_IMAGE_RE.test(rel)) return notFound();
  const top = rel.split('/')[0];
  const allowed = rel === 'photo.jpg' || rel === 'annotated.jpg' || rel === 'thumb.jpg'
    || ((top === 'crops' || top === 'engine' || top === 'zones') && rel.split('/').length >= 2);
  if (!allowed) return notFound();

  const principal = softAuthPayload(req) || verifyAssetToken(req.query.t);
  if (!principal) return res.status(401).json({ error: 'Authentication required' });
  if (!rackAccess.canAccessRack(principal, scanId, tenant)) return notFound();
  if (!store.has(scanId)) return notFound();

  try {
    const dir = store.scanDir(scanId);
    let file = path.join(dir, rel);
    if (!file.startsWith(dir + path.sep)) return notFound();
    if (rel === 'thumb.jpg') file = await ensureThumb(scanId);
    else if (rel === 'annotated.jpg' && !fs.existsSync(file)) file = await writeAnnotated(scanId);
    if (!file || !fs.existsSync(file) || !fs.statSync(file).isFile()) return notFound();
    res.setHeader('Cache-Control', 'private, max-age=60');
    return res.sendFile(file);
  } catch (err) {
    logger.warn({ event: 'scan.file_failed', scanId, file: rel, err: err.message }, 'scan file not served');
    return notFound();
  }
});

// Health + metrics - placed early so they bypass auth/static/etc and
// stay reachable even if the main app is degraded.
// The client asks for this once after sign-in and appends it to image URLs.
// Authenticated, so the token can only ever encode a session the caller
// already holds.
app.get('/api/assets/token', auth.requireAuth, (req, res) => {
  const token = signAssetToken(req.user);
  if (!token) return res.status(503).json({ error: 'Asset tokens unavailable' });
  res.json({ token, expiresIn: ASSET_TOKEN_TTL_SEC });
});

app.get('/healthz', o11y.healthHandler);
app.get('/metrics', o11y.metricsHandler);

// Which build is actually running - no auth, so you can hit
//   https://<tunnel>/api/version
// and instantly see the live commit + when the server last started. Ends the
// "did my push deploy?" guesswork.
const BUILD_COMMIT = (() => {
  try {
    return require('child_process')
      .execSync('git rev-parse --short HEAD', { cwd: path.join(__dirname, '..') })
      .toString().trim();
  } catch { return 'unknown'; }
})();
const SERVER_STARTED_AT = new Date().toISOString();
app.get('/api/version', (req, res) => {
  res.json({ commit: BUILD_COMMIT, startedAt: SERVER_STARTED_AT });
});

// Organisation setup - the estate tree behind the minimal setup: what a
// datacentre (a Site) contains, who its site contact is, which rules it
// accepted, and whether it is set up enough to scan. Read and written from
// the app, which gates on GET /api/setup/state.
//
// Mounted behind requireAuth; who may read or write WHICH Site is decided inside the router (owner, the
// org's admin, the Site's manager write; the Site's members read; strangers
// get 404). /api/analyze is deliberately NOT gated on canScan - the client
// decides what to show; the server keeps accepting scans.
try {
  app.use('/api/setup', auth.requireAuth, require('./routes/setup'));
  logger.info({ event: 'router.loaded', router: 'setup', prefix: '/api/setup' },
    'setup router loaded');
} catch (err) {
  logger.warn({ event: 'router.load_failed', router: 'setup', err: err.message },
    'setup router not loaded');
}

// The Sites the scan screen offers - one list for every role, scoped on the
// server: a technician gets their one Site, an organisation admin every Site
// of the organisation. The scan routes below take the chosen one as `siteId`
// (lib/scan_site.resolve). No coordinates: a person names the Site.
try {
  app.use('/api/scan-sites', auth.requireAuth, require('./routes/scan_sites'));
  logger.info({ event: 'router.loaded', router: 'scan_sites', prefix: '/api/scan-sites' },
    'scan sites router loaded');
} catch (err) {
  logger.warn({ event: 'router.load_failed', router: 'scan_sites', err: err.message },
    'scan sites router not loaded');
}

// The patching matrix of a Site (routes/matrix.js, lib/matrix.js).
try {
  app.use('/api/matrix', auth.requireAuth, require('./routes/matrix')({ audit }));
  logger.info({ event: 'router.loaded', router: 'matrix', prefix: '/api/matrix' }, 'matrix router loaded');
} catch (err) {
  logger.warn({ event: 'router.load_failed', router: 'matrix', err: err.message }, 'matrix router not loaded');
}
// Home, the Site overview, and an organization's data settings. Each route
// carries requireAuth itself: these routers sit on the /api prefix, which
// also serves the sign-in routes.
try {
  app.use('/api', require('./routes/home')({ requireAuth: auth.requireAuth }));
  app.use('/api', require('./routes/org_data')({ requireAuth: auth.requireAuth, audit }));
  logger.info({ event: 'router.loaded', router: 'home+org_data', prefix: '/api' }, 'home and org data routers loaded');
} catch (err) {
  logger.warn({ event: 'router.load_failed', router: 'home+org_data', err: err.message }, 'home and org data routers not loaded');
}

// The physical-layer work on a scan: zone close-ups, the sheet check, the
// A-end / Z-end label check, the worklist, device cards and reports
// (routes/physical.js). Mounted here, before the static client, like the
// routers above; each route carries requireAuth.
try {
  app.use('/api', require('./routes/physical')({
    requireAuth: auth.requireAuth,
    audit,
    saveDeviceFeedback: (req, body) => saveDeviceFeedback(req, body),
    readZone: (args) => runZoneRead(args),
    // A person's make and model: the scan's result.json is rebuilt at once.
    refreshResult: (scanId) => writeCanonicalScanResult(scanId),
  }));
  logger.info({ event: 'router.loaded', router: 'physical', prefix: '/api' }, 'physical-layer router loaded');
} catch (err) {
  logger.warn({ event: 'router.load_failed', router: 'physical', err: err.message }, 'physical-layer router not loaded');
}

// Read-only views for the result tabs, the two-rack map and the Switches
// screen (routes/detail.js): one scan in full, and the site's switches.
try {
  app.use('/api', require('./routes/detail')({ requireAuth: auth.requireAuth }));
  logger.info({ event: 'router.loaded', router: 'detail', prefix: '/api' }, 'detail router loaded');
} catch (err) {
  logger.warn({ event: 'router.load_failed', router: 'detail', err: err.message }, 'detail router not loaded');
}

// Read one zone close-up (pipeline/zone_read.py): in the warm worker when the
// pool is up, else in a fresh Python process. Tests replace it with
// app.setZoneReader(fn).
let _zoneReaderOverride = null;
function setZoneReader(fn) { _zoneReaderOverride = fn || null; }
async function runZoneRead({ imagePath, from, to }) {
  if (_zoneReaderOverride) return _zoneReaderOverride({ imagePath, from, to });
  try {
    const r = await pool.request('zone_read', { image_path: imagePath, config_path: CONFIG_PATH, from, to });
    if (r && r.ok) return r;
    if (r && r.error && !/Unknown command/.test(r.error)) return r;
    throw new Error(r?.error || 'the worker could not read it');
  } catch (e) {
    logger.warn({ event: 'zone.pool_failed', err: e.message }, 'zone close-up read fell back to a fresh process');
  }
  return new Promise((resolve) => {
    const child = spawnChild(pythonCmd, ['-u', '-m', 'pipeline.zone_read', '--image', imagePath, '--from', String(from), '--to', String(to), '--config', CONFIG_PATH], {
      cwd: PROJECT_ROOT, env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8', YOLO_VERBOSE: 'False' }, windowsHide: true,
    });
    let out = '';
    let err = '';
    const killer = setTimeout(() => { try { child.kill('SIGKILL'); } catch (_) {} }, 180_000);
    child.stdout.on('data', (c) => { out += c.toString(); });
    child.stderr.on('data', (c) => { err += c.toString(); });
    child.on('error', (e) => { clearTimeout(killer); resolve({ ok: false, error: e.message }); });
    child.on('close', () => {
      clearTimeout(killer);
      const line = out.trim().split('\n').filter(Boolean).pop() || '';
      try { resolve(JSON.parse(line)); } catch { resolve({ ok: false, error: err.slice(-400) || 'no answer' }); }
    });
  });
}

const clientDist = path.join(PROJECT_ROOT, 'client', 'dist');
if (fs.existsSync(clientDist)) {
  app.use(express.static(clientDist));
}

// ── File upload ───────────────────────────────────────────────
// The extension is taken from a whitelist match on the original name, not
// copied verbatim - otherwise an upload named `evil.jpg.bat` (filter passes
// on first match) or one with embedded path chars could land on disk with an
// attacker-controlled suffix.
// Extension for a file we are willing to store, from the NAME first and the
// declared CONTENT TYPE second.
//
// Name-only was the whole test, and a filename is not something the client
// controls. A camera capture is built here as `capture_<ts>.jpg` and always
// passed; a gallery pick is forwarded as the raw File, carrying whatever name
// the OS attached - and an Android content:// pick frequently has no extension
// at all. The same photograph was therefore accepted when taken through the
// camera and rejected as "Invalid file type" when chosen from the gallery,
// which is exactly the "sometimes works, sometimes doesn't" the testers hit.
// Falling back to the MIME type makes the decision about what the file IS.
//
// Since then the decision moved again, to the bytes. The name and the declared
// type only choose the extension the tmp file is stored under (empty when
// neither says anything); what the file IS is read from its first bytes after
// the upload, in lib/image_intake.js, and anything the pipeline cannot decode
// is converted to JPEG there.
const MIME_EXT = {
  'image/jpeg': '.jpg',  'image/jpg':  '.jpg',  'image/pjpeg': '.jpg',
  'image/png':  '.png',  'image/gif':  '.gif',  'image/webp':  '.webp',
  'image/tiff': '.tiff', 'image/avif': '.avif', 'image/bmp':   '.bmp',
  'image/heic': '.heic', 'image/heif': '.heif',
  'video/mp4':  '.mp4',  'video/quicktime': '.mov',
  'video/webm': '.webm', 'video/x-m4v': '.m4v', 'video/avi': '.avi',
};
function _safeExt(originalName, mimetype) {
  const m = String(originalName || '').match(/\.(jpe?g|jfif|png|gif|webp|tiff?|avif|bmp|heic|heif|mp4|mov|webm|m4v|avi)$/i);
  if (m) return '.' + m[1].toLowerCase();
  // `image/jpeg; charset=…` and casing both show up in the wild.
  const mt = String(mimetype || '').split(';')[0].trim().toLowerCase();
  return MIME_EXT[mt] || '';
}
const storage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, uploadsDir),
  filename:    (req, file, cb) => {
    const ext = _safeExt(file.originalname, file.mimetype);
    cb(null, `tmp_${uuidv4()}${ext}`);
  },
});
// What the client SAYS the file is can only turn away something that is
// plainly not a picture (a PDF, a spreadsheet). Anything declared image/* or
// video/* is accepted, and so is a file with no usable type at all: an Android
// content:// pick often arrives as application/octet-stream or with an empty
// type, and rejecting those here was the "sometimes works" complaint. The real
// decision is made from the bytes once the file is on disk (intakeImage below).
function _declaredTypeAllowed(mimetype) {
  const mt = String(mimetype || '').split(';')[0].trim().toLowerCase();
  if (!mt || mt === 'application/octet-stream' || !mt.includes('/')) return true;
  return mt.startsWith('image/') || mt.startsWith('video/');
}
const upload = multer({
  storage,
  limits: { fileSize: 340 * 1024 * 1024 },
  fileFilter: (req, file, cb) => {
    const ok = _declaredTypeAllowed(file.mimetype);
    // Tag the rejection so the error handler answers 400 rather than 500.
    // A user picking the wrong file type is not a server fault, and every one
    // of these was polluting the uncaught-error rate that alerting watches.
    let err = null;
    if (!ok) { err = new Error('Invalid file type'); err.status = 400; }
    cb(err, ok);
  },
});

// Shared rate limiter for all upload-bound routes. Keyed by user id when
// available so multiple techs behind one NAT aren't starved by each other.
const scanLimit = uploadLimiter();
// The live viewfinder polls /api/detect every 400ms (~150/min) while the camera
// is open, but it shared the 20/min upload budget - so ~87% of frames were
// encoded, uploaded and then rejected with 429, and live detection effectively
// stopped a few seconds into every session. /api/detect is a lightweight
// detect-only call (no scan is written), so it gets its own realistic budget.
// Still bounded, so it remains a DoS guard rather than an open door.
const detectLimit = uploadLimiter({
  rate:  Number(process.env.RATE_LIMIT_DETECT_PER_MIN) || 180,
  burst: Number(process.env.RATE_LIMIT_DETECT_BURST)   || 60,
});

// Stricter limiter for credential endpoints - throttles brute-force login /
// password-reset attempts. Keyed by the ACCOUNT (username / email in the body),
// NOT by IP: every tester reaches the server through the same Cloudflare tunnel,
// so an IP key would lump all of them into one shared bucket and lock everyone
// out. Per-account, 20 burst + 10/min is invisible to a real user but still caps
// per-account guessing. Falls back to IP when the body carries no identifier.
const authLimit = uploadLimiter({
  rate: 10, burst: 20,
  keyFn: (req) => {
    const id = String(req.body?.username || req.body?.email || '').trim().toLowerCase();
    return id ? `auth:${id}` : `authip:${req.ip || req.socket?.remoteAddress || 'unknown'}`;
  },
});

// Crash reports from the client's error boundary. Deliberately unauthenticated:
// the most valuable report is the one from a screen that broke so badly the
// session could not be read, and a tester who hits a blank page cannot be asked
// to log in first. Everything is truncated and nothing is executed or echoed -
// this only ever appends to the log. Rate limited so it cannot be used to flood
// the log store.
const clientErrorLimit = uploadLimiter({ rate: 6, burst: 12 });
app.post('/api/client-error', clientErrorLimit, express.json({ limit: '32kb' }), (req, res) => {
  const s = (v, n) => String(v == null ? '' : v).slice(0, n);
  logger.warn({
    event: 'client.render_error',
    message:        s(req.body?.message, 300),
    path:           s(req.body?.path, 200),
    stack:          s(req.body?.stack, 2000),
    componentStack: s(req.body?.componentStack, 2000),
    userAgent:      s(req.body?.userAgent || req.get('user-agent'), 300),
  }, '[client] render error');
  res.status(204).end();
});

// ── Image intake ─────────────────────────────────────────────
// First thing every image upload route does with req.file. Reads the first
// bytes to learn what the file really is, and converts anything the pipeline
// cannot decode (HEIC/HEIF, AVIF, WebP, TIFF, BMP, GIF) to an upright RGB JPEG
// through pipeline/normalize_image.py, on the same interpreter the pipeline
// runs on. JPEG, PNG and video pass through on their original path. A file
// that is not a picture at all throws an Error tagged status 400 (see
// imageIntake.isUnreadable) with a plain message for the user.
//
// Returns { path, kind, converted, width, height }. When a converted copy
// replaced the upload, the original tmp file is removed here so uploads/ never
// keeps both; the caller owns `path` from then on exactly as it owned
// req.file.path before.
async function intakeImage(file) {
  const intake = await imageIntake.normalizeForPipeline(file.path, { pythonCmd, projectRoot: PROJECT_ROOT });
  if (intake.converted) safeUnlink(file.path);
  return intake;
}

// ── Image normalization ───────────────────────────────────────
// Applies EXIF rotation and re-encodes to JPEG so downstream code (cv2,
// pipeline) always sees an upright standard JPEG. Pass the intake result so a
// copy pipeline/normalize_image.py already wrote is not re-encoded (it is
// already upright RGB JPEG with the orientation tag gone) and so video is
// recognised by its bytes rather than by an extension the upload may not have.
async function normalizeImage(inputPath, intake = null) {
  if (intake && intake.converted) return inputPath;
  const ext = path.extname(inputPath).toLowerCase();
  const isVideo = intake && intake.kind ? intake.kind === 'video' : /\.(mp4|mov|webm)$/i.test(ext);
  if (isVideo) {
    // Hand the video to the Python worker, which scores frames and writes
    // the best one to disk. From here on the rest of the pipeline treats
    // it as a normal photo upload.
    const outputPath = inputPath.replace(/\.[^.]+$/, '') + '_frame.jpg';
    const res = await pool.request('extract_best_frame', {
      video_path: inputPath,
      output_path: outputPath,
    });
    if (!res.ok) {
      safeUnlink(inputPath);
      throw new Error(res.error || 'Could not extract a frame from the video.');
    }
    safeUnlink(inputPath);
    return outputPath;
  }

  const outputPath = inputPath.replace(/\.[^.]+$/, '') + '_norm.jpg';
  await sharp(inputPath)
    .rotate()             // auto-orient from EXIF, strips the tag
    // JPEG has no alpha. Without this a transparent PNG comes out with black
    // where the viewer showed white (sharp drops the channel and keeps the RGB
    // underneath). White matches what pipeline/normalize_image.py does for the
    // formats it converts. A no-op for an image with no alpha channel: the
    // JPEG bytes, and so the rack id, are unchanged for every ordinary photo.
    .flatten({ background: '#ffffff' })
    .jpeg({ quality: 92, mozjpeg: true })
    .toFile(outputPath);
  safeUnlink(inputPath);
  return outputPath;
}

// ── Rack ID ───────────────────────────────────────────────────
// SHA-256 of the file contents, SCOPED TO THE OWNING ORGANISATION.
//
// Content-derived, so the same rack photo rescanned inside one organisation
// collapses onto the same id. That is deliberate: it is what lets several
// Sites (tenants) in an org co-own one RK- id and reuse a cached analysis.
//
// The scope prefix is what keeps that sharing INSIDE the org. This id keys
// both the scan's folder and rack_owners, so hashing content alone meant two
// unrelated organisations that uploaded the same photo (a stock image, or the
// same physical rack) landed on one id: they co-owned it, each passed the
// tenant gate for the other’s rack, and the second scan overwrote the
// first’s results. 34 such ids already exist in the live database.
function rackScope(auth) {
  if (auth && auth.organizationId) return `org:${auth.organizationId}`;
  // No organisation (the platform owner’s own Site) - fall back to the tenant
  // so an id is still never shared across unrelated accounts.
  if (auth && auth.tenantId) return `tenant:${auth.tenantId}`;
  return 'global';
}

function computeRackId(filePath, scope = 'global') {
  const hash = crypto
    .createHash('sha256')
    .update(String(scope))
    .update('\0')            // delimiter: scope cannot bleed into content
    .update(fs.readFileSync(filePath))
    .digest('hex');
  return `RK-${hash.slice(0, 8).toUpperCase()}`;
}

// ── Persistent Python worker pool ─────────────────────────────
// Workers preload the YOLO models named in config.json at boot and never
// re-read it - a model swap there (e.g. models.devices_seg) takes effect only
// on the next server restart.
const pythonCmd = resolvePython();
// RACKTRACK_WORKERS when set. Unset: 2 when the machine has 8 GB or more of
// free memory at start (each worker holds every model, a few GB), else 1.
function defaultWorkerCount(freeBytes = require('os').freemem()) {
  return freeBytes >= 8 * 1024 ** 3 ? 2 : 1;
}
const WORKER_COUNT = Math.max(1, parseInt(process.env.RACKTRACK_WORKERS, 10) || defaultWorkerCount());

// ── Worker pool, and the seam for testing without one ────────────────
//
// Three ways to get a pool, in precedence order:
//
//   RACKTRACK_POOL_MODULE=<path>  load that module as the pool. This is the
//     seam: it lets a test substitute a fake pipeline entirely, so the scan
//     path can be exercised in-process with no Python, no GPU and no model
//     weights. The module must export { request, shutdown }.
//   RACKTRACK_SKIP_WORKER_POOL=1  a stub whose request() throws.
//   otherwise                     the real pool.
//
// The skip flag used to be the only option, and it is a boolean - you could
// turn the pipeline off but not replace it. That left the product's core
// workflow (upload an image, get a rack back) with zero automated coverage by
// construction rather than by oversight: any test of /api/analyze either
// shelled out to real Python and model weights or could not run at all. A
// toggle cannot be stubbed; an injection point can.
let pool;
if (process.env.RACKTRACK_POOL_MODULE) {
  const modPath = path.isAbsolute(process.env.RACKTRACK_POOL_MODULE)
    ? process.env.RACKTRACK_POOL_MODULE
    : path.resolve(PROJECT_ROOT, process.env.RACKTRACK_POOL_MODULE);
  pool = require(modPath);
  if (typeof pool?.request !== 'function') {
    throw new Error(`RACKTRACK_POOL_MODULE ${modPath} must export a request() function`);
  }
  if (typeof pool.shutdown !== 'function') pool.shutdown = () => Promise.resolve();
  logger.warn({ event: 'worker_pool.injected', module: modPath },
    'worker pool replaced by RACKTRACK_POOL_MODULE - not for production use');
} else if (process.env.RACKTRACK_SKIP_WORKER_POOL === '1') {
  logger.warn({ event: 'worker_pool.disabled' },
    'RACKTRACK_SKIP_WORKER_POOL=1 - Python worker pool disabled; AI/ML routes will 500');
  pool = {
    request: () => { throw new Error('worker pool disabled (RACKTRACK_SKIP_WORKER_POOL=1)'); },
    shutdown: () => Promise.resolve(),
  };
} else {
  pool = new WorkerPool({
    size: WORKER_COUNT,
    pythonCmd,
    pythonArgs: ['-u', '-m', 'pipeline.worker'],
    cwd: PROJECT_ROOT,
    env: { ...process.env, PYTHONUNBUFFERED: '1', YOLO_VERBOSE: 'False' },
  });
}

// A scan that died because the GPU fell over is neither the photographer's
// fault nor a permanent failure: the pool has already killed the poisoned
// worker, so the next attempt runs on a fresh CUDA context. Say that - instead
// of blaming the photo, or pasting a torch traceback into the owner's feed,
// which is what "CUDA error: unknown error CUDA kernel errors might be
// asynchronously reported…" did to nine consecutive rows of it.
const ENGINE_DOWN_MSG =
  'The scan engine lost its GPU and is restarting itself. Please try that photo again in a moment.';
const ENGINE_DOWN_AUDIT = 'Scan engine GPU fault - worker recycled; scan is retryable';

async function runQualityCheck(imagePath) {
  return withSpan('pipeline.quality_check', async (log) => {
    try {
      return await pool.request('quality_check', { image_path: imagePath });
    } catch (err) {
      log.warn({ err: err.message }, 'quality_check skipped');
      return { ok: true, metrics: { note: 'check-failed-skipped' } };
    }
  }, { imagePath });
}

// The organization's training folder for the engine, or nothing when there is
// no organization (then the engine keeps no memory).
function alRootFor(orgId) {
  return orgId != null ? store.trainingDir(orgId) : null;
}

// `rackId` is the scan the output folder belongs to; when given, the drawn
// result (annotated.jpg) is written and the rack's own label is read. A re-read
// into a scratch folder passes none.
async function runPipelineAnalyze(imagePath, outputDir, orgId = null, rackId = null) {
  return withSpan('pipeline.analyze', async () => {
    const payload = {
      image_path: imagePath,
      config_path: CONFIG_PATH,
      output_dir:  outputDir,
    };
    // Org scope lets device-class active learning apply this org's prior
    // corrections while devices are detected (same as cable learning).
    if (orgId != null) { payload.org_id = orgId; payload.al_root = alRootFor(orgId); }
    const res = await pool.request('analyze', payload);
    if (!res.ok) throw new Error(res.error || 'pipeline analyze failed');
    if (rackId) {
      try { await writeAnnotated(rackId); } catch (err) {
        logger.warn({ event: 'scan.annotated_failed', rackId, err: err.message }, 'annotated.jpg not written');
      }
      // The rack's own label is read in the background from here, so it is
      // there by the time the results screen asks which rack this is. Never
      // awaited: a scan is finished when its devices are, and the label follows.
      try { ensureSideLabels(rackId).catch(() => {}); } catch (_) { /* best effort */ }
    }
    return res;
  }, { imagePath, outputDir });
}

async function runPipelineSelect(imagePath, outputDir, deviceIndex, port, portCategory, orgId, targetCount, indexOffset) {
  return withSpan('pipeline.select', async () => {
    const payload = {
      image_path:   imagePath,
      config_path:  CONFIG_PATH,
      output_dir:   outputDir,
      device_index: deviceIndex,
      port,
    };
    if (portCategory) payload.port_category = portCategory;
    if (orgId != null) { payload.org_id = orgId; payload.al_root = alRootFor(orgId); }   // org-scoped cable learning lookup
    if (targetCount > 0) payload.target_count = targetCount;  // user-confirmed count
    // User's port-number shift, so the DRAWN index labels match the corrected
    // numbering (port the model called N is labelled N+offset on the image).
    if (indexOffset) payload.index_offset = indexOffset;
    const res = await pool.request('select', payload);
    if (!res.ok) throw new Error(res.error || 'pipeline select failed');
    return res;
  }, { imagePath, outputDir, deviceIndex, port, portCategory });
}

// The main-port count this device is PUBLISHED with - the number the port
// dropdown, the "N detected" caption and the 1-N input bound all read. Select
// must lay out exactly this many ports so "port N" means the same physical
// position on both sides of the wire.
//
// This used to return a count ONLY for devices the user had manually
// relabeled, which left every other device selecting against a fresh
// re-detection instead of the published layout. Those two numbers disagree
// whenever detection isn't perfectly repeatable against the published count -
// most often on a device whose count came from OCR + device_db, where
// runner.py --detect_only deliberately publishes the catalogue count
// (e.g. 48) after the visual pass under-counted (e.g. 30). The UI then offered
// 1-48 while the pipeline held a 30-entry list, so:
//   * port 40 fell past the end of that list and located nothing, and
//   * ports inside it were the Nth DETECTED port, not the Nth physical one.
// Low port numbers happened to line up, which is why the first lookup on a
// device looked correct and a later, higher one came back wrong or empty.
//
// Preference order is unchanged where it matters: a user relabel still wins,
// because runner.py --detect_only writes it straight into port_count.
function publishedPortCountFor(rackId, deviceIndex) {
  try {
    const map = JSON.parse(fs.readFileSync(path.join(engineDir(rackId), 'device_unit_map.json'), 'utf8'));
    const dev = (map.devices || [])[Number(deviceIndex) - 1];
    // port_count is the published number regardless of how it was arrived at
    // (user relabel, OCR/device_db grounding, or the visual count). Pin the
    // layout to it so the UI's bound and the pipeline's list agree.
    if (dev && Number(dev.port_count) > 0) return Number(dev.port_count);
  } catch (_) {}
  return 0;
}

// Re-detect ports for one device using a user-supplied target count.
// Updates device_unit_map.json and returns the patched device.
async function runRelabelPortCount(rackDir, deviceIndex, targetCount) {
  return await pool.request('relabel_port_count', {
    rack_dir:     rackDir,
    device_index: deviceIndex,
    target_count: targetCount,
    config_path:  CONFIG_PATH,
  });
}

// ── Helpers ───────────────────────────────────────────────────
// meta.json, through the store. imagePath is never trusted from the file: a
// scan folder moves when its rack is named or its Site changes, so the photo's
// path is always the one the store gives now.
function readMeta(rackId) {
  // A truncated/corrupt meta.json (e.g. crash mid-write) must not throw -
  // callers treat null as "reconstruct or 404", not a 500.
  let m;
  try { m = store.readMeta(rackId); } catch { return null; }
  if (!m) return null;
  const photo = photoPathOf(rackId);
  return { ...m, imagePath: photo || null };
}

function writeMeta(rackId, meta) {
  // Atomic (tmp + rename) inside the store. The photo's absolute path is not
  // written: readMeta supplies the current one.
  const { imagePath: _ignored, ...rest } = meta || {};
  store.writeMeta(rackId, { scan_id: rackId, ...rest });
}

// The folder for a new scan (or the one it already has), for the Site the scan
// belongs to. `site` is the tenant id the scan is recorded under.
function ensureScanFolder(rackId, { orgId = null, siteId = null } = {}) {
  return store.newScan({ scanId: rackId, orgId, siteId, rackKey: rackId });
}

async function ensurePortCounts(rackId) {
  const rackDir = engineDir(rackId);
  const meta = readMeta(rackId);
  if (!meta) return;

  const jsonPath = path.join(rackDir, 'device_unit_map.json');
  if (!fs.existsSync(jsonPath)) return;

  const data = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
  if (!Array.isArray(data.devices)) return;
  if (data.devices.every(dev => typeof dev.port_count === 'number')) return;

  const imagePath = photoPathOf(rackId);
  // Nothing openable on this machine: leave the port counts as they are.
  // A refresh that cannot run is not a failed scan, and must not be reported
  // as one.
  if (!imagePath) {
    logger.warn({ event: 'scan.port_counts_skipped', rackId, storedPath: meta.imagePath },
      `port-count refresh skipped for ${rackId}: source image not on this machine`);
    return;
  }

  // Re-analyze scopes to the rack's org so device-class active-learning
  // corrections re-apply (and aren't clobbered) on this refresh.
  await runPipelineAnalyze(imagePath, rackDir, tenant.orgForRack(rackId), rackId);
}

// ── Heal-on-open for racks with no port data ──────────────────────────────
// A rack whose devices carry no port_count cannot be worked on: buildResponse
// maps the missing field to null, the client reads that as zero ports, and the
// results page opens with nothing to pick - no devices, no ports, and "We
// couldn't read how many ports this device has" the moment Find Port is
// pressed. Reopening a scan from Recent Scans is where testers hit this.
//
// ensurePortCounts() has always been able to rebuild it, but it was only wired
// to the three INGEST routes (analyze, stitch, analyze-video). Nothing ran it
// when an existing scan was reopened, so a rack that lost its port data stayed
// broken for good. The loss came from the select path overwriting
// device_unit_map.json (fixed in pipeline/runner.py), which is why this only
// struck *some* scans - the ones a port lookup had already been run on. Racks
// damaged before that fix still need healing, and this is what does it.
//
// A rack that was properly analysed has a numeric port_count on EVERY device,
// port-bearing or not (runner.py's --detect_only branch sets 0 for the rest),
// so an absent field is a reliable "this was never analysed, or was stripped".
function portDataMissing(rackId) {
  try {
    const jsonPath = path.join(engineDir(rackId), 'device_unit_map.json');
    if (!fs.existsSync(jsonPath)) return false;
    const data = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
    if (!Array.isArray(data.devices) || data.devices.length === 0) return false;
    // Mirrors ensurePortCounts()'s own guard, so we never start a rebuild it
    // would immediately abandon.
    return !data.devices.every(dev => typeof dev.port_count === 'number');
  } catch (_) {
    return false;
  }
}

// Racks currently being rebuilt. Opening the same damaged scan twice (or a
// double-mount in the client) must share one pipeline run, not launch two.
const _portHealInFlight = new Map();

async function healPortDataIfMissing(rackId) {
  if (!portDataMissing(rackId)) return false;

  const pending = _portHealInFlight.get(rackId);
  if (pending) {
    await pending.catch(() => {});
    return true;
  }

  const run = (async () => {
    logger.warn({ event: 'scan.port_data_missing', rackId },
      `${rackId} has no per-device port data; rebuilding before serving the scan`);
    await ensurePortCounts(rackId);
    // scan_result.json was assembled FROM the stripped map, so it carries the
    // same portless devices. Without this the client keeps being served the
    // broken payload straight from cache and the heal looks like it did
    // nothing.
    try {
      writeCanonicalScanResult(rackId);
    } catch (err) {
      logger.error({ event: 'scan.port_data_heal_result_failed', rackId, error: err.message },
        `${rackId}: port data rebuilt but canonical result refresh failed`);
    }
    logger.info({ event: 'scan.port_data_healed', rackId, stillMissing: portDataMissing(rackId) },
      `${rackId}: port data rebuild complete`);
  })();

  _portHealInFlight.set(rackId, run);
  try {
    await run;
  } catch (err) {
    logger.error({ event: 'scan.port_data_heal_failed', rackId, error: err.message },
      `${rackId}: port data rebuild failed; serving the scan as-is`);
  } finally {
    _portHealInFlight.delete(rackId);
  }
  return true;
}

function buildResponse(rackId, cached) {
  const rackDir  = engineDir(rackId);
  const jsonPath = path.join(rackDir, 'device_unit_map.json');
  const data     = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
  const meta     = readMeta(rackId);
  // imageUrl is annotated.jpg: the engine's drawing (2_devices_only.png, else
  // 3_units_and_devices.png) as a JPEG, written after every analysis.
  // Testing overlay: the full rack with device boxes + detected port boxes
  // drawn on it (7_rack_all_ports.png). Null if the render is missing.
  const overlayFile = fs.existsSync(rackImagePath(rackDir, '7_rack_all_ports.png'))
    ? rackImageUrlPath(rackDir, '7_rack_all_ports.png')
    : null;
  // The rails' numbering, when the rails were read and the photo's rows sit a
  // whole number of units off them (lib/rack_height.js). Shown only: the
  // engine's files keep the numbers it gave.
  const unitOffset = Number.isInteger(meta?.rackHeight?.unitOffset) ? meta.rackHeight.unitOffset : 0;
  const shiftUnit = (u) => {
    if (!unitOffset) return u;
    const m = String(u).match(/^u(\d+)$/i);
    if (!m) return u;
    const n = Number(m[1]) + unitOffset;
    return n >= 1 ? `u${String(n).padStart(2, '0')}` : null;
  };
  const devices = (data.devices || []).map(applyRouterRule).map(dev => ({
    ...dev,
    ...(unitOffset && Array.isArray(dev.units) ? { units: dev.units.map(shiftUnit).filter(Boolean) } : {}),
    port_count: typeof dev.port_count === 'number' ? dev.port_count : null,
    ports: dev.ports || [],
    console_ports: dev.console_ports || [],
    sfp_ports: dev.sfp_ports || [],
    other_ports: dev.other_ports || [],
    connected_ports: dev.connected_ports || [],
  }));

  // The original photo, as a URL the client uses as given (it used to rebuild
  // the path itself from an extension and asked for files that never existed).
  // Every scan's photo is photo.jpg now: the intake converts HEIC, WebP and
  // the rest to an upright JPEG before the scan folder is made.
  const originalImageUrl = photoPathOf(rackId) ? scanFileUrl(rackId, 'photo.jpg') : null;

  return {
    rackId,
    scanId:          rackId,               // kept for backwards compat
    timestamp:       meta?.timestamp || new Date().toISOString(),
    cached,
    imageUrl:        scanFileUrl(rackId, 'annotated.jpg'),
    overlayImageUrl: overlayFile ? engineImageUrl(rackId, overlayFile) : null,
    thumbUrl:        originalImageUrl ? scanFileUrl(rackId, 'thumb.jpg') : null,
    originalExt: 'jpg',                   // kept for older clients
    originalImageUrl,                     // authoritative: use this
    devices,
    units_detected:  (data.units_detected || []).map(shiftUnit).filter(Boolean)
      .filter((u) => !(Number.isInteger(meta?.rackHeight?.unitsTotal) && Number(String(u).replace(/\D/g, '')) > meta.rackHeight.unitsTotal)),
    qualityWarning:    meta?.qualityWarning || null,
    qualityWarningMsg: meta?.qualityWarningMsg || null,
    // How the rack was named after the analysis, and how tall it is
    // (settleRack below). rackNaming.ask is the one thing that makes the
    // result ask "Which rack is this?".
    rackNaming:        meta?.rackNaming ? { ...meta.rackNaming, words: rackNaming.METHOD_WORD[meta.rackNaming.method] || null } : null,
    rackHeight:        meta?.rackHeight || null,
  };
}

// ── Scan result data ──────────────────────────────────────────
// buildScanReportData(rackId) is the single source of the structured view of
// one scan; writeCanonicalScanResult saves it in the scan's result.json.
const CLASS_CODE_SRV = {
  'Switch': 'SW', 'Patch Panel': 'PP', 'Firewall': 'FW', 'Router': 'RO',
  'Server': 'SVR', 'Load Balancer': 'LB', 'Modem': 'MO',
  'Controller': 'CTRL', 'Recorder': 'REC', 'Amplifier': 'AMP', 'Gateway': 'GT',
  'PDU': 'PDU', 'PSU': 'PSU', 'UPS': 'UPS', 'Empty': 'EMP', 'Closed Unit': 'CL',
};

function formatUnitsRangeSrv(units = []) {
  const nums = [...new Set((units || [])
    .map(u => { const m = String(u).match(/\d+/); return m ? Number(m[0]) : null; })
    .filter(n => n !== null))].sort((a, b) => a - b);
  if (!nums.length) return '';
  const ranges = [];
  let start = nums[0], prev = nums[0];
  for (let i = 1; i < nums.length; i++) {
    if (nums[i] === prev + 1) { prev = nums[i]; continue; }
    ranges.push([start, prev]); start = nums[i]; prev = nums[i];
  }
  ranges.push([start, prev]);
  return ranges.map(([s, e]) =>
    s === e ? `U${String(s).padStart(2, '0')}`
            : `U${String(s).padStart(2, '0')}-U${String(e).padStart(2, '0')}`
  ).join(' ');
}

const { modelFromText, modelInText, matchesShape } = require('./lib/model_read');

// Matches a report device (by its first occupied unit + class) against the
// per-device OCR pass (ocr_devices.json in the engine folder) to recover the
// make/model read off the physical faceplate. Best-effort: OCR frequently
// only gets the make, or nothing at all - callers must handle null fields.
// The model is settled from raw_text by lib/model_read.js: read, never guessed.
function matchOcrDevice(rackDir, position, className) {
  try {
    const p = path.join(rackDir, 'ocr_devices.json');
    if (!fs.existsSync(p)) return null;
    const perDev = JSON.parse(fs.readFileSync(p, 'utf8'));
    const firstUnit = String(position || '').split(/[\s-]/)[0]; // "U04-U05" -> "U04"
    if (!firstUnit) return null;
    const rows = perDev?.devices || [];
    const row = rows.find(d => d.position === firstUnit && d.class_name === className)
             || rows.find(d => d.position === firstUnit)
             || null;
    // The model is READ, never guessed (lib/model_read.js): a stored model
    // stands only when it is in the device's own text and is not a name,
    // tag, serial, location or port; otherwise the clean model token in the
    // text, otherwise none. The old fill-ins (a partial "C9300" expanded to
    // C9300-24T, a MikroTik prefix put on any digits) were guesses.
    if (row) {
      if (row.model && row.raw_text && modelInText(row.model, row.raw_text)
        && (row.make || matchesShape(String(row.model).trim()))) return row;
      const found = row.raw_text ? modelFromText(row.raw_text, row.make) : null;
      return { ...row, model: found ? found.model : null };
    }
    return row;
  } catch { return null; }
}

// device_overrides.json in the engine folder - user-entered corrections (make
// and model) for one device, keyed by the same
// "U04" position string OCR uses. Saved via POST /api/scan/:rackId/device-override.
// A user override always wins over the OCR guess (see selectedDevice below).
// The label cache is keyed by image; the KIND carries a version so a change to
// how a label's text is read (v2: the unit segment's letter-for-digit repair)
// re-reads photographs cached under the old reading instead of serving it.
const LABELS_CACHE_KIND = 'labels_v3'; // v3: the cable flag lines travel with the labels

function deviceOverridesPath(rackDir) {
  return path.join(rackDir, 'device_overrides.json');
}
function readDeviceOverride(rackDir, position) {
  try {
    const p = deviceOverridesPath(rackDir);
    if (!fs.existsSync(p)) return null;
    const all = JSON.parse(fs.readFileSync(p, 'utf8'));
    return all[position] || null;
  } catch { return null; }
}

// Fewer than this many ports and a network box is a router. Kept in step with
// ROUTER_PORT_CEILING in pipeline/classify.py.
const ROUTER_PORT_CEILING = 10;

/**
 * The router rule, applied to a device on its way out of this server.
 *
 * The pipeline writes it into new maps; this is for every file written before
 * the rule existed, and for every route that used to copy class_name straight
 * off disk - the Results payload, the cached scan_result.json, the OCR record.
 * Same rule as pipeline/classify.py: only a Switch, only once its ports have
 * been counted, and never a class a person or the detector chose otherwise.
 */
function applyRouterRule(dev) {
  if (!dev || typeof dev !== 'object') return dev;
  const n = Number(dev.port_count);
  if (dev.class_name === 'Switch' && Number.isInteger(n) && n > 0 && n < ROUTER_PORT_CEILING) {
    return { ...dev, class_name: 'Router', class_source: `ports<${ROUTER_PORT_CEILING}` };
  }
  return dev;
}


function buildScanReportData(rackId) {
  const rackDir = engineDir(rackId);
  const meta    = readMeta(rackId);
  if (!meta) throw new Error(`Scan ${rackId} not found`);

  const mapPath = path.join(rackDir, 'device_unit_map.json');
  const mapData = fs.existsSync(mapPath) ? JSON.parse(fs.readFileSync(mapPath, 'utf8')) : {};
  const rawDevices = mapData.devices || [];
  const unitsDetected = mapData.units_detected || [];

  const counts = {};
  const devices = rawDevices.map((dev0, i) => {
    // A network box with fewer than ten ports is a router, not a switch.
    //
    // The same rule as pipeline/runner.py's reclass_small_as_router, applied
    // again here on the way out. The pipeline settles it for a scan that has
    // not run yet; this settles it for every rack already on disk, without
    // asking anyone to photograph their rack a second time. Both are narrow
    // in the same way: only a device the detector called Switch, and only
    // once its ports have actually been counted - a four-port server is a
    // server and a six-port patch panel is a patch panel.
    const dev = applyRouterRule(dev0);
    const code = CLASS_CODE_SRV[dev.class_name] || (dev.class_name || 'UNK').replace(/\s+/g, '').slice(0, 4).toUpperCase();
    counts[code] = (counts[code] || 0) + 1;
    const seq = String(counts[code]).padStart(2, '0');
    const labelUnits = dev.units?.length ? dev.units : unitsDetected.length ? [unitsDetected[0]] : [];
    const unitRange = formatUnitsRangeSrv(labelUnits) || 'U01';
    const label = `${unitRange.split(' ')[0]}-${code}${seq}`;
    return {
      index: i + 1,
      label,
      class_name: dev.class_name || 'Unknown',
      position: unitRange,
      port_count: dev.port_count || 0,
      console_ports: dev.console_ports?.length || 0,
      sfp_ports: dev.sfp_ports?.length || 0,
      other_ports: dev.other_ports?.length || 0,
      connected_ports: dev.connected_ports?.length || 0,
      // PDU power outlets (present only on PDU devices).
      power_total: dev.power_total || 0,
      power_connected: dev.power_connected || 0,
      power_empty: dev.power_empty || 0,
      powered: !!dev.powered,
    };
  });

  // Merge make/model onto every device, so the result shows a real
  // inventory ("Cisco C9300-48P") rather than only a class ("Switch").
  // Goes through matchOcrDevice (rather than reading ocr_devices.json flat)
  // so each device gets the raw_text re-parse as well -
  // the pipeline's own `model` field is often null even when the faceplate
  // text clearly carries a model. A user's manual correction
  // (device_overrides.json) outranks whatever OCR guessed.
  // Best-effort throughout: the result still builds if OCR never ran.
  for (const dv of devices) {
    const o = matchOcrDevice(rackDir, dv.position, dv.class_name);
    const ov = readDeviceOverride(rackDir, String(dv.position || '').split(/[\s\u2013\u2014-]/)[0]);
    if (o || ov) {
      dv.make     = ov?.make     || o?.make    || null;
      dv.model    = ov?.model    || o?.model   || null;
    }
  }

  // Latest port identification only - walk newest-first and take the first valid line
  const idsPath = path.join(rackDir, 'port_identifications.jsonl');
  const portIdentifications = [];
  if (fs.existsSync(idsPath)) {
    const raw = fs.readFileSync(idsPath, 'utf8').split('\n').filter(Boolean);
    for (let i = raw.length - 1; i >= 0; i--) {
      try {
        portIdentifications.push(JSON.parse(raw[i]));
        break;
      } catch { /* skip malformed line */ }
    }
  }

  // The person's feedback on this scan: the feedback rows of its
  // corrections.jsonl (the rows that carry is_correct).
  let feedbackEntries = [];
  try { feedbackEntries = store.readCorrections(rackId).filter(e => typeof e.is_correct === 'boolean'); }
  catch { feedbackEntries = []; }
  const fbCorrect = feedbackEntries.filter(e => e.is_correct).length;

  // The annotated images this scan produced, as URLs of the files route.
  const candidateImages = [
    '3_units_and_devices.png',
    '7_rack_all_ports.png',
    '5_selected_device_with_port.png',
    '6_full_rack_selected_port.png',
  ];
  const images = candidateImages
    .filter(f => fs.existsSync(rackImagePath(rackDir, f)))
    .map(f => engineImageUrl(rackId, rackImageUrlPath(rackDir, f)));

  const portIdentificationsOut = portIdentifications.map(e => {
    const p = e.port_info || {};
    const dev = devices[e.device_index - 1];
    return {
      timestamp: e.timestamp,
      device_index: e.device_index,
      device_label: dev?.label || null,
      device_class: dev?.class_name || null,
      device_position: dev?.position || null,
      port: e.port,
      port_category: p.port_category || null,
      status: p.status || null,
      confidence: p.confidence ?? null,
      location: p.location || null,
      cable_color: p.cable_color || null,
      cable_connector: p.cable_connector || null,
      cable_type: p.cable_type || null,
      cable_confidence: p.cable_confidence ?? null,
      port_type: p.port_type || null,
      port_type_confidence: p.port_type_confidence ?? null,
      device_image: e.device_image ? engineImageUrl(rackId, e.device_image) : null,
      full_rack_image: e.full_rack_image ? engineImageUrl(rackId, e.full_rack_image) : null,
    };
  });

  // "Selected device" = the device behind the most recent port identification
  // (same one the Port Identifications section already highlights), enriched
  // with whatever the per-device OCR pass read off its faceplate. make and
  // model were already resolved (override > OCR) on the devices array above.
  let selectedDevice = null;
  if (portIdentificationsOut.length) {
    const latest = portIdentificationsOut[0];
    const dev = devices[latest.device_index - 1];
    if (dev) {
      const firstUnit = String(dev.position || '').split(/[\s\u2013\u2014-]/)[0];
      const ocr = matchOcrDevice(rackDir, dev.position, dev.class_name);
      const override = readDeviceOverride(rackDir, firstUnit);
      selectedDevice = {
        ...dev,
        make: override?.make || ocr?.make || null,
        model: override?.model || ocr?.model || null,
        model_source: (override?.make || override?.model) ? 'user' : (ocr?.source || null),
        model_confidence: (override?.make || override?.model) ? null : (ocr?.match_conf ?? null),
      };
    }
  }

  return {
    rackId,
    timestamp: meta.timestamp || null,
    quality_note: meta.quality?.note || null,
    units_detected: unitsDetected,
    units_range: formatUnitsRangeSrv(unitsDetected),
    devices,
    selectedDevice,
    port_identifications: portIdentificationsOut,
    feedback: {
      total: feedbackEntries.length,
      correct: fbCorrect,
      wrong: feedbackEntries.length - fbCorrect,
      accuracy: feedbackEntries.length ? fbCorrect / feedbackEntries.length : null,
      entries: feedbackEntries,
    },
    images, // URLs of the files route (engine images)
    _rackDir: rackDir, // internal: never written to result.json
  };
}

const SCAN_RESULT_SCHEMA = 'scan_result.v1';
const RESULT_DOC_VERSION = 1;

// The rack reading in the shape the data layout names (result.json): rack,
// units, devices, ports, labels, summary, versions. Built from the engine's
// files and the API view, so the two can never disagree.
function canonicalReading(rackId, api, rackDir) {
  const mapPath = path.join(rackDir, 'device_unit_map.json');
  const map = store.readJson(mapPath, {}) || {};
  const raw = Array.isArray(map.devices) ? map.devices : [];
  const info = store.scanInfo(rackId);
  let rackLabel = null;
  try {
    const rj = store.readJson(path.join(path.dirname(path.dirname(info.dir)), 'rack.json'), null);
    rackLabel = rj?.label ?? null;
  } catch { rackLabel = null; }
  const unitNum = (u) => { const m = String(u).match(/\d+/); return m ? Number(m[0]) : null; };
  const unitsDetected = (api.units_detected || []).map(unitNum).filter((n) => n != null);

  const devices = (api.devices || []).map((d, i) => {
    const r = raw[i] || {};
    const us = (r.units || []).map(unitNum).filter((n) => n != null);
    const corrected = !!(d._correction || r.class_name_source === 'user_corrected');
    return {
      id: `d${d.index ?? i + 1}`,
      type: d.class_name || 'Unknown',
      unit_top: us.length ? Math.max(...us) : null,
      height_u: us.length ? us.length : null,
      make: d.make ?? null,
      model: d.model ?? null,
      label_text: d.label ?? null,
      confidence: typeof r.confidence === 'number' ? r.confidence : (typeof r.conf === 'number' ? r.conf : null),
      source: corrected ? 'corrected' : 'camera',
    };
  });

  const units = unitsDetected.map((u) => {
    const i = raw.findIndex((r) => (r.units || []).map(unitNum).includes(u));
    return { u, device_id: i >= 0 ? `d${i + 1}` : null };
  });

  const ports = [];
  raw.forEach((r, i) => {
    (Array.isArray(r.ports) ? r.ports : []).forEach((p, j) => {
      if (!p || typeof p !== 'object') return;
      const state = p.status === 'connected' ? 'connected' : 'empty';
      ports.push({
        device_id: `d${i + 1}`,
        index: j + 1,
        name: p.name ?? null,
        type: p.port_type ?? null,
        state,
        cable: state === 'connected' && (p.cable_connector || p.cable_color)
          ? { kind: p.cable_connector ?? null, colour: p.cable_color ?? null } : null,
        confidence: typeof p.confidence === 'number' ? p.confidence : null,
      });
    });
  });

  const labels = [];
  const front = store.readJson(path.join(rackDir, 'labels-front.json'), null);
  for (const l of (front?.labels || [])) {
    if (l && l.text) labels.push({ where: 'front', text: String(l.text), confidence: l.conf ?? null });
  }
  const side = store.readJson(path.join(rackDir, 'side_labels.json'), null);
  for (const l of (side?.labels || [])) {
    if (l && l.text) labels.push({ where: l.side || 'side', text: String(l.text), confidence: l.conf ?? null });
  }

  const meta = readMeta(rackId) || {};
  return {
    scan_id: rackId,
    // units_total: the height decided after the analysis (lib/rack_height.js),
    // else the shelves counted, as before.
    rack: {
      key: info?.rackKey ?? rackId, label: rackLabel,
      units_total: Number.isInteger(meta.rackHeight?.unitsTotal) ? meta.rackHeight.unitsTotal : unitsDetected.length,
      units_total_source: meta.rackHeight?.source || 'detected',
      named_by: meta.rackNaming?.method || null,
    },
    units,
    devices,
    ports,
    labels,
    summary: {
      devices: devices.length,
      ports_connected: ports.filter((p) => p.state === 'connected').length,
      ports_empty: ports.filter((p) => p.state === 'empty').length,
      labels_read: labels.length,
    },
    versions: { result: RESULT_DOC_VERSION, engine: meta.model_versions || null },
  };
}

// Writes the scan's result.json - the ONE document for a scan: the reading in
// the data layout's shape (scan_id, rack, units, devices, ports, labels,
// summary, versions, updated_at) and, under `api`, the merged view the
// results routes have always returned (metadata + devices + units + ports +
// selection + feedback). Atomic (tmp + rename) so a partial write is never
// observed. Returns the `api` view, which is what callers used to get.
//
// Pass `prebuiltData` when you already have the result of buildScanReportData
// to avoid re-reading the source files; otherwise we build it ourselves.
function writeCanonicalScanResult(rackId, prebuiltData) {
  try {
    const data = prebuiltData || buildScanReportData(rackId);
    const { _rackDir, ...publicData } = data;

    let selectedPort = null;
    const selPath = path.join(_rackDir, 'selected_port_info.json');
    if (fs.existsSync(selPath)) {
      try {
        selectedPort = JSON.parse(fs.readFileSync(selPath, 'utf8'));
        if (selectedPort?.selected_device) selectedPort.selected_device = applyRouterRule(selectedPort.selected_device);
      }
      catch (e) { logger.warn(`[scan_result] selected_port_info parse failed for ${rackId}: ${e.message}`); }
    }

    const meta = readMeta(rackId) || {};
    const api = {
      schema: SCAN_RESULT_SCHEMA,
      rackId,
      createdAt: meta.timestamp || null,
      updatedAt: new Date().toISOString(),
      createdBy: meta.userId ? { userId: meta.userId } : null,
      image: {
        imageHash:         meta.imageHash || null,
        originalImageUrl:  photoPathOf(rackId) ? scanFileUrl(rackId, 'photo.jpg') : null,
        qualityWarning:    meta.qualityWarning || null,
        qualityWarningMsg: meta.qualityWarningMsg || null,
      },
      ...publicData,
      // The rack's height as decided after the analysis (lib/rack_height.js)
      // and how its name was found (lib/rack_naming.js).
      rack: {
        unitsTotal: Number.isInteger(meta.rackHeight?.unitsTotal) ? meta.rackHeight.unitsTotal : null,
        unitsTotalSource: meta.rackHeight?.source || null,
        unitsTotalNote: meta.rackHeight?.note || null,
        name: meta.rackNaming?.name || null,
        namedBy: meta.rackNaming?.method || null,
        namingConfidence: meta.rackNaming?.confidence ?? null,
      },
      selectedPort,
    };

    // Overlay any user feedback corrections on top of the model's
    // predictions before persisting. Mutates `api` in place; the
    // original predictions are preserved on each modified field's
    // `_correction.original` for audit.
    applyFeedbackOverrides(rackId, api);

    const doc = { ...canonicalReading(rackId, api, _rackDir), updated_at: api.updatedAt, api };
    store.writeResult(rackId, doc);
    return api;
  } catch (err) {
    logger.error(`[scan_result] write failed for ${rackId}: ${err.message}`);
    return null;
  }
}

// Schedule a canonical-result refresh for after the response is sent. Used by
// mutation endpoints that don't already build the result inline. Also kicks
// off per-device OCR in the background - fully silent, the user never sees
// it; the result lands in the engine folder's ocr_devices.json.
function scheduleCanonicalRefresh(rackId) {
  setImmediate(() => {
    writeCanonicalScanResult(rackId);
    scheduleOcrDevices(rackId);
    scheduleOcrLabels(rackId);
    scheduleCableEnrichment(rackId);
  });
}

// Fire-and-forget: classify the cable (connector + colour) on EVERY connected
// port after a scan finishes, so the whole rack shows cable info - not just the
// one port a user taps. Runs in a worker so the initial analyze response stays
// fast; when it finishes we rewrite scan_result.json so the client picks the
// cable_* fields up on its next load. Idempotent + guarded: skips racks whose
// device_unit_map.json is already marked cables_enriched.
// Cheap gate for the confirmed-rack bypass: is there anything to match against
// for this org? Avoids paying the pHash+embedding CLI cost on every analyze
// when the org has never confirmed a rack.
function hasConfirmedRacks(orgId) {
  if (orgId == null) return false;
  const p = path.join(store.trainingDir(orgId), 'confirmed_racks', 'confirmed_racks.json');
  try {
    if (!fs.existsSync(p)) return false;
    const j = JSON.parse(fs.readFileSync(p, 'utf8'));
    return j && Object.keys(j).length > 0;
  } catch (_) { return false; }
}

// Cheap gate for the active-learning apply after a scan: does this org hold
// any device correction at all? Spawning the engine's learning CLI costs over
// a second of the first answer, and for an organization that never corrected
// a device (or chose never to train) it can only answer "nothing to apply".
function hasDeviceCorrections(orgId) {
  if (orgId == null) return false;
  const p = path.join(store.trainingDir(orgId), 'devices', 'corrections.json');
  try {
    if (!fs.existsSync(p)) return false;
    const j = JSON.parse(fs.readFileSync(p, 'utf8'));
    return !!j && Object.keys(j).length > 0;
  } catch (_) { return false; }
}

const _cableEnrichRunning = new Set();
function scheduleCableEnrichment(rackId) {
  if (!rackId || _cableEnrichRunning.has(rackId)) return;
  const rackDir  = engineDir(rackId);
  const mapPath  = path.join(rackDir, 'device_unit_map.json');
  if (!fs.existsSync(mapPath)) return;
  // Already enriched? Nothing to do - keeps repeated canonical refreshes cheap.
  try {
    const dm = JSON.parse(fs.readFileSync(mapPath, 'utf8'));
    if (dm && dm.cables_enriched) return;
  } catch (_) { return; }
  const imagePath = photoPathOf(rackId);
  if (!imagePath) return;

  _cableEnrichRunning.add(rackId);
  // Through a promise, so a pool that refuses at once (tests run with the
  // pool off) is a failed enrichment, not an uncaught exception.
  Promise.resolve().then(() => pool.request('enrich_cables', {
    image_path:  imagePath,
    config_path: CONFIG_PATH,
    output_dir:  rackDir,
  }))
    .then(() => {
      // Surface the freshly-attached cable_* fields into the canonical result.
      writeCanonicalScanResult(rackId);
      logger.info({ event: 'cable.enriched', rackId }, `cable enrichment done for ${rackId}`);
    })
    .catch(err => {
      logger.warn(`[cable-enrich] ${rackId} failed: ${err.message}`);
    })
    .finally(() => { _cableEnrichRunning.delete(rackId); });
}

// Fire-and-forget full-image label OCR after a scan finishes. Per-device OCR
// (scheduleOcrDevices) only sees the crop YOLO produced, so when the detector
// misses a device or boxes it tight enough to clip its faceplate label, the
// per-device pass returns empty text. Running label OCR on the whole rack
// photo recovers brand badges (PLANAR, TRIPP-LITE, AUDIOCODES, SONY, …) and
// rack-applied labels that fall outside any single device's bbox - the GET
// /api/ocr/labels/:rackId endpoint then maps those tokens back to devices by
// Y-overlap and surfaces a brand-token reclassification for the client.
const _ocrLabelsRunning = new Map();
// Returns the run's promise (true when labels-front.json exists after it), so
// the rack naming ladder can wait for the device labels before the result
// opens; everyone else still fires and forgets.
function scheduleOcrLabels(rackId) {
  if (!rackId) return Promise.resolve(false);
  if (_ocrLabelsRunning.has(rackId)) return _ocrLabelsRunning.get(rackId);
  const rackDir   = engineDir(rackId);
  const frontPath = path.join(rackDir, 'labels-front.json');
  if (!store.has(rackId)) return Promise.resolve(false);
  if (fs.existsSync(frontPath)) return Promise.resolve(true);
  const imagePath = photoPathOf(rackId);
  if (!imagePath) return Promise.resolve(false);
  busy.mark(busy.labelsKey(rackId));
  const run = Promise.resolve().then(() => runOcrLabels(imagePath, { vocab: siteDeviceNames(rackId) }))
    .then(result => {
      fs.mkdirSync(rackDir, { recursive: true });
      fs.writeFileSync(frontPath, JSON.stringify(result, null, 2));
      return true;
    })
    .then(async (ok) => {
      // The tags on the cables can name a rack that has no rail label. When
      // they land after the settle already asked (a slow read), the ladder
      // runs again here, never over a name a person gave.
      if (ok) await nameFromLateLabels(rackId);
      return ok;
    })
    .catch(err => {
      logger.warn(`[ocr_labels] ${rackId} failed: ${err.message}`);
      return false;
    })
    .finally(() => { busy.clear(busy.labelsKey(rackId)); _ocrLabelsRunning.delete(rackId); });
  _ocrLabelsRunning.set(rackId, run);
  return run;
}

async function nameFromLateLabels(rackId) {
  let meta = null;
  try { meta = readMeta(rackId); } catch { meta = null; }
  const naming = meta && meta.rackNaming;
  // Not asked yet (the settle is still to run and will see the labels), or answered already.
  if (!naming || !naming.ask || naming.name || naming.method === 'person') return;
  const info = (() => { try { return store.scanInfo(rackId); } catch { return null; } })();
  if (!info || info.siteId == null) return;
  try {
    const late = await rackNaming.nameAfterScan(rackId, {
      tenantId: info.siteId, userId: null,
      physicalLayer: async (id) => {
        const r = await buildPhysicalLayer(id, { refresh: true });
        return r && r.status === 200 ? r.body : null;
      },
    });
    if (late && late.name) {
      try { writeCanonicalScanResult(rackId); } catch { /* the next refresh makes it */ }
      logger.info({ event: 'rack.named_late', rackId, method: late.method, name: late.name }, `rack ${rackId} named from its labels after the settle`);
    }
  } catch (err) {
    logger.warn({ event: 'rack_naming.late_failed', rackId, err: err.message }, 'late rack naming failed');
  }
}

// Fire-and-forget per-device OCR after a scan finishes. Runs only when
// the engine folder's ocr_devices.json doesn't already exist - re-running OCR
// on every canonical refresh would be wasteful (1-2 min on CPU). The user
// can still trigger a re-run via POST /api/scan/:rackId/ocr-devices.
//
// When OCR completes, the canonical scan_result is rewritten so it carries
// the real make/model.
const _ocrRunning = new Set();
function scheduleOcrDevices(rackId) {
  if (!rackId || _ocrRunning.has(rackId)) return;
  const rackDir = engineDir(rackId);
  const ocrPath = path.join(rackDir, 'ocr_devices.json');
  const dumPath = path.join(rackDir, 'device_unit_map.json');
  // Need a device_unit_map to know what to crop; skip silently otherwise.
  if (!fs.existsSync(dumPath) || fs.existsSync(ocrPath)) return;
  _ocrRunning.add(rackId);
  const child = spawnChild(pythonCmd,
    ['-u', '-m', 'pipeline.ocr_devices', rackId, '--dir', rackDir, '--json'],
    { cwd: PROJECT_ROOT,
      env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' } });
  // Don't keep node process alive waiting on this; we just want it to run.
  if (typeof child.unref === 'function') child.unref();
  let stderr = '';
  child.stderr.on('data', c => { stderr += c.toString(); });
  child.on('close', () => {
    _ocrRunning.delete(rackId);
    if (!fs.existsSync(ocrPath)) {
      logger.warn(`[ocr_devices] ${rackId} produced no output: ${stderr.slice(-300)}`);
      return;
    }
    // OCR ran - rewrite the canonical result so it picks up the real
    // make/model. Silent and fire-and-forget; the user never sees this.
    try { writeCanonicalScanResult(rackId); } catch (_) {}
  });
  child.on('error', err => {
    _ocrRunning.delete(rackId);
    logger.warn(`[ocr_devices] spawn failed for ${rackId}: ${err.message}`);
  });
}

// Which tenant should own a scan this person just ran.
//
// Their own, when they have one. Owners and org_admins do NOT - role is what
// gets them access, not membership of a site - and rack_owners.tenant_id is
// NOT NULL, so `claimRack` returned early for them and their scans were never
// recorded at all. The owner dashboard counts rows in that table, which is why
// an owner could run a dozen scans and watch the total sit at one or two: it
// was counting everyone's scans except their own.
//
// The `default` tenant always exists (auth.js creates it at startup), so it is
// a real row to point at rather than a placeholder. created_by still records
// who ran it, so nothing about attribution is lost.
function scanOwnerTenantId(authPayload) {
  const own = authPayload?.tenantId;
  if (own) return own;
  if (!authPayload?.sub) return null;      // not signed in - nothing to claim
  try { return auth.getDefaultTenantId() || null; } catch { return null; }
}

// Which Site this scan request is for.
//
// The scan screen sends `siteId` when a person chose one. lib/scan_site checks
// the caller may read that Site and hands back the payload as a technician of
// that Site would carry it; every scan handler then uses THAT for rackScope(),
// the claim, the space check and the scan meta, so an admin scanning at Site
// 32 mints the rack id a Site 32 technician would. No `siteId` - every phone
// build before the picker - gives the payload back untouched.
//
// Answers the refusal itself and returns null, so a handler only has to clean
// up its upload and stop.
function scanSiteFor(req, res) {
  const site = scanSite.resolve(softAuthPayload(req), req.body?.siteId, scanOwnerTenantId);
  if (site.ok) return site;
  logger.warn({ event: 'scan.site_denied', siteId: String(req.body?.siteId ?? '').slice(0, 32),
    userId: softAuthUserId(req) }, 'scan asked for a Site the caller may not read');
  res.status(site.status).json({ error: site.error });
  return null;
}

// A Site somebody chose is the Site of this scan even when the photo was on
// disk already - a cache hit, or a confirmed rack served in its place. The
// comparison reads the Site from meta.json when it adopts the scan, to
// find the rack's records and the SPOC the check goes to, and a fresh analysis
// is the only other place that writes it: a rack first scanned by an admin
// with no Site would keep none for ever.
//
// When the Site really changes the map is touched as well, so anything that
// caches by the map's time sees a newer scan. Nothing happens without a
// `siteId`, or when the scan already is that Site's. Never fatal.
//
// Who may move a scan that already has a Site: an owner or an organisation
// admin, nobody else. Rack ids are scoped by organisation, so a technician of
// Site B can be served Site A's rack (a cache hit, a confirmed match), and
// their scan must not take the rack, its confirmations and its SPOC away from
// Site A. A scan with no Site, or one left on the shared default tenant from
// before the picker, is still brought home by anybody's first scan. The role
// is the caller's own: scan_site.resolve copies the payload and never edits it.
function stampScanSite(rackId, site) {
  if (!site || !site.chosen) return;
  try {
    const m = readMeta(rackId) || {};
    if (m.tenantId != null && Number(m.tenantId) === Number(site.tenantId)) return;
    if (m.tenantId != null) {
      let defaultId = null;
      try { defaultId = auth.getDefaultTenantId() || null; } catch { defaultId = null; }
      const role = site.auth?.role;
      const mayMove = role === 'owner' || role === 'org_admin';
      if (!mayMove && Number(m.tenantId) !== Number(defaultId)) return;
    }
    m.tenantId = site.tenantId;
    m.site_id = site.tenantId;
    writeMeta(rackId, m);
    // The scan's folder lives under its Site, so it moves with it.
    try { store.moveScanToSite(rackId, site.tenantId); } catch (err) {
      logger.warn({ event: 'scan.site_move_failed', rackId, err: err.message }, 'scan folder not moved to its Site');
    }
    const map = path.join(engineDir(rackId), 'device_unit_map.json');
    if (fs.existsSync(map)) { const now = new Date(); fs.utimesSync(map, now, now); }
  } catch (err) {
    logger.warn({ event: 'scan.site_stamp_failed', rackId, err: err.message },
      'could not record the chosen Site on the scan');
  }
}

// ── Routes ────────────────────────────────────────────────────

app.get('/api/health', (req, res) => {
  res.json({ status: 'ok', version: '3.0.0', service: 'RackTrack API' });
});

// Auth endpoints (signup, verify, login, resend, me)
// Throttle credential endpoints before the auth routes handle them.
// Every endpoint that accepts a credential OR a one-time code must be
// throttled. verify-reset-code and verify were missing: they check a 6-digit
// code without consuming it and with no attempt counter, so an unthrottled
// caller could brute-force the ~10^6 space in seconds and then mint a full
// token via login-with-code - account takeover without the password.
app.use(['/api/auth/login', '/api/auth/signup', '/api/auth/forgot-password',
  '/api/auth/reset-password', '/api/auth/login-with-code',
  '/api/auth/verify-reset-code', '/api/auth/verify', '/api/auth/resend-code'], authLimit);
auth.registerRoutes(app);

// ── Audit log query (auth-required) ───────────────────────────
//
// GET /api/audit?action=&targetType=&targetId=&status=&since=&until=&limit=&offset=
//
// By default returns ONLY the calling user's events. Pass `?scope=all` to see
// every event - but only if the caller's username appears in the
// AUDIT_ADMINS env var (comma-separated). Without admin status, scope=all is
// silently downgraded to scope=self so we never leak other users' actions.
app.get('/api/audit', auth.requireAuth, (req, res) => {
  const adminUsers = String(process.env.AUDIT_ADMINS || '')
    .split(',').map(s => s.trim()).filter(Boolean);
  const isAdmin = adminUsers.includes(req.user.username);
  // scope=self  → only this user's events
  // scope=tenant → every event in this user's tenant (admin-gated)
  // scope=all   → cross-tenant view (super-admin; downgraded to tenant
  //               for non-admins so we never leak across tenants)
  let scope = req.query.scope || 'self';
  if (scope === 'all' && !isAdmin) scope = 'tenant';
  if (scope === 'tenant' && !isAdmin) scope = 'self';

  try {
    const rows = audit.query({
      userId:     scope === 'self'   ? req.user.id        : undefined,
      tenantId:   scope === 'tenant' ? req.user.tenant_id : undefined,
      action:     req.query.action     || undefined,
      targetType: req.query.targetType || undefined,
      targetId:   req.query.targetId   || undefined,
      status:     req.query.status     || undefined,
      sinceTs:    req.query.since      || undefined,
      untilTs:    req.query.until      || undefined,
      limit:      req.query.limit      || 100,
      offset:     req.query.offset     || 0,
    });
    res.json({ ok: true, scope, count: rows.length, events: rows });
  } catch (err) {
    logger.error('[audit] query failed:', err);
    res.status(500).json({ ok: false, error: 'Audit query failed' });
  }
});

// ── Application log dashboard (admin-gated) ───────────────────────────
//
// The pino log stream is mirrored into SQLite by lib/log-store.js. These
// endpoints back the LogsPage: a filterable, auto-refreshing view of what the
// server has actually been logging (email/SMTP results, errors, HTTP, etc.).
// Gated to owners or the AUDIT_ADMINS allow-list - same bar as the ops
// dashboard - since logs can carry operational detail members shouldn't see.
// The owner's console: logs and the dashboard (routes/owner_console.js, shared with auth_service.js).
require('./routes/owner_console')(app, { auth, audit, logger, feedbackTally: () => feedbackTally() });

// ── Orphan GC (admin-only) ──────────────────────────────────────────
// POST /api/admin/orphan-gc/run  body: { dryRun?: bool, retentionDays?: int }
// Lists scans in the store with no rack_owners row + (when
// dryRun=false) deletes them. Default dryRun=true so it never destroys
// anything by accident.
function _isAdmin(req) {
  const adminUsers = String(process.env.AUDIT_ADMINS || '')
    .split(',').map(s => s.trim()).filter(Boolean);
  return adminUsers.length === 0 || adminUsers.includes(req.user.username);
}

app.post('/api/admin/orphan-gc/run', auth.requireAuth, (req, res) => {
  if (!_isAdmin(req)) return res.status(403).json({ ok: false, error: 'admin only' });
  const dryRun = req.body?.dryRun !== false;       // default true
  const retentionDays = Number(req.body?.retentionDays) || 14;
  try {
    const summary = orphanGC.run({ dryRun, retentionDays });
    audit.log({ req, action: 'orphan_gc.run', status: 'ok', payload: {
      dryRun, retentionDays,
      scanned: summary.scanned, removed: summary.removed,
      freedBytes: summary.freedBytes,
    }});
    recordEvent('orphan_gc.run', { dryRun, removed: summary.removed });
    res.json({ ok: true, ...summary });
  } catch (e) {
    logger.error({ event: 'orphan_gc.failed', err: e.message }, 'orphan GC failed');
    res.status(500).json({ ok: false, error: 'orphan GC failed' });
  }
});

// ── Organization retention ─────────────────────────────────────
// An organization that set retentionDays (PUT /api/orgs/:orgId/data-settings)
// has its scans older than that deleted by this daily job, through the store,
// so the folder and its index row go together. Also clears upload-cache
// leftovers older than a day (an upload is normally removed the moment its
// photo.jpg is written). Returns what it did.
function runDataRetention(now = Date.now()) {
  const out = { orgs: [], deletedScans: 0, deletedFiles: 0, uploadsCleared: 0 };
  for (const org of orgSettings.withRetention()) {
    const cutoff = new Date(now - org.retentionDays * 24 * 60 * 60 * 1000).toISOString();
    let scans = 0, files = 0;
    for (const sc of store.listScans({ orgId: org.id, olderThan: cutoff })) {
      const r = store.deleteScan(sc.scanId);
      if (r.deleted) { scans += 1; files += r.files; }
    }
    if (scans) {
      out.orgs.push({ id: org.id, deletedScans: scans });
      audit.log({
        user: { id: null, username: 'system' }, action: 'retention.scans_deleted', status: 'ok',
        targetType: 'organization', targetId: String(org.id),
        payload: { retentionDays: org.retentionDays, deletedScans: scans, deletedFiles: files },
      });
    }
    out.deletedScans += scans;
    out.deletedFiles += files;
  }
  try { out.uploadsCleared = store.cleanCache('uploads', 24 * 60 * 60 * 1000); } catch (_) { /* best effort */ }
  return out;
}
setInterval(() => {
  try {
    const r = runDataRetention();
    logger.info({ event: 'retention.run', ...r }, `retention: ${r.deletedScans} scans deleted`);
  } catch (e) {
    logger.warn({ event: 'retention.failed', err: e.message }, `retention job failed: ${e.message}`);
  }
}, 24 * 60 * 60 * 1000).unref();
// Pending matrix uploads live at most an hour; anything a restart left behind goes now.
try { require('./lib/matrix').cleanPendingUploads(0); } catch (_) { /* best effort */ }

// Scheduled daily orphan GC. Default: dry-run only (logs counts but
// doesn't delete) so an operator can review the metric before
// flipping ORPHAN_GC_APPLY=1.
const _orphanGcIntervalMs = 24 * 60 * 60 * 1000;
const _orphanGcApply = process.env.ORPHAN_GC_APPLY === '1';
const _orphanGcRetentionDays = parseInt(process.env.ORPHAN_GC_RETENTION_DAYS, 10) || 14;
setInterval(() => {
  try {
    const summary = orphanGC.run({
      dryRun: !_orphanGcApply,
      retentionDays: _orphanGcRetentionDays,
    });
    logger.info({
      event: 'orphan_gc.scheduled',
      ...summary, orphans: undefined,   // omit per-folder list from log
      sampleOrphans: (summary.orphans || []).slice(0, 5).map(o => o.rackId),
    }, `scheduled orphan GC: ${summary.removed}/${summary.scanned} ${_orphanGcApply ? 'pruned' : 'would-prune'}`);
    // When the scheduled job actually deletes customer data, record it in the
    // tamper-evident audit trail - same as the manual endpoint does. A system
    // actor (no req) stands in for the cron. Dry-runs delete nothing, so this
    // only fires once apply is deliberately enabled.
    if (_orphanGcApply && summary.removed > 0) {
      audit.log({
        user: { id: null, username: 'system' },
        action: 'orphan_gc.scheduled',
        status: 'ok',
        payload: {
          scheduled: true,
          retentionDays: _orphanGcRetentionDays,
          scanned: summary.scanned, removed: summary.removed,
          freedBytes: summary.freedBytes,
          sampleOrphans: (summary.orphans || []).slice(0, 5).map(o => o.rackId),
        },
      });
    }
  } catch (e) {
    logger.warn({ event: 'orphan_gc.scheduled_failed', err: e.message },
      `scheduled orphan GC failed: ${e.message}`);
  }
}, _orphanGcIntervalMs).unref();

/**
 * POST /api/detect
 * Stateless live-overlay detection - runs only YOLO bbox classification on
 * the uploaded JPEG. NO rack folder, NO OCR, NO port detection, NO audit
 * log, NO image renders. Used by the Camera viewfinder's per-frame loop.
 *
 * Response: { devices: [{ class_name, confidence, bbox:[x,y,w,h] }],
 *             image_size: { w, h } }
 */
app.post('/api/detect', detectLimit, upload.single('image'), async (req, res) => {
  if (!req.file) return res.status(400).json({ error: 'No image file provided' });
  let tmpPath = req.file.path;
  try {
    // The viewfinder sends JPEG frames, which pass straight through; a
    // gallery HEIC gets converted first so cv2 can read it.
    tmpPath = (await intakeImage(req.file)).path;
    const result = await pool.request('detect_only', {
      image_path:  tmpPath,
      config_path: CONFIG_PATH,
    });
    if (!result.ok) {
      return res.status(400).json({ error: result.error || 'detection failed' });
    }
    res.json({
      devices:    result.devices || [],
      image_size: result.image_size || null,
    });
  } catch (err) {
    if (imageIntake.isUnreadable(err)) {
      logger.warn({ event: 'detect.unreadable_upload', kind: err.kind, detail: err.detail }, err.message);
      return res.status(400).json({ error: err.message });
    }
    logger.error({ err: err.message }, 'detect failed');
    res.status(500).json({ error: 'detection failed' });
  } finally {
    safeUnlink(tmpPath);
    safeUnlink(req.file.path);
  }
});

/**
 * POST /api/analyze
 * 1. Hash the uploaded image → RK-XXXXXXXX
 * 2. If the scan's engine/device_unit_map.json exists → return cached result
 * 3. Otherwise make the scan folder, run pipeline --detect_only, return fresh result
 */
// Lets a mobile client reclaim a scan it started but lost when iOS suspended
// the app mid-analysis. The client sends a random `clientJobId`; we record
// jobId -> rackId here by hooking res.json, so every return path (cache hit,
// success, error) is captured even if the client socket already closed - the
// handler still runs to completion server-side. No-op without a clientJobId,
// so web and older clients are unaffected.
function trackScanJob(req, res) {
  const jobId = req.body && req.body.clientJobId;
  if (!scanJobs.isValidId(jobId)) return;
  scanJobs.start(jobId);
  const _json = res.json.bind(res);
  res.json = (payload) => {
    try {
      const rackId = payload && (payload.rackId
        || (Array.isArray(payload.racks) && (payload.racks.find(r => r && r.rackId) || {}).rackId));
      if (res.statusCode < 400 && rackId) scanJobs.done(jobId, rackId);
      else if (res.statusCode >= 400) scanJobs.fail(jobId, (payload && payload.error) || 'Analysis failed');
    } catch (_) { /* tracking must never break the response */ }
    return _json(payload);
  };
}

// Poll target for the resume flow above. Returns { status, rackId } for a
// previously-started clientJobId. 'missing' means we never saw it (expired or
// wrong id) - the client then just falls back to the normal upload screen.
app.get('/api/analyze/result/:jobId', (req, res) => {
  const j = scanJobs.get(req.params.jobId);
  if (!j) return res.json({ status: 'missing' });
  res.json({ status: j.status, rackId: j.rackId || null, error: j.error || null });
});

// The phone's position, from the upload's form fields, or nothing. Every value
// is checked, so a bad or missing reading is simply absent rather than stored.
function captureLocationFrom(body) {
  const n = (v) => (v === undefined || v === null || v === '' ? NaN : Number(v));
  const lat = n(body && body.lat);
  const lng = n(body && body.lng);
  if (!Number.isFinite(lat) || !Number.isFinite(lng) || Math.abs(lat) > 90 || Math.abs(lng) > 180) return {};
  const acc = n(body.accuracy);
  const at = String((body && body.locatedAt) || '').slice(0, 40);
  return {
    captureLocation: {
      lat: Math.round(lat * 1e6) / 1e6,
      lng: Math.round(lng * 1e6) / 1e6,
      accuracyM: Number.isFinite(acc) && acc >= 0 && acc < 1e6 ? Math.round(acc) : null,
      at: /^\d{4}-\d{2}-\d{2}T/.test(at) ? at : null,
    },
  };
}

app.post('/api/analyze', auth.requireAuth, scanLimit, upload.single('image'), async (req, res) => {
  if (!req.file) return res.status(400).json({ error: 'No image file provided' });
  trackScanJob(req, res);
  // Scans only happen through an approved organization: block a user whose
  // org is still pending owner approval (legacy/no-org users are unaffected).
  // NOT block-scoped: `_a` is also needed further down for rackScope() when the
  // rack id is computed. It used to live in a bare block, which put it out of
  // scope at the call site and made every scan throw ReferenceError - surfaced
  // to the user as "Please upload a clearer photo", blaming their image.
  const _caller = softAuthPayload(req);
  if (_caller?.organizationId && !auth.isOrgActive(_caller.organizationId)) {
    safeUnlink(req.file.path);
    return res.status(403).json({ error: 'Your organization is awaiting owner approval before you can scan.' });
  }

  // The Site the person chose on the scan screen, when they chose one. From
  // here on `_a` is the caller as a technician of that Site; without `siteId`
  // it is the caller exactly as they signed in.
  const _site = scanSiteFor(req, res);
  if (!_site) { safeUnlink(req.file.path); return undefined; }
  const _a = _site.auth;

  // Optional binding to a space the admin set up (organisation setup). The
  // phone sends `spaceId` alongside the image. The space must belong to the
  // same Site the scan is claimed for - the tenant claimRack uses below - or
  // the scan is refused before any work is done, 404 like any resource in
  // another Site. Without spaceId nothing in this handler changes.
  let _scanSpace = null;
  const _spaceIdRaw = req.body?.spaceId;
  if (_spaceIdRaw !== undefined && _spaceIdRaw !== null && String(_spaceIdRaw).trim() !== '') {
    const raw = String(_spaceIdRaw).trim();
    let sp = null;
    try {
      sp = /^\d+$/.test(raw) ? estate.getSpace(Number(raw)) : null;
    } catch (err) {
      safeUnlink(req.file.path);
      logger.error({ event: 'scan.space_lookup_failed', spaceId: raw, err: err.message }, 'space lookup failed');
      return res.status(500).json({ error: 'Could not look up the space' });
    }
    const bindTenant = scanOwnerTenantId(_a);
    if (!sp || !bindTenant || Number(sp.tenant_id) !== Number(bindTenant)) {
      safeUnlink(req.file.path);
      logger.warn({ event: 'scan.space_denied', spaceId: raw, tenantId: bindTenant, userId: _a?.sub },
        'scan asked for a space outside the caller Site');
      return res.status(404).json({ error: 'Space not found' });
    }
    _scanSpace = { id: sp.id, name: sp.name };
  }
  // Spread into the response and the persisted meta; empty when no space was given.
  const _spaceFields = _scanSpace ? { space: _scanSpace } : {};
  // Record "this rack lives in that space" (source: learned) for whichever
  // rack id the scan ends up serving. Never fatal: the scan result is the
  // product, the binding is bookkeeping.
  const _bindScanSpace = (boundRackId, tenantId, userId) => {
    if (!_scanSpace || !tenantId) return;
    try {
      estate.upsertRack(tenantId, { rack_id: boundRackId, space_id: _scanSpace.id, source: 'learned' }, userId);
    } catch (err) {
      logger.warn({ event: 'scan.space_bind_failed', rackId: boundRackId, spaceId: _scanSpace.id, err: err.message },
        'could not record the rack in its space');
    }
  };

  let tmpPath = req.file.path;
  const reqStart = Date.now();
  const timings = {};

  try {
    const tNormStart = Date.now();
    const intake = await intakeImage(req.file);
    tmpPath = intake.path;
    tmpPath = await normalizeImage(tmpPath, intake);
    timings.normalize_ms = Date.now() - tNormStart;
    const rackId    = computeRackId(tmpPath, rackScope(_a));
    const jsonPath  = path.join(engineDir(rackId), 'device_unit_map.json');

    // Tenant ownership: anyone scanning an image (cached or fresh) is
    // making a tenant-scoped claim on this rack. Idempotent - multiple
    // tenants can co-own the same RK-id when they scan the same image.
    const _authPayload = _a;
    const _scanTenantId = scanOwnerTenantId(_authPayload);
    const _scanUserId = _authPayload?.sub || null;
    if (_scanTenantId) tenant.claimRack(_scanTenantId, rackId, _scanUserId);

    // ── Cache hit ──────────────────────────────────────────
    if (fs.existsSync(jsonPath)) {
      safeUnlink(tmpPath); // discard duplicate upload
      logger.info({ event: 'scan.cache_hit', rackId, tenantId: _scanTenantId }, `cache hit ${rackId}`);
      recordEvent('scan.cache_hit', { rackId, tenantId: _scanTenantId });
      await ensurePortCounts(rackId);

      // AL auto-apply also on cache hit. Even though it's the same image,
      // a previously-saved correction may not yet be reflected in
      // device_unit_map.json - apply it now so the cached result shows
      // the user's corrected device classes.
      let _alAppliedCacheHit = 0;
      if (hasDeviceCorrections(_authPayload?.organizationId ?? null)) try {
        const alResult = await runActiveLearningCli(
          { cmd: 'apply_to_scan', rack_dir: engineDir(rackId), org_id: _authPayload?.organizationId || null },
          90000
        );
        _alAppliedCacheHit = alResult?.applied || 0;
        if (_alAppliedCacheHit > 0) {
          logger.info({
            event: 'al.auto_applied_cache',
            rackId, applied: _alAppliedCacheHit, changes: alResult.changes,
          }, `AL applied ${_alAppliedCacheHit} corrections to cached ${rackId}`);
        }
      } catch (err) {
        logger.warn({ event: 'al.auto_apply_cache_failed', rackId, error: err.message },
          'AL auto-apply on cache hit failed (non-fatal)');
      }

      // Synchronously rebuild scan_result.json BEFORE responding, so the
      // client's next /api/scan/:rackId/result call gets the AL-corrected
      // view - avoids the setImmediate race in scheduleCanonicalRefresh().
      if (_alAppliedCacheHit > 0) {
        try { writeCanonicalScanResult(rackId); } catch (_) {}
      }

      // Re-scanning the same image is a cache hit (same content-hash RK-id),
      // but the user still just scanned it - so Recent Scans should show "now",
      // not the first scan's time. Refresh the scan timestamp (preserving the
      // original as first_scanned_at). /api/scans sorts + displays by timestamp.
      try {
        const m = readMeta(rackId) || {};
        if (!m.first_scanned_at) m.first_scanned_at = m.timestamp || new Date().toISOString();
        m.timestamp = new Date().toISOString();
        // The person who just uploaded it is the one the result names ("by
        // ..."), not whoever uploaded the same photo first (kept as first_username).
        if (req.user?.username && m.username !== req.user.username) {
          if (!m.first_username) m.first_username = m.username || null;
          m.username = req.user.username;
          if (req.user.id != null) m.userId = req.user.id;
        }
        if (_scanSpace) m.space = _scanSpace;
        writeMeta(rackId, m);
      } catch (_) { /* non-fatal - history just keeps the old time */ }
      stampScanSite(rackId, _site);
      _bindScanSpace(rackId, _scanTenantId, _scanUserId);

      timings.total_ms = Date.now() - reqStart;
      timings.cached = true;
      timings.al_applied = _alAppliedCacheHit;
      audit.log({ req, action: 'scan.create', status: 'ok', targetType: 'rack', targetId: rackId, payload: { cached: true, al_applied: _alAppliedCacheHit } });
      // The rack's name, labels and height follow in the background; the
      // result opens now and asks GET /api/scan/:rackId/settle for them.
      settleInBackground(rackId, { tenantId: _scanTenantId, userId: _scanUserId });
      scheduleCanonicalRefresh(rackId);
      return res.json({ ...buildResponse(rackId, true), ..._spaceFields, timings, settling: true });
    }

    // ── Confirmed-rack bypass ──────────────────────────────
    // A re-shot photo of a rack the user already CONFIRMED gets a new content
    // hash, so it misses the cache above. If it perceptually matches a confirmed
    // rack, serve that confirmed result instead of re-detecting - "show what's
    // already confirmed." Only runs when this org has confirmed racks (cheap
    // file check first, so the ~pHash+embedding lookup cost is paid only when it
    // can possibly match). Strict thresholds guard against a wrong-rack match.
    const _forceFresh = req.body?.forceFresh === '1' || req.body?.forceFresh === 'true';
    try {
      const _orgId = _authPayload?.organizationId || null;
      if (!_forceFresh && hasConfirmedRacks(_orgId)) {
        const cr = await runActiveLearningCli(
          { cmd: 'find_confirmed_rack', image_path: tmpPath, org_id: _orgId }, 90000);
        const matchId = cr?.confirmed?.rack_id;
        if (matchId && matchId !== rackId &&
            fs.existsSync(path.join(engineDir(matchId), 'device_unit_map.json'))) {
          safeUnlink(tmpPath);
          logger.info({ event: 'scan.confirmed_bypass', uploadedAs: rackId, matchId,
            match: cr.confirmed.match_type }, `served confirmed rack ${matchId} for re-upload`);
          recordEvent('scan.confirmed_bypass', { matchId, match: cr.confirmed.match_type });
          if (_scanTenantId) tenant.claimRack(_scanTenantId, matchId, _scanUserId);
          // The rack the user is looking at is the confirmed one, so that is
          // the id the space is recorded against.
          _bindScanSpace(matchId, _scanTenantId, _scanUserId);
          // The person just uploaded this rack again: History and the result
          // show this upload's time, as on a plain cache hit (the first time
          // is kept as first_scanned_at).
          try {
            const m = readMeta(matchId) || {};
            if (!m.first_scanned_at) m.first_scanned_at = m.timestamp || new Date().toISOString();
            m.timestamp = new Date().toISOString();
            if (req.user?.username && m.username !== req.user.username) {
              if (!m.first_username) m.first_username = m.username || null;
              m.username = req.user.username;
              if (req.user.id != null) m.userId = req.user.id;
            }
            if (_scanSpace) m.space = _scanSpace;
            writeMeta(matchId, m);
          } catch (_) { /* non-fatal - the DB row above still carries the binding */ }
          stampScanSite(matchId, _site);
          await settleRack(matchId, { tenantId: _scanTenantId, userId: _scanUserId });
          timings.total_ms = Date.now() - reqStart;
          timings.confirmed_bypass = true;
          audit.log({ req, action: 'scan.create', status: 'ok', targetType: 'rack',
            targetId: matchId, payload: { confirmed_bypass: true, match: cr.confirmed.match_type } });
          return res.json({ ...buildResponse(matchId, true), ..._spaceFields, timings,
            servedFromConfirmed: true, confirmedRackId: matchId });
        }
      }
    } catch (err) {
      logger.warn(`[confirmed-rack] lookup skipped: ${err.message}`);
    }

    // ── Quality pre-check (tilt) ───────────────────────────
    const skipQualityCheck = req.body?.skipQualityCheck === '1' || req.body?.skipQualityCheck === 'true';
    const tQualStart = Date.now();
    const quality = skipQualityCheck
      ? { ok: true, metrics: { note: 'user-override' } }
      : await runQualityCheck(tmpPath);
    timings.quality_check_ms = Date.now() - tQualStart;
    if (!quality.ok) {
      safeUnlink(tmpPath);
      return res.status(400).json({
        error: quality.error,
        metrics: quality.metrics,
        kind: quality.kind || null,
        retryable: quality.retryable === true,
      });
    }

    // ── Rack-presence gate (reject non-racks at the first step) ──
    // A photo of a person / laptop / random object passes the tilt check but
    // contains no rack devices. A quick detect-only pass (~1s) rejects it here,
    // BEFORE the full pipeline, so the user isn't left watching an "analyzing"
    // spinner that returns nothing. Best-effort: if detect errors, fall through
    // to the full pipeline (whose own 0-device check still catches it).
    // Two tiers, because "is this a rack" is not a yes/no the detector can
    // answer confidently. Zero detections is a clear miss. But a photo of a
    // desktop tower is NOT zero - it yields a couple of boxes the model can
    // only call Empty or Unidentified - and the old single test (devices === 0)
    // waved it through, which is why 2D and 3D views were being generated for
    // things that are not racks at all.
    //
    // So: nothing found, or nothing found that is actual rack EQUIPMENT, and we
    // stop and say so. Both stay `retryable`, which the scan page renders as
    // "Retake / Proceed anyway": a real rack shot in bad light can detect
    // nothing, and refusing outright would strand the person holding the phone
    // in front of it. Blocking without an override is the worse failure -
    // guessing wrong about a genuine rack costs more than analysing a desk.
    if (!skipQualityCheck) {
      try {
        const det = await pool.request('detect_only', { image_path: tmpPath, config_path: CONFIG_PATH });
        if (det && det.ok && Array.isArray(det.devices)) {
          // Empty / Closed Unit / Unidentified are rack-slot placeholders, not
          // equipment - the same set the client hides from its overlay. A photo
          // whose only detections are these has not shown us a rack.
          const PLACEHOLDER = new Set(['Empty', 'Closed Unit', 'Unidentified']);
          const equipment = det.devices.filter(d => d && !PLACEHOLDER.has(d.class_name));
          if (det.devices.length === 0 || equipment.length === 0) {
            safeUnlink(tmpPath);
            logger.info({ event: 'scan.not_a_rack', detected: det.devices.length,
                          equipment: equipment.length,
                          classes: [...new Set(det.devices.map(d => d && d.class_name))].slice(0, 6) },
              `rejected upload: ${det.devices.length} detection(s), ${equipment.length} of them equipment`);
            return res.status(400).json({
              error: det.devices.length === 0
                ? "This doesn't look like a server rack. Point the camera at the front of a rack so its devices and ports are visible."
                : "No rack equipment was recognised in this photo - nothing that looks like a switch, patch panel, server or PDU. Point the camera at the front of a rack.",
              retryable: true,
              kind: 'not_a_rack',
            });
          }
        }
      } catch (_) { /* detect failed - let the full pipeline decide */ }
    }

    // ── Cache miss - run pipeline ──────────────────────────
    // The scan's folder, under its organization, Site and rack (lib/store.js).
    // The photo is kept as photo.jpg (normalizeImage() always gives a JPEG) and
    // the upload in the cache is removed the moment it is there.
    const scanPaths = ensureScanFolder(rackId, { orgId: _a?.organizationId ?? null, siteId: _scanTenantId });
    const rackDir = scanPaths.engine;
    const imagePath = scanPaths.photo;
    fs.copyFileSync(tmpPath, imagePath);
    safeUnlink(tmpPath);

    const meta = {
      rackId,
      userId:     softAuthUserId(req),  // null for unauthenticated scans
      username:   req.user?.username || null,
      tenantId:   _a?.tenantId ?? null,  // owning Site (the chosen one, when one was)
      org_id:     store.scanInfo(rackId)?.orgId ?? null,
      site_id:    _scanTenantId ?? null,
      rack_key:   store.scanInfo(rackId)?.rackKey ?? rackId,
      device:     String(req.get('user-agent') || '').slice(0, 200) || null,
      app_version: String(req.body?.appVersion || '').slice(0, 40) || null,
      imageHash:  crypto.createHash('sha256').update(fs.readFileSync(imagePath)).digest('hex'),
      timestamp:  new Date().toISOString(),
      quality:    quality.metrics || null,
      qualityWarning:    quality.warning || null,
      qualityWarningMsg: quality.warning_msg || null,
      ..._spaceFields,  // { space: { id, name } } only when the scan named one
      // Where the phone was when the photo was taken, if it said. Used by the
      // rack ladder to tell which Site the photo was taken at (lib/location.js),
      // never to pick a rack: indoors it is good to tens of metres at best.
      ...captureLocationFrom(req.body),
    };
    writeMeta(rackId, meta);
    _bindScanSpace(rackId, _scanTenantId, _scanUserId);

    const tPipeStart = Date.now();
    await runPipelineAnalyze(imagePath, rackDir, _a?.organizationId || null, rackId);
    timings.pipeline_ms = Date.now() - tPipeStart;

    // ── Front-of-rack + framing check (post-pipeline) ──────
    const mapPath = path.join(rackDir, 'device_unit_map.json');
    const mapData = fs.existsSync(mapPath) ? JSON.parse(fs.readFileSync(mapPath, 'utf8')) : {};
    const deviceCount = Array.isArray(mapData.devices) ? mapData.devices.length : 0;
    const unitCount = Array.isArray(mapData.units_detected) ? mapData.units_detected.length : 0;

    // When the user clicked "Proceed" on the quality warning, they have
    // explicitly asked us to accept whatever the pipeline produces. Don't
    // re-gate on deviceCount / unitCount in that case - the pipeline ran,
    // so whatever it found is what they'll see.
    if (!skipQualityCheck) {
      if (deviceCount === 0) {
        // Deliberately NOT deleting rackDir. The pipeline has already run - the
        // expensive part is done - and this is a retryable warning, not a
        // failure. Deleting it meant "Proceed anyway" had nothing to reuse and
        // re-ran the whole analysis on an image that had just been processed.
        // Rack ids are content hashes, so keeping the output lets the retry hit
        // the cache and return immediately.
        return res.status(400).json({
          error: 'Please take the photo from the front of the rack - we need to see the devices and ports face-on.',
          retryable: true,
          kind: 'quality',
        });
      }

      if (unitCount < 3) {
        // Kept for the same reason as above - "Proceed anyway" reuses it.
        // Say what was actually seen. "Upload a clearer photo" on a photo that
        // IS clear reads as the app being broken, and gives the user nothing
        // to act on - a small or partly visible rack needs different advice
        // from a blurry one.
        return res.status(400).json({
          error: `Only ${unitCount} rack unit${unitCount === 1 ? '' : 's'} could be made out in that photo. `
               + `Move back so the whole rack fits in the frame, or get closer if the rack is small - `
               + `we need to see at least three units to map it.`,
          retryable: true,
          kind: 'quality',
        });
      }
    }

    timings.total_ms = Date.now() - reqStart;
    timings.cached = false;
    try { const m = readMeta(rackId) || {}; m.timings = timings; writeMeta(rackId, m); } catch (_) { /* bookkeeping only */ }
    logger.info({ event: 'scan.created', rackId, durationMs: timings.total_ms, timings },
      `new scan ${rackId} (analyze ${timings.total_ms}ms)`);
    recordEvent('scan.created', { rackId, durationMs: timings.total_ms });
    audit.log({
      req,
      action: 'scan.create',
      status: 'ok',
      targetType: 'rack',
      targetId: rackId,
      payload: { devices: deviceCount, units: unitCount, totalMs: timings.total_ms },
    });

    // ── Active-learning auto-apply ────────────────────────
    // Overlay any matching device-class corrections from the AL memory
    // store BEFORE the canonical refresh, so the user's prior
    // corrections (saved on past scans of similar images) take effect
    // on this fresh scan. Mutates device_unit_map.json in place.
    // Awaited so the canonical refresh (next line) sees the updates.
    let _alAppliedMiss = 0;
    const tAl = Date.now();
    if (hasDeviceCorrections(_authPayload?.organizationId ?? null)) try {
      const alResult = await runActiveLearningCli(
        { cmd: 'apply_to_scan', rack_dir: rackDir, org_id: _authPayload?.organizationId || null },
        90000
      );
      _alAppliedMiss = alResult?.applied || 0;
      if (_alAppliedMiss > 0) {
        logger.info({
          event: 'al.auto_applied',
          rackId,
          applied: _alAppliedMiss,
          changes: alResult.changes,
        }, `AL auto-applied ${_alAppliedMiss} device corrections to ${rackId}`);
        timings.al_applied = _alAppliedMiss;
      }
    } catch (err) {
      logger.warn({ event: 'al.auto_apply_failed', rackId, error: err.message },
        'AL auto-apply failed (non-fatal)');
    }

    timings.al_ms = Date.now() - tAl;

    // Synchronously rebuild scan_result.json BEFORE responding so the
    // client's next read picks up AL corrections without a race.
    if (_alAppliedMiss > 0) {
      try { writeCanonicalScanResult(rackId); } catch (_) {}
    }

    // The result answers as soon as the devices and ports are read. Reading
    // the labels, naming the rack (the ladder) and deciding its height run
    // after it, in the background; the client asks GET /api/scan/:rackId/settle
    // and refreshes the name and labels when they are there (and asks
    // "Which rack is this?" then, if the ladder ends in ask).
    timings.first_answer_ms = Date.now() - reqStart;
    settleInBackground(rackId, { tenantId: _scanTenantId, userId: _scanUserId });
    scheduleCanonicalRefresh(rackId);
    res.json({ ...buildResponse(rackId, false), ..._spaceFields, timings, settling: true });

  } catch (err) {
    // Clean up tmp if still around
    safeUnlink(tmpPath);
    // Not a picture we can read: the user's file, said plainly, no stack trace.
    if (imageIntake.isUnreadable(err)) {
      logger.warn({ event: 'scan.unreadable_upload', kind: err.kind, detail: err.detail }, `[scan] ${err.message}`);
      audit.log({ req, action: 'scan.create', status: 'fail', error: err.message });
      return res.status(400).json({ error: err.message, retryable: true, kind: 'quality' });
    }
    logger.error({ event: 'scan.failed', err: err.message, stack: String(err.stack || '').slice(0, 1500) },
      `[scan] analyze failed: ${err.message}`);
    const deviceDown = isFatalWorkerError(err.message);
    audit.log({ req, action: 'scan.create', status: 'fail',
      error: deviceDown ? ENGINE_DOWN_AUDIT : err.message });
    if (deviceDown) {
      return res.status(503).json({ error: ENGINE_DOWN_MSG, retryable: true, kind: 'engine' });
    }

    // This used to answer "Please upload a clearer photo" for EVERY exception -
    // a crashed worker, a timeout, a bug in our own code - so the app blamed
    // the user's photograph for its own failures. Testers duly re-took clear
    // photos over and over and got the same message, and we lost the real
    // error. Only genuine image problems get the photo advice now; anything
    // else says plainly that the failure was ours.
    const m = String(err.message || '');
    const isImageProblem = /image|decode|corrupt|unsupported|format|heic|exif|dimension|too large|empty file/i.test(m);
    if (isImageProblem) {
      return res.status(400).json({
        error: 'That image could not be read. Try a JPG or PNG taken straight from the camera.',
        retryable: true,
        kind: 'quality',
      });
    }
    res.status(500).json({
      error: 'Something went wrong on our side analysing that photo - it is not a problem with your image. It has been logged; please try again.',
      retryable: true,
      kind: 'server',
    });
  }
});

/**
 * POST /api/stitch
 * Multi-image upload for tall racks. Accepts 2-8 photos (top-to-bottom),
 * normalizes each, runs pipeline/rack_stitch.py to produce a single
 * stitched panorama, then funnels the result through the SAME analyze
 * path as /api/analyze and returns the same shape - plus a `stitch`
 * sub-object describing the seams (so the client can warn the user
 * when an overlap fell back to "butt-flush").
 *
 * Form fields:
 *   images (file[], required) - 2-8 image files, ORDER MATTERS (top→bottom)
 *   skipQualityCheck (string, optional) - same as /api/analyze
 */
function runStitcher(inputPaths, outputPath) {
  return new Promise((resolve, reject) => {
    const { spawn } = require('child_process');
    const pyBin = resolvePython();
    const script = path.join(__dirname, '..', 'pipeline', 'rack_stitch.py');
    // The person's order (top first, with Earlier / Later to fix it) is
    // trusted: the stitcher's own guess at the order put a rack's bottom on
    // top when the photos overlapped little (QA, 5 October 2026).
    const args = [script, '--inputs', ...inputPaths, '--output', outputPath, '--no-auto-order'];
    const child = spawn(pyBin, args, {
      cwd: path.join(__dirname, '..'),
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
      env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' },
    });
    let out = '', err = '';
    child.stdout.on('data', d => out += d.toString());
    child.stderr.on('data', d => err += d.toString());
    child.on('close', code => {
      if (code !== 0 && !out.trim()) {
        return reject(new Error(`stitcher failed (exit ${code}): ${err.trim() || 'no output'}`));
      }
      try {
        resolve(JSON.parse(out));
      } catch (e) {
        reject(new Error(`stitcher output was not JSON: ${e.message} / stderr: ${err.trim()}`));
      }
    });
    child.on('error', reject);
  });
}

app.post('/api/stitch', scanLimit, upload.array('images', 8), async (req, res) => {
  trackScanJob(req, res);
  const files = Array.isArray(req.files) ? req.files : [];
  if (files.length < 2) {
    files.forEach(f => safeUnlink(f.path));
    return res.status(400).json({ error: 'Please upload at least 2 images to stitch (top-to-bottom).' });
  }

  // The Site chosen on the scan screen, as in /api/analyze: refused before any
  // work when the caller may not read it, and the caller's own when none was sent.
  const _site = scanSiteFor(req, res);
  if (!_site) { files.forEach(f => safeUnlink(f.path)); return undefined; }
  const _a = _site.auth;

  const reqStart = Date.now();
  const timings = {};
  const tmpPaths = [];
  let stitchedPath = null;

  try {
    // Normalize every input (HEIC->JPEG, EXIF rotate) before stitching.
    const tNormStart = Date.now();
    for (const f of files) {
      const intake = await intakeImage(f);
      // Recorded before normalising so a failure part-way still cleans it up;
      // normalizeImage removes its input on success, so the entry is replaced.
      tmpPaths.push(intake.path);
      tmpPaths[tmpPaths.length - 1] = await normalizeImage(intake.path, intake);
    }
    timings.normalize_ms = Date.now() - tNormStart;

    stitchedPath = path.join(uploadsDir, `tmp_stitched_${uuidv4()}.jpg`);

    const tStitchStart = Date.now();
    const stitchResult = await runStitcher(tmpPaths, stitchedPath);
    timings.stitch_ms = Date.now() - tStitchStart;

    if (!stitchResult.ok) {
      tmpPaths.forEach(safeUnlink);
      safeUnlink(stitchedPath);
      return res.status(400).json({
        error: stitchResult.error || 'Could not stitch the uploaded images.',
        retryable: true,
        kind: 'stitch',
        stitch: { seams: stitchResult.seams || [], uncertain: stitchResult.uncertain || [] },
      });
    }

    // A join the stitcher could not place (too little overlap, or a repeating
    // rack) would butt two photos together and repeat or lose units: the
    // person is asked to retake those photos, or to analyse it anyway.
    const _stitchAnyway = req.body?.skipQualityCheck === '1' || req.body?.skipQualityCheck === 'true';
    if (!_stitchAnyway && Array.isArray(stitchResult.uncertain) && stitchResult.uncertain.length) {
      tmpPaths.forEach(safeUnlink);
      safeUnlink(stitchedPath);
      const pairs = stitchResult.uncertain.map((i) => `${i + 1} and ${i + 2}`).join(', ');
      return res.status(400).json({
        error: `Photos ${pairs} do not overlap enough to be joined. Retake them so each photo keeps about a quarter of the one before, or analyse them anyway.`,
        retryable: true,
        kind: 'stitch',
        stitch: { seams: stitchResult.seams || [], uncertain: stitchResult.uncertain },
      });
    }

    // Inputs no longer needed - only the stitched output goes downstream.
    tmpPaths.forEach(safeUnlink);

    // ── Now mirror /api/analyze flow on the stitched image ───────
    const rackId   = computeRackId(stitchedPath, rackScope(_a));
    const jsonPath = path.join(engineDir(rackId), 'device_unit_map.json');

    const _authPayload = _a;
    const _scanTenantId = scanOwnerTenantId(_authPayload);
    const _scanUserId = _authPayload?.sub || null;
    if (_scanTenantId) tenant.claimRack(_scanTenantId, rackId, _scanUserId);

    // Cache hit - same stitched image was scanned before.
    if (fs.existsSync(jsonPath)) {
      safeUnlink(stitchedPath);
      logger.info({ event: 'scan.cache_hit', rackId, tenantId: _scanTenantId, stitched: true }, `stitch cache hit ${rackId}`);
      recordEvent('scan.cache_hit', { rackId, tenantId: _scanTenantId, stitched: true });
      await ensurePortCounts(rackId);
      stampScanSite(rackId, _site);
      timings.total_ms = Date.now() - reqStart;
      timings.cached = true;
      audit.log({ req, action: 'scan.create', status: 'ok', targetType: 'rack', targetId: rackId, payload: { cached: true, stitched: true, inputs: files.length } });
      await settleRack(rackId, { tenantId: _scanTenantId, userId: _scanUserId });
      scheduleCanonicalRefresh(rackId);
      return res.json({
        ...buildResponse(rackId, true),
        stitch: {
          seams: stitchResult.seams,
          uncertain: stitchResult.uncertain,
          image_size: stitchResult.image_size,
          input_count: files.length,
          input_order: stitchResult.input_order || null,
          auto_order: stitchResult.auto_order || null,
        },
        timings,
      });
    }

    // Quality check (optional override).
    const skipQualityCheck = req.body?.skipQualityCheck === '1' || req.body?.skipQualityCheck === 'true';
    const tQualStart = Date.now();
    const quality = skipQualityCheck
      ? { ok: true, metrics: { note: 'user-override' } }
      : await runQualityCheck(stitchedPath);
    timings.quality_check_ms = Date.now() - tQualStart;
    if (!quality.ok) {
      safeUnlink(stitchedPath);
      return res.status(400).json({
        error: quality.error,
        metrics: quality.metrics,
        kind: quality.kind || null,
        retryable: quality.retryable === true,
        stitch: { seams: stitchResult.seams, uncertain: stitchResult.uncertain },
      });
    }

    const scanPaths = ensureScanFolder(rackId, { orgId: _a?.organizationId ?? null, siteId: _scanTenantId });
    const rackDir = scanPaths.engine;
    const imagePath = scanPaths.photo;
    fs.copyFileSync(stitchedPath, imagePath);
    safeUnlink(stitchedPath);

    const meta = {
      rackId,
      userId:     softAuthUserId(req),
      tenantId:   _a?.tenantId ?? null,  // owning Site (the chosen one, when one was)
      org_id:     store.scanInfo(rackId)?.orgId ?? null,
      site_id:    _scanTenantId ?? null,
      rack_key:   store.scanInfo(rackId)?.rackKey ?? rackId,
      device:     String(req.get('user-agent') || '').slice(0, 200) || null,
      imageHash:  crypto.createHash('sha256').update(fs.readFileSync(imagePath)).digest('hex'),
      timestamp:  new Date().toISOString(),
      quality:    quality.metrics || null,
      qualityWarning:    quality.warning || null,
      qualityWarningMsg: quality.warning_msg || null,
      stitched:   true,
      stitch:     { seams: stitchResult.seams, uncertain: stitchResult.uncertain, input_count: files.length },
    };
    writeMeta(rackId, meta);

    const tPipeStart = Date.now();
    await runPipelineAnalyze(imagePath, rackDir, _a?.organizationId || null, rackId);
    timings.pipeline_ms = Date.now() - tPipeStart;

    // Post-pipeline framing check (looser than /api/analyze - the user
    // explicitly stitched a tall rack, so we expect more units, but be
    // forgiving about per-tile detection quality).
    const mapPath = path.join(rackDir, 'device_unit_map.json');
    const mapData = fs.existsSync(mapPath) ? JSON.parse(fs.readFileSync(mapPath, 'utf8')) : {};
    const deviceCount = Array.isArray(mapData.devices) ? mapData.devices.length : 0;
    if (!skipQualityCheck && deviceCount === 0) {
      store.deleteScan(rackId);
      return res.status(400).json({
        error: 'No devices were detected on the stitched rack - make sure each photo shows the front of the rack and the shots overlap.',
        retryable: true,
        kind: 'quality',
        stitch: { seams: stitchResult.seams, uncertain: stitchResult.uncertain },
      });
    }

    timings.total_ms = Date.now() - reqStart;
    timings.cached = false;
    logger.info({ event: 'scan.created', rackId, durationMs: timings.total_ms, timings, stitched: true, inputs: files.length },
      `new stitched scan ${rackId} (${files.length} inputs, ${timings.total_ms}ms)`);
    recordEvent('scan.created', { rackId, durationMs: timings.total_ms, stitched: true });
    audit.log({
      req,
      action: 'scan.create',
      status: 'ok',
      targetType: 'rack',
      targetId: rackId,
      payload: { devices: deviceCount, totalMs: timings.total_ms, stitched: true, inputs: files.length },
    });
    await settleRack(rackId, { tenantId: _scanTenantId, userId: _scanUserId });
    scheduleCanonicalRefresh(rackId);
    res.json({
      ...buildResponse(rackId, false),
      stitch: {
          seams: stitchResult.seams,
          uncertain: stitchResult.uncertain,
          image_size: stitchResult.image_size,
          input_count: files.length,
          input_order: stitchResult.input_order || null,
          auto_order: stitchResult.auto_order || null,
        },
      timings,
    });

  } catch (err) {
    tmpPaths.forEach(safeUnlink);
    files.forEach(f => safeUnlink(f.path));   // inputs not yet reached when the loop threw
    if (stitchedPath) safeUnlink(stitchedPath);
    if (imageIntake.isUnreadable(err)) {
      logger.warn({ event: 'scan.unreadable_upload', kind: err.kind, detail: err.detail, stitched: true }, err.message);
      return res.status(400).json({ error: err.message, retryable: true, kind: 'quality' });
    }
    logger.error(err.message);
    const deviceDown = isFatalWorkerError(err.message);
    audit.log({ req, action: 'scan.create', status: 'fail',
      error: deviceDown ? ENGINE_DOWN_AUDIT : err.message, payload: { stitched: true } });
    if (deviceDown) {
      // Same trap as the single-image path: a GPU fault has nothing to do with
      // how the photos overlap, and telling the user to re-shoot the rack sends
      // them round a loop they cannot win.
      return res.status(503).json({ error: ENGINE_DOWN_MSG, retryable: true, kind: 'engine' });
    }
    res.status(400).json({
      error: 'Could not stitch and analyze the rack. Make sure each photo shows the rack front and adjacent shots overlap by ~20-40%.',
      retryable: true,
      kind: 'stitch',
    });
  }
});

/**
 * POST /api/ocr/labels
 * Runs EasyOCR on an uploaded rack-front image and returns extracted text
 * labels with bounding boxes. Used to enrich device names in the analyze
 * flow when physical labels exist on the rack/devices.
 *
 * Body (multipart/form-data):
 *   image  (file, required)         - JPEG/PNG of the rack front
 *   rackId (string, optional)       - if provided, labels are cached under
 *                                     the scan's engine/labels-front.json so
 *                                     they can be mapped to detected devices.
 *
 * Response:
 *   {
 *     image_size: { w, h },
 *     labels:     [ { text, conf, bbox } ],
 *     summary:    { count, highConfCount, hasLabels }
 *   }
 */
/**
 * Read the labels in an image.
 *
 * Through the warm worker, which holds the models in memory, and only through
 * the one-shot script when that cannot start. The script pays for importing
 * torch and building an easyocr Reader on every single call - five to twenty
 * seconds before it looks at the picture - which is the whole of the wait
 * after photographing a label.
 */
/**
 * The device names the scan's site Excel holds, for the flag reader's second
 * chance (pipeline/cable_flags.py handwriting_pass): a reading whose device
 * one of them repairs is preferred. Null when there is no Excel.
 */
function siteDeviceNames(rackId) {
  try {
    const info = store.scanInfo(rackId);
    if (!info || info.siteId == null) return null;
    const doc = require('./lib/matrix').active(info.orgId, info.siteId);
    const names = new Set();
    for (const c of doc?.cables || []) {
      for (const e of ['a', 'z']) { const n = String(c[`${e}_device`] || '').trim(); if (n) names.add(n); }
    }
    // The workbook's devices and the other names they go by (Device_ID, the printed label).
    for (const d of require('./lib/sheet_model').inventoryDevices(doc)) {
      for (const n of [d.name, ...d.aliases]) if (n) names.add(String(n).trim());
    }
    return names.size ? [...names].slice(0, 2000) : null;
  } catch { return null; }
}

async function runOcrLabels(imagePath, { vocab = null } = {}) {
  // The same pixels give the same labels. Read once, remembered against the
  // image's own hash, so a photo uploaded again - or scanned as a second rack
  // - costs nothing the second time.
  const hash = ocrCache.hashFile(imagePath);
  const hit = ocrCache.get(LABELS_CACHE_KIND, hash);
  if (hit) return hit;

  let result;
  try {
    const r = await pool.request('ocr_labels', { image_path: imagePath });
    if (r && r.ok) result = r;
    else throw new Error(r?.error || 'the worker could not read it');
  } catch (e) {
    logger.warn({ event: 'ocr.labels_pool_failed', err: e.message }, 'label read fell back to a fresh process');
    result = await runOcrLabelsOneShot(imagePath);
  }
  // The flags on the cables, read by their own reader (pipeline/cable_flags.py):
  // each line with its device, port and position when it has that shape.
  // A failure here leaves the labels as they are and says so.
  if (result) {
    try {
      const f = await runCableFlags(imagePath, vocab);
      result.flagLines = Array.isArray(f.lines) ? f.lines : [];
      result.flags = Array.isArray(f.flags) ? f.flags : [];
      result.flagStats = f.stats || null;
    } catch (e) {
      logger.warn({ event: 'ocr.cable_flags_failed', err: e.message }, 'cable flags not read');
      result.flagLines = [];
      result.flags = [];
      result.flagError = e.message;
    }
  }
  // Only a real read is worth remembering; an empty one may be a bad moment
  // rather than a photograph with no labels in it.
  if (result && ((Array.isArray(result.labels) && result.labels.length) || (Array.isArray(result.flagLines) && result.flagLines.length)) && !result.flagError) {
    ocrCache.put(LABELS_CACHE_KIND, hash, result);
  }
  return result;
}

/** The cable flags of one photo: through the warm worker, else a fresh process. */
async function runCableFlags(imagePath, vocab = null) {
  try {
    const r = await pool.request('cable_flags', { image_path: imagePath, ...(Array.isArray(vocab) && vocab.length ? { vocab } : {}) });
    if (r && r.ok) return r;
    throw new Error(r?.error || 'the worker could not read the flags');
  } catch (e) {
    logger.warn({ event: 'ocr.cable_flags_pool_failed', err: e.message }, 'cable flag read fell back to a fresh process');
    return runPythonJson(['-m', 'pipeline.cable_flags', imagePath, '--json']);
  }
}

/** Run one pipeline script that prints a JSON document, and parse it. */
function runPythonJson(args) {
  return new Promise((resolve, reject) => {
    const { spawn } = require('child_process');
    const child = spawn(resolvePython(), args, {
      cwd: path.join(__dirname, '..'),
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });
    let out = '', err = '';
    child.stdout.on('data', d => out += d.toString());
    child.stderr.on('data', d => err += d.toString());
    child.on('close', code => {
      if (code !== 0) return reject(new Error(`exit ${code}: ${err.trim() || out.trim() || 'no output'}`));
      try { resolve(JSON.parse(out)); } catch (e) { reject(new Error(`output was not JSON: ${e.message}`)); }
    });
    child.on('error', e => reject(e));
  });
}

function runOcrLabelsOneShot(imagePath) {
  return new Promise((resolve, reject) => {
    const { spawn } = require('child_process');
    const pyBin = resolvePython();
    const script = path.join(__dirname, '..', 'pipeline', 'ocr_labels.py');
    const child = spawn(pyBin, [script, imagePath], {
      cwd: path.join(__dirname, '..'),
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });
    let out = '', err = '';
    child.stdout.on('data', d => out += d.toString());
    child.stderr.on('data', d => err += d.toString());
    child.on('close', code => {
      if (code !== 0) {
        return reject(new Error(`OCR failed (exit ${code}): ${err.trim() || out.trim() || 'no output'}`));
      }
      try {
        resolve(JSON.parse(out));
      } catch (e) {
        reject(new Error(`OCR output was not JSON: ${e.message}`));
      }
    });
    child.on('error', e => reject(e));
  });
}

app.post('/api/ocr/labels', scanLimit, upload.single('image'), async (req, res) => {
  if (!req.file) return res.status(400).json({ error: 'No image file provided' });

  const rackId = (req.body?.rackId || '').trim() || null;

  let tmpPath = req.file.path;
  try {
    const intake = await intakeImage(req.file);
    tmpPath = intake.path;
    tmpPath = await normalizeImage(tmpPath, intake);
    const result = await runOcrLabels(tmpPath);

    // Cache labels in the scan's engine folder so they can be mapped to
    // devices later - only for a scan that exists and the caller may open.
    if (rackId && store.has(rackId) && canAccessRack(softAuthPayload(req), rackId)) {
      fs.writeFileSync(
        path.join(engineDir(rackId), 'labels-front.json'),
        JSON.stringify(result, null, 2)
      );
    }

    safeUnlink(tmpPath);

    const HIGH_CONF  = 0.6;
    const MIN_LABELS = 3;
    const labels = Array.isArray(result.labels) ? result.labels : [];
    const highConfCount = labels.filter(l => (l.conf || 0) >= HIGH_CONF).length;

    res.json({
      image_size: result.image_size || null,
      labels,
      summary: {
        count: labels.length,
        highConfCount,
        hasLabels: highConfCount >= MIN_LABELS,
      },
    });
  } catch (e) {
    safeUnlink(tmpPath);
    if (imageIntake.isUnreadable(e)) {
      logger.warn({ event: 'ocr.unreadable_upload', kind: e.kind, detail: e.detail }, `[ocr] ${e.message}`);
      return res.status(400).json({ error: e.message, labels: [] });
    }
    logger.warn(`[ocr] failed: ${e.message}`);
    res.status(500).json({ error: 'OCR failed', labels: [] });
  }
});

/**
 * POST /api/ocr/device-label
 * Identifies one device from a close-up photo of its label - the recovery
 * path for a device the rack scan couldn't read.
 *
 * A rack photo gives each device only its slice of the frame, so a model
 * string that was perfectly legible in person arrives here 20px tall and the
 * identifier comes back empty. Rather than dead-ending the user in a text
 * field, the app asks for one more photo - of that device alone, framed on
 * its label - where the pixels actually exist. Same OCR engine and same
 * make/model identifier as the rack pass; only the framing changes.
 *
 * The answer is a SUGGESTION for the user to confirm in the manual-entry
 * editor, never a value written straight through. A close-up read is far
 * better than a rack-crop read, which is not the same as trustworthy.
 *
 * Body (multipart/form-data):
 *   image  (file, required)    - JPEG/PNG/HEIC close-up of one device
 *   preset (string, optional)  - 'closeup' (default) | 'fast' | 'accurate'
 *
 * Response 200 (a read that found nothing is still a 200 - the client falls
 * back to manual entry, which is an outcome, not an error):
 *   { ok, make, model, version, raw_text, ocr_conf, match_conf,
 *     alternates: [{make, model, conf}], source, elapsed_ms }
 */
const CLOSEUP_PRESETS = new Set(['closeup', 'fast', 'accurate']);

app.post('/api/ocr/device-label', auth.requireAuth, scanLimit, upload.single('image'), async (req, res) => {
  if (!req.file) return res.status(400).json({ ok: false, error: 'No image file provided' });

  const preset = CLOSEUP_PRESETS.has(req.body?.preset) ? req.body.preset : 'closeup';
  const t0 = Date.now();
  let tmpPath = req.file.path;
  try {
    // Auto-orients from EXIF and re-encodes to JPEG, so a HEIC from the iOS
    // photo library and a canvas capture from the in-app camera arrive at
    // the OCR engine identically.
    const intake = await intakeImage(req.file);
    tmpPath = intake.path;
    tmpPath = await normalizeImage(tmpPath, intake);
    // Same photo, same answer. Hashed AFTER normalising, because that is the
    // image the reader actually sees - the same label photographed twice is
    // two different files and is read twice, which is right; the same file
    // sent twice is not.
    // The preset changes the answer, so it is part of the key.
    const hash = ocrCache.hashFile(tmpPath);
    const kind = `closeup_${preset}`;
    let result = ocrCache.get(kind, hash);
    if (!result) {
      result = await pool.request('closeup_ocr', { image_path: tmpPath, preset });
      // A read that failed is not an answer about this photo - remembering it
      // would make one bad moment permanent.
      if (result && result.ok) ocrCache.put(kind, hash, result);
    }
    safeUnlink(tmpPath);

    const elapsedMs = Date.now() - t0;
    audit.log({ req, action: 'ocr.device_label',
                status: result?.ok ? 'ok' : 'fail',
                targetType: 'device', targetId: result?.model || result?.make || 'unread',
                payload: { source: result?.source, preset, elapsed_ms: elapsedMs } });

    if (!result?.ok) {
      return res.status(500).json({ ok: false,
        error: 'Could not read that photo. Enter the make and model instead.' });
    }
    res.json({ ...result, elapsed_ms: elapsedMs });
  } catch (e) {
    safeUnlink(tmpPath);
    if (imageIntake.isUnreadable(e)) {
      logger.warn({ event: 'ocr.unreadable_upload', kind: e.kind, detail: e.detail }, `[ocr] device-label: ${e.message}`);
      return res.status(400).json({ ok: false, error: e.message });
    }
    logger.warn(`[ocr] device-label failed: ${e.message}`);
    res.status(500).json({ ok: false,
      error: 'Could not read that photo. Enter the make and model instead.' });
  }
});

/**
 * GET /api/ocr/labels/:rackId
 * Returns the cached OCR labels for a rack's front image and maps each label
 * to its best-matching detected device by vertical bbox overlap with the
 * device's U-slot region. Falls back to ocr_devices.json (per-device crop
 * OCR) when no front label file is present - so any physical label captured
 * during the analyze flow is surfaced as a candidate name. When at least one
 * label is detected, also infers the pattern (prefix-CODE-NN) so the client
 * can mint matching names for unlabeled devices in the same rack.
 *
 * Response:
 *   {
 *     front:  { labels: [...], image_size: {w,h} } | null,
 *     deviceLabels: [
 *       { device_index, synthetic_name, label, conf,
 *         source: 'front'|'per_device' }
 *     ],
 *     pattern: { prefix, sep, classTok, padding } | null
 *   }
 */
// Pull the first identifier-shaped token out of raw OCR text. Repairs the
// most common EasyOCR confusions (O↔0, I↔1) when they sit next to digits, so
// "RVEW-CORE-SWO1 STACK MEMBER 2" yields "RVEW-CORE-SW01".
function normalizeOcrLabelText(rawText) {
  if (!rawText) return null;
  const s = String(rawText).trim();
  if (!s) return null;
  for (const tok of s.split(/\s+/)) {
    const fixed = tok
      .replace(/([A-Z])O(?=\d)/g, '$10')
      .replace(/(\d)O/g, '$10')
      .replace(/([A-Z])I(?=\d)/g, '$11')
      .replace(/(\d)I/g, '$11')
      .toUpperCase();
    // Require at least one separator and a letters-then-digits final segment,
    // e.g. RVEW-CORE-SW01 or RACK01_PDU3. Bare tokens like "SW01" don't
    // qualify on their own - they're ambiguous without a site/rack prefix.
    if (/^[A-Z][A-Z0-9]*(?:[-_][A-Z0-9]+)*[-_][A-Z]+\d+$/.test(fixed)) return fixed;
  }
  return null;
}

// Parse RVEW-CORE-SW01 → { prefix:'RVEW-CORE', sep:'-', classTok:'SW', padding:2 }
function inferLabelPattern(label) {
  if (!label) return null;
  const m = label.match(/^(.+)([-_])([A-Z]+)(\d+)$/);
  if (!m) return null;
  return { prefix: m[1], sep: m[2], classTok: m[3], padding: m[4].length };
}

// Brand → class-name lookup. The YOLO detector can't see vendor badges, so
// when OCR catches a known brand inside (or adjacent to) a device's bbox we
// upgrade the class. Order matters - more specific brands first so e.g.
// "Cisco Catalyst" hits CATALYST before CISCO.
const BRAND_CLASS = [
  ['MEDIAPACK',  'Gateway'],
  ['AUDIOCODES', 'Gateway'],
  ['PLANAR',     'Controller'],
  ['TRIPP-LITE', 'PDU'],
  ['TRIPPLITE',  'PDU'],
  ['TRIPP LITE', 'PDU'],
  ['CATALYST',   'Switch'],
  ['NEXUS',      'Switch'],
  ['ARUBA',      'Switch'],
  ['JUNIPER',    'Switch'],
  ['CEDGE',      'Router'],
  ['MERAKI',     'Switch'],
  ['PALOALTO',   'Firewall'],
  ['PALO ALTO',  'Firewall'],
  ['FORTIGATE',  'Firewall'],
  ['FORTINET',   'Firewall'],
  ['CHECKPOINT', 'Firewall'],
  ['APC',        'UPS'],
  ['EATON',      'UPS'],
  ['SCHNEIDER',  'UPS'],
  ['SONY',       'Recorder'],
  ['POLYCOM',    'Gateway'],
  ['CISCO',      'Switch'],
];

function classifyByBrand(text) {
  if (!text) return null;
  const s = String(text).toUpperCase();
  for (const [brand, cls] of BRAND_CLASS) {
    if (s.includes(brand)) return { brand, class_name: cls };
  }
  // Fuzzy matches for common OCR errors that ocr_devices.py emits on these
  // brands (verified against real rack captures).
  if (/\bBON\s+SON\b/.test(s)) return { brand: 'SONY',      class_name: 'Recorder' };
  if (/\bMEDIA\s*PACK\b/.test(s)) return { brand: 'MEDIAPACK', class_name: 'Gateway' };
  if (/\bMEDLA\s*PACK\b/.test(s)) return { brand: 'MEDIAPACK', class_name: 'Gateway' };
  // TRIPP-LITE OCR variants seen in the wild: TRIPPLITE, TRIPP-LITE,
  // TRIPPLME (l→m), TRIPPLE (dropped suffix), TRIPP_LITE, TRIPPL!TE, etc.
  // The TRIPP prefix is distinctive enough that any token starting with it
  // and continuing as letters is safely Tripp-Lite.
  if (/\bTRIPP[A-Z]{1,8}\b/.test(s)) return { brand: 'TRIPP-LITE', class_name: 'PDU' };
  if (/\bRIPP[-\s]?LITE\b/.test(s)) return { brand: 'TRIPP-LITE', class_name: 'PDU' };
  if (/\bPLAN[A4]R\b/.test(s)) return { brand: 'PLANAR', class_name: 'Controller' };
  if (/\bCED[O0]E[K_]?[O0]?[I1]?\b/.test(s)) return { brand: 'CEDGE', class_name: 'Router' };
  return null;
}

app.get('/api/ocr/labels/:rackId', (req, res) => {
  const rackId  = req.params.rackId;
  const rackDir = engineDir(rackId);
  if (!fs.existsSync(rackDir)) return res.status(404).json({ error: `Rack ${rackId} not found` });

  const readJson = (p) => {
    try { return fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, 'utf8')) : null; }
    catch { return null; }
  };

  // For racks scanned before scheduleOcrLabels existed (or where it hasn't
  // completed yet), trigger the full-image OCR pass in the background so
  // brand-token reclassification becomes available on the next refresh.
  if (!fs.existsSync(path.join(rackDir, 'labels-front.json'))) {
    try { scheduleOcrLabels(rackId); } catch (_) {}
  }

  const front = readJson(path.join(rackDir, 'labels-front.json'));
  const dum   = readJson(path.join(rackDir, 'device_unit_map.json'));
  const perDev = readJson(path.join(rackDir, 'ocr_devices.json'));

  const deviceLabels = [];
  if (dum && Array.isArray(dum.devices)) {
    // For each device, find the best-matching front label by Y-overlap.
    // device_unit_map.json stores boxes as `box: [x1,y1,x2,y2]` in pixel
    // coords; label bboxes are also pixel-absolute. Compare Y centers directly
    // rather than going through percentages - dum has no image_size field,
    // and the percentages from labels-front are tied to their own image_size.
    //
    // matchSide only considers identifier-shaped labels (RVEW-CORE-SW01,
    // RACK01-PDU3, …) for naming. Brand badges (PLANAR, TRIPPLME) and
    // descriptive chatter (STACK MEMBER 2, 1044248) are ignored here -
    // they're still consumed by mapFullImageLabels below for reclassification.
    // Without this filter the device chip would show "PLANAR" as its name,
    // and inferLabelPattern would pick a non-pattern token as the template.
    const matchSide = (sideName, sideData) => {
      if (!sideData?.labels?.length) return null;
      return dum.devices.map((dev, idx) => {
        const box = dev.box;
        if (!Array.isArray(box) || box.length < 4) return null;
        const dy = box[1];
        const dh = box[3] - box[1];
        let best = null, bestScore = -1;
        for (const l of sideData.labels) {
          if (!normalizeOcrLabelText(l.text)) continue;
          const ly = (l.bbox?.y ?? 0);
          const lh = (l.bbox?.h ?? 0);
          const lYCenter = ly + lh / 2;
          if (lYCenter < dy - 6 || lYCenter > dy + dh + 6) continue;
          const score = (l.conf || 0);
          if (score > bestScore) { bestScore = score; best = l; }
        }
        return best ? { idx, label: best, side: sideName } : null;
      }).filter(Boolean);
    };

    const frontMatches = matchSide('front', front) || [];

    const matched = new Map();
    for (const m of frontMatches) matched.set(m.idx, m);

    // Find a "stack member N" hint near a device's Y-band - captured as a
    // separate OCR label, e.g. "STACK MEMBER 2". Used to differentiate two
    // physically distinct switches that share the same hostname sticker.
    const findStackMember = (sideData, dy, dh) => {
      if (!sideData?.labels?.length) return null;
      for (const l of sideData.labels) {
        const ly = (l.bbox?.y ?? 0);
        const lh = (l.bbox?.h ?? 0);
        const lyc = ly + lh / 2;
        if (lyc < dy - 6 || lyc > dy + dh + 6) continue;
        const m = String(l.text || '').match(/(?:stack\s*)?mem(?:ber|rer|8er|ber)\s*(\d+)/i);
        if (m) return m[1];
      }
      return null;
    };

    dum.devices.forEach((dev, idx) => {
      const m = matched.get(idx);
      if (!m) {
        deviceLabels.push({ device_index: idx, synthetic_name: dev.name || `dev${idx}`, label: null, conf: null, source: null });
        return;
      }
      const rawText = m.label.text;
      const normalized = normalizeOcrLabelText(rawText) || rawText;
      const box = dev.box;
      const stackN = findStackMember(front, box[1], box[3] - box[1]);
      const finalLabel = stackN ? `${normalized}/${stackN}` : normalized;
      deviceLabels.push({
        device_index:   idx,
        synthetic_name: dev.name || `dev${idx}`,
        label:          finalLabel,
        conf:           m.label.conf,
        source:         m.side,
        stack_base:     normalized,
      });
    });

    // Per-device crop OCR fills any slot still missing a label. We match by
    // U-slot since ocr_devices.json and device_unit_map.json are generated
    // from the same detection pass. Two relaxations from the front/rear path:
    //   - Threshold 0.4 instead of 0.6 - Cisco stack members often produce
    //     mid-confidence OCR on the second/third stack switch because cables
    //     partially occlude the label, but the text is still recognizable.
    //   - Duplicate labels are kept (with a /N stack-member suffix when the
    //     raw text contains "STACK MEMBER N") so two physical switches with
    //     identical hostnames don't collapse to a single chip.
    if (perDev && Array.isArray(perDev.devices)) {
      const seenLabels = new Map(); // normalized label → count assigned
      for (const od of perDev.devices) {
        if (!od.raw_text || (od.ocr_conf || 0) < 0.4) continue;
        const norm = normalizeOcrLabelText(od.raw_text);
        if (!norm) continue;
        const pos = String(od.position || '').toLowerCase();
        const idx = dum.devices.findIndex(d => (d.units || []).some(u => String(u).toLowerCase() === pos));
        if (idx < 0) continue;
        const slot = deviceLabels[idx];
        if (!slot || slot.label) continue;
        // Stack-member differentiation: prefer the explicit "STACK MEMBER N"
        // from raw_text, otherwise increment a /N counter for repeated labels.
        let finalLabel = norm;
        const stackM = od.raw_text.match(/stack\s*member\s*(\d+)/i);
        const seenCount = seenLabels.get(norm) || 0;
        if (stackM) {
          finalLabel = `${norm}/${stackM[1]}`;
        } else if (seenCount > 0) {
          finalLabel = `${norm}/${seenCount + 1}`;
        }
        seenLabels.set(norm, seenCount + 1);
        slot.label  = finalLabel;
        slot.conf   = od.ocr_conf;
        slot.source = 'per_device';
        slot.stack_base = norm; // for the client / debugging
      }
    }
  }

  // Post-pass: symmetric stack-member suffixing. When two+ devices share the
  // same stack_base (e.g. two switches both labelled RVEW-CORE-SW01 because
  // they form a Cisco stack), suffix every member with /N - not just the
  // duplicates after the first. That way the UI doesn't visually merge the
  // primary into a single chip and leave the others looking like /2, /3.
  if (dum && Array.isArray(dum.devices)) {
    const byBase = new Map();
    deviceLabels.forEach(d => {
      if (!d.stack_base) return;
      if (!byBase.has(d.stack_base)) byBase.set(d.stack_base, []);
      byBase.get(d.stack_base).push(d);
    });
    for (const group of byBase.values()) {
      if (group.length < 2) continue;
      // Order bottom-up so suffix /1 is the LOWEST member in the rack. Racks
      // are numbered from the base (U1 at the bottom) and so is a stack; it
      // used to sort top-down, which put /1 on the highest member and read as
      // wrong to anyone standing in front of the rack.
      group.sort((a, b) => (dum.devices[b.device_index]?.box?.[1] ?? 0) - (dum.devices[a.device_index]?.box?.[1] ?? 0));
      group.forEach((d, i) => {
        // Keep an explicit /N already set (from "STACK MEMBER N" text), otherwise
        // assign by Y-order.
        const existing = d.label?.match(/\/(\d+)$/);
        if (!existing) d.label = `${d.stack_base}/${i + 1}`;
      });
    }
  }

  // Pick the highest-confidence label as the pattern template. Prefer the
  // base label (without stack suffix) so the pattern doesn't include /N.
  const bestLabeled = deviceLabels
    .filter(d => d.label && (d.conf || 0) >= 0.6)
    .sort((a, b) => (b.conf || 0) - (a.conf || 0))[0];
  const pattern = bestLabeled ? inferLabelPattern(bestLabeled.stack_base || bestLabeled.label) : null;

  // Brand-token reclassification - read every OCR'd token we have (front
  // image labels + per-device crop text) and, when a known brand name lands
  // inside or atop a device's bbox, upgrade that device's class. This
  // recovers Planar/Sony/Audiocodes/Tripp-Lite/CEdge etc. that YOLO
  // mislabels as UPS / Empty / Server based on silhouette alone.
  const reclassifications = {};
  const noteReclass = (idx, hit, srcText, conf) => {
    if (idx == null || idx < 0 || !hit) return;
    const prev = reclassifications[idx];
    if (prev && (prev.conf || 0) >= (conf || 0)) return;
    reclassifications[idx] = {
      device_index: idx,
      class_name:   hit.class_name,
      brand:        hit.brand,
      raw_text:     srcText,
      conf:         conf || 0,
    };
  };

  if (dum && Array.isArray(dum.devices)) {
    // Per-device crop text - direct device-to-text mapping.
    if (perDev && Array.isArray(perDev.devices)) {
      for (const od of perDev.devices) {
        const hit = classifyByBrand(od.raw_text);
        if (!hit) continue;
        const pos = String(od.position || '').toLowerCase();
        const idx = dum.devices.findIndex(d => (d.units || []).some(u => String(u).toLowerCase() === pos));
        noteReclass(idx, hit, od.raw_text, od.ocr_conf);
      }
    }
    // Full-image OCR labels - match to the device whose bbox vertically
    // contains the label's center. Pixel-absolute comparison; dum has no
    // image_size and dev uses `box: [x1,y1,x2,y2]`.
    const mapFullImageLabels = (sideData) => {
      if (!sideData?.labels?.length) return;
      for (const lbl of sideData.labels) {
        const hit = classifyByBrand(lbl.text);
        if (!hit) continue;
        const ly = lbl.bbox?.y ?? 0;
        const lh = lbl.bbox?.h ?? 0;
        const lYCenter = ly + lh / 2;
        let bestIdx = -1, bestDist = Infinity;
        dum.devices.forEach((dev, idx) => {
          const box = dev.box;
          if (!Array.isArray(box) || box.length < 4) return;
          const dy = box[1];
          const dh = box[3] - box[1];
          if (lYCenter < dy - 6 || lYCenter > dy + dh + 6) return;
          const dist = Math.abs(lYCenter - (dy + dh / 2));
          if (dist < bestDist) { bestDist = dist; bestIdx = idx; }
        });
        noteReclass(bestIdx, hit, lbl.text, lbl.conf);
      }
    };
    mapFullImageLabels(front);

    // Label-driven reclassification: when YOLO marked a device Unidentified
    // (or Empty) but OCR captured an identifier-shaped label whose class
    // token names a known class - e.g. "RVEW-CORE-SW01" → SW → Switch - use
    // the label's own evidence to upgrade the class. Only applies to
    // low-confidence YOLO classes so we don't overrule strong detections.
    const CLASS_FROM_CODE = {
      SW:'Switch', SWITCH:'Switch', SWT:'Switch',
      PP:'Patch Panel', PANEL:'Patch Panel',
      FW:'Firewall', FWL:'Firewall',
      RO:'Router', RTR:'Router', RT:'Router',
      SVR:'Server', SRV:'Server', SERVER:'Server',
      LB:'Load Balancer',
      GW:'Gateway', GT:'Gateway', GTW:'Gateway',
      MO:'Modem', MDM:'Modem',
      CTRL:'Controller', CTL:'Controller',
      REC:'Recorder',
      AMP:'Amplifier',
      PDU:'PDU', PSU:'PSU', UPS:'UPS',
    };
    const WEAK_CLASSES = new Set(['Unidentified', 'Empty', 'Closed Unit']);
    for (const d of deviceLabels) {
      if (!d.stack_base) continue;
      const dev = dum.devices[d.device_index];
      if (!dev || !WEAK_CLASSES.has(dev.class_name)) continue;
      const codeM = d.stack_base.match(/[-_]([A-Z]+)\d+$/);
      const inferred = codeM ? CLASS_FROM_CODE[codeM[1]] : null;
      if (inferred) {
        noteReclass(d.device_index, { brand: 'LABEL', class_name: inferred }, d.stack_base, d.conf || 0.5);
      }
    }
  }

  res.json({
    front, deviceLabels, pattern,
    reclassifications: Object.values(reclassifications),
  });
});

/**
 * POST /api/select
 * Runs full pipeline with --device_index and --port on the cached rack image.
 * Reads the photo from the scan folder - no in-memory state required.
 */
app.post('/api/select', auth.requireAuth, async (req, res) => {
  const { scanId, device_index, port, port_category } = req.body;
  const rackId = scanId;

  if (!rackId || device_index == null || port == null) {
    return res.status(400).json({ error: 'scanId, device_index, and port are required' });
  }

  // This route takes the rack id from the BODY (scanId), so the
  // app.param('rackId') guard - which only fires for routes with :rackId in
  // the path - never runs here. Enforce both checks manually:
  //   1. shape-validate to block path traversal (rackId flows into path.join
  //      and writeMeta below),
  //   2. tenant-ownership (same soft check as the app.param guard).
  if (!/^RK-[A-Za-z0-9]{4,32}$/.test(rackId)) {
    return res.status(400).json({ error: 'Invalid scanId' });
  }
  const _selAuth = softAuthPayload(req);
  if (!canAccessRack(_selAuth, rackId)) {
    return res.status(404).json({ error: 'Rack not found' });
  }

  const VALID_CATEGORIES = new Set(['main', 'sfp', 'console', 'other']);
  const portCategory = port_category && VALID_CATEGORIES.has(port_category)
    ? port_category
    : 'main';

  let meta = readMeta(rackId);

  const rackDir = engineDir(rackId);
  const imagePath = photoPathOf(rackId);
  if (!imagePath) {
    return res.status(404).json({ error: `Rack ${rackId} not found. Please re-upload the image.` });
  }

  // meta.json can go missing even when the analysis is fully intact -
  // e.g. the cache-hit analyze path skips writeMeta, or the folder was
  // created by a flow that didn't persist it. /api/select only needs the
  // device map + original image (both present here), so reconstruct and
  // heal the meta instead of hard-404ing with "please re-upload".
  if (!meta) {
    // Backfill provenance from an existing canonical result so healing the
    // meta doesn't null out userId / imageHash / the original scan time.
    let prov = {};
    try {
      const sr = store.readResult(rackId)?.api;
      if (sr) {
        prov = {
          userId: sr?.createdBy?.userId ?? null,
          imageHash: sr?.image?.imageHash ?? null,
          timestamp: sr?.createdAt || sr?.timestamp || null,
        };
      }
    } catch (_) { /* fall back to defaults below */ }
    meta = {
      rackId,
      userId: prov.userId ?? null,
      imageHash: prov.imageHash ?? null,
      timestamp: prov.timestamp || new Date().toISOString(),
      reconstructed: true,
    };
    try { writeMeta(rackId, meta); } catch (_) {}
  }

  const reqStart = Date.now();
  const timings = {};

  // ── Apply user's port-number shift before highlighting ──
  // If the user previously corrected this device's port numbering
  // (e.g. "this is port 5, model said 7"), every later "select port N"
  // must hit the same physical position N would have under the user's
  // corrected numbering. We translate the user-visible port number
  // back to the raw port number for the pipeline, then re-stamp the
  // user-visible number on the response.
  const userPort = Number(port);
  let rawPort = userPort;
  let appliedShift = 0;
  if (portCategory === 'main') {
    try {
      // Latest correction only - the offset is absolute (predicted_port is
      // stored raw), so it never compounds across corrections.
      const shift = _latestPortShift(_readFeedbackForScan(rackId), device_index);
      if (shift !== 0) {
        rawPort = userPort - shift;
        appliedShift = shift;
        logger.info({ event: 'select.port_shift_applied',
                      rackId, device_index, userPort, rawPort, shift },
          `select: user port ${userPort} → raw port ${rawPort} (shift ${shift})`);
      }
    } catch (err) {
      logger.warn({ event: 'select.port_shift_failed', rackId, error: err.message },
        'port-shift translation failed; using raw port number');
    }
  }
  if (rawPort < 1) {
    // The accumulated numbering shift pushes this port before the device's
    // first physical port - the correction history has compounded into an
    // out-of-range offset. Rather than hard-block the user, fall back to the
    // raw (un-shifted) numbering for this selection so they can still inspect
    // the port. They can re-apply a correction to fix the numbering.
    logger.warn({ event: 'select.port_shift_out_of_range',
                  rackId, device_index, userPort, rawPort, appliedShift },
      `select: shift ${appliedShift} pushes port ${userPort} out of range - ignoring shift`);
    rawPort = userPort;
    appliedShift = 0;
  }

  try {
    const tPipeStart = Date.now();
    const _targetCount = portCategory === 'main' ? publishedPortCountFor(rackId, device_index) : 0;
    await runPipelineSelect(imagePath, rackDir, device_index, rawPort, portCategory, _selAuth?.organizationId, _targetCount, appliedShift);
    timings.pipeline_ms = Date.now() - tPipeStart;

    const infoPath = path.join(rackDir, 'selected_port_info.json');
    const fullData = fs.existsSync(infoPath)
      ? JSON.parse(fs.readFileSync(infoPath, 'utf8'))
      : {};
    const portInfo = fullData.port_info || {};

    // 'invalid' is the pipeline's signal that the requested number fell outside
    // this category's port list, so it never located a port at all (runner.py
    // sets it in the `else` of `if 1 <= port_number <= cat_count`). That is a
    // different fact from "found the port, couldn't measure it", and the
    // coercion below used to flatten the two together - so an unlocatable port
    // came back as a perfectly ordinary EMPTY port. The user saw a result for a
    // port the pipeline had not found, which is the "wrong port / no port"
    // half of the second-lookup bug. Answer honestly instead, and name the real
    // range so the number they should have typed is on screen.
    if (portInfo.status === 'invalid') {
      // port_classification carries console/main/sfp only, so 'other' (USB)
      // resolves to 0 and takes the generic wording below rather than naming a
      // range we can't actually vouch for.
      const realCount = Number(fullData.port_classification?.[portCategory]) || 0;
      const catLabel = portCategory === 'main' ? '' : `${portCategory} `;
      logger.warn({ event: 'select.port_out_of_range', rackId, device_index,
                    userPort, rawPort, portCategory, realCount, targetCount: _targetCount },
        `select: ${portCategory} port ${userPort} outside detected range (${realCount})`);
      audit.log({
        req, action: 'scan.select_port', status: 'fail',
        targetType: 'rack', targetId: rackId,
        error: 'port out of range',
        payload: { device_index: Number(device_index), port: userPort, portCategory, realCount },
      });
      return res.status(422).json({
        error: realCount > 0
          ? `This device has ${realCount} ${catLabel}port${realCount === 1 ? '' : 's'} - enter a number between 1 and ${realCount}.`
          : `We couldn't locate ${catLabel}port ${userPort} on this device. Confirm the port count below, then pick a port.`,
        portOutOfRange: true,
        detectedCount: realCount,
        portCategory,
      });
    }

    // Occupancy is 'connected', 'empty', or 'unknown' - 'unknown' meaning the
    // status model couldn't measure THIS port (a coverage gap). The pipeline is
    // deliberately honest about that and never guesses (see pipeline/runner.py).
    // But testers found a bare "Unknown" on the Port Located screen confusing,
    // so for the FIND-PORT DISPLAY ONLY we present an unmeasured port as
    // 'empty' - the conservative reading (don't claim a link we didn't see).
    // The honest value is preserved on occupancy_source, and the data written
    // to disk is the pipeline's, untouched by this line.
    if (portInfo.status !== 'connected' && portInfo.status !== 'empty') {
      portInfo.occupancy_source = portInfo.occupancy_source || 'unknown';
      portInfo.status_measured = portInfo.status || 'unknown';
      portInfo.status = 'empty';
    }

    // Re-stamp port_info with the user-visible number so the UI sees
    // its own corrected numbering, not the raw pipeline output.
    if (appliedShift !== 0) {
      portInfo.port_number = userPort;
      portInfo._port_shift = { applied: appliedShift, raw_port: rawPort };
    }

    // Apply the tech's stored cable-colour correction for THIS port so a
    // re-located port shows the colour they confirmed - not the model's guess
    // again. Matched by device + port-location signature (same key the report
    // path uses). Without this, cable-colour feedback never "sticks".
    try {
      const loc = (portInfo.location || []).join(',');
      const cfb = _readFeedbackForScan(rackId)
        .filter(r => r.feedback_type === 'port'
          && Number(r.device_index) === Number(device_index)
          && (r.port_location || []).join(',') === loc
          && r.actual_cable_color)
        .sort((a, b) => (a.timestamp || '').localeCompare(b.timestamp || ''));
      const last = cfb[cfb.length - 1];
      if (last && last.actual_cable_color && last.actual_cable_color !== portInfo.cable_color) {
        portInfo._cable_color_model = portInfo.cable_color;
        portInfo.cable_color = last.actual_cable_color;
        const nt = _swapCableTypeColor(portInfo.cable_type, last.actual_cable_color);
        if (nt) portInfo.cable_type = nt;
        portInfo._corrected = true;
        logger.info({ event: 'select.cable_color_corrected', rackId, device_index,
          from: portInfo._cable_color_model, to: last.actual_cable_color },
          `select: applied cable-colour correction → ${last.actual_cable_color}`);
      }
    } catch (err) {
      logger.warn({ event: 'select.cable_fb_failed', rackId, error: err.message },
        'cable-colour feedback application failed');
    }

    // Archive per-port image copies + log this identification so the report
    // can show every port the user has inspected (not just the last one).
    // New layout: copies live under <rack>/ports/, source pipeline PNGs
    // under <rack>/images/.
    const idsPath = path.join(rackDir, 'port_identifications.jsonl');
    const baseDevice = `d${device_index}_p${port}_device.png`;
    const baseFull   = `d${device_index}_p${port}_full.png`;
    const srcDevice = rackImagePath(rackDir, '5_selected_device_with_port.png');
    const srcFull   = rackImagePath(rackDir, '6_full_rack_selected_port.png');
    const dstDevice = rackPortPath(rackDir, baseDevice);
    const dstFull   = rackPortPath(rackDir, baseFull);
    try {
      if (fs.existsSync(srcDevice)) fs.copyFileSync(srcDevice, dstDevice);
      if (fs.existsSync(srcFull))   fs.copyFileSync(srcFull, dstFull);
    } catch (e) { logger.error('port image archive failed:', e.message); }

    const idEntry = {
      timestamp: new Date().toISOString(),
      device_index: Number(device_index),
      port: Number(port),
      port_info: portInfo,
      port_classification: fullData.port_classification || null,
      device_image: fs.existsSync(dstDevice) ? rackPortRelative(rackDir, baseDevice) : null,
      full_rack_image: fs.existsSync(dstFull) ? rackPortRelative(rackDir, baseFull) : null,
    };
    try {
      fs.appendFileSync(idsPath, JSON.stringify(idEntry) + '\n');
    } catch (e) { logger.error('port id log failed:', e.message); }

    timings.total_ms = Date.now() - reqStart;
    audit.log({
      req,
      action: 'scan.select_port',
      status: 'ok',
      targetType: 'rack',
      targetId: rackId,
      payload: { device_index: Number(device_index), port: Number(port) },
    });
    scheduleCanonicalRefresh(rackId);
    res.json({
      resultImageUrl: engineImageUrl(rackId, rackImageUrlPath(rackDir, '5_selected_device_with_port.png')),
      rackImageUrl:   engineImageUrl(rackId, rackImageUrlPath(rackDir, '6_full_rack_selected_port.png')),
      portInfo,
      portClassification: fullData.port_classification || null,
      timings,
    });
  } catch (err) {
    logger.error(err.message);
    audit.log({
      req,
      action: 'scan.select_port',
      status: 'fail',
      targetType: 'rack',
      targetId: rackId,
      error: err.message,
      payload: { device_index, port },
    });
    res.status(500).json({ error: 'Pipeline failed', details: err.message });
  }
});

/**
 * POST /api/analyze-video
 *
 * A video pan: one video that may pass along several racks. The server:
 *   1. Splits the video into one best frame per rack via the worker
 *      (`split_video_racks` command, pipeline.multi_rack_split). A video of
 *      one rack gives one frame.
 *   2. For each best frame, runs the same analysis a photo gets and produces
 *      a normal RK-XXXXXXXX scan, so every per-rack page works as it is.
 *      Each rack is its own scan; nothing ties them together afterwards.
 *
 * Returns:
 *   { ok: true, count, racks: [
 *       { rackId, position, label, deviceCount, score, cached }
 *     ] }
 * The phone opens the first rack; the others are in Scan history.
 */
app.post('/api/analyze-video', auth.requireAuth, scanLimit, upload.single('video'), async (req, res) => {
  if (!req.file) return res.status(400).json({ error: 'No video file provided' });
  trackScanJob(req, res);
  const reqStart = Date.now();
  const videoPath = req.file.path;

  // The Site chosen on the scan screen, as in /api/analyze. Without one the
  // payload is the caller's own; with one, an admin's walk-through lands in
  // the Site they named.
  const _site = scanSiteFor(req, res);
  if (!_site) { safeUnlink(videoPath); return undefined; }

  // Same rules as a photo in /api/analyze: an organization still waiting for
  // approval cannot scan, and the rack is claimed for the Site when there is
  // one.
  const authPayload = _site.auth;
  if (authPayload?.organizationId && !auth.isOrgActive(authPayload.organizationId)) {
    safeUnlink(videoPath);
    return res.status(403).json({ error: 'Your organization is awaiting owner approval before you can scan.' });
  }
  const tenantId = scanOwnerTenantId(authPayload);
  const userId   = authPayload?.sub || null;
  const framesDir = path.join(uploadsDir, `frames_${uuidv4()}`);

  try {
    // 1. Split video into per-rack best frames (worker call).
    const splitResult = await withSpan('video_split', async () => {
      const r = await pool.request('split_video_racks', {
        video_path: videoPath,
        // The best frames go into the upload cache, beside the video, and are
        // removed once each rack's photo.jpg is written.
        output_dir: framesDir,
        // Every other command sends this; without it the splitter fell back to
        // its own config.json and could resolve a different model than the
        // single-rack path on a non-default deployment.
        config_path: CONFIG_PATH,
      });
      if (!r.ok) throw new Error(r.error || 'split failed');
      return r;
    }, { videoPath });

    const detected = Array.isArray(splitResult.racks) ? splitResult.racks : [];
    if (detected.length === 0) {
      safeUnlink(videoPath);
      return res.status(400).json({ error: 'No racks detected in the video. Try a clearer pan.' });
    }

    // 2. Run /api/analyze logic on each best frame, in series so we
    //    don't melt the worker pool.
    const racks = [];

    for (const r of detected) {
      try {
        // Normalize the JPEG so it goes through the same pipeline as
        // an image upload (auto-orient, mozjpeg, etc.)
        const normalizedPath = await normalizeImage(r.best_frame_path);
        const rackId = computeRackId(normalizedPath, rackScope(authPayload));
        const jsonPath = path.join(engineDir(rackId), 'device_unit_map.json');

        // Tenant ownership for each rack, as a photo scan claims it.
        if (tenantId) tenant.claimRack(tenantId, rackId, userId);

        let cached = false;
        if (fs.existsSync(jsonPath)) {
          // Cache hit: no re-analysis.
          cached = true;
          await ensurePortCounts(rackId);
          stampScanSite(rackId, _site);
        } else {
          // Fresh analysis - same path /api/analyze takes: the frame is kept
          // as the scan's photo.jpg, which the results page, the OCR passes
          // and the canonical refresh all read.
          const scanPaths = ensureScanFolder(rackId, { orgId: authPayload?.organizationId ?? null, siteId: tenantId });
          const rackDir = scanPaths.engine;
          const imagePath = scanPaths.photo;
          fs.copyFileSync(normalizedPath, imagePath);
          writeMeta(rackId, {
            // The Site itself (or none), as a photo scan records it; the
            // default tenant is only a claim, never stamped on the scan.
            rackId, userId, tenantId: authPayload?.tenantId ?? null,
            org_id: store.scanInfo(rackId)?.orgId ?? null,
            site_id: tenantId ?? null,
            rack_key: store.scanInfo(rackId)?.rackKey ?? rackId,
            video: true,
            imageHash: crypto.createHash('sha256')
              .update(fs.readFileSync(imagePath)).digest('hex'),
            timestamp: new Date().toISOString(),
          });
          await runPipelineAnalyze(imagePath, rackDir, authPayload?.organizationId || null, rackId);
          await ensurePortCounts(rackId);
        }
        safeUnlink(normalizedPath);
        await settleRack(rackId, { tenantId, userId });

        racks.push({
          rackId, position: r.position, label: r.label,
          deviceCount: r.device_count, score: r.score, cached,
        });
        scheduleCanonicalRefresh(rackId);
      } catch (err) {
        logger.warn({
          event: 'video_split.rack_failed',
          err: err.message, frameIndex: r.frame_index, position: r.position,
        }, `rack ${r.position} of the video failed: ${err.message}`);
      }
    }

    safeUnlink(videoPath);
    try { fs.rmSync(framesDir, { recursive: true, force: true }); } catch (_) { /* best effort */ }

    if (racks.length === 0) {
      return res.status(500).json({ error: 'None of the racks in the video could be read. Try again.' });
    }

    audit.log({
      req, action: 'scan.video', status: 'ok', targetType: 'rack',
      targetId: racks[0].rackId,
      payload: { count: racks.length, rackIds: racks.map(x => x.rackId), durationMs: Date.now() - reqStart },
    });
    recordEvent('video_split.scan_completed', {
      count: racks.length, tenantId,
    });
    logger.info({
      event: 'video_split.scan_completed',
      count: racks.length, durationMs: Date.now() - reqStart,
    }, `video scan: ${racks.length} racks`);

    res.json({
      ok: true, count: racks.length,
      durationMs: Date.now() - reqStart,
      racks,
    });
  } catch (err) {
    safeUnlink(videoPath);
    try { fs.rmSync(framesDir, { recursive: true, force: true }); } catch (_) { /* best effort */ }
    logger.error({
      event: 'video_split.scan_failed', err: err.message,
    }, 'video scan failed');
    res.status(500).json({ error: err.message });
  }
});

/**
 * GET /api/racks
 * The racks this caller may open, by the rule in lib/rack_access
 * visibleRackIds: the owner every rack, an org admin their organization's,
 * everyone else their own Site's. It used to take the caller's own Site for
 * everyone, so an org admin or the owner saw only the default Site's racks.
 */
app.get('/api/racks', auth.requireAuth, (req, res) => {
  try {
    const allowed = rackAccess.visibleRackIds(req.user, tenant, { userId: req.user.id });
    const racks = store.listScans(allowed === null ? {} : { scanIds: allowed })
      .map(({ scanId: name }) => {
        const meta = readMeta(name);
        return meta ? { rackId: name, timestamp: meta.timestamp } : { rackId: name };
      })
      .sort((a, b) => (b.timestamp || '').localeCompare(a.timestamp || ''));
    res.json({ racks });
  } catch (err) {
    res.json({ racks: [] });
  }
});

// ── Per-user scan history ────────────────────────────────────
// Returns the rich list of scans owned by the authenticated user, with
// device/unit counts and the timestamp of the latest port identification.
// Used by the Profile page's history list.
app.get('/api/scans', auth.requireAuth, (req, res) => {
  // History must show a repeat upload's new time at once: never cached.
  res.setHeader('Cache-Control', 'no-store');
  const { rackNameFor } = require('./lib/rack_name');
  const userId = req.user.id;
  const tenantId = req.user.tenant_id;
  // Visibility by role (lib/rack_access visibleRackIds):
  //   owner        -> every rack on the platform, all users' scans
  //   org_admin    -> every rack in their org's Sites, all users' scans
  //   site_manager -> every rack scanned at his Site, by any technician
  //   member       -> only racks he claimed himself (rack_user_claims and
  //                   rack_owners.created_by), so his own cache-hit scans of
  //                   already-scanned images still appear. The claim set is
  //                   user-scoped, so there is no gate on the file-based
  //                   meta.userId (which holds the original scanner).
  // `allowedRacks === null` means no rack-set restriction (owner).
  const allowedRacks = rackAccess.visibleRackIds(req.user, tenant, { userId, perUser: true });
  try {
    const scans = store.listScans(allowedRacks ? { scanIds: allowedRacks } : {})
      .map(({ scanId: rackId }) => {
        const meta = readMeta(rackId);
        if (!meta) return null;
        // Note: no meta.userId gate for members - allowedRacks is already the
        // set of racks THIS member claimed (tenantUserRackIds), which is the
        // correct per-user ownership signal for shared RK-ids.
        const rackDir  = engineDir(rackId);
        // One readdir instead of up to five existsSync calls. Every check
        // below is now a Set lookup: this handler is fully synchronous and
        // runs per rack, unbounded for the owner (whose allowedRacks is null,
        // so the filter above never short-circuits), and each syscall blocks
        // every other request on the server. Cheap here, but the shape is
        // linear and worst for the account most likely to hold every rack.
        let entries;
        try { entries = new Set(fs.readdirSync(rackDir)); }
        catch (_) { return null; }   // rack directory vanished between calls

        let deviceCount = 0, unitCount = 0;
        try {
          if (entries.has('device_unit_map.json')) {
            const data = JSON.parse(fs.readFileSync(path.join(rackDir, 'device_unit_map.json'), 'utf8'));
            deviceCount = Array.isArray(data.devices) ? data.devices.length : 0;
            unitCount   = Array.isArray(data.units_detected) ? data.units_detected.length : 0;
          }
        } catch (_) {}
        // Latest port identification timestamp (if any) for activity sorting
        let lastPortAt = null, portCount = 0;
        if (entries.has('port_identifications.jsonl')) {
          const lines = fs.readFileSync(path.join(rackDir, 'port_identifications.jsonl'), 'utf8')
            .split('\n').filter(Boolean);
          portCount = lines.length;
          if (lines.length) {
            try { lastPortAt = JSON.parse(lines[lines.length - 1]).timestamp || null; } catch {}
          }
        }
        // The photo (for a history thumbnail): the small copy, made on first
        // request by the files route.
        const hasPhoto = !!photoPathOf(rackId);
        const image = hasPhoto ? scanFileUrl(rackId, 'thumb.jpg') : null;
        return {
          rackId,
          // What a person calls this rack, where anybody has said: the name on
          // the scan, the rack a person confirmed it is, or the rack that
          // carries this scan's id (lib/rack_name.js). Without it every rack on
          // Home read "Unidentified rack" while the rack's own page named it
          // (the owner, 23 September 2026).
          rackName: rackNameFor(rackId, { tenantId }),
          timestamp: meta.timestamp || null,
          deviceCount,
          unitCount,
          portCount,
          lastPortAt,
          image,
          thumbUrl: image,
          photoUrl: hasPhoto ? scanFileUrl(rackId, 'photo.jpg') : null,
          qualityWarning: meta.qualityWarning || null,
          pair: meta.pair || null,
        };
      })
      .filter(Boolean)
      .sort((a, b) => (b.timestamp || '').localeCompare(a.timestamp || ''));
    res.json({ scans });
  } catch (err) {
    logger.error('/api/scans failed:', err.message);
    res.status(500).json({ error: 'Failed to load scans' });
  }
});

// Two racks read together on the Map screen: each scan remembers the other,
// so History shows the pair as one item that opens the two-rack page.
app.post('/api/pairs', auth.requireAuth, (req, res) => {
  const a = String(req.body?.aScanId || '').toUpperCase();
  const z = String(req.body?.zScanId || '').toUpperCase();
  if (!a || !z || a === z) return res.status(400).json({ error: 'Two different scans are needed.' });
  for (const id of [a, z]) {
    if (!rackAccess.isValidRackId(id) || !rackAccess.canAccessRack(req.user, id, tenant) || !store.has(id)) {
      return res.status(404).json({ error: 'One of the two scans was not found.' });
    }
  }
  const at = new Date().toISOString();
  for (const [id, other, side] of [[a, z, 'a'], [z, a, 'z']]) {
    const m = readMeta(id) || {};
    m.pair = { with: other, side, at };
    writeMeta(id, m);
  }
  res.json({ ok: true, aScanId: a, zScanId: z, at });
});

// Read a rack's stored photograph again with the models the server has now.
//
// An analysed rack is a cache hit for ever, which is right for speed and wrong
// the day a model improves: the patch panel model, the trained unit grid and the
// OCR only ever reached new photographs. This runs the same pipeline a fresh
// scan runs, then carries across everything filed against the old reading - a
// person's make and model correction, the OCR result, the feedback history -
// by matching each box to where it sits on the photo, because a better unit
// grid numbers the rack differently and the U a correction was filed under now
// names a different box. The whole folder is kept aside first; see
// lib/reanalyze.js. An admin step: it changes what everyone sees for the rack.
const reanalyze = require('./lib/reanalyze');
app.post('/api/scan/:rackId/reanalyze', auth.requireAuth, async (req, res) => {
  const { rackId } = req.params;
  if (!/^RK-[A-Za-z0-9]{4,32}$/.test(rackId)) {
    return res.status(400).json({ error: 'Invalid rack id' });
  }
  const _auth = softAuthPayload(req);
  if (!canAccessRack(_auth, rackId)) return res.status(404).json({ error: 'Rack not found' });
  if (!['owner', 'org_admin', 'site_manager'].includes(req.user?.role)) {
    return res.status(403).json({ error: 'Reading a rack again is for an admin. Ask yours to do it.' });
  }
  try {
    if (!store.has(rackId)) return res.status(404).json({ error: 'Rack not found' });
    const sp = store.paths(rackId);
    const out = await reanalyze.reanalyzeRack({
      rackId, rackDir: sp.engine, imagePath: sp.photo, metaPath: sp.meta,
      correctionsPath: sp.corrections, backupsDir: sp.backups,
      runPipeline: (image, dir) => runPipelineAnalyze(image, dir, _auth?.organizationId || null),
    });
    try { await writeAnnotated(rackId); try { fs.rmSync(sp.thumb, { force: true }); } catch (_) {} } catch (_) { /* best effort */ }
    scheduleCanonicalRefresh(rackId);
    logger.info({ event: 'scan.reanalyzed', rackId, before: out.before, after: out.after,
      renumbered: out.renumbered.length, backup: path.basename(out.backup) }, `re-read ${rackId}`);
    // The backup's path is the server's business, not the screen's.
    const { backup: _backup, ...shown } = out;
    return res.json({ ok: true, ...shown });
  } catch (err) {
    logger.warn({ event: 'scan.reanalyze_failed', rackId, error: err.message }, `re-read failed ${rackId}`);
    return res.status(err.status || 500).json({ error: err.message });
  }
});

app.post('/api/scan/:rackId/device-override', (req, res) => {
  const { rackId } = req.params;
  if (!/^RK-[A-Za-z0-9]{4,32}$/.test(rackId)) {
    return res.status(400).json({ error: 'Invalid scanId' });
  }
  const _auth = softAuthPayload(req);
  if (!canAccessRack(_auth, rackId)) {
    return res.status(404).json({ error: 'Rack not found' });
  }
  const { position, make, model } = req.body || {};
  if (!position || typeof position !== 'string') {
    return res.status(400).json({ error: 'position is required' });
  }
  const rackDir = engineDir(rackId);
  if (!fs.existsSync(rackDir)) {
    return res.status(404).json({ error: `Rack ${rackId} not found` });
  }
  try {
    const overridesPath = deviceOverridesPath(rackDir);
    let overrides = {};
    if (fs.existsSync(overridesPath)) {
      try { overrides = JSON.parse(fs.readFileSync(overridesPath, 'utf8')); } catch { overrides = {}; }
    }
    const entry = { ...(overrides[position] || {}) };
    if (make     !== undefined) entry.make     = make     || null;
    if (model    !== undefined) entry.model    = model    || null;
    entry.updated_at = new Date().toISOString();
    // Drop the row once every field is cleared, so the file doesn't
    // accumulate empty placeholders for corrections the user undid.
    if (!entry.make && !entry.model) {
      delete overrides[position];
    } else {
      overrides[position] = entry;
    }
    fs.writeFileSync(overridesPath, JSON.stringify(overrides, null, 2), 'utf8');
    res.json({ ok: true, position, override: overrides[position] || null });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// GET /api/scan/:rackId
// Returns the same shape as POST /api/analyze (cached) - devices array with
// per-port arrays, units_detected, originalExt, etc. The Results page calls
// this on mount so port counts stay in sync with the underlying
// device_unit_map.json after re-detection runs.
app.get('/api/scan/:rackId', auth.requireAuth, async (req, res) => {
  const { rackId } = req.params;
  res.setHeader('Cache-Control', 'no-store');
  const rackDir = engineDir(rackId);
  const jsonPath = path.join(rackDir, 'device_unit_map.json');
  if (!fs.existsSync(jsonPath)) {
    return res.status(404).json({ error: `Rack ${rackId} not found` });
  }
  try {
    // Reopening a scan whose port data went missing must rebuild it before
    // answering, or the page opens with no devices and no pickable ports.
    await healPortDataIfMissing(rackId);
    res.json(buildResponse(rackId, true));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/scan/:rackId/result', auth.requireAuth, async (req, res) => {
  const { rackId } = req.params;
  res.setHeader('Cache-Control', 'no-store');
  const rackDir = engineDir(rackId);
  if (!fs.existsSync(rackDir)) {
    return res.status(404).json({ error: `Rack ${rackId} not found` });
  }
  try {
    // Same as above - and it must run BEFORE the stored result.json is read,
    // because that copy is what carries the portless devices onward. A
    // successful heal rewrites result.json itself, so the read below picks up
    // the repaired payload.
    await healPortDataIfMissing(rackId);
    // The response is the `api` view of the scan's one result.json.
    const doc = store.readResult(rackId);
    if (!doc || !doc.api) {
      const result = writeCanonicalScanResult(rackId);
      // Internal filename kept out of the response - it goes in the log instead.
      if (!result) return res.status(500).json({ error: 'Could not assemble the scan result.' });
      return res.json(result);
    }
    // A file written before the rule existed says "Switch" where the report says
    // "Router". Reclassify on the way out rather than trusting the copy on disk.
    const cached = doc.api;
    if (Array.isArray(cached.devices)) cached.devices = cached.devices.map(applyRouterRule);
    if (cached.selectedPort?.selected_device) cached.selectedPort.selected_device = applyRouterRule(cached.selectedPort.selected_device);
    return res.json(cached);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

const OCR_DEVICES_TIMEOUT_MS = 5 * 60_000;
app.post('/api/scan/:rackId/ocr-devices', (req, res) => {
  const { rackId } = req.params;
  const rackDir = engineDir(rackId);
  if (!fs.existsSync(rackDir)) {
    return res.status(404).json({ ok: false, error: `rack ${rackId} not found` });
  }
  const child = spawnChild(pythonCmd,
    ['-u', '-m', 'pipeline.ocr_devices', rackId, '--dir', rackDir, '--json'],
    { cwd: PROJECT_ROOT,
      env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' } });
  let stdout = '', stderr = '', settled = false;
  const send = (status, body) => {
    if (settled) return;
    settled = true;
    res.status(status).json(body);
  };
  const killer = setTimeout(() => {
    try { child.kill('SIGKILL'); } catch (_) {}
    send(504, { ok: false, error: 'OCR timed out', rackId });
  }, OCR_DEVICES_TIMEOUT_MS);
  child.stdout.on('data', c => { stdout += c.toString(); });
  child.stderr.on('data', c => { stderr += c.toString(); });
  child.on('error', err => {
    clearTimeout(killer);
    send(500, { ok: false, error: `spawn failed: ${err.message}` });
  });
  child.on('close', () => {
    clearTimeout(killer);
    const lastLine = stdout.trim().split('\n').filter(Boolean).pop() || '';
    let parsed = null;
    try { parsed = JSON.parse(lastLine); } catch (_) {}
    if (!parsed) {
      return send(500, { ok: false,
        error: stderr.slice(-400) || 'no JSON on stdout',
        rackId });
    }
    audit.log({ req, action: 'scan.ocr_devices',
                status: parsed.ok ? 'ok' : 'fail',
                targetType: 'rack', targetId: rackId,
                payload: { devices: (parsed.devices || []).length,
                           full: (parsed.devices || []).filter(d => d.source === 'ocr_full').length,
                           partial: (parsed.devices || []).filter(d => d.source === 'ocr_make_only').length } });
    send(parsed.ok ? 200 : 500, parsed);
  });
});

// GET /api/scan/:rackId/ocr-devices
// Returns the cached ocr_devices.json if it exists (written by the
// background OCR pass or the POST endpoint above), so a caller can tell
// whether OCR has run for this rack.
app.get('/api/scan/:rackId/ocr-devices', (req, res) => {
  const { rackId } = req.params;
  res.setHeader('Cache-Control', 'no-store');
  const p = path.join(engineDir(rackId), 'ocr_devices.json');
  if (!fs.existsSync(p)) {
    return res.status(404).json({ ok: false, error: 'OCR not yet run for this rack', rackId });
  }
  try {
    res.setHeader('Content-Type', 'application/json');
    // The OCR record copied each device's class from the map at OCR time; a
    // small switch it still calls "Switch" is a Router, so apply the same rule
    // the scan result uses.
    try {
      const ocr = JSON.parse(fs.readFileSync(p, 'utf8'));
      if (Array.isArray(ocr.devices)) ocr.devices = ocr.devices.map(applyRouterRule);
      else if (Array.isArray(ocr)) return res.json(ocr.map(applyRouterRule));
      return res.json(ocr);
    } catch {
      res.send(fs.readFileSync(p, 'utf8'));
    }
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message, rackId });
  }
});

// POST /api/scan/:rackId/side-labels
// Runs pipeline.side_labels - extracts identifier-shaped text from the
// LEFT and RIGHT margins of the rack photo (the green "SWHOME / SWFIBRA1"
// chips techs apply to rack rails). This is independent of the CV
// detector: even when YOLO misses a device with an unusual fascia, the
// side label is still readable. The client uses the result to surface
// recall gaps ("5 labels found, 3 switches identified - confirm the
// missing 2") instead of silently under-counting.
//
// Same spawn pattern as ocr-devices (synchronous, generous timeout -
// EasyOCR on CPU). Cheaper than the full-image pass because we only OCR
// ~24% of the pixels (12% on each margin).
const SIDE_LABELS_TIMEOUT_MS = 3 * 60_000;
// GET /api/scan/:rackId/physical-layer
// The physical layer report for one rack: rack label or id, device labels,
// ports, cables and OCR metadata, built by pipeline.physical_layer from what
// the scan produced (device_unit_map, labels-front, side_labels, ocr_devices).
// Cached as physical_layer.json in the engine folder; pass ?refresh=1 to rebuild.
const PHYSICAL_LAYER_TIMEOUT_MS = 60_000;
// The report as { status, body }: the cached file, or a fresh build. One
// function for both callers, this route and the rack identity route below, and
// one build per rack at a time: a second caller waits for the python already
// running instead of starting its own.
const _physicalLayerBuilds = new Map();

// The labels off the rack's own rails and margins, read once per scan.
//
// The reader used to run only when something called POST .../side-labels, and
// nothing in the application ever did: the tape across the office rack was read
// on the two scans it was run for by hand, and on no photograph taken after.
// So the ladder's label rungs had nothing to stand on and every new scan came
// back "unknown". It is now part of building the physical layer - which is what
// the rack ladder reads - and is started as soon as a scan has been analysed.
// One run per rack at a time; a rack that already has its reading is left alone.
const _sideLabelRuns = new Map();
const SIDE_LABELS_WAIT_MS = 75_000;
function ensureSideLabels(rackId) {
  const rackDir = engineDir(rackId);
  if (!fs.existsSync(rackDir)) return Promise.resolve(false);
  // A reading made before the rails' unit numbers were read (no unit_numbers
  // in it) is read again once, so the rack's height can come from the rails.
  const sidePath = path.join(rackDir, 'side_labels.json');
  if (fs.existsSync(sidePath) && Array.isArray(store.readJson(sidePath, {})?.unit_numbers)) return Promise.resolve(false);
  if (_sideLabelRuns.has(rackId)) return _sideLabelRuns.get(rackId);
  // Asked of the engine worker, never started as a Python of its own. The
  // worker already holds the OCR models; a second process loading its own copy
  // took 3.6 GB on the 8 GB demo server, the kernel killed it and the
  // application went down with it (21 Sep 2026). The worker takes one request
  // at a time, so two racks can never be read at once either.
  const run = (async () => {
    let read = false;
    try {
      const res = await pool.request('side_labels', { output_dir: rackDir, rack_id: rackId });
      read = Boolean(res && res.ok) && fs.existsSync(path.join(rackDir, 'side_labels.json'));
    } catch (err) {
      logger.warn(`[side-labels] not read for ${rackId}: ${err.message}`);
    }
    // The rack ladder reads the physical layer from its cached file, so the
    // file is made again here, with the labels in it, the moment they exist.
    if (read) {
      try { await buildPhysicalLayer(rackId, { refresh: true }); } catch (_) { /* best effort */ }
    }
    return read;
  })().finally(() => { _sideLabelRuns.delete(rackId); });
  _sideLabelRuns.set(rackId, run);
  return run;
}

const _physicalLayerCalls = new Map();
function physicalLayerReport(rackId, opts = {}) {
  if (!/^RK-[A-Z0-9]+$/i.test(String(rackId))) return buildPhysicalLayer(rackId, opts);
  // Two callers at once share one answer, as they always did: the second is
  // handed the first one's promise rather than starting a wait of its own.
  const key = `${rackId}|${opts.refresh ? 1 : 0}`;
  if (_physicalLayerCalls.has(key)) return _physicalLayerCalls.get(key);
  const run = (async () => {
    // Wait for the labels, but not for ever: a slow reader must not hold a
    // results screen. If it has not finished, the layer is built from what
    // there is, and built again - with the labels - when they arrive.
    const fresh = await Promise.race([
      ensureSideLabels(rackId),
      new Promise((resolve) => setTimeout(() => resolve(false), SIDE_LABELS_WAIT_MS)),
    ]);
    return buildPhysicalLayer(rackId, { ...opts, refresh: Boolean(opts.refresh) || fresh === true });
  })().finally(() => { _physicalLayerCalls.delete(key); });
  _physicalLayerCalls.set(key, run);
  return run;
}

function buildPhysicalLayer(rackId, { refresh = false } = {}) {
  if (!/^RK-[A-Z0-9]+$/i.test(String(rackId))) {
    return Promise.resolve({ status: 400, body: { ok: false, error: 'bad rack id' } });
  }
  const rackDir = engineDir(rackId);
  const outPath = path.join(rackDir, 'physical_layer.json');
  if (!fs.existsSync(rackDir)) {
    return Promise.resolve({ status: 404, body: { ok: false, error: `rack ${rackId} not found` } });
  }
  const read = () => {
    try {
      return { status: 200, body: JSON.parse(fs.readFileSync(outPath, 'utf8')) };
    } catch (e) {
      return { status: 500, body: { ok: false, error: `physical layer unreadable: ${e.message}` } };
    }
  };
  // A layer built before the labels were read is stale the moment they arrive:
  // the reader can finish after the wait above gave up, and without this the
  // copy made without them would be served for good.
  const stale = () => {
    try {
      const labels = path.join(rackDir, 'side_labels.json');
      return fs.existsSync(labels) && fs.statSync(labels).mtimeMs > fs.statSync(outPath).mtimeMs;
    } catch { return false; }
  };
  if (!refresh && fs.existsSync(outPath) && !stale()) return Promise.resolve(read());
  if (_physicalLayerBuilds.has(rackId)) return _physicalLayerBuilds.get(rackId);
  const build = new Promise((resolve) => {
    const child = spawnChild(pythonCmd,
      ['-u', '-m', 'pipeline.physical_layer', rackId, '--dir', rackDir],
      { cwd: PROJECT_ROOT,
        env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' } });
    let stderr = '', settled = false;
    const send = (status, body) => { if (settled) return; settled = true; resolve({ status, body }); };
    const killer = setTimeout(() => {
      try { child.kill('SIGKILL'); } catch (_) {}
      send(504, { ok: false, error: 'physical layer timed out', rackId });
    }, PHYSICAL_LAYER_TIMEOUT_MS);
    child.stderr.on('data', c => { stderr += c.toString(); });
    child.on('error', err => { clearTimeout(killer); send(500, { ok: false, error: `spawn failed: ${err.message}` }); });
    child.on('close', (code) => {
      clearTimeout(killer);
      if (settled) return;
      if (code !== 0 || !fs.existsSync(outPath)) {
        return send(500, { ok: false, error: stderr.slice(-400) || `exit ${code}`, rackId });
      }
      const r = read();
      send(r.status, r.body);
    });
  }).finally(() => { _physicalLayerBuilds.delete(rackId); });
  _physicalLayerBuilds.set(rackId, build);
  return build;
}
app.get('/api/scan/:rackId/physical-layer', async (req, res) => {
  const r = await physicalLayerReport(req.params.rackId, { refresh: !!req.query.refresh });
  res.status(r.status).json(r.body);
});

// Rack identity: which of the customer's racks a scan is, with the evidence.
//   GET  /api/scan/:rackId/identity
//   POST /api/scan/:rackId/identity/confirm
// The router declares the full paths and puts requireAuth on each route itself.
// It is not mounted on the /api/scan prefix behind requireAuth, because that
// prefix also serves soft-auth routes (physical-layer, ocr-devices,
// side-labels, device-override) for scans made with no session. It is handed
// the audit log and the one physical layer builder above, so python is never
// started twice for one rack.
try {
  app.use(require('./routes/rack_identity')({
    requireAuth: auth.requireAuth,
    audit,
    physicalLayer: physicalLayerReport,
  }));
  logger.info({ event: 'router.loaded', router: 'rack_identity', prefix: '/api/scan/:rackId/identity' },
    'rack identity router loaded');
} catch (err) {
  logger.warn({ event: 'router.load_failed', router: 'rack_identity', err: err.message },
    'rack identity router not loaded');
}

// ── After every analysis, before the result opens ──────────────────────
// Name the rack (lib/rack_naming.js: the rail label, then the device labels
// against the Excel dataset, then the layout against it, and only then ask a
// person) and decide how tall it is (lib/rack_height.js: the rail numbers, then
// the dataset, then the shelves counted, snapped to a standard height within
// one unit). Both are written into the scan's meta, which buildResponse reads,
// so the answer that opens the result already carries them.
//
// The labels are read first, but not for ever: past SETTLE_LABELS_WAIT_MS the
// ladder runs on what there is. Never fatal: a scan that cannot be named opens
// and asks, as it always did.
const SETTLE_LABELS_WAIT_MS = 150_000;
const _settling = new Map();
// meta.settle: { state: 'running' | 'done', startedAt, at, ms } - how far the
// work after the first answer has got. Bookkeeping only.
function markSettle(rackId, patch) {
  try { const m = readMeta(rackId) || {}; m.settle = { ...(m.settle || {}), ...patch }; writeMeta(rackId, m); } catch (_) { /* bookkeeping */ }
}
function settleRack(rackId, { tenantId = null, userId = null } = {}) {
  if (!rackId || !store.has(rackId)) return Promise.resolve({ rackNaming: null, rackHeight: null });
  if (_settling.has(rackId)) return _settling.get(rackId);
  const run = (async () => {
    const t0 = Date.now();
    markSettle(rackId, { state: 'running', startedAt: new Date(t0).toISOString(), at: null, ms: null });
    await Promise.race([
      Promise.all([ensureSideLabels(rackId), scheduleOcrLabels(rackId)]).catch(() => null),
      new Promise((resolve) => setTimeout(resolve, SETTLE_LABELS_WAIT_MS)),
    ]);
    // The height is decided twice: first from the rails and the photo alone,
    // so the naming ladder compares the layout in the rails' numbering; then
    // again once the rack has a name, so the Excel dataset can have its say.
    const decide = (name) => {
      try {
        const engine = engineDir(rackId);
        const side = store.readJson(path.join(engine, 'side_labels.json'), null) || {};
        const map = store.readJson(path.join(engine, 'device_unit_map.json'), null) || {};
        const unitNo = (u) => { const m = String(u ?? '').match(/\d+/); return m ? Number(m[0]) : null; };
        const deviceUnits = (map.devices || []).flatMap((d) => (d.units || []).map(unitNo)).filter((n) => n != null && n <= rackHeight.MAX_RACK_UNITS);
        // The devices of the rack (not the gaps and frame rows the engine
        // adds), with where their middle is on the photo, to put them on the rails.
        const imageH = Number(side.image_size?.h) || null;
        const placed = (map.devices || [])
          .filter((d) => !['Empty', 'Closed Unit', 'Unidentified'].includes(d.class_name) && Array.isArray(d.box))
          .map((d) => ({ units: (d.units || []).map(unitNo).filter((n) => n != null), yPct: imageH ? ((d.box[1] + d.box[3]) / 2 / imageH) * 100 : null }))
          .filter((d) => d.units.length && d.yPct != null);
        const info = store.scanInfo(rackId);
        let cables = null;
        try {
          const doc = info ? require('./lib/matrix').active(info.orgId, info.siteId ?? tenantId) : null;
          // The cables, and where the workbook's inventories put every device
          // and how tall it says each rack is (RACK_INVENTORY Rack_Height_RU).
          cables = doc ? (doc.cables || []).concat(require('./lib/sheet_model').placementRows(doc)) : null;
        } catch { cables = null; }
        const h = rackHeight.decideHeight({
          railNumbers: side.unit_numbers || [],
          datasetMaxUnit: rackHeight.datasetMaxUnitOf(cables, name),
          detectedCount: (map.units_detected || []).length,
          maxDeviceUnit: deviceUnits.length ? Math.max(...deviceUnits) : null,
          devices: placed.length ? placed : null,
        });
        h.at = new Date().toISOString();
        const m = readMeta(rackId) || {};
        m.rackHeight = h;
        writeMeta(rackId, m);
        return h;
      } catch (err) {
        logger.warn({ event: 'rack_height.failed', rackId, err: err.message }, 'rack height not decided');
        return null;
      }
    };
    decide(null);
    let naming = null;
    try {
      naming = await rackNaming.nameAfterScan(rackId, {
        tenantId, userId,
        physicalLayer: async (id) => {
          const r = await buildPhysicalLayer(id, { refresh: true });
          return r && r.status === 200 ? r.body : null;
        },
      });
    } catch (err) {
      logger.warn({ event: 'rack_naming.failed', rackId, err: err.message }, 'rack naming ladder failed');
    }
    const height = decide(naming && naming.name ? naming.name : null);
    try { writeCanonicalScanResult(rackId); } catch { /* the refresh after the response makes it too */ }
    logger.info({ event: 'rack.settled', rackId, method: naming?.method || null, name: naming?.name || null,
      ask: naming ? naming.ask : null, unitsTotal: height?.unitsTotal ?? null, heightSource: height?.source ?? null,
      ms: Date.now() - t0 }, `rack ${rackId} settled`);
    return { rackNaming: naming, rackHeight: height };
  })().finally(() => {
    _settling.delete(rackId);
    markSettle(rackId, { state: 'done', at: new Date().toISOString() });
  });
  _settling.set(rackId, run);
  return run;
}

// The same, after the answer has gone: never awaited, never fatal.
function settleInBackground(rackId, opts) {
  try {
    settleRack(rackId, opts).catch((err) => {
      logger.warn({ event: 'rack.settle_failed', rackId, err: err.message }, 'settling the rack failed');
    });
  } catch (err) {
    logger.warn({ event: 'rack.settle_failed', rackId, err: err.message }, 'settling the rack failed');
  }
}

// GET /api/scan/:rackId/settle - the light status the result polls after the
// first answer: is the rack's naming, label reading and height still running,
// and what they found. state "running" | "done".
app.get('/api/scan/:rackId/settle', auth.requireAuth, (req, res) => {
  const { rackId } = req.params;
  res.setHeader('Cache-Control', 'no-store');
  if (!store.has(rackId)) return res.status(404).json({ error: 'Scan not found.' });
  const meta = readMeta(rackId) || {};
  const engine = engineDir(rackId);
  const running = _settling.has(rackId);
  res.json({
    rackId,
    state: running ? 'running' : 'done',
    labelsRead: fs.existsSync(path.join(engine, 'labels-front.json')),
    railRead: fs.existsSync(path.join(engine, 'side_labels.json')),
    makeModelRead: fs.existsSync(path.join(engine, 'ocr_devices.json')),
    rackNaming: meta.rackNaming ? { ...meta.rackNaming, words: rackNaming.METHOD_WORD[meta.rackNaming.method] || null } : null,
    rackHeight: meta.rackHeight || null,
    startedAt: meta.settle?.startedAt || null,
    at: meta.settle?.at || null,
  });
});

app.post('/api/scan/:rackId/side-labels', (req, res) => {
  const { rackId } = req.params;
  const rackDir = engineDir(rackId);
  if (!fs.existsSync(rackDir)) {
    return res.status(404).json({ ok: false, error: `rack ${rackId} not found` });
  }
  const child = spawnChild(pythonCmd,
    ['-u', '-m', 'pipeline.side_labels', rackId, '--dir', rackDir, '--json'],
    { cwd: PROJECT_ROOT,
      env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' } });
  let stdout = '', stderr = '', settled = false;
  const send = (status, body) => {
    if (settled) return;
    settled = true;
    res.status(status).json(body);
  };
  const killer = setTimeout(() => {
    try { child.kill('SIGKILL'); } catch (_) {}
    send(504, { ok: false, error: 'side-label OCR timed out', rackId });
  }, SIDE_LABELS_TIMEOUT_MS);
  child.stdout.on('data', c => { stdout += c.toString(); });
  child.stderr.on('data', c => { stderr += c.toString(); });
  child.on('error', err => {
    clearTimeout(killer);
    send(500, { ok: false, error: `spawn failed: ${err.message}` });
  });
  child.on('close', () => {
    clearTimeout(killer);
    const lastLine = stdout.trim().split('\n').filter(Boolean).pop() || '';
    let parsed = null;
    try { parsed = JSON.parse(lastLine); } catch (_) {}
    if (!parsed) {
      return send(500, { ok: false,
        error: stderr.slice(-400) || 'no JSON on stdout',
        rackId });
    }
    // Cache for cheap re-fetch from the GET endpoint.
    if (parsed.ok) {
      try {
        fs.writeFileSync(
          path.join(rackDir, 'side_labels.json'),
          JSON.stringify(parsed, null, 2),
        );
      } catch (e) {
        logger.warn(`[side_labels] cache write failed for ${rackId}: ${e.message}`);
      }
    }
    send(parsed.ok ? 200 : 500, parsed);
  });
});

// GET /api/scan/:rackId/side-labels
// Returns the cached side_labels.json if present, otherwise 404. The
// client falls back to triggering a POST when this 404s.
app.get('/api/scan/:rackId/side-labels', (req, res) => {
  const { rackId } = req.params;
  const p = path.join(engineDir(rackId), 'side_labels.json');
  if (!fs.existsSync(p)) {
    return res.status(404).json({ ok: false, error: 'side labels not yet extracted', rackId });
  }
  try {
    res.setHeader('Content-Type', 'application/json');
    res.send(fs.readFileSync(p, 'utf8'));
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message, rackId });
  }
});

// Exported for in-process use (tests, scripts):
//   const { app, writeCanonicalScanResult } = require('./app');
module.exports = module.exports || {};
module.exports.buildScanReportData      = buildScanReportData;
module.exports.writeCanonicalScanResult = writeCanonicalScanResult;
module.exports.app                      = app;
// Test-only: the tests exercise the REAL functions rather than a copy (a copy
// proves nothing about the shipped code).
module.exports._internals = {
  store,
  engineDir,
  runDataRetention,
  defaultWorkerCount,
  // The one builder of the physical layer report, shared by its own route and
  // the rack identity route: the test checks the real one shares a build.
  physicalLayerReport,
  // After an analysis: name the rack and decide its height (the tests run it
  // on a scan folder they made, with the labels already read).
  settleRack,
  // Replace the zone close-up reader (routes/physical.js) with a stub.
  setZoneReader,
};

// ── User feedback on port identification ──────────────────────
// Every feedback row is a correction of its scan: appended to that scan's
// corrections.jsonl (lib/store.js, append only), and the crops cut for it go
// in the scan's crops/ folder. Nothing is kept in a server-wide log any more,
// so deleting a scan deletes its feedback with it.
function cropsDirOf(scanId) {
  const d = store.file(scanId, 'crops');
  fs.mkdirSync(d, { recursive: true });
  return d;
}

// Append one feedback row as a correction: {at, user, kind, target, before,
// after} for the data layout, with the row's own fields kept alongside so the
// overlay and the scoreboard read exactly what they always read.
function recordFeedback(req, scanId, entry, { target = null, before = null, after = null } = {}) {
  store.appendCorrection(scanId, {
    ...entry,
    at: entry.timestamp,
    user: req.user?.username ?? null,
    kind: entry.feedback_type,
    target, before, after,
  });
  _fbAll.at = 0;
}

// Every feedback row on every scan, re-read at most every 15 seconds. The
// owner dashboard polls every 5 seconds for two integers, and a full walk of
// the scans on each poll would block every other request while it ran.
const _fbAll = { at: 0, rows: [] };
function allFeedbackRows() {
  if (Date.now() - _fbAll.at < 15000) return _fbAll.rows;
  const rows = [];
  for (const sc of store.listScans()) {
    for (const r of store.readCorrections(sc.scanId)) {
      if (typeof r.is_correct === 'boolean') rows.push(r);
    }
  }
  _fbAll.at = Date.now();
  _fbAll.rows = rows;
  return rows;
}

function feedbackTally() {
  let right = 0, wrong = 0;
  try {
    for (const e of allFeedbackRows()) e.is_correct ? right++ : wrong++;
  } catch (_) {
    // Unreadable - report zeroes rather than failing the whole dashboard for
    // one statistic.
  }
  return { right, wrong };
}

// ── Feedback override layer ──────────────────────────────────
// Reads the scan's corrections.jsonl and overlays the user's `actual_*`
// corrections on top of the model's predictions in the scan result. Keeps a
// `_correction` audit trail so the original prediction is never lost.
//
// Applied during writeCanonicalScanResult, so the corrected values land in
// result.json - every consumer sees the same overlaid view. Re-runs after
// every feedback POST because scheduleCanonicalRefresh fires after the
// audit.log success.
function _readFeedbackForScan(scanId) {
  try {
    return store.readCorrections(scanId).filter((r) => r.scanId === scanId && r.is_correct === false);
  } catch (err) {
    logger.warn(`[feedback_overlay] read failed for ${scanId}: ${err.message}`);
    return [];
  }
}

// A device's port-numbering offset from the user's LATEST renumber correction
// ONLY. Each correction SETS the offset (predicted_port is normalized to RAW
// model space at record time), so repeated corrections can't compound into a
// runaway shift the way summing every delta chronologically did. Returns the
// signed offset to add to a raw model port to get the user's number.
function _latestPortShift(rows, deviceIndex) {
  const pr = (rows || [])
    .filter(r => r.feedback_type === 'port'
      && Number(r.device_index) === Number(deviceIndex)
      && r.actual_port != null && r.predicted_port != null)
    .sort((a, b) => (a.timestamp || '').localeCompare(b.timestamp || ''));
  if (!pr.length) return 0;
  const last = pr[pr.length - 1];
  return Number(last.actual_port) - Number(last.predicted_port);
}

// device_index -> { shift, ts } from the latest correction (for bulk report
// re-indexing). Iterating in timestamp order and overwriting means the last
// (most recent) correction wins - no summing.
function _latestPortShifts(rows) {
  const byDev = new Map();
  const pr = (rows || [])
    .filter(r => r.feedback_type === 'port' && r.actual_port != null && r.predicted_port != null)
    .sort((a, b) => (a.timestamp || '').localeCompare(b.timestamp || ''));
  for (const r of pr) {
    byDev.set(Number(r.device_index), {
      shift: Number(r.actual_port) - Number(r.predicted_port),
      ts: r.timestamp,
    });
  }
  return byDev;
}

// Re-derive cable_type when the color changes. Heuristic: replace any
// known color word in the existing cable_type string. If we can't
// detect a color word, leave cable_type unchanged.
const _CABLE_COLOR_WORD = /\b(?:White|Black|Blue|Red|Green|Yellow|Grey|Gray|Brown|Orange|Purple|Pink)\b/i;
function _swapCableTypeColor(cableType, newColor) {
  if (!cableType || !newColor) return cableType;
  if (!_CABLE_COLOR_WORD.test(cableType)) return cableType;
  return cableType.replace(_CABLE_COLOR_WORD, newColor);
}

function applyFeedbackOverrides(scanId, payload) {
  if (!payload) return payload;
  const rows = _readFeedbackForScan(scanId);
  if (!rows.length) return payload;

  // Latest correction wins per (key). Keys differ by feedback_type:
  //  - port:       device_index + port_location signature
  //  - device:     device_index
  //  - port_count: device_index
  const latest = new Map();
  for (const r of rows) {
    const ft = r.feedback_type;
    let key = null;
    if (ft === 'port') key = `port:${r.device_index}:${(r.port_location || []).join(',')}`;
    else if (ft === 'device') key = `device:${r.device_index}`;
    else if (ft === 'port_count') key = `count:${r.device_index}`;
    if (!key) continue;
    const prev = latest.get(key);
    if (!prev || (r.timestamp || '') > (prev.timestamp || '')) latest.set(key, r);
  }

  // 1) selectedPort.port_info - the "Port Located" card the UI renders.
  const sp = payload.selectedPort;
  if (sp && sp.device_index != null && sp.port_info) {
    const pi = sp.port_info;
    const k = `port:${sp.device_index}:${(pi.location || []).join(',')}`;
    const fb = latest.get(k);
    if (fb) {
      const fields = [];
      const original = {};
      if (fb.actual_port != null && fb.actual_port !== pi.port_number) {
        original.port_number = pi.port_number;
        pi.port_number = fb.actual_port;
        fields.push('port_number');
      }
      if (fb.actual_cable_color && fb.actual_cable_color !== pi.cable_color) {
        original.cable_color = pi.cable_color;
        pi.cable_color = fb.actual_cable_color;
        fields.push('cable_color');
        const newType = _swapCableTypeColor(pi.cable_type, fb.actual_cable_color);
        if (newType && newType !== pi.cable_type) {
          original.cable_type = pi.cable_type;
          pi.cable_type = newType;
          fields.push('cable_type');
        }
      }
      if (fields.length) {
        pi._correction = { applied_at: fb.timestamp, source: 'user_feedback', fields, original };
      }
    }

    // selected_device.class_name from device-class feedback
    if (sp.selected_device) {
      const dfb = latest.get(`device:${sp.device_index}`);
      if (dfb && dfb.actual_device_class && dfb.actual_device_class !== sp.selected_device.class_name) {
        const original = { class_name: sp.selected_device.class_name };
        sp.selected_device.class_name = dfb.actual_device_class;
        sp.selected_device._correction = {
          applied_at: dfb.timestamp, source: 'user_feedback',
          fields: ['class_name'], original,
        };
      }
    }
  }

  // 2) devices[] - class_name (device feedback) + port_count (port-count feedback)
  for (const dev of payload.devices || []) {
    if (!dev || dev.index == null) continue;

    const dfb = latest.get(`device:${dev.index}`);
    if (dfb && dfb.actual_device_class && dfb.actual_device_class !== dev.class_name) {
      dev._correction = dev._correction || { source: 'user_feedback', fields: [], original: {} };
      dev._correction.original.class_name = dev.class_name;
      dev._correction.fields.push('class_name');
      dev._correction.applied_at = dfb.timestamp;
      dev.class_name = dfb.actual_device_class;
    }

    const cfb = latest.get(`count:${dev.index}`);
    if (cfb && cfb.actual_port_count != null && cfb.actual_port_count !== dev.port_count) {
      dev._correction = dev._correction || { source: 'user_feedback', fields: [], original: {} };
      dev._correction.original.port_count = dev.port_count;
      dev._correction.fields.push('port_count');
      dev._correction.applied_at = cfb.timestamp;
      dev.port_count = cfb.actual_port_count;
    }
  }

  // 3) Per-device port re-indexing from port-feedback corrections.
  //
  // When a user corrects a port number (e.g. "this is port 8, model said 2"),
  // they're anchoring one physical port at an absolute number. The same
  // shift applies to every other port the detector found on that device:
  // it just started counting at the wrong place.
  //
  //   shift = actual_port - predicted_port
  //
  // Positive shift  → model missed `shift` ports at the start of the row.
  //                   Every detection bumps up by `shift`. Device's
  //                   port_count grows by `shift` to make room.
  // Negative shift  → model emitted spurious detections before the actual
  //                   start of the port row. Drop the leading |shift|
  //                   detections; lower port_count by |shift|.
  //
  // The device's numbering offset comes from the user's LATEST renumber
  // correction only (predicted_port is stored raw, so each correction is an
  // absolute offset). This replaces the old chronological-sum, which let
  // repeated corrections compound into a runaway shift.
  const sortedPortRows = rows
    .filter(r => r.feedback_type === 'port' && r.actual_port != null && r.predicted_port != null)
    .sort((a, b) => (a.timestamp || '').localeCompare(b.timestamp || ''));
  const deviceShifts = _latestPortShifts(rows); // device_index -> { shift, ts }

  const idents = payload.port_identifications || [];
  const newIdents = [];
  for (const ident of idents) {
    if (!ident || ident.device_index == null) { newIdents.push(ident); continue; }
    const ds = deviceShifts.get(Number(ident.device_index));
    if (!ds || !ds.shift) { newIdents.push(ident); continue; }
    const newPort = ident.port + ds.shift;
    if (newPort <= 0) continue; // before actual start of port row - drop
    const fields = ['port'];
    const original = { port: ident.port };
    const shifted = { ...ident, port: newPort };

    // If the latest port-feedback row for this device matches THIS detection
    // (same original port number) and supplies a cable color, overlay it too.
    const matchingFb = sortedPortRows.findLast
      ? sortedPortRows.findLast(r => r.device_index === ident.device_index && r.predicted_port === ident.port)
      : [...sortedPortRows].reverse().find(r => r.device_index === ident.device_index && r.predicted_port === ident.port);
    if (matchingFb && matchingFb.actual_cable_color && matchingFb.actual_cable_color !== shifted.cable_color) {
      original.cable_color = shifted.cable_color;
      shifted.cable_color = matchingFb.actual_cable_color;
      fields.push('cable_color');
      const newType = _swapCableTypeColor(shifted.cable_type, matchingFb.actual_cable_color);
      if (newType && newType !== shifted.cable_type) {
        original.cable_type = shifted.cable_type;
        shifted.cable_type = newType;
        fields.push('cable_type');
      }
    }
    shifted._correction = {
      applied_at: ds.ts, source: 'user_feedback',
      fields, original, port_shift: ds.shift,
    };
    newIdents.push(shifted);
  }
  payload.port_identifications = newIdents;

  // Reflect the shift on each device's port_count so the picker / "port
  // 1-N" range reflects the corrected layout.
  for (const dev of payload.devices || []) {
    if (!dev || dev.index == null) continue;
    const ds = deviceShifts.get(Number(dev.index));
    if (!ds || !ds.shift) continue;
    if (typeof dev.port_count !== 'number') continue;
    const newCount = Math.max(0, dev.port_count + ds.shift);
    if (newCount === dev.port_count) continue;
    dev._correction = dev._correction || { source: 'user_feedback', fields: [], original: {} };
    if (!('port_count' in (dev._correction.original || {}))) {
      dev._correction.original = dev._correction.original || {};
      dev._correction.original.port_count = dev.port_count;
    }
    if (!Array.isArray(dev._correction.fields)) dev._correction.fields = [];
    if (!dev._correction.fields.includes('port_count')) dev._correction.fields.push('port_count');
    dev._correction.applied_at = ds.ts;
    dev._correction.port_shift = ds.shift;
    dev.port_count = newCount;
  }

  return payload;
}

async function cropBoxImage(rackId, box, destPath, padRatio = 0.25, minPad = 6) {
  if (!Array.isArray(box) || box.length !== 4) return false;
  const photo = photoPathOf(rackId);
  if (!photo) return false;
  const meta = { imagePath: photo };

  const [x1, y1, x2, y2] = box.map(v => Math.round(Number(v)));
  const w = x2 - x1, h = y2 - y1;
  if (w <= 0 || h <= 0) return false;

  try {
    const image = sharp(meta.imagePath);
    const { width: imgW, height: imgH } = await image.metadata();
    const pad = Math.max(minPad, Math.round(Math.min(w, h) * padRatio));
    const left   = Math.max(0, x1 - pad);
    const top    = Math.max(0, y1 - pad);
    const right  = Math.min(imgW, x2 + pad);
    const bottom = Math.min(imgH, y2 + pad);
    await image
      .extract({ left, top, width: right - left, height: bottom - top })
      .png()
      .toFile(destPath);
    return true;
  } catch (err) {
    logger.error('cropBoxImage failed:', err.message);
    return false;
  }
}

app.post('/api/feedback', auth.requireAuth, async (req, res) => {
  const {
    scanId, device_index, predicted_port, is_correct,
    actual_port, actual_cable_color,
  } = req.body || {};

  if (!scanId || device_index == null || predicted_port == null || typeof is_correct !== 'boolean') {
    return res.status(400).json({ error: 'scanId, device_index, predicted_port, is_correct are required' });
  }
  if (!is_correct && actual_port == null && !actual_cable_color) {
    return res.status(400).json({
      error: 'When is_correct is false, supply at least one of actual_port, actual_cable_color',
    });
  }

  // Tenant guard: feedback endpoints take scanId in the BODY (not the path),
  // so the global app.param('rackId',...) doesn't fire here. Verify the
  // calling user's tenant owns this rack before letting them write feedback.
  const fbAuth = softAuthPayload(req);
  if (!canAccessRack(fbAuth, scanId)) {
    logger.warn({ event: 'tenant.access_denied', tenantId: fbAuth?.tenantId ?? null,
      rackId: scanId, route: '/api/feedback' },
      `tenant ${fbAuth?.tenantId ?? 'anonymous'} blocked from feedback on rack ${scanId}`);
    return res.status(404).json({ error: `Scan ${scanId} not found` });
  }

  const meta = readMeta(scanId);
  if (!meta) return res.status(404).json({ error: `Scan ${scanId} not found` });

  const rackDir  = engineDir(scanId);
  const infoPath = path.join(rackDir, 'selected_port_info.json');
  const fullData = fs.existsSync(infoPath) ? JSON.parse(fs.readFileSync(infoPath, 'utf8')) : {};
  const portInfo = fullData.port_info || {};

  let deviceClass = null;
  let deviceBox = null;
  try {
    const mapPath = path.join(rackDir, 'device_unit_map.json');
    if (fs.existsSync(mapPath)) {
      const map = JSON.parse(fs.readFileSync(mapPath, 'utf8'));
      const dev = (map.devices || [])[Number(device_index) - 1];
      deviceClass = dev?.class_name || null;
      deviceBox   = dev?.box || null;
    }
  } catch (_) {}

  const wrongFields = [];
  if (!is_correct) {
    if (actual_port != null)  wrongFields.push('port');
    if (actual_cable_color)   wrongFields.push('cable_color');
  }

  let portCropSavedAs = null;
  let deviceCropSavedAs = null;
  if (!is_correct) {
    const tag = wrongFields.length ? wrongFields.join('-') : 'unspecified';
    const base = `${scanId}_dev${device_index}_pred${predicted_port}_${tag}`;
    const portDest = path.join(cropsDirOf(scanId), `${base}_port.png`);
    if (await cropBoxImage(scanId, portInfo.location, portDest, 0.25, 6)) {
      portCropSavedAs = `${base}_port.png`;
    }
    const devDest = path.join(cropsDirOf(scanId), `${base}_device.png`);
    if (await cropBoxImage(scanId, deviceBox, devDest, 0.05, 4)) {
      deviceCropSavedAs = `${base}_device.png`;
    }
  }

  // Normalize the user-visible (already-shifted) predicted port back to RAW
  // model space, so this correction records an ABSOLUTE offset rather than
  // chaining onto the device's current shift. Then _latestPortShift can use
  // just the most recent correction and shifts can't compound.
  let predictedRaw = Number(predicted_port);
  if (!is_correct && actual_port != null) {
    try {
      const priorShift = _latestPortShift(_readFeedbackForScan(scanId), device_index);
      predictedRaw = Number(predicted_port) - priorShift;
    } catch (_) { /* fall back to raw = as-sent */ }
  }

  const entry = {
    timestamp: new Date().toISOString(),
    feedback_type: 'port',
    scanId,
    device_index: Number(device_index),
    device_class: deviceClass,
    is_correct,
    wrong_fields: wrongFields,
    // Port index (RAW model space - see predictedRaw above)
    predicted_port: predictedRaw,
    actual_port: (is_correct || actual_port == null) ? null : Number(actual_port),
    // Cable color
    predicted_cable_color: portInfo.cable_color || null,
    actual_cable_color: is_correct ? null : (actual_cable_color || null),
    // Context
    port_status: portInfo.status || null,
    cable_color: portInfo.cable_color || null,
    cable_connector: portInfo.cable_connector || null,
    cable_type: portInfo.cable_type || null,
    port_location: portInfo.location || null,
    device_box: deviceBox,
    port_crop_image: portCropSavedAs,
    device_crop_image: deviceCropSavedAs,
  };

  try {
    recordFeedback(req, scanId, entry, {
      target: { device_index: entry.device_index, port: entry.predicted_port },
      before: { port: entry.predicted_port, cable_color: entry.predicted_cable_color },
      after: is_correct ? null : { port: entry.actual_port, cable_color: entry.actual_cable_color },
    });
  } catch (err) {
    logger.error('feedback write failed:', err.message);
    audit.log({ req, action: 'feedback.submit', status: 'fail', targetType: 'rack', targetId: scanId,
                error: err.message, payload: { feedback_type: 'port' } });
    return res.status(500).json({ error: 'Failed to save feedback' });
  }

  audit.log({ req, action: 'feedback.submit', status: 'ok', targetType: 'rack', targetId: scanId,
              payload: { feedback_type: 'port', device_index: Number(device_index), is_correct } });
  scheduleCanonicalRefresh(scanId);

  // AL memory: persist pHash + ResNet18 embedding for the cable color so
  // future scans of similar cables auto-apply the corrected label.
  if (!is_correct && actual_cable_color && portCropSavedAs) {
    const cropPath = path.join(cropsDirOf(scanId), portCropSavedAs);
    fireMemoryCorrection('cable', cropPath,
      portInfo.cable_color || '', actual_cable_color,
      `${scanId}_dev${device_index}_port${predicted_port}`,
      softAuthPayload(req)?.organizationId || null);
  }

  res.json({ ok: true, port_crop_image: portCropSavedAs, device_crop_image: deviceCropSavedAs });
});

// POST /api/feedback/port-type
// Active-learning correction for a port's physical TYPE (RJ45 / SFP / QSFP /
// FC / LC / SC / CONSOLE / AUX / MANAGEMENT_PORT / USB_A / USB_B / USB_C).
// Mirrors the cable path: crop the port, log to feedback.jsonl, and persist a
// pHash+embedding memory so future scans of the same port auto-apply the
// corrected type. The crop is filed under the corrected class so it is also a
// training sample, which is why this list has to be the type model's own class
// names and has to grow with it: a correction filed under a name the model
// does not have is a training sample it can never learn from.
//
// The fibre connectors came in with ports_13. The model's thirteenth class,
// `empty`, is deliberately NOT offered here. Whether a port has anything in it
// is the status model's answer, and putting "empty" in a list of connector
// types would invite a technician to file occupancy as though it were type.
const PORT_TYPE_OPTIONS = [
  'RJ45', 'SFP', 'QSFP', 'FC', 'LC', 'SC', 'CONSOLE', 'AUX', 'MANAGEMENT_PORT',
  'USB_A', 'USB_B', 'USB_C',
];
app.post('/api/feedback/port-type', auth.requireAuth, async (req, res) => {
  const {
    scanId, device_index, port,
    predicted_type = null, actual_type,
    port_location = null,
  } = req.body || {};

  if (!scanId || device_index == null) {
    return res.status(400).json({ error: 'scanId and device_index are required' });
  }

  // scanId is joined into an output path and into a written filename, and
  // path.join happily resolves "..". Validate the shape before it reaches the
  // filesystem - this holds even for callers canAccessRack still lets through.
  if (!/^RK-[A-Za-z0-9]{4,32}$/.test(String(scanId))) {
    return res.status(400).json({ error: 'Invalid scanId' });
  }

  // Ownership check, matching every sibling feedback route. This one was the
  // single /api/feedback/* endpoint that had none: it took scanId straight
  // from the body and joined it into an output path, so an unauthenticated
  // caller could probe for and write crops against any tenant's rack.
  const fbAuth = softAuthPayload(req);
  if (!canAccessRack(fbAuth, scanId)) {
    return res.status(404).json({ error: `Rack ${scanId} not found` });
  }
  if (!actual_type || !PORT_TYPE_OPTIONS.includes(String(actual_type))) {
    return res.status(400).json({ error: `actual_type must be one of: ${PORT_TYPE_OPTIONS.join(', ')}` });
  }
  const rackDir = engineDir(scanId);
  if (!fs.existsSync(rackDir)) {
    return res.status(404).json({ error: `Rack ${scanId} not found` });
  }

  // Crop the port so the correction has an image to hash and file as a sample.
  let portCropSavedAs = null;
  if (Array.isArray(port_location) && port_location.length === 4) {
    const base = `${scanId}_dev${device_index}_port${port}_type-${actual_type}`;
    const dest = path.join(cropsDirOf(scanId), `${base}_port.png`);
    if (await cropBoxImage(scanId, port_location, dest, 0.25, 6)) {
      portCropSavedAs = `${base}_port.png`;
    }
  }

  const entry = {
    timestamp: new Date().toISOString(),
    feedback_type: 'port_type',
    scanId,
    device_index: Number(device_index),
    port: port != null ? Number(port) : null,
    predicted_port_type: predicted_type || null,
    actual_port_type: String(actual_type),
    port_location: port_location || null,
    port_crop_image: portCropSavedAs,
  };
  try {
    recordFeedback(req, scanId, entry, {
      target: { device_index: entry.device_index, port: entry.port },
      before: { port_type: entry.predicted_port_type },
      after: { port_type: entry.actual_port_type },
    });
  } catch (err) {
    logger.error('port-type feedback write failed:', err.message);
    audit.log({ req, action: 'feedback.submit', status: 'fail', targetType: 'rack', targetId: scanId,
                error: err.message, payload: { feedback_type: 'port_type' } });
    return res.status(500).json({ error: 'Failed to save feedback' });
  }

  audit.log({ req, action: 'feedback.submit', status: 'ok', targetType: 'rack', targetId: scanId,
              payload: { feedback_type: 'port_type', device_index: Number(device_index), actual_type } });

  // AL memory: a re-scan of the same port will match this crop and apply
  // the corrected type. Org-scoped so it stays within the caller's org.
  if (portCropSavedAs) {
    const cropPath = path.join(cropsDirOf(scanId), portCropSavedAs);
    fireMemoryCorrection('port_type', cropPath,
      predicted_type || '', String(actual_type),
      `${scanId}_dev${device_index}_port${port}`,
      softAuthPayload(req)?.organizationId || null);
  }

  res.json({ ok: true, port_crop_image: portCropSavedAs, actual_type: String(actual_type) });
});

// POST /api/scan/:rackId/confirm-layout
// Mark a rack as CONFIRMED by the user. Registers the rack image's perceptual
// fingerprint → rackId so a later re-upload that matches serves this confirmed
// result instead of re-detecting it. "The user already fixed it - show that."
app.post('/api/scan/:rackId/confirm-layout', auth.requireAuth, async (req, res) => {
  const { rackId } = req.params;
  const rackDir = engineDir(rackId);
  if (!fs.existsSync(path.join(rackDir, 'device_unit_map.json'))) {
    return res.status(404).json({ error: `Rack ${rackId} not found` });
  }
  // The scan's photo is the fingerprint.
  const imagePath = photoPathOf(rackId);
  if (!imagePath) return res.status(400).json({ error: 'No source image to fingerprint' });

  try {
    const orgId = softAuthPayload(req)?.organizationId || null;
    const r = await runActiveLearningCli(
      { cmd: 'add_confirmed_rack', image_path: imagePath, rack_id: rackId, org_id: orgId }, 90000);
    audit.log({ req, action: 'scan.confirm_layout', status: 'ok', targetType: 'rack', targetId: rackId });
    logger.info({ event: 'scan.confirmed', rackId, phash: r?.confirmed?.phash }, `rack ${rackId} confirmed`);
    res.json({ ok: true, confirmed: r?.confirmed || null });
  } catch (err) {
    logger.error(`[confirm-layout] ${rackId}: ${err.message}`);
    audit.log({ req, action: 'scan.confirm_layout', status: 'fail', targetType: 'rack', targetId: rackId, error: err.message });
    res.status(500).json({ error: 'Failed to confirm layout' });
  }
});

// ── Device-only feedback ──────────────────────────────────────
// Independent of port/cable feedback. The user looks at a device's
// predicted class and either confirms it or supplies the actual class.
// The device correction the results screen writes (POST /api/feedback/device),
// as a function, so the worklist (routes/physical.js) writes exactly the same
// row when a person confirms or corrects a device there. Returns
// { status, body } for the route to send.
async function saveDeviceFeedback(req, { scanId, device_index, is_correct, actual_device_class } = {}) {
  if (!scanId || device_index == null || typeof is_correct !== 'boolean') {
    return { status: 400, body: { error: 'scanId, device_index, is_correct are required' } };
  }
  if (!is_correct && !actual_device_class) {
    return { status: 400, body: { error: 'actual_device_class is required when is_correct is false' } };
  }

  // Tenant guard (scanId is in body, not path)
  const fbAuth = softAuthPayload(req) || req.user || null;
  if (!canAccessRack(fbAuth, scanId)) {
    return { status: 404, body: { error: `Scan ${scanId} not found` } };
  }

  const meta = readMeta(scanId);
  if (!meta) return { status: 404, body: { error: `Scan ${scanId} not found` } };

  const rackDir = engineDir(scanId);

  let predictedClass = null;
  let deviceBox = null;
  try {
    const mapPath = path.join(rackDir, 'device_unit_map.json');
    if (fs.existsSync(mapPath)) {
      const map = JSON.parse(fs.readFileSync(mapPath, 'utf8'));
      const dev = (map.devices || [])[Number(device_index) - 1];
      predictedClass = dev?.class_name || null;
      deviceBox      = dev?.box || null;
      // Persist the corrected class into device_unit_map.json so the picker
      // dropdown, Recent Scans and any re-open reflect it - not only the
      // result-overlay. Without this the change was purely cosmetic/session.
      if (dev && !is_correct && actual_device_class && actual_device_class !== dev.class_name) {
        if (!dev.class_name_original) dev.class_name_original = dev.class_name;
        dev.class_name = actual_device_class;
        dev.class_name_source = 'user_corrected';
        const tmp = mapPath + '.tmp';
        fs.writeFileSync(tmp, JSON.stringify(map, null, 2));
        fs.renameSync(tmp, mapPath);
      }
    }
  } catch (e) { logger.warn('device feedback: device_unit_map update failed: ' + e.message); }

  let deviceCropSavedAs = null;
  if (!is_correct) {
    const safeActual = String(actual_device_class).replace(/[^A-Za-z0-9_-]+/g, '_');
    const safePred   = String(predictedClass || 'Unknown').replace(/[^A-Za-z0-9_-]+/g, '_');
    const base = `${scanId}_dev${device_index}_devclass_${safePred}_to_${safeActual}`;
    const devDest = path.join(cropsDirOf(scanId), `${base}_device.png`);
    if (await cropBoxImage(scanId, deviceBox, devDest, 0.05, 4)) {
      deviceCropSavedAs = `${base}_device.png`;
    }
  }

  const entry = {
    timestamp: new Date().toISOString(),
    feedback_type: 'device',
    scanId,
    device_index: Number(device_index),
    is_correct,
    predicted_device_class: predictedClass,
    actual_device_class: is_correct ? null : actual_device_class,
    device_box: deviceBox,
    device_crop_image: deviceCropSavedAs,
  };

  try {
    recordFeedback(req, scanId, entry, {
      target: { device_index: entry.device_index },
      before: { class_name: entry.predicted_device_class },
      after: is_correct ? null : { class_name: entry.actual_device_class },
    });
  } catch (err) {
    logger.error('device feedback write failed:', err.message);
    audit.log({ req, action: 'feedback.submit', status: 'fail', targetType: 'rack', targetId: scanId,
                error: err.message, payload: { feedback_type: 'device' } });
    return { status: 500, body: { error: 'Failed to save feedback' } };
  }

  audit.log({ req, action: 'feedback.submit', status: 'ok', targetType: 'rack', targetId: scanId,
              payload: { feedback_type: 'device', device_index: Number(device_index), is_correct } });
  scheduleCanonicalRefresh(scanId);

  // AL memory: persist pHash + ResNet18 embedding for the device class so
  // future scans of similar devices auto-apply the corrected label.
  if (!is_correct && actual_device_class && deviceCropSavedAs) {
    const cropPath = path.join(cropsDirOf(scanId), deviceCropSavedAs);
    fireMemoryCorrection('devices', cropPath,
      predictedClass || '', actual_device_class,
      `${scanId}_dev${device_index}`,
      (softAuthPayload(req)?.organizationId ?? req.user?.organization_id) || null);
  }

  return { status: 200, body: { ok: true, device_crop_image: deviceCropSavedAs } };
}

app.post('/api/feedback/device', auth.requireAuth, async (req, res) => {
  const out = await saveDeviceFeedback(req, req.body || {});
  res.status(out.status).json(out.body);
});

// ── Port-count feedback (main ports detected per device) ──────
// Independent of device-class and port/cable feedback. The user
// confirms how many main ports the model detected for the selected
// device, or supplies the actual count.
app.post('/api/feedback/port-count', auth.requireAuth, async (req, res) => {
  const { scanId, device_index, is_correct, actual_port_count } = req.body || {};

  if (!scanId || device_index == null || typeof is_correct !== 'boolean') {
    return res.status(400).json({ error: 'scanId, device_index, is_correct are required' });
  }
  const actualNum = actual_port_count == null ? null : Number(actual_port_count);
  if (!is_correct && (actualNum == null || isNaN(actualNum) || actualNum < 0)) {
    return res.status(400).json({ error: 'actual_port_count is required (>= 0) when is_correct is false' });
  }

  // Tenant guard (scanId is in body, not path)
  const fbAuth = softAuthPayload(req);
  if (!canAccessRack(fbAuth, scanId)) {
    return res.status(404).json({ error: `Scan ${scanId} not found` });
  }

  const meta = readMeta(scanId);
  if (!meta) return res.status(404).json({ error: `Scan ${scanId} not found` });

  const rackDir = engineDir(scanId);
  let predictedCount = null;
  let deviceClass = null;
  let deviceBox = null;
  try {
    const mapPath = path.join(rackDir, 'device_unit_map.json');
    if (fs.existsSync(mapPath)) {
      const map = JSON.parse(fs.readFileSync(mapPath, 'utf8'));
      const dev = (map.devices || [])[Number(device_index) - 1];
      predictedCount = typeof dev?.port_count === 'number' ? dev.port_count : null;
      deviceClass    = dev?.class_name || null;
      deviceBox      = dev?.box || null;
    }
  } catch (_) {}

  let deviceCropSavedAs = null;
  if (!is_correct) {
    const safePred = predictedCount == null ? 'na' : String(predictedCount);
    const base = `${scanId}_dev${device_index}_portcount_${safePred}_to_${actualNum}`;
    const devDest = path.join(cropsDirOf(scanId), `${base}_device.png`);
    if (await cropBoxImage(scanId, deviceBox, devDest, 0.05, 4)) {
      deviceCropSavedAs = `${base}_device.png`;
    }
  }

  const entry = {
    timestamp: new Date().toISOString(),
    feedback_type: 'port_count',
    scanId,
    device_index: Number(device_index),
    device_class: deviceClass,
    is_correct,
    predicted_port_count: predictedCount,
    actual_port_count: is_correct ? null : actualNum,
    device_box: deviceBox,
    device_crop_image: deviceCropSavedAs,
  };

  try {
    recordFeedback(req, scanId, entry, {
      target: { device_index: entry.device_index },
      before: { port_count: entry.predicted_port_count },
      after: is_correct ? null : { port_count: entry.actual_port_count },
    });
  } catch (err) {
    logger.error('port-count feedback write failed:', err.message);
    audit.log({ req, action: 'feedback.submit', status: 'fail', targetType: 'rack', targetId: scanId,
                error: err.message, payload: { feedback_type: 'port_count' } });
    return res.status(500).json({ error: 'Failed to save feedback' });
  }

  audit.log({ req, action: 'feedback.submit', status: 'ok', targetType: 'rack', targetId: scanId,
              payload: { feedback_type: 'port_count', device_index: Number(device_index), is_correct, actual_port_count: actualNum } });

  // If the user supplied an actual count, re-run port detection for this
  // device with that target so the device's port_count reflects ground truth.
  let relabel = null;
  if (!is_correct && actualNum != null) {
    try {
      const r = await runRelabelPortCount(rackDir, Number(device_index), actualNum);
      if (r?.ok) {
        relabel = {
          ok: true,
          device_index: r.device_index,
          new_port_count: r.port_count,
          device: r.device,
        };
      } else {
        relabel = { ok: false, error: r?.error || 'relabel failed' };
      }
    } catch (err) {
      logger.error('relabel_port_count failed:', err.message);
      relabel = { ok: false, error: err.message };
    }
  }

  // Refresh canonical scan_result.json after both the feedback append and any
  // port-count relabel mutation to device_unit_map.json.
  scheduleCanonicalRefresh(scanId);
  res.json({
    ok: true,
    device_crop_image: deviceCropSavedAs,
    relabel,
  });
});

// ════════════════════════════════════════════════════════════════════
// Active-learning memory layer (cable/device corrections + verified
// port store). Wraps pipeline/active_learning/cli.py via subprocess.
// Memory is additive: existing JSONL feedback still written; this
// extra write enables auto-apply on future scans and a model-bypass
// verified store for ports.
// ════════════════════════════════════════════════════════════════════
function runActiveLearningCli(payload, timeoutMs = 60000) {
  // The engine never builds a path into the data tree: the organization's
  // training folder travels with the request.
  if (payload && payload.org_id != null && !payload.al_root) {
    payload = { ...payload, al_root: alRootFor(payload.org_id) };
  }
  return new Promise((resolve, reject) => {
    const { spawn } = require('child_process');
    const pyBin = resolvePython();
    const child = spawn(pyBin, ['-m', 'pipeline.active_learning.cli'], {
      cwd: path.join(__dirname, '..'),
      windowsHide: true,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    });
    let stdout = '', stderr = '';
    const t = setTimeout(() => {
      try { child.kill(); } catch (_) {}
      reject(new Error(`AL CLI timeout after ${timeoutMs}ms`));
    }, timeoutMs);
    child.stdout.on('data', d => { stdout += d.toString(); });
    child.stderr.on('data', d => { stderr += d.toString(); });
    child.on('error', err => { clearTimeout(t); reject(err); });
    child.on('close', code => {
      clearTimeout(t);
      const lastLine = stdout.trim().split('\n').pop() || '';
      let parsed = null;
      try { parsed = JSON.parse(lastLine); } catch (_) {}
      if (!parsed) return reject(new Error(`AL CLI parse error (exit ${code}): ${stderr.slice(-400) || stdout.slice(-400)}`));
      if (parsed.ok === false) return reject(new Error(parsed.error || 'AL CLI failed'));
      resolve(parsed);
    });
    child.stdin.write(JSON.stringify(payload));
    child.stdin.end();
  });
}

// Fire-and-forget memory write. Used by the existing feedback endpoints
// so they keep their fast user-visible response while AL embeds in the
// background. Logs failures but never throws.
function fireMemoryCorrection(model, imagePath, predLabel, finalLabel, sourceName, orgId = null) {
  if (!imagePath || !fs.existsSync(imagePath)) return;
  // No organization, no memory. And an organization that chose "never
  // train" gets nothing written to its training store: the correction stays
  // on its scan (corrections.jsonl) and nowhere else.
  if (orgId == null) return;
  if (orgSettings.neverTrain(orgId)) {
    logger.info({ event: 'al.correction_skipped', model, orgId }, 'organization never trains: correction not added to the memory');
    return;
  }
  runActiveLearningCli({
    cmd: 'add_correction',
    model, image_path: imagePath,
    pred_label: predLabel || '',
    final_label: finalLabel,
    source_name: sourceName || path.basename(imagePath),
    org_id: orgId,   // org-scoped: correction saved into the org's AL partition
  }).then(r => {
    logger.info({ event: 'al.correction_saved', model, phash: r.record?.phash,
                  source: sourceName }, `AL memory saved (${model})`);
  }).catch(err => {
    logger.warn({ event: 'al.correction_failed', model, error: err.message }, 'AL memory write failed');
  });
}

// GET /api/feedback/memory/stats
// Aggregate counts of the saved corrections, per model.
app.get('/api/feedback/memory/stats', auth.requireAuth, async (req, res) => {
  try {
    const r = await runActiveLearningCli({ cmd: 'stats' }, 60000);
    res.json({ ok: true, ...r.stats });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/feedback/stats', auth.requireAuth, (req, res) => {
  try {
    const rows = allFeedbackRows();
    if (!rows.length) {
      return res.json({ total: 0, correct: 0, wrong: 0, accuracy: null, by_device_class: {} });
    }
    const byCls = {};
    let total = 0, correct = 0;
    for (const e of rows) {
      total += 1;
      if (e.is_correct) correct += 1;
      const cls = e.device_class || 'Unknown';
      if (!byCls[cls]) byCls[cls] = { total: 0, correct: 0 };
      byCls[cls].total += 1;
      if (e.is_correct) byCls[cls].correct += 1;
    }
    res.json({
      total,
      correct,
      wrong: total - correct,
      accuracy: total ? correct / total : null,
      by_device_class: byCls,
    });
  } catch (err) {
    logger.warn({ event: 'feedback.scoreboard_failed', err: err.message });
    res.status(500).json({ error: 'scoreboard failed' });
  }
});

app.get(/^\/(?!api).*/, (req, res, next) => {
  // SPA fallback for client-side routes ONLY. A request that looks like a file
  // (has an extension, e.g. /assets/ResultsPage-abc.js) must NOT fall back
  // to index.html - express.static already served it if it exists, so reaching
  // here means it's MISSING (e.g. a stale hashed chunk after a rebuild). Return
  // 404 so the browser fails cleanly and can reload, instead of getting HTML
  // where it expects a JS module ("Load failed" on a dynamic import).
  if (path.extname(req.path)) return next();
  const indexPath = path.join(clientDist, 'index.html');
  if (fs.existsSync(indexPath)) return res.sendFile(indexPath);
  next();
});

// Global error handler - must be installed AFTER all routes/middleware so
// it catches anything that throws or calls next(err). Logs with full
// context (requestId, route, stack) and returns a sanitized JSON body.
app.use(o11y.errorHandler);

// Only bind the port when run directly (`node app.js` / `npm start`). Loading
// app.js as a module (tests, scripts) must not start a second listener.
if (require.main === module) {
  // Bind with retry-on-EADDRINUSE. The previous implementation created a fresh
  // server inside setTimeout without re-attaching the 'error' handler - a second
  // EADDRINUSE then crashed the process via an unhandled 'error' event. Wrap
  // listen() so every attempt has the same handler, and retry indefinitely
  // (a stale dev process usually clears within seconds).
  let server;
  let attempt = 0;
  const MAX_ATTEMPTS = 20;
  const tryListen = () => {
    attempt += 1;
    server = app.listen(PORT, () => {
      o11y.logBootBanner({ port: PORT, workers: WORKER_COUNT });
      logger.info({
        event: 'server.listening',
        attempt, dataRoot: store.DATA_ROOT, workers: WORKER_COUNT,
      }, `listening on :${PORT}${attempt > 1 ? ` (after ${attempt} attempts)` : ''}`);
    });
    server.on('error', (err) => {
      if (err.code === 'EADDRINUSE' && attempt < MAX_ATTEMPTS) {
        logger.warn({
          event: 'server.eaddrinuse',
          port: PORT, attempt, maxAttempts: MAX_ATTEMPTS,
        }, `port ${PORT} in use, retrying in 3s`);
        setTimeout(tryListen, 3000);
        return;
      }
      logger.fatal({
        event: 'server.listen_failed',
        err: err.code || err.message,
      }, 'listen failed');
      process.exit(1);
    });
  };
  tryListen();

  const gracefulShutdown = async (signal) => {
    logger.info({ event: 'server.shutdown', signal },
      `${signal} received - stopping workers and HTTP server`);
    if (server) {
      try { server.close(); } catch (_) {}
    }
    pool.shutdown().finally(() => {
      logger.info({ event: 'server.shutdown_done' }, 'workers stopped, exiting');
      process.exit(0);
    });
    // Hard ceiling so a wedged worker / open socket can't block forever.
    // Overridable via SHUTDOWN_TIMEOUT_MS; default 30s gives long pipelines
    // a fair chance to finish before SIGKILL.
    const _shutdownTimeoutMs = parseInt(process.env.SHUTDOWN_TIMEOUT_MS, 10) || 30000;
    setTimeout(() => {
      logger.warn({ event: 'server.shutdown_forced', timeoutMs: _shutdownTimeoutMs },
        'graceful shutdown timeout - forcing exit');
      process.exit(1);
    }, _shutdownTimeoutMs).unref();
  };
  process.on('SIGINT',  () => gracefulShutdown('SIGINT'));
  process.on('SIGTERM', () => gracefulShutdown('SIGTERM'));
}
