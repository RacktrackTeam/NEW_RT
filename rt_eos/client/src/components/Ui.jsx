import { useId, useState } from 'react';
import Icon from './Icon.jsx';
import styles from './Ui.module.css';

const cx = (...a) => a.filter(Boolean).join(' ');

/**
 * The shapes every main screen is built from: a large title, sections with a
 * heading, quiet panels, numbers in a row, tables with hairlines, a tab bar
 * of words, and a legend in words.
 */

/** The large title of a main screen, a grey line under it, actions on the right. */
export function PageHead({ title, sub, actions, className, as: Tag = 'h1' }) {
  return (
    <header className={cx(styles.head, className)}>
      <div className={styles.headText}>
        <Tag className={styles.title}>{title}</Tag>
        {sub ? <p className={styles.sub}>{sub}</p> : null}
      </div>
      {actions ? <div className={styles.headActs}>{actions}</div> : null}
    </header>
  );
}

/** A section: its heading, an optional grey line, an optional action on the right. */
export function Section({ title, sub, action, children, className, id }) {
  return (
    <section className={cx(styles.section, className)} aria-labelledby={id}>
      {title ? (
        <div className={styles.sHead}>
          <div>
            <h2 className={styles.sTitle} id={id}>{title}</h2>
            {sub ? <p className={styles.sSub}>{sub}</p> : null}
          </div>
          {action ? <div className={styles.sAct}>{action}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}

/** A quiet block with a hairline edge. `tone` "accent" for the one thing to do. */
export function Panel({ children, tone, className, as: Tag = 'div', ...rest }) {
  return <Tag className={cx(styles.panel, tone && styles[`p_${tone}`], className)} {...rest}>{children}</Tag>;
}

/** Numbers in a row: [{ label, value, tone, hint }]. */
export function Stats({ items, className }) {
  return (
    <dl className={cx(styles.stats, className)}>
      {items.filter(Boolean).map((s) => (
        <div key={s.label} className={styles.stat}>
          <dt className={styles.statL}>{s.label}</dt>
          <dd className={cx(styles.statV, s.tone && styles[s.tone])}>{s.value}</dd>
          {s.hint ? <dd className={styles.statH}>{s.hint}</dd> : null}
        </div>
      ))}
    </dl>
  );
}

/**
 * A table with hairlines. columns: [{ key, label, render?, align?, main? }].
 * On a phone each row becomes a short block: the `main` column as its title
 * and the others as label and value lines.
 */
export function DataTable({ columns, rows, rowKey, onRow, empty, caption, className, rowClass }) {
  if (!rows.length) return empty ? <p className={styles.tEmpty}>{empty}</p> : null;
  return (
    <div className={cx(styles.tableWrap, className)}>
      <table className={styles.table}>
        {caption ? <caption className="visually-hidden">{caption}</caption> : null}
        <thead>
          <tr>
            {columns.map((c) => <th key={c.key} scope="col" className={c.align === 'right' ? styles.r : undefined}>{c.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr
              key={rowKey ? rowKey(r, i) : i}
              className={cx(onRow && styles.tap, rowClass?.(r))}
              onClick={onRow ? () => onRow(r) : undefined}
            >
              {columns.map((c) => {
                const v = c.render ? c.render(r) : r[c.key];
                return (
                  <td
                    key={c.key}
                    data-label={c.main ? undefined : c.label}
                    className={cx(c.main && styles.main, c.align === 'right' && styles.r, (v === null || v === undefined || v === '') && styles.blank)}
                  >
                    {v === null || v === undefined || v === '' ? <span className={styles.none}>-</span> : v}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A segmented control of words (RackTrack pills). options: [{ value, label, count? }]. `full` spans the width. */
export function SubTabs({ options, value, onChange, label, className, full }) {
  return (
    <div className={cx(styles.tabs, full && styles.full, className)} role="tablist" aria-label={label}>
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button key={o.value} type="button" role="tab" aria-selected={on} className={cx(styles.tab, on && styles.tabOn)} onClick={() => onChange(o.value)}>
            {o.label}
            {o.count != null ? <span className={styles.tabN}>{o.count}</span> : null}
          </button>
        );
      })}
    </div>
  );
}

/** A small tag of colour for a status, the same as the port cells. */
export function Swatch({ kind, className }) {
  return <span className={cx(styles.sw, styles[`sw_${kind}`], className)} aria-hidden="true" />;
}

/** The legend: a swatch and the word for each status shown. items: [{ kind, word }]. */
export function Legend({ items, className }) {
  return (
    <ul className={cx(styles.legend, className)} aria-label="What the colours mean">
      {items.map((i) => (
        <li key={i.kind}><Swatch kind={i.kind} />{i.word}</li>
      ))}
    </ul>
  );
}

/** A line of quiet facts separated by dots: "24 ports, 6 plugged in". */
export function Meta({ children, className }) {
  return <p className={cx(styles.meta, className)}>{children}</p>;
}

/**
 * A section folded to one row: its title, a count, and a chevron. Closed by
 * default so a long screen stays short; opening it shows what it holds.
 */
export function Disclosure({ title, count, sub, defaultOpen = false, children, className, tone }) {
  const [open, setOpen] = useState(!!defaultOpen);
  const id = useId();
  return (
    <section className={cx(styles.disc, open && styles.discOpen, className)} aria-label={typeof title === 'string' ? title : undefined}>
      <button type="button" className={styles.discHead} aria-expanded={open} aria-controls={id} onClick={() => setOpen((v) => !v)}>
        <span className={styles.discT}>
          {title}
          {sub ? <span className={styles.discSub}>{sub}</span> : null}
        </span>
        {count != null ? <span className={cx(styles.discN, tone && styles[`discN_${tone}`])}>{count}</span> : null}
        <Icon name="chevron_right" className={styles.discChev} />
      </button>
      {open ? <div id={id} className={styles.discBody}>{children}</div> : null}
    </section>
  );
}

/**
 * A list shown a few at a time: the first `step` items and a "Show more"
 * button, so a long list never makes a long page. children(item, index).
 */
export function PagedList({ items, step = 6, className, children, label, as: Tag = 'ul' }) {
  const [n, setN] = useState(step);
  const shown = items.slice(0, n);
  const left = items.length - shown.length;
  return (
    <>
      <Tag className={className} aria-label={label}>{shown.map((x, i) => children(x, i))}</Tag>
      {left > 0 ? (
        <button type="button" className={styles.moreBtn} onClick={() => setN((v) => v + step)}>
          Show {Math.min(step, left)} more <span className={styles.moreN}>of {left}</span>
        </button>
      ) : null}
    </>
  );
}

/** A note that says what is missing and the one thing to do about it. */
export function Missing({ title, text, action, className, icon = 'alert' }) {
  return (
    <div className={cx(styles.missing, className)}>
      <span className={styles.missIcon} aria-hidden="true"><Icon name={icon} /></span>
      {title ? <p className={styles.missT}>{title}</p> : null}
      {text ? <p className={styles.missX}>{text}</p> : null}
      {action ? <div className={styles.missA}>{action}</div> : null}
    </div>
  );
}
