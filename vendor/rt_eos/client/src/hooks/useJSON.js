import { useCallback, useEffect, useRef, useState } from 'react';
import { getJSON } from '../utils/http';

/**
 * Read one JSON route for a screen.
 *
 * `path` null means "not yet" (a screen waiting on an id). `fallback`, when
 * given, is called with the error and may return data to show instead - used
 * while a contract route is still being built on the server.
 */
export function useJSON(path, { fallback, deps = [] } = {}) {
  const [state, setState] = useState({ data: null, error: null, loading: !!path });
  const [tick, setTick] = useState(0);
  const fallbackRef = useRef(fallback);
  fallbackRef.current = fallback;

  useEffect(() => {
    if (!path) { setState({ data: null, error: null, loading: false }); return undefined; }
    let gone = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    getJSON(path)
      .then((data) => { if (!gone) setState({ data, error: null, loading: false }); })
      .catch(async (error) => {
        if (gone) return;
        if (fallbackRef.current) {
          try {
            const data = await fallbackRef.current(error);
            if (!gone && data !== undefined) { setState({ data, error: null, loading: false, fellBack: true }); return; }
          } catch (e2) {
            if (!gone) { setState({ data: null, error: e2, loading: false }); return; }
          }
        }
        if (!gone) setState({ data: null, error, loading: false });
      });
    return () => { gone = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, tick, ...deps]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  const setData = useCallback((fn) => setState((s) => ({ ...s, data: typeof fn === 'function' ? fn(s.data) : fn })), []);
  return { ...state, reload, setData };
}

export default useJSON;
