// Guard: a mobile (Capacitor) build MUST point at a public HTTPS backend.
// The packaged app runs from capacitor://localhost, so a relative or
// localhost API base means "the phone itself" - every request would fail.
const url = process.env.VITE_API_BASE || '';
const die = (msg) => {
  console.error('\n\x1b[31m✖ Mobile build blocked:\x1b[0m ' + msg);
  console.error('\n  Build like this instead:');
  console.error('    VITE_API_BASE=https://api.your-domain.com npm run build:mobile\n');
  process.exit(1);
};
if (!url) die('VITE_API_BASE is empty. The app would have no backend to call.');
const SIM = process.env.RT_SIMULATOR === '1' && /^http:\/\/localhost:\d+$/i.test(url);
if (SIM) { console.log(`\x1b[32m✔\x1b[0m Simulator build, API base: ${url} (the simulator shares this Mac's localhost)`); process.exit(0); }
// An office Wi-Fi build: phones on the same network reach the pilot server on a
// private address (10.x, 192.168.x, 172.16-31.x) over plain http. Asked for on
// purpose with RT_LAN=1; any other http address is still refused.
const LAN = process.env.RT_LAN === '1' && /^http:\/\/(10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+):\d+$/.test(url);
if (LAN) { console.log(`\x1b[32m✔\x1b[0m Office Wi-Fi build, API base: ${url} (phones on the same network only)`); process.exit(0); }
if (/localhost|127\.0\.0\.1|0\.0\.0\.0/i.test(url)) die(`VITE_API_BASE is "${url}" - on a phone, localhost is the PHONE, not your server.`);
if (!/^https:\/\//i.test(url)) die(`VITE_API_BASE is "${url}" - iOS requires HTTPS (App Transport Security).`);
if (/trycloudflare\.com/i.test(url)) {
  console.warn(`\n\x1b[33m⚠ Warning:\x1b[0m "${url}" is a TEMPORARY Cloudflare quick-tunnel.`);
  console.warn('  Its URL changes on every restart - the shipped app will break and need a rebuild.');
  console.warn('  Use a NAMED tunnel on your own domain for anything you distribute.\n');
}
console.log(`\x1b[32m✔\x1b[0m Mobile build API base: ${url}`);
