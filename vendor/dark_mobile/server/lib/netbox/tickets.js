/**
 * Raising a ticket in ServiceNow, and hearing back when somebody closes it.
 *
 * The existing ServiceNow connector writes CIs into the CMDB. This is the
 * other half: when a comparison finds something a person has to look at, an
 * incident is raised on the same instance, addressed to the rack's single
 * point of contact, and RackTrack keeps its own row pointing at it.
 *
 * Three rules the shape of this file exists to enforce.
 *
 *   IDEMPOTENT. A rack scanned monthly reports the same unresolved drift every
 *   month. Every incident carries a correlation id built from the rack and the
 *   thing that differs — not from the scan or the date — so the second scan
 *   finds the first incident and adds a comment to it instead of raising a
 *   duplicate. By month three the assignee has one ageing ticket, not four.
 *
 *   OURS FIRST. The row in the plan is the record. ServiceNow is a mirror of
 *   it. If the instance is unreachable, or no instance is configured at all,
 *   the ticket still exists here and the person is still named — it simply has
 *   no external number yet.
 *
 *   STATE FOLLOWS THE CHECK. RackTrack sets state only as the outcome of its
 *   own check; a state somebody else set is read back and never argued with.
 *
 * The row shaping is pure, so it can be tested without an instance.
 */

const norm = (s) => String(s ?? '').trim();

/** ServiceNow incident states, as the Table API reports them. */
const STATE = {
  1: 'new', 2: 'in progress', 3: 'on hold',
  6: 'resolved', 7: 'closed', 8: 'cancelled',
};
const CLOSED_STATES = new Set(['resolved', 'closed', 'cancelled']);

/**
 * A stable id for "this problem, in this rack, about this thing".
 *
 * Deliberately free of the scan id and the date. Those change every month and
 * would make every scan look like a new problem.
 */
const correlationFor = (rackId, uid) => `racktrack:${norm(rackId)}:${norm(uid)}`;

/** How urgent, from what the item is. Nothing here is a guess about business impact. */
function urgencyFor(item) {
  if (item.type === 'Device' && item.action === 'create') return 2;   // something is racked that we do not know about
  if (item.type === 'Device' && item.action === 'update') return 2;   // something moved or was replaced
  return 3;
}

/** Plain words for what differs, so the person reading the ticket can act. */
function describe(item) {
  if (item.action === 'create') {
    return `${item.type} "${item.name}" was found in the rack and is not in NetBox.`;
  }
  if (item.action === 'update' && item.diff) {
    const lines = Object.entries(item.diff).map(
      ([field, v]) => `  ${field}: NetBox says ${JSON.stringify(v.from)}, we saw ${JSON.stringify(v.to)}`);
    return `${item.type} "${item.name}" does not match NetBox.\n\n${lines.join('\n')}`;
  }
  return `${item.type} "${item.name}" needs checking. ${norm(item.reason)}`;
}

/**
 * A plan item plus its context -> the incident fields ServiceNow wants.
 *
 * `assigned_to` is the SPOC's email. ServiceNow resolves an email to a user
 * record itself; sending a name would match nothing on most instances.
 */
function toIncident({ item, rackId, rackName, siteName, spoc, question, planId, appUrl }) {
  const where = [rackName || rackId, siteName].filter(Boolean).join(', ');
  const body = [
    describe(item),
    '',
    `Rack: ${where}`,
    `Found by: a RackTrack scan of the rack`,
    question ? `\nThe admin asks: ${question}` : '',
    appUrl ? `\nOpen it in RackTrack: ${appUrl}` : '',
    '',
    'Nothing has been written to NetBox. This is waiting on somebody looking at the rack.',
  ].filter((l) => l !== '').join('\n');

  return {
    correlation_id: correlationFor(rackId, item.uid),
    correlation_display: 'RackTrack',
    short_description: `${where}: ${item.type} "${item.name}" does not match NetBox`,
    description: body,
    category: 'inquiry',
    urgency: urgencyFor(item),
    impact: 3,
    contact_type: 'integration',
    ...(spoc && spoc.email ? { assigned_to: spoc.email } : {}),
    ...(planId ? { u_racktrack_plan: String(planId) } : {}),
  };
}

const https = require('https');
const dns = require('dns');
const { URL } = require('url');
const { logger } = require('../observability');

// host -> IPv4, resolved once. getaddrinfo runs on the libuv threadpool, which
// the server's other outbound calls saturate; a resolved-once cache plus a
// synchronous lookup keeps ServiceNow calls off it entirely.
const ipCache = new Map();

// Seed the cache from /etc/hosts at load time — synchronously, no threadpool.
// The demo pins the instance there via extra_hosts, so this alone resolves it
// and getaddrinfo is never called for the ServiceNow host under load.
(function seedFromHostsFile() {
  try {
    const fsMod = require('fs');
    for (const line of fsMod.readFileSync('/etc/hosts', 'utf8').split('\n')) {
      const t = line.replace(/#.*/, '').trim().split(/\s+/);
      const ip = t[0];
      if (!/^\d+\.\d+\.\d+\.\d+$/.test(ip)) continue;
      for (const name of t.slice(1)) if (name) ipCache.set(name, ip);
    }
  } catch { /* no hosts file, or unreadable — fall back to dns.lookup */ }
})();

function primeIp(hostname) {
  if (ipCache.has(hostname)) return Promise.resolve(ipCache.get(hostname));
  return new Promise((resolve) => {
    dns.lookup(hostname, { family: 4 }, (err, address) => {
      if (!err && address) ipCache.set(hostname, address);
      resolve(ipCache.get(hostname) || null);
    });
  });
}
// A lookup() for https.request: answer from cache with no threadpool hop; fall
// back to the real resolver only if we have not primed the host yet.
function cachedLookup(hostname, options, cb) {
  const ip = ipCache.get(hostname);
  if (!ip) { dns.lookup(hostname, options, cb); return; }
  // https.request calls lookup with { all: true }, which expects an array of
  // {address, family}; the plain form expects (err, address, family). Answer in
  // whichever shape the caller asked for, or the socket gets undefined.
  const opts = options && typeof options === 'object' ? options : {};
  process.nextTick(() => {
    if (opts.all) cb(null, [{ address: ip, family: 4 }]);
    else cb(null, ip, 4);
  });
}

/**
 * A fetch() work-alike over Node's https module, for ServiceNow only.
 *
 * Why not global fetch: on the demo VPS, undici bypassed /etc/hosts and leaned
 * on Docker's embedded DNS, which drops lookups under the server's concurrent
 * load — every ServiceNow call then failed with EAI_AGAIN or ENOTFOUND, while a
 * bare process in the same container always worked. The https module resolves
 * through dns.lookup (which honours the pinned host) and, with keep-alive off,
 * never reuses a stale socket. Returns the small slice of the fetch interface
 * this file uses: status, ok, text(), and headers.getSetCookie().
 */
function httpsFetch(urlStr, opts = {}) {
  const u = new URL(urlStr);
  const body = opts.body;
  return new Promise((resolve, reject) => {
    const req = https.request({
      hostname: u.hostname, port: u.port || 443, path: u.pathname + u.search,
      method: opts.method || 'GET', headers: opts.headers || {},
      agent: new https.Agent({ keepAlive: false }),
      lookup: cachedLookup,
      timeout: 12000,
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        const setCookie = res.headers['set-cookie'] || [];
        resolve({
          status: res.statusCode, ok: res.statusCode >= 200 && res.statusCode < 300,
          headers: { get: (k) => res.headers[String(k).toLowerCase()],
                     getSetCookie: () => setCookie },
          text: async () => text,
        });
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(new Error('request timed out')); });
    if (body) req.write(body);
    req.end();
  });
}

const origin = (cfg) => String(cfg.instanceUrl || '').replace(/\/+$/, '');
const base = (cfg, table) => `${origin(cfg)}/api/now/table/${table || 'incident'}`;

/*
 * HOW WE AUTHENTICATE, AND WHY IT IS NOT THE OBVIOUS WAY.
 *
 * Every integration guide says: send `Authorization: Basic user:password`.
 * ServiceNow's Zurich release (2025) refuses that for REST on developer
 * instances — every call returns 401 "User is not authenticated", for every
 * user, every role, every password, even though the same credentials sign in
 * fine through a browser. It cost an afternoon to find out that the message
 * means "not this way", not "wrong password".
 *
 * So we sign in the way the browser does: POST the login form, keep the
 * cookies it sets, and send them with the CSRF token (`X-UserToken`) on every
 * REST call. That path is open on every release, and it is what a person's
 * browser is doing anyway. Sessions are cached per instance+user and rebuilt
 * on a 401, so a login happens once, not once per call.
 */
const sessions = new Map();

/** Cookies from a response, folded into a Cookie header we can send back. */
function harvestCookies(res, jar) {
  const set = typeof res.headers.getSetCookie === 'function'
    ? res.headers.getSetCookie()
    : [res.headers.get('set-cookie')].filter(Boolean);
  for (const line of set) {
    const [pair] = String(line).split(';');
    const eq = pair.indexOf('=');
    if (eq > 0) jar.set(pair.slice(0, eq).trim(), pair.slice(eq + 1).trim());
  }
}
const cookieHeader = (jar) => [...jar.entries()].map(([k, v]) => `${k}=${v}`).join('; ');

/** Sign in as the browser does. Returns {cookie, token} or throws with the reason. */
/** fetch with a bounded time and one retry on a dropped connection. */
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function tryFetch(fetchImpl, url, opts, attempts = 4) {
  let last;
  for (let i = 0; i < attempts; i += 1) {
    try {
      return await fetchImpl(url, { signal: AbortSignal.timeout(12000), ...opts });
    } catch (err) {
      last = err;
      // A dropped socket or a transient DNS failure (EAI_AGAIN, common under
      // Docker's embedded resolver) usually clears on a short pause.
      if (i < attempts - 1) await sleep(250 * (i + 1));
    }
  }
  throw last;
}

async function login(cfg, fetchImpl = httpsFetch) {
  await primeIp(new URL(origin(cfg)).hostname);
  const jar = new Map();
  const ua = { 'User-Agent': 'RackTrack/1.0', Accept: 'text/html,application/json', Connection: 'close' };
  const timed = (url, opts) => tryFetch(fetchImpl, url, opts);

  const page = await timed(`${origin(cfg)}/login.do`, { headers: ua, redirect: 'follow' });
  harvestCookies(page, jar);
  const html = await page.text();
  const ck = html.match(/name="sysparm_ck"\s+value="([^"]+)"/);

  const form = new URLSearchParams({
    user_name: cfg.username || '', user_password: cfg.password || '',
    sys_action: 'sysverb_login', ...(ck ? { sysparm_ck: ck[1] } : {}),
  });
  const post = await timed(`${origin(cfg)}/login.do`, {
    method: 'POST', redirect: 'manual',
    headers: { ...ua, 'Content-Type': 'application/x-www-form-urlencoded', Cookie: cookieHeader(jar) },
    body: form.toString(),
  });
  harvestCookies(post, jar);

  // A successful login sets glide_user_route; a failed one bounces to login.do
  // with the same pre-auth cookies and nothing else.
  if (!jar.has('glide_user_route')) {
    throw new Error('ServiceNow refused the sign-in: check the instance, user and password');
  }

  // The CSRF token lives on the page after login. Fetch a small one to read it.
  const after = await timed(`${origin(cfg)}/login_redirect.do?sysparm_stack=no`, {
    headers: { ...ua, Cookie: cookieHeader(jar) }, redirect: 'follow',
  });
  harvestCookies(after, jar);
  const body = await after.text();
  const tok = body.match(/g_ck\s*=\s*['"]([0-9a-f]{72})['"]/);
  return { cookie: cookieHeader(jar), token: tok ? tok[1] : null, at: Date.now() };
}

const sessionKey = (cfg) => `${origin(cfg)}|${cfg.username || ''}`;

async function session(cfg, fetchImpl = httpsFetch, fresh = false) {
  const key = sessionKey(cfg);
  if (!fresh && sessions.has(key)) return sessions.get(key);
  const s = await login(cfg, fetchImpl);
  sessions.set(key, s);
  return s;
}

const headers = (cfg, s) => ({
  'Content-Type': 'application/json',
  Accept: 'application/json',
  Connection: 'close',
  Cookie: s.cookie,
  ...(s.token ? { 'X-UserToken': s.token } : {}),
});

/**
 * One REST call, signed with a session. On a 401 the session is rebuilt once
 * and the call retried, so an expired login heals itself.
 */
async function req(url, method, _hdrsUnused, body, timeoutMs = 15000, cfg = null, fetchImpl = httpsFetch) {
  const doCall = async (s) => {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await tryFetch(fetchImpl, url, {
        method, headers: headers(cfg, s), body: body ? JSON.stringify(body) : undefined,
        signal: ctrl.signal,
      });
      const text = await res.text();
      let parsed;
      try { parsed = text ? JSON.parse(text) : {}; } catch { parsed = text.slice(0, 300); }
      return { status: res.status, ok: res.ok, body: parsed };
    } finally {
      clearTimeout(timer);
    }
  };
  if (!cfg) throw new Error('req() needs the ServiceNow config to sign the call');
  let s = await session(cfg, fetchImpl);
  let out = await doCall(s);
  // 401 or 403 means the session is no good. Drop it and log in fresh, once.
  if (out.status === 401 || out.status === 403) {
    sessions.delete(sessionKey(cfg));
    s = await session(cfg, fetchImpl, true);
    out = await doCall(s);
  }
  // A poisoned session must never persist: any failure clears it, so the next
  // request starts clean rather than reusing a login that no longer works.
  if (!out.ok) sessions.delete(sessionKey(cfg));
  return out;
}

/** Find an incident we raised before for exactly this problem. */
async function findExisting(cfg, correlationId, fetchImpl = req) {
  const url = `${base(cfg, cfg.incidentTable)}`
    + `?sysparm_query=correlation_id=${encodeURIComponent(correlationId)}`
    + '&sysparm_limit=1&sysparm_display_value=false';
  const r = await fetchImpl(url, 'GET', null, undefined, 15000, cfg);
  if (!r.ok) return { ok: false, status: r.status, error: r.body };
  const row = (r.body && r.body.result && r.body.result[0]) || null;
  return { ok: true, incident: row };
}

/**
 * Raise it, or add to the one already open.
 *
 * Returns the same shape either way, with `reused` saying which happened, so
 * the caller can tell a person "this was first raised in March" rather than
 * pretending it is new.
 */
async function raise(cfg, ctx, fetchImpl = req) {
  return raiseFields(cfg, () => toIncident(ctx), fetchImpl);
}

/**
 * The same, for incident fields already built. A task ticket builds its own
 * (toTaskIncident) and used to be handed to raise(), which read them as a
 * drift item and threw on item.action - so no task ticket ever reached
 * ServiceNow (found 24 September 2026).
 */
async function raiseFields(cfg, fieldsOf, fetchImpl = req) {
  try {
    return await raiseInner(cfg, typeof fieldsOf === 'function' ? fieldsOf() : fieldsOf, fetchImpl);
  } catch (err) {
    // A login or a network failure must not take down the whole decision.
    // The ticket still exists in RackTrack; it simply has no incident yet.
    const cause = err && err.cause ? ` (${err.cause.code || err.cause.message})` : '';
    return { ok: false, status: 0, error: `could not reach ServiceNow: ${err.message}${cause}` };
  }
}

async function raiseInner(cfg, fields, fetchImpl = req) {
  const found = await findExisting(cfg, fields.correlation_id, fetchImpl);
  if (!found.ok) return { ok: false, error: found.error, status: found.status };

  if (found.incident) {
    const sysId = found.incident.sys_id;
    const state = STATE[Number(found.incident.state)] || 'unknown';
    // Already closed, and the problem is back: reopen rather than duplicate.
    const patch = CLOSED_STATES.has(state)
      ? { state: 2, work_notes: 'Seen again on a later RackTrack scan. Reopened.' }
      : { work_notes: `Seen again on a later RackTrack scan.\n\n${fields.description}` };
    const r = await fetchImpl(`${base(cfg, cfg.incidentTable)}/${sysId}`, 'PATCH', null, patch, 15000, cfg);
    if (!r.ok) return { ok: false, status: r.status, error: r.body };
    const row = (r.body && r.body.result) || found.incident;
    return {
      ok: true, reused: true, reopened: CLOSED_STATES.has(state),
      sysId, number: row.number || found.incident.number,
      state: STATE[Number(row.state)] || state,
      url: `${String(cfg.instanceUrl).replace(/\/+$/, '')}/nav_to.do?uri=incident.do?sys_id=${sysId}`,
    };
  }

  const r = await fetchImpl(base(cfg, cfg.incidentTable), 'POST', null, fields, 15000, cfg);
  if (!r.ok) return { ok: false, status: r.status, error: r.body };
  const row = (r.body && r.body.result) || {};
  return {
    ok: true, reused: false, reopened: false,
    sysId: row.sys_id, number: row.number,
    state: STATE[Number(row.state)] || 'new',
    url: row.sys_id
      ? `${String(cfg.instanceUrl).replace(/\/+$/, '')}/nav_to.do?uri=incident.do?sys_id=${row.sys_id}`
      : null,
  };
}

/**
 * Ask ServiceNow what happened to the incidents we raised.
 *
 * This is how the loop closes: somebody updates the ticket in their own tool,
 * and RackTrack notices. State is theirs — we report it, we do not set it.
 */
async function statusOf(cfg, sysIds, fetchImpl = req) {
  const ids = (sysIds || []).filter(Boolean);
  if (!ids.length) return { ok: true, states: {} };
  try {
    return await statusOfInner(cfg, ids, fetchImpl);
  } catch (err) {
    return { ok: false, status: 0, error: `could not reach ServiceNow: ${err.message}` };
  }
}

async function statusOfInner(cfg, ids, fetchImpl = req) {
  const url = `${base(cfg, cfg.incidentTable)}`
    + `?sysparm_query=sys_idIN${ids.join(',')}`
    + '&sysparm_fields=sys_id,number,state,close_notes,resolved_at,assigned_to'
    + `&sysparm_limit=${ids.length}`;
  const r = await fetchImpl(url, 'GET', null, undefined, 15000, cfg);
  if (!r.ok) return { ok: false, status: r.status, error: r.body };

  const states = {};
  for (const row of (r.body && r.body.result) || []) {
    const state = STATE[Number(row.state)] || 'unknown';
    states[row.sys_id] = {
      number: row.number,
      state,
      closed: CLOSED_STATES.has(state),
      notes: norm(row.close_notes) || null,
      resolvedAt: norm(row.resolved_at) || null,
    };
  }
  return { ok: true, states };
}

/*
 * ONE INCIDENT FOR A WHOLE CHECK.
 *
 * Everything above raises an incident per thing that differs, and stays as it
 * is for the checks that were filed that way. From here down the incident
 * belongs to the check: it is raised once when the check is sent, in the name
 * of the person who holds it, and RackTrack moves it as its own check moves.
 *
 * Every function below answers { ok, ... } and never throws, so a ServiceNow
 * that is slow, down or refusing never takes the check down with it.
 */

/** The id of one check. A later check of the same rack gets its own, on purpose. */
const correlationForCheck = (planId) => `racktrack:check:${norm(planId)}`;

/** The states RackTrack may set, by the word the rest of the app uses. */
const STATE_CODE = { in_progress: 2, on_hold: 3, resolved: 6, closed: 7, cancelled: 8 };

// RackTrack's own keys, and the hash a rack carries before anybody has named
// it. Neither means anything to the person reading the incident.
const OWN_FIELDS = new Set(['racktrack_uid', 'racktrack_bound', 'recordId']);
const PHOTO_HASH = /^RK-[0-9A-F]{6,}$/i;
// An incident description holds 4000 characters on a stock instance. The
// drift report is attached with the full list.
const LISTED = 20;

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
const sentOn = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toUTCString().replace(' GMT', ' UTC');
};

/** The item as a person would name it: no keys of ours, no rack name on the end. */
function readable(item, rackName) {
  let name = norm(item.name);
  if (rackName && name.endsWith(rackName)) name = name.slice(0, -rackName.length).trim();
  name = name.replace(/\s+RK-[0-9A-F]{6,}$/i, '').trim() || norm(item.name);
  const diff = item.diff
    ? Object.fromEntries(Object.entries(item.diff).filter(([field]) => !OWN_FIELDS.has(field)))
    : item.diff;
  return { ...item, name, diff };
}

/** Where the check opens in the RackTrack Control, or null when no public address is set. */
function appUrlFor(planId) {
  const root = norm(process.env.PUBLIC_BASE_URL).replace(/\/+$/, '');
  return root ? `${root}/approvals/drifts/${norm(planId)}` : null;
}

/**
 * A sent check plus its context -> the incident fields ServiceNow wants.
 *
 * Pure, and it names nobody in `assigned_to`: who that is on the instance has
 * to be looked up there first, which is raiseCheck's job.
 */
function toCheckIncident({ plan, items, rackName, siteName, holder, sender, note, appUrl }) {
  const named = norm(rackName) && !PHOTO_HASH.test(norm(rackName)) ? norm(rackName) : null;
  const where = [named || 'Unidentified rack', siteName].filter(Boolean).join(', ');
  // RackTrack putting its own tag on a record is housekeeping, not a difference.
  const sent = (items || []).filter((i) => !(i.action === 'rebind' && !i.fromUid));
  const planId = plan && plan.id;

  const who = norm(sender && (sender.username || sender.name)) || norm(plan && plan.submittedBy);
  const when = sentOn(plan && plan.submittedAt);
  const heldBy = holder ? norm(holder.username || holder.name) : '';
  const said = norm(note) || norm(plan && plan.submittedNote);

  const listed = sent.slice(0, LISTED)
    .map((i, n) => `${n + 1}. ${describe(readable(i, named)).trim().replace(/\n{2,}/g, '\n')}`);
  if (sent.length > LISTED) {
    listed.push(`And ${sent.length - LISTED} more. The attached drift report lists every one.`);
  }

  const body = [
    [
      `Rack: ${where}`,
      `Found by: a RackTrack scan of the rack, check ${planId}`,
      who ? `Sent by: ${who}${when ? ` on ${when}` : ''}` : '',
      heldBy ? `With: ${heldBy}${holder.email ? ` (${holder.email})` : ''}` : '',
    ].filter(Boolean).join('\n'),
    ...listed,
    said ? `Note from the technician: ${said}` : '',
    appUrl ? `Open it in RackTrack: ${appUrl}` : '',
    'Nothing has been written to NetBox. The SPOC reviews this check and approves, rejects or changes each difference.',
  ].filter(Boolean).join('\n\n');

  return {
    correlation_id: correlationForCheck(planId),
    correlation_display: 'RackTrack',
    short_description: `${where}: ${plural(sent.length, 'difference')} from NetBox - RackTrack check ${planId}`,
    description: body,
    category: 'inquiry',
    urgency: sent.reduce((least, i) => Math.min(least, urgencyFor(i)), 3),
    impact: 3,
    contact_type: 'integration',
  };
}

/**
 * A ticket an admin raises by hand, and gives to somebody.
 *
 * The check's own incident (toCheckIncident) is raised by RackTrack when a
 * drift is sent, and it says what the photograph and the records disagree
 * about. This is the other kind: an admin looking at a check decides a person
 * should go and look at something, writes what they want done, and hands it
 * over. The owner asked for it on 23 September 2026 - "raise a ticket from
 * RackTrack to their ticketing tool, assign it to a technician or a SPOC, and
 * they will look into it".
 *
 * It carries the rack and the check it came from, so whoever opens it in
 * ServiceNow can find their way back.
 */
function toTaskIncident({ summary, note, rackName, siteName, planId, raisedBy, appUrl, key }) {
  const named = norm(rackName) && !PHOTO_HASH.test(norm(rackName)) ? norm(rackName) : null;
  const where = [named || 'Unidentified rack', siteName].filter(Boolean).join(', ');
  const body = [
    [
      `Rack: ${where}`,
      planId ? `From RackTrack check ${planId}` : '',
      raisedBy ? `Raised by: ${raisedBy}` : '',
    ].filter(Boolean).join('\n'),
    norm(note) || '',
    appUrl ? `Open the check in RackTrack: ${appUrl}` : '',
  ].filter(Boolean).join('\n\n');

  return {
    // Its own correlation, so raising two tasks on one check does not make
    // the second a duplicate of the first.
    correlation_id: `racktrack-task-${key}`,
    correlation_display: 'RackTrack',
    short_description: `${norm(summary) || 'Look at this rack'} - ${where}`,
    description: body,
    category: 'inquiry',
    urgency: 3,
    impact: 3,
    contact_type: 'integration',
  };
}

/**
 * Raise that ticket and give it to one person.
 *
 * The person is made on the instance if they are not there yet, the same way
 * a check's holder is: a ticket assigned to nobody is a ticket nobody works.
 */
async function raiseTask(cfg, ctx, fetchImpl = req) {
  try {
    const fields = toTaskIncident(ctx);
    const to = ctx.assignee || null;
    const users = await findUsers(cfg, [to && to.email], fetchImpl);
    const { user, why } = await resolveUser(cfg, users, to, fetchImpl);
    const made = await raiseFields(cfg, { ...fields, ...(user ? { assigned_to: user.sysId } : {}) }, fetchImpl);
    if (!made.ok) return made;
    return {
      ok: true,
      sysId: made.sysId, number: made.number, url: made.url,
      state: made.state || 'new',
      assigned: Boolean(user),
      assignedTo: user ? { sysId: user.sysId, name: user.name } : null,
      assignWarning: user ? null : why,
    };
  } catch (err) {
    return unreachable(err);
  }
}

/**
 * The open incidents assigned to one person.
 *
 * The other half of the ticket workflow (the owner, 23 September 2026): a
 * ticket raised in ServiceNow itself, by whoever raises tickets there, and
 * given to one of our technicians. RackTrack has to find it to be able to
 * send that person to the rack.
 *
 * Only the open ones, newest first, and only the fields a decision is made
 * from - the words of it above all, because the rack is named in them.
 */
async function assignedTo(cfg, who, fetchImpl = req) {
  const sysId = norm(who && who.sysId);
  const email = norm(who && who.email);
  try {
    let id = sysId;
    if (!id && email) {
      const found = await findUsers(cfg, [email], fetchImpl);
      const rows = (found && found[email.toLowerCase()]) || [];
      id = rows.length === 1 ? rows[0].sysId : null;
    }
    if (!id) return { ok: true, incidents: [], why: 'nobody on the instance matches that address' };

    // Open, in ServiceNow's own terms: anything before Resolved.
    const query = `assigned_to=${id}^state<6^ORDERBYDESCopened_at`;
    const url = `${base(cfg, cfg.incidentTable)}`
      + `?sysparm_query=${encodeURIComponent(query)}`
      + '&sysparm_fields=sys_id,number,state,short_description,description,opened_at,correlation_id'
      + '&sysparm_limit=25&sysparm_display_value=false';
    const r = await fetchImpl(url, 'GET', null, undefined, 15000, cfg);
    if (!r.ok) return { ok: false, status: r.status, error: refusal(r), incidents: [] };
    const rows = (r.body && r.body.result) || [];
    return {
      ok: true,
      incidents: rows.map((row) => ({
        sysId: row.sys_id,
        number: row.number,
        state: STATE[Number(row.state)] || 'unknown',
        summary: norm(row.short_description),
        description: norm(row.description),
        openedAt: norm(row.opened_at) || null,
        correlationId: norm(row.correlation_id) || null,
        url: urlOf(cfg, row.sys_id),
      })),
    };
  } catch (err) {
    return { ...unreachable(err), incidents: [] };
  }
}

/** What ServiceNow said, as one line a person can be shown. */
function refusal(r) {
  const said = r && r.body && r.body.error && (r.body.error.message || r.body.error.detail);
  const line = `ServiceNow replied ${(r && r.status) || 'nothing'}`;
  return said ? `${line}: ${norm(said).slice(0, 200)}` : line;
}
const failed = (r) => ({ ok: false, status: (r && r.status) || 0, error: refusal(r), detail: (r && r.body) ?? null });
const unreachable = (err) => {
  const cause = err && err.cause ? ` (${err.cause.code || err.cause.message})` : '';
  return { ok: false, status: 0, error: `could not reach ServiceNow: ${err && err.message}${cause}`, detail: null };
};
const urlOf = (cfg, sysId) => (sysId ? `${origin(cfg)}/nav_to.do?uri=incident.do?sys_id=${sysId}` : null);
// A reference comes back as { link, value } or as the bare sys_id, and as ''
// when it is empty.
const refOf = (v) => norm(v && typeof v === 'object' ? v.value : v) || null;

/**
 * The ServiceNow users behind a list of emails.
 *
 * Every email asked about is a key in the answer, with no, one or several
 * users under it, so the caller can tell "nobody" from "cannot say which".
 */
async function findUsers(cfg, emails, fetchImpl = req) {
  // A comma or a caret would be read as part of the query, not of the address.
  const asked = [...new Set((emails || []).map((e) => norm(e).toLowerCase()))]
    .filter((e) => e && !/[,^\s]/.test(e));
  const byEmail = Object.fromEntries(asked.map((e) => [e, []]));
  if (!asked.length) return { ok: true, byEmail };
  try {
    const url = `${base(cfg, 'sys_user')}`
      + `?sysparm_query=${encodeURIComponent(`emailIN${asked.join(',')}^active=true`)}`
      + '&sysparm_fields=sys_id,name,email&sysparm_limit=10';
    const r = await fetchImpl(url, 'GET', null, undefined, 15000, cfg);
    if (!r.ok) return { ...failed(r), byEmail };
    for (const row of (r.body && r.body.result) || []) {
      const email = norm(row.email).toLowerCase();
      if (byEmail[email] && row.sys_id) byEmail[email].push({ sysId: row.sys_id, name: norm(row.name) || null });
    }
    return { ok: true, byEmail };
  } catch (err) {
    return { ...unreachable(err), byEmail };
  }
}

/**
 * The ServiceNow user an email stands for, made if there is none.
 *
 * A check goes to the single point of contact of a Site, and an incident that
 * names nobody is an incident nobody picks up. The instance often has no user
 * for that person - a new SPOC, a demo instance, a customer who keeps their
 * directory somewhere else - and until 22 September 2026 RackTrack raised the
 * incident unassigned and said so in a warning. The owner's direction that
 * day: there should be no such case; make the user.
 *
 * So the record is created in sys_user with the email as its identity and the
 * person's own name on it. Nothing else is set: no password, no roles, no
 * groups - it is a person to address an incident to, not an account to sign
 * in with, and giving it anything more would be RackTrack deciding who may do
 * what inside somebody else's instance.
 *
 * A refusal is not an error worth failing a send over. The incident is still
 * raised, and the caller still gets the old sentence saying nobody holds it.
 */
async function makeUser(cfg, person, fetchImpl = req) {
  const email = norm(person && person.email).toLowerCase();
  if (!email) return { user: null, why: 'no email address' };
  // A username to show beside the incident. RackTrack's own username where
  // there is one, the email's local part otherwise: a person reading the
  // incident should see a name they recognise.
  const userName = norm(person && person.username) || email.split('@')[0];
  const full = norm(person && person.name) || userName;
  const bits = full.split(/[\s._-]+/).filter(Boolean);
  const first = bits[0] ? bits[0][0].toUpperCase() + bits[0].slice(1) : userName;
  const last = bits.length > 1 ? bits.slice(1).map((b) => b[0].toUpperCase() + b.slice(1)).join(' ') : '';
  try {
    const r = await fetchImpl(base(cfg, 'sys_user'), 'POST', null, {
      user_name: userName,
      email,
      first_name: first,
      ...(last ? { last_name: last } : {}),
      // Where it came from, so an operator looking at a user they did not
      // create knows who did and why.
      source: 'racktrack',
    }, 15000, cfg);
    if (!r.ok) return { user: null, why: `ServiceNow would not create a user for ${email} (${(failed(r) || {}).error || r.status})` };
    const row = (r.body && r.body.result) || {};
    if (!row.sys_id) return { user: null, why: `ServiceNow created no user for ${email}` };
    logger.info({ event: 'servicenow.user.created', email, sysId: row.sys_id },
      `created a ServiceNow user for ${email}`);
    return { user: { sysId: row.sys_id, name: norm(row.name) || full }, why: null };
  } catch (err) {
    return { user: null, why: `ServiceNow could not be reached to create a user for ${email} (${err.message})` };
  }
}

/**
 * The one user an email stands for. A caller that can wait makes the user
 * when the instance has none - see resolveUser below, which is what the
 * incident path uses; this stays synchronous for everything that only looks.
 */
function oneUser(found, person) {
  const email = norm(person && person.email).toLowerCase();
  const nobody = 'so the incident is not assigned to anybody.';
  if (!email) {
    return { user: null, why: `${norm(person && (person.username || person.name)) || 'The SPOC'} has no email address in RackTrack, ${nobody}` };
  }
  if (!found.ok) return { user: null, why: `RackTrack could not look the SPOC up in ServiceNow (${found.error}), ${nobody}` };
  const users = found.byEmail[email] || [];
  if (users.length === 1) return { user: users[0], why: null };
  return { user: null, why: `${users.length ? 'More than one' : 'No'} ServiceNow user has the email ${email}, ${nobody}` };
}

/**
 * Who to assign an incident to: the user the email names, or a new one.
 *
 * One place, so every path that assigns an incident behaves the same way and
 * the rule "there is no such thing as no ServiceNow user" is written once.
 */
async function resolveUser(cfg, found, person, fetchImpl = req) {
  const first = oneUser(found, person);
  if (first.user) return first;
  // More than one user with that email is ambiguous, not missing: making
  // another would make it worse.
  const email = norm(person && person.email).toLowerCase();
  const many = email && ((found.byEmail || {})[email] || []).length > 1;
  if (!email || many || !found.ok) return first;
  const made = await makeUser(cfg, person, fetchImpl);
  if (made.user) return made;
  return { user: null, why: `${made.why}, so the incident is not assigned to anybody.` };
}

/**
 * Raise the incident of a check, in its holder's name.
 *
 * Asked for this check's correlation id only. A send that is tried again finds
 * the incident the first try raised and answers with it; an older check of the
 * same rack has another id, so its incident is never picked up again.
 *
 * Nobody to assign it to is not a failure: the incident is still raised, and
 * the answer says `assigned: false` with the reason in words.
 */
async function raiseCheck(cfg, ctx, fetchImpl = req) {
  try {
    return await raiseCheckInner(cfg, ctx, fetchImpl);
  } catch (err) {
    return unreachable(err);
  }
}

async function raiseCheckInner(cfg, ctx, fetchImpl = req) {
  const planId = ctx.plan && ctx.plan.id;
  const fields = toCheckIncident({ ...ctx, appUrl: ctx.appUrl !== undefined ? ctx.appUrl : appUrlFor(planId) });
  const found = await findExisting(cfg, fields.correlation_id, fetchImpl);
  if (!found.ok) return failed({ status: found.status, body: found.error });

  const holder = ctx.holder || null;
  const sender = ctx.sender || null;

  if (found.incident) {
    // The first try raised it and the answer was lost on the way back. It is
    // the same incident: no note, no reopening, its state left alone. Only an
    // assignee that is missing is put right.
    const row = found.incident;
    let to = refOf(row.assigned_to);
    let user = null;
    let why = null;
    if (!to) {
      ({ user, why } = await resolveUser(cfg, await findUsers(cfg, [holder && holder.email], fetchImpl), holder, fetchImpl));
      if (user) {
        const set = await update(cfg, row.sys_id, { assigned_to: user.sysId }, fetchImpl);
        if (set.ok) to = user.sysId;
        else why = `ServiceNow would not assign it (${set.error}), so the incident is not assigned to anybody.`;
      }
    }
    return {
      ok: true, reused: true,
      sysId: row.sys_id, number: row.number,
      state: STATE[Number(row.state)] || 'unknown',
      url: urlOf(cfg, row.sys_id),
      assigned: !!to,
      assignedTo: to ? { sysId: to, name: user ? user.name : null } : null,
      assignWarning: to ? null : why,
    };
  }

  const users = await findUsers(cfg, [holder && holder.email, sender && sender.email], fetchImpl);
  // The holder must end up with somebody: the user is made if the instance
  // has none. The caller is only ever an existing user - RackTrack does not
  // create a person to be the caller of a ticket.
  const assignee = await resolveUser(cfg, users, holder, fetchImpl);
  const caller = sender && sender.email ? oneUser(users, sender).user : null;

  const r = await fetchImpl(base(cfg, cfg.incidentTable), 'POST', null, {
    ...fields,
    ...(assignee.user ? { assigned_to: assignee.user.sysId } : {}),
    ...(caller ? { caller_id: caller.sysId } : {}),
  }, 15000, cfg);
  if (!r.ok) return failed(r);

  const row = (r.body && r.body.result) || {};
  // An instance may drop the assignee it was sent (a rule that wants a group
  // first, for one). What it kept is what counts.
  const dropped = assignee.user && 'assigned_to' in row && !refOf(row.assigned_to);
  const kept = assignee.user && !dropped ? assignee.user : null;
  return {
    ok: true, reused: false,
    sysId: row.sys_id, number: row.number,
    state: STATE[Number(row.state)] || 'new',
    url: urlOf(cfg, row.sys_id),
    assigned: !!kept,
    assignedTo: kept,
    assignWarning: dropped
      ? `ServiceNow did not keep ${assignee.user.name || 'the SPOC'} as the assignee, so the incident is not assigned to anybody.`
      : assignee.why,
  };
}

/**
 * Change an incident: PATCH the fields given, and say what it looks like now.
 *
 * This is the one door every later write goes through - a state, a work note,
 * a new assignee.
 */
async function update(cfg, sysId, fields, fetchImpl = req) {
  if (!norm(sysId)) return { ok: false, status: 0, state: null, number: null, error: 'There is no incident to update.' };
  try {
    const r = await fetchImpl(`${base(cfg, cfg.incidentTable)}/${norm(sysId)}`, 'PATCH', null, fields || {}, 15000, cfg);
    if (!r.ok) return { ...failed(r), state: null, number: null };
    const row = (r.body && r.body.result) || {};
    return { ok: true, status: r.status, state: STATE[Number(row.state)] || 'unknown', number: row.number || null, error: null };
  } catch (err) {
    return { ...unreachable(err), state: null, number: null };
  }
}

/*
 * THE FIELDS A STOCK INSTANCE WILL NOT LET GO EMPTY.
 *
 * Resolved and Closed want a close_code and close_notes; On Hold wants a
 * hold_reason. The close codes are a choice list that differs by release and
 * by customer, so none is written down here as the answer: the instance is
 * asked for its own list, once an hour at most.
 */
const CHOICES_TTL_MS = 60 * 60 * 1000;
const choiceCache = new Map();   // instance|table|element -> { at, choices }
const worked = new Map();        // instance|element -> the value the instance last took

// For an instance that will not show its choice lists to the integration user:
// the stock close codes, newest release first, and Awaiting Change, Awaiting Caller.
const STOCK = {
  close_code: ['Solution provided', 'Solved (Permanently)', 'Resolution confirmed', 'Solved (Work Around)'],
  hold_reason: ['5', '1'],
};

async function choices(cfg, element, fetchImpl = req) {
  const table = cfg.incidentTable || 'incident';
  const key = `${origin(cfg)}|${table}|${element}`;
  const hit = choiceCache.get(key);
  if (hit && Date.now() - hit.at < CHOICES_TTL_MS) return { ok: true, choices: hit.choices, cached: true };
  try {
    const url = `${base(cfg, 'sys_choice')}`
      + `?sysparm_query=${encodeURIComponent(`name=${table}^element=${element}^inactive=false^language=en`)}`
      + '&sysparm_fields=value,label,sequence';
    const r = await fetchImpl(url, 'GET', null, undefined, 15000, cfg);
    if (!r.ok) return { ...failed(r), choices: [] };
    const list = ((r.body && r.body.result) || [])
      .map((c) => ({ value: norm(c.value), label: norm(c.label), sequence: Number(c.sequence) || 0 }))
      .filter((c) => c.value)
      .sort((a, b) => a.sequence - b.sequence);
    // A list that comes back empty is an instance hiding it, not an instance
    // with no choices. It is not remembered, so the next call asks again.
    if (list.length) choiceCache.set(key, { at: Date.now(), choices: list });
    return { ok: true, choices: list, cached: false };
  } catch (err) {
    return { ...unreachable(err), choices: [] };
  }
}

/**
 * The value to send for a choice field, and the ones to try after it.
 *
 * From the instance's own list when it shows one: the admin's pick if it is on
 * the list, else the first that reads as wanted, else the first. When the list
 * is refused, the value that worked last time, then the stock ones.
 */
async function pick(cfg, element, configured, wanted, fetchImpl) {
  const c = await choices(cfg, element, fetchImpl);
  if (c.ok && c.choices.length) {
    const mine = configured && c.choices.find((x) => x.value === configured || x.label === configured);
    const best = mine || c.choices.find((x) => wanted.test(x.value) || wanted.test(x.label)) || c.choices[0];
    return { value: best.value, candidates: [best.value], source: mine ? 'profile' : 'instance' };
  }
  const known = worked.get(`${origin(cfg)}|${element}`);
  const candidates = [...new Set([known, configured, ...STOCK[element]].filter(Boolean))];
  return { value: candidates[0], candidates, source: known ? 'remembered' : 'stock' };
}
const pickCloseCode = (cfg, fetchImpl = req) => pick(cfg, 'close_code', norm(cfg.closeCode) || null,
  /solved|resolved|fixed|confirmed|permanent/i, fetchImpl);
const pickHoldReason = (cfg, fetchImpl = req) => pick(cfg, 'hold_reason', norm(cfg.holdReason) || null,
  /change/i, fetchImpl);

/**
 * Move an incident to one of STATE_CODE's states, with what that state needs.
 *
 * `notes` are the close notes of a Resolved or Closed incident and a work note
 * on any other. Where the value had to be guessed from the stock list, a 400
 * or a 403 moves on to the next guess, and the one the instance took is
 * remembered for it.
 */
async function setState(cfg, sysId, state, { notes, closeCode, holdReason } = {}, fetchImpl = req) {
  const code = STATE_CODE[state];
  const none = { state: null, number: null };
  if (!code) return { ok: false, status: 0, ...none, error: `RackTrack does not set an incident to "${norm(state)}".` };
  const said = norm(notes) || 'Updated from RackTrack.';

  const closing = state === 'resolved' || state === 'closed';
  const element = closing ? 'close_code' : state === 'on_hold' ? 'hold_reason' : null;
  if (!element) return update(cfg, sysId, { state: code, work_notes: said }, fetchImpl);

  const given = norm(closing ? closeCode : holdReason);
  const picked = given
    ? { candidates: [given] }
    : await (closing ? pickCloseCode : pickHoldReason)(cfg, fetchImpl);
  let out = null;
  for (const value of picked.candidates) {
    out = await update(cfg, sysId, closing
      ? { state: code, close_code: value, close_notes: said }
      : { state: code, hold_reason: value, work_notes: said }, fetchImpl);
    if (out.ok) {
      worked.set(`${origin(cfg)}|${element}`, value);
      return { ...out, [closing ? 'closeCode' : 'holdReason']: value };
    }
    if (out.status !== 400 && out.status !== 403) break;
  }
  return out;
}

/** A note on the incident that the people working it see and the caller does not. */
const workNote = (cfg, sysId, text, fetchImpl = req) => (norm(text)
  ? update(cfg, sysId, { work_notes: norm(text) }, fetchImpl)
  : Promise.resolve({ ok: false, status: 0, state: null, number: null, error: 'There is nothing to note.' }));

/**
 * Hand the incident to another ServiceNow user, saying so in a work note. An
 * incident that was already closed is put back In Progress with `reopen`,
 * because a closed one cannot be anybody's to work.
 */
const reassign = (cfg, sysId, userSysId, { note, reopen = false } = {}, fetchImpl = req) => (norm(userSysId)
  ? update(cfg, sysId, {
    assigned_to: norm(userSysId),
    ...(reopen ? { state: STATE_CODE.in_progress } : {}),
    ...(norm(note) ? { work_notes: norm(note) } : {}),
  }, fetchImpl)
  : Promise.resolve({ ok: false, status: 0, state: null, number: null, error: 'There is no ServiceNow user to assign it to.' }));

/**
 * One call whose body is a file, signed with the same session as req().
 *
 * req() turns every body into JSON, which is right for the Table API and
 * wrong for an upload: the attachment API wants the bytes as they are, under
 * the file's own Content-Type. The 401 and 403 handling is req()'s. A dropped
 * connection is not tried again here, because nothing says whether the file
 * landed and a second try could attach it twice.
 */
async function reqRaw(url, method, headersExtra, bodyBuffer, timeoutMs = 60000, cfg = null, fetchImpl = httpsFetch) {
  const bytes = Buffer.isBuffer(bodyBuffer) ? bodyBuffer : Buffer.from(bodyBuffer ?? '');
  const doCall = async (s) => {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await tryFetch(fetchImpl, url, {
        method, body: bytes, signal: ctrl.signal,
        headers: {
          ...headers(cfg, s), 'Content-Type': 'application/octet-stream', ...(headersExtra || {}),
          'Content-Length': String(bytes.length),
        },
      }, 1);
      const text = await res.text();
      let parsed;
      try { parsed = text ? JSON.parse(text) : {}; } catch { parsed = text.slice(0, 300); }
      return { status: res.status, ok: res.ok, body: parsed };
    } finally {
      clearTimeout(timer);
    }
  };
  if (!cfg) throw new Error('reqRaw() needs the ServiceNow config to sign the call');
  let s = await session(cfg, fetchImpl);
  let out = await doCall(s);
  if (out.status === 401 || out.status === 403) {
    sessions.delete(sessionKey(cfg));
    s = await session(cfg, fetchImpl, true);
    out = await doCall(s);
  }
  if (!out.ok) sessions.delete(sessionKey(cfg));
  return out;
}

/** Put a file on an incident: the drift report, the photograph of the rack. */
async function attach(cfg, sysId, { fileName, contentType, body } = {}, fetchImpl = reqRaw) {
  const none = { sysId: null, name: norm(fileName) || null, size: 0 };
  if (!norm(sysId)) return { ok: false, status: 0, ...none, error: 'There is no incident to attach it to.' };
  const bytes = Buffer.isBuffer(body) ? body : Buffer.from(body == null ? '' : body);
  if (!norm(fileName) || !bytes.length) return { ok: false, status: 0, ...none, error: 'There is no file to attach.' };
  try {
    const url = `${origin(cfg)}/api/now/attachment/file`
      + `?table_name=${encodeURIComponent(cfg.incidentTable || 'incident')}`
      + `&table_sys_id=${encodeURIComponent(norm(sysId))}`
      + `&file_name=${encodeURIComponent(norm(fileName))}`;
    const r = await fetchImpl(url, 'POST', { 'Content-Type': norm(contentType) || 'application/octet-stream' },
      bytes, 60000, cfg);
    if (!r.ok) return { ...failed(r), ...none };
    const row = (r.body && r.body.result) || {};
    return { ok: true, status: r.status, sysId: row.sys_id || null, name: row.file_name || norm(fileName),
      size: Number(row.size_bytes) || bytes.length, error: null };
  } catch (err) {
    return { ...unreachable(err), ...none };
  }
}

module.exports = {
  toIncident, describe, correlationFor, urgencyFor,
  raise, statusOf, findExisting, login,
  STATE, CLOSED_STATES,
  correlationForCheck, toCheckIncident, appUrlFor, findUsers, makeUser, resolveUser, raiseCheck,
  toTaskIncident, raiseTask, assignedTo,
  update, setState, workNote, reassign,
  choices, pickCloseCode, pickHoldReason, attach,
  STATE_CODE,
  _req: req, _reqRaw: reqRaw, _sessions: sessions, _choiceCache: choiceCache, _worked: worked,
};
