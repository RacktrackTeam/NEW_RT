import { useState } from 'react';
import { Button, TextButton, Actions } from './Button.jsx';
import { SelectField, Field } from './Field.jsx';
import { Grey, ErrorLine } from './Text.jsx';
import { postJSON } from '../utils/http';
import {
  classOptionsExcept, PORT_COUNT_OPTIONS, PORT_TYPE_OPTIONS, CABLE_COLOR_OPTIONS, portTypeWord, portCount,
} from '../utils/rack';
import styles from './Corrections.module.css';

const OTHER = '__other';

/**
 * One reading, one question: "Is this a Switch?" Yes writes a confirmation;
 * No asks for the right answer from a list (with Other for a typed one).
 * onAnswer(isCorrect, value) does the write and may throw a sentence.
 */
export function YesNoQuestion({ question, options, otherLabel, otherType = 'text', onAnswer }) {
  const [state, setState] = useState('ask');
  const [value, setValue] = useState('');
  const [other, setOther] = useState('');
  const [error, setError] = useState('');
  const run = async (ok, v) => {
    setError('');
    setState('saving');
    try { await onAnswer(ok, v); setState('done'); } catch (e) { setError(e.message); setState(ok ? 'ask' : 'no'); }
  };
  if (state === 'done') return <div className={styles.q}><p>{question}</p><Grey m0>Saved. Thank you.</Grey></div>;
  return (
    <div className={styles.q}>
      <p>{question}</p>
      {state === 'no' || (state === 'saving' && value) ? (
        <>
          <SelectField label="The right answer" value={value} onChange={(e) => setValue(e.target.value)} options={[...options, ...(otherLabel ? [{ value: OTHER, label: 'Other' }] : [])]} placeholder="Choose" />
          {value === OTHER ? <Field label={otherLabel} type={otherType} inputMode={otherType === 'number' ? 'numeric' : undefined} value={other} onChange={(e) => setOther(e.target.value)} /> : null}
          <ErrorLine>{error}</ErrorLine>
          <Actions>
            <Button size="sm" busy={state === 'saving'} onClick={() => {
              const v = value === OTHER ? other.trim() : value;
              if (!v) { setError('Choose the right answer first.'); return; }
              run(false, v);
            }}>Save</Button>
            <TextButton small onClick={() => { setState('ask'); setValue(''); }}>Cancel</TextButton>
          </Actions>
        </>
      ) : (
        <>
          <ErrorLine>{error}</ErrorLine>
          <Actions className={styles.yn}>
            <TextButton onClick={() => run(true, null)} disabled={state === 'saving'}>Yes</TextButton>
            <TextButton onClick={() => setState('no')} disabled={state === 'saving'}>No</TextButton>
          </Actions>
        </>
      )}
    </div>
  );
}

/**
 * Correct what was read about a device: its kind and its port count. The
 * same writes the results screen always made (feedback/device and
 * feedback/port-count); the page is told so it can show the change at once.
 */
export function DeviceCorrections({ scanId, device, onChanged }) {
  if (!device) return null;
  const n = portCount(device, 'main');
  const answerClass = async (ok, cls) => {
    await postJSON('/api/feedback/device', { scanId, device_index: device.index, is_correct: ok, actual_device_class: ok ? null : cls });
    if (!ok) onChanged?.({ class_name: cls, class_name_source: 'user_corrected' });
  };
  const answerCount = async (ok, v) => {
    const actual = ok ? null : parseInt(v, 10);
    if (!ok && (!Number.isFinite(actual) || actual < 0)) throw new Error('Enter a port count of 0 or more.');
    const r = await postJSON('/api/feedback/port-count', { scanId, device_index: device.index, is_correct: ok, actual_port_count: actual });
    if (r?.relabel?.ok && r.relabel.device) onChanged?.(r.relabel.device);
    else if (!ok) onChanged?.({ port_count: actual });
  };
  return (
    <>
      <YesNoQuestion
        question={<>Is this a <b>{device.class_name}</b>?</>}
        options={classOptionsExcept(device.class_name)}
        otherLabel="What is it?"
        onAnswer={answerClass}
      />
      <YesNoQuestion
        question={n > 0 ? <>Does it have <b>{n}</b> ports?</> : <>How many ports does it have? None were counted.</>}
        options={PORT_COUNT_OPTIONS.filter((c) => c !== n).map((c) => ({ value: String(c), label: `${c} ports` }))}
        otherLabel="Number of ports"
        otherType="number"
        onAnswer={answerCount}
      />
    </>
  );
}

/**
 * Correct what was read about one port: its number, its type, and the
 * colour of the cable in it.
 */
export function PortCorrections({ scanId, device, portNo, portInfo, category = 'main', onChanged }) {
  if (!device || !portNo) return null;
  const n = portCount(device, category) || portCount(device, 'main');
  const numbers = Array.from({ length: Math.max(0, n) }, (_, i) => i + 1).filter((k) => k !== Number(portNo)).map((k) => ({ value: String(k), label: `Port ${k}` }));
  const answerNumber = async (ok, v) => {
    const actual = ok ? null : parseInt(v, 10);
    if (!ok && (!Number.isFinite(actual) || actual < 1 || (n > 0 && actual > n))) throw new Error(n > 0 ? `Port must be between 1 and ${n}.` : 'Enter a port number.');
    await postJSON('/api/feedback', { scanId, device_index: device.index, predicted_port: Number(portNo), is_correct: ok, actual_port: actual, actual_cable_color: null });
    if (!ok) onChanged?.({ kind: 'number', value: actual });
  };
  const answerType = async (ok, v) => {
    if (ok) {
      await postJSON('/api/feedback/port-type', { scanId, device_index: device.index, port: Number(portNo), predicted_type: portInfo?.port_type || null, actual_type: portInfo?.port_type || null, port_location: portInfo?.location || null });
      return;
    }
    await postJSON('/api/feedback/port-type', { scanId, device_index: device.index, port: Number(portNo), predicted_type: portInfo?.port_type || null, actual_type: v, port_location: portInfo?.location || null });
    onChanged?.({ kind: 'type', value: v });
  };
  const answerColor = async (ok, v) => {
    await postJSON('/api/feedback', { scanId, device_index: device.index, predicted_port: Number(portNo), is_correct: ok, actual_port: null, actual_cable_color: ok ? null : v });
    if (!ok) onChanged?.({ kind: 'color', value: v });
  };
  return (
    <>
      <YesNoQuestion question={<>Is this port <b>{portNo}</b>?</>} options={numbers} otherLabel="Port number" otherType="number" onAnswer={answerNumber} />
      {portInfo?.port_type ? (
        <YesNoQuestion question={<>Is it a <b>{portTypeWord(portInfo.port_type)}</b> port?</>} options={PORT_TYPE_OPTIONS.filter((o) => o.value !== portInfo.port_type)} onAnswer={answerType} />
      ) : (
        <YesNoQuestion question="What type of port is it? The type was not read." options={PORT_TYPE_OPTIONS} onAnswer={(ok, v) => (ok ? Promise.resolve() : answerType(false, v))} />
      )}
      {portInfo?.status === 'connected' ? (
        <YesNoQuestion
          question={portInfo.cable_color ? <>Is the cable <b>{portInfo.cable_color}</b>?</> : 'What colour is the cable?'}
          options={CABLE_COLOR_OPTIONS.filter((c) => c.toLowerCase() !== String(portInfo.cable_color || '').toLowerCase())}
          otherLabel="Colour"
          onAnswer={answerColor}
        />
      ) : null}
    </>
  );
}
