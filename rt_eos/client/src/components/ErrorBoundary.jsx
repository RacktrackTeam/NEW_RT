import { Component } from 'react';
import { apiUrl } from '../utils/api';
import styles from './ErrorBoundary.module.css';

/**
 * Catches a screen that failed to draw, says so in one sentence and offers
 * the way out. `resetKey` (the path) clears it when the person moves on.
 * The error is reported to the server so a failing screen is seen.
 */
export class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    try {
      fetch(apiUrl('/api/client-error'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: String(error?.message || error).slice(0, 500),
          stack: String(error?.stack || '').slice(0, 4000),
          component: String(info?.componentStack || '').slice(0, 2000),
          path: typeof window !== 'undefined' ? window.location.pathname : '',
        }),
        keepalive: true,
      }).catch(() => {});
    } catch { /* reporting is best effort */ }
  }

  componentDidUpdate(prev) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  render() {
    if (!this.state.error) return this.props.children;
    const chunk = /Loading chunk|dynamically imported module|Importing a module script failed/i.test(String(this.state.error?.message || ''));
    return (
      <main className={styles.box} role="alert">
        <h1 className={styles.t}>This screen could not open</h1>
        <p className={styles.g}>
          {chunk ? 'A newer version of RackTrack is available. Reload to get it.' : 'Something went wrong while drawing it. Reload, or go back to the start.'}
        </p>
        <div className={styles.acts}>
          <button type="button" className={styles.btn} onClick={() => window.location.reload()}>Reload</button>
          <a className={styles.lk} href="/">Go to the start</a>
        </div>
      </main>
    );
  }
}

export default ErrorBoundary;
