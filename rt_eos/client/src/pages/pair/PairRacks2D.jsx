import { useMemo } from 'react';
import { TopologyDiagram, TopologyLegend, kindKey } from '../../components/TopologyDiagram.jsx';
import { shortName, tailName } from '../../components/RackStencil.jsx';
import styles from './PairRacks2D.module.css';

/*
 * Two racks read together, as a topology: both racks on the same baseline,
 * the devices as tiles by unit, and the cables of the label check as smooth
 * curves from port dot to port dot (across the gap for a cable between the
 * two racks, a loop on the outer side for one inside a rack, a curve out to a
 * small pill tag for an end in a rack that was not photographed). An end read
 * on a tag is a filled dot, an end only expected a hollow one. Tap a cable or
 * a device to follow it; one line under the drawing says what it is.
 */

const norm = (s) => String(s ?? '').toLowerCase().replace(/[^a-z0-9]/g, '');
const portNumber = (p) => {
  const m = String(p ?? '').match(/(\d+)(?!.*\d)/);
  return m ? Number(m[1]) : null;
};

const TONE_OF = { matched: 'ok', on_tag: 'ok', wrong_port: 'bad', missing_cable: 'bad', not_found: 'grey' };
export const STATUS_WORD = { matched: 'Matched', on_tag: 'On the tag', wrong_port: 'Wrong port', missing_cable: 'Missing', not_found: 'Not read' };

const endText = (ex) => (ex ? [ex.rack, shortName(ex.device), ex.port].filter(Boolean).join(' ') : 'Not known');
const unitText = (top, bottom) => (top === bottom ? `U${top}` : `U${bottom}-${top}`);

/**
 * Each cable of the check, with where its two ends go on this drawing:
 * { id, label, status, tone, dashed, from, to, ends: [end, end] }, an end
 * being { rack: 'a' | 'z', device, order } when it is on a drawn device, or
 * { off: { key, text } } when it is not. A cable with no end on either rack
 * is left out.
 */
export function planCables(a, z, check) {
  const racks = [['a', a], ['z', z]];
  const place = (ex, read) => {
    if (!ex) return null;
    const r = norm(ex.rack);
    for (const [side, d] of racks) {
      if (!d || !r || norm(d.rackLabel) !== r) continue;
      const k = norm(ex.device);
      const dev = k ? (d.devices || []).find((v) => norm(v.sheetName) === k || norm(v.name) === k) : null;
      if (dev && dev.unitTop != null) return { rack: side, device: dev.deviceId, order: portNumber(ex.port) ?? 0, hollow: !read };
    }
    const text = [ex.rack, tailName(ex.device)].filter(Boolean).join(' ') || 'Not known';
    return { off: { key: norm(text), text } };
  };
  const out = [];
  (check?.cables || []).forEach((x, i) => {
    if (x.status === 'not_in_sheet') return;
    const ends = [place(x.a?.expected, !!x.a?.found), place(x.z?.expected, !!x.z?.found)];
    if (!ends.some((e) => e && !e.off)) return;
    const label = x.cableLabel || 'No label';
    const from = endText(x.a?.expected);
    const to = endText(x.z?.expected);
    const word = STATUS_WORD[x.status] || 'Not checked';
    out.push({
      id: `c${i}`,
      cableLabel: label,
      status: x.status,
      tone: TONE_OF[x.status] || 'ink',
      dashed: x.status === 'not_found',
      from,
      to,
      label: `${label}, ${from} to ${to}, ${word}`,
      ends: ends.map((e) => e || { off: { key: 'unknown', text: 'Not known' } }),
    });
  });
  return out;
}

/** One rack of the detail answer as the diagram draws it: devices and the units nothing could be read on. */
function rackOf(d, key) {
  const devices = (d?.devices || []).filter((v) => v.unitTop != null).map((v) => {
    const top = Math.max(v.unitTop, v.unitBottom ?? v.unitTop);
    const bottom = Math.min(v.unitTop, v.unitBottom ?? v.unitTop);
    const name = v.sheetName || v.name || v.type || 'Device';
    const ports = (v.ports || []).length;
    const sub = [v.type, ports ? `${ports} port${ports === 1 ? '' : 's'}` : null].filter(Boolean).join(', ');
    return { id: v.deviceId, name, text: shortName(name) || v.type, sub, kind: kindKey(v.type), top, bottom, unit: unitText(top, bottom), aria: `${name}, ${unitText(top, bottom)}` };
  });
  for (const u of d?.units || []) {
    if (u.kind !== 'unidentified') continue;
    devices.push({ id: `u${u.unit}`, name: 'Unknown', text: 'Unknown', top: u.unit, bottom: u.unit, unknown: true, aria: `U${u.unit}, not identified` });
  }
  const units = Math.max(d?.unitsTotal || 0, ...devices.map((v) => v.top), 1);
  return { key, name: d?.rackLabel || (key === 'a' ? 'Rack A' : 'Rack Z'), units, devices };
}

/** The one line under the drawing: the chosen cable or device, or how to choose one. */
function caption(selected, cables, racks) {
  if (selected?.kind === 'cable') {
    const c = cables.find((x) => x.id === selected.id);
    if (c) return <><span className={`mono ${styles.capLabel}`}>{c.cableLabel}</span> {c.from} to {c.to}, <span className={styles.capSt} data-tone={c.tone}>{STATUS_WORD[c.status] || 'Not checked'}</span></>;
  }
  if (selected?.kind === 'device') {
    const r = racks.find((x) => x.key === selected.rack);
    const d = r?.devices.find((v) => v.id === selected.id);
    if (d) {
      const n = cables.filter((c) => c.ends.some((e) => e.rack === r.key && e.device === d.id)).length;
      return <><b>{d.name}</b> {r.name} {d.unit}, {n} cable{n === 1 ? '' : 's'}</>;
    }
  }
  return 'Tap a cable or a device to follow it, or a rack to open it alone.';
}

export function PairRacks2D({ a, z, check, selected = null, onSelect, onOpenRack }) {
  const cables = useMemo(() => planCables(a, z, check), [a, z, check]);
  const racks = useMemo(() => [rackOf(a, 'a'), rackOf(z, 'z')], [a, z]);
  const pick = (s) => onSelect?.(selected && s && selected.kind === s.kind && (selected.key ?? selected.id) === (s.key ?? s.id) ? null : s);
  const legend = [
    cables.some((c) => c.tone === 'ok') ? ['ok', 'Matched'] : null,
    cables.some((c) => c.tone === 'bad') ? ['bad', 'Problem'] : null,
    cables.some((c) => c.tone === 'grey') ? ['grey', 'Not read', true] : null,
  ].filter(Boolean);
  return (
    <div className={styles.wrap} data-testid="pair-racks" data-cables={cables.map((c) => c.cableLabel).join('|')}>
      <TopologyDiagram
        racks={racks}
        cables={cables}
        selected={selected}
        onDevice={pick}
        onCable={pick}
        onClear={() => onSelect?.(null)}
        onRack={onOpenRack}
        label={`${racks[0].name} and ${racks[1].name}, cables`}
      />
      <p className={styles.caption} aria-live="polite">{caption(selected, cables, racks)}</p>
      <TopologyLegend items={legend} />
    </div>
  );
}
