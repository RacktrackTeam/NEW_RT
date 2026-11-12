/* wt/features: the remaining RT-EOS functions on a run, as screens of this app (#tools/<run>/<tool>) and step 6 of the result.
   The content follows RT-EOS's own screens (rt_eos/client: FindPort, Connections, Read again, Physical layer, SFP advice,
   RackTopology, the A-end / Z-end check) in the RT-EOS minimal look; the data comes from server/app.py's wt/features routes. */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); };
  function api(path, opts) {
    return fetch(path, Object.assign({ credentials: 'same-origin', cache: 'no-store' }, opts)).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) { var e = new Error(j.error || r.statusText); e.status = r.status; throw e; } return j; });
    });
  }
  function post(path, body) { return api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }); }
  var TOOLS = [
    ['find', 'Find a port', 'Pick a device and a port number: it is marked on the rack picture with its cable and label.'],
    ['connections', 'Connections', 'Per device: its problems and a card of its ports.'],
    ['cables', 'Cables', 'The Excel matrix against the ports the video read, and the A-end / Z-end check with its CSV.']
  ];
  var NAME = {}; TOOLS.forEach(function (t) { NAME[t[0]] = t[1]; });
  var STATE = { connected: 'Cable plugged in', empty: 'Empty', unknown: 'Not sure', 'not seen': 'Not seen' };
  function stateWord(s) { return STATE[s] || 'Not sure'; }
  function root() { return $('ft-root'); }
  function head(title, lead, run) {
    return '<div class="phead ft-head"><div><span class="eyebrow">' + (run ? '<a href="#result/' + esc(run) + '">The rack</a> · ' : '') + 'Rack tools</span><h1>' + esc(title) + '</h1><p class="muted">' + esc(lead) + '</p></div></div>';
  }
  function fail(e) { root().innerHTML = '<div class="fx-page"><p class="fx-err">' + esc(String(e.message || 'Something went wrong').replace(/\.\s*$/, '')) + '. Go back to the rack and try again.</p></div>'; }

  function idsBlock(d, run) {
    var x = d.ids || {};
    var rows = [['Serial', x.serial], ['Asset tag', x.asset_tag], ['Labels read', (x.labels || []).join(', ')], ['Cable labels read', (x.cables || []).join(', ')]];
    var any = rows.some(function (r) { return r[1]; });   // nothing read: one line instead of four rows of "not read" (mobile pass, 10 Nov 2026)
    return '<div class="ft-ids card"><span class="eyebrow">Serial and asset tag</span>'
      + (any ? rows.map(function (r) { return '<div class="ft-kv"><span>' + r[0] + '</span><b class="mono">' + (r[1] ? esc(r[1]) : '<em class="muted">not read</em>') + '</b></div>'; }).join('')
             : '<div class="ft-kv"><span>Serial, asset tag and labels</span><b class="mono"><em class="muted">not read</em></b></div>')
      + (x.from ? '<p class="muted small">From a close photo' + (x.by ? ' taken by ' + esc(x.by) : '') + (x.when ? ', ' + esc(window.RTWHEN ? window.RTWHEN(x.when) : x.when) : '') + '.</p>' : '<p class="muted small">Not readable from the video. Take a close, sharp photo of the label.</p>')
      + '<label class="btn file"><input type="file" accept="image/*" capture="environment" class="ft-ids-in" data-dev="' + d.index + '" data-run="' + esc(run) + '" hidden><svg class="ic"><use href="#i-upload"/></svg>Take a close photo of the label</label><p class="muted small ft-ids-msg" hidden></p></div>';
  }
  document.addEventListener('change', function (e) {
    var inp = e.target; if (!inp.classList || !inp.classList.contains('ft-ids-in')) return;
    var f = inp.files[0]; if (!f) return; var msg = inp.parentNode.parentNode.querySelector('.ft-ids-msg'); msg.hidden = false; msg.textContent = 'Reading the close-up, about a minute...';
    var fd = new FormData(); fd.append('file', f); fd.append('job', inp.dataset.run); fd.append('device', inp.dataset.dev); fd.append('ids_only', '1');
    api('/api/closeup', { method: 'POST', body: fd }).then(function (r) {
      var x = r.ids || {}; msg.textContent = x.serial || x.asset_tag ? 'Read: ' + [x.serial ? 'serial ' + x.serial : '', x.asset_tag ? 'asset tag ' + x.asset_tag : ''].filter(Boolean).join(', ') + '. Kept on this device.' : 'No serial or asset tag was read on this photo. Take it closer, straight on the label.';
      setTimeout(function () { window.dispatchEvent(new HashChangeEvent('hashchange')); }, 1200);
    }).catch(function (e) { msg.textContent = e.message; });
  });

  // ------------------------------------------------------------ the tools
  var VIEW = {};
  // the rack's name in the tab bar and, on a phone, under the header title, on every tool screen as on the result tabs (finding 27)
  function nameBar(v) { var n = $('rnav'), r = v ? ((v.rack_id && v.rack_id.id) || v.title || '') : ''; if (n && v) n.dataset.rack = r;
    var t = $('topsub'); if (t && v && /^#tools\//.test(location.hash)) { t.textContent = r; t.hidden = !r; } if (window.RTTopbarHeight) requestAnimationFrame(window.RTTopbarHeight); return v; }   // the header may grow by a line: the tab row is placed under its real height (testers, 10 Nov 2026)
  // the key to the port cells, drawn with the cells themselves (testers, 10 Nov 2026: 'Filled number, Plain, Dashed' meant nothing)
  function keyHtml(tap) { var tag = tap ? 'button' : 'span'; return '<span class="ft-keys">' + [['on', 'Cable plugged in'], ['off', 'Empty'], ['na', 'Not sure']].map(function (k) { return '<' + tag + (tap ? ' type="button" data-key="' + k[0] + '"' : '') + ' class="ft-key"><span class="ft-cell ' + k[0] + ' sw" aria-hidden="true"></span>' + k[1] + '</' + tag + '>'; }).join('') + '</span>'; }   // on Connections each item is a button: it outlines every port in that state (the tester, 10 Nov 2026)
  var KEY_MEAN = { on: 'a cable is plugged into the port', off: 'the port is empty', na: 'the reading could not tell whether a cable is in it' };
  document.addEventListener('click', function (e) { var k = e.target.closest && e.target.closest('.fx-keybox .ft-key[data-key]'); if (!k) return;
    var box = k.closest('.fx-keybox'), st = k.dataset.key, word = k.textContent.trim(), ans = box.querySelector('.fx-keyans');
    [].forEach.call(box.querySelectorAll('.ft-key'), function (x) { x.classList.toggle('on2', x === k); });
    [].forEach.call(document.querySelectorAll('.fx-card .ft-cell.khl'), function (c) { c.classList.remove('khl'); });
    var cells = [].slice.call(document.querySelectorAll('.fx-card .ft-cell.' + st)); cells.forEach(function (c) { c.classList.add('khl'); });
    ans.hidden = false;
    if (!cells.length) { ans.innerHTML = '<b>' + esc(word) + '</b>: ' + KEY_MEAN[st] + '. No port on this rack is in this state.'; return; }
    var card = cells[0].closest('.fx-card');
    ans.innerHTML = '<b>' + esc(word) + '</b>: ' + KEY_MEAN[st] + '. ' + cells.length + (cells.length === 1 ? ' port' : ' ports') + ' on this rack, outlined on the cards below. <button type="button" class="link fx-keyeg">Show port ' + esc(cells[0].textContent.trim()) + ' of D' + esc(card.dataset.dev) + '</button>'; });
  document.addEventListener('click', function (e) { var b = e.target.closest && e.target.closest('.fx-keyeg'); if (!b) return; var c = document.querySelector('.fx-card .ft-cell.khl'); if (!c) return; c.scrollIntoView({ block: 'center' }); c.click(); });   // plain swatches, no numbers: a numbered sample looked like a chosen port (the tester, 10 Nov 2026)
  function view(run) { return VIEW[run] ? Promise.resolve(nameBar(VIEW[run])) : api('/api/video/' + encodeURIComponent(run) + '/view').then(function (v) { VIEW[run] = v; return nameBar(v); }); }
  function unitOf(d) { var a = parseInt(String(d.top).replace(/\D/g, ''), 10), b = parseInt(String(d.bottom).replace(/\D/g, ''), 10); return a === b ? 'U' + a : 'U' + Math.min(a, b) + '-' + Math.max(a, b); }
  function devLabel(d) { var L = d.layout || {}; return 'D' + d.index + ' · ' + unitOf(d) + ' · ' + (d.kind || (d.likely ? 'Not sure, likely ' + d.likely : 'Not sure')) + (L.vendor ? ' · ' + L.vendor + (L.model ? ' ' + L.model : '') : ''); }

  // ---- a picture with boxes drawn on it (an svg over the image, in the image's own pixels); the highlighted box pulses once
  function fig(src, boxes, cls) {
    return '<figure class="fx-fig ' + (cls || '') + '"><img src="' + esc(src) + '" alt=""><svg aria-hidden="false">' + boxes.map(function (b) {
      var q = b.box; if (!q) return '';
      return '<rect class="fx-box ' + (b.cls || '') + '" data-n="' + esc(b.n == null ? '' : b.n) + '" x="' + q[0] + '" y="' + q[1] + '" width="' + (q[2] - q[0]) + '" height="' + (q[3] - q[1]) + '" rx="2"><title>' + esc(b.tip || '') + '</title></rect>';
    }).join('') + '</svg></figure>';
  }
  function fitFigs(el) {
    [].forEach.call(el.querySelectorAll('.fx-fig'), function (f) {
      var im = f.querySelector('img'), sv = f.querySelector('svg'); if (!sv) return; var set = function () { if (im.naturalWidth) sv.setAttribute('viewBox', '0 0 ' + im.naturalWidth + ' ' + im.naturalHeight); };
      if (im.complete) set(); else im.addEventListener('load', set);
    });
  }
  function pill(t, c) { return '<span class="fx-pill ' + (c || '') + '">' + esc(t) + '</span>'; }
  function stCls(s) { return s === 'connected' ? 'on' : s === 'empty' ? 'off' : 'na'; }
  function loading(t) { return '<p class="fx-wait"><i class="fx-spin"></i>' + esc(t) + '</p>'; }
  function empty(t, next) { return '<div class="fx-empty"><b>' + esc(t) + '</b>' + (next ? '<span>' + esc(next) + '</span>' : '') + '</div>'; }
  function fileUrl(run, name) { return '/api/video/' + encodeURIComponent(run) + '/file/' + encodeURIComponent(name); }
  function shell(title, run, inner) { return '<div class="fx-page"><div class="fx-title"><h1>' + esc(title) + '</h1></div>' + inner + '</div>'; }

  // ---- Find a port: the device crop with the port outlined, the facts beside it
  function find(run, q) {
    view(run).then(function (v) {
      var hasPorts = function (d) { return !!((d.layout || {}).ports && d.layout.ports.length); };
      var devs = (v.devices || []).slice().sort(function (a, b) { return parseInt(String(b.top).replace(/\D/g, ''), 10) - parseInt(String(a.top).replace(/\D/g, ''), 10); });   // every device, top to bottom; one not read yet gets a Read the ports button (testers, 4 Nov 2026)
      var want = (q || '').split('-'), cur = { dev: parseInt(want[0], 10) || null, port: parseInt(want[1], 10) || null }, seq = 0;
      if (!devs.length) { root().innerHTML = shell('Find a port', run, empty('No device was read on this rack', 'Scan the rack again with a slower pass.')); return; }
      var devOf = function (i) { return devs.filter(function (d) { return Number(d.index) === i; })[0]; };
      if (!devOf(cur.dev) || !hasPorts(devOf(cur.dev))) cur.port = null;
      var W = (v.rack.size || [1000, 2000])[0], H = (v.rack.size || [1000, 2000])[1];
      var boxes = (v.devices || []).filter(function (d) { return d.polygon && d.polygon.length > 1; }).map(function (d) {
        var xs = d.polygon.map(function (p) { return p[0]; }), ys = d.polygon.map(function (p) { return p[1]; });
        return { i: Number(d.index), box: [Math.min.apply(null, xs), Math.min.apply(null, ys), Math.max.apply(null, xs), Math.max.apply(null, ys)], ports: !!devOf(Number(d.index)), tip: 'D' + d.index + ' · ' + unitOf(d) + ' · ' + (d.kind || 'Not sure') + (hasPorts(d) ? '' : ' · ports not read yet') };
      });
      var picture = '<figure class="fx-fig fx-rack fp-rack" id="fp-rack"><img src="' + esc(v.rack.picture) + '" alt="The stitched rack"><svg aria-hidden="false" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none">' + boxes.map(function (b) {
        return '<rect class="fx-box fp-dev' + (b.ports ? '' : ' fp-none') + '" data-i="' + b.i + '" x="' + b.box[0] + '" y="' + b.box[1] + '" width="' + (b.box[2] - b.box[0]) + '" height="' + (b.box[3] - b.box[1]) + '" rx="2"><title>' + esc(b.tip) + '</title></rect>';
      }).join('') + '</svg></figure>';
      root().innerHTML = shell('Find a port', run, '<div class="fp-top">' + picture + '<div class="fp-pick"><label><span>Device</span><select id="fp-dev">' + (cur.dev ? '' : '<option value="">Choose a device</option>')
        + devs.map(function (d) { return '<option value="' + d.index + '"' + (Number(d.index) === cur.dev ? ' selected' : '') + '>' + esc(devLabel(d)) + (hasPorts(d) ? '' : ' · ports not read yet') + '</option>'; }).join('') + '</select></label>'
        + '<label id="fp-portlab"><span>Port</span><select id="fp-port"></select></label><div id="fp-grid" class="ft-cells fp-grid" role="group" aria-label="Ports of this device"></div><div id="fp-readbox" class="fp-read" hidden></div>'
        + '<p class="fx-note" id="fp-hint"></p><div id="fp-out"></div></div></div>');
      fitFigs(root());
      var out = $('fp-out');
      function mark() { [].forEach.call(document.querySelectorAll('#fp-rack .fp-dev'), function (r) { r.classList.toggle('sel', Number(r.dataset.i) === cur.dev); }); }
      var READING = {};                                                        // devices whose ports are being read from this screen: polled until they are there
      function readPorts(d) {
        var box = $('fp-readbox'); if (!box) return; box.innerHTML = '<p class="fx-note">Reading the ports of D' + d.index + ', about a minute. The list fills in when they are there.</p>'; READING[d.index] = 1;
        api('/api/video/' + encodeURIComponent(run) + '/ports/' + d.index, { method: 'POST' }).then(function (r) {
          if (r.state === 'none') { box.innerHTML = '<p class="fx-note">' + esc(r.detail || 'This device has no ports to read.') + '</p>'; delete READING[d.index]; return; }
          var tick = function () { if (!READING[d.index] || location.hash.indexOf('#tools/' + run + '/find') !== 0) return;
            api('/api/video/' + encodeURIComponent(run) + '/view').then(function (nv) { var nd = (nv.devices || []).filter(function (x) { return Number(x.index) === Number(d.index); })[0], st = nd && nd.layout && nd.layout.ports_state;
              if (nd && hasPorts(nd)) { delete READING[d.index]; VIEW[run] = nv; SHOWN = null; find(run, String(d.index)); return; }
              if (st === 'failed' || st === 'none') { delete READING[d.index]; box.innerHTML = '<p class="fx-note">' + (st === 'failed' ? 'The ports could not be read from the video. A close photo of the front, on the Devices tab, reads them.' : 'No port was found on this device.') + '</p>'; return; }
              setTimeout(tick, 6000); }).catch(function () { setTimeout(tick, 8000); }); };
          setTimeout(tick, 6000);
        }).catch(function (e) { box.innerHTML = '<p class="fx-err">' + esc(String(e.message).replace(/\.\s*$/, '')) + '.</p>'; delete READING[d.index]; });
      }
      function pickers() {
        var d = devOf(cur.dev), ports = d && hasPorts(d) ? d.layout.ports.filter(function (p) { return p.id != null; }) : [];
        var isNum = function (p) { return /^\d+$/.test(String(p.id)); }; ports = ports.filter(isNum).concat(ports.filter(function (p) { return !isNum(p); }));   // the numbered ports first, then console, USB and the like by their name (the tester's choice, 5 Nov 2026)
        var pname = function (p) { return isNum(p) ? 'Port ' + p.id : String(p.id).replace(/_/g, ' ').toUpperCase(); };
        var rb = $('fp-readbox'); rb.hidden = !(d && !hasPorts(d)); $('fp-portlab').hidden = !!(d && !hasPorts(d)); $('fp-grid').hidden = !!(d && !hasPorts(d));
        if (d && !hasPorts(d) && !READING[d.index]) { var st = (d.layout || {}).ports_state;
          rb.innerHTML = st === 'running' ? '<p class="fx-note">The ports of D' + d.index + ' are being read, about a minute. The list fills in when they are there.</p>' : '<p class="fx-note">The ports of this device have not been read yet.</p><button class="btn primary" type="button" id="fp-read">Read the ports</button>';
          if (st === 'running') { READING[d.index] = 1; readPorts(d); } else { var b = $('fp-read'); if (b) b.onclick = function () { readPorts(d); }; } }
        $('fp-port').innerHTML = (cur.port ? '' : '<option value="">Choose a port</option>') + ports.map(function (p) { return '<option value="' + p.id + '"' + (p.id === cur.port ? ' selected' : '') + '>' + esc(pname(p)) + ' · ' + esc(stateWord(p.status)) + '</option>'; }).join('');
        $('fp-port').disabled = !d; if (d) $('fp-dev').value = String(cur.dev);   // a device tapped on the rack picture shows in the picker too
        $('fp-grid').innerHTML = ports.map(function (p) { var st = p.status === 'connected' ? 'on' : p.status === 'empty' ? 'off' : 'na';
          return '<button type="button" class="ft-cell ' + st + (p.id === cur.port ? ' chosen' : '') + '" data-p="' + p.id + '" title="Port ' + p.id + ': ' + esc(stateWord(p.status)) + '" aria-pressed="' + (p.id === cur.port) + '">' + p.id + '</button>'; }).join('');
        $('fp-hint').innerHTML = d && hasPorts(d) ? keyHtml() : d ? '' : 'Choose a device, or tap one on the picture.';
      }
      function show() {
        mark(); pickers();
        if (cur.dev) history.replaceState(null, '', '#tools/' + run + '/find/' + cur.dev + (cur.port ? '-' + cur.port : ''));
        if (!(cur.dev && cur.port)) { out.innerHTML = ''; return; }   // no placeholder card: the line above says what to do (the tester, 10 Nov 2026)
        var my = ++seq; out.innerHTML = loading('Finding port ' + cur.port + '...');
        api('/api/video/' + encodeURIComponent(run) + '/find-port?device=' + cur.dev + '&port=' + cur.port).then(function (r) {
          if (my !== seq) return;
          var p = r.read || {}, c = r.cable, pic;
          if (r.placed === 'crop') pic = fig(fileUrl(run, r.crop_file), Object.keys(r.port_boxes).map(function (k) { return { box: r.port_boxes[k], n: k, cls: (String(k) === String(r.port) ? 'hl' : ''), tip: 'Port ' + k }; }), 'fx-find');
          else pic = '<figure class="fx-fig"><img src="' + esc(r.picture) + '" alt=""></figure>';
          var note = r.placed ? 'Tap another port on the picture to look at it.' : 'Port ' + r.port + ' could not be placed on the picture: the ports of this device were counted, not located.';
          // what is missing says why: the cable, label, other end and cable type come from the record; the colour and connector from the video (finding 28)
          var kv = [['Port type', p.type ? String(p.type).toUpperCase() : null, 'not read'], ['State', stateWord(p.state), 'not read'], ['Cable', c ? c.cable : null, 'not in the record'], ['Label', c ? c.label : null, 'not in the record'],
            ['Other end', c ? [c.other.device, c.other.port ? 'port ' + c.other.port : ''].filter(Boolean).join(' ') : null, 'not in the record'], ['Cable type', c ? c.type : null, 'not in the record'], ['Colour read', p.colour, 'not read'],
            ['Connector', p.connector ? String(p.connector).replace(/^RJ-?45$/i, 'RJ45') : null, 'not read']];
          out.innerHTML = '<div class="fx-two"><div class="fx-left">' + pic + '<p class="fx-note">' + esc(note) + '</p></div><div class="fx-right fx-in"><span class="fx-eye">' + esc(r.device) + ' · ' + esc(r.unit) + '</span>'
            + (r.found ? '<h2>Port ' + r.port + '</h2><div class="fx-pills">' + pill(stateWord(p.state), stCls(p.state)) + (r.kind ? pill(r.kind) : '') + '</div>' : '<h2>Port ' + r.port + ' was not read</h2><p class="muted">Ports read: ' + esc((r.ports || []).join(', ') || 'none') + '.</p>')
            + '<dl class="fx-kv">' + kv.map(function (x) { return '<dt>' + x[0] + '</dt><dd>' + (x[1] ? esc(x[1]) : '<em>' + x[2] + '</em>') + '</dd>'; }).join('') + '</dl></div></div>';
          fitFigs(out);
          var ot = out.getBoundingClientRect().top; if (ot > window.innerHeight - 160 || ot < 0) out.scrollIntoView({ block: 'start', behavior: 'smooth' });   // the answer comes into view by itself (testers, 4 Nov 2026)
        }).catch(function (e) { if (my === seq) out.innerHTML = '<p class="fx-err">' + esc(String(e.message).replace(/\.\s*$/, '')) + '. Try another port.</p>'; });
      }
      $('fp-dev').onchange = function () { cur.dev = parseInt(this.value, 10) || null; cur.port = null; show(); };
      $('fp-readbox').onclick = function (e) { var b = e.target.closest && e.target.closest('#fp-read'); if (b) { var d = devOf(cur.dev); if (d) readPorts(d); } };
      $('fp-port').onchange = function () { cur.port = parseInt(this.value, 10) || null; show(); };
      $('fp-grid').onclick = function (e) { var b = e.target.closest('[data-p]'); if (b) { cur.port = parseInt(b.dataset.p, 10); show(); } };
      $('fp-rack').onclick = function (e) { var r = e.target.closest('.fp-dev'); if (!r) return; cur.dev = parseInt(r.dataset.i, 10); cur.port = null; show(); };
      out.onclick = function (e) { var b = e.target.closest && e.target.closest('.fx-box'); if (b && b.dataset.n) { cur.port = parseInt(b.dataset.n, 10); show(); } };
      show();
    }).catch(fail);
  }

  function cells(ports) {
    return '<div class="ft-cells">' + ports.filter(function (p) { return p.n != null; }).map(function (p) {
      var s = p.state === 'connected' ? 'on' : p.state === 'empty' ? 'off' : 'na';
      return '<span class="ft-cell ' + s + '" title="Port ' + p.n + ': ' + esc(stateWord(p.state)) + '">' + p.n + '</span>';
    }).join('') + '</div>';
  }
  // the same problem on several ports of one device is one line ("Wrong port type, ports 25 to 28"), not one line a port
  function probGroups(list) {
    var out = [], by = {};
    list.forEach(function (p) {
      var m = p.port && /^Port \d+ of (.+?) is (.*)$/.exec(p.words || ''), k = m ? p.what + '|' + m[1] + '|' + m[2] : null;
      if (k && by[k]) { by[k].nums.push(p.port); return; }
      var g = { what: p.what, port: p.port, words: p.words, m: m, nums: [p.port] }; out.push(g); if (k) by[k] = g;
    });
    return out.map(function (g) {
      if (g.nums.length < 2) return g;
      var n = g.nums.map(Number), run = n.every(function (x, i) { return !i || x === n[i - 1] + 1; });
      var ports = run ? n[0] + ' to ' + n[n.length - 1] : n.join(', ');
      return { what: g.what, ports: ports, words: 'Ports ' + ports + ' of ' + g.m[1] + ' are ' + g.m[2] };
    });
  }
  // ---- Connections: each device's crop with its ports coloured by state, the problems and a port card beside it
  function connections(run) {
    view(run).catch(function () { return null; });                      // the rack's name in the header
    api('/api/video/' + encodeURIComponent(run) + '/connections').then(function (r) {
      root().innerHTML = shell('Connections', run, '<div class="fx-keybox">' + keyHtml(true) + '<p class="fx-keyans" hidden></p></div>'
        + (r.devices.length ? '<div class="fx-grid">' + r.devices.map(function (d, i) {
          var boxes = d.ports.map(function (p) { return { box: p.box, n: p.n, cls: stCls(p.state), tip: 'Port ' + p.n + ': ' + stateWord(p.state) }; });
          var pic = d.cut && boxes.some(function (b) { return b.box; }) ? fig(fileUrl(run, d.cut), boxes) : '<figure class="fx-fig"><img src="/api/video/' + encodeURIComponent(run) + '/crop/' + d.index + '" alt=""></figure>';
          return '<section class="fx-card fx-in" style="--i:' + i + '" data-dev="' + d.index + '">' + pic + '<div class="fx-body"><div class="fx-head"><div><span class="fx-eye">' + esc(d.unit) + ' · D' + d.index + '</span><h2>' + esc(d.name) + '</h2></div>'
            + pill(d.problems.length ? d.problems.length + (d.problems.length === 1 ? ' problem' : ' problems') : 'No problem', d.problems.length ? 'bad' : 'ok') + '</div>'
            + '<div class="fx-pills">' + pill(d.kind || 'Not sure') + (d.ports.length ? pill(d.counts.connected + ' cabled', 'on') + pill(d.counts.empty + ' empty', 'off') + (d.counts.unknown ? pill(d.counts.unknown + ' not sure', 'na') : '') : pill('No port read', 'na')) + '</div>'
            + (d.problems.length ? '<ul class="fx-probs">' + probGroups(d.problems).map(function (p) { return '<li><b>' + esc(p.what) + (p.ports ? ', ports ' + p.ports : p.port ? ', port ' + p.port : '') + '</b> ' + esc(p.words) + '</li>'; }).join('') + '</ul>' : '')
            + (d.ports.length ? '<span class="fx-eye fx-portsof">Ports of D' + d.index + '</span>' + cells(d.ports) + '<p class="fx-note fx-portsay" hidden></p>' : '') + '</div></section>';
        }).join('') + '</div>' : empty('No device was read on this rack', 'Scan the rack again with a slower pass.')));
      fitFigs(root());
      root().onclick = function (e) {
        var c = e.target.closest && e.target.closest('.ft-cell'); if (!c) return; var card = c.closest('.fx-card'), n = c.textContent.trim();
        [].forEach.call(card.querySelectorAll('.fx-box.hl'), function (b) { b.classList.remove('hl'); });
        var b = card.querySelector('.fx-box[data-n="' + n + '"]'); if (b) { void b.getBBox; b.classList.add('hl'); }
        [].forEach.call(card.querySelectorAll('.ft-cell.chosen'), function (z) { z.classList.remove('chosen'); }); c.classList.add('chosen');
        var say = card.querySelector('.fx-portsay'); if (say) { say.hidden = false; say.textContent = (c.title || '').replace(/^Port (\S+): /, 'Port $1 of D' + card.dataset.dev + ': ') + (b ? '. Outlined on the picture above.' : '. Not placed on the picture.'); }   // an empty port answers too (testers, 10 Nov 2026)
      };
    }).catch(fail);
  }

  function table(rows, cols) {
    return '<div class="ft-wrap"><table class="ft-table"><thead><tr>' + cols.map(function (c) { return '<th>' + esc(c[0]) + '</th>'; }).join('') + '</tr></thead><tbody>'
      + rows.map(function (r) { return '<tr>' + cols.map(function (c) { var v = c[1](r); return '<td data-label="' + esc(c[0]) + '">' + (v == null || v === '' ? '<span class="muted">-</span>' : esc(v)) + '</td>'; }).join('') + '</tr>'; }).join('') + '</tbody></table></div>';
  }
  var AZ = { matched: 'Matched', wrong_port: 'Wrong port', missing_cable: 'Missing cable', not_found: 'Label not found', not_in_sheet: 'Not in the sheet' };
  // ---- Cables: the rack picture, the cable ends as a short list; picking one highlights its device
  function cables(run) {
    Promise.all([api('/api/video/' + encodeURIComponent(run) + '/cables'), api('/api/video/history'), view(run)]).then(function (x) {
      var r = x[0], runs = x[1].filter(function (h) { return h.status === 'done'; }), v = x[2];
      var boxes = function (pick) { return (v.devices || []).map(function (d) { var xs = (d.polygon || []).map(function (q) { return q[0]; }), ys = (d.polygon || []).map(function (q) { return q[1]; });
        return { box: xs.length ? [Math.min.apply(0, xs), Math.min.apply(0, ys), Math.max.apply(0, xs), Math.max.apply(0, ys)] : null, n: d.index, cls: d.index === pick ? 'hl' : '', tip: 'D' + d.index }; }); };
      var sel = function (o) { return runs.map(function (h) { return '<option value="' + esc(h.id) + '"' + (h.id === run ? ' selected' : '') + '>' + esc((h.rack_id || h.title) + ' (' + [h.site_name, window.RTWHEN ? window.RTWHEN(h.started) : h.started].filter(Boolean).join(', ') + ')') + '</option>'; }).join(''); };
      root().innerHTML = shell('Cables', run, '<div class="fx-two"><div class="fx-left"><div id="cb-pic">' + fig(fileUrl(run, 'rack.png'), boxes(null), 'fx-rack') + '</div></div><div class="fx-right fx-in">'
        + (r.ends_checked || r.rows.length || r.ends_not_read ? '<div class="fx-pills">' + pill(r.ends_checked + ' ends checked', 'na') + pill(r.rows.length + ' to look at', r.rows.length ? 'bad' : 'ok') + (r.ends_not_read ? pill(r.ends_not_read + ' on unread ports', 'na') : '') + '</div>'
          + '<p class="muted">' + esc(r.words) + '</p>' : '')      // nothing to check: the one card below says so, not three lines saying the same
        + (r.rows.length ? '<ul class="fx-list" id="cb-rows">' + r.rows.map(function (w) { return '<li><button type="button" class="fx-row" data-dev="' + w.index + '">' + pill(w.what, 'bad') + '<span><b>' + esc(w.unit) + ' · D' + w.index + ', port ' + esc(w.port) + '</b><small>' + esc(w.cable || '') + '</small></span></button></li>'; }).join('') + '</ul><p class="fx-note">Pick a cable to see its device on the picture.</p>'
          : r.ends_checked ? empty('The cables agree with the matrix', 'Nothing to fix here.') : empty('No cable to check', 'The Excel copy has no cable row with an end in this rack.'))
        + '</div></div>'
        + '<form class="fx-bar" id="az-form"><label><span>A-end rack</span><select id="az-a">' + sel() + '</select></label><label><span>Z-end rack</span><select id="az-z">' + sel() + '</select></label><button class="btn primary" type="submit">Check the cable labels</button></form><div id="az-out"></div>');
      fitFigs(root());
      if ($('cb-rows')) $('cb-rows').onclick = function (e) { var b = e.target.closest('.fx-row'); if (!b) return; [].forEach.call($('cb-rows').querySelectorAll('.fx-row.on'), function (z) { z.classList.remove('on'); }); b.classList.add('on');
        $('cb-pic').innerHTML = fig(fileUrl(run, 'rack.png'), boxes(parseInt(b.dataset.dev, 10)), 'fx-rack'); fitFigs($('cb-pic')); };
      $('az-form').addEventListener('submit', function (e) {
        e.preventDefault(); $('az-out').innerHTML = loading('Checking the labels...');
        post('/api/cables/az', { a: $('az-a').value, z: $('az-z').value }).then(function (c) {
          var k = c.counts;
          $('az-out').innerHTML = '<div class="fx-pills fx-in">' + pill(k.matched + ' matched', 'ok') + pill(k.wrongPort + ' wrong port', k.wrongPort ? 'bad' : 'na') + pill(k.missingCable + ' missing', k.missingCable ? 'bad' : 'na') + pill(k.notFound + ' not found', 'na') + pill(k.notInSheet + ' not in the workbook', 'na')
            + pill(c.accuracy.labelsRead + ' of ' + c.accuracy.labelsExpected + ' labels read') + '<a class="btn" id="az-csv" href="/api/cables/az/' + esc(c.id) + '.csv" download>Download the CSV</a></div>'
            + table(c.cables, [['Cable', function (x) { return x.cableLabel; }], ['Status', function (x) { return AZ[x.status] || x.status; }],
              ['A-end', function (x) { return x.a && x.a.expected ? [x.a.expected.device, x.a.expected.port ? 'port ' + x.a.expected.port : ''].filter(Boolean).join(' ') + ': ' + (AZ[x.a.status] || 'not in these racks') : x.a && x.a.found ? 'read on ' + x.a.found.device : ''; }],
              ['Z-end', function (x) { return x.z && x.z.expected ? [x.z.expected.device, x.z.expected.port ? 'port ' + x.z.expected.port : ''].filter(Boolean).join(' ') + ': ' + (AZ[x.z.status] || 'not in these racks') : ''; }],
              ['Why', function (x) { return x.aExplained || x.zExplained || ''; }]].filter(function (col) { return col[0] !== 'Why' || c.cables.some(function (x) { return x.aExplained || x.zExplained; }); }));   // no Why column when no row has a reason
        }).catch(function (e2) { $('az-out').innerHTML = '<p class="fx-err">' + esc(String(e2.message).replace(/\.\s*$/, '')) + '. Check both racks are scanned and try again.</p>'; });
      });
    }).catch(fail);
  }

  function authBase() { return fetch('/api/auth-service', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (j) { return j.url ? j.url.replace(/\/+$/, '') : location.protocol + '//' + location.hostname + ':' + (j.port || 3302); }); }
  function site() {
    Promise.all([api('/api/me'), authBase()]).then(function (x) {
      var me = x[0], base = x[1];
      if (!(me.role === 'org_owner' || me.role === 'org_admin' || me.role === 'site_manager') || !me.site) {   // a technician (or the platform owner, who has no site) cannot change it
        root().innerHTML = head('Site position', 'Only a site manager or an organization admin can set where ' + (me.site_name || 'the site') + ' is.', null); return;
      }
      return fetch(base + '/api/setup/' + me.site, { credentials: 'include', cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (s) {
        var dc = (s && (s.datacentre || (s.setup && s.setup.datacentre))) || {};
        root().innerHTML = head('Site position', 'Where ' + (me.site_name || 'your site') + ' is. A scan sent with the phone\'s position is checked against it: racks of sites within 300 m stay in, the others drop out. The position only narrows the racks, it never decides.', null)
          + '<form class="card ft-form" id="st-form"><label class="ft-field"><span>Latitude</span><input id="st-lat" inputmode="decimal" value="' + esc(dc.lat == null ? '' : dc.lat) + '" placeholder="17.4486"></label>'
          + '<label class="ft-field"><span>Longitude</span><input id="st-lng" inputmode="decimal" value="' + esc(dc.lng == null ? '' : dc.lng) + '" placeholder="78.3908"></label><button class="btn primary" type="submit">Save</button></form><p class="muted small" id="st-msg"></p>';
        $('st-form').addEventListener('submit', function (e) {
          e.preventDefault();
          fetch(base + '/api/setup/' + me.site + '/datacentre', { method: 'PUT', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ lat: parseFloat($('st-lat').value), lng: parseFloat($('st-lng').value) }) })
            .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || 'Not saved.'); return j; }); })
            .then(function (j) { var d = j.datacentre || {}; $('st-msg').textContent = 'Saved: ' + d.lat + ', ' + d.lng + '.'; })
            .catch(function (e2) { $('st-msg').textContent = e2.message; });
        });
      });
    }).catch(fail);
  }

  var RUNNERS = { find: find, connections: connections, cables: cables };
  var SHOWN = null;                                   // the tool on screen: the app routes again now and then (session checks), which must not reload it
  function open(run, tool, q) {
    var key = [run, tool, q].join('/'); if (key === SHOWN && root().children.length) return; SHOWN = key;
    root().onclick = null;
    if (run === 'site') return site();
    if (!run) return;
    if (!RUNNERS[tool]) tool = 'find';                  // an old link to a removed tool (Read again, Physical layer, SFP, the old cable drawing) opens Find a port
    root().innerHTML = '<div class="fx-page">' + loading('Loading ' + NAME[tool].toLowerCase() + '...') + '</div>';
    RUNNERS[tool](run, q);
  }
  function onHash() {
    var h = (location.hash || '').slice(1).split('/');
    if (h[0] !== 'tools' || [h[1], h[2], h[3]].join('/') !== SHOWN) SHOWN = null;
    if (h[0] === 'result' && h[1]) delete VIEW[h[1]];
  }
  window.addEventListener('hashchange', onHash); onHash();
  // SFP advice on a device's own page: which optic and cable fit the fibre or SFP ports read on it (nothing for a device without them)
  var SFPS = {};
  function sfpInto(box, run, index) {
    if (!box) return;
    (SFPS[run] ? Promise.resolve(SFPS[run]) : api('/api/video/' + encodeURIComponent(run) + '/sfp-advice').then(function (r) { SFPS[run] = r; return r; })).then(function (r) {
      var xs = (r.items || []).filter(function (x) { return x.index === index; }); if (!xs.length || !box.isConnected) return;
      box.insertAdjacentHTML('beforeend', '<div class="card ft-sfp"><span class="eyebrow">SFP advice</span>' + xs.map(function (x) {
        return '<h2>' + x.count + ' ' + esc(x.port_type) + (x.count === 1 ? ' port' : ' ports') + '</h2><div class="ft-kv"><span>Optic</span><b>' + esc(x.suggestion.optic) + '</b></div><div class="ft-kv"><span>Cable</span><b>' + esc(x.suggestion.cable) + '</b></div>' + (x.suggestion.note ? '<p class="muted small">' + esc(x.suggestion.note) + '</p>' : ''); }).join('') + '</div>');
    }).catch(function (e) { if (window.console) console.warn('RackTrack: SFP advice failed: ' + ((e && e.message) || e)); });
  }
  window.RTFEAT = { open: open, idsBlock: idsBlock, sfpInto: sfpInto };
})();
