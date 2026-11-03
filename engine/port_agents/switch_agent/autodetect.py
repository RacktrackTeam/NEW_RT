#!/usr/bin/env python3
"""
autodetect.py - find and number switch ports in a photo without a template.

Used by map_switch.py when no template matches (or with --auto). Works on a photo
cropped roughly to the switch's front panel.

    1. Candidates.  Port openings are dark, roughly rectangular regions of a fixed
                    size. MSER plus a few dark thresholds propose them.
    2. Grid.        Candidates of the same size on the same row vote for the port
                    pitch. Columns from both rows are merged, gaps of a whole number
                    of pitches are filled (ports hidden by cables), and blocks are
                    padded to the same width so their spacing stays even.
    3. SFP.         A second, smaller or wider row of openings to the right of the
                    RJ45 grid is taken as SFP cages and numbered after the RJ45 ports.
    4. Numbering.   "column" (default): 1 top-left, 2 below it, 3 top of the next
                    column ...  "row": the whole top row first, then the bottom row.
"""
from itertools import combinations, product

import cv2
import numpy as np

WORK_H = 240              # photo is resized to this height before detection
SFP_AR = 1.5              # openings wider than this (w/h) are SFP cages, narrower are RJ45
RJ45_MIN_H = 0.14         # RJ45 openings are at least this fraction of the panel height
MIN_SUPPORT = 3           # detections needed (over methods x enhanced versions) to keep a candidate
BLOCK_COLS = (4, 6, 8, 12)   # usual block widths in columns, tried first when several fit


# ----------------------------------------------------------------------------- candidates

def _boxes_from_mask(mask, H, min_ext=0.6):
    out = []
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=4)
    for x, y, w, h, a in stats[1:]:
        if 0.09 * H <= h <= 0.34 * H and 0.7 <= w / h <= 2.4 and a / (w * h) >= min_ext:
            out.append((int(x), int(y), int(w), int(h)))
    return out


def _iou(a, b):
    ix = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    i = ix * iy
    return i / (a[2] * a[3] + b[2] * b[3] - i)


def _stretch(f, lo=1, hi=99):
    a, b = np.percentile(f, (lo, hi))
    return np.clip((f - a) * 255.0 / max(b - a, 1e-3), 0, 255).astype(np.uint8)


def enhanced(gray):
    """Versions of the photo that make openings stand out under bad conditions:
      - as is (contrast-limited equalisation only)
      - lighting removed: divided by a heavily blurred copy, so shadows, dark photos
        and washed-out photos all come out at the same contrast; lightly denoised
      - sharpened, for blurred or upscaled low-resolution photos"""
    H = gray.shape[0]
    clahe = cv2.createCLAHE(2.0, (8, 8))
    f = gray.astype(np.float32)
    den = cv2.GaussianBlur(f, (0, 0), H * 0.006)
    flat = _stretch(den / (cv2.GaussianBlur(den, (0, 0), H * 0.35) + 4.0))
    soft = cv2.GaussianBlur(f, (0, 0), H * 0.01)
    sharp = _stretch(f + 1.5 * (f - cv2.GaussianBlur(f, (0, 0), H * 0.02)))
    return [clahe.apply(gray), clahe.apply(flat), clahe.apply(sharp), clahe.apply(_stretch(soft))]


def candidates(gray, debug=None):
    """Dark rectangular openings (port holes, empty SFP cages), pooled over enhanced
    versions of the photo."""
    versions = enhanced(gray)
    boxes = []
    for g in versions:
        boxes += _candidates_one(g)
    kept = _nms(boxes, gray.shape)
    # a real opening is found by many detector/version pairs; a stray dark patch
    # (chassis next to a block, printed text) by only one or two
    support = [sum(_iou(k, b) >= 0.4 for b in boxes) for k in kept]
    if debug is not None:
        debug["enhanced"] = versions
        debug["candidates"] = list(zip(kept, support))
    return [k for k, n in zip(kept, support) if n >= MIN_SUPPORT]


def _nms(boxes, shape):
    H, W = shape
    # regions cut off by the photo edge are cables or bezel, not openings
    boxes = [b for b in boxes if b[1] > 1 and b[1] + b[3] < H - 1 and b[0] > 1 and b[0] + b[2] < W - 1]
    boxes.sort(key=lambda b: -b[2] * b[3])
    out = []
    for b in boxes:
        if all(_iou(b, o) < 0.4 for o in out):
            out.append(b)
    return out


def _candidates_one(g):
    H = g.shape[0]
    boxes = []
    mser = cv2.MSER_create(delta=4, min_area=int((0.08 * H) ** 2), max_area=int((0.4 * H) ** 2),
                           max_variation=0.5)
    regs, bbs = mser.detectRegions(g)
    for r, (x, y, w, h) in zip(regs, bbs):
        if 0.09 * H <= h <= 0.34 * H and 0.7 <= w / h <= 2.4 and len(r) / (w * h) >= 0.6:
            boxes.append((int(x), int(y), int(w), int(h)))
    # dark relative to the local surroundings, at a few strengths
    k = int(H * 0.45) | 1
    local = cv2.blur(g.astype(np.float32), (k, k))
    for c in (15, 30, 45):
        m = ((local - g) > c).astype(np.uint8) * 255
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        boxes += _boxes_from_mask(m, H)
    return boxes


# ----------------------------------------------------------------------------- grid

def _rows(boxes, tol=0.35):
    """Group boxes of similar height whose centres share a row."""
    rows = []
    for b in sorted(boxes, key=lambda b: b[1] + b[3] / 2):
        cy = b[1] + b[3] / 2
        for r in rows:
            if abs(cy - r["cy"]) < tol * r["h"] and 0.7 < b[3] / r["h"] < 1.4:
                r["boxes"].append(b)
                r["cy"] = np.mean([q[1] + q[3] / 2 for q in r["boxes"]])
                r["h"] = np.median([q[3] for q in r["boxes"]])
                break
        else:
            rows.append({"cy": cy, "h": b[3], "boxes": [b]})
    return rows


def _pitch(xs, w):
    """Smallest spacing that most neighbouring centres are a whole multiple of."""
    xs = sorted(xs)
    d = np.diff(xs)
    d = d[(d > 0.9 * w) & (d < 4.5 * w)]
    if len(d) == 0:
        return None
    best = None
    for p in np.arange(0.95 * w, 1.9 * w, 0.01 * w):
        k = np.round(d / p)
        ok = (k >= 1) & (k <= 3) & (np.abs(d / p - k) < 0.08)
        score = ok.sum() - 0.01 * (p / w)
        if best is None or score > best[0]:
            best = (score, p, ok)
    if best[0] < 1:
        return None
    _, p, ok = best
    k = np.round(d / p)
    one = ok & (k == 1)
    if one.sum() >= 2:                                    # refine on direct neighbours
        return float(np.median(d[one]))
    return float(np.sum(d[ok]) / np.sum(k[ok]))


def _cluster_1d(vals, tol):
    vals = sorted(vals)
    groups = [[vals[0]]]
    for v in vals[1:]:
        if v - np.mean(groups[-1]) < tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [float(np.mean(g)) for g in groups]


def _gap_k(gap, p):
    """Number of pitches a gap between two seen columns spans, or 0 if it is not a
    whole number (a gap between blocks)."""
    k = round(gap / p)
    # blur can pull a column centre ~15% of a pitch towards a neighbouring cable
    return k if 1 <= k <= 3 and abs(gap / p - k) < (0.16 if k == 1 else 0.12) else 0


def _blocks(cols, p, breaks=()):
    """Split column centres into blocks and fill whole-pitch gaps inside a block.
    `breaks` lists indices into cols where a filled gap is split instead (a fill over
    hidden columns is a guess; it can also bridge into a neighbouring SFP strip)."""
    blocks = [[cols[0]]]
    for i, c in enumerate(cols[1:], 1):
        gap = c - blocks[-1][-1]
        k = _gap_k(gap, p)
        if k and i not in breaks:
            blocks[-1] += [blocks[-1][-1] + gap * j / k for j in range(1, k + 1)]
        else:
            blocks.append([c])
    return blocks


def _lattice(blocks, p, W, max_cols=12):
    """Identical port blocks repeat at an even spacing. Find the smallest block width
    (in columns) for which every seen block can be padded so that block starts are
    evenly spaced. Needs 3+ blocks to say anything; returns None if nothing fits."""
    if len(blocks) < 3:
        return None
    lo = max(len(b) for b in blocks)
    # when blocks are missing end columns several widths can fit; usual widths go first
    order = [n for n in BLOCK_COLS if n >= lo] + [n for n in range(lo, max_cols + 1) if n not in BLOCK_COLS]
    for n in order:
        best = None
        for lefts in product(*[range(n - len(b) + 1) for b in blocks]):
            starts = [b[0] - l * p for b, l in zip(blocks, lefts)]
            T = (starts[-1] - starts[0]) / (len(starts) - 1)
            if T < (n + 0.25) * p or starts[0] < -0.5 * p or starts[-1] + (n - 0.5) * p > W:
                continue
            dev = max(abs(st - (starts[0] + i * T)) for i, st in enumerate(starts))
            if dev < 0.25 * p and (best is None or dev < best[0]):
                best = (dev, starts)
        if best:
            return [[st + i * p for i in range(n)] for st in best[1]]
    return None


def _equalise(blocks, p, W):
    """Pad short blocks to the widest block's column count. The side to pad on is
    the one that makes the block-to-block spacing most even."""
    n = max(len(b) for b in blocks)
    if len(blocks) < 2 or all(len(b) == n for b in blocks):
        return blocks
    choices = []
    for b in blocks:
        m = n - len(b)
        choices.append([(left, m - left) for left in range(m + 1)] if m else [(0, 0)])
    best = None
    for combo in product(*choices):
        bs = [[b[0] - p * i for i in range(l, 0, -1)] + b + [b[-1] + p * i for i in range(1, r + 1)]
              for b, (l, r) in zip(blocks, combo)]
        if bs[0][0] < 0 or bs[-1][-1] > W:
            continue
        starts = [b[0] for b in bs]
        overlap = any(bs[i + 1][0] - bs[i][-1] < 1.2 * p for i in range(len(bs) - 1))
        cost = np.std(np.diff(starts)) + (1e6 if overlap else 0)
        if best is None or cost < best[0]:
            best = (cost, bs)
    return best[1] if best else blocks


def _grid_from(group, W):
    """Fit 1 or 2 rows of evenly pitched columns to boxes of one size."""
    rows = [r for r in _rows(group) if len(r["boxes"]) >= 2]
    best = None
    for r in rows:
        # a partner row: directly above or below
        partner = None
        for o in rows:
            if o is not r and 1.05 * r["h"] < abs(o["cy"] - r["cy"]) < 2.2 * r["h"]:
                if partner is None or len(o["boxes"]) > len(partner["boxes"]):
                    partner = o
        rws = sorted([r] + ([partner] if partner else []), key=lambda q: q["cy"])
        members = [b for q in rws for b in q["boxes"]]
        w = float(np.median([b[2] for b in members]))
        p = _pitch([b[0] + b[2] / 2 for b in members], w)
        if p is None:
            continue
        cols = _cluster_1d([b[0] + b[2] / 2 for b in members], 0.35 * p)
        inside = lambda blks: [b for b in members if any(blk[0] - 0.4 * p <= b[0] + b[2] / 2 <= blk[-1] + 0.4 * p
                                                         for blk in blks)]
        fills = [i for i in range(1, len(cols)) if _gap_k(cols[i] - cols[i - 1], p) >= 2]
        found = None
        # try without breaking any filled gap first, then breaking one, two, three
        for nb in range(min(len(fills), 3 if len(fills) <= 6 else 1) + 1):
            for brk in combinations(fills, nb):
                blocks = _blocks(cols, p, brk)
                # a block needs two seen openings; a lone one is a console or management port
                blocks = [blk for blk in blocks if len(inside([blk])) >= 2]
                if not blocks:
                    continue
                blocks, lat = _choose_blocks(blocks, inside(blocks), rws, p, W)
                if found is None or (lat and not found[1]):
                    found = (blocks, lat)
                if lat:
                    break
            if found and found[1]:
                break
        if found is None:
            continue
        blocks, lat = found
        members = inside(blocks)
        # more openings explained wins; evenly spaced blocks count as a 25% bonus
        key = len(members) * (1.25 if lat is not None else 1.0)
        if best is None or key > best["key"]:
            best = {"rows": [float(q["cy"]) for q in rws], "w": w,
                    "h": float(np.median([b[3] for b in members])), "pitch": p,
                    "blocks": lat or _equalise(blocks, p, W), "members": members, "key": key,
                    "seen_blocks": blocks, "even_spacing": lat is not None}
    return best


def _choose_blocks(blocks, members, rws, p, W):
    """Pick which blocks belong to the RJ45 grid, and fit their even spacing.
    In a two-row grid an end block with openings in only one row, next to blocks with
    openings in both, is often an SFP strip; it is dropped when that makes the spacing
    fit. If the spacing doesn't fit, one end block that breaks it is dropped (an SFP
    strip or LED panel blurred into port-like blobs)."""
    def two_rows(blk):
        inside = [b for b in members if blk[0] - 0.4 * p <= b[0] + b[2] / 2 <= blk[-1] + 0.4 * p]
        return len({min(range(len(rws)), key=lambda i: abs(b[1] + b[3] / 2 - rws[i]["cy"]))
                    for b in inside}) == 2
    opts = []
    if len(rws) == 2:
        t = list(blocks)
        while len(t) >= 3 and sum(map(two_rows, t)) >= 2:
            if not two_rows(t[-1]):
                t = t[:-1]
            elif not two_rows(t[0]):
                t = t[1:]
            else:
                break
        if t != blocks:
            opts.append(t)
    opts.append(blocks)
    for o in opts:
        lat = _lattice(o, p, W)
        if lat:
            return o, lat
    for o in opts:
        if len(o) >= 4:
            for keep in (o[:-1], o[1:]):
                lat = _lattice(keep, p, W)
                if lat:
                    return keep, lat
    return opts[0], None


def fit_grid(boxes, W, min_ar=0.0, max_ar=9.0):
    """Find the main port grid. Port openings are all the same size, so candidates
    are first grouped by size; the size group that forms the largest grid wins."""
    best = None
    for s in boxes:
        if not min_ar <= s[2] / s[3] < max_ar:
            continue
        group = [b for b in boxes if 0.8 < b[2] / s[2] < 1.25 and 0.8 < b[3] / s[3] < 1.25]
        if len(group) < 2:
            continue
        g = _grid_from(group, W)
        if g and (best is None or g["key"] > best["key"]):
            best = g
    return best


def _unhalve_columns(grid, expected_rj45):
    """Twice the expected number of ports means the column pitch was measured at half
    its true value - an RJ45's opening and the latch recess beside it read as two
    separate holes - so every physical port became two cells side by side. Keep every
    other column, in the phase whose cells actually have a detected opening under
    them, and widen the cell to the pair it was split from.

    Only ever runs when the model is known, because only then is "twice as many as
    there should be" evidence of anything: a count on its own is not."""
    if not expected_rj45 or not grid or not grid.get("blocks"):
        return grid
    cols = [c for b in grid["blocks"] for c in b]
    nrows = len(grid["rows"])
    if not cols or not (1.75 * expected_rj45 <= len(cols) * nrows <= 2.25 * expected_rj45):
        return grid
    w, h = grid["w"], grid["h"]
    best = None
    for phase in (0, 1):
        blocks = [b[phase::2] for b in grid["blocks"]]
        if not all(blocks):
            continue
        hits = sum(1 for cx in [c for b in blocks for c in b] for cy in grid["rows"]
                   if any(abs(m[0] + m[2] / 2 - cx) < w / 2 and abs(m[1] + m[3] / 2 - cy) < h / 2
                          for m in grid["members"]))
        if best is None or hits > best[0]:
            best = (hits, blocks)
    if best is None:
        return grid
    return dict(grid, blocks=best[1], w=w * 2)


def _fit_to_expected(grid, expected_rj45, width=None):
    """Make the column run hold exactly the number of ports the model has.

    Two things go wrong on a cabled panel, and they pull in opposite directions. A
    jack half behind a cable is often found twice, a few pixels apart, which inflates
    the column count; and a run of covered ports at one end is found not at all,
    which cuts the block short. Trimming alone fixes the first and makes the second
    worse - it shaves real columns off the end while the duplicates stay.

    So: merge columns closer together than half a pitch, then set the run to the
    model's own column count at the established pitch, growing from whichever end has
    real detections behind it. Only the count is taken on faith; the pitch and the
    anchor still come from what was seen."""
    if not expected_rj45 or not grid or not grid.get("blocks"):
        return grid
    nrows = len(grid["rows"])
    cols = sorted(c for b in grid["blocks"] for c in b)
    want = expected_rj45 // nrows if nrows else 0
    if want < 2 or len(cols) < 2:
        return grid
    # How far the detected run may be stretched to reach the model's count. Kept
    # tight on purpose: when only a third of the ports are found the seeds are as
    # likely to be the LED strip as the port row, and laying the full count from
    # them puts every box on the wrong band of the panel - confidently wrong, which
    # is worse than visibly short. Filling a few hidden ports is safe; inventing
    # most of them is not.
    w, h = grid["w"], grid["h"]

    def seen(cx):
        return sum(1 for cy in grid["rows"]
                   if any(abs(m[0] + m[2] / 2 - cx) < w / 2 and abs(m[1] + m[3] / 2 - cy) < h / 2
                          for m in grid["members"]))

    gaps = [b - a for a, b in zip(cols, cols[1:])]
    pitch = float(np.median([g for g in gaps if g > 0.5 * np.median(gaps)])) if gaps else 0.0
    if pitch <= 0:
        return grid
    # one port found twice: keep whichever of the pair has real evidence
    merged = [cols[0]]
    for c in cols[1:]:
        if c - merged[-1] < 0.5 * pitch:
            if seen(c) > seen(merged[-1]):
                merged[-1] = c
        else:
            merged.append(c)
    if len(merged) == want:
        cols = merged
    elif abs(len(merged) - want) <= max(3, 0.4 * want):
        # anchor on the end with the most evidence and lay `want` columns at pitch
        head = sum(seen(c) for c in merged[:max(1, len(merged) // 3)])
        tail = sum(seen(c) for c in merged[-max(1, len(merged) // 3):])
        if tail > head:
            cols = [merged[-1] - pitch * k for k in range(want)][::-1]
        else:
            cols = [merged[0] + pitch * k for k in range(want)]
        # Extending past the panel would number thin air. When the run will not fit
        # going one way, lay it from the other end instead; if it fits neither, the
        # pitch or the count is wrong and the grid is left as detected.
        if width is not None:
            half = grid["w"] / 2.0
            if cols[-1] + half > width or cols[0] - half < 0:
                alt = ([merged[-1] - pitch * k for k in range(want)][::-1]
                       if tail <= head else [merged[0] + pitch * k for k in range(want)])
                if alt[-1] + half <= width and alt[0] - half >= 0:
                    cols = alt
                else:
                    return grid
    else:
        return grid       # too far off to be these ports at all

    gaps = [b - a for a, b in zip(cols, cols[1:])]
    blocks, cur = [], [cols[0]]
    for a, b in zip(cols, cols[1:]):
        if (b - a) > 1.5 * pitch:
            blocks.append(cur)
            cur = [b]
        else:
            cur.append(b)
    blocks.append(cur)
    return dict(grid, blocks=blocks)


# ----------------------------------------------------------------------------- numbering


# ----------------------------------------------------------------------------- numbering

def _number(grid, kind, first, numbering):
    cols = [c for b in grid["blocks"] for c in b]
    nrows = len(grid["rows"])
    w, h = grid["w"], grid["h"]
    order = ([(ci, ri) for ci in range(len(cols)) for ri in range(nrows)] if numbering == "column"
             else [(ci, ri) for ri in range(nrows) for ci in range(len(cols))])
    out = []
    for n, (ci, ri) in enumerate(order):
        cx, cy = cols[ci], grid["rows"][ri]
        # an open hole was seen here; otherwise the port was filled in from the grid
        # (usually because a cable covers it)
        seen = any(abs(b[0] + b[2] / 2 - cx) < w / 2 and abs(b[1] + b[3] / 2 - cy) < h / 2
                   for b in grid["members"])
        out.append({"id": first + n, "type": kind, "opening_visible": seen,
                    "box": [cx - w / 2, cy - h / 2, w, h]})
    return out


def number_ports(grid, sfp, numbering="column"):
    """RJ45 ports first, then SFP cages, each numbered from the top-left."""
    ports = _number(grid, "rj45", 1, numbering) if grid else []
    if sfp:
        ports += _number(sfp, "sfp", len(ports) + 1, numbering)
    return ports


def detect_ports(bgr, numbering="column", debug=None, method="auto", expected=None):
    """Returns (ports, info). Port boxes are x,y,w,h in the original photo's pixels.
    method "auto": RackTrack's trained port models when available (they find cabled
    ports too), else the classical detector below; "models" or "classical" to force one.
    Pass a dict as `debug` to get the intermediate steps of the classical detector."""
    if method in ("auto", "models"):
        import yolo_ports
        if yolo_ports.available():
            return yolo_ports.detect_ports(bgr, numbering)
        if method == "models":
            raise RuntimeError("RackTrack port models not found (set RACKTRACK_REPO)")
    f = WORK_H / bgr.shape[0]
    img = cv2.resize(bgr, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC if f > 1 else cv2.INTER_AREA)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    boxes = candidates(gray, debug)
    W = gray.shape[1]
    # RJ45 openings are about 1.2x as wide as tall, SFP cages about 1.6-2x
    grid = fit_grid([b for b in boxes if b[3] >= RJ45_MIN_H * WORK_H], W, max_ar=SFP_AR)
    grid = _unhalve_columns(grid, (expected or {}).get("rj45"))
    grid = _fit_to_expected(grid, (expected or {}).get("rj45"), gray.shape[1])
    if grid:
        # SFP cages: a row of openings beside the RJ45 grid, level with its rows and of a
        # similar height (their shape varies with the cage and dust plugs)
        x0 = grid["blocks"][0][0] - 0.6 * grid["pitch"]
        x1 = grid["blocks"][-1][-1] + 0.6 * grid["pitch"]
        top, bot = grid["rows"][0] - grid["h"] / 2, grid["rows"][-1] + grid["h"] / 2
        boxes_sfp = [b for b in boxes if not x0 <= b[0] + b[2] / 2 <= x1
                     and 0.6 < b[3] / grid["h"] < 1.3 and top <= b[1] + b[3] / 2 <= bot]
        sfp = fit_grid(boxes_sfp, W, min_ar=0.9)
    else:
        sfp = fit_grid(boxes, W, min_ar=SFP_AR)
    if debug is not None:
        debug.update(scale=f, work=img, grid=grid, sfp=sfp)
    if grid is None and sfp is None:
        return [], {"candidates": len(boxes), "seen": 0}
    ports = number_ports(grid, sfp, numbering)
    for p in ports:
        p["box"] = [round(v / f, 1) for v in p["box"]]
    info = {"candidates": len(boxes),
            "seen": sum(len(g["members"]) for g in (grid, sfp) if g),
            "rj45": sum(p["type"] == "rj45" for p in ports), "sfp": sum(p["type"] == "sfp" for p in ports)}
    if grid:
        info.update(rows=len(grid["rows"]), blocks=[len(b) for b in grid["blocks"]],
                    pitch_px=round(grid["pitch"] / f, 1))
    return ports, info
