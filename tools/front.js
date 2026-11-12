#!/usr/bin/env node
/* One front door for testers outside the network: tools/front.js [port]  (default 8710)

   The app's screens live on the engine (8700) and sign in against the sign-in service (3302) on the same host, which sets
   the rt_access cookie for that host. A tunnel exposes one port, so this proxy puts both behind one address: the sign-in
   and organization paths go to the sign-in service, everything else to the engine, and /api/auth-service answers with
   this very address so the screens call the sign-in service through the same door. Then share the door:

       node tools/front.js            # listens on 127.0.0.1:8710
       ~/bin/ngrok http 8710          # or any tunnel to 8710; give testers the https address it prints

   The Host header is passed through unchanged, so the sign-in service's same-origin check sees the public address on both
   sides. No package is needed: Node's http module only. Stop it with Ctrl+C or by its pid (lsof -ti TCP:8710). */
'use strict';
const http = require('http');

const PORT = Number(process.argv[2] || process.env.FRONT_PORT || 8710);
const ENGINE = { host: '127.0.0.1', port: Number(process.env.RACKTRACK_PORT || 8700) };
const SIGNIN = { host: '127.0.0.1', port: Number(process.env.RT_AUTH_PORT || 3302) };

// paths the sign-in service owns (its routes and its organization pages); the engine keeps /api/auth/state and /api/auth-service
const SIGNIN_PREFIXES = ['/api/auth/', '/api/setup', '/api/scan-sites', '/api/invites', '/api/orgs', '/api/sites', '/api/my-org',
  '/api/admin', '/api/assets', '/api/dashboard', '/api/logs', '/api/support', '/org', '/healthz'];
const ENGINE_EXACT = new Set(['/api/auth/state', '/api/auth/']);

function target(url) {
  const path = url.split('?')[0];
  if (ENGINE_EXACT.has(path)) return ENGINE;
  for (const p of SIGNIN_PREFIXES) {
    if (path === p || path.startsWith(p.endsWith('/') ? p : p + '/') || (p === '/org' && path === '/org')) return SIGNIN;
  }
  return ENGINE;
}

const server = http.createServer((req, res) => {
  const path = req.url.split('?')[0];
  if (path === '/api/auth-service') {                 // the screens ask where the sign-in service is: through this door
    const proto = (req.headers['x-forwarded-proto'] || 'http').split(',')[0].trim();
    const host = req.headers['x-forwarded-host'] || req.headers.host;
    res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    res.end(JSON.stringify({ url: `${proto}://${host}`, port: SIGNIN.port, front: true }));
    return;
  }
  const to = target(req.url);
  const headers = Object.assign({}, req.headers);
  headers['x-forwarded-for'] = [req.headers['x-forwarded-for'], req.socket.remoteAddress].filter(Boolean).join(', ');
  if (!headers['x-forwarded-proto']) headers['x-forwarded-proto'] = 'http';
  const up = http.request({ host: to.host, port: to.port, method: req.method, path: req.url, headers }, (r) => {
    res.writeHead(r.statusCode, r.headers);
    r.pipe(res);
  });
  up.on('error', (e) => {
    if (!res.headersSent) res.writeHead(502, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: `the ${to === ENGINE ? 'engine' : 'sign-in service'} on port ${to.port} did not answer: ${e.code || e.message}` }));
  });
  req.pipe(up);
});
server.on('upgrade', (req, socket) => socket.destroy());   // no websockets behind this door
server.listen(PORT, '127.0.0.1', () => {
  console.log(`front door on http://127.0.0.1:${PORT} -> engine ${ENGINE.port}, sign-in ${SIGNIN.port}`);
});
