"""
New edge measurements of the JOIN between two vertically adjacent units (i above j). No device model.

  hough   : long straight horizontal lines near the join (probabilistic Hough on Canny edges)
  bands   : share of columns crossed by an edge, at three band widths
  vcont   : vertical edges continuing across the join (inside one device) vs stopping at it (boundary)
  gap     : darkest / brightest row at the join vs the strips around it (dark gap, lit chassis rim)
  screws  : small round blobs in the rack-ear zones (left / right 7%) close to the join
  hog     : gradient-direction histograms above vs below the join (chi-square distance)
"""
import cv2
import numpy as np

N_FEATS = 21


def _clip(v, lo, hi):
    return int(np.clip(round(v), lo, hi))


def pair_edge_feats(gray, a, b):
    H, W = gray.shape
    x1, x2 = max(a[0], b[0]), min(a[2], b[2])
    w = x2 - x1
    h = ((a[3] - a[1]) + (b[3] - b[1])) / 2
    if w < 16 or h < 4:
        return np.zeros(N_FEATS, np.float32)
    s = (a[3] + b[1]) / 2
    xi1, xi2 = _clip(x1 + 0.08 * w, 0, W - 1), _clip(x2 - 0.08 * w, 1, W)
    y0, y1 = _clip(s - 0.6 * h, 0, H - 1), _clip(s + 0.6 * h, 1, H)
    roi = cv2.GaussianBlur(gray[y0:y1, xi1:xi2], (3, 3), 0)
    if roi.shape[0] < 6 or roi.shape[1] < 12:
        return np.zeros(N_FEATS, np.float32)
    # normalise size: 64 px per unit height
    sc = 64.0 / h
    roi = cv2.resize(roi, (max(16, int(roi.shape[1] * sc)), max(8, int(roi.shape[0] * sc))), interpolation=cv2.INTER_AREA)
    rh, rw = roi.shape
    c = int((s - y0) * sc)                      # join row in roi
    edges = cv2.Canny(roi, 50, 150)
    f = []
    # hough long lines within +-0.15 unit of the join
    band = edges[max(0, c - 10):min(rh, c + 10)]
    lines = cv2.HoughLinesP(band, 1, np.pi / 180, threshold=20, minLineLength=int(0.15 * rw), maxLineGap=int(0.03 * rw) + 2)
    cover = np.zeros(rw, bool); n_long = 0
    if lines is not None:
        for x_a, y_a, x_b, y_b in lines.reshape(-1, 4):
            if abs(y_b - y_a) <= max(2, 0.05 * abs(x_b - x_a)):
                cover[min(x_a, x_b):max(x_a, x_b) + 1] = True; n_long += 1
    f += [cover.mean(), min(n_long, 10) / 10]
    # column coverage at three band widths
    for half in (3, 7, 13):
        bb = edges[max(0, c - half):min(rh, c + half)]
        f.append(bb.any(0).mean() if bb.size else 0.0)
    # vertical-edge continuity
    gx = np.abs(cv2.Sobel(roi.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3))
    up, mid, dn = gx[max(0, c - 28):max(1, c - 6)], gx[max(0, c - 4):min(rh, c + 4)], gx[min(rh - 1, c + 6):min(rh, c + 28)]
    if up.size and dn.size and mid.size:
        pu, pm, pd = up.mean(0), mid.mean(0), dn.mean(0)
        thr = np.percentile(np.concatenate([pu, pd]), 75)
        strong_both = (pu > thr) & (pd > thr)
        f += [strong_both.mean(), ((pm > thr) & strong_both).mean() / max(strong_both.mean(), 1e-3),
              float(np.corrcoef(pu, pd)[0, 1]) if pu.std() > 1e-3 and pd.std() > 1e-3 else 0.0,
              pm.mean() / (0.5 * (pu.mean() + pd.mean()) + 1e-3)]
    else:
        f += [0.0, 0.0, 0.0, 1.0]
    # dark gap / bright rim at the join
    rows = roi.astype(np.float32).mean(1)
    near = rows[max(0, c - 6):min(rh, c + 6)]
    far = np.concatenate([rows[max(0, c - 30):max(0, c - 12)], rows[min(rh, c + 12):min(rh, c + 30)]])
    ref = np.median(far) if far.size else rows.mean()
    f += [(ref - near.min()) / 255 if near.size else 0.0, (near.max() - ref) / 255 if near.size else 0.0,
          (np.argmin(near) - len(near) / 2) / max(len(near), 1) if near.size else 0.0]
    # screws in the ear zones near the join (small, compact, high-contrast blobs)
    ears = 0
    for xs in (slice(0, max(2, int(0.07 * rw))), slice(min(rw - 2, int(0.93 * rw)), rw)):
        z = roi[max(0, c - 22):min(rh, c + 22), xs]
        if z.size < 20:
            continue
        bw = cv2.adaptiveThreshold(z, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 11, 8)
        n, _, st, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
        for k in range(1, n):
            ww, hh, ar = st[k, 2], st[k, 3], st[k, 4]
            if 6 <= ar <= 120 and 0.6 <= ww / max(hh, 1) <= 1.6 and ar / max(ww * hh, 1) > 0.45:
                ears += 1
    f.append(min(ears, 8) / 8)
    # gradient-direction histograms above vs below
    gy = cv2.Sobel(roi.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
    ang = (np.arctan2(gy, gx * np.sign(cv2.Sobel(roi.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3) + 1e-6)) % np.pi)
    mag = np.hypot(gx, gy)

    def hist(sl):
        hh, _ = np.histogram(ang[sl], bins=8, range=(0, np.pi), weights=mag[sl])
        return hh / (hh.sum() + 1e-6)
    ha, hb = hist(slice(max(0, c - 32), max(1, c - 6))), hist(slice(min(rh - 1, c + 6), min(rh, c + 32)))
    f.append(0.5 * float(((ha - hb) ** 2 / (ha + hb + 1e-6)).sum()))
    # edge density above / below / at the join
    for sl in (slice(max(0, c - 32), max(1, c - 6)), slice(min(rh - 1, c + 6), min(rh, c + 32)), slice(max(0, c - 4), min(rh, c + 4))):
        e = edges[sl]
        f.append(e.mean() / 255 if e.size else 0.0)
    # brightness & contrast difference of the two halves
    A, B = roi[max(0, c - 32):max(1, c - 6)].astype(np.float32), roi[min(rh - 1, c + 6):min(rh, c + 32)].astype(np.float32)
    f += [abs(A.mean() - B.mean()) / 255 if A.size and B.size else 0.0, abs(A.std() - B.std()) / 255 if A.size and B.size else 0.0]
    # geometry
    f += [(b[1] - a[3]) / h, np.log((a[3] - a[1]) / max(b[3] - b[1], 1))]
    out = np.nan_to_num(np.array(f, np.float32))
    assert len(out) == N_FEATS, len(out)
    return out

