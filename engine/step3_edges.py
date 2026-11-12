"""Step 3, Prasanna's methods (their approaches.py from rack_report_code 2, copied unchanged below this note).

Two classical computer-vision ways to decide which neighbouring taken units are one device, from the photo alone:
  * Prasanna's method 1, edge lines  (their "Approach 1", function approach8): find horizontal device edges anywhere in the
    edge picture, then snap each one to the nearest unit line; an edge on a unit line splits the units there.
  * Prasanna's method 2, brightness  (their "Approach 2", function approach1): read the grayscale photo top to bottom; a
    brightness step in plain areas, or a strong outline in cluttered areas, splits the units.
Input: the unit rows (top to bottom) and taken/not per unit. device_grouping.group_units() calls these.
"""
"""The two approaches. Input to each: the photo, the unit rows (from the units model) and
occupied/empty per unit (from the classifier). Output: which neighbouring occupied units are one device.

Nothing is typed in: the rack's left/right limits, unit height and every window size
are computed from the unit rows of THIS photo. The device finder / device names are never used here.

  Approach 1  top-to-bottom grayscale scan; where it is cluttered keep only strong outlines
  Approach 2  edge sketch + a rectangle fitted to each group's outer outline
"""
import cv2
import numpy as np

COL = [(255, 120, 0), (0, 200, 255), (0, 220, 90), (200, 0, 220), (0, 120, 255), (255, 220, 0), (120, 255, 0), (255, 0, 120)]


# ------------------------------------------------------------------ geometry from the unit rows
class Geo:
    def __init__(self, units, occupied, shape):
        b = np.array([u["box"] for u in units], float)
        self.n = len(units)
        self.x0, self.x1 = int(np.median(b[:, 0])), int(np.median(b[:, 2]))
        self.W = self.x1 - self.x0
        self.h = float(np.median(b[:, 3] - b[:, 1]))                   # typical unit height in pixels
        ys = [b[0, 1]] + [(b[k - 1, 3] + b[k, 1]) / 2 for k in range(1, self.n)] + [b[-1, 3]]
        self.ys = [int(round(v)) for v in ys]                           # n+1 unit lines, top to bottom
        self.occ = list(occupied)
        self.labels = [u["unit"] for u in units]
        self.H, self.Wimg = shape[:2]

    def group(self, split):
        """split[k] (k = 1..n-1) True = a device edge between unit k-1 and unit k. Returns groups of unit indices."""
        groups, cur = [], None
        for k in range(self.n):
            if not self.occ[k]:
                cur = None
                continue
            if cur is not None and not split.get(k, False):
                cur.append(k)
            else:
                cur = [k]
                groups.append(cur)
        return groups

    def name(self, g):
        a, b = self.labels[g[0]], self.labels[g[-1]]
        return a if a == b else f"{a}-{b}"


def _smooth(a, k):
    k = max(3, int(k)) | 1
    return np.convolve(a, np.ones(k) / k, mode="same")


def _gray(bgr):
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)


def edge_sketch(bgr, lo=25, hi=70):
    g = cv2.createCLAHE(2.0, (8, 8)).apply(_gray(bgr))
    ed = cv2.Canny(cv2.GaussianBlur(g, (3, 3), 0), lo, hi)
    sk = np.zeros((*ed.shape, 3), np.uint8)
    sk[ed > 0] = (255, 255, 0)
    return ed, sk


# ------------------------------------------------------------------ approach 1
def approach1(bgr, geo, plain_thr=0.45, clutter_thr=0.10, strong_thr=0.70):
    gray = _gray(bgr)
    x0, x1 = geo.x0, geo.x1
    rowm = _smooth(gray[:, x0:x1].mean(axis=1), 5)
    step = np.abs(np.gradient(rowm))
    step = step / (np.percentile(step, 99) + 1e-6)
    weak = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 30, 80)[:, x0:x1] > 0
    clutter = _smooth(weak.mean(axis=1), 0.9 * geo.h)                    # window about one unit tall
    strong = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 90, 200)
    kw = max(15, int(0.08 * geo.W))
    strong = cv2.morphologyEx(strong, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (kw, 1)))
    strong = cv2.dilate(strong, cv2.getStructuringElement(cv2.MORPH_RECT, (max(9, kw // 2), 3)))
    scover = (strong[:, x0:x1] > 0).mean(axis=1)
    tol = max(3, int(0.12 * geo.h))
    ev, split = [], {}
    for k in range(1, geo.n):
        y = geo.ys[k]
        s = float(step[y - tol:y + tol + 1].max()); sc = float(scover[y - tol:y + tol + 1].max()); cl = float(clutter[y])
        by = None
        if cl < clutter_thr and s >= plain_thr: by = "step"
        elif sc >= strong_thr: by = "outline"
        split[k] = by is not None
        ev.append(dict(k=k, y=y, step=s, clutter=cl, outline=sc, by=by))
    return dict(split=split, ev=ev, step=step, clutter=clutter, strong=strong, groups=geo.group(split))


def vis_approach1(bgr, geo, r):
    gray3 = cv2.cvtColor(_gray(bgr), cv2.COLOR_GRAY2BGR)
    H, W = gray3.shape[:2]
    pw = max(110, W // 5)
    pan = np.full((H, pw, 3), 25, np.uint8)
    for y in range(1, H):
        cv2.line(pan, (int(min(r["step"][y - 1], 1) * (pw - 4)), y - 1), (int(min(r["step"][y], 1) * (pw - 4)), y), (255, 255, 0), 1)
        cv2.line(pan, (int(min(r["clutter"][y - 1] * 5, 1) * (pw - 4)), y - 1), (int(min(r["clutter"][y] * 5, 1) * (pw - 4)), y), (0, 120, 255), 1)
    scan = np.hstack([gray3, pan])
    for e in r["ev"]:
        col = (0, 255, 0) if e["by"] == "step" else (0, 0, 255) if e["by"] == "outline" else (0, 200, 200)
        cv2.line(scan, (geo.x0, e["y"]), (W + pw, e["y"]), col, 1)
    strong = np.zeros_like(gray3)
    strong[r["strong"] > 0] = (0, 255, 255)
    return gray3, scan, strong


# ------------------------------------------------------------------ approach 2
def _row_score(bgr, geo):
    """Edge sketch plus, for every pixel row, how strongly the picture changes there (edges, brightness, colour),
    and real(y): does the picture really change across row y?"""
    ed, sketch = edge_sketch(bgr)
    gray = _gray(bgr)
    x0, x1 = geo.x0, geo.x1
    kw = max(12, int(0.06 * geo.W))
    hor = cv2.morphologyEx(ed, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (kw, 1)))
    hor = cv2.dilate(hor, cv2.getStructuringElement(cv2.MORPH_RECT, (max(9, int(0.04 * geo.W)), 3)))
    a = (hor[:, x0:x1] > 0).mean(axis=1)
    rowm = _smooth(gray[:, x0:x1].mean(axis=1), 7)
    b = np.abs(np.gradient(rowm)); b = b / (b.max() + 1e-6)
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)[:, x0:x1, 1:].mean(axis=1).astype(np.float32)
    c = np.linalg.norm(np.gradient(cv2.GaussianBlur(lab, (1, 7), 0), axis=0), axis=1); c = c / (c.max() + 1e-6)
    score = 0.5 * a + 0.3 * b + 0.2 * c
    g = gray.astype(float)
    o1, o2 = int(0.30 * geo.h), int(0.07 * geo.h)

    def real(y):
        up, dn = g[max(y - o1, 0):max(y - o2, 1), x0:x1], g[y + o2:y + o1, x0:x1]
        mid = g[y - 2:y + 3, x0:x1]
        if up.size == 0 or dn.size == 0:
            return False
        return abs(up.mean() - dn.mean()) >= 6 or abs((up.mean() + dn.mean()) / 2 - mid.mean()) >= 15
    return ed, sketch, score, real


def draw_outlines(bgr, ed, geo, groups):
    """Thicken each group's edges into one solid shape, keep only its thin outer outline (white), fit a rectangle."""
    outline = bgr.copy()
    m = int(0.04 * geo.W)
    for i, grp in enumerate(groups):
        y1, y2 = geo.ys[grp[0]] + 2, geo.ys[grp[-1] + 1] - 2
        band = np.zeros_like(ed)
        band[y1:y2, max(geo.x0 - m, 0):geo.x1 + m] = ed[y1:y2, max(geo.x0 - m, 0):geo.x1 + m]
        blob = cv2.dilate(band, cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, int(0.018 * geo.W)), max(3, int(0.1 * geo.h)))))
        blob = cv2.morphologyEx(blob, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (max(9, int(0.12 * geo.W)), max(5, int(0.3 * geo.h)))))
        cnts, _ = cv2.findContours(blob, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        col = COL[i % len(COL)]
        top, hgt = geo.ys[grp[0]], geo.ys[grp[-1] + 1] - geo.ys[grp[0]]
        if cnts:
            hull = cv2.convexHull(np.vstack(cnts))
            cv2.drawContours(outline, [hull], -1, (255, 255, 255), 1)
            x, _y, w, _h = cv2.boundingRect(hull)
            x = max(x, geo.x0 - m); w = min(x + w, geo.x1 + m) - x
        else:
            x, w = geo.x0, geo.W
        cv2.rectangle(outline, (x, top + 1), (x + w, top + hgt - 1), col, 2)
        cv2.putText(outline, geo.name(grp), (x + 4, top + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1, cv2.LINE_AA)
    return outline


def approach8(bgr, geo, cut_thr=0.22, match_frac=0.25):
    """(Earlier called approach 8.) Find the device boundaries FREELY in the edge picture, anywhere along the
    rack, without looking at the unit lines. Then match each one to the nearest unit line by position.
    A boundary that lands on a unit line splits the units there. Rectangles are drawn only on occupied units."""
    ed, sketch, score, real = _row_score(bgr, geo)
    gap = max(6, int(0.45 * geo.h))
    lo, hi = geo.ys[0] - int(0.2 * geo.h), geo.ys[-1] + int(0.2 * geo.h)
    found = []
    for y in np.argsort(-score):
        y = int(y)
        if score[y] < cut_thr:
            break
        if lo <= y <= hi and all(abs(y - f) >= gap for f in found) and real(y):
            found.append(y)
    found.sort()
    tol = match_frac * geo.h
    ev, split = [], {}
    for y in found:
        k = int(np.argmin([abs(y - v) for v in geo.ys]))              # nearest unit line (0 = top of the rack)
        off = y - geo.ys[k]
        where = "top of the rack" if k == 0 else "bottom of the rack" if k == geo.n else f"{geo.labels[k - 1]} | {geo.labels[k]}"
        ok = abs(off) <= tol
        ev.append(dict(y=y, k=k, where=where, line_y=geo.ys[k], offset=float(off), matched=bool(ok), score=float(score[y])))
        if ok and 0 < k < geo.n:
            split[k] = True
    groups = geo.group(split)
    layout = sketch.copy()
    for k in range(geo.n + 1):                                          # unit lines: thin white
        cv2.line(layout, (geo.x0, geo.ys[k]), (geo.x1, geo.ys[k]), (255, 255, 255), 1)
    for e in ev:                                                        # edge boundaries: green = on a unit line, red = between lines
        col = (0, 255, 0) if e["matched"] else (0, 0, 255)
        cv2.line(layout, (geo.x0 - 6, e["y"]), (geo.x1 + 6, e["y"]), col, 2)
        cv2.line(layout, (geo.x0 - 6, e["y"]), (geo.x0 - 6, e["line_y"]), (0, 165, 255), 2)   # orange = offset to the unit line
    return dict(split=split, ev=ev, groups=groups, layout=layout, outline=draw_outlines(bgr, ed, geo, groups))


# ------------------------------------------------------------------ reference (not an input)


def agreement(pred, ref):
    p = {tuple(x) for x in pred}; r = {tuple(x) for x in ref}
    return len(p & r), len(r)


