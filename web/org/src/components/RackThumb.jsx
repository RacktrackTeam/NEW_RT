import { Link } from 'react-router-dom';
import AssetImg from './AssetImg.jsx';
import Status from './Status.jsx';
import styles from './RackThumb.module.css';

const cx = (...a) => a.filter(Boolean).join(' ');

/**
 * A rack as a picture: the photo, the rack's name, one grey line and a status
 * word. `none` draws the dashed "Not scanned" box for a rack only the sheet knows.
 */
export function RackThumb({ src, name, meta, status, to, onClick, none, className }) {
  const pic = none
    ? <span className={cx(styles.tph, styles.none)}>Not scanned</span>
    : <span className={styles.tph}>{src ? <AssetImg src={src} alt="" className={styles.img} loading="lazy" /> : null}</span>;
  const body = (
    <>
      {pic}
      <span className={styles.tn}>{name}</span>
      {meta ? <span className={styles.rm}>{meta}</span> : null}
      {status ? <Status kind={status} className={styles.st} /> : null}
    </>
  );
  if (to) return <Link to={to} className={cx(styles.thumb, className)}>{body}</Link>;
  if (onClick) return <button type="button" className={cx(styles.thumb, styles.btn, className)} onClick={onClick}>{body}</button>;
  return <div className={cx(styles.thumb, className)}>{body}</div>;
}

/** Thumbnails side by side, wrapping. */
export function Thumbs({ children, className }) {
  return <div className={cx(styles.thumbs, className)}>{children}</div>;
}

export default RackThumb;
