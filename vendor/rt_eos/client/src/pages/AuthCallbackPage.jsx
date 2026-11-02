import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import styles from './AuthPages.module.css';
import AuthLayout from '../components/AuthLayout.jsx';
import { useAuth } from '../AuthContext.jsx';
import { parseAuthFragment } from '../utils/socialSession';

/**
 * Where the browser lands after a social sign-in. The session rides in the
 * URL fragment; it is read once, wiped from the address bar, and the person
 * goes Home (or back to sign in with the reason).
 */
export default function AuthCallbackPage() {
  const navigate = useNavigate();
  const { adoptSession } = useAuth();
  const handled = useRef(false);
  useEffect(() => {
    if (handled.current) return;
    handled.current = true;
    const result = parseAuthFragment(window.location.hash);
    try { window.history.replaceState(null, '', window.location.pathname); } catch { /* not fatal */ }
    if (!result) { navigate('/login', { replace: true }); return; }
    if (!result.ok) { navigate('/login', { replace: true, state: { socialError: result.message } }); return; }
    adoptSession(result.token, result.user);
    navigate('/', { replace: true });
  }, [adoptSession, navigate]);
  return (
    <AuthLayout>
      <p className={styles.subheading} role="status" aria-live="polite">Signing you in&hellip;</p>
      <span className={styles.spinner} />
    </AuthLayout>
  );
}
