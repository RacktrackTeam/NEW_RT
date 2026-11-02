"""Approach "posts" / "Unit height by vote": the photo is straightened (teammate rectify), the rack width is
chosen inside the units model's Rack box (candidates from line ends, long vertical edges and the columns that
repeat every unit, scored on five checks), the UNIT HEIGHT is decided by a vote between independent estimates
(19-inch rule on the width, the posts' repeat distance, the best repeating column, the most common gap between
device edges; estimates within 5 % agree, estimates implying rails wider than the box count little), then the
best-chain lines run between the rack's top and bottom (Rack box capped at the cross rails and cabinet edges).

    rows, info = unit_persp.find(bgr, debug=True, chain=True)   # like unit_logic.find_units(debug=True)
"""
import copy
import hashlib
import pathlib

import cv2
import numpy as np

import unit_logic as ul
import unit_methods as um

# The steps that do not depend on the rack size (the working copy, the straightening, the grey image, the line
# mask, the vanishing point, the column continuity) are computed once per photo while find_any_rack runs its
# passes; outside find_any_rack nothing is kept. Keys carry a digest of the image, so a different image never hits.
_MEMO = None


def _memo(key, fn, copy_out=False):
    if _MEMO is None:
        return fn()
    if key not in _MEMO:
        _MEMO[key] = fn()
    return copy.deepcopy(_MEMO[key]) if copy_out else _MEMO[key]


def _digest(a):
    return hashlib.blake2b(np.ascontiguousarray(a).tobytes(), digest_size=8).digest()

BOX_WIDTH_RANGE = (0.55, 1.15)   # the rails span this share of the Rack box's width (800 mm cabinet 0.60 ... open frame 0.95)
BOX_CENTRE_TOL = 0.08            # ... and sit centred in it, within this share of its width
POST_WINDOW = 0.06               # a post segment lies within this share of the rack width of the rail x
POST_MIN_LEN = 0.06              # a near-vertical segment at least this share of the image height is a post candidate
POST_MAX_TILT = 0.25             # |dx/dy| of a post line; steeper is not a post
MAX_FILL_U = 0.4                 # a unit is added at an end only when it fits inside the rack to within this many units


# ---------------------------------------------------------------- posts by their repeating holes
PERIOD_PCT = 70          # a column's repetition score is this percentile of its windowed self-correlation
PERIOD_STRONG = 0.5      # a symmetric pair of columns repeating this well IS the pair of posts


def _windowed_corr(t, L, W):
    """Normalised correlation of profile t with itself shifted by L, in sliding windows of W rows."""
    a, b = t[:-L], t[L:]
    n = len(a)
    if n < W + 2:
        return np.zeros(1)

    def ws(v):
        c = np.concatenate([[0.0], np.cumsum(v)])
        return c[W:] - c[:-W]

    sa, sb, saa, sbb, sab = ws(a), ws(b), ws(a * a), ws(b * b), ws(a * b)
    cov = sab - sa * sb / W
    va = saa - sa * sa / W
    vb = sbb - sb * sb / W
    c = cov / np.sqrt(np.maximum(va * vb, 1e-9))
    c[(va < 1e-6) | (vb < 1e-6)] = 0
    return c


def _windowed_corr_pct(T, L, W, C1=None, C2=None):
    """_windowed_corr for every column of T at once, reduced to its PERIOD_PCT percentile per column.
    C1, C2: running sums of T and T*T from the top (with a leading zero row); the upper half's sums are their prefixes."""
    a, b = T[:-L], T[L:]
    n = len(a)
    if n < W + 2:
        return np.zeros(T.shape[1])

    def ws(v):
        c = np.concatenate([np.zeros((1, v.shape[1])), np.cumsum(v, axis=0)])
        return c[W:] - c[:-W]

    if C1 is None:
        sa, saa, sb, sbb = ws(a), ws(a * a), ws(b), ws(b * b)
    else:   # the lower half starts L rows down: its window sums are the same running sums, read L rows later
        sa, saa = C1[W:n + 1] - C1[:n + 1 - W], C2[W:n + 1] - C2[:n + 1 - W]
        sb, sbb = C1[L + W:L + n + 1] - C1[L:L + n + 1 - W], C2[L + W:L + n + 1] - C2[L:L + n + 1 - W]
    sab = ws(a * b)
    cov = sab - sa * sb / W
    va = saa - sa * sa / W
    vb = sbb - sb * sb / W
    c = cov / np.sqrt(np.maximum(va * vb, 1e-9))
    c[(va < 1e-6) | (vb < 1e-6)] = 0
    return np.percentile(c, PERIOD_PCT, axis=0)


def column_periodicity(gray, box, step=3):
    """For columns across the Rack box: how well the column's brightness profile repeats at some lag in
    the range one unit can have for this box (0.55 .. 1.12 of the box width x 0.0953), and that lag.
    The mounting holes, cage nuts and screws on a post repeat every unit, so the posts stand out.
    Returns xs, best_lag, best_corr (one entry per sampled column)."""
    from scipy.ndimage import uniform_filter1d
    h, w = gray.shape
    bx1, by1, bx2, by2 = [int(round(v)) for v in box]
    bw = bx2 - bx1
    y0, y1 = max(0, by1), min(h, by2)
    g = cv2.blur(gray[y0:y1].astype(np.float32), (3, 1))
    half = max(1, int(0.004 * bw))
    lo_l = int(np.floor(ul.PITCH_OVER_WIDTH * bw * BOX_WIDTH_RANGE[0]))
    hi_l = int(np.ceil(ul.PITCH_OVER_WIDTH * bw * BOX_WIDTH_RANGE[1]))
    lags = np.arange(max(4, lo_l), max(5, hi_l) + 1)
    xs = np.arange(max(half, bx1 - int(0.04 * bw)), min(w - half, bx2 + int(0.04 * bw)), step)
    best_lag, best_c = np.zeros(len(xs)), np.zeros(len(xs))
    if len(lags) == 0 or y1 - y0 < 3 * lags.max():
        return xs, best_lag, best_c
    if len(xs) == 0:
        return xs, best_lag, best_c
    hp = int(2 * lags.max()) | 1
    T = np.stack([g[:, x - half:x + half + 1].mean(axis=1) for x in xs], axis=1)   # one brightness profile per column
    T = T - uniform_filter1d(T, hp, axis=0, mode="nearest")
    C1 = np.concatenate([np.zeros((1, T.shape[1])), np.cumsum(T, axis=0)])
    C2 = np.concatenate([np.zeros((1, T.shape[1])), np.cumsum(T * T, axis=0)])
    CS = np.stack([_windowed_corr_pct(T, L, 2 * L, C1, C2) for L in lags], axis=1)  # (column, lag)
    for i, x in enumerate(xs):
        cs = CS[i]
        j = int(np.argmax(cs))
        lag = float(lags[j])
        if 0 < j < len(lags) - 1:
            # sub-pixel: parabola through the three correlations around the best integer lag
            d = cs[j - 1] - 2 * cs[j] + cs[j + 1]
            if d < 0:
                lag += 0.5 * (cs[j - 1] - cs[j + 1]) / d
        best_lag[i], best_c[i] = lag, cs[j]
    return xs, best_lag, best_c


def post_pair(period, box):
    """The best pair of repeating columns that is symmetric about the box centre, has the same lag on
    both sides, and whose lag agrees with the 19-inch rule for its width. (corr, xl, xr, lag) or None."""
    xs, lag, c = period
    bw, bc = box[2] - box[0], (box[0] + box[2]) / 2
    left = sorted([(c[k], xs[k], lag[k]) for k in range(len(xs)) if xs[k] < bc - 0.2 * bw], reverse=True)[:60]
    right = sorted([(c[k], xs[k], lag[k]) for k in range(len(xs)) if xs[k] > bc + 0.2 * bw], reverse=True)[:60]
    best = None
    for cl, xl, Ll in left:
        for cr, xr, Lr in right:
            if Lr <= 0 or abs(Ll / Lr - 1) > 0.06 or abs((xl + xr) / 2 - bc) > 0.06 * bw:
                continue
            if not (BOX_WIDTH_RANGE[0] * bw <= xr - xl <= BOX_WIDTH_RANGE[1] * bw):
                continue
            if abs((Ll + Lr) / 2 / (ul.PITCH_OVER_WIDTH * (xr - xl)) - 1) > 0.12:
                continue
            s = min(cl, cr)
            if best is None or s > best[0]:
                best = (float(s), float(xl), float(xr), float((Ll + Lr) / 2))
    return best


def subharmonic_fix(gray, box, xs_cols, lag, corr, ratio=1.5, keep=0.7, min_corr2=0.15):
    """The posts' holes come three per unit, so a lag of two thirds of a unit correlates almost as well as a
    full unit. If the lag times `ratio` still repeats at least `keep` as well at the same columns, the unit
    height is lag * ratio. Returns (lag, corr, fixed: bool)."""
    from scipy.ndimage import uniform_filter1d
    h, w = gray.shape
    bx1, by1, bx2, by2 = [int(round(v)) for v in box]
    bw = bx2 - bx1
    y0, y1 = max(0, by1), min(h, by2)
    L2 = int(round(lag * ratio))
    if L2 > ul.PITCH_OVER_WIDTH * bw * BOX_WIDTH_RANGE[1] * 1.02 or y1 - y0 < 3 * L2 + 4:
        return lag, corr, False
    g = cv2.blur(gray[y0:y1].astype(np.float32), (3, 1))
    half = max(1, int(0.004 * bw))
    hp = int(2 * L2) | 1
    best2, best1 = 0.0, 0.0
    for x in xs_cols:
        for dx in (-half, 0, half):
            xx = int(min(w - half - 1, max(half, x + dx)))
            t = g[:, xx - half:xx + half + 1].mean(axis=1)
            t = t - uniform_filter1d(t, hp, mode="nearest")
            for L in (L2 - 1, L2, L2 + 1):
                best2 = max(best2, float(np.percentile(_windowed_corr(t, L, 2 * L), PERIOD_PCT)))
            L1 = int(round(lag))
            best1 = max(best1, float(np.percentile(_windowed_corr(t, L1, 2 * L1), PERIOD_PCT)))
    if best2 >= keep * best1 and best2 > min_corr2:
        return lag * ratio, best2, True
    return lag, corr, False


def _period_at(period, x, bw):
    xs, lag, c = period
    near = np.abs(xs - x) <= max(2, 0.015 * bw)
    return float(c[near].max()) if near.any() else 0.0


# ---------------------------------------------------------------- rack width
W_SUPPORT, W_WHOLE, W_CONT, W_RAIL, W_PERIOD = 1.0, 2.0, 0.5, 1.0, 2.0   # weights of the width score


def choose_width(mask, rows, gray, box, period=None):
    """Rail pair (x_left, x_right, device lines ...): candidate pairs from the line ends, the long vertical
    edges and the repeating columns, kept only when they fit inside the Rack box; each scored by how well
    (a) the device edges fall on a unit grid at the 19-inch pitch of that width, (b) the gaps between
    device edges are whole units, (c) both x run the height as a vertical edge, (d) the strips over them
    repeat at the unit pitch, (e) the columns repeat at all. The best score wins."""
    h, w = gray.shape
    pairs = list(ul.rack_extent_candidates(rows, w)) if rows else []
    cont = _memo(("cont", _digest(gray)), lambda: ul.vertical_continuity(gray))
    peaks = ul._peaks(cont, 0.45, int(0.02 * w))
    pp = None
    if box is not None:
        bx1, bx2 = box[0], box[2]
        bw, bc = bx2 - bx1, (bx1 + bx2) / 2
        lefts = [x for x in peaks if bx1 - 0.05 * bw <= x < bc - 0.2 * bw][:6] + [bx1, bx1 + 0.02 * bw]
        rights = [x for x in peaks if bc + 0.2 * bw < x <= bx2 + 0.05 * bw][:6] + [bx2, bx2 - 0.02 * bw]
        pairs += [(int(round(xl)), int(round(xr))) for xl in lefts for xr in rights]
        pairs += [(int(round(bc - r * bw / 2)), int(round(bc + r * bw / 2))) for r in (0.80, 0.90, 0.60)]
        if period is not None:
            pp = post_pair(period, box)
            if pp:
                pairs.insert(0, (int(round(pp[1])), int(round(pp[2]))))
    band = ul.EXTENT_BAND_FRAC * w
    uniq = []
    for p_ in pairs:
        if p_[1] - p_[0] >= 0.2 * w and all(abs(p_[0] - q_[0]) > band or abs(p_[1] - q_[1]) > band for q_ in uniq):
            uniq.append((max(0, int(p_[0])), min(w - 1, int(p_[1]))))
    if box is not None:
        inside = [p_ for p_ in uniq if BOX_WIDTH_RANGE[0] * bw <= p_[1] - p_[0] <= BOX_WIDTH_RANGE[1] * bw
                  and abs((p_[0] + p_[1]) / 2 - bc) <= BOX_CENTRE_TOL * bw]
        uniq = inside or uniq
    scored = []
    for xl, xr in uniq:
        bounds = ul.device_lines_coverage(mask, xl, xr)
        if len(bounds) < ul.MIN_LINES:
            continue
        rw = float(xr - xl)
        prior = ul.PITCH_OVER_WIDTH * rw
        wts = np.array([b[1] for b in bounds], float)
        coef, inl = ul.fit_grid(bounds, prior, curve=False, q_tol=0.06)
        support = float(wts[inl].sum() / max(1e-6, wts.sum()))
        d = np.diff([b[0] for b in bounds])
        n = np.round(d / prior)
        rel = np.abs(d - n * prior) / prior
        bw_ = np.array([min(bounds[k][1], bounds[k + 1][1]) for k in range(len(bounds) - 1)], float)
        whole = float(bw_[(n >= 1) & (n <= 8) & (rel <= 0.10)].sum() / max(1e-6, bw_.sum())) if len(d) else 0.0
        anchors = int(np.sum((n == 1) & (rel <= 0.084)))
        y0, y1 = int(bounds[0][0]), int(bounds[-1][0])
        reg = ul.rail_regularity(ul.rail_strips(gray, xl, xr), coef[1])
        rail = float(np.median(reg[max(0, y0):max(y0 + 1, y1)])) if len(reg) > y1 else 0.0
        per = min(_period_at(period, xl, bw), _period_at(period, xr, bw)) if (period is not None and box is not None) else 0.0
        c = float(min(cont[xl], cont[xr]))
        score = W_SUPPORT * support + W_WHOLE * whole + W_CONT * c + W_RAIL * rail + W_PERIOD * per
        scored.append(dict(xl=xl, xr=xr, bounds=bounds, support=support, whole=whole, anchors=anchors, cont=c, rail=rail,
                           period=per, score=score, n=len(bounds), q_prior_fit=float(coef[1])))
    if not scored:
        return None
    pick = max(scored, key=lambda s: s["score"])
    pick["how"] = "best score"
    if pp and pp[0] >= PERIOD_STRONG:
        # the posts themselves were found: they are the width, and their lag is the unit height
        near = [s for s in scored if abs(s["xl"] - pp[1]) <= 0.02 * bw and abs(s["xr"] - pp[2]) <= 0.02 * bw]
        if near:
            pick = near[0]
            pick["how"] = f"posts repeating every {pp[3]:.0f} px (corr {pp[0]:.2f})"
            pick["q_posts"] = pp[3]
    pick["candidates"] = [(s["xl"], s["xr"], round(s["score"], 2), round(s["support"], 2), round(s["whole"], 2), round(s["rail"], 2), round(s["period"], 2)) for s in scored]
    pick["post_pair"] = pp
    return pick


# ---------------------------------------------------------------- posts as lines
def _fit_line(pts, wts, rw):
    """x = a + b y through weighted points, dropping outliers twice. Returns (a, b, n_used) or None."""
    pts, wts = np.asarray(pts, float), np.asarray(wts, float)
    keep = np.ones(len(pts), bool)
    sol = None
    for _ in range(3):
        if keep.sum() < 2 or np.ptp(pts[keep, 1]) < 1e-6:
            return None
        A = np.vstack([np.ones(keep.sum()), pts[keep, 1]]).T
        sol, *_ = np.linalg.lstsq(A * wts[keep, None], pts[keep, 0] * wts[keep], rcond=None)
        res = np.abs(pts[:, 0] - (sol[0] + sol[1] * pts[:, 1]))
        new = res <= 0.012 * rw
        if new.sum() < 2 or (new == keep).all():
            break
        keep = new
    return float(sol[0]), float(sol[1]), int(keep.sum())


def post_lines(gray, xl, xr, rows=None):
    """The left and right post as straight lines x = a + b*y (work pixels), from the long near-vertical
    LSD segments near each rail x. A side with too little segment length is not trusted: when only one
    side is found the photo is treated as rotated (both posts lean the same way); when neither is, the
    posts are taken as vertical (no straightening). Returns ((a_l, b_l), (a_r, b_r), how)."""
    h, w = gray.shape
    rw = float(xr - xl)
    lsd = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
    segs = lsd.detect(gray)[0]
    segs = segs.reshape(-1, 4) if segs is not None else np.zeros((0, 4), np.float32)
    found, how = {}, []
    for side, x_rail in (("left", xl), ("right", xr)):
        pts, wts = [], []
        for x1, y1, x2, y2 in segs:
            dy = abs(y2 - y1)
            if dy < POST_MIN_LEN * h or abs(x2 - x1) > POST_MAX_TILT * dy:
                continue
            if abs((x1 + x2) / 2 - x_rail) > POST_WINDOW * rw:
                continue
            pts += [(x1, y1), (x2, y2)]
            wts += [dy, dy]
        fit = _fit_line(pts, wts, rw) if len(pts) >= 4 else None
        total = sum(wts) / 2
        span = (max(p[1] for p in pts) - min(p[1] for p in pts)) if pts else 0
        if fit and total >= 0.3 * h and span >= 0.4 * h and abs(fit[1]) <= POST_MAX_TILT:
            found[side] = fit[:2]
            how.append(f"{side}: {fit[2] // 2} segments")
        else:
            how.append(f"{side}: not found")
    if "left" in found and "right" in found:
        (al, bl), (ar, br) = found["left"], found["right"]
        if abs(bl - br) > 0.12 or (ar - al) <= 0.2 * rw or (ar + br * h) - (al + bl * h) <= 0.2 * rw:
            return (float(xl), 0.0), (float(xr), 0.0), ["posts rejected (implausible lean): " + "; ".join(how)]
        return (al, bl), (ar, br), how
    if "left" in found or "right" in found:
        a, b = found.get("left", found.get("right"))
        # one post: same lean on both sides = the photo is rotated, not keystoned
        ym = h / 2.0
        if "left" in found:
            return (a, b), (float(xr) - b * ym, b), how + ["right assumed parallel"]
        return (float(xl) - b * ym, b), (a, b), how + ["left assumed parallel"]
    return (float(xl), 0.0), (float(xr), 0.0), how


def straighten(img, left, right):
    """Homography that makes the two post lines vertical and parallel, keeping the middle row's
    scale (so the 19-inch rule still holds there). Returns warped image, H, (x_left, x_right) after."""
    h, w = img.shape[:2]
    (al, bl), (ar, br) = left, right
    ym = h / 2.0
    xl_m, xr_m = al + bl * ym, ar + br * ym
    src = np.float32([[al, 0], [ar, 0], [ar + br * h, h], [al + bl * h, h]])
    dst = np.float32([[xl_m, 0], [xr_m, 0], [xr_m, h], [xl_m, h]])
    H = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(img, H, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return warped, H, (xl_m, xr_m)


def _map_y(H, x, ys):
    pts = np.float32([[[x, y] for y in ys]])
    return [float(p[1]) for p in cv2.perspectiveTransform(pts, H)[0]]


def _box_through(H, box):
    x1, y1, x2, y2 = box
    pts = cv2.perspectiveTransform(np.float32([[[x1, y1]], [[x2, y1]], [[x2, y2]], [[x1, y2]]]), H).reshape(-1, 2)
    return [float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())]


# ---------------------------------------------------------------- chain that stays on the unit height without evidence
def dp_chain_reg(E, lo, hi, q, tol=0.07, bonus=0.08, lam=0.3):
    """Best chain of rows one unit apart (within tol) with the most score, where every step pays
    lam * |step - q| / q: with no edge evidence the chain keeps the unit height instead of wandering."""
    step_lo, step_hi = int(round((1 - tol) * q)), int(round((1 + tol) * q))
    steps = np.arange(step_lo, step_hi + 1)
    pen = lam * np.abs(steps - q) / q
    n = hi - lo + 1
    dp = E[lo:hi + 1].astype(np.float64).copy()
    back = np.full(n, -1, int)
    for i in range(n):
        j = i - steps                       # candidate previous rows
        ok = j >= 0
        if not ok.any():
            continue
        cand = dp[j[ok]] - pen[ok]
        m = int(np.argmax(cand))
        v = cand[m] + bonus + E[lo + i]
        if v > dp[i]:
            dp[i] = v
            back[i] = j[ok][m]
    i = int(np.argmax(dp))
    chain = []
    while i >= 0:
        chain.append(lo + i)
        i = back[i]
    return chain[::-1]


# ---------------------------------------------------------------- rigid grid whose unit height changes linearly
def curved_grid(E, q, first, last, top, bot, q_tol=0.04, r_max=0.25, r_step=0.025, r_pen=0.0, n_q=9, r_fixed=None, region="devices", center=False):
    """Unit lines y_k with a unit height that changes linearly down the photo (perspective left after
    straightening), anchored on the voted unit height q (searched within +-q_tol) and a phase; the ladder
    whose lines collect the most row score E inside the device region [first, last] wins. Lines are then
    continued with the same rule up to `top` and down to `bot`. Returns (lines, q_mid, r)."""
    n = len(E)
    span = max(q, float(last - first))
    y_mid = (first + last) / 2.0
    region_lo, region_hi = (first - 0.3 * q, last + 0.3 * q) if region == "devices" else (top - 0.3 * q, bot + 0.3 * q)
    if center:
        a_, b_ = int(max(0, region_lo)), int(min(n, region_hi))
        if b_ > a_:
            E = E - float(np.mean(E[a_:b_]))
    best = None
    flat_best = -1.0
    r_values = np.arange(-r_max, r_max + 1e-9, r_step) if r_fixed is None else np.array([r_fixed])
    phases = np.arange(int(first - q), int(first) + 1)              # the line just above/at the first device edge
    for qm in np.linspace(q * (1 - q_tol), q * (1 + q_tol), n_q):
        for r in r_values:
            # offsets from the anchor line, stepping with the local unit height p(y) = qm * (1 + r * (y - y_mid) / span)
            def walk(y0, direction, limit):
                ys, y = [], y0
                for _ in range(ul.MAX_RACK_UNITS):
                    pstep = qm * (1 + r * (y - y_mid) / span)
                    if pstep < 0.5 * qm or pstep > 1.6 * qm:
                        break
                    y = y + direction * pstep
                    if (direction > 0 and y > limit) or (direction < 0 and y < limit):
                        break
                    ys.append(y)
                return ys
            a0 = float(first)
            down = walk(a0, +1, region_hi + 2 * qm)
            up = walk(a0, -1, region_lo - 2 * qm)
            off = np.array(up[::-1] + [a0] + down) - a0
            pos = phases[:, None] + off[None, :]                     # (n_phase, n_lines)
            inside = (pos >= region_lo) & (pos <= region_hi) & (pos >= 0) & (pos <= n - 1)
            idx = np.clip(np.round(pos).astype(int), 0, n - 1)
            sc = (E[idx] * inside).sum(axis=1) - r_pen * abs(r) * inside.sum(axis=1).mean()
            j = int(np.argmax(sc))
            if abs(r) < 1e-9 or r_fixed is not None:
                flat_best = max(flat_best, float(sc[j]))
            if best is None or sc[j] > best[0]:
                best = (float(sc[j]), float(phases[j]), float(qm), float(r))
    best_score, a, qm, r = best
    # the final ladder over the whole rack: walk from the anchor to the top and the bottom
    def walk_full(y0, direction, limit):
        ys, y = [], y0
        for _ in range(ul.MAX_RACK_UNITS):
            pstep = qm * (1 + r * (y - y_mid) / span)
            if pstep < 0.5 * qm or pstep > 1.6 * qm:
                break
            y = y + direction * pstep
            if (direction > 0 and y > limit) or (direction < 0 and y < limit):
                break
            ys.append(y)
        return ys
    lines = walk_full(a, -1, top - 0.4 * qm)[::-1] + [a] + walk_full(a, +1, bot + 0.4 * qm)
    return [float(y) for y in lines], qm, r, {"best": best_score, "flat": flat_best}


# ---------------------------------------------------------------- perspective from the vanishing point
def vertical_vanishing(gray, box, min_len=0.08, max_tilt=0.3):
    """Vanishing point of the long near-vertical segments inside the Rack box (RANSAC over pairwise
    intersections). Returns (y_vp, n_inliers, n_segments) or None. With a vanishing point at y_vp, a
    horizontal width W at row y scales as (y_vp - y), so the unit height changes linearly down the rack."""
    h, w = gray.shape
    lsd = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
    segs = lsd.detect(gray)[0]
    if segs is None:
        return None
    segs = segs.reshape(-1, 4)
    bx1, by1, bx2, by2 = box
    keep = []
    for x1, y1, x2, y2 in segs:
        dy = abs(y2 - y1)
        if dy < min_len * h or abs(x2 - x1) > max_tilt * dy:
            continue
        xm = (x1 + x2) / 2
        if not (bx1 - 0.05 * (bx2 - bx1) <= xm <= bx2 + 0.05 * (bx2 - bx1)):
            continue
        keep.append((x1, y1, x2, y2, dy))
    if len(keep) < 6:
        return None
    # lines in homogeneous form, weighted by length
    L = []
    for x1, y1, x2, y2, dy in keep:
        p1, p2 = np.array([x1, y1, 1.0]), np.array([x2, y2, 1.0])
        L.append((np.cross(p1, p2), dy))
    rng = np.random.default_rng(0)
    best = None
    idx = np.arange(len(L))
    for _ in range(400):
        i, j = rng.choice(idx, 2, replace=False)
        vp = np.cross(L[i][0], L[j][0])
        if abs(vp[2]) < 1e-9:
            continue
        vp = vp / vp[2]
        if abs(vp[1]) > 50 * h:      # practically parallel: no measurable perspective
            continue
        # inliers: segments whose line passes within a small angle of the vanishing point
        score, inl = 0.0, 0
        for (ln, dy), (x1, y1, x2, y2, _) in zip(L, keep):
            # distance from vp to the segment's line, normalised by the distance to the segment
            d = abs(ln @ vp) / np.hypot(ln[0], ln[1])
            dist = np.hypot(vp[0] - (x1 + x2) / 2, vp[1] - (y1 + y2) / 2)
            if d / max(1.0, dist) < 0.004:
                score += dy
                inl += 1
        if best is None or score > best[0]:
            best = (score, float(vp[1]), inl)
    if best is None or best[2] < 4:
        return None
    return best[1], best[2], len(keep)


# ---------------------------------------------------------------- the holes themselves, as a chain along a post
def hole_chain(gray, x, q, y0=0, y1=None, half=None):
    """Positions of the mounting holes (or the screws in them) along a post column: a matched filter for a
    small square blob (0.21 unit wide) on the high-passed column profile, peaks chained at the hole spacing
    (a third of a unit, within 15 %, gaps up to one unit bridged). Returns (ys, score) with score = share of
    the expected holes found over the chain's span."""
    from scipy.ndimage import uniform_filter1d
    from scipy.signal import find_peaks
    h, w = gray.shape
    y1 = h if y1 is None else int(y1)
    y0 = int(y0)
    half = max(1, int(round(0.08 * q))) if half is None else half
    x = int(min(w - half - 1, max(half, x)))
    g = cv2.blur(gray.astype(np.float32), (3, 1))
    t = g[y0:y1, x - half:x + half + 1].mean(axis=1)
    if len(t) < 2 * q:
        return [], 0.0
    t = t - uniform_filter1d(t, int(q) | 1, mode="nearest")
    # blob filter: a hole is a run of ~0.21 U rows that differ from their surroundings (either sign)
    k = max(3, int(round(0.21 * q)) | 1)
    box = uniform_filter1d(t, k, mode="nearest")
    ring = uniform_filter1d(t, 3 * k, mode="nearest")
    resp = np.abs(box - ring)
    sd = resp.std() + 1e-6
    resp = resp / sd
    spacing = q / 3.0
    peaks, props = find_peaks(resp, distance=max(2, int(0.6 * spacing)), height=0.8)
    if len(peaks) < 4:
        return [], 0.0
    # dynamic programming: longest chain of peaks with steps of 1, 2 or 3 hole spacings (within 15 %)
    pk = peaks.astype(float)
    n = len(pk)
    best = np.ones(n)
    back = np.full(n, -1, int)
    for i in range(n):
        for j in range(max(0, i - 12), i):
            d = pk[i] - pk[j]
            m = round(d / spacing)
            if 1 <= m <= 3 and abs(d - m * spacing) <= 0.15 * spacing * m:
                v = best[j] + 1 - 0.3 * (m - 1)
                if v > best[i]:
                    best[i] = v
                    back[i] = j
    i = int(np.argmax(best))
    chain = []
    while i >= 0:
        chain.append(pk[i])
        i = back[i]
    chain = sorted(chain)
    if len(chain) < 4:
        return [], 0.0
    expected = (chain[-1] - chain[0]) / spacing + 1
    score = float(len(chain) / max(1.0, expected))
    return [float(c + y0) for c in chain], score


# ---------------------------------------------------------------- unit height by vote
def band_mode_pitch(mask, box):
    """Width-free unit height: the most common gap between strong horizontal lines over the middle 60 % of
    the Rack box, within the range one unit can have for this box. Returns (q, peak share) or None."""
    h, w = mask.shape
    bw, bc = box[2] - box[0], (box[0] + box[2]) / 2
    lo, hi = ul.PITCH_OVER_WIDTH * bw * BOX_WIDTH_RANGE[0], ul.PITCH_OVER_WIDTH * bw * BOX_WIDTH_RANGE[1]
    x0, x1 = int(max(0, bc - 0.3 * bw)), int(min(w, bc + 0.3 * bw))
    cover = mask[:, x0:x1].mean(axis=1)
    is_line = cover >= 0.5
    border = max(2, int(0.01 * h))
    is_line[:border] = False
    is_line[h - border:] = False
    ys = np.flatnonzero(is_line)
    lines = ul._merge_lines(ys.astype(float), cover[ys], 0.25 * lo / (ul.MERGE_FRAC * ul.PITCH_OVER_WIDTH))
    if len(lines) < 3:
        return None
    y = np.array([l[0] for l in lines])
    st = np.array([l[1] for l in lines], float)
    gaps, wts = [], []
    for a in range(len(y)):
        for b in range(a + 1, len(y)):
            g = y[b] - y[a]
            if g > 1.2 * hi:
                break
            if lo <= g <= hi:
                gaps.append(g)
                wts.append(min(st[a], st[b]))
    if not gaps:
        return None
    gaps, wts = np.array(gaps), np.array(wts)
    edges = np.exp(np.arange(np.log(lo), np.log(hi) + 0.02, 0.02))
    hist, _ = np.histogram(gaps, bins=edges, weights=wts)
    sm = np.convolve(hist, [0.25, 0.5, 0.25], mode="same")
    j = int(np.argmax(sm))
    q = float(np.sqrt(edges[j] * edges[min(j + 1, len(edges) - 1)]))
    near = np.abs(gaps / q - 1) <= 0.04
    if near.any():
        q = float(np.average(gaps[near], weights=wts[near]))
    return q, float(sm[j] / max(1e-6, sm.sum()))


VOTE_TOL = 0.05          # unit-height estimates within this of each other agree


def vote_pitch(estimates):
    """estimates: list of (name, q, weight). The cluster (within VOTE_TOL) with the most weight wins; the
    answer is the weighted median of the cluster. Returns (q, members, weight) or None."""
    est = [(n, q, w) for n, q, w in estimates if q and q > 0]
    if not est:
        return None
    best = None
    for n0, q0, w0 in est:
        members = [(n, q, w) for n, q, w in est if abs(q / q0 - 1) <= VOTE_TOL]
        weight = sum(w for _, _, w in members)
        if best is None or weight > best[0] + 1e-9 or (abs(weight - best[0]) <= 1e-9 and len(members) > len(best[1])):
            best = (weight, members)
    weight, members = best
    qs = np.array([q for _, q, _ in members])
    ws = np.array([w for _, _, w in members])
    order = np.argsort(qs)
    cum = np.cumsum(ws[order])
    q = float(qs[order][min(len(qs) - 1, int(np.searchsorted(cum, 0.5 * cum[-1])))])
    return q, [n for n, _, _ in members], weight


# ---------------------------------------------------------------- the whole thing
def _find_impl(bgr, boxes=None, debug=False, chain=False, ends="box", keep=False, straighten_by="mate", use_barriers=False, vote=True, step_tol=None, rigid_when_sure=False, pair_weight=1.3, lam=0.0, flex=False, cab_trim=True, curve=False, r_max=0.25, r_pen=0.0, curve_q_tol=0.04, n_q=9, hires=0, ends_count=False, edge_w=0.0, region="devices", lean="none", q_from="cluster", center=False, rail_inset=0.5, harm_thr=1.02, snap=0.0, refit=False, e_power=1.0, e_inner=1.0, rewidth=False, multi=False, subharm=False, refit_deg=1, vp_min_r=0.08, vp_min_in=6, subharm_gaps=False, hole_phase=False, sure3=False, postedge=False, postedge_thr=0.3, refit_mode="ls", trim_guard=False, postedge_mode="median", postedge_max=2, gaps_fullwidth=False, model_lines=None, model_w=0.0, model_edge=0.0, model_ends=0.0, model_fill=0.0, refit_tol_sure=0.03, vp_gaps=False, box_slack=None, width3=False, rail_vote=0.0, rail_phase_w=0.0, dev_ends=None, dev_lines=None, dev_w=0.0):
    """rows (u01 at the bottom) and info. boxes = (racks, rails) in original pixels, else the model is run.
    chain=True lays the lines with the best-chain (dynamic programming) instead of the even grid.
    ends: 'box' fills to the Rack box capped at the cross rails (as 'frame' does)."""
    name = "posts" + ("_dp" if chain else "")
    small0, f = _memo(("work", id(bgr), bgr.shape), lambda: ul._work_copy(bgr))
    h, w = small0.shape[:2]
    info = {"ok": False, "stage": name, "scale": f}
    racks, rails = boxes if boxes is not None else um.model_frame(bgr)
    racks = [[v / f for v in b] for b in racks]
    rails = [[v / f for v in b] for b in rails]
    if straighten_by == "mate":
        # the teammate's straightening (rotation from the horizontal segments, keystone only when both
        # sides agree): proven on this set; everything below then happens in the straightened frame
        import sys as _s
        import rack_units as R
        small, H0, rot = _memo(("rectify", _digest(small0)), lambda: R.rectify(small0), copy_out=True)
        racks = [_box_through(H0, b) for b in racks]
        rails = [_box_through(H0, b) for b in rails]
        info["rotation_deg"] = round(float(rot), 2)
    else:
        small, H0 = small0, np.eye(3)
    gray = _memo(("gray", _digest(small)), lambda: cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), copy_out=True)
    box = max(racks, key=lambda b: (b[2] - b[0]) * (b[3] - b[1])) if racks else None
    ml0 = None
    if model_lines is not None and len(model_lines) >= 3:
        # the units model's own ladder (RT-EOS rules), as extra evidence: mapped like the boxes, along the box centre
        mcx = (box[0] + box[2]) / 2 if box is not None else w / 2
        ml0 = _map_y(H0, mcx, [y / f for y in model_lines])
    dl0 = None
    if dev_lines is not None and len(dev_lines) >= 3:
        # one unit height read from the device model's boxes (device_centre_pitch), as three lines one unit apart
        dcx = (box[0] + box[2]) / 2 if box is not None else w / 2
        dl0 = _map_y(H0, dcx, [y / f for y in dev_lines])
    mask = _memo(("mask", _digest(gray)), lambda: ul.horizontal_line_mask(gray), copy_out=True)
    rows = ul.candidate_rows(mask)
    period = column_periodicity(gray, box) if box is not None else None
    pick = choose_width(mask, rows, gray, box, period)
    if pick is None:
        info["reason"] = "no rack width"
        return ([], info) if debug else []
    xl, xr = pick["xl"], pick["xr"]
    info.update(x_left=xl * f, x_right=xr * f, width_candidates=pick["candidates"], width_how=pick["how"],
                rack_box=[v * f for v in box] if box else None, post_pair=pick.get("post_pair"))
    # posts and straightening
    if straighten_by == "posts":
        left, right, how = post_lines(gray, xl, xr, rows)
        warped, H, (xl2, xr2) = straighten(small, left, right)
    else:
        left, right, how = (float(xl), 0.0), (float(xr), 0.0), ["teammate rectify"]
        warped, H, (xl2, xr2) = small, np.eye(3), (float(xl), float(xr))
    xl2, xr2 = int(round(xl2)), int(round(xr2))
    info.update(posts={"left": left, "right": right, "how": how},
                lean_px=round(float(abs(left[1] - right[1]) * h) * f, 1))
    gray_w = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    mask_w = _memo(("mask", _digest(gray_w)), lambda: ul.horizontal_line_mask(gray_w), copy_out=True)
    bounds, cab = ul.device_lines_coverage(mask_w, xl2, xr2, with_cabinet=True)
    ml1 = _map_y(H, (xl2 + xr2) / 2, ml0) if ml0 is not None else None
    if keep:
        info["keep"] = dict(small0=small0, H0=H0, straighten_by=straighten_by, small=small, gray=gray, mask=mask, rows=rows, period=period, pick=pick, box=box, rails=rails,
                            left=left, right=right, H=H, warped=warped, mask_w=mask_w, xl2=xl2, xr2=xr2, bounds=bounds, cab=cab)
    if len(bounds) < ul.MIN_LINES:
        info["reason"] = f"only {len(bounds)} device lines after straightening"
        return ([], info) if debug else []
    rw = float(xr2 - xl2)
    q_width, how_q = ul.pitch_from_bands(bounds, rw)
    coef_w, _ = ul.fit_grid(bounds, q_width, curve=False)
    q_width = float(coef_w[1])
    if vote and box is not None:
        # the unit height by vote: width-based estimate, the posts' repeat distance (symmetric pair, and the
        # best single column), and the most common gap between device edges; estimates within 5 % agree
        if width3:
            # the posts can be measured at the ear edges, the hole centres or the clear opening: offer all three
            # standard ratios and let the other cues pick the one that agrees
            est = [(f"width {r_:.4f}", q_width * r_ / ul.PITCH_OVER_WIDTH, 1.0 / 3) for r_ in (ul.U_MM / ul.FACEPLATE_MM, ul.U_MM / 465.1, ul.U_MM / 450.85)]
        else:
            est = [("width", q_width, 1.0)]
        pp = pick.get("post_pair")
        if pp:
            lag_p, corr_p = float(pp[3]), float(pp[0])
            if subharm:
                keep_, mc_ = (0.7, 0.15) if subharm is True else subharm
                lag_p2, corr_p2, fixed_ = subharmonic_fix(gray, box, [pp[1], pp[2]], lag_p, corr_p, keep=keep_, min_corr2=mc_)
                if fixed_ and subharm_gaps:
                    bm_ = band_mode_pitch(mask, box)
                    fixed_ = bool(bm_ and abs(bm_[0] / lag_p2 - 1) <= 0.10)
                if fixed_:
                    lag_p, corr_p = lag_p2, corr_p2
                if fixed_:
                    how_q_extra = how_q_extra if "how_q_extra" in dir() else {}
                    pick["post_pair"] = (corr_p, pp[1], pp[2], lag_p)
                    pp = pick["post_pair"]
            # measured: a symmetric pair repeating with correlation >= 0.3 has the unit height right on 27 of 28 photos
            est.append(("posts pair", lag_p, pair_weight if corr_p >= 0.3 else (0.9 if corr_p >= 0.2 else 0.5)))
        if period is not None:
            xs_, lag_, c_ = period
            j = int(np.argmax(c_))
            if c_[j] > 0.15:
                lag_c, corr_c = float(lag_[j]), float(c_[j])
                if subharm:
                    keep_, mc_ = (0.7, 0.15) if subharm is True else subharm
                    lag_c2, corr_c2, fx_ = subharmonic_fix(gray, box, [xs_[j]], lag_c, corr_c, keep=keep_, min_corr2=mc_)
                    if fx_ and subharm_gaps:
                        bm_ = band_mode_pitch(mask, box)
                        fx_ = bool(bm_ and abs(bm_[0] / lag_c2 - 1) <= 0.10)
                    if fx_:
                        lag_c, corr_c = lag_c2, corr_c2
                est.append(("best column", lag_c, 0.6))
        if rail_vote > 0:
            # teammate v2: unit size read from the rails' EIA hole pattern (both rails must agree within 3 %)
            import rack_units as _R
            rg_ = _R.rail_grid(gray)
            info["rail_grid"] = {"ok": bool(rg_["ok"]), "U": round(float(rg_["U"]) * f, 1)}
            if rg_["ok"]:
                est.append(("rail holes", float(rg_["U"]), float(rail_vote)))
        bm = band_mode_pitch(mask, box)
        if gaps_fullwidth:
            # only edges that reach both posts (within 3 % of the width) count: seams and cables rarely do
            rw_ = float(xr - xl)
            m_ = max(2, int(0.03 * rw_))
            full_ = []
            for y_, st_ in ul.device_lines_coverage(mask, xl, xr):
                yy = int(np.clip(round(y_), 0, mask.shape[0] - 1))
                if mask[max(0, yy - 1):yy + 2, max(0, xl):xl + m_].mean() >= 0.5 and mask[max(0, yy - 1):yy + 2, max(0, xr - m_):xr].mean() >= 0.5:
                    full_.append((y_, st_))
            if len(full_) >= 3:
                q_full, _h = ul.pitch_from_bands(full_, rw_)
                bm = (q_full, 1.0)
        if bm:
            est.append(("device gaps", bm[0], 1.0))
        if ml0 is not None and model_w > 0:
            est.append(("model", float(np.median(np.diff(ml0))), model_w))
        if dl0 is not None and dev_w > 0:
            est.append(("device centres", float(np.median(np.diff(dl0))), dev_w))
        bw_ = box[2] - box[0]
        est = [(n, q, w * (0.3 if q / (ul.PITCH_OVER_WIDTH * bw_) > harm_thr else 1.0)) for n, q, w in est]
        v = vote_pitch(est)
        q0 = v[0]
        sure = bool(pp and pp[0] >= 0.3 and "posts pair" in v[1])
        if sure and q_from == "pair":
            q0 = float(pp[3])
        how_q = {"how": "vote: " + ", ".join(v[1]), "estimates": {n: round(q * f, 1) for n, q, _ in est}, "weight": v[2], "sure": sure}
        coef, inl = ul.fit_grid(bounds, q0, curve=False, q_tol=0.04 if not (sure and q_from == "pair") else 0.015)
    else:
        q0 = q_width
        coef, inl = ul.fit_grid(bounds, q0, curve=False)
    q = coef[1]
    if rewidth and vote and box is not None and pick.get("candidates"):
        # the voted unit height implies a rail width (19-inch rule); when the chosen width disagrees by more than
        # 15 %, switch to the candidate pair that matches it and redo the device edges
        want = q0 / (ul.PITCH_OVER_WIDTH if rewidth == "same" else ul.U_MM / ul.FACEPLATE_MM)
        bc_ = (box[0] + box[2]) / 2
        bw_ = box[2] - box[0]
        cur_w = float(xr2 - xl2)
        if abs(cur_w / want - 1) > 0.15:
            cands = [c for c in pick["candidates"] if abs((c[0] + c[1]) / 2 - bc_) <= 0.08 * bw_]
            if cands:
                best_c = min(cands, key=lambda c: abs((c[1] - c[0]) / want - 1))
                if abs((best_c[1] - best_c[0]) / want - 1) < abs(cur_w / want - 1):
                    xl2, xr2 = int(best_c[0]), int(best_c[1])
                    bounds2, cab2 = ul.device_lines_coverage(mask_w, xl2, xr2, with_cabinet=True)
                    if len(bounds2) >= ul.MIN_LINES:
                        bounds, cab = bounds2, cab2
                        coef, inl = ul.fit_grid(bounds, q0, curve=False, q_tol=0.03)
                        q = coef[1]
                        how_q["rewidth"] = [xl2 * f, xr2 * f]
    grid_tol = curve_q_tol
    if vote and box is not None and how_q.get("sure") and q_from == "pair":
        grid_tol = min(curve_q_tol, 0.025)
    if sure3 and vote and box is not None and how_q.get("estimates"):
        vals_ = np.array(list(how_q["estimates"].values())) / f
        agree_ = int(np.sum(np.abs(vals_ / q0 - 1) <= 0.03))
        if agree_ >= 3:
            grid_tol = min(grid_tol, 0.015)
            how_q["sure3"] = agree_
    if keep:
        info["keep"].update(q0=q0, coef=coef, inl=inl)
    use_chain = chain and not (rigid_when_sure and vote and box is not None and how_q.get("sure"))
    if curve:
        E = um.row_score(warped, mask_w, xl2, xr2)
        first, last = float(bounds[0][0]), float(bounds[-1][0])
        if box is not None:
            bx = _box_through(H, box)
            top_c, bot_c = bx[1], bx[3]
            import unit_grid as _ug
            top_c2, bot_c2 = _ug._cap_at_rails(top_c, bot_c, first, last, [_box_through(H, r) for r in rails])
            if rail_inset > 0:
                if top_c2 > top_c + 1e-6:
                    top_c2 += rail_inset * q
                if bot_c2 < bot_c - 1e-6:
                    bot_c2 -= rail_inset * q
            top_c, bot_c = top_c2, bot_c2
        else:
            top_c, bot_c = first - 0.5 * q, last + 0.5 * q
        if use_barriers:
            above_ = [c_ for c_ in cab if c_ < first - 0.5 * q]
            below_ = [c_ for c_ in cab if c_ > last + 0.5 * q]
            if above_:
                top_c = max(top_c, max(above_))
            if below_:
                bot_c = min(bot_c, min(below_))
        top_c, bot_c = max(0.0, top_c), min(float(h - 1), bot_c)
        scale = 1.0
        if hires and hires > 1:
            # place the grid on a higher-resolution straightened copy: a pixel of unit-height error on a
            # 40-unit rack is a whole unit at the far end
            sc_ = float(hires)
            big = cv2.resize(small0, None, fx=sc_, fy=sc_, interpolation=cv2.INTER_CUBIC)
            S_ = np.diag([sc_, sc_, 1.0])
            Hbig = S_ @ (H @ H0) @ np.linalg.inv(S_)
            warped_big = cv2.warpPerspective(big, Hbig, (int(w * sc_), int(h * sc_)), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            gray_big = cv2.cvtColor(warped_big, cv2.COLOR_BGR2GRAY)
            mask_big = ul.horizontal_line_mask(gray_big)
            E = um.row_score(warped_big, mask_big, int(xl2 * sc_), int(xr2 * sc_))
            scale = sc_
        if e_inner < 1.0:
            # row score over the inner part of the width only (cables and ears crowd the posts)
            cx_ = (xl2 + xr2) / 2
            half_w = (xr2 - xl2) * e_inner / 2
            E = um.row_score(warped, mask_w, int(cx_ - half_w), int(cx_ + half_w))
        if e_power != 1.0:
            E = np.power(np.clip(E, 0, None), e_power)
        E_base = E.copy()

        def place(q_h):
            """Grid for one unit-height hypothesis: edge-augmented score, grid search, snap, refit.
            Returns lines, qm, r_fit, cs, composite consistency score (dict)."""
            E_h = E_base.copy()
            if edge_w > 0:
                # device edges as an extra reward: an impulse per edge, spread over +-0.12 unit, scaled like E
                Eb = np.zeros(len(E_h))
                for y_, st_ in bounds:
                    a_, b_ = int(max(0, (y_ - 0.12 * q_h) * scale)), int(min(len(E_h) - 1, (y_ + 0.12 * q_h) * scale))
                    Eb[a_:b_ + 1] = np.maximum(Eb[a_:b_ + 1], st_)
                if Eb.max() > 0:
                    E_h = E_h + edge_w * Eb / Eb.max()
            if rail_phase_w > 0:
                # teammate v2: where the unit lines fall, read from the rail holes at this unit height
                import rack_units as _R
                ph_ = _R.rail_phase(gray_w, q_h)[0]
                if ph_ is not None:
                    Er = np.zeros(len(E_h))
                    y_ = ph_
                    while y_ < len(E_h) / scale:
                        a_, b_ = int(max(0, (y_ - 0.08 * q_h) * scale)), int(min(len(E_h) - 1, (y_ + 0.08 * q_h) * scale))
                        if b_ >= a_:
                            Er[a_:b_ + 1] = 1.0
                        y_ += q_h
                    E_h = E_h + rail_phase_w * Er * (E_h.max() if E_h.max() > 0 else 1.0)
            if model_edge > 0 and ml1 is not None:
                # the units model's lines as a second reward, same shape as the device-edge impulses
                Em = np.zeros(len(E_h))
                for y_ in ml1:
                    a_, b_ = int(max(0, (y_ - 0.12 * q_h) * scale)), int(min(len(E_h) - 1, (y_ + 0.12 * q_h) * scale))
                    if b_ >= a_:
                        Em[a_:b_ + 1] = 1.0
                E_h = E_h + model_edge * Em
            lines_h, qm_h, r_h, cs_h = curved_grid(E_h, q_h * scale, first * scale, last * scale, top_c * scale, bot_c * scale, q_tol=grid_tol, r_max=r_max, r_pen=r_pen, n_q=n_q, r_fixed=r_vp, region=region, center=center)
            lines_h = [y / scale for y in lines_h]
            qm_h /= scale
            if snap > 0 and len(lines_h) > 2:
                moved = []
                rad = max(1, int(round(snap * qm_h * scale)))
                for y in lines_h:
                    yi = int(round(y * scale))
                    if yi < 0 or yi >= len(E_h):
                        moved.append(y)
                        continue
                    a_, b_ = max(0, yi - rad), min(len(E_h) - 1, yi + rad)
                    if b_ <= a_:
                        moved.append(y)
                        continue
                    j = a_ + int(np.argmax(E_h[a_:b_ + 1]))
                    moved.append(j / scale if E_h[j] > max(0.15, 1.5 * E_h[yi]) else y)
                if refit:
                    ys_ = np.array(moved)
                    k_ = np.arange(len(ys_))
                    w_ = np.array([max(0.05, float(E_h[int(np.clip(round(y * scale), 0, len(E_h) - 1))])) for y in moved])
                    if refit_mode == "phase" or (refit_mode == "phase_sure" and how_q.get("sure")):
                        off_ = ys_ - (lines_h[0] + k_ * qm_h)
                        shift_ = float(np.average(off_, weights=w_))
                        lines_h = [float(y + shift_) for y in lines_h]
                    elif refit_deg == 2 and len(k_) >= 6:
                        # a grid with a linear change of unit height: keeps real tilt when the snapped lines show it
                        A_ = np.vstack([np.ones(len(k_)), k_, k_ ** 2]).T
                        sol, *_ = np.linalg.lstsq(A_ * w_[:, None], ys_ * w_, rcond=None)
                        a2, b2, c2 = (float(v) for v in sol)
                        change = 2 * c2 * len(k_) / max(1e-6, b2)
                        if abs(b2 / qm_h - 1) < 0.05 and abs(change) < 0.45:
                            lines_h = [float(a2 + b2 * k + c2 * k * k) for k in k_]
                            qm_h = float(np.median(np.diff(lines_h)))
                    else:
                        A_ = np.vstack([np.ones(len(k_)), k_]).T
                        sol, *_ = np.linalg.lstsq(A_ * w_[:, None], ys_ * w_, rcond=None)
                        if abs(sol[1] / qm_h - 1) < (refit_tol_sure if how_q.get("sure") else 0.03):
                            lines_h = [float(sol[0] + sol[1] * k) for k in k_]
                            qm_h = float(sol[1])
                else:
                    lines_h = moved
            # consistency of this ladder with the photo (all on the raw row score, comparable across hypotheses)
            E_raw = um.row_score(warped, mask_w, xl2, xr2)
            reg_lo, reg_hi = int(max(0, first - 0.3 * qm_h)), int(min(len(E_raw) - 1, last + 0.3 * qm_h))
            inside_ = [y for y in lines_h if first - 0.3 * qm_h <= y <= last + 0.3 * qm_h]
            base_m = float(E_raw[reg_lo:reg_hi + 1].mean()) if reg_hi > reg_lo else 1e-6
            s1 = float(np.mean([E_raw[int(np.clip(round(y), 0, len(E_raw) - 1))] for y in inside_])) / max(1e-6, base_m) if inside_ else 0.0
            s1n = float(np.clip((s1 - 1.0) / 2.0, 0.0, 1.0))
            # posts: windowed self-correlation at this unit height along the pair columns
            s2 = 0.0
            pp_ = pick.get("post_pair")
            if pp_:
                from scipy.ndimage import uniform_filter1d
                L_ = int(round(qm_h))
                gb = cv2.blur(gray_w.astype(np.float32), (3, 1))
                for x_ in (int(pp_[1]), int(pp_[2])):
                    hb = max(1, int(0.004 * (box[2] - box[0])))
                    x_ = min(gb.shape[1] - hb - 1, max(hb, x_))
                    t_ = gb[int(max(0, top_c)):int(min(gb.shape[0], bot_c)), x_ - hb:x_ + hb + 1].mean(axis=1)
                    t_ = t_ - uniform_filter1d(t_, (2 * L_) | 1, mode="nearest")
                    if len(t_) > 3 * L_ + 4:
                        s2 = max(s2, float(np.percentile(_windowed_corr(t_, L_, 2 * L_), PERIOD_PCT)))
            # device gaps that are whole units at this height; edges that sit on a line
            d_ = np.diff([b_[0] for b_ in bounds])
            n_ = np.round(d_ / qm_h)
            rel_ = np.abs(d_ - n_ * qm_h) / qm_h
            wts_ = np.array([min(bounds[k][1], bounds[k + 1][1]) for k in range(len(bounds) - 1)], float)
            s3 = float(wts_[(n_ >= 1) & (n_ <= 8) & (rel_ <= 0.10)].sum() / max(1e-6, wts_.sum())) if len(d_) else 0.0
            ys_b = np.array([b_[0] for b_ in bounds]); w_b = np.array([b_[1] for b_ in bounds], float)
            la = np.array(lines_h) if lines_h else np.array([0.0])
            on_ = np.abs(ys_b[:, None] - la[None, :]).min(axis=1) <= 0.12 * qm_h
            s4 = float(w_b[on_].sum() / max(1e-6, w_b.sum()))
            comp = s1n + s2 + 0.5 * s3 + s4
            return lines_h, qm_h, r_h, cs_h, {"s1": round(s1, 2), "s2": round(s2, 2), "s3": round(s3, 2), "s4": round(s4, 2), "comp": round(comp, 3)}

        r_vp = None
        if lean == "vp" and box is not None:
            vp = _memo(("vp", _digest(gray_w), tuple(float(v) for v in box)), lambda: vertical_vanishing(gray_w, box))
            if vp is not None:
                y_vp, n_in, n_seg = vp
                # unit height is proportional to (y_vp - y): change over the device span, relative to the middle
                mid_ = (first + last) / 2
                if abs(y_vp - mid_) > 0.5 * (last - first) + 1:
                    r_est = (y_vp - last) / (y_vp - first) - 1
                    if n_in >= vp_min_in and abs(r_est) >= vp_min_r and abs(r_est) <= 0.45:
                        r_vp = float(r_est)
                    elif vp_gaps and n_in >= 3 and abs(r_est) >= vp_min_r and abs(r_est) <= 0.45:
                        # too few straight verticals for the vanishing point alone: accept it when the device gaps
                        # (whole units between device edges) shrink or grow the same way down the rack
                        ys_g, qs_g = [], []
                        for k_ in range(len(bounds) - 1):
                            d_ = bounds[k_ + 1][0] - bounds[k_][0]
                            n_ = round(d_ / q)
                            if 1 <= n_ <= 4 and abs(d_ / n_ / q - 1) <= 0.2:
                                ys_g.append((bounds[k_ + 1][0] + bounds[k_][0]) / 2)
                                qs_g.append(d_ / n_)
                        if len(ys_g) >= 4 and (max(ys_g) - min(ys_g)) >= 0.4 * (last - first):
                            sl_, ic_ = np.polyfit(ys_g, qs_g, 1)
                            qm_ = ic_ + sl_ * (first + last) / 2
                            r_g = float(sl_ * (last - first) / max(1e-6, qm_))
                            how_q["vp_gaps"] = round(r_g, 3)
                            if np.sign(r_g) == np.sign(r_est) and abs(r_g) >= 0.5 * abs(r_est):
                                r_vp = float(r_est)
                how_q["vp"] = {"y_vp": round(y_vp, 1), "inliers": n_in, "segments": n_seg, "r": r_vp}
        hyps = [q]
        if multi and vote and box is not None and how_q.get("estimates"):
            # other unit-height clusters from the vote, farthest apart first
            vals = sorted(set(round(v / f, 1) for v in how_q["estimates"].values()))
            alts = [v for v in vals if abs(v / q0 - 1) > 0.05]
            # merge alternatives within 5 % of each other
            merged = []
            for v in alts:
                if all(abs(v / m - 1) > 0.05 for m in merged):
                    merged.append(v)
            hyps += merged[:2]
        results = [place(q_h) for q_h in hyps]
        best_i = 0
        if len(results) > 1:
            how_q["hypotheses"] = [{"q": round(q_h * f, 1), **r_[4]} for q_h, r_ in zip(hyps, results)]
            if multi == "veto":
                # the vote's winner stands when the posts clearly repeat at its height; otherwise an alternative
                # that fits clearly more device edges and whole-unit gaps takes over
                w0 = results[0][4]
                if w0["s2"] < 0.4:
                    fit0 = w0["s3"] + w0["s4"]
                    cand_i = max(range(1, len(results)), key=lambda k: results[k][4]["s3"] + results[k][4]["s4"])
                    fitc = results[cand_i][4]["s3"] + results[cand_i][4]["s4"]
                    if fitc >= fit0 + 0.25:
                        best_i = cand_i
            else:
                best_i = int(np.argmax([r_[4]["comp"] for r_ in results]))
        lines, qm, r_fit, cs, comp_ = results[best_i]
        how_q["consistency"] = comp_
        pp_ = pick.get("post_pair")
        if hole_phase and pp_ and pp_[0] >= 0.3 and len(lines) > 3:
            from scipy.ndimage import uniform_filter1d
            gb = cv2.blur(gray_w.astype(np.float32), (3, 1))
            hb = max(1, int(0.004 * (box[2] - box[0])))
            L_ = int(round(qm))
            offs = np.array([0.143, 0.5, 0.857])
            resp = {}
            for shift in (0.0, 0.5):
                tot = []
                for x_ in (int(pp_[1]), int(pp_[2])):
                    x_ = min(gb.shape[1] - hb - 1, max(hb, x_))
                    t_ = gb[:, x_ - hb:x_ + hb + 1].mean(axis=1)
                    t_ = t_ - uniform_filter1d(t_, (2 * L_) | 1, mode="nearest")
                    sd = t_.std() + 1e-6
                    vals = []
                    for y in lines[:-1]:
                        for o in offs:
                            yy = int(round(y + (o + shift) * qm))
                            if 0 <= yy < len(t_):
                                vals.append(t_[yy] / sd)
                    if vals:
                        tot.append(abs(float(np.mean(vals))))     # holes are all dark or all bright: a consistent sign
                resp[shift] = max(tot) if tot else 0.0
            how_q["hole_phase"] = {k: round(v, 3) for k, v in resp.items()}
            if resp[0.5] > 2.0 * resp[0.0] and resp[0.5] > 0.25:
                lines = [y + 0.5 * qm for y in lines]
                how_q["hole_phase"]["shifted"] = True
        if ends_count and box is not None and how_q is not None:
            # both cross rails seen: the rack's height between them is a whole number of units; take the count that
            # collects the most row score and divide the height exactly (the user's "top, bottom, divide")
            caps = [_box_through(H, r) for r in rails]
            top_rail = any((r[2] - r[0]) > 2 * (r[3] - r[1]) and (r[1] + r[3]) / 2 <= first for r in caps)
            bot_rail = any((r[2] - r[0]) > 2 * (r[3] - r[1]) and (r[1] + r[3]) / 2 >= last for r in caps)
            if top_rail and bot_rail and bot_c - top_c > 2 * qm:
                n0 = int(round((bot_c - top_c) / qm))
                best_n = None
                for n_ in (n0 - 1, n0, n0 + 1):
                    if n_ < 2:
                        continue
                    cand = np.linspace(top_c, bot_c, n_ + 1)
                    idx = np.clip(np.round(cand * scale).astype(int), 0, len(E) - 1)
                    sc_n = float(E[idx].mean())
                    if best_n is None or sc_n > best_n[0]:
                        best_n = (sc_n, [float(y) for y in cand])
                if best_n:
                    lines = best_n[1]
                    qm = (bot_c - top_c) / (len(lines) - 1)
                    how_q["ends_count"] = len(lines) - 1
        q = float(np.median(np.diff(lines))) if len(lines) > 1 else q
        how_q["curve"] = {"q_mid": qm * f, "change": r_fit, "score": cs["best"], "flat_score": cs["flat"], "n_edges": len(bounds), "span_u": (last - first) / qm}
        use_chain = False
        lines_set = True
    else:
        lines_set = False
    if use_chain:
        E = um.row_score(warped, mask_w, xl2, xr2)
        first, last = int(bounds[0][0]), int(bounds[-1][0])
        lo, hi = max(0, int(first - 0.5 * q)), min(h - 1, int(last + 0.5 * q))
        tol = um.DP_STEP_TOL if step_tol is None else step_tol
        if flex:
            ch = um._dp_flex(E, lo, hi, q, step=0.03, rng=0.20, bonus=0.08)
        else:
            ch = dp_chain_reg(E, lo, hi, q, tol=tol, bonus=0.08, lam=lam) if lam > 0 else um._dp_chain(E, lo, hi, q, tol=tol, bonus=0.08)
        if len(ch) < 3:
            info["reason"] = "chain shorter than two units"
            return ([], info) if debug else []
        lines = [float(y) for y in ch]
        q = float(np.median(np.diff(lines)))
    elif not lines_set:
        lines, _ = ul.lay_grid(bounds, coef, inl)
    # top and bottom: the Rack box (capped at the cross rails), in the straightened frame
    first, last = bounds[0][0], bounds[-1][0]
    if box is not None:
        bx = _box_through(H, box)
        top, bot = bx[1], bx[3]
        import unit_grid as _ug
        top2, bot2 = _ug._cap_at_rails(top, bot, first, last, [_box_through(H, r) for r in rails])
        if rail_inset > 0:
            if top2 > top + 1e-6:
                top2 += rail_inset * q
            if bot2 < bot - 1e-6:
                bot2 -= rail_inset * q
        top, bot = top2, bot2
        src = "Rack box" + (" + cross rails" if rails else "")
    else:
        top, bot, src = first - 0.5 * q, last + 0.5 * q, "device edges (no Rack box)"
    top, bot = max(0.0, top), min(float(h - 1), bot)
    if keep:
        info["keep"].update(lines_devices=list(lines), box_top=top, box_bot=bot)
    barriers = [y for y in cab if y < lines[0] - 0.3 * q or y > lines[-1] + 0.3 * q]
    if use_barriers:
        top = max(top, max([b for b in barriers if b < lines[0]], default=0.0))
        bot = min(bot, min([b for b in barriers if b > lines[-1]], default=float(h - 1)))
    if ends == "rails":
        # walk out only while the strips over the posts keep repeating every unit (holes, cage nuts, screws),
        # never past the Rack box or a cabinet line
        lines, (rt, rb) = ul.extend_over_rails(list(lines), q, gray_w, xl2, xr2, how="repeat", barriers=[top, bot])
        lines = [y for y in lines if top - MAX_FILL_U * q <= y <= bot + MAX_FILL_U * q]
    elif lines_set:
        # the curved grid already runs to the rack's ends with its own local unit height
        lines = [y for y in lines if top - MAX_FILL_U * q <= y <= bot + MAX_FILL_U * q]
    else:
        while lines[0] - q >= top - MAX_FILL_U * q and len(lines) <= ul.MAX_RACK_UNITS:
            lines.insert(0, lines[0] - q)
        while lines[-1] + q <= bot + MAX_FILL_U * q and len(lines) <= ul.MAX_RACK_UNITS:
            lines.append(lines[-1] + q)
        lines = [y for y in lines if top - MAX_FILL_U * q <= y <= bot + MAX_FILL_U * q]
    if ends == "holes" and len(lines) >= 4 and pick.get("post_pair") and box is not None:
        pp_ = pick["post_pair"]
        chains = []
        for x_ in (pp_[1], pp_[2]):
            for dx in (-0.01, 0.0, 0.01):
                ys_, sc_ = hole_chain(gray_w, x_ + dx * (box[2] - box[0]), q, y0=max(0, top - 2 * q), y1=min(h, bot + 2 * q))
                if ys_ and sc_ >= 0.5:
                    chains.append((sc_, ys_))
        info["hole_chains"] = [(round(sc_, 2), len(ys_), round(ys_[0], 1), round(ys_[-1], 1)) for sc_, ys_ in chains]
        if chains:
            # the rack spans from the first hole to the last hole seen on either post (longest chains), plus the
            # 0.143 unit between a boundary and its nearest hole
            first_hole = min(ys_[0] for _, ys_ in chains)
            last_hole = max(ys_[-1] for _, ys_ in chains)
            span_u = (last_hole - first_hole) / q
            n_units = len(lines) - 1
            if span_u >= 0.6 * n_units:
                top_h, bot_h = first_hole - 0.143 * q, last_hole + 0.143 * q
                lines = [y for y in lines if y >= top_h - 0.4 * q and y <= bot_h + 0.4 * q]
                while len(lines) >= 2 and lines[0] + 0.5 * q < top_h - 0.4 * q:
                    lines = lines[1:]
    if postedge and len(lines) >= 6 and box is not None:
        # the posts stop where the rack stops: in roof, plinth and frame-bar cells the posts' vertical edges are
        # gone (measured: edge energy 0.28 of the inner cells there, 0.94 inside). Trim up to two end units whose
        # post edges fall under 0.3 of the inner cells' median.
        pp_ = pick.get("post_pair")
        xs_ = [int(pp_[1]), int(pp_[2])] if pp_ else [int(xl2), int(xr2)]
        gxm = np.abs(cv2.Sobel(cv2.GaussianBlur(gray_w, (3, 3), 0), cv2.CV_32F, 1, 0, ksize=3))
        sw_ = max(2, int(0.012 * (box[2] - box[0])))

        def cell_e(y0_, y1_):
            vals = []
            for x_ in xs_:
                a_, b_ = max(0, x_ - sw_), min(gxm.shape[1], x_ + sw_)
                y0i, y1i = int(max(0, y0_)), int(min(gxm.shape[0], y1_))
                if y1i > y0i and b_ > a_:
                    vals.append(float(gxm[y0i:y1i, a_:b_].mean()))
            return max(vals) if vals else 0.0
        n_ = len(lines) - 1
        inner_ = [cell_e(lines[k], lines[k + 1]) for k in range(2, n_ - 2)]
        ref_ = float(np.median(inner_)) if inner_ else 0.0
        if ref_ > 1e-6:
            dev_ys = [b_[0] for b_ in bounds]
            from scipy.ndimage import uniform_filter1d
            gbl = cv2.blur(gray_w.astype(np.float32), (3, 1))
            hb_ = max(1, int(0.006 * (box[2] - box[0])))
            profs_ = []
            for x_ in xs_:
                xx_ = int(min(gbl.shape[1] - hb_ - 1, max(hb_, x_)))
                t_ = gbl[:, xx_ - hb_:xx_ + hb_ + 1].mean(axis=1)
                profs_.append(t_ - uniform_filter1d(t_, int(2 * q) | 1, mode="nearest"))

            def matches_neighbour(y0_, y1_, direction):
                # correlation of this cell's post strip with the next cell inward (one unit away)
                best_ = -1.0
                for t_ in profs_:
                    a0, a1 = int(max(0, y0_)), int(min(len(t_), y1_))
                    b0, b1 = a0 + direction * int(round(q)), a1 + direction * int(round(q))
                    if a1 - a0 < 4 or b0 < 0 or b1 > len(t_):
                        continue
                    a_, b_ = t_[a0:a1], t_[b0:b1]
                    if len(a_) != len(b_) or a_.std() < 1e-3 or b_.std() < 1e-3:
                        continue
                    best_ = max(best_, float(np.corrcoef(a_, b_)[0, 1]))
                return best_

            def has_device(y0_, y1_, direction=1):
                if not trim_guard:
                    return False
                if any(y0_ + 0.15 * q <= y_ <= y1_ - 0.15 * q for y_ in dev_ys):
                    return True
                if trim_guard == 2 and matches_neighbour(y0_, y1_, direction) >= 0.3:
                    return True
                return False
            def low(k_, direction):
                e_ = cell_e(lines[k_], lines[k_ + 1])
                if postedge_mode == "combo":
                    # beyond the rack when the posts' vertical edges vanish (e < a), or are weak (e < b) while either a
                    # frame line crosses the cell or the posts' pattern does not continue into the cell inward
                    a_, b_, m_ = postedge_thr, postedge_thr + 0.25, 0.0
                    r_ = e_ / ref_
                    if r_ < a_:
                        return True
                    if r_ < b_:
                        y0_, y1_ = lines[k_], lines[k_ + 1]
                        if any(y0_ - 0.12 * q <= c_ <= y1_ + 0.12 * q for c_ in cab) or matches_neighbour(y0_, y1_, direction) < m_:
                            return True
                    return False
                if postedge_mode == "local":
                    nb = cell_e(lines[k_ + direction], lines[k_ + direction + 1]) if 0 <= k_ + direction < n_ else ref_
                    return e_ < postedge_thr * max(nb, 0.5 * ref_)
                return e_ / ref_ < postedge_thr
            # diagnostics: the first and last three cells' post-edge energy (relative), neighbour match, device edge inside
            def cell_feats(y0_, y1_, direction):
                a0, a1 = int(max(0, y0_)), int(min(gray_w.shape[0], y1_))
                face = gray_w[a0:a1, int(xl2):int(xr2)] if a1 > a0 else None
                busy = float(mask_w[a0:a1, int(xl2):int(xr2)].mean()) if a1 > a0 else 0.0
                bstd = float(face.std()) if face is not None and face.size else 0.0
                edges_at = int(any(abs(y_ - y0_) <= 0.12 * q for y_ in dev_ys)) + int(any(abs(y_ - y1_) <= 0.12 * q for y_ in dev_ys))
                cabl = int(any(y0_ - 0.12 * q <= c_ <= y1_ + 0.12 * q for c_ in cab))
                return (round(cell_e(y0_, y1_) / ref_, 2), round(matches_neighbour(y0_, y1_, direction), 2),
                        int(any(y0_ + 0.15 * q <= y_ <= y1_ - 0.15 * q for y_ in dev_ys)), round(busy, 3), round(bstd, 1), edges_at, cabl, round(float(y0_), 1), round(float(y1_), 1))
            info["postedge_cells"] = {"top": [cell_feats(lines[k], lines[k + 1], +1) for k in range(min(3, n_))],
                                      "bottom": [cell_feats(lines[n_ - 1 - k], lines[n_ - k], -1) for k in range(min(3, n_))],
                                      "inner_busy": round(float(np.median([mask_w[int(lines[k]):int(lines[k + 1]), int(xl2):int(xr2)].mean() for k in range(2, n_ - 2)])), 3) if n_ > 4 else None,
                                      "inner_bstd": round(float(np.median([gray_w[int(lines[k]):int(lines[k + 1]), int(xl2):int(xr2)].std() for k in range(2, n_ - 2)])), 1) if n_ > 4 else None}
            kt = 0
            while kt < postedge_max and n_ - kt > 4 and low(kt, +1) and not has_device(lines[kt], lines[kt + 1], +1):
                kt += 1
            kb = 0
            while kb < postedge_max and n_ - kt - kb > 4 and low(n_ - 1 - kb, -1) and not has_device(lines[n_ - 1 - kb], lines[n_ - kb], -1):
                kb += 1
            if kt or kb:
                lines = lines[kt:len(lines) - kb]
                info["postedge_trim"] = [kt, kb]
            if box_slack and not (kt or kb):
                # the Rack box can be too tight: add end cells beyond it while the posts there still repeat the
                # neighbouring cell (strip correlation) and still show their vertical edges, up to box_slack[0] units
                max_u, thr_ = int(box_slack[0]), float(box_slack[1])
                added = [0, 0]
                while added[0] < max_u and lines[0] - q >= 0:
                    y0_, y1_ = lines[0] - q, lines[0]
                    if any(y0_ < c_ < y1_ for c_ in cab) or matches_neighbour(y0_, y1_, +1) < thr_ or cell_e(y0_, y1_) / ref_ < postedge_thr:
                        break
                    lines.insert(0, y0_); added[0] += 1
                while added[1] < max_u and lines[-1] + q <= h - 1:
                    y0_, y1_ = lines[-1], lines[-1] + q
                    if any(y0_ < c_ < y1_ for c_ in cab) or matches_neighbour(y0_, y1_, -1) < thr_ or cell_e(y0_, y1_) / ref_ < postedge_thr:
                        break
                    lines.append(y1_); added[1] += 1
                if any(added):
                    info["box_slack"] = added
    if dev_ends is not None and len(lines) >= 4:
        # empty units are only kept up to dev_ends units beyond the first and last device edge
        f_, l_ = float(bounds[0][0]), float(bounds[-1][0])
        lines = [y for y in lines if f_ - (dev_ends + 0.5) * q <= y <= l_ + (dev_ends + 0.5) * q]
    if cab_trim and len(lines) >= 4:
        if any(abs(c - lines[1]) < 0.25 * q for c in cab):
            lines = lines[1:]
        if any(abs(c - lines[-2]) < 0.25 * q for c in cab):
            lines = lines[:-1]
    if model_ends > 0 and ml1 is not None and len(lines) >= 4:
        # cells entirely above the model's first line (or below its last) by more than model_ends units are dropped
        kt_ = kb_ = 0
        while len(lines) - kt_ - kb_ > 3 and lines[kt_ + 1] < ml1[0] - model_ends * q:
            kt_ += 1
        while len(lines) - kt_ - kb_ > 3 and lines[-2 - kb_] > ml1[-1] + model_ends * q:
            kb_ += 1
        if kt_ or kb_:
            lines = lines[kt_:len(lines) - kb_]
            info["model_ends_trim"] = [kt_, kb_]
    if model_fill > 0 and ml1 is not None and len(lines) >= 3:
        # the logic stopped short: add cells while they stay inside the model's ladder (within model_fill units)
        nf_ = [0, 0]
        while lines[0] - q >= ml1[0] - model_fill * q and lines[0] - q >= 0 and len(lines) <= ul.MAX_RACK_UNITS:
            lines.insert(0, lines[0] - q); nf_[0] += 1
        while lines[-1] + q <= ml1[-1] + model_fill * q and lines[-1] + q <= h - 1 and len(lines) <= ul.MAX_RACK_UNITS:
            lines.append(lines[-1] + q); nf_[1] += 1
        if any(nf_):
            info["model_fill"] = nf_
    if len(lines) < 2:
        info["reason"] = "no units inside the rack box"
        return ([], info) if debug else []
    if keep:
        info["keep"].update(lines_w=list(lines), top=top, bot=bot, barriers=barriers, q=q)
    # back to the photo along the rack's centre column (through both warps)
    Hinv = np.linalg.inv(H @ H0)
    cx = (xl2 + xr2) / 2
    lines_o = _map_y(Hinv, cx, lines)
    xs = cv2.perspectiveTransform(np.float32([[[xl2, (top + bot) / 2], [xr2, (top + bot) / 2]]]), Hinv)[0][:, 0]
    rows_out = um.rows_from_lines(lines_o, float(xs.min()), float(xs.max()), f, bgr.shape)
    info.update(ok=bool(rows_out), pitch=q * f, pitch_how=how_q, rack_from=src + f"; ends by {ends}", unit_lines=[y * f for y in lines_o],
                device_lines=[(y * f, s) for y, s in bounds], x_left=float(xs.min()) * f, x_right=float(xs.max()) * f,
                rail_top=lines_o[0] * f, rail_bottom=lines_o[-1] * f)
    return (rows_out, info) if debug else rows_out


# One rack unit is 44.45 mm on every EIA rack; only the width between the posts changes with the rack size,
# so the unit as a share of that width does too (19-inch: faceplate 482.6 / opening 450.8 mm; 23-inch:
# 584.2 / 552 mm; 10-inch: 254 / 222 mm). Open Compute's 48 mm unit on a 538 mm opening (8.9 %) falls inside
# the 19-inch tolerance. ETSI racks use their own 25 mm unit.
RACK_SIZES = {"19-inch": 0.5 * (44.45 / 482.6 + 44.45 / 450.8), "23-inch": 0.5 * (44.45 / 584.2 + 44.45 / 552.0),
              "10-inch": 0.5 * (44.45 / 254.0 + 44.45 / 222.0),
              "ETSI": 0.5 * (25.0 / 535.0 + 25.0 / 500.0)}   # ETSI EN 300 119: 25 mm unit, about 535 mm wide (500 mm inside)


# The sizes "auto" chooses between. 10-inch and ETSI racks are left out on purpose: a 10-inch unit is two 19-inch
# units as a share of the width (18.8 % against 9.5 %) and an ETSI unit half of one (4.8 %), so a 19-inch rack read
# with a two-unit or half-unit ladder looks just as consistent and the photo alone cannot tell them apart. Those
# racks are set by name (rack_size="10-inch" or "ETSI"). Open Rack (48 mm units, 8.9 %) is covered by auto.
# Measured on the 64 tuning photos, each also stretched sideways to the other standards' proportions
# (2026-10-13): auto 74.4 % on 19-inch (fixed 19-inch 71.6), 71.0 on Open Rack (66.0), 65.2 on 23-inch (46.8);
# set by name, 10-inch 67.6 % and ETSI 62.6 %.
AUTO_SIZES = ("19-inch", "23-inch")


def find_any_rack(bgr, boxes=None, debug=False, sizes=None, **kw):
    """The logic for a rack whose size is not given: run once per size in AUTO_SIZES and keep the run whose unit
    height the three size-free estimates (both rails, best single rail, equipment gaps) back best; ties go to
    19-inch, the common size. info["rack_size"] names the size chosen and info["rack_sizes"] what each size gave."""
    global _MEMO
    best, seen = None, {}
    if boxes is None:          # the units model's Rack and rail boxes do not depend on the rack size: ask it once
        boxes = um.model_frame(bgr)
    _MEMO = {}
    try:
        return _find_any_rack(bgr, boxes, debug, sizes, best, seen, **kw)
    finally:
        _MEMO = None


def _find_any_rack(bgr, boxes, debug, sizes, best, seen, **kw):
    for name in (sizes or AUTO_SIZES):
        rows, info = find(bgr, boxes=copy.deepcopy(boxes), debug=True, pitch_ratio=RACK_SIZES[name], **kw)
        q = info.get("pitch")
        est = (info.get("pitch_how") or {}).get("estimates") or {}
        support = sum(1 for k_, v_ in est.items() if not k_.startswith("width") and v_ and q and abs(v_ / q - 1) <= 0.05)
        key = (support, float((info.get("pitch_how") or {}).get("weight") or 0), name == "19-inch")
        seen[name] = {"units": len(rows), "pitch": q, "support": support}
        if rows and (best is None or key > best[0]):
            best = (key, rows, info, name)
    if best is None:
        return find(bgr, boxes=boxes, debug=debug, **kw)
    _, rows, info, name = best
    info["rack_size"], info["rack_sizes"] = name, seen
    return rows, info


def find(bgr, boxes=None, debug=False, box_range=None, pitch_ratio=None, work_side=None, any_rack=False, rack_size=None, **kw):
    """rows (u01 at the bottom) and info; see _find_impl for every setting.
    box_range: the share of the Rack box's width the rails may span (default BOX_WIDTH_RANGE); wide cabinets
    with side vents need a lower bound near 0.4, else the true unit height falls outside the search.
    rack_size: "auto" (or any_rack=True) chooses between AUTO_SIZES; a name from RACK_SIZES ("19-inch",
    "23-inch", "10-inch", "ETSI") sets the size; None keeps the 19-inch ratio (PITCH_OVER_WIDTH)."""
    if (any_rack or rack_size == "auto") and not pitch_ratio:   # an explicit pitch_ratio wins
        return find_any_rack(bgr, boxes=boxes, debug=debug, box_range=box_range, work_side=work_side, **kw)
    if rack_size and rack_size != "auto" and not pitch_ratio:
        if rack_size not in RACK_SIZES:
            raise ValueError(f"rack_size {rack_size!r}: use 'auto' or one of {sorted(RACK_SIZES)}")
        pitch_ratio = RACK_SIZES[rack_size]
    global BOX_WIDTH_RANGE
    saved, saved_r, saved_w = BOX_WIDTH_RANGE, ul.PITCH_OVER_WIDTH, ul.WORK_LONG_SIDE
    if work_side:
        ul.WORK_LONG_SIDE = int(work_side)            # size of the working copy (long side, px)
    if box_range:
        BOX_WIDTH_RANGE = (float(box_range[0]), float(box_range[1]))
    if pitch_ratio:
        ul.PITCH_OVER_WIDTH = float(pitch_ratio)      # unit height as a share of the width between the posts
    try:
        return _find_impl(bgr, boxes=boxes, debug=debug, **kw)
    finally:
        BOX_WIDTH_RANGE, ul.PITCH_OVER_WIDTH, ul.WORK_LONG_SIDE = saved, saved_r, saved_w


# the settings of the best approach so far (each measured on the 44 test photos, 2026-10-12)
GRID_VOTE_KW = dict(curve=True, r_max=0.0, curve_q_tol=0.06, n_q=41, edge_w=0.8, e_power=2.0, q_from="pair",
                    rail_inset=0.5, snap=0.15, refit=True, rewidth=True, multi="veto", lean="vp", postedge=True, postedge_thr=0.4, postedge_mode="combo",
                    rack_size="auto")   # 19- or 23-inch chosen from the photo; set 10-inch or ETSI racks by name (AUTO_SIZES)


def method_grid_vote(bgr, p=None, boxes=None):
    """Unit height by vote, then one rigid even grid placed where the rows' edge, brightness and colour steps
    (squared) plus the device edges add up most; lines snapped to nearby peaks and refitted; a second
    unit-height hypothesis takes over only when the posts do not back the first and it fits clearly more edges.
    boxes: the units model's (Rack boxes, rail boxes) when the caller already has them."""
    rows, info = find(bgr, boxes=boxes, debug=True, **GRID_VOTE_KW)
    info["stage"] = "grid_vote"
    return rows, info



def ends_from_features(model_lines, logic_lines):
    """RT-EOS units' unit lines (its unit height), cut to the rack's ends found by image features.

    Each end is whichever of the two ladders' ends lies further inside: the logic's end comes from the Rack box,
    the cross rails, cabinet lines and where the posts' vertical edges stop; the units model's own ladder almost
    never stops short (0 of 64 tuning photos) but often runs past the rack. Where the model ladder ends short of
    the chosen end it is extended with its own local unit height. Chosen on the 64 tuning photos (88.1 % against
    85.9 % for RT-EOS units alone); on the 50 test photos 87.0 % against 86.2 %."""
    M = np.array(sorted(model_lines), float)
    if len(M) < 2:
        return list(logic_lines)
    if len(logic_lines) < 2:
        return list(M)
    top, bot = max(min(logic_lines), M[0]), min(max(logic_lines), M[-1])
    q = float(np.median(np.diff(M)))
    keep = list(M[(M >= top - 0.5 * q) & (M <= bot + 0.5 * q)])
    if len(keep) < 2:
        return list(M)
    qt, qb = keep[1] - keep[0], keep[-1] - keep[-2]
    while keep[0] - top > 0.5 * qt:
        keep.insert(0, keep[0] - qt)
    while bot - keep[-1] > 0.5 * qb:
        keep.append(keep[-1] + qb)
    return keep


def method_feature_ends(bgr, p=None):
    """Unit height and lines from RT-EOS units (units model + its ladder rules), ends from image features (see ends_from_features)."""
    import unit_grid as ug
    import pathlib as _pl
    weights = _pl.Path(__file__).resolve().parents[1] / "weights/rack_unit_finder.pt"
    mrows = ug.build_unit_grid_from_model(bgr, str(weights)) or []
    lrows, linfo = method_grid_vote(bgr)
    ml = model_ladder_lines(mrows)
    ll = sorted({r["box"][1] for r in lrows} | {r["box"][3] for r in lrows})
    lines = ends_from_features(ml, ll)
    src = mrows or lrows
    xl, xr = (src[0]["box"][0], src[0]["box"][2]) if src else (0, bgr.shape[1] - 1)
    rows = [{"box": [int(xl), int(lines[k]), int(xr), int(lines[k + 1])], "label": f"u{len(lines) - 1 - k:02d}"} for k in range(len(lines) - 1)]
    info = {"stage": "feature_ends", "ok": bool(rows), "x_left": xl, "x_right": xr, "reason": "" if rows else "no ladder",
            "pitch": float(np.median(np.diff(lines))) if len(lines) > 1 else None}
    return rows, info


def device_centre_pitch(devices, xl, xr, conf=0.3):
    """One unit height (original pixels) from the device model's boxes, or None.

    The boxes are loose (most are 1.06-1.38 units tall), so their heights are a poor ruler; the distance between
    the centres of two stacked devices is not, since the padding cancels: it is a whole or half-whole number of
    units (1U on 1U = 1, 1U on 2U = 1.5). The largest height that fits nine in ten of those distances wins, then a
    least-squares refit. devices: [{"cls", "conf", "box"}] as cached by scripts/units/cache_model_boxes.py --devices."""
    B = [d["box"] for d in devices if d["conf"] >= conf and d["cls"] != "Empty"
         and min(d["box"][2], xr) - max(d["box"][0], xl) > 0.5 * (d["box"][2] - d["box"][0])]
    if len(B) < 3:
        return None
    B = sorted(B, key=lambda b: (b[1] + b[3]) / 2)
    c = np.array([(b[1] + b[3]) / 2 for b in B])
    hgt = np.array([b[3] - b[1] for b in B])
    d = np.diff(c)
    d = d[d > 0.3 * np.median(hgt)]          # stacked neighbours, not boxes side by side
    if len(d) < 2:
        return None
    qs = np.exp(np.linspace(np.log(np.median(hgt) * 0.3), np.log(np.median(hgt) * 1.2), 600))

    def fit(q):
        r = d / q
        n = np.round(r * 2) / 2
        return float(((n >= 1) & (np.abs(r - n) <= 0.12)).mean())
    F = np.array([fit(q) for q in qs])
    q = float(qs[F >= 0.9 * F.max()].max())
    n = np.maximum(1, np.round(d / q * 2) / 2)
    m = np.abs(d / q - n) <= 0.12
    return float((d[m] * n[m]).sum() / (n[m] ** 2).sum()) if m.any() else q


# RT-EOS units + logic: the units model's ladder, with its end units checked against the model's own confidence,
# the logic's ends and the Rack box. Chosen on the 64 tuning photos (2026-10-13): 91.2 % against 85.9 % for
# RT-EOS units alone; graded once on the 50 test photos: 89.5 % against 86.4 %. The model's ladder almost never
# stops short of the rack, so only end units are removed, never added.
END_CONF_LOW, END_CONF_WEAK, END_LOOK = 0.3, 0.5, 6


def trim_end_units(model_lines, unit_boxes, logic_lines, rack_box, keep_inside=None, far=None, far_conf=1.01):
    """model_lines: the RT-EOS ladder (y, top to bottom); unit_boxes: the model's raw unit boxes
    [{"box": [x0, y0, x1, y1], "confidence"}]; logic_lines: the logic's ladder; rack_box: [x0, y0, x1, y1] or None.
    An end unit is removed when the model's best unit box over it has confidence below END_CONF_LOW, or below
    END_CONF_WEAK while the unit's centre lies beyond the logic's end or outside the Rack box. Removal goes from the
    outermost unit inwards and stops at the first unit that stays (at most END_LOOK per end).
    keep_inside (units): never remove a unit lying this far inside the logic's end (the logic sees the rack go on).
    far (units): also remove a unit the model was confident about (below far_conf) when it lies this far beyond the
    logic's end and outside the Rack box."""
    M = sorted(model_lines)
    if len(M) < 3:
        return M
    L = sorted(logic_lines or [])

    def conf(y0, y1):
        return max([u["confidence"] for u in unit_boxes
                    if max(0.0, min(u["box"][3], y1) - max(u["box"][1], y0)) / max(1e-6, y1 - y0) >= 0.5] or [0.0])

    q = float(np.median(np.diff(M)))

    def drop(k, top):
        y0, y1 = M[k], M[k + 1]
        c = (y0 + y1) / 2
        inside_by = ((c - L[0]) if top else (L[-1] - c)) / q if L else -99.0   # units inside the logic's end (negative = beyond)
        beyond_logic = bool(L) and inside_by < 0
        outside_box = rack_box is not None and (c < rack_box[1] if top else c > rack_box[3])
        u = conf(y0, y1)
        if keep_inside is not None and L and inside_by >= keep_inside:
            return False
        if far is not None and L and -inside_by >= far and outside_box and u < far_conf:
            return True
        return u < END_CONF_LOW or (u < END_CONF_WEAK and (beyond_logic or outside_box))

    a, b = 0, len(M) - 1
    for _ in range(END_LOOK):
        if b - a > 1 and drop(a, True):
            a += 1
        else:
            break
    for _ in range(END_LOOK):
        if b - a > 1 and drop(b - 1, False):
            b -= 1
        else:
            break
    return M[a:b + 1]


GAP_UNIT = 0.1   # a gap between two neighbouring model boxes this tall (in units) or more is a unit of its own; chosen
                 # on the tuning photos (0.1 to 0.8 all score within 0.15 points there)
SPLIT_ROW = 1.8  # a model box at least this many unit heights tall holds several units (patch panels under cables are
                 # boxed as one): it is cut into round(height / unit) equal units. 2026-10-14: no such box on the tuning
                 # photos; +0.14 points on the test set (11 boxes)


def model_ladder_lines(mrows):
    """The RT-EOS ladder's unit boundaries, top to bottom: the top of the first box, then the bottom of every box, plus
    the top of a box that starts a clear gap (GAP_UNIT or more) below its neighbour, and the inner lines of a box tall
    enough to hold several units (SPLIT_ROW). Taking every box's top and bottom, as before 2026-10-13, left a sliver
    unit (0.01-0.1 U tall) wherever two neighbours did not touch exactly."""
    if not mrows:
        return []
    q = float(np.median([r["box"][3] - r["box"][1] for r in mrows]))
    L = [mrows[0]["box"][1]]
    for k, r in enumerate(mrows):
        y0, y1 = r["box"][1], r["box"][3]
        if k and y0 - mrows[k - 1]["box"][3] >= GAP_UNIT * q:
            L.append(y0)
        n = int(round((y1 - y0) / q)) if y1 - y0 >= SPLIT_ROW * q else 1
        L += [y0 + (y1 - y0) * j / n for j in range(1, n)]
        L.append(y1)
    return sorted(L)


def method_rteos_logic(bgr, p=None):
    """RT-EOS units (units model + its ladder rules) with the end units checked by trim_end_units."""
    import unit_grid as ug
    import pathlib as _pl
    model = ug.load_model(str(_pl.Path(__file__).resolve().parents[1] / "weights/rack_unit_finder.pt"))
    small, f = ug.shrink_for_inference(bgr)
    res = model.predict(small, conf=0.25, imgsz=768, verbose=False)
    units, racks, rails = ug._unit_rows_from_result(res[0], model.names)
    for u in units:
        u["box"] = [v * f for v in u["box"]]
    racks = [[v * f for v in r] for r in racks]
    rails = [[v * f for v in r] for r in rails]
    mrows = ug.ladder_from_unit_boxes([dict(u) for u in units], racks, bgr.shape[0], bgr.shape[1], rails) if units else []
    lrows, _ = method_grid_vote(bgr, boxes=copy.deepcopy((racks, rails)))   # the same Rack and rail boxes the logic would ask the model for
    ml = model_ladder_lines(mrows)
    ll = sorted({r["box"][1] for r in lrows} | {r["box"][3] for r in lrows})
    rk = max(racks, key=lambda b: (b[2] - b[0]) * (b[3] - b[1])) if racks else None
    lines = trim_end_units(ml, units, ll, rk) if ml else ll
    src = mrows or lrows
    xl, xr = (src[0]["box"][0], src[0]["box"][2]) if src else (0, bgr.shape[1] - 1)
    rows = [{"box": [int(xl), int(lines[k]), int(xr), int(lines[k + 1])], "label": f"u{len(lines) - 1 - k:02d}"} for k in range(len(lines) - 1)]
    info = {"stage": "rteos_logic", "ok": bool(rows), "x_left": xl, "x_right": xr, "reason": "" if rows else "no ladder",
            "pitch": float(np.median(np.diff(lines))) if len(lines) > 1 else None,
            "model_units": [{"box": [float(v) for v in u["box"]], "confidence": float(u.get("confidence", 0.0))} for u in units]}
    return rows, info
