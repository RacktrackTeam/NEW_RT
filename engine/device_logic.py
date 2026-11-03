"""Step 3 by logic only: which neighbouring taken units are one device?

Input: the unit rows (top to bottom, from the ladder) and taken / free per unit (from step 2). For every seam
between two taken neighbours the photo is measured around the unit line, and a rule says "boundary" or "same
device". Runs of taken units are then cut at the boundaries; every run piece is a device.

What is measured at a seam (all sizes relative to the unit height h and the rack width W of THIS photo):
  line_*    a horizontal edge line ON the unit line: how much of the width it covers, and how strong it is
            compared with the edges inside the two units (a device's bezel edge runs the whole width; a drive
            row inside a server does not reach the ears)
  ear_*     the same, in the two narrow strips at the rails where the mounting ears are
  vcont     vertical features that continue across the seam (bezel edges, bay columns, LED columns): the
            correlation of the vertical-edge column profiles of the band above and the band below
  tone_*    brightness, colour and texture difference between the band above and the band below
  extent_*  change of the face's left and right extent across the seam (a narrower device below a wider one)
"""
import cv2
import numpy as np

FEATURES = ["line_cov", "line_ratio", "line_cov_best", "line_off", "ear_cov", "ear_ratio", "ear_vcont",
            "vcont", "vcont_wide", "tone_l", "tone_ab", "tone_std", "hist_int", "extent_l", "extent_r",
            "run_len", "run_pos", "n_units",
            "gap_dark", "gap_peaks", "ear_peak", "ear_dark", "unit_sim", "unit_sim_next", "face_line_peak",
            "side_cont", "side_line", "side_dark", "side_shift",
            "nline_cov", "nline_peak", "peak_cov", "nline_best", "ctx_ratio", "ctx_nb", "px_unit",
            "rc_off", "rc_line_cov", "rc_nline_cov", "rc_vcont", "rc_gap_dark", "rc_hist_int", "rc_tone_l", "rc_peak_cov"]


class Geo:
    """Rack geometry from the unit rows: left / right of the units, unit height, the n+1 unit lines."""
    def __init__(self, rows, shape):
        b = np.array([r["box"] for r in rows], float)
        self.n = len(rows)
        self.x0, self.x1 = float(np.median(b[:, 0])), float(np.median(b[:, 2]))
        self.W = self.x1 - self.x0
        self.h = float(np.median(b[:, 3] - b[:, 1]))
        self.ys = [float(b[0, 1])] + [float((b[k - 1, 3] + b[k, 1]) / 2) for k in range(1, self.n)] + [float(b[-1, 3])]
        self.H, self.Wimg = shape[:2]


def _clip(v, lo, hi):
    return int(min(max(round(v), lo), hi))


class SeamMeasurer:
    """Measures every seam of one photo. Heavy filters are computed once per photo."""
    def __init__(self, bgr, geo):
        self.geo = geo
        g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        self.gray = g
        self.lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
        blur = cv2.GaussianBlur(g, (3, 3), 0)
        self.gy = np.abs(cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3))
        self.gx = np.abs(cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3))
        # contrast-normalised vertical gradient: a dark slit between two black devices is as visible as a bright one
        local = cv2.blur(blur, (9, 9)).astype(np.float32)
        self.gyn = self.gy / (local + 12.0)
        ed = cv2.Canny(blur, 40, 110)
        kw = max(7, int(0.04 * geo.W))
        # horizontal edge pixels that belong to a run at least kw long: lines, not texture
        self.hline = cv2.morphologyEx(ed, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (kw, 1))) > 0
        self.hedge = (self.gy > 40)

    def _band(self, y0, y1, x0, x1):
        H, Wi = self.geo.H, self.geo.Wimg
        return slice(_clip(y0, 0, H - 1), max(_clip(y1, 1, H), _clip(y0, 0, H - 1) + 1)), slice(_clip(x0, 0, Wi - 1), max(_clip(x1, 1, Wi), _clip(x0, 0, Wi - 1) + 1))

    def _line(self, y, x0, x1, tol):
        """Coverage and strength of a horizontal line within +-tol of y over columns x0..x1, and the best
        offset within +-0.3 h."""
        g = self.geo
        ys_, xs_ = self._band(y - tol, y + tol + 1, x0, x1)
        cov = float(self.hline[ys_, xs_].any(axis=0).mean())
        strength = float(self.gy[ys_, xs_].max(axis=0).mean())
        # inside the units above and below (0.2 .. 0.8 h away from the seam)
        ya, xa = self._band(y - 0.8 * g.h, y - 0.2 * g.h, x0, x1)
        yb, xb = self._band(y + 0.2 * g.h, y + 0.8 * g.h, x0, x1)
        inner = np.concatenate([self.gy[ya, xa].ravel(), self.gy[yb, xb].ravel()])
        ratio = strength / (float(np.percentile(inner, 90)) + 4.0)
        # the strongest line within +-0.3 h: the ladder may be a little off the real boundary
        yw, xw = self._band(y - 0.3 * g.h, y + 0.3 * g.h + 1, x0, x1)
        prof = self.hline[yw, xw].mean(axis=1)
        k = int(np.argmax(prof)) if prof.size else 0
        best_cov = float(prof[k]) if prof.size else 0.0
        off = (yw.start + k - y) / g.h if prof.size else 0.0
        return cov, ratio, best_cov, off

    def _vcont(self, y, x0, x1):
        """Correlation of the vertical-edge column profiles above and below the seam."""
        g = self.geo
        ya, xa = self._band(y - 0.45 * g.h, y - 0.12 * g.h, x0, x1)
        yb, xb = self._band(y + 0.12 * g.h, y + 0.45 * g.h, x0, x1)
        pa, pb = self.gx[ya, xa].mean(axis=0), self.gx[yb, xb].mean(axis=0)
        n = min(len(pa), len(pb))
        pa, pb = pa[:n], pb[:n]
        if n < 8 or pa.std() < 1e-3 or pb.std() < 1e-3:
            return 0.0
        return float(np.corrcoef(pa, pb)[0, 1])

    def _tone(self, y, x0, x1):
        g = self.geo
        ya, xa = self._band(y - 0.45 * g.h, y - 0.12 * g.h, x0, x1)
        yb, xb = self._band(y + 0.12 * g.h, y + 0.45 * g.h, x0, x1)
        A, B = self.lab[ya, xa].reshape(-1, 3), self.lab[yb, xb].reshape(-1, 3)
        if not len(A) or not len(B):
            return 0.0, 0.0, 0.0, 1.0
        dl = abs(A[:, 0].mean() - B[:, 0].mean()) / 255
        dab = float(np.linalg.norm(A[:, 1:].mean(0) - B[:, 1:].mean(0))) / 255
        ds = abs(A[:, 0].std() - B[:, 0].std()) / 255
        ha = np.histogram(A[:, 0], 16, (0, 255))[0]; hb = np.histogram(B[:, 0], 16, (0, 255))[0]
        hi = float(np.minimum(ha / max(1, ha.sum()), hb / max(1, hb.sum())).sum())
        return float(dl), dab, float(ds), hi

    def _extent(self, y, x0, x1):
        """Left and right edge of the face above and below the seam: the column of the strongest vertical
        edge in the outer 20 % on each side; returns the shift (in W) on each side."""
        g = self.geo
        m = 0.2 * g.W
        out = []
        for side in ("l", "r"):
            xs0, xs1 = (x0 - 0.05 * g.W, x0 + m) if side == "l" else (x1 - m, x1 + 0.05 * g.W)
            ya, xa = self._band(y - 0.45 * g.h, y - 0.12 * g.h, xs0, xs1)
            yb, xb = self._band(y + 0.12 * g.h, y + 0.45 * g.h, xs0, xs1)
            pa, pb = self.gx[ya, xa].mean(axis=0), self.gx[yb, xb].mean(axis=0)
            n = min(len(pa), len(pb))
            if n < 4:
                out.append(0.0); continue
            out.append(abs(int(np.argmax(pa[:n])) - int(np.argmax(pb[:n]))) / g.W)
        return out

    def _gap(self, y, x0, x1):
        """A physical gap between two devices is a dark row at the seam, often between two bezel lines.
        gap_dark: how much darker the darkest row within +-0.15 h is than the units' typical brightness (in
        units of the units' brightness spread); gap_peaks: number of horizontal-line peaks within +-0.25 h;
        face_line_peak: the strongest row of horizontal edge energy within +-0.15 h over the inner face, relative
        to the typical row inside the units."""
        g = self.geo
        yw, xw = self._band(y - 0.15 * g.h, y + 0.15 * g.h + 1, x0, x1)
        yu, xu = self._band(y - 0.9 * g.h, y + 0.9 * g.h + 1, x0, x1)
        rows_w = self.gray[yw, xw].mean(axis=1).astype(np.float32)
        rows_u = self.gray[yu, xu].mean(axis=1).astype(np.float32)
        if rows_w.size < 2 or rows_u.size < 4:
            return 0.0, 0, 0.0
        dark = (float(np.median(rows_u)) - float(rows_w.min())) / (float(rows_u.std()) + 4.0)
        ye, xe = self._band(y - 0.25 * g.h, y + 0.25 * g.h + 1, x0, x1)
        prof = self.gy[ye, xe].mean(axis=1)
        ref = float(np.median(self.gy[yu, xu].mean(axis=1))) + 2.0
        pk = (prof[1:-1] > prof[:-2]) & (prof[1:-1] >= prof[2:]) & (prof[1:-1] > 2.5 * ref)
        peaks = int(pk.sum())
        pw = self.gy[yw, xw].mean(axis=1)
        return dark, peaks, float(pw.max() / ref)

    def _ear(self, y, x0, x1):
        """Mounting-ear strip: the vertical-gradient row profile; a device edge shows as a peak at the seam."""
        g = self.geo
        yw, xw = self._band(y - 0.12 * g.h, y + 0.12 * g.h + 1, x0, x1)
        yu, xu = self._band(y - 0.9 * g.h, y + 0.9 * g.h + 1, x0, x1)
        if self.gy[yu, xu].size < 8:
            return 0.0, 0.0
        prof_u = self.gy[yu, xu].mean(axis=1)
        peak = float(self.gy[yw, xw].mean(axis=1).max()) / (float(np.median(prof_u)) + 2.0)
        rows_u = self.gray[yu, xu].mean(axis=1).astype(np.float32)
        rows_w = self.gray[yw, xw].mean(axis=1).astype(np.float32)
        dark = (float(np.median(rows_u)) - float(rows_w.min())) / (float(rows_u.std()) + 4.0)
        return peak, dark

    def _unit_sim(self, ya0, ya1, yb0, yb1, x0, x1):
        """How alike two whole unit strips are (correlation of the 8 x 32 thumbnails)."""
        sa, xa = self._band(ya0, ya1, x0, x1); sb, xb = self._band(yb0, yb1, x0, x1)
        A, B = self.gray[sa, xa], self.gray[sb, xb]
        if A.shape[0] < 3 or B.shape[0] < 3 or A.shape[1] < 8:
            return 0.0
        a = cv2.resize(A, (32, 8), interpolation=cv2.INTER_AREA).astype(np.float32).ravel()
        b = cv2.resize(B, (32, 8), interpolation=cv2.INTER_AREA).astype(np.float32).ravel()
        if a.std() < 1e-3 or b.std() < 1e-3:
            return 0.0
        return float(np.corrcoef(a, b)[0, 1])

    def _sides(self, y):
        """The chassis sides. In the band above and the band below the seam, the strongest vertical edge in the
        outer 30 % of the rack on each side is the device's side edge. Returns:
          side_cont  how strongly that vertical edge runs THROUGH the seam row (one device: continuous frame;
                     two devices: the gap cuts it), relative to its strength inside the bands
          side_line  horizontal line coverage on the seam in the 8 % strip just inside the side edges
          side_dark  dark gap on the seam in those strips
          side_shift how far the side edge moves between the band above and the band below (in W)"""
        g = self.geo
        ya, _ = self._band(y - 0.45 * g.h, y - 0.12 * g.h, 0, 1)
        yb, _ = self._band(y + 0.12 * g.h, y + 0.45 * g.h, 0, 1)
        ys_, _ = self._band(y - max(1.5, 0.06 * g.h), y + max(1.5, 0.06 * g.h) + 1, 0, 1)
        m = 0.3 * g.W
        cont, line, dark, shift = [], [], [], []
        for side in ("l", "r"):
            xs0, xs1 = (g.x0 - 0.1 * g.W, g.x0 + m) if side == "l" else (g.x1 - m, g.x1 + 0.1 * g.W)
            _, xw = self._band(0, 1, xs0, xs1)
            pa, pb = self.gx[ya, xw].mean(axis=0), self.gx[yb, xw].mean(axis=0)
            n = min(len(pa), len(pb))
            if n < 6:
                continue
            ea, eb = int(np.argmax(pa[:n])), int(np.argmax(pb[:n]))
            shift.append(abs(ea - eb) / g.W)
            e = xw.start + (ea if pa[ea] >= pb[eb] else eb)
            w2 = max(1, int(0.006 * g.W))
            _, xe = self._band(0, 1, e - w2, e + w2 + 1)
            inside = float(np.concatenate([self.gx[ya, xe].mean(axis=1), self.gx[yb, xe].mean(axis=1)]).mean())
            at_seam = float(self.gx[ys_, xe].mean(axis=1).min())
            cont.append(at_seam / (inside + 2.0))
            # the strip just inside the side edge
            sx0, sx1 = (e, e + 0.08 * g.W) if side == "l" else (e - 0.08 * g.W, e)
            yl, xl = self._band(y - max(2.0, 0.08 * g.h), y + max(2.0, 0.08 * g.h) + 1, sx0, sx1)
            line.append(float(self.hline[yl, xl].any(axis=0).mean()))
            yu, xu = self._band(y - 0.9 * g.h, y + 0.9 * g.h + 1, sx0, sx1)
            ru = self.gray[yu, xu].mean(axis=1).astype(np.float32); rw = self.gray[yl, xl].mean(axis=1).astype(np.float32)
            dark.append((float(np.median(ru)) - float(rw.min())) / (float(ru.std()) + 4.0) if ru.size > 3 and rw.size else 0.0)
        if not cont:
            return 1.0, 0.0, 0.0, 0.0
        return float(np.mean(cont)), float(np.mean(line)), float(np.mean(dark)), float(np.mean(shift))

    def _nline(self, y, x0, x1):
        """Contrast-normalised line at the seam: coverage (columns whose normalised gradient within +-0.08 h of the
        seam exceeds 0.12), the row-profile peak within +-0.1 h against the typical row within +-1 h, the best such
        peak within +-0.35 h (the ladder may be a little off), and peak coverage: the share of columns in which
        the seam is the strongest horizontal edge within +-0.5 h."""
        g = self.geo
        tol = max(1.0, 0.08 * g.h)
        yw, xw = self._band(y - tol, y + tol + 1, x0, x1)
        yu, xu = self._band(y - g.h, y + g.h + 1, x0, x1)
        if self.gyn[yu, xu].shape[0] < 4:
            return 0.0, 0.0, 0.0, 0.0
        cov = float((self.gyn[yw, xw].max(axis=0) > 0.12).mean())
        prof = self.gyn[yu, xu].mean(axis=1)
        ref = float(np.median(prof)) + 0.01
        c = int(round(y)) - yu.start
        r1 = max(1, int(0.1 * g.h)); r2 = max(1, int(0.35 * g.h))
        peak = float(prof[max(0, c - r1):c + r1 + 1].max()) / ref if c + 1 > 0 else 0.0
        best = float(prof[max(0, c - r2):c + r2 + 1].max()) / ref if c + 1 > 0 else 0.0
        yh, xh = self._band(y - 0.5 * g.h, y + 0.5 * g.h + 1, x0, x1)
        G = self.gy[yh, xh]
        ch = int(round(y)) - yh.start
        near = G[max(0, ch - r1):ch + r1 + 1].max(axis=0)
        far = G.max(axis=0); med = np.median(G, axis=0)
        pk = float(((near >= 0.7 * far) & (near >= 3 * med + 3)).mean()) if G.shape[0] > 2 else 0.0
        return cov, peak, best, pk

    def _recentre(self, y, x0, x1):
        """The row within +-0.35 h of the ladder seam where the contrast-normalised horizontal edge is strongest:
        the hand-drawn boxes sit more than a quarter unit off the ladder line on 23 % of device edges."""
        g = self.geo
        r = max(1, int(0.35 * g.h))
        yw, xw = self._band(y - r, y + r + 1, x0, x1)
        prof = self.gyn[yw, xw].mean(axis=1)
        if prof.size < 3:
            return y
        return float(yw.start + int(np.argmax(prof)))

    def measure(self, k, run_len=1, run_pos=0):
        """Features of the seam between unit k-1 and unit k (k = 1..n-1)."""
        g = self.geo
        y = g.ys[k]
        tol = max(2.0, 0.08 * g.h)
        ear = max(4.0, 0.06 * g.W)
        xi0, xi1 = g.x0 + 0.12 * g.W, g.x1 - 0.12 * g.W              # the face, ears left out
        line_cov, line_ratio, line_best, line_off = self._line(y, xi0, xi1, tol)
        el = self._line(y, g.x0 - 0.5 * ear, g.x0 + ear, tol)
        er = self._line(y, g.x1 - ear, g.x1 + 0.5 * ear, tol)
        ear_cov, ear_ratio = (el[0] + er[0]) / 2, (el[1] + er[1]) / 2
        ear_v = (self._vcont(y, g.x0 - 0.5 * ear, g.x0 + ear) + self._vcont(y, g.x1 - ear, g.x1 + 0.5 * ear)) / 2
        vc = self._vcont(y, xi0, xi1)
        vcw = self._vcont(y, g.x0, g.x1)
        tl, tab, ts, hi = self._tone(y, xi0, xi1)
        exl, exr = self._extent(y, g.x0, g.x1)
        gap_dark, gap_peaks, face_pk = self._gap(y, xi0, xi1)
        epl, edl = self._ear(y, g.x0 - 0.3 * ear, g.x0 + ear)
        epr, edr = self._ear(y, g.x1 - ear, g.x1 + 0.3 * ear)
        usim = self._unit_sim(g.ys[k - 1], g.ys[k], g.ys[k], g.ys[k + 1], xi0, xi1)
        usim_next = self._unit_sim(g.ys[k], g.ys[k + 1], g.ys[k + 1], g.ys[k + 2], xi0, xi1) if k + 2 <= g.n else usim
        sc, sl, sd, ss = self._sides(y)
        nl_cov, nl_peak, nl_best, pk_cov = self._nline(y, xi0, xi1)
        # the same cues re-centred on the strongest line near the seam
        yb = self._recentre(y, xi0, xi1)
        rc_line = self._line(yb, xi0, xi1, tol)[0]
        rc_nl = self._nline(yb, xi0, xi1)
        rc_vc = self._vcont(yb, xi0, xi1)
        rc_gap = self._gap(yb, xi0, xi1)[0]
        rc_tl, _, _, rc_hi = self._tone(yb, xi0, xi1)
        return dict(zip(FEATURES, [line_cov, line_ratio, line_best, line_off, ear_cov, ear_ratio, ear_v, vc, vcw,
                                   tl, tab, ts, hi, exl, exr, run_len, run_pos, g.n,
                                   gap_dark, gap_peaks, max(epl, epr), max(edl, edr), usim, usim_next, face_pk,
                                   sc, sl, sd, ss,
                                   nl_cov, nl_peak, pk_cov, nl_best, 0.0, 0.0, g.h,
                                   abs(yb - y) / g.h, rc_line, rc_nl[0], rc_vc, rc_gap, rc_hi, rc_tl, rc_nl[3]]))


def runs_of(taken):
    """Maximal runs of taken units: list of (first, last) indices."""
    runs, start = [], None
    for k, t in enumerate(list(taken) + [False]):
        if t and start is None:
            start = k
        elif not t and start is not None:
            runs.append((start, k - 1)); start = None
    return runs


WORK_UNIT_PX = 64   # every seam is measured on a copy of the photo scaled so one unit is this many pixels tall


def seam_features(bgr, rows, taken, work_px=WORK_UNIT_PX):
    """Features for every seam between two taken neighbours: {k: features} with k the lower unit's index.
    The photo and the unit boxes are first scaled so a unit is work_px pixels tall, so the pixel-sized filters
    (blur, edge thresholds, the line-opening kernel) see the same thing on a 40 px and a 240 px photo."""
    h0 = float(np.median([r["box"][3] - r["box"][1] for r in rows]))
    if work_px and h0 > 0 and abs(h0 / work_px - 1) > 0.05:
        f = work_px / h0
        bgr = cv2.resize(bgr, (max(8, int(round(bgr.shape[1] * f))), max(8, int(round(bgr.shape[0] * f)))),
                         interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_CUBIC)
        rows = [{**r, "box": [v * f for v in r["box"]]} for r in rows]
    geo = Geo(rows, bgr.shape)
    M = SeamMeasurer(bgr, geo)
    out = {}
    px_unit = h0
    for a, b in runs_of(taken):
        ks = list(range(a + 1, b + 1))
        for k in ks:
            out[k] = M.measure(k, run_len=b - a + 1, run_pos=k - a)
            out[k]["px_unit"] = px_unit
        # context: this seam's line against the run's strongest, and the weaker of its two neighbours
        # (a run end next to a free unit is a sure boundary and counts as 1)
        top = max([out[k]["nline_peak"] for k in ks] + [1e-6])
        for k in ks:
            out[k]["ctx_ratio"] = out[k]["nline_peak"] / top
            nb = [out[j]["nline_peak"] / top if j in out else 1.0 for j in (k - 1, k + 1)]
            out[k]["ctx_nb"] = min(nb)
    return out


# ---------------------------------------------------------------- the rule
# A short decision list, tuned on the train / valid photos that are NOT in the test set (scripts/devices/tune_device_logic.py
# fit). Each node tests one cue against one threshold; "lo" is taken when cue <= threshold. Leaves say boundary / same.
# RULE-BEGIN
RULE = {'f': 'line_cov', 't': 0.133, 'lo': {'f': 'vcont', 't': 0.415, 'lo': {'f': 'gap_dark', 't': 0.755, 'lo': {'f': 'line_ratio', 't': 0.733, 'lo': {'boundary': False, 'p': 0.333}, 'hi': {'boundary': True, 'p': 0.583}}, 'hi': {'f': 'hist_int', 't': 0.876, 'lo': {'boundary': True, 'p': 0.822}, 'hi': {'boundary': False, 'p': 0.483}}}, 'hi': {'f': 'line_ratio', 't': 0.748, 'lo': {'f': 'gap_dark', 't': 0.86, 'lo': {'boundary': False, 'p': 0.085}, 'hi': {'boundary': False, 'p': 0.358}}, 'hi': {'f': 'tone_ab', 't': 0.009, 'lo': {'boundary': False, 'p': 0.268}, 'hi': {'boundary': True, 'p': 0.529}}}}, 'hi': {'f': 'vcont', 't': 0.464, 'lo': {'f': 'gap_dark', 't': 0.993, 'lo': {'f': 'line_cov', 't': 0.412, 'lo': {'boundary': True, 'p': 0.688}, 'hi': {'boundary': True, 'p': 0.854}}, 'hi': {'f': 'face_line_peak', 't': 3.464, 'lo': {'boundary': True, 'p': 0.902}, 'hi': {'boundary': True, 'p': 0.989}}}, 'hi': {'f': 'line_cov', 't': 0.598, 'lo': {'f': 'unit_sim', 't': 0.777, 'lo': {'boundary': False, 'p': 0.353}, 'hi': {'boundary': True, 'p': 0.754}}, 'hi': {'f': 'n_units', 't': 22.5, 'lo': {'boundary': True, 'p': 0.698}, 'hi': {'boundary': True, 'p': 0.948}}}}}
# RULE-END


# How common each device height is (1U, 2U, ... 8U), measured on the tuning photos (tune_device_logic.py fit)
# PRIOR-BEGIN
PRIOR = [0.75, 0.1944, 0.0325, 0.0141, 0.0044, 0.0016, 0.0012, 0.0019]
# PRIOR-END


# The scoring rule: P(new device) = 1 / (1 + exp(-(intercept + sum of weight x cue))), weights on the raw cues,
# fitted once on the tuning seams (tune_device_logic.py fit-score). Empty = use the decision list's leaf shares.
# A new device starts when P(new device) >= CUT, i.e. when the score passes zero. 0.4 scores more devices exact on the
# 1,000 test photos (82.6 vs 82.0 %) but fewer photos with every device right (48.0 vs 50.9 %); 0.5 is kept.
# CUT-BEGIN
CUT = 0.5
# CUT-END
# SCORE-BEGIN
SCORE = {'intercept': -1.7199, 'weights': {'line_cov': 2.45248, 'ear_vcont': 0.69481, 'vcont': -2.10312, 'vcont_wide': -0.94028, 'hist_int': -0.92966, 'run_pos': 0.0349, 'n_units': 0.01055, 'gap_dark': 0.60922, 'gap_peaks': -0.18382, 'unit_sim_next': 0.82203, 'face_line_peak': -0.11259, 'side_cont': -0.69068, 'nline_cov': 2.44275, 'peak_cov': 1.06937, 'ctx_ratio': 1.99096, 'ctx_nb': -1.04923, 'rc_off': -1.05913, 'rc_line_cov': -1.22334, 'rc_hist_int': -1.18922, 'rc_tone_l': 1.38038, 'rc_peak_cov': 1.61216}, 'standardised': {'line_cov': 0.661, 'ear_vcont': 0.213, 'vcont': -0.521, 'vcont_wide': -0.205, 'hist_int': -0.152, 'run_pos': 0.156, 'n_units': 0.105, 'gap_dark': 0.462, 'gap_peaks': -0.205, 'unit_sim_next': 0.275, 'face_line_peak': -0.213, 'side_cont': -0.214, 'nline_cov': 0.325, 'peak_cov': 0.263, 'ctx_ratio': 0.535, 'ctx_nb': -0.286, 'rc_off': -0.119, 'rc_line_cov': -0.339, 'rc_hist_int': -0.206, 'rc_tone_l': 0.132, 'rc_peak_cov': 0.358}}
# SCORE-END


def seam_prob(f, rule=None):
    """P(a new device starts at this seam): the scoring rule when there is one, else the leaf's share of
    boundaries on the tuning seams (1 / 0 when the rule carries no such share)."""
    if rule is None and SCORE:
        z = SCORE["intercept"] + sum(w * f[c] for c, w in SCORE["weights"].items())
        return float(1.0 / (1.0 + np.exp(-max(-30.0, min(30.0, z)))))
    node = rule or RULE
    while "boundary" not in node:
        node = node["lo"] if f[node["f"]] <= node["t"] else node["hi"]
    return float(node.get("p", 1.0 if node["boundary"] else 0.0))


def partition(probs, a, b, prior=None, max_h=8):
    """Cut the run of taken units a..b into devices: the partition with the highest
    sum of log P(cut) / log P(keep) over its seams plus log prior(height) over its devices.
    probs: {k: P(boundary at seam k)} for k = a+1..b. Returns the devices as lists of unit indices."""
    prior = prior or PRIOR
    n = b - a + 1
    best = [(-1e18, -1)] * (n + 1)
    best[0] = (0.0, -1)
    lp = {k: (np.log(max(1e-6, pk)), np.log(max(1e-6, 1 - pk))) for k, pk in probs.items()}
    for j in range(1, n + 1):
        for i in range(max(0, j - max_h), j):
            if best[i][0] < -1e17:
                continue
            sc = best[i][0] + np.log(prior[min(j - i, len(prior)) - 1])
            for k in range(a + i + 1, a + j):          # seams kept inside this device
                sc += lp[k][1]
            if i > 0:                                 # the seam cut at the top of this device
                sc += lp[a + i][0]
            if sc > best[j][0]:
                best[j] = (sc, i)
    devices, j = [], n
    while j > 0:
        i = best[j][1]
        devices.append(list(range(a + i, a + j)))
        j = i
    return devices[::-1]


def is_boundary(f, rule=None):
    """True when the seam separates two devices: the scoring rule at 0.5, else walk the decision list."""
    if rule is None and SCORE:
        return seam_prob(f) >= CUT
    node = rule or RULE
    while "boundary" not in node:
        node = node["lo"] if f[node["f"]] <= node["t"] else node["hi"]
    return bool(node["boundary"])


def group(bgr, rows, taken, rule=None, feats=None, mode="seam"):
    """Devices as lists of unit indices (top to bottom), plus the per-seam decisions.
    mode "partition": each run of taken units is cut into the most likely devices given the seams' probabilities
    and the device-height prior; mode "seam": every seam is decided on its own."""
    feats = feats if feats is not None else seam_features(bgr, rows, taken)
    rule = rule or RULE
    devices = []
    if mode == "partition" and (SCORE or any("p" in leaf for leaf in _leaves(rule))):
        for a, b in runs_of(taken):
            devices += partition({k: seam_prob(feats[k], None if SCORE else rule) for k in range(a + 1, b + 1)}, a, b)
        first = {d[0] for d in devices}
        split = {k: (k in first) for k in feats}
    else:
        split = {k: (seam_prob(f) >= CUT if SCORE else is_boundary(f, rule)) for k, f in feats.items()}
        for a, b in runs_of(taken):
            cur = [a]
            for k in range(a + 1, b + 1):
                if split.get(k, False):
                    devices.append(cur); cur = [k]
                else:
                    cur.append(k)
            devices.append(cur)
    return devices, split, feats


def _leaves(node):
    if "boundary" in node:
        return [node]
    return _leaves(node["lo"]) + _leaves(node["hi"])
