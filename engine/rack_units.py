"""
Rack-unit (U) identification from a front photo of a 19" rack - classical CV, no ML model.

Core idea
---------
Every rack-mount device is an integer number of rack units (U) tall, and a U is a
fixed fraction of the rack width (EIA-310: 1U = 1.75", faceplate = 19").  So:

  1. Rectify the photo (rotation + keystone) so faceplate edges are horizontal.
  2. Get a *scale prior* for U from the faceplate width:  U ~= width * 1.75 / 19.
  3. Build a per-row "device boundary" score: how much of the faceplate span is covered
     by a continuous horizontal edge, with extra weight on the ear/flange zones (device
     edges run through the ears, interior details like vents/drive bays do not).
  4. Jointly search U (inside the prior window) and the segmentation of the rack into
     devices with dynamic programming, under the constraint that each device spans
     n * U (n = 1..MAX_U).  Each device is measured independently, so a device that is
     mounted one hole off-grid is still counted correctly.
  5. Derive the global U grid (phase), number the slots, label empty gaps and flag
     off-grid devices.

Usage:  python rack_units.py image1.jpg [image2.jpg ...] [--out outdir] [--debug]
        -> outdir/<name>_boxes.png (bounding boxes on the original photo) + <name>_units.json
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, asdict, field

import cv2
import numpy as np
from scipy.signal import find_peaks

# ---- EIA-310 constants -------------------------------------------------------------
U_INCH = 1.75
FACEPLATE_INCH = 19.0
U_PER_WIDTH = U_INCH / FACEPLATE_INCH          # ~0.0921

# ---- tunables (all relative, so they survive resolution changes) -------------------
WORK_WIDTH = 1000          # images are resampled to this width
MAX_DEVICE_U = 8           # tallest device we try to explain
PRIOR_LO, PRIOR_HI = 0.85, 1.15   # U search window around the width prior
FIT_TOL = 0.12             # |height/U - n| tolerance (fraction of U)
EMPTY_HOLES = 0.6          # fraction of expected rail-hole gaps visible => slot is empty


@dataclass
class Segment:
    kind: str              # "device" | "empty" | "irregular" (not an n*U block) | "frame"
    top: float             # y in original image pixels
    bottom: float
    units: int             # rounded U count
    units_exact: float     # height / U
    slot_from: int = 0     # 1-based slot index from the top of the detected stack
    slot_to: int = 0
    off_grid: bool = False
    confidence: float = 0.0
    holes: float = 0.0     # fraction of expected rail holes visible (high -> empty)
    bbox: list = field(default_factory=list)   # [x1, y1, x2, y2] in original image pixels


@dataclass
class Result:
    image: str
    u_pitch_px: float      # in original-image pixels
    total_units: int
    segments: list = field(default_factory=list)


# =====================================================================================
# 1. Geometry normalisation
# =====================================================================================
def _segments(gray):
    lsd = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
    lines = lsd.detect(gray)[0]
    if lines is None:
        return np.zeros((0, 4), np.float32)
    return lines.reshape(-1, 4)


def _weighted_median(values, weights):
    if len(values) == 0:
        return 0.0
    o = np.argsort(values)
    cw = np.cumsum(weights[o])
    return float(values[o][np.searchsorted(cw, cw[-1] / 2)])


def rectify(img):
    """Remove in-plane rotation and left/right keystone (converging rails).

    Rotation: length-weighted median angle of long near-horizontal segments (faceplate
    edges are numerous and reliable).  Keystone: near-vertical segments in the left and
    right thirds give two rail directions; if they converge we warp them parallel.
    Returns rectified image and the 3x3 homography (original -> rectified).
    """
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    L = _segments(gray)
    dx, dy = L[:, 2] - L[:, 0], L[:, 3] - L[:, 1]
    ln = np.hypot(dx, dy)
    ang = (np.degrees(np.arctan2(dy, dx)) + 90) % 180 - 90       # (-90, 90]

    hz = (np.abs(ang) < 12) & (ln > 0.08 * w)
    rot = _weighted_median(ang[hz], ln[hz] ** 2) if hz.sum() >= 3 else 0.0
    R = cv2.getRotationMatrix2D((w / 2, h / 2), rot, 1.0)
    Hm = np.vstack([R, [0, 0, 1]])

    # keystone from near-vertical lines after rotation
    vt = (np.abs(np.abs(ang) - 90) < 12) & (ln > 0.10 * h)
    if vt.sum() >= 4:
        pts = L[vt].reshape(-1, 2, 2)
        pts = cv2.perspectiveTransform(pts.reshape(-1, 1, 2), Hm).reshape(-1, 2, 2)
        xm = pts[:, :, 0].mean(1)
        # tilt = dx/dy (0 for perfectly vertical)
        tilt = (pts[:, 1, 0] - pts[:, 0, 0]) / np.where(np.abs(pts[:, 1, 1] - pts[:, 0, 1]) < 1e-6,
                                                         1e-6, pts[:, 1, 1] - pts[:, 0, 1])
        lw = ln[vt]
        left, right = xm < w / 3, xm > 2 * w / 3
        if left.sum() >= 2 and right.sum() >= 2:
            tl = _weighted_median(tilt[left], lw[left] ** 2)
            tr = _weighted_median(tilt[right], lw[right] ** 2)
            # only correct mild, plausible convergence (rails lean towards each other)
            if abs(tl - tr) > 0.006 and abs(tl) < 0.2 and abs(tr) < 0.2:
                xl, xr = np.median(xm[left]), np.median(xm[right])
                yc = h / 2
                src = np.float32([[xl - tl * yc, 0], [xr - tr * yc, 0],
                                  [xr + tr * (h - yc), h], [xl + tl * (h - yc), h]])
                dst = np.float32([[xl, 0], [xr, 0], [xr, h], [xl, h]])
                Hm = cv2.getPerspectiveTransform(src, dst) @ Hm

    out = cv2.warpPerspective(img, Hm, (w, h), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_REPLICATE)
    return out, Hm, rot


# =====================================================================================
# 2-3. Edge evidence
# =====================================================================================
def _runs(row):
    d = np.diff(np.r_[0, row.astype(np.int8), 0])
    return np.flatnonzero(d == 1), np.flatnonzero(d == -1)


def horizontal_edge_mask(gray):
    g = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 1.2)
    gy = np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1))
    gx = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0))
    thr = max(np.percentile(gy, 80), 8.0)
    m = ((gy > 2 * gx) & (gy > thr)).astype(np.uint8)
    m = cv2.dilate(m, np.ones((3, 1), np.uint8))                       # 1px wobble
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((1, 21), np.uint8))  # bridge small gaps
    return m


def faceplate_span(m):
    """Estimate faceplate x-extent [xa, xb] from the longest horizontal edge runs."""
    H, W = m.shape
    rows = []
    for y in range(H):
        s, e = _runs(m[y])
        if len(s):
            k = np.argmax(e - s)
            if (e[k] - s[k]) < 0.97 * W:            # skip wall/background lines
                rows.append((e[k] - s[k], s[k], e[k]))
    if not rows:
        return 0.05 * W, 0.95 * W
    rows = np.array(sorted(rows, reverse=True)[:max(10, H // 25)], float)
    xa, xb = np.median(rows[:, 1]), np.median(rows[:, 2])
    return float(xa), float(xb)


def boundary_profile(m, xa, xb):
    """Per-row device-boundary score in [0, 1]."""
    H, W = m.shape
    xa_i, xb_i = int(max(0, xa)), int(min(W, xb))
    span = max(1, xb_i - xa_i)
    ear = max(3, int(0.09 * span))
    sub = m[:, xa_i:xb_i].astype(np.float32)

    cover = sub.mean(1)
    left = sub[:, :ear].mean(1)
    right = sub[:, -ear:].mean(1)
    longest = np.zeros(H, np.float32)
    for y in range(H):
        s, e = _runs(sub[y] > 0)
        if len(s):
            longest[y] = (e - s).max() / span
    # one ear is often hidden by cables: trust the clearer ear, but reward agreement
    ears = 0.5 * np.maximum(left, right) + 0.5 * np.minimum(left, right)
    # ears dominate: interior details (vents, drive-bay rows, labels) stop short of them
    b = 0.25 * cover + 0.15 * longest + 0.60 * ears
    # tolerate the 1-2px misalignment between edge rows
    b = np.maximum.reduce([b, np.r_[b[1:], 0], np.r_[0, b[:-1]]])
    return b


# =====================================================================================
# 4a. U pitch: phase-invariant fit on boundary *differences* + width prior
# =====================================================================================
def fit_pitch(b, U_prior, strong=0.7, debug=False, alt_priors=()):
    """A device's height is n*U wherever it is mounted, so the distances between strong
    boundaries cluster at integer multiples of U even when a device sits one hole
    off-grid.  Score(U) = weighted Rayleigh coherence of those distances (phase-free),
    plus a log-normal pull towards the nearest 19"-width estimate.  `alt_priors` are
    extra width-based estimates (e.g. from the ear edges); the coherence decides which
    one the photo agrees with."""
    priors = [U_prior] + [u for u in alt_priors if u > 0]
    pk, _ = find_peaks(b, height=0.3, distance=max(3, int(0.12 * min(priors))))
    S = pk[b[pk] >= strong]
    Us = np.arange(PRIOR_LO * min(priors), PRIOR_HI * max(priors), 0.002 * U_prior)
    if len(S) >= 3:
        w = b[S]
        D = S[None, :] - S[:, None]
        Wt = (w[None, :] * w[:, None])
        sel = (D > 0.6 * min(priors)) & (D < 4.5 * PRIOR_HI * max(priors))
        d, wt = D[sel].astype(float), Wt[sel]
        coh = np.array([np.sum(wt * np.cos(2 * np.pi * d / u)) for u in Us]) / max(wt.sum(), 1e-6)
    else:
        coh = np.zeros_like(Us)
    prior = np.max([np.exp(-np.log(Us / p) ** 2 / (2 * 0.05 ** 2)) for p in priors], axis=0)
    score = coh + 0.35 * prior
    U = float(Us[int(np.argmax(score))])
    if debug:
        print(f"    pitch: priors={[round(p, 1) for p in priors]} strong={len(S)} -> U={U:.2f}")
    return U


def _vertical_edge_profile(gray):
    H = gray.shape[0]
    g = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 1.2)
    gx = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0))
    gy = np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1))
    v = ((gx > 1.5 * gy) & (gx > np.percentile(gx, 80))).astype(np.uint8)
    v = cv2.morphologyEx(v, cv2.MORPH_OPEN, np.ones((max(5, H // 50), 1), np.uint8))
    return v.mean(0)


def _local_coherence(b, U, strong=0.7):
    """Best boundary-distance coherence within +-7% of U (see fit_pitch)."""
    pk, _ = find_peaks(b, height=0.3, distance=max(3, int(0.12 * U)))
    S = pk[b[pk] >= strong]
    if len(S) < 3:
        return 0.0, U
    w = b[S]
    D = S[None, :] - S[:, None]
    Wt = w[None, :] * w[:, None]
    sel = (D > 0.6 * U) & (D < 6 * U)
    d, wt = D[sel].astype(float), Wt[sel]
    if wt.sum() <= 0:
        return 0.0, U
    Us = np.arange(0.93 * U, 1.07 * U, 0.005 * U)
    c = [np.sum(wt * np.cos(2 * np.pi * d / u)) / wt.sum() for u in Us]
    i = int(np.argmax(c))
    return float(c[i]), float(Us[i])


def rack_span_search(gray, m, xa, xb):
    """Find the rack width without trusting horizontal edges (cables break them up).
    Candidates: the edge-run span plus every pair of strong long vertical edges (rails,
    ear edges, posts) that is at least 45% of the image wide and roughly centred.
    Each candidate implies a U size (width * 1.75/19); the photo's device boundaries,
    scored with that span's ear zones, must repeat at that U.  Among near-equal fits the
    largest U wins, because boundary spacing also fits U/2, U/3, ..."""
    H, W = gray.shape
    prof = _vertical_edge_profile(gray)
    pk, _ = find_peaks(prof, height=0.12, distance=8)
    pk = sorted(pk, key=lambda p: -prof[p])[:14]
    cands = [(xa, xb)]
    for l in pk:
        for r in pk:
            if r - l >= 0.45 * W and abs((l + r) / 2 - W / 2) < 0.2 * W:
                cands.append((float(l), float(r)))
    scored = []
    for l, r in cands:
        U = (r - l) * U_PER_WIDTH
        c, Ub = _local_coherence(boundary_profile(m, l, r), U)
        scored.append((c, Ub, l, r))
    best = max(s[0] for s in scored)
    near = [s for s in scored if s[0] >= 0.85 * best]
    c, Ub, l, r = max(near, key=lambda s: s[1])
    return l, r, Ub, c


def faceplate_candidates(gray, xa, xb):
    """Alternative faceplate extents from vertical edges: when the horizontal edge runs
    stop where dark ears with screws begin, the run-based span is too narrow.  Ear outer
    edges (and rack posts) show up as long vertical edges, symmetric about the rack centre,
    0-20% wider than the run-based span."""
    H, W = gray.shape
    g = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 1.2)
    gx = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0))
    gy = np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1))
    v = ((gx > 1.5 * gy) & (gx > np.percentile(gx, 80))).astype(np.uint8)
    v = cv2.morphologyEx(v, cv2.MORPH_OPEN, np.ones((max(5, H // 50), 1), np.uint8))
    prof = v.mean(0)
    pk, _ = find_peaks(prof, height=0.25, distance=6)
    sp, cx = xb - xa, (xa + xb) / 2
    left = [p for p in pk if p < xa + 0.01 * sp]
    right = [p for p in pk if p > xb - 0.01 * sp]
    cands = []
    for l in left:
        for r in right:
            wdt = r - l
            if 1.02 * sp <= wdt <= 1.2 * sp and abs((l + r) / 2 - cx) < 0.03 * sp:
                cands.append((float(l), float(r)))
    return cands


# =====================================================================================
# 4a'. U grid read directly from the rail holes (when the rails are visible)
# =====================================================================================
EIA_HOLES = (0.25 / 1.75, 0.875 / 1.75, 1.5 / 1.75)   # hole centres within a U (0.143, 0.5, 0.857)
FOLD_BINS = 60


def _eia_template(width=0.06):
    t = np.arange(FOLD_BINS) / FOLD_BINS
    tm = np.zeros(FOLD_BINS)
    for h in EIA_HOLES:
        d = np.minimum(np.abs(t - h), 1 - np.abs(t - h))
        tm += np.exp(-d ** 2 / (2 * width ** 2))
    return (tm - tm.mean()) / tm.std()


def rail_phase(gray, U):
    """With U already known, read only WHERE the U lines fall from the rail holes: fold
    each outer column at U, match the EIA template, take the best column per side.
    Returns (phase or None, left, right); phase only if both rails agree within 0.12U."""
    H, W = gray.shape
    g = gray.astype(np.float32)
    bw = 6
    cs = np.cumsum(np.pad(g, ((0, 0), (1, 0))), axis=1)
    xs = np.arange(0, W - bw, 2)
    xs = xs[(xs < 0.35 * W) | (xs > 0.65 * W)]
    P = (cs[:, xs + bw] - cs[:, xs]) / bw
    P = P - cv2.GaussianBlur(P, (1, 0), sigmaX=0.01, sigmaY=10)
    bins = ((np.arange(H) % U) / U * FOLD_BINS).astype(int)
    F = np.zeros((FOLD_BINS, len(xs)))
    np.add.at(F, bins, P)
    F /= np.maximum(np.bincount(bins, minlength=FOLD_BINS), 1)[:, None]
    energy = F.var(0) / (P.var(0) + 1e-6)
    Fz = (F - F.mean(0)) / (F.std(0) + 1e-6)
    TF = np.conj(np.fft.rfft(_eia_template()))
    C = np.fft.irfft(np.fft.rfft(-Fz, axis=0) * TF[:, None], n=FOLD_BINS, axis=0) / FOLD_BINS
    sc = np.clip(C.max(0), 0, None) * energy
    ph = C.argmax(0) / FOLD_BINS * U
    side = []
    for mask in (xs < 0.5 * W, xs > 0.5 * W):
        idx = np.flatnonzero(mask)
        i = idx[int(np.argmax(sc[idx]))]
        side.append(dict(x=float(xs[i] + bw / 2), phase=float(ph[i]), score=float(sc[i])))
    L, Rr = side
    dphi = abs(((L["phase"] - Rr["phase"]) + U / 2) % U - U / 2)
    if dphi <= 0.25 * U and min(L["score"], Rr["score"]) >= 0.1:
        z = np.exp(2j * np.pi * L["phase"] / U) + np.exp(2j * np.pi * Rr["phase"] / U)
        return (np.angle(z) / (2 * np.pi) * U) % U, L, Rr
    return None, L, Rr


def rail_grid(gray, debug=False):
    """Read U size and U-line positions straight from the rail holes.

    Every column band in the outer 35% of each side is folded at each candidate U (all
    units stacked onto one).  At the true U the EIA pattern - three holes at 0.143 / 0.5 /
    0.857 of the unit, gaps 5/8", 5/8", 1/2" - adds up; at any other U it averages away.
    Score = (energy kept by the fold) x (match with the EIA template).  The template
    position gives where the U lines fall.

    Only trusted when the LEFT and RIGHT rails independently agree on U (within 3%);
    the phase is used only if they also agree on where the lines fall."""
    H, W = gray.shape
    g = gray.astype(np.float32)
    bw = 6
    cs = np.cumsum(np.pad(g, ((0, 0), (1, 0))), axis=1)
    xs = np.arange(0, W - bw, 4)
    xs = xs[(xs < 0.35 * W) | (xs > 0.65 * W)]
    P = (cs[:, xs + bw] - cs[:, xs]) / bw
    P = P - cv2.GaussianBlur(P, (1, 0), sigmaX=0.01, sigmaY=10)    # keep hole-sized detail only
    var = P.var(0) + 1e-6
    TF = np.conj(np.fft.rfft(_eia_template()))
    y = np.arange(H)
    best = np.full(len(xs), -1.0)
    bestU = np.zeros(len(xs))
    bestph = np.zeros(len(xs))
    for U in np.arange(0.03 * W, 0.11 * W, 0.5):
        bins = ((y % U) / U * FOLD_BINS).astype(int)
        F = np.zeros((FOLD_BINS, len(xs)))
        np.add.at(F, bins, P)
        F /= np.maximum(np.bincount(bins, minlength=FOLD_BINS), 1)[:, None]
        energy = F.var(0) / var
        Fz = (F - F.mean(0)) / (F.std(0) + 1e-6)
        C = np.fft.irfft(np.fft.rfft(-Fz, axis=0) * TF[:, None], n=FOLD_BINS, axis=0) / FOLD_BINS
        sc = np.clip(C.max(0), 0, None) * energy
        upd = sc > best
        best[upd], bestU[upd], bestph[upd] = sc[upd], U, C.argmax(0)[upd]
    side = []
    for mask in (xs < 0.5 * W, xs > 0.5 * W):
        idx = np.flatnonzero(mask)
        i = idx[int(np.argmax(best[idx]))]
        side.append(dict(x=float(xs[i] + bw / 2), U=float(bestU[i]), phase=float(bestph[i]) / FOLD_BINS * bestU[i],
                         score=float(best[i])))
    L, Rr = side
    agree_U = abs(np.log(L["U"] / Rr["U"])) <= 0.03 and min(L["score"], Rr["score"]) >= 0.1
    U = (L["U"] + Rr["U"]) / 2
    dphi = abs(((L["phase"] - Rr["phase"]) + U / 2) % U - U / 2)
    agree_phase = agree_U and dphi <= 0.12 * U
    phase = None
    if agree_phase:
        z = np.exp(2j * np.pi * L["phase"] / U) + np.exp(2j * np.pi * Rr["phase"] / U)
        phase = (np.angle(z) / (2 * np.pi) * U) % U
    if debug:
        print(f"    rail grid: L U={L['U']:.1f} ph={L['phase']:.0f} s={L['score']:.2f} | "
              f"R U={Rr['U']:.1f} ph={Rr['phase']:.0f} s={Rr['score']:.2f} -> "
              f"{'U agrees' if agree_U else 'no agreement'}{', phase agrees' if agree_phase else ''}")
    return dict(ok=agree_U, U=U, phase=phase, x_left=L["x"], x_right=Rr["x"], left=L, right=Rr)


# =====================================================================================
# 4b. Segmentation with fixed U (dynamic programming)
# =====================================================================================
def segment_dp(b, U, tau=0.55, max_u=MAX_DEVICE_U, tol=FIT_TOL):
    """Choose a chain of boundary rows.  Consecutive chain rows form a 'unit block' if
    their distance is ~n*U (bonus), otherwise an 'irregular' block (penalty).
    Node reward is (b - tau), so weak edges are only used when they complete n*U blocks."""
    pk, _ = find_peaks(b, height=0.25, distance=max(3, int(0.1 * U)))
    ys, bs = pk.astype(float), b[pk]
    n = len(ys)
    if n < 2:
        return ys, bs, [], []
    best = bs - tau
    prev = -np.ones(n, int)
    kind = [None] * n
    for j in range(n):
        for i in range(j):
            d = ys[j] - ys[i]
            if d < 0.6 * U:
                continue
            k = d / U
            r = int(round(k))
            if 1 <= r <= max_u and abs(k - r) <= tol:
                s = best[i] + (bs[j] - tau)
                kd = ("unit", r)
            else:
                s = best[i] + (bs[j] - tau) - 0.3
                kd = ("irregular", k)
            if s > best[j]:
                best[j], prev[j], kind[j] = s, i, kd
    j = int(np.argmax(best))
    chain, kinds = [j], []
    while prev[j] >= 0:
        kinds.append(kind[j])
        j = prev[j]
        chain.append(j)
    return ys, bs, chain[::-1], kinds[::-1]


def is_frame_line(m, y, xa, xb):
    """Rack frame / cabinet edges run past the faceplate span on both sides."""
    sp = xb - xa
    y0, y1 = int(max(0, y - 1)), int(min(m.shape[0], y + 2))
    l0, l1 = int(max(0, xa - 0.06 * sp)), int(max(0, xa - 0.02 * sp))
    r0, r1 = int(min(m.shape[1], xb + 0.02 * sp)), int(min(m.shape[1], xb + 0.06 * sp))
    if l1 - l0 < 3 or r1 - r0 < 3:
        return False
    return m[y0:y1, l0:l1].max(0).mean() > 0.45 and m[y0:y1, r0:r1].max(0).mean() > 0.45


GRID_SPLIT_MIN = 0.38     # every internal grid row of a block must reach this to split it


def split_stacked_blocks(b, U, ys, bs, chain, kinds):
    """A multi-U block whose internal U-grid rows ALL carry a (possibly weak) boundary is
    a stack of separate devices whose edges were dimmed by cables, not one tall device.
    A real tall chassis may line up with the grid once by chance, but not on every row."""
    ys, bs = list(ys), list(bs)
    new_chain, new_kinds = [chain[0]], []
    for i, (kd, n) in enumerate(kinds):
        t, bt = ys[chain[i]], ys[chain[i + 1]]
        if kd == "unit" and n >= 2:
            step, w = (bt - t) / n, max(1, int(0.06 * U))
            rows = []
            for k in range(1, n):
                y = int(round(t + k * step))
                lo = max(0, y - w)
                seg = b[lo:y + w + 1]
                rows.append((lo + int(np.argmax(seg)), float(seg.max())))
            if min(s for _, s in rows) >= GRID_SPLIT_MIN:
                for y, s in rows:
                    ys.append(float(y))
                    bs.append(s)
                    new_chain.append(len(ys) - 1)
                    new_kinds.append(("unit", 1))
                new_kinds.append(("unit", 1))
                new_chain.append(chain[i + 1])
                continue
        new_kinds.append((kd, n))
        new_chain.append(chain[i + 1])
    return np.array(ys), np.array(bs), new_chain, new_kinds


# =====================================================================================
# 4c. Empty-slot test: are the rail holes visible?
# =====================================================================================
HOLE_GAPS = (0.357, 0.286)      # 5/8" and 1/2" in units of U (EIA-310 hole spacing)


HOLE_SCALES = (0.06,)           # blob sigma as a fraction of U (0.035 also catches screw centres -> false holes)


def _blobs(gray, x0, x1, y0, y1, U, k_sigma=0.06):
    H, W = gray.shape
    x0, x1, y0, y1 = int(max(0, x0)), int(min(W, x1)), int(max(0, y0)), int(min(H, y1))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return []
    s = gray[y0:y1, x0:x1]
    s = cv2.createCLAHE(2.0, (2, 2)).apply(s).astype(np.float32)
    sg = k_sigma * U
    # holes are DARK (you look through them into the rack); screws/labels are bright
    r = np.clip(cv2.GaussianBlur(s, (0, 0), 2.5 * sg) - cv2.GaussianBlur(s, (0, 0), sg), 0, None)
    k = int(max(3, 0.2 * U)) | 1
    mx = cv2.dilate(r, np.ones((k, k), np.uint8))
    med = np.median(r)
    thr = max(3.0, med + 3 * np.median(np.abs(r - med)))
    peak = (r == mx) & (r > thr)
    b = k // 2                                   # maxima on the window border are artefacts
    peak[:, :b] = peak[:, -b:] = False
    yy, xx = np.where(peak)
    return list(zip(yy + y0, xx + x0))


HOLE_INSET = (FACEPLATE_INCH - 18.312) / 2 / FACEPLATE_INCH   # hole centre 0.344" inside faceplate edge


def _rail_strips(xa, xb):
    """Search windows around the EIA hole-column position on each rail."""
    sp = xb - xa
    xl, xr = xa + HOLE_INSET * sp, xb - HOLE_INSET * sp
    return ((xl - 0.035 * sp, xl + 0.035 * sp), (xr - 0.035 * sp, xr + 0.035 * sp))


def _matched_gaps(ys, U):
    gaps = np.diff(np.sort(ys)) / U
    return sum(any(abs(g - h) < 0.05 for h in HOLE_GAPS) for g in gaps)


class HoleCols(list):
    """[x_left, x_right] (None if not found) plus the blob scale that found each column."""
    scales = (0.06, 0.06)


def hole_columns(gray, xa, xb, U):
    """x of the rail-hole column on each side: the x where dark blobs repeat at the EIA
    hole spacing most often (dark gaps/cables are not periodic, holes are).  Two blob
    scales are tried (round holes are small, square cage-nut holes are large); the
    winner is the scale/column with the most hole-spaced blobs, minus a penalty for
    distance from the standard hole position (0.344" inside the faceplate edge)."""
    sp = xb - xa
    expected = (xa + HOLE_INSET * sp, xb - HOLE_INSET * sp)
    cols, scales = HoleCols(), []
    for (x0, x1), x_exp in zip(_rail_strips(xa, xb), expected):
        best = (-1e9, None, 0.06)
        for k in HOLE_SCALES:
            bl = _blobs(gray, x0, x1, 0, gray.shape[0], U, k)
            if len(bl) < 3:
                continue
            xs = np.array([p[1] for p in bl], float)
            ys = np.array([p[0] for p in bl], float)
            bw = max(2.0, 0.05 * U)
            for c in np.arange(x0, x1, 1.0):
                v = _matched_gaps(ys[np.abs(xs - c) < bw], U)
                if v < 2:
                    continue
                s = v - abs(c - x_exp) / (0.01 * sp)
                if s > best[0]:
                    best = (s, float(c), k)
        cols.append(best[1])
        scales.append(best[2])
    cols.scales = tuple(scales)
    return cols


def hole_evidence(gray, xa, xb, top, bottom, U, cols):
    """Per rail: fraction of the expected hole gaps (3 holes per U) seen in the hole
    column.  Returns (best_rail, worst_rail); worst is None if only one column known."""
    exp_gaps = max(1.0, 3 * (bottom - top) / U - 1)
    vals = []
    for (x0, x1), xc, k in zip(_rail_strips(xa, xb), cols, getattr(cols, "scales", (0.06, 0.06))):
        if xc is None:
            continue
        bl = _blobs(gray, x0, x1, top, bottom, U, k)
        ys = [p[0] for p in bl if abs(p[1] - xc) < 0.05 * U]
        vals.append(min(1.0, _matched_gaps(ys, U) / exp_gaps))
    if not vals:
        return 0.0, None
    return max(vals), (min(vals) if len(vals) == 2 else None)


def screw_circles(gray, x0, x1, y0, y1, U):
    """Screw heads on a rail strip: circles 0.18-0.4U across (rail holes are smaller
    round holes or squares).  A screw means something is fastened in that slot."""
    H, W = gray.shape
    x0, x1, y0, y1 = int(max(0, x0)), int(min(W, x1)), int(max(0, y0)), int(min(H, y1))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return []
    s = cv2.GaussianBlur(gray[y0:y1, x0:x1], (0, 0), 1.5)
    c = cv2.HoughCircles(s, cv2.HOUGH_GRADIENT, dp=1, minDist=0.2 * U, param1=120,
                         param2=max(10, 0.18 * U), minRadius=int(0.09 * U), maxRadius=int(0.2 * U))
    if c is None:
        return []
    # a screw head is brighter inside than just around it; a rail hole is darker inside
    out = []
    yy, xx = np.mgrid[0:s.shape[0], 0:s.shape[1]]
    for cx, cy, r in c[0]:
        d2 = (xx - cx) ** 2 + (yy - cy) ** 2
        inner = s[d2 <= (0.7 * r) ** 2]
        ring = s[(d2 >= (1.2 * r) ** 2) & (d2 <= (1.7 * r) ** 2)]
        if len(inner) and len(ring) and inner.mean() > ring.mean() + 10:
            out.append([float(cx) + x0, float(cy) + y0, float(r)])
    return out


def slot_is_empty(gray, xa, xb, top, bottom, U, cols):
    """Empty = rail holes visible at the EIA spacing AND no screw head on either rail.
    In fully screwed racks the shadows between screw heads mimic the hole spacing; the
    screws themselves give the slot away."""
    if not looks_empty(hole_evidence(gray, xa, xb, top - 0.05 * U, bottom + 0.05 * U, U, cols)):
        return False
    return not any(screw_circles(gray, x0, x1, top + 0.02 * U, bottom - 0.02 * U, U)
                   for x0, x1 in _rail_strips(xa, xb))


def object_in_slot(m, gray, xa, xb, top, bottom, U):
    """Body of an object inside the slot: long horizontal edges (>= 45% of the inner
    width) on >= 15% of the slot's inner rows, over a textured region (not bare depth)."""
    sp = xb - xa
    x0, x1 = int(xa + 0.08 * sp), int(xb - 0.08 * sp)
    y0, y1 = int(max(0, top + 0.12 * U)), int(min(m.shape[0], bottom - 0.12 * U))
    if y1 - y0 < 3 or x1 - x0 < 10:
        return False
    sub = m[y0:y1, x0:x1] > 0
    long_rows = 0
    for row in sub:
        s, e = _runs(row)
        if len(s) and (e - s).max() >= 0.45 * sub.shape[1]:
            long_rows += 1
    return long_rows >= 0.15 * (y1 - y0) and gray[y0:y1, x0:x1].std() >= 22


def looks_empty(ev):
    """Holes visible on both rails, or unmistakably on one (the other may be cabled over)."""
    best, worst = ev
    return best >= 0.99 or (best >= EMPTY_HOLES and (worst is None or worst >= 0.3))


# =====================================================================================
# 5. Assemble
# =====================================================================================
def analyse(path, debug=False):
    img0 = cv2.imread(path)
    if img0 is None:
        raise FileNotFoundError(path)
    scale = WORK_WIDTH / img0.shape[1]
    img = cv2.resize(img0, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    rect, Hm, rot = rectify(img)
    gray = cv2.cvtColor(rect, cv2.COLOR_BGR2GRAY)

    m = horizontal_edge_mask(gray)
    xa, xb = faceplate_span(m)
    # rack width from vertical structure; overrides the edge-run span when they disagree
    # (heavy cabling breaks the horizontal edges and makes the edge-run span too narrow)
    sl, sr, U_search, coh = rack_span_search(gray, m, xa, xb)
    if abs(np.log((xb - xa) * U_PER_WIDTH / U_search)) > 0.08:
        if debug:
            print(f"    edge-run span ({xa:.0f},{xb:.0f}) disagrees with rack-span search "
                  f"({sl:.0f},{sr:.0f}) U={U_search:.1f} coh={coh:.2f} -> using the search")
        xa, xb = sl, sr
        searched = True
    else:
        searched = False
    U_prior = (xb - xa) * U_PER_WIDTH
    b = boundary_profile(m, xa, xb)
    alt = [] if searched else faceplate_candidates(gray, xa, xb)   # the search already tried those pairs
    U = fit_pitch(b, U_prior, debug=debug, alt_priors=[(r - l) * U_PER_WIDTH for l, r in alt])
    # if an ear-edge span explains the measured U better, adopt it and refine once
    best = min([(xa, xb)] + alt, key=lambda s: abs(np.log((s[1] - s[0]) * U_PER_WIDTH / U)))
    if best != (xa, xb):
        xa, xb = best
        U_prior = (xb - xa) * U_PER_WIDTH
        b = boundary_profile(m, xa, xb)
        U = fit_pitch(b, U_prior, debug=debug)
        if debug:
            print(f"    faceplate span widened to ({xa:.0f},{xb:.0f}) from the ear edges")
    # rails first: if both rails show the EIA hole pattern at the same U, that wins
    rg = rail_grid(gray, debug)
    if rg["ok"] and abs(np.log(rg["U"] / U)) > 0.03:
        U = rg["U"]
        cx = (rg["x_left"] + rg["x_right"]) / 2
        xa, xb = cx - U / U_PER_WIDTH / 2, cx + U / U_PER_WIDTH / 2
        b = boundary_profile(m, xa, xb)
        if debug:
            print(f"    U taken from the rail holes: {U:.1f}, span ({xa:.0f},{xb:.0f})")
    ys, bs, chain, kinds = segment_dp(b, U)
    ys, bs, chain, kinds = split_stacked_blocks(b, U, ys, bs, chain, kinds)
    if debug:
        print(f"    rot={rot:.2f}deg span=({xa:.0f},{xb:.0f}) chain={[int(ys[c]) for c in chain]}")

    cols = hole_columns(gray, xa, xb, U)
    if debug:
        print(f"    hole columns={cols}")
    def has_frame(y0, y1):
        return any(is_frame_line(m, y, xa, xb) for y in range(int(max(1, y0)), int(min(len(b) - 1, y1))))

    def slot_empty(t, bt):
        return slot_is_empty(gray, xa, xb, t, bt, U, cols)

    segs = []
    for i, (kd, n) in enumerate(kinds):
        t, bt = ys[chain[i]], ys[chain[i + 1]]
        conf = float(min(bs[chain[i]], bs[chain[i + 1]]))
        units = int(n) if kd == "unit" else int(round((bt - t) / U))
        # peel empty slots (visible rail holes) off both ends of the block; inside a
        # block of 2U+ a slot that shows an object's body stays (equipment on a shelf
        # leaves the rail holes beside it visible)
        tall = units >= 2

        def peelable(a, z):
            return slot_empty(a, z) and not (tall and object_in_slot(m, gray, xa, xb, a, z, U))

        while units >= 1 and peelable(t, t + U):
            segs.append(Segment("empty", t, t + U, 1, 1.0, confidence=round(conf, 2), holes=1.0))
            t, units = t + U, units - 1
        tail = []
        while units >= 1 and peelable(bt - U, bt):
            tail.insert(0, Segment("empty", bt - U, bt, 1, 1.0, confidence=round(conf, 2), holes=1.0))
            bt, units = bt - U, units - 1
        if units >= 1 and bt - t > 0.5 * U:
            exact = (bt - t) / U
            holes = hole_evidence(gray, xa, xb, t, bt, U, cols)[0]
            kind = "device" if kd == "unit" else "irregular"
            outer = (i == 0 and has_frame(t - 2, t + 0.35 * U)) or \
                    (i == len(kinds) - 1 and has_frame(bt - 0.35 * U, bt + 2))
            if outer and kind != "empty":
                kind = "frame"                         # rack top/bottom member, not a device
            segs.append(Segment(kind, t, bt, units if kd == "unit" else int(round(exact)),
                                round(exact, 2), confidence=round(conf, 2), holes=round(holes, 2)))
        segs.extend(tail)

    # where the U lines fall ("phase"): candidates from the devices and from the rails
    blocks = [s for s in segs if s.kind in ("device", "empty")]
    dev_phase = None
    if blocks:
        z = sum(max(s.units, 1) * np.exp(2j * np.pi * s.top / U) for s in blocks)
        dev_phase = (np.angle(z) / (2 * np.pi) * U) % U
    # rail reading at the FINAL U (reading at a slightly different U drifts down the photo)
    rail_ph = rail_phase(gray, U)[0]
    if rail_ph is None and rg["ok"] and rg["phase"] is not None:
        rail_ph = rg["phase"]

    def edge_alignment(ph):
        """Share of strong device edges that land on a U line for this phase."""
        pk, _ = find_peaks(b, height=0.6, distance=max(3, int(0.12 * U)))
        if not len(pk):
            return 0.0
        dd = np.abs(((pk - ph) + U / 2) % U - U / 2) / U
        return float((b[pk] * (dd <= 0.12)).sum() / b[pk].sum())

    if rail_ph is not None and dev_phase is not None:
        diff = abs(((rail_ph - dev_phase) + U / 2) % U - U / 2) / U
        if diff >= 0.38:
            # ~half a U apart: in fully screwed racks the gap between two screw heads
            # looks like a hole and the rail reading slips by half a unit.  Let the
            # device edges decide.
            if edge_alignment(dev_phase) > edge_alignment(rail_ph):
                phase, phase_src = dev_phase, "devices (rail reading half a U off)"
            else:
                phase, phase_src = rail_ph, "rail holes"
        else:
            phase, phase_src = rail_ph, "rail holes"   # devices may sit a hole off the rail grid
    elif rail_ph is not None:
        phase, phase_src = rail_ph, "rail holes"
    elif dev_phase is not None:
        phase, phase_src = dev_phase, "devices"
    else:
        phase = ys[chain[0]] % U if len(chain) else 0.0
        phase_src = "first boundary"
    if debug:
        print(f"    U lines from {phase_src}: phase {phase:.1f}")
    top0 = ys[chain[0]] if len(chain) else 0.0
    first_line = phase + np.floor((top0 - phase) / U + 0.5) * U
    for s in segs:
        off = (s.top - phase) / U
        s.off_grid = bool(abs(off - round(off)) > 0.2) and s.kind == "device"
        s.slot_from = int(round((s.top - first_line) / U)) + 1
        s.slot_to = s.slot_from + max(s.units, 1) - 1

    # map y back to original image coordinates
    Hinv = np.linalg.inv(Hm)
    cx = (xa + xb) / 2

    def to_orig(y):
        p = cv2.perspectiveTransform(np.float32([[[cx, y]]]), Hinv)[0, 0]
        return float(p[1] / scale)

    work_y = [(s.top, s.bottom) for s in segs]
    for s in segs:
        corners = np.float32([[[xa, s.top]], [[xb, s.top]], [[xb, s.bottom]], [[xa, s.bottom]]])
        c = cv2.perspectiveTransform(corners, Hinv).reshape(-1, 2) / scale
        s.bbox = [round(float(v), 1) for v in (c[:, 0].min(), c[:, 1].min(), c[:, 0].max(), c[:, 1].max())]
        s.top, s.bottom = round(to_orig(s.top), 1), round(to_orig(s.bottom), 1)

    res = Result(os.path.basename(path), round(U / scale, 2),
                 int(sum(s.units for s in segs if s.kind == "device")),
                 [asdict(s) for s in segs])
    return res, dict(rect=rect, b=b, U=U, phase=phase, work_y=work_y, xa=xa, xb=xb, original=img0,
                     gray=gray, m=m, cols=cols, Hm=Hm, scale=scale, rot=rot, rail=rg)


def draw(dbg, res):
    """Overlay on the rectified work image (+ boundary-score strip on the right)."""
    img = dbg["rect"].copy()
    U, xa, xb = dbg["U"], int(dbg["xa"]), int(dbg["xb"])
    H = img.shape[0]
    y = dbg["phase"] % U
    while y < H:                                     # faint global U grid
        cv2.line(img, (xa, int(y)), (xb, int(y)), (190, 190, 190), 1, cv2.LINE_AA)
        y += U
    palette = [(0, 200, 255), (255, 120, 0), (0, 220, 0), (255, 0, 200), (0, 120, 255), (200, 200, 0)]
    for i, ((t, bt), seg) in enumerate(zip(dbg["work_y"], res.segments)):
        t, bt = int(t), int(bt)
        if seg["kind"] == "device":
            c = palette[i % len(palette)]
            ov = img.copy()
            cv2.rectangle(ov, (xa, t + 2), (xb, bt - 2), c, -1)
            img = cv2.addWeighted(ov, 0.2, img, 0.8, 0)
            cv2.rectangle(img, (xa, t + 2), (xb, bt - 2), c, 2)
            label = f"{seg['units']}U device" + (" (off-grid)" if seg["off_grid"] else "")
        elif seg["kind"] == "empty":
            cv2.rectangle(img, (xa, t + 2), (xb, bt - 2), (170, 170, 170), 1)
            label = f"{seg['units']}U empty"
        elif seg["kind"] == "frame":
            cv2.rectangle(img, (xa, t + 2), (xb, bt - 2), (90, 90, 90), 1)
            label = "rack frame"
        else:
            cv2.rectangle(img, (xa, t + 2), (xb, bt - 2), (0, 0, 255), 2)
            label = f"irregular ~{seg['units_exact']:.1f}U"
        for col, th in (((0, 0, 0), 5), ((255, 255, 255), 2)):
            cv2.putText(img, label, (xa + 8, (t + bt) // 2 + 8), cv2.FONT_HERSHEY_SIMPLEX, 0.75, col, th, cv2.LINE_AA)
        slot = f"slot {seg['slot_from']}" + (f"-{seg['slot_to']}" if seg["slot_to"] > seg["slot_from"] else "")
        for col, th in (((0, 0, 0), 4), ((255, 255, 255), 1)):
            cv2.putText(img, slot, (xb - 120, (t + bt) // 2 + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.55, col, th, cv2.LINE_AA)
    strip = np.full((H, 60, 3), 30, np.uint8)
    for yy in range(H):
        cv2.line(strip, (0, yy), (int(dbg["b"][yy] * 60), yy), (0, 255, 255), 1)
    return np.hstack([img, strip])


BOX_COLORS = {"device": (0, 200, 0), "empty": (200, 200, 200), "irregular": (0, 0, 255), "frame": (120, 120, 120)}


def draw_boxes(img, res):
    """Bounding boxes + labels on the original photo."""
    out = img.copy()
    th = max(2, round(out.shape[1] / 500))
    fs = max(0.5, out.shape[1] / 1600)
    for s in res.segments:
        x1, y1, x2, y2 = map(int, s["bbox"])
        c = BOX_COLORS[s["kind"]]
        if s["kind"] == "device":
            label = f"{s['units']}U device" + (" (off-grid)" if s["off_grid"] else "")
        elif s["kind"] == "empty":
            label = f"{s['units']}U empty"
        elif s["kind"] == "frame":
            label = "rack frame"
        else:
            label = f"irregular ~{s['units_exact']:.1f}U"
        slot = f"slot {s['slot_from']}" + (f"-{s['slot_to']}" if s["slot_to"] > s["slot_from"] else "")
        pad = th + 1
        cv2.rectangle(out, (x1 + pad, y1 + pad), (x2 - pad, y2 - pad), c, th, cv2.LINE_AA)
        text = f"{label} | {slot}"
        (tw, tht), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, fs, th)
        ty = y1 + pad + tht + 2 * th
        cv2.rectangle(out, (x1 + pad, y1 + pad), (x1 + pad + tw + 2 * th, ty + base), c, -1)
        cv2.putText(out, text, (x1 + pad + th, ty), cv2.FONT_HERSHEY_SIMPLEX, fs,
                    (0, 0, 0) if s["kind"] in ("empty", "device") else (255, 255, 255), th, cv2.LINE_AA)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+")
    ap.add_argument("--out", default="out")
    ap.add_argument("--debug", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    for p in a.images:
        res, dbg = analyse(p, a.debug)
        stem = os.path.splitext(os.path.basename(p))[0]
        cv2.imwrite(os.path.join(a.out, f"{stem}_boxes.png"), draw_boxes(dbg["original"], res))
        if a.debug:
            cv2.imwrite(os.path.join(a.out, f"{stem}_debug.png"), draw(dbg, res))
        with open(os.path.join(a.out, f"{stem}_units.json"), "w") as f:
            json.dump(asdict(res), f, indent=2)
        print(f"{p}: U pitch = {res.u_pitch_px}px, {res.total_units}U of devices")
        for s in res.segments:
            tag = {"device": f"{s['units']}U device", "empty": f"{s['units']}U empty",
                   "irregular": f"irregular (~{s['units_exact']}U)", "frame": "rack frame"}[s["kind"]]
            extra = "  [off-grid]" if s["off_grid"] else ""
            print(f"   slot {s['slot_from']:>2}-{s['slot_to']:<2}  y={s['top']:7.1f}..{s['bottom']:7.1f}  "
                  f"{tag:<22} edge={s['confidence']:.2f} holes={s['holes']:.2f}{extra}")


if __name__ == "__main__":
    main()
