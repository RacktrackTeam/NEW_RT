import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { TopBar } from '../components/TopBar.jsx';
import { Button, TextButton, Links, Actions } from '../components/Button.jsx';
import { DockSheet, Sheet } from '../components/Sheet.jsx';
import { Field } from '../components/Field.jsx';
import { DevicePicker } from '../components/DevicePicker.jsx';
import { Seg } from '../components/Controls.jsx';
import { PhotoWithReadings } from '../components/PhotoWithReadings.jsx';
import { AssetImg } from '../components/AssetImg.jsx';
import { DriftLine } from '../components/Drift.jsx';
import { DeviceCorrections, PortCorrections } from '../components/Corrections.jsx';
import { SubTitle, Grey, Facts, ErrorLine, Loading, EmptyState } from '../components/Text.jsx';
import { useToast } from '../components/Toast.jsx';
import useScan from '../hooks/useScan';
import { postJSON, enc, getJSON } from '../utils/http';
import { apiUrl, bustUrl } from '../utils/api';
import { shareBlob } from '../utils/exportApi';
import { categoriesOf, deviceName, deviceTag, isPortDevice, photoOf, portCount, portTypeWord } from '../utils/rack';
import styles from './PortPage.module.css';

const STATE_WORD = { connected: 'Cable plugged in', empty: 'Empty', unknown: 'Not clear on the photo', invalid: 'Not a port' };

/** Whole numbers only, inside the device's count. Returns a sentence or null. */
export function portProblem(raw, max) {
  const p = parseInt(raw, 10);
  if (!String(raw || '').trim() || Number.isNaN(p) || p < 1 || String(p) !== String(raw).trim()) return 'Enter a whole port number.';
  if (max === 0) return 'The number of ports on this device was not read. Set the port count first.';
  if (max > 0 && p > max) return `This device has ${max} ports. Enter a number from 1 to ${max}.`;
  return null;
}

/**
 * Port lookup. The device front is the picture, with the port ringed; the
 * answer and the box for the next port are in the sheet. Tap another port on
 * the picture to look that one up.
 */
export default function PortPage() {
  const { scanId } = useParams();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const toast = useToast();
  const scan = useScan(scanId);
  const { devices, rackName, result, error: scanError } = scan;
  const portDevices = useMemo(() => devices.filter(isPortDevice), [devices]);
  const deviceIdx = Number(params.get('device')) || null;
  const device = portDevices.find((d) => d.index === deviceIdx) || null;
  const cats = useMemo(() => (device ? categoriesOf(device) : []), [device]);
  const [cat, setCat] = useState('main');
  const [portNo, setPortNo] = useState('');
  const [next, setNext] = useState('');
  const [found, setFound] = useState(null);
  const [view, setView] = useState('device');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [expanded, setExpanded] = useState(false);
  const [fix, setFix] = useState(null);
  const [drift, setDrift] = useState(null);
  const max = device ? portCount(device, cat) : 0;

  useEffect(() => {
    if (device && cats.length && !cats.some((c) => c.value === cat)) setCat(cats[0].value);
  }, [device, cats, cat]);
  useEffect(() => { setFound(null); setError(''); setPortNo(''); }, [deviceIdx]);

  // Phone back steps out of a found port, then out of the device.
  useEffect(() => {
    const onBack = (e) => {
      if (found) { e.preventDefault(); setFound(null); return; }
      if (deviceIdx) { e.preventDefault(); setParams({}, { replace: true }); }
    };
    window.addEventListener('rt:back', onBack);
    return () => window.removeEventListener('rt:back', onBack);
  }, [found, deviceIdx, setParams]);

  // The drift check of a port, worked out by the server from the same
  // reading and the Excel (lib/drift.js): no second lookup here.
  const loadDrift = (p) => getJSON(`/api/scans/${enc(scanId)}/detail`)
    .then((r) => {
      const dev = (r?.devices || []).find((x) => x.deviceId === device.index);
      const port = dev?.ports?.find((x) => x.portNo === p);
      setDrift(port?.drift || null);
    })
    .catch(() => {});

  const lookup = async (raw) => {
    const problem = portProblem(raw, max);
    if (problem) { setError(problem); return; }
    const p = parseInt(raw, 10);
    setBusy(true); setError('');
    try {
      const d = await postJSON('/api/select', { scanId, device_index: device.index, port: p, port_category: cat });
      setFound({
        port: p,
        info: d.portInfo || null,
        img: d.resultImageUrl ? bustUrl(d.resultImageUrl) : null,
        rack: d.rackImageUrl ? bustUrl(d.rackImageUrl) : null,
      });
      setPortNo(String(p));
      setNext('');
      setView('device');
      setDrift(null);
      loadDrift(p);
    } catch (e) {
      setError(e.body?.portOutOfRange ? `This device has ${e.body.detectedCount} ports. Enter a number from 1 to ${e.body.detectedCount}.` : e.message);
    } finally {
      setBusy(false);
    }
  };

  const share = async () => {
    const src = view === 'rack' && found?.rack ? found.rack : found?.img;
    if (!src) return;
    try {
      const res = await fetch(apiUrl(src));
      if (!res.ok) throw new Error('The picture could not be read. Try again.');
      const blob = await res.blob();
      const out = await shareBlob(blob, `${String(rackName || scanId).replace(/[^A-Za-z0-9_-]/g, '_')}-port-${found.port}.${/png/i.test(blob.type) ? 'png' : 'jpg'}`);
      if (out?.text) toast(out.text);
    } catch (e) { toast(e.message); }
  };

  const onFixed = (change) => {
    if (change.kind === 'number') { lookup(String(found.port)); return; }
    if (change.kind === 'type') setFound((f) => ({ ...f, info: { ...f.info, port_type: change.value, _port_type_user: true } }));
    if (change.kind === 'color') setFound((f) => ({ ...f, info: { ...f.info, cable_color: change.value } }));
    if (change.kind === 'type' || change.kind === 'color') loadDrift(found.port);
  };

  const title = found ? `Port ${found.port}` : 'Look up a port';
  const info = found?.info;
  const sheetMatch = drift?.fields?.find((f) => f.field === 'otherEnd')?.expected || null;

  const picture = device ? (
    found?.img ? (
      <button type="button" className={styles.picBtn} onClick={() => setView((v) => (v === 'rack' && found.img ? 'device' : found.rack ? 'rack' : 'device'))} aria-label={view === 'rack' ? 'Show the device' : 'Show the whole rack'}>
        <AssetImg src={view === 'rack' && found.rack ? found.rack : found.img} alt={`${deviceName(device)}, port ${found.port} ringed`} className={styles.pic} />
      </button>
    ) : (
      <PhotoWithReadings
        src={photoOf(result, scanId)}
        alt={`${rackName || 'Rack'}, ${deviceName(device)} outlined`}
        readings={[{ id: device.index, box: device.box, tag: deviceTag(device) }]}
        selectedId={device.index}
      />
    )
  ) : null;

  const finder = (label, value, setValue, onGo) => (
    <form className={styles.pair} onSubmit={(e) => { e.preventDefault(); onGo(value); }} noValidate>
      <Field label={label} inputMode="numeric" value={value} onChange={(e) => { setValue(e.target.value.replace(/\D/g, '').replace(/^0+/, '').slice(0, 4)); setError(''); }} />
      <Button type="submit" busy={busy} className={styles.find}>Find port</Button>
    </form>
  );

  if (scanError && !result) {
    return (
      <>
        <TopBar title="Look up a port" />
        <EmptyState className={styles.pad} text={scanError.message} action={<TextButton to="/history">Go to History</TextButton>} />
      </>
    );
  }

  return (
    <>
      <TopBar title={title} sub={rackName || 'Unidentified rack'} backTo={`/results/${enc(scanId)}`} />
      <div className={styles.stage}>
        <div className={styles.picture}>
          {!result ? <Loading /> : picture || (
            <PhotoWithReadings src={photoOf(result, scanId)} alt={`${rackName || 'Rack'} photo`} readings={portDevices.map((d) => ({ id: d.index, box: d.box, tag: deviceTag(d) }))} onSelect={(id) => setParams({ device: String(id) }, { replace: true })} />
          )}
        </div>
        <DockSheet expanded={expanded} onExpandedChange={setExpanded} label={title}>
          {!found ? (
            <>
              <SubTitle>Look up a port</SubTitle>
              <DevicePicker
                label="Device"
                value={device ? String(device.index) : ''}
                onChange={(v) => setParams(v !== '' && v != null ? { device: String(v) } : {}, { replace: true })}
                options={portDevices.map((d) => ({ value: String(d.index), unit: d.unitText || null, name: deviceName(d) }))}
                placeholder={portDevices.length ? 'Choose a device' : 'No devices with ports'}
              />
              {cats.length > 1 ? (
                <Seg options={cats.map((c) => ({ value: c.value, label: `${c.label} (${portCount(device, c.value)})` }))} value={cat} onChange={(v) => { setCat(v); setError(''); }} label="Kind of port" />
              ) : null}
              {device ? finder('Port number', portNo, setPortNo, lookup) : null}
              {device ? <Grey>{max ? `${max} ports read on this device. Type the number of the port.` : 'The number of ports on this device was not read.'}</Grey> : null}
              <ErrorLine>{error}</ErrorLine>
              {device && !max ? <p><TextButton onClick={() => setFix('device')}>Set the port count</TextButton></p> : null}
            </>
          ) : (
            <>
              <SubTitle>Port {found.port} <span className={styles.gsub}>{deviceName(device)}{device.unitText ? `, ${device.unitText}` : ''}</span></SubTitle>
              <Facts pairs={[
                ['Status', STATE_WORD[info?.status] || 'Not clear on the photo'],
                info?.port_type ? ['Port type', `${portTypeWord(info.port_type)}${info._port_type_user ? ' (your correction)' : ''}`] : null,
                info?.status === 'connected' && (info.cable_connector || info.cable_color || info.cable_type) ? ['Cable', [info.cable_connector || info.cable_type, info.cable_color].filter(Boolean).join(', ')] : null,
                info?.label ? ['Label read', <span key="l" className="mono">{info.label}</span>] : null,
                sheetMatch ? ['Sheet says', sheetMatch] : null,
              ]} />
              <DriftLine drift={drift} className={styles.drift} />
              {info?.status === 'empty' && cat !== 'main' && !/RJ45|RJ-45|CONSOLE|AUX|MANAGEMENT|USB/i.test(String(info?.port_type || '')) ? <p className={styles.capHint}>Empty port: make sure its dust cap is on.</p> : null}
              {finder('Next port', next, setNext, lookup)}
              <ErrorLine>{error}</ErrorLine>
              <Links>
                <TextButton onClick={() => setFix('port')}>Correct this reading</TextButton>
                <TextButton onClick={share}>Share picture</TextButton>
                <TextButton onClick={() => { setFound(null); setParams({}, { replace: true }); }}>Another device</TextButton>
                <TextButton onClick={() => navigate('/scan')}>New scan</TextButton>
              </Links>
            </>
          )}
        </DockSheet>
      </div>
      <Sheet open={!!fix} onClose={() => setFix(null)} title={fix === 'port' ? `Correct port ${found?.port}` : device ? `Correct ${deviceName(device)}` : ''}>
        {fix === 'port' ? <PortCorrections scanId={scanId} device={device} portNo={found?.port} portInfo={info} category={cat} onChanged={onFixed} /> : null}
        {fix === 'device' ? <DeviceCorrections scanId={scanId} device={device} onChanged={(patch) => scan.patchDevice(device.index, patch)} /> : null}
        <Actions><TextButton onClick={() => setFix(null)}>Done</TextButton></Actions>
      </Sheet>
    </>
  );
}
