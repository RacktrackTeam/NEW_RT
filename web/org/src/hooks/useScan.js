import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { getJSON, enc } from '../utils/http';
import { listDevices } from '../utils/rack';
import { useScanSite } from './useScanSite';

/**
 * One scan as the rack screens need it: the reading, its devices (with the
 * labels read off them), which rack it is and which site it stands in.
 * A result handed over by the scan screen shows at once and is refreshed
 * from the server underneath.
 */
export function useScan(scanId) {
  const location = useLocation();
  const handed = location.state?.result && String(location.state.result.rackId || location.state.result.scanId) === String(scanId)
    ? location.state.result : null;
  const [result, setResult] = useState(handed);
  const [error, setError] = useState(null);
  const [identity, setIdentity] = useState(null);
  const [labels, setLabels] = useState(null);
  // The work after the first answer (reading the labels, naming the rack,
  // its height) runs on the server in the background. While it does, the
  // result shows what it has and asks GET /api/scan/:id/settle; when it is
  // done, `settled` counts up and everything is read again.
  const [settling, setSettling] = useState(!!handed?.settling);
  const [settled, setSettled] = useState(0);
  const wasSettling = useRef(!!handed?.settling);
  const { sites } = useScanSite();

  useEffect(() => {
    let gone = false;
    setError(null);
    getJSON(`/api/scan/${enc(scanId)}`)
      .then((d) => { if (!gone) setResult(d); })
      .catch((e) => { if (!gone) setError(e); });
    return () => { gone = true; };
  }, [scanId, settled]);

  useEffect(() => {
    let gone = false;
    let timer = null;
    let tries = 0;
    const ask = async () => {
      try {
        const s = await getJSON(`/api/scan/${enc(scanId)}/settle`);
        if (gone) return;
        if (s?.state === 'running' && tries < 240) {
          wasSettling.current = true;
          setSettling(true);
          tries += 1;
          timer = setTimeout(ask, 1500);
          return;
        }
        setSettling(false);
        if (wasSettling.current) { wasSettling.current = false; setSettled((n) => n + 1); }
      } catch { if (!gone) setSettling(false); }
    };
    ask();
    return () => { gone = true; if (timer) clearTimeout(timer); };
  }, [scanId]);

  // The server names the rack before the result opens (the rail label, the
  // device labels or the layout against the Excel dataset). A scan read before
  // that existed has no naming yet, so the answer is asked again a few times
  // until it settles.
  useEffect(() => {
    let gone = false;
    let timer = null;
    const waits = [2500, 4000, 6000, 8000];
    let n = 0;
    const ask = async () => {
      try {
        const d = await getJSON(`/api/scan/${enc(scanId)}/identity`);
        if (gone) return;
        setIdentity(d);
        const settled = !!d?.naming || ['matched', 'suggested', 'ambiguous'].includes(d?.decision) || (d?.evidence?.labels || []).length > 0;
        if (!settled && n < waits.length) { timer = setTimeout(ask, waits[n]); n += 1; }
      } catch { /* the rack stays unnamed */ }
    };
    ask();
    return () => { gone = true; if (timer) clearTimeout(timer); };
  }, [scanId, settled]);

  useEffect(() => {
    let gone = false;
    getJSON(`/api/ocr/labels/${enc(scanId)}`).then((d) => { if (!gone) setLabels(d); }).catch(() => {});
    return () => { gone = true; };
  }, [scanId, settled]);

  const devices = useMemo(() => listDevices(result?.devices || [], labels), [result, labels]);

  // How the server named the rack after the analysis: { name, method, words,
  // confidence, ask }. `ask` is the one thing that opens "Which rack is this?".
  const naming = identity?.naming || result?.rackNaming || null;
  // Asked only once the ladder has finished: a rack still being named is not unknown.
  const askName = !settling && !identity?.rack && !!naming?.ask;

  const rackName = useMemo(() => {
    const r = identity?.rack;
    const suggested = identity?.decision === 'suggested' ? (identity.candidates || [])[0] : null;
    const one = r || suggested;
    return one?.facilityId || one?.name || (naming && !naming.ask ? naming.name : null) || result?.rackName || result?.rackLabel || null;
  }, [identity, result, naming]);

  const site = useMemo(() => {
    for (const s of sites || []) {
      if ((s.racks || []).some((r) => r && String(r.rackId) === String(scanId))) return s;
    }
    return sites?.length === 1 ? sites[0] : null;
  }, [sites, scanId]);

  const patchDevice = useCallback((index, patch) => {
    setResult((prev) => {
      if (!prev) return prev;
      const list = (prev.devices || []).map((d, i) => (i + 1 === index ? { ...d, ...(typeof patch === 'function' ? patch(d) : patch) } : d));
      return { ...prev, devices: list };
    });
  }, []);

  return {
    result, error, devices, identity, setIdentity, rackName, confirmed: !!identity?.rack, naming, askName,
    identityLoaded: !!identity, settling, settled,
    site, siteName: site?.name || identity?.siteName || null, sites, patchDevice,
  };
}

export default useScan;
