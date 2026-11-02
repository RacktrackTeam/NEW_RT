/**
 * Where the server keeps its files. One setting decides all of them.
 *
 *   RACKTRACK_AUTH_DB   the SQLite file for accounts, organizations, sites,
 *                       racks and the audit log. Default: <repo>/data/db/auth.db.
 *
 * Everything else follows that file's folder:
 *
 *   DB_DIR     the folder the auth database sits in. scan-jobs.json, logs.db,
 *              the store index (index.db), the mail token cache and, only when
 *              JWT_SECRET is not set, a generated signing key live here.
 *   DATA_ROOT  the data tree (lib/store.js): orgs/ and cache/. It is the parent
 *              of DB_DIR when DB_DIR is named "db" (the normal layout,
 *              data/db/auth.db gives data/), else DB_DIR itself (a test that
 *              points RACKTRACK_AUTH_DB at <tmp>/auth.db gets <tmp>/orgs).
 *
 * So pointing RACKTRACK_AUTH_DB at a throwaway folder moves everything there,
 * and a test or a seed can never write to the live files by accident.
 *
 *   LOG_DB_PATH         optional: the request log database on its own
 *                       (':memory:' keeps it in memory). Default: logs.db
 *                       beside the auth database.
 *
 * The data layout is written down in lib/store.js. No other module builds a
 * path into the data tree.
 */
// The settings below may come from <repo>/.env, so load it first.
require('./load_env');
const path = require('path');

const REPO_ROOT = path.join(__dirname, '..', '..');
const DEFAULT_DATA_ROOT = path.join(REPO_ROOT, 'data');
const DEFAULT_AUTH_DB = path.join(DEFAULT_DATA_ROOT, 'db', 'auth.db');

const AUTH_DB = path.resolve(process.env.RACKTRACK_AUTH_DB || DEFAULT_AUTH_DB);
const DB_DIR = path.dirname(AUTH_DB);
const DATA_ROOT = path.basename(DB_DIR) === 'db' ? path.dirname(DB_DIR) : DB_DIR;
const LOG_DB = process.env.LOG_DB_PATH || path.join(DB_DIR, 'logs.db');

module.exports = {
  AUTH_DB,
  DB_DIR,
  // Older name for DB_DIR, kept for the modules and scripts that use it.
  DATA_DIR: DB_DIR,
  DATA_ROOT,
  LOG_DB,
  // The store's own index of scans (lib/store.js).
  INDEX_DB: path.join(DB_DIR, 'index.db'),
  // Used only when JWT_SECRET is not in .env (tests, throwaway data folders).
  FALLBACK_SIGNING_KEY_FILE: path.join(DB_DIR, 'signing.key'),
  // A rotated Microsoft refresh token for the mail sender (lib/graphMail.js).
  MAIL_TOKEN_CACHE_FILE: path.join(DB_DIR, 'mail_token_cache.json'),
  SCAN_JOBS_FILE: path.join(DB_DIR, 'scan-jobs.json'),
  DEFAULT_AUTH_DB,
  isDefault: AUTH_DB === path.resolve(DEFAULT_AUTH_DB),
};
