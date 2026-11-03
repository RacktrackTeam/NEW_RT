"""Read the printed flags on the cables in a rack photo or a close-up.

A cable flag is a small white tag wrapped round a cable. It carries one line
per end of the cable, each line in the site's own words, typically
    <device name> <port> <rack position>
    nvs-sgkc-sis-sw05b Gi1-0-14 B12
and it hangs from the cable, so its text stands at 90 degrees to the photo
and is a few pixels tall. The rack photo reader (pipeline/ocr_labels.py)
reads upright text of 20 pixels and more in one pass over the whole photo,
so it walks past every flag.

This module reads the photo in tiles, each tile enlarged (twice and three
times) and turned three ways (as it is, a quarter turn left, a quarter turn right), with RapidOCR,
whose detector finds text lines of a few pixels once the tile is enlarged
and whose input-size cap is raised so the enlargement is not undone. Every
line read is then:

  1. looked at again
                 a line that did not parse, or is unsure, is cut out along
                 its own angle and read once more as one piece of text, so a
                 flag hanging at 70 degrees is read upright too
     a second chance
                 a line still read poorly once kept (handwritten, faint:
                 no device, or under 0.80) is straightened larger and read
                 four more ways (contrast evened out, black and white two
                 ways, sharpened); the reading that parses furthest and whose
                 device the names known repair replaces it (handwriting_pass)
  2. joined      pieces of one line the detector cut apart (a flag folded or
                 curved) are put back together on their baseline
  3. merged      the same line read in two tiles or two turns is kept once,
                 the fuller reading first, the surer reading second
  4. repaired    the camera's usual confusions (O/0, I/1, S/5, B/8) put right
                 only where a character can be nothing else: inside a rack
                 position (B12), inside the digits of a port (Gi1-0-14),
                 between the digits of a device name (sw05b)
  5. parsed      into the device, the port and the rack position when the
                 line has that shape; a line without it (a faceplate word, a
                 serial, a rail number) is kept as read with ends []
  6. repaired against the photo
                 a flag's device name that is cut or one character off a
                 name the reader read with confidence elsewhere on the same
                 photo (a faceplate's nvs-sgkc-sis-rt02) carries that name as
                 deviceRepaired, deviceMatch "photo"; two names that fit is
                 no repair
  7. grouped     lines that sit side by side on one tag are one flag

Nothing is invented: every line keeps its raw reading next to the repaired
one, a line the parser cannot place has no ends, and the confidence is the
reader's own score.

Output (boxes are [x1, y1, x2, y2] in image pixels):

    {
      "image_size": {"w": 1859, "h": 1395},
      "lines": [
        {"box": [..], "text": "nvs-sgkc-sis-sw05b Gi1-0-14 B12", "raw": "...",
         "conf": 0.95, "orientation": "vertical" | "horizontal", "turn": 0 | 90 | -90,
         "end": {"device", "port", "position", "portNo"} | null, "flag": 3}
      ],
      "flags": [{"id": 3, "box": [..], "lines": [line index, ...], "text": "line / line"}],
      "stats": {"tiles": 12, "reads": 61, "kept": 44, "ends": 21, "ms": 9800}
    }

Run on its own:
    python -m pipeline.cable_flags photo.jpg [--json] [--debug-dir out/]
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
import time

import cv2
import numpy as np

# ---------------------------------------------------------------- settings

# Tiles are sized on a 1859 px wide reference (a messaging-app copy); a
# larger photo gets proportionally larger tiles and less enlargement, so the
# reader always sees text at about the same size.
REF_WIDTH = 1859.0
TILE_REF = 800          # tile side at REF_WIDTH
OVERLAP_REF = 260       # tile overlap at REF_WIDTH: longer than a flag, so each flag is whole in one tile
ENLARGES_REF = (2.0, 3.0)  # enlargements at REF_WIDTH: each finds lines the other misses (31 of 47 read together, 27 alone)
DET_LIMIT = 2400        # RapidOCR detector input cap (its default, 736, shrinks a tile to nothing)
MIN_SCORE = 0.40        # reader score under which a line is dropped
MIN_CHARS = 2
TURNS = (0, 90, -90)    # as is, quarter turn clockwise, quarter turn anticlockwise
SECOND_LOOK_CONF = 0.85 # a line surer than this that parses is not read again
SECOND_LOOK_HEIGHT = 56 # the straightened line's text height for the second look
PHOTO_VOCAB = True      # repair a flag's device name against names read confidently on the photo
HAND_PASS = True        # the second chance for handwritten and faint lines (handwriting_pass)
HAND_CONF = 0.80        # a kept line under this, or one that names no device, gets the second chance
HAND_HEIGHT = 80        # the straightened line's text height for it: larger than the second look
HAND_MAX = 160          # at most this many lines per photo, the least sure first


def _k(w: int) -> float:
    return max(0.25, float(w) / REF_WIDTH)


# ---------------------------------------------------------------- reader

_READER = None


def _reader():
    """RapidOCR, built once. Its logging is turned down: the worker's stdout
    is a JSON protocol and the reader announces every model it loads."""
    global _READER
    if _READER is None:
        for name in ("RapidOCR", "rapidocr"):
            logging.getLogger(name).setLevel(logging.ERROR)
        from rapidocr import RapidOCR

        try:
            _READER = RapidOCR(params={"Det.limit_side_len": DET_LIMIT, "Det.limit_type": "max"})
        except Exception:
            _READER = RapidOCR()
        for name in ("RapidOCR", "rapidocr"):
            logging.getLogger(name).setLevel(logging.ERROR)
    return _READER


def _run_reader(reader, img) -> list[tuple[np.ndarray, str, float]]:
    """[(polygon 4x2, text, score)] in the image's own pixels."""
    try:
        res = reader(img, use_cls=False)
    except Exception:
        return []
    txts = getattr(res, "txts", None) or ()
    scores = getattr(res, "scores", None) or ()
    boxes = getattr(res, "boxes", None)
    out = []
    for i, (t, s) in enumerate(zip(txts, scores)):
        t = (t or "").strip()
        if len(t) < MIN_CHARS or float(s) < MIN_SCORE:
            continue
        if boxes is None or i >= len(boxes):
            continue
        poly = np.asarray(boxes[i], np.float32).reshape(-1, 2)
        out.append((poly, t, float(s)))
    return out


_REC = None


def _rec_reader():
    """A second RapidOCR that only recognises: it takes a straightened line
    as one piece of text and reads it, with no detector in front. Its own
    instance, because RapidOCR keeps the last call's settings, and a
    recognition-only call would switch the detector off for the tile reads."""
    global _REC
    if _REC is None:
        for name in ("RapidOCR", "rapidocr"):
            logging.getLogger(name).setLevel(logging.ERROR)
        from rapidocr import RapidOCR

        _REC = RapidOCR(params={"Global.use_det": False, "Global.use_cls": False})
    return _REC


def straighten_poly(img: np.ndarray, poly, height: int = SECOND_LOOK_HEIGHT, pad: float = 0.18):
    """The text box cut out along its own long edge, whatever its angle, with
    a margin all round and the text enlarged to `height` pixels. None when
    the box is degenerate."""
    pts = np.asarray(poly, np.float32).reshape(-1, 2)
    if pts.shape[0] != 4:
        return None
    d01 = float(np.linalg.norm(pts[0] - pts[1]))
    d12 = float(np.linalg.norm(pts[1] - pts[2]))
    if d01 >= d12:
        long_side, short_side, src = d01, d12, np.array([pts[0], pts[1], pts[2], pts[3]], np.float32)
    else:
        long_side, short_side, src = d12, d01, np.array([pts[1], pts[2], pts[3], pts[0]], np.float32)
    if long_side < 4 or short_side < 3:
        return None
    f = height / short_side
    px, py = long_side * pad * f, short_side * pad * f
    W = int(round(long_side * f + 2 * px))
    H = int(round(short_side * f + 2 * py))
    if W < 8 or H < 8 or W > 4000:
        return None
    dst = np.array([[px, py], [px + long_side * f, py], [px + long_side * f, py + short_side * f], [px, py + short_side * f]], np.float32)
    M = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(img, M, (W, H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _recognise(crop) -> tuple[str, float]:
    """One straightened line read both ways up; the fuller, surer reading."""
    reader = _rec_reader()
    best = ("", 0.0)
    for rot in (None, cv2.ROTATE_180):
        im = crop if rot is None else cv2.rotate(crop, rot)
        try:
            res = reader(im)
        except Exception:
            continue
        txts = getattr(res, "txts", None) or ()
        scores = getattr(res, "scores", None) or ()
        if not txts:
            continue
        t = (txts[0] or "").strip()
        sc = float(scores[0]) if scores else 0.0
        if sc * max(1, len(t)) > best[1] * max(1, len(best[0])):
            best = (t, sc)
    return best


def _parts(text: str) -> int:
    e = parse_end(repair_line(text))
    return 0 if not e else (1 if e.get("device") else 0) + (1 if e.get("port") else 0) + (1 if e.get("position") else 0)


def second_look(img: np.ndarray, reads: list[dict]) -> int:
    """Every reading that did not parse, or is unsure, read once more from
    its own box straightened along its own angle. The quarter turns serve a
    flag hanging straight; a flag hanging at 70 degrees is read upright only
    this way. The new reading replaces the old one when it parses further,
    or parses as well and is clearly surer. Returns how many were replaced."""
    n = 0
    for r in reads:
        old_parts = _parts(r["text"])
        if old_parts == 3 and r["conf"] >= SECOND_LOOK_CONF:
            continue
        crop = straighten_poly(img, r["poly"])
        if crop is None:
            continue
        text, conf = _recognise(crop)
        if not text or len(text) < MIN_CHARS or conf < MIN_SCORE:
            continue
        new_parts = _parts(text)
        better = new_parts > old_parts or (new_parts == old_parts and conf * len(text) > 1.05 * r["conf"] * len(r["text"]))
        if better:
            r["text"], r["conf"], r["second"] = text, round(conf, 3), True
            n += 1
    return n


# ---------------------------------------------------------------- second chance

def image_variants(crop: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """A straightened line made easier to read four ways: local contrast
    evened out (CLAHE), that turned black and white (Otsu), black and white
    by neighbourhood (adaptive, for a tag lit unevenly), and sharpened. Pen
    on a tag is thin and grey; each brings out strokes the others lose."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(4, 4)).apply(gray)
    _, otsu = cv2.threshold(cv2.GaussianBlur(clahe, (3, 3), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    block = max(15, (min(gray.shape[:2]) // 2) | 1)
    adaptive = cv2.adaptiveThreshold(clahe, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, 9)
    blur = cv2.GaussianBlur(gray, (0, 0), 2.0)
    sharp = cv2.addWeighted(gray, 1.7, blur, -0.7, 0)
    out = []
    for name, im in (("clahe", clahe), ("otsu", otsu), ("adaptive", adaptive), ("sharp", sharp)):
        out.append((name, cv2.cvtColor(im, cv2.COLOR_GRAY2BGR)))
    return out


def extend_poly(poly, factor: float, w: int, h: int):
    """The box grown along its own text direction by `factor` of its length
    at each end (kept inside the image): a line the detector cut short
    ("nvs-50kc" of a whole flag line) is read again with the rest of it."""
    p = np.asarray(poly, np.float32).reshape(-1, 2)
    if p.shape[0] != 4:
        return None
    d01 = float(np.linalg.norm(p[1] - p[0]))
    d12 = float(np.linalg.norm(p[2] - p[1]))
    if d01 >= d12:
        u = (p[1] - p[0]) / max(d01, 1e-6)
        grow = u * d01 * factor
        q = np.array([p[0] - grow, p[1] + grow, p[2] + grow, p[3] - grow], np.float32)
    else:
        u = (p[2] - p[1]) / max(d12, 1e-6)
        grow = u * d12 * factor
        q = np.array([p[0] - grow, p[1] - grow, p[2] + grow, p[3] + grow], np.float32)
    q[:, 0] = np.clip(q[:, 0], 0, w - 1)
    q[:, 1] = np.clip(q[:, 1], 0, h - 1)
    return q


def reading_score(text: str, names: list[str] | None = None) -> tuple[int, int]:
    """How far a reading goes: (parts parsed, how well its device fits). The
    device fits best (2) when the names known (the Excel's, or those read
    confidently on the photo) repair it, next (1) when it at least has a
    device name's shape (letters, two dashes or more), else 0."""
    e = parse_end(repair_line(text))
    if not e:
        return (0, 0)
    parts = (1 if e.get("device") else 0) + (1 if e.get("port") else 0) + (1 if e.get("position") else 0)
    dev = e.get("device") or ""
    fit = 0
    if dev:
        if names and (_canon(dev) in {_canon(n) for n in names} or repair_against(dev, names)):
            fit = 2
        elif _NAME_RE.match(dev):
            fit = 1
    return (parts, fit)


def _worth_a_second_chance(ln: dict) -> bool:
    t = ln.get("text") or ""
    c = _canon(t)
    if len(c) < 5 or not re.search(r"[A-Z]", c) or not ("-" in t or re.search(r"\d", t)):
        return False
    e = ln.get("end")
    return not e or not e.get("device") or ln.get("conf", 0) < HAND_CONF


def handwriting_pass(img: np.ndarray, lines: list[dict], names: list[str] | None = None) -> int:
    """The second chance for a line read poorly (handwritten, faint, or
    cut): its box straightened larger than the second look, made easier to
    read four ways (image_variants), and each read both ways up. The reading
    that parses furthest, then whose device the names known repair (the
    Excel's when the caller gives them, else the photo's own), then the
    surest, replaces the old one, and only when it goes further than the old
    reading or is as far and clearly surer. Nothing is guessed: a variant that
    reads no further leaves the line as it was. Returns how many changed."""
    names = list(names or []) + photo_vocabulary(lines)
    todo = [ln for ln in lines if ln.get("poly") is not None and _worth_a_second_chance(ln)]
    todo.sort(key=lambda ln: ln.get("conf", 0))
    n = 0
    for ln in todo[:HAND_MAX]:
        crop = straighten_poly(img, ln["poly"], height=HAND_HEIGHT, pad=0.22)
        if crop is None:
            continue
        old = reading_score(ln["raw"] if ln.get("raw") else ln["text"], names)
        best = None
        tries = list(image_variants(crop))
        # A line cut short: read again with the rest of the flag around it.
        if not ln.get("end"):
            hh, ww = img.shape[:2]
            for f in (0.6, 1.2):
                grown = extend_poly(ln["poly"], f, ww, hh)
                g = straighten_poly(img, grown, height=HAND_HEIGHT, pad=0.12) if grown is not None else None
                if g is not None:
                    tries.append((f"longer{f}", g))
                    tries.append((f"longer{f}-clahe", image_variants(g)[0][1]))
        for name, im in tries:
            text, conf = _recognise(im)
            if not text or len(text) < MIN_CHARS or conf < MIN_SCORE:
                continue
            sc = reading_score(text, names)
            key = (sc, conf * len(text))
            if best is None or key > best[0]:
                best = (key, text, conf, name)
        if best is None:
            continue
        (sc, weight), text, conf, name = best
        further = sc > old
        surer = sc == old and sc[0] >= 1 and weight > 1.1 * ln.get("conf", 0) * len(ln.get("text") or "")
        if sc[0] < old[0] or not (further or surer):
            continue
        ln["raw"] = text
        ln["text"] = repair_line(text)
        ln["end"] = parse_end(ln["text"])
        ln["conf"] = round(conf, 3)
        ln["hand"] = name
        n += 1
    return n


# ---------------------------------------------------------------- tiles

def tiles_of(w: int, h: int) -> list[tuple[int, int, int, int]]:
    """Tile windows [x1, y1, x2, y2] covering the image with overlap."""
    k = _k(w)
    side = max(256, int(round(TILE_REF * k)))
    step = max(64, side - int(round(OVERLAP_REF * k)))
    xs = list(range(0, max(1, w - side) + 1, step))
    ys = list(range(0, max(1, h - side) + 1, step))
    if xs[-1] + side < w:
        xs.append(max(0, w - side))
    if ys[-1] + side < h:
        ys.append(max(0, h - side))
    out = []
    for y in ys:
        for x in xs:
            out.append((x, y, min(w, x + side), min(h, y + side)))
    return out


def _unturn(poly: np.ndarray, turn: int, tw: int, th: int) -> np.ndarray:
    """Points in the turned tile (tw x th before turning) back to the tile."""
    p = poly.copy()
    if turn == 90:      # cv2.ROTATE_90_CLOCKWISE: (x, y) -> (th - 1 - y, x)
        x = p[:, 1]
        y = (th - 1) - p[:, 0]
        return np.stack([x, y], axis=1)
    if turn == -90:     # cv2.ROTATE_90_COUNTERCLOCKWISE: (x, y) -> (y, tw - 1 - x)
        x = (tw - 1) - p[:, 1]
        y = p[:, 0]
        return np.stack([x, y], axis=1)
    return p


def read_tiles(img: np.ndarray, reader=None) -> list[dict]:
    """Every line read in every tile and turn, in image pixels:
    [{box, poly, text, conf, turn, orientation}]."""
    reader = reader or _reader()
    h, w = img.shape[:2]
    factors = sorted({round(min(3.0, max(1.0, e / _k(w))), 2) for e in ENLARGES_REF})
    out = []
    for (x1, y1, x2, y2) in tiles_of(w, h):
        tile = img[y1:y2, x1:x2]
        if tile.size == 0:
            continue
        for f in factors:
            big = tile if f <= 1.01 else cv2.resize(tile, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
            th, tw = big.shape[:2]
            out.extend(_read_turns(reader, big, tw, th, f, x1, y1))
    return out


def _quad_axis(poly):
    """The text direction (unit vector along the long edge) and the text
    height of a four-point polygon."""
    p = np.asarray(poly, np.float32)
    e0 = p[1] - p[0]
    e1 = p[2] - p[1]
    l0 = float(np.linalg.norm(e0))
    l1 = float(np.linalg.norm(e1))
    if l0 >= l1:
        return e0 / max(l0, 1e-6), max(1.0, l1), l0
    return e1 / max(l1, 1e-6), max(1.0, l0), l1


def join_pieces(reads, max_gap=1.6, max_offset=0.6, max_angle_deg=25.0):
    """Pieces of one line the detector cut apart, joined into one reading: a
    flag folded or curved is read as "nvs-sgkc-sis-waf01e2", "FO", "B11".
    Two pieces join when they run the same way, sit on one baseline (offset
    across the text under max_offset heights) and the gap between them along
    the text is under max_gap heights. Two lines side by side on one tag are
    a height apart across the text, so they never join."""
    n = len(reads)
    if n < 2:
        return list(reads)
    axes = [_quad_axis(r["poly"]) for r in reads]
    centres = [np.mean(np.asarray(r["poly"], np.float32), axis=0) for r in reads]
    cos_max = math.cos(math.radians(max_angle_deg))
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        di, hi, li = axes[i]
        for j in range(i + 1, n):
            dj, hj, lj = axes[j]
            if max(hi, hj) > 1.8 * min(hi, hj):
                continue
            if abs(float(np.dot(di, dj))) < cos_max:
                continue
            v = centres[j] - centres[i]
            along = float(np.dot(v, di))
            across = float(abs(v[0] * di[1] - v[1] * di[0]))
            h = (hi + hj) / 2.0
            if across > max_offset * h:
                continue
            gap = abs(along) - (li + lj) / 2.0
            if gap > max_gap * h or gap < -0.3 * h:
                continue
            parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = []
    for members in groups.values():
        if len(members) == 1:
            out.append(reads[members[0]])
            continue
        d = axes[members[0]][0]
        members.sort(key=lambda m: float(np.dot(centres[m], d)))
        # Read in the text's own direction: the first piece is the one the
        # text starts at, whichever way the direction vector points.
        first = reads[members[0]]
        fd, _, _ = _quad_axis(first["poly"])
        if float(np.dot(fd, d)) < 0:
            members.reverse()
        pts = np.concatenate([np.asarray(reads[m]["poly"], np.float32) for m in members])
        x1, y1 = float(pts[:, 0].min()), float(pts[:, 1].min())
        x2, y2 = float(pts[:, 0].max()), float(pts[:, 1].max())
        chars = sum(len(reads[m]["text"]) for m in members)
        out.append({
            **reads[members[0]],
            "box": [x1, y1, x2, y2],
            "poly": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
            "text": " ".join(reads[m]["text"] for m in members),
            "conf": round(sum(reads[m]["conf"] * len(reads[m]["text"]) for m in members) / max(1, chars), 3),
            "pieces": len(members),
        })
    return out


def _read_turns(reader, big, tw, th, f, x1, y1):
    out = []
    if True:
        for turn in TURNS:
            got = []
            turned = big if turn == 0 else cv2.rotate(big, cv2.ROTATE_90_CLOCKWISE if turn == 90 else cv2.ROTATE_90_COUNTERCLOCKWISE)
            for poly, text, score in _run_reader(reader, turned):
                p = _unturn(poly, turn, tw, th) / f
                p[:, 0] += x1
                p[:, 1] += y1
                bx1, by1 = float(p[:, 0].min()), float(p[:, 1].min())
                bx2, by2 = float(p[:, 0].max()), float(p[:, 1].max())
                if bx2 - bx1 < 2 or by2 - by1 < 2:
                    continue
                got.append({
                    "box": [bx1, by1, bx2, by2],
                    "poly": [[round(float(px), 1), round(float(py), 1)] for px, py in p],
                    "text": text,
                    "conf": round(score, 3),
                    "turn": turn,
                    "scale": f,
                    "orientation": "vertical" if (by2 - by1) > 1.3 * (bx2 - bx1) else "horizontal",
                })
            # The pieces stay beside what they were joined into; the merge
            # keeps whichever parses further, so a wrong join never costs a
            # line that read well on its own.
            out.extend(got)
            out.extend(j for j in join_pieces(got) if j.get("pieces"))
    return out


# ---------------------------------------------------------------- merge

def _inter(a, b) -> float:
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def _area(a) -> float:
    return max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])


def _canon(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def same_print(a: dict, b: dict) -> bool:
    """The same piece of print read twice: boxes that mostly coincide, or
    one inside the other with one text inside the other. Two neighbouring
    lines that the detector once boxed together stay two lines."""
    i = _inter(a["box"], b["box"])
    if i <= 0:
        return False
    aa, ab = _area(a["box"]), _area(b["box"])
    union = aa + ab - i
    if union > 0 and i / union >= 0.6:
        return True
    small = min(aa, ab)
    if small <= 0 or i / small < 0.7:
        return False
    ca, cb = _canon(a["text"]), _canon(b["text"])
    if not ca or not cb:
        return True
    short, long_ = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
    return short in long_ or short[-6:] in long_ or short[:6] in long_


def _quality(line: dict) -> tuple:
    end = line.get("end")
    parts = 0
    if end:
        parts = (1 if end.get("device") else 0) + (1 if end.get("port") else 0) + (1 if end.get("position") else 0)
    return (parts, len(line["text"]) * line["conf"], line["conf"])


def merge_lines(lines: list[dict]) -> list[dict]:
    """One reading per piece of print: of two that overlap, the one that
    parses further, then the longer, then the surer. (A vote among several
    readings of one line was tried on 30 Sep 2026 and lost two lines: the
    surest reading is more often right than the majority.)"""
    ranked = sorted(lines, key=_quality, reverse=True)
    kept: list[dict] = []
    for ln in ranked:
        if any(same_print(ln, k) for k in kept):
            continue
        kept.append(ln)
    kept.sort(key=lambda l: (l["box"][1], l["box"][0]))
    return kept


# ---------------------------------------------------------------- repair

# Upper-case confusables are read for digits often (O/0, I/1, S/5, B/8);
# in lower case only o, l and the bar are.
_TO_DIGIT = str.maketrans({"O": "0", "o": "0", "Q": "0", "D": "0", "I": "1", "l": "1", "|": "1", "S": "5", "B": "8", "Z": "2", "G": "6"})
_CONFUSABLE = set("OoQDIl|SBZG")
_POSITION_RE = re.compile(r"^([A-Za-z])([0-9OoQDIlSBZG|]{1,2})$")
_DEVICE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)+$", re.I)
# A device name without hyphens, as a flag prints it when the site part is
# cut off: sw05b, pg09i1, waf01e2.
_DEVICE_SHORT_RE = re.compile(r"^[a-z]{1,6}\d{1,3}[a-z]?\d?$")
# Interface names the port token may start with, as printed on these flags
# and in the usual vendor spellings, longest first.
_PORT_PREFIXES = ("MGMT", "Mgmt", "Mod", "Twe", "Eth", "Gig", "LOM", "MGT", "Hu", "Fa", "Fo", "Gi", "Te", "Tw", "Po", "Et", "HA", "FO", "TE", "E", "P", "G", "T")
_PREFIX_RE = re.compile(
    r"^(?:(?i:MGMT|Mgmt|Mod|Twe|Eth|Gig|LOM|MGT|Hu|Fa|Fo|Gi|Te|Tw|Po|Et|HA|FO|TE)|E|P|G|T)(?=[0-9OoQDIlSBZG|])")
# Port words with no number at all, printed as they are.
_PORT_WORDS = {"LOM", "MGMT", "MGT", "FO", "CON", "AUX", "ILO", "IPMI", "BMC", "HA", "CONSOLE"}


def _fix_digit_run(s: str) -> str:
    return s.translate(_TO_DIGIT)


def _fix_mixed_segment(seg: str) -> str:
    """sw05b, pgO9i1, cgO1e1, waf01el, 5w05b: a confusable between digits,
    or between a letter run and a digit, is a digit; a trailing l or o after
    a digit is a digit; a digit that opens a segment before a letter is the
    letter it is usually read for."""
    chars = list(seg)
    n = len(chars)
    for i in range(n):
        c = chars[i]
        if c.isdigit():
            if i == 0 and n > 1 and chars[1].isalpha():
                chars[i] = {"5": "s", "0": "o", "1": "l", "8": "b", "2": "z", "6": "g"}.get(c, c)
            continue
        if c not in _CONFUSABLE:
            continue
        left_digit = any(chars[j].isdigit() for j in range(max(0, i - 2), i))
        right_digit = any(chars[j].isdigit() for j in range(i + 1, min(n, i + 3)))
        if left_digit and right_digit:
            chars[i] = c.translate(_TO_DIGIT)
        elif right_digit and i > 0 and chars[i - 1].isalpha() and chars[i - 1] not in _CONFUSABLE and c in "OoQDI|":
            chars[i] = c.translate(_TO_DIGIT)
        elif i == n - 1 and left_digit and c in "lI|Oo":
            chars[i] = c.translate(_TO_DIGIT)
    return "".join(chars)


def repair_device(tok: str) -> str:
    parts = []
    for seg in tok.strip("-").split("-"):
        if any(ch.isdigit() for ch in seg):
            parts.append(_fix_mixed_segment(seg))
        else:
            parts.append(seg)
    return "-".join(parts)


def is_port_token(tok: str) -> bool:
    """Gi1-0-14, G12-0-3, Eth1/16, E1-48, Mod3-4B, HA1-A, TE1, P4-0, LOM."""
    t = tok.strip("-/")
    if not t:
        return False
    if t.upper() in _PORT_WORDS:
        return True
    if _PREFIX_RE.match(t):
        return True
    # Upper-case letters then a digit, with no lower case: SSP-40 is not a
    # port on a flag, but E1, P4 and TE0 are; keep it to short heads.
    if not re.match(r"^[A-Z]{1,3}[0-9OoQDIlSBZG|]", t) or any(ch.islower() for ch in t):
        return False
    return len(t) <= 5 or "-" in t[:6] or "/" in t[:6]


def repair_port(tok: str) -> str:
    """The leading letters stay; what follows is digits and separators, a
    single trailing letter allowed (Mod3-4B, HA1-A). G1 is Gi: no
    interface is named G1."""
    t = tok.strip("-/")
    if t.upper() in _PORT_WORDS:
        return t.upper()
    m = re.match(r"^([A-Za-z]+)(.*)$", t)
    if not m:
        return t
    head, tail = m.group(1), m.group(2)
    if head == "G" and tail[:1] in ("1", "l", "I", "|") and len(tail) > 1 and (tail[1].isdigit() or tail[1] in _CONFUSABLE):
        head, tail = "Gi", tail[1:]
    # E1h1-24, Elh1-24: the t of Eth read as 1 or l.
    if head == "E" and re.match(r"^[1lI|]h", tail):
        head, tail = "Eth", tail[2:]
    elif head in ("Elh", "EIh"):
        head = "Eth"
    fixed = []
    for part in re.split(r"([-/])", tail):
        if part in ("-", "/", ""):
            fixed.append(part)
            continue
        if part.isalpha() and len(part) <= 2:
            fixed.append(part.upper())
            continue
        if part[-1].isalpha() and len(part) >= 2 and all(ch.isdigit() or ch in _CONFUSABLE for ch in part[:-1]):
            fixed.append(_fix_digit_run(part[:-1]) + part[-1].upper())
            continue
        fixed.append(_fix_digit_run(part))
    return head + "".join(fixed)


def repair_position(tok: str) -> str:
    m = _POSITION_RE.match(tok)
    if not m:
        return tok
    return m.group(1).upper() + _fix_digit_run(m.group(2))


def split_glued(text: str) -> str:
    """A port glued to the device or the position: pg09i1Eth1-10B09 ->
    pg09i1 Eth1-10 B09. A known interface name after a letter or digit
    opens a new token; a rack position (letter, two digits) at the end does
    too; and a B read as 8 between the port's number and the position
    (Eth1-13809) is put back."""
    t = text
    for pre in sorted(_PORT_PREFIXES, key=len, reverse=True):
        if pre in ("E", "P", "G", "T"):
            continue
        t = re.sub(rf"(?<=[0-9a-z])({pre})(?=[0-9OoIl|])", r" \1", t, flags=re.I)
    t = re.sub(r"(?<=\d)([A-Z][0-9OoIl|]{2})$", r" \1", t)
    t = re.sub(r"(?<=\d)8(\d{2})$", r" B\1", t)
    t = re.sub(r"(?<=\d) 8(\d{2})$", r" B\1", t)
    return t


def repair_line(raw: str) -> str:
    text = raw.replace("\u2014", "-").replace("\u2013", "-").replace("~", "-").replace("_", "-").strip()
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"[^\w\s/|.-]", "", text)
    text = split_glued(text)
    toks = [t.strip("-") for t in text.split()]
    toks = [t for t in toks if t]
    out = []
    for i, tok in enumerate(toks):
        if is_port_token(tok):
            out.append(repair_port(tok))
        elif "-" in tok and _DEVICE_RE.match(tok.replace("|", "1")):
            out.append(repair_device(tok))
        elif i == len(toks) - 1 and len(toks) >= 2 and _POSITION_RE.match(tok) and not tok.isalpha():
            out.append(repair_position(tok))
        elif _DEVICE_SHORT_RE.match(tok.replace("|", "1").replace("O", "0")):
            out.append(_fix_mixed_segment(tok))
        else:
            out.append(tok)
    return " ".join(out)


# ---------------------------------------------------------------- parse

def port_number(port: str | None) -> int | None:
    """The port's number as the check compares it: the last run of digits."""
    if not port:
        return None
    m = re.search(r"(\d+)(?!.*\d)", port)
    return int(m.group(1)) if m else None


def parse_end(line: str) -> dict | None:
    """One line of a flag as its device, port and rack position, or None
    when the line does not have that shape: at most two tokens of device
    name, then a port, then optionally a rack position (B12). A line that
    starts with the port (its device cut off) is an end with device None."""
    toks = line.split()
    if not toks:
        return None
    port_i = next((i for i, t in enumerate(toks) if is_port_token(t)), None)
    if port_i is None or port_i > 2:
        return None
    before = toks[:port_i]
    if before and not all(_DEVICE_RE.match(t) or _DEVICE_SHORT_RE.match(t) or t.isdigit() or re.fullmatch(r"[a-z0-9]+", t) for t in before):
        return None
    device = "-".join(before) if before else None
    if device and not any(ch.isalpha() for ch in device):
        device = None
    rest = toks[port_i:]
    position = None
    if len(rest) >= 2 and re.fullmatch(r"[A-Z]\d{1,2}", rest[-1]):
        position = rest[-1]
        rest = rest[:-1]
    port = " ".join(rest).strip()
    if not port or len(rest) > 2:
        return None
    if device is None and position is None:
        return None
    return {"device": device, "port": port, "position": position, "portNo": port_number(port)}


# ---------------------------------------------------------------- photo vocabulary

_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+){2,}$", re.I)


def _one_edit(a: str, b: str) -> bool:
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(1 for x, y in zip(a, b) if x != y) == 1
    s_, l_ = (a, b) if len(a) < len(b) else (b, a)
    i = j = 0
    skipped = False
    while i < len(s_) and j < len(l_):
        if s_[i] == l_[j]:
            i += 1
            j += 1
            continue
        if skipped:
            return False
        skipped = True
        j += 1
    return True


def photo_vocabulary(lines: list[dict]) -> list[str]:
    """The device names the reader itself read with confidence on this
    photo: name-shaped lines with no end (a faceplate's nvs-sgkc-sis-rt02)
    read at 0.90 or more, and flag ends of four segments or more read at
    0.97 or more. One entry per name once normalised."""
    seen: dict[str, str] = {}
    for ln in lines:
        text = ln["text"].strip()
        e = ln.get("end")
        if e:
            name = e.get("device") or ""
            if ln["conf"] < 0.97 or name.count("-") < 3 or not _NAME_RE.match(name):
                continue
        else:
            name = text
            if ln["conf"] < 0.90 or len(name) < 10 or not _NAME_RE.match(name):
                continue
        seen.setdefault(_canon(name), name)
    return list(seen.values())


def repair_against(token: str, names: list[str]) -> str | None:
    """A flag's device token against the photo's names: the same once
    normalised, one character off exactly one name (six characters or
    more), or the token as the end of exactly one name (four or more).
    None when nothing fits, or two do."""
    c = _canon(token)
    if not c or not names:
        return None
    by = {_canon(n): n for n in names}
    if c in by:
        return by[c] if by[c] != token else None
    if len(c) >= 6:
        near = [n for k, n in by.items() if _one_edit(c, k)]
        if len(near) == 1:
            return near[0]
        if len(near) > 1:
            return None
    if len(c) >= 4:
        tails = [n for k, n in by.items() if len(k) > len(c) and k.endswith(c)]
        if len(tails) == 1:
            return tails[0]
    return None


def apply_photo_vocabulary(lines: list[dict]) -> int:
    names = photo_vocabulary(lines)
    n = 0
    for ln in lines:
        e = ln.get("end")
        if not e or not e.get("device"):
            continue
        name = repair_against(e["device"], names)
        if name and _canon(name) != _canon(e["device"]):
            e["deviceRepaired"] = name
            e["deviceMatch"] = "photo"
            n += 1
    return n


# ---------------------------------------------------------------- flags

def _gap(a, b, axis: int) -> float:
    """Distance between two boxes along one axis (0 = x, 1 = y); 0 when they overlap."""
    lo = 2 * axis
    hi = lo + 2 - 1 + 1 if False else lo + 2
    a1, a2 = a[axis], a[axis + 2]
    b1, b2 = b[axis], b[axis + 2]
    return max(0.0, max(a1, b1) - min(a2, b2))


def _overlap_len(a, b, axis: int) -> float:
    a1, a2 = a[axis], a[axis + 2]
    b1, b2 = b[axis], b[axis + 2]
    return max(0.0, min(a2, b2) - max(a1, b1))


def same_flag(a: dict, b: dict) -> bool:
    """Two lines on one tag: the same way round, side by side across the
    text (a gap under one line's thickness) and overlapping along it."""
    if a["orientation"] != b["orientation"]:
        return False
    along, across = (1, 0) if a["orientation"] == "vertical" else (0, 1)
    ta = a["box"][across + 2] - a["box"][across]
    tb = b["box"][across + 2] - b["box"][across]
    thick = max(1.0, (ta + tb) / 2.0)
    if _gap(a["box"], b["box"], across) > 1.1 * thick:
        return False
    la = a["box"][along + 2] - a["box"][along]
    lb = b["box"][along + 2] - b["box"][along]
    ov = _overlap_len(a["box"], b["box"], along)
    return ov >= 0.35 * min(la, lb)


def group_flags(lines: list[dict]) -> list[dict]:
    n = len(lines)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if same_flag(lines[i], lines[j]):
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    flags = []
    for gid, (root, members) in enumerate(sorted(groups.items(), key=lambda kv: min(lines[m]["box"][1] for m in kv[1]))):
        along_axis = 0 if lines[members[0]]["orientation"] == "vertical" else 1
        members.sort(key=lambda m: lines[m]["box"][along_axis])
        xs1 = min(lines[m]["box"][0] for m in members)
        ys1 = min(lines[m]["box"][1] for m in members)
        xs2 = max(lines[m]["box"][2] for m in members)
        ys2 = max(lines[m]["box"][3] for m in members)
        for m in members:
            lines[m]["flag"] = gid
        flags.append({
            "id": gid,
            "box": [int(xs1), int(ys1), int(math.ceil(xs2)), int(math.ceil(ys2))],
            "lines": members,
            "text": " / ".join(lines[m]["text"] for m in members),
            "ends": sum(1 for m in members if lines[m].get("end")),
        })
    return flags


# ---------------------------------------------------------------- whole photo

def read_flags(image_path: str | None = None, img: np.ndarray | None = None, reader=None, debug_dir: str | None = None, vocab: list[str] | None = None) -> dict:
    """The flags of one photo. vocab: device names the site knows (its
    Excel), when the caller has them: the second chance prefers a reading
    whose device they repair."""
    t0 = time.time()
    if img is None:
        img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")
    h, w = img.shape[:2]
    reads = read_tiles(img, reader)
    looked = second_look(img, reads)
    for r in reads:
        r["raw"] = r["text"]
        r["text"] = repair_line(r["raw"])
        r["end"] = parse_end(r["text"])
    lines = merge_lines(reads)
    hand = handwriting_pass(img, lines, vocab) if HAND_PASS else 0
    if hand:
        lines = merge_lines(lines)
    repaired = apply_photo_vocabulary(lines) if PHOTO_VOCAB else 0
    for ln in lines:
        ln["box"] = [int(ln["box"][0]), int(ln["box"][1]), int(math.ceil(ln["box"][2])), int(math.ceil(ln["box"][3]))]
        ln["orientation"] = "vertical" if (ln["box"][3] - ln["box"][1]) > 1.3 * (ln["box"][2] - ln["box"][0]) else "horizontal"
        ln.pop("poly", None)
        ln["flag"] = None
    flags = group_flags(lines)
    if debug_dir:
        os.makedirs(debug_dir, exist_ok=True)
        vis = img.copy()
        for ln in lines:
            col = (40, 160, 40) if ln.get("end") else (0, 140, 230)
            b = ln["box"]
            cv2.rectangle(vis, (b[0], b[1]), (b[2], b[3]), col, 2)
        for f in flags:
            b = f["box"]
            cv2.rectangle(vis, (b[0] - 3, b[1] - 3), (b[2] + 3, b[3] + 3), (200, 40, 40), 1)
        cv2.imwrite(os.path.join(debug_dir, "lines.jpg"), vis)
    return {
        "image_size": {"w": w, "h": h},
        "lines": lines,
        "flags": flags,
        "stats": {
            "tiles": len(tiles_of(w, h)),
            "reads": len(reads),
            "second": looked,
            "hand": hand,
            "repaired": repaired,
            "kept": len(lines),
            "ends": sum(1 for l in lines if l.get("end")),
            "ms": int((time.time() - t0) * 1000),
        },
    }


def main():
    ap = argparse.ArgumentParser(description="Read the cable flags in a photo.")
    ap.add_argument("image")
    ap.add_argument("--debug-dir", default=None)
    ap.add_argument("--json", action="store_true", help="print the JSON only")
    ap.add_argument("--vocab", default=None, help="a file of device names the site knows, one per line")
    args = ap.parse_args()
    vocab = None
    if args.vocab:
        with open(args.vocab, encoding="utf-8") as fh:
            vocab = [ln.strip() for ln in fh if ln.strip()]
    try:
        out = read_flags(args.image, debug_dir=args.debug_dir, vocab=vocab)
    except Exception as e:  # always answer JSON so a caller can parse it
        sys.stdout.write(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)
    if args.json:
        sys.stdout.write(json.dumps(out))
        return
    s = out["stats"]
    print(f"{args.image}: {s['tiles']} tiles, {s['reads']} reads, {s['kept']} lines, {s['ends']} cable ends, {s['ms']} ms")
    for ln in out["lines"]:
        e = ln.get("end")
        tag = "end " if e else "    "
        print(f"  {tag} {ln['conf']:.2f} {ln['orientation'][:1]} {ln['box']}  {ln['text']}" + (f"   raw: {ln['raw']}" if ln["raw"] != ln["text"] else ""))


if __name__ == "__main__":
    main()
