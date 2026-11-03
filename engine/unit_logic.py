"""Rack-unit ladder from the photo alone - no detector, no classifier.

Scope: straight, front-facing, high-resolution photos. Perspective is not corrected,
so an angled photo gives a wrong ladder; that is by design for now.

How a unit is found (the current stage; earlier stages are kept behind STAGES so the
report can show how each change behaved on the same photos):
  1. Long horizontal edges. Every rack-mounted device has a top and a bottom edge that
     runs across the whole 19-inch opening. A pixel counts as "on a line" when most of a
     short window around it along the row is edge, so an edge interrupted by screws,
     ports and labels still reads as one line while a lone cable does not.
  2. The rack's width. Candidate rail pairs come from where the lines start and end;
     the pair wins whose gaps between device edges come out as whole units of
     0.0953 x width (a 19-inch faceplate is 482.6 mm wide, one unit 44.45 mm). Pairs
     that do not run the rack's height as a vertical edge, or sit on the photo's own
     border, are dropped; of the pairs that score well, the widest wins.
  3. One device that is one unit tall. A band between two device lines whose height
     matches the prior is a 1U device, and its height IS the pitch. Taller bands are 2U,
     3U, ... and refine the pitch as integer multiples.
  4. The ladder. Starting at the topmost device line, unit lines are laid one pitch apart,
     snapping to a real device line whenever one lies close, down to the bottommost device
     line. Then the ladder is extended past the first and last device while the rails
     still repeat at the unit pitch (holes, cage nuts, screws), and stops where the
     cabinet is plain. Rows are numbered u01 at the BOTTOM, as everywhere in the project.

Output format matches unit_grid.ladder_from_unit_boxes / occupancy.detect_units so the
rest of the pipeline can take it unchanged.
"""
import cv2
import numpy as np
from scipy.ndimage import uniform_filter1d

# ---- physical constants (EIA-310) -------------------------------------------
U_MM = 44.45                 # one rack unit
FACEPLATE_MM = 482.6         # 19-inch faceplate, ear to ear
OPENING_MM = 450.8           # opening between the two rails
# 1U as a fraction of the width we measure. The device lines usually span the faceplate
# (ears included) or the opening; the prior sits between the two, with a tolerance that
# covers both.
PITCH_OVER_WIDTH = 0.5 * (U_MM / FACEPLATE_MM + U_MM / OPENING_MM)   # 0.0953
PITCH_TOL = 0.12             # a band within +-12 % of the prior is a 1U device

# ---- image processing settings ----------------------------------------------
WORK_LONG_SIDE = 1600        # processing copy; boxes are mapped back to the original
EDGE_PERCENTILE = 85         # a pixel is an edge when its vertical gradient is in the top 15 %
DENSITY_WIN_FRAC = 0.03      # window (fraction of image width) over which edge density is measured
DENSITY_MIN = 0.5            # a pixel is on a line when at least half the window is edge
MIN_ROW_FRAC = 0.20          # a row with line pixels over at least this much of the image is a candidate
EXTENT_PERCENTILE = 0.15     # the rails are where the outer 15 % of the candidate rows start / end ...
EXTENT_BAND_FRAC = 0.04      # ... tightened to the median of rows within this (of image width) of it
EXTENT_CLUSTERS = 3          # also try the 3 strongest clusters of line starts and of line ends
LINE_COVER_FRAC = 0.50       # a device line has line pixels over at least this much of the rack width
MARGIN_FRAC = (0.03, 0.12)   # a line that also covers the margins this far outside BOTH rails is a cabinet line
MERGE_FRAC = 0.25            # device lines closer than this (in U) are the same boundary
MIN_LINES = 3                # fewer device lines than this: no ladder
RAIL_STRIP = (-0.06, 0.04)   # strip over the rail, relative to the rail x, in rack widths (holes sit just outside the device lines)
BORDER_FRAC = 0.01           # lines or rails this close to the photo's edge are the photo's edge
CONTINUITY_FRAC = 0.6        # a rail pair must run the rack's height at least this well, relative to the best pair
RAIL_REGULAR_MIN = 0.40      # rail texture repeating at the unit pitch at least this well = still rack ...
RAIL_REGULAR_MAX = 0.55      # ... never asking for more than this, however regular the rails are over the devices
RAIL_LAG_TOL = 0.10          # the repetition is looked for at lags within this of the pitch (the pitch can be a few % off)
RAIL_TOP_BOTH = True         # walking UP past the first device needs both rails to repeat; walking down needs one
EXTEND_UP_MAX_U = 58         # at most this many rows added above the first device (0 = never extend upward)
PAIR_SCORE_FRAC = 0.7        # among rail pairs scoring at least this share of the best, the widest wins
PAIR_SUPPORT_FRAC = 0.85     # ... but only pairs whose unit grid carries at least this share of the best pair's edge support
PAIR_RAIL_MIN = 9.0          # when some pair's strips repeat at its pitch at least this well, the rails are visible ...
PAIR_RAIL_FRAC = 0.75        # ... and only pairs repeating at least this share as well are considered
USE_POST_CANDIDATES = False  # also try rail pairs from long vertical edges (tested 9 Oct: mixed, off)
EXTEND_MIN_U = 0.6           # extend by a row only when at least this much of a row fits
MAX_RACK_UNITS = 58

# ---- the stages the method went through (for the report) --------------------
# edges:  "runs"     = an edge must be one unbroken run of edge pixels
#         "density"  = an edge is where most of a short window along the row is edge
# width:  "median"   = typical start / end of the long lines
#         "envelope" = where the outer 15 % of the lines reach
#         "pairs"    = several candidates, the one whose device bands are whole units wins
# lines:  "ends"     = a device line must reach within tol of both rails
#         "coverage" = a device line covers at least half the rack width (ends free)
# extend: "none" | "texture" (edge energy on the rails) | "repeat1d" (profile repeats at the
#         pitch) | "repeat" (each rail column repeats at the pitch, lag tolerant)
# fit:    False = lines laid from the first device edge one pitch apart, snapping to device edges
#         True  = pitch AND phase chosen together as the grid that most device edges fall on
#                 (a device's mid-face seam no longer shifts the whole ladder by half a unit),
#                 and the ladder never crosses a cabinet edge (a line past both rails)
FIT_Q_TOL = 0.08             # the grid fit may move the pitch this far from the 1U-band estimate
FIT_IN_U = 0.12              # a device edge this close (in U) to a grid line supports the grid
FIT_CURVE_MAX = 0.15         # the pitch may change by at most this share from the top of the ladder to the bottom


def _work_copy(bgr):
    h, w = bgr.shape[:2]
    f = max(h, w) / float(WORK_LONG_SIDE)
    if f <= 1.0:
        return bgr, 1.0
    small = cv2.resize(bgr, (int(round(w / f)), int(round(h / f))), interpolation=cv2.INTER_AREA)
    return small, f


# ---------------------------------------------------------------- edges
def horizontal_line_mask(gray):
    """Binary mask of horizontal-edge pixels that sit on a long line.

    Only edges steeper vertically than horizontally are used (cables and texture are not);
    a pixel is kept when at least half of a window around it along the row is edge.
    """
    w = gray.shape[1]
    g = cv2.GaussianBlur(gray, (3, 3), 0)
    gy = np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
    gx = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3))
    thr = max(float(np.percentile(gy, EDGE_PERCENTILE)), 6.0)
    e = ((gy > thr) & (gy > 1.5 * gx)).astype(np.float32)
    win = max(9, int(DENSITY_WIN_FRAC * w)) | 1
    density = cv2.blur(e, (win, 1))
    return (density > DENSITY_MIN).astype(np.uint8)


def candidate_rows(mask):
    """Rows with enough line pixels: [(y, first_x, last_x)]."""
    h, w = mask.shape
    counts = mask.sum(axis=1)
    out = []
    for y in np.flatnonzero(counts >= MIN_ROW_FRAC * w):
        xs = np.flatnonzero(mask[y])
        out.append((int(y), int(xs[0]), int(xs[-1])))
    return out


# ---------------------------------------------------------------- rack width


def rack_extent_envelope(rows, img_w):
    """Stage "outer": where the outer 15 % of the lines reach, tightened to the median of
    the lines ending near there."""
    if not rows:
        return None
    starts = np.array([s for _, s, _ in rows], float)
    ends = np.array([e for _, _, e in rows], float)
    lo = np.percentile(starts, 100 * EXTENT_PERCENTILE)
    hi = np.percentile(ends, 100 * (1 - EXTENT_PERCENTILE))
    band = EXTENT_BAND_FRAC * img_w
    near_lo = starts[np.abs(starts - lo) <= band]
    near_hi = ends[np.abs(ends - hi) <= band]
    x_left = float(np.median(near_lo)) if len(near_lo) else lo
    x_right = float(np.median(near_hi)) if len(near_hi) else hi
    return int(round(x_left)), int(round(x_right))


def _clusters(xs, img_w, n):
    """The n most populated 1 %-wide bins of xs, each as the median x of its rows."""
    if len(xs) == 0:
        return []
    b = max(1, int(0.01 * img_w))
    bins = (xs // b).astype(int)
    counts = np.bincount(bins)
    out = []
    for i in np.argsort(counts)[::-1][:n]:
        if counts[i] == 0:
            break
        out.append(float(np.median(xs[bins == i])))
    return out


def rack_extent_candidates(rows, img_w):
    """Possible (left rail, right rail) pairs: the outer envelope plus the strongest
    clusters of line starts and of line ends (faceplate ends, cabinet frame, texture)."""
    if not rows:
        return []
    env = rack_extent_envelope(rows, img_w)
    starts = np.array([s for _, s, _ in rows], float)
    ends = np.array([e for _, _, e in rows], float)
    band = EXTENT_BAND_FRAC * img_w
    lefts = [float(env[0])] + _clusters(starts, img_w, EXTENT_CLUSTERS)
    rights = [float(env[1])] + _clusters(ends, img_w, EXTENT_CLUSTERS)
    pairs = []
    for xl in lefts:
        for xr in rights:
            if xr - xl < 0.25 * img_w:
                continue
            pair = (int(round(xl)), int(round(xr)))
            if all(abs(pair[0] - p[0]) > band or abs(pair[1] - p[1]) > band for p in pairs):
                pairs.append(pair)
    return pairs


def vertical_continuity(gray):
    """Per column, the share of image rows with a strong vertical edge within 1 % of it.
    The rails and the faceplate ends run the whole height of the rack as one vertical
    edge; texture inside the devices does not."""
    h, w = gray.shape
    gx = np.abs(cv2.Sobel(cv2.GaussianBlur(gray, (3, 3), 0), cv2.CV_32F, 1, 0, ksize=3))
    thr = float(np.percentile(gx, 75))
    strong = (gx > thr).astype(np.float32)
    k = max(3, int(0.02 * w)) | 1
    near = cv2.blur(strong, (k, 1)) * k
    return (near >= 1).mean(axis=0)


def choose_extent(mask, pairs, gray):
    """The rail pair whose device bands come out as whole units.

    For each candidate pair the device lines are found and the bands between them
    measured against that pair's own 1U prior (0.0953 x width). The right rails make
    1U devices land on the prior and taller devices on whole multiples of it; rails on
    the cabinet frame (prior too big) or on inner texture (prior too small) do not.
    Pairs whose x do not run the height of the rack as a vertical edge, or that sit on
    the photo's own border, are only used when nothing else works. Of the pairs that
    score well, the widest wins (texture always lies inside the real rails).
    Returns (x_left, x_right, bounds, score) or None.
    """
    h, w = gray.shape
    cont = vertical_continuity(gray)
    border = max(2, int(BORDER_FRAC * w))
    # The posts themselves are a second source of candidates: columns with a long vertical
    # edge. Device bodies seen from the back, or recessed faceplates, end well inside the
    # rails, so line ends alone can miss the real width (and then the unit comes out small).
    band = EXTENT_BAND_FRAC * w
    peaks = _peaks(cont, 0.45, int(0.02 * w)) if USE_POST_CANDIDATES else []
    pairs = list(pairs)
    for xl in [x for x in peaks if x < 0.45 * w][:5]:
        for xr in [x for x in peaks if x > 0.55 * w][:5]:
            if xr - xl >= 0.4 * w and all(abs(xl - p[0]) > band or abs(xr - p[1]) > band for p in pairs):
                pairs.append((int(xl), int(xr)))
    rated = [(xl, xr, min(cont[max(0, xl)], cont[min(w - 1, xr)]), xl <= border or xr >= w - 1 - border) for xl, xr in pairs]
    inner = [r for r in rated if not r[3]]
    if inner:
        c_max = max(r[2] for r in inner)
        rated = [r for r in inner if r[2] >= CONTINUITY_FRAC * c_max] or inner
    scored = []
    for xl, xr, c, _ in rated:
        bounds = device_lines_coverage(mask, xl, xr)
        if len(bounds) < MIN_LINES:
            continue
        prior = PITCH_OVER_WIDTH * (xr - xl)
        d = np.diff([b[0] for b in bounds])
        n = np.round(d / prior)
        rel = np.abs(d - n * prior) / prior
        anchors = int(np.sum((n == 1) & (rel <= PITCH_TOL * 0.7)))
        multiples = int(np.sum((n >= 2) & (n <= 8) & (rel <= 0.12)))
        score = anchors + 0.5 * multiples
        if anchors == 0 and multiples == 0:
            score -= 1
        # Two decisive tests. (1) With this width's pitch, how much of the device-edge
        # evidence falls on one unit grid? (2) Do the strips over these two x repeat at that
        # pitch (holes, cage nuts, screws)? The real rails do both; a cabinet frame, inner
        # texture or the back of a server body does neither.
        q, _how = pitch_from_bands(bounds, float(xr - xl))
        coef, inl = fit_grid(bounds, q, curve=False)
        wts = np.array([b[1] for b in bounds], float)
        support = float(wts[inl].sum() / max(1e-6, wts.sum()))
        y0, y1 = int(bounds[0][0]), int(bounds[-1][0])
        rail = 0.0
        if PAIR_RAIL_MIN <= 1.0:
            reg = rail_regularity(rail_strips(gray, xl, xr), coef[1])
            rail = float(np.median(reg[max(0, y0):max(y0 + 1, y1)])) if len(reg) > y1 else 0.0
        scored.append((support, score, xr - xl, c, xl, xr, bounds, rail))
    if not scored:
        return None
    best_rail = max(s[7] for s in scored)
    good = [s for s in scored if s[7] >= PAIR_RAIL_FRAC * best_rail] if best_rail >= PAIR_RAIL_MIN else scored
    best_support = max(s[0] for s in good)
    good = [s for s in good if s[0] >= PAIR_SUPPORT_FRAC * best_support]
    best_score = max(s[1] for s in good)
    good = [s for s in good if s[1] >= PAIR_SCORE_FRAC * best_score] if best_score > 0 else good
    support, score, _, _, xl, xr, bounds, rail = max(good, key=lambda s: (s[2], s[0], s[1]))
    return xl, xr, bounds, score


def _peaks(v, min_height, min_dist):
    """Local maxima of a 1-D array at least min_height high and min_dist apart, strongest first."""
    idx = [i for i in range(1, len(v) - 1) if v[i] >= min_height and v[i] >= v[i - 1] and v[i] >= v[i + 1]]
    idx.sort(key=lambda i: -v[i])
    out = []
    for i in idx:
        if all(abs(i - j) >= min_dist for j in out):
            out.append(i)
    return out


# ---------------------------------------------------------------- device lines
def _merge_lines(ys, weights, rack_width):
    """Consecutive rows are one edge; edges closer than a quarter unit are one boundary.
    Returns [(y, strength)]."""
    if len(ys) == 0:
        return []
    merge_px = MERGE_FRAC * PITCH_OVER_WIDTH * rack_width
    groups, cur = [], [0]
    for i in range(1, len(ys)):
        if ys[i] - ys[cur[-1]] <= max(2, merge_px):
            cur.append(i)
        else:
            groups.append(cur)
            cur = [i]
    groups.append(cur)
    return [(float(np.average(ys[g], weights=weights[g])), len(g)) for g in groups]


def device_lines_coverage(mask, x_left, x_right, with_cabinet=False):
    """Stages "outer" and later: a device line is any row whose line pixels cover at
    least half the rack width; a row that also carries the line well outside BOTH rails
    is a cabinet edge and is dropped (returned separately with with_cabinet=True, since
    the ladder must never cross one). Rows on the photo's own edge are not devices."""
    h, w = mask.shape
    rw = float(x_right - x_left)
    cover = mask[:, x_left:x_right].mean(axis=1)
    m0, m1 = int(MARGIN_FRAC[0] * rw), int(MARGIN_FRAC[1] * rw)
    lm = mask[:, max(0, x_left - m1):max(0, x_left - m0)]
    rm = mask[:, min(w, x_right + m0):min(w, x_right + m1)]
    margins = [m.mean(axis=1) > 0.5 for m in (lm, rm) if m.shape[1] >= 3]
    cabinet = np.logical_and.reduce(margins) if margins else np.zeros(h, bool)
    covered = cover >= LINE_COVER_FRAC
    is_line = covered & ~cabinet
    border = max(2, int(BORDER_FRAC * h))
    is_line[:border] = False
    is_line[h - border:] = False
    ys = np.flatnonzero(is_line)
    lines = _merge_lines(ys.astype(float), cover[ys], rw)
    if not with_cabinet:
        return lines
    cy = np.flatnonzero(covered & cabinet)
    return lines, [y for y, _ in _merge_lines(cy.astype(float), cover[cy], rw)]


def fit_grid(bounds, q0, curve=True, q_tol=None):
    """Pitch and phase together: the unit grid that the most device edges fall on.

    The pitch may move FIT_Q_TOL from the 1U-band estimate q0 (never further, so half or
    double pitches cannot win), and every device edge is tried as the grid's origin. A
    device's top and bottom edges both land on the grid; its mid-face seam lands only on
    its own, so the edges outvote the seams and the phase comes out on the unit boundaries.
    Returns (q, y0, inlier mask over bounds)."""
    ys = np.array([b[0] for b in bounds], float)
    w = np.array([b[1] for b in bounds], float)
    q_tol = FIT_Q_TOL if q_tol is None else q_tol
    best = None
    for q in np.linspace(q0 * (1 - q_tol), q0 * (1 + q_tol), 2 * int(q_tol * 200) + 1):
        for y0 in ys:
            k = np.round((ys - y0) / q)
            res = np.abs(ys - (y0 + k * q)) / q
            inl = res <= FIT_IN_U
            score = float((w[inl] * (1 - res[inl] / FIT_IN_U)).sum())
            if best is None or score > best[0]:
                best = (score, q, y0, inl)
    _, q, y0, inl = best
    # least-squares refinement on the inliers: ys ~ y0 + k q
    k = np.round((ys[inl] - y0) / q)
    if inl.sum() >= 2 and np.ptp(k) > 0:
        A = np.vstack([np.ones(inl.sum()), k]).T
        sol, *_ = np.linalg.lstsq(A * w[inl, None], ys[inl] * w[inl], rcond=None)
        y0_new, q_new = float(sol[0]), float(sol[1])
        if abs(q_new / q0 - 1) <= q_tol * 1.5 and q_new > 0:
            y0, q = y0_new, q_new
    # A phone held a little above or below the rack's middle makes the units a few percent
    # taller at the near end. Let the pitch change linearly down the rack (y = a + b k + c k^2)
    # when enough edges support it and the change stays small; the curved grid wins only if
    # it explains at least as many edges as the straight one.
    kall = np.round((ys - y0) / q)
    inl = np.abs(ys - (y0 + kall * q)) / q <= FIT_IN_U
    coef = (y0, q, 0.0)
    span = max(1.0, (ys.max() - ys.min()) / q)

    def assign(a, b, c):
        """Which grid line each edge belongs to under y = a + b k + c k^2, and how far off it is."""
        k = np.round((ys - a) / b)
        k = np.round((ys - a - c * k * k) / b)
        return k, np.abs(ys - (a + b * k + c * k * k)) / b

    best = (float((w[inl] * (1 - np.abs(ys - (y0 + kall * q))[inl] / q / FIT_IN_U)).sum()), coef, inl)
    if not curve:
        return coef, inl
    # The straight grid fits one end of a drifting rack and loses the other, so the curved
    # grids are searched outright: every pitch change from -15 % to +15 % across the ladder.
    for r in np.linspace(-FIT_CURVE_MAX, FIT_CURVE_MAX, 31):
        c = r * q / (2 * span)
        k, res = assign(y0, q, c)
        ok = res <= FIT_IN_U
        if ok.sum() < 4 or np.ptp(k[ok]) < 3:
            continue
        A = np.vstack([np.ones(ok.sum()), k[ok], k[ok] ** 2]).T
        sol, *_ = np.linalg.lstsq(A * w[ok, None], ys[ok] * w[ok], rcond=None)
        a2, b2, c2 = (float(v) for v in sol)
        if not (b2 > 0 and abs(b2 / q0 - 1) <= FIT_Q_TOL * 1.5 and abs(2 * c2 * span) <= FIT_CURVE_MAX * b2):
            continue
        k2, res2 = assign(a2, b2, c2)
        ok2 = res2 <= FIT_IN_U
        score = float((w[ok2] * (1 - res2[ok2] / FIT_IN_U)).sum())
        if score > best[0] + 1e-6:
            best = (score, (a2, b2, c2), ok2)
    _, coef, inl = best
    return coef, inl


def lay_grid(bounds, coef, inl):
    """Unit lines of the fitted grid from the first device edge to the last (every device
    edge, not only the ones on the grid: a device off the grid is still a device).
    Returns the lines and the pitch at the top and at the bottom of the ladder."""
    a, b, c = coef
    ys = np.array([b_[0] for b_ in bounds], float)
    k_min = int(np.round((ys.min() - a) / b))
    k_max = int(np.round((ys.max() - a) / b))
    lines = [a + b * k + c * k * k for k in range(k_min, k_max + 1)]
    return lines, (b + 2 * c * k_min, b + 2 * c * k_max)


# ---------------------------------------------------------------- pitch and ladder
def pitch_from_bands(bounds, rack_width):
    """The 1U pitch in pixels, plus how it was found.

    A band between two boundaries whose height is within PITCH_TOL of the physical prior
    is a 1U device: the pitch is the strength-weighted median of those. If there is none,
    the pitch is the value near the prior that best explains every band as a whole number
    of units. Either way it is then refined over all bands that fit a whole number.
    """
    prior = PITCH_OVER_WIDTH * rack_width
    ys = [b[0] for b in bounds]
    d = np.diff(ys)
    w = np.array([min(bounds[i][1], bounds[i + 1][1]) for i in range(len(bounds) - 1)], float)
    if len(d) == 0:
        return prior, {"how": "prior only", "anchors": 0, "prior": prior}
    one_u = np.abs(d - prior) <= PITCH_TOL * prior
    if one_u.any():
        order = np.argsort(d[one_u])
        dd, ww = d[one_u][order], w[one_u][order]
        q = float(dd[min(len(dd) - 1, np.searchsorted(np.cumsum(ww), 0.5 * ww.sum()))])
        how = "1U device band"
    else:
        qs = np.linspace(0.85 * prior, 1.15 * prior, 61)
        cost = [np.sum(w * np.abs(d / q - np.round(d / q))) for q in qs]
        q = float(qs[int(np.argmin(cost))])
        how = "whole-number fit"
    n = np.round(d / q)
    fit = (n >= 1) & (np.abs(d - n * q) <= 0.15 * q) & (n <= 8)
    if fit.any():
        q = float(d[fit].sum() / n[fit].sum())
    return q, {"how": how, "anchors": int(one_u.sum()), "prior": prior, "bands": int(len(d)),
               "bands_fit": int(fit.sum())}


# ---------------------------------------------------------------- where the rack ends
def rail_strips(gray, x_left, x_right):
    """The two image strips over the rails (holes, cage nuts, screws), as float arrays
    with slow brightness changes removed; empty when a rail falls outside the photo."""
    h, w = gray.shape
    rw = float(x_right - x_left)
    a0, a1 = int(RAIL_STRIP[0] * rw), int(RAIL_STRIP[1] * rw)
    g = gray.astype(np.float32)
    strips = []
    for s0, s1 in ((x_left + a0, x_left + a1), (x_right - a1, x_right - a0)):
        s0, s1 = max(0, s0), min(w, s1)
        if s1 - s0 >= 4:
            strip = g[:, s0:s1]
            strips.append(strip - cv2.blur(strip, (1, (3 * max(8, int(0.1 * rw))) | 1)))
    return strips


def rail_texture_energy(gray, x_left, x_right):
    """Stage "outer": per row, the mean vertical-edge energy over the two rail strips."""
    h, w = gray.shape
    rw = float(x_right - x_left)
    gy = np.abs(cv2.Sobel(cv2.GaussianBlur(gray, (3, 3), 0), cv2.CV_32F, 0, 1, ksize=3))
    a0, a1 = int(-0.02 * rw), int(0.05 * rw)
    prof = np.zeros(h, np.float32)
    n = 0
    for s0, s1 in ((x_left + a0, x_left + a1), (x_right - a1, x_right - a0)):
        s0, s1 = max(0, s0), min(w, s1)
        if s1 > s0:
            prof += gy[:, s0:s1].mean(axis=1)
            n += 1
    return prof / max(1, n)


def rail_regularity_1d(strips, q):
    """Stage "pairs": the strip's row profile compared with itself one unit further down,
    exact lag only. Sharp holes and a pitch a few percent off make this read near zero."""
    if not strips:
        return np.zeros(1, np.float32)
    n = strips[0].shape[0]
    L = max(4, int(round(q)))
    out = np.zeros(n, np.float32)
    for strip in strips:
        t = strip.mean(axis=1)
        reg = np.zeros(n, np.float32)
        for y in range(0, n - 2 * L):
            a, b = t[y:y + L], t[y + L:y + 2 * L]
            sa, sb = a.std(), b.std()
            if sa > 1e-6 and sb > 1e-6:
                reg[y + L] = float(np.dot(a - a.mean(), b - b.mean()) / (L * sa * sb))
        out = np.maximum(out, reg)
    return uniform_filter1d(out, max(3, L // 2) | 1, mode="nearest")


def rail_regularity(strips, q):
    """Per row, how well the rail strip two units above a row matches the two units below
    it (correlation, -1..1, best column of the best rail). The holes, cage nuts and screws
    on a rail repeat every unit, so this is high over the mounting space; a plain cabinet
    frame does not repeat at the unit pitch and scores near zero.

    Holes occupy a few columns of the strip and the rest is cabinet, so every column
    (averaged with its neighbours) is tested on its own. The holes are a few pixels tall,
    so the match is sharp in the lag: lags within RAIL_LAG_TOL of the pitch are tried.
    """
    if not strips:
        return np.zeros(1, np.float32)
    n = strips[0].shape[0]
    L = max(4, int(round(q)))
    W = 2 * L
    lags = sorted(set(int(round(L * s)) for s in np.linspace(1 - RAIL_LAG_TOL, 1 + RAIL_LAG_TOL, 2 * int(RAIL_LAG_TOL * L) + 1)))

    def wsum(v):
        c = np.cumsum(v, axis=0)
        c = np.vstack([np.zeros((1, v.shape[1])), c])
        return c[W:] - c[:-W]

    out = np.zeros(n, np.float32)
    for strip in strips:
        x = cv2.blur(strip.astype(np.float64), (3, 1))
        reg = np.zeros(n, np.float64)
        # the running sums of x and x*x from the top are the same for every lag: a lag only shortens them
        c1 = np.vstack([np.zeros((1, x.shape[1])), np.cumsum(x, axis=0)])
        c2 = np.vstack([np.zeros((1, x.shape[1])), np.cumsum(x * x, axis=0)])
        # the window sum and variance at every start row, once per strip: the upper half of a lag is the first
        # windows, the lower half the same windows read `lag` rows later
        s1 = c1[W:] - c1[:-W]
        var = (c2[W:] - c2[:-W]) - s1 * s1 / W
        for lag in lags:
            m = n - lag
            if m <= W or lag < 2:
                continue
            a, b = x[:m], x[lag:lag + m]
            k = m + 1 - W
            sa, va = s1[:k], var[:k]
            sb, vb = s1[lag:lag + k], var[lag:lag + k]
            cov = wsum(a * b) - sa * sb / W
            corr = cov / np.sqrt(np.maximum(va * vb, 1e-9))
            corr[(va < 1e-6) | (vb < 1e-6)] = 0
            best = corr.max(axis=1)
            centre = slice(L, L + len(best))
            reg[centre] = np.maximum(reg[centre], best)
        out = np.maximum(out, reg.astype(np.float32))
    return uniform_filter1d(out, max(3, L // 2) | 1, mode="nearest")


def _extend(lines, q, on, barriers=(), q_ends=None, on_up=None):
    """Walk out from the first and last unit line while `on` holds; add whole rows.
    Never walk across a barrier (a cabinet edge). q_ends = (pitch at the top, pitch at
    the bottom) when the grid is curved; otherwise q is used at both ends. on_up, when
    given, is the stricter test used for the walk upwards (both rails must repeat: the
    cabinet roof, studs and vents above a rack fool one rail strip more often than two)."""
    q_top, q_bot = q_ends if q_ends else (q, q)
    on_up = on if on_up is None else on_up
    h = len(on)
    y0, y1 = int(lines[0]), int(lines[-1])
    above = [int(b) for b in barriers if b < y0 - 0.3 * q_top]
    below = [int(b) for b in barriers if b > y1 + 0.3 * q_bot]
    top_limit = max(above) if above else 0
    bot_limit = min(below) if below else h - 1
    top = y0
    while top - 1 >= top_limit and on_up[top - 1]:
        top -= 1
    bot = y1
    while bot + 1 <= bot_limit and on[bot + 1]:
        bot += 1
    up = int((lines[0] - top) / q_top + (1 - EXTEND_MIN_U))
    down = int((bot - lines[-1]) / q_bot + (1 - EXTEND_MIN_U))
    up = max(0, min(up, MAX_RACK_UNITS - (len(lines) - 1), EXTEND_UP_MAX_U))
    down = max(0, min(down, MAX_RACK_UNITS - (len(lines) - 1) - up))
    above = [max(float(top), lines[0] - (i + 1) * q_top) for i in range(up)][::-1]
    below = [min(float(bot), lines[-1] + (i + 1) * q_bot) for i in range(down)]
    return above + list(lines) + below, (top, bot)


def extend_over_rails(lines, q, gray, x_left, x_right, how="repeat", barriers=(), q_ends=None):
    """Add unit lines above the first device and below the last while the rails still
    look like rack; stop at the image edge or a cabinet edge. `how` picks the stage's test."""
    y0, y1 = int(lines[0]), int(lines[-1])
    if how == "texture":
        prof = rail_texture_energy(gray, x_left, x_right)
        sm = uniform_filter1d(prof, max(3, int(round(q))) | 1, mode="nearest")
        level = float(np.median(sm[max(0, y0):max(y0 + 1, y1)]))
        on = sm >= 0.35 * level
        return _extend(lines, q, on, barriers, q_ends)
    strips = rail_strips(gray, x_left, x_right)
    reg = rail_regularity_1d(strips, q) if how == "repeat1d" else rail_regularity(strips, q)
    if len(reg) < 2:
        return list(lines), (y0, y1)
    level = float(np.median(reg[max(0, y0):max(y0 + 1, y1)]))
    on_up = None
    if how == "repeat1d":
        on = reg >= min(0.40, max(0.20, 0.5 * level))
    else:
        thr = min(RAIL_REGULAR_MAX, max(RAIL_REGULAR_MIN, 0.5 * level))
        gap = max(1, int(round(0.5 * q)))

        def bridged(r):
            # a cable across the rail breaks the repetition for a few rows; gaps up to half
            # a unit are bridged
            return np.convolve((r >= thr).astype(np.float32), np.ones(gap), mode="same") > 0

        on = bridged(reg)
        if RAIL_TOP_BOTH and len(strips) == 2:
            per_rail = [bridged(rail_regularity([s], q)) for s in strips]
            on_up = per_rail[0] & per_rail[1]
    return _extend(lines, q, on, barriers, q_ends, on_up)


# ---------------------------------------------------------------- the whole thing


_FONT = {}


def _font(px):
    """A clean bold sans for the unit tags (SF on macOS, else PIL's default)."""
    from PIL import ImageFont
    if px not in _FONT:
        for path in ("/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/HelveticaNeue.ttc", "DejaVuSans-Bold.ttf"):
            try:
                f = ImageFont.truetype(path, px)
                try:
                    f.set_variation_by_name("Bold")
                except Exception:  # noqa: BLE001 - not a variable font
                    pass
                _FONT[px] = f
                break
            except OSError:
                continue
        else:
            _FONT[px] = ImageFont.load_default()
    return _FONT[px]


def draw(bgr, rows, info, max_h=1400, truth=None, unit_ok=None, simple=True, marks=False):
    """The finished result, drawn like a rack elevation: the photo dimmed slightly outside the rack, every unit
    bounded by a thin white line with a faint tint on every other unit, a dark numbering strip on the rack's
    left edge (U1 at the bottom) and a small badge with the unit count. No grading colours.
    marks=True (only where a page explains a mistake): the number of each wrong unit (unit_ok False) turns amber.
    truth and simple are accepted for older callers and ignored. Drawn at the output size, so it stays crisp."""
    from PIL import Image, ImageDraw
    H0, W0 = bgr.shape[:2]
    f = min(1.0, max_h / H0)
    vis = cv2.resize(bgr, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else bgr.copy()
    H, Wd = vis.shape[:2]
    S = lambda v: int(round(v * f))
    boxes = [(S(r["box"][0]), S(r["box"][1]), S(r["box"][2]), S(r["box"][3])) for r in rows]
    if not boxes:
        return vis
    xl = max(0, min(b[0] for b in boxes)); xr = min(Wd - 1, max(b[2] for b in boxes))
    yt = max(0, boxes[0][1]); yb = min(H - 1, boxes[-1][3])
    wrong = [bool(marks and unit_ok is not None and not unit_ok[k]) for k in range(len(boxes))]
    # 1. focus: dim everything outside the drawn rack
    dim = (vis * 0.55).astype(vis.dtype)
    mask = np.zeros((H, Wd), bool); mask[yt:yb, xl:xr] = True
    vis = np.where(mask[..., None], vis, dim)
    # 2. faint tint on every other unit (RackTrack blue)
    tint = vis.copy()
    for k, (x1, y1, x2, y2) in enumerate(boxes):
        if k % 2 == 0:
            cv2.rectangle(tint, (x1, y1), (x2, y2), (207, 95, 29), -1)
    vis = cv2.addWeighted(tint, 0.12, vis, 0.88, 0)
    img = Image.fromarray(cv2.cvtColor(vis, cv2.COLOR_BGR2RGB)).convert("RGBA")
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0)); d = ImageDraw.Draw(lay)
    lw = max(1, round(H / 900))
    # 3. unit boundaries: thin white; the rack's top and bottom a little stronger
    ys = [boxes[0][1]] + [b[3] for b in boxes]
    for j, y in enumerate(ys):
        end = j in (0, len(ys) - 1)
        d.line((xl, y, xr, y), fill=(255, 255, 255, 235 if end else 150), width=lw + (1 if end else 0))
    d.line((xl, yt, xl, yb), fill=(255, 255, 255, 200), width=lw); d.line((xr, yt, xr, yb), fill=(255, 255, 255, 200), width=lw)
    # 4. numbering strip on the left edge, like the numbers printed on a rack rail
    pitch = float(np.median([y2 - y1 for _, y1, _, y2 in boxes]))
    px = int(np.clip(pitch * 0.46, 9, 26)); font = _font(px)
    n = len(boxes)
    sw = int(d.textbbox((0, 0), f"U{n}", font=font)[2] + px * 0.9)
    x0 = xl - sw if xl - sw >= 0 else xl
    d.rectangle((x0, yt, x0 + sw, yb), fill=(14, 20, 30, 200))
    step = 1 if pitch >= 11 else 2
    for k, (x1, y1, x2, y2) in enumerate(boxes):
        num = n - k
        d.line((x0, y1, x0 + sw, y1), fill=(255, 255, 255, 70), width=1)
        if (num - 1) % step == 0:
            col = (255, 183, 77, 255) if wrong[k] else (255, 255, 255, 240)
            if wrong[k]:
                d.rectangle((x0 + 1, y1 + 1, x0 + sw - 1, y2 - 1), fill=(120, 60, 0, 160))
            d.text((x0 + sw / 2, (y1 + y2) / 2), f"U{num}", font=font, fill=col, anchor="mm")
    # 5. the count, as a badge above the rack (inside it when there is no room)
    bf = _font(int(np.clip(H / 42, 14, 30))); label = f"{n} U"
    tw = d.textbbox((0, 0), label, font=bf)[2]; bh = bf.size * 1.6; bw = tw + bf.size * 1.4
    above = yt - bh - 6 >= 4
    by = yt - bh - 6 if above else yt + 6
    bx = (x0 if x0 + bw < Wd else max(0, Wd - bw - 4)) if above else max(0, min(Wd - bw - 4, xr - bw - 6))   # inside the rack: at the right, never over the U labels on the left (user, 2026-10-30)
    d.rounded_rectangle((bx, by, bx + bw, by + bh), radius=bh / 2, fill=(14, 20, 30, 225))
    d.text((bx + bw / 2, by + bh / 2), label, font=bf, fill=(255, 255, 255, 255), anchor="mm")
    img = Image.alpha_composite(img, lay).convert("RGB")
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
