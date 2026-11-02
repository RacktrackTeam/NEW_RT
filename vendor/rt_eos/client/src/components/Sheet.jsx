import { useEffect, useId, useRef } from 'react';
import { createPortal } from 'react-dom';
import styles from './Sheet.module.css';

const cx = (...a) => a.filter(Boolean).join(' ');

const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

/**
 * A modal bottom sheet: the only surface with a shadow. On a laptop it docks
 * on the right instead. Escape and the scrim close it; focus moves in and
 * comes back to whatever opened it.
 */
export function Sheet({ open, onClose, title, children, className, label }) {
  const ref = useRef(null);
  const titleId = useId();
  useEffect(() => {
    if (!open) return undefined;
    const before = document.activeElement;
    const el = ref.current;
    const first = el?.querySelector('input,select,textarea') || el?.querySelector(FOCUSABLE);
    (first || el)?.focus?.();
    const onKey = (e) => {
      if (e.key === 'Escape') { e.stopPropagation(); onClose?.(); return; }
      if (e.key !== 'Tab' || !el) return;
      const items = Array.from(el.querySelectorAll(FOCUSABLE));
      if (!items.length) return;
      const a = items[0];
      const z = items[items.length - 1];
      if (e.shiftKey && document.activeElement === a) { z.focus(); e.preventDefault(); }
      else if (!e.shiftKey && document.activeElement === z) { a.focus(); e.preventDefault(); }
    };
    // The phone's back button closes the sheet before it leaves the page.
    const onBack = (e) => { e.preventDefault(); onClose?.(); };
    document.addEventListener('keydown', onKey);
    window.addEventListener('rt:back', onBack);
    return () => {
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('rt:back', onBack);
      if (before && typeof before.focus === 'function') before.focus();
    };
  }, [open, onClose]);
  if (!open) return null;
  return createPortal(
    <div className={styles.layer}>
      <div className={styles.scrim} onClick={onClose} aria-hidden="true" />
      <div
        ref={ref}
        className={cx(styles.sheet, className)}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        aria-label={title ? undefined : label}
        tabIndex={-1}
      >
        <span className={styles.grab} aria-hidden="true" />
        {title ? <h2 id={titleId} className={styles.title}>{title}</h2> : null}
        {children}
      </div>
    </div>,
    document.body,
  );
}

/**
 * The sheet that sits over a photo and stays (rack result, port lookup).
 * Phone: at half height over the picture; the grab line or a drag takes it to
 * full height and back. Laptop: docked over the right side of the stage.
 */
export function DockSheet({ expanded, onExpandedChange, children, label = 'Details', className }) {
  const start = useRef(null);
  const onPointerDown = (e) => { start.current = e.clientY; };
  const onPointerUp = (e) => {
    if (start.current == null) return;
    const dy = e.clientY - start.current;
    start.current = null;
    if (dy < -30) onExpandedChange?.(true);
    else if (dy > 30) onExpandedChange?.(false);
  };
  return (
    <section className={cx(styles.dock, expanded && styles.full, className)} aria-label={label}>
      <button
        type="button"
        className={styles.grabBtn}
        aria-label={expanded ? 'Show less' : 'Show more'}
        aria-expanded={!!expanded}
        onClick={() => onExpandedChange?.(!expanded)}
        onPointerDown={onPointerDown}
        onPointerUp={onPointerUp}
      >
        <span className={styles.grab} />
      </button>
      <div className={styles.dockBody}>{children}</div>
    </section>
  );
}

export default Sheet;
