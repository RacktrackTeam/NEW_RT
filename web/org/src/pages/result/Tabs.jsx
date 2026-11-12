import { DevicePicker } from '../../components/DevicePicker.jsx';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Button, ButtonPair, TextButton } from '../../components/Button.jsx';
import { RackCamera } from '../../components/RackCamera.jsx';
import { useToast } from '../../components/Toast.jsx';
import { ActionRow, ActionRows } from '../../components/ActionRow.jsx';
import { KeyNumbers } from '../../components/KeyNumbers.jsx';
import { Sheet } from '../../components/Sheet.jsx';
import { DriftLine } from '../../components/Drift.jsx';
import { Status } from '../../components/Status.jsx';
import { PhotoWithReadings } from '../../components/PhotoWithReadings.jsx';
import RackTopology from './RackTopology.jsx';
import { DownloadLink } from '../../components/DownloadLink.jsx';
import { Loading, ErrorLine } from '../../components/Text.jsx';
import { Missing, Disclosure, SubTabs } from '../../components/Ui.jsx';
import { MakeModelEdit } from '../../components/MakeModel.jsx';
import { enc, postForm, postJSON } from '../../utils/http';
import { plural, dateOf, timeOf, NO_EXCEL_TEXT, personName } from '../../utils/format';
import { PORT_STATUS, PROBLEMS, endText, cableText, capWords } from '../../utils/ports';
import { sourceWords } from '../../utils/sources';
import Icon from '../../components/Icon.jsx';
import styles from './Tabs.module.css';

const makeModel = (d) => [d.make, d.model].filter(Boolean).join(' ');
const cx = (...a) => a.filter(Boolean).join(' ');
const unitsOf = (d) => (d.unitTop == null ? null : d.unitBottom != null && d.unitBottom !== d.unitTop ? `U${d.unitBottom}-${d.unitTop}` : `U${d.unitTop}`);

function PortStatus({ status }) {
  const s = PORT_STATUS[status];
  if (!s) return null;
  return <Status kind={s.tone ? status : undefined} className={s.tone ? undefined : styles.plain}>{s.word}</Status>;
}

/**
 * A whole-rack photo is too far to read a data-centre switch's ports and
 * caps: the server says so per device (closeUp), and the screens show one
 * plain line with the close-up action instead of a row of "not clear" per port.
 */
const CLOSE_UP_WHY = {
  no_ports: 'No port could be read on the rack photo.',
  ports: 'Most ports were not clear on the rack photo.',
  caps: 'Most dust caps were not clear on the rack photo.',
};
/** The reason with its numbers: 16 of 30 ports not clear, rather than "most". */
export function closeUpWhy(c, portCount) {
  if (!c) return null;
  if (c.why === 'ports' && c.notClear && portCount) return `${c.notClear} of ${portCount} ports could not be told plugged in or empty on the rack photo.`;
  if (c.why === 'caps' && c.capsNotClear) return `The dust cap of ${c.capsNotClear} empty ports was not clear on the rack photo.`;
  return CLOSE_UP_WHY[c.why] || null;
}
export function CloseUpLine({ scanId, device, className }) {
  if (!device?.closeUp || !scanId) return null;
  return (
    <ActionRow
      className={className}
      icon="camera"
      to={`/results/${enc(scanId)}/zones?device=${enc(device.deviceId)}`}
      title={device.closeUp.text}
      sub={closeUpWhy(device.closeUp, device.ports?.length)}
    />
  );
}

/** Where a port's number came from when the tag hid it (lib/checks.js foldedFaces). */
export const PORT_FROM = {
  other_face: 'Hidden on the tag; taken from the other face of its tag',
  only_cable: 'Hidden on the tag; taken from the only Excel cable on this device not yet seen',
};

/** "FC-001, between port 3 and 4": a label read between two ports, placed on neither. */
export const betweenWords = (l) => `${l.text}, between port ${l.between[0]} and ${l.between[1]}`;

/* ── Overview ──────────────────────────────────────────────────────── */

/** The sheet check's kinds of flag, as rows a person can read. */
/** A unit as a number: 12, "12" and "U12" are all 12. */
const unitNo = (u) => { const n = Number(String(u ?? '').replace(/^U/i, '')); return u != null && u !== '' && Number.isFinite(n) ? n : null; };

export function flagRows(sheet, detail) {
  if (!sheet && !detail) return [];
  const rack = sheet?.rackLabel || detail?.rackLabel || null;
  const to = (end) => endText(end && rack && end.rack === rack ? { ...end, rack: null } : end);
  const on = (name, portNo) => `${name}${portNo != null ? ` port ${portNo}` : ''}`;
  return [
    ...(sheet?.missing || []).map((m) => ({
      key: `m${m.deviceId}-${m.portNo}`, kind: 'missing_cable', deviceId: m.deviceId, portNo: m.portNo, what: `${m.deviceName} port ${m.portNo}`,
      detail: `Empty. ${m.cableLabel ? `Cable ${m.cableLabel}` : 'A cable'} should be here${m.otherEnd?.device ? `, to ${to(m.otherEnd)}` : ''}.`,
      on: on(m.deviceName, m.portNo), device: m.deviceName, cable: m.cableLabel || null, excel: m.otherEnd?.device ? to(m.otherEnd) : null,
    })),
    ...(detail?.cables || []).filter((c) => c.status === 'wrong_port' && c.from).map((c) => ({
      key: `x${c.from.deviceId}-${c.label}`, kind: 'wrong_port', deviceId: c.from.deviceId, portNo: c.from.portNo ?? null,
      what: `Label ${c.label}`,
      detail: `Found on ${c.from.device}${c.from.portNo != null ? ` port ${c.from.portNo}` : ''}; it belongs somewhere else.`,
      on: on(c.from.device, c.from.portNo ?? null), device: c.from.device, cable: c.label || null,
      excel: c.explained ? String(c.explained).replace(/^.*the Excel says\s*/i, '')
        : c.sheetEnds ? [endText(c.sheetEnds.a), endText(c.sheetEnds.z)].filter(Boolean).join(' to ') || null : null,
    })),
    ...(sheet?.unexpected || []).map((m) => ({
      key: `u${m.deviceId}-${m.portNo}`, kind: 'not_in_sheet', deviceId: m.deviceId, portNo: m.portNo, what: `${m.deviceName} port ${m.portNo}`,
      detail: m.expectedBy === 'port' ? 'A cable is plugged in here; the Excel expects this port empty.' : 'A cable is plugged in here that is not in the Excel.',
      on: on(m.deviceName, m.portNo), device: m.deviceName, cable: m.cableLabel || null, excel: m.expectedBy === 'port' ? 'Empty' : null,
    })),
    ...(sheet?.wrongType || []).map((m) => ({
      key: `t${m.deviceId}-${m.portNo}`, kind: 'wrong_type', deviceId: m.deviceId, portNo: m.portNo, what: `${m.deviceName} port ${m.portNo}`,
      detail: `The photo reads ${m.found}; the Excel says ${m.expected}.`,
      on: on(m.deviceName, m.portNo), device: m.deviceName, excel: m.expected,
    })),
    ...(sheet?.units || []).filter((u) => u.status === 'wrong_unit').map((u) => ({
      key: `w${u.deviceId}`, kind: 'wrong_unit', deviceId: u.deviceId, portNo: null, what: u.name,
      detail: `In U${u.unitFound}; the Excel says U${String(u.unitExpected).replace(/^U/i, '')}.`,
      on: u.name, device: u.name, unit: u.unitFound != null ? `U${u.unitFound}` : null, excel: u.unitExpected != null ? `U${String(u.unitExpected).replace(/^U/i, '')}` : null,
    })),
    ...(detail?.notSeen || []).map((n) => {
      const u = unitNo(n.unit);
      return {
        key: `n${n.name}-${n.unit ?? ''}`, kind: 'not_seen', deviceId: null, portNo: null, what: n.name,
        detail: `The Excel lists it${u != null ? ` in U${u}` : ''}; the photo did not find it.`,
        on: n.name, device: n.name, excel: u != null ? `U${u}` : null,
      };
    }),
    // A cap the photo could not tell is asked on the port card, not listed
    // here: a whole rack photo leaves most caps unclear, and that is not a fault.
    ...(sheet?.uncapped || []).map((m) => ({
      key: `c${m.deviceId}-${m.portNo}`, kind: 'uncapped', deviceId: m.deviceId, portNo: m.portNo, what: `${m.deviceName} port ${m.portNo}`,
      detail: 'Empty fibre or SFP port with no dust cap. Fit a cap so dust cannot reach the optics.',
      on: on(m.deviceName, m.portNo), device: m.deviceName, excel: null,
    })),
    ...(sheet?.opticNoCap || []).map((m) => ({
      key: `o${m.deviceId}-${m.portNo}`, kind: 'optic_no_cap', deviceId: m.deviceId, portNo: m.portNo, what: `${m.deviceName} port ${m.portNo}`,
      detail: 'Optic fitted without a dust cap.',
      on: on(m.deviceName, m.portNo), device: m.deviceName, excel: null,
    })),
  ];
}

const FLAG_WORD = { not_seen: 'Not seen', wrong_unit: 'Wrong unit', missing_cable: 'Missing cable', wrong_port: 'Wrong port', not_in_sheet: 'Unexpected cable', uncapped: 'No dust cap', cap_unsure: 'Check cap', optic_no_cap: 'Optic fitted, cap off', wrong_type: 'Wrong port type' };

/** What each kind of flag means, in one sentence. */
export const FLAG_MEANING = {
  missing_cable: 'The Excel lists a cable on this port, but the photo shows the port empty.',
  wrong_port: 'This cable label was read here, but the Excel puts the cable somewhere else.',
  not_in_sheet: 'A cable is plugged in here that is not in the Excel.',
  wrong_unit: 'The device sits in a different rack unit from the one in the Excel.',
  not_seen: 'The Excel lists this device in the rack, but the photo did not find it.',
  uncapped: 'An empty fibre or SFP port with no dust cap.',
  cap_unsure: 'The photo could not tell whether this empty port has its dust cap on.',
  optic_no_cap: 'An optic is fitted with no cable and no dust cap.',
  wrong_type: 'The port is a different type from the one in the Excel, for example SFP where the Excel says QSFP28.',
};

/** The one thing to do about each kind of flag. */
export const FLAG_TODO = {
  missing_cable: 'Check the port. Plug the cable in, or correct the Excel if it was removed on purpose.',
  wrong_port: 'Move the cable to the port the Excel lists, or correct the Excel.',
  not_in_sheet: 'Add the cable to the Excel, or remove it.',
  wrong_unit: 'Move the device, or correct its unit in the Excel.',
  not_seen: 'Check the rack. Take a clearer photo, or correct the Excel.',
  uncapped: 'Fit a dust cap.',
  cap_unsure: 'Look at the port and say whether the cap is on, or take a close-up.',
  optic_no_cap: 'Fit a dust cap.',
  wrong_type: 'Check the port and the optic, or correct the port type in the Excel.',
};

/** "U12 Switch - D-Link": where a device is, what it is, and its make (or name). */
/** Make and model belong to switches only; other devices are named by what they are. */
export const isSwitch = (d) => /switch/i.test(String(d?.type || ''));

/** The device picker's rows: unit, name, and the kind with ports (make for switches). */
export function pickerOptions(devices) {
  return devices.map((d) => {
    const mm = isSwitch(d) ? [d.make, d.model].filter(Boolean).join(' ') : '';
    const sub = [d.type, mm && !mm.includes(d.name) ? mm : null, d.ports.length ? plural(d.ports.length, 'port') : 'no ports read'].filter(Boolean).join(' · ');
    return { value: String(d.deviceId), unit: unitsOf(d), name: d.name, sub };
  });
}

export function deviceLabel(d) {
  const low = (v) => String(v || '').toLowerCase();
  if (!isSwitch(d)) {
    const named = d.name && !low(d.name).includes(low(d.type));
    return `${[unitsOf(d), d.type].filter(Boolean).join(' ')}${named ? ` - ${d.name}` : ''}`;
  }
  // The name only when it says more than the type and make already do.
  const nameAdds = d.name && ![d.type, d.make].some((x) => x && low(d.name).includes(low(x)));
  const tail = [d.make, nameAdds ? d.name : null].filter(Boolean).join(', ');
  return `${[unitsOf(d), d.type].filter(Boolean).join(' ')}${tail ? ` - ${tail}` : ''}`;
}

const STATE_WORD = { connected: 'Connected', empty: 'Empty' };
/** A port read as copper is not one a dust cap is for (the server's sheet_check rule). */
const COPPER = /RJ45|RJ-45|CONSOLE|AUX|MANAGEMENT|USB/i;
const capPort = (p) => !!p.sfpType || (p.type ? !COPPER.test(String(p.type)) : p.stateSource === 'close-up');
const stateWord = (s) => STATE_WORD[s] || 'Not clear on the photo';

/** The key numbers as one short line: "4 units, 2 devices, 1 of 3 ports connected, 1 cable". */
export function keyLine(counts) {
  if (!counts) return '';
  return [plural(counts.units, 'unit'), plural(counts.devices, 'device'), `${counts.connected} of ${plural(counts.ports, 'port')} connected`, plural(counts.cables, 'cable')].join(', ');
}

/** A port's kind as a word for the finder: RJ45, SFP, QSFP, LC ... or Other. */
export const typeOf = (p) => (p.type ? String(p.type).toUpperCase().replace(/_/g, '-') : 'Other');

/** The kinds of port a device has, the most common first: [{ type, ports }]. */
export function portTypes(device) {
  const m = new Map();
  for (const p of device?.ports || []) {
    const t = typeOf(p);
    if (!m.has(t)) m.set(t, []);
    m.get(t).push(p);
  }
  return [...m.entries()].map(([type, ports]) => ({ type, ports: ports.sort((a, b) => a.portNo - b.portNo) }))
    .sort((a, b) => b.ports.length - a.ports.length);
}

/** "RJ45 ports 1-24", or "SFP ports 25, 26". */
export function rangeWords(type, ports) {
  const n = ports.map((p) => p.portNo);
  const contiguous = n.every((v, i) => i === 0 || v === n[i - 1] + 1);
  const list = contiguous && n.length > 1 ? `${n[0]}-${n[n.length - 1]}` : n.join(', ');
  return `${type} port${n.length === 1 ? '' : 's'} ${list}`;
}

/**
 * The port a typed number means among one kind of port: that port number
 * when a port of the kind has it, else the nth port of the kind (SFP 1 is the
 * first SFP cage). Null when there is no such port.
 */
export function findTyped(ports, raw) {
  const n = Number(String(raw || '').trim());
  if (!Number.isInteger(n) || n < 1) return null;
  return ports.find((p) => p.portNo === n) || ports[n - 1] || null;
}

/** "24 RJ45, 2 SFP - 7 connected, 1 missing cable" for one device. */
export function deviceSummary(device) {
  const kinds = portTypes(device).map((t) => `${t.ports.length} ${t.type}`).join(', ');
  const missing = device.ports.filter((p) => p.status === 'missing_cable').length;
  const other = device.ports.filter((p) => p.status !== 'missing_cable' && (PROBLEMS.has(p.status) || p.uncapped || p.opticNoCap)).length;
  return [kinds, [`${device.counts.connected} connected`, missing ? plural(missing, 'missing cable') : null, other ? `${other} to check` : null].filter(Boolean).join(', ')].filter(Boolean).join(' - ');
}

/**
 * Find a port the simple way: the kind of port (only when the device has more
 * than one) and its number, then Find. onFind(portNo) is called with the
 * port found; a number that is not there says so with the valid range.
 */
function PortFinder({ device, value, onFind, idPrefix }) {
  const types = portTypes(device);
  const chosen = value != null ? device.ports.find((p) => p.portNo === Number(value)) : null;
  const [type, setType] = useState(chosen ? typeOf(chosen) : types[0]?.type || null);
  const [num, setNum] = useState('');
  const [err, setErr] = useState('');
  const group = types.find((t) => t.type === type) || types[0];
  const kindWord = String(device.type || 'device').toLowerCase();
  const find = (e) => {
    e.preventDefault();
    const hit = findTyped(group.ports, num);
    if (!hit) { setErr(`This ${kindWord} has ${rangeWords(group.type, group.ports)}.`); return; }
    setErr('');
    onFind(hit.portNo);
  };
  const first = group.ports[0]?.portNo;
  const last = group.ports.at(-1)?.portNo;
  return (
    <form className={styles.pf} onSubmit={find} noValidate>
      {/* One row: the port type (a dropdown when the device has more than one), the number, Find. */}
      <div className={styles.pfRow}>
        {types.length > 1 ? (
          <span className={styles.pfPick}>
            <select
              className={styles.pfSelect}
              aria-label="Port type"
              value={group.type}
              onChange={(e) => { setType(e.target.value); setErr(''); }}
            >
              {types.map((t) => <option key={t.type} value={t.type}>{t.type} ({t.ports.length})</option>)}
            </select>
          </span>
        ) : group ? <span className={styles.pfKind}>{group.type}</span> : null}
        <input
          id={`${idPrefix}-port`}
          className={styles.pfInput}
          aria-label={`${group.type} port number`}
          inputMode="numeric"
          pattern="[0-9]*"
          autoComplete="off"
          value={num}
          onChange={(e) => { setNum(e.target.value.replace(/[^0-9]/g, '').slice(0, 3)); setErr(''); }}
          placeholder={first != null ? (first === last ? `${first}` : `${first} to ${last}`) : 'Port'}
        />
        <button type="submit" className={styles.pfGo}>Find</button>
      </div>
      {err ? <p className={styles.findErr} role="alert">{err}</p> : null}
    </form>
  );
}

const PHOTO_STATE = { connected: 'Connected', empty: 'Empty' };

/** The badge a port card shows: the check's result, else what the photo shows. */
const BADGE = { matched: 'Matched', missing_cable: 'Missing cable', wrong_port: 'Wrong port', not_in_sheet: 'Unexpected cable' };
function PortBadge({ p }) {
  if (BADGE[p.status]) return <Status kind={p.status}>{BADGE[p.status]}</Status>;
  if (PHOTO_STATE[p.state]) return <span className={styles.pcState}>{PHOTO_STATE[p.state]}</span>;
  return null;
}

/** One end of a cable in the A-to-Z strip. */
function EndBox({ label, name, sub, none }) {
  return (
    <div className={cx(styles.pcEnd, none && styles.pcEndNone)} data-end="">
      <span className={styles.pcEndL}>{label}</span>
      <span className={styles.pcEndN}>{name}</span>
      {sub ? <span className={styles.pcEndS}>{sub}</span> : null}
    </div>
  );
}

/**
 * The rack photo zoomed to a port or a device, as a small thumbnail on the
 * right of a card. Tapping it opens it large.
 */
function Thumb({ src, readings, focus, crop, alt, title }) {
  const [big, setBig] = useState(false);
  return (
    <>
      <button type="button" className={cx(styles.thumb, crop && styles.thumbCrop)} onClick={() => setBig(true)} aria-label={`${alt}, open it large`}>
        <PhotoWithReadings src={src} readings={readings} tags={false} focus={focus} crop={crop} alt={alt} className={cx(styles.thumbPhoto, crop && styles.thumbCrop)} />
      </button>
      <Sheet open={big} onClose={() => setBig(false)} title={title}>
        <PhotoWithReadings src={src} readings={readings} tags={false} focus={focus} crop={crop} alt={`${alt}, large`} className={cx(styles.bigPhoto, crop && styles.bigCrop)} />
      </Sheet>
    </>
  );
}

/** "Port 5, R02": a cable end's port, and its rack only when it is another rack. */
const endSub = (end, rack) => [end?.port != null && end.port !== '' ? `Port ${end.port}` : null, end?.rack && end.rack !== rack ? end.rack : null].filter(Boolean).join(', ');

const normName = (v) => String(v ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '');
const lastNumber = (v) => { const m = String(v ?? '').match(/(\d+)(?!.*\d)/); return m ? Number(m[1]) : null; };

/**
 * A cable end from the Excel ("SP-R1-U7-PP3", port "53A" or "Port4") found
 * on this rack's photo: its device by name, and its port by the label read
 * on it ("53A") or by number ("Port4"). Null when the end is on another
 * rack or its device was not read.
 */
/* The tag on a device's box on the photo: its unit, then its label when one
   was read (or the Excel names it), else the kind of device. Never the make
   and model: those live on the device page. */
export function boxTag(d) {
  const label = d?.sheetName || d?.labelText || null;
  return [unitsOf(d), label || d?.type || 'Device'].filter(Boolean).join(' ');
}

export function findOtherEnd(detail, end) {
  if (!detail || !end?.device) return null;
  if (end.rack && detail.rackLabel && normName(end.rack) !== normName(detail.rackLabel)) return null;
  const want = normName(end.device);
  const device = (detail.devices || []).find((d) => (d.name && normName(d.name) === want) || (d.sheetName && normName(d.sheetName) === want));
  if (!device) return null;
  const raw = String(end.port ?? '').trim();
  const code = normName(raw);
  let port = code ? (device.ports || []).find((x) => x.label?.text && normName(x.label.text) === code) : null;
  // The server places a panel's printed code by the site's convention (column and row).
  if (!port && end.portNo != null) port = (device.ports || []).find((x) => x.portNo === Number(end.portNo)) || null;
  // "40A" is a label code, not port 40: only a label read on a port settles it.
  if (!port && raw && !/\d[A-Za-z]+$/.test(raw)) {
    const n = lastNumber(raw);
    port = n == null ? null : (device.ports || []).find((x) => x.portNo === n) || null;
  }
  // The port was not read, but the server placed the code's column on the panel.
  const spot = !port && Array.isArray(end.box) && end.box.length === 4 ? end.box : null;
  return { device, port: port || null, spot };
}

/** The blocks of ports one close-up covers, as the close-ups screen splits them (six for a device of 24 ports or fewer, else twelve). */
export function zoneBlocks(count) {
  const size = count <= 24 ? 6 : 12;
  const out = [];
  for (let from = 1; from <= Math.max(1, count); from += size) out.push({ from, to: Math.min(from + size - 1, Math.max(1, count)) });
  return out;
}

/**
 * A close-up for one port, from the port card: the block of ports that holds
 * it, taken live or uploaded, read, and the card shown again with the label.
 * For an end whose port is not placed yet (the Excel names it by a printed
 * code such as 42A), the person says which block the code is printed above.
 */
function CloseUpSheet({ open, onClose, scanId, device, portNo, code, onDone }) {
  const toast = useToast();
  const fileRef = useRef(null);
  const [live, setLive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [pick, setPick] = useState(null);
  const blocks = zoneBlocks(device.ports.length);
  const holding = portNo != null ? blocks.find((b) => portNo >= b.from && portNo <= b.to) || blocks[0] : null;
  const block = holding || pick || (blocks.length === 1 ? blocks[0] : null);
  const send = async (file) => {
    if (!file || !block) return;
    setBusy(true); setError('');
    try {
      const form = new FormData();
      form.append('deviceId', String(device.deviceId));
      form.append('from', String(block.from));
      form.append('to', String(block.to));
      form.append('image', file);
      const out = await postForm(`/api/scans/${enc(scanId)}/zones`, form);
      const read = (Array.isArray(out?.labels) ? out.labels : []).filter((l) => l?.text).length;
      toast(read ? `${plural(read, 'label')} read.` : 'No label could be read. Try a closer, sharper shot.');
      if (onDone) onDone();
      onClose();
    } catch (e) {
      setError(e.message || 'The close-up could not be read.');
    } finally {
      setBusy(false);
    }
  };
  const title = portNo != null ? `Close-up of port ${portNo}` : `Find ${code} on ${device.name}`;
  const line = portNo != null
    ? `${device.name}: photograph ports ${block.from} to ${block.to} so port ${portNo} and its label are sharp.`
    : `${device.name}: which ports is ${code} printed above?`;
  return (
    <>
      <Sheet open={open && !live} onClose={onClose} title={title}>
        <p className={styles.cuLine}>{line}</p>
        {portNo == null && blocks.length > 1 ? (
          <div className={styles.pfTypes} role="tablist" aria-label="Ports">
            {blocks.map((b) => (
              <button key={b.from} type="button" role="tab" aria-selected={block === b} className={cx(styles.pfType, block === b && styles.pfTypeOn)} onClick={() => setPick(b)}>
                {b.from}-{b.to}
              </button>
            ))}
          </div>
        ) : null}
        <ErrorLine>{error}</ErrorLine>
        <div className={styles.cuActions}>
          <Button onClick={() => setLive(true)} disabled={!block || busy} busy={busy}>Take a photo</Button>
          <Button variant="secondary" onClick={() => fileRef.current?.click()} disabled={!block || busy}>Upload a photo</Button>
        </div>
        <input ref={fileRef} type="file" accept="image/*" className="visually-hidden" tabIndex={-1} aria-label="Close-up photo" data-testid="port-close-up-file" onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; if (f) send(f); }} />
      </Sheet>
      {live ? <RackCamera closeUp title={block ? `Ports ${block.from} to ${block.to}` : device.name} onClose={() => setLive(false)} onShot={(f) => { setLive(false); send(f); }} /> : null}
    </>
  );
}

/**
 * The dust cap on a port that has no cable: what the photo check found (on,
 * off, or too small to tell), a person's own mark first. When the photo could
 * not tell, the person says so here in one tap, and that answer is kept.
 */
const CAP_WORD = { empty: 'Dust cap: off.', capped: 'Dust cap: on.', too_small: 'Dust cap: too small on the photo to check. Take a close-up.' };
function CapLine({ p, device, scanId, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  if (capWords(p)) return <p className={styles.pcCap}>{capWords(p)}</p>;
  if (p.state === 'connected' || p.capState === 'connected') return null;
  const mark = async (flag) => {
    setBusy(true); setErr('');
    try {
      await postJSON(`/api/scans/${enc(scanId)}/ports/cap`, { deviceId: device.deviceId, portNo: p.portNo, flag });
      if (onChanged) onChanged();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  if (p.capState === 'uncertain' && scanId) {
    return (
      <div className={styles.pcCapAsk}>
        <span className={styles.pcCap}>Dust cap: not clear on the photo. Is it on?</span>
        <TextButton small disabled={busy} onClick={() => mark(null)}>Cap on</TextButton>
        <TextButton small disabled={busy} onClick={() => mark('uncapped')}>Cap off</TextButton>
        {err ? <span className={styles.pcCapErr}>{err}</span> : null}
      </div>
    );
  }
  if (CAP_WORD[p.capState]) return <p className={styles.pcCap}>{CAP_WORD[p.capState]}</p>;
  return p.state === 'empty' && capPort(p) ? <p className={styles.pcCap}>Empty port: make sure its dust cap is on.</p> : null;
}

/** A grey box where a photo would be, with one short line in it. */
function NoPhoto({ text }) {
  return <div className={styles.pcNoPhoto}>{text}</div>;
}

/** One end's photo cell: the zoomed photo, or a grey box, then its close-up action. */
function EndPhoto({ which, photo, device, port, spot, missing, name, noPhoto, caption, onShot }) {
  const ringBox = port?.box || spot || null;
  return (
    <div className={cx(styles.pcShot, which === 'a' ? styles.pcShotA : styles.pcShotZ)}>
      {device && photo && (ringBox || device.box) ? (
        <Thumb
          src={photo}
          readings={[{ id: 'd', box: device.box }, ...(ringBox ? [{ id: 'p', box: ringBox, ring: true, status: missing ? { tone: 'bad' } : null }] : [])]}
          crop={device.box || null}
          focus={device.box ? null : ringBox}
          alt={name}
          title={port ? `${device.name}, port ${port.portNo}` : device.name}
        />
      ) : noPhoto ? <NoPhoto text={noPhoto} /> : null}
      {caption ? <span className={styles.pcShotNote}>{caption}</span> : null}
      {onShot ? <TextButton small className={styles.pcShotLink} onClick={onShot} aria-label={`Close-up of the label, ${device.name}${port ? ` port ${port.portNo}` : ''}`}>Close-up of the label</TextButton> : null}
    </div>
  );
}

/**
 * One port as a card: "Port 5" with its badge, the device and unit under it,
 * the cable from this end to the other with its label on the line, and (on
 * Connections) each end zoomed on the rack photo with a close-up action
 * under it, one quiet line of facts, and Previous and Next through the
 * device's ports of the same kind.
 */
function PortCard({ device, port: p, rack, photo, detail, scanId, onStep, onChanged, others = [] }) {
  const [shot, setShot] = useState(null);
  const missing = p.status === 'missing_cable';
  const wrong = p.status === 'wrong_port' && p.labelBelongsTo;
  const label = p.label?.text || p.expected?.cableLabel || p.labelBelongsTo?.cableLabel || null;
  const hasCable = p.state === 'connected' || !!p.expected || !!p.labelBelongsTo || !!label;
  const here = (end) => (end && rack && end.rack === rack ? { ...end, rack: null } : end);
  let other;
  let otherEnd = null;
  if (wrong) {
    const c = p.labelBelongsTo;
    other = <EndBox label="Excel puts it" name={endText(here(c.a)) || 'Not known'} sub={c.z ? `to ${endText(here(c.z))}` : null} />;
    const mine = [device.name, device.sheetName].filter(Boolean).map(normName);
    otherEnd = c.a?.device && !mine.includes(normName(c.a.device)) ? c.a : c.z?.device ? c.z : c.a || null;
  } else if (p.expected?.other?.device) {
    other = <EndBox label="Other end" name={p.expected.other.device} sub={endSub(p.expected.other, rack) || null} />;
    otherEnd = p.expected.other;
  } else if (p.tag?.other) {
    other = <EndBox label="Other end, from its tag" name={p.tag.other.device} sub={[endSub(p.tag.other, rack), p.tag.other.portHidden ? 'Port hidden on the tag' : null].filter(Boolean).join(', ') || null} />;
    otherEnd = p.tag.other;
  } else {
    other = <EndBox label="Other end" name="Not read yet" sub={p.state === 'connected' ? 'Take a close-up of its tag' : null} none />;
  }
  // The other end on this rack's photo, else on the other rack read with it (two racks).
  let far = photo && otherEnd ? findOtherEnd(detail, otherEnd) : null;
  let farPhoto = photo;
  let farElsewhere = null;
  if (!far && otherEnd) {
    for (const o of others) {
      const hit = o.photo ? findOtherEnd(o.detail, otherEnd) : null;
      if (hit) { far = hit; farPhoto = o.photo; farElsewhere = o.detail?.rackLabel || 'the other rack'; break; }
    }
  }
  const farOnPhoto = !!(far && (far.port?.box || far.device.box));
  const thisOnPhoto = !!(p.box || device.box);
  const photos = !!photo && hasCable && (thisOnPhoto || !!otherEnd);
  // What the photo reader and the Excel say about this port, as plain rows.
  const onPhoto = p.state === 'connected' ? ['Connected', cableText(p)].filter(Boolean).join(', ')
    : p.state === 'empty' ? 'Empty' : 'Not clear';
  const rows = [
    ['Port', p.type || 'Type not read'],
    ['On the photo', onPhoto],
    // A label read on the cable's tag, else the one the Excel gives: said
    // which, so a label from the Excel is never taken for one read.
    p.label?.text ? ['Label read', p.label.text] : null,
    !p.label?.text && label ? ['Cable label in the Excel', label] : null,
    p.label?.portFrom && PORT_FROM[p.label.portFrom] ? ['Port number', PORT_FROM[p.label.portFrom]] : null,
    !label && p.tag?.read ? ['Read on the tag', p.tag.read] : null,
    !p.label && p.labelsBetween?.length ? ['Label nearby', p.labelsBetween.map(betweenWords).join('; ')] : null,
    p.expected?.cableType || p.labelBelongsTo?.cableType ? ['Cable type', p.expected?.cableType || p.labelBelongsTo.cableType] : null,
    ['This end', `${device.name}, port ${p.portNo}`],
    otherEnd ? ['Other end', endText(here(otherEnd)) || 'Not known'] : null,
  ].filter(Boolean);
  const group = portTypes(device).find((t) => t.ports.some((x) => x.portNo === p.portNo))?.ports || [];
  const at = group.findIndex((x) => x.portNo === p.portNo);
  const prev = at > 0 ? group[at - 1] : null;
  const next = at >= 0 && at < group.length - 1 ? group[at + 1] : null;
  // With no cable there is no strip, so this end's photo sits on the right as before.
  const sideThumb = !!photo && !hasCable && thisOnPhoto;
  return (
    <section className={cx(styles.pc, missing && styles.pcBad)} aria-label={`Port ${p.portNo} of ${device.name}`}>
      <div className={cx(styles.pcCols, sideThumb && styles.pcWithThumb)}>
        <div className={styles.pcMain}>
          <div className={styles.pcHead}>
            <h2 className={styles.pcTitle}>Port {p.portNo}</h2>
            <span className={styles.pcBadges}>
              <PortBadge p={p} />
              {p.uncapped || (p.state === 'empty' && p.capState === 'empty' && capPort(p)) ? <Status kind="uncapped">No dust cap</Status> : null}
              {!p.uncapped && p.state === 'empty' && p.capState === 'uncertain' ? <Status kind="cap_unsure">Check cap</Status> : null}
              {p.opticNoCap ? <Status kind="optic_no_cap">Optic fitted, cap off</Status> : null}
            </span>
          </div>
          <p className={styles.pcSub}>{[device.name, unitsOf(device)].filter(Boolean).join(', ')}</p>
          {/* The drift check runs by itself on the chosen port (lib/drift.js). */}
          <DriftLine drift={p.drift} className={styles.pcDrift} />
          {hasCable ? (
            <div className={cx(styles.pcStrip, photos && styles.pcStripPhotos)}>
              <div className={styles.pcA}><EndBox label="This end" name={device.name} sub={`Port ${p.portNo}`} /></div>
              <div className={cx(styles.pcLink, missing && styles.pcLinkBad)} aria-hidden={label ? undefined : 'true'}>
                {label ? <span className={styles.pcCable}><span className="visually-hidden">Cable </span>{label}</span> : null}
              </div>
              <div className={styles.pcZ}>{other}</div>
              {photos ? (
                <>
                  <EndPhoto
                    which="a" photo={photo} device={device} port={p} missing={missing}
                    name={`Port ${p.portNo} on the rack photo`}
                    noPhoto={thisOnPhoto ? null : 'Not found on the photo'}
                    onShot={scanId ? () => setShot({ device, portNo: p.portNo, code: null }) : null}
                  />
                  <EndPhoto
                    which="z" photo={farPhoto} device={farOnPhoto ? far.device : null} port={far?.port || null} spot={far?.spot || null}
                    name={far ? `Other end, ${far.device.name}${far.port ? ` port ${far.port.portNo}` : far.spot ? ` ${otherEnd?.port || ''} by its column` : ''} on the ${farElsewhere ? `${farElsewhere} ` : ''}rack photo` : ''}
                    noPhoto={otherEnd && !farOnPhoto ? 'Not on this photo' : null}
                    // The Excel names a panel port by its printed code: placed by its column when the port itself was not read.
                    caption={farOnPhoto && !far.port && otherEnd?.port ? (far.spot ? 'Placed by the panel column; the port itself was not read.' : `${otherEnd.port} not read on this panel yet, so the port is not placed`) : null}
                    onShot={scanId && farOnPhoto && !farElsewhere ? () => setShot({ device: far.device, portNo: far.port?.portNo ?? null, code: far.port ? null : String(otherEnd.port || label || '') }) : null}
                  />
                </>
              ) : null}
            </div>
          ) : null}
          <dl className={styles.pfList}>
            {rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
          </dl>
          <CapLine p={p} device={device} scanId={scanId} onChanged={onChanged} />
        </div>
        {sideThumb ? (
          <Thumb
            src={photo}
            readings={[{ id: 'd', box: device.box }, ...(p.box ? [{ id: 'p', box: p.box, ring: true, status: missing ? { tone: 'bad' } : null }] : [])]}
            focus={p.box || device.box}
            alt={`Port ${p.portNo} on the rack photo`}
            title={`${device.name}, port ${p.portNo}`}
          />
        ) : null}
      </div>
      {onStep && group.length > 1 ? (
        <ButtonPair className={styles.pcSteps}>
          <Button variant="secondary" disabled={!prev} onClick={() => prev && onStep(prev.portNo)}>Previous port</Button>
          <Button variant="secondary" disabled={!next} onClick={() => next && onStep(next.portNo)}>Next port</Button>
        </ButtonPair>
      ) : null}
      {shot && scanId ? (
        <CloseUpSheet open onClose={() => setShot(null)} scanId={scanId} device={shot.device} portNo={shot.portNo} code={shot.code} onDone={onChanged} />
      ) : null}
    </section>
  );
}

/**
 * The Overview is a port finder: the rack photo on top, then a device and a
 * port to choose. The chosen port is ringed on the photo and zoomed to, and
 * what is known of it is shown under the pickers: the cable, its label, the
 * other end from the Excel dataset, and a Missing cable flag.
 */
/**
 * Where the Overview photo is zoomed while a port is found: the whole rack
 * first, then the chosen device, then its port, one step at a time so the
 * person sees where the port is. Another port on the same device goes from
 * the device straight to the new port.
 */
function useZoomSteps(deviceKey, portNo) {
  const [stage, setStage] = useState(deviceKey ? (portNo != null ? 'port' : 'device') : 'rack');
  const last = useRef(deviceKey);
  useEffect(() => {
    const timers = [];
    const later = (ms, st) => timers.push(setTimeout(() => setStage(st), ms));
    if (!deviceKey) {
      setStage('rack');
    } else if (last.current !== deviceKey) {
      setStage('rack');
      later(400, 'device');
      if (portNo != null) later(1200, 'port');
    } else if (portNo != null) {
      setStage('device');
      later(600, 'port');
    } else {
      setStage('device');
    }
    last.current = deviceKey;
    return () => timers.forEach(clearTimeout);
  }, [deviceKey, portNo]);
  return stage;
}

export function OverviewTab({ scanId, photo, detail, sheet, sheetError, manager, rackName, qualityWarning, deviceId, portNo, onChoose, onCapChanged, onWrong, onFlags }) {
  const [wide, setWide] = useState(false);
  const devices = useMemo(() => [...(detail?.devices || [])].sort((a, b) => (b.unitTop ?? -1) - (a.unitTop ?? -1)), [detail]);
  const device = devices.find((d) => String(d.deviceId) === String(deviceId)) || null;
  const port = device ? device.ports.find((p) => p.portNo === Number(portNo)) || null : null;
  const unitStatus = new Map((sheet?.units || []).map((u) => [String(u.deviceId), u]));
  const readings = devices.map((d) => {
    const missing = d.ports.filter((p) => p.status === 'missing_cable').length;
    const wrong = unitStatus.get(String(d.deviceId))?.status === 'wrong_unit';
    const status = wrong ? { word: 'Wrong unit', tone: 'bad' } : missing ? { word: missing === 1 ? 'Missing cable' : `${missing} missing cables`, tone: 'bad' } : null;
    return { id: d.deviceId, box: d.box, tag: boxTag(d), status };
  });
  if (port?.box) readings.push({ id: `p${port.portNo}`, box: port.box, ring: true, status: port.status === 'missing_cable' ? { tone: 'bad' } : null });
  const stage = useZoomSteps(device ? String(device.deviceId) : null, port ? port.portNo : null);
  const focus = wide ? null : stage === 'port' && port?.box ? port.box : stage !== 'rack' && device ? device.box : null;
  // The port's cut: a third of its device's width around it, so the ports on
  // either side show where it sits.
  const portCut = (p, d) => {
    if (!p?.box || !d?.box) return p?.box || null;
    const w = (d.box[2] - d.box[0]) / 3;
    const c = (p.box[0] + p.box[2]) / 2;
    return [c - w / 2, d.box[1], c + w / 2, d.box[3]];
  };
  const cropTo = wide || !device || stage === 'rack' ? null : stage === 'port' && port?.box ? portCut(port, device) : device.box;
  const rows = flagRows(sheet, detail);
  const hasMatrix = !!sheet?.matrixImportId || !!detail?.matrix;
  const choose = (dev, p) => { setWide(false); onChoose(dev, p); };
  return (
    <div className={styles.overview}>
      <div className={cx(styles.photoCol, device && styles.stuck)}>
        <div className={styles.photoWrap}>
          {photo ? (
            <PhotoWithReadings
              src={photo}
              readings={readings}
              selectedId={device ? device.deviceId : null}
              onSelect={(id) => { if (!String(id).startsWith('p')) choose(id, null); }}
              // Whole rack, then the device cut out at one fixed shape, then a
              // tighter cut around the port, the device still on both sides.
              crop={cropTo}
              cropAspect={cropTo ? 2 : undefined}
              focus={cropTo ? null : focus}
              alt={`${rackName}, rack photo`}
              className={styles.photo}
            />
          ) : <Loading label="Opening the rack" />}
          {photo && device ? (
            <button type="button" className={styles.zoomBtn} onClick={() => setWide((w) => !w)} aria-pressed={!wide}>
              {wide ? (port ? 'Zoom to the port' : 'Zoom to the device') : 'Whole rack'}
            </button>
          ) : null}
        </div>
        {qualityWarning ? <p className={styles.warnLine}>{qualityWarning} <TextButton small to="/scan">Scan again</TextButton></p> : null}
      </div>
      <div className={styles.infoCol}>
        {!detail ? <Loading /> : (
          <>
            {devices.length ? (
              <DevicePicker
                label="Device"
                value={device ? String(device.deviceId) : ''}
                onChange={(v) => choose(v || null, null)}
                options={pickerOptions(devices)}
                placeholder="Choose a device to find a port"
              />
            ) : <p className={styles.hint}>No devices were read on this rack. <TextButton small to="/scan">Scan again</TextButton></p>}
            {device && device.ports.length ? (
              <>
                <PortFinder key={device.deviceId} device={device} value={port ? port.portNo : null} onFind={(n) => choose(device.deviceId, n)} idPrefix="ov" />
              </>
            ) : null}
            {device && isSwitch(device) && (!device.make || !device.model) ? (
              <div className={styles.mmPrompt}>
                <MakeModelEdit key={device.deviceId} prompt scanId={scanId} deviceId={device.deviceId} make={device.make} model={device.model} onSaved={() => { if (onCapChanged) onCapChanged(); }} />
              </div>
            ) : null}
            {device && !device.ports.length && !device.closeUp ? <p className={styles.hint}>No ports were read on this device.</p> : null}
            {device && device.closeUp ? <CloseUpLine scanId={scanId} device={device} className={styles.cuRow} /> : null}
            {port ? <PortCard device={device} port={port} rack={detail?.rackLabel} scanId={scanId} onStep={(n) => choose(device.deviceId, n)} onChanged={onCapChanged} /> : null}
          </>
        )}
        {sheetError ? <ErrorLine>{sheetError.message}</ErrorLine> : null}
        {sheet && !hasMatrix ? (
          <p className={styles.quietLine}>
            <span>{NO_EXCEL_TEXT}</span>
            {manager ? <TextButton small to="/matrix">Upload the Excel file</TextButton> : <span>Ask your site manager to upload it.</span>}
          </p>
        ) : null}
        {detail ? (
          <ActionRows className={styles.ovActions}>
            {rows.length ? <ActionRow icon="alert" title={`${plural(rows.length, 'flag')} on this rack`} sub={hasMatrix ? 'Against the Excel dataset and the photo' : 'From the photo only'} onClick={onFlags} /> : null}
            <ActionRow icon="camera" to={`/results/${enc(scanId)}/zones`} title="Read cable labels" sub="Take close-ups of a device" />
            {onWrong ? <ActionRow quiet icon="report" title="Report a problem" onClick={onWrong} /> : null}
          </ActionRows>
        ) : null}
      </div>
    </div>
  );
}

function FlagList({ rows }) {
  return (
    <ul className={styles.flags}>
      {rows.map((r) => (
        <li key={r.key}>
          <span className={styles.flagWhat}>{r.what}</span>
          <Status kind={r.kind}>{FLAG_WORD[r.kind]}</Status>
          <span className={styles.flagDetail}>{r.detail}</span>
        </li>
      ))}
    </ul>
  );
}

/* ── Flags ───────────────────────────────────────────────────────── */

const FLAG_ORDER = ['missing_cable', 'wrong_port', 'not_in_sheet', 'wrong_type', 'wrong_unit', 'not_seen', 'uncapped', 'cap_unsure', 'optic_no_cap'];

/** One flag in plain words: what it means, the facts, the port on the photo, and what to do. */
function FlagSheet({ row, detail, photo, onClose }) {
  const device = row && row.deviceId != null ? (detail?.devices || []).find((d) => String(d.deviceId) === String(row.deviceId)) || null : null;
  const port = device && row?.portNo != null ? device.ports.find((p) => p.portNo === Number(row.portNo)) || null : null;
  const seen = port ? PHOTO_STATE[port.state] || null : null;
  const facts = row ? [
    ['Device', row.device || device?.name],
    row.portNo != null ? ['Port', `${row.portNo}${port?.type ? `, ${port.type}` : ''}`] : null,
    ['Unit', row.unit || (device ? unitsOf(device) : null)],
    ['Cable', row.cable || port?.label?.text || port?.expected?.cableLabel || null],
    ['Excel says', row.excel || (port?.labelBelongsTo ? [endText(port.labelBelongsTo.a), endText(port.labelBelongsTo.z)].filter(Boolean).join(' to ') : null)],
    ['Seen on the photo', seen],
  ].filter((r) => r && r[1]) : [];
  const box = port?.box || device?.box || null;
  return (
    <Sheet open={!!row} onClose={onClose} title={row ? FLAG_WORD[row.kind] : ''}>
      {row ? (
        <div className={styles.fs}>
          <p className={styles.fsOn}>{row.on || row.what}</p>
          <div className={cx(styles.pcCols, photo && box && styles.pcWithThumb)}>
            <div className={styles.fsMain}>
              <p className={styles.fsMeaning}>{FLAG_MEANING[row.kind]}</p>
              {facts.length ? (
                <dl className={styles.pfList}>
                  {facts.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
                </dl>
              ) : null}
            </div>
            {photo && box ? (
              <Thumb
                src={photo}
                readings={[{ id: 'd', box: device.box }, ...(port?.box ? [{ id: 'p', box: port.box, ring: true, status: { tone: 'bad' } }] : [])]}
                focus={box}
                alt={`${row.on || row.what} on the rack photo`}
                title={row.on || row.what}
              />
            ) : null}
          </div>
          <p className={styles.fsLabel}>What to do</p>
          <p className={styles.fsTodo}>{FLAG_TODO[row.kind]}</p>
          <Button variant="secondary" onClick={onClose}>Close</Button>
        </div>
      ) : null}
    </Sheet>
  );
}

/** Every flag on the rack, counted by kind; a flag opens a sheet that explains it. */
export function FlagsTab({ detail, sheet, photo, scanId }) {
  const [kind, setKind] = useState('all');
  const [open, setOpen] = useState(null);
  if (!detail || !sheet) return <Loading />;
  const noExcel = !detail.matrix && !sheet.matrixImportId;
  const rows = flagRows(sheet, detail).sort((a, b) => FLAG_ORDER.indexOf(a.kind) - FLAG_ORDER.indexOf(b.kind));
  if (noExcel && !rows.length) {
    return <Missing title="No Excel file uploaded" text={NO_EXCEL_TEXT} />;
  }
  // Devices the rack photo could not read closely enough: one line each, not a flag.
  const sid = scanId ?? detail.scanId;
  const closeUps = (detail.devices || []).filter((d) => d.closeUp);
  const closeUpBlock = closeUps.length && sid ? (
    <ActionRows className={styles.cuRows}>
      {closeUps.map((d) => <CloseUpLine key={d.deviceId} scanId={sid} device={d} />)}
    </ActionRows>
  ) : null;
  if (!rows.length) {
    return (
      <>
        {closeUpBlock}
        <Missing title="No flags" text={closeUps.length ? 'Nothing read on this rack disagrees with the Excel dataset. The close-ups above read what the rack photo could not.' : 'Nothing on this rack disagrees with the Excel dataset. Close-ups of each device read the cable labels and can find more.'} />
      </>
    );
  }
  const counts = FLAG_ORDER.map((k) => [k, rows.filter((r) => r.kind === k).length]).filter(([, n]) => n);
  const shown = kind === 'all' || counts.length < 2 ? rows : rows.filter((r) => r.kind === kind);
  return (
    <div className={styles.flagsTab}>
      {closeUpBlock}
      {noExcel ? <p className={styles.quietLine}><span>{NO_EXCEL_TEXT}</span></p> : null}
      {/* All is every flag; a kind is only that kind. With one kind only, the
          two would list the same flags, so the line says so instead. */}
      {counts.length > 1
        ? <SubTabs label="Kind of flag" value={kind} onChange={setKind} options={[{ value: 'all', label: 'All flags', count: rows.length }, ...counts.map(([k, n]) => ({ value: k, label: FLAG_WORD[k], count: n }))]} />
        : <p className={styles.flagsOne}>{rows.length === 1 ? `1 flag: ${FLAG_WORD[counts[0][0]]}` : `${rows.length} flags, all ${FLAG_WORD[counts[0][0]]}`}</p>}
      <ul className={styles.flagCards} aria-label="Flags">
        {shown.map((r) => (
          <li key={r.key}>
            <button type="button" className={styles.flagCard} onClick={() => setOpen(r)} aria-haspopup="dialog">
              <span className={styles.flagTop}>
                <span className={styles.flagWhat}>{r.what}</span>
                <Status kind={r.kind}>{FLAG_WORD[r.kind]}</Status>
              </span>
              <span className={styles.flagDetail}>{r.detail}</span>
            </button>
          </li>
        ))}
      </ul>
      <FlagSheet row={open} detail={detail} photo={photo} onClose={() => setOpen(null)} />
    </div>
  );
}

/* ── Devices ───────────────────────────────────────────────────────── */

/**
 * The rack from the top unit down as one list: each device on its unit(s)
 * with its type, make and model, height and how sure the reading is, and the
 * units with nothing in them folded into one short line ("U3-U1 empty").
 */
export function deviceRows(detail) {
  const devices = detail?.devices || [];
  const byId = new Map(devices.map((d) => [d.deviceId, d]));
  const rows = [];
  const units = detail?.units || [];
  if (!units.length) {
    return devices.slice().sort((a, b) => (b.unitTop ?? -1) - (a.unitTop ?? -1)).map((d) => ({ kind: 'device', key: `d${d.deviceId}`, device: d }));
  }
  const shown = new Set();
  for (const u of units) {
    const d = u.kind === 'device' ? byId.get(u.deviceId) : null;
    if (d) {
      if (!shown.has(d.deviceId)) { shown.add(d.deviceId); rows.push({ kind: 'device', key: `d${d.deviceId}`, device: d }); }
      continue;
    }
    const kind = u.kind === 'unidentified' ? 'unidentified' : 'empty';
    const last = rows[rows.length - 1];
    if (last && last.kind === kind && last.to === u.unit + 1) { last.to = u.unit; continue; }
    rows.push({ kind, key: `${kind}${u.unit}`, from: u.unit, to: u.unit });
  }
  // A device read with no unit still belongs on the list.
  for (const d of devices) if (!shown.has(d.deviceId)) rows.push({ kind: 'device', key: `d${d.deviceId}`, device: d });
  return rows;
}

/** "Kind seen on the photo, 91% sure; make read from the label, 80% sure" for one device. */
export function deviceSureLine(d) {
  const f = d.fields || {};
  return [
    f.type ? `Kind ${sourceWords(f.type)}` : null,
    f.make ? `make ${sourceWords(f.make)}` : null,
    f.model ? `model ${sourceWords(f.model)}` : null,
    f.sheetName ? `name ${sourceWords(f.sheetName)}` : null,
  ].filter(Boolean).join('; ');
}


/** A device's flag, if any: "bad" for a missing cable or wrong unit, "warn" for the rest. */
export function deviceFlag(d) {
  if (d.sheetStatus === 'wrong_unit' || d.ports.some((p) => p.status === 'missing_cable' || p.status === 'wrong_port')) return 'bad';
  if (d.ports.some((p) => ['not_in_sheet', 'not_found', 'uncapped', 'optic_no_cap'].includes(p.status) || p.uncapped || p.opticNoCap)) return 'warn';
  return null;
}




/**
 * The rack from the top unit down, one calm row per device: its own picture
 * cut from the photo, the unit and a slim bar as tall as the device, make and
 * model with the kind under it, its ports as a strip of dots, how sure the
 * camera was, and a dot when something is flagged. Empty units fold into one
 * short line. Tapping a row opens the device.
 */
export function DevicesTab({ detail, onOpen }) {
  if (!detail) return <Loading />;
  if (!detail.devices.length) {
    return <Missing title="No devices were read" text="Scan again straight on, with the whole rack in the frame." action={<TextButton to="/scan">Scan again</TextButton>} />;
  }
  const rows = deviceRows(detail).filter((r) => r.kind === 'device');
  // Devices the Excel lists in this rack that the photo did not find, at their unit.
  for (const n of detail.notSeen || []) {
    const u = unitNo(n.unit);
    const row = { kind: 'not_seen', key: `n${n.name}-${n.unit ?? ''}`, name: n.name, unit: u };
    const at = u == null ? -1 : rows.findIndex((r) => r.device && (r.device.unitTop ?? -1) < u);
    if (at < 0) rows.push(row); else rows.splice(at, 0, row);
  }
  const total = detail.unitsTotal;
  const usedU = detail.devices.reduce((n, d) => n + (d.heightU || 1), 0);
  const ports = detail.devices.reduce((n, d) => n + d.ports.length, 0);
  const inUse = detail.devices.reduce((n, d) => n + (d.counts?.connected || 0), 0);
  return (
    <>
      <KeyNumbers className={styles.kpiGap} label="Rack in numbers" items={[
        { label: 'Devices', value: detail.devices.length },
        { label: 'Units used', value: total ? `${usedU}/${total}` : usedU },
        { label: 'Ports', value: ports },
        { label: 'In use', value: inUse },
      ]} />
      <ul className={styles.dtable} aria-label="Devices, top unit first">
        {rows.map((r) => {
          if (r.kind === 'not_seen') {
            return (
              <li key={r.key}>
                <div className={cx(styles.trow, styles.trowGhost)}>
                  <span className={styles.tunit}>{r.unit != null ? `U${r.unit}` : '-'}</span>
                  <span className={styles.tmain}>
                    <span className={styles.tname}>{r.name}</span>
                    <span className={styles.tmeta}>Not seen on the photo</span>
                  </span>
                  <span className={styles.tports} />
                  <span className={cx(styles.tflag, styles.flag_warn)} aria-hidden="true" />
                </div>
              </li>
            );
          }
          const d = r.device;
          const mmRaw = isSwitch(d) ? makeModel(d) : null;
          const mm = mmRaw && !String(mmRaw).includes(d.name) ? mmRaw : null;
          const flag = deviceFlag(d);
          const meta = [d.type, d.heightU ? `${d.heightU}U` : null, mm, d.closeUp ? 'Needs a close-up' : null].filter(Boolean).join(' · ');
          const pct = d.ports.length ? Math.round((100 * (d.counts?.connected || 0)) / d.ports.length) : null;
          return (
            <li key={r.key}>
              <button type="button" className={styles.trow} onClick={() => onOpen(d)} aria-label={`${d.name}, ${unitsOf(d) || 'unit not read'}${flag ? ', flagged' : ''}, open`}>
                <span className={styles.tunit}>{unitsOf(d) || '-'}</span>
                <span className={styles.tmain}>
                  <span className={styles.tname}>{d.name}</span>
                  <span className={styles.tmeta}>{meta}</span>
                </span>
                <span className={styles.tports}>
                  {d.ports.length ? (
                    <>
                      <span className={styles.tnum}>{d.counts?.connected || 0}/{d.ports.length}</span>
                      <span className={styles.tbar}><i style={{ width: `${pct}%` }} /></span>
                    </>
                  ) : <span className={styles.tnum}>-</span>}
                </span>
                <span className={cx(styles.tflag, flag && styles[`flag_${flag}`])} aria-hidden="true" />
              </button>
            </li>
          );
        })}
      </ul>
    </>
  );
}

/* ── Connections ───────────────────────────────────────────────────── */


const pickPort = (filter) => (p) => (filter === 'connected' ? p.state === 'connected'
  : filter === 'empty' ? p.state === 'empty'
    : filter === 'problems' ? PROBLEMS.has(p.status) || !!p.uncapped || !!p.opticNoCap : true);

/** Where the other end of a port's cable is, in words. */
export function otherEndWords(p, hasMatrix) {
  if (p.expected?.other) return endText(p.expected.other);
  if (p.labelBelongsTo) {
    const c = p.labelBelongsTo;
    return `cable ${c.cableLabel} runs ${endText(c.a)} to ${endText(c.z)}`;
  }
  return hasMatrix ? 'Other end not known' : 'Other end not known: no Excel file has been uploaded';
}

/**
 * Connections, the simple way: the filter chips, a device, one line about
 * its ports ("24 RJ45 - 7 connected, 1 missing cable"), its problems if it
 * has any, and a port found by its kind and number, with its cable and where
 * the other end goes. No list of every port.
 */
export function ConnectionsTab({ scanId, detail, photo, onChanged, others = [] }) {
  const [pick, setPick] = useState(null);
  const [portNo, setPortNo] = useState(null);
  const withPorts = useMemo(() => (detail?.devices || []).filter((d) => d.ports.length)
    .sort((a, b) => (b.unitTop ?? -1) - (a.unitTop ?? -1)), [detail]);
  if (!detail) return <Loading />;
  // Devices whose ports the rack photo could not read at all: one line each.
  const sid = scanId ?? detail.scanId;
  const unread = (detail.devices || []).filter((d) => d.closeUp && !d.ports.length);
  const unreadBlock = unread.length && sid ? (
    <ActionRows className={styles.cuRows}>
      {unread.map((d) => <CloseUpLine key={d.deviceId} scanId={sid} device={d} />)}
    </ActionRows>
  ) : null;
  if (!withPorts.length) {
    return unreadBlock ? <div className={styles.conn}>{unreadBlock}</div> : <Missing title="No ports were read" text="No device on this rack showed ports in the photo." />;
  }
  const shownDevices = withPorts;
  const device = shownDevices.find((d) => String(d.deviceId) === String(pick)) || shownDevices[0] || null;
  const port = device && portNo != null ? device.ports.find((p) => p.portNo === portNo) || null : null;
  const problems = device ? device.ports.filter(pickPort('problems')) : [];
  // A cable label read on this device (not placed on one port) whose cable
  // the Excel puts on another device: said in full, both ends.
  const strays = device ? (detail.cables || []).filter((c) => c.from?.deviceId === device.deviceId
    && c.from.portNo == null && c.status === 'wrong_port' && c.explained) : [];
  // Labels read between two ports, placed on neither: said with both ports.
  const between = device ? (detail.cables || []).filter((c) => c.from?.deviceId === device.deviceId
    && c.from.portNo == null && Array.isArray(c.from.between) && c.status !== 'wrong_port') : [];
  return (
    <div className={styles.conn}>
      {unreadBlock}
      {!device ? <p className={styles.hint}>No ports were read.</p> : (
        <>
          <div className={styles.connPick}>
            <DevicePicker
              label="Device"
              value={String(device.deviceId)}
              onChange={(v) => { setPick(v); setPortNo(null); }}
              options={pickerOptions(shownDevices)}
            />
            <KeyNumbers label="Ports of this device" items={[
              { label: 'Ports', value: device.ports.length },
              { label: 'Connected', value: device.ports.filter(pickPort('connected')).length },
              { label: 'Empty', value: device.ports.filter(pickPort('empty')).length },
              { label: 'Problems', value: problems.length, tone: problems.length ? 'bad' : undefined },
            ]} />
            {problems.length ? (
              <div className={styles.probs}>
                <p className={styles.probsT}>Problems on this device</p>
                <ul className={styles.probList}>
                  {problems.slice(0, 6).map((p) => (
                    <li key={p.portNo}>
                      <button type="button" className={styles.probRow} onClick={() => setPortNo(p.portNo)}>
                        <span className={styles.probNo}>Port {p.portNo}</span>
                        {PROBLEMS.has(p.status) ? <PortStatus status={p.status} /> : p.uncapped ? <PortStatus status="uncapped" /> : <PortStatus status="optic_no_cap" />}
                      </button>
                    </li>
                  ))}
                </ul>
                {problems.length > 6 ? <p className={styles.hint}>and {problems.length - 6} more; find them by number.</p> : null}
              </div>
            ) : null}
            {device.closeUp && device.ports.length ? <CloseUpLine scanId={sid} device={device} /> : null}
            {strays.length ? (
              <div className={styles.probs}>
                <p className={styles.probsT}>Labels read on this device that the Excel puts elsewhere</p>
                <ul className={styles.probList}>
                  {strays.map((c) => (
                    <li key={c.label} className={styles.strayRow}>
                      <span className={styles.probRow}>
                        <span className={styles.probNo}>Label {c.label}</span>
                        <PortStatus status="wrong_port" />
                      </span>
                      <span className={styles.cdetail}>{c.explained}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            {between.length ? (
              <div className={styles.probs}>
                <p className={styles.probsT}>Labels read between two ports</p>
                <ul className={styles.probList}>
                  {between.map((c) => (
                    <li key={c.label}>
                      <button type="button" className={styles.probRow} onClick={() => setPortNo(c.from.between[0])}>
                        <span className={styles.probNo}>{betweenWords({ text: c.label, between: c.from.between })}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            <PortFinder key={device.deviceId} device={device} value={portNo} onFind={setPortNo} idPrefix="cn" />
          </div>
          {port ? <PortCard device={device} port={port} rack={detail.rackLabel} photo={photo} detail={detail} scanId={scanId ?? detail.scanId} onStep={setPortNo} onChanged={onChanged} others={others} /> : null}
        </>
      )}
    </div>
  );
}

/* ── Topology ──────────────────────────────────────────────────────── */

export function TopologyTab({ detail, onOpen }) {
  if (!detail) return <Loading />;
  if (!detail.devices?.length) return <Missing title="No devices were read" text="Scan again straight on, with the whole rack in the frame." />;
  return <RackTopology detail={detail} onOpen={onOpen} />;
}

/* ── Report ────────────────────────────────────────────────────────── */

/** Every unit from the top down with the device in it, or Empty. */
export function unitTableRows(detail) {
  const byId = new Map((detail?.devices || []).map((d) => [d.deviceId, d]));
  const seen = new Set();
  const rows = (detail?.units || []).map((u) => {
    const d = u.deviceId != null ? byId.get(u.deviceId) : null;
    if (!d) return { key: `u${u.unit}`, unit: `U${u.unit}`, device: u.kind === 'unidentified' ? 'Not identified' : u.kind === 'not_read' ? 'Not read' : 'Empty', empty: true };
    const first = !seen.has(d.deviceId);
    seen.add(d.deviceId);
    return {
      key: `u${u.unit}`,
      unit: `U${u.unit}`,
      device: first ? d.name : `${d.name} (continued)`,
      kind: first ? d.type : '',
      make: first ? makeModel(d) : '',
      ports: first && d.counts.ports ? `${d.counts.connected} of ${d.counts.ports} connected` : '',
      more: !first,
    };
  });
  for (const d of detail?.devices || []) {
    if (!seen.has(d.deviceId)) rows.push({ key: `d${d.deviceId}`, unit: unitsOf(d) || '-', device: d.name, kind: d.type, make: makeModel(d), ports: d.counts.ports ? `${d.counts.connected} of ${d.counts.ports} connected` : '' });
  }
  return rows;
}

/** One download button; a small menu offers the report as PDF, CSV or JSON. */
function DownloadMenu({ scanId, reportName }) {
  const [open, setOpen] = useState(false);
  const base = `/api/scans/${enc(scanId)}/report`;
  return (
    <div className={styles.dlWrap}>
      <button type="button" className={styles.dlBtn} aria-label="Download the report" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <Icon name="download" />
      </button>
      {open ? (
        <>
          <button type="button" className={styles.dlScrim} aria-label="Close" tabIndex={-1} onClick={() => setOpen(false)} />
          <div className={styles.dlMenu} role="menu" onClick={() => setOpen(false)}>
            {[['pdf', 'PDF', 'For reading and printing'], ['csv', 'CSV', 'For Excel'], ['json', 'JSON', 'For other systems']].map(([ext, word, sub]) => (
              <DownloadLink key={ext} className={styles.dlItem} path={`${base}.${ext}`} name={`${reportName}.${ext}`}>
                <span className={styles.dlWord}>{word}</span><span className={styles.dlSub}>{sub}</span>
              </DownloadLink>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}

/**
 * Everything about the rack, short: a small photo (tap to see it large), the
 * key numbers in one block, and three folded sections - units and devices,
 * ports and cables by device, flags - each opened on demand. The downloads
 * are at the top; the PDF keeps every detail.
 */
export function ReportTab({ scanId, detail, sheet, photo, rackName, reportName, when, by }) {
  const [big, setBig] = useState(false);
  const withPorts = useMemo(() => (detail?.devices || []).filter((d) => d.ports.length)
    .sort((a, b) => (b.unitTop ?? -1) - (a.unitTop ?? -1)), [detail]);
  const rows = flagRows(sheet, detail);
  const hasMatrix = !!detail?.matrix;
  const readings = (detail?.devices || []).map((d) => {
    const missing = d.ports.filter((p) => p.status === 'missing_cable').length;
    return { id: d.deviceId, box: d.box, tag: boxTag(d), status: missing ? { word: missing === 1 ? 'Missing cable' : `${missing} missing cables`, tone: 'bad' } : null };
  });
  const c = detail?.counts;
  const bad = rows.filter((r) => r.kind === 'missing_cable' || r.kind === 'wrong_unit').length;
  return (
    <div className={styles.report}>
      <div className={styles.dlBar}>
        <DownloadMenu scanId={scanId} reportName={reportName} />
      </div>
      {!detail ? <Loading /> : (
        <>
          <div className={cx(styles.card, styles.rsum)}>
            <button type="button" className={styles.rthumb} onClick={() => setBig(true)} aria-label={`${rackName}, rack photo, open it large`} disabled={!photo}>
              {photo ? <PhotoWithReadings src={photo} readings={readings} tags={false} alt={`${rackName}, rack photo`} className={styles.rthumbPhoto} /> : null}
              {photo ? <span className={styles.rthumbHint}>Tap to enlarge</span> : null}
            </button>
            <dl className={styles.rfacts}>
              <div><dt>Scanned</dt><dd>{when ? `${dateOf(when)}, ${timeOf(when)}` : 'Not known'}{by ? `, ${personName(by)}` : ''}</dd></div>
              <div><dt>Units</dt><dd>{c.units}{detail.unitsTotal ? ` (${detail.unitsTotal}U rack)` : ''}</dd></div>
              <div><dt>Devices</dt><dd>{c.devices}</dd></div>
              <div><dt>Ports</dt><dd>{c.connected} connected, {c.empty} empty{c.unknown ? `, ${c.unknown} not clear` : ''}</dd></div>
              <div><dt>Cables</dt><dd>{c.cables}</dd></div>
              <div><dt>Flags</dt><dd className={bad ? styles.badText : undefined}>{sheet ? rows.length : '-'}{!hasMatrix ? ' (no Excel dataset)' : ''}</dd></div>
            </dl>
          </div>

          <Disclosure title="Units and devices" count={detail.devices.length} sub="Every unit from the top down">
            {detail.units.length || detail.devices.length ? (
              <table className={styles.rtable}>
                <thead><tr><th scope="col">Unit</th><th scope="col">Device</th><th scope="col" className={styles.wideOnly}>Make and model</th><th scope="col">Ports</th></tr></thead>
                <tbody>
                  {unitTableRows(detail).filter((r) => !r.more).map((r) => (
                    <tr key={r.key} className={r.empty ? styles.dim : undefined}>
                      <td className={styles.tdUnit}>{r.unit}</td>
                      <td>{r.device}{r.kind && r.kind !== r.device ? <span className={styles.tdSub}>{r.kind}</span> : null}</td>
                      <td className={styles.wideOnly}>{r.make || ''}</td>
                      <td>{r.ports || ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <p className={styles.hint}>No units were read on this rack.</p>}
          </Disclosure>

          <Disclosure title="Ports and cables" count={c.ports} sub={withPorts.length ? `By device, ${plural(withPorts.length, 'device')} with ports` : 'No ports read'}>
            {withPorts.map((d) => (
              <Disclosure key={d.deviceId} className={styles.inner} title={d.name} sub={[unitsOf(d), d.type].filter(Boolean).join(', ')} count={`${d.counts.connected}/${d.counts.ports}`}>
                {d.closeUp ? <p className={styles.hint}>{d.closeUp.text}. {CLOSE_UP_WHY[d.closeUp.why]}</p> : null}
                <table className={styles.rtable}>
                  <thead><tr><th scope="col">Port</th><th scope="col">State</th><th scope="col">Cable</th><th scope="col">Other end</th></tr></thead>
                  <tbody>
                    {d.ports.map((p) => (
                      <tr key={p.portNo}>
                        <td className={styles.tdUnit}>{p.portNo}</td>
                        <td>{stateWord(p.state)}{PROBLEMS.has(p.status) ? <span className={styles.tdSub}><PortStatus status={p.status} /></span> : null}</td>
                        <td>{[cableText(p), p.label ? p.label.text : null].filter(Boolean).join(', ')}</td>
                        <td>{p.state === 'connected' || p.expected ? otherEndWords(p, hasMatrix) : ''}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Disclosure>
            ))}
          </Disclosure>

          <Disclosure title="Flags" count={sheet ? rows.length : null} tone={bad ? 'bad' : rows.length ? 'warn' : undefined} sub={!sheet ? 'Loading' : !sheet.matrixImportId ? 'From the photo only: no Excel file has been uploaded' : 'Against the Excel dataset'}>
            {sheet && rows.length ? <FlagList rows={rows} /> : null}
            {sheet && !rows.length ? <p className={styles.hint}>Nothing is flagged on this rack.</p> : null}
          </Disclosure>
        </>
      )}
      <Sheet open={big} onClose={() => setBig(false)} title={rackName}>
        {photo ? <div className={styles.reportPhoto}><PhotoWithReadings src={photo} readings={readings} alt={`${rackName}, rack photo with the readings`} className={styles.photo} /></div> : null}
      </Sheet>
    </div>
  );
}
