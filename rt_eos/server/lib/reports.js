/**
 * Reports a person downloads, made with pdfkit (pure JavaScript, no
 * browser), and the CSV beside them.
 *
 *   scanPdf(view, sheet, ctx)    the rack, on as many pages as it takes: the
 *                                photo with its readings and key numbers,
 *                                every unit top to bottom with its device,
 *                                every port with its cable and other end,
 *                                then the flags (ctx.doc is the Excel dataset)
 *   scanCsv(view, sheet, doc)    one row per port
 *   checkPdf(check, ctx)         an A-end / Z-end label check for head office,
 *                                one page: each list is cut to what fits and
 *                                says how many more there are
 */
'use strict';

const fs = require('fs');
const PDFDocument = require('pdfkit');
const store = require('./store');
const scanDetail = require('./scan_detail');
const { csvCell } = require('./checks');
const sheetCheck = require('./sheet_check');
const sheetModel = require('./sheet_model');

const INK = '#111111';
const GREY = '#666666';
const RULE = '#dddddd';
const TONE = { bad: '#b42318', warn: '#b54708', ok: '#067647' };
const M = 32;

function when(iso) {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return '';
  return d.toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) + ' UTC';
}

function pictureOf(scanId) {
  for (const name of ['annotated', 'photo']) {
    try {
      const p = store.file(scanId, name);
      if (fs.existsSync(p)) return p;
    } catch { /* no scan */ }
  }
  return null;
}

function toBuffer(doc) {
  return new Promise((resolve, reject) => {
    const parts = [];
    doc.on('data', (c) => parts.push(c));
    doc.on('end', () => resolve(Buffer.concat(parts)));
    doc.on('error', reject);
    doc.end();
  });
}

function newDoc(title) {
  return new PDFDocument({ size: 'A4', margin: M, autoFirstPage: true, info: { Title: title, Producer: 'RackTrack', Creator: 'RackTrack' } });
}

// One line of text that never wraps onto a new page.
function text(doc, s, x, y, { size = 9, bold = false, color = INK, width = 250, align = 'left' } = {}) {
  doc.font(bold ? 'Helvetica-Bold' : 'Helvetica').fontSize(size).fillColor(color)
    .text(String(s ?? ''), x, y, { width, align, lineBreak: false, ellipsis: true });
  return y + size * 1.35;
}

// A sentence that may wrap; returns the y under it.
function para(doc, s, x, y, { size = 9, color = INK, width = 250 } = {}) {
  doc.font('Helvetica').fontSize(size).fillColor(color);
  const h = doc.heightOfString(String(s ?? ''), { width });
  doc.text(String(s ?? ''), x, y, { width });
  return y + h + 2;
}

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function rule(doc, y, x1 = M, x2 = doc.page.width - M) {
  doc.moveTo(x1, y).lineTo(x2, y).lineWidth(0.5).strokeColor(RULE).stroke();
}

function header(doc, title, lines) {
  let y = M;
  y = text(doc, title, M, y, { size: 16, bold: true, width: doc.page.width - 2 * M });
  y += 2;
  for (const l of lines.filter(Boolean)) y = para(doc, l, M, y, { size: 9, color: GREY, width: doc.page.width - 2 * M }) - 1;
  y += 4;
  rule(doc, y);
  return y + 8;
}

function footer(doc, note) {
  const y = doc.page.height - M - 10;
  rule(doc, y - 4);
  text(doc, note, M, y, { size: 7.5, color: GREY, width: doc.page.width - 2 * M });
}

function image(doc, file, x, y, w, h) {
  if (!file) {
    text(doc, 'No photo for this scan.', x, y, { color: GREY, width: w });
    return y + 14;
  }
  try {
    const img = doc.openImage(file);
    const k = Math.min(w / img.width, h / img.height);
    const iw = img.width * k;
    const ih = img.height * k;
    const ix = x + (w - iw) / 2;
    doc.image(img, ix, y, { width: iw, height: ih });
    doc.rect(ix, y, iw, ih).lineWidth(0.5).strokeColor(RULE).stroke();
    return y + ih;
  } catch {
    text(doc, 'The photo could not be placed.', x, y, { color: GREY, width: w });
  }
  return y + h;
}

// ── The rack ────────────────────────────────────────────────────────────
// Everything about one rack, in this order: the photo with its readings and
// the key numbers; every unit from the top down with the device in it (or
// Empty); for each device every port, Empty or Connected, the cable on it
// (kind, colour, label read) and the other end; then the flags. As many
// pages as it takes; nothing is cut.

const FLAG_WORD = { missing_cable: 'Missing cable', wrong_port: 'Wrong port', not_in_sheet: 'Not in sheet', not_found: 'Not found', uncapped: 'Uncapped', optic_no_cap: 'Optic fitted, cap off' };
const FLAG_TONE = { missing_cable: TONE.bad, wrong_port: TONE.bad, not_in_sheet: TONE.warn, not_found: TONE.warn, uncapped: TONE.warn, optic_no_cap: TONE.warn };

const stateWord = (s) => (s === 'connected' ? 'Connected' : s === 'empty' ? 'Empty' : 'Could not tell');
const unitsText = (d) => (d.unitTop == null ? '' : d.unitBottom != null && d.unitBottom !== d.unitTop ? `U${d.unitBottom}-${d.unitTop}` : `U${d.unitTop}`);
const endText = (e) => (!e ? '' : [[e.device, e.port != null && e.port !== '' ? `port ${e.port}` : null].filter(Boolean).join(' '), e.rack].filter(Boolean).join(', '));

/** "LC, Aqua, label FC-001" - what is known of the cable on a port. */
function cableWords(p) {
  if (p.state !== 'connected' && !p.label) return '';
  const corrected = '';
  return [p.cableConnector, p.cableColor, p.label ? `label ${p.label.text}${corrected}` : null].filter(Boolean).join(', ') || 'nothing read on it';
}

/** Where a port's cable goes, from the Excel dataset (or the label read). */
function otherEndWords(p) {
  if (p.expected?.other) return endText(p.expected.other);
  if (p.labelBelongsTo) return `${endText(p.labelBelongsTo.a)} to ${endText(p.labelBelongsTo.z)}`;
  return '';
}

/** The flags of the sheet check as rows [tone, what, detail]. */
function flagLines(sheet) {
  if (!sheet) return [];
  return [
    ...sheet.missing.map((m) => [TONE.bad, `Missing cable ${m.cableLabel || ''}`.trim(),
      `${m.deviceName} port ${m.portNo} is empty${m.otherEnd?.device ? `; the Excel dataset runs it to ${endText(m.otherEnd)}` : ''}`]),
    ...sheet.units.filter((u) => u.status === 'wrong_unit').map((u) => [TONE.bad, `${u.name} in U${u.unitFound}`, `the Excel dataset says U${u.unitExpected}`]),
    ...sheet.unexpected.map((m) => [TONE.warn, `${m.deviceName} port ${m.portNo}`, m.expectedBy === 'port' ? 'a cable where the Excel dataset expects the port empty' : 'a cable the Excel dataset does not have']),
    ...(sheet.wrongType || []).map((m) => [TONE.warn, `${m.deviceName} port ${m.portNo}`, `port type ${m.found}; the Excel dataset says ${m.expected}`]),
    ...sheet.uncapped.map((m) => [TONE.warn, `${m.deviceName} port ${m.portNo}`, `uncapped: empty port with the dust cap off, marked by ${m.markedBy || 'someone'}`]),
    ...(sheet.opticNoCap || []).map((m) => [TONE.warn, `${m.deviceName} port ${m.portNo}`, `optic fitted without a dust cap, marked by ${m.markedBy || 'someone'}`]),
  ];
}

async function scanPdf(view, sheet, { siteName, printedBy, printedAt = new Date().toISOString(), doc: matrixDoc = null } = {}) {
  const detail = scanDetail.build(view, matrixDoc);
  const doc = new PDFDocument({ size: 'A4', margin: M, autoFirstPage: true, bufferPages: true,
    info: { Title: `Rack report ${view.rackLabel || view.scanId}`, Producer: 'RackTrack', Creator: 'RackTrack' } });
  const W = doc.page.width;
  const inner = W - 2 * M;
  const bottom = doc.page.height - M - 22;
  let y = header(doc, `Rack report: ${view.rackLabel || 'rack not named yet'}`, [
    [siteName, `photo taken ${when(view.takenAt)}`, view.by ? `by ${view.by}` : null].filter(Boolean).join(', '),
    `Scan ${view.scanId}. Report made ${when(printedAt)}${printedBy ? ` by ${printedBy}` : ''}.`,
  ]);
  const need = (h) => {
    if (y + h <= bottom) return;
    doc.addPage();
    y = M;
  };
  const heading = (s) => {
    need(40);
    y += 6;
    y = text(doc, s, M, y, { size: 12, bold: true, width: inner }) + 2;
  };
  const table = (cols, rows, { size = 8 } = {}) => {
    const head = () => {
      cols.forEach(([dx, w, h]) => text(doc, h, M + dx, y, { size, bold: true, color: GREY, width: w - 4 }));
      y += size + 5;
      rule(doc, y - 2);
    };
    head();
    for (const r of rows) {
      if (y + size + 4 > bottom) { doc.addPage(); y = M; head(); }
      r.cells.forEach((v, i) => {
        const [dx, w] = cols[i];
        const c = r.colors?.[i] || INK;
        text(doc, v, M + dx, y, { size, width: w - 4, color: c, bold: !!r.bold?.[i] });
      });
      y += size + 4;
    }
  };

  // 1. The photo with its readings, and the key numbers beside it.
  const picW = 250;
  const picH = Math.min(470, bottom - y - 10);
  const top = y;
  const picBottom = image(doc, pictureOf(view.scanId), M, top, picW, picH);
  const x = M + picW + 18;
  const colW = W - M - x;
  const c = detail.counts;
  const f = detail.flags;
  let ry = text(doc, 'This rack', x, top, { size: 11, bold: true, width: colW }) + 4;
  const facts = [
    ['Units', `${c.units}${detail.unitsTotal ? ` (${detail.unitsTotal}U rack)` : ''}`],
    ['Devices', String(c.devices)],
    ['Ports', `${c.ports}: ${c.connected} connected, ${c.empty} empty${c.unknown ? `, ${c.unknown} could not tell` : ''}`],
    ['Cables', String(c.cables)],
    ['Excel dataset', detail.matrix ? `${detail.matrix.fileName || 'imported'}, ${plural(detail.matrix.cableCount, 'cable')}` : 'none imported, nothing checked'],
  ];
  if (detail.matrix) {
    facts.push(['Missing cables', String(f.missingCable)]);
    facts.push(['Other flags', String(f.wrongUnit + f.unexpectedCable + f.uncapped + (f.opticNoCap || 0))]);
  }
  for (const [k, v] of facts) {
    text(doc, k, x, ry, { size: 8.5, color: GREY, width: 80 });
    ry = para(doc, v, x + 82, ry, { size: 8.5, width: colW - 82, color: k === 'Missing cables' && f.missingCable ? TONE.bad : INK });
  }
  y = Math.max(picBottom, ry) + 10;

  // 2. Every unit, top to bottom.
  heading('Units, top to bottom');
  const byId = new Map(detail.devices.map((d) => [d.deviceId, d]));
  const uCols = [[0, 44, 'Unit'], [44, 170, 'Device'], [214, 90, 'Kind'], [304, 140, 'Make and model'], [444, inner - 444, 'Ports']];
  const uRows = [];
  const listed = new Set();
  for (const u of detail.units) {
    const d = u.deviceId != null ? byId.get(u.deviceId) : null;
    if (!d) {
      const word = u.kind === 'unidentified' ? 'Not identified' : u.kind === 'not_read' ? 'Not read' : 'Empty';
      uRows.push({ cells: [`U${u.unit}`, word, '', '', ''], colors: [INK, GREY] });
      continue;
    }
    const first = !listed.has(d.deviceId);
    listed.add(d.deviceId);
    uRows.push({ cells: [`U${u.unit}`, first ? d.name : `${d.name} (continued)`, first ? d.type : '',
      first ? [d.make, d.model].filter(Boolean).join(' ') : '', first && d.counts.ports ? `${d.counts.connected} of ${d.counts.ports} connected` : ''],
    colors: [INK, first ? INK : GREY] });
  }
  for (const d of detail.devices) {
    if (!listed.has(d.deviceId)) uRows.push({ cells: [unitsText(d) || '-', d.name, d.type, [d.make, d.model].filter(Boolean).join(' '), d.counts.ports ? `${d.counts.connected} of ${d.counts.ports} connected` : ''] });
  }
  if (uRows.length) table(uCols, uRows);
  else y = text(doc, 'No units were read on this rack.', M, y, { color: GREY, width: inner });

  // 3. Every port of every device, with the cable on it.
  heading('Ports and cables, by device');
  const withPorts = [...detail.devices].filter((d) => d.ports.length).sort((a, b) => (b.unitTop ?? -1) - (a.unitTop ?? -1));
  if (!withPorts.length) y = text(doc, 'No device on this rack showed ports in the photo.', M, y, { color: GREY, width: inner });
  const pCols = [[0, 30, 'Port'], [30, 50, 'Type'], [80, 62, 'State'], [142, 130, 'Cable (kind, colour, label)'], [272, 170, 'Other end'], [442, inner - 442, 'Flag']];
  for (const d of withPorts) {
    need(44);
    y = text(doc, `${[unitsText(d), d.name].filter(Boolean).join(' ')}${d.type && d.type !== d.name ? `, ${d.type}` : ''}, ${d.counts.connected} of ${d.counts.ports} connected`, M, y + 4, { size: 9.5, bold: true, width: inner }) + 2;
    table(pCols, d.ports.map((p) => {
      const flag = FLAG_WORD[p.status] || '';
      const other = otherEndWords(p);
      const cable = p.status === 'missing_cable' && p.expected ? `none; expected ${p.expected.cableLabel || 'a cable'}` : cableWords(p);
      return {
        cells: [String(p.portNo), p.type || '', stateWord(p.state), cable, other, flag],
        colors: [INK, INK, p.state === 'connected' ? INK : GREY, INK, INK, FLAG_TONE[p.status] || INK],
        bold: [false, false, false, false, false, !!flag],
      };
    }));
  }

  // 4. The flags.
  heading('Flags against the Excel dataset');
  if (!sheet || !sheet.matrixImportId) {
    y = text(doc, 'No Excel dataset is imported for this site, so nothing was checked against it.', M, y, { color: GREY, width: inner }) + 2;
  }
  {
    const rows = flagLines(sheet);
    if (!rows.length && sheet?.matrixImportId) y = text(doc, 'Nothing is flagged on this rack.', M, y, { color: GREY, width: inner });
    for (const [tone, a, b] of rows) {
      need(14);
      text(doc, a, M, y, { size: 8.5, color: tone, bold: true, width: 170 });
      y = para(doc, b, M + 175, y, { size: 8.5, width: inner - 175 }) + 1;
    }
  }

  // The same footer and a page number on every page.
  const range = doc.bufferedPageRange();
  for (let i = range.start; i < range.start + range.count; i += 1) {
    doc.switchToPage(i);
    doc.page.margins.bottom = 0;
    footer(doc, `RackTrack pilot, physical layer only. Every reading comes from a photograph; confirm on the rack before you act on it. Page ${i - range.start + 1} of ${range.count}.`);
  }
  return toBuffer(doc);
}

/** One row per port: the device it is on, what the photo shows and what the Excel dataset says. */
function scanCsv(view, sheet, doc) {
  const detail = scanDetail.build(view, doc);
  const units = new Map((sheet?.units || []).map((u) => [u.deviceId, u]));
  const head = ['Rack', 'Unit', 'Device', 'Kind', 'Make', 'Model', 'Port', 'Port type', 'State', 'State from', 'Cable kind', 'Cable colour', 'Label read',
    'Flag', 'Sheet cable', 'Sheet other end', 'Sheet unit check', 'Uncapped', 'Optic fitted without cap',
    'Sheet port type', 'Port type check', 'Sheet occupied', 'Sheet dust cap'];
  // What the manager's workbook expects on each port (lib/sheet_model.js), and the ports whose type it disagrees with.
  const devicesOfSheet = doc ? sheetCheck.sheetDevices(doc) : new Map();
  const wrongType = new Map((sheet?.wrongType || []).map((m) => [`${m.deviceId}:${m.portNo}`, m]));
  const rows = [head];
  for (const d of [...detail.devices].sort((a, b) => (b.unitTop ?? -1) - (a.unitTop ?? -1))) {
    const u = units.get(d.deviceId);
    const unitWord = !u || !sheet?.matrixImportId ? '' : u.status === 'wrong_unit' ? `wrong unit, sheet says U${u.unitExpected}` : u.status.replace(/_/g, ' ');
    const dev = devicesOfSheet.get(String(d.name || '').toUpperCase().replace(/[^A-Z0-9]/g, ''));
    const want = doc && d.name ? sheetModel.expectedPorts(doc, dev ? dev.name : d.name, view.rackLabel, dev ? dev.aliases : []) : new Map();
    for (const p of d.ports) {
      const o = p.expected?.other;
      const x = want.get(p.portNo) || {};
      const t = wrongType.get(`${d.deviceId}:${p.portNo}`);
      rows.push([view.rackLabel || '', d.unitTop ? `U${d.unitTop}` : '', d.name, d.type, d.make || '', d.model || '',
        p.portNo, p.type || '', stateWord(p.state), p.stateSource || '', p.cableConnector || '', p.cableColor || '', p.label ? p.label.text : '',
        FLAG_WORD[p.status] || '', p.expected ? p.expected.cableLabel || '' : '',
        o ? [o.rack, o.device, o.port].filter(Boolean).join(' ') : '',
        unitWord, p.uncapped ? `yes, ${p.uncapped.markedBy || ''}`.trim() : '', p.opticNoCap ? `yes, ${p.opticNoCap.markedBy || ''}`.trim() : '',
        x.portType || '', t ? `wrong, photo reads ${t.found}` : x.portType ? 'ok or not read' : '', x.occupied || '', x.dustCap || '']);
    }
  }
  return rows.map((r) => r.map(csvCell).join(',')).join('\r\n') + '\r\n';
}

// ── An A-end / Z-end check ─────────────────────────────────────────────
const STATUS_WORD = { matched: 'Matched', on_tag: 'Printed on the tag', wrong_port: 'Wrong port', not_in_sheet: 'Not in sheet', not_found: 'Not found', missing_cable: 'Missing cable' };
const STATUS_TONE = { matched: TONE.ok, on_tag: TONE.ok, wrong_port: TONE.bad, missing_cable: TONE.bad, not_found: TONE.warn, not_in_sheet: TONE.warn };

function endWords(end) {
  if (!end || (!end.expected && !end.found)) return '';
  const fixed = '';
  const f = end.found ? `${[end.found.device, end.found.port ? `port ${end.found.port}` : null].filter(Boolean).join(' ')}${fixed}` : 'not read';
  const e = end.expected ? [end.expected.device, end.expected.port ? `port ${end.expected.port}` : null].filter(Boolean).join(' ') : '';
  return e && end.status !== 'matched' ? `${f}; sheet: ${e}` : f;
}

async function checkPdf(check, { siteName, printedBy, printedAt = new Date().toISOString() } = {}) {
  const doc = newDoc(`Label check ${check.aRack || ''} to ${check.zRack || ''}`);
  const W = doc.page.width;
  const bottom = doc.page.height - M - 22;
  let y = header(doc, `Label check: ${check.aRack || 'A-end'} to ${check.zRack || 'Z-end'}`, [
    [siteName, `checked ${when(check.at)}`, check.by ? `by ${check.by}` : null].filter(Boolean).join(', '),
    `Every fibre label read in both racks, looked up in the patching matrix (import ${check.matrixImportId || 'none'}). Report made ${when(printedAt)}${printedBy ? ` by ${printedBy}` : ''}.`,
  ]);
  const half = (W - 2 * M - 12) / 2;
  const picH = 190;
  text(doc, `A-end, ${check.aRack || 'rack not named'}`, M, y, { size: 9, bold: true, width: half });
  text(doc, `Z-end, ${check.zRack || 'rack not named'}`, M + half + 12, y, { size: 9, bold: true, width: half });
  y += 13;
  image(doc, pictureOf(check.aScanId), M, y, half, picH);
  image(doc, pictureOf(check.zScanId), M + half + 12, y, half, picH);
  y += picH + 12;

  const c = check.counts || {};
  const tiles = [['Matched', c.matched, TONE.ok], ...(c.onTag ? [['Printed on the tag', c.onTag, TONE.ok]] : []),
    ['Wrong port', c.wrongPort, TONE.bad], ['Missing cable', c.missingCable, TONE.bad],
    ['Not found', c.notFound, TONE.warn], ['Not in sheet', c.notInSheet, TONE.warn]];
  const tw = (W - 2 * M) / tiles.length;
  tiles.forEach(([w, n, tone], i) => {
    text(doc, String(n ?? 0), M + i * tw, y, { size: 18, bold: true, color: tone, width: tw });
    text(doc, w, M + i * tw, y + 22, { size: 8.5, color: GREY, width: tw });
  });
  y += 38;
  const a = check.accuracy || {};
  const rate = a.readRate == null ? 'no label was expected in these racks' : `${(a.readRate * 100).toFixed(1)} per cent`;
  y = para(doc, `Labels read: ${a.labelsRead ?? 0} of ${a.labelsExpected ?? 0} cable ends the sheet puts in these racks (${rate}). `
    + `Devices in the unit the sheet says: ${a.unitPlacementOk ?? 0} of ${a.unitPlacementTotal ?? 0}. `

    + `Uncapped (empty, no dust cap): ${c.uncapped ?? 0}. Optic fitted without a dust cap: ${c.opticNoCap ?? 0}.`, M, y, { width: W - 2 * M });
  y += 6;
  rule(doc, y);
  y += 6;

  const problems = (check.cables || []).filter((x) => x.status !== 'matched');
  y = text(doc, `Problems, ${problems.length}`, M, y, { size: 11, bold: true, width: 300 });
  const cols = [[0, 80, 'Cable'], [80, 72, 'Status'], [152, (W - 2 * M - 152) / 2, 'A-end'], [152 + (W - 2 * M - 152) / 2, (W - 2 * M - 152) / 2, 'Z-end']];
  for (const [dx, w, h] of cols) text(doc, h, M + dx, y, { size: 8, bold: true, color: GREY, width: w });
  y += 12;
  let n = 0;
  for (const p of problems) {
    if (y > bottom - 12) break;
    text(doc, p.cableLabel, M, y, { size: 8, width: 78 });
    text(doc, STATUS_WORD[p.status] || p.status, M + 80, y, { size: 8, color: STATUS_TONE[p.status] || INK, width: 70 });
    text(doc, endWords(p.a), M + cols[2][0], y, { size: 8, width: cols[2][1] - 4 });
    text(doc, endWords(p.z), M + cols[3][0], y, { size: 8, width: cols[3][1] });
    y += 11.5;
    n += 1;
  }
  if (!(check.cables || []).length) text(doc, 'No cable of the sheet was placed in these two racks, and no label was read in them.', M, y, { color: GREY, width: W - 2 * M });
  else if (!problems.length) text(doc, 'Every cable found matched the sheet.', M, y, { color: GREY, width: 300 });
  else if (n < problems.length) text(doc, `and ${problems.length - n} more in the CSV`, M, y, { size: 8, color: GREY, width: 300 });
  footer(doc, 'RackTrack pilot, physical layer only. Labels are read from photographs; a label not read is reported as not found, never as matched.');
  return toBuffer(doc);
}

module.exports = { scanPdf, scanCsv, checkPdf };
