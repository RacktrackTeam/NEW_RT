import { useId, useState } from 'react';
import { Sheet } from './Sheet.jsx';
import Icon from './Icon.jsx';
import styles from './DevicePicker.module.css';

const cx = (...a) => a.filter(Boolean).join(' ');

/**
 * A device chosen from a list sheet rather than the phone's own menu, which
 * wraps long names. The field shows the unit and the name on one line.
 *   options: [{ value, unit, name, sub }]
 */
export function DevicePicker({ label = 'Device', value, options = [], onChange, placeholder = 'Choose a device', className }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const cur = options.find((o) => String(o.value) === String(value)) || null;
  return (
    <div className={cx(styles.field, className)}>
      <span className={styles.label} id={id}>{label}</span>
      <button type="button" className={styles.trigger} aria-labelledby={id} aria-haspopup="dialog" data-value={cur ? cur.value : ''} onClick={() => setOpen(true)}>
        {cur ? (
          <>
            <span className={styles.unit}>{cur.unit || '-'}</span>
            <span className={styles.main}>
              <span className={styles.name}>{cur.name}</span>
              {cur.sub ? <span className={styles.sub}>{cur.sub}</span> : null}
            </span>
          </>
        ) : <span className={styles.placeholder}>{placeholder}</span>}
        <Icon name="chevron_right" className={styles.chev} />
      </button>
      <Sheet open={open} onClose={() => setOpen(false)} title={`Choose a ${label.toLowerCase()}`}>
        <ul className={styles.list}>
          {options.map((o) => {
            const on = cur && String(o.value) === String(cur.value);
            return (
              <li key={o.value}>
                <button type="button" className={cx(styles.row, on && styles.on)} aria-pressed={!!on} onClick={() => { setOpen(false); onChange(o.value); }}>
                  <span className={styles.unit}>{o.unit || '-'}</span>
                  <span className={styles.main}>
                    <span className={styles.name}>{o.name}</span>
                    {o.sub ? <span className={styles.sub}>{o.sub}</span> : null}
                  </span>
                  {on ? <Icon name="check" className={styles.tick} /> : null}
                </button>
              </li>
            );
          })}
        </ul>
      </Sheet>
    </div>
  );
}

export default DevicePicker;
