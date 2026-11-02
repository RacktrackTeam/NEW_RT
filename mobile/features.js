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
  function fail(e) { root().insertAdjacentHTML('beforeend', '<p class="err">' + esc(e.message) + '</p>'); }

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

  function find(run, q) {
    view(run).then(function (v) {
      var devs = (v.devices || []).filter(function (d) { return (d.layout || {}).ports && d.layout.ports.length; }).sort(function (a, b) { return parseInt(String(b.top).replace(/\D/g, ''), 10) - parseInt(String(a.top).replace(/\D/g, ''), 10); });
      var want = (q || '').split('-'), wd = parseInt(want[0], 10), wp = parseInt(want[1], 10);
      root().innerHTML = head('Find a port', 'Pick a device and a port number. The port is marked on the rack picture with what the video read on it and the cable and label the Excel matrix puts there.', run)
        + '<form class="card ft-form" id="fp-form"><label class="ft-field"><span>Device</span><select id="fp-dev">' + devs.map(function (d) { return '<option value="' + d.index + '"' + (d.index === wd ? ' selected' : '') + '>' + esc(devLabel(d)) + '</option>'; }).join('') + '</select></label>'
        + '<label class="ft-field"><span>Port number</span><input id="fp-port" type="number" min="1" max="999" value="' + (wp || 1) + '" inputmode="numeric"></label><button class="btn primary" type="submit">Find</button></form><div id="fp-out"></div>';
      $('fp-form').addEventListener('submit', function (e) { e.preventDefault(); location.hash = '#tools/' + run + '/find/' + $('fp-dev').value + '-' + $('fp-port').value; });
      if (wd && wp) api('/api/video/' + encodeURIComponent(run) + '/find-port?device=' + wd + '&port=' + wp).then(function (r) {
        var p = r.read || {}, c = r.cable;
        $('fp-out').innerHTML = '<div class="card ft-found"><span class="eyebrow">' + esc(r.device) + ' · ' + esc(r.unit) + ' · port ' + r.port + '</span>'
          + (r.found ? '<h2>' + esc(stateWord(p.state)) + '</h2>' : '<h2>Port ' + r.port + ' was not read on this device</h2><p class="muted">Ports read: ' + esc((r.ports || []).join(', ') || 'none') + '.</p>')
          + '<figure class="ft-pic"><img src="' + esc(r.picture) + '" alt="Port ' + r.port + ' of ' + esc(r.device) + ' on the rack picture"><figcaption class="muted small">The device outlined in blue' + (r.found && p.rack_polygon ? ', the port ringed in red.' : '; this port has no place on the picture.') + '</figcaption></figure>'
          + '<div class="ft-kvs">' + [['Device', r.device + ' (D' + r.index + ', ' + (r.kind || 'not sure') + ')'], ['Port type', p.type ? String(p.type).toUpperCase() : null], ['Cable colour read', p.colour], ['Connector read', p.connector],
            ['Cable in the matrix', c ? c.cable : 'none'], ['Label expected', c ? c.label : null], ['Other end', c ? [c.other.device, c.other.port ? 'port ' + c.other.port : '', c.other.rack].filter(Boolean).join(', ') : null], ['Cable type', c ? c.type : null]]
            .map(function (x) { return '<div class="ft-kv"><span>' + x[0] + '</span><b>' + (x[1] ? esc(x[1]) : '<em class="muted">not known</em>') + '</b></div>'; }).join('') + '</div></div>';
      }).catch(function (e) { $('fp-out').innerHTML = '<p class="err">' + esc(e.message) + '</p>'; });
    }).catch(fail);
  }

  function cells(ports) {
    return '<div class="ft-cells">' + ports.filter(function (p) { return p.n != null; }).map(function (p) {
      var s = p.state === 'connected' ? 'on' : p.state === 'empty' ? 'off' : 'na';
      return '<span class="ft-cell ' + s + '" title="Port ' + p.n + ': ' + esc(stateWord(p.state)) + '">' + p.n + '</span>';
    }).join('') + '</div>';
  }
  function connections(run) {
    api('/api/video/' + encodeURIComponent(run) + '/connections').then(function (r) {
      root().innerHTML = head('Connections', 'Each device with what is wrong on it (the differences and the cable check against ' + (r.rack_key ? 'the Excel copy of ' + r.rack_key : 'the Excel copy') + ') and a card of its ports.', run)
        + '<p class="ft-legend"><span class="ft-cell on">1</span> cable plugged in <span class="ft-cell off">2</span> empty <span class="ft-cell na">3</span> not sure</p>'
        + r.devices.map(function (d) {
          return '<div class="card ft-dev"><div class="ft-devh"><div><span class="eyebrow">' + esc(d.unit) + ' · D' + d.index + '</span><h2>' + esc(d.name) + '</h2><p class="muted small">' + esc(d.kind || 'Not sure')
            + (d.serial ? ' · serial ' + esc(d.serial) : '') + (d.asset_tag ? ' · asset tag ' + esc(d.asset_tag) : '') + '</p></div><span class="chip ' + (d.problems.length ? 'fail' : 'done') + '">' + (d.problems.length ? d.problems.length + (d.problems.length === 1 ? ' problem' : ' problems') : 'No problem') + '</span></div>'
            + (d.problems.length ? '<ul class="ft-probs">' + d.problems.map(function (p) { return '<li><b>' + esc(p.what) + (p.port ? ', port ' + p.port : '') + '</b> ' + esc(p.words) + '</li>'; }).join('') + '</ul>' : '')
            + (d.ports.length ? '<p class="muted small">' + d.ports.length + ' ports: ' + d.counts.connected + ' with a cable, ' + d.counts.empty + ' empty, ' + d.counts.unknown + ' not sure</p>' + cells(d.ports) : '<p class="muted small">No port read on this device.</p>') + '</div>';
        }).join('');
    }).catch(fail);
  }

  function table(rows, cols) {
    return '<div class="ft-wrap"><table class="ft-table"><thead><tr>' + cols.map(function (c) { return '<th>' + esc(c[0]) + '</th>'; }).join('') + '</tr></thead><tbody>'
      + rows.map(function (r) { return '<tr>' + cols.map(function (c) { var v = c[1](r); return '<td data-label="' + esc(c[0]) + '">' + (v == null || v === '' ? '<span class="muted">-</span>' : esc(v)) + '</td>'; }).join('') + '</tr>'; }).join('') + '</tbody></table></div>';
  }
  var AZ = { matched: 'Matched', wrong_port: 'Wrong port', missing_cable: 'Missing cable', not_found: 'Label not found', not_in_sheet: 'Not in the sheet' };
  function cables(run) {
    Promise.all([api('/api/video/' + encodeURIComponent(run) + '/cables'), api('/api/video/history')]).then(function (x) {
      var r = x[0], runs = x[1].filter(function (h) { return h.status === 'done'; });
      root().innerHTML = head('Cables', 'Every cable the Excel matrix puts in this rack, against the ports the video read: a missing cable, an extra cable, a cable in the wrong port.', run)
        + '<div class="card"><p>' + esc(r.words) + ' ' + (r.rows ? r.ends_checked + ' cable ends checked, ' + r.ends_not_read + ' on ports the video could not read.' : '') + '</p></div>'
        + (r.rows.length ? table(r.rows, [['What', function (x) { return x.what; }], ['Device', function (x) { return x.device; }], ['Port', function (x) { return x.port; }], ['Cable', function (x) { return x.cable; }], ['Record', function (x) { return x.record; }], ['Video', function (x) { return x.read; }], ['In words', function (x) { return x.words; }]])
          : '<p class="muted">No difference on the cables.</p>')
        + '<h2 class="ft-h2">A-end / Z-end check</h2><p class="muted">Every cable label read in two racks, looked up in the matrix, and each end placed against the device and port the sheet says. The same run may be both ends.</p>'
        + '<form class="card ft-form" id="az-form"><label class="ft-field"><span>A-end run</span><select id="az-a">' + runs.map(function (h) { return '<option value="' + esc(h.id) + '"' + (h.id === run ? ' selected' : '') + '>' + esc((h.rack_id || h.title) + ' (' + h.id + ')') + '</option>'; }).join('') + '</select></label>'
        + '<label class="ft-field"><span>Z-end run</span><select id="az-z">' + runs.map(function (h) { return '<option value="' + esc(h.id) + '"' + (h.id === run ? ' selected' : '') + '>' + esc((h.rack_id || h.title) + ' (' + h.id + ')') + '</option>'; }).join('') + '</select></label>'
        + '<button class="btn primary" type="submit">Check the labels</button></form><div id="az-out"></div>';
      $('az-form').addEventListener('submit', function (e) {
        e.preventDefault(); $('az-out').innerHTML = '<p class="muted">Checking...</p>';
        post('/api/cables/az', { a: $('az-a').value, z: $('az-z').value }).then(function (c) {
          var k = c.counts;
          $('az-out').innerHTML = '<div class="card"><p><b>' + k.matched + '</b> matched, <b>' + k.wrongPort + '</b> wrong port, <b>' + k.missingCable + '</b> missing cable, <b>' + k.notFound + '</b> label not found, <b>' + k.notInSheet + '</b> not in the sheet. '
            + c.accuracy.labelsRead + ' of ' + c.accuracy.labelsExpected + ' end labels read.</p><a class="btn" id="az-csv" href="/api/cables/az/' + esc(c.id) + '.csv" download>Download the CSV</a></div>'
            + table(c.cables, [['Cable', function (x) { return x.cableLabel; }], ['Status', function (x) { return AZ[x.status] || x.status; }],
              ['A-end', function (x) { return x.a && x.a.expected ? [x.a.expected.device, x.a.expected.port ? 'port ' + x.a.expected.port : ''].filter(Boolean).join(' ') + ': ' + (AZ[x.a.status] || 'not in these racks') : x.a && x.a.found ? 'read on ' + x.a.found.device : ''; }],
              ['Z-end', function (x) { return x.z && x.z.expected ? [x.z.expected.device, x.z.expected.port ? 'port ' + x.z.expected.port : ''].filter(Boolean).join(' ') + ': ' + (AZ[x.z.status] || 'not in these racks') : ''; }],
              ['Why', function (x) { return x.aExplained || x.zExplained || ''; }]]);
        }).catch(function (e2) { $('az-out').innerHTML = '<p class="err">' + esc(e2.message) + '</p>'; });
      });
    }).catch(fail);
  }

  function worklist(run) {
    api('/api/video/' + encodeURIComponent(run) + '/worklist').then(function (r) {
      root().innerHTML = head('Worklist', 'The readings the video is least sure of, least sure first: a device whose kind was read below 65 %, and up to 8 ports per device whose state it could not tell. Confirm or correct each one.', run)
        + (r.items.length ? '<p class="muted">' + r.items.length + ' to look at.</p>' + r.items.map(function (it) {
          var btns = it.kind === 'device'
            ? '<select class="wl-kind">' + r.kinds.map(function (k) { return '<option' + (k === it.read ? ' selected' : '') + '>' + esc(k) + '</option>'; }).join('') + '</select><button class="btn primary wl-go" type="button" data-id="' + it.id + '">Save</button>'
            : '<button class="btn wl-go" type="button" data-id="' + it.id + '" data-a="connected">Cable plugged in</button><button class="btn wl-go" type="button" data-id="' + it.id + '" data-a="empty">Empty</button>';
          return '<div class="card ft-wl" data-item="' + it.id + '"><span class="eyebrow">' + esc(it.unit) + ' · D' + it.index + (it.port ? ' · port ' + it.port : '') + '</span><b>' + esc(it.question) + '</b><p class="muted small">' + esc(it.why) + '</p>'
            + (it.kind === 'port' ? '<a class="link small" href="#tools/' + esc(run) + '/find/' + it.index + '-' + it.port + '">Show it on the rack picture</a>' : '') + '<div class="ft-btns">' + btns + '</div></div>';
        }).join('') : '<div class="card"><p>Nothing left to confirm on this run.</p></div>');
      root().onclick = function (e) {
        var b = e.target.closest('.wl-go'); if (!b) return;
        var card = b.closest('.ft-wl'), ans = b.dataset.a || card.querySelector('.wl-kind').value; b.disabled = true;
        post('/api/video/' + encodeURIComponent(run) + '/worklist/' + b.dataset.id, { answer: ans }).then(function () { card.classList.add('ft-done'); card.querySelector('.ft-btns').innerHTML = '<span class="chip done">Saved: ' + esc(STATE[ans] || ans) + '</span>'; delete VIEW[run]; })
          .catch(function (e2) { b.disabled = false; card.insertAdjacentHTML('beforeend', '<p class="err">' + esc(e2.message) + '</p>'); });
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
    root().innerHTML = '<p class="muted">Loading ' + esc(NAME[tool]) + '...</p>';
    RUNNERS[tool](run, q);
  }
  function onHash() {
    var h = (location.hash || '').slice(1).split('/');
    if (h[0] !== 'tools' || [h[1], h[2], h[3]].join('/') !== SHOWN) SHOWN = null;
    if (h[0] === 'result' && h[1]) { delete VIEW[h[1]]; fillResult(h[1]); }
  }
  window.addEventListener('hashchange', onHash); onHash();
  window.RTFEAT = { open: open, idsBlock: idsBlock, fillResult: fillResult };
})();
