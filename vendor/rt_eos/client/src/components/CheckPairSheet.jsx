import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Sheet } from './Sheet.jsx';
import { Button, TextButton, Actions } from './Button.jsx';
import { SelectField } from './Field.jsx';
import { Grey, ErrorLine } from './Text.jsx';
import { getJSON, postJSON } from '../utils/http';
import { rackNameOf, scanIdOf } from '../utils/rack';
import { whenShort } from '../utils/format';

/**
 * Start an A-end / Z-end label check: pick the two racks (their latest
 * scans), run it, open the result. `aScanId` pre-fills the A-end when the
 * check is started from a rack.
 */
export function CheckPairSheet({ open, onClose, aScanId }) {
  const navigate = useNavigate();
  const [scans, setScans] = useState(null);
  const [a, setA] = useState(aScanId || '');
  const [z, setZ] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return undefined;
    let gone = false;
    setError('');
    setA(aScanId || '');
    getJSON('/api/scans')
      .then((d) => { if (!gone) setScans(Array.isArray(d?.scans) ? d.scans : []); })
      .catch((e) => { if (!gone) { setScans([]); setError(e.message); } });
    return () => { gone = true; };
  }, [open, aScanId]);

  const options = useMemo(() => (scans || []).map((s) => ({
    value: scanIdOf(s),
    label: `${rackNameOf(s)}, ${whenShort(s.takenAt || s.timestamp)}`,
  })), [scans]);

  const run = async () => {
    if (!a || !z) { setError('Pick the rack at each end.'); return; }
    setBusy(true); setError('');
    try {
      const check = await postJSON('/api/checks', { aScanId: a, zScanId: z });
      const id = check?.id ?? check?.check?.id;
      onClose?.();
      if (id != null) navigate(`/checks/${encodeURIComponent(id)}`);
    } catch (e) {
      setError(e.status === 404 ? 'Label checks are not available on this server yet.' : e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Sheet open={open} onClose={onClose} title="Check labels against the Excel dataset">
      <Grey>Pick the rack at each end of the cables. Every fibre label read in both racks is looked up in the Excel dataset.</Grey>
      {scans && !scans.length ? <Grey>No racks scanned yet. Scan both racks first.</Grey> : null}
      <SelectField label="A-end rack" required value={a} onChange={(e) => setA(e.target.value)} options={options} placeholder="Choose a rack" />
      <SelectField label="Z-end rack" required value={z} onChange={(e) => setZ(e.target.value)} options={options} placeholder="Choose a rack" />
      <ErrorLine>{error}</ErrorLine>
      <Actions>
        <Button size="auto" onClick={run} busy={busy}>Run the check</Button>
        <TextButton onClick={onClose}>Cancel</TextButton>
      </Actions>
    </Sheet>
  );
}

export default CheckPairSheet;
