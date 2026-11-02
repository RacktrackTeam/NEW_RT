import { useState } from 'react';
import { Sheet } from './Sheet.jsx';
import { Button, ButtonPair, TextButton, Actions } from './Button.jsx';
import { Field, SelectField } from './Field.jsx';
import { ErrorLine, Grey } from './Text.jsx';
import { SubTabs } from './Ui.jsx';
import { DevicePicker } from './DevicePicker.jsx';
import { useToast } from './Toast.jsx';
import { MakeModelEdit } from './MakeModel.jsx';
import { postJSON, putJSON, postForm, enc } from '../utils/http';
import { classOptionsExcept, PORT_TYPE_OPTIONS } from '../utils/rack';
import styles from './SomethingWrong.module.css';

const CHOICES = [
  ['device', 'Device details', 'Wrong device type, or make and model'],
  ['unit', 'Rack unit', 'A device shown at the wrong unit'],
  ['port', 'Port status', 'A port read as connected or empty by mistake, or the wrong port type'],
  ['rack', 'Rack name', 'The rack name is missing or wrong'],
  ['other', 'Other issue', 'Describe the issue and attach a photo if needed'],
];

const unitsOf = (d) => (d.unitTop == null ? '' : d.unitBottom != null && d.unitBottom !== d.unitTop ? `U${d.unitBottom}-${d.unitTop} ` : `U${d.unitTop} `);
const typeOf = (p) => (p.type ? String(p.type).toUpperCase() : 'Other');

/**
 * "Report a problem" on a rack result: what is wrong, then the one thing to
 * put right. Every answer goes to the correction routes the rest of the app
 * uses (a device's kind trains the organization's memory unless it chose
 * never to train); "Something else" goes to support with the scan attached.
 *   racks: [{ scanId, label, devices }] - one rack, or the two of a map
 *   onRackName(scanId): open the rack name sheet
 *   onChanged(): read the result again
 */
export function SomethingWrong({ open, onClose, racks = [], onRackName, onChanged }) {
  const toast = useToast();
  const [what, setWhat] = useState(null);
  const [side, setSide] = useState(0);
  const [deviceId, setDeviceId] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [kind, setKind] = useState('');
  const [ptFix, setPtFix] = useState('');
  const [unit, setUnit] = useState('');
  const [ptype, setPtype] = useState(null);
  const [pnum, setPnum] = useState('');
  const [text, setText] = useState('');
  const [photo, setPhoto] = useState(null);
  const rack = racks[Math.min(side, Math.max(0, racks.length - 1))] || { devices: [] };
  const device = (rack.devices || []).find((d) => String(d.deviceId) === String(deviceId)) || null;

  const reset = () => { setWhat(null); setDeviceId(''); setErr(''); setKind(''); setPtFix(''); setUnit(''); setPtype(null); setPnum(''); setText(''); setPhoto(null); };
  const close = () => { reset(); onClose(); };
  const done = (msg = 'Correction saved.') => { toast(msg); if (onChanged) onChanged(); close(); };
  const run = async (fn, msg) => {
    setBusy(true); setErr('');
    try { await fn(); done(msg); } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  const types = device ? [...new Set(device.ports.map(typeOf))] : [];
  const type = ptype && types.includes(ptype) ? ptype : types[0] || null;
  const ofType = device ? device.ports.filter((p) => typeOf(p) === type).sort((a, b) => a.portNo - b.portNo) : [];
  const n = Number(pnum);
  const port = ofType.find((p) => p.portNo === n) || (Number.isInteger(n) && n >= 1 ? ofType[n - 1] : null) || null;

  const devicePicker = (
    <DevicePicker
      label="Device"
      value={deviceId}
      onChange={(v) => { setDeviceId(String(v)); setErr(''); setPnum(''); setPtype(null); }}
      placeholder="Choose a device"
      options={(rack.devices || []).filter((d) => what !== 'port' || d.ports.length)
        .sort((a, b) => (b.unitTop ?? -1) - (a.unitTop ?? -1))
        .map((d) => ({ value: String(d.deviceId), unit: unitsOf(d).trim() || null, name: d.name || d.type, sub: [d.type, /switch/i.test(String(d.type || '')) ? [d.make, d.model].filter(Boolean).join(' ') : null].filter(Boolean).join(' · ') }))}
    />
  );

  return (
    <Sheet open={open} onClose={close} title={what ? CHOICES.find((c) => c[0] === what)[1] : 'Report a problem'}>
      {racks.length > 1 && what !== 'other' ? (
        <SubTabs full label="Which rack" value={side} onChange={(v) => { setSide(v); setDeviceId(''); }} options={racks.map((r, i) => ({ value: i, label: r.label || `Rack ${i + 1}` }))} className={styles.side} />
      ) : null}

      {!what ? (
        <ul className={styles.choices}>
          {CHOICES.map(([k, t, sub]) => (
            <li key={k}>
              <button type="button" className={styles.choice} onClick={() => { if (k === 'rack') { const id = rack.scanId; close(); if (onRackName) onRackName(id); return; } setWhat(k); }}>
                <span className={styles.choiceT}>{t}</span>
                <span className={styles.choiceS}>{sub}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {what === 'device' ? (
        <div className={styles.step}>
          {devicePicker}
          {device ? (
            <>
              <div className={styles.block}>
                <p className={styles.blockT}>Device type <span className={styles.now}>currently {device.type}</span></p>
                <div className={styles.row}>
                  <SelectField label="Correct type" compact value={kind} onChange={(e) => setKind(e.target.value)} placeholder="Choose" options={classOptionsExcept(device.type)} className={styles.grow} />
                  <Button size="auto" busy={busy} disabled={!kind} onClick={() => run(() => postJSON('/api/feedback/device', { scanId: rack.scanId, device_index: device.deviceId, is_correct: false, actual_device_class: kind }))}>Save</Button>
                </div>
              </div>
              {/switch/i.test(String(device.type || '')) ? <div className={styles.block}>
                <p className={styles.blockT}>Make and model <span className={styles.now}>currently {[device.make, device.model].filter(Boolean).join(' ') || 'not read'}</span></p>
                <MakeModelEdit scanId={rack.scanId} deviceId={device.deviceId} make={device.make} model={device.model} onSaved={() => done()} />
              </div> : null}
            </>
          ) : null}
        </div>
      ) : null}

      {what === 'unit' ? (
        <div className={styles.step}>
          {devicePicker}
          {device ? (
            <div className={styles.block}>
              <p className={styles.blockT}>Rack unit <span className={styles.now}>currently {device.unitTop != null ? `U${device.unitTop}` : 'not read'}</span></p>
              <div className={styles.row}>
                <Field label="Top unit" inputMode="numeric" value={unit} onChange={(e) => setUnit(e.target.value.replace(/[^0-9]/g, '').slice(0, 2))} className={styles.grow} />
                <Button size="auto" busy={busy} disabled={!unit} onClick={() => run(() => putJSON(`/api/scans/${enc(rack.scanId)}/devices/${enc(device.deviceId)}/unit`, { unitTop: Number(unit) }))}>Save</Button>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      {what === 'port' ? (
        <div className={styles.step}>
          {devicePicker}
          {device ? (
            <>
              {types.length > 1 ? <SubTabs label="Port type" value={type} onChange={(v) => { setPtype(v); setPnum(''); }} options={types.map((t) => ({ value: t, label: t }))} /> : null}
              <Field label={`${type || ''} port number`.trim()} inputMode="numeric" value={pnum} onChange={(e) => setPnum(e.target.value.replace(/[^0-9]/g, '').slice(0, 3))} />
              {pnum && !port ? <p className={styles.bad}>There is no {type} port {pnum} on this device.</p> : null}
              {port ? (
                <>
                  <div className={styles.block}>
                    <p className={styles.blockT}>Port {port.portNo} is <span className={styles.now}>read as {port.state === 'connected' ? 'plugged in' : port.state === 'empty' ? 'empty' : 'not clear'}</span></p>
                    <ButtonPair>
                      <Button variant="secondary" busy={busy} aria-pressed={port.state === 'connected'} onClick={() => run(() => putJSON(`/api/scans/${enc(rack.scanId)}/ports/state`, { deviceId: device.deviceId, portNo: port.portNo, state: 'connected' }))}>Plugged in</Button>
                      <Button variant="secondary" busy={busy} aria-pressed={port.state === 'empty'} onClick={() => run(() => putJSON(`/api/scans/${enc(rack.scanId)}/ports/state`, { deviceId: device.deviceId, portNo: port.portNo, state: 'empty' }))}>Empty</Button>
                    </ButtonPair>
                  </div>
                  <div className={styles.block}>
                    <p className={styles.blockT}>Port type <span className={styles.now}>read as {port.type || 'not read'}</span></p>
                    <div className={styles.row}>
                      <SelectField label="Correct type" compact value={ptFix} onChange={(e) => setPtFix(e.target.value)} placeholder="Choose" options={PORT_TYPE_OPTIONS.filter((o) => o.value !== port.type)} className={styles.grow} />
                      <Button size="auto" busy={busy} disabled={!ptFix} onClick={() => run(() => postJSON('/api/feedback/port-type', { scanId: rack.scanId, device_index: device.deviceId, port: port.portNo, predicted_type: port.type || null, actual_type: ptFix, port_location: null }))}>Save</Button>
                    </div>
                  </div>
                </>
              ) : null}
            </>
          ) : null}
        </div>
      ) : null}

      {what === 'other' ? (
        <form
          className={styles.step}
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim().length < 5) { setErr('Describe the issue in a few words.'); return; }
            const context = JSON.stringify({ scanIds: racks.map((r) => r.scanId), app: 'rt-eos', from: 'something-wrong' });
            const subject = `Problem reported on ${racks.map((r) => r.label || r.scanId).join(' and ')}`;
            run(async () => {
              if (photo) {
                const form = new FormData();
                form.append('subject', subject);
                form.append('message', `${text.trim()}\n\nScan: ${racks.map((r) => r.scanId).join(', ')}`);
                form.append('context', context);
                form.append('attachments', photo);
                await postForm('/api/support/contact', form);
              } else {
                await postJSON('/api/support/contact', { subject, message: `${text.trim()}\n\nScan: ${racks.map((r) => r.scanId).join(', ')}`, context });
              }
            }, 'Report sent to RackTrack support.');
          }}
          noValidate
        >
          <Field label="Description" multiline rows={4} value={text} onChange={(e) => setText(e.target.value)} />
          <label className={styles.file}>
            <span>{photo ? photo.name : 'Attach a photo'}</span>
            <input type="file" accept="image/*" onChange={(e) => setPhoto(e.target.files?.[0] || null)} />
          </label>
          <Grey m0>The scan is attached to the report.</Grey>
          <Button type="submit" busy={busy}>Send report</Button>
        </form>
      ) : null}

      <ErrorLine>{err}</ErrorLine>
      {what ? <Actions><TextButton small onClick={() => { reset(); }}>Back</TextButton><TextButton small onClick={close}>Cancel</TextButton></Actions> : null}
    </Sheet>
  );
}

export default SomethingWrong;
