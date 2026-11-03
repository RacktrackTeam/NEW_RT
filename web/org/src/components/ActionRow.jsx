import { Link } from 'react-router-dom';
import Icon from './Icon.jsx';
import styles from './ActionRow.module.css';

const cx = (...a) => a.filter(Boolean).join(' ');

/**
 * A row that goes somewhere: a 36 px icon tile, a title, an optional grey
 * line under it, and a chevron. `quiet` gives the tile a grey fill for a
 * lesser action. With `to` it is a link, otherwise a button.
 */
export function ActionRow({ icon, title, sub, to, onClick, quiet, right, className, ...rest }) {
  const body = (
    <>
      {icon ? <span className={styles.icon} aria-hidden="true"><Icon name={icon} /></span> : null}
      <span className={styles.main}>
        <span className={styles.t}>{title}</span>
        {sub ? <span className={styles.s}>{sub}</span> : null}
      </span>
      {right ? <span className={styles.right}>{right}</span> : null}
      <Icon name="chevron_right" className={styles.chev} />
    </>
  );
  const cls = cx(styles.row, quiet && styles.quiet, className);
  if (to) return <Link to={to} className={cls} {...rest}>{body}</Link>;
  return <button type="button" className={cls} onClick={onClick} {...rest}>{body}</button>;
}

/** Action rows stacked with 8 px between them. */
export function ActionRows({ children, className }) {
  return <div className={cx(styles.stack, className)}>{children}</div>;
}

export default ActionRow;
