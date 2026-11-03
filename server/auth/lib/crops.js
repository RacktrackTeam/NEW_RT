/**
 * Small cut-outs of a scan's pictures for the screens: a device on the rack
 * photo, a port, a label on a zone close-up. Written once into the scan's
 * crops/ folder (lib/store.js) and served by GET /api/scans/:scanId/files/crops/<name>.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const sharp = require('sharp');
const store = require('./store');

const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.jpg$/;
const building = new Map();

const fileUrl = (scanId, rel) => `/api/scans/${encodeURIComponent(scanId)}/files/${rel}`;

/**
 * Cut box [x1, y1, x2, y2] (pixels of src) with some margin and save it as
 * crops/<name>. Returns the file URL, or null when it cannot be made.
 */
async function ensureCrop(scanId, name, src, box, { pad = 0.15, minPad = 8, width = 640, mark = false } = {}) {
  if (!NAME_RE.test(name) || !Array.isArray(box) || box.length < 4 || !src) return null;
  let dest;
  try {
    const dir = store.file(scanId, 'crops');
    fs.mkdirSync(dir, { recursive: true });
    dest = path.join(dir, name);
  } catch { return null; }
  if (fs.existsSync(dest)) return fileUrl(scanId, `crops/${name}`);
  if (building.has(dest)) return building.get(dest);
  const run = (async () => {
    try {
      if (!fs.existsSync(src)) return null;
      const meta = await sharp(src).metadata();
      const W = meta.width || 0;
      const H = meta.height || 0;
      const [x1, y1, x2, y2] = box.map(Number);
      const px = Math.max(minPad, (x2 - x1) * pad);
      const py = Math.max(minPad, (y2 - y1) * pad);
      const left = Math.max(0, Math.floor(x1 - px));
      const top = Math.max(0, Math.floor(y1 - py));
      const right = Math.min(W, Math.ceil(x2 + px));
      const bottom = Math.min(H, Math.ceil(y2 + py));
      if (right - left < 4 || bottom - top < 4) return null;
      const tmp = `${dest}.${process.pid}.tmp`;
      let img = sharp(src).extract({ left, top, width: right - left, height: bottom - top });
      if (mark) {
        // Ring the thing asked about, so a crop with its neighbours in it
        // still shows which one the question is on.
        const cw = right - left;
        const ch = bottom - top;
        const t = Math.max(2, Math.round(Math.min(cw, ch) / 60));
        const rx = Math.max(0, Math.round(x1 - left));
        const ry = Math.max(0, Math.round(y1 - top));
        const rw = Math.max(t * 2, Math.min(cw - rx, Math.round(x2 - x1)));
        const rh = Math.max(t * 2, Math.min(ch - ry, Math.round(y2 - y1)));
        const bar = (w, h) => ({ create: { width: w, height: h, channels: 4, background: '#f79009' } });
        img = sharp(await img.png().toBuffer()).composite([
          { input: bar(rw, t), left: rx, top: ry },
          { input: bar(rw, t), left: rx, top: Math.min(ch - t, ry + rh - t) },
          { input: bar(t, rh), left: rx, top: ry },
          { input: bar(t, rh), left: Math.min(cw - t, rx + rw - t), top: ry },
        ]);
      }
      await img
        .resize({ width, withoutEnlargement: true })
        .flatten({ background: '#ffffff' })
        .jpeg({ quality: 85 }).toFile(tmp);
      fs.renameSync(tmp, dest);
      return fileUrl(scanId, `crops/${name}`);
    } catch { return null; }
  })().finally(() => building.delete(dest));
  building.set(dest, run);
  return run;
}

module.exports = { ensureCrop, fileUrl };
