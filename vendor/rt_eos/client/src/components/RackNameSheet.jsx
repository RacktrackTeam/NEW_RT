import { useState } from 'react';
import { Button, TextButton, Actions } from './Button.jsx';
import { Sheet } from './Sheet.jsx';
import { ListRow } from './ListRow.jsx';
import { Field } from './Field.jsx';
import { Grey, ErrorLine } from './Text.jsx';
import { postJSON, enc } from '../utils/http';
import styles from './RackNameSheet.module.css';

/**
 * "Which rack is this?" - pick one the site knows, or type its name. Asked
 * only when the server could not name the rack after the analysis (the rail
 * label, the device labels and the layout against the Excel dataset all
 * failed), or when a person taps Change on the name it found.
 */
export function RackNameSheet({ open, onClose, scanId, site, onDone, title = 'Which rack is this?', why = null }) {
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  // A rack the site already has: asked before this scan is saved to it, so
  // an existing rack is never updated by a slip of the finger.
  const [confirm, setConfirm] = useState(null); // { name, payload }
  const racks = (site?.racks || []).filter((r) => r && r.name);
  const known = (typed) => racks.find((r) => String(r.name).trim().toLowerCase() === String(typed).trim().toLowerCase()) || null;
  const close = () => { setConfirm(null); setErr(''); onClose(); };
  const send = async (payload) => {
    setBusy(true); setErr('');
    try {
      let body;
      try {
        body = await postJSON(`/api/scan/${enc(scanId)}/identity/confirm`, payload);
      } catch (e) {
        if (e.status === 409 && e.body?.knownRackId && !payload.knownRackId) {
          // The server knows a rack by that name: ask before saving to it.
          const hit = racks.find((r) => Number(r.id) === Number(e.body.knownRackId));
          setConfirm({ name: hit?.name || payload.name || 'That rack', payload: { knownRackId: Number(e.body.knownRackId) } });
          return;
        }
        throw e;
      }
      setConfirm(null);
      onDone?.(body?.identity || null);
      onClose();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  const pick = (r) => setConfirm({ name: r.name, payload: r.id ? { knownRackId: Number(r.id) } : { name: r.name } });
  const save = () => {
    const typed = name.trim();
    if (!typed) { setErr('Type the rack name, or pick one above.'); return; }
    const hit = known(typed);
    if (hit) pick(hit); else send({ name: typed });
  };
  return (
    <Sheet open={open} onClose={close} title={confirm ? 'This rack already exists' : title}>
      {confirm ? (
        <>
          <p className={styles.confirm}>{`${confirm.name} already exists. Do you want to update it with this scan?`}</p>
          <Grey>Its earlier scans stay in History. This scan becomes its latest reading.</Grey>
          <ErrorLine>{err}</ErrorLine>
          <Actions>
            <Button size="auto" busy={busy} onClick={() => send(confirm.payload)}>Yes, update it</Button>
            <TextButton onClick={() => { setConfirm(null); setErr(''); }}>No, go back</TextButton>
          </Actions>
        </>
      ) : (
        <>
          <Grey>{why || 'Every later reading of this photo is kept against the rack you pick.'}</Grey>
          {racks.map((r) => (
            <ListRow key={r.id || r.name} compact title={r.name} onClick={() => pick(r)} right={<span className={styles.pick}>This one</span>} />
          ))}
          <Field label={racks.length ? 'Or type its name' : 'Rack name'} value={name} onChange={(e) => setName(e.target.value)} />
          <ErrorLine>{err}</ErrorLine>
          <Actions>
            <Button size="auto" busy={busy} onClick={save}>Save</Button>
            <TextButton onClick={close}>Not now</TextButton>
          </Actions>
        </>
      )}
    </Sheet>
  );
}

export default RackNameSheet;
