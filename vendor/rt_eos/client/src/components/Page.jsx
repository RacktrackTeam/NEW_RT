import styles from './Page.module.css';

const cx = (...a) => a.filter(Boolean).join(' ');

/**
 * The content area under a bar. Full device width: 16 px gutter on a phone,
 * 32 px on a laptop, no centred column. The main navigation's room is
 * kept by the app shell; `stick` leaves room for a button stuck to the bottom.
 */
export function Page({ children, stick, className, as: Tag = 'main', ...rest }) {
  return (
    <Tag className={cx(styles.pad, stick && styles.stick, className)} {...rest}>
      {children}
    </Tag>
  );
}

/** A primary button stuck to the bottom of a phone screen (in flow on a laptop). */
export function Stick({ children, className }) {
  return <div className={cx(styles.stickBar, className)}>{children}</div>;
}

/** Two columns on a laptop, one on a phone. `eq` for equal halves. */
export function Two({ children, eq, className }) {
  return <div className={cx(styles.two, eq && styles.eq, className)}>{children}</div>;
}

/** A horizontal line of things with a spacer ("bar" on the board). */
export function Bar({ children, className }) {
  return <div className={cx(styles.bar, className)}>{children}</div>;
}

export const Spacer = () => <span className={styles.sp} />;

/** Caps the length of a line of text only; the page itself stays full width. */
export function Measure({ children, wide, className }) {
  return <div className={cx(styles.measure, wide && styles.wide, className)}>{children}</div>;
}

export default Page;
