"""
fit.py - fit a switch template to a photo and draw the overlay.

From the switch-mapper project (src/map_switch.py), the fitting part only:
  Template           a vendor front panel + its port boxes (front.png + template.json)
  align()            homography template -> photo: rough guesses (multi-scale NCC and
                     SIFT/MAGSAC++), patch refinement with RANSAC, plausibility check,
                     ECC polish, port check against the detected ports, fallbacks that
                     align by the ports themselves
  passes()           is an alignment good enough to use
  port_agreement()   how well the template's ports land on the detected ports
  snap_to_ports()    re-fit through the detected ports
  snap_polygons()    each port box pulled onto the detected ports around it
  add_photo_ports()  ports the photo shows but the template lacks (console, uplinks)
  draw_overlay()     the photo with each port's box (template result)
  draw_auto_overlay() the photo with the detected ports (no template)
"""
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

CLAHE = cv2.createCLAHE(3.0, (8, 8))


MIN_INLIERS = 45          # alignment is not trusted below these (tested: true matches


MIN_CONFIDENCE = 0.5      # score 68+ inliers / 0.70+, non-matches 26-29 / 0.25-0.33)


PORTS_AGREE = 0.8         # share of ports that must line up with the detected ones (port_agreement)


COARSE_MAX_W = 1200       # photo is downscaled to this width for the coarse search


class Template:
    def __init__(self, folder: Path):
        self.folder = folder
        self.key = folder.name
        self.meta = json.loads((folder / "template.json").read_text())
        self.bgr = cv2.imread(str(folder / self.meta["image"]))
        if self.bgr is None:
            raise FileNotFoundError(folder / self.meta["image"])
        self.gray = cv2.cvtColor(self.bgr, cv2.COLOR_BGR2GRAY)
        h, w = self.gray.shape
        # mask of pixels used for alignment: match_regions minus ignore_regions
        self.mask = np.zeros((h, w), np.uint8)
        # regions are clipped to the image: a negative x or y (padding past the edge) would make
        # the slice wrap or come out empty and leave the RJ45 block in the match
        clip = lambda x, y, bw, bh: (max(0, x), max(0, y), min(w, x + bw), min(h, y + bh))
        for x0, y0, x1, y1 in (clip(*r) for r in self.meta.get("match_regions", [[0, 0, w, h]])):
            self.mask[y0:y1, x0:x1] = 255
        for x0, y0, x1, y1 in (clip(*r) for r in self.meta.get("ignore_regions", [])):
            self.mask[y0:y1, x0:x1] = 0
        self.edges = self._edges()

    def _edges(self):
        g = cv2.GaussianBlur(self.gray, (3, 3), 0)
        e = cv2.Canny(g, 40, 110)
        x, y, w, h = self.meta.get("match_regions", [[0, 0, *self.gray.shape[::-1]]])[0]
        keep = np.zeros_like(e)
        keep[max(0, y - 6):y + h + 6, max(0, x - 6):x + w + 6] = 255
        return cv2.bitwise_and(e, keep)

    @property
    def label(self):
        return f'{self.meta["vendor"]} {self.meta["model"]}'


def _prep(gray, blur=1.2):
    return cv2.GaussianBlur(CLAHE.apply(gray), (0, 0), blur)


def coarse_candidates(tpl: Template, photo_gray, top_per_scale=2, fracs=None):
    """Masked multi-scale NCC search. NCC alone is not reliable here (small scales can
    score high on random texture), so this only proposes candidates: the best few
    positions at each scale, each as a 3x3 template->photo homography. `fracs`: the
    template widths to try, as a share of the photo width (default 0.20-1.15)."""
    H0, W0 = photo_gray.shape
    f = min(1.0, COARSE_MAX_W / W0)
    small = cv2.resize(photo_gray, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    pad = int(0.04 * small.shape[1])
    P = cv2.copyMakeBorder(_prep(small, 1.5), pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
    th, tw = tpl.gray.shape
    back = np.diag([1 / f, 1 / f, 1.0])
    cands = []
    for frac in (np.arange(0.2, 1.16, 0.03) if fracs is None else fracs):   # template width / photo width
        per_scale = []
        for ar in (0.9, 1.0, 1.1):                       # vertical squash/stretch
            sx = frac * small.shape[1] / tw
            sy = sx * ar
            w, h = int(tw * sx), int(th * sy)
            if h < 12 or h > P.shape[0] or w > P.shape[1]:
                continue
            T = _prep(cv2.resize(tpl.gray, (w, h), interpolation=cv2.INTER_AREA), 1.5)
            M = cv2.resize(tpl.mask, (w, h), interpolation=cv2.INTER_NEAREST)
            r = cv2.matchTemplate(P, T, cv2.TM_CCOEFF_NORMED, mask=M)
            r[~np.isfinite(r)] = -1
            for _ in range(top_per_scale):
                _, mv, _, ml = cv2.minMaxLoc(r)
                if mv <= 0:
                    break
                Hs = np.array([[sx, 0, ml[0] - pad], [0, sy, ml[1] - pad], [0, 0, 1]], float)
                per_scale.append((mv, back @ Hs))
                cv2.rectangle(r, (ml[0] - w // 4, ml[1] - h // 2), (ml[0] + w // 4, ml[1] + h // 2), -1, -1)
        per_scale.sort(key=lambda c: -c[0])
        cands += per_scale[:top_per_scale]
    return cands


_SIFT = None


def feature_candidates(tpl: Template, photo_gray, min_inliers=12):
    """Placements proposed by keypoint matching (SIFT on the template's match region vs
    the photo, ratio test, MAGSAC++ homography). Unlike the NCC search this is invariant
    to rotation and perspective, so a photo taken from the side, tilted, or with the
    switch upside down still gets a starting point. Returns [(inliers, H)] (0-2 items)."""
    global _SIFT
    if _SIFT is None:
        _SIFT = cv2.SIFT_create(nfeatures=4000, contrastThreshold=0.03)
    f = min(1.0, 1600 / photo_gray.shape[1])
    small = cv2.resize(photo_gray, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else photo_gray
    kt, dt = _SIFT.detectAndCompute(CLAHE.apply(tpl.gray), tpl.mask)
    kp, dp = _SIFT.detectAndCompute(CLAHE.apply(small), None)
    if dt is None or dp is None or len(kt) < 20 or len(kp) < 20:
        return []
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    src, dst = [], []
    for m in matcher.knnMatch(dt, dp, k=2):
        if len(m) == 2 and m[0].distance < 0.8 * m[1].distance:
            src.append(kt[m[0].queryIdx].pt); dst.append(kp[m[0].trainIdx].pt)
    out = []
    src, dst = np.float32(src), np.float32(dst)
    back = np.diag([1 / f, 1 / f, 1.0])
    for _ in range(2):                                   # a second, disjoint hypothesis
        if len(src) < min_inliers:
            break
        try:
            H, inl = cv2.findHomography(src, dst, cv2.USAC_MAGSAC, 4.0, maxIters=5000, confidence=0.999)
        except cv2.error:
            H, inl = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
        if H is None:
            break
        inl = inl.ravel().astype(bool)
        if inl.sum() >= min_inliers and plausible(tpl, back @ H, photo_gray.shape)[0]:
            out.append((float(inl.sum()), back @ H))
        src, dst = src[~inl], dst[~inl]
    return out


def refine_align(tpl: Template, photo_gray, H, rounds=(40, 25, 15, 10), debug=None):
    """Local patch search around the current estimate, then RANSAC homography.
    Patches come only from the visible-in-practice parts of the panel (LEDs, logo,
    SFP cages, chassis edges), so cables over the RJ45 block don't throw it off."""
    Pp = _prep(photo_gray)
    Tp = _prep(tpl.gray)
    th, tw = tpl.gray.shape
    pw = max(16, int(tw / 34))           # patch size in template pixels
    step = max(8, int(tw / 74))
    h, w = photo_gray.shape
    inliers, scores = 0, []
    for R_rel in rounds:
        Tw = cv2.warpPerspective(Tp, H, (w, h))
        Mw = cv2.warpPerspective(tpl.mask, H, (w, h), flags=cv2.INTER_NEAREST)
        scale = np.sqrt(abs(np.linalg.det(H[:2, :2])))
        half = max(6, int(pw * scale / 2))
        R = max(4, int(R_rel * scale * tw / 890 / 2.5))   # radius scales with apparent size
        src, dst, sc = [], [], []
        for cy in range(pw // 2, th - pw // 2, step):
            for cx in range(pw // 2, tw - pw // 2, step):
                if tpl.mask[cy - pw // 2:cy + pw // 2, cx - pw // 2:cx + pw // 2].min() == 0:
                    continue
                p = cv2.perspectiveTransform(np.float32([[[cx, cy]]]), H)[0, 0]
                px, py = int(round(p[0])), int(round(p[1]))
                if px - half - R < 0 or py - half - R < 0 or px + half + R >= w or py + half + R >= h:
                    continue
                patch = Tw[py - half:py + half, px - half:px + half]
                if Mw[py - half:py + half, px - half:px + half].min() == 0 or patch.std() < 12:
                    continue
                win = Pp[py - half - R:py + half + R, px - half - R:px + half + R]
                r = cv2.matchTemplate(win, patch, cv2.TM_CCOEFF_NORMED)
                _, mv, _, ml = cv2.minMaxLoc(r)
                if mv < 0.45:
                    continue
                r2 = r.copy()
                cv2.circle(r2, ml, 6, -1, -1)
                if mv - r2.max() < 0.04:          # ambiguous peak (repetitive texture)
                    continue
                src.append((cx, cy))
                dst.append((p[0] + ml[0] - R, p[1] + ml[1] - R))
                sc.append(mv)
        if len(src) < 8:
            break
        Hn, inl = cv2.findHomography(np.float32(src), np.float32(dst), cv2.RANSAC, 4.0)
        if Hn is None:
            break
        if _lean(tpl, Hn) > 0.3:
            # the matched patches sit in a thin strip (LED row, port rows), which leaves a
            # perspective fit free to lean the panel over; a similarity through the same
            # points keeps what the strip does pin down (place, scale, turn)
            A, inl_s = cv2.estimateAffinePartial2D(np.float32(src), np.float32(dst), method=cv2.RANSAC,
                                                   ransacReprojThreshold=4.0)
            if A is not None and inl_s is not None:
                Hn, inl = np.vstack([A, [0, 0, 1]]), inl_s
        H = Hn
        inl = inl.ravel().astype(bool)
        inliers = int(inl.sum())
        scores = list(np.array(sc)[inl])
        if debug is not None:                 # matched points of the last good round
            debug.update(src=src, dst=dst, inlier=inl.tolist())
    return H, inliers, float(np.mean(scores)) if scores else 0.0


def _masked_ncc(tpl, photo_prep, H):
    """Zero-mean normalised correlation between the template warped by H and the photo,
    over the template's match region: how well the whole panel lines up, not just patches."""
    h, w = photo_prep.shape
    Tw = cv2.warpPerspective(_prep(tpl.gray), H, (w, h))
    Mw = cv2.warpPerspective(tpl.mask, H, (w, h), flags=cv2.INTER_NEAREST) > 0
    if Mw.sum() < 500:
        return -1.0
    a, b = Tw[Mw].astype(np.float64), photo_prep[Mw].astype(np.float64)
    a -= a.mean(); b -= b.mean()
    den = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / den) if den > 0 else -1.0


def refine_dense(tpl, photo_gray, H, photo_ports=None, photo_shape=None, width=800, iters=40):
    """Photometric (ECC) refinement of the whole panel at once, started from H. Patch
    matching ends at integer-ish patch positions; ECC minimises the intensity difference
    between the warped template and the photo over every pixel of the match region, which
    settles the homography to sub-pixel accuracy. Kept only when it improves things: a
    higher panel correlation, still plausible, and (with port detections) ports landing
    at least as well. Returns (H, accepted)."""
    f = min(1.0, width / photo_gray.shape[1])
    small = cv2.resize(photo_gray, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else photo_gray
    S = np.diag([f, f, 1.0])
    Hs = S @ H
    Hs = Hs / Hs[2, 2]
    Pp = _prep(small).astype(np.float32)
    Tp = _prep(tpl.gray).astype(np.float32)
    try:
        # ECC's mask applies to the input image, so the template plays that role: the
        # warp it estimates maps photo -> template (H⁻¹), masked by the match region
        warp = np.linalg.inv(Hs).astype(np.float32)
        warp /= warp[2, 2]
        crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, iters, 1e-5)
        _, warp2 = cv2.findTransformECC(Pp, Tp, warp, cv2.MOTION_HOMOGRAPHY, crit, tpl.mask, 5)
        H2 = np.linalg.inv(S) @ np.linalg.inv(warp2.astype(np.float64))
        H2 /= H2[2, 2]
    except (cv2.error, np.linalg.LinAlgError):
        return H, False
    if not np.all(np.isfinite(H2)) or not plausible(tpl, H2, photo_shape or photo_gray.shape)[0]:
        return H, False
    before, after = _masked_ncc(tpl, Pp, Hs), _masked_ncc(tpl, Pp, S @ H2)
    if after < before:
        return H, False
    if photo_ports:
        a0, a1 = port_agreement(tpl, H, photo_ports, photo_shape), port_agreement(tpl, H2, photo_ports, photo_shape)
        if a0 and a1 and (a1["pairs"] < a0["pairs"] or (a1.get("err") or 0) > (a0.get("err") or 0) + 0.02):
            return H, False
    return H2, True


def _lean(tpl, H):
    """How far the warped panel's sides are from square to its top (0 = square, as the
    cosine of the corner angle). A camera angle makes a trapezoid; a parallelogram leaning
    sideways means the fit's vertical direction was never pinned down."""
    x, y, w, h = tpl.meta.get("match_regions", [[0, 0, tpl.gray.shape[1], tpl.gray.shape[0]]])[0]
    q = cv2.perspectiveTransform(np.float32([[[x, y]], [[x + w, y]], [[x + w, y + h]], [[x, y + h]]]), H).reshape(4, 2)
    worst = 0.0
    for a, b in ((q[1] - q[0], q[3] - q[0]), (q[2] - q[3], q[2] - q[1])):
        worst = max(worst, abs(float(a @ b) / max(np.linalg.norm(a) * np.linalg.norm(b), 1e-6)))
    return worst


def plausible(tpl, H, photo_shape):
    """Is this a believable placement of the switch in the photo? Patch matching can lock
    onto a small detail (e.g. printed text) and squeeze the whole template into it with
    many 'inliers'. The template's front face must stay convex, keep roughly its shape,
    and span a good part of the photo (photos are cropped to the front panel)."""
    x, y, w, h = tpl.meta.get("match_regions", [[0, 0, tpl.gray.shape[1], tpl.gray.shape[0]]])[0]
    q = cv2.perspectiveTransform(np.float32([[[x, y]], [[x + w, y]], [[x + w, y + h]], [[x, y + h]]]), H).reshape(4, 2)
    if not cv2.isContourConvex(q.astype(np.float32)):
        return False, "outline is folded"
    top, bot = np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[2] - q[3])
    left, right = np.linalg.norm(q[3] - q[0]), np.linalg.norm(q[2] - q[1])
    if min(top, bot) / max(top, bot, 1e-6) < 0.6 or min(left, right) / max(left, right, 1e-6) < 0.6:
        return False, "outline is skewed"
    aspect = ((top + bot) / 2) / max((left + right) / 2, 1e-6)
    if not 0.6 < aspect / (w / h) < 1.6:
        return False, "outline is stretched"
    if _lean(tpl, H) > 0.35:                                     # more than ~20° from square
        return False, "outline leans over"
    if (top + bot) / 2 < 0.3 * photo_shape[1]:
        return False, f"outline covers only {(top + bot) / 2 / photo_shape[1]:.0%} of the photo width"
    return True, ""


def _grid_order(centres, w):
    """Indices of points ordered column by column (left to right), top to bottom inside a
    column, plus the number of columns."""
    order = sorted(range(len(centres)), key=lambda i: centres[i][0])
    cols = []
    for i in order:
        if cols and centres[i][0] - np.mean([centres[j][0] for j in cols[-1]]) < 0.5 * w:
            cols[-1].append(i)
        else:
            cols.append([i])
    return [i for c in cols for i in sorted(c, key=lambda j: centres[j][1])], len(cols)


def refine_with_ports(tpl, H, photo_ports, rounds=3):
    """Re-fit the alignment through the ports themselves. Template matching only uses the
    parts cables don't cover (logo, LEDs, SFP, edges), so across the RJ45 block it can
    drift by a third of a port. Each template port is paired with a detected port: in
    column order when the trained models found the same layout (same number of ports and
    columns per kind), otherwise with the nearest unclaimed detection of its kind. The
    homography is fitted to the pairs, the pairs are redone with that better fit, and so on
    for a few rounds, so a port first paired with its neighbour ends up on its own.
    Returns (H, number of pairs used) or (H, 0) unchanged if the ports can't be matched."""
    seen_n = sum(p.get("status") != "not seen" for p in photo_ports if p["type"] in ("rj45", "sfp"))
    if seen_n < 6:
        return H, 0
    pw = float(np.median([p["box"][2] for p in photo_ports]))
    src, dst = _ordered_pairs(tpl, H, photo_ports)
    if len(src) < 8:
        src, dst = _nearest_pairs(tpl, H, photo_ports, 0.5 * pw)
    best = (H, 0)
    for _ in range(rounds):
        if len(src) < max(8, 0.5 * seen_n):
            break
        H2, inl = _fit_ports(np.float32(src), np.float32(dst), 0.3 * pw, tpl)
        if H2 is None or inl.sum() < 0.75 * len(src):
            break
        best = (H2, int(inl.sum()))
        src2, dst2 = _nearest_pairs(tpl, H2, photo_ports, 0.5 * pw)
        if len(src2) <= len(src) and src2 == src:
            break
        src, dst = src2, dst2
    return best


def _fit_ports(src, dst, thresh, tpl=None):
    """Transform through port-centre pairs: affine (RANSAC). The ports lie in a strip two
    rows high, where a full perspective fit is free to lean the panel over; the
    perspective across such a strip is below the detection jitter anyway. An affine can
    still shear on two jittery rows, so a leaning one gives way to a similarity (place,
    scale, turn). Returns (3x3 matrix, inlier mask) or (None, None)."""
    A, inl = cv2.estimateAffine2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=thresh)
    if A is not None and tpl is not None and _lean(tpl, np.vstack([A, [0, 0, 1]])) > 0.15:
        A, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=thresh)
    if A is None:
        return None, None
    return np.vstack([A, [0, 0, 1]]), inl


def _ordered_pairs(tpl, H, photo_ports):
    """(template centre, detected centre) pairs by column order, per kind, when the
    template and the detections have the same number of ports and columns of that kind."""
    src, dst = [], []
    for kind in ("rj45", "sfp"):
        tp = [p for p in tpl.meta["ports"] if p["type"] == kind]
        dp_all = [p for p in photo_ports if p["type"] == kind]
        dp = [p for p in dp_all if p.get("status") != "not seen"]
        if not tp or len(tp) != len(dp_all):
            continue
        tc = [(p["box"][0] + p["box"][2] / 2, p["box"][1] + p["box"][3] / 2) for p in tp]
        proj = cv2.perspectiveTransform(np.float32(tc).reshape(-1, 1, 2), H).reshape(-1, 2)
        dc = [(p["box"][0] + p["box"][2] / 2, p["box"][1] + p["box"][3] / 2) for p in dp_all]
        w = float(np.median([p["box"][2] for p in dp])) if dp else 1
        to, tn = _grid_order(proj.tolist(), w)
        do, dn = _grid_order(dc, w)
        if tn != dn:
            continue
        seen = {i for i, p in enumerate(dp_all) if p.get("status") != "not seen"}
        for a, b in zip(to, do):
            if b in seen:                            # only ports the model actually found
                src.append(tuple(tc[a])); dst.append(tuple(dc[b]))
    return src, dst


def _nearest_pairs(tpl, H, photo_ports, radius):
    """(template centre, detected centre) pairs: nearest, one to one, within radius."""
    seen = [p for p in photo_ports or [] if p.get("status") != "not seen" and p["type"] in ("rj45", "sfp")]
    tp = [p for p in tpl.meta["ports"] if p["type"] in ("rj45", "sfp")]
    if not seen or not tp:
        return [], []
    tc = np.float32([(p["box"][0] + p["box"][2] / 2, p["box"][1] + p["box"][3] / 2) for p in tp])
    dc = np.float32([(q["box"][0] + q["box"][2] / 2, q["box"][1] + q["box"][3] / 2) for q in seen])
    proj = cv2.perspectiveTransform(tc.reshape(-1, 1, 2), np.asarray(H, np.float64)).reshape(-1, 2)
    d = np.linalg.norm(proj[:, None, :] - dc[None, :, :], axis=2)
    src, dst, ut, ud = [], [], set(), set()
    for k in np.argsort(d, axis=None):
        a, b = divmod(int(k), d.shape[1])
        if d[a, b] > radius:
            break
        if a not in ut and b not in ud and tp[a]["type"] == seen[b]["type"]:
            ut.add(a); ud.add(b); src.append(tuple(tc[a])); dst.append(tuple(dc[b]))
    return src, dst


def align_by_ports(tpl, photo_ports, photo_shape):
    """Alignment from the ports alone, for when patch matching fails (a vendor render can
    look quite different from a photo). Template ports and the ports the trained models
    found in the photo are paired in column order when both have the same layout (same
    number of ports and columns per kind). Returns an alignment dict like align(), or None."""
    tc_all, dc_all = [], []
    for kind in ("rj45", "sfp"):
        tp = [p for p in tpl.meta["ports"] if p["type"] == kind]
        dp = [p for p in photo_ports if p["type"] == kind]
        if not tp or len(tp) != len(dp):
            continue
        tc = [(p["box"][0] + p["box"][2] / 2, p["box"][1] + p["box"][3] / 2) for p in tp]
        dc = [(p["box"][0] + p["box"][2] / 2, p["box"][1] + p["box"][3] / 2) for p in dp]
        to, tn = _grid_order(tc, float(np.median([p["box"][2] for p in tp])))
        do, dn = _grid_order(dc, float(np.median([p["box"][2] for p in dp])))
        if tn != dn:
            continue
        for a, b in zip(to, do):
            if dp[b].get("status") != "not seen":
                tc_all.append(tc[a]); dc_all.append(dc[b])
    if len(tc_all) < 8:
        return None
    pw = float(np.median([p["box"][2] for p in photo_ports]))
    H, inl = _fit_ports(np.float32(tc_all), np.float32(dc_all), 0.3 * pw, tpl)
    if H is None:
        return None
    n = int(inl.sum())
    ok, why = plausible(tpl, H, photo_shape)
    ratio = n / len(tc_all)
    if not ok or ratio < 0.8:
        return None
    return dict(H=H, inliers=n, coarse=0.0, patch=ratio, confidence=round(ratio, 3),
                plausible=True, implausible_why="", by_ports=True)


def _clusters(vals, tol):
    """1-D cluster centres (sorted), merging values closer than tol."""
    out = []
    for v in sorted(vals):
        if out and v - out[-1][-1] < tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [float(np.mean(c)) for c in out]


def align_by_port_grid(tpl, photo_ports, photo_shape):
    """Alignment from the ports when some are hidden or missed (cables over the first
    block, a dark photo), so the counts differ and align_by_ports can't pair them in
    order. Every pairing of a template jack column with a detected column, and of a
    template row with a detected row, gives a placement at the port-width scale; the
    one putting most template ports on detections is refined by pairing each port with
    its nearest detection and re-fitting, a few rounds. Returns an alignment dict or None."""
    seen = [p for p in photo_ports or [] if p.get("status") != "not seen" and p["type"] in ("rj45", "sfp")]
    tp = [p for p in tpl.meta["ports"] if p["type"] in ("rj45", "sfp")]
    if len(seen) < 6 or len(tp) < 6:
        return None
    cen = lambda p: (p["box"][0] + p["box"][2] / 2, p["box"][1] + p["box"][3] / 2)
    D = np.float32([cen(p) for p in seen])
    T = np.float32([cen(p) for p in tp])
    pw = float(np.median([p["box"][2] for p in seen]))
    tw = float(np.median([p["box"][2] for p in tp if p["type"] == "rj45"] or [p["box"][2] for p in tp]))
    tcols, dcols = _clusters(T[:, 0], 0.5 * tw), _clusters(D[:, 0], 0.5 * pw)
    trows, drows = _clusters(T[:, 1], 0.5 * tw), _clusters(D[:, 1], 0.5 * pw)

    def score(P):
        d = np.linalg.norm(P[:, None, :] - D[None, :, :], axis=2)
        return int((d.min(axis=1) < 0.4 * pw).sum() + (d.min(axis=0) < 0.4 * pw).sum())

    hyps = []
    for s in pw / tw * np.array([0.85, 0.92, 1.0, 1.08, 1.17]):     # box widths are only roughly comparable
        q = 0.15 * pw                                                    # offsets this close are one hypothesis
        for tx in {round((dc - s * tc) / q) * q for tc in tcols for dc in dcols}:
            for ty in {round((dr - s * tr) / q) * q for tr in trows for dr in drows}:
                hyps.append((score(T * s + np.float32([tx, ty])), s, tx, ty))
    hyps.sort(reverse=True)
    best = None
    for _, s, tx, ty in hyps[:6]:
        H = np.float64([[s, 0, tx], [0, s, ty], [0, 0, 1]])
        for it in range(5):
            P = cv2.perspectiveTransform(T.reshape(-1, 1, 2), H).reshape(-1, 2)
            d = np.linalg.norm(P[:, None, :] - D[None, :, :], axis=2)
            pairs, ut, ud = [], set(), set()
            for k in np.argsort(d, axis=None):
                a, b = divmod(int(k), d.shape[1])
                if d[a, b] > 0.6 * pw:
                    break
                if a not in ut and b not in ud:
                    ut.add(a); ud.add(b); pairs.append((a, b))
            if len(pairs) < 6:
                break
            src, dst = T[[a for a, _ in pairs]], D[[b for _, b in pairs]]
            if it < 2 or len(_clusters(dst[:, 1], 0.5 * pw)) < 2:      # one row: similarity only
                A, _ = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=0.3 * pw)
                H = np.vstack([A, [0, 0, 1]]) if A is not None else None
            else:
                H, _ = _fit_ports(src, dst, 0.3 * pw, tpl)
            if H is None:
                break
        if not plausible(tpl, H, photo_shape)[0]:
            continue
        a = port_agreement(tpl, H, photo_ports, photo_shape)
        key = (min(a["on"], a["covered"]), a["pairs"]) if a else (0, 0)
        if best is None or key > best[0]:
            best = (key, H, a)
    if best is None or not best[2]:
        return None
    (q, n), H, a = best
    return dict(H=H, inliers=n, coarse=0.0, patch=q, confidence=round(q, 3), plausible=True,
                implausible_why="", by_ports=True, by_grid=True)


def passes(r):
    """Is an alignment good enough to use? Patch matching needs MIN_INLIERS points and
    MIN_CONFIDENCE; a port-based alignment needs 8+ port pairs agreeing (80%+)."""
    if r is None or r.get("ports_ok") is False:      # ports don't land on the photo's ports
        return False
    if r.get("by_ports"):
        return r["inliers"] >= 8 and r["confidence"] >= 0.8
    a = r.get("ports")
    if r.get("ports_ok") and a and a["pairs"] >= 8 and min(a["on"], a["covered"]) >= 0.95:
        # every port lands on a detected port: far stronger evidence than a point count,
        # so a weak feature match (dull photo, render vs photo) is enough
        return r["inliers"] >= 20 and r["confidence"] >= 0.3
    return r["inliers"] >= MIN_INLIERS and r["confidence"] >= MIN_CONFIDENCE


def port_agreement(tpl, H, photo_ports, photo_shape=None):
    """Do the template's ports land on the ports the trained models found in the photo?

    Feature matching alone can be fooled: rows of identical jacks give hundreds of
    'inliers' for a template one block short, or with its SFP cages on RJ45 jacks. Each
    template port is projected into the photo and paired with the nearest unpaired
    detection within half a port width.
    Returns dict(on=share of template ports in the photo that land on a detection,
    covered=share of detections with a template port on them, same_kind=share of pairs
    whose kind agrees, pairs, detections), or None with too few detections to judge."""
    seen = [p for p in photo_ports or [] if p.get("status") != "not seen" and p["type"] in ("rj45", "sfp")]
    if len(seen) < 6:
        return None
    pw = float(np.median([q["box"][2] for q in seen]))
    dc = np.float32([(q["box"][0] + q["box"][2] / 2, q["box"][1] + q["box"][3] / 2) for q in seen])
    tp = [p for p in tpl.meta["ports"] if p["type"] in ("rj45", "sfp")]
    if not tp:
        return None
    tc = np.float32([(p["box"][0] + p["box"][2] / 2, p["box"][1] + p["box"][3] / 2) for p in tp])
    proj = cv2.perspectiveTransform(tc.reshape(-1, 1, 2), np.asarray(H, np.float64)).reshape(-1, 2)
    if photo_shape is not None:      # ports outside a cropped photo can't be checked
        h, w = photo_shape[:2]
        inside = [i for i, (x, y) in enumerate(proj) if -0.3 * pw <= x <= w + 0.3 * pw and -0.3 * pw <= y <= h + 0.3 * pw]
    else:
        inside = list(range(len(tp)))
    if not inside:
        return dict(on=0.0, covered=0.0, same_kind=0.0, pairs=0, detections=len(seen), checked=0, err=None)
    d = np.linalg.norm(proj[inside][:, None, :] - dc[None, :, :], axis=2)
    pairs, used_t, used_d = [], set(), set()
    for k in np.argsort(d, axis=None):
        a, b = divmod(int(k), d.shape[1])
        if d[a, b] > 0.5 * pw:
            break
        if a not in used_t and b not in used_d:
            used_t.add(a); used_d.add(b); pairs.append((inside[a], b))
    same = sum(tp[a]["type"] == seen[b]["type"] for a, b in pairs)
    errs = [float(np.linalg.norm(proj[a] - dc[b])) / pw for a, b in pairs]
    return dict(on=round(len(pairs) / len(inside), 3), covered=round(len(pairs) / len(seen), 3),
                same_kind=round(same / max(len(pairs), 1), 3), pairs=len(pairs), detections=len(seen),
                checked=len(inside), err=round(float(np.mean(errs)), 3) if errs else None)


def ports_agree(a):
    """Verdict on port_agreement(): True, False, or None when it can't be judged.
    Most template ports must land on a detection and most detections must have a
    template port on them. On 14 batch results every visibly right overlay scored 0.9+
    on both and every wrong one 0.75 or less on one. Both halves always apply: with
    only the second, a 28-port template passed on a TL-SX3008F photo where the models
    found 7 ports (7 of 28 landed). A photo with most ports hidden goes to auto mode."""
    if a is None:
        return None
    return a["covered"] >= PORTS_AGREE and a["on"] >= PORTS_AGREE


def check_ports(tpl, r, photo_ports, photo_shape):
    """Attach the port check to an alignment dict (r["ports"], r["ports_ok"])."""
    if r is not None and photo_ports:
        a = port_agreement(tpl, r["H"], photo_ports, photo_shape)
        r["ports"], r["ports_ok"] = a, ports_agree(a)
    return r


def snap_to_ports(tpl, r, photo, ports=None):
    """Alignment re-fitted through the ports found by the trained port models, when they
    are available and found the same layout; otherwise r unchanged. `ports`: detections
    already made for this photo (else the models run here)."""
    from . import ports as yolo_ports
    if r is None or (ports is None and not yolo_ports.available()):
        return r
    if ports is None:
        ports, _ = yolo_ports.detect_ports(photo)
    H2, n = refine_with_ports(tpl, r["H"], ports)
    if n and plausible(tpl, H2, photo.shape[:2])[0]:
        return dict(r, H=H2, refined_pairs=n, photo_ports=ports)
    return dict(r, photo_ports=ports)



def snap_polygons(tpl, H, photo_ports):
    """Port polygons in the photo, each pulled onto the port the models detected there.

    The homography is one global fit, so at any single port it can still be off by a
    third of a port. Template ports are paired with detected ports by projection
    (nearest, one-to-one, within 0.6 port widths); each pair's offset is a local
    correction. Every box then takes the shift and scale that best map the anchors
    around it onto their detections (moving least squares, weighted by distance, capped
    at 0.9 port widths): paired ports land on their detections, ports hidden under cables
    follow their neighbours and take the local port spacing. (Grew out of teammate 1's
    snap_overlay, a distance-weighted shift; that raised ports within a quarter port from
    89% to 94% on 60 test cases.)
    Returns ({port id: 4 corners}, {port id: status}, number of anchors)."""
    H = np.asarray(H, np.float64)
    seen = [p for p in photo_ports or [] if p.get("status") != "not seen" and p["type"] in ("rj45", "sfp")]
    polys, status = {}, {}
    for p in tpl.meta["ports"]:
        x, y, w, h = p["box"]
        polys[p["id"]] = cv2.perspectiveTransform(
            np.float32([[[x, y]], [[x + w, y]], [[x + w, y + h]], [[x, y + h]]]), H).reshape(4, 2)
    if len(seen) < 3:
        return polys, status, 0
    pw = float(np.median([q["box"][2] for q in seen]))
    centre = lambda q: (q["box"][0] + q["box"][2] / 2, q["box"][1] + q["box"][3] / 2)
    taken, anchors = set(), []
    for p in tpl.meta["ports"]:
        if p["type"] not in ("rj45", "sfp"):
            continue
        cx, cy = polys[p["id"]].mean(axis=0)
        best, bd = None, 0.6 * pw
        for i, q in enumerate(seen):
            if i in taken:
                continue
            qx, qy = centre(q)
            d = float(np.hypot(qx - cx, qy - cy))
            if d < bd:
                best, bd = i, d
        if best is not None:
            taken.add(best)
            qx, qy = centre(seen[best])
            anchors.append((cx, cy, qx - cx, qy - cy))
            status[p["id"]] = seen[best].get("status")
    # too few anchors and one bad detection pulls whole blocks off (seen on a dark
    # photo with 6 of 28 ports detected), so snap only with good coverage
    data_ports = sum(p["type"] in ("rj45", "sfp") for p in tpl.meta["ports"])
    if len(anchors) < max(8, 0.4 * data_ports):
        return polys, status, 0
    # each box follows the detections around it rather than its own alone: a detected
    # box wraps the plug too where a cable is in, so single detections sit a little
    # high or low, and averaging over the neighbours evens that out
    sigma, cap = 1.2 * pw, 0.6 * pw
    A = np.array([[ax, ay] for ax, ay, _, _ in anchors], np.float64)
    B = A + np.array([[dx, dy] for _, _, dx, dy in anchors], np.float64)
    for pid, quad in polys.items():
        cx, cy = quad.mean(axis=0)
        wts = np.exp(-((A[:, 0] - cx) ** 2 + (A[:, 1] - cy) ** 2) / (2 * sigma * sigma))
        if wts.sum() < 1e-9:
            continue
        # moving least squares: the similarity (shift, scale, rotation) that best maps
        # the nearby anchors onto their detections, so a box also takes the local
        # spacing and size of the ports around it; falls back to the plain shift when
        # too few anchors are near or the fit asks for an unlikely scale or turn
        new = _local_similarity(A, B, wts, quad)
        if new is None:
            shift = (wts[:, None] * (B - A)).sum(axis=0) / wts.sum()
            norm = float(np.hypot(*shift))
            if norm > cap:
                shift *= cap / norm
            new = quad + shift
        else:
            move = new.mean(axis=0) - quad.mean(axis=0)
            norm = float(np.hypot(*move))
            if norm > cap:
                new = new - move + move * cap / norm
        polys[pid] = new.astype(np.float32)
    for p in tpl.meta["ports"]:
        if p["type"] in ("rj45", "sfp"):
            status.setdefault(p["id"], "not seen")
    return polys, status, len(anchors)


def _local_similarity(A, B, w, quad, max_scale=0.15):
    """Weighted shift + uniform scale A -> B applied to quad (no rotation: that is the
    global fit's job, and two close rows of jittery boxes don't pin a rotation down), or
    None when the weights rest on fewer than ~3 anchors or the scale is implausible."""
    if w.sum() / w.max() < 2.5:
        return None
    ws = w / w.sum()
    ma, mb = (ws[:, None] * A).sum(axis=0), (ws[:, None] * B).sum(axis=0)
    Ac, Bc = A - ma, B - mb
    var = float((ws * (Ac * Ac).sum(axis=1)).sum())
    if var < 1e-6:
        return None
    sc = float((ws * (Ac * Bc).sum(axis=1)).sum() / var)
    if not (1 - max_scale) <= sc <= (1 + max_scale):
        return None
    return sc * (quad - ma) + mb


def align(tpl, photo_gray, debug=None, photo_ports=None, port_fallback=True):
    """Refine every coarse candidate; the one with the most RANSAC inliers wins.
    Pass a dict as `debug` to get the coarse candidates and the winner's matched points.
    With `photo_ports` (from the trained port models) the candidate whose ports land on
    the detected ports is preferred and the result is checked against them (r["ports_ok"]);
    with `port_fallback`, a failed match falls back to aligning by the ports themselves
    (align_by_ports)."""
    # fast path first: the scale the ports predict, the best dozen guesses; the full
    # search over every width only when that doesn't give a passing fit
    r = None
    if photo_ports:
        dbg_fast = {} if debug is not None else None
        r = _finish(tpl, photo_gray, _align_patches(tpl, photo_gray, dbg_fast, photo_ports, fast=True), photo_ports)
        if passes(r):
            if debug is not None:
                debug.update(dbg_fast, fast=True)
            return r
    r_full = _finish(tpl, photo_gray, _align_patches(tpl, photo_gray, debug, photo_ports), photo_ports)
    if r is None or r_full is not None and (passes(r_full) or not passes(r)):
        r = r_full
    if photo_ports and port_fallback and not passes(r):
        for fn in (align_by_ports, align_by_port_grid):
            rp = check_ports(tpl, fn(tpl, photo_ports, photo_gray.shape), photo_ports, photo_gray.shape)
            if rp is not None and passes(rp):
                if debug is not None:
                    debug["by_ports"] = True
                return rp
    return r


def _finish(tpl, photo_gray, r, photo_ports):
    """Dense polish, port check and the drift rescue for one patch alignment."""
    if r is not None and r["plausible"]:
        H2, dense = refine_dense(tpl, photo_gray, r["H"], photo_ports, photo_gray.shape)
        if dense:
            r = dict(r, H=H2, dense=True)
    r = check_ports(tpl, r, photo_ports, photo_gray.shape)
    if r is not None and r.get("ports_ok") is False and r["ports"]["on"] >= 0.4:
        # roughly in place but drifted across the jack rows (cables hide them from
        # patch matching): re-fit through the ports, as the final overlay does, and
        # judge that. A template far off (squeezed into half the switch) isn't rescued.
        H2, n = refine_with_ports(tpl, r["H"], photo_ports)
        if n and plausible(tpl, H2, photo_gray.shape)[0]:
            r2 = check_ports(tpl, dict(r, H=H2, refined_pairs=n), photo_ports, photo_gray.shape)
            if r2["ports_ok"]:
                r = r2
    return r


FAST_TOP = 12                    # rough guesses refined on the fast path


def predicted_fracs(tpl, photo_gray, photo_ports):
    """Template widths to try, from the ports: a port is about as wide in the template
    as in the photo once scaled, so the photo's port width over the template's gives
    the scale directly. Returns 7 widths within 15 % of that, or None without enough
    ports on both sides (then every width is tried)."""
    seen = [q for q in photo_ports or [] if q.get("status") != "not seen" and q["type"] == "rj45"]
    tp = [p for p in tpl.meta["ports"] if p["type"] == "rj45"]
    if len(seen) < 6 or len(tp) < 6:
        return None
    sc = float(np.median([q["box"][2] for q in seen])) / float(np.median([p["box"][2] for p in tp]))
    frac = sc * tpl.gray.shape[1] / photo_gray.shape[1]
    if not 0.15 < frac < 1.3:
        return None
    return [frac * k for k in (0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15)]


def _align_patches(tpl, photo_gray, debug=None, photo_ports=None, fast=False):
    """Refine the rough guesses and keep the best. fast=True: only the template widths
    the ports predict, and only the FAST_TOP best guesses (plus the keypoint ones) are
    refined; align() falls back to the full search when that doesn't pass."""
    best = None
    fracs = predicted_fracs(tpl, photo_gray, photo_ports) if fast else None
    if fast and fracs is None:
        return None
    coarse = coarse_candidates(tpl, photo_gray, fracs=fracs)
    if fast:
        coarse = sorted(coarse, key=lambda c: -c[0])[:FAST_TOP]
    coarse += feature_candidates(tpl, photo_gray)
    for i, (coarse_score, H0) in enumerate(coarse):
        d = {} if debug is not None else None
        H, inliers, patch_score = refine_align(tpl, photo_gray, H0, debug=d)
        r = _result(tpl, H, inliers, patch_score, coarse_score, photo_gray.shape)
        # rows of identical jacks give many inliers at a wrong offset or scale; where
        # the port models found the ports, the placement that puts ports on them wins,
        # and among those the one whose ports sit most exactly on them (a candidate can
        # collect more patch inliers on the chassis yet be a quarter port off across
        # the jack block); a placement that passes beats one that doesn't
        a = port_agreement(tpl, H, photo_ports, photo_gray.shape) if photo_ports else None
        on_ports = round(min(a["on"], a["covered"]), 1) if a else 0.0
        exact = -round(a["err"], 2) if a and a.get("err") is not None else 0.0
        ok = passes(check_ports(tpl, dict(r), photo_ports, photo_gray.shape)) if photo_ports else passes(r)
        key = (r["plausible"], on_ports, ok, exact, inliers, patch_score)
        if best is None or key > best[0]:
            best = (key, r, i, d)
    if best is None:
        return None
    _, r, idx, d = best
    if debug is not None:
        debug.update(coarse=coarse, chosen=idx, matches=d or {})
    return r


def _result(tpl, H, inliers, patch_score, coarse_score, photo_shape):
    ok, why = plausible(tpl, H, photo_shape)
    confidence = float(np.clip(patch_score * min(1.0, inliers / 60), 0, 1))
    if not ok:                     # never passes MIN_CONFIDENCE, whatever the point count
        confidence = min(confidence, 0.2)
    return dict(H=H, inliers=inliers, coarse=coarse_score, patch=patch_score, confidence=confidence,
                plausible=ok, implausible_why=why)


def font(size, bold=True):
    names = ["DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"] if bold else ["DejaVuSans.ttf", "arial.ttf", "Arial.ttf"]
    for n in names:
        for base in ("", "/usr/share/fonts/truetype/dejavu/", "C:/Windows/Fonts/", "/Library/Fonts/"):
            try:
                return ImageFont.truetype(base + n, size)
            except OSError:
                pass
    return ImageFont.load_default()


def draw_overlay(tpl: Template, photo_bgr, H, upscale, polys=None, trace=False):
    """Photo with every port's box and number, and the front panel's outline. With
    `polys` (from snap_polygons) the boxes are the snapped ones. `trace` also draws the
    template's edges warped onto the photo (how the vendor image itself lines up)."""
    h, w = photo_bgr.shape[:2]
    S = np.diag([upscale, upscale, 1.0])
    Hs = S @ H
    W, Hh = int(w * upscale), int(h * upscale)
    base = Image.fromarray(cv2.cvtColor(cv2.resize(photo_bgr, (W, Hh), interpolation=cv2.INTER_LANCZOS4), cv2.COLOR_BGR2RGB))
    if trace:
        e = cv2.dilate(tpl.edges, np.ones((2, 2), np.uint8))
        ew = cv2.warpPerspective(e, Hs, (W, Hh))
        base.paste(Image.new("RGB", (W, Hh), (0, 235, 255)), (0, 0), Image.fromarray(ew))
    scale = np.sqrt(abs(np.linalg.det(Hs[:2, :2])))
    fs = font(max(13, int(tpl.gray.shape[1] * scale / 72)))
    lw = max(2, int(round(upscale * 1.3)))
    # dark fills + bright borders on a transparent layer, composited, so each port reads
    # as a dark annotation box the number sits on, over any cable or panel colour
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    x, y, pw_, ph_ = tpl.meta.get("match_regions", [[0, 0, tpl.gray.shape[1], tpl.gray.shape[0]]])[0]
    panel = cv2.perspectiveTransform(np.float32([[[x, y]], [[x + pw_, y]], [[x + pw_, y + ph_]], [[x, y + ph_]]]), Hs).reshape(4, 2)
    ld.polygon([tuple(map(float, c)) for c in panel], outline=(0, 235, 255, 180), width=max(1, lw - 1))
    centres = []
    for p in tpl.meta["ports"]:
        if polys and p["id"] in polys:
            q = polys[p["id"]] * upscale
        else:
            bx, by, bw, bh = p["box"]
            q = cv2.perspectiveTransform(np.float32([[[bx, by]], [[bx + bw, by]], [[bx + bw, by + bh]], [[bx, by + bh]]]), Hs).reshape(4, 2)
        c = q.mean(axis=0)
        q = c + (q - c) * 0.9
        col = (0, 235, 255) if p["type"] == "rj45" else (255, 90, 230) if p["type"] == "sfp" else (255, 190, 0)
        ld.polygon([tuple(map(float, k)) for k in q], fill=(0, 0, 0, 150), outline=col + (255,), width=lw)
        centres.append((p["id"], float(c[0]), float(c[1])))
    base = Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB")
    d = ImageDraw.Draw(base)
    for pid, cx, cy in centres:                    # every port numbered, in a readable dark box
        d.text((cx, cy), str(pid), font=fs, fill=(255, 255, 255), anchor="mm",
               stroke_width=max(3, lw), stroke_fill=(0, 0, 0))
    return base


def draw_auto_overlay(photo_bgr, ports, upscale):
    """Photo with port outlines (named ports labelled; numbers are in result.json). With
    the trained port models: cyan = empty,
    orange = cable plugged in, dashed = not detected, filled in from the grid. With the
    classical detector: solid = open port seen, dashed = filled in from the grid."""
    h, w = photo_bgr.shape[:2]
    W, Hh = int(w * upscale), int(h * upscale)
    base = Image.fromarray(cv2.cvtColor(cv2.resize(photo_bgr, (W, Hh), interpolation=cv2.INTER_LANCZOS4), cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(base)
    pw = np.median([p["box"][2] for p in ports]) * upscale if ports else 20
    lw = max(2, int(pw / 18))
    f = font(max(12, int(pw * 0.5)))
    for p in ports:
        x, y, bw, bh = [v * upscale for v in p["box"]]
        col = (0, 235, 255) if p["type"] == "rj45" else (255, 120, 220)
        if p.get("status") == "connected":
            col = (255, 150, 0)
        if p.get("status") in ("connected", "empty", "unknown") or p.get("status") is None and p["opening_visible"]:
            d.rectangle([x, y, x + bw, y + bh], outline=col, width=lw)
        else:
            dash = max(4, int(bw / 6))
            for x0, y0, x1, y1 in [(x, y, x + bw, y), (x, y + bh, x + bw, y + bh), (x, y, x, y + bh), (x + bw, y, x + bw, y + bh)]:
                n = max(1, int(max(abs(x1 - x0), abs(y1 - y0)) / dash))
                for i in range(0, n, 2):
                    a, b = i / n, min(1, (i + 1) / n)
                    d.line([(x0 + (x1 - x0) * a, y0 + (y1 - y0) * a), (x0 + (x1 - x0) * b, y0 + (y1 - y0) * b)], fill=col, width=lw)
        if not isinstance(p["id"], int):           # port numbers are in result.json, not drawn
            d.text((x + bw / 2, y + bh / 2), str(p["id"]), font=f, fill=(255, 255, 255), anchor="mm",
                   stroke_width=max(2, lw + 1), stroke_fill=(0, 0, 0))
    return base


NAMED_PORTS = {"CONSOLE": ("Console", "console"), "MANAGEMENT_PORT": ("Mgmt", "management"),
               "USB_A": ("USB", "usb"), "USB_B": ("USB", "usb"), "USB_C": ("USB", "usb")}


DATA_PORTS = {"RJ45": "rj45", "SFP": "sfp", "QSFP": "sfp"}


MIN_ADDED_CONF = 0.45            # a data port the template lacks is added only above this


def add_photo_ports(tpl, H, photo, polys, status, save=True):
    """Ports the trained models see in the photo but the template lacks, added to the
    overlay. Two ways a template misses a port: the vendor image is a small render where
    the console reads as an SFP cage (MikroTik CRS328's 768 px product shot), or the
    grid step sets a small block apart as stray (the four copper uplinks of a
    DES-1210-28P). Every raw detection whose centre lies on the fitted front panel and
    not on a template port is added:
      - console, management, USB: by name; also saved into the template (save=True), so
        the next photo of the model has it even when a cable hides it;
      - RJ45 / SFP (confidence >= MIN_ADDED_CONF): numbered after the template's last
        port, column by column. Not saved: the template's port count is what the port
        check and the spec compare, and the printed labels of such ports (25T, 26T) are
        too small to read on a vendor image to number them for sure.
    Adds to `polys` and `status`; returns the added ports (template coordinates)."""
    from . import ports as yolo_ports
    if photo is None or not yolo_ports.available():
        return []
    H = np.asarray(H, np.float64)
    Hinv = np.linalg.inv(H)
    th, tw = tpl.gray.shape
    data = [p for p in tpl.meta["ports"] if p["type"] in ("rj45", "sfp")]
    pw = float(np.median([p["box"][2] for p in data])) if data else 0.05 * tw
    taken = [(p["box"][0] + p["box"][2] / 2, p["box"][1] + p["box"][3] / 2) for p in tpl.meta["ports"]]
    new_named, new_data = [], []
    for d in yolo_ports.raw_detections(photo):
        kind = NAMED_PORTS.get(d["label"]) or DATA_PORTS.get(d["label"])
        if kind is None or (d["label"] in DATA_PORTS and d["conf"] < MIN_ADDED_CONF):
            continue
        x1, y1, x2, y2 = d["xyxy"]
        quad = np.float32([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])
        tq = cv2.perspectiveTransform(quad.reshape(-1, 1, 2), Hinv).reshape(4, 2)
        cx, cy = tq.mean(axis=0)
        if not (0 <= cx <= tw and 0 <= cy <= th):
            continue                                   # another device, outside this panel
        if any(np.hypot(cx - a, cy - b) < 0.6 * pw for a, b in taken):
            continue                                   # the template has a port there
        taken.append((cx, cy))
        bx0, by0 = tq.min(axis=0)
        bx1, by1 = tq.max(axis=0)
        port = {"type": None, "poe": None, "found_in_photo": True,
                "box": [int(round(bx0)), int(round(by0)), int(round(bx1 - bx0)), int(round(by1 - by0))]}
        (new_named if d["label"] in NAMED_PORTS else new_data).append((port, quad, d, kind))
    added = []
    for port, quad, d, (name, kind) in new_named:
        pid = name if name not in polys else f"{name} {sum(str(k).startswith(name) for k in polys) + 1}"
        port.update(id=pid, type=kind)
        added.append((port, quad, d))
    # data ports: next numbers, column by column (left to right, top to bottom)
    nxt = max([p["id"] for p in tpl.meta["ports"] if isinstance(p["id"], int)], default=0) + 1
    for port, quad, d, kind in sorted(new_data, key=lambda e: (round(e[0]["box"][0] / max(pw, 1)), e[0]["box"][1])):
        port.update(id=nxt, type=kind)
        nxt += 1
        added.append((port, quad, d))
    for port, quad, d in added:
        tpl.meta["ports"].append(port)
        polys[port["id"]] = quad
        status[port["id"]] = d.get("status")
    named = [p for p, _, _ in added if not isinstance(p["id"], int)]
    if named and save and (tpl.folder / "template.json").exists():
        saved = json.loads((tpl.folder / "template.json").read_text())
        saved["ports"] += [dict(p) for p in named]
        (tpl.folder / "template.json").write_text(json.dumps(saved, indent=1))
    return [p for p, _, _ in added]

