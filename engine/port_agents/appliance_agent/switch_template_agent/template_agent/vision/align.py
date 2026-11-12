"""Register the vendor front face onto the site photo (homography face -> photo).

Cables hide much of the panel, so several independent estimates are tried and
the one whose warped edges best agree with the photo's edges wins:
  * SIFT keypoints (logo, LED field, unplugged ports)  -> homography
  * OCR anchors: the same printed text in both images  -> similarity transform
  * fit-to-frame fallback (photo is a detector crop of the switch)
The winner is refined with ECC on gradient images when that improves the score.
Port boxes from the template are then projected through the homography, which
places every port in the photo, including those hidden behind cables.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .catalog import norm
from .ocr import OcrToken


@dataclass
class Alignment:
    H: np.ndarray            # 3x3, template-face pixels -> photo pixels
    method: str
    score: float             # edge NCC of the warped template vs the photo (-1..1)
    inliers: int = 0


def _gray(img):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(g)


def _grad(g):
    g = cv2.GaussianBlur(g.astype(np.float32), (3, 3), 0)
    m = cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    return cv2.GaussianBlur(m, (5, 5), 0)


def _scale_h(s: float) -> np.ndarray:
    return np.diag([s, s, 1.0])


def plausible(H: np.ndarray, face_shape, photo_shape) -> bool:
    fh, fw = face_shape[:2]
    ph, pw = photo_shape[:2]
    quad = cv2.perspectiveTransform(np.float32([[[0, 0]], [[fw, 0]], [[fw, fh]], [[0, fh]]]), H)[:, 0]
    if not cv2.isContourConvex(quad.astype(np.float32)):
        return False
    top, bot = np.linalg.norm(quad[1] - quad[0]), np.linalg.norm(quad[2] - quad[3])
    left, right = np.linalg.norm(quad[3] - quad[0]), np.linalg.norm(quad[2] - quad[1])
    if min(top, bot) < 0.3 * pw or max(top, bot) > 2.5 * pw:
        return False
    if max(top, bot) / min(top, bot) > 1.6 or max(left, right) / max(1e-6, min(left, right)) > 1.8:
        return False
    aspect = (top + bot) / max(1e-6, left + right)
    return 0.5 < aspect / (fw / fh) < 2.0


def width_coverage(H: np.ndarray, face_shape, photo_shape) -> float:
    """Width of the warped template panel as a fraction of the photo width."""
    fh, fw = face_shape[:2]
    q = cv2.perspectiveTransform(np.float32([[[0, fh / 2]], [[fw, fh / 2]]]), H)[:, 0]
    return float(abs(q[1, 0] - q[0, 0]) / photo_shape[1])


def score(H, face_g, photo_g, photo_grad=None) -> float:
    ph, pw = photo_g.shape
    warped = cv2.warpPerspective(face_g, H, (pw, ph))
    mask = cv2.warpPerspective(np.ones_like(face_g), H, (pw, ph)) > 0
    mask = cv2.erode(mask.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    if mask.sum() < 0.1 * ph * pw:
        return -1.0
    a = _grad(warped)[mask]
    b = (photo_grad if photo_grad is not None else _grad(photo_g))[mask]
    a, b = a - a.mean(), b - b.mean()
    d = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / d) if d > 0 else -1.0


# ------------------------------------------------------------------ estimators
def _sift_matches(face_g, photo_g):
    """Ratio-tested SIFT matches: (template points, photo points, template scale) or None."""
    # bring the template to roughly the photo's scale so keypoint scales overlap
    s = min(1.0, photo_g.shape[1] / face_g.shape[1] * 1.2)
    small = cv2.resize(face_g, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    sift = cv2.SIFT_create(nfeatures=4000, contrastThreshold=0.02)
    k1, d1 = sift.detectAndCompute(small, None)
    k2, d2 = sift.detectAndCompute(photo_g, None)
    if d1 is None or d2 is None or len(k1) < 8 or len(k2) < 8:
        return None
    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(d1, d2, k=2)
    good = [m for m, n in (p for p in pairs if len(p) == 2) if m.distance < 0.8 * n.distance]
    if len(good) < 8:
        return None
    src = np.float32([k1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([k2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    return src, dst, s


def by_sift(face_g, photo_g) -> tuple[np.ndarray | None, int]:
    """Full homography from SIFT matches (8 parameters: handles perspective)."""
    m = _sift_matches(face_g, photo_g)
    if m is None:
        return None, 0
    src, dst, s = m
    H, inl = cv2.findHomography(src, dst, cv2.USAC_MAGSAC, 4.0, maxIters=5000)
    if H is None:
        return None, 0
    return H @ _scale_h(s), int(inl.sum())


def by_sift_affine(face_g, photo_g) -> tuple[np.ndarray | None, int]:
    """Affine fit from the same matches (6 parameters). Steadier than a full
    homography when the matches cluster in one area of a near-frontal photo,
    where the homography's extra perspective terms overfit."""
    m = _sift_matches(face_g, photo_g)
    if m is None:
        return None, 0
    src, dst, s = m
    A, inl = cv2.estimateAffine2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=4.0,
                                  maxIters=5000, confidence=0.995)
    if A is None:
        return None, 0
    return np.vstack([A, [0, 0, 1]]) @ _scale_h(s), int(inl.sum())


def best_feature_fit(face_shape, photo_shape, face_g, photo_g) -> tuple[np.ndarray | None, int]:
    """The homography if it is plausible, else the affine fit if that is: (H, inliers)."""
    for fit in (by_sift, by_sift_affine):
        H, n = fit(face_g, photo_g)
        if H is not None and plausible(H, face_shape, photo_shape):
            return H, n
    return None, 0


def by_ocr_anchors(face_tokens: list[OcrToken], photo_tokens: list[OcrToken]) -> tuple[np.ndarray | None, int]:
    def key(t):
        return norm(t.text)
    # only text that is unique in both images can anchor
    fc, pc = {}, {}
    for t in face_tokens:
        fc.setdefault(key(t), []).append(t)
    for t in photo_tokens:
        if t.box.any():
            pc.setdefault(key(t), []).append(t)
    src, dst = [], []
    for k, ft in fc.items():
        if len(k) < 2 or len(ft) != 1 or len(pc.get(k, [])) != 1:
            continue
        src.append(ft[0].box.mean(0))
        dst.append(pc[k][0].box.mean(0))
    if len(src) < 3:
        return None, len(src)
    M, inl = cv2.estimateAffinePartial2D(np.float32(src), np.float32(dst), method=cv2.RANSAC,
                                         ransacReprojThreshold=6.0)
    if M is None or inl.sum() < 3:
        return None, 0
    return np.vstack([M, [0, 0, 1]]), int(inl.sum())


def by_frame(face_shape, photo_shape) -> np.ndarray:
    """Assume the photo is a crop of the switch: fit the face to the frame width."""
    fh, fw = face_shape[:2]
    ph, pw = photo_shape[:2]
    s = pw / fw
    H = _scale_h(s)
    H[1, 2] = (ph - fh * s) / 2
    return H


def refine_ecc(H, face_g, photo_g, iters=200) -> np.ndarray | None:
    """ECC on gradient images, run at the photo's scale (face pre-warped by H)."""
    ph, pw = photo_g.shape
    tmpl = cv2.warpPerspective(face_g, H, (pw, ph))
    mask = (cv2.warpPerspective(np.ones_like(face_g), H, (pw, ph)) > 0).astype(np.uint8)
    a, b = _grad(tmpl), _grad(photo_g)
    a /= a.max() or 1
    b /= b.max() or 1
    W = np.eye(3, dtype=np.float32)
    try:
        # photo is the ECC "template", the pre-warped face is the masked "input"
        _, W = cv2.findTransformECC(b, a, W, cv2.MOTION_HOMOGRAPHY,
                                    (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, iters, 1e-5),
                                    mask, 5)
    except cv2.error:
        return None
    # W maps photo coords -> pre-warped face coords; compose to face -> photo
    return np.linalg.inv(W) @ H


def align(face: np.ndarray, photo: np.ndarray,
          face_tokens: list[OcrToken], photo_tokens: list[OcrToken]) -> tuple[Alignment, list[Alignment]]:
    face_g, photo_g = _gray(face), _gray(photo)
    pgrad = _grad(photo_g)
    cands: list[Alignment] = []

    H, n = by_sift(face_g, photo_g)
    if H is not None:
        cands.append(Alignment(H, "sift", 0, n))
    H, n = by_sift_affine(face_g, photo_g)
    if H is not None:
        cands.append(Alignment(H, "sift_affine", 0, n))
    H, n = by_ocr_anchors(face_tokens, photo_tokens)
    if H is not None:
        cands.append(Alignment(H, "ocr_anchors", 0, n))
    cands.append(Alignment(by_frame(face.shape, photo.shape), "frame_fit", 0))

    for c in list(cands):
        if not plausible(c.H, face.shape, photo.shape):
            c.score = -1
            continue
        c.score = score(c.H, face_g, photo_g, pgrad)
        R = refine_ecc(c.H, face_g, photo_g)
        if R is not None and plausible(R, face.shape, photo.shape):
            s = score(R, face_g, photo_g, pgrad)
            if s > c.score + 0.005:
                cands.append(Alignment(R, c.method + "+ecc", s, c.inliers))
    best = max(cands, key=lambda c: c.score)
    return best, cands


# ------------------------------------------------------------------ outputs
def project_ports(polys: dict[int, np.ndarray], H: np.ndarray) -> dict[int, np.ndarray]:
    return {k: cv2.perspectiveTransform(p.reshape(-1, 1, 2), H)[:, 0] for k, p in polys.items()}


def _port_groups(polys: dict[int, np.ndarray]) -> list[list[int]]:
    """Physical port blocks: ports whose columns are separated by less than half a port width."""
    items = sorted(polys, key=lambda k: polys[k][:, 0].min())
    pw = np.median([np.ptp(polys[k][:, 0]) for k in items])
    groups, right = [], -1e9
    for k in items:
        x0 = polys[k][:, 0].min()
        if x0 - right > 0.5 * pw:
            groups.append([])
        groups[-1].append(k)
        right = max(right, polys[k][:, 0].max())
    return groups


def snap_ports(face: np.ndarray, photo: np.ndarray, H: np.ndarray,
               face_polys: dict[int, np.ndarray], min_peak=0.22, min_gain=0.08
               ) -> tuple[dict[int, np.ndarray], dict[int, tuple[float, float]]]:
    """Local correction on top of the global homography.

    Real photos are rarely a perfect homography of the product shot (lens distortion,
    stitched/stretched crops, bent panels), so each port block is shifted on its own
    to where its edges best match the photo. Hidden ports move with their block, so
    their position still comes from the vendor geometry."""
    ph, pw = photo.shape[:2]
    face_g = _gray(face)
    W = _grad(cv2.warpPerspective(face_g, H, (pw, ph)))
    M = cv2.warpPerspective(np.ones_like(face_g), H, (pw, ph)) > 0
    P = _grad(_gray(photo))
    polys = project_ports(face_polys, H)
    shifts = {k: (0.0, 0.0) for k in polys}

    for group in _port_groups(face_polys):  # blocks from the clean vendor image
        pts = np.vstack([polys[k] for k in group])
        x0, y0 = pts.min(0)
        x1, y1 = pts.max(0)
        pad = 0.25 * (y1 - y0)
        x0, y0, x1, y1 = int(x0 - pad), int(y0 - pad), int(x1 + pad), int(y1 + pad)
        if x0 < 0 or y0 < 0 or x1 > pw or y1 > ph or not M[y0:y1, x0:x1].all():
            continue  # block (partly) outside the photo: keep the global estimate
        # mostly horizontal search: vertical error is small on a 1U panel, and a
        # tall search lets cable-covered blocks lock onto the wrong row
        rx, ry = int(0.04 * pw) + 2, int(0.1 * (y1 - y0)) + 2
        sx0, sy0 = max(0, x0 - rx), max(0, y0 - ry)
        sx1, sy1 = min(pw, x1 + rx), min(ph, y1 + ry)
        tmpl, search = W[y0:y1, x0:x1], P[sy0:sy1, sx0:sx1]
        if tmpl.std() < 1e-3 or search.shape[0] <= tmpl.shape[0] or search.shape[1] <= tmpl.shape[1]:
            continue
        r = cv2.matchTemplate(search, tmpl, cv2.TM_CCOEFF_NORMED)
        _, peak, _, (bx, by) = cv2.minMaxLoc(r)
        zero = r[y0 - sy0, x0 - sx0]
        if peak >= min_peak and peak - zero >= min_gain:
            d = (float(bx + sx0 - x0), float(by + sy0 - y0))
            for k in group:
                shifts[k] = d
    return {k: polys[k] + np.float32(shifts[k]) for k in polys}, shifts


def port_visibility(face: np.ndarray, photo: np.ndarray, H: np.ndarray,
                    photo_polys: dict[int, np.ndarray]) -> dict[int, float]:
    """Similarity of each port region between photo and warped template (NCC, -1..1).
    High = port looks like the empty vendor port; low = cable plugged in or hidden."""
    ph, pw = photo.shape[:2]
    warped = cv2.cvtColor(cv2.warpPerspective(face, H, (pw, ph)), cv2.COLOR_BGR2GRAY).astype(np.float32)
    pg = cv2.cvtColor(photo, cv2.COLOR_BGR2GRAY).astype(np.float32)
    out = {}
    for k, poly in photo_polys.items():
        m = np.zeros((ph, pw), np.uint8)
        cv2.fillPoly(m, [poly.round().astype(np.int32)], 1)
        sel = m > 0
        if sel.sum() < 12:
            out[k] = float("nan")   # port lies outside the photo
            continue
        a, b = warped[sel] - warped[sel].mean(), pg[sel] - pg[sel].mean()
        d = np.sqrt((a * a).sum() * (b * b).sum())
        out[k] = float((a * b).sum() / d) if d > 0 else 0.0
    return out


def outline_overlay(face: np.ndarray, photo: np.ndarray, H: np.ndarray,
                    photo_polys: dict[int, np.ndarray], visibility: dict[int, float]) -> np.ndarray:
    ph, pw = photo.shape[:2]
    scale = max(1, round(900 / ph)) if ph < 450 else 1   # draw at a readable size
    big = cv2.resize(photo, (pw * scale, ph * scale), interpolation=cv2.INTER_CUBIC)
    S = _scale_h(scale)
    Hs = S @ H
    edges = cv2.Canny(cv2.GaussianBlur(_gray(face), (3, 3), 0), 40, 120)
    edges = cv2.dilate(edges, np.ones((2, 2), np.uint8))
    we = cv2.warpPerspective(edges, Hs, (big.shape[1], big.shape[0]))
    out = (big * 0.75).astype(np.uint8)
    out[we > 0] = (255, 230, 40)  # cyan (BGR)
    for k, poly in photo_polys.items():
        p = (poly * scale).round().astype(np.int32)
        v = visibility.get(k, 0)
        color = (80, 255, 80) if v >= 0.35 else (60, 170, 255)  # green empty / orange occupied
        cv2.polylines(out, [p], True, color, 2, cv2.LINE_AA)
        c = p.mean(0)
        s = max(0.45, (p[:, 1].max() - p[:, 1].min()) / 40)
        t = str(k)
        (tw, th), _ = cv2.getTextSize(t, cv2.FONT_HERSHEY_SIMPLEX, s, 2)
        org = (int(c[0] - tw / 2), int(c[1] + th / 2))
        cv2.putText(out, t, org, cv2.FONT_HERSHEY_SIMPLEX, s, (0, 0, 0), 5, cv2.LINE_AA)
        cv2.putText(out, t, org, cv2.FONT_HERSHEY_SIMPLEX, s, (255, 255, 255), 2, cv2.LINE_AA)
    return out
