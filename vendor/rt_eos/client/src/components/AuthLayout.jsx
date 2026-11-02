import { useEffect, useRef } from 'react';
import { useIsLaptop } from '../hooks/useIsLaptop';
import Icon from './Icon.jsx';
import styles from '../pages/AuthPages.module.css';

/**
 * The frame every signed-out page except sign in sits in - copied from
 * RackTrack's AuthLayout so the two apps share one look.
 *
 * On a laptop the screen is split 57 / 43: footage of a real datacenter hall
 * on the left, the form on plain white to the right of a hard edge. On a phone
 * the footage drops away and the form is the screen.
 */

/* The glyphs come from the app's one icon set (components/Icon.jsx), drawn
   as RackTrack draws them. */
export const IcEye = ({ off, ...p }) => <Icon name={off ? 'eye_off' : 'eye'} width="18" height="18" strokeWidth="1.6" {...p} />;
export const IcAlert = (p) => <Icon name="alert_circle" width="18" height="18" {...p} />;
export const IcInfo = (p) => <Icon name="info" width="18" height="18" {...p} />;
const IcBack = () => <Icon name="arrow_back" width="18" height="18" strokeWidth="1.7" />;

/**
 * @param {React.ReactNode} children   the form
 * @param {() => void} [onBack]        renders the back arrow when given
 * @param {string} [backLabel]
 * @param {React.ReactNode} [aside]    top-right slot (contextual link)
 */
export default function AuthLayout({ children, onBack, backLabel = 'Back', aside }) {
  // The footage is only on screen at 1024 px and up. A hidden <video> still
  // downloads, so a phone never renders it at all.
  const wide = useIsLaptop();
  // "Reduce motion" gets the poster frame instead of a loop.
  const film = useRef(null);
  useEffect(() => {
    const el = film.current;
    if (!el || !window.matchMedia) return undefined;
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const apply = () => { if (mq.matches) el.pause(); else el.play?.()?.catch?.(() => {}); };
    apply();
    mq.addEventListener?.('change', apply);
    return () => mq.removeEventListener?.('change', apply);
  }, [wide]);

  return (
    <div className={styles.authPage}>
      <div className={styles.media} aria-hidden="true">
        {wide && (
          <video
            ref={film}
            className={styles.mediaImg}
            src="/racktrack-greenrow.mp4"
            poster="/hero-rack.jpg"
            autoPlay
            muted
            loop
            playsInline
            preload="auto"
            tabIndex={-1}
          />
        )}
        <div className={styles.mediaWash} />

        <div className={styles.mediaBrand}>
          <img src="/logo.jpg" alt="" className={styles.mediaMark} />
          <span className={styles.mediaName}>RackTrack</span>
        </div>

        <div className={styles.mediaCopy}>
          <p className={styles.mediaTitle}>Know what&apos;s actually in the rack.</p>
          <p className={styles.mediaSub}>
            A photo returns the rack, its switches and their live ports.
          </p>
        </div>
      </div>

      <section className={styles.formSide}>
        <header className={styles.topBar}>
          {onBack && (
            <button type="button" className={styles.backArrow} onClick={onBack} aria-label={backLabel}>
              <IcBack />
            </button>
          )}
          <div className={styles.brand}>
            <img src="/logo.jpg" alt="" className={styles.brandMark} />
            <span className={styles.brandName}>RackTrack</span>
          </div>
          {aside}
        </header>

        <div className={styles.formScroll}>
          <main className={styles.authShell}>
            {children}
          </main>
        </div>
      </section>
    </div>
  );
}

/** The "already have an account?" line in the top-right slot. */
export function AuthAside({ text, children }) {
  return (
    <div className={styles.asideNote}>
      <span className={styles.asideText}>{text}</span>
      {children}
    </div>
  );
}
