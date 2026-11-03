import { backUrl } from '../utils/orgOnly';
import styles from './BackToApp.module.css';
import { EmptyState } from './Text.jsx';

/* The top of every organization page in the /org/ build: the way back to the app, top left. */
export function BackToApp() {
  return (
    <div className={styles.bar}>
      <a className={styles.back} href={backUrl()}>Back to RackTrack</a>
    </div>
  );
}

/* Where the full RT-EOS app would show Home, Scan, Map or History: go back to the app instead. */
export function ToApp() {
  if (typeof window !== 'undefined') window.location.replace(backUrl());
  return null;
}

/* A page the person's role cannot open, in the /org/ build: say so, with the way back (never a silent jump or a blank page). */
export function Refused() {
  return <EmptyState text="Your role cannot open this page. Ask your organization admin if you need it." action={<a className={styles.back} href={backUrl()}>Back to RackTrack</a>} />;
}

export default BackToApp;
