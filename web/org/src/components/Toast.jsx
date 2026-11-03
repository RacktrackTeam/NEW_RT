import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import styles from './Toast.module.css';

const ToastContext = createContext(() => {});

/** One line at the bottom, gone after 3 seconds. */
export function ToastProvider({ children }) {
  const [text, setText] = useState('');
  const timer = useRef(null);
  const show = useCallback((t, ms = 3000) => {
    setText(String(t || ''));
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setText(''), ms);
  }, []);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className={styles.region} role="status" aria-live="polite">
        {text ? <div className={styles.toast}>{text}</div> : null}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}

export default ToastProvider;
