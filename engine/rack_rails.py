"""The rack's two rails, from the stitched picture alone (user, 2026-10-30: "make both rails straight and start after the
rail"). Rails carry mounting holes, three per unit. Where the holes can be read they give the rails' lines, where each
rail ends. Where they cannot (dark on a black cabinet) the picture is left as it is."""
import cv2
import numpy as np

MIN_RUN = 4            # regularly spaced holes in a row before they count as a rail (marks on a floor are not regular)
MIN_RUN_HOLES = 18     # regular holes one rail must show to be trusted


def hole_centres(gray, pitch):
    """Small compact dark spots."""
    k = max(5, int(round(pitch * 0.35))) | 1
    bh = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    _, m = cv2.threshold(bh, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    n, _, st, c = cv2.connectedComponentsWithStats(m, 8)
    lo, hi = (pitch * 0.05) ** 2, (pitch * 0.32) ** 2; out = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if lo <= a <= hi and 0.45 <= w / max(h, 1) <= 2.2 and a / (w * h) > 0.45:
            out.append((c[i][0], c[i][1]))
    return np.array(out) if out else np.zeros((0, 2))


def _column(pts, x_guess, pitch, W, y_mid):
    """The near-vertical column of holes that covers the most unit heights near x_guess, refined to a curve."""
    band = 0.16 * W; cand = pts[np.abs(pts[:, 0] - x_guess) < band]
    if len(cand) < MIN_RUN_HOLES:
        return None
    best = None
    for x_c in np.arange(x_guess - band, x_guess + band, max(3.0, pitch * 0.1)):
        for slope in np.linspace(-0.12, 0.12, 13):
            inl = np.abs(cand[:, 0] - (x_c + slope * (cand[:, 1] - y_mid))) < pitch * 0.22
            cover = np.unique((cand[inl, 1] / pitch).astype(int)).size
            if best is None or cover > best[0]:
                best = (cover, x_c, slope)
    _, x_c, slope = best
    p = np.array([slope, x_c - slope * y_mid]); inl = None
    for it in range(6):
        inl = np.abs(cand[:, 0] - np.polyval(p, cand[:, 1])) < pitch * (0.32 if it < 2 else 0.2)
        if inl.sum() < 5:
            return None
        p = np.polyfit(cand[inl, 1], cand[inl, 0], min(3, 1 + it))
    return cand[inl]


def _regular(ys, pitch):
    """The holes in regular runs: neighbours about a third of a unit apart. Returns (ys kept, the gaps)."""
    ys = np.sort(ys); keep, gaps, run = [], [], [ys[0]] if len(ys) else []
    for a, b in zip(ys, ys[1:]):
        if 0.2 * pitch <= b - a <= 0.5 * pitch:
            run.append(b)
        else:
            if len(run) >= MIN_RUN:
                keep += run; gaps += list(np.diff(run))
            run = [b]
    if len(run) >= MIN_RUN:
        keep += run; gaps += list(np.diff(run))
    return np.array(keep), np.array(gaps)


def find_rails(bgr, pitch, boxes):
    """{"left": poly x(y), "right": poly x(y), "from": "holes" | "boxes", "top", "bottom", "unit_px"} or None. top, bottom and
    unit_px are set only when both rails show enough regular holes. boxes = detector boxes [x1, y1, x2, y2, ...]."""
    H, W = bgr.shape[:2]; boxes = [b for b in boxes if b[3] - b[1] < 8 * pitch]
    if len(boxes) < 2:
        return None
    gx = (float(np.median([b[0] for b in boxes])), float(np.median([b[2] for b in boxes])))
    gray = cv2.createCLAHE(2.0, (8, 8)).apply(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)); pts = hole_centres(gray, pitch)
    y_mid = H / 2; sides = []
    for g in gx:
        col = _column(pts, g, pitch, W, y_mid) if len(pts) else None
        if col is None:
            sides = None; break
        ys, gaps = _regular(col[:, 1], pitch)
        if len(ys) < MIN_RUN_HOLES:
            sides = None; break
        sel = np.isin(col[:, 1], ys); sides.append((col[sel], ys, gaps))
    if sides:
        fit = [np.polyfit(c[:, 1], c[:, 0], 3 if len(c) > 30 and np.ptp(c[:, 1]) > 0.6 * H else 2) for c, _, _ in sides]
        gaps = np.concatenate([s[2] for s in sides]); unit = 3.0 * float(np.median(gaps))
        return {"left": fit[0], "right": fit[1], "from": "holes", "top": float(max(s[1].min() for s in sides)), "bottom": float(min(s[1].max() for s in sides)), "unit_px": unit, "holes": [len(s[1]) for s in sides]}
    return None      # no readable holes: the picture stays as it is (device edges bent a black cabinet into an hourglass)


def sane(fit, H, W):
    """A rail pair that can be real: a good width from top to bottom, not collapsing or fanning out, rails not far from straight."""
    ys = np.linspace(0, H - 1, 40); xl, xr = np.polyval(fit[0], ys), np.polyval(fit[1], ys); w = xr - xl
    return bool(w.min() > 0.25 * W and w.max() < 1.5 * w.min() and np.ptp(xl) < 0.3 * W and np.ptp(xr) < 0.3 * W)


def from_frames(views, pitch, boxes, size, max_frames=70):
    """The rails from the holes in the video frames, carried onto the rack picture: frames are sharper than the stitch, and
    a rail hidden in one frame shows in another. views = [(frame BGR, H frame -> rack)]. A hole counts when at least two frames saw it.
    Same result as find_rails, or None when the frames do not show both rails."""
    H, W = size; boxes = [b for b in boxes if b[3] - b[1] < 8 * pitch]
    if len(boxes) < 2 or not views:
        return None
    gx = (float(np.median([b[0] for b in boxes])), float(np.median([b[2] for b in boxes]))); step = max(1, len(views) // max_frames); pts = []
    for fi, (fr, M) in enumerate(views[::step]):
        s = abs(np.linalg.det(np.asarray(M, np.float64)[:2, :2])) ** 0.5
        g = cv2.createCLAHE(2.0, (8, 8)).apply(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)); h = hole_centres(g, pitch / max(s, 1e-3))
        if len(h):
            q = cv2.perspectiveTransform(h[None].astype(np.float32), np.asarray(M, np.float64))[0]; pts.append(np.c_[q, np.full(len(q), fi)])
    if not pts:
        return None
    A = np.vstack(pts); A = A[(A[:, 1] >= 0) & (A[:, 1] < H) & (A[:, 0] >= 0) & (A[:, 0] < W)]; sides = []
    for g in gx:
        c = A[np.abs(A[:, 0] - g) < 0.16 * W]
        if len(c) < MIN_RUN_HOLES:
            return None
        best = None
        for x_c in np.arange(g - 0.16 * W, g + 0.16 * W, max(3.0, pitch * 0.1)):              # the column that covers the most units, seen by the most frames
            for slope in np.linspace(-0.12, 0.12, 13):
                inl = np.abs(c[:, 0] - (x_c + slope * (c[:, 1] - H / 2))) < pitch * 0.2
                sc = np.unique((c[inl, 1] / pitch).astype(int)).size
                if best is None or sc > best[0]:
                    best = (sc, x_c, slope)
        _, x_c, slope = best; pl = np.array([slope, x_c - slope * H / 2]); inl = None
        for it in range(6):
            inl = np.abs(c[:, 0] - np.polyval(pl, c[:, 1])) < pitch * (0.3 if it < 2 else 0.18)
            if inl.sum() < 8:
                return None
            pl = np.polyfit(c[inl, 1], c[inl, 0], min(3, 1 + it))
        kept, gaps_ = [], []                                                                    # spacing is exact inside one frame, not across frames
        col = c[inl]
        for fi in np.unique(col[:, 2]):
            q = col[col[:, 2] == fi]; ys, g2 = _regular(q[:, 1], pitch)
            if len(ys):
                kept.append(q[np.isin(q[:, 1], ys)]); gaps_.append(g2)
        if not kept:
            return None
        q = np.vstack(kept)
        if len(q) < MIN_RUN_HOLES:
            return None
        sides.append((q, np.concatenate(gaps_)))
    fit = [np.polyfit(s[0][:, 1], s[0][:, 0], 2 if np.ptp(s[0][:, 1]) > 0.5 * H else 1) for s in sides]
    if not sane(fit, H, W):
        return None
    ends = [rail_end(A, f, pitch, H) for f in fit]
    return {"left": fit[0], "right": fit[1], "from": "frames", "bottom": min(ends) if all(e is not None for e in ends) else None, "bottoms": ends, "holes": [len(s[0]) for s in sides]}


def rail_end(A, fit, pitch, H):
    """Where a rail ends at the bottom: walking up from the bottom of the picture, the first three unit heights in a row
    where holes on the rail's line were seen by at least two frames each. Cables can hide a rail higher up, so only the
    lowest stretch matters. None when no such stretch exists. A = [x, y, frame]."""
    near = A[np.abs(A[:, 0] - np.polyval(fit, A[:, 1])) < 0.25 * pitch]
    if not len(near):
        return None
    good = lambda k: len({int(f) for f in near[(near[:, 1] >= k * pitch) & (near[:, 1] < (k + 1) * pitch), 2]}) >= 2
    for k in range(int(H / pitch), 1, -1):
        if good(k) and good(k - 1) and good(k - 2):
            q = near[(near[:, 1] >= k * pitch) & (near[:, 1] < (k + 1) * pitch)]
            return float(np.percentile(q[:, 1], 90))
    return None


def straighten(bgr, rails):
    """Both rails straight and parallel: each row of the picture is resampled sideways so the left rail lands on one x and
    the right rail on another. Same picture size. Returns (picture, how far a rail moved at most in px)."""
    H, W = bgr.shape[:2]; ys = np.arange(H, dtype=np.float64)
    yv = np.clip(ys, 0, H - 1); xl = np.polyval(rails["left"], yv); xr = np.polyval(rails["right"], yv)
    ok = xr - xl > 0.2 * W
    if not ok.all() and ok.sum() < 0.5 * H:
        return bgr, 0.0
    XL, XR = float(np.median(xl[ok])), float(np.median(xr[ok])); gx = np.arange(W, dtype=np.float64)
    k = (xr - xl) / (XR - XL)
    mx = xl[:, None] + (gx[None, :] - XL) * k[:, None]; my = np.repeat(ys[:, None], W, axis=1)
    out = cv2.remap(bgr, mx.astype(np.float32), my.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    return out, float(max(np.abs(xl - XL).max(), np.abs(xr - XR).max()))


def base_bar_top(bgr, rows, pitch, rails_x=None):
    """The rack's base frame under the rails, on the stitched picture: the first solid bar near the bottom of the ladder,
    as bright as the rails beside it and running across most of the width. Returns its top y, or None."""
    rows = sorted(rows, key=lambda r: r["box"][1]); x0_, x1_ = (int(rails_x[0]), int(rails_x[1])) if rails_x else (rows[-1]["box"][0], rows[-1]["box"][2]); w = x1_ - x0_
    x0, x1 = x0_ + int(0.12 * w), x1_ - int(0.12 * w); y1 = rows[-1]["box"][3]; Y0 = max(0, int(y1 - 3 * pitch)); Y1 = min(bgr.shape[0], int(y1 + 2 * pitch))
    g = cv2.GaussianBlur(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32), (0, 0), 2); band = g[Y0:Y1, x0:x1]
    if not band.size:
        return None
    med = np.median(band, axis=1); uni = (np.abs(band - med[:, None]) < 14).mean(axis=1)
    side = np.concatenate([g[Y0:Y1, max(0, x0_ - int(0.04 * w)):x0_ + int(0.04 * w)].ravel(), g[Y0:Y1, x1_ - int(0.04 * w):x1_ + int(0.04 * w)].ravel()]); rail = float(np.median(side))
    i = 0
    while i < len(uni):
        if uni[i] > 0.6:
            j = i
            while j + 1 < len(uni) and uni[j + 1] > 0.6:
                j += 1
            if j - i > 0.3 * pitch and abs(float(np.median(med[i:j + 1])) - rail) < 45:
                return float(Y0 + i)
            i = j + 1
        else:
            i += 1
    return None


def on_base_in_frames(row, views, rails_x=None, min_frames=2):
    """The second check, in the frames: at least two frames show this unit's band as one solid bar."""
    x0, y0, x1, y1 = row["box"]
    if rails_x:
        x0, x1 = rails_x
    w = x1 - x0; ok = 0
    for fr, H in views:
        Hi = np.linalg.inv(np.asarray(H, np.float64)); q = cv2.perspectiveTransform(np.float32([[[x0 + 0.2 * w, y0], [x1 - 0.2 * w, y1]]]), Hi)[0]
        a, b = max(0, int(min(q[:, 0]))), min(fr.shape[1], int(max(q[:, 0]))); c, d = max(0, int(min(q[:, 1]))), min(fr.shape[0], int(max(q[:, 1])))
        if d - c < 6 or b - a < 40:
            continue
        g = cv2.GaussianBlur(cv2.cvtColor(fr[c:d, a:b], cv2.COLOR_BGR2GRAY).astype(np.float32), (0, 0), 1.5); med = np.median(g, axis=1, keepdims=True)
        if float((np.abs(g - med) < 18).mean()) > 0.55:
            ok += 1
            if ok >= min_frames:
                return True
    return False


def off_rail_rows(bgr, rows, pitch, views, rails_x=None, limit=2, owned=None):
    """How many units at the bottom are on the base frame and not on the rail: most of the unit is on the bar in the
    picture, and the frames agree. Never more than `limit`. A unit a device sits on (owned(row) true: the device
    detector's box covers it) is never the frame: a plain patch panel at the bottom can look like one solid bar (vdo1's
    patch panel at U1-U2 was cut to U1 and every unit above renumbered one lower, 3 Nov 2026)."""
    top = base_bar_top(bgr, rows, pitch, rails_x)
    if top is None:
        return 0, None
    n = 0
    for r in reversed(sorted(rows, key=lambda x: x["box"][1])):
        if (r["box"][1] + r["box"][3]) / 2 > top and n < limit:
            if owned is not None and owned(r):
                break
            seen = [views[i] for i in (r.get("frames_seen") or []) if i in views]
            if not on_base_in_frames(r, seen, rails_x):
                break
            n += 1
        else:
            break
    return n, top
