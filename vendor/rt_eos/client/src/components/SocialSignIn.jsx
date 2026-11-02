import { useEffect, useState } from 'react';
import { Capacitor } from '@capacitor/core';
import { Browser } from '@capacitor/browser';
import { apiUrl } from '../utils/api';
import styles from '../pages/AuthPages.module.css';

/**
 * The social sign-in buttons, copied from RackTrack. Which providers appear is
 * decided by the server (/api/auth/providers); nothing renders when none are
 * configured. Native opens a real in-app browser tab, the web hands the page
 * to the provider. Google stays hidden, as in RackTrack. The provider logos
 * are left out: RT-EOS draws no pictures in SVG (utils/noSvg.test.js).
 */
const HIDDEN_PROVIDERS = new Set(['google']);

/**
 * @param {'login'|'invite'} mode
 * @param {string} [inviteCode]  required when mode === 'invite'
 * @param {(providers: Array) => void} [onLoaded]  told which providers exist,
 *        so the parent can tailor its copy (e.g. the failed-password hint)
 */
export default function SocialSignIn({ mode = 'login', inviteCode, onLoaded }) {
  const [providers, setProviders] = useState([]);
  const [busy, setBusy] = useState(null);

  useEffect(() => {
    let cancelled = false;
    fetch(apiUrl('/api/auth/providers'))
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled || !d?.providers) return;
        const shown = d.providers.filter((p) => !HIDDEN_PROVIDERS.has(p.name));
        setProviders(shown);
        // The parent tailors its copy from this ("you may have signed up with
        // Google"), so it must be told what is actually on screen, not what the
        // server offers.
        onLoaded?.(shown);
      })
      // A server that doesn't know this route yet, or is simply unreachable,
      // just means no buttons. Never block the password form on it.
      .catch(() => {});
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Native only: the user swiped the browser tab away without finishing. There
  // is no deep link in that case, so nothing else would ever clear the spinner.
  useEffect(() => {
    if (!Capacitor.isNativePlatform()) return;
    let sub;
    (async () => {
      sub = await Browser.addListener('browserFinished', () => setBusy(null));
    })();
    return () => { sub?.remove?.(); };
  }, []);

  if (!providers.length) return null;

  const start = async (name) => {
    setBusy(name);
    const native = Capacitor.isNativePlatform();
    const q = new URLSearchParams({ platform: native ? 'native' : 'web', mode });
    if (mode === 'invite' && inviteCode) q.set('invite', inviteCode);
    const url = apiUrl(`/api/auth/oauth/${name}/start?${q}`);

    if (native) {
      // An in-app browser tab (SFSafariViewController / Chrome Custom Tabs).
      // It is dismissed by the appUrlOpen handler in App.jsx once the server
      // redirects to our custom scheme.
      try {
        await Browser.open({ url, presentationStyle: 'popover' });
      } catch {
        setBusy(null);
      }
      // Leave `busy` set: the tab is covering the app, and clearing it would
      // just flash the button back to life underneath. The handler that adopts
      // the session navigates away; a cancelled tab is caught below.
      return;
    }
    window.location.assign(url);
  };

  return (
    <div className={styles.socialBlock}>
      {/* Two providers sit side by side and one spans the row - the grid
          decides, so adding a third on the server needs nothing here. */}
      <div className={styles.socialGrid}>
        {providers.map((p) => (
          <button
            key={p.name}
            type="button"
            className={styles.socialBtn}
            onClick={() => start(p.name)}
            disabled={!!busy}
          >
            {busy === p.name ? <span className={styles.socialIcon}><span className={styles.socialSpinner} /></span> : null}
            <span>Sign in with {p.label}</span>
          </button>
        ))}
      </div>
      <div className={styles.socialDivider}><span>or</span></div>
    </div>
  );
}
