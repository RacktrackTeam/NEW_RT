import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Page } from '../components/Page.jsx';
import { Button, TextButton, Actions } from '../components/Button.jsx';
import { SelectField } from '../components/Field.jsx';
import { Sheet } from '../components/Sheet.jsx';
import { ListRow } from '../components/ListRow.jsx';
import { ErrorLine } from '../components/Text.jsx';
import { AnalysingView } from '../components/AnalysingView.jsx';
import { SubTabs } from '../components/Ui.jsx';
import { TopBar } from '../components/TopBar.jsx';
import Icon from '../components/Icon.jsx';
import { RackCamera } from '../components/RackCamera.jsx';
import useIsLaptop from '../hooks/useIsLaptop';
import { useScanSite } from '../hooks/useScanSite';
import { validateMedia } from '../utils/validateMedia';
import { IMAGE_ACCEPT, VIDEO_ACCEPT } from '../utils/mediaAccept';
import { sendScan } from '../utils/sendScan';
import { holdDone } from '../utils/analysing';
import styles from './ScanPage.module.css';

const MODES = [{ value: 'photo', label: 'Photo' }, { value: 'stitch', label: 'Stitch' }, { value: 'video', label: 'Video' }];
const MAX_SHOTS = 8;

const HELP = {
  photo: 'One photo of the whole rack.',
  stitch: 'For a rack too tall for one photo. Take it in parts from the top down, each photo overlapping the one before.',
  video: 'A short video along the racks. Each rack in it becomes its own scan.',
};

/* What to take next in a stitch, after n photos. */
function stitchNext(n) {
  if (n >= MAX_SHOTS) return `${MAX_SHOTS} photos, the most a stitch takes. Press Analyse.`;
  if (n === 1) return 'Photo 1 added (the top). Next: the part below it, keeping about a quarter of this photo in view.';
  return `Photo ${n} added. If the bottom of the rack is in it, press Analyse. If not, take the next part down.`;
}

const isVideoFile = (f) => String(f?.type || '').startsWith('video/') || /\.(mp4|mov|m4v|webm|avi)$/i.test(String(f?.name || ''));

/* A blob URL for each file, released when the list changes. */
function useObjectUrls(files) {
  const [urls, setUrls] = useState([]);
  useEffect(() => {
    const next = files.map((f) => (f ? URL.createObjectURL(f) : ''));
    setUrls(next);
    return () => next.forEach((u) => u && URL.revokeObjectURL(u));
  }, [files]);
  return urls;
}

/**
 * Scan a rack: capture (a photo, a stitch of photos or a video), look at what
 * was captured, then press Analyse. The camera is full screen on a phone;
 * everywhere, files can be chosen (or dropped on a laptop) instead.
 */
export default function ScanPage() {
  const navigate = useNavigate();
  const laptop = useIsLaptop();
  const [mode, setMode] = useState('photo');
  const [shots, setShots] = useState([]);
  const [single, setSingle] = useState(null);
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(null);
  const [finished, setFinished] = useState(false);
  const [error, setError] = useState('');
  const [quality, setQuality] = useState(null);
  const [siteSheet, setSiteSheet] = useState(false);
  const [camera, setCamera] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef(null);
  const { sites, siteId, chooseSite, needsSite } = useScanSite();
  const site = sites.find((s) => String(s.id) === String(siteId));
  const shotUrls = useObjectUrls(shots);
  const singleList = useMemo(() => (single ? [single] : []), [single]);
  const singleUrls = useObjectUrls(singleList);
  const hasCamera = !laptop && typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia;

  const switchMode = (m) => { setMode(m); setShots([]); setSingle(null); setError(''); setQuality(null); };

  const read = useCallback(async (kind, files, { override = false } = {}) => {
    if (needsSite) { setError('Choose the site first.'); return; }
    setError(''); setQuality(null);
    if (!override && kind !== 'video') {
      for (let i = 0; i < files.length; i += 1) {
        const check = await validateMedia(files[i]);
        if (check.ok) continue;
        const which = files.length > 1 ? `Photo ${i + 1} of ${files.length}: ` : '';
        if (check.retryable) { setQuality({ kind, files, text: which + check.error }); return; }
        setError(which + check.error);
        return;
      }
    }
    setBusy(true); setFinished(false); setReading({ kind, files });
    const stop = () => { setBusy(false); setReading(null); };
    try {
      const out = await sendScan({ kind, files, siteId, override });
      if (out.quality) { stop(); setQuality({ kind, files, text: out.quality }); return; }
      const data = out.data;
      if (kind === 'video') {
        const racks = (data.racks || []).filter((r) => r.rackId);
        if (!racks.length) { stop(); setError('No racks were found in the video. Try a slower, steadier pan.'); return; }
        const notice = racks.length > 1 ? `${racks.length} racks found in the video. The others are in History.` : null;
        setFinished(true); await holdDone();
        navigate(`/results/${encodeURIComponent(racks[0].rackId)}`, { state: { notice } });
        return;
      }
      setFinished(true); await holdDone();
      navigate(data.rackId ? `/results/${encodeURIComponent(data.rackId)}` : '/', { state: { result: data } });
    } catch (e) {
      stop();
      setError(e.message);
    }
  }, [needsSite, siteId, navigate]);

  const addFiles = (list) => {
    const files = Array.from(list || []).filter(Boolean);
    if (!files.length) return;
    setError(''); setQuality(null);
    if (mode === 'stitch') { setShots((prev) => [...prev, ...files].slice(0, MAX_SHOTS)); return; }
    const f = files[0];
    if (mode === 'video' && !isVideoFile(f)) { setError('Pick a video file.'); return; }
    setSingle(f);
  };

  const move = (i, d) => setShots((prev) => {
    const j = i + d;
    if (j < 0 || j >= prev.length) return prev;
    const next = prev.slice();
    [next[i], next[j]] = [next[j], next[i]];
    return next;
  });
  const remove = (i) => setShots((prev) => prev.filter((_, k) => k !== i));

  const count = mode === 'stitch' ? shots.length : single ? 1 : 0;
  const ready = mode === 'stitch' ? shots.length >= 2 : !!single;
  const analyseLabel = mode === 'stitch' ? (shots.length ? `Analyse ${shots.length} photos` : 'Analyse') : mode === 'video' ? 'Analyse the video' : 'Analyse';
  const analyse = () => {
    if (mode === 'stitch') {
      if (shots.length < 2) { setError(`Add ${2 - shots.length} more photo${shots.length === 1 ? '' : 's'}. A stitch needs 2 to 8.`); return; }
      read('stitch', shots);
      return;
    }
    if (!single) { setError(mode === 'video' ? 'Add a video of the racks first.' : 'Take or choose a photo of the rack first.'); return; }
    read(mode, [single]);
  };

  const fileInput = (
    <input
      ref={fileRef}
      type="file"
      className="visually-hidden"
      tabIndex={-1}
      aria-hidden="true"
      accept={mode === 'video' ? VIDEO_ACCEPT : IMAGE_ACCEPT}
      multiple={mode === 'stitch'}
      onChange={(e) => { addFiles(e.target.files); e.target.value = ''; }}
    />
  );

  const previews = mode === 'stitch' ? shots : singleList;
  const urls = mode === 'stitch' ? shotUrls : singleUrls;
  const where = (i, n) => (n === 1 ? '' : i === 0 ? ', top' : i === n - 1 ? ', bottom' : '');
  const canAdd = mode === 'stitch' ? shots.length < MAX_SHOTS : !single;

  const chooseButtons = (
    <div className={styles.chooseActs}>
      {hasCamera ? <Button size="auto" onClick={() => { setError(''); setCamera(true); }}><Icon name={mode === 'video' ? 'video' : 'camera'} />{mode === 'video' ? 'Record a video' : count ? 'Take another photo' : 'Open the camera'}</Button> : null}
      <Button size="auto" variant="secondary" onClick={() => fileRef.current?.click()}><Icon name="image" />{mode === 'video' ? 'Choose a video' : mode === 'stitch' ? 'Choose photos' : 'Choose a photo'}</Button>
    </div>
  );

  const stage = (
    <div
      className={`${styles.stage} ${dragging ? styles.drag : ''} ${previews.length ? styles.filled : ''}`}
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => { e.preventDefault(); setDragging(false); addFiles(e.dataTransfer.files); }}
    >
      {previews.length ? (
        <div className={`${styles.previews} ${mode === 'stitch' ? styles.many : ''}`}>
          {previews.map((f, i) => (
            <figure key={`${f.name}-${i}-${f.lastModified}`} className={styles.shot}>
              {isVideoFile(f)
                ? <video src={urls[i]} className={styles.prev} muted playsInline controls />
                : <img src={urls[i]} alt={`Rack, part ${i + 1}`} className={styles.prev} />}
              <figcaption className={styles.cap}>
                <span>{mode === 'video' ? f.name : mode === 'stitch' ? `Photo ${i + 1}${where(i, previews.length)}` : 'Your photo'}</span>
                <span className={styles.capActs}>
                  {mode === 'stitch' && i > 0 ? <TextButton small onClick={() => move(i, -1)}>Earlier</TextButton> : null}
                  {mode === 'stitch' && i < previews.length - 1 ? <TextButton small onClick={() => move(i, 1)}>Later</TextButton> : null}
                  <TextButton small onClick={() => (mode === 'stitch' ? remove(i) : setSingle(null))}>Remove</TextButton>
                </span>
              </figcaption>
            </figure>
          ))}
        </div>
      ) : (
        <div className={styles.empty}>
          <span className={styles.emptyIcon} aria-hidden="true"><Icon name={mode === 'video' ? 'video' : mode === 'stitch' ? 'layers' : 'camera'} /></span>
          <p className={styles.emptyT}>{mode === 'video' ? 'No video yet' : mode === 'stitch' ? 'No photos yet' : 'No photo yet'}</p>
          <p className={styles.emptyX}>{laptop ? 'Drop the files here, or choose them.' : hasCamera ? 'Take it with the camera, or choose it from your files.' : 'Choose it from your files.'}</p>
          {chooseButtons}
        </div>
      )}
      {previews.length && canAdd ? chooseButtons : null}
    </div>
  );

  return (
    <>
      <TopBar title="Scan a rack" back={false} />
      <Page className={styles.page}>
        <div className={styles.layout}>
          <div className={styles.side}>
            {sites.length > 1 && !laptop ? (
              <p className={styles.siteLine}>
                {site?.name || 'No site chosen'}{' '}
                <TextButton small onClick={() => setSiteSheet(true)}>{site ? 'Change' : 'Choose'}</TextButton>
              </p>
            ) : null}
            {sites.length > 1 && laptop ? (
              <SelectField label="Site" required value={siteId} onChange={(e) => { chooseSite(e.target.value); setError(''); }} options={sites.map((s) => ({ value: String(s.id), label: s.name }))} placeholder="Choose a site" />
            ) : null}
            <SubTabs options={MODES} value={mode} onChange={switchMode} label="How to scan" className={styles.modes} full />
            <p className={styles.help}>{HELP[mode]}</p>
            {mode === 'stitch' ? (
              <ol className={styles.stitchSteps}>
                <li><b>How many:</b> 3 photos for most racks: the top third, the middle, the bottom. 2 for a short rack, up to 8 for a very tall one.</li>
                <li><b>How:</b> stand in the same spot, phone level, and only tilt or step down between photos.</li>
                <li><b>Overlap:</b> keep about a quarter of the last photo in the next one, so they join.</li>
                <li><b>Done:</b> when the bottom of the rack is in a photo, press Analyse.</li>
              </ol>
            ) : null}
            {laptop ? (
              <div className={styles.steps}>
                <p><b>1</b> Capture: {mode === 'video' ? 'add the video' : mode === 'stitch' ? 'add the photos, top first' : 'add the photo'}.</p>
                <p><b>2</b> Check what you captured.</p>
                <p><b>3</b> Analyse: every unit, device, port and cable is read.</p>
              </div>
            ) : null}
            {laptop ? (
              <>
                <ErrorLine>{error}</ErrorLine>
                <Button className={styles.analyse} onClick={analyse} busy={busy} disabled={!ready}>{analyseLabel}</Button>
              </>
            ) : null}
          </div>
          <div className={styles.main}>
            {stage}
            {!laptop ? (
              <>
                <ErrorLine>{error}</ErrorLine>
                <Button size="big" className={styles.analyse} onClick={analyse} busy={busy} disabled={!ready}>{analyseLabel}</Button>
                {mode === 'stitch' && shots.length ? <p className={styles.help} role="status">{stitchNext(shots.length)}</p> : null}
              </>
            ) : null}
          </div>
        </div>
        {fileInput}
      </Page>

      {camera ? (
        <RackCamera
          mode={mode}
          shots={shots.length}
          onShot={(f) => {
            if (mode === 'stitch') setShots((prev) => [...prev, f].slice(0, MAX_SHOTS));
            else { setSingle(f); setCamera(false); }
          }}
          onVideo={(f) => { setSingle(f); setCamera(false); }}
          onClose={() => setCamera(false)}
        />
      ) : null}

      <Sheet open={!!quality} onClose={() => setQuality(null)} title={quality?.kind === 'stitch' ? 'These photos may not join well' : 'This may not read well'}>
        <p>{quality?.text}</p>
        <Actions>
          <Button size="auto" onClick={() => { const k = quality.kind; setQuality(null); if (k === 'photo') setSingle(null); }}>{quality?.kind === 'stitch' ? 'Change the photos' : 'Take it again'}</Button>
          <TextButton onClick={() => read(quality.kind, quality.files, { override: true })}>Analyse it anyway</TextButton>
        </Actions>
      </Sheet>
      <Sheet open={siteSheet} onClose={() => setSiteSheet(false)} title="Which site is this rack in?">
        {sites.map((s) => (
          <ListRow key={s.id} title={s.name} meta={s.rackCount != null ? `${s.rackCount} racks` : null} onClick={() => { chooseSite(String(s.id)); setSiteSheet(false); setError(''); }} right={String(s.id) === String(siteId) ? 'Chosen' : null} />
        ))}
      </Sheet>
      {busy && reading ? (
        <AnalysingView
          label={reading.kind === 'video' ? 'Analysing the video' : 'Analysing the rack'}
          racks={[{ key: 'rack', files: reading.files, state: finished ? 'done' : 'reading' }]}
          finished={finished}
        />
      ) : null}
    </>
  );
}
