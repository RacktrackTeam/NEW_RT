import { useEffect, useMemo, useRef, useState } from 'react';
import { STAGES, stageAt } from '../utils/analysing';
import styles from './AnalysingView.module.css';

// Marks along the rail while the photo is read. They are not the rack's units:
// how tall the rack is is only known once it has been read (the rail numbers,
// the Excel dataset or the shelves counted), so no total is drawn here. A
// mark's number shows only once the beam has passed it, counting down the
// photo as far as the beam has gone.
const TICKS = 40;
const LEDS = 9;
const SWEEP_MS = 3600;
const STATE_WORD = { reading: 'Analysing', done: 'Read', failed: 'Not read' };

const isVideo = (f) => String(f?.type || '').startsWith('video/') || /\.(mp4|mov|m4v|webm|avi)$/i.test(String(f?.name || ''));

function reducedMotion() {
  try { return !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches; } catch { return false; }
}

/* Blob URLs for the files, released when they change. */
function useUrls(files) {
  const [urls, setUrls] = useState([]);
  useEffect(() => {
    const next = files.map((f) => {
      try { return f ? URL.createObjectURL(f) : ''; } catch { return ''; }
    });
    setUrls(next);
    return () => next.forEach((u) => { try { if (u) URL.revokeObjectURL(u); } catch { /* ignore */ } });
  }, [files]);
  return urls;
}

/* One rack: the real photo between two rails, a beam sweeping down it. */
function RackFrame({ rack, phase, compact, still }) {
  const files = rack.files || [];
  const urls = useUrls(files);
  const frameRef = useRef(null);
  const [broken, setBroken] = useState(false);
  const state = rack.state || 'reading';
  const moving = state === 'reading' && !still;

  useEffect(() => {
    const el = frameRef.current;
    if (!el) return undefined;
    if (!moving || typeof window.requestAnimationFrame !== 'function') {
      el.style.setProperty('--p', state === 'reading' ? '0' : '1');
      el.style.setProperty('--beam', '0');
      return undefined;
    }
    let raf = 0;
    const start = performance.now() - phase * SWEEP_MS;
    const tick = (now) => {
      const t = ((now - start) % SWEEP_MS) / SWEEP_MS;
      const run = 0.86;
      const k = t < run ? t / run : 1;
      const p = 0.5 - Math.cos(Math.PI * k) / 2;
      const beam = t < run ? Math.min(1, t / 0.06) : 1 - (t - run) / (1 - run);
      el.style.setProperty('--p', p.toFixed(4));
      el.style.setProperty('--beam', beam.toFixed(3));
      raf = window.requestAnimationFrame(tick);
    };
    raf = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(raf);
  }, [moving, phase, state]);

  const stitch = files.length > 1;
  const video = files.length === 1 && isVideo(files[0]);
  let picture;
  if (broken || !urls[0]) {
    picture = <div className={styles.noPic}><span>{files[0]?.name || 'Your photo'}</span></div>;
  } else if (stitch) {
    picture = (
      <div className={styles.stack}>
        {urls.map((u, i) => <img key={i} src={u} alt={`${rack.word || 'Rack'}, part ${i + 1} of ${urls.length}`} />)}
      </div>
    );
  } else if (video) {
    picture = <video className={styles.pic} src={urls[0]} muted playsInline autoPlay loop aria-label={`${rack.word || 'Rack'}, the video being analysed`} />;
  } else {
    picture = <img className={styles.pic} src={urls[0]} alt={`${rack.word || 'Rack'}, being analysed`} onError={() => setBroken(true)} />;
  }

  return (
    <figure className={`${styles.rack} ${styles[`is_${state}`] || ''}`}>
      <div ref={frameRef} className={`${styles.frame} ${compact ? styles.compact : ''} ${still ? styles.still : ''}`}>
        <div className={styles.rail} aria-hidden="true">
          {Array.from({ length: TICKS }, (_, i) => (
            <i key={i} className={(i + 1) % 5 === 0 ? styles.major : undefined} style={{ top: `${((i + 0.5) / TICKS) * 100}%`, '--at': ((i + 0.5) / TICKS).toFixed(4) }}>
              {(i + 1) % 5 === 0 && !compact ? <b>{i + 1}</b> : null}
            </i>
          ))}
        </div>
        <div className={styles.shot}>
          {picture}
          <span className={styles.veil} aria-hidden="true" />
          <span className={styles.trail} aria-hidden="true" />
          <span className={styles.beam} aria-hidden="true" />
          <span className={`${styles.corner} ${styles.tl}`} aria-hidden="true" />
          <span className={`${styles.corner} ${styles.tr}`} aria-hidden="true" />
          <span className={`${styles.corner} ${styles.bl}`} aria-hidden="true" />
          <span className={`${styles.corner} ${styles.br}`} aria-hidden="true" />
          <span className={styles.pulse} aria-hidden="true" />
        </div>
        <div className={`${styles.rail} ${styles.leds}`} aria-hidden="true">
          {Array.from({ length: LEDS }, (_, i) => (
            <i key={i} style={{ top: `${((i + 0.5) / LEDS) * 100}%`, animationDelay: `${((i * 7) % LEDS) * 0.17 + phase}s` }} />
          ))}
        </div>
      </div>
      {rack.word ? (
        <figcaption className={styles.cap}>
          <span className={styles.word}>{rack.word}</span>
          <span className={`${styles.state} ${styles[`st_${state}`] || ''}`}>{STATE_WORD[state] || STATE_WORD.reading}</span>
        </figcaption>
      ) : null}
    </figure>
  );
}

/**
 * Shown while /api/analyze runs, for Scan (one rack) and Map (two racks):
 * the real photos being read, the RackTrack mark, one line that steps
 * through the stages with a thin bar. Nothing else: no Cancel (the owner,
 * 29 Sep 2026) - the answer comes, or a plain sentence says why it did not.
 *
 * racks: [{ key, word?, files: File[], state: 'reading' | 'done' | 'failed' }]
 * finished: the answer has arrived (the line shows Done).
 */
export function AnalysingView({ racks, finished = false, label = 'Analysing the rack' }) {
  const [elapsed, setElapsed] = useState(0);
  const still = useMemo(reducedMotion, []);
  useEffect(() => {
    const t0 = Date.now();
    const id = setInterval(() => setElapsed(Date.now() - t0), 250);
    return () => clearInterval(id);
  }, []);
  const stage = stageAt(elapsed, finished);
  const two = racks.length > 1;

  return (
    <div className={styles.overlay} role="dialog" aria-modal="true" aria-label={label}>
      <div className={styles.inner}>
        <div className={`${styles.racks} ${two ? styles.two : ''}`}>
          {racks.map((r, i) => <RackFrame key={r.key || i} rack={r} phase={i * 0.45} compact={two} still={still} />)}
        </div>
        <div className={styles.foot}>
          <img src="/logo.jpg" alt="RackTrack" data-nozoom="" className={styles.logo} width="30" height="30" />
          <p key={stage.text} className={`${styles.stage} ${finished ? styles.done : ''}`} role="status" aria-live="polite">{stage.text}</p>
          <span
            className={styles.bar}
            role="progressbar"
            aria-label={label}
            aria-valuemin={0}
            aria-valuemax={STAGES.length}
            aria-valuenow={stage.index}
            aria-valuetext={stage.text}
          >
            <i style={{ transform: `scaleX(${stage.progress})` }} />
          </span>
          <span className={styles.dots} aria-hidden="true">
            {STAGES.map((s, i) => <i key={s} className={i < stage.index ? styles.past : i === stage.index ? styles.now : undefined} />)}
          </span>
        </div>
      </div>
    </div>
  );
}

export default AnalysingView;
