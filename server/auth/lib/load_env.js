/**
 * Load the one secrets file, <repo>/.env, into process.env.
 *
 * Required first thing in app.js, so every module that reads process.env at
 * require time sees the values, from `npm run dev`, `npm start` or a plain
 * `node app.js` in any folder. A value already in the real environment wins.
 *
 * Format: KEY=VALUE per line, # comments on their own line, optional matching
 * quotes around the value. An empty value is skipped, so the code's default
 * applies. No substitution and no multi-line values.
 *
 * RT_ENV_FILE points at another file (the test runner points it at a file that
 * does not exist, so tests never read the live secrets).
 */
const fs = require('fs');
const path = require('path');

const ENV_FILE = process.env.RT_ENV_FILE || path.join(__dirname, '..', '..', '..', 'data', 'auth', '.env');

function loadEnv(file = ENV_FILE) {
  if (!fs.existsSync(file)) return false;
  for (const line of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
    const s = line.trim();
    if (!s || s.startsWith('#')) continue;
    const eq = s.indexOf('=');
    if (eq < 1) continue;
    const key = s.slice(0, eq).trim();
    if (key in process.env) continue;
    let val = s.slice(eq + 1).trim();
    if (val.length > 1 && (val[0] === '"' || val[0] === "'") && val.endsWith(val[0])) val = val.slice(1, -1);
    if (val === '') continue; // an empty key means "use the default"
    process.env[key] = val;
  }
  return true;
}

loadEnv();

module.exports = { ENV_FILE, loadEnv };
