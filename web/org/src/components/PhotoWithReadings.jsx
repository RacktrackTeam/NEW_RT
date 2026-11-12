import { useState } from 'react';
import AssetImg from './AssetImg.jsx';
import styles from './PhotoWithReadings.module.css';

const cx = (...a) => a.filter(Boolean).join(' ');

/**
 * The photo is the hero; the readings are drawn on it.
 *
 * readings: [{ id, box: [x1, y1, x2, y2] in photo pixels, tag, status: { word, tone } }]
 * Each reading is an outline with a small white tag, coloured by its status
 * (white when nothing is wrong, red, amber). Tapping one calls onSelect(id).
 * `size` is the photo's own width and height when known; otherwise it is read
 * from the image once it loads.
 * `focus` is a box [x1, y1, x2, y2] to zoom to: the photo is enlarged around
 * it (up to five times) and the tags are put away, so a port can be seen.
 * A reading with `ring: true` is drawn as a heavier ring (a chosen port).
 * `crop` is a box to show alone: the photo is cut to it (see cropBox), so a
 * device is shown without the devices above and below it. With `cropAspect`
 * the cut has that exact shape (width / height) around the box, so the
 * frame keeps one size while the cut moves from a device to one of its ports.
 */
export function PhotoWithReadings({ src, alt = 'Rack photo', readings = [], selectedId, onSelect, size, tags = true, zoomable, focus, focusFill, focusMax, crop, cropAspect, className, children }) {
  const [natural, setNatural] = useState(size || null);
  const [zoomed, setZoomed] = useState(false);
  const nat = size || natural;
  const pct = (v, total) => `${(v / total) * 100}%`;
  const cut = nat && nat.w > 0 && crop ? cropStyle(crop, nat, cropAspect) : null;
  const zoom = !cut && nat && nat.w > 0 ? zoomTo(focus, nat, { fill: focusFill, max: focusMax }) : null;
  const showTags = tags && !zoom && !cut;
  return (
    <div className={cx(styles.stage, zoomed && styles.zoomed, className)}>
      <div className={cx(styles.frame, zoom && styles.focused, cut && styles.cropped)} style={cut ? cut.frame : undefined}>
        <div className={cx(styles.layer, cut && styles.cropLayer)} style={cut ? cut.layer : zoom ? zoom.style : undefined}>
        <AssetImg
          src={src}
          alt={alt}
          className={cx(styles.img, cut && styles.cropImg)}
          onLoad={(e) => { if (!size) setNatural({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight }); }}
        />
        {nat && nat.w > 0 ? readings.map((r) => {
          const [x1, y1, x2, y2] = r.box || [];
          if (![x1, y1, x2, y2].every(Number.isFinite)) return null;
          const tone = r.status?.tone || '';
          const on = selectedId != null && String(selectedId) === String(r.id);
          const Tag = onSelect ? 'button' : 'span';
          return (
            <Tag
              key={r.id}
              type={onSelect ? 'button' : undefined}
              className={cx(styles.ov, tone && styles[tone], on && styles.sel, r.ring && styles.ring)}
              style={{ left: pct(x1, nat.w), top: pct(y1, nat.h), width: pct(x2 - x1, nat.w), height: pct(y2 - y1, nat.h) }}
              onClick={onSelect ? () => onSelect(r.id) : undefined}
              aria-label={onSelect ? [r.tag, r.status?.word].filter(Boolean).join(', ') : undefined}
              aria-pressed={onSelect ? on : undefined}
            >
              {showTags && (r.tag || r.status?.word) ? (
                <span className={styles.tags}>
                  {r.tag ? <span className={styles.tag}>{r.tag}</span> : null}
                  {r.status?.word ? <span className={cx(styles.tag, tone && styles[`t${tone}`])}>{r.status.word}</span> : null}
                </span>
              ) : null}
            </Tag>
          );
        }) : null}
        {children}
        </div>
      </div>
      {zoomable ? (
        <button type="button" className={styles.zoom} onClick={() => setZoomed((z) => !z)} aria-pressed={zoomed}>
          {zoomed ? 'Zoom out' : 'Zoom in'}
        </button>
      ) : null}
    </div>
  );
}

/**
 * The transform that enlarges the photo around box, or null (`fill` and
 * `max` as above). The zoom keeps
 * the photo filling its frame: near an edge the box is shown off-centre
 * rather than with empty space beside it.
 */
export function zoomTo(box, nat, { fill = 0.4, max = 5 } = {}) {
  const [x1, y1, x2, y2] = Array.isArray(box) ? box.map(Number) : [];
  if (![x1, y1, x2, y2].every(Number.isFinite) || !nat?.w || !nat?.h) return null;
  const bw = Math.max(1, x2 - x1) / nat.w;
  const bh = Math.max(1, y2 - y1) / nat.h;
  // `fill` is the share of the frame the box may take: 0.4 for a port, near
  // 1 for a whole device so it fills the frame. `max` caps the enlargement.
  const k = Math.max(1, Math.min(max ?? 5, (fill ?? 0.4) / bw, (fill ?? 0.4) / bh));
  if (k < 1.05) return null;
  const clamp = (v) => Math.min(1 - 0.5 / k, Math.max(0.5 / k, v));
  const cx = clamp((x1 + x2) / 2 / nat.w);
  const cy = clamp((y1 + y2) / 2 / nat.h);
  const r = (v) => Math.round(v * 10000) / 100;
  return {
    k,
    style: {
      transformOrigin: `${r(cx)}% ${r(cy)}%`,
      transform: `translate(${r(0.5 - cx)}%, ${r(0.5 - cy)}%) scale(${Math.round(k * 100) / 100})`,
      '--k': Math.round(k * 100) / 100,
    },
  };
}

/**
 * A box to show alone: the box with a small margin, made no wider than
 * maxAspect times its height (a 1U device is a thin strip; it is shown a
 * little taller so its ports can be seen), kept inside the photo.
 */
export function cropBox(box, nat, { margin = 0.04, maxAspect = 3, aspect = null } = {}) {
  const [x1, y1, x2, y2] = Array.isArray(box) ? box.map(Number) : [];
  if (![x1, y1, x2, y2].every(Number.isFinite) || !nat?.w || !nat?.h) return null;
  if (aspect) {
    // That exact shape around the box's centre, as wide as the box (with its
    // margin) or as tall, whichever needs more, and kept inside the photo.
    let w = Math.max((x2 - x1) * (1 + 2 * margin), (y2 - y1) * (1 + 2 * margin) * aspect);
    let h = w / aspect;
    if (w > nat.w) { w = nat.w; h = w / aspect; }
    if (h > nat.h) { h = nat.h; w = h * aspect; }
    const cxm = (x1 + x2) / 2;
    const cym = (y1 + y2) / 2;
    const a = Math.min(Math.max(0, cxm - w / 2), nat.w - w);
    const t = Math.min(Math.max(0, cym - h / 2), nat.h - h);
    return [a, t, a + w, t + h];
  }
  const mx = (x2 - x1) * margin;
  let a = Math.max(0, x1 - mx);
  let b = Math.min(nat.w, x2 + mx);
  const w = b - a;
  let h = Math.max(y2 - y1, w / maxAspect);
  h = Math.min(h, nat.h);
  let top = (y1 + y2) / 2 - h / 2;
  top = Math.min(Math.max(0, top), nat.h - h);
  return [a, top, b, top + h];
}

/** The frame and layer styles that show only `box` of the photo. */
function cropStyle(box, nat, aspect) {
  const c = cropBox(box, nat, aspect ? { aspect } : undefined);
  if (!c) return null;
  const [a, t, b, bottom] = c;
  const cw = b - a;
  const ch = bottom - t;
  const r = (v) => Math.round(v * 100) / 100;
  return {
    frame: { aspectRatio: `${r(cw)} / ${r(ch)}` },
    layer: { width: `${r((nat.w / cw) * 100)}%`, left: `${r((-a / cw) * 100)}%`, top: `${r((-t / ch) * 100)}%` },
  };
}

export default PhotoWithReadings;
