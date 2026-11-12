import styles from './Drift.module.css';

/**
 * The drift check of one port, as the server works it out (lib/drift.js),
 * kept to one line: Matched with a green dot, or Drift detected with a
 * dropdown that lists only the fields that differ (what the Excel has, what
 * was read). It runs by itself when a port is chosen; nothing is typed.
 */

/** The fields that differ, in plain words: "Cable colour: Excel Blue, read Red". */
export function driftLines(drift) {
  return (drift?.fields || [])
    .filter((f) => f.result === 'mismatch')
    .map((f) => ({ field: f.field, label: f.label, expected: f.expected ?? '-', actual: f.actual ?? '-' }));
}

export function DriftLine({ drift, className }) {
  if (!drift) return null;
  const cls = [styles.line, className].filter(Boolean).join(' ');
  if (drift.result === 'no_drift') {
    return <p className={`${cls} ${styles.ok}`}><span className={styles.dot} aria-hidden="true" />Matched</p>;
  }
  if (drift.result === 'drift') {
    const lines = driftLines(drift);
    return (
      <details className={`${cls} ${styles.bad}`}>
        <summary><span className={styles.dot} aria-hidden="true" />Drift detected</summary>
        <ul className={styles.list}>
          {lines.map((l) => (
            <li key={l.field}><b>{l.label}</b>: Excel {l.expected}, read {l.actual}</li>
          ))}
        </ul>
      </details>
    );
  }
  return <p className={`${cls} ${styles.none}`}><span className={styles.dot} aria-hidden="true" />Not checked: {drift.reason ? drift.reason.charAt(0).toLowerCase() + drift.reason.slice(1) : 'port not clear on the photo'}</p>;
}

export default DriftLine;
