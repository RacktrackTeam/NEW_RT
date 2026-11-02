/* wt/features: the remaining RT-EOS functions on a run, as screens of this app (#tools/<run>/<tool>) and step 6 of the result.
   The content follows RT-EOS's own screens (rt_eos/client: FindPort, Connections, Worklist, Read again, Physical layer, SFP advice,
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
    ['cables', 'Cables', 'The Excel matrix against the ports the video read, and the A-end / Z-end check with its CSV.'],
    ['worklist', 'Worklist', 'The readings the video is least sure of: confirm or correct them.'],
    ['again', 'Read again', 'Read every device again with the current models. Your corrections are kept.'],
    ['physical', 'Physical layer', 'The physical layer document: JSON and a page to print.'],
    ['sfp', 'SFP advice', 'Which optic and cable fit the fibre and SFP ports.'],
    ['topology', 'Topology', 'The devices and the cables between them, drawn.']
  ];
  var NAME = {}; TOOLS.forEach(function (t) { NAME[t[0]] = t[1]; });
  var STATE = { connected: 'Cable plugged in', empty: 'Empty', unknown: 'Not sure', 'not seen': 'Not seen' };
  function stateWord(s) { return STATE[s] || 'Not sure'; }
  function root() { return $('ft-root'); }
  function head(title, lead, run) {
    return '<div class="phead ft-head"><div><span class="eyebrow">' + (run ? '<a href="#result/' + esc(run) + '">The rack</a> · ' : '') + 'Rack tools</span><h1>' + esc(title) + '</h1><p class="muted">' + esc(lead) + '</p></div></div>';
  }
  function fail(e) { root().innerHTML = '<div class="fx-page"><p class="fx-err">' + esc(e.message || 'Something went wrong') + '. Go back to the rack and try again.</p></div>'; }

  // ------------------------------------------------------------ step 6 of the result: the list of tools
  function toolList(run) {
    return '<div class="card group ft-list">' + TOOLS.map(function (t) {
      return '<a class="grow" href="#tools/' + esc(run) + '/' + t[0] + '"><span class="ft-t"><b>' + esc(t[1]) + '</b><small>' + esc(t[2]) + '</small></span><span class="arr">→</span></a>';
    }).join('') + '</div>';
  }
  function fillResult(run) {
    var el = $('res-tools'); if (!el) return;
    el.innerHTML = '<div class="card group ft-list">' + TOOLS.map(function (t) {
      return '<a class="grow" href="#tools/' + esc(run) + '/' + t[0] + '"><span class="ft-t"><b>' + esc(t[1]) + '</b><small>' + esc(t[2]) + '</small></span><span class="arr">→</span></a>';
    }).join('') + '</div>';
  }

  // ------------------------------------------------------------ the device page: serial, asset tag, labels (close-up)
  function idsBlock(d, run) {
    var x = d.ids || {};
    var rows = [['Serial', x.serial], ['Asset tag', x.asset_tag], ['Labels read', (x.labels || []).join(', ')], ['Cable labels read', (x.cables || []).join(', ')]];
    return '<div class="ft-ids card"><span class="eyebrow">Serial and asset tag</span>'
      + rows.map(function (r) { return '<div class="ft-kv"><span>' + r[0] + '</span><b class="mono">' + (r[1] ? esc(r[1]) : '<em class="muted">not read</em>') + '</b></div>'; }).join('')
      + (x.from ? '<p class="muted small">From a close-up' + (x.by ? ' taken by ' + esc(x.by) : '') + (x.when ? ', ' + esc(String(x.when).slice(0, 16).replace('T', ' ')) : '') + '.</p>' : '<p class="muted small">The video is too small to read them. A close-up of the device label reads them.</p>')
      + '<label class="btn file"><input type="file" accept="image/*" capture="environment" class="ft-ids-in" data-dev="' + d.index + '" data-run="' + esc(run) + '" hidden>Read from a close-up</label><p class="muted small ft-ids-msg" hidden></p></div>';
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
  function view(run) { return VIEW[run] ? Promise.resolve(VIEW[run]) : api('/api/video/' + encodeURIComponent(run) + '/view').then(function (v) { VIEW[run] = v; return v; }); }
  function unitOf(d) { var a = parseInt(String(d.top).replace(/\D/g, ''), 10), b = parseInt(String(d.bottom).replace(/\D/g, ''), 10); return a === b ? 'U' + a : 'U' + Math.min(a, b) + '-' + Math.max(a, b); }
  function devLabel(d) { var L = d.layout || {}; return 'D' + d.index + ' · ' + unitOf(d) + ' · ' + (d.kind || 'Not sure') + (L.vendor ? ' · ' + L.vendor + (L.model ? ' ' + L.model : '') : ''); }

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
      var devs = (v.devices || []).filter(function (d) { return (d.layout || {}).ports && d.layout.ports.length; }).sort(function (a, b) { return parseInt(String(b.top).replace(/\D/g, ''), 10) - parseInt(String(a.top).replace(/\D/g, ''), 10); });
      var want = (q || '').split('-'), wd = parseInt(want[0], 10), wp = parseInt(want[1], 10);
      root().innerHTML = shell('Find a port', run,
        '<form class="fx-bar" id="fp-form"><label><span>Device</span><select id="fp-dev">' + devs.map(function (d) { return '<option value="' + d.index + '"' + (d.index === wd ? ' selected' : '') + '>' + esc(devLabel(d)) + '</option>'; }).join('') + '</select></label>'
        + '<label class="fx-num"><span>Port</span><input id="fp-port" type="number" min="1" max="999" value="' + (wp || 1) + '" inputmode="numeric"></label><button class="btn primary" type="submit">Show this port</button></form><div id="fp-out">'
        + (wd && wp ? loading('Finding port ' + wp + '...') : empty('Pick a device and a port', 'The port is outlined on the picture of the device.')) + '</div>');
      $('fp-form').addEventListener('submit', function (e) { e.preventDefault(); location.hash = '#tools/' + run + '/find/' + $('fp-dev').value + '-' + $('fp-port').value; });
      if (!(wd && wp)) return;
      api('/api/video/' + encodeURIComponent(run) + '/find-port?device=' + wd + '&port=' + wp).then(function (r) {
        var p = r.read || {}, c = r.cable, pic;
        if (r.placed === 'crop') pic = fig(fileUrl(run, r.crop_file), Object.keys(r.port_boxes).map(function (k) { return { box: r.port_boxes[k], n: k, cls: (String(k) === String(r.port) ? 'hl' : ''), tip: 'Port ' + k }; }), 'fx-find');
        else pic = '<figure class="fx-fig"><img src="' + esc(r.picture) + '" alt=""></figure>';
        var note = r.placed ? 'Tap another port on the picture to look at it.' : 'Port ' + r.port + ' could not be placed on the picture: the ports of this device were counted, not located.';
        var kv = [['Cable', c ? c.cable : null], ['Label', c ? c.label : null], ['Other end', c ? [c.other.device, c.other.port ? 'port ' + c.other.port : ''].filter(Boolean).join(' ') : null], ['Cable type', c ? c.type : null], ['Colour read', p.colour], ['Connector', p.connector]];
        $('fp-out').innerHTML = '<div class="fx-two"><div class="fx-left">' + pic + '<p class="fx-note">' + esc(note) + '</p></div><div class="fx-right fx-in"><span class="fx-eye">' + esc(r.device) + ' · ' + esc(r.unit) + '</span>'
          + (r.found ? '<h2>Port ' + r.port + '</h2><div class="fx-pills">' + pill(stateWord(p.state), stCls(p.state)) + (p.type ? pill(String(p.type).toUpperCase()) : '') + pill(r.kind || 'Not sure') + '</div>'
            : '<h2>Port ' + r.port + ' was not read</h2><p class="muted">Ports read: ' + esc((r.ports || []).join(', ') || 'none') + '.</p>')
          + '<dl class="fx-kv">' + kv.map(function (x) { return '<dt>' + x[0] + '</dt><dd>' + (x[1] ? esc(x[1]) : '<em>none</em>') + '</dd>'; }).join('') + '</dl></div></div>';
        fitFigs($('fp-out'));
        $('fp-out').onclick = function (e) { var b = e.target.closest && e.target.closest('.fx-box'); if (b && b.dataset.n) location.hash = '#tools/' + run + '/find/' + wd + '-' + b.dataset.n; };
      }).catch(function (e) { $('fp-out').innerHTML = '<p class="fx-err">' + esc(e.message) + '. Try another port.</p>'; });
    }).catch(fail);
  }

  function cells(ports) {
    return '<div class="ft-cells">' + ports.filter(function (p) { return p.n != null; }).map(function (p) {
      var s = p.state === 'connected' ? 'on' : p.state === 'empty' ? 'off' : 'na';
      return '<span class="ft-cell ' + s + '" title="Port ' + p.n + ': ' + esc(stateWord(p.state)) + '">' + p.n + '</span>';
    }).join('') + '</div>';
  }
  // ---- Connections: each device's crop with its ports coloured by state, the problems and a port card beside it
  function connections(run) {
    api('/api/video/' + encodeURIComponent(run) + '/connections').then(function (r) {
      root().innerHTML = shell('Connections', run, '<p class="fx-key">' + pill('Cable plugged in', 'on') + pill('Empty', 'off') + pill('Not sure', 'na') + '</p>'
        + (r.devices.length ? '<div class="fx-grid">' + r.devices.map(function (d, i) {
          var boxes = d.ports.map(function (p) { return { box: p.box, n: p.n, cls: stCls(p.state), tip: 'Port ' + p.n + ': ' + stateWord(p.state) }; });
          var pic = d.cut && boxes.some(function (b) { return b.box; }) ? fig(fileUrl(run, d.cut), boxes) : '<figure class="fx-fig"><img src="/api/video/' + encodeURIComponent(run) + '/crop/' + d.index + '" alt=""></figure>';
          return '<section class="fx-card fx-in" style="--i:' + i + '" data-dev="' + d.index + '">' + pic + '<div class="fx-body"><div class="fx-head"><div><span class="fx-eye">' + esc(d.unit) + ' · D' + d.index + '</span><h2>' + esc(d.name) + '</h2></div>'
            + pill(d.problems.length ? d.problems.length + (d.problems.length === 1 ? ' problem' : ' problems') : 'No problem', d.problems.length ? 'bad' : 'ok') + '</div>'
            + '<div class="fx-pills">' + pill(d.kind || 'Not sure') + (d.ports.length ? pill(d.counts.connected + ' cabled', 'on') + pill(d.counts.empty + ' empty', 'off') + (d.counts.unknown ? pill(d.counts.unknown + ' not sure', 'na') : '') : pill('No port read', 'na')) + '</div>'
            + (d.problems.length ? '<ul class="fx-probs">' + d.problems.map(function (p) { return '<li><b>' + esc(p.what) + (p.port ? ', port ' + p.port : '') + '</b> ' + esc(p.words) + '</li>'; }).join('') + '</ul>' : '')
            + (d.ports.length ? cells(d.ports) : '') + '</div></section>';
        }).join('') + '</div>' : empty('No device was read on this rack', 'Scan the rack again with a slower pass.')));
      fitFigs(root());
      root().onclick = function (e) {
        var c = e.target.closest && e.target.closest('.ft-cell'); if (!c) return; var card = c.closest('.fx-card'), n = c.textContent.trim();
        [].forEach.call(card.querySelectorAll('.fx-box.hl'), function (b) { b.classList.remove('hl'); });
        var b = card.querySelector('.fx-box[data-n="' + n + '"]'); if (b) { void b.getBBox; b.classList.add('hl'); }
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
      var sel = function (o) { return runs.map(function (h) { return '<option value="' + esc(h.id) + '"' + (h.id === run ? ' selected' : '') + '>' + esc((h.rack_id || h.title) + ' (' + h.id + ')') + '</option>'; }).join(''); };
      root().innerHTML = shell('Cables', run, '<div class="fx-two"><div class="fx-left"><div id="cb-pic">' + fig(fileUrl(run, 'rack.png'), boxes(null), 'fx-rack') + '</div></div><div class="fx-right fx-in">'
        + '<div class="fx-pills">' + pill(r.rows ? r.ends_checked + ' ends checked' : 'No matrix cable', 'na') + (r.rows ? pill(r.rows.length + ' to look at', r.rows.length ? 'bad' : 'ok') : '') + (r.ends_not_read ? pill(r.ends_not_read + ' on unread ports', 'na') : '') + '</div>'
        + '<p class="muted">' + esc(r.words) + '</p>'
        + (r.rows.length ? '<ul class="fx-list" id="cb-rows">' + r.rows.map(function (w) { return '<li><button type="button" class="fx-row" data-dev="' + w.index + '">' + pill(w.what, 'bad') + '<span><b>' + esc(w.unit) + ' · D' + w.index + ', port ' + esc(w.port) + '</b><small>' + esc(w.cable || '') + '</small></span></button></li>'; }).join('') + '</ul><p class="fx-note">Pick a cable to see its device on the picture.</p>'
          : empty('The cables agree with the matrix', 'Nothing to fix here.'))
        + '</div></div>'
        + '<form class="fx-bar" id="az-form"><label><span>A-end rack</span><select id="az-a">' + sel() + '</select></label><label><span>Z-end rack</span><select id="az-z">' + sel() + '</select></label><button class="btn primary" type="submit">Check the cable labels</button></form><div id="az-out"></div>');
      fitFigs(root());
      if ($('cb-rows')) $('cb-rows').onclick = function (e) { var b = e.target.closest('.fx-row'); if (!b) return; [].forEach.call($('cb-rows').querySelectorAll('.fx-row.on'), function (z) { z.classList.remove('on'); }); b.classList.add('on');
        $('cb-pic').innerHTML = fig(fileUrl(run, 'rack.png'), boxes(parseInt(b.dataset.dev, 10)), 'fx-rack'); fitFigs($('cb-pic')); };
      $('az-form').addEventListener('submit', function (e) {
        e.preventDefault(); $('az-out').innerHTML = loading('Checking the labels...');
        post('/api/cables/az', { a: $('az-a').value, z: $('az-z').value }).then(function (c) {
          var k = c.counts;
          $('az-out').innerHTML = '<div class="fx-pills fx-in">' + pill(k.matched + ' matched', 'ok') + pill(k.wrongPort + ' wrong port', k.wrongPort ? 'bad' : 'na') + pill(k.missingCable + ' missing', k.missingCable ? 'bad' : 'na') + pill(k.notFound + ' not found', 'na') + pill(k.notInSheet + ' not in the sheet', 'na')
            + pill(c.accuracy.labelsRead + ' of ' + c.accuracy.labelsExpected + ' labels read') + '<a class="btn" id="az-csv" href="/api/cables/az/' + esc(c.id) + '.csv" download>Download the CSV</a></div>'
            + table(c.cables, [['Cable', function (x) { return x.cableLabel; }], ['Status', function (x) { return AZ[x.status] || x.status; }],
              ['A-end', function (x) { return x.a && x.a.expected ? [x.a.expected.device, x.a.expected.port ? 'port ' + x.a.expected.port : ''].filter(Boolean).join(' ') + ': ' + (AZ[x.a.status] || 'not in these racks') : x.a && x.a.found ? 'read on ' + x.a.found.device : ''; }],
              ['Z-end', function (x) { return x.z && x.z.expected ? [x.z.expected.device, x.z.expected.port ? 'port ' + x.z.expected.port : ''].filter(Boolean).join(' ') + ': ' + (AZ[x.z.status] || 'not in these racks') : ''; }],
              ['Why', function (x) { return x.aExplained || x.zExplained || ''; }]]);
        }).catch(function (e2) { $('az-out').innerHTML = '<p class="fx-err">' + esc(e2.message) + '. Check both racks are scanned and try again.</p>'; });
      });
    }).catch(fail);
  }

  // ---- Worklist: each doubtful reading with its crop; confirm or correct on the crop
  function worklist(run) {
    api('/api/video/' + encodeURIComponent(run) + '/worklist').then(function (r) {
      root().innerHTML = shell('Worklist', run, (r.items.length ? '<p class="fx-key"><span id="wl-left">' + pill(r.items.length + ' to look at', 'bad') + '</span>' + pill('Least sure first') + '</p><div class="fx-grid fx-wl">' + r.items.map(function (it, i) {
        var pic = it.kind === 'port' && it.cut && it.box ? fig(fileUrl(run, it.cut), [{ box: it.box, n: it.port, cls: 'hl' }]) : '<figure class="fx-fig"><img src="/api/video/' + encodeURIComponent(run) + '/crop/' + it.index + '" alt=""></figure>';
        var acts = it.kind === 'device'
          ? '<button class="btn primary wl-go" type="button" data-id="' + it.id + '" data-a="' + esc(it.read) + '">Confirm ' + esc(it.read) + '</button><button class="btn wl-fix" type="button">Correct it</button><select class="wl-kind" hidden>' + r.kinds.map(function (k) { return '<option' + (k === it.read ? ' selected' : '') + '>' + esc(k) + '</option>'; }).join('') + '</select><button class="btn primary wl-go" type="button" data-id="' + it.id + '" hidden>Save</button>'
          : '<button class="btn primary wl-go" type="button" data-id="' + it.id + '" data-a="connected">Cable plugged in</button><button class="btn wl-go" type="button" data-id="' + it.id + '" data-a="empty">Empty</button>';
        return '<section class="fx-card fx-in ft-wl" style="--i:' + Math.min(i, 8) + '" data-item="' + it.id + '">' + pic + '<div class="fx-body"><span class="fx-eye">' + esc(it.unit) + ' · D' + it.index + (it.port ? ' · port ' + it.port : '') + '</span><b>' + esc(it.question) + '</b>'
          + '<p class="muted small">' + esc(it.why) + '</p><div class="ft-btns fx-acts">' + acts + '</div></div></section>';
      }).join('') + '</div>' : empty('Nothing left to confirm', 'Every reading on this rack is settled.')));
      fitFigs(root());
      root().onclick = function (e) {
        var fix = e.target.closest('.wl-fix'); if (fix) { var cd = fix.closest('.ft-wl'); [].forEach.call(cd.querySelectorAll('.wl-go, .wl-fix'), function (z) { z.hidden = !z.hidden; }); cd.querySelector('.wl-kind').hidden = false; return; }
        var b = e.target.closest('.wl-go'); if (!b) return;
        var card = b.closest('.ft-wl'), ans = b.dataset.a || card.querySelector('.wl-kind').value; b.disabled = true;
        post('/api/video/' + encodeURIComponent(run) + '/worklist/' + b.dataset.id, { answer: ans }).then(function () { card.classList.add('ft-done'); card.querySelector('.ft-btns').innerHTML = pill('Saved: ' + (STATE[ans] || ans), 'ok'); delete VIEW[run];
          var left = root().querySelectorAll('.ft-wl:not(.ft-done)').length; if ($('wl-left')) $('wl-left').innerHTML = left ? pill(left + ' to look at', 'bad') : pill('All done', 'ok'); })
          .catch(function (e2) { b.disabled = false; card.querySelector('.fx-body').insertAdjacentHTML('beforeend', '<p class="fx-err">' + esc(e2.message) + '. Try again.</p>'); });
      };
    }).catch(fail);
  }

  var againTimer = null;
  function again(run) {
    clearInterval(againTimer);
    var draw = function (s) {
      var st = s.state;
      root().innerHTML = head('Read again', 'Every device of this run is read again with the models as they are today: its make, model and ports. What people corrected (kinds, makes and models, port states) and what close-ups read stay as they are.', run)
        + '<div class="card ft-again"><p>' + (st === 'running' ? 'Reading device ' + Math.min((s.done || 0) + 1, s.of || 1) + ' of ' + (s.of || '?') + '. One device takes about a minute; you can leave this page.'
          : st === 'done' ? 'Read again on ' + esc(String(s.finished || '').replace('T', ' ')) + ': ' + s.of + ' devices.' : 'Not read again yet.') + '</p>'
        + (st === 'running' ? '<div class="ft-bar"><i style="width:' + Math.round(100 * (s.done || 0) / (s.of || 1)) + '%"></i></div>' : '<button class="btn primary" id="ag-go" type="button">Read again</button>') + '</div>';
      var b = $('ag-go'); if (b) b.addEventListener('click', function () { b.disabled = true; post('/api/video/' + encodeURIComponent(run) + '/read-again', {}).then(function () { delete VIEW[run]; again(run); }).catch(fail); });
      if (st === 'running') againTimer = setTimeout(function () { if ((location.hash || '').indexOf('/again') > 0) again(run); }, 4000);
    };
    api('/api/video/' + encodeURIComponent(run) + '/read-again').then(draw).catch(fail);
  }

  function physical(run) {
    api('/api/video/' + encodeURIComponent(run) + '/physical-layer').then(function (d) {
      var c = d.counts, devs = [];
      d.units.forEach(function (u) { u.devices.forEach(function (x) { devs.push(Object.assign({ unit: u.unit }, x)); }); });
      root().innerHTML = head('Physical layer', 'The physical layer document of this rack, in the RT-EOS shape (schema ' + d.schema + '): every device with its label, make, model, serial and asset tag, every port with its state and the cable on it. Nothing is invented.', run)
        + '<div class="card"><p><b>' + esc(d.rack_id) + '</b>, rack label ' + esc(d.rack.label.text || 'not read') + '. ' + d.units_count + ' units, ' + d.devices_count + ' devices. ' + c.ports + ' ports: ' + c.connected + ' cabled, ' + c.empty + ' empty, ' + c.unknown + ' unknown. ' + d.cables.length + ' cables with a colour or connector read.</p>'
        + '<div class="ft-btns"><a class="btn primary" href="/api/video/' + esc(run) + '/physical-layer.html" target="_blank" rel="noopener">Open the page to print</a><a class="btn" href="/api/video/' + esc(run) + '/physical-layer" download="physical-layer-' + esc(run) + '.json">Download the JSON</a></div></div>'
        + table(devs, [['Units', function (x) { return x.unit.toUpperCase().replace(/,/g, ', '); }], ['Device', function (x) { return x.device; }], ['Record', function (x) { return x.record_name; }], ['Kind', function (x) { return x.device_type; }],
          ['Make and model', function (x) { return [x.ocr.make, x.ocr.model].filter(Boolean).join(' '); }], ['Serial', function (x) { return x.serial; }], ['Asset tag', function (x) { return x.asset_tag; }],
          ['Ports', function (x) { var k = x.ports.filter(function (p) { return p.status === 'connected'; }).length; return x.port_count + ' (' + k + ' cabled)'; }]]);
    }).catch(fail);
  }

  function sfp(run) {
    api('/api/video/' + encodeURIComponent(run) + '/sfp-advice').then(function (r) {
      root().innerHTML = head('SFP advice', 'For each switch, router, firewall or gateway with fibre or SFP ports: which optic and which cable fit them, from the port type the video read, the model read on the device and the cable types the Excel matrix lists for it.', run)
        + (r.items.length ? r.items.map(function (x) {
          return '<div class="card ft-sfp"><span class="eyebrow">' + esc(x.unit) + ' · D' + x.index + (x.model ? ' · ' + esc(x.model) : '') + '</span><h2>' + esc(x.device) + ': ' + x.count + ' ' + esc(x.port_type) + (x.count === 1 ? ' port' : ' ports') + '</h2>'
            + '<div class="ft-kv"><span>Optic</span><b>' + esc(x.suggestion.optic) + '</b></div><div class="ft-kv"><span>Cable</span><b>' + esc(x.suggestion.cable) + '</b></div>' + (x.suggestion.note ? '<p class="muted small">' + esc(x.suggestion.note) + '</p>' : '') + '</div>';
        }).join('') : '<div class="card"><p>No fibre or SFP port was read on a switch, router, firewall or gateway of this rack.</p></div>');
    }).catch(fail);
  }

  function topology(run) {
    api('/api/video/' + encodeURIComponent(run) + '/topology').then(function (detail) {
      root().innerHTML = head('Topology', 'RT-EOS\'s topology of this rack: the devices read on the video and the cables the Excel matrix puts on their ports' + (detail.matrix ? ' (' + detail.cables_in_matrix + ' in the matrix)' : '; no matrix is copied in yet, so only the devices show') + '. Tap a device or a cable.', run)
        + '<div class="rteos ft-topo" id="ft-topo"></div>';
      var mount = function () { if (!window.RTEOSTopology) return setTimeout(mount, 150); window.RTEOSTopology.mount($('ft-topo'), detail, function (o) { if (o && o.deviceId != null) location.hash = '#result/' + run + '/devices'; }); };
      mount();
    }).catch(fail);
  }

  // ------------------------------------------------------------ Profile > Site position (the sign-in service's PUT /api/setup/:site/datacentre)
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

  var RUNNERS = { find: find, connections: connections, cables: cables, worklist: worklist, again: again, physical: physical, sfp: sfp, topology: topology };
  var SHOWN = null;                                   // the tool on screen: the app routes again now and then (session checks), which must not reload it
  function open(run, tool, q) {
    var key = [run, tool, q].join('/'); if (key === SHOWN && root().children.length) return; SHOWN = key;
    clearInterval(againTimer); root().onclick = null;
    if (run === 'site') return site();
    if (!run) return;
    if (!RUNNERS[tool]) {
      root().innerHTML = head('Rack tools', 'Everything else RackTrack does with this rack.', run) + toolList(run); return;
    }
    root().innerHTML = '<div class="fx-page">' + loading('Loading ' + NAME[tool].toLowerCase() + '...') + '</div>';
    RUNNERS[tool](run, q);
  }
  function onHash() {
    var h = (location.hash || '').slice(1).split('/');
    if (h[0] !== 'tools' || [h[1], h[2], h[3]].join('/') !== SHOWN) SHOWN = null;
    if (h[0] === 'result' && h[1]) { delete VIEW[h[1]]; fillResult(h[1]); }
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
    }).catch(function () {});
  }
  window.RTFEAT = { open: open, idsBlock: idsBlock, fillResult: fillResult, sfpInto: sfpInto };
})();
