import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Page } from '../components/Page.jsx';
import { Button, TextButton, Actions } from '../components/Button.jsx';
import { SelectField } from '../components/Field.jsx';
import { Sheet } from '../components/Sheet.jsx';
import { ListRow } from '../components/ListRow.jsx';
import { ErrorLine } from '../components/Text.jsx';
import { AnalysingView } from '../components/AnalysingView.jsx';
import { TopBar } from '../components/TopBar.jsx';
import Icon from '../components/Icon.jsx';
import { RackCamera } from '../components/RackCamera.jsx';
import useIsLaptop from '../hooks/useIsLaptop';
import { useScanSite } from '../hooks/useScanSite';
import { validateMedia } from '../utils/validateMedia';
import { IMAGE_ACCEPT } from '../utils/mediaAccept';
import { sendScan } from '../utils/sendScan';
import { holdDone } from '../utils/analysing';
import { postJSON } from '../utils/http';
import scan from './ScanPage.module.css';
import styles from './MapPage.module.css';

const SIDES = [
  { key: 'a', word: 'Rack A' },
  { key: 'z', word: 'Rack Z' },
];
const isHeic = (f) => /\.(heic|heif)$/i.test(String(f?.name || '')) || /image\/hei[cf]/i.test(String(f?.type || ''));

/* The longest edge of a preview. A 2 to 4 MB photo is drawn down to a small
   JPEG for the slot, so two of them do not hold the whole files in memory. */
const PREVIEW_EDGE = 1200;

/* The photo drawn down to a small JPEG data URL, or '' when this browser
   cannot decode it (no createImageBitmap, or a format it does not read). */
async function drawPreview(file) {
  if (typeof createImageBitmap !== 'function' || typeof document === 'undefined') return '';
  let bmp = null;
  try {
    bmp = await createImageBitmap(file);
    const k = Math.min(1, PREVIEW_EDGE / Math.max(bmp.width, bmp.height, 1));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(bmp.width * k));
    canvas.height = Math.max(1, Math.round(bmp.height * k));
    const ctx = canvas.getContext('2d');
    if (!ctx) return '';
    ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height);
    const url = canvas.toDataURL('image/jpeg', 0.82);
    return url.startsWith('data:image/jpeg') ? url : '';
  } catch {
    return '';
  } finally {
    bmp?.close?.();
  }
}

/* The whole file as a data URL: the last try when a preview will not show. */
function readDataUrl(file) {
  return new Promise((resolve) => {
    try {
      const r = new FileReader();
      r.onload = () => resolve(typeof r.result === 'string' ? r.result : '');
      r.onerror = () => resolve('');
      r.readAsDataURL(file);
    } catch {
      resolve('');
    }
  });
}

/*
 * The preview of one chosen photo: a small drawn JPEG, else a blob URL, and
 * when the image still fails, the file read as a data URL once. `failed` is
 * true only after every way has failed for this very file. The <img> is only
 * drawn once a URL is ready: an <img src=""> (the first render, before any
 * URL exists) or a blob URL revoked by React's double effect in development
 * fires onError at once, and that used to mark every photo as unreadable.
 */
function usePreview(file) {
  const [state, setState] = useState({ file: null, url: '', how: '', failed: false });
  useEffect(() => {
    if (!file) { setState({ file: null, url: '', how: '', failed: false }); return undefined; }
    let live = true;
    let blob = '';
    setState({ file, url: '', how: '', failed: false });
    // A large photo can take a phone a long time to draw down: after 3 s the
    // file itself is shown instead, so the card never stays blank.
    const slow = new Promise((done) => { setTimeout(() => done(''), 3000); });
    Promise.race([drawPreview(file), slow]).then((drawn) => {
      if (!live) return;
      if (drawn) { setState({ file, url: drawn, how: 'drawn', failed: false }); return; }
      try { blob = URL.createObjectURL(file); } catch { blob = ''; }
      if (blob) { setState({ file, url: blob, how: 'blob', failed: false }); return; }
      readDataUrl(file).then((read) => { if (live) setState({ file, url: read, how: 'read', failed: !read }); });
    });
    return () => { live = false; if (blob) URL.revokeObjectURL(blob); };
  }, [file]);
  const current = state.file === file ? state : { file, url: '', how: '', failed: false };
  const onError = () => {
    if (current.how === 'read' || !file) { setState((s) => (s.file === file ? { ...s, url: '', failed: true } : s)); return; }
    readDataUrl(file).then((read) => {
      setState((s) => (s.file === file ? { file, url: read, how: 'read', failed: !read } : s));
    });
  };
  return { url: current.url, failed: current.failed, onError };
}

/* One rack's photo: its name, the photo (or two small buttons to get one). */
function Slot({ side, file, hasCamera, laptop, onCamera, onChoose, onDrop, onRemove }) {
  const { url, failed, onError } = usePreview(file);
  const [dragging, setDragging] = useState(false);
  return (
    <section
      className={`${styles.slot} ${dragging ? styles.drag : ''}`}
      aria-label={side.word}
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => { e.preventDefault(); setDragging(false); onDrop(e.dataTransfer.files); }}
    >
      <div className={styles.head}>
        <span className={styles.word}>{side.word}</span>
        {file ? <TextButton small onClick={onRemove}>Remove</TextButton> : null}
      </div>
      {file ? (
        <div className={`${styles.frame} ${styles.filled}`}>
          {failed ? (
            <div className={styles.noPreview}>
              <p>{file.name}</p>
              <p>{isHeic(file) ? 'This phone cannot show a HEIC photo here. It is read when you press Analyse.' : 'Preview not available. It is read when you press Analyse.'}</p>
            </div>
          ) : url ? (
            <img src={url} alt={`${side.word}, chosen for analysis`} className={styles.prev} onError={onError} />
          ) : (
            <div className={styles.noPreview}>
              <p>{file.name || 'Photo chosen'}</p>
              <p>Getting the photo ready</p>
            </div>
          )}
        </div>
      ) : (
        <div className={`${styles.frame} ${styles.empty}`}>
          <span className={styles.emptyIcon} aria-hidden="true"><Icon name="image" /></span>
          <p className={styles.emptyX}>{laptop ? 'Drop a photo here' : 'No photo yet'}</p>
        </div>
      )}
      <div className={styles.acts}>
        {file ? (
          <TextButton small onClick={onChoose}>Change photo</TextButton>
        ) : (
          <>
            {hasCamera ? <Button size="sm" variant="secondary" className={styles.small} onClick={onCamera}><Icon name="camera" />Camera</Button> : null}
            <Button size="sm" variant="secondary" className={styles.small} onClick={onChoose}><Icon name="upload" />Upload</Button>
          </>
        )}
      </div>
    </section>
  );
}

/**
 * Map two racks: the Scan page with two photos, Rack A and Rack Z. Analyse
 * reads both, then opens the two racks together.
 */
export default function MapPage() {
  const navigate = useNavigate();
  const laptop = useIsLaptop();
  const [files, setFiles] = useState({ a: null, z: null });
  const [done, setDone] = useState({ a: null, z: null });
  const [steps, setSteps] = useState(null);
  const [error, setError] = useState('');
  const [quality, setQuality] = useState(null);
  const [camera, setCamera] = useState(null);
  const [siteSheet, setSiteSheet] = useState(false);
  const fileRef = useRef(null);
  const target = useRef('a');
  const { sites, siteId, chooseSite, needsSite } = useScanSite();
  const site = sites.find((s) => String(s.id) === String(siteId));
  const hasCamera = !laptop && typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia;
  const busy = !!steps;
  const media = useMemo(() => ({ a: files.a ? [files.a] : [], z: files.z ? [files.z] : [] }), [files]);

  const put = (key, list) => {
    const f = Array.from(list || []).find(Boolean);
    if (!f) return;
    setError(''); setQuality(null);
    setFiles((prev) => ({ ...prev, [key]: f }));
    setDone((prev) => ({ ...prev, [key]: null }));
  };
  const remove = (key) => { setFiles((prev) => ({ ...prev, [key]: null })); setDone((prev) => ({ ...prev, [key]: null })); };
  const choose = (key) => { target.current = key; fileRef.current?.click(); };

  const ready = !!(files.a && files.z);

  const read = async ({ override = [] } = {}) => {
    if (needsSite) { setError('Choose the site first.'); return; }
    setError(''); setQuality(null);
    const todo = SIDES.filter((s) => !done[s.key]);
    for (const s of todo) {
      if (override.includes(s.key)) continue;
      const check = await validateMedia(files[s.key]);
      if (check.ok) continue;
      if (check.retryable) { setQuality({ key: s.key, text: check.error }); return; }
      setError(`${s.word}: ${check.error}`);
      return;
    }
    setSteps(Object.fromEntries(SIDES.map((s) => [s.key, done[s.key] ? 'done' : 'reading'])));
    const got = { ...done };
    const results = await Promise.all(todo.map(async (s) => {
      try {
        const out = await sendScan({ files: [files[s.key]], siteId, override: override.includes(s.key), pending: false });
        if (out.quality) { setSteps((p) => ({ ...p, [s.key]: 'failed' })); return { key: s.key, quality: out.quality }; }
        const id = out.data?.rackId || out.data?.scanId;
        if (!id) throw new Error('The rack could not be read. Try again.');
        got[s.key] = id;
        setSteps((p) => ({ ...p, [s.key]: 'done' }));
        return { key: s.key };
      } catch (e) {
        setSteps((p) => ({ ...p, [s.key]: 'failed' }));
        return { key: s.key, error: e.message };
      }
    }));
    setDone(got);
    if (got.a && got.z) {
      /* Remember the two as a pair, so History shows them as one item. A
         failure here never keeps the person from the result. */
      const paired = postJSON('/api/pairs', { aScanId: got.a, zScanId: got.z }).catch(() => null);
      await holdDone();
      await Promise.race([paired, new Promise((r) => setTimeout(r, 3000))]);
      navigate(`/map/${encodeURIComponent(got.a)}/${encodeURIComponent(got.z)}`);
      return;
    }
    setSteps(null);
    const q = results.find((r) => r.quality);
    if (q) { setQuality({ key: q.key, text: q.quality }); return; }
    const bad = results.filter((r) => r.error);
    setError(bad.map((r) => `${SIDES.find((s) => s.key === r.key).word}: ${r.error}`).join(' '));
  };

  return (
    <>
      <TopBar title="Map two racks" back={false} />
      <Page className={scan.page}>
        {sites.length > 1 && !laptop ? (
          <p className={styles.siteLine}>
            {site?.name || 'No site chosen'}{' '}
            <TextButton small onClick={() => setSiteSheet(true)}>{site ? 'Change' : 'Choose'}</TextButton>
          </p>
        ) : null}
        {sites.length > 1 && laptop ? (
          <div className={styles.sitePick}>
            <SelectField label="Site" required value={siteId} onChange={(e) => { chooseSite(e.target.value); setError(''); }} options={sites.map((s) => ({ value: String(s.id), label: s.name }))} placeholder="Choose a site" />
          </div>
        ) : null}
        <div className={styles.pair}>
          {SIDES.map((s) => (
            <Slot
              key={s.key}
              side={s}
              file={files[s.key]}
              hasCamera={hasCamera}
              laptop={laptop}
              onCamera={() => { setError(''); setCamera(s.key); }}
              onChoose={() => choose(s.key)}
              onDrop={(list) => put(s.key, list)}
              onRemove={() => remove(s.key)}
            />
          ))}
        </div>
        <ErrorLine>{error}</ErrorLine>
        <div className={styles.go}>
          <Button size={laptop ? undefined : 'big'} className={scan.analyse} onClick={() => read()} busy={busy} disabled={!ready}>Analyse</Button>
        </div>
        <input
          ref={fileRef}
          type="file"
          className="visually-hidden"
          tabIndex={-1}
          aria-hidden="true"
          data-testid="map-file"
          accept={IMAGE_ACCEPT}
          onChange={(e) => { put(target.current, e.target.files); e.target.value = ''; }}
        />
      </Page>

      {camera ? (
        <RackCamera
          mode="photo"
          title={SIDES.find((s) => s.key === camera)?.word}
          onShot={(f) => { put(camera, [f]); setCamera(null); }}
          onClose={() => setCamera(null)}
        />
      ) : null}

      <Sheet open={!!quality} onClose={() => setQuality(null)} title={quality ? `${SIDES.find((s) => s.key === quality.key)?.word} may not read well` : ''}>
        <p>{quality?.text}</p>
        <Actions>
          <Button size="auto" onClick={() => { const k = quality.key; setQuality(null); remove(k); }}>Take it again</Button>
          <TextButton onClick={() => read({ override: [quality.key] })}>Analyse it anyway</TextButton>
        </Actions>
      </Sheet>
      <Sheet open={siteSheet} onClose={() => setSiteSheet(false)} title="Which site are these racks in?">
        {sites.map((s) => (
          <ListRow key={s.id} title={s.name} meta={s.rackCount != null ? `${s.rackCount} racks` : null} onClick={() => { chooseSite(String(s.id)); setSiteSheet(false); setError(''); }} right={String(s.id) === String(siteId) ? 'Chosen' : null} />
        ))}
      </Sheet>
      {busy ? (
        <AnalysingView
          label="Analysing both racks"
          racks={SIDES.map((s) => ({ key: s.key, word: s.word, files: media[s.key], state: steps[s.key] }))}
          finished={SIDES.every((s) => steps[s.key] === 'done')}
        />
      ) : null}
    </>
  );
}
