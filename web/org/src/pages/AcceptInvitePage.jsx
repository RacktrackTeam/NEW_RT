import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Capacitor } from '@capacitor/core';
import styles from './AuthPages.module.css';
import AuthLayout, { AuthAside, IcAlert, IcEye } from '../components/AuthLayout.jsx';
import { nextPasswordRule } from '../components/PasswordFields.jsx';
import { apiUrl } from '../utils/api';
import { setItem } from '../utils/safeStorage';
import { roleWord } from '../utils/format';

/** Create an account from an invite link - RackTrack's invite screen - then land on Home. */
export default function AcceptInvitePage() {
  const { code } = useParams();
  const [invite, setInvite] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch(apiUrl(`/api/invites/${encodeURIComponent(code)}`))
      .then((r) => r.json().catch(() => ({})).then((d) => ({ ok: r.ok, d })))
      .then(({ ok, d }) => {
        if (cancelled) return;
        if (ok && d.invite) {
          setInvite(d.invite);
          const local = String(d.invite.email || '').split('@')[0].replace(/[^A-Za-z0-9._-]/g, '');
          setUsername((u) => u || local);
        } else setErr(d.error || 'This invite is invalid.');
      })
      .catch(() => { if (!cancelled) setErr('Could not load this invite.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [code]);

  const submit = async (e) => {
    e.preventDefault();
    setErr(null);
    if (!username.trim()) { setErr('Choose a username.'); return; }
    if (!password) { setErr('Create a password.'); return; }
    const rule = nextPasswordRule(password);
    if (rule) { setErr(`${rule}.`); return; }
    setBusy(true);
    try {
      const r = await fetch(apiUrl(`/api/invites/${encodeURIComponent(code)}/accept`), {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          ...(Capacitor.isNativePlatform() ? { 'X-Client-Platform': 'native' } : {}),
        },
        body: JSON.stringify({ username: username.trim(), password }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || 'Could not accept the invite.');
      if (d.token) setItem('rt_authToken', d.token);
      if (d.user) setItem('rt_authUser', JSON.stringify(d.user));
      // A full load so the session is read fresh, straight into Home.
      window.location.assign('/');
    } catch (ex) { setErr(ex.message); setBusy(false); }
  };

  const org = String(invite?.organization?.name || invite?.organization || 'your organization');
  const site = invite?.site?.name || invite?.site || null;

  return (
    <AuthLayout
      aside={(
        <AuthAside text="Already have an account?">
          <Link to="/login" className={styles.asideLink}>Sign in</Link>
        </AuthAside>
      )}
    >
      {loading ? (
        <p className={styles.subheading}>Loading your invite&hellip;</p>
      ) : !invite ? (
        <>
          <h1 className={styles.heading}>Invite unavailable</h1>
          <p className={styles.subheading}>{err || 'This invite is invalid or has expired.'}</p>
          <div className={styles.altRow}>
            <Link to="/login" className={styles.altLink}>Go to sign in</Link>
          </div>
        </>
      ) : (
        <>
          <h1 className={styles.heading}>You&apos;re invited</h1>
          <p className={styles.subheading}>
            Join <b>{org}</b>{site ? <> · {String(site)}</> : null} as {roleWord(invite.role)}.
          </p>


          <form className={styles.form} onSubmit={submit} autoComplete="on" noValidate>
            <div className={styles.field}>
              <label className={styles.label} htmlFor="inv-email">Email</label>
              <div className={styles.inputWrap}>
                <input id="inv-email" className={styles.input} value={invite.email || ''} disabled readOnly />
              </div>
            </div>
            <div className={styles.field}>
              <label className={styles.label} htmlFor="inv-user">Choose a username</label>
              <div className={styles.inputWrap}>
                <input
                  id="inv-user"
                  autoFocus
                  className={styles.input}
                  type="text"
                  autoComplete="username"
                  autoCapitalize="none"
                  autoCorrect="off"
                  value={username}
                  onChange={(e) => { setUsername(e.target.value); setErr(null); }}
                />
              </div>
            </div>
            <div className={styles.field}>
              <label className={styles.label} htmlFor="inv-pw">Create a password</label>
              <div className={styles.inputWrap}>
                <input
                  id="inv-pw"
                  className={`${styles.input} ${styles.inputPw}`}
                  type={showPw ? 'text' : 'password'}
                  autoComplete="new-password"
                  value={password}
                  onChange={(e) => { setPassword(e.target.value); setErr(null); }}
                />
                <button
                  type="button"
                  className={styles.eyeBtn}
                  onClick={() => setShowPw((v) => !v)}
                  aria-label={showPw ? 'Hide password' : 'Show password'}
                >
                  <IcEye off={showPw} />
                </button>
              </div>
            </div>
            {err && (
              <div className={styles.errBox} role="alert"><IcAlert width="14" height="14" />{err}</div>
            )}
            <button type="submit" className={styles.primaryBtn} disabled={busy}>
              {busy && <span className={styles.spinner} />}
              <span>{busy ? 'Joining…' : `Join ${org}`}</span>
            </button>
          </form>
        </>
      )}
    </AuthLayout>
  );
}
