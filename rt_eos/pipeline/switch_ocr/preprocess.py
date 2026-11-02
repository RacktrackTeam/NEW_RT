"""Image preprocessing variants.

Each variant is OCR'd independently and the results are merged. This is the
main defence against blur, noise, glare and tiny text: a reading that fails
on the raw photo is often recovered on the contrast-enhanced or upscaled
copy.

Every function returns ``(image_bgr, scale)`` where *scale* maps variant
coordinates back to the original image (``original_xy = variant_xy / scale``).
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from .config import OCRConfig

log = logging.getLogger("switch_ocr")

# Guard against pathological inputs (panoramas, scans) exhausting memory.
_HARD_MAX_SIDE = 6000


def load_image(path: str) -> np.ndarray:
    """Read an image as BGR, honouring EXIF orientation, tolerating unicode paths."""
    data = np.fromfile(path, dtype=np.uint8)  # works on paths cv2.imread chokes on
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Could not decode image: {path}")
    img = _apply_exif_orientation(path, img)
    h, w = img.shape[:2]
    longest = max(h, w)
    if longest > _HARD_MAX_SIDE:
        f = _HARD_MAX_SIDE / longest
        img = cv2.resize(img, (int(w * f), int(h * f)), interpolation=cv2.INTER_AREA)
        log.debug("Downscaled oversized image %s by %.2f", path, f)
    return img


def _apply_exif_orientation(path: str, img: np.ndarray) -> np.ndarray:
    """Rotate according to EXIF so phone photos are upright."""
    try:
        from PIL import Image, ImageOps  # local import: Pillow is a dependency anyway

        with Image.open(path) as pil:
            exif = pil.getexif()
            orientation = exif.get(274, 1)  # 274 = Orientation tag
        if orientation == 1:
            return img
        rotations = {
            2: lambda m: cv2.flip(m, 1),
            3: lambda m: cv2.rotate(m, cv2.ROTATE_180),
            4: lambda m: cv2.flip(m, 0),
            5: lambda m: cv2.flip(cv2.rotate(m, cv2.ROTATE_90_CLOCKWISE), 1),
            6: lambda m: cv2.rotate(m, cv2.ROTATE_90_CLOCKWISE),
            7: lambda m: cv2.flip(cv2.rotate(m, cv2.ROTATE_90_COUNTERCLOCKWISE), 1),
            8: lambda m: cv2.rotate(m, cv2.ROTATE_90_COUNTERCLOCKWISE),
        }
        fn = rotations.get(int(orientation))
        return fn(img) if fn else img
    except Exception:  # missing/corrupt EXIF must never kill the pipeline
        return img


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def estimate_noise(gray: np.ndarray) -> float:
    """Fast noise sigma estimate (Immerkær's method, Laplacian-based)."""
    h, w = gray.shape[:2]
    if h < 8 or w < 8:
        return 0.0
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    return float(np.median(np.abs(lap))) / 1.1926  # MAD -> sigma for this kernel


def _denoise_if_noisy(l_chan: np.ndarray, cfg: OCRConfig) -> np.ndarray:
    """Denoise only when the image actually is noisy — CLAHE and sharpening
    amplify sensor noise into text-shaped garbage, but denoising a clean
    image just wastes time and softens strokes."""
    if cfg.denoise_strength <= 0:
        return l_chan
    sigma = estimate_noise(l_chan)
    if sigma < 2.5:
        return l_chan
    h = min(int(round(cfg.denoise_strength + max(0.0, sigma - 5.0))), 17)
    return cv2.fastNlMeansDenoising(
        l_chan, None, h=h, templateWindowSize=7, searchWindowSize=21
    )


def _unsharp(img: np.ndarray, amount: float, sigma: float = 2.0) -> np.ndarray:
    if amount <= 0:
        return img
    blur = cv2.GaussianBlur(img, (0, 0), sigmaX=sigma)
    return cv2.addWeighted(img, 1.0 + amount, blur, -amount, 0)


def _upscale_factor(h: int, w: int, cfg: OCRConfig) -> float:
    """How much to enlarge: enough that the longest side reaches
    ``upscale_target`` AND the shortest side reaches ``upscale_min_side``
    (rescues wide rack-strip crops), capped at ``max_upscale``."""
    by_long = cfg.upscale_target / max(h, w)
    by_short = cfg.upscale_min_side / min(h, w)
    return min(cfg.max_upscale, max(by_long, by_short, 1.0))


# --------------------------------------------------------------------------- #
# Variants
# --------------------------------------------------------------------------- #
def variant_original(img: np.ndarray, cfg: OCRConfig) -> Tuple[np.ndarray, float]:
    """The photo as-is."""
    return img, 1.0


def variant_enhanced(img: np.ndarray, cfg: OCRConfig) -> Tuple[np.ndarray, float]:
    """Contrast + clarity rescue for low-quality photos.

    Denoise (adaptive) -> CLAHE (local contrast) -> unsharp mask, on the
    luminance channel only, so colours (used by the detector) survive.
    Denoising must come FIRST: CLAHE amplifies noise as happily as text.
    """
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l_chan, a_chan, b_chan = cv2.split(lab)

    l_chan = _denoise_if_noisy(l_chan, cfg)
    clahe = cv2.createCLAHE(clipLimit=cfg.clahe_clip, tileGridSize=(8, 8))
    l_chan = clahe.apply(l_chan)
    l_chan = _unsharp(l_chan, cfg.sharpen_amount)

    out = cv2.cvtColor(cv2.merge([l_chan, a_chan, b_chan]), cv2.COLOR_LAB2BGR)
    return out, 1.0


def variant_upscaled(img: np.ndarray, cfg: OCRConfig) -> Tuple[np.ndarray, float]:
    """Enlarge so tiny text (port numbers, serials) has enough pixels.

    Adaptive denoise (at original size, where it's cheap and noise isn't yet
    magnified) -> Lanczos resample -> mild sharpen. The scale factor is
    returned so detections map back to original coordinates.
    """
    h, w = img.shape[:2]
    scale = _upscale_factor(h, w, cfg)
    if scale <= 1.05:  # already big enough — enhance instead of upscale
        return variant_enhanced(img, cfg)

    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l_chan, a_chan, b_chan = cv2.split(lab)
    l_chan = _denoise_if_noisy(l_chan, cfg)
    img = cv2.cvtColor(cv2.merge([l_chan, a_chan, b_chan]), cv2.COLOR_LAB2BGR)

    up = cv2.resize(img, (int(round(w * scale)), int(round(h * scale))),
                    interpolation=cv2.INTER_LANCZOS4)
    up = _unsharp(up, 0.6, sigma=1.5)
    return up, scale


def variant_binarized(img: np.ndarray, cfg: OCRConfig) -> Tuple[np.ndarray, float]:
    """Auto-polarity Otsu threshold, full frame.

    Rescues glare, washed-out printing and white-on-dark chassis labels
    that survive contrast enhancement but still trip up the recognizer on
    raw grey levels — a hard binary pass sometimes reads what a
    grey-level pass cannot.
    """
    return otsu_binarize(img), 1.0


def variant_gamma(img: np.ndarray, cfg: OCRConfig) -> Tuple[np.ndarray, float]:
    """Auto gamma correction toward mid-grey.

    Rescues silkscreen text lost in deep shadow (dim rack photos) or
    blown out by camera flash glare — cases CLAHE's local contrast alone
    doesn't fully recover because the whole frame is off-exposure, not
    just low-contrast.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mean = float(np.clip(gray.mean(), 1.0, 254.0))
    gamma = float(np.clip(np.log(0.5) / np.log(mean / 255.0), 0.35, 3.0))
    table = (np.linspace(0, 1, 256, dtype=np.float64) ** (1.0 / gamma) * 255).astype(np.uint8)
    return cv2.LUT(img, table), 1.0


_VARIANTS = {
    "original": variant_original,
    "enhanced": variant_enhanced,
    "upscaled": variant_upscaled,
    "binarized": variant_binarized,
    "gamma": variant_gamma,
}


# --------------------------------------------------------------------------- #
# Auto-deskew (fine rotation, data-driven angle)
# --------------------------------------------------------------------------- #
def estimate_skew_angle(img: np.ndarray, max_angle: float = 20.0) -> float:
    """Estimate the dominant tilt of text/edge lines via Hough voting.

    Unlike the fixed-angle rotate retry, this picks the ACTUAL angle
    (e.g. -12.3 deg) instead of guessing from a small discrete set.
    Returns degrees to rotate (matching :func:`rotate_image`'s
    convention), or ``0.0`` when there isn't enough line evidence to
    trust — callers must skip the pass rather than rotate on a guess.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    short = min(gray.shape[:2])
    lines = cv2.HoughLinesP(edges, 1, np.pi / 360, threshold=max(30, short // 20),
                            minLineLength=max(20, short // 6), maxLineGap=8)
    if lines is None:
        return 0.0
    angles = []
    for x1, y1, x2, y2 in lines[:, 0]:
        dx, dy = x2 - x1, y2 - y1
        if dx == 0 and dy == 0:
            continue
        angle = float(np.degrees(np.arctan2(dy, dx)))
        if angle > 90:
            angle -= 180
        elif angle < -90:
            angle += 180
        # Fold near-vertical lines (device edges, port columns) into the
        # same bucket as near-horizontal ones: a single rotation shifts
        # both by the same amount.
        if abs(angle) <= max_angle:
            angles.append(angle)
        elif angle >= 90 - max_angle:
            angles.append(angle - 90)
        elif angle <= -(90 - max_angle):
            angles.append(angle + 90)
    if len(angles) < 3:
        return 0.0
    angle = float(np.median(angles))
    return angle if abs(angle) >= 0.5 else 0.0


# --------------------------------------------------------------------------- #
# Perspective correction (angled photos of the device body)
# --------------------------------------------------------------------------- #
def _order_quad_points(pts: np.ndarray) -> np.ndarray:
    """Sort 4 points into [top-left, top-right, bottom-right, bottom-left]."""
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    tl, br = pts[np.argmin(s)], pts[np.argmax(s)]
    tr, bl = pts[np.argmin(d)], pts[np.argmax(d)]
    return np.array([tl, tr, br, bl], dtype=np.float32)


def find_device_quad(img: np.ndarray) -> Optional[np.ndarray]:
    """Locate the largest plausible 4-corner outline (device body or
    faceplate) for perspective correction.

    Returns 4 points or ``None`` when no confident quad is found —
    callers must skip the pass rather than warp on a guess.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 40, 120)
    edges = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    h, w = img.shape[:2]
    img_area = float(h * w)
    best, best_area = None, 0.0
    for c in contours:
        peri = cv2.arcLength(c, True)
        if peri < 0.2 * (h + w):  # too small to be the device outline
            continue
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        area = cv2.contourArea(approx)
        if area < 0.15 * img_area or area > 0.97 * img_area:
            continue  # whole-frame or noise-speck contours are useless
        if area > best_area:
            best_area, best = area, approx.reshape(4, 2).astype(np.float32)
    return best


def perspective_correct(img: np.ndarray, quad: np.ndarray):
    """Warp a detected quad to a fronto-parallel rectangle.

    Returns ``(warped, inverse_homography_3x3)``; map detections back with
    homogeneous coords: ``mapped = ([x, y, 1] @ inv.T)``, then divide the
    first two components by the third.
    """
    pts = _order_quad_points(quad)
    tl, tr, br, bl = pts
    w1, w2 = np.linalg.norm(br - bl), np.linalg.norm(tr - tl)
    h1, h2 = np.linalg.norm(tr - br), np.linalg.norm(tl - bl)
    max_w, max_h = int(max(w1, w2)), int(max(h1, h2))
    if max_w < 40 or max_h < 20:
        return None, None
    dst = np.array([[0, 0], [max_w - 1, 0], [max_w - 1, max_h - 1], [0, max_h - 1]],
                   dtype=np.float32)
    m = cv2.getPerspectiveTransform(pts, dst)
    warped = cv2.warpPerspective(img, m, (max_w, max_h), borderValue=(127, 127, 127))
    return warped, np.linalg.inv(m)


# --------------------------------------------------------------------------- #
# Zoom-and-retry ("zoom and see, enhance and see")
# --------------------------------------------------------------------------- #
def plan_zoom_regions(img_shape, detections, cfg: OCRConfig) -> List[List[int]]:
    """Choose regions worth a magnified second look.

    Around every first-pass detection (model numbers live NEXT TO the text
    we already found — usually right below the logo), generously expanded.
    If the first pass found almost nothing, fall back to overlapping bands
    across the faceplate. Overlapping regions are merged; the best
    ``cfg.zoom_max_regions`` are returned.
    """
    h, w = img_shape[:2]
    proposals: List[Tuple[float, List[int]]] = []  # (priority, box)

    for d in detections:
        x1, y1, x2, y2 = d.box
        bh = max(10, y2 - y1)
        mx, my = int(3.5 * bh), int(2.5 * bh)
        box = [max(0, x1 - mx), max(0, y1 - my), min(w, x2 + mx), min(h, y2 + my)]
        if box[2] - box[0] < 24 or box[3] - box[1] < 16:
            continue
        proposals.append((float(d.confidence), box))

    if len(detections) < 3:  # nearly blind first pass -> sweep in bands
        if w >= 2.5 * h:  # rack-strip shaped: overlapping horizontal thirds
            step = w // 3
            for i in range(3):
                x1 = max(0, i * step - step // 4)
                proposals.append((0.1, [x1, 0, min(w, (i + 1) * step + step // 4), h]))
        else:  # quadrants with overlap
            for qx in (0, 1):
                for qy in (0, 1):
                    proposals.append((0.1, [max(0, qx * w // 2 - w // 8),
                                            max(0, qy * h // 2 - h // 8),
                                            min(w, (qx + 1) * w // 2 + w // 8),
                                            min(h, (qy + 1) * h // 2 + h // 8)]))

    # Merge overlapping proposals (union), keep highest priority.
    proposals.sort(key=lambda p: p[0], reverse=True)
    merged: List[Tuple[float, List[int]]] = []
    for prio, box in proposals:
        absorbed = False
        for i, (mp, mb) in enumerate(merged):
            ix = max(0, min(box[2], mb[2]) - max(box[0], mb[0]))
            iy = max(0, min(box[3], mb[3]) - max(box[1], mb[1]))
            inter = ix * iy
            smaller = min((box[2]-box[0])*(box[3]-box[1]), (mb[2]-mb[0])*(mb[3]-mb[1]))
            if smaller > 0 and inter / smaller > 0.4:
                merged[i] = (max(mp, prio),
                             [min(mb[0], box[0]), min(mb[1], box[1]),
                              max(mb[2], box[2]), max(mb[3], box[3])])
                absorbed = True
                break
        if not absorbed:
            merged.append((prio, box))

    full_area = float(w * h)
    out = [b for _, b in merged
           if (b[2]-b[0]) * (b[3]-b[1]) < 0.85 * full_area]  # whole image = pointless
    return out[: cfg.zoom_max_regions]


def rotate_image(img: np.ndarray, angle: float):
    """Rotate around the centre, expanding the canvas so nothing is cut.

    Returns ``(rotated, inverse_affine_2x3)``; map detections back with
    ``original_xy = [x, y, 1] @ inverse.T``.
    """
    h, w = img.shape[:2]
    centre = (w / 2.0, h / 2.0)
    m = cv2.getRotationMatrix2D(centre, angle, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    m[0, 2] += nw / 2.0 - centre[0]
    m[1, 2] += nh / 2.0 - centre[1]
    rotated = cv2.warpAffine(img, m, (nw, nh), flags=cv2.INTER_LINEAR,
                             borderValue=(127, 127, 127))
    return rotated, cv2.invertAffineTransform(m)


def _interior_blowout_mask(gray: np.ndarray) -> np.ndarray:
    """Mask of blown-out blobs that do NOT touch the image border.

    A plain white/light studio background behind a product photo is also
    "blown out" by a raw brightness threshold but always touches the
    frame edge; a genuine flash highlight on the device body sits fully
    inside the frame, surrounded by non-white pixels. Keeping only
    border-free blobs is what keeps this from mistaking a normal product
    photo's background for glare.
    """
    _, mask = cv2.threshold(gray, 248, 255, cv2.THRESH_BINARY)
    h, w = mask.shape
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    keep = np.zeros_like(mask)
    for i in range(1, n):  # label 0 is the background of the mask itself
        x, y, bw, bh, area = stats[i]
        touches_border = x <= 0 or y <= 0 or x + bw >= w or y + bh >= h
        if not touches_border:
            keep[labels == i] = 255
    return keep


def glare_fraction(img: np.ndarray) -> float:
    """Fraction of the frame covered by INTERIOR blown-out blobs (genuine
    highlights, not background) — cheap check run BEFORE paying for an
    inpaint + OCR pass, so clean photos never pay for glare repair they
    don't need."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mask = _interior_blowout_mask(gray)
    return float(cv2.countNonZero(mask)) / mask.size


def deglare(img: np.ndarray, cfg: OCRConfig) -> np.ndarray:
    """Inpaint blown-out specular highlights (camera flash off shiny metal
    chassis, glossy plastic) — text UNDER the highlight is unrecoverable,
    but text at its EDGE, half-clipped by it, often survives once the
    highlight itself is filled in from surrounding texture instead of
    OCR'd as a wall of solid white."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mask = _interior_blowout_mask(gray)
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=2)
    repaired = cv2.inpaint(img, mask, 4, cv2.INPAINT_TELEA)
    out, _ = variant_enhanced(repaired, cfg)
    return out


def adaptive_binarize(img: np.ndarray) -> np.ndarray:
    """Local-neighbourhood adaptive threshold (auto polarity).

    Complements the global Otsu pass: a single global threshold clips one
    side of a label to solid black or white when lighting is uneven across
    the frame (common on rack photos lit from one side); adaptive
    thresholding re-derives the cutoff per neighbourhood instead.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    block = max(15, (min(gray.shape[:2]) // 8) | 1)  # odd, scales with crop size
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, block, 5)
    if float(np.mean(binary)) < 127:
        binary = 255 - binary
    return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)


def best_contrast_channel(img: np.ndarray, cfg: OCRConfig) -> np.ndarray:
    """Pick whichever raw B/G/R channel has the strongest local contrast,
    instead of always using the standard luminance mix.

    Rescues silkscreen text whose colour combination (e.g. white-on-light-
    blue) nearly cancels out in a normal grayscale conversion — the
    channels aren't mixed evenly, so one of them alone often shows the
    stroke/background split sharply where the luminance blend hides it.
    """
    b, g, r = cv2.split(img)
    channel = max((b, g, r), key=lambda c: float(c.std()))
    channel = cv2.createCLAHE(clipLimit=cfg.clahe_clip, tileGridSize=(8, 8)).apply(channel)
    channel = _unsharp(channel, cfg.sharpen_amount)
    return cv2.cvtColor(channel, cv2.COLOR_GRAY2BGR)


def otsu_binarize(img: np.ndarray) -> np.ndarray:
    """Otsu threshold with automatic polarity: always returns dark text on a
    light background (handles white-on-dark chassis labels), as 3-channel."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if float(np.mean(binary)) < 127:  # mostly dark -> text was light: invert
        binary = 255 - binary
    return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)


def zoom_region(img: np.ndarray, box: List[int], cfg: OCRConfig):
    """Crop a region, enhance it, magnify it hard.

    Returns ``(zoomed_bgr, (offset_x, offset_y), scale)`` so detections map
    back via ``original_xy = zoomed_xy / scale + offset``.
    """
    x1, y1, x2, y2 = box
    crop = img[y1:y2, x1:x2]
    if crop.size == 0:
        return None, (x1, y1), 1.0

    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB)
    l_chan, a_chan, b_chan = cv2.split(lab)
    l_chan = _denoise_if_noisy(l_chan, cfg)
    l_chan = cv2.createCLAHE(clipLimit=max(2.0, cfg.clahe_clip - 1.0),
                             tileGridSize=(8, 8)).apply(l_chan)
    crop = cv2.cvtColor(cv2.merge([l_chan, a_chan, b_chan]), cv2.COLOR_LAB2BGR)

    short = max(1, min(crop.shape[:2]))
    scale = min(cfg.zoom_max_scale, max(1.0, cfg.zoom_target_side / short))
    if scale > 1.01:
        crop = cv2.resize(crop, (int(round(crop.shape[1] * scale)),
                                 int(round(crop.shape[0] * scale))),
                          interpolation=cv2.INTER_LANCZOS4)
        crop = _unsharp(crop, 0.5, sigma=1.2)
    return crop, (x1, y1), scale


def build_variants(img: np.ndarray, cfg: OCRConfig) -> List[Tuple[str, np.ndarray, float]]:
    """Return ``[(name, image, scale), ...]`` for every configured variant."""
    out = []
    for name in cfg.variants:
        image, scale = _VARIANTS[name](img, cfg)
        out.append((name, image, scale))
    return out
