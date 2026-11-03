"""Vendor (catalog) image -> front-face template with numbered port boxes.

The layout is cached as `<vendor_image>.ports.json` next to the image. If auto
detection gets a model wrong, edit that JSON (coordinates are 0-1 fractions of
the cropped front face) and it will be used as-is on the next run.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

import cv2
import numpy as np

from .catalog import CatalogEntry
from .ocr import OcrToken, easyocr_read

TEMPLATE_WIDTH = 1600  # front face is resized to this width for all processing


@dataclass
class Port:
    number: int
    kind: str                 # rj45 | sfp
    box: list[float]          # x0, y0, x1, y1 as fractions of the face


@dataclass
class VendorTemplate:
    entry: CatalogEntry
    image: np.ndarray         # full vendor image (BGR)
    face: np.ndarray          # cropped + resized front face (BGR)
    face_crop: tuple[int, int, int, int]
    ports: list[Port]
    face_tokens: list[OcrToken]   # printed text on the face (alignment anchors)

    def port_polys(self) -> dict[int, np.ndarray]:
        """Port rectangles in face-pixel coordinates (4x2, clockwise from top-left)."""
        h, w = self.face.shape[:2]
        out = {}
        for p in self.ports:
            x0, y0, x1, y1 = p.box
            out[p.number] = np.float32([[x0 * w, y0 * h], [x1 * w, y0 * h],
                                        [x1 * w, y1 * h], [x0 * w, y1 * h]])
        return out


# ------------------------------------------------------------------ loading
def decode_image(data: bytes) -> np.ndarray | None:
    """Decode to BGR; transparent product cut-outs (PNG/WebP) go onto white,
    otherwise their background turns black and the face crop fails."""
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        a = img[:, :, 3:4].astype(np.float32) / 255
        return (img[:, :, :3] * a + 255 * (1 - a)).astype(np.uint8)
    return img


def load_image(entry: CatalogEntry, cache_dir: Path) -> np.ndarray:
    src = entry.vendor_image
    if not src:
        raise FileNotFoundError(f"catalog has no vendor_image for {entry.vendor} {entry.model}")
    if src.startswith(("http://", "https://")):
        cache_dir.mkdir(parents=True, exist_ok=True)
        local = cache_dir / f"{entry.model}{Path(src.split('?')[0]).suffix or '.jpg'}"
        if not local.exists():
            import requests  # urllib lacks a CA bundle on some macOS Pythons
            r = requests.get(src, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            r.raise_for_status()
            local.write_bytes(r.content)
        path = local
    else:
        path = entry.image_path
    img = decode_image(Path(path).read_bytes()) if Path(path).exists() else None
    if img is None:
        raise FileNotFoundError(f"cannot read vendor image {path}")
    return img


def _light_background(g: np.ndarray) -> bool:
    """True for catalog-style shots (switch on white); False for photos with a scene."""
    b = max(4, min(g.shape) // 25)
    border = np.concatenate([g[:b].ravel(), g[-b:].ravel(), g[:, :b].ravel(), g[:, -b:].ravel()])
    return float(np.mean(border > 225)) > 0.6


def _odd(n: int) -> int:
    return n + 1 if n % 2 == 0 else n


def _face_from_ports(img: np.ndarray) -> tuple[int, int, int, int] | None:
    """Front panel located from its ports, for images with a background scene
    (review photos on a desk, in a rack): find the band of equally sized dark
    port openings, then grow out to the panel's long edges around it."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    H, W = g.shape
    th = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV,
                               _odd(max(15, W // 30)), 12)
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(th)
    rects = [st[i][:4] for i in range(1, n)
             if W / 90 < st[i][2] < W / 10 and 0.6 * st[i][3] < st[i][2] < 2.6 * st[i][3]
             and st[i][4] > 0.5 * st[i][2] * st[i][3]]
    if len(rects) < 6:
        return None
    mh = float(np.median([r[3] for r in rects]))
    mw = float(np.median([r[2] for r in rects]))
    rects = [r for r in rects if 0.6 < r[3] / mh < 1.6 and 0.6 < r[2] / mw < 1.6]
    # the horizontal band holding the most openings is the port area
    cys = sorted(r[1] + r[3] / 2 for r in rects)
    best = max(range(len(cys)), key=lambda i: sum(1 for c in cys if cys[i] <= c <= cys[i] + 3 * mh))
    band = [r for r in rects if cys[best] <= r[1] + r[3] / 2 <= cys[best] + 3 * mh]
    if len(band) < 6:
        return None
    px0, px1 = min(r[0] for r in band), max(r[0] + r[2] for r in band)
    py0, py1 = min(r[1] for r in band), max(r[1] + r[3] for r in band)
    bh = py1 - py0
    gf = cv2.GaussianBlur(g, (3, 3), 0).astype(np.float32)
    dy = np.abs(cv2.Sobel(gf, cv2.CV_32F, 0, 1, ksize=3))
    dx = np.abs(cv2.Sobel(gf, cv2.CV_32F, 1, 0, ksize=3))
    # top / bottom panel edges: long horizontal lines just outside the port band
    prof = dy[:, px0:px1].mean(1)
    t0, t1 = max(0, int(py0 - 1.2 * bh)), max(1, py0 - 2)
    b0, b1 = min(H - 2, py1 + 2), min(H, int(py1 + 1.2 * bh))
    top = t0 + int(np.argmax(prof[t0:t1])) if t1 > t0 else py0
    bottom = b0 + int(np.argmax(prof[b0:b1])) + 1 if b1 > b0 else py1
    # left / right panel edges: vertical lines that run through the whole panel height
    strong = dx[top:bottom] > np.percentile(dx[top:bottom], 90)
    cont = cv2.blur(strong.mean(0).astype(np.float32).reshape(1, -1), (5, 1)).ravel()
    left = int(np.argmax(cont[:px0])) if px0 > 5 else 0
    right = px1 + int(np.argmax(cont[px1:])) + 1 if px1 < W - 5 else W
    if (right - left) < 3 * (bottom - top) or (right - left) < 0.3 * W:
        return None
    return left, top, right, bottom


def find_face(img: np.ndarray) -> tuple[int, int, int, int]:
    """Front face of a rack switch.

    Catalog shots (switch on white): the non-white object, with the lid in
    perspective above the face cut off at the strongest horizontal edge that
    leaves a 1U-like aspect ratio (w/h ~ 7-14). Photos with a background scene:
    located from the band of port openings instead."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if not _light_background(g):
        face = _face_from_ports(img)
        if face is not None:
            return face
    m = cv2.morphologyEx((g < 235).astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cs:
        h, w = g.shape
        return 0, 0, w, h
    x, y, w, h = cv2.boundingRect(max(cs, key=cv2.contourArea))
    bottom = y + h
    # trim soft shadow under the device: last strong edge in the lower part
    dy = np.abs(cv2.Sobel(g.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3))
    prof = dy[:, x + w // 10: x + w * 9 // 10].mean(1)
    lower = range(y + h * 3 // 4, y + h)
    # 0.35: a light panel on a white background has a faint bottom edge; shadows stay below it
    strong_low = [r for r in lower if prof[r] > 0.35 * prof[lower.start:lower.stop].max()]
    if strong_low:
        bottom = max(strong_low) + 1

    def valid(r):
        return 7 <= w / max(1, bottom - r) <= 14
    # top edge in the upper part of the product; only if there is none there (a lid
    # filling most of the shot) search down to just above the bottom edge
    rows = [r for r in range(y, y + int(h * 0.6)) if valid(r)] or \
        [r for r in range(y, bottom - 10) if valid(r)]
    if rows:
        peak = max(prof[r] for r in rows)
        top = next(r for r in rows if prof[r] >= 0.5 * peak)
    else:
        top = y
    return x, top, x + w, bottom


# ------------------------------------------------------------------ port detection
def _dark_rects(g: np.ndarray) -> list[tuple[int, int, int, int, float]]:
    """Dark rectangular openings: RJ45 holes and SFP cages. Returns x,y,w,h,ring_brightness."""
    H, _ = g.shape
    panel = float(np.median(g))
    thr = panel * 0.72 if panel > 60 else panel * 0.6
    dark = cv2.morphologyEx((g < thr).astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(dark)
    blobs = [(i, *st[i]) for i in range(1, n)
             if 0.12 * H < st[i][3] < 0.6 * H and st[i][4] > 0.55 * st[i][2] * st[i][3]]
    # one port's width, measured on port-sized openings only (labels and LEDs are smaller)
    singles = [b for b in blobs if 0.6 * b[4] < b[3] < 2.2 * b[4] and b[4] >= 0.2 * H]
    unit = float(np.median([b[3] for b in singles])) if singles else 0.0
    out = []
    for i, x, y, w, h, a in blobs:
        comp = (lab == i).astype(np.uint8)
        ring = cv2.dilate(comp, np.ones((7, 7), np.uint8)) - comp
        rb = float(g[ring > 0].mean())
        if 0.6 * h < w < 3 * h:
            out.append((x, y, w, h, rb))
        elif unit and w >= 1.6 * unit:
            # neighbouring openings merged where the thin divider between them blurs
            # (small or soft photos): a run of k ports, k = width / one port's width
            k = round(w / unit)
            if k >= 2 and abs(w / unit - k) < 0.3:
                step = w / k
                out += [(int(x + j * step), y, int(step * 0.9), h, rb) for j in range(k)]
    if not out:
        return out
    # keep the dominant size class (drops LEDs, logo blobs, etc.)
    mh, mw = np.median([r[3] for r in out]), np.median([r[2] for r in out])
    return [r for r in out if 0.65 < r[3] / mh < 1.5 and 0.65 < r[2] / mw < 1.5]


def _columns(rects, tol):
    cols = []
    for r in sorted(rects, key=lambda r: r[0] + r[2] / 2):
        cx = r[0] + r[2] / 2
        if cols and abs(cols[-1][-1][0] + cols[-1][-1][2] / 2 - cx) < tol:
            cols[-1].append(r)
        else:
            cols.append([r])
    return [sorted(c, key=lambda r: r[1]) for c in cols]


def _number(rects, numbering: str, start: int) -> list[tuple[int, tuple]]:
    if not rects:
        return []
    tol = np.median([r[2] for r in rects]) * 0.5
    cols = _columns(rects, tol)
    if numbering == "odd_top":
        ordered = [r for c in cols for r in c]
    else:
        ys = sorted({round(r[1] / (np.median([q[3] for q in rects]) * 0.7)) for r in rects})
        rows = [[r for r in rects if round(r[1] / (np.median([q[3] for q in rects]) * 0.7)) == k]
                for k in ys]
        if numbering == "sequential_bottom":
            rows = rows[::-1]
        ordered = [r for row in rows for r in sorted(row, key=lambda r: r[0])]
    return [(start + i, r) for i, r in enumerate(ordered)]


def _cluster(vals: list[float], tol: float) -> list[list[float]]:
    groups: list[list[float]] = []
    for v in sorted(vals):
        if groups and v - groups[-1][-1] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return groups


def _complete_grid(rects: list, expected: int) -> list:
    """RJ45 ports sit on a regular rows x columns grid. Fill grid cells that
    detection missed (an opening touching its frame, a reflection), then drop
    a lone column far from the others (e.g. a console port) if that brings the
    count to the catalog's number. Kept only if it gets closer to `expected`."""
    if len(rects) < 3:
        return rects
    mw, mh = float(np.median([r[2] for r in rects])), float(np.median([r[3] for r in rects]))
    cx = [r[0] + r[2] / 2 for r in rects]
    cy = [r[1] + r[3] / 2 for r in rects]
    rows = [g for g in _cluster(cy, 0.5 * mh)]
    big = max(len(g) for g in rows)
    rows = [float(np.mean(g)) for g in rows if len(g) >= max(2, 0.25 * big)]
    cols = [float(np.mean(g)) for g in _cluster(cx, 0.5 * mw)]
    if not rows:
        return rects

    def at(c, r):
        return next((q for q, x, y in zip(rects, cx, cy) if abs(x - c) < 0.5 * mw and abs(y - r) < 0.5 * mh), None)

    grid = {}
    for c in cols:
        for r in rows:
            hit = at(c, r)
            grid[(c, r)] = (hit, hit is not None)
    filled = [hit if real else (int(c - mw / 2), int(r - mh / 2), int(mw), int(mh), 0.0)
              for (c, r), (hit, real) in grid.items()]

    # a lone column far from the others with at most one real opening (a console
    # port) is not a data port: drop it when it exceeds the catalog count, or always
    # when the count is unknown
    if (not expected or len(filled) > expected) and len(cols) > 2:
        gaps = np.diff(cols)
        pitch = float(np.median(gaps))
        for i, c in enumerate(cols):
            left = gaps[i - 1] if i > 0 else np.inf
            right = gaps[i] if i < len(gaps) else np.inf
            n_real = sum(1 for r in rows if grid[(c, r)][1])
            if min(left, right) > 1.6 * pitch and n_real <= 1:
                filled = [f for f in filled
                          if not (abs(f[0] + f[2] / 2 - c) < 0.5 * mw)]
                if expected and len(filled) <= expected:
                    break

    if expected and abs(len(filled) - expected) > abs(len(rects) - expected):
        return rects
    return filled


def layout_slope(ports: list[Port]) -> float:
    """Tilt of the RJ45 rows (dy/dx in face fractions scaled to pixels ~ 10:1).
    ~0 for a straight-on product shot, larger for angled marketing shots."""
    pts = [((p.box[0] + p.box[2]) / 2, (p.box[1] + p.box[3]) / 2) for p in ports if p.kind == "rj45"]
    if len(pts) < 4:
        return 0.0
    ys = [y for _, y in pts]
    rows = _cluster(ys, 0.08)
    top = max(rows, key=len)
    row = [(x, y) for x, y in pts if top[0] - 1e-9 <= y <= top[-1] + 1e-9]
    if len(row) < 3:
        return 0.0
    xs, yv = zip(*row)
    return abs(float(np.polyfit(xs, yv, 1)[0])) / 10  # face is ~10x wider than tall


def _split_sfp(rects: list) -> tuple[list, list]:
    """Port counts unknown: split off the SFP cages. They sit in the rightmost block
    and differ from RJ45 openings by a darker surrounding rim (metal cage vs. light
    jack frame) or by a wider, flatter opening."""
    if len(rects) < 6:
        return rects, []
    mw = float(np.median([r[2] for r in rects]))
    by_x = sorted(rects, key=lambda r: r[0])
    blocks, right = [], -1e9
    for r in by_x:                       # blocks = runs of columns separated by a gap
        if r[0] - right > 0.6 * mw:
            blocks.append([])
        blocks[-1].append(r)
        right = max(right, r[0] + r[2])
    if len(blocks) < 2:
        return rects, []
    main = [r for b in blocks[:-1] for r in b]
    ring = float(np.median([r[4] for r in main]))
    w0, h0 = float(np.median([r[2] for r in main])), float(np.median([r[3] for r in main]))
    last = blocks[-1]
    lring = float(np.median([r[4] for r in last]))
    lw, lh = float(np.median([r[2] for r in last])), float(np.median([r[3] for r in last]))
    if lring < 0.85 * ring or (lw > 1.1 * w0 and lh < 0.95 * h0):
        return main, last
    return rects, []


def detect_ports(face: np.ndarray, entry: CatalogEntry) -> list[Port]:
    g = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
    H, W = g.shape
    rects = _dark_rects(g)
    n_sfp = entry.sfp_ports
    if n_sfp and len(rects) > n_sfp:
        # SFP cages sit at the right end on almost all access switches
        by_x = sorted(rects, key=lambda r: r[0])
        rj45, sfp = by_x[:-n_sfp], by_x[-n_sfp:]
    elif not n_sfp and not entry.rj45_ports:
        rj45, sfp = _split_sfp(rects)    # counts unknown: tell cages from jacks by look
    else:
        rj45, sfp = rects, []
    rj45 = _complete_grid(rj45, entry.rj45_ports)
    ports = []
    for kind, group, start in (("rj45", rj45, 1), ("sfp", sfp, len(rj45) + 1)):
        for num, (x, y, w, h, _) in _number(group, entry.numbering, start):
            ports.append(Port(num, kind, [x / W, y / H, (x + w) / W, (y + h) / H]))
    return ports


# ------------------------------------------------------------------ public
def load_template(entry: CatalogEntry, cache_dir: Path, redetect: bool = False) -> tuple[VendorTemplate, list[str]]:
    warnings = []
    img = load_image(entry, cache_dir)
    src = entry.image_path or cache_dir / f"{entry.model}.jpg"
    layout_path = Path(str(src) + ".ports.json")

    saved = None
    if layout_path.exists() and not redetect:
        saved = json.loads(layout_path.read_text())
        h, w = img.shape[:2]
        x0, y0, x1, y1 = saved.get("face_crop", (0, 0, 0, 0))
        # a layout made for a different image (the file was replaced) must not be reused
        if (saved.get("image_size") and list(saved["image_size"]) != [w, h]) or not (
                0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
            warnings.append(f"{layout_path.name} was made for another image; ports detected again")
            saved = None

    crop = tuple(saved["face_crop"]) if saved else (entry.face_crop or find_face(img))
    x0, y0, x1, y1 = crop
    face = img[y0:y1, x0:x1]
    face = cv2.resize(face, (TEMPLATE_WIDTH, round(TEMPLATE_WIDTH * face.shape[0] / face.shape[1])),
                      interpolation=cv2.INTER_CUBIC)

    if saved:
        ports = [Port(**p) for p in saved["ports"]]
    else:
        ports = detect_ports(face, entry)
    if saved and "face_ocr" in saved:
        tokens = [OcrToken(t["text"], t["conf"], np.float32(t["box"])) for t in saved["face_ocr"]]
    else:
        tokens = easyocr_read(face, target_height=320, label_zone=0).tokens
    if not saved or "face_ocr" not in saved:
        layout_path.write_text(json.dumps(
            {"model": entry.model, "image_size": [int(img.shape[1]), int(img.shape[0])],
             "face_crop": [int(v) for v in crop],
             "ports": [asdict(p) for p in ports],
             "face_ocr": [{"text": t.text, "conf": round(t.conf, 3), "box": t.box.round(1).tolist()}
                          for t in tokens]}, indent=1))

    expected = entry.rj45_ports + entry.sfp_ports
    if expected and len(ports) != expected:
        warnings.append(f"detected {len(ports)} ports on vendor image, catalog says {expected}; "
                        f"fix {layout_path.name} by hand if numbering is off")
    return VendorTemplate(entry, img, face, crop, ports, tokens), warnings


def blueprint(t: VendorTemplate) -> np.ndarray:
    """White edge tracing of the face on a blueprint-blue grid, ports numbered."""
    g = cv2.cvtColor(t.face, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(g, (3, 3), 0), 40, 120)
    h, w = g.shape
    bp = np.full((h, w, 3), (110, 50, 16), np.uint8)  # BGR navy
    for x in range(0, w, 20):
        cv2.line(bp, (x, 0), (x, h), (130, 70, 30), 1)
    for y in range(0, h, 20):
        cv2.line(bp, (0, y), (w, y), (130, 70, 30), 1)
    bp[edges > 0] = (255, 245, 235)
    for num, poly in t.port_polys().items():
        cv2.polylines(bp, [poly.astype(np.int32)], True, (90, 220, 255), 2)
        _label(bp, str(num), poly, (90, 220, 255))
    return bp


def _label(img, text, poly, color):
    c = poly.mean(0)
    s = max(0.4, (poly[:, 1].max() - poly[:, 1].min()) / 45)
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, s, 2)
    org = (int(c[0] - tw / 2), int(c[1] + th / 2))
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, s, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, s, color, 2, cv2.LINE_AA)
