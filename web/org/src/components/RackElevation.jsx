import styles from './RackElevation.module.css';

const cx = (...a) => a.filter(Boolean).join(' ');

const KIND_WORD = { empty: 'Empty', unidentified: 'Not identified', not_read: 'Nothing read' };

/**
 * The unit rows of a scan grouped into blocks: a device covering several
 * units is one block; every other unit is a block of its own.
 * rows: the detail route's units (top unit first).
 */
export function blocksOf(rows = []) {
  const out = [];
  for (const r of rows) {
    const last = out[out.length - 1];
    if (r.kind === 'device' && last && last.kind === 'device' && last.deviceId === r.deviceId) {
      last.units.push(r.unit);
      if (r.expected && !last.expected) { last.expected = r.expected; last.expectedStatus = r.expectedStatus; }
      continue;
    }
    out.push({ kind: r.kind, deviceId: r.deviceId, units: [r.unit], expected: r.expected, expectedStatus: r.expectedStatus });
  }
  return out;
}

export const unitRange = (units) => {
  const lo = Math.min(...units);
  const hi = Math.max(...units);
  return lo === hi ? `U${hi}` : `U${lo}-${hi}`;
};

/**
 * A rack drawn from real data with HTML and CSS: top unit first, each unit
 * with the device in it or what the photo showed there. renderDevice(device,
 * block) fills a device's block; `compact` for two racks side by side.
 */
export function RackElevation({ rows, devices, renderDevice, renderEmpty, compact, label, className, selectedDeviceId }) {
  const byId = new Map((devices || []).map((d) => [d.deviceId, d]));
  const blocks = blocksOf(rows);
  if (!blocks.length) return null;
  return (
    <div className={cx(styles.rack, compact && styles.compact, className)} role="list" aria-label={label || 'Rack elevation'}>
      {blocks.map((b) => {
        const d = b.kind === 'device' ? byId.get(b.deviceId) : null;
        const key = `${b.units[0]}-${b.deviceId ?? b.kind}`;
        return (
          <div
            key={key}
            role="listitem"
            className={cx(styles.row, d ? styles.dev : styles.gap, d && selectedDeviceId === d.deviceId && styles.sel)}
            style={{ '--u': b.units.length }}
          >
            <span className={styles.u}>{unitRange(b.units)}</span>
            <div className={styles.body}>
              {d ? renderDevice(d, b) : (
                renderEmpty ? renderEmpty(b) : (
                  <span className={styles.gapWord}>
                    {KIND_WORD[b.kind] || 'Empty'}
                    {b.expected ? <span className={styles.exp}>Sheet: {b.expected}</span> : null}
                  </span>
                )
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/**
 * The ports of one device as small cells, coloured by status, with the port
 * number in each. Tapping one calls onPort(port).
 */
export function PortCells({ ports, onPort, selected, label }) {
  if (!ports?.length) return null;
  return (
    <div className={styles.cells} role="group" aria-label={label}>
      {ports.map((p) => {
        const on = selected != null && selected === p.portNo;
        return (
          <button
            key={p.portNo}
            type="button"
            className={cx(styles.cell, styles[`c_${p.status}`], on && styles.cellOn)}
            onClick={onPort ? () => onPort(p) : undefined}
            aria-label={`Port ${p.portNo}, ${p.statusWord}`}
            aria-pressed={onPort ? on : undefined}
            title={`Port ${p.portNo}: ${p.statusWord}`}
          >
            {p.portNo}
          </button>
        );
      })}
    </div>
  );
}

export default RackElevation;
