#!/usr/bin/env node
/**
 * npm test: run the server suite against a throwaway database folder.
 *
 * Several test files load app.js and write accounts, racks and log rows into
 * whatever database the server opens. Run plainly, that was the live
 * data/db/auth.db. This sets RACKTRACK_AUTH_DB (and with it every other
 * file in lib/data_paths.js) to a fresh temporary folder for the whole run,
 * then removes the folder. A test file that picks its own database still does.
 * It also keeps the live <repo>/.env out of the run (see RT_ENV_FILE below).
 *
 * Extra arguments go to node --test, for example a single file:
 *   npm test -- test/scan_sites.test.js
 */
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const serverDir = path.join(__dirname, '..');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'rt-eos-tests-'));
// RT_ENV_FILE names a file that does not exist, so no test reads the live
// secrets in <repo>/.env (and no test can reach a real mailbox).
const env = {
  ...process.env,
  RACKTRACK_AUTH_DB: path.join(tmp, 'auth.db'),
  RT_ENV_FILE: path.join(tmp, 'no-such.env'),
};
delete env.LOG_DB_PATH;

const extra = process.argv.slice(2);
const args = ['--test', '--test-concurrency=1', ...(extra.length ? extra : ['test/'])];
const r = spawnSync(process.execPath, args, { cwd: serverDir, env, stdio: 'inherit' });

try { fs.rmSync(tmp, { recursive: true, force: true }); } catch { /* best effort */ }
process.exit(r.status == null ? 1 : r.status);
