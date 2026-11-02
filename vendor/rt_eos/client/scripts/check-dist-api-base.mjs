// Runs before `cap copy` / `cap sync` (capacitor:copy:before hook). A plain
// `npm run build` empties dist and rebuilds it for the web, with no API base;
// copying that into the phone app makes every request go to the phone itself
// and the sign-in silently fails (15 Sep 2026). Refuse unless the bundle was
// built by build:mobile and still carries its API base.
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
const die = (m) => { console.error('\n\x1b[31m✖ Copy blocked:\x1b[0m ' + m + '\n  Run: VITE_API_BASE=https://api.your-domain.com npm run build:mobile\n'); process.exit(1); };
if (!existsSync('dist/.api-base')) die('dist was not built by build:mobile (no dist/.api-base). It is a web build.');
const url = readFileSync('dist/.api-base', 'utf8').trim();
const dir = 'dist/assets';
const hit = existsSync(dir) && readdirSync(dir).some((f) => f.endsWith('.js') && readFileSync(join(dir, f), 'utf8').includes(url));
if (!hit) die(`dist/assets does not contain the API base ${url}.`);
console.log(`\x1b[32m✔\x1b[0m dist carries API base ${url}`);
