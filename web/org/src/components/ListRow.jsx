import { Link } from 'react-router-dom';
import styles from './ListRow.module.css';

const cx = (...a) => a.filter(Boolean).join(' ');

/**
 * A list row: hairline divided, never a card.
 * title, meta (grey line), sub (second grey line), right (status word, link,
 * chevron), lead (avatar). With `to` or `onClick` the whole row is the target.
 */
export function ListRow({ title, meta, sub, right, lead, to, onClick, compact, className, titleClassName, ...rest }) {
  const body = (
    <>
      {lead ? <span className={styles.lead}>{lead}</span> : null}
      <span className={styles.rt}>
        <span className={cx(styles.rn, titleClassName)}>{title}</span>
        {meta ? <span className={styles.rm}>{meta}</span> : null}
        {sub ? <span className={styles.rm}>{sub}</span> : null}
      </span>
      {right ? <span className={styles.rr}>{right}</span> : null}
    </>
  );
  const cls = cx(styles.row, compact && styles.compact, (to || onClick) && styles.tap, className);
  if (to) return <Link to={to} className={cls} {...rest}>{body}</Link>;
  if (onClick) return <button type="button" className={cls} onClick={onClick} {...rest}>{body}</button>;
  return <div className={cls} {...rest}>{body}</div>;
}

export default ListRow;
