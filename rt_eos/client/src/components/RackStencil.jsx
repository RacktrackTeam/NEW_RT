import styles from './RackStencil.module.css';

/*
 * One rack drawn flat from the front, in the style of a rack stencil: a grey
 * cabinet with a top cap, a plinth and two wheels, light rails with a screw
 * at each device's ears, and every device spanning rail to rail with a face
 * for its kind (switch ports, patch panel jacks, server drive caddies, PDU
 * gauges, storage trays). Plain HTML and CSS only: no svg, no pictures.
 *
 * There is no text on the faces. Each port hole carries data-port (its port
 * number) and each device data-dev (its id), so a drawing laid over the rack
 * (the cable lines) can find where a port is on screen.
 */

/* A device name short enough for a narrow column: nvs-sgkc-sis-sw05b is sis-sw05b. */
export const shortName = (n) => {
  const t = String(n || '').trim();
  if (t.length <= 14) return t;
  // The last two parts, with the separator between them as written.
  const seps = [...t.matchAll(/[-_ ]+/g)].filter((m) => m.index > 0 && m.index + m[0].length < t.length);
  if (seps.length < 2) return t;
  const k = seps[seps.length - 2];
  return t.slice(k.index + k[0].length);
};

/* The last part of a device name only, for a small tag: b08-reg-gwlc02 is gwlc02. */
export const tailName = (n) => String(n || '').trim().split(/[-_ ]+/).filter(Boolean).pop() || '';

const KIND = {
  switch: 'switch', router: 'router', firewall: 'router', 'patch panel': 'patch', server: 'server', pdu: 'pdu',
  storage: 'storage', 'cable manager': 'manager',
};
export const faceKind = (type) => KIND[String(type || '').trim().toLowerCase()] || 'unknown';

/* Cabinet proportions, as fractions of the cabinet width or of one unit. */
const POST = 0.055;
const RAIL = 0.045;
const CAP_U = 1.1;
const PLINTH_U = 2;
const WHEEL_U = 0.55;

/**
 * Where everything sits in a rack drawn uh pixels per unit and w pixels wide:
 * the units from the top down, each device's box, and the cabinet's size.
 */
export function stencilGeometry(detail, uh, w) {
  const units = detail?.units || [];
  const devices = detail?.devices || [];
  const n = Math.max(1, Number(detail?.unitsTotal) || 0, ...units.map((u) => Number(u.unit) || 0), ...devices.map((d) => Number(d.unitTop) || 0));
  const cap = Math.round(uh * CAP_U);
  const post = Math.max(4, Math.round(w * POST));
  const rail = Math.max(3, Math.round(w * RAIL));
  const yOf = (unit) => cap + (n - unit) * uh; // top edge of a unit
  const rows = [];
  const seen = new Set();
  for (const d of devices) {
    const top = Number(d.unitTop) || Number(d.units?.[0]) || 0;
    const bottom = Number(d.unitBottom) || top;
    if (!top) continue;
    const hi = Math.max(top, bottom);
    const lo = Math.min(top, bottom);
    for (let u = lo; u <= hi; u += 1) seen.add(u);
    rows.push({ key: `d${d.deviceId}`, device: d, kind: faceKind(d.type), unitTop: hi, unitBottom: lo, y: yOf(hi), h: (hi - lo + 1) * uh });
  }
  for (const u of units) {
    if (u.kind !== 'unidentified' || seen.has(u.unit)) continue;
    seen.add(u.unit);
    rows.push({ key: `u${u.unit}`, device: null, kind: 'unknown', unitTop: u.unit, unitBottom: u.unit, y: yOf(u.unit), h: uh });
  }
  rows.sort((p, q) => q.unitTop - p.unitTop);
  const bodyH = cap + n * uh + Math.round(uh * PLINTH_U);
  return { n, uh, w, cap, post, rail, rows, bodyH, height: bodyH + Math.round(uh * WHEEL_U) };
}

/* Ports drawn on a face: the real ones in port order, or a sensible number for the kind when none were read. */
const DEFAULT_PORTS = { switch: 24, router: 8, patch: 24 };
const isCage = (t) => /sfp|qsfp|osfp|xfp|gbic/i.test(String(t || ''));
function facePorts(device, kind) {
  const ports = Array.isArray(device?.ports) ? [...device.ports].sort((p, q) => (p.portNo ?? 0) - (q.portNo ?? 0)) : [];
  if (!ports.length) {
    const n = DEFAULT_PORTS[kind] || 0;
    return { jacks: Array.from({ length: n }, (_, i) => ({ portNo: i + 1 })), cages: [] };
  }
  const jacks = ports.filter((p) => !isCage(p.type)).slice(0, 96);
  const cages = ports.filter((p) => isCage(p.type)).slice(0, 16);
  return { jacks, cages };
}

/* RJ45 jacks in blocks of six across, two rows (odd on top), like a switch or a patch panel. */
function JackBlocks({ jacks, patch }) {
  const blocks = [];
  for (let i = 0; i < jacks.length; i += 12) blocks.push(jacks.slice(i, i + 12));
  return blocks.map((b, i) => (
    <span key={i} className={`${styles.block} ${patch ? styles.blockPatch : ''}`}>
      {b.map((p, k) => <i key={k} className={styles.jack} data-port={p.portNo} />)}
    </span>
  ));
}

function Cages({ cages }) {
  if (!cages.length) return null;
  return (
    <span className={styles.cages}>
      {cages.map((p, k) => <i key={k} className={styles.cage} data-port={p.portNo} />)}
    </span>
  );
}

function Face({ kind, device, units }) {
  if (kind === 'switch' || kind === 'router') {
    const { jacks, cages } = facePorts(device, kind);
    const half = Math.ceil(Math.ceil(jacks.length / 12) / 2) * 12;
    return (
      <span className={styles.ports}>
        <JackBlocks jacks={jacks.slice(0, half)} />
        {kind === 'switch' ? <span className={styles.leds} /> : <span className={styles.console}><i /><i /></span>}
        <JackBlocks jacks={jacks.slice(half)} />
        <Cages cages={cages} />
      </span>
    );
  }
  if (kind === 'patch') {
    const { jacks } = facePorts(device, kind);
    return <span className={styles.ports}><JackBlocks jacks={jacks} patch /></span>;
  }
  if (kind === 'server') {
    const cols = units > 1 ? 3 : 4;
    return (
      <span className={styles.server}>
        <span className={styles.caddies} style={{ gridTemplateColumns: `repeat(${cols}, 1fr)`, gridTemplateRows: `repeat(${Math.min(units, 3)}, 1fr)` }}>
          {Array.from({ length: cols * Math.min(units, 3) }, (_, i) => <i key={i} className={styles.caddy} />)}
        </span>
        <span className={styles.status}><i /><i /><i /></span>
      </span>
    );
  }
  if (kind === 'pdu') {
    return (
      <span className={styles.pdu}>
        <i className={styles.outlet} />
        {Array.from({ length: 6 }, (_, i) => <i key={i} className={styles.gauge} style={{ '--a': `${-50 + i * 17}deg` }} />)}
        <i className={styles.knob} />
      </span>
    );
  }
  if (kind === 'storage') {
    const rows = Math.max(1, Math.min(units, 4));
    return (
      <span className={styles.trays} style={{ gridTemplateRows: `repeat(${rows}, 1fr)` }}>
        {Array.from({ length: rows * 3 }, (_, i) => <i key={i} className={styles.tray} />)}
      </span>
    );
  }
  if (kind === 'manager') return <span className={styles.manager} />;
  return <span className={styles.vents} />;
}

/**
 * The rack itself. `side` and the data attributes let a drawing over it find
 * a device (data-dev) and a port on it (data-port).
 */
export function RackStencil({ detail, uh, width, side, label }) {
  const g = stencilGeometry(detail, uh, width);
  return (
    <div
      className={styles.rack}
      data-rack={side}
      role="img"
      aria-label={label || `Rack ${detail?.rackLabel || ''}`.trim()}
      style={{ width, height: g.height, '--uh': `${uh}px`, '--post': `${g.post}px`, '--rail': `${g.rail}px`, '--cap': `${g.cap}px` }}
    >
      <div className={styles.body} style={{ height: g.bodyH }}>
        <div className={styles.bay} style={{ top: g.cap, height: g.n * uh }}>
          <span className={`${styles.rail} ${styles.railL}`} />
          <span className={`${styles.rail} ${styles.railR}`} />
        </div>
        {g.rows.map((r) => (
          <div
            key={r.key}
            className={`${styles.dev} ${styles[`k_${r.kind}`] || ''}`}
            data-dev={r.device ? r.device.deviceId : undefined}
            data-kind={r.kind}
            style={{ top: r.y, height: r.h }}
          >
            <span className={styles.ear} />
            <span className={styles.face}><Face kind={r.kind} device={r.device} units={r.unitTop - r.unitBottom + 1} /></span>
            <span className={styles.ear} />
          </div>
        ))}
      </div>
      <span className={`${styles.wheel} ${styles.wheelL}`} />
      <span className={`${styles.wheel} ${styles.wheelR}`} />
    </div>
  );
}
