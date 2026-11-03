import { backUrl } from '../utils/orgOnly';
import styles from './BackToApp.module.css';

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

export default BackToApp;
