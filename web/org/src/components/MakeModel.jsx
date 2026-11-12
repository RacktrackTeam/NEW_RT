import { useEffect, useId, useState } from 'react';
import { Button, TextButton } from './Button.jsx';
import { Field } from './Field.jsx';
import { ErrorLine } from './Text.jsx';
import { useToast } from './Toast.jsx';
import { getJSON, putJSON, enc } from '../utils/http';
import styles from './MakeModel.module.css';

let vendorCache = null;
function useVendors() {
  const [list, setList] = useState(vendorCache || []);
  useEffect(() => {
    if (vendorCache) return undefined;
    let gone = false;
    getJSON('/api/devices/vendors').then((d) => {
      vendorCache = Array.isArray(d?.vendors) ? d.vendors : [];
      if (!gone) setList(vendorCache);
    }).catch(() => {});
    return () => { gone = true; };
  }, []);
  return list;
}

/**
 * Add the make and model of a device by hand, or correct them. The make is
 * picked from the makers the reader knows, or typed. Saving keeps it on the
 * scan (a person's reading from then on) and the screens read it again.
 *   prompt: true shows the "not read - add them" line first
 */
export function MakeModelEdit({ scanId, deviceId, make, model, onSaved, prompt = false, onCancel }) {
  const toast = useToast();
  const vendors = useVendors();
  const listId = useId();
  const [open, setOpen] = useState(!prompt);
  const [mk, setMk] = useState(make || '');
  const [md, setMd] = useState(model || '');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  useEffect(() => { setMk(make || ''); setMd(model || ''); }, [make, model]);

  const save = async (e) => {
    e.preventDefault();
    if (!mk.trim() && !md.trim()) { setErr('Enter the make, the model or both.'); return; }
    setBusy(true); setErr('');
    try {
      const out = await putJSON(`/api/scans/${enc(scanId)}/devices/${enc(deviceId)}/make-model`, { make: mk.trim(), model: md.trim() });
      toast('Saved. The make and model are yours from now on.');
      setOpen(!prompt ? true : false);
      if (onSaved) onSaved(out);
    } catch (x) { setErr(x.message); } finally { setBusy(false); }
  };

  if (!open) {
    return (
      <div className={styles.prompt}>
        <p className={styles.promptT}>{!make && !model ? 'Make and model not read' : !model ? 'Model not read' : 'Make not read'} - add them</p>
        <Button size="sm" onClick={() => setOpen(true)}>Add make and model</Button>
      </div>
    );
  }
  return (
    <form className={styles.form} onSubmit={save} noValidate>
      <Field label="Make" value={mk} onChange={(e) => setMk(e.target.value)} list={listId} autoComplete="off" placeholder="Pick or type, for example Cisco" />
      <datalist id={listId}>
        {vendors.map((v) => <option key={v} value={v} />)}
      </datalist>
      <Field label="Model" value={md} onChange={(e) => setMd(e.target.value)} autoComplete="off" spellCheck="false" placeholder="As printed on the device" />
      <ErrorLine>{err}</ErrorLine>
      <div className={styles.acts}>
        <Button type="submit" size="sm" busy={busy}>Save</Button>
        <TextButton small onClick={() => { if (prompt) setOpen(false); if (onCancel) onCancel(); }}>Cancel</TextButton>
      </div>
    </form>
  );
}
