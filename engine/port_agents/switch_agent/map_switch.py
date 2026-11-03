#!/usr/bin/env python3
"""
map_switch.py - identify a network switch from one front-panel photo and map its ports.

Usage:
    python map_switch.py photo.jpg
    python map_switch.py photo.jpg --out results/ --model tp-link_tl-sg2428p

Pipeline:
    1. Original photo
    2. Make and model   - every template in data/templates/ is aligned to the photo; the best
                          alignment score wins (or use --model to force one)
    3. Vendor image     - the clean front view stored with the template
    4. Blueprint        - edges traced from the vendor image plus port numbers
    5. Overlay          - vendor outline warped onto the photo with port numbers

Outputs (in --out): 1_original.png, 3_vendor.png, 4_blueprint.png, 5_overlay.png,
flow.png (all five steps in one image) and result.json (model, confidence,
homography and every port's outline in photo pixel coordinates).
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"          # saved templates, one folder per model
VENDOR_PHOTOS = HERE / "vendor_photos"  # saved vendor product photos (+ web/ download cache)
VENDORS_JSON = HERE / "vendors.json"    # vendor websites for the image search
WEB = HERE / "ui"                       # the UI page
OUTPUT = HERE / "output"                # results; created on first use
UI_RUNS = OUTPUT / "ui_runs"            # one folder per UI run
SHOWCASE = HERE / "showcase"            # UI showcase examples
from autodetect import detect_ports

CLAHE = cv2.createCLAHE(3.0, (8, 8))
MIN_INLIERS = 45          # alignment is not trusted below these (tested: true matches
MIN_CONFIDENCE = 0.5      # score 68+ inliers / 0.70+, non-matches 26-29 / 0.25-0.33)
PORTS_AGREE = 0.8         # share of ports that must line up with the detected ones (port_agreement)
# How big each port's annotation box is drawn, as a multiple of the port measured on the
# vendor image. Drawing only: result.json always carries the measured size. 1.0 is the
# port itself; the detector measures the opening while the jack a person sees includes
# the shield around it, so over 1 reads as a better fit on a photo. This is what the
# drawing *asks* for - _box_scale() lowers it per template where there is not room.
PORT_BOX_SCALE = 1.15
PORT_BOX_GAP = 0.05       # of the pitch; the gap that must survive, so boxes never merge
COARSE_MAX_W = 1200       # photo is downscaled to this width for the coarse search


# ----------------------------------------------------------------------------- templates

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


def load_templates(folder: Path):
    out = []
    for d in sorted(folder.iterdir()) if folder.is_dir() else []:
        if (d / "template.json").exists():
            out.append(Template(d))
    return out


# ----------------------------------------------------------------------------- alignment

def _prep(gray, blur=1.2):
    return cv2.GaussianBlur(CLAHE.apply(gray), (0, 0), blur)


def coarse_candidates(tpl: Template, photo_gray, top_per_scale=2):
    """Masked multi-scale NCC search. NCC alone is not reliable here (small scales can
    score high on random texture), so this only proposes candidates: the best few
    positions at each scale, each as a 3x3 template->photo homography."""
    H0, W0 = photo_gray.shape
    f = min(1.0, COARSE_MAX_W / W0)
    small = cv2.resize(photo_gray, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    pad = int(0.04 * small.shape[1])
    P = cv2.copyMakeBorder(_prep(small, 1.5), pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
    th, tw = tpl.gray.shape
    back = np.diag([1 / f, 1 / f, 1.0])
    cands = []
    for frac in np.arange(0.2, 1.16, 0.03):              # template width / photo width
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
    if r.get("open_ok") is False:                     # open jacks in the photo left uncovered
        return False
    if r.get("layout_ok") is False:                   # another hardware revision (port layout)
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
    # jacks hidden under cables: the port models fill their positions in from the port grid
    # ("not seen"); a template port landing on one of those is on a port too
    hidden = [p for p in photo_ports or [] if p.get("status") == "not seen" and p["type"] in ("rj45", "sfp")]
    on_hidden = 0
    if hidden:
        hc = np.float32([(q["box"][0] + q["box"][2] / 2, q["box"][1] + q["box"][3] / 2) for q in hidden])
        left = [i for i in inside if i not in {a for a, _ in pairs}]
        used_h = set()
        for i in left:
            dh = np.linalg.norm(hc - proj[i], axis=1)
            j = int(np.argmin(dh))
            if dh[j] <= 0.5 * pw and j not in used_h:
                used_h.add(j); on_hidden += 1
    return dict(on=round(len(pairs) / len(inside), 3), covered=round(len(pairs) / len(seen), 3),
                same_kind=round(same / max(len(pairs), 1), 3), pairs=len(pairs), detections=len(seen),
                checked=len(inside), err=round(float(np.mean(errs)), 3) if errs else None,
                on_with_hidden=round((len(pairs) + on_hidden) / len(inside), 3))


def ports_agree(a):
    """Verdict on port_agreement(): True, False, or None when it can't be judged.
    Most template ports must land on a detection and most detections must have a
    template port on them. On 14 batch results every visibly right overlay scored 0.9+
    on both and every wrong one 0.75 or less on one. Both halves always apply: with
    only the second, a 28-port template passed on a TL-SX3008F photo where the models
    found 7 ports (7 of 28 landed). A photo with most ports hidden goes to auto mode."""
    if a is None:
        return None
    if a["covered"] >= 0.95 and a.get("on_with_hidden", 0) >= PORTS_AGREE:
        return True       # every visible port matched; the rest land on jacks hidden under cables
    return a["covered"] >= PORTS_AGREE and a["on"] >= PORTS_AGREE


_OPEN = {}     # photo fingerprint -> open jacks found by the classical detector


def open_jacks(photo_gray):
    """Ports the classical detector (auto mode) sees as open jacks, for the port check
    when the trained port models are not available. It only finds jacks with no cable
    in them, so it can say "this visible jack has no template port on it" but not
    "this template port has no jack under it"."""
    return _classical(photo_gray)[0]


def _classical(photo_gray):
    """Classical detection of the photo, once per photo: (open jacks, fitted grid or None)."""
    small = cv2.resize(photo_gray, (64, 16), interpolation=cv2.INTER_AREA)
    key = (photo_gray.shape, small.tobytes())
    if key not in _OPEN:
        _OPEN.clear()
        dbg = {}
        try:
            ports, _ = detect_ports(cv2.cvtColor(photo_gray, cv2.COLOR_GRAY2BGR), method="classical", debug=dbg)
        except Exception:
            ports = []
        _OPEN[key] = ([dict(p, status="empty" if p.get("opening_visible") else "not seen") for p in ports],
                      dbg.get("grid"))
    return _OPEN[key]


def photo_layout(photo_gray):
    """Port blocks seen in the photo: {"blocks": n, "seen": [open columns per block]}, or
    None when too few open jacks are seen to say (a block hidden under cables would
    read as missing). Seen column counts are lower bounds: cables hide end columns."""
    g = _classical(photo_gray)[1]
    if not g or len(g["members"]) < 8:
        return None
    seen = [len(b) for b in g["seen_blocks"] if len(b) >= 2]     # a lone column is a stray or an uplink
    if len(seen) < 2:
        return None
    return {"blocks": len(seen), "seen": seen}


def photo_layout_from_ports(photo_ports):
    """Port blocks from the port models' detections: {"blocks": n, "seen": [columns per
    block]}, or None when they can't tell. photo_layout() counts open jacks, so on a fully
    cabled panel it sees nothing and the wrong hardware revision passed (DGS-1024C photo of
    6 + 6 columns fitted with the 4 + 4 + 4 template); the models find cabled ports too.
    Columns more than 1.12 pitches apart start a block, as in template_layout(); a gap of
    1.7 pitches or more could be one column missed inside a block, so then it can't tell."""
    seen = [q["box"] for q in photo_ports or [] if q["type"] == "rj45" and q.get("status") != "not seen"]
    if len(seen) < 8:
        return None
    w = float(np.median([b[2] for b in seen]))
    cols = []
    for x in sorted(b[0] + b[2] / 2 for b in seen):
        if cols and x - np.mean(cols[-1]) < 0.5 * w:
            cols[-1].append(x)
        else:
            cols.append([x])
    cols = [float(np.mean(c)) for c in cols]
    d = np.diff(cols)
    if not len(d) or not np.any(d < 1.6 * w):
        return None
    pitch = float(np.median(d[d < 1.6 * w]))
    if np.any(d >= 1.7 * pitch):
        return None
    blocks = [1]
    for g in d:
        if g > 1.12 * pitch:
            blocks.append(1)
        else:
            blocks[-1] += 1
    blocks = [n for n in blocks if n >= 2]
    return {"blocks": len(blocks), "seen": blocks} if len(blocks) >= 2 else None


def template_layout(meta):
    """RJ45 blocks of a template: columns per block, e.g. [4, 4, 4] or [6, 6]. Columns
    more than 1.12 port pitches apart start a new block (measured on 10 templates: 0.97-1.02
    inside a block, 1.24-2.02 between blocks). Single-column blocks (uplinks) are left out."""
    rj = [p["box"] for p in meta["ports"] if p["type"] == "rj45"]
    if len(rj) < 4:
        return []
    w = float(np.median([b[2] for b in rj]))
    xs = sorted(b[0] + b[2] / 2 for b in rj)
    cols = [xs[0]]
    for x in xs[1:]:
        if x - cols[-1] > 0.5 * w:
            cols.append(x)
    if len(cols) < 2:
        return [len(cols)]
    pitch = float(np.median(np.diff(cols)))
    blocks = [1]
    for d in np.diff(cols):
        if d > 1.12 * pitch:
            blocks.append(1)
        else:
            blocks[-1] += 1
    return [n for n in blocks if n >= 2]


def layout_match(tpl, photo_gray, photo_ports=None):
    """Does the template's port layout fit the photo's? Same model names can hide other
    hardware revisions (DGS-1024C: 3 blocks of 4, or 2 blocks of 6). Returns
    (verdict, text): False only when the photo rules the template out - more open
    columns in a block than the template's widest block, or more blocks than it has;
    None when the photo can't tell (fewer blocks seen can be a block under cables);
    True when blocks and columns agree."""
    ph = photo_layout_from_ports(photo_ports) if photo_ports else None
    return compare_layouts(ph or photo_layout(photo_gray), template_layout(tpl.meta))


def compare_layouts(ph, tb):
    """layout_match on a photo layout (photo_layout) and template blocks (template_layout)."""
    if ph is None or not tb:
        return None, ""
    desc = lambda bl: " + ".join(str(n) for n in bl)
    said = (f"template {len(tb)} blocks ({desc(tb)} columns), photo {ph['blocks']} blocks "
            f"({desc(ph['seen'])} open columns seen)")
    if max(ph["seen"]) > max(tb) or ph["blocks"] > len(tb):
        return False, f"port layout does not match: {said}. Another hardware revision of this model."
    if ph["blocks"] < len(tb):
        return None, f"port layout unclear: {said}; a block may be hidden by cables."
    return True, f"port layout matches: {said}."


def check_open_jacks(tpl, r, photo_gray):
    """Without the port models: every open jack seen in the photo must have a template
    port on it (r["open_jacks"], r["open_ok"]). A template that is shifted by a block,
    squeezed into part of the switch, or of another hardware revision leaves visible
    jacks uncovered. Judged only with 6+ open jacks seen."""
    if r is None:
        return r
    jacks = open_jacks(photo_gray)
    a = port_agreement(tpl, r["H"], jacks, photo_gray.shape)
    if a is None or a["detections"] < 6:
        return r
    r["open_jacks"], r["open_ok"] = a, a["covered"] >= PORTS_AGREE
    # patch matching leaves the RJ45 block out (cables), so it can sit a third of a port
    # off across the jacks: re-fit through the open jacks, keep it if they land better
    if a["covered"] >= 0.4:
        H2, n = refine_with_ports(tpl, r["H"], jacks)
        if n and plausible(tpl, H2, photo_gray.shape)[0]:
            a2 = port_agreement(tpl, H2, jacks, photo_gray.shape)
            if a2 and a2["covered"] >= a["covered"] and (a2["err"] or 1) < (a["err"] or 1):
                r = dict(r, H=H2, refined_pairs=n, open_jacks=a2, open_ok=a2["covered"] >= PORTS_AGREE)
    return r


def check_ports(tpl, r, photo_ports, photo_shape):
    """Attach the port check to an alignment dict (r["ports"], r["ports_ok"])."""
    if r is not None and photo_ports:
        a = port_agreement(tpl, r["H"], photo_ports, photo_shape)
        r["ports"], r["ports_ok"] = a, ports_agree(a)
    return r


def snap_to_ports(tpl, r, photo, ports=None):
    """Attach port detections to the alignment so the overlay boxes can be snapped onto
    the ports the photo actually shows (snap_polygons). The trained port models are used
    when available; otherwise the classical detector's openings. `ports`: detections
    already made for this photo (else they are detected here). When the detections share
    the template's layout, the alignment is also re-fitted through them."""
    import yolo_ports
    if r is None:
        return r
    if ports is None:
        if yolo_ports.available():
            ports, _ = yolo_ports.detect_ports(photo)
        else:
            # the classical detector's openings still anchor the per-box snap; grid-filled
            # positions are marked "not seen" so only openings actually seen are used
            import autodetect
            ports, _ = autodetect.detect_ports(photo, method="classical")
            ports = [dict(p, status="ok" if p.get("opening_visible", True) else "not seen")
                     for p in ports]
    if not ports:
        return r
    H2, n = refine_with_ports(tpl, r["H"], ports)
    out = (dict(r, H=H2, refined_pairs=n, photo_ports=ports)
           if (n and plausible(tpl, H2, photo.shape[:2])[0])
           else dict(r, photo_ports=ports))

    # Last: make the template's port block agree with the photo's in tilt and span.
    # refine_with_ports needs the two layouts to correspond, so it does nothing when
    # the photo shows only some of its ports - which is exactly when a fit arrives
    # undersized and offset. This measures the block as a whole instead. Kept only
    # when the boxes end up closer to the ports and none comes off one it was on.
    # Iterated: a badly scaled fit pairs only the few ports that happen to land near
    # their counterparts, so the first correction is partial - and a partial correction
    # brings more ports within reach of the next one. Two or three passes converge;
    # it stops as soon as a pass stops helping.
    for _ in range(4):
        H3, note = fit_port_block(tpl, out["H"], ports)
        if not note or not plausible(tpl, H3, photo.shape[:2])[0]:
            break
        b_res, a_res = port_residual(tpl, out["H"], ports), port_residual(tpl, H3, ports)
        b_on = port_agreement(tpl, out["H"], ports, photo.shape[:2])
        a_on = port_agreement(tpl, H3, ports, photo.shape[:2])
        if not (a_res is not None and b_res is not None and a_res < b_res - 1e-3
                and (not a_on or not b_on or a_on["on"] >= b_on["on"] - 1e-6)):
            break
        print("  %s; ports %.3f -> %.3f port widths off" % (note, b_res, a_res))
        out = dict(out, H=H3, block_fitted=note)

    # Everything above needs ports to have been *detected*. On a fully cabled panel the
    # opening detector finds almost none (4 of 24 on a DGS-1024C at 635 px wide), every
    # correction above declines, and whatever the chassis-driven fit produced ships as
    # is - which on that photo is a port block some 15 % short, right at port 1 and a
    # jack out by port 24. Matching the port pitch in the photo was tried here and
    # removed: a comb of identical jacks is periodic, so the scale it recovers is good
    # but the phase is not, and with too few detections to pin the phase it slides a
    # whole port as readily as it corrects one. The fix this needs is a detector that
    # finds cabled ports (connector bodies, not openings), which is what the RackTrack
    # port models do and the classical detector does not.
    return out


def port_residual(tpl, H, photo_ports):
    """Mean distance, in port widths, from each port the photo shows to the template
    port landing nearest it. Finer than port_agreement, which only asks whether a box
    is on a port at all and so cannot see a fit getting tighter."""
    seen = [p for p in photo_ports or [] if p.get("status") != "not seen"
            and p["type"] in ("rj45", "sfp")]
    tps = [p for p in tpl.meta["ports"] if p["type"] in ("rj45", "sfp")]
    if not seen or not tps:
        return None
    H = np.asarray(H, np.float64)
    centre = lambda b: (b[0] + b[2] / 2.0, b[1] + b[3] / 2.0)
    proj = cv2.perspectiveTransform(
        np.float32([[centre(p["box"])] for p in tps]), H).reshape(-1, 2).astype(np.float64)
    edges = np.float32([[[p["box"][0], p["box"][1]]] for p in tps]
                       + [[[p["box"][0] + p["box"][2], p["box"][1]]] for p in tps])
    e = cv2.perspectiveTransform(edges, H).reshape(-1, 2)
    pw = float(np.median(np.hypot(*(e[len(tps):] - e[:len(tps)]).T)))
    if not np.isfinite(pw) or pw <= 1:
        return None
    d = [min(float(np.hypot(*(q - np.array(centre(o["box"]))))) for q in proj) for o in seen]
    return float(np.mean(d)) / pw


def fit_port_block(tpl, H, photo_ports, max_scale_change=0.35, max_rot_deg=8.0):
    """Correct H by matching the port block's geometry - its tilt and its span -
    between the vendor image and the photo.

    The vendor image is a straight-on product shot; the photo almost never is. Feature
    matching gets the template close, but a couple of degrees of tilt and a scale error
    of a fifth survive it, and across a 24-port run that is several ports by the far
    end - the drift that reads as the overlay sitting undersized and off to one side.

    Span is measured between *corresponding* ports, never between the ends of each
    block. The photo only ever shows some of its ports, so its visible run is shorter
    than the block through no fault of the alignment; comparing that against the
    template's full width would shrink the overlay to match a measurement error.

    Returns (H, note); H unchanged when there is too little to measure or the
    correction implied is too large to believe."""
    seen = [p for p in photo_ports or [] if p.get("status") != "not seen"
            and p["type"] in ("rj45", "sfp")]
    tps = [p for p in tpl.meta["ports"] if p["type"] in ("rj45", "sfp")]
    if len(seen) < 4 or len(tps) < 4:
        return H, None
    H = np.asarray(H, np.float64)
    centre = lambda b: (b[0] + b[2] / 2.0, b[1] + b[3] / 2.0)
    proj = cv2.perspectiveTransform(
        np.float32([[centre(p["box"])] for p in tps]), H).reshape(-1, 2).astype(np.float64)
    obs = np.array([centre(p["box"]) for p in seen], np.float64)

    edges = np.float32([[[p["box"][0], p["box"][1]]] for p in tps]
                       + [[[p["box"][0] + p["box"][2], p["box"][1]]] for p in tps])
    e = cv2.perspectiveTransform(edges, H).reshape(-1, 2)
    pw = float(np.median(np.hypot(*(e[len(tps):] - e[:len(tps)]).T)))
    if not np.isfinite(pw) or pw <= 1:
        return H, None

    # nearest one-to-one pairing, so the fit below compares like with like. The radius
    # is generous: a badly scaled fit starts with its ports well away from the real ones.
    taken, src, dst = set(), [], []
    for o in obs:
        best, bd = None, 1.4 * pw
        for i, q in enumerate(proj):
            if i in taken:
                continue
            d = float(np.hypot(*(q - o)))
            if d < bd:
                best, bd = i, d
        if best is not None:
            taken.add(best)
            src.append(proj[best])
            dst.append(o)
    if len(src) < 4:
        return H, None

    M, inl = cv2.estimateAffinePartial2D(np.float32(src), np.float32(dst),
                                         method=cv2.RANSAC,
                                         ransacReprojThreshold=max(2.0, 0.35 * pw))
    if M is None or inl is None or int(inl.sum()) < 4:
        return H, None
    scale = float(np.hypot(M[0, 0], M[1, 0]))
    rot = float(np.arctan2(M[1, 0], M[0, 0]))
    if abs(scale - 1.0) > max_scale_change or abs(np.degrees(rot)) > max_rot_deg:
        return H, None
    A = np.vstack([M, [0, 0, 1]]).astype(np.float64)
    return A @ H, ("port block matched through %d ports: scale x%.3f, tilt %+.1f deg"
                   % (int(inl.sum()), scale, np.degrees(rot)))


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
    polys, status, shape = {}, {}, {}
    for p in tpl.meta["ports"]:
        x, y, w, h = p["box"]
        # the box is drawn at the size the vendor image measured it, not by mapping its
        # four corners separately: see _port_quad
        M, c = _jacobian(H, x + w / 2.0, y + h / 2.0)
        shape[p["id"]] = (p["box"], M, np.array(c, np.float64))
        polys[p["id"]] = _port_quad(p["box"], M, c)
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
        return polys, _named_status(tpl, polys, status, photo_ports, pw), 0
    # each box follows the detections around it rather than its own alone: a detected
    # box wraps the plug too where a cable is in, so single detections sit a little
    # high or low, and averaging over the neighbours evens that out
    sigma, cap = 1.2 * pw, 0.6 * pw
    A = np.array([[ax, ay] for ax, ay, _, _ in anchors], np.float64)
    B = A + np.array([[dx, dy] for _, _, dx, dy in anchors], np.float64)
    for pid in polys:
        box, M, c = shape[pid]
        cx, cy = c
        wts = np.exp(-((A[:, 0] - cx) ** 2 + (A[:, 1] - cy) ** 2) / (2 * sigma * sigma))
        if wts.sum() < 1e-9:
            continue
        # moving least squares: the shift and uniform scale that best map the nearby
        # anchors onto their detections, so a box also takes the local spacing of the
        # ports around it; falls back to the plain shift when too few anchors are near
        # or the fit asks for an unlikely scale. Only the centre and the scale are taken
        # from it - the box keeps the vendor image's width and height either way.
        sim = _local_similarity(A, B, wts)
        if sim is None:
            shift = (wts[:, None] * (B - A)).sum(axis=0) / wts.sum()
            norm = float(np.hypot(*shift))
            if norm > cap:
                shift *= cap / norm
            new_c, sc = c + shift, 1.0
        else:
            sc, ma, mb = sim
            new_c = sc * (c - ma) + mb
            move = new_c - c
            norm = float(np.hypot(*move))
            if norm > cap:
                new_c = c + move * cap / norm
        polys[pid] = _port_quad(box, sc * M, new_c)
    for p in tpl.meta["ports"]:
        if p["type"] in ("rj45", "sfp"):
            status.setdefault(p["id"], "not seen")
    return polys, _named_status(tpl, polys, status, photo_ports, pw), len(anchors)


def _named_status(tpl, polys, status, photo_ports, pw):
    """Console, management and USB ports take the cable status of the photo detection on them
    (any port type: the model may call a management jack RJ45), 'unknown' when none is there."""
    found = [q for q in photo_ports or [] if q.get("status") not in (None, "not seen")]
    for p in tpl.meta["ports"]:
        if p["type"] in ("rj45", "sfp"):
            continue
        cx, cy = polys[p["id"]].mean(axis=0)
        best, bd = None, 0.6 * pw
        for q in found:
            d = float(np.hypot(q["box"][0] + q["box"][2] / 2 - cx, q["box"][1] + q["box"][3] / 2 - cy))
            if d < bd:
                best, bd = q, d
        status[p["id"]] = best.get("status") if best is not None else "unknown"
    return status


def _local_similarity(A, B, w, max_scale=0.15):
    """Weighted shift + uniform scale A -> B (no rotation: that is the global fit's job,
    and two close rows of jittery boxes don't pin a rotation down). Returns
    (scale, centroid of A, centroid of B) so the caller can move a point with
    `sc * (p - ma) + mb`, or None when the weights rest on fewer than ~3 anchors or the
    scale is implausible."""
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
    return sc, ma, mb


def _jacobian(H, x, y):
    """The homography's local 2x2 linear part at (x, y): how a small rectangle sitting
    there is scaled, rotated and sheared by the fit. Returns (M, projected point)."""
    d = H[2, 0] * x + H[2, 1] * y + H[2, 2]
    if abs(d) < 1e-12:
        return np.eye(2), (float(x), float(y))
    u = (H[0, 0] * x + H[0, 1] * y + H[0, 2]) / d
    v = (H[1, 0] * x + H[1, 1] * y + H[1, 2]) / d
    M = np.array([[(H[0, 0] - u * H[2, 0]) / d, (H[0, 1] - u * H[2, 1]) / d],
                  [(H[1, 0] - v * H[2, 0]) / d, (H[1, 1] - v * H[2, 1]) / d]], np.float64)
    return M, (float(u), float(v))


def _port_quad(box, M, centre):
    """A port's four corners: the width and height measured on the vendor image, put
    through the 2x2 map M and placed at `centre`.

    Mapping the box's own corners through the homography instead gives a trapezoid that
    grows at one end of a tilted panel and shrinks at the other, so two identical jacks
    come out visibly different sizes and the numbers inside them do too. The size is a
    property of the hardware, read once off the product shot; only where the box sits,
    and how the panel as a whole is scaled and turned, come from the fit."""
    w, h = float(box[2]), float(box[3])
    rel = np.array([[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]], np.float64)
    return (rel @ np.asarray(M, np.float64).T + np.asarray(centre, np.float64)).astype(np.float32)


def align(tpl, photo_gray, debug=None, photo_ports=None, port_fallback=True):
    """Refine every coarse candidate; the one with the most RANSAC inliers wins.
    Pass a dict as `debug` to get the coarse candidates and the winner's matched points.
    With `photo_ports` (from the trained port models) the candidate whose ports land on
    the detected ports is preferred and the result is checked against them (r["ports_ok"]);
    with `port_fallback`, a failed match falls back to aligning by the ports themselves
    (align_by_ports)."""
    r = _align_patches(tpl, photo_gray, debug, photo_ports)
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
    if photo_ports and port_fallback and not passes(r):
        for fn in (align_by_ports, align_by_port_grid):
            rp = check_ports(tpl, fn(tpl, photo_ports, photo_gray.shape), photo_ports, photo_gray.shape)
            if rp is not None and passes(rp):
                if debug is not None:
                    debug["by_ports"] = True
                return rp
    if not photo_ports:
        r = check_open_jacks(tpl, r, photo_gray)
    if r is not None:
        r["layout_ok"], r["layout"] = layout_match(tpl, photo_gray, photo_ports)
    return r


def _align_patches(tpl, photo_gray, debug=None, photo_ports=None):
    best = None
    coarse = coarse_candidates(tpl, photo_gray) + feature_candidates(tpl, photo_gray)
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


# ----------------------------------------------------------------------------- drawing

def font(size, bold=True):
    names = ["DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"] if bold else ["DejaVuSans.ttf", "arial.ttf", "Arial.ttf"]
    for n in names:
        for base in ("", "/usr/share/fonts/truetype/dejavu/", "C:/Windows/Fonts/", "/Library/Fonts/"):
            try:
                return ImageFont.truetype(base + n, size)
            except OSError:
                pass
    return ImageFont.load_default()


def port_label_pos(port, H=None):
    x, y, w, h = port["box"]
    cx, cy = x + w / 2, y + h / 2
    # every number sits in the middle of its port; SFP numbers used to go under the
    # cage, which put a top-row cage's number on the cage below it
    if H is None:
        return cx, cy
    q = cv2.perspectiveTransform(np.float32([[[cx, cy]]]), H)[0, 0]
    return float(q[0]), float(q[1])


def draw_blueprint(tpl: Template):
    BG = (14, 52, 110)
    h, w = tpl.gray.shape
    bp = Image.new("RGB", (w, h + int(h * 0.08)), BG)
    d = ImageDraw.Draw(bp, "RGBA")
    g = max(10, w // 60)
    for x in range(0, bp.width, g):
        d.line([(x, 0), (x, bp.height)], fill=(255, 255, 255, 20))
    for y in range(0, bp.height, g):
        d.line([(0, y), (bp.width, y)], fill=(255, 255, 255, 20))
    e = cv2.dilate(tpl.edges, np.ones((2, 2), np.uint8))
    bp.paste(Image.new("RGB", (w, h), (230, 242, 255)), (0, 0), Image.fromarray(e))
    d = ImageDraw.Draw(bp)
    f = font(max(12, w // 75))
    for p in tpl.meta["ports"]:
        d.text(port_label_pos(p), str(p["id"]), font=f, fill=(255, 214, 90), anchor="mm",
               stroke_width=3, stroke_fill=BG)
    return bp


def draw_vendor_ports(tpl: Template, upscale=None):
    """The vendor image with every port boxed and numbered.

    This is the template as it was read off the product photo, before any fitting: what
    the port detector found and what number each port was given. Drawn exactly like the
    final overlay (identity homography instead of the fitted one), so the two images can
    be put side by side - if a port is missing or misnumbered here, it will be missing or
    misnumbered on the photo too, and this is where to see it."""
    if upscale is None:
        upscale = max(1.0, min(4.0, 1600 / tpl.bgr.shape[1]))
    return draw_overlay(tpl, tpl.bgr, np.eye(3), upscale)


def _box_scale(tpl):
    """How much each port kind's box may be inflated when drawn: {kind: factor}.

    PORT_BOX_SCALE is what the drawing wants; this is what the panel allows. Port
    spacing varies a lot between models - a DGS-1024C's boxes are 0.93 of the column
    pitch, a DES-1210-28P's are 1.06 and already overlap each other in the vendor image
    - so one global factor either leaves the roomy panels looking cramped or welds the
    tight ones into a single bar. Each kind is capped so PORT_BOX_GAP of the pitch
    survives in both directions, and the smaller of the two caps is used so the box
    keeps its shape."""
    out = {}
    for kind in {p["type"] for p in tpl.meta["ports"]}:
        boxes = [p["box"] for p in tpl.meta["ports"] if p["type"] == kind]
        if len(boxes) < 2:
            out[kind] = PORT_BOX_SCALE
            continue
        w = float(np.median([b[2] for b in boxes])) or 1.0
        h = float(np.median([b[3] for b in boxes])) or 1.0
        caps = []
        for vals, size in (([b[0] + b[2] / 2.0 for b in boxes], w),
                           ([b[1] + b[3] / 2.0 for b in boxes], h)):
            groups = []
            for v in sorted(vals):
                if groups and v - groups[-1][-1] < 0.45 * size:
                    groups[-1].append(v)
                else:
                    groups.append([v])
            centres = [float(np.mean(g)) for g in groups]
            if len(centres) < 2:
                continue                       # one row or one column: nothing to collide with
            pitch = float(np.median(np.diff(centres)))
            if np.isfinite(pitch) and pitch > 0:
                caps.append((1.0 - PORT_BOX_GAP) * pitch / size)
        out[kind] = float(min([PORT_BOX_SCALE] + caps))
    return out


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
    f = font(max(13, int(tpl.gray.shape[1] * scale / 72)))
    lw = max(2, int(round(upscale * 1.3)))
    # draw the dark fills and borders on a transparent layer, then composite, so each
    # port reads as a dark annotation box the number sits on, over any cable or panel
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    x, y, pw_, ph_ = tpl.meta.get("match_regions", [[0, 0, tpl.gray.shape[1], tpl.gray.shape[0]]])[0]
    panel = cv2.perspectiveTransform(np.float32([[[x, y]], [[x + pw_, y]], [[x + pw_, y + ph_]], [[x, y + ph_]]]), Hs).reshape(4, 2)
    ld.polygon([tuple(map(float, c)) for c in panel], outline=(0, 235, 255, 180), width=max(1, lw - 1))
    scales = _box_scale(tpl)
    quads = []
    for p in tpl.meta["ports"]:
        if polys and p["id"] in polys:
            q = polys[p["id"]] * upscale
        else:
            bx, by, bw, bh = p["box"]
            M, c = _jacobian(np.asarray(Hs, np.float64), bx + bw / 2.0, by + bh / 2.0)
            q = _port_quad(p["box"], M, c)       # vendor-image size, not a warped corner box
        c = q.mean(axis=0)
        q = c + (q - c) * scales.get(p["type"], PORT_BOX_SCALE)
        col = (0, 235, 255) if p["type"] == "rj45" else (255, 90, 230) if p["type"] == "sfp" else (255, 190, 0)
        pts = [tuple(map(float, k)) for k in q]
        ld.polygon(pts, fill=(0, 0, 0, 150), outline=col + (255,), width=lw)   # dark box + bright border
        quads.append((p, c))
    base = Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB")
    d = ImageDraw.Draw(base)
    for p, c in quads:
        d.text((float(c[0]), float(c[1])), str(p["id"]), font=f, fill=(255, 255, 255), anchor="mm",
               stroke_width=max(3, lw), stroke_fill=(0, 0, 0))
    return base


def draw_auto_overlay(photo_bgr, ports, upscale):
    """Photo with port outlines and numbers. With the trained port models: cyan = empty,
    orange = cable plugged in, dashed = not detected, filled in from the grid. With the
    classical detector: solid = open port seen, dashed = filled in from the grid."""
    h, w = photo_bgr.shape[:2]
    W, Hh = int(w * upscale), int(h * upscale)
    base = Image.fromarray(cv2.cvtColor(cv2.resize(photo_bgr, (W, Hh), interpolation=cv2.INTER_LANCZOS4), cv2.COLOR_BGR2RGB))
    pw = np.median([p["box"][2] for p in ports]) * upscale if ports else 20
    lw = max(2, int(pw / 18))
    f = font(max(12, int(pw * 0.5)))
    # dark fills on a transparent layer first, so numbers read on any background
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    centres = []
    for p in ports:
        x, y, bw, bh = [v * upscale for v in p["box"]]
        col = (0, 235, 255) if p["type"] == "rj45" else (255, 120, 220)
        if p.get("status") == "connected":
            col = (255, 150, 0)
        seen = p.get("status") in ("connected", "empty", "unknown") or \
            (p.get("status") is None and p["opening_visible"])
        if seen:
            ld.rectangle([x, y, x + bw, y + bh], fill=(0, 0, 0, 150), outline=col + (255,), width=lw)
        else:                                    # filled in from the grid (under a cable): dashed
            ld.rectangle([x, y, x + bw, y + bh], fill=(0, 0, 0, 110))
            dash = max(4, int(bw / 6))
            for x0, y0, x1, y1 in [(x, y, x + bw, y), (x, y + bh, x + bw, y + bh), (x, y, x, y + bh), (x + bw, y, x + bw, y + bh)]:
                n = max(1, int(max(abs(x1 - x0), abs(y1 - y0)) / dash))
                for i in range(0, n, 2):
                    a, b = i / n, min(1, (i + 1) / n)
                    ld.line([(x0 + (x1 - x0) * a, y0 + (y1 - y0) * a), (x0 + (x1 - x0) * b, y0 + (y1 - y0) * b)],
                            fill=col + (255,), width=lw)
        centres.append((p["id"], x + bw / 2, y + bh / 2))
    base = Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB")
    d = ImageDraw.Draw(base)
    for pid, cx, cy in centres:
        d.text((cx, cy), str(pid), font=f, fill=(255, 255, 255), anchor="mm",
               stroke_width=max(2, lw + 1), stroke_fill=(0, 0, 0))
    return base


def run_auto(photo, args, out, reason, vendor=None, expected=None):
    """No template: find and number the ports directly in the photo.

    vendor is recorded when the make was read but the model was not. expected is the
    model's port count when it is known - from its spec, from the vendor images, or
    from the digits in its name (TL-SG1016 is 16 ports). The detector numbers
    whatever it finds, which on a cabled panel runs past the port block or splits
    each port in two; a count to check against is what catches that."""
    out.mkdir(parents=True, exist_ok=True)
    print(f"{reason} Detecting ports directly in the photo.")
    ports, info = detect_ports(photo, args.numbering, expected=expected)
    if not ports:
        # nothing detectable (every port behind a cable, or too angled): still write the
        # photo as the overlay so there is always a result, with an on-image note
        print("No port grid found in the photo (every port may be behind a cable, or the "
              "photo is too angled). Writing the photo without an overlay.")
        up = max(1.0, min(3.0, 2400 / photo.shape[1]))
        big = cv2.resize(photo, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
        msg = "No ports could be detected - crop to the front panel, straight-on"
        cv2.putText(big, msg, (14, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4)
        cv2.putText(big, msg, (14, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1)
        Image.fromarray(cv2.cvtColor(photo, cv2.COLOR_BGR2RGB)).save(out / "1_original.png")
        Image.fromarray(cv2.cvtColor(big, cv2.COLOR_BGR2RGB)).save(out / "5_overlay.png")
        (out / "result.json").write_text(json.dumps(
            {"photo": str(Path(args.photo).resolve()), "mode": "none", "vendor": None,
             "model": None, "ports": [], "note": "no ports detected"}, indent=1))
        print(f"Wrote {out}/5_overlay.png (no overlay)")
        return
    rj, sf = info["rj45"], info["sfp"]
    models = info.get("detector") in ("RackTrack port models", "merged port model")
    hidden = sum(p.get("status") == "not seen" for p in ports) if models else sum(not p["opening_visible"] for p in ports)
    layout = (f'{info["rows"]} row{"s" if info["rows"] > 1 else ""}, blocks of '
              f'{" + ".join(str(b * info["rows"]) for b in info["blocks"])} ports' if rj else "no RJ45 ports found")
    if models:
        print(f"Found {rj} RJ45 + {sf} SFP ports with the RackTrack port models ({layout}); "
              f"{info.get('connected', 0)} with a cable, {hidden} filled in from the grid")
    else:
        print(f"Found {rj} RJ45 + {sf} SFP ports ({layout}); {hidden} filled in from the grid")
    if not rj:
        print("Warning: no RJ45 ports were visible (cables may cover them all); only SFP cages were mapped.")

    upscale = max(1.0, min(3.0, 4800 / photo.shape[1]))
    stage1 = Image.fromarray(cv2.cvtColor(photo, cv2.COLOR_BGR2RGB))
    stage2 = draw_auto_overlay(photo, ports, upscale)
    for stale in ("3_vendor.png", "3b_vendor_ports.png", "4_blueprint.png",
                  "5b_overlay_trace.png"):
        (out / stale).unlink(missing_ok=True)
    stage1.save(out / "1_original.png")
    stage2.save(out / "5_overlay.png")
    numbering = "1 top-left, 2 below it, 3 next column" if args.numbering == "column" else "top row first, then bottom row"
    flow = draw_flow(
        f"Switch port mapping (auto-detected) - {Path(args.photo).stem}",
        [("Original image", "Photo supplied", stage1),
         ("Make and model", None, None),
         ("Detected ports",
          ("Cyan: empty port. Orange: cable plugged in. Dashed: not detected, filled in from the grid. "
           if models else "Solid: open port seen. Dashed: filled in from the grid (usually under a cable). ")
          + f"Numbering: {numbering}",
          stage2)],
        ["Unknown model", f"{rj} RJ45 ports + {sf} SFP ports detected", layout],
    )
    flow.save(out / "flow.png")

    result = {
        "photo": str(Path(args.photo).resolve()),
        "mode": "auto", "vendor": vendor, "model": None, "numbering": args.numbering,
        "detection": info,
        "ports": [{"id": p["id"], "type": p["type"], "opening_visible": p["opening_visible"],
                   "status": p.get("status"),
                   "photo_polygon": [[round(x, 1), round(y, 1)] for x, y in
                                     [(p["box"][0], p["box"][1]), (p["box"][0] + p["box"][2], p["box"][1]),
                                      (p["box"][0] + p["box"][2], p["box"][1] + p["box"][3]),
                                      (p["box"][0], p["box"][1] + p["box"][3])]]}
                  for p in ports],
    }
    (out / "result.json").write_text(json.dumps(result, indent=1))
    print(f"Wrote {out}/flow.png and stage images + result.json")


def draw_flow(title, panels, model_lines):
    B, R = font, lambda s: font(s, bold=False)
    W, IW, PAD = 2400, 2200, 40
    BG, CARD, EDGE = (244, 246, 250), (255, 255, 255), (200, 208, 222)
    INK, SUB, ACC = (28, 34, 48), (95, 105, 125), (0, 120, 212)

    def fit(im):
        return im.resize((IW, round(im.height * IW / im.width)), Image.LANCZOS)

    panels = [(t, s, fit(im) if im is not None else None) for t, s, im in panels]
    HEAD, ARROW, MM_H, TOP = 110, 90, 230, 150
    heights = [HEAD + (MM_H if im is None else im.height) + PAD for _, _, im in panels]
    c = Image.new("RGB", (W, TOP + sum(heights) + ARROW * (len(panels) - 1) + 60), BG)
    d = ImageDraw.Draw(c)
    d.text((PAD + 60, 55), title, font=B(46), fill=INK)
    y = TOP
    for i, ((t, s, im), h) in enumerate(zip(panels, heights)):
        x0, x1 = PAD + 60, W - PAD - 60
        d.rounded_rectangle([x0, y, x1, y + h], 22, fill=CARD, outline=EDGE, width=3)
        cx, cy = x0 + 55, y + 55
        d.ellipse([cx - 32, cy - 32, cx + 32, cy + 32], fill=ACC)
        d.text((cx, cy), str(i + 1), font=B(34), fill="white", anchor="mm")
        d.text((cx + 60, cy - 20), t, font=B(36), fill=INK, anchor="lm")
        if s:
            d.text((cx + 60, cy + 22), s, font=R(26), fill=SUB, anchor="lm")
        if im is None:
            ty = y + HEAD + 10
            d.text((x0 + 60, ty), model_lines[0], font=B(64), fill=INK)
            d.text((x0 + 60, ty + 90), model_lines[1], font=R(34), fill=SUB)
            d.text((x0 + 60, ty + 145), model_lines[2], font=R(30), fill=INK)
        else:
            c.paste(im, ((W - IW) // 2, y + HEAD))
        y += h
        if i < len(panels) - 1:
            ax = W // 2
            d.line([(ax, y + 8), (ax, y + ARROW - 22)], fill=ACC, width=8)
            d.polygon([(ax - 22, y + ARROW - 30), (ax + 22, y + ARROW - 30), (ax, y + ARROW - 4)], fill=ACC)
            y += ARROW
    return c


# ----------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Identify a switch from a photo and map its ports.")
    ap.add_argument("photo")
    ap.add_argument("--templates", default=str(TEMPLATES))
    ap.add_argument("--out", default=None, help="output folder (default: output/<photo name>)")
    ap.add_argument("--model", default=None, help="force a template folder name, skip identification")
    ap.add_argument("--auto", action="store_true", help="skip templates, detect ports directly in the photo")
    ap.add_argument("--numbering", choices=["column", "row"], default="column",
                    help="auto mode: 'column' = 1 top-left, 2 below, 3 next column (default); "
                         "'row' = whole top row, then bottom row")
    args = ap.parse_args()

    photo = cv2.imread(args.photo, cv2.IMREAD_COLOR)
    if photo is None:
        sys.exit(f"Could not read {args.photo}")
    gray = cv2.cvtColor(photo, cv2.COLOR_BGR2GRAY)
    out = Path(args.out or OUTPUT / Path(args.photo).stem)
    out.mkdir(parents=True, exist_ok=True)

    if args.auto:
        return run_auto(photo, args, out, "Template matching skipped (--auto).")
    templates = load_templates(Path(args.templates))
    if args.model:
        templates = [t for t in templates if t.key == args.model]
        if not templates:
            sys.exit(f"Template '{args.model}' not found")

    results = []
    for t in templates:
        r = align(t, gray)
        if r:
            results.append((r["confidence"], t, r))
            print(f"  {t.label:30s} confidence {r['confidence']:.2f}  inliers {r['inliers']}")
    results.sort(key=lambda x: -x[0])
    if not results or not passes(results[0][2]):
        if args.model:
            sys.exit(f"Template '{args.model}' did not match this photo. Retake the photo "
                     "straight-on with the logo and LED panel in frame.")
        known = ", ".join(t.label for t in load_templates(Path(args.templates))) or "none"
        return run_auto(photo, args, out, f"No template matched (known models: {known}).")
    conf, tpl, r = results[0]
    print(f"Identified: {tpl.label} (confidence {conf:.2f})")
    r = snap_to_ports(tpl, r, photo)
    if r.get("refined_pairs"):
        print(f"  alignment re-fitted through {r['refined_pairs']} detected ports")
    render(args.photo, photo, tpl, r, out,
           candidates=[{"template": t.key, "confidence": round(c, 3)} for c, t, _ in results],
           how="Best match among the saved templates")


def render(photo_path, photo, tpl, r, out: Path, candidates=None, how=None):
    """Write the five stage images, flow.png and result.json."""
    out.mkdir(parents=True, exist_ok=True)
    H, conf = r["H"], r["confidence"]
    # longest side of the photo output ~4800px for crisp labels on small photos
    upscale = max(1.0, min(3.0, 4800 / photo.shape[1]))
    stage1 = Image.fromarray(cv2.cvtColor(photo, cv2.COLOR_BGR2RGB))
    stage3 = Image.fromarray(cv2.cvtColor(tpl.bgr, cv2.COLOR_BGR2RGB))
    stage3b = draw_vendor_ports(tpl)          # the same ports, numbered on the vendor image
    stage4 = draw_blueprint(tpl)
    # each box pulled onto the port the models detected (when they ran)
    polys, status, anchors = snap_polygons(tpl, H, r.get("photo_ports"))
    stage5 = draw_overlay(tpl, photo, H, upscale, polys)
    # the same fit with the template's own edges painted on. On a panel whose jacks are
    # all behind cables the numbered boxes alone cannot be judged - there is nothing
    # visible under them - but the logo, the label and the LED row can, and if those
    # land the ports do too. This is the picture to look at when an overlay is doubted.
    stage5b = draw_overlay(tpl, photo, H, upscale, polys, trace=True)
    stage1.save(out / "1_original.png")
    stage3.save(out / "3_vendor.png")
    stage3b.save(out / "3b_vendor_ports.png")
    stage4.save(out / "4_blueprint.png")
    stage5.save(out / "5_overlay.png")
    stage5b.save(out / "5b_overlay_trace.png")

    m = tpl.meta
    flow = draw_flow(
        f"Switch identification and port mapping - {Path(photo_path).stem}",
        [("Original image", "Photo supplied", stage1),
         ("Make and model", how, None),
         ("Vendor model image", m.get("image_source") or "Clean front view stored with the template", stage3),
         ("Ports found in the vendor image",
          f'{m.get("summary", "")} - numbering: {m.get("numbering_scheme", "top-odd")}', stage3b),
         ("Vendor image blueprint", "Edges traced from the vendor image, with port numbers", stage4),
         ("Original image with model outline",
          f"Vendor outline warped to the photo (confidence {conf:.0%}, {r['inliers']} matched points"
          + (f"; re-fitted through {r['refined_pairs']} detected ports" if r.get("refined_pairs") else "")
          + (f"; boxes snapped to {anchors} detected ports)" if anchors else ")"), stage5)],
        [f'{m["vendor"]} {m["model"]}', m.get("description", ""), m.get("summary", "")],
    )
    flow.save(out / "flow.png")

    ports = []
    for p in m["ports"]:
        entry = {**{k: v for k, v in p.items() if k != "box"},
                 "photo_polygon": [[round(float(a), 1), round(float(b), 1)] for a, b in polys[p["id"]]]}
        if p["id"] in status:
            entry["status"] = status[p["id"]]
        ports.append(entry)
    result = {
        "photo": str(Path(photo_path).resolve()),
        "mode": "template", "vendor": m["vendor"], "model": m["model"], "template": tpl.key,
        "identified_by": how,
        "template_auto_generated": bool(m.get("auto_generated")),
        "numbering_scheme": m.get("numbering_scheme"),
        "confidence": round(conf, 3), "matched_points": r["inliers"],
        "refined_with_ports": r.get("refined_pairs", 0),
        "snapped_to_ports": anchors,
        "candidates": candidates or [],
        "homography_template_to_photo": H.tolist(),
        "ports": ports,
    }
    (out / "result.json").write_text(json.dumps(result, indent=1))
    print(f"Wrote {out}/flow.png and stage images + result.json")


# The port overlay and per-box snap are provided by the switch_fit package (dark numbered
# boxes, moving-least-squares snap onto the detected ports). Rebinding the module names
# makes render() above use switch_fit's versions in place of the ones defined earlier.
try:
    import importlib as _il
    _switch_fit = _il.import_module("switch_fit.fit")   # the module, not the fit() function
    draw_overlay = _switch_fit.draw_overlay
    snap_polygons = _switch_fit.snap_polygons
except Exception as _sf_err:        # keep the built-in overlay if switch_fit is absent
    print(f"(switch_fit overlay not loaded: {_sf_err}; using the built-in overlay)")


if __name__ == "__main__":
    main()
