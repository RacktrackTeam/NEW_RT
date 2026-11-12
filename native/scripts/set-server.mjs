// npm run server -- http://10.0.0.5:8600   writes the server the app opens first (www/config.js)
import { writeFileSync } from 'node:fs';
const url = (process.argv[2] || '').replace(/\/+$/, '');
if (!/^https?:\/\/[^/]+$/.test(url)) { console.error('usage: npm run server -- http://<mac-ip>:8700'); process.exit(1); }
writeFileSync(new URL('../www/config.js', import.meta.url), `// The server the app opens first. Written by \`npm run server -- http://<mac-ip>:8700\`; the user can change it in the app.\nwindow.RT_DEFAULT_SERVER = '${url}';\n`);
console.log('default server:', url);
