import { useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import s from './SignInPage.module.css';
import { IcAlert, IcEye } from '../components/AuthLayout.jsx';
import { useAuth } from '../AuthContext.jsx';
import { safeRedirect } from '../utils/safeRedirect';

export const SUPPORT_EMAIL = 'support@racktrack.ai';

/**
 * Sign in: logo, name, two fields, one button - RackTrack's login screen, the
 * same layout, styles and words. Accounts are issued; there is no sign-up.
 * The sign-in itself is RT-EOS's own (AuthContext.login).
 */
export default function SignInPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { login, loading } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPw, setShowPw] = useState(false);
  // A failed social sign-in lands here with its reason.
  const [error, setError] = useState(location.state?.socialError || null);

  // Everybody lands on Home; a deep link that bounced here is still honoured.
  const from = safeRedirect(location.state?.from, '/');

  const submit = async (e) => {
    e?.preventDefault();
    setError(null);
    if (!username.trim() || !password) {
      setError('Enter your username and password.');
      return;
    }
    try {
      await login(username.trim(), password, '');
      navigate(from, { replace: true });
    } catch (err) {
      setError(err.message);
    }
  };

  return (
    <div className={s.page}>
      <main className={s.stack}>
        <img src="/logo.jpg" alt="" className={s.logo} width="108" height="108" />
        <h1 className={s.brand}>RackTrack</h1>

        <form className={s.form} onSubmit={submit} autoComplete="on" noValidate>
          <div className={s.field}>
            <input
              className={s.input}
              type="text"
              placeholder="Username or email"
              aria-label="Username or email"
              autoComplete="username"
              autoCapitalize="none"
              autoCorrect="off"
              value={username}
              onChange={(e) => { setUsername(e.target.value); setError(null); }}
            />
          </div>

          <div className={s.field}>
            <input
              className={`${s.input} ${s.inputPw}`}
              type={showPw ? 'text' : 'password'}
              placeholder="Password"
              aria-label="Password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => { setPassword(e.target.value); setError(null); }}
            />
            <button
              type="button"
              className={s.eye}
              onClick={() => setShowPw((v) => !v)}
              aria-label={showPw ? 'Hide password' : 'Show password'}
            >
              <IcEye off={showPw} />
            </button>
          </div>

          {error && (
            <div className={s.err} role="alert"><IcAlert width="14" height="14" />{error}</div>
          )}

          <button type="submit" className={s.cta} disabled={loading}>
            {loading && <span className={s.spinner} />}
            <span>Get started</span>
          </button>
        </form>

        <div className={s.links}>
          <Link
            to="/forgot-password"
            state={{ email: username.includes('@') ? username.trim() : '', from: location.state?.from }}
          >
            Forgot password?
          </Link>
        </div>
      </main>

      {/* RackTrack is invitation-only: an account is created for you by an
          administrator, never by signing up. */}
      <footer className={s.foot}>
        No account? Contact your administrator.
        <br />
        <a href={`mailto:${SUPPORT_EMAIL}?subject=RackTrack%20access%20request`}>
          {SUPPORT_EMAIL}
        </a>
      </footer>
    </div>
  );
}
