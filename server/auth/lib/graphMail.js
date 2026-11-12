/**
 * Microsoft Graph email sender.
 *
 * Pilot mail (sign-up codes, password resets, the contact form) goes out FROM
 * support@racktrack.ai. Every setting comes from <repo>/.env:
 *
 *   MS_TENANT_ID                    the Microsoft 365 tenant of the mailbox
 *   MS_CLIENT_ID, MS_CLIENT_SECRET  optional app-only path (client credentials,
 *                                   Mail.Send application permission)
 *   MS_REFRESH_TOKEN_SUPPORT        the support mailbox's delegated refresh token
 *   MS_REFRESH_CLIENT_ID_SUPPORT    the Azure app that issued that refresh token
 *                                   (a refresh token is only redeemable by the
 *                                   client that issued it; falls back to
 *                                   MS_CLIENT_ID)
 *   MAIL_FROM_SUPPORT               override the sender address
 *
 * Token order: app-only first (one token serves every mailbox), then the
 * mailbox's refresh token. Each refresh hands back a rotated refresh token; it
 * is kept in a small cache file in the data folder (lib/data_paths.js) so the
 * mailbox keeps working past the life of the one in .env. Changing the value in
 * .env makes the cache stale, and the new .env value is used.
 *
 * If no token can be obtained, sendGraphMail returns false and the caller
 * falls back to SMTP (see auth.js), so mail delivery never hard-fails here.
 */
const fs = require('fs');
const crypto = require('crypto');
const dataPaths = require('./data_paths');

let _logger;
function logger() {
  if (_logger) return _logger;
  try { _logger = require('./observability').logger; }
  catch { _logger = console; }
  return _logger;
}

const env = (k) => (process.env[k] && String(process.env[k]).trim()) || null;

function readIds() {
  return { clientId: env('MS_CLIENT_ID'), tenantId: env('MS_TENANT_ID') };
}

const SENDERS = {
  support: {
    address: env('MAIL_FROM_SUPPORT') || 'support@racktrack.ai',
    refreshTokenEnv: 'MS_REFRESH_TOKEN_SUPPORT',
    refreshClientEnv: 'MS_REFRESH_CLIENT_ID_SUPPORT',
  },
};

// ── Refresh tokens: .env first, then the rotated copy in the data folder ─────
const CACHE_FILE = dataPaths.MAIL_TOKEN_CACHE_FILE;
const seedOf = (token) => crypto.createHash('sha256').update(token).digest('hex').slice(0, 16);

function readCache() {
  try { return JSON.parse(fs.readFileSync(CACHE_FILE, 'utf8')) || {}; }
  catch { return {}; }
}

function writeCache(senderKey, entry) {
  try {
    const all = readCache();
    all[senderKey] = entry;
    const tmp = `${CACHE_FILE}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(all), { mode: 0o600 });
    fs.renameSync(tmp, CACHE_FILE);
  } catch (err) {
    logger().warn(`[graphMail] could not keep the rotated ${senderKey} token: ${err.message}`);
  }
}

// The refresh tokens to try, newest first: the rotated one from the cache when
// it grew from the token now in .env, then the .env token itself.
function refreshCandidates(senderKey) {
  const s = SENDERS[senderKey];
  if (!s) return [];
  const envToken = env(s.refreshTokenEnv);
  const clientId = env(s.refreshClientEnv) || readIds().clientId;
  if (!envToken || !clientId) return [];
  const seed = seedOf(envToken);
  const out = [];
  const cached = readCache()[senderKey];
  if (cached && cached.seed === seed && cached.refresh_token && cached.refresh_token !== envToken) {
    out.push({ secret: cached.refresh_token, clientId, seed });
  }
  out.push({ secret: envToken, clientId, seed });
  return out;
}

const _msalTokens = new Map(); // senderKey -> { token, exp }

async function refreshToken(senderKey) {
  const now = Date.now() / 1000;
  const hit = _msalTokens.get(senderKey);
  if (hit && hit.exp - 60 > now) return hit.token;

  const { tenantId } = readIds();
  if (!tenantId) return null;

  for (const rt of refreshCandidates(senderKey)) {
    try {
      const body = new URLSearchParams({
        client_id: rt.clientId,
        refresh_token: rt.secret,
        grant_type: 'refresh_token',
        scope: 'https://graph.microsoft.com/Mail.Send offline_access',
      });
      const r = await fetch(`https://login.microsoftonline.com/${tenantId}/oauth2/v2.0/token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body,
      });
      if (!r.ok) {
        logger().error(`[graphMail] refresh-token grant failed for ${senderKey}: ${r.status} ${await r.text().catch(() => '')}`);
        continue;
      }
      const j = await r.json();
      if (!j.access_token) continue;
      if (j.refresh_token) writeCache(senderKey, { refresh_token: j.refresh_token, seed: rt.seed, saved_at: new Date().toISOString() });
      _msalTokens.set(senderKey, { token: j.access_token, exp: now + (j.expires_in || 3600) });
      logger().info(`[graphMail] minted a ${senderKey} token from its refresh token`);
      return j.access_token;
    } catch (err) {
      logger().error(`[graphMail] refresh-token request error for ${senderKey}: ${err.message}`);
    }
  }
  return null;
}

// App-only token via client credentials, cached in memory until ~1 min before
// expiry so we refresh proactively.
let _appToken = { token: null, exp: 0 };
async function appToken() {
  const now = Date.now() / 1000;
  if (_appToken.token && _appToken.exp - 60 > now) return _appToken.token;

  const { clientId, tenantId } = readIds();
  const secret = env('MS_CLIENT_SECRET');
  if (!clientId || !tenantId || !secret) return null;

  try {
    const body = new URLSearchParams({
      client_id: clientId,
      client_secret: secret,
      scope: 'https://graph.microsoft.com/.default',
      grant_type: 'client_credentials',
    });
    const r = await fetch(`https://login.microsoftonline.com/${tenantId}/oauth2/v2.0/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body,
    });
    if (!r.ok) {
      logger().error(`[graphMail] client-credentials token failed: ${r.status} ${await r.text().catch(() => '')}`);
      return null;
    }
    const j = await r.json();
    _appToken = { token: j.access_token, exp: now + (j.expires_in || 3600) };
    return _appToken.token;
  } catch (err) {
    logger().error(`[graphMail] token request error: ${err.message}`);
    return null;
  }
}

async function tokenFor(senderKey) {
  const app = await appToken();
  if (app) return app;
  return refreshToken(senderKey);
}

// True when at least one path could produce a token, so callers can decide
// whether to even attempt Graph before falling back to SMTP. Makes no network
// call and sends nothing.
function isConfigured() {
  const { clientId, tenantId } = readIds();
  if (!tenantId) return false;
  if (clientId && env('MS_CLIENT_SECRET')) return true;
  return Object.keys(SENDERS).some((k) => refreshCandidates(k).length > 0);
}

// Graph rejects a sendMail whose whole serialised message exceeds 4 MB; larger
// payloads need a draft plus an upload session, which is a lot of machinery for
// the one case that needs it. Attachments are base64 in the JSON body, so the
// wire cost is ~4/3 of the raw bytes - the ceiling below is on the ENCODED size
// and leaves room for the body and headers. Past it we return false, and the
// caller falls back to SMTP, which carries the larger message happily.
const GRAPH_MAX_ENCODED_BYTES = 3.5 * 1024 * 1024;

/**
 * Send an email FROM one of the configured mailboxes via Microsoft Graph.
 * Returns true on delivery (HTTP 202), false if not configured, too large for
 * a single sendMail, or on failure - the caller then falls back to SMTP.
 *
 * `attachments` is the nodemailer shape - { filename, content: Buffer,
 * contentType } - so one array serves both transports and callers do not have
 * to know which one will carry the message.
 */
async function sendGraphMail({ sender = 'support', to, subject, html, text, replyTo, attachments }) {
  const s = SENDERS[sender];
  if (!s) throw new Error(`graphMail: unknown sender "${sender}"`);

  const token = await tokenFor(sender);
  if (!token) return false;

  const recipients = (Array.isArray(to) ? to : [to])
    .filter(Boolean)
    .map((address) => ({ emailAddress: { address } }));
  if (!recipients.length) throw new Error('graphMail: no recipients');

  const files = (attachments || []).filter((a) => a && a.content);
  const graphAttachments = files.map((a) => ({
    '@odata.type': '#microsoft.graph.fileAttachment',
    name: a.filename || 'attachment',
    contentType: a.contentType || 'application/octet-stream',
    contentBytes: Buffer.isBuffer(a.content)
      ? a.content.toString('base64')
      : Buffer.from(a.content).toString('base64'),
  }));

  const encodedBytes = graphAttachments.reduce((n, a) => n + a.contentBytes.length, 0);
  if (encodedBytes > GRAPH_MAX_ENCODED_BYTES) {
    logger().warn(
      `[graphMail] ${Math.round(encodedBytes / 1024)}KB of attachments exceeds the single-request ` +
      `sendMail limit - deferring to SMTP`,
    );
    return false;
  }

  const message = {
    subject: subject || '',
    body: { contentType: html ? 'HTML' : 'Text', content: html || text || '' },
    toRecipients: recipients,
    ...(replyTo ? { replyTo: [{ emailAddress: { address: replyTo } }] } : {}),
    ...(graphAttachments.length ? { attachments: graphAttachments } : {}),
  };

  try {
    const r = await fetch(
      `https://graph.microsoft.com/v1.0/users/${encodeURIComponent(s.address)}/sendMail`,
      {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, saveToSentItems: true }),
      },
    );
    if (r.status === 202) {
      logger().info(`[graphMail] sent from ${s.address} to ${recipients.map((x) => x.emailAddress.address).join(', ')}`);
      return true;
    }
    logger().error(`[graphMail] sendMail from ${s.address} failed: ${r.status} ${await r.text().catch(() => '')}`);
    return false;
  } catch (err) {
    logger().error(`[graphMail] sendMail from ${s.address} error: ${err.message}`);
    return false;
  }
}

module.exports = { sendGraphMail, isConfigured, SENDERS };
