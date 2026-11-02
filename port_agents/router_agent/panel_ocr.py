"""
panel_ocr.py - two-stage panel OCR on RapidOCR.

Adapted from the switch_ocr package in F:\\Ports_overlayy (teammate 1's, Jaya), which
does the same two stages on easyocr. The structure is what makes it work; the engine is
swapped because easyocr on torch-CPU costs about 110 s per photo here, against a 30 s
budget for the whole pipeline. RapidOCR does the same crops in a few seconds and, on the
magnified logo region, reads them better: on device_05 easyocr returns '0L5024708' at
confidence 0.12 where RapidOCR returns 'TL-602420P' at 0.85 (true model TL-SG2428P).

  Stage 1  the whole frame, tiled, plus an inverted pass. Switch panels are very wide
           with text a few pixels high, so one pass over the whole image at native size
           reads almost nothing.
  Stage 2  the area around the brand name, re-read at several magnifications and both
           polarities. The model number is printed far smaller than the logo and is
           usually illegible in stage 1. Vendors put it under the logo (D-Link, Dell) or
           on the same baseline to its right (TP-Link), so both are cropped.

Stage 2 rarely produces one clean reading of a bad panel; it produces a dozen half
readings of the same silkscreen, which is what the matcher's consensus term is for.

    from panel_ocr import read_panel
    doc = read_panel(photo_bgr)          # {"detections": [...], "roi_detections": [...]}
    import switch_identify as si
    si.identify(doc)

Everything is bounded: `budget_s` caps the wall clock and stage 2 stops early rather
than overrunning, so a hard photo degrades to fewer re-reads instead of a slow run.
"""
import re
import time

import cv2
import numpy as np

STAGE1_TARGET_H = 480      # panels are read tiled at about this height
TILE_ASPECT = 3            # tiles this many times wider than tall
TILE_OVERLAP = 0.75        # step as a fraction of a tile width
DEFAULT_BUDGET_S = 12.0    # wall clock for both stages together
MAX_LOGO_ANCHORS = 2
ROI_TARGETS = (400, 650, 200)   # magnified crop heights, best-first
MIN_ROI_CONF = 0.25        # below this a re-reading is noise, not a half-read

_ENGINE = None

# Brand names worth anchoring a magnified crop on. The model number sits beside or
# below one of these on every rack-mount faceplate.
_LOGO_HINT = re.compile(
    r"(LINK|CISCO|DELL|NETGEAR|ARUBA|PROCURVE|JUNIPER|UBIQUITI|UNIFI|MIKROTIK"
    r"|ZYXEL|HUAWEI|EXTREME|LINKSYS|ARISTA|BROCADE|3COM|QNAP|PLANET|TP|JETSTREAM"
    r"|OMADA|POWERCONNECT|CATALYST|PROSAFE|SMART|SWITCH)", re.I)


def engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _ENGINE = RapidOCR()
    return _ENGINE


def available():
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


# ----------------------------------------------------------------------------- image ops

def _gray(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img


def _clahe(gray, clip=3.0, grid=8):
    return cv2.createCLAHE(clipLimit=clip, tileGridSize=(grid, grid)).apply(gray)


def _scale_for(img, target_h):
    return max(1.0, min(12.0, target_h / max(img.shape[0], 1)))


def _run(img):
    """RapidOCR over one image. Returns [(box, text, conf)]."""
    try:
        res, _ = engine()(img)
    except Exception:
        return []
    return res or []


# ----------------------------------------------------------------------------- stage 1

def _tiles(img, target_h=STAGE1_TARGET_H):
    """The photo upscaled to a readable height and cut into overlapping tiles.
    Yields (tile, scale, x_offset_in_scaled_image)."""
    h, w = img.shape[:2]
    sc = target_h / h if h < target_h else 1.0
    big = cv2.resize(img, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC) if sc != 1 else img
    H, W = big.shape[:2]
    tw = int(min(W, H * TILE_ASPECT))
    step = max(1, int(tw * TILE_OVERLAP))
    for x in range(0, max(1, W - tw // 4), step):
        yield big[:, x:x + tw], sc, x


def stage1(img):
    """Every piece of text on the panel, in original-photo coordinates.

    Two polarities: faceplate silkscreen is usually light on a dark chassis, and the
    inverted pass picks up legends the direct one misses (and vice versa on the pale
    D-Link panels)."""
    out = {}
    for tile, sc, x0 in _tiles(img):
        passes = [tile]
        g = _clahe(_gray(tile))
        passes.append(cv2.bitwise_not(g))
        for p in passes:
            for box, text, conf in _run(p):
                b = np.asarray(box, float)
                _merge(out, text, conf,
                       [[(x0 + px) / sc, py / sc] for px, py in b])
    return _finish(out)


# ----------------------------------------------------------------------------- stage 2

def logo_hint(words):
    """A brand-word pattern from a vendor word list (router / appliance catalogues), used in
    place of the switch vendors' _LOGO_HINT. Words of 2+ characters, longest first."""
    ws = sorted({re.escape(w.upper()) for w in words if len(w.strip()) >= 2}, key=len, reverse=True)
    return re.compile("(" + "|".join(ws) + ")", re.I) if ws else _LOGO_HINT


def logo_rois(img, detections, hint=None):
    """Crops likely to hold the model number, anchored on the brand name.

    Ported from switch_ocr.ocr.logo_rois. The crop is kept close to the logo's own
    width: a wider one drags in the LED array and the port numbers, and that noise both
    distracts the detector and skews the magnification."""
    h, w = img.shape[:2]
    anchors = []
    for d in detections:
        if not d.get("box") or not (hint or _LOGO_HINT).search(d["text"]):
            continue
        xs = [p[0] for p in d["box"]]
        ys = [p[1] for p in d["box"]]
        anchors.append((min(xs), min(ys), max(xs), max(ys), d["conf"]))
    anchors.sort(key=lambda a: -a[4])

    rois = []
    for i, (x0, y0, x1, y1, _c) in enumerate(anchors[:MAX_LOGO_ANCHORS]):
        bw, bh = max(x1 - x0, 4), max(y1 - y0, 4)
        rois.append(("logo%d_below" % i,
                     (x0 - bw * 0.35, y0 + bh * 0.4, x1 + bw * 0.6, y1 + bh * 3.0)))
        rois.append(("logo%d_right" % i,
                     (x0 - bw * 0.2, y0 - bh * 0.8, x1 + bw * 3.0, y1 + bh * 1.5)))
    # Fallbacks that do not need a logo at all: D-Link in particular prints the model
    # along the very bottom edge of the faceplate.
    rois.append(("band_left", (0.0, 0.0, w * 0.30, float(h))))
    rois.append(("band_lower_left", (0.0, h * 0.50, w * 0.42, float(h))))

    out = []
    for name, (x0, y0, x1, y1) in rois:
        x0 = int(max(0, min(w - 2, x0)))
        y0 = int(max(0, min(h - 2, y0)))
        x1 = int(max(x0 + 2, min(w, x1)))
        y1 = int(max(y0 + 2, min(h, y1)))
        crop = img[y0:y1, x0:x1]
        if crop.size and crop.shape[0] >= 4 and crop.shape[1] >= 8:
            out.append((name, crop))
    return out


def roi_renders(crop, targets=ROI_TARGETS, extra=False):
    """Magnified renderings of a small crop, both polarities.

    The recognizer is sensitive to glyph height and the right magnification for a
    six-pixel silkscreen is not knowable in advance, so a few are tried. Lanczos is used
    for the enlargement: on type this small, bicubic's softer edges cost whole
    characters."""
    gray = _gray(crop)
    out = []
    for target in targets:
        scale = _scale_for(gray, target)
        if scale <= 1.0 and target > min(targets):
            continue
        g = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_LANCZOS4)
        cl = _clahe(g, clip=4.0)
        out.append(("x%d" % target, cl))
        out.append(("x%d_inv" % target, cv2.bitwise_not(cl)))
        if extra:
            # sharpened (unsharp mask): blurry or low-resolution photos
            blur = cv2.GaussianBlur(cl, (0, 0), 2.0)
            out.append(("x%d_sharp" % target, cv2.addWeighted(cl, 1.6, blur, -0.6, 0)))
            # thresholded black / white: silkscreen on brushed metal, glare
            k = max(15, (int(cl.shape[0] * 0.25) // 2) * 2 + 1)
            out.append(("x%d_bw" % target, cv2.adaptiveThreshold(cl, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                                                   cv2.THRESH_BINARY, k, 10)))
    return out


def stage2(img, stage1_dets, deadline, hint=None, extra=False):
    """Re-read the logo neighbourhood at magnification until the deadline.

    Returns many short, low-confidence readings of the same text on purpose: one
    half-read proves nothing, a dozen agreeing half-reads are the silkscreen."""
    out = {}
    for _name, crop in logo_rois(img, stage1_dets, hint):
        if time.time() >= deadline:
            break
        for _rname, render in roi_renders(crop, extra=extra):
            if time.time() >= deadline:
                break
            for _box, text, conf in _run(render):
                if conf >= MIN_ROI_CONF:
                    _merge(out, text, conf, None)
    return _finish(out)


# ----------------------------------------------------------------------------- merge

def _merge(target, text, conf, box):
    text = (text or "").strip()
    if not text:
        return
    key = text.upper()
    e = target.setdefault(key, {"text": text, "conf": 0.0, "box": None, "sightings": 0})
    e["sightings"] += 1
    if conf > e["conf"]:
        e["conf"], e["text"] = float(conf), text
        if box is not None:
            e["box"] = [[float(x), float(y)] for x, y in box]


def _finish(d):
    out = sorted(d.values(), key=lambda x: -x["conf"])
    for e in out:
        e["conf"] = round(e["conf"], 3)
    return out


# ----------------------------------------------------------------------------- driver

def read_panel(img_bgr, budget_s=DEFAULT_BUDGET_S, logo_words=None, extra_renders=False):
    """Two-stage read of a front-panel photo.

    logo_words: brand words that anchor the magnified crops (default: the switch vendors'
    _LOGO_HINT); extra_renders: also read sharpened and thresholded versions of each crop.
    Both default off, so the switch mapper reads exactly as before.

    Returns the document shape switch_identify.identify() expects:
    {"detections": stage 1, "roi_detections": stage 2}."""
    deadline = time.time() + budget_s
    hint = logo_hint(logo_words) if logo_words else None
    s1 = stage1(img_bgr)
    s2 = stage2(img_bgr, s1, deadline, hint, extra_renders) if time.time() < deadline else []
    return {"detections": s1, "roi_detections": s2}


def tokens(doc):
    """Stage-1 readings as (text, confidence, cx, cy), the form map_any_switch uses for
    the printed port numbers."""
    out = []
    for d in doc.get("detections", []):
        if not d.get("box"):
            continue
        xs = [p[0] for p in d["box"]]
        ys = [p[1] for p in d["box"]]
        out.append((d["text"], d["conf"], sum(xs) / len(xs), sum(ys) / len(ys)))
    return out
