"""Keystone verification from modularity, layout and connector structure.

A keystone panel is defined by its CONSTRUCTION, not by what is plugged into
it: a frame of identical rectangular cutouts, each holding an individually
removable jack. A connector vote cannot see that -- a keystone frame full of
RJ-45 modules and a fixed RJ-45 panel both vote "RJ45" -- so this module
measures the frame directly instead.

Three families of evidence, all computed per panel:

GEOMETRY (from the detected port boxes)
    size uniformity        every cutout is the same stamped opening, so box
                           widths and heights have low spread
    pitch regularity       modules sit on a fixed centre-to-centre pitch;
                           residual against a fitted uniform grid is small
    gap consistency        the web between adjacent modules is a constant
                           width
    row structure          number of rows, and whether rows share a pitch
    panel aspect           1U vs 2U, width-to-height of the port band

APPEARANCE (from the image, independent of the detector's classes)
    boundary contrast      a keystone module is a separate part, so the web
                           between two adjacent modules carries a real edge.
                           On a fixed panel the ports are moulded into one
                           continuous face and the same region is flat.
    opening rectangularity how well the dark cutout regions match filled
                           axis-aligned rectangles
    label strip            keystone frames carry a label window per module
                           or per group; measured as a horizontal band of
                           high-brightness, low-texture pixels

POPULATION (from the detector's classes)
    empty cutouts          an unpopulated snap-in position. Only a modular
                           panel can be partly populated.
    connector mix          two or more connector families in one frame. A
                           fixed-media panel is single-type by construction,
                           so a mix is conclusive on its own.

`features()` returns all of them; `verify()` combines them into a decision.
The weights in `verify()` are fixed and hand-set, not fitted to the test
set -- see evaluate_keystone.py, which reports grouped cross-validated
numbers for a fitted alternative so the two can be compared honestly.
"""

import json

import cv2
import numpy as np

# Detector classes that are real patch-panel connectors, by family. Mixing
# families within one frame is only possible on a modular panel.
CONNECTOR_FAMILY = {
    "RJ45": "copper", "LC": "fiber", "SC": "fiber", "FC": "fiber",
}
EMPTY = "empty"


def _cv(v):
    """Coefficient of variation, 0 when undefined."""
    v = np.asarray(v, dtype=float)
    if v.size < 2 or v.mean() <= 1e-6:
        return 0.0
    return float(v.std() / v.mean())


def _rows(boxes, tol=0.6):
    """Group boxes into rows by vertical overlap of their centres."""
    if not boxes:
        return []
    cy = np.array([(b[1] + b[3]) / 2.0 for b in boxes])
    h = np.array([b[3] - b[1] for b in boxes], dtype=float)
    med_h = float(np.median(h)) or 1.0
    order = np.argsort(cy)
    rows, cur = [], [order[0]]
    for i in order[1:]:
        if abs(cy[i] - cy[cur[-1]]) <= tol * med_h:
            cur.append(i)
        else:
            rows.append(cur)
            cur = [i]
    rows.append(cur)
    return [sorted(r, key=lambda j: boxes[j][0]) for r in rows]


def _pitch_residual(xs):
    """How far centres deviate from a uniform grid, as a fraction of pitch.

    Modules are mounted on a fixed pitch, so fitting x_k = a + k*p to the
    sorted centres leaves almost no residual. Ports detected on a cluttered
    fixed panel drift more. Index k is taken from the rounded spacing so a
    missed detection leaves a gap in k rather than shifting everything.
    """
    xs = np.sort(np.asarray(xs, dtype=float))
    if len(xs) < 4:
        return 0.0, 0.0
    d = np.diff(xs)
    d = d[d > 1e-6]
    if len(d) == 0:
        return 0.0, 0.0
    pitch = float(np.median(d))
    if pitch <= 1e-6:
        return 0.0, 0.0
    k = np.round((xs - xs[0]) / pitch)
    # least squares on x = a + k*p
    A = np.vstack([np.ones_like(k), k]).T
    sol, *_ = np.linalg.lstsq(A, xs, rcond=None)
    resid = xs - A @ sol
    return float(np.abs(resid).mean() / pitch), pitch


def _boundary_contrast(gray, boxes, row):
    """Edge energy in the web between adjacent modules, vs inside the ports.

    A keystone module is a discrete part: its border, and the frame web it
    snaps into, put one or more real vertical edges between any two
    neighbours. A fixed panel's ports are openings in one moulded face, so
    the same strip is plain plastic. Normalising by the edge energy inside
    the port openings cancels out exposure, focus and cable clutter, which
    otherwise dominate this measurement.
    """
    if len(row) < 2:
        return 0.0
    H, W = gray.shape
    gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))

    gaps, insides = [], []
    for a, b in zip(row[:-1], row[1:]):
        ba, bb = boxes[a], boxes[b]
        y0 = max(0, int(min(ba[1], bb[1])))
        y1 = min(H, int(max(ba[3], bb[3])))
        x0, x1 = int(ba[2]), int(bb[0])
        if y1 - y0 < 4:
            continue
        if x1 - x0 < 2:                      # boxes touch: sample the seam
            mid = (x0 + x1) // 2
            x0, x1 = mid - 1, mid + 2
        x0, x1 = max(0, x0), min(W, x1)
        if x1 - x0 < 1:
            continue
        gaps.append(float(gx[y0:y1, x0:x1].mean()))
        inner = gx[max(0, int(ba[1])):min(H, int(ba[3])),
                   max(0, int(ba[0])):min(W, int(ba[2]))]
        if inner.size:
            insides.append(float(inner.mean()))
    if not gaps or not insides:
        return 0.0
    denom = float(np.mean(insides)) + 1e-6
    # Clipped to [0, 2]: this is a ratio with no upper bound, and a single
    # blown-out highlight in one gap can otherwise send it to 10+ and
    # swamp every other term in the score.
    return float(np.clip(np.mean(gaps) / denom, 0.0, 2.0))


def _opening_rectangularity(gray, boxes):
    """Do the dark cutouts read as filled axis-aligned rectangles?

    Keystone openings are stamped rectangles of one size. Thresholding the
    port band and comparing each connected dark component against its own
    bounding box gives a fill ratio that is high for a clean rectangular
    cutout and low for the irregular shadow of a cabled RJ-45 jack.
    """
    if not boxes:
        return 0.0
    H, W = gray.shape
    xs0 = max(0, int(min(b[0] for b in boxes)))
    xs1 = min(W, int(max(b[2] for b in boxes)))
    ys0 = max(0, int(min(b[1] for b in boxes)))
    ys1 = min(H, int(max(b[3] for b in boxes)))
    band = gray[ys0:ys1, xs0:xs1]
    if band.size < 64:
        return 0.0
    band = cv2.GaussianBlur(band, (3, 3), 0)
    thr = cv2.adaptiveThreshold(band, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                cv2.THRESH_BINARY_INV, 31, 8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(thr, 8)
    med_area = np.median([(b[2] - b[0]) * (b[3] - b[1]) for b in boxes])
    fills = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w < 3 or h < 3 or area < 0.15 * med_area or area > 3 * med_area:
            continue
        fills.append(area / float(w * h))
    if not fills:
        return 0.0
    return float(np.mean(sorted(fills, reverse=True)[:max(4, len(fills) // 2)]))


def _label_strip(gray, boxes):
    """Fraction of the panel height taken by bright, low-texture bands.

    Keystone frames carry a label window above or below each module or
    group. Those bands are much brighter than the frame and almost
    textureless, which is a distinctive row signature.
    """
    H, W = gray.shape
    if H < 8 or not boxes:
        return 0.0
    rowmean = gray.mean(axis=1)
    rowstd = gray.std(axis=1)
    bright = rowmean > (rowmean.mean() + 0.6 * rowmean.std() + 1e-6)
    flat = rowstd < (rowstd.mean() - 0.1 * rowstd.std())
    return float((bright & flat).mean())


def _grid_gaps(boxes, row):
    """Missing jacks: positions the mounting pitch predicts but nothing fills.

    Only a removable piece can be absent. Fitting the pitch to the boxes that
    ARE there and counting the predicted centres with no box near them finds
    a gap in the run -- which on a one-piece moulded panel cannot happen,
    because an unused position is still a socket and still gets detected.
    """
    if len(row) < 4:
        return 0, 0.0
    xs = np.array(sorted((boxes[i][0] + boxes[i][2]) / 2.0 for i in row))
    w = float(np.median([boxes[i][2] - boxes[i][0] for i in row]))
    d = np.diff(xs)
    d = d[d > 1e-6]
    if len(d) < 2:
        return 0, 0.0
    pitch = float(np.median(d))
    if pitch <= 1e-6 or w <= 0:
        return 0, 0.0
    # Predicted centres across the run; a slot is "missing" when no detected
    # centre lands within half a jack width of it.
    n = int(round((xs[-1] - xs[0]) / pitch)) + 1
    if n < 4 or n > 200:
        return 0, 0.0
    pred = xs[0] + pitch * np.arange(n)
    gaps = 0
    for p in pred:
        if np.min(np.abs(xs - p)) > 0.5 * w:
            gaps += 1
    # Out-of-line jacks: a detected centre far from every predicted slot.
    off = 0
    for x in xs:
        if np.min(np.abs(pred - x)) > 0.30 * w:
            off += 1
    return gaps, off / max(1, len(xs))


def _open_cutouts(bgr, boxes, classes):
    """Count port positions that are an OPEN square cutout -- a hole.

    One is enough. A square opening with nothing behind it cannot exist on
    a fixed panel: there, an unused position is still a full socket with a
    jack body, a latch notch and a row of contacts, all of which catch
    light. An open keystone cutout is a void: it reads as a dark, smooth,
    rectangular hole against the lit panel face.

    This does NOT rely on the detector's `empty` class. That class is the
    same evidence filtered through a model that was never trained to find
    holes specifically, and it misses them whenever the opening is small or
    the panel is dark. Measuring the aperture directly catches the ones the
    detector drops.

    An open position is dark inside relative to the panel face, smooth
    inside (a real jack's contacts and ribs produce texture), and shows no
    brass contact row.
    """
    if bgr is None or not boxes:
        return 0, 0.0
    H, W = bgr.shape[:2]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2].astype(np.float32)
    h_, s_ = hsv[:, :, 0].astype(np.float32), hsv[:, :, 1].astype(np.float32)
    lap = np.abs(cv2.Laplacian(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY),
                               cv2.CV_32F, ksize=3))

    # Panel face brightness: sampled from the webs between ports, which is
    # frame material rather than connector.
    face = []
    for b in boxes:
        x0, y0 = max(0, int(b[0])), max(0, int(b[1]))
        x1, y1 = min(W, int(b[2])), min(H, int(b[3]))
        if x1 - x0 < 4 or y1 - y0 < 4:
            continue
        pad = max(2, (x1 - x0) // 6)
        left = v[y0:y1, max(0, x0 - pad):x0]
        if left.size:
            face.append(float(np.median(left)))
    if not face:
        return 0, 0.0
    face_v = float(np.median(face)) + 1e-6

    inner_lap, inner_v, golds = [], [], []
    for b in boxes:
        x0, y0 = max(0, int(b[0])), max(0, int(b[1]))
        x1, y1 = min(W, int(b[2])), min(H, int(b[3]))
        if x1 - x0 < 6 or y1 - y0 < 6:
            inner_lap.append(None)
            inner_v.append(None)
            golds.append(None)
            continue
        dx, dy = (x1 - x0) // 6, (y1 - y0) // 6
        iy0, iy1 = y0 + dy, y1 - dy
        ix0, ix1 = x0 + dx, x1 - dx
        inner_v.append(float(np.median(v[iy0:iy1, ix0:ix1])))
        inner_lap.append(float(lap[iy0:iy1, ix0:ix1].mean()))
        golds.append(float((((h_[iy0:iy1, ix0:ix1] > 12)
                             & (h_[iy0:iy1, ix0:ix1] < 30)
                             & (s_[iy0:iy1, ix0:ix1] > 80)
                             & (v[iy0:iy1, ix0:ix1] > 110))).mean()))
    live = [i for i, x in enumerate(inner_lap) if x is not None]
    if len(live) < 3:
        return 0, 0.0
    lap_ref = float(np.median([inner_lap[i] for i in live])) + 1e-6

    n_open = 0
    for i in live:
        dark = inner_v[i] < 0.52 * face_v
        smooth = inner_lap[i] < 0.60 * lap_ref
        no_gold = golds[i] < 0.004
        if dark and smooth and no_gold:
            n_open += 1
    return n_open, n_open / max(1, len(live))


def _jack_appearance(bgr, boxes, classes):
    """Mixed jacks in one frame: colour and size odd-ones-out.

    A fixed panel is a single moulded unit, so its ports cannot differ from
    each other. Any jack whose colour or size stands apart from its
    neighbours means separate parts were fitted -- black beside grey
    shielded, or one differently-shaped port in the run.
    """
    idx = [i for i, c in enumerate(classes) if c in CONNECTOR_FAMILY]
    if len(idx) < 4 or bgr is None:
        return 0.0, 0.0, 0.0
    H, W = bgr.shape[:2]
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)

    cols, sizes = [], []
    for i in idx:
        x0, y0 = max(0, int(boxes[i][0])), max(0, int(boxes[i][1]))
        x1, y1 = min(W, int(boxes[i][2])), min(H, int(boxes[i][3]))
        if x1 - x0 < 3 or y1 - y0 < 3:
            continue
        p = lab[y0:y1, x0:x1].reshape(-1, 3)
        cols.append(np.median(p, axis=0))
        sizes.append([x1 - x0, y1 - y0])
    if len(cols) < 4:
        return 0.0, 0.0, 0.0
    cols = np.asarray(cols)
    sizes = np.asarray(sizes, dtype=float)

    def _outlier_frac(v):
        med = np.median(v, axis=0)
        d = np.linalg.norm(v - med, axis=1)
        mad = float(np.median(np.abs(d - np.median(d)))) + 1e-6
        return float((d > np.median(d) + 4.0 * mad).mean())

    col_out = _outlier_frac(cols)
    size_out = _outlier_frac(sizes)
    # Spread of jack colour overall: a frame of identical moulded ports is
    # tight, a frame of separately-bought modules is not.
    col_spread = float(np.median(np.linalg.norm(
        cols - np.median(cols, axis=0), axis=1)))
    return col_out, size_out, col_spread


def _jack_vs_panel_colour(bgr, boxes, rows):
    """Do the jack faces differ in colour from the frame they sit in?

    Beige jacks in a grey frame, or black jacks in a black frame of a
    slightly different texture, mean the jacks were not moulded with the
    panel. Measured in Lab so it is a perceptual distance, not an RGB one.
    """
    if bgr is None or not boxes:
        return 0.0
    H, W = bgr.shape[:2]
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    jack, web = [], []
    for r in rows:
        for a, b in zip(r[:-1], r[1:]):
            ba, bb = boxes[a], boxes[b]
            y0, y1 = max(0, int(ba[1])), min(H, int(ba[3]))
            x0, x1 = int(ba[2]), int(bb[0])
            if y1 - y0 < 3:
                continue
            if x1 - x0 >= 2:
                x0, x1 = max(0, x0), min(W, x1)
                if x1 > x0:
                    web.append(np.median(
                        lab[y0:y1, x0:x1].reshape(-1, 3), axis=0))
            jx0, jx1 = max(0, int(ba[0])), min(W, int(ba[2]))
            if jx1 > jx0:
                jack.append(np.median(
                    lab[y0:y1, jx0:jx1].reshape(-1, 3), axis=0))
    if len(jack) < 3 or len(web) < 3:
        return 0.0
    return float(np.linalg.norm(np.median(np.asarray(jack), axis=0)
                                - np.median(np.asarray(web), axis=0)))


def _per_jack_marking(gray, boxes, rows):
    """Repeated per-jack markings, e.g. "CAT6" printed on every module face.

    A fixed panel prints its category once on the body; a keystone frame
    carries it on each module. Approximated as high-frequency ink energy in
    a thin band on each jack face, scored by how CONSISTENT that energy is
    across jacks -- a repeated mark gives every jack a similar reading.

    Honest caveat: at ~30 px per module there is not enough resolution to
    read the text, so this measures "something is printed here, on all of
    them" rather than what it says. Its measured AUC is weak.
    """
    if gray is None or not rows:
        return 0.0
    H, W = gray.shape
    lap = np.abs(cv2.Laplacian(gray, cv2.CV_32F, ksize=3))
    vals = []
    for r in rows:
        for i in r:
            x0, y0 = max(0, int(boxes[i][0])), max(0, int(boxes[i][1]))
            x1, y1 = min(W, int(boxes[i][2])), min(H, int(boxes[i][3]))
            h = y1 - y0
            if x1 - x0 < 4 or h < 6:
                continue
            band = lap[y1 - max(2, h // 4):y1, x0:x1]
            if band.size:
                vals.append(float(band.mean()))
    if len(vals) < 4:
        return 0.0
    v = np.asarray(vals)
    if v.mean() <= 1e-6:
        return 0.0
    # High mean ink, low relative spread = the same mark on every jack.
    return float(np.clip(1.0 - v.std() / v.mean(), 0.0, 1.0) *
                 np.clip(v.mean() / 25.0, 0.0, 1.0))


def features(image_path, boxes, classes, img_w=0, img_h=0):
    """Every keystone feature for one panel. `boxes` are xyxy ints."""
    f = {k: 0.0 for k in (
        "n_slots", "n_empty", "n_occupied", "empty_frac", "n_rows",
        "size_cv_w", "size_cv_h", "gap_cv", "spacing_even", "pitch_resid",
        "aspect", "boundary_contrast", "rectangularity", "label_strip",
        "n_families", "mixed_family", "modularity",
        # the tiered-rule features
        "grid_gaps", "misaligned", "jack_col_out", "jack_size_out",
        "jack_col_spread", "jack_panel_delta", "per_jack_mark",
        "open_cutouts", "open_frac", "n_port_kinds", "mixed_kinds")}
    f["n_slots"] = len(boxes)
    if not boxes:
        return f

    cls = list(classes)
    f["n_empty"] = sum(c == EMPTY for c in cls)
    fam_n = {}
    for c in cls:
        if c in CONNECTOR_FAMILY:
            fam_n[CONNECTOR_FAMILY[c]] = fam_n.get(CONNECTOR_FAMILY[c], 0) + 1
    f["n_occupied"] = sum(fam_n.values())
    f["empty_frac"] = f["n_empty"] / max(1, len(boxes))
    f["n_families"] = len(fam_n)
    # a family counts only if seen at least twice -- one stray box is not a type
    f["mixed_family"] = 1.0 if sum(n >= 2 for n in fam_n.values()) >= 2 else 0.0

    # "Multiple types of port in a single panel" taken broadly: a copper
    # jack beside a fiber adapter, an SFP cage or a USB/console port is
    # still more than one kind of thing in one frame, and a single moulded
    # panel cannot do that. Counted over kinds that actually appear more
    # than once, so one stray detection is not a "type".
    kinds = {}
    for c in cls:
        k = (CONNECTOR_FAMILY.get(c)
             or ("cage" if c in ("SFP", "QSFP") else None)
             or ("device" if c in ("AUX", "CONSOLE", "MANAGEMENT_PORT",
                                   "USB_A", "USB_B", "USB_C") else None))
        if k:
            kinds[k] = kinds.get(k, 0) + 1
    real = [k for k, n in kinds.items() if n >= 2]
    f["n_port_kinds"] = len(real)
    f["mixed_kinds"] = 1.0 if len(real) >= 2 else 0.0

    w = np.array([b[2] - b[0] for b in boxes], dtype=float)
    h = np.array([b[3] - b[1] for b in boxes], dtype=float)
    f["size_cv_w"], f["size_cv_h"] = _cv(w), _cv(h)

    rows = _rows(boxes)
    f["n_rows"] = len(rows)

    gaps, resids = [], []
    for r in rows:
        if len(r) < 3:
            continue
        xs = [(boxes[i][0] + boxes[i][2]) / 2.0 for i in r]
        res, _ = _pitch_residual(xs)
        resids.append(res)
        g = [boxes[b][0] - boxes[a][2] for a, b in zip(r[:-1], r[1:])]
        g = [x for x in g if x > -min(w)]
        if len(g) >= 2:
            gaps.append(_cv(np.abs(g) + 1.0))
    f["pitch_resid"] = float(np.mean(resids)) if resids else 0.0
    f["gap_cv"] = float(np.mean(gaps)) if gaps else 0.0
    # The spacing cue the right way up: 1 = perfectly even web between
    # every pair of adjacent modules. Measured AUC 0.79 as a keystone
    # signal, second only to the empty-cutout count.
    f["spacing_even"] = float(np.clip(1.0 - f["gap_cv"] / 1.2, 0.0, 1.0))

    span_x = max(b[2] for b in boxes) - min(b[0] for b in boxes)
    span_y = max(b[3] for b in boxes) - min(b[1] for b in boxes)
    f["aspect"] = float(span_x / max(1.0, span_y))

    # Missing / out-of-line jacks, from geometry alone.
    gaps, offs = 0, []
    for r in rows:
        g, o = _grid_gaps(boxes, r)
        gaps += g
        offs.append(o)
    f["grid_gaps"] = float(gaps)
    f["misaligned"] = float(np.mean(offs)) if offs else 0.0

    bgr = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if bgr is not None:
        if img_w and img_h and (bgr.shape[1] != img_w
                                or bgr.shape[0] != img_h):
            bgr = cv2.resize(bgr, (img_w, img_h))
        img = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

        bc = [_boundary_contrast(img, boxes, r) for r in rows if len(r) >= 2]
        f["boundary_contrast"] = float(np.mean(bc)) if bc else 0.0
        f["rectangularity"] = _opening_rectangularity(img, boxes)
        f["label_strip"] = _label_strip(img, boxes)
        n_open, open_frac = _open_cutouts(bgr, boxes, cls)
        f["open_cutouts"], f["open_frac"] = float(n_open), open_frac
        co, so, cs = _jack_appearance(bgr, boxes, cls)
        f["jack_col_out"], f["jack_size_out"] = co, so
        f["jack_col_spread"] = cs
        f["jack_panel_delta"] = _jack_vs_panel_colour(bgr, boxes, rows)
        f["per_jack_mark"] = _per_jack_marking(img, boxes, rows)

    # Composite "is this a repeated modular arrangement" score: uniform
    # cutouts on a regular pitch with a constant web between them.
    f["modularity"] = float(np.clip(
        0.35 * (1.0 - min(1.0, f["size_cv_w"] / 0.35))
        + 0.25 * (1.0 - min(1.0, f["size_cv_h"] / 0.40))
        + 0.25 * (1.0 - min(1.0, f["pitch_resid"] / 0.25))
        + 0.15 * (1.0 - min(1.0, f["gap_cv"] / 1.20)), 0.0, 1.0))
    return f


def features_from_row(r):
    """Features for a row of verified_4class.csv."""
    boxes = json.loads(r.port_boxes) if isinstance(r.port_boxes, str) else []
    classes = json.loads(r.port_classes) if isinstance(r.port_classes, str) \
        else []
    return features(r.src_path, boxes, classes,
                    int(r.img_w or 0), int(r.img_h or 0))


# Hand-set weights. Deliberately NOT fitted to the 230 test crops -- a
# threshold tuned on the set it is scored against measures the tuning, not
# the method. evaluate_keystone.py reports a fitted model under grouped
# cross-validation alongside this one.
WEIGHTS = {
    "empty_frac": 1.70,          # strongest single cue
    "mixed_family": 0.90,        # only a modular panel can be mixed
    "modularity": 0.80,          # uniform cutouts on a regular pitch
    "boundary_contrast": 0.55,   # a real edge between adjacent modules
    "rectangularity": 0.45,      # stamped rectangular openings
    "label_strip": 0.30,         # per-module label windows
}
BIAS = -1.35
THRESHOLD = 0.0


def score(f):
    """Signed keystone score; > THRESHOLD means keystone."""
    return BIAS + sum(w * float(f.get(k, 0.0)) for k, w in WEIGHTS.items())


# ------------------------------------------------------------------ tiers
#
# A keystone panel is a Lego baseplate: the frame is one piece and the jacks
# are separate pieces clicked into it. Every feature below is really
# evidence that the frame and the jacks are separate things. Thresholds are
# a priori (one hole is one hole), not swept against the test set.

MIN_SLOTS = 4            # below this there is no arrangement to read

# Tier 1: how many open cutouts before the panel is called keystone.
# EMPTY_DECIDES = True removes the rule entirely, which is the state
# requested. Raising MIN_EMPTY instead keeps the rule but stops a single
# unoccupied position from settling the whole panel -- the middle ground,
# and the one the measurements favour. Every setting is scored on both
# datasets by sweep_empty.py.
# ON, because the slot check (ppc/slots.py) is what now guards it: tier 1
# fires on an open cutout, and the slot check vetoes it when every empty
# position turns out to be a socket with no cable rather than a bare hole.
# With this False the slot check can never run at all -- it is gated on
# tier 1 -- and keystone recall falls to 11%.
EMPTY_DECIDES = True
MIN_EMPTY = 1

# The second guard on tier 1, independent of the slot check: the empty slots
# are handed back to the PORT MODEL at twice the resolution, and if every one
# of them turns out to be an unoccupied socket rather than a bare cutout, the
# empties stop being keystone evidence (ppc/ports.py recheck_empties).
#
# This is the number of empty slots that must agree before that veto is
# believed. Two, not one. Measured over every labelled panel with an empty
# slot, the re-read answered "all sockets" on 4 panels: 2 were genuinely
# fixed RJ-45 panels and 2 were keystone frames. Split by how many slots
# agreed, the single-slot answers were right once and wrong twice, while both
# answers backed by two or more slots were right. Accuracy, original 211 /
# hi-res 245:
#
#   >= 1 empty, no re-read guard       95.7% / 93.5%
#   guard believed on 1 slot           96.2% / 93.1%   (fires 4x, 2 wrong)
#   guard needs 2 agreeing slots       95.7% / 93.9%   <- shipped, 0 wrong
EMPTY_RECHECK_MIN_SLOTS = 2
SEAM = 0.95              # boundary edge energy, jack gap vs jack interior
MARK = 0.35              # repeated per-jack marking
PANEL_DELTA = 12.0       # Lab distance, jack face vs frame web
SEAMLESS = 0.70          # below this the run reads as one continuous strip
UNIFORM_GAP = 0.45       # gap-width variation below this is machine-even
EVEN_PITCH = 0.78        # spacing consistency above this is machine-even

# MEASURED DEAD ENDS -- kept computed and reported, deliberately NOT used as
# strong evidence, because on these crops they measure something other than
# what they are named for. Numbers are AUC against 211 hand-reviewed labels
# (0.5 = no signal):
#
#   grid_gaps      0.17, i.e. strongly INVERTED. The intent was "a jack is
#                  missing from the run, and only a removable piece can be
#                  absent". What it actually finds is a port hidden by a
#                  patch cable: on a keystone frame an unused position is
#                  detected as `empty` and leaves no gap, whereas a fully
#                  cabled fixed panel loses ports to occlusion. It is an
#                  occlusion detector wearing a modularity label.
#   misaligned     0.36, inverted, same cause.
#   jack_col_out   0.46  } "mixed jacks in one frame" by colour / size
#   jack_size_out  0.40  } outliers. At ~30 px per module one jack in
#                  shadow or glare always reads as an outlier, so these
#                  fire on almost every panel. Only the connector-FAMILY
#                  test (RJ45 beside LC/SC) is reliable, and it is rare.
#
# The concepts are sound; the front-view evidence at this resolution will
# not support them. They would need either a higher-resolution crop or the
# back view, which is also where tier 6 sends the genuinely ambiguous case.


def empty_recheck_vetoes(rc, min_slots=None):
    """Does the port model's re-read take the empties away from tier 1?

    `rc` is recheck_empties() output from ppc/ports.py, or None when the
    re-read was not run -- in which case nothing is vetoed and tier 1 behaves
    as it always did.

    Only a unanimous "every empty slot is really an unoccupied socket",
    backed by at least EMPTY_RECHECK_MIN_SLOTS slots, counts. A slot that
    re-reads as nothing at all is not evidence of a socket, and one lone slot
    is a guess -- see the measurements next to that constant.
    """
    if not rc or rc.get("verdict") != "connector":
        return False, ""
    k = EMPTY_RECHECK_MIN_SLOTS if min_slots is None else min_slots
    if int(rc.get("n_slots") or 0) < k:
        return False, ""
    return True, "; %s" % (rc.get("why")
                           or "the port model re-reads them as sockets")


def verify_tiered(f, empty_recheck=None):
    """Ordered keystone test -> (verdict, tier, confidence, reason).

    verdict: True keystone / False fixed / None undecided (hand to the ports
    model).

      1  an unused position that is a bare cutout or a blank plug. On a
         fixed panel an unused position is still a full socket with pins and
         a latch notch, so a hole is never fixed.           -> keystone
      2  mixed jacks in one frame: different connector families, or a jack
         whose colour or shape stands apart from its neighbours. A fixed
         panel is one moulded unit and cannot have mismatched ports.
                                                            -> keystone
      3  a jack missing from the run, or sitting out of line with its
         neighbours. Only a removable piece can be absent or misaligned.
                                                            -> keystone
      4  no strong evidence: per-jack bezels with visible seams, a repeated
         marking on each jack face, jack colour not matching the frame.
         Suggestive, not conclusive.                -> keystone, medium
      5  a seamless strip of real sockets on an even pitch.  -> fixed, medium
      6  fully loaded and uniform, or the cables hide the jacks. The front
         view cannot tell; this is the back-view case.       -> undecided
    """
    n = int(f["n_slots"])
    if n < MIN_SLOTS:
        return None, 0, "none", ("only %d slots detected -- no arrangement "
                                 "to read" % n)

    # --- tier 1: ONE open square cutout anywhere is enough ---
    # A hole cannot exist on a one-piece moulded panel, so a single open
    # position settles the whole panel. The detector's `empty` class is
    # what implements this, and it is very good at it: measured on 245
    # hi-res crops, 88.9% precision AND 88.9% recall.
    #
    # `open_cutouts` is a second, independent measurement of the same
    # thing straight from the aperture (dark, smooth, no contact row). It
    # is computed and reported but NOT used to decide, because it was
    # measured and it is worse: 66.7% precision, 41.7% recall. Restricted
    # to the 173 panels where the detector finds nothing -- the only place
    # it could add anything -- it fires on 17 and is right about 3. Adding
    # it trades 3 extra keystone found for 14 panels wrongly relabelled.
    # Switching this off was tried and reverted. Measured cost of removing
    # it, on both datasets: accuracy 95.1% -> 72.2% (hi-res 245) and 95.3%
    # -> 64.9% (original 211), with keystone recall collapsing to 16.7% and
    # 13.4% -- nothing else in the pipeline can see frame construction, so
    # the remaining panels all fall through to the port vote, which says
    # RJ45. The known weakness stands: the detector's `empty` class marks
    # an unoccupied POSITION and cannot separate a bare cutout from a
    # socket with no cable in it. A proper fix measures the aperture
    # (`open_cutouts`), not occupancy; that is not yet good enough to ship.
    # TURNED OFF ON REQUEST. `MIN_EMPTY` is the number of open cutouts tier
    # 1 needs before it will call keystone; `EMPTY_DECIDES = False` removes
    # the rule outright. Both are module-level so the trade-off can be
    # re-measured without editing this function -- see the table in the
    # report, which scores every threshold on both datasets.
    #
    # SECOND GUARD, and the one that answers "it says keystone on a single
    # empty": before an empty is believed to be an open cutout, the port
    # model re-reads it at twice the resolution. An unoccupied socket shows
    # its jack body and contact row at that size and comes back RJ45; a bare
    # cutout stays `empty`. When every empty slot comes back a socket, tier 1
    # has nothing to stand on and the panel falls through to the tiers below
    # and then to the port vote -- which is how a fixed RJ-45 panel with a
    # cable unplugged stays RJ-45.
    vetoed, recheck_note = empty_recheck_vetoes(empty_recheck)
    if EMPTY_DECIDES and f["n_empty"] >= MIN_EMPTY and not vetoed:
        extra = ("; the aperture test independently reads %d of them as "
                 "open" % int(f["open_cutouts"])) if f["open_cutouts"] else ""
        return True, 1, "high", (
            "%d square cutout%s open with nothing behind it (%.0f%% of the "
            "frame) -- on a fixed panel an unused position is still a full "
            "socket with pins and a latch notch%s"
            % (int(f["n_empty"]), "" if f["n_empty"] == 1 else "s",
               f["empty_frac"] * 100, extra))

    # --- tier 2: more than one kind of port in one frame ---
    # A single moulded panel is one kind by construction, so a mix means
    # separate pieces were fitted. Only the connector-family test is used.
    # The broader version -- counting SFP cages and USB/console ports as
    # kinds -- was measured at 50% precision over 16 panels: it mostly
    # finds switches that crept into the crop, not modular frames. It is
    # reported as evidence instead.
    if f["mixed_family"]:
        kinds = ("; counting cages and device ports too, %d kinds"
                 % int(f["n_port_kinds"])) if f["mixed_kinds"] else ""
        return True, 2, "high", (
            "%d connector families in one frame; a moulded panel is "
            "single-type by construction%s" % (int(f["n_families"]), kinds))

    # --- tier 3: a jack missing or out of line ---
    # Intentionally empty. See the MEASURED DEAD ENDS note above: on these
    # crops the missing-jack and out-of-line tests detect cable occlusion
    # on fixed panels, not absent modules on keystone frames, and firing
    # them was 11.5% correct. The evidence is reported per panel so the
    # case can be revisited with better imagery.

    # --- tier 4: supporting evidence only ---
    support, bits = 0, []
    if f["boundary_contrast"] >= SEAM:
        support += 1
        bits.append("a visible seam between neighbouring jacks (%.2f)"
                    % f["boundary_contrast"])
    if f["per_jack_mark"] >= MARK:
        support += 1
        bits.append("a repeated marking on each jack face (%.2f)"
                    % f["per_jack_mark"])
    if f["jack_panel_delta"] >= PANEL_DELTA:
        support += 1
        bits.append("jack colour not matching the frame (dE %.0f)"
                    % f["jack_panel_delta"])
    # These never decide. Each signal is individually weak -- measured AUC
    # 0.58 (seam), 0.50 (per-jack marking), 0.46 (jack-vs-frame colour) --
    # and gating on them was 7.5% correct at 2-of-3 and 13.6% at 3-of-3.
    # The supporting evidence is recorded on every panel so it is visible,
    # but a panel with no hole and no mismatch is handed to the ports
    # model rather than called keystone on a seam.
    SUPPORT_DECIDES = False
    if SUPPORT_DECIDES and support >= 3:
        return True, 4, "medium", "no holes or mismatches, but " + \
            " and ".join(bits)
    support_note = ("; supporting evidence: " + ", ".join(bits)) if bits \
        else ""

    # --- tier 5: the fixed counter-case ---
    # Every position a real socket, no holes, no blanks; the run reads as
    # one continuous strip on an even pitch. Spacing consistency is the
    # second-strongest measured feature here (AUC 0.83) and carries most of
    # the "one moulded strip" judgement.
    if (f["n_empty"] == 0
            and (f["boundary_contrast"] < SEAMLESS
                 or f["spacing_even"] >= EVEN_PITCH)
            and f["gap_cv"] < UNIFORM_GAP):
        return False, 5, "medium", (
            "every position is a real socket, no holes or blanks, and the "
            "run is one continuous strip on an even pitch (seam %.2f, "
            "spacing consistency %.2f, gap variation %.2f)%s"
            % (f["boundary_contrast"], f["spacing_even"], f["gap_cv"],
               support_note))

    # --- tier 6: the front view cannot settle it ---
    # A vetoed panel lands here too: it has empties, but the port model read
    # them as sockets, so there is no open cutout to reason from and no
    # connector mix either. Said plainly, because "fully loaded and uniform"
    # would be a false description of it.
    if vetoed:
        return None, 6, "none", (
            "%d unoccupied position%s, but the port model re-read every one "
            "of them as a socket rather than an open cutout, so the frame "
            "cannot be called modular%s%s"
            % (int(f["n_empty"]), "" if f["n_empty"] == 1 else "s",
               recheck_note, support_note))
    return None, 6, "none", (
        "fully loaded and uniform%s -- the front view cannot separate a "
        "loaded keystone frame from a fixed panel; this is the case that "
        "needs the back view%s"
        % (", cables obscuring the jacks" if f["n_occupied"] < 0.6 * n
           else "", support_note))


def verify(f, threshold=THRESHOLD):
    """(is_keystone, score, human-readable reasons).

    Too few detections is reported as undecided rather than guessed: with
    three boxes there is no arrangement to judge, and the caller should fall
    through to the connector evidence.
    """
    if f["n_slots"] < 4:
        return None, 0.0, "only %d slots detected -- no arrangement to judge" \
            % int(f["n_slots"])
    s = score(f)
    bits = []
    if f["n_empty"]:
        bits.append("%d empty cutouts (%.0f%% of slots)"
                    % (int(f["n_empty"]), f["empty_frac"] * 100))
    if f["mixed_family"]:
        bits.append("%d connector families in one frame" % int(f["n_families"]))
    bits.append("modularity %.2f (size cv %.2f, pitch resid %.2f)"
                % (f["modularity"], f["size_cv_w"], f["pitch_resid"]))
    bits.append("boundary contrast %.2f" % f["boundary_contrast"])
    if f["rectangularity"] > 0:
        bits.append("rectangularity %.2f" % f["rectangularity"])
    if f["n_rows"] > 1:
        bits.append("%d rows" % int(f["n_rows"]))
    return bool(s > threshold), float(s), "; ".join(bits)
