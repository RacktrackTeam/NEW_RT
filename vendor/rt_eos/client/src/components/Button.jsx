import { Link } from 'react-router-dom';
import styles from './Button.module.css';

const cx = (...a) => a.filter(Boolean).join(' ');

/**
 * A button, 52 tall with radius 12: full width on a phone, its own width on
 * a laptop.
 * size: undefined, "big" (more room above), "auto" (own width everywhere), "sm" (36, inside a row).
 * variant: undefined (primary, ink), "secondary" (white, ink outline) or "danger" (red outline).
 */
export function Button({ size, variant, busy = false, className, children, type = 'button', disabled, to, ...rest }) {
  const cls = cx(styles.btn, size && styles[size], variant && styles[variant], className);
  if (to) {
    return <Link to={to} className={cls} {...rest}>{children}</Link>;
  }
  return (
    <button type={type} className={cls} disabled={disabled || busy} aria-busy={busy || undefined} {...rest}>
      {children}
    </button>
  );
}

/**
 * Secondary action: an underlined text button. Renders a router link with
 * `to`, a plain link with `href`, otherwise a button.
 */
export function TextButton({ to, href, small, danger, onDark, className, children, type = 'button', ...rest }) {
  const cls = cx(styles.lk, small && styles.small, danger && styles.danger, onDark && styles.onDark, className);
  if (to) return <Link to={to} className={cls} {...rest}>{children}</Link>;
  if (href) return <a href={href} className={cls} {...rest}>{children}</a>;
  return <button type={type} className={cls} {...rest}>{children}</button>;
}

/** A row of text buttons, spaced as on the board. */
export function Links({ center, className, children }) {
  return <div className={cx(styles.links, center && styles.center, className)}>{children}</div>;
}

/** Two buttons side by side, half the width each. */
export function ButtonPair({ className, children }) {
  return <div className={cx(styles.pair, className)}>{children}</div>;
}

/** Primary button with its text buttons beside it (sheet actions). */
export function Actions({ className, children }) {
  return <div className={cx(styles.acts, className)}>{children}</div>;
}

export default Button;
