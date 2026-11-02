import { useCallback, useEffect, useRef, useState } from 'react';
import { apiUrl } from '../utils/api';
import styles from './PhotoZoom.module.css';

/**
 * A closer look at any picture: tap an image anywhere in the app and it
 * opens full screen. Pinch or double-tap to zoom, drag to move, the wheel on
 * a laptop. A cropped picture (a tag cut from the rack photo, data-zoom-src
 * and data-zoom-box) opens the whole photo already zoomed on that part.
 *
 * Images inside a button or a link keep their own action, and an image with
 * data-nozoom (the logo, icons) is left alone.
 */
const MAX = 8;

function Viewer({ src, box, onClose }) {
  const stage = useRef(null);
  const [nat, setNat] = useState(null);
  const [view, setView] = useState({ k: 1, x: 0, y: 0 });
  const touch = useRef(null);
  const lastTap = useRef(0);

  // The size of the picture at k = 1: the whole picture fitted to the screen.
  const fit = useCallback(() => {
    const el = stage.current;
    if (!el || !nat) return null;
    const W = el.clientWidth;
    const H = el.clientHeight;
    const s = Math.min(W / nat.w, H / nat.h);
    return { W, H, s, w: nat.w * s, h: nat.h * s };
  }, [nat]);

  const clamp = useCallback((v) => {
    const f = fit();
    if (!f) return v;
    const k = Math.min(MAX, Math.max(1, v.k));
    const mx = Math.max(0, (f.w * k - f.W) / 2);
    const my = Math.max(0, (f.h * k - f.H) / 2);
    return { k, x: Math.min(mx, Math.max(-mx, v.x)), y: Math.min(my, Math.max(-my, v.y)) };
  }, [fit]);

  // Open on the part asked for, else the whole picture.
  useEffect(() => {
    const f = fit();
    if (!f) return;
    if (Array.isArray(box) && box.length === 4) {
      const bw = Math.max(8, box[2] - box[0]) * f.s;
      const bh = Math.max(8, box[3] - box[1]) * f.s;
      // The part spans the screen's width (a device is wide and short), or
      // 60% of its height when it is tall.
      const k = Math.min(MAX, Math.max(1, Math.min((f.W * 0.95) / bw, (f.H * 0.6) / bh)));
      const cx = ((box[0] + box[2]) / 2) * f.s - f.w / 2;
      const cy = ((box[1] + box[3]) / 2) * f.s - f.h / 2;
      setView(clamp({ k, x: -cx * k, y: -cy * k }));
    } else {
      setView({ k: 1, x: 0, y: 0 });
    }
  }, [fit, box, clamp]);

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Zoom by a factor around a point on the screen (relative to the stage centre).
  const zoomAt = (factor, px, py) => setView((v) => {
    const k = Math.min(MAX, Math.max(1, v.k * factor));
    const r = k / v.k;
    return clamp({ k, x: px - (px - v.x) * r, y: py - (py - v.y) * r });
  });
  const centred = (clientX, clientY) => {
    const r = stage.current.getBoundingClientRect();
    return [clientX - r.left - r.width / 2, clientY - r.top - r.height / 2];
  };

  const lastTouch = useRef(0);
  const onTouchStart = (e) => {
    lastTouch.current = Date.now();
    if (e.touches.length === 2) {
      lastTap.current = 0;
      const [a, b] = e.touches;
      touch.current = { pinch: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY), mid: centred((a.clientX + b.clientX) / 2, (a.clientY + b.clientY) / 2) };
    } else if (e.touches.length === 1) {
      const t = e.touches[0];
      const now = Date.now();
      if (now - lastTap.current < 300) {
        const [px, py] = centred(t.clientX, t.clientY);
        if (view.k > 1.2) setView({ k: 1, x: 0, y: 0 }); else zoomAt(3, px, py);
        lastTap.current = 0;
        touch.current = null;
        return;
      }
      lastTap.current = now;
      touch.current = { pan: [t.clientX, t.clientY] };
    }
  };
  const onTouchMove = (e) => {
    const t0 = touch.current;
    if (!t0) return;
    e.preventDefault();
    if (e.touches.length === 2 && t0.pinch) {
      const [a, b] = e.touches;
      const d = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      zoomAt(d / t0.pinch, t0.mid[0], t0.mid[1]);
      touch.current = { ...t0, pinch: d };
    } else if (e.touches.length === 1 && t0.pan) {
      const t = e.touches[0];
      const dx = t.clientX - t0.pan[0];
      const dy = t.clientY - t0.pan[1];
      setView((v) => clamp({ ...v, x: v.x + dx, y: v.y + dy }));
      touch.current = { pan: [t.clientX, t.clientY] };
    }
  };
  const drag = useRef(null);
  const onMouseDown = (e) => { if (Date.now() - lastTouch.current < 1000) return; drag.current = [e.clientX, e.clientY]; };
  const onMouseMove = (e) => {
    if (!drag.current) return;
    const dx = e.clientX - drag.current[0];
    const dy = e.clientY - drag.current[1];
    drag.current = [e.clientX, e.clientY];
    setView((v) => clamp({ ...v, x: v.x + dx, y: v.y + dy }));
  };
  const onWheel = (e) => {
    const [px, py] = centred(e.clientX, e.clientY);
    zoomAt(e.deltaY < 0 ? 1.2 : 1 / 1.2, px, py);
  };
  const onDouble = (e) => {
    // A double tap on a phone is handled by the touch code above; the mouse
    // double-click the browser then makes from it would undo that zoom.
    if (Date.now() - lastTouch.current < 1000) return;
    const [px, py] = centred(e.clientX, e.clientY);
    if (view.k > 1.2) setView({ k: 1, x: 0, y: 0 }); else zoomAt(3, px, py);
  };

  const f = fit();
  return (
    <div className={styles.zoom} role="dialog" aria-modal="true" aria-label="Closer look">
      <div
        ref={stage}
        className={styles.stage}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={() => { touch.current = null; }}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={() => { drag.current = null; }}
        onMouseLeave={() => { drag.current = null; }}
        onWheel={onWheel}
        onDoubleClick={onDouble}
      >
        <img
          src={src}
          alt="Closer look"
          data-nozoom=""
          draggable={false}
          onLoad={(e) => setNat({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
          style={f ? { width: f.w, height: f.h, transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})` } : { visibility: 'hidden' }}
        />
      </div>
      <div className={styles.bar}>
        <span className={styles.hint}>Pinch or double-tap to zoom</span>
        <button type="button" className={styles.close} onClick={onClose}>Close</button>
      </div>
    </div>
  );
}

export function PhotoZoom() {
  const [open, setOpen] = useState(null);
  useEffect(() => {
    const onClick = (e) => {
      const img = e.target instanceof Element ? e.target.closest('img') : null;
      if (!img || img.closest('[data-nozoom], button, a, [role="tab"], [role="dialog"][aria-label="Closer look"]')) return;
      if (img.hasAttribute('data-nozoom') || img.clientWidth < 24) return;
      // data-zoom-src is a server path (the whole rack photo): it gets the
      // API address and the asset token the way AssetImg's own source does.
      const zoomSrc = img.getAttribute('data-zoom-src');
      const src = zoomSrc ? (/^(blob:|data:|https?:)/.test(zoomSrc) ? zoomSrc : apiUrl(zoomSrc)) : img.currentSrc || img.src;
      if (!src) return;
      const raw = img.getAttribute('data-zoom-box');
      const box = raw ? raw.split(',').map(Number) : null;
      e.preventDefault();
      e.stopPropagation();
      setOpen({ src, box: box && box.length === 4 && box.every(Number.isFinite) ? box : null });
    };
    document.addEventListener('click', onClick, true);
    return () => document.removeEventListener('click', onClick, true);
  }, []);
  if (!open) return null;
  return <Viewer src={open.src} box={open.box} onClose={() => setOpen(null)} />;
}

export default PhotoZoom;
