import { useEffect, useState } from 'react';
import { Link, NavLink, useLocation } from 'react-router-dom';
import { useAuth } from '../AuthContext.jsx';
import Icon from './Icon.jsx';
import { Sheet } from './Sheet.jsx';
import { personName, roleWord } from '../utils/format';
import styles from './NavBar.module.css';

const cx = (...a) => a.filter(Boolean).join(' ');

/* The places, and the addresses that belong to each. History is its own
   place on a laptop (laptop: true); on a phone it stays under Scan. You is
   the profile at the right end of a laptop bar (phone: true keeps it in the
   phone's bar). */
const YOU = ['/you', '/site', '/settings', '/matrix', '/console', '/help', '/contact'];
export const NAV = [
  { to: '/', label: 'Home', icon: 'home', owns: (p) => p === '/' },
  { to: '/scan', label: 'Scan', icon: 'camera', owns: (p, laptop) => p.startsWith('/scan') || p.startsWith('/results') || (!laptop && p.startsWith('/history')) },
  { to: '/map', label: 'Map', icon: 'two_racks', owns: (p) => p.startsWith('/map') || p.startsWith('/checks') },
  { to: '/history', label: 'History', icon: 'history', laptop: true, owns: (p, laptop) => laptop && p.startsWith('/history') },
  { to: '/you', label: 'You', icon: 'person', phone: true, owns: (p) => YOU.some((x) => p.startsWith(x)) },
];

export const navFor = (pathname, laptop = false) => NAV.find((n) => n.owns(pathname, laptop)) || null;

const isLaptop = () => typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(min-width: 1024px)').matches;

/** The right end of a laptop bar: the site, and who is signed in (to the profile). */
function NavEnd({ user, pathname }) {
  const site = user?.tenant?.name || '';
  const name = personName(user?.name || user?.displayName || user?.username || '');
  const onYou = YOU.some((x) => pathname.startsWith(x));
  return (
    <div className={styles.end}>
      {site ? (
        <span className={styles.place} title="Your site">
          <span className={styles.placeWord}>Site</span>{site}
        </span>
      ) : null}
      {name ? (
        <Link to="/you" className={cx(styles.me, onYou && styles.meOn)} aria-current={onYou ? 'page' : undefined} aria-label={`${name}, your profile`}>
          <span className={styles.avatar} aria-hidden="true">{name.charAt(0)}</span>
          <span className={styles.meText}>
            <span className={styles.meName}>{name}</span>
            <span className={styles.meRole}>{roleWord(user?.role)}</span>
          </span>
        </Link>
      ) : null}
    </div>
  );
}

function Brand() {
  return (
    <Link to="/" className={styles.brand} aria-label="RackTrack, Home">
      <img src="/logo.jpg" alt="" data-nozoom="" className={styles.brandLogo} width="28" height="28" />
      <span>RackTrack</span>
    </Link>
  );
}

/**
 * The main navigation: Home, Scan, Map and You, each an icon over its
 * word. On a phone a white pill floats over the bottom of the screen (the
 * RackTrack bar); on a laptop a bar across the top. The current place is in ink and a step heavier. It tells the page
 * layout it is there (body.has-nav) so bottom bars and docked sheets keep
 * clear of it.
 */
export function NavBar() {
  const { pathname } = useLocation();
  const { user } = useAuth();
  const [laptop, setLaptop] = useState(isLaptop);
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;
    const mq = window.matchMedia('(min-width: 1024px)');
    const on = () => setLaptop(mq.matches);
    mq.addEventListener?.('change', on);
    return () => mq.removeEventListener?.('change', on);
  }, []);
  const current = navFor(pathname, laptop);
  useEffect(() => {
    document.body.classList.add('has-nav');
    return () => document.body.classList.remove('has-nav');
  }, []);
  return (
    <nav className={styles.nav} aria-label="Main">
      <Brand />
      <div className={styles.items}>
        {NAV.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            end={n.to === '/'}
            className={() => cx(styles.item, current === n && styles.on, n.laptop && styles.laptopOnly, n.phone && styles.phoneOnly)}
            aria-current={current === n ? 'page' : undefined}
          >
            <span className={styles.icon} aria-hidden="true"><Icon name={n.icon} /></span>
            <span className={styles.word}>{n.label}</span>
          </NavLink>
        ))}
      </div>
      <NavEnd user={user} pathname={pathname} />
    </nav>
  );
}

/* Screens where the main navigation gives way to the screen's own tabs: an
   open result, one rack or two. */
export const hasOwnTabs = (pathname) => /^\/results\/[^/]+\/?$/.test(pathname) || /^\/map\/[^/]+\/[^/]+\/?$/.test(pathname);

/**
 * A result's own tabs in the main navigation's place and style: the white
 * pill at the bottom of a phone, the bar across the top of a laptop.
 * items: [{ value, label, icon }]. The first `shown` (four) are tabs; when
 * there are more, a fifth item, More (three dots), opens a small sheet with
 * the rest, and is the one lit while one of those is open.
 */
export function TabNav({ items, value, onChange, label, shown = 4 }) {
  const { user } = useAuth();
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  useEffect(() => {
    document.body.classList.add('has-nav');
    return () => document.body.classList.remove('has-nav');
  }, []);
  const folded = items.length > shown + 1;
  const tabs = folded ? items.slice(0, shown) : items;
  const rest = folded ? items.slice(shown) : [];
  const restOn = rest.find((n) => n.value === value) || null;
  return (
    <nav className={cx(styles.nav, styles.tabNav)} aria-label={label}>
      <Brand />
      <div className={styles.items}>
        <div className={styles.tabs} role="tablist" aria-label={label}>
          {tabs.map((n) => {
            const on = n.value === value;
            return (
              <button
                key={n.value}
                type="button"
                role="tab"
                aria-selected={on}
                className={cx(styles.item, on && styles.on)}
                onClick={() => onChange(n.value)}
              >
                <span className={styles.icon} aria-hidden="true"><Icon name={n.icon} /></span>
                <span className={styles.word}>{n.label}</span>
              </button>
            );
          })}
        </div>
        {folded ? (
          <button
            type="button"
            className={cx(styles.item, restOn && styles.on)}
            onClick={() => setOpen(true)}
            aria-haspopup="dialog"
            aria-expanded={open}
            aria-current={restOn ? 'page' : undefined}
            data-testid="nav-more"
          >
            <span className={styles.icon} aria-hidden="true"><Icon name="more" /></span>
            <span className={styles.word}>More</span>
          </button>
        ) : null}
      </div>
      <NavEnd user={user} pathname={pathname} />
      <Sheet open={open} onClose={() => setOpen(false)} title="More" className={styles.moreSheet}>
        <ul className={styles.moreList} aria-label={`More of ${label}`}>
          {rest.map((n) => {
            const on = n.value === value;
            return (
              <li key={n.value}>
                <button
                  type="button"
                  className={cx(styles.moreRow, on && styles.moreOn)}
                  aria-current={on ? 'page' : undefined}
                  onClick={() => { setOpen(false); onChange(n.value); }}
                >
                  <span className={styles.moreIcon} aria-hidden="true"><Icon name={n.icon} /></span>
                  <span className={styles.moreWord}>{n.label}</span>
                  {on ? <span className={styles.moreNow}>Open now</span> : <Icon name="chevron_right" className={styles.moreChev} />}
                </button>
              </li>
            );
          })}
        </ul>
      </Sheet>
    </nav>
  );
}

export default NavBar;
