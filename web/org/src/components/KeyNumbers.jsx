import styles from './KeyNumbers.module.css';

const cx = (...a) => a.filter(Boolean).join(' ');

/**
 * The key numbers as a row of small tiles: the figure (16-20 px, tabular)
 * over its word. items: [{ label, value, tone? }] - tone "bad" or "warn"
 * colours the figure. Four to a row on a phone.
 */
export function KeyNumbers({ items, className, label }) {
  const shown = items.filter(Boolean);
  return (
    <dl className={cx(styles.kpis, className)} aria-label={label}>
      {shown.map((k) => (
        <div key={k.label} className={styles.tile}>
          <dt className={styles.l}>{k.label}</dt>
          <dd className={cx(styles.v, k.tone && styles[k.tone])}>{k.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export default KeyNumbers;
