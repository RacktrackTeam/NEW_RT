/* RackTrack mobile app: sign in, film or send a sweep, follow the run, read the result and the report, the history. */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); };
  function quiet(what) { return function (e) { if (window.console) console.warn('RackTrack: ' + what + ' failed: ' + ((e && e.message) || e)); }; }   // a side call that may fail without stopping the screen, logged
  function rackKey(x) { return [x.org == null ? '' : x.org, x.site == null ? '' : x.site, String(x.rack_id || x.id).toUpperCase()].join('|'); }   // one rack = its id within its organization and site
  var me = null, pollTimer = null, guideWatch = null, sweepStart = 0, toastTimer = null, mode = null;
  // inside the iOS / Android app (native/): the shell page that picks the server lives on the app's own origin
  var NATIVE = /RackTrackSweep/.test(navigator.userAgent), SHELL_URL = /Android/.test(navigator.userAgent) ? 'https://localhost/' : 'capacitor://localhost/';

  // ---------------------------------------------------------------- api
  function api(path, opts, again) {
    opts = opts || {};
    if (RES.id && /^\/api\/(sources|connections)/.test(path) && path.indexOf('run=') < 0) path += (path.indexOf('?') < 0 ? '?' : '&') + 'run=' + encodeURIComponent(RES.id);   // the platform owner has no organization: the server acts in this run's own
    return fetch(path, Object.assign({ credentials: 'same-origin', cache: 'no-store' }, opts)).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (r.status === 401 && !again) return AUTH.refresh().then(function (ok) { if (ok) return api(path, opts, true); me = null; go('signin'); throw new Error('Sign in first.'); });
        if (r.status === 401) { me = null; go('signin'); throw new Error('Sign in first.'); }
        if (r.status === 403 && j.org_status) { me = null; go('pending'); throw new Error(j.error); }
        if (!r.ok) throw new Error(j.error || j.detail || r.statusText);
        return j;
      });
    });
  }
  // an upload with progress (fetch cannot report it): videos are often a few hundred MB
  function upload(path, fd, onProgress) {
    return new Promise(function (resolve, reject) {
      var x = new XMLHttpRequest(); x.open('POST', path); x.withCredentials = true;
      x.upload.onprogress = function (e) { if (e.lengthComputable) onProgress(e.loaded / e.total); };
      x.onload = function () { var j = {}; try { j = JSON.parse(x.responseText); } catch (e) {}
        if (x.status === 401) { me = null; go('signin'); return reject(new Error('Sign in first.')); }
        if (x.status >= 400) return reject(new Error(j.error || j.detail || x.statusText)); resolve(j); };
      x.onerror = function () { reject(new Error('The upload was cut off. Check the connection and try again.')); };
      x.send(fd);
    });
  }
  function toast(t) { var el = document.querySelector('.toast'); if (!el) { el = document.createElement('div'); el.className = 'toast'; document.body.appendChild(el); }
    el.textContent = t; el.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(function () { el.hidden = true; }, 3200); }

  // ---------------------------------------------------------------- routing: #signin #home #history #sweep #job/<id> #result/<id> #report/<id> #more #models
  var DETAIL = { job: 'Run', result: 'Result', history: 'History', models: 'Models and accuracy', connections: 'Connections', copies: 'Database copies', tools: 'Rack tools' };   // tools: wt/features
  // the result's bar: Main is the rack read from the data; the other four show the report's steps in groups
  var TABS = { rack: 'Rack', devices: 'Devices', layout: 'Layout', report: 'Report' };   // one page in four parts; the compare and its differences live inside the Report
  var ALIAS = { elevation: 'layout', drift: 'compare', record: 'compare', diff: 'diff', ports: 'devices', compare: 'compare' };   // ports live on each device (user, 2026-10-27: one place per device)
  var KINDS = ['Switch', 'Router', 'Firewall', 'Gateway', 'Patch Panel', 'Server', 'Storage unit', 'PDU', 'UPS', 'Other'];
  var LAST = null;                                   // the run the Report tab opens: the one being looked at, else the newest finished one
  try { LAST = localStorage.getItem('rt_last') || null; } catch (e) {}
  function remember(id) { if (!id) return; LAST = id; try { localStorage.setItem('rt_last', id); } catch (e) {} }
  function go(h) { location.hash = '#' + h; }
  // motion (user, 2026-10-28: 'I see no animations'): whatever appears slides in, its blocks one after another
  function motion(el) { [].filter.call(el.children, function (c) { return !c.hidden; }).forEach(function (c, i) { c.style.setProperty('--mo', Math.min(i, 10)); });
    el.classList.remove('mo'); void el.offsetWidth; el.classList.add('mo'); }
  new MutationObserver(function (ms) { ms.forEach(function (m) { var t = m.target; if (t.hidden) return;
    if (m.type === 'attributes' && (t.classList.contains('screen') || t.classList.contains('rpart') || t.id === 'dv-pick' || t.id === 'dv-one')) motion(t);
    if (m.type === 'childList' && t.id === 'dv-list') motion(t);
    if (t.id === 'toptitle' || (t.parentNode && t.parentNode.id === 'toptitle')) { var tt = $('toptitle'); tt.classList.remove('mo-t'); void tt.offsetWidth; tt.classList.add('mo-t'); } }); })
    .observe(document.body, { attributes: true, attributeFilter: ['hidden'], childList: true, characterData: true, subtree: true });
  function route() {
    var h = (location.hash || '#home').slice(1), name = h.split('/')[0], arg = h.split('/')[1], arg2 = h.split('/')[2];
    if (name === 'report') { if (arg) return go('result/' + arg + '/report'); if (LAST) return go('result/' + LAST + '/report'); toast('The report appears here once a video has been analysed.'); return go('home'); }
    var tool = (name === 'tools' && arg && arg !== 'site') ? (arg2 || 'find') : null;                 // a rack tool: its own tab in the result's bar
    var rtab = name === 'result' ? (TABS[arg2] ? arg2 : ALIAS[arg2] ? (ALIAS[arg2] === 'compare' || ALIAS[arg2] === 'diff' ? 'report' : ALIAS[arg2]) : 'rack') : null;
    if (name === 'result' && (ALIAS[arg2] === 'compare' || ALIAS[arg2] === 'diff')) RES.jump = ALIAS[arg2];
    if (rtab) { var order = Object.keys(TABS), was = order.indexOf(RES.tab || 'rack'), now = order.indexOf(rtab); document.body.dataset.dir = now > was ? 'fwd' : now < was ? 'back' : 'same'; RES.tab = rtab; }   // the tab's content slides in from the side it came from
    if (!me && !AUTH_SCREENS[name]) { name = 'signin'; }
    if (me && AUTH_SCREENS[name]) { name = 'home'; }
    document.querySelectorAll('.screen').forEach(function (s) { s.hidden = s.dataset.screen !== name; });
    document.body.classList.toggle('on-home', name === 'home');                  // the home page is white, like racktrack.ai
    var tab = name === 'job' ? 'sweep' : (name === 'history' || name === 'models' || name === 'connections' || name === 'copies') ? 'more' : name;
    var rn = $('rnav'), wide = window.matchMedia('(min-width:1000px)').matches;             // web layout: the result's tabs sit at the top of the page, not in a bar at the bottom (user, 2026-10-29)
    if (!rn._home) { rn._home = rn.parentNode; rn._next = rn.nextSibling; }
    if (wide && rtab) { if (!rn.classList.contains('inpage') || rn.parentNode !== document.querySelector('[data-screen="result"]')) { document.querySelector('[data-screen="result"]').insertBefore(rn, $('res-pending')); rn.classList.add('inpage'); } }
    else if (wide && tool) { var ts = document.querySelector('[data-screen="tools"]'); if (rn.parentNode !== ts) { ts.insertBefore(rn, ts.firstChild); rn.classList.add('inpage'); } }
    else if (rn.classList.contains('inpage')) { rn._home.insertBefore(rn, rn._next); rn.classList.remove('inpage'); }
    document.body.classList.toggle('web', wide);
    $('rnav').hidden = !(me && (rtab || tool)); document.body.classList.toggle('tabbed', !!(me && (rtab || tool))); document.body.classList.toggle('result', !!(me && rtab));
    if (rtab || tool) document.querySelectorAll('#rnav a').forEach(function (a) { a.href = a.dataset.tool ? '#tools/' + arg + '/' + a.dataset.rtab : '#result/' + arg + (a.dataset.rtab === 'rack' ? '' : '/' + a.dataset.rtab); a.classList.toggle('on', a.dataset.rtab === (rtab || tool)); });
    if (!tool) rn.dataset.rack = ''; else if (RES.id === arg && $('res-title').textContent) rn.dataset.rack = $('res-title').textContent;
    if (tool) { var onA = document.querySelector('#rnav a.on'); if (onA && onA.scrollIntoView) onA.scrollIntoView({ inline: 'center', block: 'nearest' }); }
    else if (rtab) { var onR = document.querySelector('#rnav a.on'), pill = onR && onR.parentNode; if (pill && pill.scrollWidth > pill.clientWidth) { var ra = onR.getBoundingClientRect(), rp = pill.getBoundingClientRect(); pill.scrollLeft += (ra.left + ra.width / 2) - (rp.left + rp.width / 2); } }   // on a phone the marked tab is brought into view sideways only (the page does not move)
    document.querySelectorAll('[data-nav]').forEach(function (a) { a.classList.toggle('on', a.dataset.nav === tab); });
    $('nav').hidden = !me || !!rtab || !!tool; $('top').hidden = !me; $('side').hidden = !me; document.body.classList.toggle('signed', !!me);
    $('back').hidden = !DETAIL[name]; $('topmark').hidden = !!DETAIL[name]; $('toptitle').textContent = (rtab || tool) ? 'Result' : (DETAIL[name] || 'RackTrack');
    $('top').classList.toggle('detail', !!DETAIL[name]); $('who').hidden = !!DETAIL[name]; $('topspacer').hidden = !DETAIL[name]; if (name !== 'result') { $('topsub').hidden = true; }
    var TT = { home: 'Racks', sweep: 'Scan a rack', job: 'Reading the rack', history: 'History', more: 'Profile', models: 'Models and accuracy', connections: 'Connections', copies: 'Database copies', signin: 'Sign in', contact: 'Contact us', forgot: 'Reset your password', invite: 'Accept your invite', pending: 'Waiting for approval' };
    var tt = rtab ? TABS[rtab] : tool ? ({ find: 'Find a port', connections: 'Connections', cables: 'Cables' }[tool] || 'Rack tool') : TT[name] || 'RackTrack';
    document.title = tt + (tt === 'RackTrack' ? '' : ' - RackTrack');
    clearInterval(pollTimer); stopGuide();
    window.scrollTo(0, 0);
    if (name === 'home') loadHome(); else setTimeout(reveal, 0);
    if (name === 'more') api('/api/video/history').then(function (rows) { var mine = rows.filter(function (x) { return !x.demo; }), n = mine.length; $('runs-count').textContent = n ? n + (n === 1 ? ' scan' : ' scans') : '';
      var done = mine.filter(function (x) { return x.status === 'done' && x.summary; }), racks = {}; done.forEach(function (x) { racks[rackKey(x)] = 1; });   // racks as Home counts them, not scans
      $('ps-racks').dataset.n = Object.keys(racks).length; $('ps-racks').nextElementSibling.textContent = Object.keys(racks).length === 1 ? 'rack read' : 'racks read'; $('ps-units').dataset.n = done.reduce(function (t, x) { return t + (x.summary.units || 0); }, 0); $('ps-devs').dataset.n = done.reduce(function (t, x) { return t + (x.summary.devices || 0); }, 0); countUp($('ps-racks').parentNode.parentNode); }).catch(quiet('the Profile numbers'));
    if (name === 'history') loadHistory();
    if (name === 'connections') loadConnections();
    if (name === 'copies') loadCopies();
    if (name === 'more') api('/api/sources/copies').then(function (d) { $('copies-count').textContent = d.copies.length ? d.copies.length + (d.copies.length === 1 ? ' copy' : ' copies') : ''; }).catch(quiet('the copies count'));
    if (name === 'sweep') openSweep();
    if (name === 'job') openJob(arg);
    if (name === 'result') { document.querySelector('[data-screen="result"]').dataset.tab = rtab; openResult(arg, rtab); }
    if (name === 'invite') AUTH.openInvite(arg);
    if (name === 'forgot') AUTH.openForgot();
    if (name === 'tools' && window.RTFEAT) window.RTFEAT.open(arg, arg2, h.split('/')[3]);      // wt/features: the rack tools (mobile/features.js)
  }
  window.addEventListener('hashchange', route);
  $('back').addEventListener('click', function () { if (location.hash.indexOf('#result/') === 0) return go('history'); if (history.length > 1) history.back(); else go('home'); });

  // ---------------------------------------------------------------- sign in (racktrack-app sign-in)
  // RT-EOS's sign-in service (auth.sh, rt_eos/server/auth_service.js) signs the session: these screens call it directly and it sets
  // the rt_access cookie on this host, which the engine reads. Its own screens (organization, sites and people, settings, the
  // platform owner's console, the first-run setup) are RT-EOS's React screens at <service>/org/, sharing the same cookie.
  var AUTH_SCREENS = { signin: 1, forgot: 1, invite: 1, pending: 1, contact: 1 };
  var AUTH = (function () {
    var base = location.protocol + '//' + location.hostname + ':3302';
    function url(p) { return base + p; }
    // an organization page at /org/ carries the way back into this app (the /org/ build shows it top left, same host only)
    function orgUrl(p, back) { return url(p) + '?back=' + encodeURIComponent(location.origin + location.pathname + '#' + (back || 'more')); }
    function call(p, body, method) {
      return fetch(url(p), { method: method || (body ? 'POST' : 'GET'), credentials: 'include', cache: 'no-store',
        headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined })
        .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) { var e = new Error((j.error === 'rate_limited' && j.message) || j.error || r.statusText); e.status = r.status; e.body = j; throw e; } return j; }); });
    }
    function where() { return fetch('/api/auth-service', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (j) {
      base = j.url ? j.url.replace(/\/+$/, '') : location.protocol + '//' + location.hostname + ':' + (j.port || 3302); }).catch(quiet('finding the sign-in service')); }
    function refresh() { return call('/api/auth/refresh', {}).then(function () { return true; }).catch(function () { return false; }); }
    function err(id, e) { $(id).textContent = e.message; $(id).hidden = false; }
    // after the service answers a sign-in: an admin whose organization is not set up yet goes to RT-EOS's first-run setup; a
    // technician never does (RT-EOS decides needsSetup); an organization still waiting shows the waiting screen
    function landed() {
      return call('/api/auth/me').then(function (j) {
        var u = j.user || {};
        if (u.organization && u.organization.status && u.organization.status !== 'active' && u.role !== 'owner') { me = null; return go('pending'); }
        if ((u.role === 'org_admin' || u.role === 'org_owner') && u.setup && u.setup.needsSetup) { location.href = orgUrl('/org/setup', 'home'); return; }
        return api('/api/me').then(function (m) { me = m; setWho(m); go('home'); });
      });
    }
    $('signin').addEventListener('submit', function (e) {
      e.preventDefault(); $('si-err').hidden = true;
      call('/api/auth/login', { username: $('si-user').value.trim(), password: $('si-pass').value })
        .then(function () { $('si-pass').value = ''; try { localStorage.setItem('rt_user', $('si-user').value.trim()); } catch (x) {} return landed(); })
        .catch(function (x) { err('si-err', x); });
    });
    $('contact').addEventListener('submit', function (e) {          // Contact us: kept in the local outbox for RackTrack (no mail is sent from here)
      e.preventDefault(); $('ct-err').hidden = true; $('ct-ok').hidden = true;
      fetch('/api/contact', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: $('ct-name').value, email: $('ct-email').value, organization: $('ct-org').value, message: $('ct-msg').value }) })
        .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || r.statusText); $('contact').reset(); $('ct-ok').hidden = false; }); })
        .catch(function (x) { $('ct-err').textContent = x.message; $('ct-err').hidden = false; });
    });
    var fgStep = 1;
    function openForgot() { fgStep = 1; $('fg-step2').hidden = true; $('fg-go').textContent = 'Send the code'; $('fg-err').hidden = true; $('fg-lead').textContent = 'Enter the email on your account. We send a code to it.'; }
    $('forgot').addEventListener('submit', function (e) {
      e.preventDefault(); $('fg-err').hidden = true;
      var email = $('fg-email').value.trim();
      if (fgStep === 1) return call('/api/auth/forgot-password', { email: email }).then(function () {
        fgStep = 2; $('fg-step2').hidden = false; $('fg-go').textContent = 'Set the new password'; $('fg-lead').textContent = 'If the email is on an account, a code is on its way. It is valid for one minute.'; $('fg-code').focus();
      }).catch(function (x) { err('fg-err', x); });
      call('/api/auth/reset-password', { email: email, code: $('fg-code').value.trim(), password: $('fg-pass').value })
        .then(function () { toast('Password changed. Sign in with the new one.'); $('fg-pass').value = ''; go('signin'); })
        .catch(function (x) { err('fg-err', x); });
    });
    var ivCode = null;
    function openInvite(code) {
      ivCode = code; $('invite').hidden = true; $('iv-err').hidden = true; $('iv-lead').textContent = 'Checking the invite...';
      call('/api/invites/' + encodeURIComponent(code || '')).then(function (j) {
        var i = j.invite || {}; $('iv-lead').textContent = 'You are invited to ' + (i.site || 'a site') + (i.organization ? ' of ' + i.organization : '') + (i.email ? ', as ' + i.email : '') + '.'; $('invite').hidden = false;
      }).catch(function () { $('iv-lead').textContent = 'This invite is not valid any more. Ask for a new one.'; });
    }
    $('invite').addEventListener('submit', function (e) {
      e.preventDefault(); $('iv-err').hidden = true;
      call('/api/invites/' + encodeURIComponent(ivCode) + '/accept', { username: $('iv-user').value.trim(), password: $('iv-pass').value })
        .then(function () { return landed(); }).catch(function (x) { err('iv-err', x); });
    });
    $('pd-check').addEventListener('click', function () { refresh().then(function () { return landed(); }).catch(function () { toast('Still waiting for approval.'); }); });
    $('pd-out').addEventListener('click', function () { signout(); });
    function links(role) {     // Profile: the RT-EOS organization screens, by role (as RT-EOS's own routes allow them)
      var L = [];
      if (role === 'owner') L.push(['/org/console', 'Platform console', 'Organizations waiting, activity']);
      if (role === 'owner' || role === 'org_owner' || role === 'org_admin') L.push(['/org/settings', 'Organization settings', 'Profile, sites, data settings']);
      if (role === 'owner' || role === 'org_owner' || role === 'org_admin' || role === 'site_manager') L.push(['/org/site', 'Sites and people', 'People, roles, invites']);
      var row = function (x) { return '<a class="grow" href="' + esc(orgUrl(x[0])) + '"><svg class="ic"><use href="#i-server"/></svg><span>' + esc(x[1]) + '</span><em>' + esc(x[2]) + '</em><span class="arr">→</span></a>'; };
      $('org-links').innerHTML = L.map(row).join('');
      $('org-links').hidden = !L.length; $('org-label').hidden = !L.length;                // a technician has no organization entries: no empty heading
      $('acct-settings').innerHTML = row(['/org/you', 'Settings', 'Your account']);      // the person's own settings sit under Account, beside Sign out
    }
    return { url: url, call: call, where: where, refresh: refresh, landed: landed, openForgot: openForgot, openInvite: openInvite, links: links };
  })();
  var ADMIN = false;
  function setWho(j) {
    var ROLE = { owner: 'Platform owner', org_owner: 'Organization owner', org_admin: 'Organization admin', site_manager: 'Site manager', member: 'Technician' }, site = [ROLE[j.role] || '', j.site_name || ''].filter(Boolean).join(' · '), ini = (j.name || '?').trim().charAt(0).toUpperCase();
    ADMIN = j.role === 'owner' || j.role === 'org_owner' || j.role === 'org_admin';           // logins and copies are the admins' (docs/AUTH.md); the server checks it too
    document.body.dataset.role = j.role || ''; document.body.classList.toggle('is-admin', ADMIN);
    $('site-pos').hidden = !(j.role === 'org_owner' || j.role === 'org_admin' || j.role === 'site_manager');   // only those who may save a site's position see it
    $('cp-in').hidden = !ADMIN;
    $('who').textContent = ini; $('av2').textContent = ini; $('av3').textContent = ini; $('who2').textContent = j.name; $('who3').textContent = j.name; $('site2').innerHTML = [ROLE[j.role] || '', j.site_name || ''].filter(Boolean).map(function (x) { return '<span>' + esc(x) + '</span>'; }).join(''); $('site3').textContent = site; $('site2').hidden = !site; $('site3').hidden = !site;   // the side bar: role and site each on its own line, never broken
    AUTH.links(j.role);
  }
  function signout() { AUTH.call('/api/auth/logout', {}).catch(quiet('signing out')).then(function () { me = null; go('signin'); }); }
  $('signout').addEventListener('click', signout); $('signout2').addEventListener('click', signout);
  $('who').addEventListener('click', function () { go('more'); });

  // ---------------------------------------------------------------- home and history
  function chip(x) { var c = x.demo ? 'demo' : x.status === 'done' ? 'done' : x.status === 'running' ? (x.partial ? 'part' : 'run') : 'fail';
    return '<span class="chip ' + c + '">' + (x.demo ? 'demo' : x.status === 'done' ? 'ready' : x.status === 'running' ? (x.partial ? 'ports reading' : 'running') : 'failed') + '</span>'; }
  var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function when(iso) { if (!iso) return ''; var d = new Date(String(iso).length === 16 ? iso + ':00' : iso); if (isNaN(d)) return String(iso); var two = function (n) { return (n < 10 ? '0' : '') + n; };   // one date style on every screen: 27 Oct 2026, 12:53
    return d.getDate() + ' ' + MON[d.getMonth()] + ' ' + d.getFullYear() + (/T\d/.test(String(iso)) ? ', ' + two(d.getHours()) + ':' + two(d.getMinutes()) : ''); }
  window.RTWHEN = when;
  function summary(s) { if (!s || s.units == null) return ''; return s.units + ' units · ' + s.devices + ' devices' + (s.named != null ? ', ' + s.named + ' named' : '') + ' · ports read on ' + s.layouts + (s.layouts === 1 ? ' device' : ' devices'); }
  function thumb(x) { return (x.status === 'done' || x.partial) ? '<img class="thumbimg" src="/api/video/' + encodeURIComponent(x.id) + '/file/rack.png" alt="" loading="lazy">' : ''; }
  function loadHistory() {
    api('/api/video/history').then(function (rows) {
      $('hist').innerHTML = rows.length ? rows.map(function (x) {
        var href = (x.status === 'done' || x.partial) ? '#result/' + x.id : '#job/' + x.id;
        var note = x.status === 'running' ? esc(x.progress || '') : x.error ? esc(x.error) : esc(summary(x.summary));
        return '<a class="run" href="' + href + '"><span class="thumb">' + thumb(x) + '<svg class="ic"><use href="#i-rack"/></svg></span><span class="t"><b>' + esc(x.title || x.name) + '</b><small>' + esc(when(x.started)) + '</small>'
          + (note ? '<span class="note2">' + note + '</span>' : '') + '</span><span class="side2">' + chip(x) + '<svg class="ic chev"><use href="#i-next"/></svg></span></a>';
      }).join('') : '<div class="card waitcard"><span class="dropic"><svg class="ic"><use href="#i-rack"/></svg></span><b>No scans yet</b><p class="muted small">Scan a rack to begin.</p><a class="btn primary" href="#sweep">Scan a rack</a></div>';
    }).catch(function (e) { $('hist').innerHTML = '<p class="err">' + esc(e.message) + '</p>'; });
  }
  $('hist-refresh').addEventListener('click', loadHistory);
  document.addEventListener('error', function (e) { var t = e.target; if (t && t.classList && t.classList.contains('thumbimg')) t.remove(); }, true);
  function loadHome() {
    var hr = new Date().getHours(), part = hr < 12 ? 'Good morning' : hr < 18 ? 'Good afternoon' : 'Good evening';
    $('greet-sub').textContent = part + (me && me.name ? ', ' + me.name : '');
    api('/api/video/history').then(function (rows) {
      var running = rows.filter(function (x) { return x.status === 'running'; })[0], last = rows.filter(function (x) { return x.status === 'done' && !x.demo; })[0];
      if (last && !LAST) remember(last.id); else if (LAST) remember(LAST);
      $('now-card').hidden = !running;
      if (running) { var n = stepOf(running.progress, running.status); $('now-title').textContent = running.partial ? 'Rack read, ports next' : 'Reading a rack'; $('now-step').textContent = running.partial ? '' : 'step ' + n + ' of 15'; $('now-card').href = (running.partial ? '#result/' : '#job/') + running.id;
        clearInterval(pollTimer); pollTimer = setInterval(function () { if ((location.hash || '#home') === '#home') loadHome(); }, 5000); }
      // one row per rack: its latest run (with that run's compare state); earlier runs of the rack are listed on its result page
      var byRack = {}, order = [];
      rows.filter(function (x) { return !x.demo && x.status !== 'running'; }).forEach(function (x) { var k = rackKey(x);
        if (!byRack[k]) { byRack[k] = []; order.push(k); } byRack[k].push(x); });
      var list = order.map(function (k) { var g = byRack[k].slice().sort(function (a, b) { return (b.started || '').localeCompare(a.started || '') || (b.record ? 1 : 0) - (a.record ? 1 : 0); }); g[0]._scans = g.length; return g[0]; });
      $('home-racks').innerHTML = list.length ? '<div class="rktable"><div class="rkhead"><span>Rack</span><span>Last scan</span><span>Compared with</span><span>State</span><span></span></div>' + list.map(function (x) {
        var href = (x.status === 'done' || x.partial) ? '#result/' + x.id : '#job/' + x.id, site = [me && me.role === 'owner' ? x.org_name : null, x.site_name].filter(Boolean).join(' · ') || (x.org == null && me && me.role === 'owner' ? 'From before sign-in' : '');
        var rec = x.record ? esc(x.record.source) : '-';                  // the state pill on the same row already says Not compared
        var st = x.status === 'failed' ? ['bad', 'Failed'] : x.differences == null ? (x.status === 'done' ? ['grey', 'Not compared'] : ['grey', 'Not finished']) : x.differences ? ['bad', pl(x.differences, 'difference')] : ['ok', 'No differences'];
        return '<a class="rkrow2" href="' + href + '"><span class="c-rack"><b>' + esc(x.rack_id || x.title || x.name) + '</b>' + (site ? '<small>' + esc(site) + '</small>' : '') + '</span>'
          + '<span class="c-when">' + esc(when(x.started)) + (x._scans > 1 ? '<small>' + x._scans + ' scans</small>' : '') + '</span><span class="c-with">' + rec + '</span><span class="c-state"><span class="spill ' + st[0] + '">' + esc(st[1]) + '</span></span><svg class="ic chev"><use href="#i-next"/></svg></a>'; }).join('') + '</div>'
        : '<div class="card waitcard"><b>No racks yet</b><p class="muted small">Scan a rack to begin: record it with the camera or upload a video.</p><a class="btn primary" href="#sweep">Scan a rack</a></div>';
    }).catch(function (e) { $('home-racks').innerHTML = '<p class="err">' + esc(e.message) + '</p>'; });
  }
  // the racktrack.ai entrance: blocks fade up as they come into view
  var revealer = 'IntersectionObserver' in window ? new IntersectionObserver(function (es) { es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('in'); revealer.unobserve(e.target); } }); }, { threshold: 0.12 }) : null;
  function reveal() { document.querySelectorAll('.screen:not([hidden]) .rv:not(.in)').forEach(function (el, i) { el.style.transitionDelay = Math.min(i, 5) * 60 + 'ms'; if (revealer) revealer.observe(el); else el.classList.add('in'); }); }

  // ---------------------------------------------------------------- sweep: Camera (the guided capture) or Upload; every video is read at one frame in five
  var STRIDE = '5';
  function openSweep() { stopGuide(true); $('v-err').hidden = true; $('sw-choices').hidden = false; $('sw-guide').hidden = true; if (!picked) $('sw-send').hidden = true; }
  function $$(sel) { return document.querySelector(sel); }
  function stopGuide(force) { clearInterval(guideWatch); guideWatch = null; var f = $('guide'); if (f && f.src && (force || !location.hash.startsWith('#sweep'))) { f.removeAttribute('src'); } }
  $('sw-camera').addEventListener('click', function () {
    $('v-err').hidden = true;
    if (!window.isSecureContext) { $('vcam').click(); return; }          // the guide needs https for the camera; over http the phone's own camera films it
    $('sw-choices').hidden = true; $('sw-send').hidden = true; $('sw-guide').hidden = false;
    sweepStart = Date.now(); $('guide').src = '/capture/?v=' + sweepStart; guideWatch = setInterval(watchGuide, 1500); window.scrollTo(0, 0);
  });
  $('dropzone').addEventListener('click', function (e) { if (e.target.closest('#dz-clear') || e.target.closest('#dz-pick') || e.target.closest('#sw-camera')) return; $('v-err').hidden = true; $('vfile').click(); });
  $('dropzone').addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('vfile').click(); } });
  ['dragenter', 'dragover'].forEach(function (n) { $('dropzone').addEventListener(n, function (e) { e.preventDefault(); $('dropzone').classList.add('over'); }); });
  ['dragleave', 'drop'].forEach(function (n) { $('dropzone').addEventListener(n, function (e) { e.preventDefault(); $('dropzone').classList.remove('over'); }); });
  $('dropzone').addEventListener('drop', function (e) { var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]; if (f) showPick(f); });
  $('dz-clear').addEventListener('click', function () { showPick(null); });
  function showPick(f) {
    picked = f || null; $('dz-idle').hidden = !!f; $('dz-pick').hidden = !f; $('vsend').disabled = !f; $('dropzone').classList.toggle('picked', !!f);
    var v = $('dz-video'); if (v.src) { try { URL.revokeObjectURL(v.src); } catch (e) {} v.removeAttribute('src'); }
    if (!f) return;
    $('dz-name').textContent = f.name; $('dz-info').textContent = (f.size / 1e6).toFixed(0) + ' MB';
    try { v.src = URL.createObjectURL(f); v.onloadedmetadata = function () { var d = Math.round(v.duration || 0); $('dz-info').textContent = (f.size / 1e6).toFixed(0) + ' MB · ' + (d >= 60 ? Math.floor(d / 60) + ' min ' + (d % 60) + ' s' : d + ' s'); try { v.currentTime = Math.min(1, (v.duration || 2) / 2); } catch (e) {} }; } catch (e) {}
  }
  document.querySelector('.swback').addEventListener('click', openSweep);
  var starting = false, picked = null;
  function watchGuide() {
    if (starting) return;
    var f = $('guide'), txt = '';
    try { txt = (f.contentDocument && f.contentDocument.body) ? f.contentDocument.body.innerText : ''; } catch (e) { txt = ''; }
    if (!/\bSent\b/i.test(txt)) return;
    starting = true;
    api('/api/captures').then(function (j) {
      var mine = (j.takes || []).filter(function (t) { return t.source === 'app' && t.video && t.time && new Date(t.time).getTime() > sweepStart - 120000; });
      if (!mine.length) { starting = false; return; }
      mine.sort(function (a, b) { return new Date(b.time) - new Date(a.time); });
      var fd = new FormData(); fd.append('capture', mine[0].id); fd.append('stride', STRIDE);
      return api('/api/video', { method: 'POST', body: fd }).then(function (r) { starting = false; stopGuide(true); $('sw-guide').hidden = true; $('sw-choices').hidden = false; if (r.reused) { analysingReused(r.job); return; } analysing(null, r.job); });
    }).catch(function (e) { starting = false; $('v-err').textContent = e.message; $('v-err').hidden = false; });
  }
  function onPicked(inp) { var f = inp.files[0]; inp.value = ''; showPick(f || null); }
  $('vfile').addEventListener('change', function () { onPicked($('vfile')); }); $('vcam').addEventListener('change', function () { onPicked($('vcam')); });
  $('vsend').addEventListener('click', function () {
    var f = picked; if (!f) return; $('vsend').disabled = true; $('v-err').hidden = true;
    var fd = new FormData(); fd.append('video', f); fd.append('stride', STRIDE);
    analysing(f, null);
    upload('/api/video', fd, function () {})
      .then(function (r) { showPick(null); if (r.reused) { analysingReused(r.job); return; } analysing(f, r.job); })
      .catch(function (e) { analysingOff(); $('v-err').textContent = e.message; $('v-err').hidden = false; $('vsend').disabled = false; });
  });

  // ---------------------------------------------------------------- the run's progress: the 15 steps
  var STEPS = ['Input', 'Quality check', 'Stitched rack picture', 'Frames placed on the rack', 'Unit detection', 'Unit classification', 'Device detection', 'Device classification',
    'OCR: make and model', 'Agents: vendor templates', 'Overlay: port layouts', 'Fallback: port model', 'Port review', 'Rack report', 'Done'];
  function stepOf(progress, status) {
    if (status === 'done') return 15; var p = (progress || '').toLowerCase();
    if (p.indexOf('which way') >= 0) return 1; if (p.indexOf('stitch') >= 0) return 3; if (p.indexOf('placing') >= 0 || p.indexOf('register') >= 0) return 4;
    if (p.indexOf('frame ') === 0 || p.indexOf('units') >= 0) return 5; if (p.indexOf('fus') >= 0 || p.indexOf('occupan') >= 0) return 6; if (p.indexOf('device') >= 0 && p.indexOf('layout') < 0 && p.indexOf('classif') < 0) return 7;
    if (p.indexOf('best frames') >= 0) return 4; if (p.indexOf('classif') >= 0) return 8; if (p.indexOf('layout') >= 0 || p.indexOf('port') >= 0 || p.indexOf('ocr') >= 0) return 10;
    if (p.indexOf('report') >= 0) return 14; if (p.indexOf('draw') >= 0) return 13; return 1;
  }
  function renderSteps(cur, status) {
    $('job-steps').innerHTML = STEPS.map(function (s, i) { var n = i + 1, cls = n < cur ? 'done' : n === cur && status === 'running' ? 'on' : (status === 'done' ? 'done' : ''); return '<li class="' + cls + '"><span class="n">' + (n < cur || status === 'done' ? '&#10003;' : n) + '</span><span>' + esc(s) + '</span><span class="t">' + (n === cur && status === 'running' ? 'now' : '') + '</span></li>'; }).join('');
  }
  var PICS = [['rack.png', 3, 'The stitched rack picture', 'one straight picture from the sharpest frames'], ['rack_units.jpg', 5, 'Every unit numbered', 'voted across the frames'],
    ['rack_occupancy_frames.jpg', 6, 'Full, free or uncertain', 'the five classifiers per unit'], ['rack_devices.jpg', 7, 'Every device and its span', 'device boxes agreed across frames']];
  // RT-EOS's Analysing screen (mobile/rt, window.RTEOSAnalysing): the video being read, the stage line; it stays until the run
  // is done, then the result opens (user, 2026-10-27: 'use rt-eos exact analysis page')
  var AN = { el: null, timer: null, job: null };
  function analysing(file, job) {
    if (!window.RTEOSAnalysing) { if (job) go('job/' + job); return; }
    AN.el = $('rt-analysing'); AN.file = file || AN.file; AN.job = job || AN.job;
    var word = (me && me.name) ? me.name + "'s rack" : 'Your rack';
    window.RTEOSAnalysing.mount(AN.el, { racks: [{ key: 'r', word: word, files: AN.file ? [AN.file] : [], state: 'reading' }], finished: false, label: 'Analysing the rack' });
    if (!AN.job) return;
    var id = AN.job; remember(id); firstFrame(AN.file, id); var f = AN.file; analysingOff(); go('job/' + id);
  }
  function analysingReused(job) {                                      // a video read before: the analysing animation for a few seconds, then its result (user, 2026-10-30)
    remember(job); RES.sel = null; RES.pick = null;
    if (!window.RTEOSAnalysing) { setTimeout(function () { go('result/' + job); }, 2500); return; }
    AN.el = $('rt-analysing'); AN.job = null; var word = (me && me.name) ? me.name + "'s rack" : 'Your rack';
    window.RTEOSAnalysing.mount(AN.el, { racks: [{ key: 'r', word: word, files: AN.file ? [AN.file] : [], state: 'reading' }], finished: false, label: 'Analysing the rack' });
    setTimeout(function () { window.RTEOSAnalysing.mount(AN.el, { racks: [{ key: 'r', word: word, files: AN.file ? [AN.file] : [], state: 'done' }], finished: true, label: 'Analysing the rack' }); }, 2600);
    setTimeout(function () { analysingOff(); go('result/' + job); }, 3500);
  }
  function analysingOff() { clearInterval(AN.timer); if (AN.el && window.RTEOSAnalysing) window.RTEOSAnalysing.unmount(AN.el); AN.file = null; AN.job = null; }
  // The run page: the picture so far, one heading, the stages (current marked, finished ticked), the time, and the way out.
  var RUN_STAGES = [['Reading the video', 1, 2], ['Stitched picture of the rack', 3, 4], ['Units', 5, 6], ['Devices', 7, 8], ['Makes and models', 9, 10], ['Port layouts', 11, 13], ['Rack report', 14, 14]];
  var FIRST = {};                                                      // run id -> first frame of the video, taken on this phone or laptop
  function firstFrame(file, id) {
    if (!file || !window.URL || FIRST[id]) return;
    var v = document.createElement('video'), u = URL.createObjectURL(file); v.muted = true; v.preload = 'auto'; v.src = u;
    v.addEventListener('loadeddata', function () { try { v.currentTime = 0.1; } catch (e) {} });
    v.addEventListener('seeked', function () { try { var c = document.createElement('canvas'); c.width = v.videoWidth; c.height = v.videoHeight; c.getContext('2d').drawImage(v, 0, 0); FIRST[id] = c.toDataURL('image/jpeg', .7); } catch (e) {} URL.revokeObjectURL(u); });
  }
  function portsLine(ports) { var k = Object.keys(ports || {}); if (!k.length) return ''; var d = k.filter(function (x) { return ports[x] === 'done'; }).length; return d + ' of ' + k.length + ' devices'; }
  function renderRun(j, id) {
    var running = j.status === 'running', failed = j.status === 'failed', done = j.status === 'done', n = stepOf(failed ? j.failed_at : j.progress, j.status);
    var html = RUN_STAGES.map(function (s) {
      var fin = done || n > s[2], on = running && !fin && n >= s[1], note = '';
      if (s[0] === 'Port layouts') { var pl = portsLine(j.ports); if (pl && (on || fin)) note = pl; }
      var cls = fin ? 'done' : on ? 'on' : failed && n >= s[1] && n <= s[2] ? 'bad' : '';
      return '<li class="' + cls + '"><span class="mk">' + (fin ? '&#10003;' : '') + '</span><span>' + esc(s[0]) + '</span><span class="t">' + esc(note) + '</span></li>';
    }).join('');
    $('run-stages').innerHTML = html;
  }
  function openJob(id) {
    if (!id) return go('home');
    var t0 = null, shown = null;
    function tick() {
      api('/api/video/' + id).then(function (j) {
        if (!t0) t0 = new Date(j.started).getTime() || Date.now();
        var secs = Math.max(0, Math.round(((j.finished ? new Date(j.finished).getTime() : Date.now()) - t0) / 1000));
        var ports = j.partial && j.status === 'running', ready = ports || j.status === 'done', failed = j.status === 'failed';
        $('run-time').textContent = (ready ? 'Took ' : 'Elapsed ') + (secs >= 60 ? Math.floor(secs / 60) + ' min ' + (secs % 60) + ' s' : secs + ' s');
        $('job-title').textContent = failed ? 'The reading stopped' : ready ? 'Rack read' : 'Reading the rack';
        $('run-line').textContent = failed ? 'The run could not finish.' : ready ? (ports ? 'Units, devices and kinds are in. Port layouts keep reading in the background.' : 'The rack is read. Open the result to see it.') : 'The video is read frame by frame. First one straight picture of the rack, then every unit, device and port.';
        $('run-leave').hidden = ready || failed;
        renderRun(j, id);
        $('job-err').hidden = !failed; $('job-err').textContent = j.error || 'The analysis failed.';
        $('job-open').hidden = !ready; $('job-open').href = '#result/' + id; $('job-retry').hidden = !failed;
        $('job-del').hidden = j.status === 'running' || !canRemoveRun();
        var files = j.files || [], pic = files.indexOf('rack.png') >= 0 ? '/api/video/' + id + '/file/rack.png' : (FIRST[id] || null);
        if (pic && pic !== shown) { shown = pic; $('run-img').src = pic; $('run-img').hidden = false; $('run-ph').hidden = true; }
        if (j.status !== 'running') clearInterval(pollTimer);
      }).catch(function (e) { clearInterval(pollTimer); if (/No such run/i.test(e.message)) { toast('That run is not here any more.'); return go(LAST ? 'result/' + LAST : 'history'); } $('job-err').textContent = e.message; $('job-err').hidden = false; });
    }
    $('run-img').hidden = true; $('run-ph').hidden = false; $('job-open').hidden = true; $('job-retry').hidden = true; $('job-del').hidden = true; $('run-stages').innerHTML = '';
    tick(); pollTimer = setInterval(tick, 2000);
  }
  openJob.seen = {};
  // The server lets only a site manager, an organization admin or owner and the platform owner remove a scan (ROLE_RULES)
  function canRemoveRun() { return !!(me && ['owner', 'org_owner', 'org_admin', 'site_manager'].indexOf(me.role) >= 0); }
  $('job-del').addEventListener('click', function () {
    var id = (location.hash.split('/')[1] || ''); if (!id) return;
    var b = $('job-del'), label = b.querySelector('span'); if (b.dataset.sure !== '1') { b.dataset.sure = '1'; label.textContent = 'Tap again to remove it for good'; setTimeout(function () { b.dataset.sure = ''; label.textContent = 'Remove this run'; }, 4000); return; }
    api('/api/video/' + id, { method: 'DELETE' }).then(function () { toast('Run removed.'); go('history'); }).catch(function (e) { toast(e.message); });
  });

  // ---------------------------------------------------------------- the report, inside the app
  function fitFrame(f) {
    var fit = function () { try { var d = f.contentDocument, el = d.querySelector('.page') || d.body, h = Math.ceil(el.getBoundingClientRect().height) + 80; if (Math.abs(h - (parseInt(f.style.height) || 0)) > 2) f.style.height = h + 'px'; } catch (e) {} };
    f.fit = fit;
    var opened = Date.now();
    f.onload = function () { fit(); if (Date.now() - opened < 4000) window.scrollTo(0, 0); try { new ResizeObserver(fit).observe(f.contentDocument.body); f.contentDocument.addEventListener('click', function () { setTimeout(fit, 80); }); } catch (e) {} };
  }

  // ---------------------------------------------------------------- the Rack tab: one picture, four overlays (units, space, devices, kinds)
  var LAY = 'units', LAYNAMES = ['units', 'space', 'devices', 'kinds'];
  // the Rack tab fits the screen (user, 2026-10-27): the picture takes the height left above the bar; Detail = full width, scrolls
  var FIT = (function () { try { return localStorage.getItem('rt_fit') !== 'detail'; } catch (e) { return true; } })();
  function fitHint(cap) { var plain = (cap || '').replace(/<[^>]+>/g, ''); return (cap || '') + (FIT && plain.length < 26 ? (cap ? ' · ' : '') + '<span class="muted">tap to zoom</span>' : ''); }   // the hint only where the line stays short
  function applyFit() {
    var sh = $('rackshot'); sh.classList.toggle('fit', FIT); $('fit-tog').textContent = FIT ? 'Zoom in' : 'Fit to screen';
    var ch = $('lay-chips'); ch.classList.toggle('row', FIT); if (FIT && ch.parentNode === sh) sh.parentNode.insertBefore(ch, sh); else if (!FIT && ch.parentNode !== sh) sh.appendChild(ch);   // compact: the chips above the picture; detail: on its corner
    var inner = $('rackinner');
    if (!FIT) { sh.style.removeProperty('--fit-h'); var C = RES.crop; if (C && C[2] && inner.clientWidth) inner.style.height = Math.round(inner.clientWidth * C[3] / C[2]) + 'px'; return; }   // WebKit gives the box no height from its aspect ratio: set it
    inner.style.height = '';
    var top = sh.getBoundingClientRect().top + window.scrollY, nav = $('rnav').getBoundingClientRect(), bottom = (nav.height ? nav.top : window.innerHeight) - 10 - $('caprow').offsetHeight - 8;
    sh.style.setProperty('--fit-h', Math.max(240, bottom - top) + 'px');
    var settle = function () { var over = document.body.scrollHeight - window.innerHeight; if (over > 0) sh.style.setProperty('--fit-h', Math.max(240, parseFloat(sh.style.getPropertyValue('--fit-h')) - over) + 'px'); };   // nothing left to scroll
    requestAnimationFrame(settle); setTimeout(settle, 400);

  }
  $('fit-tog').addEventListener('click', function () { FIT = !FIT; try { localStorage.setItem('rt_fit', FIT ? 'fit' : 'detail'); } catch (e) {} window.scrollTo(0, 0); applyFit();
    var redraw = function () { if (RES.view && !$('res-rack').hidden) { applyFit(); drawLayer(RES.view, LAY, false); } }; redraw(); setTimeout(redraw, 120); setTimeout(redraw, 500); });
  window.addEventListener('resize', function () { if (RES.view && !$('res-rack').hidden) applyFit(); });
  window.addEventListener('resize', function () { var wide = window.matchMedia('(min-width:1000px)').matches; if (wide !== document.body.classList.contains('web')) route(); });
  // the rack's two rails, as a line each (x as a function of y), fitted to the devices that span the rack; the whole picture when none do
  function railsOf(devs, W, rowW) {
    var spans = devs.filter(function (d) { if (!d.polygon) return false; var xs = d.polygon.map(function (p) { return p[0]; }); return !rowW || Math.max.apply(null, xs) - Math.min.apply(null, xs) < rowW * 0.97; }).map(function (d) { var xs = d.polygon.map(function (p) { return p[0]; }), ys = d.polygon.map(function (p) { return p[1]; }); return { l: Math.min.apply(null, xs), r: Math.max.apply(null, xs), y: (Math.min.apply(null, ys) + Math.max.apply(null, ys)) / 2 }; });
    if (!spans.length) return { l: function () { return 0; }, r: function () { return W; } };
    var widest = Math.max.apply(null, spans.map(function (s) { return s.r - s.l; })), wide = spans.filter(function (s) { return s.r - s.l >= widest * 0.85; });
    var fit = function (key) { var n = wide.length, my = 0, mx = 0, sxy = 0, syy = 0; wide.forEach(function (s) { my += s.y; mx += s[key]; }); my /= n; mx /= n;
      wide.forEach(function (s) { sxy += (s.y - my) * (s[key] - mx); syy += (s.y - my) * (s.y - my); }); var k = n > 1 && syy ? sxy / syy : 0; return function (y) { return mx + k * (y - my); }; };
    return { l: fit('l'), r: fit('r') };
  }
  var plural = function (w, n) { return n === 1 || w === 'not sure' ? w : /(s|x|ch|sh)$/.test(w) ? w + 'es' : w + 's'; };
  var unumOf = function (s) { return parseInt(String(s).replace(/\D/g, ''), 10); };
  function drawLayer(v, which, animate) {
    var svg = $('lay-svg'), units = v.units || [], devs = v.devices || [], W = (v.rack.size || [1000, 2000])[0], H = (v.rack.size || [1000, 2000])[1], out = [];
    if (which !== LAY) LAYSOLO = null; LAY = which;
    document.querySelectorAll('#lay-seg button').forEach(function (b, i) { var on = b.dataset.lay === which; b.classList.toggle('on', on); b.setAttribute('aria-selected', on); if (on) { var k = $('segknob'); k.style.width = b.offsetWidth + 'px'; k.style.transform = 'translateX(' + b.offsetLeft + 'px)'; } });
    var inDev = {}; devs.forEach(function (d) { (d.units || []).forEach(function (x) { inDev[x] = d; }); });
    var fs = Math.round(W * 0.03), cap = '';
    var rowsB = units.filter(function (x) { return x.box; }), rowW = rowsB.length ? rowsB[0].box[2] - rowsB[0].box[0] : 0;
    var lean = railsOf(devs, W, rowW), ym = H / 2, L = lean.l(ym), R = lean.r(ym);
    // A front view built from the unit grid: the rails stand straight (x L..R), every unit is one even row at its true height,
    // and every device is one level box from rail to rail over exactly its units. The photo is resampled into that grid.
    var m = W * 0.03;
    var cs = rowsB.map(function (x) { return (x.box[1] + x.box[3]) / 2; }), was = cs.length > 1 ? (cs[cs.length - 1] - cs[0]) / (cs.length - 1) : 0;
    var pitch = was;                                    // each unit at its average height in the photo: straight, but as tall as it was filmed
    var ds = cs.map(function (c, i) { return cs[0] + i * pitch; });
    var lerp = function (a, b, y) { var n = a.length; if (n < 2) return y; if (y <= a[0]) return y + b[0] - a[0]; if (y >= a[n - 1]) return y + b[n - 1] - a[n - 1]; var i = 1; while (a[i] < y) i++; return b[i - 1] + (y - a[i - 1]) * (b[i] - b[i - 1]) / ((a[i] - a[i - 1]) || 1); };
    var Y = function (y) { return lerp(cs, ds, y); };
    var uidx = {}; rowsB.forEach(function (x, i) { uidx[x.unit] = i; });
    var rowOf = function (b) { var i = uidx[b === undefined ? '' : b]; return i; };
    var rails = { l: function () { return L; }, r: function () { return R; } };
    var band = function (b, inset) { inset = inset || 0; var c = Y((b[1] + b[3]) / 2), y0 = c - pitch / 2 + inset, y1 = c + pitch / 2 - inset; return [[L, y0], [R, y0], [R, y1], [L, y1]]; };
    // one box per device: its units, top to bottom; a unit already taken by the device above is not given twice
    var dbox = {}, lastRow = -1;
    devs.filter(function (d) { return uidx[d.top] != null && uidx[d.bottom] != null; }).sort(function (a, b) { return uidx[a.top] - uidx[b.top]; }).forEach(function (d) {
      var i0 = Math.max(uidx[d.top], lastRow + 1), i1 = uidx[d.bottom]; if (i0 > i1) return; lastRow = i1;
      dbox[d.index] = { i0: i0, i1: i1, y0: ds[i0] - pitch / 2, y1: ds[i1] + pitch / 2 }; });
    var unitBox = {}; units.forEach(function (x) { if (x.box) unitBox[x.unit] = x.box; });
    RES.geo = { L: L, R: R, ds: ds, pitch: pitch, rows: rowsB.map(function (x) { return x.unit; }), dbox: dbox };
    var pts = function (q) { return q.map(function (p) { return p[0].toFixed(1) + ',' + p[1].toFixed(1); }).join(' '); };
    var boxes = rowsB, top = boxes.length ? ds[0] - pitch / 2 - 6 : 0, bot = boxes.length ? ds[ds.length - 1] + pitch / 2 + 6 : H, Hc = Math.round(Y(H));
    var cx0 = Math.max(0, L - m), cy0 = Math.max(0, top - m), cw = Math.min(W, R + m) - cx0, ch = Math.min(Hc, bot + m) - cy0;
    RES.crop = [cx0, cy0, cw, ch];
    if (boxes.length) out.push('<path class="shade" fill-rule="evenodd" d="M' + cx0 + ' ' + cy0 + 'h' + cw + 'v' + ch + 'h' + (-cw) + 'Z M' + (L - 6) + ' ' + top + ' L' + (R + 6) + ' ' + top + ' L' + (R + 6) + ' ' + bot + ' L' + (L - 6) + ' ' + bot + 'Z"/>');
    var px = Math.min(innerWidth - 32, 560) / cw, unitPx = pitch * px;                                    // on-screen pixels per picture pixel, and per unit
    var fsU = Math.min(fs, pitch * 0.58), every = unitPx >= 15 ? 1 : unitPx >= 8 ? 2 : unitPx >= 4 ? 5 : 10;
    svg.setAttribute('viewBox', cx0 + ' ' + cy0 + ' ' + cw + ' ' + ch);
    var inner = $('rackinner'), img = $('res-rack-plain'), cv = $('res-rack-flat'); inner.style.setProperty('--ar', cw + ' / ' + ch);
    // the picture: each output pixel is read from the photo along a line whose ends follow each device's own corners, so tilted devices come out level
    var kL = [], kR = [];
    devs.forEach(function (d) { var bx = dbox[d.index], q = d.polygon; if (!bx || !q || q.length < 4) return;
      var mx = q.reduce(function (s, p) { return s + p[0]; }, 0) / q.length, lt = q.filter(function (p) { return p[0] < mx; }), rt = q.filter(function (p) { return p[0] >= mx; }); if (!lt.length || !rt.length) return;
      var ys = function (a, f) { return f.apply(null, a.map(function (p) { return p[1]; })); };
      kL.push([bx.y0, ys(lt, Math.min)], [bx.y1, ys(lt, Math.max)]); kR.push([bx.y0, ys(rt, Math.min)], [bx.y1, ys(rt, Math.max)]); });
    ds.forEach(function (dd, i) { if (Object.keys(dbox).some(function (k) { return i >= dbox[k].i0 && i <= dbox[k].i1; })) return; kL.push([dd, cs[i]]); kR.push([dd, cs[i]]); });
    var clean = function (k) { k.sort(function (a, b) { return a[0] - b[0]; }); var o = []; k.forEach(function (p) { var z = o[o.length - 1]; if (!z || (p[0] > z[0] + 0.5 && p[1] > z[1])) o.push(p); }); return o; };
    kL = clean(kL); kR = clean(kR);
    var mapOf = function (k) { var a = k.map(function (p) { return p[0]; }), b = k.map(function (p) { return p[1]; }); return function (y) { return lerp(a, b, y); }; }, YL = mapOf(kL), YR = mapOf(kR);
    var key = v.id + '|' + [L, R, pitch, cx0, cy0, cw, ch].map(function (x) { return x.toFixed(1); }).join('|') + '|fv2';
    var paint = function () { if (cv.dataset.key === key) return;
      var s = document.createElement('canvas'); s.width = W; s.height = H; var sg = s.getContext('2d'); sg.drawImage(img, 0, 0); var sp = sg.getImageData(0, 0, W, H).data;
      var OW = Math.round(cw), OH = Math.round(ch); cv.width = OW; cv.height = OH; var g = cv.getContext('2d'), outI = g.createImageData(OW, OH), op = outI.data, span = R - L;
      for (var oy = 0; oy < OH; oy++) { var yd = cy0 + oy, yl = YL(yd), yr = YR(yd), row = oy * OW * 4;
        for (var ox = 0; ox < OW; ox++) { var xd = cx0 + ox, t = (xd - L) / span, yy = yl + t * (yr - yl), xx = lean.l(yy) + t * (lean.r(yy) - lean.l(yy)), o = row + ox * 4;
          if (xx < 0 || yy < 0 || xx > W - 2 || yy > H - 2) { op[o + 3] = 0; continue; }
          var x0 = xx | 0, y0 = yy | 0, fx = xx - x0, fy = yy - y0, i00 = (y0 * W + x0) * 4, i10 = i00 + 4, i01 = i00 + W * 4, i11 = i01 + 4;     // bilinear: smooth, not blocky
          for (var c = 0; c < 3; c++) op[o + c] = (sp[i00 + c] * (1 - fx) + sp[i10 + c] * fx) * (1 - fy) + (sp[i01 + c] * (1 - fx) + sp[i11 + c] * fx) * fy;
          op[o + 3] = 255; } }
      g.putImageData(outI, 0, 0); cv.dataset.key = key; cv.hidden = false; img.style.visibility = 'hidden'; cv.dispatchEvent(new Event('painted')); };
    var want = v.rack.picture;
    if (img.getAttribute('src') !== want) { cv.hidden = true; delete cv.dataset.key; img.src = want; }
    if (img.complete && img.naturalWidth && img.getAttribute('src') === want) paint(); else img.addEventListener('load', function () { if (img.getAttribute('src') === want) paint(); }, { once: true });
    if (img.complete && !img.naturalWidth && img.getAttribute('src') === want && !img.dataset.retried) { img.dataset.retried = '1'; cv.hidden = true; delete cv.dataset.key; img.src = want + (want.indexOf('?') >= 0 ? '&' : '?') + 'r=' + Date.now(); }   // a picture that failed to load (the server restarting) is fetched again, never painted black
    if (which === 'units') {
      units.forEach(function (u, i) { if (!u.box) return; var b = u.box, h = b[3] - b[1], fu;      // each number sized to its own row (rows near the camera are taller)
        var cy = Y(b[1] + h / 2), lx = rails.l(cy);
        var q = band(b, 0), tk = W * 0.02;      // no fill over the rack: a short tick on each rail at the unit's top edge, the number on the left rail
        out.push('<g class="lu" data-unit="' + u.unit + '" style="--i:' + i + '"><rect class="uhit" x="' + L + '" y="' + q[0][1] + '" width="' + (R - L) + '" height="' + (q[3][1] - q[0][1]) + '"/><path class="utick" d="M' + (L - tk * 0.3) + ' ' + q[0][1] + 'h' + tk + 'M' + (R + tk * 0.3) + ' ' + q[0][1] + 'h' + (-tk) + (i === rowsB.length - 1 ? 'M' + (L - tk * 0.3) + ' ' + q[3][1] + 'h' + tk + 'M' + (R + tk * 0.3) + ' ' + q[3][1] + 'h' + (-tk) : '') + '"/>'
          + ((fu = fsU) * px >= 6.5 && (every === 1 || unumOf(u.unit) % every === 0 || unumOf(u.unit) === 1) ? '<rect class="utag" x="' + (lx + 10) + '" y="' + (cy - fu * 0.75) + '" width="' + fu * 2.9 + '" height="' + fu * 1.5 + '" rx="' + fu * 0.75 + '"/><text class="utxt" x="' + (lx + 10 + fu * 1.45) + '" y="' + cy + '" font-size="' + fu + '">' + esc(String(u.unit).toUpperCase().replace(/^U0/, 'U')) + '</text>' : '') + '</g>'); });
      cap = '';                                                       // no 'numbered from the bottom' and the like (user, 2026-10-27)
    } else if (which === 'space') {
      var free = 0, used = 0, unsure = 0, n = units.length, runs = [], WORD = { used: 'In use', free: 'free', unsure: 'Not sure' };
      units.forEach(function (u, i) { if (!u.box) return; var b = u.box, st = inDev[u.unit] || u.status === 'occupied' ? 'used' : u.status === 'uncertain' ? 'unsure' : 'free';
        if (st === 'free') free++; else if (st === 'used') used++; else unsure++;
        var last = runs[runs.length - 1]; if (last && last.st === st) { last.y1 = b[3]; last.n++; } else runs.push({ st: st, y0: b[1], y1: b[3], n: 1, i: n - i });
        var q = band(b, 3), ew = W * 0.016, edge = [q[0], [q[0][0] + ew, q[0][1]], [q[3][0] + ew, q[3][1]], q[3]];
        out.push('<g class="sp ' + st + '" data-cls="' + st + '" data-unit="' + u.unit + '" style="--i:' + (n - i) + '"><polygon class="spfill" points="' + pts(q) + '"/><polygon class="spedge" points="' + pts(edge) + '"/></g>'); });
      runs.forEach(function (r) { if (r.st === 'used') return; var hS = Y(r.y1) - Y(r.y0), f = Math.min(fs, hS * 0.62); if (f * px < 7) return;
        var label = r.st === 'free' ? r.n + 'U free' : (r.n > 1 ? r.n + 'U ' : '') + 'not sure', tw = label.length * f * 0.6 + f * 1.4, cy = (Y(r.y0) + Y(r.y1)) / 2, lx = rails.l(cy) + W * 0.016 + 14;
        out.push('<g class="splab ' + r.st + '" data-cls="' + r.st + '" style="--i:' + r.i + '"><rect x="' + lx + '" y="' + (cy - f * 0.85) + '" width="' + tw + '" height="' + f * 1.7 + '" rx="' + f * 0.85 + '"/><text x="' + (lx + tw / 2) + '" y="' + cy + '" font-size="' + f + '">' + label + '</text></g>'); });
      var heldU = (v.record_passive || []).filter(function (x) { return units.some(function (u) { return u.unit === x.unit && u.status !== 'occupied' && u.status !== 'uncertain'; }); }).length;
      cap = '<b>' + free + 'U free</b> · ' + used + 'U in use' + (unsure ? ' · ' + unsure + 'U not sure' : '') + (heldU ? ' · ' + heldU + 'U of the free hold cable managers or blank panels in the record' : '');
    } else {
      var kinds = {}, kcol = {}, placed = [];
      var unsureU = {}, onDev = {}; units.forEach(function (u) { if (u.status === 'uncertain') unsureU[u.unit] = 1; }); devs.forEach(function (d) { (d.units || []).forEach(function (u) { onDev[u] = 1; }); });
      units.forEach(function (u, i) { if (!unsureU[u.unit] || onDev[u.unit] || !u.box) return;            // not sure, no device on it: amber, in words (user, 2026-10-30)
        var k = (RES.geo.rows || []).indexOf(u.unit); if (k < 0) return; var y0u = RES.geo.ds[k] - RES.geo.pitch / 2 + 2, y1u = RES.geo.ds[k] + RES.geo.pitch / 2 - 2, fU = Math.min(fs, (y1u - y0u) * 0.5), lab = UP(u.unit) + ' · not sure', twU = lab.length * fU * 0.6 + fU * 1.2;
        out.push('<g class="lu unc" data-unit="' + u.unit + '" style="--i:' + i + '"><rect x="' + L + '" y="' + y0u + '" width="' + (R - L) + '" height="' + (y1u - y0u) + '"/>'
          + (fU * px >= 7 ? '<rect class="dtag" x="' + (L + 12) + '" y="' + ((y0u + y1u) / 2 - fU * 0.78) + '" width="' + twU + '" height="' + fU * 1.56 + '" rx="' + fU * 0.78 + '"/><text class="dtxt" x="' + (L + 12 + twU / 2) + '" y="' + ((y0u + y1u) / 2) + '" font-size="' + fU + '">' + esc(lab) + '</text>' : '') + '</g>'); });
      devs.forEach(function (d, i) { var bx = dbox[d.index]; if (!bx) return;
        var poly = [[L, bx.y0 + 3], [R, bx.y0 + 3], [R, bx.y1 - 3], [L, bx.y1 - 3]], pts = poly.map(function (p) { return p[0].toFixed(1) + ',' + p[1].toFixed(1); }).join(' ');
        var xs = poly.map(function (p) { return p[0]; }), ys = poly.map(function (p) { return p[1]; }), x0 = Math.min.apply(null, xs), y0 = Math.min.apply(null, ys), yh = Math.max.apply(null, ys) - y0;
        var kk = RTTopology.kindKey(d.kind), col = (RTTopology.KIND[kk] || RTTopology.KIND.unknown)[0], word = d.kind && d.kind !== 'Other' ? d.kind : 'Not sure';
        if (which === 'kinds') { kinds[word] = (kinds[word] || 0) + 1; kcol[word] = col; }
        var nU = (d.units || []).filter(function (u) { return unsureU[u]; }).length;
        var fD = Math.min(fs, yh * 0.5), label = (which === 'kinds' ? word : 'D' + d.index) + (nU ? ' · not sure' : ''), tw = label.length * fD * 0.62 + fD * 1.2, ty = y0 + Math.min(yh / 2, fD * 1.1), x1 = Math.max.apply(null, xs), lx = x0 + 12;
        var hits = function (x) { return placed.some(function (p) { return x < p[2] && x + tw > p[0] && ty - fD * 0.8 < p[3] && ty + fD * 0.8 > p[1]; }); };
        if (hits(lx)) lx = x1 - tw - 12; var showTag = fD * px >= 7 && !hits(lx); if (showTag) placed.push([lx, ty - fD * 0.8, lx + tw, ty + fD * 0.8]);
        out.push('<g class="ld ' + which + (nU ? ' unc' : '') + '" data-dev="' + d.index + '" data-cls="' + esc(word) + '" style="--i:' + i + ';--k:' + col + '"><polygon pathLength="1" points="' + pts + '"/>'
          + (showTag ? '<rect class="dtag" x="' + lx + '" y="' + (ty - fD * 0.78) + '" width="' + tw + '" height="' + fD * 1.56 + '" rx="' + fD * 0.78 + '"/><text class="dtxt" x="' + (lx + tw / 2) + '" y="' + ty + '" font-size="' + fD + '">' + esc(label) + '</text>' : '') + '</g>'); });
      cap = which === 'devices' ? '<b>' + devs.length + ' devices</b> · tap one' : '<b>' + devs.length + ' devices</b> · tap a type to filter';   // the counts are on the chips: not said twice
    }
    var chips = which === 'space' ? [['free', 'Free', free, '#1fd67c'], ['used', 'In use', used, '#e9ece8'], ['unsure', 'Not sure', unsure, '#ff9a2e']].filter(function (c) { return c[2]; })
      : which === 'kinds' ? Object.keys(kinds).sort(function (a, b) { return kinds[b] - kinds[a]; }).map(function (k) { return [k, k, kinds[k], kcol[k]]; }) : [];
    $('lay-chips').innerHTML = (chips.length ? '<button type="button" class="lchip all" data-cls="">All</button>' : '') + chips.map(function (c, i) { return '<button type="button" class="lchip" data-cls="' + esc(c[0]) + '" style="--k:' + c[3] + ';--i:' + (i + 1) + '"><i></i>' + esc(c[1]) + '<b>' + c[2] + '</b></button>'; }).join('');
    svg.innerHTML = out.join(''); svg.classList.remove('go'); void svg.getBoundingClientRect(); if (animate !== false) svg.classList.add('go'); else svg.classList.add('go', 'still');
    $('lay-cap').innerHTML = fitHint(cap);
    applyFit();
    LAYCAP = cap; applySolo();
    if (animate !== false) { }
  }
  document.querySelectorAll('#lay-seg button').forEach(function (b) { b.addEventListener('click', function () { if (RES.view) drawLayer(RES.view, b.dataset.lay, true); }); });
  var LAYSOLO = null, LAYCAP = '';
  function applySolo() {
    $('lay-svg').classList.toggle('solo', !!LAYSOLO);
    document.querySelectorAll('#lay-svg [data-cls]').forEach(function (el) { el.classList.toggle('off', !!LAYSOLO && el.dataset.cls !== LAYSOLO); });
    document.querySelectorAll('#lay-chips .lchip').forEach(function (b) { b.classList.toggle('on', b.dataset.cls === (LAYSOLO || '')); });
    var on = LAYSOLO && document.querySelector('#lay-chips .lchip.on'); $('lay-cap').innerHTML = on ? 'Showing <b>' + esc(on.textContent.replace(/\d+$/, '').trim().toLowerCase()) + '</b> only' : fitHint(LAYCAP);
  }
  $('lay-chips').addEventListener('click', function (e) { var b = e.target.closest('.lchip'); if (!b) return; LAYSOLO = !b.dataset.cls || LAYSOLO === b.dataset.cls ? null : b.dataset.cls; applySolo(); });
  $('lay-svg').addEventListener('click', function (e) { var g = e.target.closest('[data-dev]'), un = e.target.closest('[data-unit]');
    if (g) { g.classList.add('lift'); var id = parseInt(g.dataset.dev, 10); setTimeout(function () { selectDevice(id); go('result/' + RES.id + '/devices'); }, 240); } else if (un) { selectUnit(un.dataset.unit); go('result/' + RES.id + '/devices'); }
    else if (FIT) $('fit-tog').click(); });                                                 // a tap on the small rack opens the Detail view

  // the Report tab: what is in the rack, top to bottom, in words
  // ---------------------------------------------------------------- the result: picture, elevation, device, how it was read
  var RES = { id: null, view: null, sel: null, timer: null, tab: 'rack' };
  var STATUS_WORD = { occupied: 'full', unoccupied: 'free', uncertain: 'not sure' }, STATUS_CLS = { occupied: 'occ', unoccupied: 'free', uncertain: 'unc' };
  function openResult(id, tab) {
    if (!id) return go('history');
    var fresh = RES.id !== id;
    if (fresh) { RES.cmpUi = 'idle'; if (typeof cmpMode === 'function') cmpMode(false); RES.sel = null; RES.view = null; RES.pick = null; RES.labelAsked = null; RES.cmp = null; RES.cmpSrc = null; RES.cmpUi = 'idle'; RES.cmpPick = false; RES.diffMode = null; $('cmp-menu').hidden = true; $('df-top').innerHTML = ''; $('diff-top').innerHTML = ''; }
    RES.id = id; clearInterval(RES.timer); remember(id);
    RES.tab = tab; $('res-eyebrow').textContent = 'Result'; $('rep-full').hidden = NATIVE;   // only the open tab shows (data-tab)
    loadView(true);
    watchPorts();
    if (fresh || !RES.cmp) loadDrift();
    if (fresh) loadOtherScans(id);
  }
  function loadView(first) {
    api('/api/video/' + RES.id + '/view').then(function (v) { RES.view = v; renderResult(v, first); if (first) { secTo(RES.jump || RES.tab); RES.jump = null; } }).catch(function (e) { toast(e.message); if (first) go('job/' + RES.id); });
  }
  function loadOtherScans(id) {                                         // the other scans of the same rack, newest first (Home lists a rack once)
    var el = $('res-others'); el.innerHTML = '';
    api('/api/video/history').then(function (rows) { var me_ = rows.filter(function (x) { return x.id === id; })[0]; if (!me_ || !me_.rack_id || RES.id !== id) return;
      var others = rows.filter(function (x) { return x.id !== id && x.rack_id && rackKey(x) === rackKey(me_) && (x.started || '') < (me_.started || ''); });   // the same rack of the same site, scanned before this one (a copy of the same scan, started at the same moment, is not earlier)
      if (others.length) el.innerHTML = '<details class="card othscans"><summary>Earlier scans of this rack (' + others.length + ')</summary><ul class="dflines">' + others.map(function (x) { var s = x.summary || {};
        return '<li><b>' + esc(when(x.started)) + '</b> <span class="muted small">' + (s.devices != null ? pl(s.devices, 'device') + ' · ' + s.units + ' units · ' : '') + (x.record ? 'compared with ' + esc(x.record.source) + ', ' + (x.differences ? pl(x.differences, 'difference') : 'no differences') : 'not compared') + '</span> <a href="#result/' + encodeURIComponent(x.id) + '">Open</a></li>'; }).join('') + '</ul></details>';
    }).catch(quiet('the earlier scans'));
  }
  function secTo(tab) { if (TABS[tab]) { if (RES.tab !== tab && RES.id) return go('result/' + RES.id + (tab === 'rack' ? '' : '/' + tab)); return window.scrollTo(0, 0); } var el = $('sec-' + tab); if (el) el.scrollIntoView({ block: 'start' }); }   // open a tab (the page scrolls to the top), or jump to a block inside the Report
  function portCells(ports, max) {
    if (!ports || !ports.length) return '';
    var list = max ? ports.slice(0, max) : ports;
    return '<div class="cells">' + list.map(function (p, i) { return '<span class="cell ' + (p.status === 'connected' ? 'on' : p.status === 'empty' ? 'off' : 'na') + '" style="--i:' + i + '" title="Port ' + esc(p.id) + ': ' + esc(p.status || 'not sure') + '">' + esc(p.id) + '</span>'; }).join('') + (max && ports.length > max ? '<span class="cell more">+' + (ports.length - max) + '</span>' : '') + '</div>';
  }
  function devName(d) { var L = d.layout || {}; return L.vendor ? esc(L.vendor) + (L.model ? ' ' + esc(L.model) : '') : L.vendor_read ? esc(L.vendor_read + (L.model_read ? ' ' + L.model_read : '')) + ', not confirmed' : (L.mapped ? 'make and model not read' : ''); }
  function renderResult(v, first) {
    var s = v.rack.summary || {}, devs = v.devices || [], named = devs.filter(function (d) { return d.kind && d.kind !== 'Other'; }).length, lay = devs.filter(function (d) { return d.layout && (d.layout.fitted || d.layout.auto); }).length;
    $('res-title').textContent = (v.rack_id && v.rack_id.id) || v.title || 'Rack';
    // the result has no header: the top bar names the tab
    $('res-pending').hidden = true;
    applyFit(); drawLayer(v, LAY, first); renderReport(v); renderRackSide(v); renderRackAsk(v);
    var RL = v.rack_label || {}, rid = RL.id && RL.id.text, rname = RL.name && RL.name.text;
    var RID = v.rack_id && v.rack_id.id;                                        // the ID the user confirmed comes first (29 Oct 2026)
    $('topsub').textContent = RID || rid || (rname ? rname.replace(/\b(\w)(\w*)/g, function (m, a, b) { return a + b.toLowerCase(); }) : ''); $('topsub').hidden = !$('topsub').textContent;
    $('res-sub').textContent = RID ? 'Rack ' + RID : (rid || (rname ? rname.replace(/\b(\w)(\w*)/g, function (m, a, b) { return a + b.toLowerCase(); }) : ''));
    // the rack's own label (src/rack_label.py): its ID and name as read off the video, or why not
    var rk = $('rk-id'); rk.hidden = true;                                       // the header shows the rack's ID or name; nothing twice
    rk.innerHTML = v.rack_label ? (rid || rname ? (rid ? '<b class="mono">' + esc(rid) + '</b>' : '') + (rname ? '<span>' + esc(rname.replace(/\b(\w)(\w*)/g, function (m, a, b) { return a + b.toLowerCase(); })) + '</span>' : '')
        : '<span>Rack label not read · film the label at the top of the rack, close and sharp</span>')
      : '<span><i class="sig live"></i> Reading the rack label…</span>';
    if (!v.rack_label && !v.rack_label_running && !RES.labelAsked) { RES.labelAsked = RES.id; api('/api/video/' + RES.id + '/rack-label', { method: 'POST' }).catch(quiet('reading the rack label')); }
    if (!v.rack_label) { clearTimeout(RES.labelT); RES.labelT = setTimeout(function () { if ((location.hash || '').indexOf('#result/' + RES.id) === 0) loadView(false); }, 12000); }
    // the elevation: RT-EOS's own cable drawing tab (2D diagram and 3D scene, mobile/rt/ built from RT-EOS by tools/rt-topology), fed this run as its "detail"
    var unum = function (s) { return parseInt(String(s).replace(/\D/g, ''), 10); };
    var cards = {}; devs.forEach(function (d) { var ps = PORT_KINDS[d.kind] ? devState(d, v) : null, L = d.layout || {}, ports = L.ports || [], on = ports.filter(function (p) { return p.status === 'connected'; }).length, off = ports.filter(function (p) { return p.status === 'empty'; }).length;
      cards[d.index] = { crop: d.crop, unit: URANGE(d.top, d.bottom, d.height), size: d.height + 'U', kind: d.kind && d.kind !== 'Other' ? d.kind : 'Not sure', state: ps ? ps.word : null, stateKey: ps ? ps.key : null,
        ports: ps && ps.facts && ports.length ? ports.length + ' ports · ' + on + ' connected' + (off ? ' · ' + off + ' empty' : '') + (ports.length - on - off ? ' · ' + (ports.length - on - off) + ' not sure' : '') : null }; });
    var RLb = v.rack_label || {}, detail = { scanId: v.id, cards: cards, rackLabel: (RLb.id && RLb.id.text) || (RLb.name && RLb.name.text) || v.title || 'This rack', unitsTotal: v.rack.units,
      devices: devs.map(function (d) { var L = d.layout || {};
        return { deviceId: d.index, name: (L.vendor ? L.vendor + (L.model ? ' ' + L.model : '') : '') || 'D' + d.index, type: d.kind && d.kind !== 'Other' ? d.kind : 'unidentified',
          heightU: d.height, unitTop: unum(d.top), unitBottom: unum(d.bottom), make: L.vendor || null, model: L.model || null,
          ports: (L.ports || []).map(function (p) { return { portNo: p.id, kind: /^(sfp|qsfp|osfp|lc|sc|fc)$/.test(String(p.type || '')) ? 'sfp' : 'main', type: p.type, state: p.status, cableColor: p.cable_colour || null, cableConnector: p.cable_connector || null, cableType: p.cable_type || null }; }) }; }) };
    $('rt-legend').innerHTML = byKind(devs).map(function (g, i) { return '<span style="--k:' + g.colour + ';--i:' + i + '"><i></i>' + esc(g.kind) + '<b>' + g.devs.length + '</b></span>'; }).join('');   // the kinds and their colours, as the drawing uses them
    var mountRT = function () { if (!window.RTEOSTopology) return setTimeout(mountRT, 150);
      if (!$('rt-fit') && window.RTEOSTopology.classes) { var K = window.RTEOSTopology.classes, sty = document.createElement('style'); sty.id = 'rt-fit';   // the 2D drawing and the 3D scene fit the screen
        sty.textContent = '.rteos .' + K.scene + '{height:var(--elev-h);min-height:0;border:0;border-radius:14px;box-shadow:0 1px 2px rgba(17,22,20,.04),0 18px 40px -26px rgba(17,22,20,.32)}.rteos svg[data-zoom]{width:100% !important;height:auto !important;max-height:var(--elev-h)}.rteos .' + K.summary + ',.rteos .' + K.hint + ',.rteos .' + K.caption + ',.rteos .' + K.note + '{font-size:12px}'; document.head.appendChild(sty); }
      window.RTEOSTopology.mount($('rt-topo'), detail, function (o) { if (o && o.deviceId != null) { selectDevice(parseInt(o.deviceId, 10)); go('result/' + RES.id + '/devices'); } }); };
    mountRT();
    // no filming notes on the result: they are for us, not for the person reading the rack
    renderOne();
  }
  var PORT_KINDS = { Switch: 1, Router: 1, Firewall: 1, Gateway: 1, 'Patch Panel': 1 };   // the kinds whose ports are read (patch panels by the team's panel reader)
  // devices by kind (user, 2026-10-27: category wise): KINDS order, 'Not sure' last; the kind's colour is RT-EOS's
  function byKind(devs) {
    var groups = {}; devs.forEach(function (d) { var k = d.kind && d.kind !== 'Other' ? d.kind : 'Not sure'; (groups[k] = groups[k] || []).push(d); });
    var order = KINDS.concat(['Not sure']);
    return Object.keys(groups).sort(function (a, b) { return (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99); }).map(function (k) { return { kind: k, devs: groups[k], colour: k === 'Not sure' ? '#9aa39e' : (window.RTTopology && RTTopology.KIND[RTTopology.kindKey(k)] || ['#9aa39e'])[0] }; });
  }
  function kindHead(g, extra) { return '<div class="pgh"><i style="--k:' + g.colour + '"></i><b>' + esc(g.kind) + '</b><span class="n">' + g.devs.length + '</span>' + (extra || '') + '</div>'; }
  // the Ports tab (user, 2026-10-27): only the classified port-bearing devices, by kind, one card each. Two cases: the make and
  // model were read and a vendor template fits -> that template laid over the crop; otherwise the crop and a close-up photo
  // is asked for. Nothing the port model found on its own (port boxes, cabled counts) is shown.
  function modelForm(d) { var L = d.layout || {}; return '<details class="mform"><summary class="link">Enter the make and model</summary><div class="mrow2"><input type="text" class="m-vendor" placeholder="Make, e.g. TP-Link" value="' + esc(L.vendor_read || '') + '" maxlength="40"><input type="text" class="m-model" placeholder="Model, e.g. TL-SG2428P" value="' + esc(L.model_read || '') + '" maxlength="40"><button class="btn primary setmodel" type="button" data-dev="' + d.index + '">Set</button></div></details>'; }
  document.addEventListener('click', function (e) { var b = e.target.closest && e.target.closest('.setmodel'); if (!b) return;
    var box = b.closest('.mform'), vendor = box.querySelector('.m-vendor').value.trim(), model = box.querySelector('.m-model').value.trim();
    if (!vendor || !model) { toast('Type both the make and the model.'); return; } b.disabled = true;
    api('/api/video/' + RES.id + '/device/' + b.dataset.dev + '/model', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ vendor: vendor, model: model }) })
      .then(function () { toast('Fitting the ' + vendor + ' ' + model + ' layout, about a minute.'); if (RES.view) RES.view.pending_ports = true; watchPorts(); loadView(false); }).catch(function (err) { toast(err.message); b.disabled = false; }); });
  // an identified device's facts: the ports in total, by class, and how many have a cable (user, 2026-10-27)
  function ranges(ids) {                                                // 1, 2, 3, 5 -> '1-3, 5'; names that are not numbers stay as they are
    var nums = ids.filter(function (x) { return /^\d+$/.test(String(x)); }).map(Number).sort(function (a, b) { return a - b; }), rest = ids.filter(function (x) { return !/^\d+$/.test(String(x)); });
    var out = [], s0 = null, p = null; nums.forEach(function (n) { if (s0 === null) { s0 = p = n; } else if (n === p + 1) { p = n; } else { out.push(s0 === p ? String(s0) : s0 + '-' + p); s0 = p = n; } }); if (s0 !== null) out.push(s0 === p ? String(s0) : s0 + '-' + p);
    return out.concat(rest.map(String)).join(', ');
  }
  var TYPE_NAMES = { rj45: 'RJ45', sfp: 'SFP', sfpplus: 'SFP+', qsfp: 'QSFP', osfp: 'OSFP', lc: 'LC', sc: 'SC', fc: 'FC', console: 'Console', management_port: 'Management', usb_a: 'USB-A', usb_b: 'USB-B', usb_c: 'USB-C', usb: 'USB', aux: 'Aux', combo: 'Combo', hidden: 'hidden' };
  function typeName(t) { t = String(t || 'port').toLowerCase(); return TYPE_NAMES[t] || t.toUpperCase(); }   // the port detector's 13 classes, readable (auto mode, 29 Oct)
  function portFacts(L) {
    var ports = L.ports || []; if (!ports.length) return '';
    var byType = {}; ports.forEach(function (p) { var t = typeName(p.type); byType[t] = (byType[t] || 0) + 1; });
    var on = ports.filter(function (p) { return p.status === 'connected'; }), off = ports.filter(function (p) { return p.status === 'empty'; }), unk = ports.filter(function (p) { return p.status !== 'connected' && p.status !== 'empty'; });
    var ids = function (l) { return l.map(function (p) { return p.id; }); }, which = function (l) { return l.length === ports.length ? 'all' : l.length ? '<small class="ids"> · ' + esc(ranges(ids(l))) + '</small>' : ''; };
    var row = function (k, val) { return '<span>' + k + '</span><b>' + val + '</b>'; }, kinds = Object.keys(byType).map(function (t) { return byType[t] + ' ' + esc(t); });
    return row('Ports', (L.mode === 'patchpanel' && L.size_known === false ? ports.length + ' seen · size not sure' : L.mode === 'patchpanel' && L.n_ports > ports.length ? L.n_ports + ' · ' + ports.length + ' seen, ' + (L.n_ports - ports.length) + ' hidden' : ports.length + (kinds.length ? ' (' + kinds.join(' and ') + ')' : ''))) + ((L.combo || []).length ? row('Combo', L.combo.length + '<small class="ids"> · ports ' + esc(ranges(L.combo.map(function (c) { return c.of; }))) + ' also take an SFP module</small>') : '') + row('Connected', on.length === ports.length && on.length > 1 ? 'all ' + on.length : on.length + which(on)) + row('Empty', off.length === ports.length && off.length > 1 ? 'all ' + off.length : off.length ? off.length + which(off) : '0') + (unk.length ? row('Not sure', unk.length + which(unk)) : '');
  }
  // the specifications of the model, looked up when the dropdown opens (toggle does not bubble: listened to in capture)
  document.addEventListener('click', function (e) { var c = e.target.closest && e.target.closest('#cine'); if (!c) return; c.classList.remove('play'); void c.offsetWidth; c.classList.add('play'); });
  function specFromAgent(sp, combo) {                                   // the port agent's spec (28 Oct package): found, ports by type, rack units, datasheet, photo check
    if (!sp || !sp.found) return null;
    var rows = [], P = sp.ports || {}, names = { rj45: 'RJ45', sfp: 'SFP', qsfp: 'QSFP', combo: 'Combo', console: 'Console', usb: 'USB', management_port: 'Management', other: 'Other' };
    // a combo port is one port with two faces: the agent's spec can list it as RJ45 and again as SFP (DGS-1210-52: "52 RJ45 + 4 SFP",
    // 56 in all). When the device's own reading found that many combo ports, the spec counts them once, as the facts above do.
    if (combo && P.sfp === combo && P.rj45 && (!sp.total_ports || sp.total_ports === P.rj45 + P.sfp)) {
      rows.push(['Ports', P.rj45 + ' RJ45, ' + combo + ' of them combo ports that also take an SFP module']); rows.push(['All ports', String(P.rj45)]);
    } else {
      if (sp.port_config) rows.push(['Ports', sp.port_config]); else { var parts = Object.keys(P).filter(function (k) { return P[k]; }).map(function (k) { return P[k] + ' ' + (names[k] || k); }); if (parts.length) rows.push(['Ports', parts.join(' + ')]); }
      if (sp.total_ports) rows.push(['All ports', String(sp.total_ports)]);
    }
    if (sp.rack_units != null) rows.push(['Height', sp.rack_units === 'desktop' ? 'Desktop' : sp.rack_units + 'U']);
    if (sp.source) rows.push(['Source', String(sp.source).replace(/_/g, ' ')]);
    var m = sp.matches_photo, note = m && m.agrees === false && m.note ? m.note : '';
    return { rows: rows, datasheet: sp.datasheet_url || null, note: note };
  }
  function loadSpecs(d) { if (!d || !d.dataset.model || d.dataset.loaded) return; d.dataset.loaded = '1';
    var box = d.querySelector('.specbody');
    var sp = d.dataset.spec ? specFromAgent(JSON.parse(d.dataset.spec), parseInt(d.dataset.combo || '0', 10)) : null;
    if (sp) { box.innerHTML = '<div class="dfacts">' + sp.rows.map(function (r) { return '<span>' + esc(r[0]) + '</span><b>' + esc(r[1]) + '</b>'; }).join('') + '</div>'
        + (sp.note ? '<p class="muted small">' + esc(sp.note) + '</p>' : '') + (sp.datasheet ? '<a class="link small" href="' + esc(sp.datasheet) + '" target="_blank" rel="noopener">Datasheet</a>' : ''); return; }
    api('/api/specs?vendor=' + encodeURIComponent(d.dataset.vendor) + '&model=' + encodeURIComponent(d.dataset.model)).then(function (r) {
      if (!r.ok) { box.innerHTML = '<p class="muted small">' + esc(r.note || 'No specifications found for this model.') + '</p>'; return; }
      var keys = Object.keys(r.specs || {}).filter(function (k) { return k !== 'Ports' && k !== 'Height'; });   // shown above already
      box.innerHTML = (keys.length ? '<div class="dfacts">' + keys.map(function (k) { return '<span>' + esc(k) + '</span><b>' + esc(r.specs[k]) + '</b>'; }).join('') + '</div>' : '<p class="muted small">Nothing beyond the name is known for this model.</p>')
        + (r.datasheet ? '<a class="link small" href="' + esc(r.datasheet) + '" target="_blank" rel="noopener">Datasheet</a>' : '');
    }).catch(function (err) { box.innerHTML = '<p class="muted small">' + esc(err.message) + '</p>'; }); }
  document.addEventListener('click', function (e) { var sm = e.target.closest && e.target.closest('.specs > summary'); if (sm) loadSpecs(sm.parentNode); });
  document.addEventListener('click', function (e) { var b = e.target.closest && e.target.closest('.addbtn'); if (!b) return;
    var box = b.closest('.adddev'), top = box.querySelector('.ad-top').value, bottom = box.querySelector('.ad-bottom').value, kind = box.querySelector('.ad-kind').value || null; b.disabled = true;
    api('/api/video/' + RES.id + '/device', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ top: top, bottom: bottom, kind: kind }) })
      .then(function (r) { toast('D' + r.index + ' added.'); return api('/api/video/' + RES.id + '/view').then(function (v) { RES.view = v; RES.geo = null; drawLayer(v, LAY, false); selectDevice(r.index); }); })
      .catch(function (err) { toast(err.message); b.disabled = false; }); });
  document.addEventListener('click', function (e) { var b = e.target.closest && e.target.closest('.rmbtn'); if (!b) return;
    if (!b.classList.contains('armed')) { b.classList.add('armed'); b.textContent = 'Tap again to remove D' + b.dataset.dev; setTimeout(function () { if (b.isConnected && !b.disabled) { b.classList.remove('armed'); b.textContent = 'Not a device? Remove it'; } }, 4000); return; }
    b.disabled = true; b.textContent = 'Removing…';
    api('/api/video/' + RES.id + '/device/' + b.dataset.dev + '/remove', { method: 'POST' })
      .then(function () { toast('D' + b.dataset.dev + ' removed. The devices below it moved up one number.'); return api('/api/video/' + RES.id + '/view').then(function (v) { RES.view = v; RES.geo = null; drawLayer(v, LAY, false); selectUnit(b.dataset.top); }); })
      .catch(function (err) { toast(err.message); b.disabled = false; b.classList.remove('armed'); b.textContent = 'Not a device? Remove it'; }); });
  function closeupBtn(d) { return '<div class="up slim"><label class="btn primary file"><input type="file" accept="image/*" capture="environment" class="closeup-in" data-dev="' + d.index + '" hidden><svg class="ic"><use href="#i-upload"/></svg>Take a close photo</label><p class="muted small upmsg" hidden></p></div>'; }
  // a port-bearing device's state (user, 2026-10-27): identified = a template fitted OR the make and model known (typed or read)
  // even with no layout to lay over; auto = the port model found the ports but no make and model (the overlay is shown, a
  // close-up is asked for); needs a close-up; reading; not read yet
  function devState(d, v) { var L = d.layout || {}, P = L.pictures || {};
    var busy = L.ports_state === 'running' || (L.mapped && !L.fitted && !L.auto && v.pending_ports);
    var mm = L.vendor ? L.vendor + (L.model ? ' ' + L.model : '') : L.typed ? L.typed.vendor + ' ' + L.typed.model : '';
    if (L.mode === 'patchpanel' && !(L.ports || []).length) return { key: 'unc', word: 'No ports seen', title: 'Patch panel', pic: d.crop, ask: true, panel: true };
    if (L.mode === 'patchpanel') return { key: 'on', word: 'Ports found', title: mm || ((L.panel ? L.panel + ' ' : '') + 'patch panel'), pic: P.overlay || d.crop, facts: true, panel: true, vendor: L.vendor, model: L.model };
    if (L.fitted) return { key: 'on', word: 'Identified', title: mm, pic: P.overlay || P.vendor_ports || P.trace || P.cut || d.crop, facts: true, vendor: L.vendor, model: L.model };
    if (busy) return { key: 'busy', word: 'Reading', title: 'Reading…', pic: d.crop };
    if (mm && L.mapped) return { key: 'on', word: 'Identified', title: mm, pic: L.auto && P.overlay ? P.overlay : d.crop, facts: !!L.auto, vendor: L.vendor || L.typed.vendor, model: L.model || L.typed.model, nolayout: !L.auto };
    if (L.auto) return { key: 'auto', word: (L.ports || []).length ? (L.ports.length === 1 ? '1 port found' : L.ports.length + ' ports found') : 'Ports found', title: L.vendor_read ? L.vendor_read + ' · model not read' : 'Make and model not read', pic: P.overlay || d.crop, facts: true, ask: true };
    if (L.mapped) return { key: 'unc', word: 'Needs a close photo', title: L.vendor_read ? L.vendor_read + (L.model_read ? ' ' + L.model_read : ' · model not read') : 'Make and model not read', pic: d.crop, ask: true };
    return { key: 'off', word: L.ports_state === 'failed' ? 'Could not find it' : 'Not checked yet', title: 'Not checked yet', pic: d.crop, read: true };
  }
  var CABLE_HEX = { white: '#f1f1ee', grey: '#9aa1a6', gray: '#9aa1a6', blue: '#2f6bd8', yellow: '#f2c230', green: '#27a35a', red: '#d8473f', orange: '#ee8a2a', black: '#2a2d2f', purple: '#8a5bd1', pink: '#e47fb2', aqua: '#36b9c9', brown: '#8a5a35' };
  function cableHex(c) { return CABLE_HEX[String(c || '').toLowerCase()] || '#cfd4d0'; }
  function cableName(p) { var t = String(p.cable_type || '').replace(/_/g, '-').replace(/^RJ-45/i, 'RJ45'); return t || 'cable'; }   // "RJ_45 White" -> "RJ45 White"
  function portOverlay(d, L, P) {
    var ports = (L.ports || []).concat(L.combo || []).filter(function (p) { return p.polygon && p.polygon.length >= 3; }); if (!P.cut || !ports.length) return null;   // a combo port's SFP cage is drawn too
    var hs = ports.map(function (p) { var ys = p.polygon.map(function (q) { return q[1]; }); return Math.max.apply(null, ys) - Math.min.apply(null, ys); }).sort(function (a, b) { return a - b; }), fs = Math.max(6, hs[hs.length >> 1] * 0.55);
    var marks = ports.map(function (p, i) { var xs = p.polygon.map(function (q) { return q[0]; }), ys = p.polygon.map(function (q) { return q[1]; }), cx = xs.reduce(function (a, b) { return a + b; }, 0) / xs.length, top = Math.min.apply(null, ys);
      var pts = p.polygon.map(function (q) { return q[0].toFixed(1) + ',' + q[1].toFixed(1); }).join(' ');
      var tip = 'Port ' + p.id + ' · ' + typeName(p.type) + ' · ' + (p.status === 'connected' ? 'connected' + (p.cable_type ? ' · ' + cableName(p) : '') : p.status === 'empty' ? 'empty' : 'not sure');
      return '<polygon class="pbox ' + (p.status === 'connected' ? 'on' : p.status === 'empty' ? 'off' : 'unc') + (p.cable_colour ? ' cab' : '') + '" style="--i:' + i + (p.cable_colour ? ';--cc:' + cableHex(p.cable_colour) : '') + '" pathLength="1" points="' + pts + '"><title>' + esc(tip) + '</title></polygon>'
        + '<text class="pnum" style="--i:' + i + '" x="' + cx.toFixed(1) + '" y="' + (top - fs * 0.35).toFixed(1) + '" font-size="' + fs.toFixed(1) + '">' + esc(p.id) + '</text>'; }).join('');
    var cab = {}; ports.forEach(function (p) { if (p.status === 'connected' && p.cable_type) { var k = cableName(p); cab[k] = (cab[k] || 0) + 1; } });
    var cabLine = Object.keys(cab).length ? '<p class="cabline"><span class="cabk">Cables</span>' + Object.keys(cab).sort(function (a, b) { return cab[b] - cab[a]; }).map(function (k) { var w = k.split(' '); return '<span><i style="--cc:' + cableHex(w[w.length - 1]) + '"></i>' + cab[k] + ' × ' + esc(w.length > 1 ? w.slice(1).join(' ').toLowerCase() + ' ' + w[0] : k) + '</span>'; }).join('') + '</p>' : '';
    return '<figure class="pshot cine one svgov" id="cine" title="Tap to play again"><img class="c-base" src="' + P.cut + '" alt="D' + d.index + '"><svg class="c-svg" viewBox="0 0 1000 200" preserveAspectRatio="none" aria-hidden="true">' + marks + '</svg><span class="c-scan"></span>' + '</figure>' + cabLine;
  }
  function portSection(d, v) {
    if (!PORT_KINDS[d.kind]) return '';
    var st = devState(d, v), L = d.layout || {};
    var P = L.pictures || {}, body = L.fitted && P.overlay
      ? '<figure class="pshot cine" id="cine" title="Tap to play again"><img class="c-base" src="' + (P.cut || d.crop) + '" alt=""><img class="c-over" src="' + P.overlay + '" alt="D' + d.index + '"><span class="c-scan"></span>' + '</figure>'
      : '<figure class="pshot rv2"><img src="' + st.pic + '" alt="D' + d.index + '" loading="lazy"></figure>';
    if (st.key === 'on') body += (L.fitted ? '' : L.auto ? '<p class="pline">Ports found.</p>' : '<p class="pline">No port drawing for this model yet.</p>') + (st.facts ? portFacts(L) : '')
      + '<details class="more specs" data-vendor="' + esc(st.vendor || '') + '" data-model="' + esc(st.model || '') + '" data-combo="' + ((L.combo || []).length) + '" data-spec="' + esc(L.spec ? JSON.stringify(L.spec) : '') + '"><summary class="link">Specifications</summary><div class="morebody specbody"><p class="muted small">Looking up…</p></div></details>' + (L.confirm ? closeupBtn(d) : '');
    else if (st.key === 'busy') body += '<p class="pline">Finding the make and model in the video, about a minute…</p>';
    else if (st.key === 'auto') body += '<p class="pline">Ports found. ' + (L.vendor_read ? 'Make read: <b>' + esc(L.vendor_read) + '</b>, model not read. ' : 'The make and model were not read. ') + 'A close, sharp photo of the front gives the model and its ports.</p>' + portFacts(L) + closeupBtn(d) + modelForm(d);
    else if (st.key === 'unc') body += '<p class="pline">' + (L.model_read ? 'Read once, not confirmed. ' : L.vendor_read ? 'The model was not read. ' : 'The make and model were not read. ') + 'A close, sharp photo of the front gives the model and its ports.</p>' + closeupBtn(d) + modelForm(d);
    else body += '<button class="btn primary readports" type="button" data-dev="' + d.index + '"><svg class="ic"><use href="#i-refresh"/></svg>' + (L.ports_state === 'failed' ? 'Read again' : 'Read the make and model') + '</button>' + modelForm(d);
    return '<section class="card dvports">' + (st.key === 'on' ? '' : '<div class="ph2"><b>Ports</b><span class="stat ' + st.key + '"><i></i>' + st.word + '</span></div>') + body + '</section>';
  }
  document.addEventListener('click', function (e) { var b = e.target.closest && e.target.closest('.readall'); if (!b) return; b.disabled = true;
    var ids = b.dataset.devs.split(','); Promise.all(ids.map(function (i) { return api('/api/video/' + RES.id + '/ports/' + i, { method: 'POST' }).catch(quiet('reading D' + i)); }))
      .then(function () { toast('Reading ' + ids.length + ' devices, about a minute each.'); if (RES.view) RES.view.pending_ports = true; watchPorts(); loadView(false); }); });
  function countUp(root) {                                              // the numbers run up to their value, the ring fills
    var t0 = performance.now(), els = [].slice.call(root.querySelectorAll('.num[data-n]'));
    (function step(t) { var k = Math.min(1, (t - t0) / 700), e = 1 - Math.pow(1 - k, 3); els.forEach(function (el) { el.textContent = Math.round(parseFloat(el.dataset.n) * e); }); if (k < 1) requestAnimationFrame(step); })(t0);
    var ring = root.querySelector('.ring .val'); if (ring) requestAnimationFrame(function () { ring.style.strokeDashoffset = ring.dataset.off; });
  }
  // the Report tab (user, 2026-10-27, 'completely change the report'): one page a person can read top to bottom - the rack's
  // name and the sweep, the space in one line, a drawn elevation (units, devices, kinds) next to the key numbers, the devices as
  // a table in rack order, the free space, what needs a look. No model internals; the 15-step page stays behind Technical details.
  // ---- the web-only side panels (hidden below 1000 px): more about what is selected (user, 2026-10-29)
  window.imgRetry = function (img) { var n = parseInt(img.dataset.retry || '0', 10); if (n >= 2) { if (img.closest('.sideframes')) img.remove(); else img.classList.add('broken'); return; } img.dataset.retry = n + 1; var src = img.getAttribute('src').replace(/([?&])r=\d+/, ''); setTimeout(function () { img.src = src + (src.indexOf('?') >= 0 ? '&' : '?') + 'r=' + Date.now(); }, 1500 * (n + 1)); };
  function renderRackSide(v) {
    var el = $('rack-side'); if (!el) return;
    var s = v.rack.summary || {}, units = v.units || [], devs = v.devices || [];
    var st = function (u) { return unitState(v, u); };
    var held = {}; (v.record_passive || []).forEach(function (x) { held[x.unit] = x; });      // the record's cable managers and blank panels are not free space (finding 15)
    var runs = [], cur = null; units.forEach(function (u) { if (st(u) === 'Free' && !held[u.unit]) { if (cur) { cur.bottom = u.unit; cur.n++; } else { cur = { top: u.unit, bottom: u.unit, n: 1 }; runs.push(cur); } } else cur = null; });
    var heldFree = units.filter(function (u) { return st(u) === 'Free' && held[u.unit]; });
    var kinds = {}; devs.forEach(function (d) { var k = d.kind && d.kind !== 'Other' ? d.kind : 'Not sure'; kinds[k] = (kinds[k] || 0) + 1; });
    var used = units.filter(function (u) { return st(u) === 'In use'; }).length, free = units.filter(function (u) { return st(u) === 'Free'; }).length - heldFree.length, unc = units.length - used - free - heldFree.length;
    var big = runs.length ? Math.max.apply(null, runs.map(function (r) { return r.n; })) : 0;
    var html = '<div class="card sidecard"><span class="glabel">This rack</span><div class="sidekpis"><div><b>' + units.length + 'U</b><span>units</span></div><div><b>' + used + '</b><span>in use</span></div><div><b class="free">' + free + '</b><span>free</span></div>' + (unc ? '<div><b class="unc">' + unc + '</b><span>not sure</span></div>' : '') + (big ? '<div><b class="free">' + big + 'U</b><span>largest free</span></div>' : '') + '</div>'
      + '<div class="ubar sideubar">' + units.map(function (u, i) { var k = st(u); return '<i class="' + (k === 'Free' ? 'free' : k === 'Not sure' ? 'unc' : '') + '" style="--i:' + i + '" data-unit="' + u.unit + '" title="' + UP(u.unit) + ' · ' + k.toLowerCase() + '"></i>'; }).join('') + '</div><div class="ubarcap"><span>' + UP(units[0] ? units[0].unit : 'U1') + '</span><span>' + UP(units.length ? units[units.length - 1].unit : '') + '</span></div>'
      + '<span class="glabel">Free space</span>' + (runs.length ? '<div class="fchips">' + runs.map(function (r) { return '<span class="fchip"><b>' + r.n + 'U</b> ' + URANGE(r.top, r.bottom, r.n) + '</span>'; }).join('') + '</div>' : '<p class="sideline">No free unit.</p>')
      + (heldFree.length ? '<p class="sideline">' + heldFree.map(function (u) { return UP(u.unit); }).join(', ') + (heldFree.length === 1 ? ' looks empty but holds a ' : ' look empty but hold ') + esc(String(held[heldFree[0].unit].kind).toLowerCase()) + (heldFree.length === 1 ? '' : 's') + ' in the record.</p>' : '') + '</div>';
    html += '<div class="card sidecard"><span class="glabel">Devices · ' + devs.length + '</span><p class="sideline">' + Object.keys(kinds).sort(function (a, b) { return kinds[b] - kinds[a]; }).map(function (k) { return kinds[k] + ' ' + esc(k.toLowerCase()); }).join(' · ') + '</p>'
      + '<div class="sidelist">' + devs.map(function (d) { var L = d.layout || {}, mm = L.vendor && L.model ? L.vendor + ' ' + L.model : L.mode === 'patchpanel' && L.panel ? L.panel + ' patch panel' : L.vendor ? L.vendor : '';
        return '<button class="srow" type="button" data-dev="' + d.index + '"><span class="iu">' + URANGE(d.top, d.bottom, d.height) + '</span><b>D' + d.index + '</b><span class="sk">' + esc(d.kind && d.kind !== 'Other' ? d.kind : 'Not sure') + (mm ? ' · ' + esc(mm) : '') + '</span>' + (L.ports && L.ports.length ? '<span class="sp">' + L.ports.length + (L.ports.length === 1 ? ' port' : ' ports') + '</span>' : '') + '</button>'; }).join('') + '</div></div>';
    el.innerHTML = html; [].forEach.call(el.children, function (c, i) { c.style.setProperty('--i', i); }); [].forEach.call(el.querySelectorAll('.srow'), function (c, i) { c.style.setProperty('--i', Math.min(i, 24)); });
    if (el.dataset.counted !== v.id) { el.dataset.counted = v.id; [].forEach.call(el.querySelectorAll('.sidekpis b'), function (b) { var m = b.textContent.match(/^(\d+)(.*)$/); if (!m) return; var to = +m[1], suf = m[2], t0 = performance.now(), dur = 700;
      var tick = function (t) { var k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3); b.textContent = Math.round(to * e) + suf; if (k < 1) requestAnimationFrame(tick); }; b.textContent = '0' + suf; requestAnimationFrame(tick); }); }
  }
  // hover a device in the list, or a unit on the bar: it lights up on the rack picture (web)
  function rackHi(y0, y1, label) {
    var svg = $('lay-svg'), G = RES.geo; if (!svg || !G) return; var g = $('rk-hi'); if (!g) { g = document.createElementNS('http://www.w3.org/2000/svg', 'g'); g.id = 'rk-hi'; svg.appendChild(g); } else svg.appendChild(g);
    if (y0 == null) { g.innerHTML = ''; return; }
    var fs = Math.max(10, Math.min(28, (y1 - y0) * 0.5)), w = label.length * fs * 0.62 + fs * 1.1;
    g.innerHTML = '<rect class="hi" x="' + (G.L - 4) + '" y="' + y0 + '" width="' + (G.R - G.L + 8) + '" height="' + (y1 - y0) + '" rx="6"/>'
      + '<g class="hil"><rect x="' + (G.R - w - 6) + '" y="' + (y0 + (y1 - y0) / 2 - fs * 0.75) + '" width="' + w + '" height="' + fs * 1.5 + '" rx="' + fs * 0.75 + '"/><text x="' + (G.R - w / 2 - 6) + '" y="' + (y0 + (y1 - y0) / 2) + '" font-size="' + fs + '">' + esc(label) + '</text></g>';
  }
  document.addEventListener('mouseover', function (e) {
    var t = e.target.closest && e.target.closest('#rack-side .srow, #rack-side .ubar i'); var G = RES.geo; if (!G) return;
    if (!t) { if (e.target.closest && !e.target.closest('#rack-side')) rackHi(null); return; }
    if (t.classList.contains('srow')) { var b = G.dbox[t.dataset.dev]; if (b) rackHi(b.y0, b.y1, 'D' + t.dataset.dev); }
    else { var i = (G.rows || []).indexOf(t.dataset.unit); if (i >= 0) rackHi(G.ds[i] - G.pitch / 2, G.ds[i] + G.pitch / 2, UP(t.dataset.unit)); }
  });
  document.addEventListener('mouseleave', function (e) { if (e.target && e.target.id === 'rack-side') rackHi(null); }, true);
  document.addEventListener('click', function (e) { var b = e.target.closest && e.target.closest('#rack-side .srow'); if (!b) return; selectDevice(parseInt(b.dataset.dev, 10)); go('result/' + RES.id + '/devices'); });
  function renderDevSide() {
    var el = $('dev-side'); if (!el) return;
    var v = RES.view, pick = RES.pick; if (!v || !pick || pick.kind !== 'dev') { el.innerHTML = ''; return; }
    var d = (v.devices || []).filter(function (x) { return x.index === pick.id; })[0]; if (!d) { el.innerHTML = ''; return; }
    var L = d.layout || {}, P = L.pictures || {}, ports = L.ports || [], html = '';
    var frames = (d.views || d.frames || []).slice(0, 3);
    if (frames.length) html += '<div class="card sidecard"><span class="glabel">Seen in the video</span><div class="sideframes">' + frames.map(function (f, i) { return '<img src="' + f + '" alt="D' + d.index + ' view ' + (i + 1) + '" loading="lazy" onerror="imgRetry(this)">'; }).join('') + '</div><p class="sideline">' + (frames.length === 1 ? 'The sharpest view of this device, used for the reading.' : 'The sharpest views of this device, used for the reading.') + ' ' + (matchMedia('(hover: none)').matches ? 'Tap' : 'Click') + ' a picture to see it large.</p></div>';
    var how = [];
    how.push(['Found on', d.units && d.units.length ? d.units.map(UP).join(', ') : UP(d.top)]);
    how.push(['Type', esc(d.kind_words || d.kind || 'Not sure')]);   // the words carry the picture's per cent (source_db.device_kind)   // the one kind and where it came from (server: source_db.device_kind)
    if (L.ports_state || L.mapped) how.push(['Ports read', L.from === 'close-up' ? 'from a close photo' : L.fitted ? 'vendor layout fitted' : L.auto ? 'found and typed from the video' : L.mode === 'patchpanel' ? 'patch panel reader' : L.ports_state === 'running' ? 'reading now' : ports.length ? 'from the video, no vendor layout' : 'not yet']);
    if (L.vendor_read || L.model_read) how.push(['Read on the panel', esc((L.vendor_read || '') + (L.model_read ? ' ' + L.model_read : '')) + (L.fitted ? '' : ' · not confirmed')]);
    var PD0 = L.port_detector || {};
    if (PD0.make_dropped) how.push(['Make', 'not read on this device. "' + esc(PD0.make_dropped) + '" is printed on the device next to it']);
    var C = L.confidence; if (C && typeof C === 'object' && !L.fitted && !PD0.make_dropped && (C.vendor || C.model)) { var cp = []; if (C.vendor != null) cp.push('make ' + Math.round(C.vendor * 100) + ' %'); if (C.model != null) cp.push('model ' + Math.round(C.model * 100) + ' %'); if (cp.length) how.push(['Read confidence', cp.join(' · ') + (C.vendor_doubt ? ' · could also be ' + esc(C.vendor_doubt) : '')]); }
    else if (typeof C === 'number' && L.fitted) how.push(['Fit', Math.round(C * 100) + ' %']);
    var PDt = L.port_detector; if (PDt && PDt.dropped) how.push(['Kept out', PDt.dropped + ' port' + (PDt.dropped > 1 ? 's' : '') + ' of the device next to it']);
    if (L.frames_voted) how.push(['Cables', 'read in ' + (L.frames_voted + 1) + ' views of the video, the answer most of them agree on']);
    if (L.numbering) how.push(['Numbering', esc(L.numbering === 'column' ? '1 top left, 2 below it' : L.numbering)]);
    if (L.size_known === false) how.push(['Panel size', 'not read: only the ports seen are counted']);
    html += '<div class="card sidecard"><span class="glabel">How it was read</span><div class="dfacts">' + how.map(function (r) { return '<span>' + r[0] + '</span><b>' + r[1] + '</b>'; }).join('') + '</div></div>';
    if (ports.length) html += '<div class="card sidecard"><span class="glabel">Every port</span><div class="sidetable"><table><thead><tr><th>Port</th><th>Type</th><th>State</th><th>Cable</th></tr></thead><tbody>'
      + ports.map(function (p) { var cab = p.cable_type ? '<i class="cdot" style="--cc:' + cableHex(p.cable_colour) + '"></i>' + esc(cableName(p)) + (p.cable_views && p.cable_views.views > 1 ? '<small class="cv" title="read in ' + p.cable_views.views + ' views">' + p.cable_views.views + ' views</small>' : '') : /cap/i.test(String(p.cap_state || '')) && !/no.?cap/i.test(String(p.cap_state)) ? 'dust cap' : ''; return '<tr><td class="mono">' + esc(p.id) + '</td><td>' + esc(typeName(p.type)) + '</td><td class="' + (p.status === 'connected' ? 'on' : p.status === 'empty' ? 'off' : 'unc') + '">' + (p.status === 'connected' ? 'Connected' : p.status === 'empty' ? 'Empty' : 'Not sure') + '</td><td>' + cab + '</td></tr>'; }).join('') + '</tbody></table></div></div>';
    if (P.rack_trace) html += '<div class="card sidecard"><span class="glabel">On the rack</span><img class="sidepic" src="' + P.rack_trace + '" alt="Ports of D' + d.index + ' on the rack" loading="lazy" onerror="imgRetry(this)"><p class="sideline">The ports traced back onto the rack picture.</p></div>';
    el.innerHTML = html; [].forEach.call(el.children, function (c, i) { c.style.setProperty('--i', i); }); [].forEach.call(el.querySelectorAll('.sidetable tbody tr'), function (c, i) { c.style.setProperty('--i', Math.min(i, 30)); });
  }
  // ---- the rack ID: always asked. The ID read off the label is offered first to verify; else the other racks' IDs, the next
  // in a sequence, or a typed one. The answer is the key the network layer will use (user, 2026-10-29).
  function renderRackAsk(v) {
    var el = $('rk-ask'); if (!el) return;
    var R = v.rack_id, RL = v.rack_label || {}, read = RL.id && RL.id.text, opts = v.rack_id_options || [];
    if (R && R.id && RES.rkEdit !== v.id) {
      var web = document.body.classList.contains('web');
      el.hidden = false; el.className = 'card rkask done' + (web ? '' : ' slim');
      if (!web) {                                                        // the phone: the line shows for 3 seconds after the answer, then folds away (user, 2026-10-30)
        var fresh = RES.rkJust === v.id + '|' + R.id; RES.rkJust = null;
        if (fresh) { el.innerHTML = '<div class="rkrow"><span class="sig"></span><div><b>Rack ID verified</b></div></div>'; el.classList.add('flash');
          clearTimeout(RES.rkT); RES.rkT = setTimeout(function () { el.classList.add('fold'); setTimeout(function () { el.hidden = true; el.classList.remove('fold', 'flash'); applyFit(); }, 420); }, 3000); }
        else el.hidden = true;
        var cr = $('caprow'); if (cr && !cr.querySelector('.rkchange')) $('fit-tog').insertAdjacentHTML('beforebegin', '<button class="link rkchange" type="button" data-rkid-clear>Change ID</button>');
        renderNext(v); return; }
      var rc = document.querySelector('#caprow .rkchange'); if (rc) rc.remove();
      el.innerHTML = '<div class="rkrow"><span class="sig"></span><div><b>Rack ' + esc(R.id) + '</b><small>' + esc(R.source === 'verified from the label' ? 'verified from the label' : R.source === 'typed' ? 'entered' : 'chosen') + (R.on ? ' · ' + esc(when(R.on)) : '') + '</small></div><button class="link" type="button" data-rkid-clear>Change</button></div>';
      renderNext(v); return;
    }
    renderNext(v);
    if (R && R.skipped) { el.hidden = false; el.className = 'card rkask soft'; el.innerHTML = '<div class="rkrow"><div><b>Rack ID not given yet</b><small>Needed to link this rack to the network layer.</small></div><button class="link" type="button" data-rkid-clear>Give it</button></div>'; return; }
    el.hidden = false; el.className = 'card rkask';
    var chips = opts.map(function (o, i) { return '<button class="rkopt' + (i === 0 && read && o.id === read ? ' first' : '') + '" type="button" data-rkid="' + esc(o.id) + '"><b>' + esc(o.id) + '</b><small>' + esc(o.why) + '</small></button>'; }).join('');
    el.innerHTML = '<div class="rkhead"><b>' + (read ? 'Is this rack ' + esc(read) + '?' : v.rack_label_running ? 'Which rack is this? Reading the label…' : 'Which rack is this?') + '</b><small>' + (read ? 'Read off the label in the video. Confirm it, pick another, or type the ID.' : 'No ID could be read off the label. Pick one or type it.') + ' The ID links this rack to the network layer.</small></div>'
      + (chips ? '<div class="rkopts">' + chips + '</div>' : '')
      + '<form class="rktype" data-rkid-form><input type="text" name="id" placeholder="Type the rack ID, e.g. SP-HYD-RM01-R01-R2" autocomplete="off" spellcheck="false"><button class="btn primary" type="submit">Save</button></form>'
      + '<button class="link rkskip" type="button" data-rkid-skip>Not now</button>';
  }
  // what to do next with this rack: one line, the first open step (user, 2026-10-30: no clear next step after a scan)
  function nextSteps(v) {
    var devs = v.devices || [], R = v.rack_id;
    var unsure = devs.filter(function (d) { return d.unsure || !d.kind || d.kind === 'Other'; });
    var unread = devs.filter(function (d) { return PORT_KINDS[d.kind] && !(d.layout && (d.layout.mapped || d.layout.ports_state)); });
    var steps = [
      { done: !!(R && R.id), text: 'Verify the rack ID', go: null },
      { done: !(v.units || []).some(function (u) { return u.status === 'uncertain'; }), text: 'Answer ' + (v.units || []).filter(function (u) { return u.status === 'uncertain'; }).length + ' units marked Not sure', go: (function () { var u = (v.units || []).filter(function (u) { return u.status === 'uncertain'; })[0]; return u ? { unit: u.unit } : null; })() },
      { done: !unsure.length, text: 'Check ' + unsure.length + (unsure.length === 1 ? ' device' : ' devices') + ' marked Not sure', go: unsure.length ? { dev: unsure[0].index } : null },
      { done: !unread.length, text: 'Read the ports of ' + unread.length + (unread.length === 1 ? ' device' : ' devices'), go: unread.length ? { dev: unread[0].index } : null }
    ];
    return steps;
  }
  function renderNext(v) {
    var el = $('rk-next'); if (!el) return;
    var steps = nextSteps(v), open = steps.filter(function (s) { return !s.done; }), done = steps.length - open.length;
    if (!open.length) { el.hidden = true; el.className = 'nextstrip alldone'; el.innerHTML = '<span class="nx-k">Done</span><b>Every device checked</b><small>Open the report</small><span class="nx-arrow">›</span>'; el.href = '#result/' + v.id + '/report'; el.dataset.dev = ''; return; }
    var s0 = open[0];
    el.hidden = false; el.className = 'nextstrip'; el.innerHTML = '<span class="nx-k">Next</span><b>' + esc(s0.text) + '</b><small>' + done + ' of ' + steps.length + ' done</small><span class="nx-arrow">›</span>';
    el.href = s0.go ? '#result/' + v.id + '/devices' : '#'; el.dataset.dev = s0.go && s0.go.dev ? s0.go.dev : ''; el.dataset.unit = s0.go && s0.go.unit ? s0.go.unit : '';
  }
  document.addEventListener('click', function (e) { var a = e.target.closest && e.target.closest('#rk-next'); if (!a) return;
    if (a.dataset.unit) { e.preventDefault(); selectUnit(a.dataset.unit); go('result/' + RES.id + '/devices'); return; }
    if (a.dataset.dev) { e.preventDefault(); selectDevice(parseInt(a.dataset.dev, 10)); go('result/' + RES.id + '/devices'); }
    else if (a.getAttribute('href') === '#') { e.preventDefault(); var ask = $('rk-ask'); if (ask) { ask.scrollIntoView({ behavior: 'smooth', block: 'start' }); ask.classList.remove('nudge'); void ask.offsetWidth; ask.classList.add('nudge'); } } });
  document.addEventListener('click', function (e) {                     // step 2 answers: in use / free, or part of the device next to it
    var b = e.target.closest && e.target.closest('[data-umark]'); if (!b) return;
    var unit = b.dataset.unit, status = b.dataset.umark, box = b.closest('.umark');
    if (status === 'occupied' && !b.dataset.join && !b.dataset.new && box && box.querySelector('.umjoin')) { box.querySelector('.umjoin').hidden = false; box.querySelector('.umbtns').hidden = true; return; }
    if (b.dataset.new) { var f = document.querySelector('.adddev'); if (f) { f.scrollIntoView({ behavior: 'smooth', block: 'center' }); f.classList.remove('nudge'); void f.offsetWidth; f.classList.add('nudge'); } }
    b.disabled = true;
    api('/api/video/' + RES.id + '/unit/' + unit, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b.dataset.join ? { status: status, join: parseInt(b.dataset.join, 10) } : { status: status }) })
      .then(function (r) { toast(r.removed ? UP(unit) + ' is free. D' + r.removed + ' had no unit left and was taken out.' : r.device && status === 'occupied' ? UP(unit) + ' is in use, part of D' + r.device + '.' : UP(unit) + (status === 'occupied' ? ' is in use.' : ' is free.'));
        return api('/api/video/' + RES.id + '/view').then(function (v) { RES.view = v; RES.geo = null; drawLayer(v, LAY, false); renderRackSide(v); renderRackAsk(v); selectUnit(unit); }); })
      .catch(function (err) { b.disabled = false; toast(err.message); });
  });
  function saveRackId(body) { RES.rkEdit = null; if (body.id) RES.rkJust = RES.id + '|' + body.id; return api('/api/video/' + RES.id + '/rack-id', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(function () { return loadView(false); }).catch(function (e) { toast(e.message); }); }
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('#rk-ask [data-rkid], #rk-ask [data-rkid-skip], [data-rkid-clear]'); if (!b) return;
    if (b.dataset.rkid) saveRackId({ id: b.dataset.rkid });
    else if ('rkidSkip' in b.dataset) { if (RES.rkEdit === RES.id) { RES.rkEdit = null; renderRackAsk(RES.view); } else saveRackId({ skip: true }); }
    else if (RES.view && RES.view.rack_id && RES.view.rack_id.id) { RES.rkEdit = RES.id; renderRackAsk(RES.view); }   // Change: ask again, but the confirmed ID stays until a new one is saved (Not now keeps it)
    else saveRackId({ clear: true });
  });
  document.addEventListener('submit', function (e) { var f = e.target.closest && e.target.closest('#rk-ask [data-rkid-form]'); if (!f) return; e.preventDefault(); var id = f.id.value.trim(); if (!id) return; saveRackId({ id: id, typed: true }); });
  function renderReport(v) {
    var units = v.units || [], devs = (v.devices || []).slice().sort(function (a, b) { return unumOf(b.top) - unumOf(a.top); }), n = units.length;
    var st = function (u) { return unitState(v, u); }, RL = v.rack_label || {}, rid = (v.rack_id && v.rack_id.id) || (RL.id && RL.id.text), rname = RL.name && RL.name.text;
    var held0 = {}; (v.record_passive || []).forEach(function (x) { held0[x.unit] = 1; });
    var used = units.filter(function (u) { return st(u) === 'In use'; }).length, unc = units.filter(function (u) { return st(u) === 'Not sure'; }).length,
      free = n - used - unc - units.filter(function (u) { return st(u) === 'Free' && held0[u.unit]; }).length;     // the record's cable managers are not free
    var kinds = byKind(devs), kindLine = kinds.map(function (g) { return g.devs.length + ' ' + (g.kind === 'Not sure' ? 'not sure' : g.kind.toLowerCase() + (g.devs.length > 1 ? (/ch$/.test(g.kind) ? 'es' : 's') : '')); }).join(' · ');
    var title = rid || (rname ? rname.replace(/\b(\w)(\w*)/g, function (m, a, b) { return a + b.toLowerCase(); }) : 'This rack');
    // the elevation: every unit a row, devices as tiles in their kind's colour, free units dotted green
    var UH = n > 30 ? 10 : 13, W = 300, L0 = 30, R0 = W - 8, H = n * UH + 8, byTop = {}; devs.forEach(function (d) { byTop[d.top] = d; });
    var rows = units.map(function (u, i) { var y = 4 + i * UH, s = st(u), d = byTop[u.unit];
      var tick = (unumOf(u.unit) % 5 === 0 || unumOf(u.unit) === 1 || i === 0) ? '<text class="eu" x="' + (L0 - 5) + '" y="' + (y + UH / 2) + '">' + UP(u.unit) + '</text>' : '';
      if (d) { var g = kinds.filter(function (k) { return k.devs.indexOf(d) >= 0; })[0], hgt = d.height * UH - 2, mm = devName(d).replace(/<[^>]+>/g, ''); mm = mm && mm.indexOf('not read') < 0 && mm.indexOf('not confirmed') < 0 ? mm : '';
        return tick + '<g class="ed" data-dev="' + d.index + '" style="--i:' + i + '"><rect x="' + L0 + '" y="' + (y + 1) + '" width="' + (R0 - L0) + '" height="' + hgt + '" rx="3" fill="' + g.colour + '" fill-opacity=".14"/><rect x="' + L0 + '" y="' + (y + 1) + '" width="4" height="' + hgt + '" rx="2" fill="' + g.colour + '"/>'
          + '<text class="et" x="' + (L0 + 10) + '" y="' + (y + 1 + hgt / 2) + '">D' + d.index + (mm ? ' · ' + esc(mm) : d.kind && d.kind !== 'Other' ? ' · ' + esc(d.kind) : '') + '</text></g>'; }
      var covered = devs.some(function (d2) { return (d2.units || []).indexOf(u.unit) >= 0; }); if (covered) return tick;
      return tick + '<rect class="es ' + (s === 'Free' ? 'free' : s === 'Not sure' ? 'unc' : 'on') + '" x="' + L0 + '" y="' + (y + 1) + '" width="' + (R0 - L0) + '" height="' + (UH - 2) + '" rx="2"/>'; }).join('');
    var elev = '<svg class="elev" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="The rack, top to bottom"><rect x="' + (L0 - 2) + '" y="2" width="' + (R0 - L0 + 4) + '" height="' + (H - 4) + '" rx="6" class="ef"/>' + rows + '</svg>';
    // the devices, top to bottom, in a table
    var table = devs.map(function (d) { var L = d.layout || {}, mm = L.vendor ? L.vendor + (L.model ? ' ' + L.model : '') : '', kind = d.kind && d.kind !== 'Other' ? d.kind : 'Not sure';
      return '<tr data-dev="' + d.index + '"><td class="mono">' + URANGE(d.top, d.bottom, d.height) + '</td><td><b>D' + d.index + '</b><span class="kd">' + esc(kind) + '</span></td><td>' + (mm ? esc(mm) : L.mode === 'patchpanel' && L.panel ? esc(L.panel) + ' patch panel' + (L.n_ports ? '<span class="kd">' + L.n_ports + (L.size_known === false ? ' ports seen' : ' ports') + '</span>' : '') : L.vendor_read ? esc(L.vendor_read + (L.model_read ? ' ' + L.model_read : '')) + '<span class="kd">' + (L.model_read ? 'read, not confirmed' : 'model not read') + '</span>' : '<span class="kd">not read</span>') + '</td></tr>'; }).join('');   // a patch panel has no make and model to read: its type stands there (user, 2026-10-28)
    // the free space as stretches
    // a unit the confirmed record fills with a cable manager or a blank panel is not free space, though the video sees it empty
    var passive = {}; (v.record_passive || []).forEach(function (x) { passive[x.unit] = x; });
    var runs = [], cur = null; units.forEach(function (u) { var s = st(u); if (s === 'Free' && !passive[u.unit]) { if (cur) { cur.bottom = u.unit; cur.n++; } else { cur = { top: u.unit, bottom: u.unit, n: 1 }; runs.push(cur); } } else cur = null; });
    var held = units.filter(function (u) { return st(u) === 'Free' && passive[u.unit]; });
    var freeChips = runs.map(function (r) { return '<span class="fchip"><b>' + r.n + 'U</b> ' + URANGE(r.top, r.bottom, r.n) + '</span>'; }).join('')
      + (held.length ? '<p class="muted small">' + held.map(function (u) { return UP(u.unit); }).join(', ') + (held.length === 1 ? ' looks empty but holds a ' : ' look empty but hold ') + esc(String(passive[held[0].unit].kind).toLowerCase()) + (held.length === 1 ? '' : 's') + ' in the ' + esc(passive[held[0].unit].source) + ', so ' + (held.length === 1 ? 'it is' : 'they are') + ' not free space.</p>' : '');
    var look = units.filter(function (u) { return st(u) === 'Not sure'; }).map(function (u) { return '<button class="lrow" type="button" data-unit="' + u.unit + '"><span class="iu">' + UP(u.unit) + '</span><b>Not sure if in use</b><span class="arr">›</span></button>'; })
      .concat(devs.map(function (d) { var ps = PORT_KINDS[d.kind] ? devState(d, v) : null, why = d.unsure ? 'device not sure' : !d.kind || d.kind === 'Other' ? 'type not sure'
          : ps && ps.key === 'unc' ? (ps.panel ? 'no ports seen' : 'needs a close photo') : ps && ps.key === 'auto' ? 'model not read' : ps && ps.key === 'off' ? 'make and model not checked yet' : null;   // the device page's own state
        return why ? '<button class="lrow" type="button" data-dev="' + d.index + '"><span class="iu">' + UP(d.top) + '</span><b>D' + d.index + ' · ' + why + '</b><span class="arr">›</span></button>' : null; }).filter(Boolean))
      // the ports not sure, one row per device, as the worklist asks them ("Is there a cable in port 2 of D3?")
      .concat(devs.map(function (d) { var un = ((d.layout || {}).ports || []).filter(function (p) { return p.status !== 'connected' && p.status !== 'empty'; });
        return un.length ? '<button class="lrow" type="button" data-dev="' + d.index + '"><span class="iu">' + UP(d.top) + '</span><b>D' + d.index + ' · ' + (un.length === 1 ? 'port ' + esc(un[0].id) + ' not sure' : un.length + ' ports not sure: ' + esc(un.slice(0, 6).map(function (p) { return p.id; }).join(', ')) + (un.length > 6 ? ' and ' + (un.length - 6) + ' more' : '')) + '</b><span class="arr">›</span></button>' : null; }).filter(Boolean));
    $('rp-top').innerHTML = '<header class="rphead"><span class="eyebrow">Rack report</span><h2>' + esc(title) + '</h2><p class="muted">' + esc(v.title || '') + '</p><p class="rpline"><b>' + n + 'U</b> · <b>' + used + '</b> in use · <b class="free">' + free + '</b> free' + (unc ? ' · <b class="unc">' + unc + '</b> not sure' : '') + '</p></header>'
      + '<div class="rpcol b"><section class="card rpelev"><div class="rpkpis"><div><b>' + devs.length + '</b><span>devices</span></div><div><b>' + kinds.length + '</b><span>kinds</span></div><div><b>' + free + 'U</b><span>free</span></div><p class="muted small">' + esc(kindLine) + '</p></div>' + elev + '</section>'
      + '</div><div class="rpcol a"><span class="glabel">Devices, top to bottom</span><div class="card flush"><table class="rptable"><thead><tr><th>Unit</th><th>Device</th><th>Make and model</th></tr></thead><tbody>' + table + '</tbody></table></div></div><div class="rpcol c">'
      + '<span class="glabel">Free space</span><div class="fchips">' + (freeChips || '<span class="muted small">No free unit.</span>') + '</div>'
      + '<span class="glabel">Needs a look</span>' + (look.length ? '<div class="card flush look">' + look.join('') + '</div>' : '<div class="card look none"><i class="sig"></i>Nothing. Every unit and device is clear.</div>') + '</div>';
  }
  document.addEventListener('click', function (e) { var t = e.target.closest && e.target.closest('#rp-top .ed, #rp-top tr[data-dev]'); if (t) { selectDevice(parseInt(t.dataset.dev, 10)); go('result/' + RES.id + '/devices'); } });
  document.addEventListener('click', function (e) { var b = e.target.closest && e.target.closest('#rp-top .lrow'); if (!b) return;
    if (b.dataset.unit) selectUnit(b.dataset.unit); else selectDevice(parseInt(b.dataset.dev, 10)); go('result/' + RES.id + '/devices'); });
  // the Drift tab: choose the database for this rack (Excel, NetBox or CMDB), see which record matched and why, and every difference
  // ---------------------------------------------------------------- 3 The record and 4 Differences: compare the run against one database copy
  // (copying in and the logins live under Profile: Database copies and Connections)
  var HOW = { exact: 'exactly', close: 'with spaces, dashes and case ignored', picked: 'picked by a person', ladder: 'by the steps above', confirmed: 'as a person confirmed before' };
  function pc(x) { return x == null ? '' : Math.round(x * 100) + ' %'; }
  var OUTCOME = { one: 'named one rack', narrowed: 'narrowed the racks', skipped: 'skipped', none: 'no single rack', tied: 'tied' };
  var HINT = { missing: 'Check whether the device is still in the rack. If the video did not reach that unit, scan the whole rack.',
    extra: 'A device is in the rack that the record does not list: add it to the record, or check the reading under Devices.',
    moved: 'The device sits at another unit than the record says: update the record, or check the unit numbers on the rack picture.',
    height: 'The record and the video give the device a different size.', kind: 'The record and the video name a different kind of device.',
    make: 'The faceplate reads another make than the record.', model: 'The faceplate reads another model than the record.', serial: 'The serial read differs from the record.',
    port_type: 'Look at the port: if the video is right, the record has the wrong port type.', port_occupied: 'A cable is where the record expects none, or missing where it expects one.',
    dust_cap: 'An empty port the record expects capped has no dust cap.' };
  function loadDrift() { var id = RES.id; api('/api/video/' + id + '/drift').then(function (d) { if (RES.id === id) renderCompare(d); }).catch(function (e) { $('df-top').innerHTML = '<p class="err">' + esc(e.message) + '</p>'; }); }
  // one line under the compare button: a spinner while something runs, one sentence if it fails
  function cmpStatus(text, kind) { var el = $('cmp-status'); if (!el) return; if (!text) { el.hidden = true; el.innerHTML = ''; return; } el.hidden = false; el.className = 'cmpstatus ' + (kind || ''); el.setAttribute('role', kind === 'err' ? 'alert' : 'status'); el.innerHTML = (kind === 'err' ? '' : '<span class="spin" aria-hidden="true"></span>') + '<span>' + esc(text) + '</span>'; }
  // while a database is being compared, the compare owns the top of the Report: the summary and device table step aside
  function cmpMode(on) { var r = $('res-report'); if (!r) return; r.classList.toggle('cmp-on', !!on); $('cmp-back').hidden = !on; if (on) { cmpStatus('Starting...'); window.scrollTo(0, 0); } else { cmpStatus(''); $('df-top').innerHTML = ''; $('diff-top').innerHTML = ''; } }
  // each compare section shows only when it holds something; set here and not with :has(:empty) in CSS, which Safari 26.4 does not re-check when the panel is filled in, so the area stayed blank
  [['sec-compare', 'df-top'], ['sec-diff', 'diff-top']].forEach(function (p) { var sec = $(p[0]), box = $(p[1]); if (!sec || !box) return; var sync = function () { sec.hidden = !box.firstChild; }; new MutationObserver(sync).observe(box, { childList: true }); sync(); });
  function setDb(body) { return api('/api/video/' + RES.id + '/database', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(function (d) { cmpStatus(''); renderCompare(d); return d; }).catch(function (e) { cmpStatus(String(e.message || 'The compare did not run.').replace(/\s*$/, '').replace(/([^.!?])$/, '$1.'), 'err'); }); }
  function copyWhen(d, s) { return d.imported[s] ? 'copy of ' + when(d.imported[s]) : 'no copy yet'; }
  var SRCLABEL = { excel: 'Excel workbook', netbox: 'NetBox', cmdb: 'ServiceNow CMDB' };
  // the first sentence in full (never cut mid-word), starting with a capital letter
  function firstSentence(t) { t = String(t || '').replace(/\s+/g, ' ').trim(); var m = t.match(/^.*?[.!?](\s|$)/); t = m ? m[0].trim() : t; return t.charAt(0).toUpperCase() + t.slice(1); }
  // the compare, in the owner's order: a button, a dropdown of the three databases, one panel to confirm the rack id, then the differences.
  // RES.cmpUi: idle (nothing shown) > confirm (the panel) > drift (the differences)
  function stepsHtml(I) { return '<ul class="idlines">' + (I.steps || []).filter(function (st) { return st.outcome !== 'skipped' || st.n === 0; }).map(function (st) { return '<li><b>' + esc(st.title) + '</b><span class="idc">' + (st.conf != null ? pc(st.conf) : (OUTCOME[st.outcome] || '')) + '</span><span class="muted small idw">' + esc(firstSentence(st.why || st.proof)) + '</span></li>'; }).join('') + '</ul>'; }
  // after the source is chosen: a rack identified for certain goes straight to the differences (accepted by itself, remembered);
  // anything less opens the confirm panel as before
  function afterFind(src) { return function (d) { var I = (d && d.identify) || {};
    if (d && I.certain && d.match && d.match.how !== 'picked') { RES.cmpUi = 'drift'; cmpStatus('Rack identified for certain. Comparing...'); return setDb({ source: src, auto: true }).then(function (r) { if (r) return secTo('diff'); RES.cmpUi = 'confirm'; renderCompare(d); }); }
    if (d) { RES.cmpUi = 'confirm'; renderCompare(d); }
    return d; }; }
  function renderCompare(d) { try { renderCompareInner(d); } catch (e) { console.error('compare', e); cmpStatus('The compare could not be shown: ' + String(e.message || e).replace(/[.!?]*$/, '') + '.', 'err'); } }
  function renderCompareInner(d) {
    RES.cmp = d; var ui = RES.cmpUi || 'idle', src = RES.cmpSrc || d.source || 'excel', I = d.identify || { steps: [] };
    // the owner's rule (3 Nov 2026): a rack identified for certain was accepted by itself; one line says so, the steps fold under it,
    // and Not this rack opens the picker, so the person can still change it without being stopped
    if (ui === 'drift' && d.auto && d.match && d.source === src) {
      $('df-top').innerHTML = '<div class="card idcard idauto"><div class="dfrow"><span>Rack <b>' + esc(d.match.rack_key) + '</b>, identified with high confidence'
        + (I.certain_why ? ' (' + esc(I.certain_why) + ')' : '') + '</span><button class="link" type="button" id="cmp-notthis">Not this rack</button></div>'
        + '<details class="idhow"><summary>Show how</summary>' + stepsHtml(I) + '</details></div>';
      return renderDiff(d, true);
    }
    if (ui !== 'confirm' || !(d.chosen && d.source === src)) { $('df-top').innerHTML = ''; return renderDiff(d, ui === 'drift'); }
    var m = d.match, other = m && m.how === 'picked' && I.rack_key && I.rack_key !== m.rack_key;   // a person picked another record than the steps found: the steps' confidence is not this record's
    var h = '<div class="card idcard"><div class="dfrow"><b>Confirm the verified rack id</b><span class="muted small">' + esc(SRCLABEL[src] || d.sources[src]) + '</span></div>'
      + '<div class="dfrow idhead"><span class="idrack">' + (m ? esc(m.rack_key) : 'No single rack found') + '</span>' + (other ? '<span class="muted small">' + esc(m.why) + '</span>' : I.confidence != null ? '<span class="idconf ' + esc(I.state) + '">' + pc(I.confidence) + ' overall</span>' : '') + '</div>'
      + (other ? '<p class="muted small">The steps below found ' + esc(I.rack_key) + ', not this record. Confirm only if you are sure.</p>' : '')
      + '<figure class="lblstrip"><img src="/api/video/' + encodeURIComponent(RES.id) + '/rack-label.jpg" alt="The rack label crop" onerror="this.parentNode.hidden=true"></figure>'
      + stepsHtml(I)
      + '<div class="dfrow idbtns"><button class="btn primary" type="button" id="cmp-continue"' + (m ? '' : ' disabled') + '>Confirm</button><button class="btn" type="button" id="cmp-change">Change</button></div>';
    if (RES.cmpPick || !m) {
      h += '<div class="idpick">' + (!m && I.top && I.top.length ? '<p class="glabel">The closest racks</p><div class="idtop">' + I.top.map(function (t) { return '<button class="btn" type="button" data-cmppick="' + esc(t.rack_key) + '">' + esc(t.rack_key) + '<small class="muted"> ' + esc(t.why) + '</small></button>'; }).join('') + '</div>' : '')
        + '<div class="dfrow"><label class="muted small" for="cmp-type">Which record is this rack?</label><input id="cmp-type" list="cmp-list" placeholder="Search or type a rack id" autocomplete="off"><datalist id="cmp-list">'
        + d.candidates.map(function (r) { return '<option value="' + esc(r.rack_key) + '">' + esc(r.rack_key) + ' - ' + pl(r.devices, 'device') + '</option>'; }).join('') + '</datalist><button class="btn" type="button" id="cmp-use">Use this record</button></div></div>';
    }
    $('df-top').innerHTML = h + '</div>';
    renderDiff(d, false);
  }
  function recordRow(d, name) { var r = (d.record || []).filter(function (x) { return x.name === name; })[0]; if (!r) return '';
    return [r.name, r.kind, (r.manufacturer && r.model && String(r.model).toLowerCase().indexOf(String(r.manufacturer).toLowerCase()) === 0 ? r.model : [r.manufacturer, r.model].filter(Boolean).join(' ')), r.ru_start != null ? 'U' + r.ru_start + (r.ru_height > 1 ? '-' + (r.ru_start + r.ru_height - 1) : '') : ''].filter(Boolean).map(esc).join(' · '); }
  function renderDiff(d, show) {
    var el = $('diff-top');
    if (!(show && d.confirmed && d.drift && d.match)) { el.innerHTML = '';
      if (show) { var why = !d.match ? 'No single rack of this copy was named, so nothing could be compared. Press Change and choose the rack.' : !d.confirmed ? 'The rack was not confirmed, so nothing was compared. Press Confirm again.' : 'The copy gave no devices to compare. Copy the database in again under Profile.'; console.error('compare: no drift', d.source, !!d.match, !!d.confirmed, !!d.drift); cmpStatus(why, 'err'); }
      return; }
    cmpStatus('');
    var rows = d.drift.rows, mode = RES.diffMode || 'short', by = {}, src = d.sources[d.source];
    rows.forEach(function (r) { by[r.what] = (by[r.what] || 0) + 1; });
    var head = '<b>' + rows.length + '</b> ' + (rows.length === 1 ? 'difference' : 'differences') + ' against <b>' + esc(d.match.rack_key) + '</b> in ' + esc(src);
    var h = '<div class="card"><p>' + head + '</p><p class="muted small">' + d.drift.checked + ' devices compared' + (d.drift.ports_checked ? ' · ' + d.drift.ports_checked + ' ports compared' : '') + ' · ' + d.drift.video_devices + ' in the video · ' + d.drift.record_devices + ' in the record</p>'
      + (rows.length ? '<ul class="wbkinds">' + Object.keys(by).map(function (k) { return '<li><b>' + by[k] + '</b> ' + esc(k.toLowerCase()) + '</li>'; }).join('') + '</ul>' : '')
      + '<div class="seg2"><button class="btn' + (mode === 'short' ? ' on' : '') + '" type="button" data-dfmode="short">Short</button><button class="btn' + (mode === 'detailed' ? ' on' : '') + '" type="button" data-dfmode="detailed">Detailed</button></div></div>';
    if (!rows.length) h += '<div class="card"><p class="dfok">No differences. The rack matches the record.</p></div>';
    else if (mode === 'short') h += '<div class="card"><ul class="dflines">' + rows.map(function (r) { return '<li data-dftype="' + esc(r.type) + '"><b>' + esc(r.what) + '</b> <span class="mono">' + esc(r.unit) + '</span><p>' + esc(r.words) + '</p></li>'; }).join('') + '</ul></div>';
    else h += rows.map(function (r) { return '<div class="card dfcard" data-dftype="' + esc(r.type) + '"><div class="dfrow"><b>' + esc(r.what) + '</b><span class="mono">' + esc(r.unit) + '</span></div><div class="dfpair">'
      + '<div><span class="glabel">Record</span><p><b>' + esc(r.record) + '</b></p><p class="muted small">' + (recordRow(d, r.record_name) || 'Not in the record') + '</p></div>'
      + '<div><span class="glabel">Video</span><p><b>' + esc(r.read) + '</b></p>' + (r.index ? '<img class="dfcrop" src="/api/video/' + encodeURIComponent(RES.id) + '/crop/' + r.index + '" alt="D' + r.index + ' in the video" loading="lazy">' : '<p class="muted small">Nothing seen at this unit</p>') + '</div></div>'
      + '<p>' + esc(r.words) + '</p><p class="muted small">' + esc(HINT[r.type] || '') + '</p></div>'; }).join('');
    if (d.drift.notes.length) h += '<details class="card"><summary>Not checked (' + d.drift.notes.length + ')</summary><ul class="muted small">' + d.drift.notes.map(function (n) { return '<li>' + esc(n) + '</li>'; }).join('') + '</ul></details>';
    el.innerHTML = h;
  }
    document.addEventListener('click', function (e) { var t = e.target.closest && e.target.closest('#cmp-btn, #cmp-back, #cmp-change, #cmp-notthis, [data-cmpsrc], #cmp-use, #cmp-continue, [data-cmppick], [data-dfmode]'); if (!t) return;
    if (t.id === 'cmp-notthis') { RES.cmpUi = 'confirm'; RES.cmpPick = true; renderCompare(RES.cmp); var ty1 = $('cmp-type'); if (ty1) ty1.focus(); return; }
    if (t.id === 'cmp-back') { e.preventDefault(); RES.cmpUi = 'idle'; cmpMode(false); window.scrollTo(0, 0); return; }
    if (t.id === 'cmp-btn') { var mn = $('cmp-menu'); mn.hidden = !mn.hidden; t.setAttribute('aria-expanded', String(!mn.hidden)); return; }
    if (t.id === 'cmp-change') { RES.cmpPick = !RES.cmpPick; renderCompare(RES.cmp); var ty0 = $('cmp-type'); if (ty0) ty0.focus(); return; }
    var src = RES.cmpSrc || (RES.cmp && RES.cmp.source) || 'excel';
    if (t.dataset.dfmode) { RES.diffMode = t.dataset.dfmode; return renderDiff(RES.cmp, true); }
    if (t.dataset.cmpsrc) { try { src = t.dataset.cmpsrc; RES.cmpSrc = src; $('cmp-menu').hidden = true; $('cmp-btn').setAttribute('aria-expanded', 'false'); RES.cmpPick = false; RES.cmpUi = 'finding'; cmpMode(true);    // the confirm panel waits until the ladder says whether the rack is certain
      var toCompare = function () { var el = $('sec-compare'); if (el && el.scrollIntoView) el.scrollIntoView({ block: 'start', behavior: 'smooth' }); };   // on a phone the panel opens below the report: go to it
      if (RES.cmp && RES.cmp.imported[src]) { cmpStatus('Finding the rack...'); return Promise.resolve(setDb({ source: src })).then(afterFind(src)).then(toCompare); }
      if (src === 'excel') { RES.cmpUi = 'idle'; cmpMode(false); toast('No workbook is copied in yet. Copy one in under Database copies.'); return go('copies'); }
      cmpStatus('Copying in from ' + SRCLABEL[src] + '...');
      return api('/api/sources/' + src + '/import', { method: 'POST' }).then(function (r) { cmpStatus('Finding the rack...'); return Promise.resolve(setDb({ source: src })).then(afterFind(src)).then(toCompare); })
        .catch(function (er) { RES.cmpUi = 'idle'; cmpStatus(String(er.message || 'The copy did not work.').replace(/([^.!?])$/, '$1.'), 'err'); });  } catch (er) { console.error('compare', er); RES.cmpUi = 'idle'; cmpStatus(String(er && er.message || 'The compare did not start').replace(/[.!?]*$/, '') + '.', 'err'); } return; }
    if (t.dataset.cmppick) return setDb({ source: src, rack_key: t.dataset.cmppick });
    if (t.id === 'cmp-use') { var v = ($('cmp-type').value || '').trim(); if (!v) return toast('Type or pick a rack id first.');
      var known = (RES.cmp.candidates || []).some(function (r) { return r.rack_key === v; });
      if (known) return setDb({ source: src, rack_key: v });
      return saveRackId({ id: v }).then(function () { toast(v + ' is kept as this rack\'s id; it is not in this copy.'); return setDb({ source: src }); }); }
    if (t.id === 'cmp-continue') { var m = RES.cmp.match; RES.cmpUi = 'drift'; return setDb({ source: src, rack_key: m && m.how === 'picked' ? m.rack_key : null, confirmed: true }).then(function () { secTo('diff'); }); }
  });
  // the workbook's validation report (RT-EOS's checks), shown before anything is imported; Import makes a new Excel copy (Profile > Database copies)
  function pl(n, w) { return n + ' ' + w + (n === 1 ? '' : 's'); }
  document.addEventListener('click', function (e) { var t = e.target.closest && e.target.closest('[data-dfimp], #df-xlsx'); if (!t) return;
    if (t.dataset.dfimp) { t.disabled = true; var was = t.textContent; t.textContent = 'Copying...'; return api('/api/sources/' + t.dataset.dfimp + '/import', { method: 'POST' }).then(function (r) { toast(pl(r.devices, 'device') + ' copied in from ' + pl(r.racks, 'rack') + ' as copy ' + r.version); t.disabled = false; t.textContent = was; loadCopies(); })
      .catch(function (er) { t.disabled = false; t.textContent = was; toast(er.message + ' Nothing was copied; the copy from before is still the one used.'); }); }
    var f = $('df-file').files[0]; if (!f) return toast('Choose the workbook first.');
    $('df-report').innerHTML = '<p class="muted small">Reading ' + esc(f.name) + '...</p>';
    var fd = new FormData(); fd.append('workbook', f); upload('/api/sources/excel', fd, function () {}).then(renderWbReport).catch(function (er) { $('df-report').innerHTML = ''; toast(er.message); }); });
  function renderWbReport(r) {
    var c = r.counts, kinds = Object.keys(r.by_kind), MAX = 100;
    var h = '<div class="wbrep"><p><b>' + esc(r.file.replace(/^workbook_[0-9a-f]{8}_/, '')) + '</b>: ' + pl(c.racks, 'rack') + ', ' + pl(c.devices, 'device') + ', ' + pl(c.ports, 'port') + ', ' + pl(c.cables, 'cable') + '. '
      + (c.problems ? '<b class="' + (c.errors ? 'err' : '') + '">' + c.problems + (c.problems === 1 ? ' problem' : ' problems') + '</b> (' + c.errors + ' errors, ' + c.warnings + ' warnings).' : '<span class="dfok">No problems found.</span>') + '</p>';
    if (kinds.length) h += '<ul class="wbkinds">' + kinds.map(function (k) { return '<li><b>' + r.by_kind[k] + '</b> ' + esc(k) + '</li>'; }).join('') + '</ul>';
    var groups = [], byKey = {};
    r.problems.forEach(function (x) { var k = x.sheet + '|' + x.level + '|' + x.reason; if (!byKey[k]) { byKey[k] = { x: x, rows: [] }; groups.push(byKey[k]); } if (x.row_no) byKey[k].rows.push(x.row_no); });
    if (c.problems) h += '<table class="dftable wbtable"><thead><tr><th>Sheet</th><th>Rows</th><th>Problem</th></tr></thead><tbody>' + groups.slice(0, MAX).map(function (g) { var x = g.x;
      return '<tr data-wblevel="' + x.level + '"><td class="mono small">' + esc(x.sheet) + '</td><td class="mono">' + esc(ranges(g.rows)) + '</td><td><span class="' + (x.level === 'error' ? 'err' : '') + '">' + (x.level === 'error' ? 'Error' : 'Warning') + (g.rows.length > 1 ? ' on ' + g.rows.length + ' rows' : '') + '</span><p>' + esc(x.reason) + '</p></td></tr>'; }).join('') + '</tbody></table>'
      + (groups.length > MAX ? '<p class="muted small">And ' + (groups.length - MAX) + ' more.</p>' : '');
    h += '<div class="dfrow">' + (r.blocking ? '<p class="err">This workbook cannot be imported until the missing sheets and columns are added.</p>' : '<button class="btn primary" type="button" id="df-import" data-upload="' + esc(r.upload) + '">Import</button>') + '<button class="btn" type="button" id="df-cancel">Cancel</button></div></div>';
    $('df-report').innerHTML = h;
  }
  document.addEventListener('click', function (e) { var t = e.target.closest && e.target.closest('#df-import, #df-cancel'); if (!t) return;
    if (t.id === 'df-cancel') { $('df-report').innerHTML = ''; return; }
    t.disabled = true; api('/api/sources/excel/import', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ upload: t.dataset.upload }) })
      .then(function (r) { toast(pl(r.devices, 'device') + ' and ' + pl(r.ports, 'port') + ' imported as Excel copy ' + r.version); $('df-report').innerHTML = ''; loadCopies(); }).catch(function (er) { t.disabled = false; toast(er.message); }); });
  // Connections (Profile): the organization's NetBox and ServiceNow logins; the server sends back the address and the state only, never a secret
  function loadConnections() { api('/api/connections').then(renderConnections).catch(function (e) { $('cn-list').innerHTML = '<p class="err">' + esc(e.message) + '</p>'; }); }
  function renderConnections(d) {
    var c = d.connections;
    $('cn-list').innerHTML = Object.keys(c).map(function (t) { var x = c[t], nb = t === 'netbox', held = function (k) { return x[k] ? 'saved, leave blank to keep' : ''; };
      return '<form class="card cnform" data-cn="' + t + '"><div class="dfrow"><b>' + esc(x.label) + '</b><span class="cnstate ' + esc(x.state) + '">' + esc({ ok: 'Connected', failed: 'Not connected', untested: 'Not tested', mock: 'Local test copy' }[x.state] || x.state) + '</span></div>'
        + '<p class="muted small" data-cnwords>' + esc(x.state_words || '') + (x.tested_at ? ' Tested ' + esc(when(x.tested_at)) + '.' : '') + '</p>'
        + '<label>Address<input name="address" placeholder="' + (nb ? 'https://netbox.example.com' : 'https://example.service-now.com') + '" value="' + esc(x.address || '') + '" autocomplete="off"></label>'
        + (nb ? '<label>Token<input name="token" type="password" autocomplete="new-password" placeholder="' + held('token') + '"></label><p class="muted small">Or a username and password:</p>' : '')
        + '<label>Username<input name="username" autocomplete="off" placeholder="' + held('username') + '"></label>'
        + '<label>Password<input name="password" type="password" autocomplete="new-password" placeholder="' + held('password') + '"></label>'
        + (ADMIN ? '<div class="dfrow"><button class="btn primary" type="submit">Save</button><button class="btn" type="button" data-cntest' + (x.saved ? '' : ' disabled') + '>Test connection</button>'
        + (x.saved ? '<button class="btn danger" type="button" data-cndel>Remove</button>' : '') + '</div>' : '<p class="muted small">Only an organization admin can change this login.</p>') + '</form>'; }).join('');
    if (!ADMIN) $('cn-list').querySelectorAll('input').forEach(function (i) { i.disabled = true; });
  }
  document.addEventListener('submit', function (e) { var f = e.target.closest && e.target.closest('form[data-cn]'); if (!f) return; e.preventDefault();
    var body = {}; ['address', 'username', 'password', 'token'].forEach(function (k) { if (f.elements[k]) body[k] = f.elements[k].value; });
    api('/api/connections/' + f.dataset.cn, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(function (d) { toast('Login saved.'); renderConnections(d); }).catch(function (er) { toast(er.message); }); });
  document.addEventListener('click', function (e) { var b = e.target.closest && e.target.closest('form[data-cn] [data-cntest], form[data-cn] [data-cndel]'); if (!b) return;
    var t = b.closest('form').dataset.cn;
    if (b.hasAttribute('data-cntest')) { b.disabled = true; b.textContent = 'Testing...'; return api('/api/connections/' + t + '/test', { method: 'POST' }).then(renderConnections).catch(function (er) { toast(er.message); loadConnections(); }); }
    api('/api/connections/' + t, { method: 'DELETE' }).then(function (d) { toast('Login removed. The local test copy is used again.'); renderConnections(d); }).catch(function (er) { toast(er.message); }); });
  // Database copies (Profile): every copy with its source, version, time and counts; the active ones marked; delete
  function loadCopies() { api('/api/sources/copies').then(renderCopies).catch(function (e) { $('cp-list').innerHTML = '<p class="err">' + esc(e.message) + '</p>'; }); }
  function renderCopies(d) {
    if (!d.copies.length) { $('cp-list').innerHTML = '<div class="card"><p class="muted">No copy yet. ' + (ADMIN ? 'Read a workbook or copy NetBox or the CMDB in above.' : 'An organization admin copies the databases in.') + '</p></div>'; return; }
    $('cp-list').innerHTML = '<div class="card flush"><table class="dftable cptable"><thead><tr><th>Source</th><th class="wide">Version</th><th class="wide">Copied</th><th class="cpn">Racks</th><th class="cpn">Devices</th><th class="cpn wide">Ports</th><th class="wide">From</th><th>State</th><th></th></tr></thead><tbody>'
      + d.copies.map(function (c) { return '<tr data-copy="' + c.id + '"' + (c.active ? ' class="on"' : '') + '><td><b>' + esc(d.sources[c.source] || c.source) + '</b><span class="cpwhen small muted"><span>v' + c.version + '</span><span>' + esc(when(c.imported_at)) + '</span></span></td><td class="mono wide">v' + c.version + '</td><td class="mono small wide">' + esc(when(c.imported_at)) + '</td>'
        + '<td class="cpn">' + c.racks + '</td><td class="cpn">' + c.devices + '</td><td class="cpn wide">' + (c.ports || 0) + '</td><td class="wide small muted">' + esc(/^https?:\/\/(127\.0\.0\.1|localhost)[:/]/.test(String(c.file || '')) ? 'The local test copy' : /^https?:/.test(String(c.file || '')) ? String(c.file).replace(/^https?:\/\//, '').replace(/\/.*$/, '') : String(c.file || '').replace(/^workbook_[0-9a-f]{8}_/, '')) + '</td>'
        + '<td>' + (c.active ? '<span class="cpon">Active</span>' : '<span class="muted small">Older</span>') + '</td><td>' + (ADMIN ? '<button class="btn danger" type="button" data-cpdel="' + c.id + '">Delete</button>' : '') + '</td></tr>'; }).join('') + '</tbody></table></div>';
  }
  document.addEventListener('click', function (e) { var b = e.target.closest && e.target.closest('[data-cpdel]'); if (!b) return;
    if (!confirm('Delete this copy? Runs checked against it use the copy before it, if there is one.')) return;
    api('/api/sources/copies/' + b.dataset.cpdel, { method: 'DELETE' }).then(function (d) { toast('Copy deleted.'); renderCopies(d); }).catch(function (er) { toast(er.message); }); });
  // the Devices tab shows ONE thing: a device or a unit (RES.pick), or the list to choose one from
  function selectDevice(i, auto) {
    RES.pick = { kind: 'dev', id: i }; RES.sel = i;
    if (RES.navDir) { document.body.dataset.dir = RES.navDir; RES.navDir = null; var one = $('dv-one'); if (one && !one.hidden) { one.classList.remove('mo'); void one.offsetWidth; one.classList.add('mo'); } }
    renderOne(); secTo('devices');
    var v = RES.view, d = v && (v.devices || []).filter(function (x) { return x.index === i; })[0]; if (!d || auto) return;
    var L = d.layout || {};
    if (PORT_KINDS[d.kind] && !L.mapped && !L.ports_state) {                              // the user's selection: read this device's ports now
      L.ports_state = 'running'; v.pending_ports = true;
      api('/api/video/' + RES.id + '/ports/' + i, { method: 'POST' }).then(function () { watchPorts(); }).catch(function () { L.ports_state = null; });
    }
  }
  function selectUnit(name) { RES.pick = { kind: 'unit', id: name }; RES.sel = null; renderOne(); secTo('devices'); }
  function watchPorts() { clearInterval(RES.timer); RES.timer = setInterval(function () { if ((location.hash || '').indexOf('#result/' + RES.id) === 0) { if (RES.view && RES.view.pending_ports) loadView(false); } else clearInterval(RES.timer); }, 6000); }
  function renderDevice() { renderOne(); }
  var UP = function (s) { return String(s).toUpperCase().replace(/^U0/, 'U'); };
  function URANGE(top, bottom, n) { if (!(n > 1)) return UP(top); var a = UP(bottom), b = UP(top).replace('U', ''); return a + '-' + b; }   // U10-11: low to high, plain hyphen, as the drift writes it
  function devOfUnit(v, name) { return (v.devices || []).filter(function (d) { return (d.units || []).indexOf(name) >= 0; })[0]; }
  function unitState(v, x) { return x.status === 'uncertain' ? 'Not sure' : devOfUnit(v, x.unit) || x.status === 'occupied' ? 'In use' : 'Free'; }   // not sure stays not sure, with a device on it too (user, 2026-10-30)
  // the clearer of two crops: the device's best frame, or its cut from the stitched rack (sharpness = variance of the Laplacian)
  function sharpness(src, box) { var c = document.createElement('canvas'), w = 240, h = Math.max(8, Math.round(w * (box ? (box[3] - box[1]) / (box[2] - box[0]) : src.naturalHeight / src.naturalWidth)));
    c.width = w; c.height = h; var g = c.getContext('2d'); if (box) g.drawImage(src, box[0], box[1], box[2] - box[0], box[3] - box[1], 0, 0, w, h); else g.drawImage(src, 0, 0, w, h);
    var p = g.getImageData(0, 0, w, h).data, gr = new Float32Array(w * h), s = 0, s2 = 0, n = 0; for (var i = 0; i < w * h; i++) gr[i] = p[i * 4] * .3 + p[i * 4 + 1] * .59 + p[i * 4 + 2] * .11;
    for (var y = 1; y < h - 1; y++) for (var x = 1; x < w - 1; x++) { var k = y * w + x, l = 4 * gr[k] - gr[k - 1] - gr[k + 1] - gr[k - w] - gr[k + w]; s += l; s2 += l * l; n++; } return n ? s2 / n - (s / n) * (s / n) : 0; }
  function clearestCrop(d, el) {
    var rack = $('res-rack-plain'), q = d.polygon; el.src = d.crop;
    if (!q || !rack.complete || !rack.naturalWidth) return;
    var xs = q.map(function (p) { return p[0]; }), ys = q.map(function (p) { return p[1]; }), box = [Math.max(0, Math.min.apply(null, xs)), Math.max(0, Math.min.apply(null, ys)), Math.min(rack.naturalWidth, Math.max.apply(null, xs)), Math.min(rack.naturalHeight, Math.max.apply(null, ys))];
    var fr = new Image(); fr.crossOrigin = 'anonymous'; fr.onload = function () { try {
      if (sharpness(rack, box) > sharpness(fr)) { var c = document.createElement('canvas'); c.width = box[2] - box[0]; c.height = box[3] - box[1]; c.getContext('2d').drawImage(rack, box[0], box[1], c.width, c.height, 0, 0, c.width, c.height); el.src = c.toDataURL('image/jpeg', .92); }
    } catch (e) {} }; fr.src = d.crop; }
  // one line that answers 'what is it, what is plugged in': "1U Switch · 28 ports · 19 in use · 19 white RJ45"
  function summaryLine(dv, L, kind, mm) {
    var parts = [dv.height + 'U ' + (kind === 'Not sure' ? 'device, type not sure' : kind.toLowerCase())], ports = L.ports || [];
    if (ports.length) { var on = ports.filter(function (p) { return p.status === 'connected' && p.type !== 'hidden'; }).length, cab = {};   // a hidden port is not counted as in use
      ports.forEach(function (p) { if (p.status === 'connected' && p.type !== 'hidden' && p.cable_type) { var k = cableName(p); cab[k] = (cab[k] || 0) + 1; } });
      var byT = {}; ports.forEach(function (p) { var t = typeName(p.type); byT[t] = (byT[t] || 0) + 1; }); var tk = Object.keys(byT);
      parts.push((L.mode === 'patchpanel' && L.size_known === false ? ports.length + ' ports seen' : tk.length > 1 && tk.length <= 3 ? tk.map(function (t) { return byT[t] + ' ' + t; }).join(' + ') : pl(ports.length, 'port')) + ' · ' + on + ' in use');
      var ks = Object.keys(cab).sort(function (a, b) { return cab[b] - cab[a]; }), typed = ks.reduce(function (n, k) { return n + cab[k]; }, 0), untyped = on - typed;
      // the cable line counts the same ports as "in use": a cable whose colour and type were not read is counted as such
      if (ks.length) parts.push('cables: ' + ks.slice(0, 2).map(function (k) { var w = k.split(' '); return cab[k] + ' ' + (w[1] ? w.slice(1).join(' ').toLowerCase() + ' ' : '') + w[0]; }).join(', ') + (ks.length > 2 ? ' and ' + (typed - ks.slice(0, 2).reduce(function (n, k) { return n + cab[k]; }, 0)) + ' more' : '') + (untyped > 0 ? ', ' + untyped + ' not typed' : '')); }
    else if (PORT_KINDS[dv.kind]) parts.push(L.ports_state === 'running' ? 'reading the ports' : L.mapped ? 'no ports read' : 'ports not read yet');
    if (!mm && L.vendor_read) parts.push(esc(L.vendor_read + (L.model_read ? ' ' + L.model_read : '')) + ' read on the panel, not confirmed');
    return esc(parts.join(' · ')).replace(/&amp;/g, '&');
  }
  document.addEventListener('click', function (e) { var b = e.target.closest && e.target.closest('[data-dvnav]'); if (!b) return; RES.navDir = b.textContent.trim().charAt(0) === '‹' ? 'back' : 'fwd'; selectDevice(parseInt(b.dataset.dvnav, 10)); });
  (function () { var x0 = null, y0 = null; var el = document.getElementById('dv-one'); if (!el) return;              // a swipe on the device page: the device above or below
    el.addEventListener('touchstart', function (e) { var t = e.touches[0]; x0 = t.clientX; y0 = t.clientY; }, { passive: true });
    el.addEventListener('touchend', function (e) { if (x0 == null) return; var t = e.changedTouches[0], dx = t.clientX - x0, dy = t.clientY - y0; x0 = null;
      if (Math.abs(dx) < 70 || Math.abs(dy) > 50) return; var b = el.querySelector(dx < 0 ? '[data-dvnav]:last-of-type' : '[data-dvnav]:first-of-type'); if (b && b.dataset.dvnav && ((dx < 0 && b.textContent.indexOf('›') >= 0) || (dx > 0 && b.textContent.indexOf('‹') >= 0))) b.click(); }, { passive: true }); })();
  function renderOne() {
    var v = RES.view; if (!v) return; var p = RES.pick, devs = (v.devices || []).slice().sort(function (a, b) { return unumOf(b.top) - unumOf(a.top); }), units = (v.units || []).filter(function (x) { return x.box; });
    var isUnit = p && p.kind === 'unit', list = isUnit ? units.map(function (x) { return x.unit; }) : devs.map(function (d) { return d.index; });
    var at = p ? list.indexOf(p.id) : -1;
    $('dv-pick').hidden = at >= 0; $('dv-one').hidden = at < 0;
    if (at < 0) {                                                       // the list: every device with its place (or every unit)
      var wantUnits = p && p.kind === 'unitlist';
      $('dv-pick-title').textContent = wantUnits ? 'Choose a unit' : 'Choose a device';
      $('dv-list').classList.remove('in'); $('dv-list').innerHTML = wantUnits ? units.map(function (x) { var d = devOfUnit(v, x.unit), st = unitState(v, x);
          return '<button class="prow" type="button" data-unit="' + x.unit + '"><span class="iu">' + UP(x.unit) + '</span><span class="pt"><b class="' + (st === 'Free' ? 'free' : st === 'Not sure' ? 'unc' : '') + '">' + st + '</b>' + (d ? '<small>D' + d.index + ' · ' + esc(devName(d).replace(/<[^>]+>/g, '') || d.kind || 'device') + '</small>' : '') + '</span><span class="arr">›</span></button>'; }).join('')
        : byKind(devs).map(function (g) { return kindHead(g) + g.devs.map(function (d) { var mm = devName(d).replace(/<[^>]+>/g, ''); mm = mm && mm.indexOf('not read') < 0 && mm.indexOf('reading') < 0 ? mm : '';
          var ur = URANGE(d.top, d.bottom, d.height), kindName = d.kind && d.kind !== 'Other' ? d.kind : 'Not sure';
          if (!PORT_KINDS[d.kind]) return '<button class="prow" type="button" data-dev="' + d.index + '"><img class="pthumb" src="' + d.crop + '" alt="" loading="lazy"><span class="pt"><b>' + esc(mm || kindName) + '</b><small>' + ur + ' · D' + d.index + ' · ' + d.height + 'U' + (d.kind_from === 'faceplate' || d.kind_from === 'ports' || d.kind_from === 'corrected' ? ' · ' + esc(d.kind_words) : '') + '</small></span><span class="arr">›</span></button>';
          var st = devState(d, v); return '<button class="prow" type="button" data-dev="' + d.index + '"><img class="pthumb" src="' + d.crop + '" alt="" loading="lazy"><span class="pt"><b>' + esc(st.key === 'on' ? st.title : (mm || kindName)) + '</b><small>' + ur + ' · D' + d.index + ' · ' + esc(d.kind_words || kindName) + '</small></span><span class="stat ' + st.key + '"><i></i>' + st.word + '</span><span class="arr">›</span></button>'; }).join(''); }).join('');
      [].forEach.call($('dv-list').children, function (el, i) { el.style.setProperty('--i', i); }); requestAnimationFrame(function () { $('dv-list').classList.add('in'); });
      return;
    }
    $('dv-another').textContent = isUnit ? 'Pick another unit' : 'Pick another device';
    var G = RES.geo || {}, C = RES.crop || [0, 0, 1, 1];
    var hl = isUnit ? (function () { var i = (G.rows || []).indexOf(list[at]); return i < 0 ? null : [G.ds[i] - G.pitch / 2, G.ds[i] + G.pitch / 2]; })() : (G.dbox && G.dbox[list[at]] ? [G.dbox[list[at]].y0, G.dbox[list[at]].y1] : null);
    var others = Object.keys(G.dbox || {}).map(function (k) { var x = G.dbox[k], on = !isUnit && String(k) === String(list[at]);
      return on ? '' : '<rect class="lo" data-dev="' + k + '" x="' + G.L + '" y="' + (x.y0 + 2) + '" width="' + (G.R - G.L) + '" height="' + (x.y1 - x.y0 - 4) + '" rx="6"/>'; }).join('');
    var loc = '<div class="loc" id="dv-loc" style="aspect-ratio:' + C[2] + ' / ' + C[3] + '"><canvas id="loc-cv"></canvas><svg viewBox="' + C.join(' ') + '" preserveAspectRatio="none">'
      + '<path class="ldim" fill-rule="evenodd" d="M' + C[0] + ' ' + C[1] + 'h' + C[2] + 'v' + C[3] + 'h' + (-C[2]) + 'Z' + (hl ? 'M' + G.L + ' ' + hl[0] + 'H' + G.R + 'V' + hl[1] + 'H' + G.L + 'Z' : '') + '"/>' + others
      + (hl ? '<rect class="lhl" id="dv-hl" x="' + (G.L - 4) + '" y="' + hl[0] + '" width="' + (G.R - G.L + 8) + '" height="' + (hl[1] - hl[0]) + '" rx="8"/>' : '') + '</svg>'
      + (hl ? '<span class="ltap" style="top:' + ((hl[0] + hl[1]) / 2 - C[1]) / C[3] * 100 + '%">' + (matchMedia('(hover: none)').matches ? 'tap' : 'click') + '</span>' : '') + '</div>';
    var place = function (x) {                                          // what sits in a unit, in a few words
      if (!x) return 'Rack end'; var d = devOfUnit(v, x.unit), st = unitState(v, x);
      return UP(x.unit) + ' · ' + (d ? 'D' + d.index + (d.kind && d.kind !== 'Other' ? ' ' + esc(d.kind.toLowerCase()) : '') : st.toLowerCase()); };
    var row = function (k, val) { return '<span>' + k + '</span><b>' + val + '</b>'; };
    var W0 = (v.rack.size || [1000, 2000])[0], H0 = (v.rack.size || [1000, 2000])[1];
    var cutOf = function (b0, cls) { var bw = b0[2] - b0[0], bh = b0[3] - b0[1];
      return '<div class="ucrop ' + cls + '" style="aspect-ratio:' + bw + ' / ' + bh + ';background-image:url(' + v.rack.picture + ');background-size:' + (W0 / bw * 100) + '% auto;background-position:' + (b0[0] / Math.max(1, W0 - bw) * 100) + '% ' + (b0[1] / Math.max(1, H0 - bh) * 100) + '%"></div>'; };
    var info, below = '';
    if (isUnit) {                                                       // one unit: its state and the device in it
      var x = units[at], st = unitState(v, x), d = devOfUnit(v, x.unit);
      info = '<span class="eyebrow">Unit</span><h2 class="onet">' + UP(x.unit) + '</h2><div class="stat ' + (st === 'Free' ? 'free' : st === 'Not sure' ? 'unc' : 'on') + '"><i></i>' + st + '</div>'
        + '<div class="dfacts">' + row('Device', d ? '<button class="link" type="button" data-open-dev="' + d.index + '">D' + d.index + ' · ' + esc(devName(d).replace(/<[^>]+>/g, '') || d.kind || 'device') + ' ›</button>' : 'None') + '</div>';
      below = '<figure class="pshot rv2 one">' + cutOf(x.box, '') + '</figure>';
      var nbIdx = (function () { var order = units.map(function (u) { return u.unit; }), k = order.indexOf(x.unit), out = [];             // the devices right above and below this unit
        [k - 1, k + 1].forEach(function (q) { if (q < 0 || q >= order.length) return; var nd = devOfUnit(v, order[q]); if (nd && out.indexOf(nd) < 0) out.push(nd); }); return out; })();
      var sure = x.decided_by === 'person';
      if (st === 'Not sure' || !sure) {                                  // step 2, answered by a person (user, 2026-10-30)
        below += '<section class="card dvports umark' + (st === 'Not sure' ? ' ask' : '') + '"><div class="ph2"><b>' + (st === 'Not sure' ? 'Is ' + UP(x.unit) + ' in use?' : 'Is this right?') + '</b></div>'
          + '<p class="pline">' + (st === 'Not sure' ? 'The frames could not tell. Look at the picture above and answer: the devices follow your answer.' : 'The frames say <b>' + st.toLowerCase() + '</b>. If that is wrong, change it: the devices follow.') + '</p>'
          + '<div class="umbtns"><button class="btn ' + (st === 'In use' ? 'ghost' : 'primary') + '" type="button" data-umark="occupied" data-unit="' + x.unit + '">In use</button><button class="btn ' + (st === 'Free' ? 'ghost' : 'primary') + '" type="button" data-umark="unoccupied" data-unit="' + x.unit + '">Free</button></div>'
          + (!d && nbIdx.length ? '<div class="umjoin" hidden><p class="pline">In use as part of:</p>' + nbIdx.map(function (nd) { return '<button class="btn ghost" type="button" data-umark="occupied" data-join="' + nd.index + '" data-unit="' + x.unit + '">D' + nd.index + ' · ' + esc(devName(nd).replace(/<[^>]+>/g, '') || nd.kind || 'device') + '</button>'; }).join('') + '<button class="btn ghost" type="button" data-umark="occupied" data-unit="' + x.unit + '" data-new="1">A new device here</button></div>' : '')
          + '</section>';
      } else {
        below += '<p class="pline umdone">Marked ' + st.toLowerCase() + '. <button class="link" type="button" data-umark="' + (st === 'Free' ? 'occupied' : 'unoccupied') + '" data-unit="' + x.unit + '">Change to ' + (st === 'Free' ? 'in use' : 'free') + '</button></p>';
      }
      if (!d) {                                                         // a device the sweep missed can be added over its units (user, 2026-10-27)
        var opts = units.map(function (u) { return '<option value="' + u.unit + '"' + (u.unit === x.unit ? ' selected' : '') + '>' + UP(u.unit) + '</option>'; }).join('');
        below += '<section class="card dvports adddev"><div class="ph2"><b>A device here?</b></div><p class="pline">If a device is missing here, mark the units it fills and add it.</p>'
          + '<div class="mrow2"><label class="sel"><span>From</span><select class="ad-top">' + opts + '</select></label><label class="sel"><span>To</span><select class="ad-bottom">' + opts + '</select></label><label class="sel"><span>Kind</span><select class="ad-kind"><option value="">Not sure</option>' + KINDS.filter(function (k) { return k !== 'Other'; }).map(function (k) { return '<option>' + k + '</option>'; }).join('') + '</select></label></div>'
          + '<button class="btn primary addbtn" type="button">Add the device</button></section>';
      }
      RES.zoom = { html: cutOf(x.box, 'big'), title: UP(x.unit) };
    } else {                                                            // one device: what it is, its ports, its picture, its specifications
      var dv = devs[at], L = dv.layout || {}, mm = L.vendor ? L.vendor + (L.model ? ' ' + L.model : '') : L.typed ? L.typed.vendor + ' ' + L.typed.model : '';
      var kind = dv.kind && dv.kind !== 'Other' ? esc(dv.kind) : 'Not sure', ps = PORT_KINDS[dv.kind] ? devState(dv, v) : null, P = L.pictures || {};
      var prev = at > 0 ? devs[at - 1] : null, next = at < devs.length - 1 ? devs[at + 1] : null;
      var nav = '<div class="dvnav">' + (prev ? '<button class="link" type="button" data-dvnav="' + prev.index + '">‹ D' + prev.index + '</button>' : '<span></span>') + '<span class="dvpos">' + (at + 1) + ' of ' + devs.length + '</span>' + (next ? '<button class="link" type="button" data-dvnav="' + next.index + '">D' + next.index + ' ›</button>' : '<span></span>') + '</div>';
      var web = document.body.classList.contains('web');
      info = nav + '<span class="eyebrow">D' + dv.index + ' · ' + URANGE(dv.top, dv.bottom, dv.height) + '</span><h2 class="onet">' + esc(mm || (ps && ps.panel ? ps.title : dv.kind && dv.kind !== 'Other' ? dv.kind : 'Device')) + '</h2>'
        + '<div class="stat ' + (dv.unsure ? 'unc' : ps ? ps.key : 'on') + '"><i></i>' + (dv.unsure ? 'Needs a look' : ps ? ps.word : 'Checked') + '</div>'
        + '<p class="dvsum">' + summaryLine(dv, L, kind, mm) + '</p>'
        + '<details class="dvmore"' + (web ? ' open' : '') + '><summary>Details</summary><div class="dfacts">' + row('Size', dv.height + 'U') + row('Type', esc(dv.kind_words || kind)) + (mm || (ps && ps.panel) ? '' : (L.vendor_read ? row('Make', esc(L.vendor_read) + ' <span class="kd">read, not confirmed</span>') : '') + row('Model', L.model_read ? esc(L.model_read) + ' <span class="kd">read, not confirmed</span>' : L.mapped ? 'Not read' : 'Not read yet'))
        + (ps && ps.facts ? portFacts(L) : '') + '</div></details>';
      // the one picture: the port layout over the crop when identified (animated), else the crop
      below = ((L.fitted || L.mode === 'patchpanel') && portOverlay(dv, L, P)) || (L.fitted && P.overlay ? '<figure class="pshot cine one" id="cine" title="Tap to play again"><img class="c-base" src="' + (P.cut || dv.crop) + '" alt=""><img class="c-over" src="' + P.overlay + '" alt="D' + dv.index + '"><span class="c-scan"></span>' + '</figure>' : null)
        || '<figure class="pshot rv2 one"><img id="dv-inline" src="' + (ps && ps.pic ? ps.pic : dv.crop) + '" alt="D' + dv.index + '"></figure>';
      if (L.fitted && P.vendor) below = '<p class="figlab">In this rack</p>' + below + '<p class="figlab">The model</p><figure class="pshot vfig"><img src="' + P.vendor + '" alt="' + esc(mm) + '"></figure>';   // the model's own picture under the photo, never pinned on it (user, 2026-10-28)
      if (ps) {
        if (ps.key === 'on' && ps.panel) below += '';                                     // a patch panel: its type and its ports, no make and model (user, 2026-10-27)
        else if (ps.key === 'on') below += (L.fitted ? '' : L.auto ? '<p class="pline">Ports found.</p>' : '<p class="pline">No port drawing for this model yet.</p>') + '<div class="specblock" data-vendor="' + esc(ps.vendor || '') + '" data-model="' + esc(ps.model || '') + '" data-combo="' + ((L.combo || []).length) + '" data-spec="' + esc(L.spec ? JSON.stringify(L.spec) : '') + '"><span class="glabel">Specifications</span><div class="specbody"><p class="muted small">Looking up…</p></div></div>' + (L.confirm ? closeupBtn(dv) : '');
        else if (ps.key === 'busy') below += '<p class="pline">Finding the make and model in the video, about a minute…</p>';
        else if (ps.key === 'auto') below += '<p class="pline">' + pl((L.ports || []).length, 'port') + ' found and typed from the video; a port out of view or hidden is not counted. ' + (L.vendor_read ? 'Make read: <b>' + esc(L.vendor_read) + '</b>, model not read. ' : 'The make and model were not read. ') + 'A close, sharp photo of the front gives the model and its ports.</p>' + closeupBtn(dv) + modelForm(dv);
        else if (ps.key === 'unc' && ps.panel) below += '<p class="pline">No port was seen on this panel: a sealed enclosure, or the ports are hidden. A sharp photo of the front shows them.</p>' + closeupBtn(dv);
        else if (ps.key === 'unc') below += '<p class="pline">' + (L.model_read ? 'Read once, not confirmed. ' : L.vendor_read ? 'The model was not read. ' : 'The make and model were not read. ') + 'A close, sharp photo of the front gives the model and its ports.</p>' + closeupBtn(dv) + modelForm(dv);
        else below += '<button class="btn primary readports" type="button" data-dev="' + dv.index + '"><svg class="ic"><use href="#i-refresh"/></svg>' + (L.ports_state === 'failed' ? 'Try again' : dv.kind === 'Patch Panel' ? 'Find the ports' : 'Find the make and model') + '</button>' + (dv.kind === 'Patch Panel' ? '' : modelForm(dv));
      } else below += modelForm(dv);
      below += '<div class="rmdev"><button class="link rmbtn" type="button" data-dev="' + dv.index + '" data-top="' + dv.top + '">Not a device? Remove it</button></div>';   // a box that is no device (user, 2026-10-27: 'there is no D6')
      RES.zoom = { html: '<img class="zimg" id="dv-crop" alt="D' + dv.index + '">', title: 'D' + dv.index + (mm ? ' · ' + esc(mm) : ''), dev: dv };
    }
    if (!isUnit && window.RTFEAT) below = window.RTFEAT.idsBlock(devs[at], RES.id) + below;     // wt/features: serial and asset tag from a close-up
    $('res-dev').innerHTML = '<div class="dvwrap">' + loc + '<div class="dvinfo">' + info + '</div></div><div class="dvbelow">' + below + '</div>';
    renderDevSide();
    var cine = $('cine'); if (cine) setTimeout(function () { cine.classList.add('play'); }, 350);
    var ob = $('res-dev').querySelector('.svgov .c-base'); if (ob) { var fitOv = function () { ob.nextElementSibling.setAttribute('viewBox', '0 0 ' + ob.naturalWidth + ' ' + ob.naturalHeight); }; if (ob.complete && ob.naturalWidth) fitOv(); else ob.onload = fitOv; }
    loadSpecs($('res-dev').querySelector('.specblock'));
    if (!isUnit && window.RTFEAT && devs[at]) window.RTFEAT.sfpInto($('res-dev').querySelector('.dvbelow'), RES.id, devs[at].index);   // SFP advice lives on the device page now, not in a tab
    [].forEach.call($('res-dev').querySelectorAll('.dvinfo > *, .dvbelow > *'), function (el, i) { el.style.setProperty('--i', i); });
    drawLocator();
  }
  // the locator: the front-view rack, small, copied from the Rack tab's picture
  function drawLocator() { var src = $('res-rack-flat'), dst = $('loc-cv'); if (!dst) return;
    var go = function () { dst.width = Math.max(1, Math.round(src.width / 2)); dst.height = Math.max(1, Math.round(src.height / 2)); dst.getContext('2d').drawImage(src, 0, 0, dst.width, dst.height); dst.classList.add('in'); };
    if (src.dataset.key && src.width > 1) go(); else src.addEventListener('painted', go, { once: true }); }
  // the close look: the crop grows out of the glowing box
  function openZoom() { var z = RES.zoom; if (!z) return; var ov = $('dv-zoomov'), hl = $('dv-hl');
    ov.innerHTML = '<div class="zcard"><span class="ztitle">' + z.title + '</span>' + z.html + '<span class="zhint">tap to close</span></div>'; ov.hidden = false;
    if (z.dev) clearestCrop(z.dev, $('dv-crop'));
    var card = ov.querySelector('.zcard'), r = hl ? hl.getBoundingClientRect() : null, c = card.getBoundingClientRect();
    if (r) { card.style.transformOrigin = '0 0'; card.style.transform = 'translate(' + (r.left - c.left) + 'px,' + (r.top - c.top) + 'px) scale(' + (r.width / c.width) + ',' + (r.height / c.height) + ')'; card.style.opacity = '0.2'; }
    requestAnimationFrame(function () { requestAnimationFrame(function () { ov.classList.add('on'); card.style.transform = ''; card.style.opacity = ''; }); }); }
  function closeZoom() { var ov = $('dv-zoomov'); ov.classList.remove('on'); setTimeout(function () { ov.hidden = true; ov.innerHTML = ''; }, 280); }
  $('dv-another').addEventListener('click', function () { RES.pick = { kind: RES.pick && RES.pick.kind === 'unit' ? 'unitlist' : 'devlist' }; renderOne(); secTo('devices'); });
  $('dv-rack').addEventListener('click', function () { go('result/' + RES.id); });
  $('dv-list').addEventListener('click', function (e) { var b = e.target.closest('.prow'); if (!b) return; if (b.dataset.unit) selectUnit(b.dataset.unit); else selectDevice(parseInt(b.dataset.dev, 10)); });
  $('res-dev').addEventListener('click', function (e) { var b = e.target.closest('[data-open-dev]'); if (b) return selectDevice(parseInt(b.dataset.openDev, 10));
    var o = e.target.closest('#dv-loc .lo'); if (o) return selectDevice(parseInt(o.dataset.dev, 10));
    if (e.target.closest('#dv-loc')) openZoom(); });
  $('dv-zoomov').addEventListener('click', closeZoom);
  function uploader(d, L) {
    if (!(L.mapped && (!L.fitted || L.confirm))) return '';
    return '<div class="up"><span class="eyebrow">Close-up</span><span class="small">' + (L.fitted ? 'Not sure about the model? ' : 'The make or model was not read from the video. ') + 'A sharp photo of the front panel is read for its make, model and ports.</span>'
      + '<label class="btn primary file"><input type="file" accept="image/*" capture="environment" class="closeup-in" data-dev="' + d.index + '" hidden><svg class="ic"><use href="#i-upload"/></svg>Take a close photo</label><p class="muted small upmsg" hidden></p></div>';
  }
  document.addEventListener('change', function (e) {
    var inp = e.target; if (!inp.classList || !inp.classList.contains('closeup-in')) return;
    var f = inp.files[0]; if (!f) return; var msg = inp.closest('.up').querySelector('.upmsg'); msg.hidden = false; msg.textContent = 'Reading the close-up, about a minute…';
    var fd = new FormData(); fd.append('file', f); fd.append('job', RES.id); fd.append('device', inp.dataset.dev);
    api('/api/closeup', { method: 'POST', body: fd }).then(function (r) { msg.textContent = r.shows_device === false ? r.detail : (r.fitted ? 'Read: ' + r.vendor + ' ' + r.model + ', ' + r.n_ports + ' ports. Updating…' : 'The close-up did not give a model either.'); setTimeout(function () { loadView(false); }, 1500); }).catch(function (err) { msg.textContent = err.message; });
  });
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('.readports'); if (!b) return;
    b.disabled = true;
    api('/api/video/' + RES.id + '/ports/' + b.dataset.dev, { method: 'POST' }).then(function (r) { if (r.state === 'none') { toast(r.detail); b.disabled = false; return; } toast('Reading D' + b.dataset.dev + ' in the background.'); if (RES.view) RES.view.pending_ports = true; watchPorts(); loadView(false); }).catch(function (err) { toast(err.message); b.disabled = false; });
  });
  // the kind can be corrected: Change -> the kinds -> saved with the run
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('.kind-edit'); if (b) { var pick = $('kind-pick'); pick.hidden = !pick.hidden; return; }
    var o = e.target.closest && e.target.closest('.kind-opt'); if (!o) return;
    var kind = o.dataset.kind === '' ? null : o.dataset.kind;
    api('/api/video/' + RES.id + '/device/' + o.dataset.dev + '/kind', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: kind }) })
      .then(function () { toast(kind ? 'D' + o.dataset.dev + ' is now ' + kind + '.' : 'Back to what the model said.'); loadView(false); }).catch(function (err) { toast(err.message); });
  });
  document.addEventListener('click', function (e) { var b = e.target.closest && e.target.closest('[data-scroll]'); if (b) { var strip = b.closest('.card').querySelector('.howstrip'); strip.scrollBy({ left: parseInt(b.dataset.scroll, 10) * Math.round(strip.clientWidth * 0.8), behavior: 'smooth' }); } });

  // ---------------------------------------------------------------- more
  $('install-btn').hidden = NATIVE || window.matchMedia('(display-mode: standalone)').matches;
  $('install-btn').addEventListener('click', function () { toast(/iPhone|iPad/.test(navigator.userAgent) ? 'In Safari: Share, then Add to Home Screen.' : 'In Chrome: the menu, then Install app.'); });
  if (NATIVE) { $('si-server').hidden = false; $('si-server').innerHTML = 'Server ' + esc(location.host) + ' · <a href="' + SHELL_URL + '?change=1">change</a>'; }

  // ---------------------------------------------------------------- pictures full size
  document.addEventListener('error', function (e) { var i = e.target; if (i && i.tagName === 'IMG' && i.getAttribute('src') && i.getAttribute('src').indexOf('data:') !== 0 && i.id !== 'lbimg') window.imgRetry(i); }, true);
  // the side menu opens and closes on the web; the choice is kept for this browser
  (function () { var k = 'rt_side_min', set = function (on) { document.body.classList.toggle('side-min', on); var b = $('side-tog'); if (b) { b.setAttribute('aria-label', on ? 'Open the menu' : 'Close the menu'); b.title = on ? 'Open the menu' : 'Close the menu'; } };
    try { set(localStorage.getItem(k) === '1'); } catch (e) {}
    var b = $('side-tog'); if (b) b.addEventListener('click', function () { var on = !document.body.classList.contains('side-min'); set(on); try { localStorage.setItem(k, on ? '1' : '0'); } catch (e) {} setTimeout(function () { window.dispatchEvent(new Event('resize')); }, 320); }); })();
  var lb = $('lb'), lbimg = $('lbimg');
  document.addEventListener('click', function (e) { var i = e.target.closest && e.target.closest('img'); if (i && i.id !== 'lbimg' && !i.closest('a') && !i.closest('.thumb') && !i.closest('#dv-zoomov') && !i.closest('.loc') && !i.closest('.dvshot') && !i.closest('.pthumb')) { lbimg.src = i.currentSrc || i.src; lb.hidden = false; } });   // any picture opens large (user, 2026-10-29); the small rack and list thumbnails keep their own taps
  lb.addEventListener('click', function () { lb.hidden = true; lbimg.src = ''; });

  // ---------------------------------------------------------------- start: who am I, then the splash goes; the service worker for the install
  if ('serviceWorker' in navigator && !NATIVE) { navigator.serviceWorker.register('/sw.js').catch(quiet('the offline helper')); }
  try { $('si-user').value = localStorage.getItem('rt_user') || ''; } catch (e) {}
  var t0 = Date.now();
  function ready() { setTimeout(function () { $('splash').classList.add('gone'); setTimeout(function () { $('splash').hidden = true; }, 400); }, Math.max(0, 450 - (Date.now() - t0))); }
  // racktrack-app sign-in: find the sign-in service, then ask the engine who is signed in (a short session is renewed once)
  function whoAmI(again) {
    // ask first whether there is a session or a renewal cookie at all: a signed-out browser then makes no refused call
    if (!again) return fetch('/api/auth/state', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : {}; }).then(function (st) {
      if (st.waiting) return { pending: true };
      if (st.signed_in) return whoAmI(true);
      if (st.can_renew) return AUTH.refresh().then(function (ok) { return ok ? whoAmI(true) : null; });
      return null;
    });
    return fetch('/api/me', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) {
      if (r.status === 401 && !again) return AUTH.refresh().then(function (ok) { return ok ? whoAmI(true) : null; });
      if (r.status === 403) return { pending: true };
      return r.ok ? r.json() : null;
    });
  }
  AUTH.where().then(function () { return whoAmI(); }).then(function (j) {
    me = (j && j.name) ? j : null; if (me) setWho(me);
    var h = location.hash.slice(1).split('/')[0];
    if (j && j.pending) location.hash = '#pending';
    else if (!location.hash || h === 'signin' || (me && AUTH_SCREENS[h])) location.hash = me ? '#home' : '#signin';
    route(); ready();
  }).catch(function () { route(); ready(); });
})();
