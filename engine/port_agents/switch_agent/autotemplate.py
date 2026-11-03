"""
autotemplate.py - build a switch template automatically from a vendor product photo.

Given a vendor image (front or slightly-above front view on a plain background) it:
  1. finds the front face of the switch (the 1U panel below the top surface)
  2. finds the RJ45 jacks (edge contours of jack size, then completed by matching a
     median jack patch along the face so missed or partly-hidden jacks are filled in)
  3. finds SFP cages (a strip of equal cells, usually right of the RJ45 block)
  4. writes front.png + template.json in the same format as the hand-made templates

Port numbering can't be read from vendor images (the printed numbers are a few pixels
high), so ports are numbered with a layout scheme ("top-odd", "top-even" or "row");
map_any_switch.py picks the scheme from text read off the user's photo, or asks.
"""
import json
import re
from pathlib import Path

import cv2
import numpy as np

UPSCALE_TO_FACE_H = 180   # template face is resized so its height is about this


# ----------------------------------------------------------------------------- geometry helpers

def _iou_small(a, b):
    ix = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    return ix * iy / max(1, min(a[2] * a[3], b[2] * b[3]))


def _nms(boxes, thr=0.3):
    keep = []
    for b in sorted(boxes, key=lambda b: -b[2] * b[3]):
        if all(_iou_small(b, k) <= thr for k in keep):
            keep.append(b)
    return keep


def _cluster_1d(values, tol):
    """Group sorted values whose gaps are <= tol. Returns list of index lists."""
    order = np.argsort(values)
    groups, cur = [], [order[0]]
    for i in order[1:]:
        if values[i] - values[cur[-1]] <= tol:
            cur.append(i)
        else:
            groups.append(cur)
            cur = [i]
    groups.append(cur)
    return groups


# ----------------------------------------------------------------------------- face

def product_bbox(gray):
    """Bounding box of the product on a light background."""
    border = np.concatenate([gray[0], gray[-1], gray[:, 0], gray[:, -1]])
    bg = np.median(border)
    if bg < 200:      # not a plain light background; use the whole image
        return 0, 0, gray.shape[1], gray.shape[0]
    m = (gray < bg - 20).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(m)
    if n < 2:
        return 0, 0, gray.shape[1], gray.shape[0]
    i = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    return tuple(int(v) for v in st[i, :4])


def front_face(gray):
    """Front panel band: the strongest horizontal edge (top surface / face boundary) and
    the strongest edge roughly one rack unit below it. 1U faces are ~1/10 of the width."""
    x0, y0, W, H = product_bbox(gray)
    roi = gray[y0:y0 + H, x0 + W // 10:x0 + W - W // 10].astype(np.float32)
    gy = np.abs(cv2.Sobel(cv2.GaussianBlur(roi, (0, 0), 1.5), cv2.CV_32F, 0, 1))
    prof = gy.mean(1)
    min_h, max_h = int(W / 14), int(W / 5.5)
    if H <= min_h + 2:                       # image already is just the face
        return x0, y0, W, H
    top = int(np.argmax(prof[:max(1, H - min_h)]))
    lo, hi = top + min_h, min(H - 1, top + max_h)
    bot = lo + int(np.argmax(prof[lo:hi])) if hi > lo else min(H - 1, top + int(W / 9))
    return x0, y0 + top, W, bot - top


# ----------------------------------------------------------------------------- ports

def _jack_candidates(face, FH):
    e = cv2.Canny(cv2.GaussianBlur(face, (0, 0), 1.0), 30, 90)
    e = cv2.dilate(e, np.ones((2, 2), np.uint8))
    cnts, _ = cv2.findContours(e, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if not (FH * 0.14 < h < FH * 0.42) or not (0.85 < w / h < 2.2):
            continue
        if cv2.contourArea(cv2.convexHull(c)) / (w * h) < 0.75:
            continue
        out.append((x, y, w, h))
    return _nms(out)


def _complete_by_matching(face, seeds, FH):
    """Use the median-looking seed jack as a patch and find every place it repeats."""
    if not seeds:
        return [], []
    mw = int(np.median([b[2] for b in seeds]))
    mh = int(np.median([b[3] for b in seeds]))
    same = [b for b in seeds if abs(b[2] - mw) < 0.25 * mw and abs(b[3] - mh) < 0.25 * mh]
    if not same:
        return [], []
    # build a mean patch from the seeds (more robust than any single one)
    pad = max(2, mh // 6)
    patches = []
    for x, y, w, h in same:
        cx, cy = x + w / 2, y + h / 2
        x0, y0 = int(cx - mw / 2 - pad), int(cy - mh / 2 - pad)
        p = face[max(0, y0):y0 + mh + 2 * pad, max(0, x0):x0 + mw + 2 * pad]
        if p.shape == (mh + 2 * pad, mw + 2 * pad):
            patches.append(p.astype(np.float32))
    if not patches:
        return same, []
    tpl = np.median(np.stack(patches), axis=0).astype(np.uint8)
    r = cv2.matchTemplate(face, tpl, cv2.TM_CCOEFF_NORMED)
    found = []
    thr = 0.55
    r2 = r.copy()
    while True:
        _, mv, _, ml = cv2.minMaxLoc(r2)
        if mv < thr:
            break
        found.append((ml[0] + pad, ml[1] + pad, mw, mh, float(mv)))
        cv2.rectangle(r2, (ml[0] - mw // 2, ml[1] - mh // 2), (ml[0] + mw // 2, ml[1] + mh // 2), -1, -1)
    # also try the vertically flipped patch (top and bottom rows are often mirrored)
    tplf = cv2.flip(tpl, 0)
    r = cv2.matchTemplate(face, tplf, cv2.TM_CCOEFF_NORMED)
    while True:
        _, mv, _, ml = cv2.minMaxLoc(r)
        if mv < thr:
            break
        found.append((ml[0] + pad, ml[1] + pad, mw, mh, float(mv)))
        cv2.rectangle(r, (ml[0] - mw // 2, ml[1] - mh // 2), (ml[0] + mw // 2, ml[1] + mh // 2), -1, -1)
    found.sort(key=lambda f: -f[4])
    kept = []
    for f in found:
        if all(_iou_small(f[:4], k[:4]) <= 0.3 for k in kept):
            kept.append(f)
    # SFP cages and other look-alikes match the jack patch, but clearly worse than real
    # jacks do. Keep boxes scoring close to the typical jack score; return the rest
    # as "weaker" candidates (often SFP cages) for the SFP step.
    med = np.median([f[4] for f in kept])
    strong = [f[:4] for f in kept if f[4] >= 0.85 * med]
    weak = [f[:4] for f in kept if f[4] < 0.85 * med]
    return strong, weak


def _keep_grid(boxes, FH):
    """Keep jacks that sit in rows with a regular pitch. Within a row, jacks are split
    into groups wherever the gap is much bigger than the pitch; a group of one (for
    example a console port next to the block) is reported separately."""
    if not boxes:
        return [], []
    cy = np.array([b[1] + b[3] / 2 for b in boxes])
    rows = _cluster_1d(cy, FH * 0.08)
    grid, lone = [], []
    for g in rows:
        row = sorted((boxes[i] for i in g), key=lambda b: b[0])
        if len(row) < 2:
            lone += row
            continue
        cx = np.array([b[0] + b[2] / 2 for b in row])
        pitch = np.median(np.diff(cx)) if len(row) > 2 else np.diff(cx)[0]
        groups, cur = [], [row[0]]
        for a, b in zip(row, row[1:]):
            if (b[0] + b[2] / 2) - (a[0] + a[2] / 2) > 1.6 * min(pitch, a[2] * 1.4):
                groups.append(cur)
                cur = [b]
            else:
                cur.append(b)
        groups.append(cur)
        for gr in groups:
            (grid if len(gr) >= 2 else lone).extend(gr)
    if not grid:
        return [], lone
    gx0 = min(b[0] for b in grid)
    lone = [b for b in lone if b[0] + b[2] < gx0]      # console ports sit left of the block
    return grid, lone[:1]


def _sfp_cells(face, FH, avoid):
    """SFP cages: a strip of equal cells. Found as a long rectangle (or several cells),
    split using the vertical-edge profile inside it."""
    e = cv2.Canny(cv2.GaussianBlur(face, (0, 0), 1.0), 25, 80)
    e = cv2.dilate(e, np.ones((2, 2), np.uint8))
    cnts, _ = cv2.findContours(e, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    strips = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if not (FH * 0.15 < h < FH * 0.45) or w < 2.2 * h:
            continue
        if any(_iou_small((x, y, w, h), a) > 0.2 for a in avoid):
            continue
        strips.append((x, y, w, h))
    strips = _nms(strips, 0.5)
    cells = []
    for x, y, w, h in strips:
        sub = cv2.GaussianBlur(face[y:y + h, x:x + w].astype(np.float32), (0, 0), 1.0)
        gx = np.abs(cv2.Sobel(sub, cv2.CV_32F, 1, 0)).mean(0)
        n_best, best = None, -1
        for n in range(2, 9):
            cw = w / n
            if not (0.9 < cw / h < 2.0):
                continue
            pos = [int(round(k * cw)) for k in range(1, n)]
            s = np.mean([gx[max(0, p - 2):p + 3].max() for p in pos]) / (gx.mean() + 1e-6)
            if s > best:
                best, n_best = s, n
        if n_best and best > 1.4:
            cw = w / n_best
            cells += [(int(x + k * cw), y, int(cw), h) for k in range(n_best)]
    return cells


def detect_ports(gray, face_box=None):
    """Returns (face_box_in_image, scale, face_gray_scaled, rj45_boxes, sfp_boxes, console_box).
    face_box (x, y, w, h): the front face when the caller already knows it (the template
    agent's find_face); otherwise front_face() finds it."""
    fx, fy, fw, fh = face_box or front_face(gray)
    U = UPSCALE_TO_FACE_H / max(1, fh)
    face = cv2.resize(gray[fy:fy + fh, fx:fx + fw], None, fx=U, fy=U, interpolation=cv2.INTER_CUBIC)
    FH = face.shape[0]
    seeds = _jack_candidates(face, FH)
    rj, weak = _complete_by_matching(face, seeds, FH)
    rj, lone = _keep_grid(rj, FH)
    sfp = _sfp_cells(face, FH, rj + lone)
    if rj:
        # SFP cages sit level with the jacks: drop strips outside the jack rows' height band
        top = min(b[1] for b in rj); bot = max(b[1] + b[3] for b in rj)
        sfp = [b for b in sfp if top - 0.15 * FH <= b[1] + b[3] / 2 <= bot + 0.15 * FH]
        if len(sfp) > 8:
            sfp = []
    if not sfp and weak and rj:
        # weaker look-alikes in a row to the right of the RJ45 block are usually SFP cages
        rx = max(b[0] + b[2] for b in rj)
        cand = sorted([b for b in weak if b[0] > rx], key=lambda b: b[0])
        if len(cand) >= 2:
            sfp = cand
    return (fx, fy, fw, fh), U, face, rj, sfp, (lone[0] if lone else None)


# ----------------------------------------------------------------------------- numbering

SCHEMES = {
    "top-odd": "columns, top row odd (1,3,5 on top, 2,4,6 below)",
    "top-even": "columns, top row even (2,4,6 on top, 1,3,5 below)",
    "row": "rows, left to right (top row first)",
}


def number_ports(rj, scheme):
    """Assign numbers to RJ45 boxes. Returns list of (number, box)."""
    if not rj:
        return []
    cy = np.array([b[1] + b[3] / 2 for b in rj])
    h = np.median([b[3] for b in rj])
    rows = _cluster_1d(cy, h * 0.5)
    rows = sorted(([rj[i] for i in g] for g in rows), key=lambda r: r[0][1])
    rows = [sorted(r, key=lambda b: b[0]) for r in rows]
    out = []
    if scheme == "row" or len(rows) != 2:
        n = 1
        for r in rows:
            for b in r:
                out.append((n, b))
                n += 1
        return out
    top, bot = rows
    # pair top/bottom by column (x overlap)
    cols = []
    used = set()
    for t in top:
        match = min(range(len(bot)), key=lambda j: abs(bot[j][0] - t[0])) if bot else None
        if match is not None and abs(bot[match][0] - t[0]) < t[2] * 0.6 and match not in used:
            used.add(match)
            cols.append((t, bot[match]))
        else:
            cols.append((t, None))
    cols += [(None, bot[j]) for j in range(len(bot)) if j not in used]
    cols.sort(key=lambda c: (c[0] or c[1])[0])
    n = 1
    for t, b in cols:
        first, second = (t, b) if scheme == "top-odd" else (b, t)
        if first is not None:
            out.append((n, first))
        if second is not None:
            out.append((n + 1, second))
        n += 2
    return out


# ----------------------------------------------------------------------------- build

def build_template(vendor_img_bgr, out_dir: Path, vendor, model, source="", scheme="top-odd",
                   sfp_names=None, face_box=None):
    gray = cv2.cvtColor(vendor_img_bgr, cv2.COLOR_BGR2GRAY)
    (fx, fy, fw, fh), U, face, rj, sfp, console = detect_ports(gray, face_box)
    if len(rj) < 4:
        raise ValueError(f"only {len(rj)} RJ45 ports detected in vendor image")
    # crop a small margin around the face and save at the same scale as `face`
    my = int(fh * 0.06)
    y0, y1 = max(0, fy - my), min(gray.shape[0], fy + fh + my)
    # The detected band can stop short of the panel - a stacked second row of jacks
    # then falls outside it and the crop slices through the ports. A half jack is not
    # a jack: the saved image shows a cut panel and detection on it later reads the
    # sliced openings as SFP cages, so the template comes back with almost no RJ45.
    # Whatever ports were found must survive the crop whole, with room around them.
    if rj:
        py0 = fy + min(b[1] for b in rj) / U
        py1 = fy + max(b[1] + b[3] for b in rj) / U
        pad = 0.4 * (py1 - py0)
        y0 = max(0, min(y0, int(py0 - pad)))
        y1 = min(gray.shape[0], max(y1, int(py1 + pad)))
    crop = cv2.resize(vendor_img_bgr[y0:y1, fx:fx + fw], None, fx=U, fy=U, interpolation=cv2.INTER_CUBIC)
    oy = int(round((fy - y0) * U))            # face offset inside crop
    sh = lambda b: [int(b[0]), int(b[1] + oy), int(b[2]), int(b[3])]

    numbered = number_ports(rj, scheme)
    n_rj = max((n for n, _ in numbered), default=0)     # an all-SFP template has no RJ45
    ports = [{"id": n, "type": "rj45", "poe": None, "box": sh(b)} for n, b in numbered]
    for k, b in enumerate(sorted(sfp, key=lambda b: b[0])):
        pid = sfp_names[k] if sfp_names and k < len(sfp_names) else n_rj + k + 1
        ports.append({"id": pid, "type": "sfp", "poe": False, "box": sh(b)})
    if console is not None:
        ports.append({"id": "Console", "type": "console", "poe": False, "box": sh(console)})

    H, W = crop.shape[:2]
    xs = [b[0] for b in rj]; ys = [b[1] for b in rj]
    xe = [b[0] + b[2] for b in rj]; ye = [b[1] + b[3] for b in rj]
    pad = int(np.median([b[3] for b in rj]) * 0.25)
    ignore = [[min(xs) - pad, min(ys) + oy - pad, max(xe) - min(xs) + 2 * pad, max(ye) - min(ys) + 2 * pad]]

    out_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_dir / "front.png"), crop)
    meta = {
        "vendor": vendor, "model": model,
        "description": "", "summary": _summary(ports),
        "image": "front.png", "image_source": (str(source) if re.match(r"https?://", str(source or "")) else re.split(r"[\\/]", str(source or ""))[-1]),
        "auto_generated": True, "numbering_scheme": scheme,
        "match_regions": [[0, oy, W, int(fh * U)]],
        "ignore_regions": ignore,
        "ports": ports,
    }
    (out_dir / "template.json").write_text(json.dumps(meta, indent=1))
    return meta


def renumber(folder: Path, scheme, sfp_names=None):
    """Re-apply a numbering scheme to an auto-generated template."""
    meta = json.loads((folder / "template.json").read_text())
    rj = [tuple(p["box"]) for p in meta["ports"] if p["type"] == "rj45"]
    others = [p for p in meta["ports"] if p["type"] != "rj45"]
    numbered = number_ports(rj, scheme)
    n_rj = max((n for n, _ in numbered), default=0)     # an all-SFP template has no RJ45
    ports = [{"id": n, "type": "rj45", "poe": None, "box": list(b)} for n, b in numbered]
    sfps = sorted([p for p in others if p["type"] == "sfp"], key=lambda p: p["box"][0])
    for k, p in enumerate(sfps):
        if sfp_names and k < len(sfp_names):
            p["id"] = sfp_names[k]
        elif not (isinstance(p["id"], str) and p["id"].endswith("F")):
            # a combo name set before (DES-1210-28P: 25F, 26F) stays; numbering it after
            # the RJ45 ports would give a 28-port switch a port 30
            p["id"] = n_rj + k + 1
    meta["ports"] = ports + others
    meta["numbering_scheme"] = scheme
    meta["summary"] = _summary(meta["ports"])
    (folder / "template.json").write_text(json.dumps(meta, indent=1))
    return meta


def _summary(ports):
    rj = [p for p in ports if p["type"] == "rj45"]
    sf = [p for p in ports if p["type"] == "sfp"]
    parts = []
    if rj:
        parts.append(f"{len(rj)}x RJ45 (ports {rj[0]['id'] if False else min(p['id'] for p in rj)}-{max(p['id'] for p in rj)})")
    if sf:
        parts.append(f"{len(sf)}x SFP ({', '.join(str(p['id']) for p in sf)})")
    if any(p["type"] == "console" for p in ports):
        parts.append("console port")
    return " | ".join(parts) + " | detected automatically"
