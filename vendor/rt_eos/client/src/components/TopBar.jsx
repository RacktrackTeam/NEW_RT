import { Link, useLocation, useNavigate } from 'react-router-dom';
import Icon from './Icon.jsx';
import styles from './TopBar.module.css';

const cx = (...a) => a.filter(Boolean).join(' ');

/** Go back one step, or to `fallback` when this page was opened cold. */
export function useBack(fallback = '/') {
  const navigate = useNavigate();
  const location = useLocation();
  return () => {
    if (location.key && location.key !== 'default') navigate(-1);
    else navigate(fallback, { replace: true });
  };
}

/**
 * The one header of every screen, like an iOS navigation bar: the title in
 * the middle, the back control on the left when there is a screen to go
 * back to, one action on the right (or the three dots). Same height, type
 * and spacing on every page, phone and laptop; it sits under the main
 * navigation on a laptop. `sub` is a small grey line under the title.
 * `signedOut` puts the app name on the left instead of the back control.
 */
export function TopBar({ title, sub, back = true, backTo = '/', onBack, right, onMore, moreLabel = 'More', dark, signedOut, className }) {
  const goBack = useBack(backTo);
  return (
    <header className={cx(styles.top, dark && styles.dark, className)}>
      <div className={styles.left}>
        {back ? (
          <button type="button" className={styles.back} onClick={onBack || goBack} aria-label="Back">
            <Icon name="arrow_back" />
          </button>
        ) : null}
        {signedOut && !back ? <Link to="/login" className={styles.brand}>RackTrack</Link> : null}
      </div>
      <div className={styles.mid}>
        {title ? <h1 className={styles.tt}>{title}</h1> : null}
        {sub ? <span className={styles.tsub}>{sub}</span> : null}
      </div>
      <div className={styles.right}>
        {right}
        {onMore ? (
          <button type="button" className={styles.more} onClick={onMore} aria-label={moreLabel}>
            <Icon name="more" />
          </button>
        ) : null}
      </div>
    </header>
  );
}

export default TopBar;
