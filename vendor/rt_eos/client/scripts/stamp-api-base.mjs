// After a mobile build: record which API base the bundle was built with, so
// the copy step can refuse a bundle that was rebuilt for the web in between.
import { writeFileSync } from 'node:fs';
const url = process.env.VITE_API_BASE || '';
if (!url) { console.error('stamp-api-base: VITE_API_BASE is empty'); process.exit(1); }
writeFileSync('dist/.api-base', url + '\n');
console.log(`\x1b[32m✔\x1b[0m Stamped dist/.api-base = ${url}`);
