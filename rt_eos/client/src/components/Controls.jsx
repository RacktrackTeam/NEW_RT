import { useId } from 'react';
import styles from './Controls.module.css';

const cx = (...a) => a.filter(Boolean).join(' ');

/** A switch in a row: label and grey line on the left, the switch on the right. Ink, never the accent. */
export function Switch({ label, meta, checked, onChange, disabled }) {
  const id = useId();
  return (
    <div className={styles.row}>
      <label htmlFor={id} className={styles.rt}>
        <span>{label}</span>
        {meta ? <span className={styles.rm}>{meta}</span> : null}
      </label>
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={!!checked}
        disabled={disabled}
        className={cx(styles.sw, checked && styles.on)}
        onClick={() => onChange(!checked)}
      >
        <i />
      </button>
    </div>
  );
}

/** A tick box in a row. Ink, never the accent. */
export function Tick({ label, meta, checked, onChange, disabled }) {
  const id = useId();
  return (
    <div className={styles.chk}>
      <input
        id={id}
        type="checkbox"
        className={styles.box}
        checked={!!checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <label htmlFor={id} className={styles.rt}>
        <span>{label}</span>
        {meta ? <span className={styles.rm}>{meta}</span> : null}
      </label>
    </div>
  );
}

/**
 * Text tabs with an ink underline (Photo / Stitch / Video, Activity / Log).
 * options: [{ value, label }] or strings. `dark` for the camera.
 */
export function Seg({ options, value, onChange, dark, label, className }) {
  return (
    <div className={cx(styles.seg, dark && styles.dk, className)} role="tablist" aria-label={label}>
      {options.map((o) => {
        const v = typeof o === 'object' ? o.value : o;
        const l = typeof o === 'object' ? o.label : o;
        const on = v === value;
        return (
          <button
            key={v}
            type="button"
            role="tab"
            aria-selected={on}
            className={cx(styles.segBtn, on && styles.segOn)}
            onClick={() => onChange(v)}
          >
            {l}
          </button>
        );
      })}
    </div>
  );
}

/** A thin progress line (worklist). value 0 to 1. */
export function Progress({ value, label }) {
  const pct = Math.max(0, Math.min(1, Number(value) || 0)) * 100;
  return (
    <div className={styles.prog} role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}>
      <i style={{ width: `${pct}%` }} />
    </div>
  );
}
