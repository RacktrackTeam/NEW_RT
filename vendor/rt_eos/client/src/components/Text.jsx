import styles from './Text.module.css';

const cx = (...a) => a.filter(Boolean).join(' ');

/** The one big title a screen has (30 px), with an optional grey line under it. */
export function BigTitle({ children, sub, className, as: Tag = 'h1' }) {
  return (
    <div className={className}>
      <Tag className={styles.big}>{children}</Tag>
      {sub ? <p className={styles.bigSub}>{sub}</p> : null}
    </div>
  );
}

/** Secondary text: 13 px grey. */
export function Grey({ children, className, m0, as: Tag = 'p', ...rest }) {
  return <Tag className={cx(styles.g, m0 && styles.m0, className)} {...rest}>{children}</Tag>;
}

/** A section label: small grey words over a hairline. */
export function SectionLabel({ children, className, as: Tag = 'h2' }) {
  return <Tag className={cx(styles.sl, className)}>{children}</Tag>;
}

/** A section heading with a text action on the right ("Recent", "See all"). */
export function SectionHead({ title, action, className }) {
  return (
    <div className={cx(styles.shead, className)}>
      <h2 className={styles.sheadT}>{title}</h2>
      {action}
    </div>
  );
}

/** Sheet or card heading: 17 px, semibold. */
export function SubTitle({ children, className, as: Tag = 'h2' }) {
  return <Tag className={cx(styles.st2, className)}>{children}</Tag>;
}

/** Label and value pairs, one per hairline row. */
export function Facts({ pairs, className }) {
  return (
    <dl className={cx(styles.facts, className)}>
      {pairs.filter(Boolean).map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/** One sentence and at most one action. */
export function EmptyState({ text, action, className }) {
  return (
    <div className={cx(styles.empty, className)}>
      <p>{text}</p>
      {action}
    </div>
  );
}

/** A red line saying what went wrong and what to do. */
export function ErrorLine({ children, className }) {
  if (!children) return null;
  return <p className={cx(styles.err, className)} role="alert">{children}</p>;
}

/** A thin line that says a screen is still loading. */
export function Loading({ label = 'Loading' }) {
  return (
    <div className={styles.loading} role="status" aria-live="polite">
      <span className={styles.bar}><i /></span>
      <span className="visually-hidden">{label}</span>
    </div>
  );
}
