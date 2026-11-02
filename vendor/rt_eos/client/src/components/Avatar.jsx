import styles from './Avatar.module.css';

const cx = (...a) => a.filter(Boolean).join(' ');

/** "priya.s" -> "PS", "mandar" -> "M": the first letter of up to two parts of the name. */
export function initialsOf(user) {
  const s = String(user?.username || user?.email || '').split('@')[0];
  const parts = s.split(/[^A-Za-z0-9]+/).filter(Boolean);
  const out = parts.slice(0, 2).map((p) => p[0].toUpperCase()).join('');
  return out || '?';
}

/**
 * The person's circle: the photo they uploaded, else their initials in text.
 * size: sm 24, md 32, xl 64.
 */
export function Avatar({ user, size = 'md', active, className, label }) {
  const photo = user?.avatarPhoto || null;
  return (
    <span className={cx(styles.av, styles[size], active && styles.active, className)} aria-label={label} role={label ? 'img' : undefined} aria-hidden={label ? undefined : 'true'}>
      {photo ? <img src={photo} alt="" className={styles.img} /> : <span>{initialsOf(user)}</span>}
    </span>
  );
}

export default Avatar;
