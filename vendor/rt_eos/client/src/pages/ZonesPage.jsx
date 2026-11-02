import { useMemo, useRef, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { TopBar } from '../components/TopBar.jsx';
import { Page } from '../components/Page.jsx';
import { Button, TextButton, Actions } from '../components/Button.jsx';
import { Field } from '../components/Field.jsx';
import { DevicePicker } from '../components/DevicePicker.jsx';
import { Grey, EmptyState, ErrorLine, Loading } from '../components/Text.jsx';
import { Sheet } from '../components/Sheet.jsx';
import { PhotoWithReadings } from '../components/PhotoWithReadings.jsx';
import { photoOf } from '../utils/rack';
import { useToast } from '../components/Toast.jsx';
import { useJSON } from '../hooks/useJSON.js';
import { RackCamera } from '../components/RackCamera.jsx';
import { postForm, deleteJSON, notBuilt, enc } from '../utils/http';
import { plural } from '../utils/format';
import styles from './ZonesPage.module.css';

const NOT_BUILT = 'Close-ups are not available on this server yet.';
const CLEAR = 0.6;

const idOf = (d) => d?.deviceId ?? d?.device_id ?? d?.id;
const zoneIdOf = (z) => z?.zoneId ?? z?.zone_id ?? null;
const sameId = (a, b) => a != null && b != null && String(a) === String(b);

function normDevice(d) {
  const zones = (Array.isArray(d?.zones) ? d.zones : [])
    .map((z) => ({ ...z, from: Number(z.from), to: Number(z.to), done: !!z.done, zoneId: zoneIdOf(z) }))
    .sort((a, b) => a.from - b.from);
  return {
    ...d,
    deviceId: idOf(d),
    name: d?.name || [d?.make, d?.model].filter(Boolean).join(' ') || 'Device',
    portCount: Number(d?.portCount ?? d?.port_count) || zones.reduce((m, z) => Math.max(m, z.to), 0),
    zones,
  };
}

/** "U39" from 39, "u39" or "U39". */
export function unitWord(u) {
  if (u == null || u === '') return '';
  const s = String(u).trim();
  return /^u/i.test(s) ? `U${s.slice(1)}` : `U${s}`;
}

const deviceTitle = (d) => [d.name, unitWord(d.unit)].filter(Boolean).join(', ');
const range = (z) => `${z.from}-${z.to}`;
const firstOpen = (d) => d?.zones.find((z) => !z.done) || null;

/** How many labels a close-up read clearly, and how many it could not. */
export function labelTally(labels) {
  const list = Array.isArray(labels) ? labels : [];
  const unclear = list.filter((l) => !l?.text || (l.confidence != null && Number(l.confidence) < CLEAR)).length;
  return { read: list.length - unclear, unclear };
}

/** "?from=5&to=5" as { from, to }, or null. */
function spanOf(params) {
  const from = Number(params.get('from'));
  const to = params.get('to') != null ? Number(params.get('to')) : from;
  return Number.isInteger(from) && Number.isInteger(to) && from >= 1 && to >= from ? { from, to } : null;
}

/**
 * The zone to open on when the link names ports (from a port card): the zone
 * that holds them, else those ports as a range of their own. Null keeps the
 * first zone not taken.
 */
export function startRange(device, span) {
  if (!device || !span) return null;
  const z = device.zones.find((x) => x.from <= span.from && span.to <= x.to)
    || device.zones.find((x) => x.from <= span.from && span.from <= x.to);
  if (z) return z === firstOpen(device) ? null : { from: z.from, to: z.to };
  if (device.portCount && span.to > device.portCount) return null;
  return { from: span.from, to: span.to };
}

export default function ZonesPage() {
  const { scanId } = useParams();
  const [params] = useSearchParams();
  const zones = useJSON(`/api/scans/${enc(scanId)}/zones`);
  const scan = useJSON(zones.data ? `/api/scan/${enc(scanId)}` : null);
  const s = scan.data || {};
  const rack = zones.data?.rackLabel || zones.data?.rackName || s.rackLabel || s.rackName || s.rack?.label || 'Unidentified rack';

  if (zones.loading && !zones.data) return (<><TopBar title="Close-ups" backTo={`/results/${enc(scanId)}`} /><Page><Loading /></Page></>);
  if (zones.error) {
    return (
      <>
        <TopBar title="Close-ups" backTo={`/results/${enc(scanId)}`} />
        <Page>
          {notBuilt(zones.error) ? <Grey>{NOT_BUILT}</Grey> : <ErrorLine>{zones.error.message}</ErrorLine>}
          {!notBuilt(zones.error) ? <TextButton onClick={zones.reload}>Try again</TextButton> : null}
        </Page>
      </>
    );
  }
  const devices = (Array.isArray(zones.data?.devices) ? zones.data.devices : []).map(normDevice).filter((d) => d.portCount > 0 || d.zones.length);
  if (!devices.length) {
    return (
      <>
        <TopBar title="Close-ups" sub={rack} backTo={`/results/${enc(scanId)}`} />
        <Page>
          <EmptyState text="No devices with ports were read on this rack." action={<TextButton to={`/results/${enc(scanId)}`}>Back to the rack</TextButton>} />
        </Page>
      </>
    );
  }
  return <Zones scanId={scanId} rack={rack} devices={devices} setData={zones.setData} wanted={params.get('device')} span={spanOf(params)} scan={scan.data} />;
}

function Zones({ scanId, rack, devices, setData, wanted, span, scan }) {
  const toast = useToast();
  const [picked, setPicked] = useState(wanted != null);
  const [live, setLive] = useState(false);
  const fileRef = useRef(null);

  const [deviceId, setDeviceId] = useState(() => {
    const byParam = wanted != null && devices.find((d) => sameId(d.deviceId, wanted));
    return idOf(byParam || devices.find((d) => firstOpen(d)) || devices[0]);
  });
  const [custom, setCustom] = useState(() => startRange(devices.find((d) => sameId(d.deviceId, wanted)), span));
  const [stats, setStats] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [doneZone, setDoneZone] = useState(null);
  const [changeOpen, setChangeOpen] = useState(false);
  const [lastShot, setLastShot] = useState(null);
  const [form, setForm] = useState({ deviceId: '', from: '', to: '', error: '' });

  const device = devices.find((d) => sameId(d.deviceId, deviceId)) || devices[0];
  const current = custom || firstOpen(device);
  const zonesOf = device.zones;
  const taken = zonesOf.filter((z) => z.done).length;

  const counts = useMemo(() => zonesOf.filter((z) => z.done).reduce((acc, z) => {
    const s = stats[`${device.deviceId}:${range(z)}`];
    const read = s?.read ?? z.labelsRead ?? z.labels_read ?? z.labelCount ?? null;
    const unclear = s?.unclear ?? z.notClear ?? z.not_clear ?? z.unclear ?? null;
    if (read != null) { acc.known = true; acc.read += Number(read) || 0; }
    if (unclear != null) { acc.known = true; acc.unclear += Number(unclear) || 0; }
    return acc;
  }, { read: 0, unclear: 0, known: false }), [zonesOf, stats, device.deviceId]);

  const countLine = [
    zonesOf.length ? `${taken} of ${plural(zonesOf.length, 'zone')} taken.` : '',
    counts.known ? `${plural(counts.read, 'label')} read, ${counts.unclear} not clear.` : '',
  ].filter(Boolean).join(' ');

  const markZone = (devId, zone, patch) => {
    setData((d) => ({
      ...d,
      devices: (d?.devices || []).map((raw) => {
        if (!sameId(idOf(raw), devId)) return raw;
        const list = Array.isArray(raw.zones) ? raw.zones : [];
        const hit = list.some((z) => Number(z.from) === zone.from && Number(z.to) === zone.to);
        const next = hit
          ? list.map((z) => (Number(z.from) === zone.from && Number(z.to) === zone.to ? { ...z, ...patch } : z))
          : [...list, { from: zone.from, to: zone.to, ...patch }];
        return { ...raw, zones: next };
      }),
    }));
  };

  const send = async (file) => {
    if (!file || !current) return;
    const zone = { from: current.from, to: current.to };
    setBusy(true); setError('');
    try {
      const form = new FormData();
      form.append('image', file);
      form.append('deviceId', String(device.deviceId));
      form.append('from', String(zone.from));
      form.append('to', String(zone.to));
      const out = await postForm(`/api/scans/${enc(scanId)}/zones`, form);
      const t = labelTally(out?.labels);
      if (out?.photoUrl) setLastShot({ src: out.photoUrl, labels: Array.isArray(out.labels) ? out.labels : [] });
      toast(`${plural(t.read, 'label')} read, ${t.unclear} not clear.`);
      setStats((s) => ({ ...s, [`${device.deviceId}:${range(zone)}`]: t }));
      markZone(device.deviceId, zone, { done: true, zoneId: zoneIdOf(out) });
      setCustom(null);
      // Move on: the next zone on this device, else the next device with one open.
      const left = device.zones.filter((z) => !z.done && !(z.from === zone.from && z.to === zone.to));
      if (!left.length) {
        const i = devices.findIndex((d) => sameId(d.deviceId, device.deviceId));
        const nextDev = [...devices.slice(i + 1), ...devices.slice(0, i)].find((d) => firstOpen(d));
        if (nextDev) setDeviceId(nextDev.deviceId);
      }
    } catch (e) {
      setError(notBuilt(e) ? NOT_BUILT : e.message);
    } finally {
      setBusy(false);
    }
  };

  const onFile = (e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (file) send(file);
  };

  const remove = async () => {
    const z = doneZone;
    if (!z) return;
    if (z.zoneId == null) { setError('This close-up cannot be removed from here. Take it again instead.'); setDoneZone(null); return; }
    setBusy(true); setError('');
    try {
      await deleteJSON(`/api/scans/${enc(scanId)}/zones/${enc(z.zoneId)}`);
      markZone(device.deviceId, z, { done: false, zoneId: null });
      setStats((s) => { const n = { ...s }; delete n[`${device.deviceId}:${range(z)}`]; return n; });
      toast('Close-up removed.');
      setDoneZone(null);
    } catch (e) {
      setError(notBuilt(e) ? NOT_BUILT : e.message);
      setDoneZone(null);
    } finally {
      setBusy(false);
    }
  };

  const openChange = () => {
    setForm({ deviceId: String(device.deviceId), from: current ? String(current.from) : '1', to: current ? String(current.to) : String(Math.min(12, device.portCount || 12)), error: '' });
    setChangeOpen(true);
  };

  const applyRange = () => {
    const dev = devices.find((d) => sameId(d.deviceId, form.deviceId)) || device;
    const from = Number(form.from);
    const to = Number(form.to);
    const max = dev.portCount || Infinity;
    if (!Number.isInteger(from) || !Number.isInteger(to) || from < 1 || to < from) {
      setForm((f) => ({ ...f, error: 'Enter the first and last port, the first no bigger than the last.' }));
      return;
    }
    if (to > max) {
      setForm((f) => ({ ...f, error: `This device has ${max} ports. Enter a last port of ${max} or less.` }));
      return;
    }
    setDeviceId(dev.deviceId);
    setCustom({ from, to });
    setChangeOpen(false);
  };

  const tapZone = (z) => {
    if (z.done) setDoneZone(z);
    else setCustom(z === firstOpen(device) ? null : { from: z.from, to: z.to });
  };

  /* The real picture: the latest close-up with the labels it read, else the
     rack photo with this device outlined. */
  const scanDevice = (scan?.devices || [])[Number(device.deviceId) - 1] || null;
  const picture = lastShot ? (
    <PhotoWithReadings
      src={lastShot.src}
      alt="The last close-up"
      readings={lastShot.labels.filter((l) => Array.isArray(l.box) && l.text && (l.confidence == null || Number(l.confidence) >= CLEAR)).map((l, i) => {
        const [x, y, w, h] = l.box;
        return { id: i, box: [x, y, x + w, y + h], tag: String(l.text) };
      })}
    />
  ) : scan ? (
    <PhotoWithReadings
      src={photoOf(scan, scanId)}
      alt={`${rack} photo, ${deviceTitle(device)} outlined`}
      readings={scanDevice?.box ? [{ id: 'd', box: scanDevice.box, tag: deviceTitle(device) }] : []}
      selectedId="d"
    />
  ) : null;

  const strip = (
    <div className={styles.zstrip}>
      {zonesOf.map((z) => {
        const now = current && z.from === current.from && z.to === current.to;
        const cls = [styles.zone, z.done && !now ? styles.done : '', now ? styles.now : ''].filter(Boolean).join(' ');
        return (
          <button
            key={range(z)}
            type="button"
            className={cls}
            onClick={() => tapZone(z)}
            aria-label={`Ports ${z.from} to ${z.to}, ${now ? 'next' : z.done ? 'taken' : 'not taken yet'}`}
          >
            <b />
            {range(z)}
          </button>
        );
      })}
    </div>
  );

  const doneLine = devices.some((d) => firstOpen(d))
    ? 'Every zone on this device is taken. Choose another device.'
    : 'Every zone on this rack is taken.';

  const fileInput = (
    <input
      ref={fileRef}
      type="file"
      accept="image/*"
      className="visually-hidden"
      tabIndex={-1}
      aria-label="Close-up photo"
      data-testid="zone-file"
      onChange={onFile}
    />
  );

  const sheets = (
    <>
      <Sheet open={!!doneZone} onClose={() => setDoneZone(null)} title={doneZone ? `Ports ${doneZone.from} to ${doneZone.to}` : ''}>
        <Grey>This zone is taken.</Grey>
        <Actions>
          <Button size="auto" onClick={() => { setCustom({ from: doneZone.from, to: doneZone.to }); setDoneZone(null); }}>Take it again</Button>
          <TextButton danger onClick={remove} disabled={busy}>Remove this close-up</TextButton>
        </Actions>
      </Sheet>
      <Sheet open={changeOpen} onClose={() => setChangeOpen(false)} title="Change ports">
        <DevicePicker
          label="Device"
          value={form.deviceId}
          onChange={(v) => setForm((f) => ({ ...f, deviceId: String(v), error: '' }))}
          options={devices.map((d) => ({ value: String(d.deviceId), unit: unitWord(d.unit) || null, name: d.name, sub: d.portCount ? `${d.portCount} ports` : null }))}
        />
        <div className={styles.pair}>
          <Field label="From" type="number" inputMode="numeric" min={1} value={form.from} onChange={(e) => setForm((f) => ({ ...f, from: e.target.value, error: '' }))} />
          <Field label="To" type="number" inputMode="numeric" min={1} value={form.to} onChange={(e) => setForm((f) => ({ ...f, to: e.target.value, error: '' }))} />
        </div>
        <ErrorLine>{form.error}</ErrorLine>
        <Actions>
          <Button size="auto" onClick={applyRange}>Use this range</Button>
          <TextButton onClick={() => setChangeOpen(false)}>Cancel</TextButton>
        </Actions>
      </Sheet>
    </>
  );

  // Step 1: which device.
  if (!picked) {
    return (
      <>
        <TopBar title="Close-ups" sub={rack} backTo={`/results/${enc(scanId)}`} />
        <Page>
          <h2 className={styles.ask}>Which device?</h2>
          <p className={styles.askSub}>Choose the device whose cable labels you want read.</p>
          <div className={styles.devList}>
            {devices.map((d) => {
              const t = d.zones.filter((z) => z.done).length;
              return (
                <button key={String(d.deviceId)} type="button" className={styles.dev} onClick={() => { setDeviceId(d.deviceId); setCustom(null); setPicked(true); setLastShot(null); }}>
                  <span className={styles.devName}>{deviceTitle(d)}</span>
                  <span className={styles.devMeta}>{d.zones.length ? (t === d.zones.length ? 'Done' : `${t} of ${d.zones.length}`) : `${d.portCount} ports`}</span>
                </button>
              );
            })}
          </div>
        </Page>
      </>
    );
  }

  // Step 2: the guided shot, from the camera or a photo.
  return (
    <>
      <TopBar title={deviceTitle(device)} sub={rack} backTo={`/results/${enc(scanId)}`} />
      <Page>
        {picture ? <div className={styles.pic}>{picture}</div> : null}
        {strip}
        {current ? (
          <div className={styles.guideCard}>
            <p className={styles.gTitle}>Ports {current.from} to {current.to}</p>
            <ol className={styles.steps}>
              <li>Stand 20 to 30 cm from the device.</li>
              <li>Keep the ports level, filling the frame.</li>
              <li>Make sure every cable label faces you.</li>
            </ol>
          </div>
        ) : <Grey>{doneLine}</Grey>}
        {countLine && current ? <Grey>{countLine}</Grey> : null}
        <ErrorLine>{error}</ErrorLine>
        {current ? (
          <div className={styles.two}>
            <Button onClick={() => { setError(''); setLive(true); }} busy={busy}>{`Take close-up of ${range(current)}`}</Button>
            <Button variant="secondary" onClick={() => fileRef.current?.click()} disabled={busy}>Upload a photo</Button>
          </div>
        ) : null}
        <div className={styles.links}>
          <TextButton onClick={() => setPicked(false)}>Choose another device</TextButton>
          <TextButton onClick={openChange}>Change ports</TextButton>
        </div>
        {fileInput}
      </Page>
      {live ? (
        <RackCamera
          closeUp
          title={current ? `Ports ${current.from} to ${current.to}` : deviceTitle(device)}
          onClose={() => setLive(false)}
          onShot={(f) => { setLive(false); send(f); }}
        />
      ) : null}
      {sheets}
    </>
  );
}
