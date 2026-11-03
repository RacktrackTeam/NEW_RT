"""
rectify.py - turn an angled product shot of a switch into a straight front view.

Shops often show older hardware revisions only in a three-quarter view (front panel at
an angle, lid on top). The template builder needs a straight-on front panel, so these
images used to be skipped ("0 RJ45 ports detected"). The jacks still sit on a flat
panel in two straight rows, so the panel can be straightened: find the jack openings,
fit the two rows, and map the four corner jacks onto a rectangle with the usual jack
spacing (row spacing 0.95 of the column pitch, the median over 9 straight templates).
What is left of the perspective is absorbed by the homography fit to the site photo.

    from rectify import rectify_front
    straight = rectify_front(img)      # BGR image, or None if no two port rows are found
"""
import cv2
import numpy as np

ROW_TO_COL = 0.95         # row spacing / column pitch of RJ45 jacks (median of 9 templates)
OUT_PITCH = 60            # column pitch of the straightened image, px


def _openings(gray):
    """Centres and sizes of dark, roughly rectangular openings (jacks), any size."""
    out = []
    k = max(15, (gray.shape[0] // 8) | 1)
    local = cv2.blur(gray.astype(np.float32), (k, k))
    for c in (25, 40, 60):
        m = ((local - gray) > c).astype(np.uint8) * 255
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        n, _, st, cen = cv2.connectedComponentsWithStats(m)
        for i in range(1, n):
            x, y, w, h, a = st[i]
            if a < 30 or not 0.6 <= w / max(h, 1) <= 2.6 or a / (w * h) < 0.45:
                continue
            out.append((float(cen[i][0]), float(cen[i][1]), float(w), float(h)))
    # one entry per opening (the strengths overlap)
    keep = []
    for o in sorted(out, key=lambda o: -o[2] * o[3]):
        if all(np.hypot(o[0] - k[0], o[1] - k[1]) > 0.5 * min(o[2], k[2]) for k in keep):
            keep.append(o)
    return keep


def _best_row(pts, size, used):
    """The straight line through the most openings of one size (RANSAC over pairs)."""
    best = None
    idx = [i for i in range(len(pts)) if i not in used]
    for a in idx:
        for b in idx:
            if b <= a:
                continue
            p, q = pts[a], pts[b]
            d = np.hypot(*(q - p))
            if d < 1e-6 or abs(q[1] - p[1]) > 0.6 * abs(q[0] - p[0]):     # rows run left-right
                continue
            n = np.array([-(q[1] - p[1]), q[0] - p[0]]) / d
            dist = np.abs((pts[idx] - p) @ n)
            inl = [i for i, dd in zip(idx, dist) if dd < 0.3 * size[i]]
            if best is None or len(inl) > len(best):
                best = inl
    return best or []


def rectify_front(img):
    """Straight front view of the panel in an angled shot, or None when two parallel
    port rows of 4+ jacks each are not found."""
    h0, w0 = img.shape[:2]
    s = 900 / w0 if w0 < 900 else 1.0                 # small shop images: work larger
    big = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC) if s != 1 else img
    gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
    ops = _openings(gray)
    if len(ops) < 8:
        return None
    # the jacks are the most common opening size
    hs = np.array([o[3] for o in ops])
    med = np.median(hs)
    ops = [o for o in ops if 0.6 * med <= o[3] <= 1.6 * med]
    pts = np.array([(o[0], o[1]) for o in ops], np.float32)
    size = np.array([o[3] for o in ops])
    r1 = _best_row(pts, size, set())
    r2 = _best_row(pts, size, set(r1))
    if len(r1) < 4 or len(r2) < 4:
        return None
    rows = sorted([r1, r2], key=lambda r: pts[r][:, 1].mean())
    top, bot = (pts[r][np.argsort(pts[r][:, 0])] for r in rows)
    gap = np.median(np.abs(bot[:, 1].mean() - top[:, 1].mean()))
    if gap > 3 * med or gap < 0.8 * med:              # not one stacked pair of jack rows
        return None
    n = max(len(top), len(bot))
    src = np.float32([top[0], top[-1], bot[-1], bot[0]])
    W, R = (n - 1) * OUT_PITCH, ROW_TO_COL * OUT_PITCH
    dst = np.float32([[0, 0], [W, 0], [W, R], [0, R]])
    H = cv2.getPerspectiveTransform(src, dst)
    # canvas: the whole image mapped through H, limited to a 1U-like strip around the rows
    corners = cv2.perspectiveTransform(np.float32([[[0, 0]], [[big.shape[1], 0]], [[big.shape[1], big.shape[0]]],
                                                   [[0, big.shape[0]]]]), H).reshape(-1, 2)
    x0, x1 = max(corners[:, 0].min(), -0.9 * W), min(corners[:, 0].max(), 1.9 * W)
    y0, y1 = -1.6 * R, 2.6 * R
    T = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], np.float64)
    out = cv2.warpPerspective(big, T @ H, (int(x1 - x0), int(y1 - y0)), flags=cv2.INTER_CUBIC,
                              borderValue=(255, 255, 255))
    return out
