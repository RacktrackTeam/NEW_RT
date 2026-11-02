"""The video experiment: units, occupancy and devices from the source video frames, fused on the constructed
(stitched) rack image.

    1. The constructed rack image is the global reference: units, occupancy and devices as in src/pipeline.py,
       kept as the comparison ("the rack image alone").
    2. Frames are read from the video (every STRIDE-th), scored for sharpness and registered to the rack image with
       SIFT features and a RANSAC homography: every frame gets its own map frame -> rack coordinates.
    3. For every band of the rack the best frames (sharp, the band near the frame's centre) are chosen and the whole
       photo pipeline runs on each raw frame, exactly as on an uploaded photo: its own ladder (step 1), its
       occupancy (step 2) and its device seams (step 3). Nothing is detected on warped or cropped images: the
       detector is tight on photos and loose on warps.
    4. Only unit-level answers cross over to the rack, through the homography. Every frame's unit lines vote one
       fused ladder (lines closer than LINE_TOL of a pitch are one line; a line stays when most of the frames whose
       ladder reaches it drew it; too-close lines merge and gaps are filled at the LOCAL pitch, the median pitch of
       the frame ladders reaching that height, because a stitched image can be stretched unevenly). Every fused unit's occupancy is voted by the
       frames that see it, a vote winning with at least half of them (and at least VOTE_MIN): a device box over more
       than half of its height -> occupied (height, not area: a half-width switch is a device too), an empty slot
       -> free, else the classifiers' mean, and uncertain when the typed finder sees a device the classifiers call
       free (the team rule). Every seam between two occupied units is voted by the frames that see both units: step 3's rule with
       the height owner (one box over both -> same device, two boxes -> new device, otherwise the logic).
       Types are voted over the frames' typed finder boxes and the rack image's. Where fewer than VOTE_MIN frames
       can vote, the rack image's own answer stays. Boxes cross as polygons (four mapped corners), never as
       bounding boxes.
    5. Pictures: the rack with the fused ladder / occupancy / devices next to the rack-only ones, every device's five
       best frames (sharp, well matched, the whole polygon inside, among all mapped frames) cut out around its polygon,
       every device drawn
       as the polygon the frames saw (the detector's box through each frame's homography, averaged over the frames;
       the rack image's own box for the rack-only devices; a unit band only where no box owns the device), the
       frames' device polygons on the rack, section crops from the best frame of every device, the result projected
       back onto the frames. With a NetBox device export (netbox_devices.csv) every answer is scored against the truth
       (src/netbox_truth.py).

Command line:  python3 src/rack_video.py VIDEO.mov --out DIR [--truth netbox_devices.csv]
(the video is the only input: turned upright if it lies on its side, then stitched into the rack picture by
src/rack_stitch.py, saved as DIR/rack.png)
"""
import argparse
import json
import re
import os
import pathlib
import sys
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import device_join as DJ      # noqa: E402
import frame_locator as FL    # noqa: E402
import device_model as DM     # noqa: E402
import netbox_truth as NT     # noqa: E402
import rack_rails as RAILS    # noqa: E402
import occupancy as occ       # noqa: E402
import pipeline as P          # noqa: E402

STRIDE = 5                 # every 5th frame of a 30 fps video
MIN_INLIERS = 40           # a frame maps to the rack when its homography has this many inliers
FRAMES_PER_BAND = 3        # the best frames kept for every band (one pitch) of the rack
EDGE = 40                  # a band must sit this far (rack px) inside a frame's projected area to count as covered
CANDIDATE_SCORE = 0.3      # a detector box on the rack picture from this score up is a "possible device": a free unit under it
                           # becomes "not sure" (the Corning panel 0.39, the drobo 0.30 and the power switch 0.51 of 27 Oct; a shelf 0.25)
VOTE_MIN = 2               # frames needed before their votes replace the rack-level decision; a vote wins with at
                           # least half of the frames that see the unit (and at least VOTE_MIN of them)
LINE_TOL = 0.35            # mapped unit lines closer than this share of a pitch are one line
MIN_GAP = 0.6              # two fused lines closer than this share of a pitch merge
MAX_GAP = 1.5              # a gap wider than this share of a pitch gets lines inserted at the local pitch
OVERLAP = 0.5              # a frame's unit speaks for the fused unit it overlaps by this share (of the smaller one)
HEIGHT_COVER = 0.5         # a device box owns a unit when it covers more than half of the unit's height ...
WIDTH_MIN = 0.2            # ... and at least this share of its width (a narrow device is a device too)
SLIVER = 0.6               # an end row thinner than this share of a unit is the ladder reaching onto the rack's frame
CROP_PAD = 0.12            # the clean device crop (clean_crop) reaches this share of the device's height around it


# ---------------------------------------------------------------- frames and their map to the rack
def read_frames(video, stride=STRIDE):
    """Every stride-th frame: (index, bgr)."""
    cap = cv2.VideoCapture(str(video))
    out, k = [], 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if k % stride == 0:
            out.append((k, fr))
        k += 1
    cap.release()
    return out, k


def sharpness(bgr):
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def register(frames, rack_bgr, progress=None):
    """A homography (frame -> rack) per frame with its inliers, sharpness and the rack area it shows."""
    sift = cv2.SIFT_create(nfeatures=8000)
    g_rack = cv2.cvtColor(rack_bgr, cv2.COLOR_BGR2GRAY)
    kr, dr = sift.detectAndCompute(g_rack, None)
    bf = cv2.BFMatcher(cv2.NORM_L2)
    H_rack, W_rack = rack_bgr.shape[:2]
    out = []
    for n, (idx, fr) in enumerate(frames):
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        kf, df = sift.detectAndCompute(g, None)
        rec = {"index": idx, "sharpness": sharpness(fr), "inliers": 0, "H": None, "y0": None, "y1": None, "scale": None}
        if df is not None and len(kf) >= 12:
            good = [a for a, b in bf.knnMatch(df, dr, k=2) if a.distance < 0.75 * b.distance]
            if len(good) >= 12:
                src = np.float32([kf[a.queryIdx].pt for a in good]); dst = np.float32([kr[a.trainIdx].pt for a in good])
                H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
                inl = int(mask.sum()) if mask is not None else 0
                if H is not None and inl >= MIN_INLIERS:
                    h, w = g.shape
                    q = cv2.perspectiveTransform(np.float32([[[0, 0], [w, 0], [w, h], [0, h]]]), H)[0]
                    pw, ph = float(np.max(q[:, 0]) - np.min(q[:, 0])), float(np.max(q[:, 1]) - np.min(q[:, 1]))
                    if pw > 0.3 * W_rack and 0.2 < ph / max(1, pw) < 3:          # a sane map: the frame lands upright on the rack
                        rec.update(inliers=inl, H=H, y0=float(np.min(q[:, 1])), y1=float(np.max(q[:, 1])),
                                   x0=float(np.min(q[:, 0])), x1=float(np.max(q[:, 0])),
                                   scale=float(h / max(1.0, ph)))                 # frame px per rack px
        out.append(rec)
        if progress:
            progress(f"registered frame {idx} ({n + 1}/{len(frames)})")
    med = float(np.median([r["sharpness"] for r in out])) or 1.0
    for r in out:
        r["quality"] = (min(1.0, r["inliers"] / 150.0) * min(1.5, r["sharpness"] / med)) if r["H"] is not None else 0.0
    return out


def frames_for_band(y1, y2, regs):
    """The best frames showing a band of the rack: covered with a margin, scored by quality and how central it sits."""
    cand = []
    for r in regs:
        if r["H"] is None or not (r["y0"] + EDGE <= y1 and y2 <= r["y1"] - EDGE):
            continue
        c = (r["y0"] + r["y1"]) / 2
        central = 1.0 - min(1.0, abs((y1 + y2) / 2 - c) / max(1.0, (r["y1"] - r["y0"]) / 2))
        cand.append((r["quality"] * (0.5 + 0.5 * central), r))
    cand.sort(key=lambda t: -t[0])
    return [r for _, r in cand[:FRAMES_PER_BAND]]


def to_rack(H, pts):
    return cv2.perspectiveTransform(np.float32([pts]), H)[0]


def poly_of(H, box):
    """A frame box -> its four corners on the rack (a polygon, never its bounding box)."""
    x1, y1, x2, y2 = box[:4]
    return [(float(a), float(b)) for a, b in to_rack(H, [(x1, y1), (x2, y1), (x2, y2), (x1, y2)])]


def poly_span(poly):
    """A polygon's span on the rack: the mean of its top edge and of its bottom edge, not its bounding box."""
    ys = sorted(p[1] for p in poly); xs = [p[0] for p in poly]
    return [min(xs), (ys[0] + ys[1]) / 2, max(xs), (ys[2] + ys[3]) / 2]


def back_project_poly(H, poly):
    """A polygon on the rack -> the same polygon in frame coordinates."""
    q = cv2.perspectiveTransform(np.float32([poly]), np.linalg.inv(H))[0]
    return [(float(a), float(b)) for a, b in q]


def inside_frame(f, poly, hw, margin=10):
    """True when the rack polygon lands wholly inside the frame (every corner, with a margin). The frames are
    tilted, so the frame's y-range on the rack says nothing about one device: the polygon itself is checked."""
    h, w = hw
    return all(margin <= x <= w - margin and margin <= y <= h - margin for x, y in back_project_poly(f["H"], poly))


def device_frame_scores(cands, poly, imgs):
    """How well each candidate frame shows ONE device: the sharpness of the device's own area (against the median of
    its candidates, capped at 1.5), how central the device sits in the frame (1 at the centre, 0.5 at the edge) and how
    well the frame is placed on the rack (agreeing matches, full marks at 150). Score = the three multiplied."""
    rec = []
    for f in cands:
        pts = np.array(back_project_poly(f["H"], poly), dtype=np.float32)
        img = imgs.get(f["index"]) if imgs else None
        if img is None:
            rec.append((f, f["quality"], None, None)); continue
        h, w = img.shape[:2]
        x1, y1 = max(0, int(pts[:, 0].min())), max(0, int(pts[:, 1].min())); x2, y2 = min(w, int(pts[:, 0].max()) + 1), min(h, int(pts[:, 1].max()) + 1)
        sharp = sharpness(img[y1:y2, x1:x2]) if x2 - x1 > 8 and y2 - y1 > 8 else 0.0
        cx, cy = pts[:, 0].mean(), pts[:, 1].mean()
        off = min(1.0, float(np.hypot((cx - w / 2) / (w / 2), (cy - h / 2) / (h / 2))) / np.sqrt(2) * 2)
        rec.append((f, sharp, 1.0 - 0.5 * off, min(1.0, f["inliers"] / 150.0)))
    sh = [r[1] for r in rec if r[2] is not None]
    med = float(np.median(sh)) if sh else 1.0
    out = []
    for f, sharp, central, place in rec:
        if central is None:
            out.append((f["quality"], f, None)); continue
        dsharp = min(1.5, sharp / med) if med else 0.0
        out.append((dsharp * central * place, f, {"device_sharpness": round(dsharp, 2), "central": round(central, 2), "placement": round(place, 2)}))
    return sorted(out, key=lambda t: -t[0])


def device_polygon(idx, rows, fa, band):
    """The device as the frames saw it: in every frame where one detector box owns the device's top and bottom
    units, that box carried through the frame's homography (a polygon), averaged corner by corner over the frames
    (weights = frame quality). The unit band stands in when no frame box owns the device."""
    top, bot = rows[idx[0]]["box"], rows[idx[-1]]["box"]
    acc, wsum, used = np.zeros((4, 2)), 0.0, []
    for f in fa:
        ua, ub = unit_over(f, top[1], top[3]), unit_over(f, bot[1], bot[3])
        if ua is None or ub is None or ua["own"] < 0 or ua["own"] != ub["own"]:
            continue
        acc += f["quality"] * np.array(f["an"]["boxes"][ua["own"]]["poly"]); wsum += f["quality"]; used.append(f["index"])
    if wsum > 0:
        return [(float(x), float(y)) for x, y in acc / wsum], "frames", sorted(used)
    x1, y1, x2, y2 = band
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)], "units", []


def draw_polygons(img, rows, devices):
    """The rack with one translucent polygon per device (its shape, not its unit band) and its label."""
    base = img.convert("RGBA")
    W, H = base.size
    lay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    hs = [r["box"][3] - r["box"][1] for r in rows] or [20]
    font = occ._font(int(max(11, min(float(np.median(hs)) * 0.6, W / 40))))
    lw = max(2, int(min(W, H) / 400))
    unsure = {r["unit"] for r in rows if r.get("status") == "uncertain"}; on_dev = {u for dv in devices for u in dv.get("units") or []}
    AMBER = (255, 154, 46)
    for r in rows:                                           # a unit the frames left not sure, with no device on it: amber, said in words
        if r["unit"] in unsure and r["unit"] not in on_dev:
            x1, y1, x2, y2 = r["box"]; d.rectangle([x1, y1 + 1, x2, y2 - 1], fill=AMBER + (70,), outline=AMBER + (230,), width=lw)
            tt = f"{r['unit'].upper()} · not sure"; tw, th = d.textbbox((0, 0), tt, font=font)[2:]
            d.rounded_rectangle([x1 + 6, y1 + 2, x1 + tw + 18, y1 + th + 10], radius=4, fill=(179, 93, 0, 235)); d.text((x1 + 12, y1 + 5), tt, fill="white", font=font)
    for dev in devices:
        n_uns = sum(1 for u in dev.get("units") or [] if u in unsure)
        col = AMBER if n_uns else (25, 195, 125)                 # one colour for every device; amber where its units are not sure
        poly = dev["polygon"]
        d.polygon(poly, fill=col + (60,), outline=col + (230,), width=lw)
        t = (dev.get("label") or f"D{dev['index']} · {dev['height']}U") + (f" · {n_uns}U not sure" if n_uns else "")
        tw, th = d.textbbox((0, 0), t, font=font)[2:]
        x2, y1 = max(poly[0][0], poly[1][0]), min(poly[0][1], poly[1][1])         # the top-right corner of the polygon
        d.rounded_rectangle([x2 - tw - 12 - lw, y1 + lw, x2 - lw, y1 + th + 8], radius=4, fill=col + (235,))
        d.text((x2 - tw - 6 - lw, y1 + 3), t, fill="white", font=font)
    return Image.alpha_composite(base, lay).convert("RGB")


def owner_by_height(unit, boxes):
    """The detector box that owns the unit: the one covering the most of the unit's height, if that is more than
    HEIGHT_COVER of it and the box spans at least WIDTH_MIN of the unit's width; -1 when none does. Step 3's owner
    rule asks for device_model.COVER of the unit's AREA, which a narrow device (a half-width switch, a desktop
    firewall) can never reach; the height rule treats narrow and full-width devices alike."""
    x1, y1, x2, y2 = unit
    best, best_vy = -1, 0.0
    for j, b in enumerate(boxes):
        vy = max(0.0, min(y2, b[3]) - max(y1, b[1])) / max(1.0, y2 - y1)
        vx = max(0.0, min(x2, b[2]) - max(x1, b[0])) / max(1.0, x2 - x1)
        if vy > HEIGHT_COVER and vx >= WIDTH_MIN and vy > best_vy:
            best, best_vy = j, vy
    return best


# ---------------------------------------------------------------- the photo pipeline on one frame
def analyse_frame(fr, H, approach):
    """The whole photo pipeline on one raw frame; every unit-level answer mapped onto the rack."""
    units, info = P.units_from_photo(fr)
    if not units:
        return None
    rgb = np.ascontiguousarray(fr[:, :, ::-1])
    img = Image.fromarray(rgb)
    types = occ.detect_devices(img) if occ.device_finder_available() else []
    rows = P.occupancy_rows(img, units, approach, devices=types)
    boxes = [b for b in DM.detect(rgb) if b[4] >= DM.SCORE] if P.detector_available() else []
    urows = [{"unit": u["unit"], "box": list(u["box"])} for u in units]
    taken = [r["status"] != "unoccupied" for r in rows]
    own = DM.owners(urows, boxes) if boxes else [-1] * len(units)
    probs, source = {}, {}
    if any(taken):
        if P.detector_available():
            _, _, probs, source = DM.group(fr, urows, taken, boxes=boxes)
        else:
            _, _, probs = DJ.group(fr, urows, taken)
            source = {k: "logic" for k in probs}
    own_h = [owner_by_height(u["box"], boxes) for u in units]
    out = []
    for k, (u, r) in enumerate(zip(units, rows)):
        x1, y1, x2, y2 = u["box"]; xc, yc = (x1 + x2) / 2, (y1 + y2) / 2
        if k and own_h[k - 1] >= 0 and own_h[k] >= 0:            # both neighbours owned by a box: the detector decides the seam
            seam_p, seam_by = (0.0 if own_h[k - 1] == own_h[k] else 1.0), "model"
        else:
            seam_p, seam_by = (None if k not in probs else float(probs[k])), source.get(k)
        ry = to_rack(H, [(xc, y1), (xc, y2)])[:, 1]
        rx = to_rack(H, [(x1, yc), (x2, yc)])[:, 0]
        found = r.get("device") or ""                       # the typed finder's trusted sighting over this row, "Class (0.xx)"
        dev = found.split(" (")[0].lower()
        out.append({"k": k, "y": [float(ry[0]), float(ry[1])], "x": [float(rx[0]), float(rx[1])], "status": r["status"],
                    "p": float(r["p_occupied"]), "device_seen": own_h[k] >= 0, "own": own_h[k], "empty_seen": dev in occ.FREE_DEVICE_CLASSES,
                    "typed_seen": " (" in found and dev not in occ.FREE_DEVICE_CLASSES, "typed": found.split(" (")[0] if " (" in found else None,
                    "seam_p": seam_p, "seam_by": seam_by})
    lines = [u["y"][0] for u in out] + [out[-1]["y"][1]]
    s = occ.summarize(rows)
    return {"units": out, "pitch": float(np.median(np.diff(lines))), "n_units": len(units),
            "boxes": [{"poly": poly_of(H, b), "score": float(b[4])} for b in boxes],
            "types": [{"class_name": d["class_name"], "confidence": float(d["confidence"]), "poly": poly_of(H, d["box"])} for d in types],
            "occupied": s["occupied"], "free": s["free"], "uncertain": s["uncertain"]}


# ---------------------------------------------------------------- fusion
def fuse_ladder(fa, rack_lines):
    """One ladder on the rack from the frames' ladders, with the rack image's own ladder as one more voter.
    Returns the lines (top to bottom), how many voters drew each (0 = inserted), the pitch and the counts."""
    if not fa:
        return None
    pitch = float(np.median([f["an"]["pitch"] for f in fa]))
    q_rack = float(np.median([f["quality"] for f in fa]))
    votes = []
    for f in fa:
        us = f["an"]["units"]
        for y in [u["y"][0] for u in us] + [us[-1]["y"][1]]:
            votes.append((y, f["quality"], f["index"]))
    for y in rack_lines:
        votes.append((float(y), q_rack, "rack"))
    votes.sort(key=lambda v: v[0])
    clusters = []
    for y, w, idx in votes:
        if clusters and abs(y - clusters[-1]["y"]) <= LINE_TOL * pitch:
            c = clusters[-1]; c["sw"] += w; c["sy"] += w * y; c["y"] = c["sy"] / c["sw"]; c["voters"].add(idx)
        else:
            clusters.append({"y": y, "sy": w * y, "sw": w, "voters": {idx}})
    reach = [(f["an"]["units"][0]["y"][0] - 0.5 * pitch, f["an"]["units"][-1]["y"][1] + 0.5 * pitch, f["quality"]) for f in fa]
    if rack_lines:
        reach.append((min(rack_lines) - 0.5 * pitch, max(rack_lines) + 0.5 * pitch, q_rack))
    pitches = [(f["an"]["units"][0]["y"][0] - 0.5 * pitch, f["an"]["units"][-1]["y"][1] + 0.5 * pitch, f["an"]["pitch"]) for f in fa]
    def local_pitch(y):
        """The pitch where the stitched image may be stretched: the median pitch of the frame ladders reaching y."""
        near = [pp for a, b, pp in pitches if a <= y <= b]
        return float(np.median(near)) if near else pitch
    kept = []
    for c in clusters:
        could = sum(q for a, b, q in reach if a <= c["y"] <= b)
        if c["sw"] >= 0.5 * could:                     # most of the ladders that reach here drew this line
            kept.append([c["y"], len(c["voters"])])
    merged = 0
    out = []
    for y, nv in kept:
        if out and y - out[-1][0] < MIN_GAP * local_pitch(y):
            out[-1] = [(out[-1][0] + y) / 2, max(out[-1][1], nv)]; merged += 1
        else:
            out.append([y, nv])
    lines, voters, inserted = [], [], 0
    for y, nv in out:
        if lines:
            gap = y - lines[-1]; lp = local_pitch((y + lines[-1]) / 2); n = int(round(gap / lp))
            if gap > MAX_GAP * lp and n >= 2:
                base = lines[-1]
                for i in range(1, n):
                    lines.append(base + gap * i / n); voters.append(0); inserted += 1
        lines.append(y); voters.append(nv)
    return {"lines": lines, "voters": voters, "pitch": pitch, "votes": len(votes), "clusters": len(clusters),
            "kept": len(kept), "merged": merged, "inserted": inserted}


def lst(ids, cap=10):
    """Frame numbers as text: '165, 170, 190' (the first `cap`, then '…')."""
    ids = sorted(ids)
    return ", ".join(str(i) for i in ids[:cap]) + (" …" if len(ids) > cap else "")


def unit_over(f, y1, y2):
    """The frame's unit that speaks for the rack band y1..y2 (overlap >= OVERLAP of the smaller one), or None."""
    best = None
    for u in f["an"]["units"]:
        ov = max(0.0, min(y2, u["y"][1]) - max(y1, u["y"][0])) / max(1.0, min(y2 - y1, u["y"][1] - u["y"][0]))
        if ov >= OVERLAP and (best is None or ov > best[0]):
            best = (ov, u)
    return None if best is None else best[1]


def rack_row_over(y1, y2, rack_rows):
    best = None
    for r in rack_rows:
        b = r["box"]
        ov = max(0.0, min(y2, b[3]) - max(y1, b[1])) / max(1.0, min(y2 - y1, b[3] - b[1]))
        if ov >= OVERLAP and (best is None or ov > best[0]):
            best = (ov, r)
    return None if best is None else best[1]


def vote_units(fused, fa, rack_rows, candidates=()):
    """Occupancy of every fused unit from the frames that see it; the rack image's answer where fewer than VOTE_MIN do.
    candidates: the detector's weaker boxes on the rack picture (CANDIDATE_SCORE up); a unit the classifiers alone call
    free under one of them is "uncertain" with candidate_box set, so no device is dropped unseen."""
    below = occ.UNCERTAIN_BELOW
    out = []
    for g in fused:
        y1, y2 = g["box"][1], g["box"][3]
        cand = max((float(b[4]) for b in candidates if min(b[3], y2) - max(b[1], y1) >= DM.COVER * (y2 - y1)), default=None)
        votes = [(f["quality"], u, f["index"]) for f in fa for u in [unit_over(f, y1, y2)] if u is not None]
        n = len(votes)
        seen = [i for _, _, i in votes]
        box_l = [i for _, u, i in votes if u["device_seen"]]; emp_l = [i for _, u, i in votes if u["empty_seen"]]; typed_l = [i for _, u, i in votes if u["typed_seen"]]
        dev_f, emp_f, typed_f = len(box_l), len(emp_l), len(typed_l)
        typed = max((u["typed"] for _, u, _ in votes if u["typed_seen"]), key=[u["typed"] for _, u, _ in votes if u["typed_seen"]].count, default=None)
        p = (sum(q * u["p"] for q, u, _ in votes) / sum(q for q, _, _ in votes)) if votes else None
        rr = rack_row_over(y1, y2, rack_rows)
        rack_status = rr["status"] if rr else None
        lean = None if p is None else ("occupied" if p >= 0.5 else "unoccupied")
        wins = lambda k: k >= VOTE_MIN and 2 * k >= n                      # at least half of the frames that see the unit
        saw = f"{n} frame{'s' if n != 1 else ''} saw this unit ({lst(seen)})." if n else "No frame saw this unit."
        if wins(dev_f) and dev_f > emp_f:
            status, by = "occupied", f"{dev_f} of {n} frames"
            how = (f"{saw} The device detector drew a box over it in {dev_f} of them ({lst(box_l)}); the classifiers' mean was {p:.0%} occupied. "
                   f"A box in at least half of the frames wins, so: occupied.")
        elif wins(emp_f) and emp_f > dev_f:
            status, by = "unoccupied", f"{emp_f} of {n} frames"
            how = (f"{saw} The device finder saw an empty slot or a closed panel in {emp_f} of them ({lst(emp_l)}); the classifiers' mean was {p:.0%} occupied. "
                   f"An empty slot in at least half of the frames wins, so: free.")
        elif n >= VOTE_MIN:
            if wins(typed_f) and lean == "unoccupied":
                status, by = "uncertain", f"{n} frames"
                how = (f"{saw} The device finder saw a device in {typed_f} of them ({lst(typed_l)}), but the classifiers' mean was only {p:.0%} occupied and no detector box owned the unit. "
                       f"Finder and classifier disagree, so: uncertain, for a person to check.")
            elif (dev_f and lean == "unoccupied") or (emp_f and lean == "occupied"):
                status, by = "uncertain", f"{n} frames"
                how = (f"{saw} {'A detector box over it in ' + str(dev_f) + ' (' + lst(box_l) + ')' if dev_f else 'An empty slot in ' + str(emp_f) + ' (' + lst(emp_l) + ')'}, "
                       f"fewer than half, while the classifiers' mean leans {('occupied' if lean == 'occupied' else 'free')} ({p:.0%} occupied). The evidence conflicts, so: uncertain, for a person to check.")
            elif max(p, 1 - p) < below:
                status, by = "uncertain", f"{n} frames"
                how = (f"{saw} No detector box and no empty slot in any of them; the classifiers' mean is {p:.0%} occupied, less than {below:.0%} sure either way, so: uncertain, for a person to check.")
            else:
                status, by = lean, f"classifier over {n} frames"
                how = (f"{saw} No detector box and no empty slot in any of them; the classifiers' mean is {p:.0%} occupied, so: {('occupied' if lean == 'occupied' else 'free')}.")
        elif rr is not None:
            status, by = rr["status"], f"rack image ({rr['decided_by']})"
            how = f"{saw} Fewer than {VOTE_MIN} frames, so the rack image's own answer stays."
        else:
            status, by, how = "uncertain", "no frame", "No frame and no rack unit here, so: uncertain."
        if rr is None:
            why_image = f"The rack image's own ladder has no unit here (it found {len(rack_rows)} units), so it gave no answer."
        else:
            rp = rr.get("p_occupied", 0.5); rb = rr.get("decided_by", "")
            base = {"occupied": "Occupied", "unoccupied": "Free", "uncertain": "Uncertain"}.get(rr["status"], rr["status"])
            m_ = re.search(r"\(([0-9.]+)\)", rr.get("device") or "")      # the finder's sighting, its class left out (no type anywhere)
            seen_dev = f"a device ({float(m_.group(1)):.0%})" if m_ else "a device"
            if rb == "classifier":
                why_image = f"{base} on its unit {rr['unit'].upper()}: the classifier alone said {rp:.0%} occupied, and no device finder box lay over it."
            elif rb == "classifier + device detector":
                why_image = f"{base} on its unit {rr['unit'].upper()}: the classifier ({rp:.0%} occupied) and the device finder ({seen_dev}) agreed."
            elif "warning" in rb:
                why_image = f"{base} on its unit {rr['unit'].upper()}: the device finder saw {seen_dev} but the classifier said free ({rp:.0%} occupied), so it asked for a look."
            elif rb == "device detector":
                why_image = f"{base} on its unit {rr['unit'].upper()}: the device finder saw {seen_dev} and decided."
            else:
                why_image = f"{base} on its unit {rr['unit'].upper()}: {rr.get('reason') or rb}"
        if status == "unoccupied" and cand is not None and not (wins(emp_f) and emp_f > dev_f) and ((0.5 if p is None else p) >= 0.3 or n < 3):
            status, by = "uncertain", "possible device"      # free by the classifiers alone, but the detector sees something here (and the frames, when 3 or more saw it, were not firmly free: user 2026-10-30, U25 and U14 of Network Rack 1)
            how += (f" The device detector draws a possible device over this unit on the rack picture ({cand:.0%} sure, below its usual "
                    f"{DM.SCORE:.0%}), so: not sure, for a person to check rather than free.")
        pp = 0.5 if p is None else p
        out.append({**g, "status": status, "p_occupied": pp, "leaning": lean or status, "confidence": max(pp, 1 - pp), "candidate_box": cand if status == "uncertain" else None,
                    "uncertain": status == "uncertain", "decided_by": by, "attention": "uncertain" if status == "uncertain" else None,
                    "reason": how, "why_image": why_image, "frames_seen": sorted(seen), "frames_box": sorted(box_l), "frames_emptyslot": sorted(emp_l),
                    "side_angle": False, "device": None, "rack_unit": rr["unit"] if rr else None, "rack_status": rack_status,
                    "frames_voting": n, "frames_device": dev_f, "frames_empty": emp_f, "frames_typed": typed_f,
                    "changed": rack_status is not None and status != rack_status})
    return out


def trim_frame_rows(rows, pitch):
    """The rack's frame is no unit (user, 2026-10-27): the fused ladder can reach past the rails onto the frame above the
    top unit or below the bottom one. A row at either end that no frame saw and whose two lines the frames did not both
    draw, or that is thinner than SLIVER of a unit, lies on the frame: such rows are dropped, top and bottom, and the
    units are renumbered from the bottom. Returns (rows, {"top": n, "bottom": n})."""
    def on_frame(r):
        return (r["box"][3] - r["box"][1]) < SLIVER * pitch or (not r.get("frames_voting") and (r.get("det_conf") or 0) < 1)
    top = bottom = 0
    while len(rows) > 1 and on_frame(rows[0]):
        rows = rows[1:]; top += 1
    while len(rows) > 1 and on_frame(rows[-1]):
        rows = rows[:-1]; bottom += 1
    n = len(rows)
    for i, r in enumerate(rows):
        r["unit"] = f"u{n - i:02d}"
    return rows, {"top": top, "bottom": bottom}


def retrim(run_dir, top=0, bottom=0):
    """trim_frame_rows applied to a finished run's result.json (runs made before the rule), plus `top` / `bottom` rows a
    person says lie on the frame: the rows, the devices' units, the seams and the summary are renumbered and the three
    rack pictures redrawn. Returns what was trimmed."""
    run_dir = pathlib.Path(run_dir); rf = run_dir / "result.json"; r = json.loads(rf.read_text())
    rows = r["occupancy_frames"]["rows"]; old = [x["unit"] for x in rows]
    rows, trimmed = trim_frame_rows(rows, r["ladder"]["pitch_px"])
    if top or bottom:
        rows = rows[top: len(rows) - bottom]; trimmed = {"top": trimmed["top"] + top, "bottom": trimmed["bottom"] + bottom}
        for i, x in enumerate(rows):
            x["unit"] = f"u{len(rows) - i:02d}"
    was = r["ladder"].get("trimmed") or {"top": 0, "bottom": 0}
    r["ladder"]["trimmed"] = {"top": was["top"] + trimmed["top"], "bottom": was["bottom"] + trimmed["bottom"]}
    if trimmed["top"] or trimmed["bottom"]:
        rename = dict(zip(old[trimmed["top"]: len(old) - trimmed["bottom"]], [x["unit"] for x in rows]))
        devices = []
        for d in r["devices"]:
            us = [rename[u] for u in d["units"] if u in rename]
            if us:                                           # a device whose units all lay on the frame was no device
                d.update(units=us, top=us[0], bottom=us[-1], height=len(us), label=f"D{d['index']} · {len(us)}U"); devices.append(d)
        for s in r.get("seams") or []:
            s["above"], s["below"] = rename.get(s["above"], s["above"]), rename.get(s["below"], s["below"])
        heights = [x["box"][3] - x["box"][1] for x in rows]
        r["devices"] = devices; r["occupancy_frames"]["rows"] = rows; r["occupancy_frames"]["summary"] = occ.summarize(rows)
        r["ladder"].update(units=len(rows), unit_height_px=[int(min(heights)), int(max(heights))])
        rack_bgr = cv2.imread(str(run_dir / "rack.png")); rack_pil = Image.fromarray(cv2.cvtColor(rack_bgr, cv2.COLOR_BGR2RGB))
        fused = [{k: x[k] for k in ("unit", "box", "source", "det_conf")} for x in rows]
        P.draw_units(rack_bgr, fused, {"x_left": rows[0]["box"][0], "x_right": rows[0]["box"][2]}).save(run_dir / "rack_units.jpg", quality=85)
        P.draw_occupancy(rack_pil, rows).save(run_dir / "rack_occupancy_frames.jpg", quality=85)
        draw_polygons(rack_pil, rows, devices).save(run_dir / "rack_devices.jpg", quality=85)
    rf.write_text(json.dumps(r, indent=1))
    return trimmed


REG_MIN_RATIO = 1.3        # a row or gap must be at least this many units out of size before it is touched


def regularise_rows(rows):
    """Every rack unit is the same size (user, 2026-10-30), so a ladder of N units has N rows. N comes from the ladder
    itself: its span at the median spacing of its detected rows. Until the rows count N, the most deviant one is fixed:
    the tallest row is split into the units it holds, the widest gap gets a unit, or (too many rows) the two closest
    rows merge. Nothing less than REG_MIN_RATIO units out of size is touched, and a ladder that already counts N is left
    as it is. The rows' heights are NOT evened out: the stitched picture is not to scale where the stitch stretched, and
    forcing equal rows onto it moves lines off the real units (it turned 1U devices into 2U on the server room rack).
    Equal heights have to come from a stitch that is to scale, not from this step. Rows come top to bottom and leave
    top to bottom, renumbered from the bottom (u01). Returns (rows, counts, rename); counts["carved"] maps an old unit
    name to the new rows carved from it."""
    rows = [dict(x, _old=x["unit"]) for x in sorted(rows, key=lambda x: x["box"][1])]
    counts = {"split": 0, "inserted": 0, "merged": 0}
    if len(rows) < 4:
        for i, x in enumerate(rows):
            x["unit"] = f"u{len(rows) - i:02d}"
        return rows, dict(counts, carved={}), {x["_old"]: x["unit"] for x in rows}
    c = lambda x: (x["box"][1] + x["box"][3]) / 2
    det = [x for x in rows if x.get("source") == "detected"]
    m = float(np.median(np.diff([c(x) for x in (det if len(det) > 2 else rows)])))
    target = int(round((c(rows[-1]) - c(rows[0])) / m)) + 1
    blank = {k: None for k in ("p_occupied", "device", "candidate_box", "attention", "why_image")}
    for _ in range(40):
        n = len(rows)
        if n < target:
            tall = max(range(n), key=lambda k: rows[k]["box"][3] - rows[k]["box"][1]); hr = (rows[tall]["box"][3] - rows[tall]["box"][1]) / m
            gaps = [(c(rows[k + 1]) - c(rows[k])) / m for k in range(n - 1)]; wide = max(range(n - 1), key=lambda k: gaps[k]); gr = gaps[wide]
            if max(hr, gr) < REG_MIN_RATIO:
                break
            if hr >= gr:
                x = rows[tall]; h = x["box"][3] - x["box"][1]
                parts = [dict(x, box=[x["box"][0], round(x["box"][1] + h * i / 2), x["box"][2], round(x["box"][1] + h * (i + 1) / 2)], source="split", _old=x["_old"] if i == 0 else None,
                              _from=list(x.get("_from") or ([x["_old"]] if x.get("_old") else [])),
                              reason=f"This row was {hr:.1f} units tall and was split into units of the same size. " + str(x.get("reason") or "")) for i in range(2)]
                rows[tall:tall + 1] = parts; counts["split"] += 1
            else:
                a, b = rows[wide], rows[wide + 1]; y0, y1 = a["box"][3], b["box"][1]
                if y1 - y0 < 0.4 * m:
                    y0, y1 = round((c(a) + c(b)) / 2 - m / 2), round((c(a) + c(b)) / 2 + m / 2); a["box"][3] = y0; b["box"][1] = y1
                rows.insert(wide + 1, dict(blank, unit=None, box=[a["box"][0], round(y0), a["box"][2], round(y1)], source="inferred", det_conf=0.0, status="uncertain", confidence=0.5,
                                           decided_by="inserted", reason=f"The rows above and below were {gr:.1f} units apart, so a unit must lie between them. Left for a person.",
                                           rack_unit=None, rack_status=None, frames_voting=0, frames_device=0, frames_empty=0, changed=False, frames_seen=[], frames_box=[], frames_emptyslot=[], _old=None))
                counts["inserted"] += 1
        elif n > target:
            gaps = [(c(rows[k + 1]) - c(rows[k])) / m for k in range(n - 1)]; k = min(range(n - 1), key=lambda q: gaps[q])
            if gaps[k] > 1 / REG_MIN_RATIO:
                break
            a, b = rows[k], rows[k + 1]; keep, drop = (a, b) if (a.get("confidence") or 0) >= (b.get("confidence") or 0) else (b, a)
            keep["box"] = [a["box"][0], min(a["box"][1], b["box"][1]), a["box"][2], max(a["box"][3], b["box"][3])]; keep["source"] = "merged"; keep["_old"] = keep.get("_old") or drop.get("_old")
            keep["_from"] = [o for o in (a.get("_from") or [a.get("_old")]) + (b.get("_from") or [b.get("_old")]) if o]
            rows[k:k + 2] = [keep]; counts["merged"] += 1
        else:
            break
    rename, carved = {}, {}
    for i, x in enumerate(rows):
        new = f"u{len(rows) - i:02d}"
        if x.get("_old"):
            rename[x["_old"]] = new
        for o in x.get("_from") or ([x["_old"]] if x.get("_old") else []):
            carved.setdefault(o, []).append(new)
        x["unit"] = new; x.pop("_old", None); x.pop("_from", None)
    counts["carved"] = carved
    return rows, counts, rename


def regularise_result(r, run_dir=None):
    """regularise_rows on a run's result (in place): the rows, each device's units by where its outline sits, the seams,
    the summary and the ladder counts; the three rack pictures redrawn when run_dir is given. Returns the counts."""
    if r.get("ladder", {}).get("regularised"):              # once per run: a second pass would measure its own split rows
        return {"split": 0, "inserted": 0, "merged": 0}
    rows, counts, rename = regularise_rows(r["occupancy_frames"]["rows"])
    carved = counts.pop("carved", {})
    if not any(counts.values()):
        return counts
    touched = {o for o, news in carved.items() if len(news) != 1 or rename.get(o) != news[0]}
    order = [x["unit"] for x in rows]
    def units_of(d):
        olds = d.get("units") or []
        if olds and not any(u in touched for u in olds) and all(u in rename for u in olds):
            return [rename[u] for u in olds]                 # nothing changed around it: the same units, renamed
        us = sorted({n for u in olds for n in carved.get(u, [])}, key=order.index)
        if d.get("polygon_from") != "units" and d.get("polygon"):   # a detector outline: the rows whose centre it covers
            ys = [q[1] for q in d["polygon"]]; y0, y1 = min(ys), max(ys)
            geo = [x["unit"] for x in rows if y0 <= (x["box"][1] + x["box"][3]) / 2 <= y1]
            if geo and abs(len(geo) - len(us)) <= 1:
                us = geo
        return us or [rename[u] for u in olds if u in rename]
    for d in r["devices"]:
        us = units_of(d)
        if us:
            d.update(units=us, top=us[0], bottom=us[-1], height=len(us), label=f"D{d['index']} · {len(us)}U")
    r["seams"] = [dict(s, above=rename[s["above"]], below=rename[s["below"]]) for s in r.get("seams") or [] if s.get("above") in rename and s.get("below") in rename]
    heights = [x["box"][3] - x["box"][1] for x in rows]
    r["occupancy_frames"]["rows"] = rows; r["occupancy_frames"]["summary"] = occ.summarize(rows)
    r["ladder"].update(units=len(rows), unit_height_px=[int(min(heights)), int(max(heights))], regularised=counts)
    if run_dir is not None:
        run_dir = pathlib.Path(run_dir)
        rack_bgr = cv2.imread(str(run_dir / "rack.png")); rack_pil = Image.fromarray(cv2.cvtColor(rack_bgr, cv2.COLOR_BGR2RGB))
        fused = [{k: x[k] for k in ("unit", "box", "source", "det_conf")} for x in rows]
        P.draw_units(rack_bgr, fused, {"x_left": rows[0]["box"][0], "x_right": rows[0]["box"][2]}).save(run_dir / "rack_units.jpg", quality=85)
        P.draw_occupancy(rack_pil, rows).save(run_dir / "rack_occupancy_frames.jpg", quality=85)
        draw_polygons(rack_pil, rows, r["devices"]).save(run_dir / "rack_devices.jpg", quality=85)
    return counts


def rectify_map(rows, H):
    """The vertical correction that makes every unit row the same height, from the rows themselves: the row boundaries
    (the middle of each gap) are the knots of a piecewise-linear map old y -> new y whose targets are evenly spaced
    from the first row's top to the last row's bottom. Above and below the ladder the picture is left as it is, so the
    picture's height does not change. Returns (knots_old, knots_new) as arrays over [0, H]."""
    rows = sorted(rows, key=lambda x: x["box"][1]); n = len(rows)
    b = [rows[0]["box"][1]] + [(rows[k]["box"][3] + rows[k + 1]["box"][1]) / 2 for k in range(n - 1)] + [rows[-1]["box"][3]]
    p = (b[-1] - b[0]) / n; nb = [b[0] + k * p for k in range(n + 1)]
    ko = np.array([0.0] + b + [float(H)]); kn = np.array([0.0] + nb + [float(H)])
    return ko, kn


def _ymap(ko, kn):
    return lambda y: float(np.interp(y, ko, kn))


def rectify_result(r, rack_bgr, views=None):
    """Make every unit the same height (user, 2026-10-30): the stitched picture is resampled along y with rectify_map,
    and everything in rack coordinates follows: the unit rows (set to the equal rows exactly), the rack-image pass rows,
    each device's outline, old outline and box, the ports' rack polygons, and each device frame's homography (composed
    with the local affine of the map over the device's rows, exact where the map is linear and within a pixel elsewhere).
    views = {index: {rank: (frame, H)}} is updated in place when given. Returns the rectified picture, or None when the
    rows are already even (every row within 4 % of the median)."""
    rows = sorted(r["occupancy_frames"]["rows"], key=lambda x: x["box"][1])
    if len(rows) < 4:
        return None
    hs = np.array([x["box"][3] - x["box"][1] for x in rows]); med = float(np.median(hs))
    if np.all(np.abs(hs - med) <= 0.04 * med):
        return None
    Hh, Ww = rack_bgr.shape[:2]; ko, kn = rectify_map(rows, Hh); f = _ymap(ko, kn)
    inv = lambda y: float(np.interp(y, kn, ko))
    ys = np.arange(Hh, dtype=np.float32); src_y = np.interp(ys, kn, ko).astype(np.float32)
    map_y = np.repeat(src_y[:, None], Ww, axis=1); map_x = np.repeat(np.arange(Ww, dtype=np.float32)[None, :], Hh, axis=0)
    out = cv2.remap(rack_bgr, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    n = len(rows); p = (kn[n + 1] - kn[1]) / n
    for k, x in enumerate(rows):                             # the rows: exactly equal now
        x["box"] = [x["box"][0], round(float(kn[1] + k * p)), x["box"][2], round(float(kn[1] + (k + 1) * p))]
    poly_f = lambda poly: [[round(float(q[0]), 1), round(f(q[1]), 1)] for q in poly]
    box_f = lambda b: [b[0], round(f(b[1]), 1) if isinstance(b[1], float) else round(f(b[1])), b[2], round(f(b[3]), 1) if isinstance(b[3], float) else round(f(b[3]))]
    for x in (r.get("rack") or {}).get("rows") or []:
        if x.get("box"):
            x["box"] = box_f(x["box"])
    for d in r.get("devices") or []:
        for key in ("polygon", "polygon_old"):
            if d.get(key):
                d[key] = poly_f(d[key])
        if d.get("box"):
            d["box"] = box_f(d["box"])
        if d.get("candidate_box"):
            d["candidate_box"] = box_f(d["candidate_box"])
        ys_ = [q[1] for q in d.get("polygon") or []]
        if ys_:                                              # the local affine of the map over this device: y' = s*y + t
            y0, y1 = min(ys_), max(ys_); oy0, oy1 = inv(y0), inv(y1)
            s = (y1 - y0) / (oy1 - oy0) if oy1 > oy0 else 1.0; tt = y0 - s * oy0
            A = np.array([[1.0, 0.0, 0.0], [0.0, s, tt], [0.0, 0.0, 1.0]])
            for tf in d.get("top_frames") or []:
                if tf.get("H") is not None:
                    tf["H"] = (A @ np.asarray(tf["H"], np.float64)).tolist()
                    if views is not None and d["index"] in views and tf["rank"] in views[d["index"]]:
                        views[d["index"]][tf["rank"]] = (views[d["index"]][tf["rank"]][0], np.asarray(tf["H"], np.float64))
        for src in (d.get("layout") or {}, d.get("closeup") or {}):
            for pt in src.get("ports") or []:
                if pt.get("rack_polygon"):
                    pt["rack_polygon"] = poly_f(pt["rack_polygon"])
            for rd in src.get("reads") or []:
                pass
    r["occupancy_frames"]["rows"] = rows
    r["ladder"].update(unit_height_px=[int(round(p))] * 2, pitch_px=round(float(p), 1), rectified={"knots": int(len(ko)), "max_shift_px": round(float(np.max(np.abs(kn - ko))), 1)})
    return out


def deshear_result(r, rack_bgr, views=None):
    """Take the lean out of the stitched picture (user, 2026-10-30: "the top rail is not straight"): every device's
    outline from the detector carries the local lean of the picture (its top and bottom edges slope where the stitcher
    joined frames at an angle). The lean s(y) is read off those outlines, interpolated down the rack, and the picture
    is resampled so y' = y - s(y) (x - xc): a line that leaned is level afterwards. Everything in rack coordinates
    follows, and each device frame's homography is composed with the local affine. The unit rows, which run the full
    width, keep their y at the centre. Returns the corrected picture, or None when the lean is under 0.5 % everywhere."""
    Hh, Ww = rack_bgr.shape[:2]; xc = Ww / 2
    samples = []
    for d in r.get("devices") or []:
        if d.get("polygon_from") != "frames" or not d.get("polygon"):
            continue
        q = np.array(d["polygon"], np.float64); mx = q[:, 0].mean(); L = q[q[:, 0] < mx]; R = q[q[:, 0] >= mx]
        if len(L) < 2 or len(R) < 2 or R[:, 0].mean() - L[:, 0].mean() < 0.3 * Ww:
            continue
        s = ((R[:, 1].min() - L[:, 1].min()) + (R[:, 1].max() - L[:, 1].max())) / 2 / (R[:, 0].mean() - L[:, 0].mean())
        samples.append((float(q[:, 1].mean()), float(np.clip(s, -0.25, 0.25))))
    if len(samples) < 2 or max(abs(s) for _y, s in samples) < 0.005:
        return None
    samples.sort(); ys = np.array([y for y, _ in samples]); ss = np.array([s for _, s in samples])
    knots_y = np.concatenate([[0.0], ys, [float(Hh)]]); knots_s = np.concatenate([[ss[0]], ss, [ss[-1]]])
    s_of = lambda y: float(np.interp(y, knots_y, knots_s))
    yy = np.arange(Hh, dtype=np.float32)[:, None]; xx = np.arange(Ww, dtype=np.float32)[None, :]
    s_col = np.interp(yy[:, 0], knots_y, knots_s).astype(np.float32)[:, None]
    map_y = (yy + s_col * (xx - xc)).astype(np.float32); map_x = np.repeat(xx, Hh, axis=0).astype(np.float32)
    out = cv2.remap(rack_bgr, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    fwd = lambda x, y: y - s_of(y) * (x - xc)
    poly_f = lambda poly: [[round(float(q[0]), 1), round(fwd(q[0], q[1]), 1)] for q in poly]
    for d in r.get("devices") or []:
        for key in ("polygon", "polygon_old"):
            if d.get(key):
                d[key] = poly_f(d[key])
        if d.get("polygon"):
            xs_ = [q[0] for q in d["polygon"]]; ys_ = [q[1] for q in d["polygon"]]
            d["box"] = [round(min(xs_)), round(min(ys_)), round(max(xs_)), round(max(ys_))]
            s = s_of(float(np.mean(ys_))); A = np.array([[1.0, 0.0, 0.0], [-s, 1.0, s * xc], [0.0, 0.0, 1.0]])
            for tf in d.get("top_frames") or []:
                if tf.get("H") is not None:
                    tf["H"] = (A @ np.asarray(tf["H"], np.float64)).tolist()
                    if views is not None and d["index"] in views and tf["rank"] in views[d["index"]]:
                        views[d["index"]][tf["rank"]] = (views[d["index"]][tf["rank"]][0], np.asarray(tf["H"], np.float64))
        for src in (d.get("layout") or {}, d.get("closeup") or {}):
            for pt in src.get("ports") or []:
                if pt.get("rack_polygon"):
                    pt["rack_polygon"] = poly_f(pt["rack_polygon"])
    r["ladder"]["desheared"] = {"samples": len(samples), "max_lean_pct": round(float(max(abs(ss)) * 100), 1)}
    return out


def redraw_run(run_dir):
    """The space and device pictures again from result.json, after answers or removals changed the rows or devices."""
    run_dir = pathlib.Path(run_dir); r = json.loads((run_dir / "result.json").read_text()); rows = r["occupancy_frames"]["rows"]
    rack_pil = Image.open(run_dir / "rack.png").convert("RGB")
    P.draw_occupancy(rack_pil, rows).save(run_dir / "rack_occupancy_frames.jpg", quality=85)
    draw_polygons(rack_pil, rows, r["devices"]).save(run_dir / "rack_devices.jpg", quality=85)


def deshear_run(run_dir):
    run_dir = pathlib.Path(run_dir); rf = run_dir / "result.json"; r = json.loads(rf.read_text())
    rack_bgr = cv2.imread(str(run_dir / "rack.png")); out = deshear_result(r, rack_bgr)
    if out is None:
        return None
    cv2.imwrite(str(run_dir / "rack.png"), out); rows = r["occupancy_frames"]["rows"]; rack_pil = Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB))
    fused = [{k: x[k] for k in ("unit", "box", "source", "det_conf")} for x in rows]
    P.draw_units(out.copy(), fused, {"x_left": rows[0]["box"][0], "x_right": rows[0]["box"][2]}).save(run_dir / "rack_units.jpg", quality=85)
    P.draw_occupancy(rack_pil, rows).save(run_dir / "rack_occupancy_frames.jpg", quality=85)
    draw_polygons(rack_pil, rows, r["devices"]).save(run_dir / "rack_devices.jpg", quality=85)
    for f in run_dir.glob("section_D*_clean*.jpg"):
        f.unlink()
    rf.write_text(json.dumps(r, indent=1))
    return r["ladder"]["desheared"]


def rectify_run(run_dir):
    """rectify_result on a stored run: rack.png and the three rack pictures rewritten, the port traces redrawn, the
    cached clean crops dropped (they are cut again from the frames with the new mappings)."""
    run_dir = pathlib.Path(run_dir); rf = run_dir / "result.json"; r = json.loads(rf.read_text())
    rack_bgr = cv2.imread(str(run_dir / "rack.png"))
    out = rectify_result(r, rack_bgr)
    if out is None:
        return None
    cv2.imwrite(str(run_dir / "rack.png"), out)
    rows = r["occupancy_frames"]["rows"]; rack_pil = Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB))
    fused = [{k: x[k] for k in ("unit", "box", "source", "det_conf")} for x in rows]
    P.draw_units(out.copy(), fused, {"x_left": rows[0]["box"][0], "x_right": rows[0]["box"][2]}).save(run_dir / "rack_units.jpg", quality=85)
    P.draw_occupancy(rack_pil, rows).save(run_dir / "rack_occupancy_frames.jpg", quality=85)
    draw_polygons(rack_pil, rows, r["devices"]).save(run_dir / "rack_devices.jpg", quality=85)
    for d in r["devices"]:
        L = d.get("layout") or {}
        if (L.get("fitted") or L.get("auto")) and any(pt.get("rack_polygon") for pt in L.get("ports") or []):
            try:
                rack_overlay(out, d["polygon"], L["ports"], run_dir / f"layout_D{d['index']}_rack_trace.jpg")
            except Exception:
                pass
    for f in run_dir.glob("section_D*_clean*.jpg"):
        f.unlink()
    rf.write_text(json.dumps(r, indent=1))
    return r["ladder"]["rectified"]


def m_of(rows):
    return float(np.median([x["box"][3] - x["box"][1] for x in rows]))


def regularise_run(run_dir):
    """regularise_result on a stored run's result.json."""
    run_dir = pathlib.Path(run_dir); rf = run_dir / "result.json"; r = json.loads(rf.read_text())
    counts = regularise_result(r, run_dir)
    rf.write_text(json.dumps(r, indent=1))
    return counts


CROP_INSIDE = 0.9          # a frame is used for a crop only when this share of the device's box falls inside it
UNIT_SHARE = 44.45 / 482.6  # one rack unit's height as a share of a 19-inch panel's width


def _crop_boxes(dv, devices):
    """Where to cut a device's crop, best first: (frame number, [x1, y1, x2, y2] in that frame). A device whose outline
    the detector confirmed: its polygon back-projected into its own best frames. One whose outline comes from its unit
    rows has no box to confirm any frame's mapping, and its rail-to-rail outline lands a unit off or slanted (Network
    Rack 1 D5 showed D4): its box is laid against the box of its nearest detector-confirmed neighbour in that neighbour's
    best frames, as wide as the neighbour, the units between them and its own units tall at the neighbour's unit height."""
    def bbox(q):
        xs = [p[0] for p in q]; ys = [p[1] for p in q]
        return [min(xs), min(ys), max(xs), max(ys)]
    if dv.get("polygon_from") != "units" or not devices:          # a detector-confirmed device: its own frames place it
        return [(t["frame"], bbox(back_project_poly(np.asarray(t["H"], np.float64), dv["polygon"])), t["H"]) for t in dv.get("top_frames") or [] if t.get("H") is not None]
    num = lambda u: int(str(u)[1:])
    T, B = num(dv["top"]), num(dv["bottom"])
    near = sorted((x for x in devices if x is not dv and x.get("polygon_from") == "frames" and x.get("polygon") and (num(x["bottom"]) > T or num(x["top"]) < B)),
                  key=lambda x: min(abs(num(x["bottom"]) - T), abs(B - num(x["top"]))))[:2]
    out = []
    for nb in near:
        for t in nb.get("top_frames") or []:
            if t.get("H") is None:
                continue
            q = back_project_poly(np.asarray(t["H"], np.float64), nb["polygon"]); x1, y1, x2, y2 = bbox(q)
            uh = UNIT_SHARE * (x2 - x1)                                     # a tilted box is taller than its units: one unit is 44.45 mm of a 482.6 mm panel
            if num(nb["bottom"]) > T:                                       # the neighbour is above
                top = y2 + (num(nb["bottom"]) - T - 1) * uh; box = [x1, top, x2, top + dv["height"] * uh]
            else:                                                           # the neighbour is below
                bot = y1 - (B - num(nb["top"]) - 1) * uh; box = [x1, bot - dv["height"] * uh, x2, bot]
            out.append((t["frame"], box, t["H"]))
    return out


def crop_source(dv, devices, frame_of):
    """Where the device's crop is cut from, one rule for the crop and the overlay: the first candidate of _crop_boxes
    whose box lies inside its frame (CROP_INSIDE), snapped to the device detector's box in that frame when one matches
    (crop_box_in_frame). frame_of(n) gives the frame image, or None. Returns (frame_no, [x1, y1, x2, y2], H) or None."""
    for f, b, H in _crop_boxes(dv, devices):
        if not (b[2] > b[0] and b[3] > b[1]):
            continue
        img = frame_of(f)
        if img is None:
            continue
        h, w = img.shape[:2]; area = (b[2] - b[0]) * (b[3] - b[1])
        if max(0, min(w, b[2]) - max(0, b[0])) * max(0, min(h, b[3]) - max(0, b[1])) / area < CROP_INSIDE:
            continue
        snapped = None
        if "snapped to its units" not in str(dv.get("polygon_refined") or ""):      # before the snap: the detector's box places the device
            try:
                snapped = crop_box_in_frame(dict(dv, polygon=[[q[0], q[1]] for q in cv2.perspectiveTransform(np.array([[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]]], np.float64), np.asarray(H, np.float64))[0].tolist()]), img, H)
            except Exception:
                snapped = None
        return f, [float(v) for v in (snapped or b)], H
    return None


def crop_box_in_frame(dv, frame_bgr, H, devices=None):
    """The box the device's clean crop is cut from, in its frame: the outline back-projected into the frame, snapped
    to the device detector's box there (overlap 0.3 or more, not much taller than the device). None when no box."""
    import device_model
    q = cv2.perspectiveTransform(np.array([dv["polygon"]], np.float64), np.linalg.inv(np.asarray(H, np.float64)))[0]
    exp = [float(q[:, 0].min()), float(q[:, 1].min()), float(q[:, 0].max()), float(q[:, 1].max())]; eh = exp[3] - exp[1]
    def iou(a, b):
        ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1])); i = ix * iy
        u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i
        return i / u if u > 0 else 0.0
    best = max(((iou(exp, b[:4]), [float(v) for v in b[:4]]) for b in device_model.detect(frame_bgr[:, :, ::-1]) if (b[3] - b[1]) <= 1.15 * eh), default=(0, None), key=lambda c: c[0])
    return best[1] if best[0] >= 0.3 else None


def outlines_from_crops(devices, views, unit_px=None):
    """The overlay drawn from the same box as the crop (user, 2026-10-30: the crops are right, the overlay spans other
    devices): each device's outline becomes the detector's box in its best frame, carried onto the rack picture through
    that frame's homography. Then no two outlines may overlap: where neighbours do, both are cut at the middle of the
    overlap. views = {index: {rank: (frame_bgr, H)}}. Returns the indexes whose outline came from the crop box."""
    done = []
    frames = {}                                              # frame number -> image, from the views of every device
    for v in views.values():
        for fr, _H in v.values():
            pass
    for dv in devices:
        for t in dv.get("top_frames") or []:
            v = views.get(dv["index"]) or {}
            if t["rank"] in v:
                frames[t["frame"]] = v[t["rank"]][0]
    for dv in devices:
        if not dv.get("polygon") or dv.get("polygon_from") == "person":
            continue
        src = crop_source(dv, devices, frames.get)
        if src is None:
            continue
        f, (x1, y1, x2, y2), H = src
        quad = cv2.perspectiveTransform(np.array([[[x1, y1], [x2, y1], [x2, y2], [x1, y2]]], np.float64), np.asarray(H, np.float64))[0]
        new_h = float(quad[:, 1].max() - quad[:, 1].min()); expect = (unit_px or (max(q[1] for q in dv["polygon"]) - min(q[1] for q in dv["polygon"]) / max(1, dv.get("height") or 1))) * max(1, dv.get("height") or 1)
        if not 0.55 <= new_h / max(1.0, expect) <= 1.6:
            continue                                         # a box of a different size than this device's units: not it
        dv["polygon_old"] = dv.get("polygon_old") or dv["polygon"]
        dv["polygon"] = [[round(float(x), 1), round(float(y), 1)] for x, y in quad]
        dv["polygon_refined"] = f"crop box, frame {f}"
        done.append(dv["index"])
    # no overlap between neighbours: cut both at the middle of the overlap
    order = sorted((d for d in devices if d.get("polygon")), key=lambda d: min(q[1] for q in d["polygon"]))
    for a, b in zip(order, order[1:]):
        ab = max(q[1] for q in a["polygon"]); bt = min(q[1] for q in b["polygon"])
        if ab > bt + 1:
            mid = (ab + bt) / 2
            a["polygon"] = [[x, min(y, mid)] for x, y in a["polygon"]]; b["polygon"] = [[x, max(y, mid)] for x, y in b["polygon"]]
            a["polygon_clipped"] = b["polygon_clipped"] = True
    for d in devices:
        if d.get("polygon"):
            xs = [q[0] for q in d["polygon"]]; ys = [q[1] for q in d["polygon"]]
            d["box"] = [round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))]
    return done


def snap_outlines_to_units(devices, rows, rails=None):
    """A device fills whole units (user, 2026-10-30: "not even overlaying 90 % of the device"): every outline becomes
    exactly its units tall, from the top of its first unit row to the bottom of its last. The detector box (the current
    outline) decides which units: the window of N consecutive rows that overlaps it most, N the device's height, each
    row given to one device only (the best-overlapping device first). Width: the box's own when it is at least half
    the rails (a narrow device stays narrow), else rail to rail. Returns the indexes whose units changed."""
    rows = sorted(rows, key=lambda x: x["box"][1]); n = len(rows)
    if not rows:
        return []
    unit_px = float(np.median([x["box"][3] - x["box"][1] for x in rows]))
    def box_ok(d):                                           # a detector box about as tall as the device's units is a real box, not a sliver
        if "crop box" not in str(d.get("polygon_refined") or "") or not d.get("polygon"):
            return False
        src = d.get("polygon_box") or d["polygon"]; ys = [q[1] for q in src]
        return (max(ys) - min(ys)) >= 0.6 * unit_px * max(1, int(d.get("height") or 1))
    good = [d for d in devices if box_ok(d)]
    if rails:
        rl = rails
    elif len(good) >= 3:                                     # the rails: where the real boxes' edges lie
        ls = [min(q[0] for q in (d.get("polygon_box") or d["polygon"])) for d in good]; rs = [max(q[0] for q in (d.get("polygon_box") or d["polygon"])) for d in good]
        rl = (float(np.percentile(ls, 20)), float(np.percentile(rs, 80)))
    else:
        rl = (rows[0]["box"][0], rows[0]["box"][2])
    rail_w = rl[1] - rl[0]
    by_unit = {x["unit"]: k for k, x in enumerate(rows)}
    def yr(d):
        q = np.array(d.get("polygon_box") or d["polygon"], np.float64); xc = q[:, 0].mean(); L = q[q[:, 0] < xc]; R = q[q[:, 0] >= xc]
        if len(L) < 2 or len(R) < 2:
            return float(q[:, 1].min()), float(q[:, 1].max())
        return (L[:, 1].min() + R[:, 1].min()) / 2, (L[:, 1].max() + R[:, 1].max()) / 2
    cands = []
    for d in devices:
        if not d.get("polygon") or d.get("polygon_from") == "person":
            continue
        N = max(1, int(d.get("height") or 1)); y0, y1 = yr(d)
        best = []
        free = lambda k: rows[k].get("status") == "unoccupied"
        for k in range(0, n - N + 1):
            if any(free(k + j) for j in range(N)):          # a device never sits on a unit the frames or a person called free
                continue
            wt, wb = rows[k]["box"][1], rows[k + N - 1]["box"][3]
            ov = max(0.0, min(y1, wb) - max(y0, wt)) / max(1.0, y1 - y0)
            cur = [by_unit.get(u) for u in d.get("units") or []]
            same = 0.05 if cur and cur[0] == k else 0.0                                # the current units win a near tie
            best.append((ov + same, k))
        if not best:
            continue
        best.sort(reverse=True); cands.append((best[0][0], d, best))
    taken = set(); changed = []
    for _score, d, best in sorted(cands, key=lambda c: -c[0]):
        N = max(1, int(d.get("height") or 1)); pick = None
        for ov, k in best:
            if ov <= 0 and pick is not None:
                break
            if not any(k + j in taken for j in range(N)):
                pick = k; break
        if pick is None:
            continue
        units = [rows[pick + j]["unit"] for j in range(N)]; taken.update(range(pick, pick + N))
        top, bot = rows[pick]["box"][1], rows[pick + N - 1]["box"][3]
        src = d.get("polygon_box") or d["polygon"]; xs = [q[0] for q in src]; bw = max(xs) - min(xs)
        own = d.get("polygon_from") == "frames"              # only a device the detector owns keeps a narrow box; one built from unit rows spans the rails
        x1, x2 = (min(xs), max(xs)) if (own and box_ok(d) and bw >= 0.3 * rail_w) else rl
        if units != d.get("units"):
            changed.append(d["index"])
        d["polygon_box"] = d.get("polygon_box") or d["polygon"]
        d.update(units=units, top=units[0], bottom=units[-1], height=N, label=f"D{d['index']} · {N}U",
                 polygon=[[round(float(x1), 1), round(float(top), 1)], [round(float(x2), 1), round(float(top), 1)], [round(float(x2), 1), round(float(bot), 1)], [round(float(x1), 1), round(float(bot), 1)]],
                 box=[round(float(x1)), round(float(top)), round(float(x2)), round(float(bot))], polygon_refined=str(d.get("polygon_refined") or "") + " · snapped to its units")
    for d in devices:                                        # a device added on the phone keeps its units: its outline is drawn on exactly them
        if d.get("polygon_from") == "person" and d.get("units"):
            bs = [rows[by_unit[u]]["box"] for u in d["units"] if u in by_unit]
            if bs:
                top, bot = min(b[1] for b in bs), max(b[3] for b in bs); x1, x2 = rl
                d["polygon"] = [[round(float(x1), 1), round(float(top), 1)], [round(float(x2), 1), round(float(top), 1)], [round(float(x2), 1), round(float(bot), 1)], [round(float(x1), 1), round(float(bot), 1)]]
                d["box"] = [round(float(x1)), round(float(top)), round(float(x2)), round(float(bot))]
    return changed


ALIGN_SCORE = 0.5          # only the detector's confident boxes say where a device really sits
ALIGN_MAX = 0.6            # a device is never moved by more than this share of a unit


def device_centres(devices, frames, unit_px):
    """Where each detector-owned device really sits on the rack picture: the median centre of its confident detector box
    over its best frames (the box nearest its units, about its own height), carried through each frame's homography.
    frames = {frame_no: image}. Returns {index: (observed_y, target_y)} with target the centre of its units."""
    import device_model
    targets = {}
    for d in devices:                                        # every device competes for the boxes, so none takes its neighbour's
        if d.get("polygon"):
            ys_ = [q[1] for q in d["polygon"]]; targets[d["index"]] = ((min(ys_) + max(ys_)) / 2, max(1, int(d.get("height") or 1)))
    cache = {}
    def boxes_of(f):
        if f not in cache:
            cache[f] = [b for b in device_model.detect(frames[f][:, :, ::-1]) if b[4] >= ALIGN_SCORE]
        return cache[f]
    out = {}
    for d in devices:
        if d.get("polygon_from") != "frames" or d["index"] not in targets:
            continue
        target, N = targets[d["index"]]; seen = []
        for tf in (d.get("top_frames") or [])[:3]:
            if frames.get(tf["frame"]) is None or tf.get("H") is None:
                continue
            H = np.asarray(tf["H"], np.float64); best = None
            for b in boxes_of(tf["frame"]):
                q = cv2.perspectiveTransform(np.array([[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]]], np.float64), H)[0]
                y0, y1 = float(q[:, 1].min()), float(q[:, 1].max()); h = y1 - y0; c = (y0 + y1) / 2
                if not 0.6 * unit_px * N <= h <= 2.0 * unit_px * N:
                    continue
                owner = min(targets, key=lambda k: abs(c - targets[k][0]))          # the box belongs to the device whose units it is nearest
                if owner != d["index"]:
                    continue
                if best is None or abs(c - target) < abs(best - target):
                    best = c
            if best is not None:
                seen.append(best)
        if len(seen) >= 2:
            out[d["index"]] = (float(np.median(seen)), target)
    return out


def align_to_devices(r, rack_bgr, frames, views=None):
    """The picture moved along y so every detector-owned device sits on its units (user, 2026-10-30: "the cut between
    D4 and D5 is wrong"): knots from device_centres (observed -> target), kept only when the move is under ALIGN_MAX of
    a unit and the order stays the same; picture edges fixed. The unit grid and the device outlines (on their units) stay
    where they are; the picture content and everything attached to it moves: the ports' rack polygons, the detector
    boxes kept beside the outlines, and each frame's homography. Returns the moved picture, or None when nothing moves
    by more than 2 px."""
    rows = r["occupancy_frames"]["rows"]; unit_px = float(np.median([x["box"][3] - x["box"][1] for x in rows])) if rows else 50.0
    cs = device_centres(r.get("devices") or [], frames, unit_px)
    knots = sorted((o, tg) for o, tg in cs.values() if abs(o - tg) <= ALIGN_MAX * unit_px)
    keep = []
    for o, tg in knots:                                      # the order of the devices never changes
        if not keep or (o > keep[-1][0] + 4 and tg > keep[-1][1] + 4):
            keep.append((o, tg))
    if not keep or max(abs(o - tg) for o, tg in keep) <= 2:
        return None
    Hh, Ww = rack_bgr.shape[:2]
    ko = np.array([0.0] + [o for o, _ in keep] + [float(Hh)]); kn = np.array([0.0] + [tg for _, tg in keep] + [float(Hh)])
    fwd = lambda y: float(np.interp(y, ko, kn)); inv = lambda y: float(np.interp(y, kn, ko))
    src_y = np.interp(np.arange(Hh, dtype=np.float32), kn, ko).astype(np.float32)
    out = cv2.remap(rack_bgr, np.repeat(np.arange(Ww, dtype=np.float32)[None, :], Hh, axis=0), np.repeat(src_y[:, None], Ww, axis=1), interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    poly_f = lambda poly: [[round(float(q[0]), 1), round(fwd(q[1]), 1)] for q in poly]
    for d in r.get("devices") or []:
        for key in ("polygon_box", "polygon_old"):
            if d.get(key):
                d[key] = poly_f(d[key])
        ys_ = [q[1] for q in d.get("polygon") or []]
        if ys_:                                              # the frame's homography: composed with the map's local affine over the device
            y0, y1 = min(ys_), max(ys_); oy0, oy1 = inv(y0), inv(y1)
            s = (y1 - y0) / (oy1 - oy0) if oy1 > oy0 else 1.0; tt = y0 - s * oy0
            A = np.array([[1.0, 0.0, 0.0], [0.0, s, tt], [0.0, 0.0, 1.0]])
            for tf in d.get("top_frames") or []:
                if tf.get("H") is not None:
                    tf["H"] = (A @ np.asarray(tf["H"], np.float64)).tolist()
                    if views is not None and d["index"] in views and tf["rank"] in views[d["index"]]:
                        views[d["index"]][tf["rank"]] = (views[d["index"]][tf["rank"]][0], np.asarray(tf["H"], np.float64))
        for src in (d.get("layout") or {}, d.get("closeup") or {}):
            for pt in src.get("ports") or []:
                if pt.get("rack_polygon"):
                    pt["rack_polygon"] = poly_f(pt["rack_polygon"])
    r["ladder"]["aligned"] = {"devices": len(keep), "max_move_px": round(float(max(abs(o - tg) for o, tg in keep)), 1)}
    return out


def outlines_from_crops_run(run_dir):
    """outlines_from_crops on a stored run: the best frames re-read from the video, the device pictures redrawn, the
    cached crops dropped."""
    run_dir = pathlib.Path(run_dir); rf = run_dir / "result.json"; r = json.loads(rf.read_text()); st = json.loads((run_dir / "status.json").read_text())
    video = st.get("video_file") or str(run_dir / "video.mp4"); devs = r["devices"]
    want = {t["frame"] for dv in devs for t in (dv.get("top_frames") or []) if t.get("H") is not None}
    imgs = {}; cap = cv2.VideoCapture(video); n = 0
    while want - set(imgs):
        if not cap.grab():
            break
        if n in want:
            ok, fr = cap.retrieve()
            if ok:
                imgs[n] = fr
        n += 1
    cap.release()
    views = {dv["index"]: {t["rank"]: (imgs[t["frame"]], np.asarray(t["H"], np.float64)) for t in dv.get("top_frames") or [] if t["frame"] in imgs and t.get("H") is not None} for dv in devs}
    done = outlines_from_crops(devs, views, r["ladder"].get("pitch_px"))
    snap_outlines_to_units(devs, r["occupancy_frames"]["rows"]); r["occupancy_frames"]["summary"] = occ.summarize(r["occupancy_frames"]["rows"])
    rack_bgr = cv2.imread(str(run_dir / "rack.png")); rack_pil = Image.fromarray(cv2.cvtColor(rack_bgr, cv2.COLOR_BGR2RGB))
    draw_polygons(rack_pil, r["occupancy_frames"]["rows"], devs).save(run_dir / "rack_devices.jpg", quality=85)
    for f in run_dir.glob("section_D*_clean*.jpg"):
        f.unlink()
    rf.write_text(json.dumps(r, indent=1))
    return done


def refine_outlines(devices, views, n_frames=3):
    """A device whose outline came from the video frames gets the device detector's box in its best frames, carried onto
    the stitched rack picture through each frame's homography, the median of up to n_frames (user, 2026-10-29: D2's
    outline cut off its top and took in the label of the switch below). Kept only when it agrees with the old outline
    (IoU >= 0.5, height 0.7 to 1.5 of it) and overlaps no neighbour by more than a quarter of its height.
    views = {index: {rank: (frame_bgr, H frame->rack)}}. Returns the indexes changed."""
    import device_model
    def iou(a, b):
        ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1])); i = ix * iy
        u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i
        return i / u if u > 0 else 0.0
    bb = lambda q: [min(p[0] for p in q), min(p[1] for p in q), max(p[0] for p in q), max(p[1] for p in q)]
    changed = []
    for dv in devices:
        if dv.get("polygon_from") != "frames" or not dv.get("polygon"):
            continue
        quads = []
        for rank in sorted((views.get(dv["index"]) or {}))[:n_frames]:
            fr, H = views[dv["index"]][rank]
            H = np.asarray(H, np.float64)
            q = cv2.perspectiveTransform(np.array([dv["polygon"]], np.float64), np.linalg.inv(H))[0]
            exp = [q[:, 0].min(), q[:, 1].min(), q[:, 0].max(), q[:, 1].max()]; eh = exp[3] - exp[1]
            cands = [(iou(exp, b[:4]), b[:4]) for b in device_model.detect(fr[:, :, ::-1]) if (b[3] - b[1]) <= 1.15 * eh]
            best = max(cands, default=(0, None), key=lambda c: c[0])
            if best[0] < 0.3:
                continue
            x1, y1, x2, y2 = [float(v) for v in best[1]]
            quads.append(cv2.perspectiveTransform(np.array([[[x1, y1], [x2, y1], [x2, y2], [x1, y2]]], np.float64), H)[0])
        if not quads:
            continue
        quad = np.median(np.array(quads), axis=0)
        old, new = bb(dv["polygon"]), bb(quad.tolist())
        hr = (new[3] - new[1]) / max(1e-6, old[3] - old[1])
        if iou(old, new) < 0.5 or not 0.7 <= hr <= 1.5:
            continue
        h = new[3] - new[1]
        if any(max(0, min(new[3], o[3]) - max(new[1], o[1])) > 0.25 * h for o in (bb(x["polygon"]) for x in devices if x is not dv and x.get("polygon"))):
            continue
        dv["polygon_old"] = dv["polygon"]; dv["polygon"] = [[round(float(x), 1), round(float(y), 1)] for x, y in quad]
        dv["polygon_refined"] = f"device detector, {len(quads)} frames"; changed.append(dv["index"])
    return changed


def clean_crop(dv, video, rack_path, out, devices=None, nth=0):
    """The device's crop with nothing drawn on it: CROP_PAD of its height around its box in the first frame
    (_crop_boxes) that holds the whole box; the stitched picture's cut when no frame does or the video cannot be read.
    devices = all the run's devices, for a device placed against its neighbour. nth > 0: the nth later frame that holds
    the box (the device's other views, no outline drawn); False when there is no such frame."""
    fr, box = None, None
    cands = [(f, b) for f, b, _H in _crop_boxes(dv, devices) if b[2] > b[0] and b[3] > b[1]]
    skip = nth
    if cands and video and pathlib.Path(video).exists():
        cap = cv2.VideoCapture(str(video)); k = 0; got = {}
        for f in sorted({f for f, _ in cands}):                     # sequential: the upright copy (FFV1) does not seek exactly
            while k < f and cap.grab():
                k += 1
            ok, img = cap.read(); k += 1
            if ok:
                got[f] = img
        cap.release()
        for f, b in cands:
            img = got.get(f)
            if img is None:
                continue
            h, w = img.shape[:2]; area = (b[2] - b[0]) * (b[3] - b[1])
            if max(0, min(w, b[2]) - max(0, b[0])) * max(0, min(h, b[3]) - max(0, b[1])) / area >= CROP_INSIDE:
                if skip:
                    skip -= 1; continue
                fr, box = img, b
                break
    if fr is None and nth:
        return False
    if fr is None:
        fr = cv2.imread(str(rack_path)); box = [min(p[0] for p in dv["polygon"]), min(p[1] for p in dv["polygon"]), max(p[0] for p in dv["polygon"]), max(p[1] for p in dv["polygon"])]
    elif "snapped to its units" not in str(dv.get("polygon_refined") or ""):
        try:                                                        # the box snaps to the device detector's box in that frame: the back-projected one
            import device_model                                     # can sit a little low and clip the device's top (D2 of the server room, 2026-10-29)
            def iou(a, b):
                ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1])); inter = ix * iy
                u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
                return inter / u if u > 0 else 0.0
            eh = box[3] - box[1]
            best = max(((iou(box, b[:4]), [float(v) for v in b[:4]]) for b in device_model.detect(fr[:, :, ::-1]) if (b[3] - b[1]) <= 1.15 * eh), default=(0, None))
            if best[0] >= 0.3:
                box = best[1]
        except Exception:
            pass
    pad = CROP_PAD * (box[3] - box[1]); h, w = fr.shape[:2]
    x1, y1, x2, y2 = int(max(0, box[0] - pad)), int(max(0, box[1] - pad)), int(min(w, box[2] + pad)), int(min(h, box[3] + pad))
    cv2.imwrite(str(out), fr[y1:y2, x1:x2], [cv2.IMWRITE_JPEG_QUALITY, 88])
    return True


def vote_seams(rows, fa, rack_rows, rack_seams):
    """Every seam between two occupied fused units, voted by the frames that see both units as neighbours."""
    taken = [r["status"] != "unoccupied" for r in rows]
    by_below = {s["below"]: s for s in rack_seams}
    seams, new_dev = [], {}
    for j in range(1, len(rows)):
        if not (taken[j - 1] and taken[j]):
            continue
        a, b = rows[j - 1]["box"], rows[j]["box"]
        vb = vs = 0.0; n = nm = c_new = 0
        who = {"model_new": [], "model_same": [], "logic_new": [], "logic_same": []}
        for f in fa:
            ua, ub = unit_over(f, a[1], a[3]), unit_over(f, b[1], b[3])
            if ua is None or ub is None or ub["k"] != ua["k"] + 1 or ub["seam_p"] is None:
                continue
            w = f["quality"] * (1.0 if ub["seam_by"] == "model" else 0.7)
            vb += w * ub["seam_p"]; vs += w * (1 - ub["seam_p"]); n += 1; nm += ub["seam_by"] == "model"; c_new += ub["seam_p"] >= 0.5
            who[("model" if ub["seam_by"] == "model" else "logic") + ("_new" if ub["seam_p"] >= 0.5 else "_same")].append(f["index"])
        rs = by_below.get(rows[j]["rack_unit"]) if rows[j]["rack_unit"] else None
        rack_p = None if rs is None else rs["p_new_device"]
        seen = sorted(sum(who.values(), []))
        parts = []
        if who["model_new"]: parts.append(f"two different detector boxes in {len(who['model_new'])} ({lst(who['model_new'])}), which means a new device")
        if who["model_same"]: parts.append(f"one detector box over both units in {len(who['model_same'])} ({lst(who['model_same'])}), which means the same device")
        if who["logic_new"]: parts.append(f"no box owning both, and the logic read a new device in {len(who['logic_new'])} ({lst(who['logic_new'])})")
        if who["logic_same"]: parts.append(f"no box owning both, and the logic read the same device in {len(who['logic_same'])} ({lst(who['logic_same'])})")
        if n >= VOTE_MIN:
            p, by = vb / max(1e-9, vb + vs), f"{n} frames ({nm} model, {n - nm} logic)"
            reason = (f"{n} frames saw both units ({lst(seen)}): " + "; ".join(parts) + ". Weighted by frame quality, with the boxes counting more than the logic, that is "
                      f"{p:.0%} for a new device, so: {'a new device starts here' if p >= 0.5 else 'the same device continues'}.")
        elif rack_p is not None:
            p, by = rack_p, f"rack image ({rs['decided_by']})"
            reason = (f"{'Only ' + str(n) + ' frame saw both units (' + lst(seen) + ')' if n else 'No frame saw both units'}; fewer than {VOTE_MIN}, so the rack image's own decision stays: "
                      f"{'a new device' if rack_p >= 0.5 else 'the same device'} ({rack_p:.0%} new, by its {rs['decided_by']}).")
        else:
            p, by = 1.0, "no vote"
            reason = "No frame and no rack seam here; counted as a new device."
        if rs is None:
            why_image = "No seam here on the rack image alone: one of the two units was free there, or its ladder had no unit here."
        elif rs["decided_by"] == "model":
            why_image = (f"{'New device' if rack_p >= 0.5 else 'Same device'} on its units {rs['above'].upper()}/{rs['below'].upper()}: "
                         f"{'two different detector boxes' if rack_p >= 0.5 else 'one detector box over both units'} on the stitched image.")
        else:
            why_image = (f"{'New device' if rack_p >= 0.5 else 'Same device'} on its units {rs['above'].upper()}/{rs['below'].upper()}: no box owned both units, "
                         f"so the logic read the pixels between them ({rack_p:.0%} new).")
        new_dev[j] = p >= 0.5
        seams.append({"above": rows[j - 1]["unit"], "below": rows[j]["unit"], "p_new_device": round(float(p), 3), "new_device": bool(p >= 0.5),
                      "decided_by": by, "reason": reason, "why_image": why_image, "frames_seen": seen, "frames_who": {k: sorted(v) for k, v in who.items()}, "frames_voting": n, "rack_p_new_device": None if rack_p is None else round(float(rack_p), 3),
                      "rack_decided_by": None if rs is None else rs["decided_by"],
                      "changed": rack_p is not None and (p >= 0.5) != (rack_p >= 0.5)})
    return seams, new_dev, taken


def type_votes(dbox, fa, rack_types):
    """The type of a device box on the rack, voted over the frames' typed finder boxes (as polygons) and the rack image's."""
    cands = [(1.0, d["class_name"], float(d["confidence"]), [float(v) for v in d["box"]]) for d in rack_types]
    for f in fa:
        cands += [(f["quality"], t["class_name"], t["confidence"], poly_span(t["poly"])) for t in f["an"]["types"]]
    votes, n = {}, 0
    for w, cname, conf, b in cands:
        vy = max(0.0, min(dbox[3], b[3]) - max(dbox[1], b[1])) / max(1.0, dbox[3] - dbox[1])
        vx = max(0.0, min(dbox[2], b[2]) - max(dbox[0], b[0])) / max(1.0, dbox[2] - dbox[0])
        if vy >= 0.6 and vx >= 0.5:
            votes[cname] = votes.get(cname, 0.0) + w * conf; n += 1
    if not votes or (fa and n < VOTE_MIN):              # with frames voting, one typed box is not enough to name a type
        return None, 0.0, n
    best = max(votes, key=votes.get)
    return best, votes[best] / sum(votes.values()), n


TOP_FRAMES = 5             # the best frames kept for every device (sharp, well matched, the whole polygon inside)


# ------------------------------------------------------------------ device classification (the last step)
# The classifier itself is called through pipeline.classify_crops (one swap point for photos and videos).
CLASSIFIER_NAME = P.CLASSIFIER_NAME
classify_crops = P.classify_crops
classify_views = P.classify_views


CLS_FRAMES = TOP_FRAMES    # the device's best frames the classifier looks at (all 5)
FUSE = True                # one answer from all 5 frames together (their features averaged, weighted by the frame locator's
                           # sharpness score), instead of the best-ranked frame's answer; each frame's own answer is kept too


def classify_devices(devices, views, rack_pil):
    """Every device is classified on each of its top 5 best frames, cut along its polygon as in training, and (FUSE) once
    on all of them together: one answer from their fused features. pick_answer keeps the fused answer, else the
    best-ranked frame's. The stitched picture alone is classified once, for comparison."""
    from device_classifier import crop_polygon
    jobs = []
    for dv in devices:
        for tf in sorted(dv["top_frames"], key=lambda t: t["rank"])[:CLS_FRAMES]:
            fr, H = views[dv["index"]][tf["rank"]]
            jobs.append((dv, tf, crop_polygon(Image.fromarray(fr[:, :, ::-1]), back_project_poly(H, dv["polygon"]))))
        jobs.append((dv, None, crop_polygon(rack_pil, [tuple(q) for q in dv["polygon"]])))
    res = classify_crops([c for _, _, c in jobs]) if jobs else []
    for dv in devices:
        dv["classification"] = {"model": CLASSIFIER_NAME, "label": None, "frames": [], "rack": None}
    for (dv, tf, _), r in zip(jobs, res):
        ans = {"label": r["label"], "class": r["class"], "prob": round(r["prob"], 3)}
        if tf is None:
            dv["classification"]["rack"] = ans
        else:
            dv["classification"]["frames"].append(dict(ans, frame=tf["frame"], rank=tf["rank"], file=tf.get("file")))
    if FUSE:
        by_dev = {}
        for dv, tf, crop in jobs:
            if tf is not None:
                by_dev.setdefault(dv["index"], []).append((crop, float(tf.get("score") or 1.0)))
        for dv in devices:
            v = by_dev.get(dv["index"])
            if v:
                r = classify_views([c for c, _ in v], [w for _, w in v])
                dv["classification"]["fused"] = {"label": r["label"], "class": r["class"], "prob": round(r["prob"], 3), "views": r["views"],
                                                 "other_reason": r.get("other_reason")}
    for dv in devices:
        pick_answer(dv["classification"])
    second_check(devices, {dv["index"]: next((c for d_, tf, c in jobs if d_ is dv and tf is not None), None) for dv in devices})
    return devices


def second_check(devices, crops):
    """Model 2 (device_second: the DINOv2-B detector) on each device's best crop; by agreement.json ('both agree') a device
    the two models name differently is left as Other for a person (user, 2026-10-27: 'use model 1 + model 2')."""
    import device_second as DS
    m = DS.second_opinion()
    if m is None:
        return
    try:                                                     # the agreement study's own cutoff: below it model 2 barely found a device, and its
        cut = float(json.load(open(pathlib.Path(__file__).resolve().parent.parent / "models" / "device_cls" / "agreement.json"))["model2_cut"])
    except Exception:                                        # "disagreement" is no evidence (user, 2026-10-30: clear devices shown Not sure)
        cut = 0.5
    for dv in devices:
        c = crops.get(dv["index"]); cl = dv.get("classification") or {}
        if c is None or not cl.get("label"):
            continue
        t2, p2 = m.top(c)
        cl["second"] = {"model": "dinov2_b", "class": t2, "prob": round(p2, 3)}
        if t2 and p2 >= cut and cl["label"] != "Other" and t2 != cl["label"] and not DS.confirmed(dv):
            cl["model1_label"] = cl["label"]; cl["label"] = "Other"; cl["other_reason"] = "the two models disagree"


LAYOUT_FRAMES = 3          # a switch's best frames the mapper reads for make, model and layout (was all 5: 'reading too much time', user 2026-10-27)
LAYOUTS = True             # OCR, agent, overlay and port fallback run on every video (user, 2026-10-23; before: off, it reads every
                           # switch's 5 frames with the OCR mapper, about 10 min per run). Turn on with --layouts or layouts=True.
MODEL_AGREE = 2            # a model counts only when this many of those frames read it (one blurred read fitted a wrong model)


def rack_crop_box(rack_hw, polygon):
    """The switch's crop of the stitched rack image: its polygon's box, 3 % wider and 60 % of its height above and below."""
    p = np.float32(polygon); (x1, y1), (x2, y2) = p.min(0), p.max(0); h, w = rack_hw
    dx, dy = 0.03 * (x2 - x1), 0.6 * (y2 - y1)
    return int(max(0, x1 - dx)), int(max(0, y1 - dy)), int(min(w, x2 + dx)), int(min(h, y2 + dy))


def rack_overlay(rack_bgr, polygon, ports, out_path, src_bgr=None, min_inliers=20):
    """The vendor layout laid over the switch's crop of the stitched rack image: every port's outline on the rack
    picture, its number, filled blue with a cable, green when empty. `ports` carry "rack_polygon" (a video read, carried
    through the frame's homography), or "photo_polygon" in `src_bgr` (a close-up), which is then matched to the rack
    crop (SIFT, ratio 0.75, RANSAC homography). Returns the ports' rack polygons, or None when the close-up does not
    match the rack crop."""
    X1, Y1, X2, Y2 = rack_crop_box(rack_bgr.shape[:2], polygon)
    crop = rack_bgr[Y1:Y2, X1:X2]
    if src_bgr is not None:
        up = 2.0                                            # the rack crop is small: match it enlarged
        g2 = cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
        g1 = cv2.cvtColor(src_bgr, cv2.COLOR_BGR2GRAY); s1 = min(1.0, 2.0 * g2.shape[1] / g1.shape[1])
        g1 = cv2.resize(g1, None, fx=s1, fy=s1, interpolation=cv2.INTER_AREA)
        sift = cv2.SIFT_create(8000); k1, d1 = sift.detectAndCompute(g1, None); k2, d2 = sift.detectAndCompute(g2, None)
        if d1 is None or d2 is None:
            return None
        good = [a for a, b in (m for m in cv2.BFMatcher().knnMatch(d1, d2, k=2) if len(m) == 2) if a.distance < 0.75 * b.distance]
        if len(good) < min_inliers:
            return None
        Hc, mask = cv2.findHomography(np.float32([k1[m.queryIdx].pt for m in good]), np.float32([k2[m.trainIdx].pt for m in good]), cv2.RANSAC, 4.0)
        if Hc is None or int(mask.sum()) < min_inliers:
            return None
        H = np.array([[1, 0, X1], [0, 1, Y1], [0, 0, 1]]) @ np.diag([1 / up, 1 / up, 1]) @ Hc @ np.diag([s1, s1, 1])
        for pt in ports:
            if pt.get("photo_polygon"):
                pt["rack_polygon"] = [[round(float(x), 1), round(float(y), 1)] for x, y in cv2.perspectiveTransform(np.array([pt["photo_polygon"]], np.float64), H)[0]]
    k = max(1.0, 1800.0 / max(1, X2 - X1))
    im = Image.fromarray(cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)[:, :, ::-1]); dr = ImageDraw.Draw(im, "RGBA")
    font = occ._font(max(10, int(11 * k / 1.5)))
    polys = []
    for pt in ports:
        q = pt.get("rack_polygon")
        if not q:
            continue
        xy = [((x - X1) * k, (y - Y1) * k) for x, y in q]; polys.append(q)
        st = pt.get("status")
        fill = (29, 95, 207, 120) if st == "connected" else (26, 127, 69, 90) if st == "empty" else (255, 255, 255, 40)
        dr.polygon(xy, fill=fill, outline=(255, 214, 0, 255), width=2)
        if pt.get("id") is not None:
            cx, cy = sum(x for x, _ in xy) / len(xy), sum(y for _, y in xy) / len(xy)
            dr.text((cx, cy), str(pt["id"]), fill="white", font=font, anchor="mm", stroke_width=2, stroke_fill="black")
    im.save(out_path, quality=88)
    return polys


CLOSEUP_MATCH = 25         # agreeing points a close-up needs with the rack image to be placed on it


def _photo_to_rack(src_bgr, rack_bgr, polygons):
    """A homography close-up -> rack image, from the whole photo matched to the rack around the switches (SIFT on
    the rack area enlarged 2x, ratio 0.75, MAGSAC), and its number of agreeing points; (None, n) when too few."""
    allp = np.vstack([np.float32(p) for p in polygons]); (x1, y1), (x2, y2) = allp.min(0), allp.max(0); hh = y2 - y1
    X1, Y1 = int(max(0, x1 - 50)), int(max(0, y1 - 0.5 * hh)); X2, Y2 = int(min(rack_bgr.shape[1], x2 + 50)), int(min(rack_bgr.shape[0], y2 + 0.5 * hh))
    up = 2.0
    g2 = cv2.resize(cv2.cvtColor(rack_bgr[Y1:Y2, X1:X2], cv2.COLOR_BGR2GRAY), None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
    g1 = cv2.cvtColor(src_bgr, cv2.COLOR_BGR2GRAY); s1 = min(1.0, 1600 / g1.shape[1]); g1 = cv2.resize(g1, None, fx=s1, fy=s1, interpolation=cv2.INTER_AREA)
    sift = cv2.SIFT_create(12000); k1, d1 = sift.detectAndCompute(g1, None); k2, d2 = sift.detectAndCompute(g2, None)
    if d1 is None or d2 is None:
        return None, 0
    good = [a_ for a_, b_ in (m for m in cv2.BFMatcher().knnMatch(d1, d2, k=2) if len(m) == 2) if a_.distance < 0.75 * b_.distance]
    if len(good) < 12:
        return None, 0
    H, mask = cv2.findHomography(np.float32([k1[m.queryIdx].pt for m in good]), np.float32([k2[m.trainIdx].pt for m in good]), cv2.USAC_MAGSAC, 6.0)
    n = int(mask.sum()) if mask is not None else 0
    if H is None or n < CLOSEUP_MATCH:
        return None, n
    return np.array([[1, 0, X1], [0, 1, Y1], [0, 0, 1]]) @ np.diag([1 / up, 1 / up, 1]) @ H @ np.diag([s1, s1, 1]), n


def closeup_layouts(img, rack_bgr, switches, work, uploaded_on=None, mapped=None):
    """Steps 5 and 6 on a close-up photo of a video run. A close-up shows one to three devices; each is found (the
    device detector) and given to the switch it is:
      1. by the rack image: the whole photo is matched to the rack (at least CLOSEUP_MATCH agreeing points) and a
         device belongs to the switch whose outline its centre falls in;
      2. else by its label: a unit number printed next to it (e.g. SP-R1-U13-SW01) names the switch at that unit;
      3. else, when the photo shows one device only, the switch it was uploaded on.
    Each placed device is read for its switch (make, model, vendor layout, ports) and its vendor layout is laid over
    the switch's crop of the rack image. switches = {index: {"polygon", "units"}}. Returns {index: layout}, each
    with "match" {by: rack image | label | upload, inliers, box, devices_in_photo, uploaded_on}. mapped = the indices sent
    to the port mappers (default: all of switches); any other device gets its cut only, for classification."""
    src = np.asarray(img.convert("RGB"))[:, :, ::-1].copy(); h, w = src.shape[:2]
    boxes = sorted([b for b in DM.detect(np.ascontiguousarray(src[:, :, ::-1])) if b[4] >= DM.SCORE], key=lambda b: -b[4]) if P.detector_available() else []
    H, n = _photo_to_rack(src, rack_bgr, [v["polygon"] for v in switches.values()])
    assign = {}
    for b in boxes:
        x1, y1, x2, y2 = [float(v) for v in b[:4]]; py = 0.25 * (y2 - y1)
        bx = (int(max(0, x1)), int(max(0, y1 - py)), int(min(w, x2)), int(min(h, y2 + py)))
        i, by = None, None
        if H is not None:
            c = cv2.perspectiveTransform(np.float32([[[(x1 + x2) / 2, (y1 + y2) / 2]]]), H)[0][0]
            i = next((k for k, v in switches.items() if cv2.pointPolygonTest(np.float32(v["polygon"]).reshape(-1, 1, 2), (float(c[0]), float(c[1])), False) >= 0), None)
            by = "rack image"
        else:
            sys.path.insert(0, str(P.SWITCH_AGENT)); import panel_ocr
            lab = src[int(max(0, y1 - py)):int(min(h, y2 + py)), int(max(0, x1 - 0.1 * (x2 - x1))):int(min(w, x2 + 0.1 * (x2 - x1)))]
            doc = panel_ocr.read_panel(lab, budget_s=10)
            units = [int(m) for d_ in doc["detections"] for m in re.findall(r"U[-\s]?(\d{1,2})(?!\d)", d_.get("text", "").upper())]
            i = next((k for k, v in switches.items() for u in units if f"u{u:02d}" in v["units"]), None)
            by = "label"
        if i is not None and i not in assign:
            assign[i] = (bx, by, (x1, y1, x2, y2))
    if not assign and len(boxes) <= 1 and uploaded_on in switches:
        b = boxes[0] if boxes else None
        assign[uploaded_on] = ((int(b[0]), int(b[1]), int(b[2]), int(b[3])) if b is not None else None, "upload", None)
    def room(raw):
        """How far a cut of this device may reach up and down: to the nearest other device box above and below."""
        x1, y1, x2, y2 = raw; top, bot = 0, h
        for b in boxes:
            bx1, by1, bx2, by2 = [float(v) for v in b[:4]]
            if min(x2, bx2) - max(x1, bx1) <= 0.3 * (x2 - x1):
                continue                                # beside it, not above or below
            ov = max(0.0, min(y2, by2) - max(y1, by1))
            if ov > 0.5 * min(y2 - y1, by2 - by1):
                continue                                # the same device (a second box on it)
            if by2 <= y1 + 0.2 * (y2 - y1):
                top = max(top, by2)
            elif by1 >= y2 - 0.2 * (y2 - y1):
                bot = min(bot, by1)
        return top, bot
    def clamp(raw, pad):
        x1, y1, x2, y2 = raw; top, bot = room(raw)
        return (int(max(0, x1)), int(max(top, y1 - pad)), int(min(w, x2)), int(min(bot, y2 + pad)))
    jobs, cut_only = [], []
    for i, (bx, by, raw) in assign.items():
        if raw:
            bx = clamp(raw, 0.25 * (raw[3] - raw[1]))
        cut, origin = P.switch_cut(img, bx) if bx else (img, (0, 0))
        dev_pic = None
        if raw:                                         # the device alone, tight, for classification (as the classifier was trained)
            pathlib.Path(work).mkdir(parents=True, exist_ok=True); dev_pic = pathlib.Path(work) / f"D{i}_device.jpg"
            img.crop((int(max(0, raw[0])), int(max(0, raw[1])), int(min(w, raw[2])), int(min(h, raw[3])))).save(dev_pic, quality=92)
        (jobs if (mapped is None or i in mapped) else cut_only).append({"key": f"D{i}", "image": cut, "origin": origin, "i": i, "bx": bx, "by": by, "raw": raw,
                                                                          "box": [raw[0] - origin[0], raw[1] - origin[1], raw[2] - origin[0], raw[3] - origin[1]] if raw else None,
                                                                          "kind": switches[i].get("kind") or "switch", "device": str(dev_pic) if dev_pic else None})
    def own_device(j, lay):
        """A fitted layout belongs to this device only when its ports sit inside the device's own detected box: the
        cut's margin, and the taller retry, reach the neighbour above or below, whose port row is then read instead."""
        if not j.get("raw") or lay.get("mode") not in ("template", "auto"):
            return True
        x1, y1, x2, y2 = j["raw"]; ys = [q[1] for p_ in lay.get("ports") or [] for q in (p_.get("photo_polygon") or [])]
        return not ys or (y1 - 0.1 * (y2 - y1) <= float(np.median(ys)) <= y2 + 0.1 * (y2 - y1))
    res = P.agent_layouts(jobs, work) if jobs else {}
    for j in jobs:
        if j["key"] in res and not own_device(j, res[j["key"]]):
            res[j["key"]] = dict(res[j["key"]], mode="neighbour", model=None, ports=[], n_ports=0, by_type={}, connected=0, fitted=False)
    retry = []                                          # no model read: the print can sit just outside the device's box
    for j in jobs:
        if j["bx"] and not (res.get(j["key"]) or {}).get("model"):
            x1, y1, x2, y2 = j["bx"]; py = y2 - y1
            bx = clamp(j["raw"], py) if j.get("raw") else (x1, int(max(0, y1 - py)), x2, int(min(h, y2 + py)))
            if bx == tuple(j["bx"]):
                continue                                # no room to grow: the neighbours are right against it
            cut, origin = P.switch_cut(img, bx)
            retry.append(dict(j, key=j["key"] + "_tall", image=cut, origin=origin, bx=bx))
    if retry:
        res2 = P.agent_layouts(retry, work)
        for j2 in retry:
            r2 = res2.get(j2["key"]) or {}
            if not r2.get("model"):
                continue
            k = next(n for n, j in enumerate(jobs) if j["i"] == j2["i"])
            if not own_device(jobs[k], r2):
                continue                                # the taller cut read the neighbour above or below: not this device
            jobs[k] = dict(j2, key=j2["key"]); res[j2["key"]] = r2
    out = {}
    pathlib.Path(work).mkdir(parents=True, exist_ok=True)
    for j in cut_only:                                  # not a mapper kind: the cut is kept so the close-up can classify the device
        f_ = pathlib.Path(work) / f"{j['key']}_cut.jpg"; j["image"].save(f_, quality=92)
        out[j["i"]] = {"ok": True, "fitted": False, "mode": "cut_only", "vendor": None, "model": None, "ports": [], "n_ports": 0, "by_type": {}, "connected": 0,
                       "pictures": {"cut": str(f_), "device": j["device"]}, "match": {"by": j["by"], "inliers": n if j["by"] == "rack image" else None, "box": list(j["bx"]) if j["bx"] else None,
                                                             "matched": True, "devices_in_photo": len(boxes), "uploaded_on": uploaded_on}}
    for j in jobs:
        i = j["i"]; lay = res.get(j["key"]) or {"ok": False, "error": "no result"}
        lay["fitted"] = lay.get("mode") == "template"; lay["auto"] = lay.get("mode") == "auto"
        if j.get("device"):
            lay.setdefault("pictures", {})["device"] = j["device"]
        lay["match"] = {"by": j["by"], "inliers": n if j["by"] == "rack image" else None, "box": list(j["bx"]) if j["bx"] else None,
                        "matched": True, "devices_in_photo": len(boxes), "uploaded_on": uploaded_on}
        P.add_port_details(lay, j["image"], work, j["key"])
        if lay["fitted"] or lay["auto"]:
            dst = pathlib.Path(work) / f"{j['key']}_rack_trace.jpg"; ports = lay.get("ports") or []
            if H is not None:
                for p in ports:
                    if p.get("photo_polygon"):
                        p["rack_polygon"] = [[round(float(a_), 1), round(float(b_), 1)] for a_, b_ in cv2.perspectiveTransform(np.array([p["photo_polygon"]], np.float64), H)[0]]
                ok = rack_overlay(rack_bgr, switches[i]["polygon"], ports, dst) is not None
            else:
                ok = rack_overlay(rack_bgr, switches[i]["polygon"], ports, dst, src_bgr=src) is not None
            if ok:
                lay.setdefault("pictures", {})["rack_trace"] = str(dst)
        out[i] = lay
    return out


def closeup_record(lay, run_dir, index):
    """A close-up's layout as kept with switch D<index> of a run: its pictures copied as closeup_D<index>_<name>.jpg
    (only the close-up and its port map unless a vendor layout fitted; never the overlay on the close-up itself)."""
    fitted = bool(lay.get("fitted")); auto = bool(lay.get("auto")); used = fitted or auto
    pics = {k: v for k, v in (lay.get("pictures") or {}).items() if (used and k not in ("trace", "overlay")) or k in ("cut", "portmap", "device")}
    for old in pathlib.Path(run_dir).glob(f"closeup_D{index}_*.jpg"):
        old.unlink()
    saved = {}
    for k, src in pics.items():
        dst = f"closeup_D{index}_{k}.jpg"; Image.open(src).convert("RGB").save(pathlib.Path(run_dir) / dst, quality=88); saved[k] = dst
    return {"vendor": lay.get("vendor"), "model": lay.get("model"), "fitted": fitted, "auto": auto, "status": lay.get("status"),
            "identify_confidence": lay.get("identify_confidence"), "ports": lay.get("ports") if used else [], "n_ports": lay.get("n_ports") if used else 0,
            "by_type": lay.get("by_type") if used else {}, "connected": lay.get("connected") if used else 0,
            "numbering": lay.get("numbering") if used else None, "match": lay.get("match"),
            "port_details": {k: v for k, v in (lay.get("port_details") or {}).items() if k != "picture"}, "spec": lay.get("spec"), "pictures": saved}


SOURCES = ("stitched rack crop", "top 5 frames", "uploaded crop")     # the order the make and model are taken in (user)


def identity(dv):
    """A switch's make and model, taken in the order 1. its crop of the stitched rack image, 2. its top 5 frames,
    3. an uploaded crop (close-up): the first source that reads the model gives make, model and layout; with no model
    anywhere, the first that reads the make gives the make. Returns {vendor, model, fitted, source, make_source,
    layout} where source / make_source are 1-3 (SOURCES) or None and layout is the layout to show."""
    L = dv.get("layout") or {}; C = dv.get("closeup") or {}; R = L.get("rack_read") or {}
    steps = [(1, R, L if L.get("source") == 1 else None), (2, L if L.get("source") == 2 else {}, L if L.get("source") == 2 else None),
             (3, C, C)]
    for n, got, lay in steps:
        if got.get("model"):
            return {"vendor": got.get("vendor"), "model": got["model"], "fitted": bool((lay or {}).get("fitted")), "source": n,
                    "make_source": n, "layout": lay}
    makes = [(1, R.get("vendor")), (2, L.get("vendor") if L.get("make_source") == 2 else None), (3, C.get("vendor"))]
    n, v = next(((n, v) for n, v in makes if v), (None, None))
    return {"vendor": v, "model": None, "fitted": False, "source": None, "make_source": n, "layout": None}


def model_key(m):
    """A model name reduced to letters and digits, upper case: the spelling the agents read varies ("TL-SG2428P", "SG2428P")."""
    return re.sub(r"[^A-Z0-9]", "", str(m).upper())


def same_model(names):
    """The distinct models among read names: a name whose key ends another's key is the same model (a dropped prefix),
    and the longest spelling is kept."""
    out = []
    for m in sorted(names, key=lambda m: -len(model_key(m))):
        k = model_key(m)
        if not any(model_key(o).endswith(k) or k.endswith(model_key(o)) for o in out):
            out.append(m)
    return set(out)


def switch_layouts(devices, views, out_dir, rack_bgr=None, progress=None, hints=None):
    """Steps 5 and 6 for every device classified as a Switch, the make and model taken in the order of SOURCES: the
    switch's crop of the stitched rack image is read first; when it gives no fitted catalogue model, its top 5 best
    frames are (a model needs MODEL_AGREE frames, the frame kept is the strongest fit); an uploaded crop comes third
    (closeup_layouts, identity). Each crop goes to the switch mapper (pipeline.switch_layouts), which reads the make and
    model and fits that model's vendor layout (template mode); with no template the agent's auto mode boxes the ports
    from the photo and numbers them left to right. Ports are carried onto the rack picture; the agent's pictures are
    saved as layout_D<i>_<name>.jpg. hints = {index: {"vendor", "model"}}: a make and model a person typed; the mapper
    then fits that model's layout to every crop instead of reading the panel (user, 2026-10-27)."""
    jobs = []; kinds_ = {}; hints = hints or {}
    rack_pil = Image.fromarray(rack_bgr[:, :, ::-1]) if rack_bgr is not None else None
    for dv in devices:
        kind = P.MAPPER_KIND.get((dv.get("kind_override") or {}).get("kind") or (dv.get("classification") or {}).get("label"))   # a corrected kind counts
        if not kind:
            continue
        kinds_[dv["index"]] = kind
        h = {k: hints[dv["index"]][k] for k in ("vendor", "model") if hints.get(dv["index"], {}).get(k)} if hints.get(dv["index"]) else {}
        if rack_pil is not None:                            # 1. the stitched rack crop: its ports are in rack coordinates already
            pg = np.float32(dv["polygon"]); (x1, y1), (x2, y2) = pg.min(0), pg.max(0)
            cut, origin = P.switch_cut(rack_pil, (float(x1), float(y1), float(x2), float(y2)))
            jobs.append({"key": f"D{dv['index']}_rack", "group": f"D{dv['index']}", "image": cut, "origin": origin, "device": dv, "kind": kind,
                         "frame": None, "rank": 0, "H": np.eye(3), "device_px": [round(float(x2 - x1)), round(float(y2 - y1))],
                         "box": [float(x1) - origin[0], float(y1) - origin[1], float(x2) - origin[0], float(y2) - origin[1]], **h})
        for tf in sorted(dv["top_frames"], key=lambda t: t["rank"])[:LAYOUT_FRAMES]:
            if tf["rank"] not in (views.get(dv["index"]) or {}):
                continue                                    # the frame is not at hand (an old run without stored homographies)
            fr, H = views[dv["index"]][tf["rank"]]; poly = back_project_poly(H, dv["polygon"])
            xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
            cut, origin = P.switch_cut(Image.fromarray(fr[:, :, ::-1]), (min(xs), min(ys), max(xs), max(ys)))
            dev_px = [round(max(xs) - min(xs)), round(max(ys) - min(ys))]         # the switch's own size in the frame
            jobs.append({"key": f"D{dv['index']}_r{tf['rank']}", "group": f"D{dv['index']}", "image": cut, "origin": origin, "device": dv, "kind": kind,
                         "frame": tf["frame"], "rank": tf["rank"], "H": H, "device_px": dev_px,
                         "box": [min(xs) - origin[0], min(ys) - origin[1], max(xs) - origin[0], max(ys) - origin[1]], **h})
    import shutil
    for dv in devices:                                      # an earlier reading of these devices
        for old in pathlib.Path(out_dir).glob(f"layout_D{dv['index']}_*.jpg"):
            old.unlink()
        for old in (pathlib.Path(out_dir) / "switch_layouts").glob(f"D{dv['index']}_*"):
            shutil.rmtree(old, ignore_errors=True) if old.is_dir() else old.unlink()
        ledger = pathlib.Path(out_dir) / "switch_layouts" / "_agree.txt"       # the batch's "two crops agree" ledger: this device's old lines go too
        if ledger.exists():
            ledger.write_text("".join(ln for ln in ledger.read_text().splitlines(keepends=True) if not ln.startswith(f"D{dv['index']}\t")))
    res = P.agent_layouts(jobs, pathlib.Path(out_dir) / "switch_layouts", progress=progress) if jobs else {}
    by_dev = {}
    for j in jobs:
        lay = dict(res.get(j["key"]) or {"ok": False, "error": "no result"}, frame=j["frame"], rank=j["rank"]); j["lay"] = lay
        by_dev.setdefault(j["device"]["index"], []).append(j)
    for dv in devices:
        js = by_dev.get(dv["index"])
        if not js:
            continue
        rk = next((j for j in js if j["rank"] == 0), None); js = [j for j in js if j["rank"] != 0] or ([rk] if rk else [])   # no frames at hand: the rack crop alone
        js_all = js
        rack_read = None
        if rk is not None:
            name = f"layout_D{dv['index']}_rackcrop.jpg"; rk["image"].save(pathlib.Path(out_dir) / name, quality=90)
            rack_read = {"vendor": rk["lay"].get("vendor"), "model": rk["lay"].get("model"), "fitted": rk["lay"].get("mode") == "template", "file": name}
        if rk is not None and rack_read["fitted"]:          # 1. the rack crop names a catalogue model and its layout fits
            js_all, js = js, [rk]
        fitted = [j for j in js if j["lay"].get("ok") and j["lay"].get("mode") == "template"]
        models_read = same_model({j["lay"]["model"] for j in js if j["lay"].get("model")})
        conflict = len(models_read) > 1                     # the views read different models: unsure, ask for a close-up
        n_read = sum(1 for j in js if j["lay"].get("model"))
        lone = next(iter(models_read)) if len(models_read) == 1 and n_read < MODEL_AGREE else None   # one frame alone read it
        if (js and js[0]["rank"] == 0) or hints.get(dv["index"]):   # the rule for frames does not apply to the rack crop, nor to a typed model
            conflict, lone = False, None
        if conflict or lone:
            fitted = []
        autos = [j for j in js if j["lay"].get("ok") and j["lay"].get("mode") == "auto" and j["lay"].get("ports")] if not (conflict or lone) else []
        if fitted:
            best = max(fitted, key=lambda j: (j["lay"].get("confidence") or 0, -j["rank"]))
        elif autos:                                         # no template: the agent's auto mode. Its port count swings between views
            counts = sorted(j["lay"].get("n_ports") or 0 for j in autos)          # of one device (16 to 30 on one switch in the sample
            median = counts[len(counts) // 2]                                      # run), so the view nearest the median count is kept,
            best = min(autos, key=lambda j: (abs((j["lay"].get("n_ports") or 0) - median), j["rank"]))   # the sharper one on a tie
        else:
            best = max(js, key=lambda j: (bool(j["lay"].get("model")), bool(j["lay"].get("vendor")), -j["rank"]))
        lay = best["lay"]
        if not fitted:                                      # no fit: the make most of the views read, when MODEL_AGREE of them agree
            makes = [j["lay"].get("vendor") for j in js if j["lay"].get("vendor")]
            top = max(set(makes), key=lambda v: (makes.count(v), -min(j["rank"] for j in js if j["lay"].get("vendor") == v))) if makes else None
            lay = dict(lay, vendor=top if top and makes.count(top) >= MODEL_AGREE else None)
        source = 1 if js and js[0]["rank"] == 0 else (2 if (fitted or (lay.get("model") and not (conflict or lone))) else None)
        if js and js[0]["rank"] == 0:
            js = js_all
        make_source = 1 if rack_read and rack_read["vendor"] else (2 if lay.get("vendor") else None)
        if make_source == 1 and source != 1:
            lay = dict(lay, vendor=rack_read["vendor"])
        reads = []
        for j in sorted(js, key=lambda j: j["rank"]):
            name = f"layout_D{dv['index']}_r{j['rank']}_view.jpg"; j["image"].save(pathlib.Path(out_dir) / name, quality=90)
            tf = next((t for t in dv["top_frames"] if t["rank"] == j["rank"]), {})
            reads.append({"frame": j["frame"], "rank": j["rank"], "vendor": j["lay"].get("vendor"), "model": j["lay"].get("model"),
                          "fitted": j["lay"].get("mode") == "template", "auto": j["lay"].get("mode") == "auto", "status": j["lay"].get("status"),
                          "identify_confidence": j["lay"].get("identify_confidence"), "confidence": j["lay"].get("confidence"), "file": name,
                          "cut_px": list(j["image"].size), "device_px": j.get("device_px"), "sharpness": tf.get("score")})
        pics = {}
        usable = lay.get("mode") in ("template", "auto") and not (conflict or lone)     # a template fitted, or the agent's auto mode
        for name, src in (lay.get("pictures") or {}).items():
            if name != "cut" and not usable:
                continue
            dst = f"layout_D{dv['index']}_{name}.jpg"
            im = Image.open(src).convert("RGB"); im.thumbnail((1800, 1800)); im.save(pathlib.Path(out_dir) / dst, quality=86)
            pics[name] = dst
        if usable:
            H = np.asarray(best["H"], dtype=np.float64)
            for p in lay["ports"]:
                if p.get("photo_polygon"):
                    p["rack_polygon"] = [[round(float(x), 1), round(float(y), 1)] for x, y in
                                         cv2.perspectiveTransform(np.array([p["photo_polygon"]], dtype=np.float64), H)[0]]
            if rack_bgr is not None:                        # the vendor layout on the switch's crop of the stitched rack image
                dst = f"layout_D{dv['index']}_rack_trace.jpg"
                rack_overlay(rack_bgr, dv["polygon"], lay["ports"], pathlib.Path(out_dir) / dst); pics["rack_trace"] = dst
        else:                                               # nothing mapped: no port layout
            lay = dict(lay, ports=[], n_ports=0, by_type={}, connected=0, numbering=None)
        dv["layout"] = dict(lay, kind=kinds_.get(dv["index"], "switch"), pictures=pics, reads=reads, fitted=lay.get("mode") == "template" and not conflict,
                            auto=lay.get("mode") == "auto" and usable,
                            conflict=sorted(models_read) if conflict else None, rack_read=rack_read, source=source, make_source=make_source)
        if lone:
            dv["layout"].update(model=None, mode="lone_read", lone_read=lone, fitted=False, ports=[], n_ports=0, by_type={}, connected=0,
                                numbering=None, pictures={k: v for k, v in pics.items() if k == "cut"})
        if conflict:
            dv["layout"].update(model=None, mode="conflict", ports=[], n_ports=0, by_type={}, connected=0, numbering=None,
                                pictures={k: v for k, v in pics.items() if k == "cut"})
        try:                                                # step 6 in detail: every port, empty or connected, its spec and cable
            import port_details as PD
            if PD.available():
                cut_bgr = cv2.cvtColor(np.asarray(best["image"]), cv2.COLOR_RGB2BGR)
                det = PD.port_details(cut_bgr, dv["layout"])
                try:                                        # the cables (and unknown states) voted over the device's other frames
                    def mapper(H, origin, inverse=False):
                        Hm = np.asarray(H, np.float64); Hm = np.linalg.inv(Hm) if inverse else Hm; ox, oy = origin
                        if inverse:
                            return lambda pts: [[a - ox, b - oy] for a, b in cv2.perspectiveTransform(np.array([pts], np.float64), Hm)[0].tolist()]
                        return lambda pts: cv2.perspectiveTransform(np.array([[[a + ox, b + oy] for a, b in pts]], np.float64), Hm)[0].tolist()
                    others = [j for j in jobs if j.get("device") is dv and j is not best and j.get("image") is not None and j.get("H") is not None]
                    others.sort(key=lambda j: j["rank"])
                    views_ = [(cv2.cvtColor(np.asarray(j["image"]), cv2.COLOR_RGB2BGR), mapper(j["H"], j["origin"], inverse=True)) for j in others]
                    det = PD.vote_frames(det, cut_bgr, mapper(best["H"], best["origin"]), views_)
                except Exception as ex:
                    det["frames_error"] = f"{type(ex).__name__}: {ex}"
                name = f"layout_D{dv['index']}_portmap.jpg"; cv2.imwrite(str(pathlib.Path(out_dir) / name), PD.draw(cut_bgr, det))
                dv["layout"]["port_details"] = dict(det, picture=name, frame=best["frame"])
        except Exception as ex:                             # never stop the run for the port details
            dv["layout"]["port_details"] = {"error": f"{type(ex).__name__}: {ex}"}
    return devices


def read_device_ports(out_dir, video, rack_bgr, index, progress=None, hint=None):
    """Steps 5 and 6 for ONE device of a finished run, when the user selects it: its top frames are read back from the
    video (frame numbers and homographies are in result.json), its crops go to the port agent, and result.json is
    updated in place. Returns the device. A device that is not a switch, router, firewall or gateway gets no layout."""
    out_dir = pathlib.Path(out_dir); rf = out_dir / "result.json"; result = json.load(open(rf))
    dv = next(d for d in result["devices"] if d["index"] == index)
    hint = hint or (dv.get("layout") or {}).get("typed")                 # a make and model a person typed stays the hint on every later read
    want = {tf["frame"] for tf in dv.get("top_frames") or [] if tf.get("H") is not None}
    imgs = {}
    if want:
        cap = cv2.VideoCapture(str(video))
        for n in range(max(want) + 1):
            if n in want:
                ok, fr = cap.read()
                if ok:
                    imgs[n] = fr
            else:
                cap.grab()
        cap.release()
    views = {index: {tf["rank"]: (imgs[tf["frame"]], np.asarray(tf["H"], np.float64)) for tf in dv.get("top_frames") or [] if tf["frame"] in imgs}}
    t = time.perf_counter()
    switch_layouts([dv], views, out_dir, rack_bgr, progress=progress, hints={index: hint} if hint else None)
    dv["layout_seconds"] = round(time.perf_counter() - t, 1)
    if hint and dv.get("layout") is not None:
        dv["layout"]["typed"] = hint
    result["devices"] = [dv if d["index"] == index else d for d in result["devices"]]
    result.setdefault("times_s", {})["layout"] = round(sum(d.get("layout_seconds") or 0 for d in result["devices"]), 1)
    json.dump(result, open(rf, "w"), indent=1)
    return dv


def best_device_frames(video, rack_bgr, devices, regs, by_idx, fr_by_idx, progress=None):
    """Each device's top 5 frames by the lead's frame locator (src/frame_locator.py): every frame of the video, not only
    the mapped ones, gated (the whole device inside, a sound match) and ranked by the sharpness of the device itself,
    at least 10 frames apart. A device the locator finds in no frame keeps its frames from the mapped frames
    (device_frame_scores). Our own maps of the nearest mapped frame (within 2 frames) only skip a device that lies far
    outside a frame. Returns {device index: {rank: (bgr, H frame -> rack)}}."""
    mapped = sorted((r["index"], r["H"]) for r in regs if r["H"] is not None)
    near = {}
    for idx, H in mapped:
        for k in range(idx - 2, idx + 3):
            if k not in near or abs(k - idx) < abs(k - near[k][0]):
                near[k] = (idx, H)
    found, imgs = FL.locate_devices(video, rack_bgr, {dv["index"]: dv["polygon"] for dv in devices},
                                    hint=lambda n: near[n][1] if n in near else None, progress=progress)
    views = {}
    for dv in devices:
        r = found.get(dv["index"]) or {"frames": [], "scanned": 0, "eligible": 0, "reasons": {}, "warnings": ["not searched"]}
        dv["frame_search"] = {k: r[k] for k in ("scanned", "eligible", "reasons", "warnings")}
        if r["frames"]:
            dv["top_frames"] = [dict({k: v for k, v in h.items() if k not in ("H", "polygon_px")}, H=np.asarray(h["H"], np.float64).round(6).tolist()) for h in r["frames"]]
            views[dv["index"]] = {h["rank"]: (imgs[h["frame"]], np.asarray(h["H"], np.float64)) for h in r["frames"]}
            dv["frame_search"]["by"] = "frame locator"
            line = (f"Best frames: {r['eligible']} of the video's {r['scanned']} frames show the whole device; the {len(r['frames'])} sharpest, "
                    f"at least {FL.Config().min_frame_gap} frames apart, are frames {', '.join(str(h['frame']) for h in r['frames'])}.")
        else:
            views[dv["index"]] = {tf["rank"]: (fr_by_idx[tf["frame"]], by_idx[tf["frame"]]["H"]) for tf in dv["top_frames"]}
            for tf in dv["top_frames"]:
                tf["H"] = np.asarray(by_idx[tf["frame"]]["H"], np.float64).round(6).tolist()
            dv["frame_search"]["by"] = "mapped frames"
            line = f"Best frames: no video frame shows the whole device, so its {len(dv['top_frames'])} best mapped frames are used."
        dv["reason"] = dv["reason"].split(" Best frame")[0] + " " + line
        dv["best_frame"] = dv["top_frames"][0]["frame"] if dv["top_frames"] else None
    return views


def pick_answer(c):
    """The fused answer of all the device's frames when there is one (FUSE); else the answer of the best-ranked frame,
    where a lower-ranked frame speaks only when every frame above it said Other."""
    if c.get("fused"):
        f = c["fused"]
        c.update(label=f["label"], **{"class": f["class"]}, prob=f["prob"], frame=None, rank=None, file=None)
        return c
    fr = sorted(c.get("frames") or [], key=lambda x: x["rank"])
    if fr:
        pick = next((x for x in fr if x["label"] != "Other"), fr[0])
        c.update(label=pick["label"], **{"class": pick["class"]}, prob=pick["prob"], frame=pick["frame"], rank=pick["rank"], file=pick.get("file"))
    return c


def clip_to_units(poly, dbox, n_units, tol=0.1):
    """The outline held inside the device's own unit rows (dbox, top to bottom), with tol of a unit of slack for the
    ladder line itself. The detector's boxes, averaged over frames, run 1.5 to 2.5 units tall on a 1U device (cables,
    bezels, blur), so neighbours overlapped by up to 80 % (user, 2026-10-27); the units are the measured truth."""
    y0, y1 = float(dbox[1]), float(dbox[3]); slack = tol * (y1 - y0) / max(1, n_units)
    return [[float(x), float(min(max(y, y0 - slack), y1 + slack))] for x, y in poly]


_TEXT_OCR = [None]
TEXT_FRAMES = 4            # frames read per free or not-sure unit
MAKE_FRAMES = 2            # frames that must read a make or model before it counts
TEXT_SCORE = 0.6           # the reader's score a word needs


def _known_makes():
    """Makes and model patterns from the catalogue: (lower-case names, compiled model patterns)."""
    import re as _re
    v = json.loads((pathlib.Path(__file__).resolve().parents[1] / "data" / "catalogues" / "vendors.json").read_text())
    names = {a_.lower() for x in v for a_ in [x["name"]] + list(x.get("aliases") or []) if len(a_) >= 4}
    pats = [_re.compile(q) for x in v for q in x.get("model_prefixes") or []]
    return names, pats


_MAKES = [None]


def is_make_or_model(word):
    """A word that names a known make, or looks like a known model (a known pattern, with a digit, 6 characters or more)."""
    import re as _re
    if _MAKES[0] is None:
        _MAKES[0] = _known_makes()
    low = word.lower(); names, pats = _MAKES[0]
    if any(_re.search(rf"(?<![a-z]){_re.escape(n)}(?![a-z])", low) for n in names):
        return True
    up = word.upper().strip()
    return len(up) >= 6 and any(c.isdigit() for c in up) and any(q.search(up) for q in pats)


def text_on_unit(row, frames_h):
    """What each frame can read on a unit: words lying flat, inside the unit's band and away from its ends. frames_h =
    [(frame, H frame -> rack)]. Returns {word: number of frames that read it}."""
    if _TEXT_OCR[0] is None:
        from rapidocr_onnxruntime import RapidOCR
        _TEXT_OCR[0] = RapidOCR()
    x0, y0, x1, y1 = row["box"]; words = {}
    for fr, H in frames_h:
        q = np.array(back_project_poly(H, [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]), np.float64); h_, w_ = fr.shape[:2]
        a, b = max(0, int(q[:, 0].min())), min(w_, int(q[:, 0].max())); c, d = max(0, int(q[:, 1].min())), min(h_, int(q[:, 1].max()))
        if d - c < 8 or b - a < 40:
            continue
        crop = cv2.resize(fr[c:d, a:b], None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC); here = set()
        try:
            got = (_TEXT_OCR[0](crop) or [None])[0] or []
        except Exception:  # noqa: BLE001    a crop the reader cannot size
            got = []
        for box, txt, sc in got:
            bx = np.array(box, np.float64); cw, ch = np.ptp(bx[:, 0]), np.ptp(bx[:, 1]); cx = bx[:, 0].mean() / crop.shape[1]; cy = bx[:, 1].mean() / crop.shape[0]
            if float(sc) >= TEXT_SCORE and sum(c_.isalpha() for c_ in txt) >= 3 and "RACK" not in txt.upper() and cw > 1.5 * ch and 0.05 < cx < 0.95 and 0.1 < cy < 0.9:   # the rack's own label is not on a unit
                here.add(txt.strip())
        for w in here:
            words[w] = words.get(w, 0) + 1
    return words


def read_units_text(rows, views):
    """Text, labels and make and model count at the unit step, checked across frames, never from one picture (user,
    2026-10-30): a unit called free or not sure where at least MAKE_FRAMES frames read a known make or model is occupied;
    a unit called free where any frame reads text stays "uncertain" for a person (a unit with a label holds something).
    views = {frame index: (frame, H)}. Returns [(unit, was, now, words)]."""
    changed = []
    for r in rows:
        if r.get("status") not in ("unoccupied", "uncertain") or r.get("decided_by") == "person":
            continue
        seen = [views[i] for i in (r.get("frames_seen") or []) if i in views]
        seen = seen[:: max(1, len(seen) // TEXT_FRAMES)][:TEXT_FRAMES]
        words = text_on_unit(r, seen) if seen else {}
        if not words:
            continue
        r["text_read"] = sorted(words); was = r["status"]
        makes = [w for w, n in words.items() if n >= MAKE_FRAMES and is_make_or_model(w)]
        if makes:
            r.update(status="occupied", decided_by=f"make or model read in {MAKE_FRAMES}+ frames", attention=None, reason=f'{", ".join(makes[:3])} read on this unit in several frames, so a device sits here. ' + str(r.get("reason") or ""))
        elif was == "unoccupied":
            r.update(status="uncertain", decided_by="text seen", attention=None, reason=f'Called free by the frames, but text can be read on it: {", ".join(sorted(words)[:4])}. Left for a person. ' + str(r.get("reason") or ""))
        if r["status"] != was:
            changed.append((r["unit"], was, r["status"], sorted(words)))
    return changed


def devices_from(rows, new_dev, taken, fa, rack_types, rack_devices, regs_used, hw, seams, imgs=None):
    by_below = {s["below"]: s for s in seams}
    def edge(seam, side, k_other):
        """Why the device ends here: a seam that voted "new device", a free unit, or the end of the ladder."""
        if seam is not None:
            return (f"{side}: a new device starts ({seam['frames_voting']} frames, {seam['p_new_device']:.0%} new)."
                    if seam["frames_voting"] >= VOTE_MIN else f"{side}: a new device starts by the rack image ({seam['p_new_device']:.0%} new).")
        if k_other is None:
            return f"{side}: the ladder ends."
        return f"{side}: the unit {'above' if side == 'Top' else 'below'} is free."
    out = []
    # a group whose units are all uncertain is no device: those units stay "uncertain", for a person to check (user, 2026-10-15)
    def seen_something(d):
        """An all-uncertain group is still a device to check when anything saw a device there: a possible-device box on
        the rack picture, a detector box in a frame, or the device finder's sighting (user 2026-10-27: no device may be missed)."""
        return any(rows[k].get("candidate_box") or rows[k].get("frames_device") or rows[k].get("frames_typed") for k in d)
    groups = [d for d in DJ.cut(taken, new_dev) if not all(rows[k]["status"] == "uncertain" for k in d) or seen_something(d)]
    for i, d in enumerate(groups, 1):
        top, bot = rows[d[0]], rows[d[-1]]
        dbox = [int(min(rows[k]["box"][0] for k in d)), int(top["box"][1]), int(max(rows[k]["box"][2] for k in d)), int(bot["box"][3])]
        ty, tconf, tn = type_votes(dbox, fa, rack_types)
        unsure = all(rows[k]["status"] == "uncertain" for k in d)   # every unit left to a person: no type is asserted
        hint, ty = (ty if unsure else None), (None if unsure else ty)
        rack_ty, rack_tconf, _ = type_votes(dbox, [], rack_types)
        rack_same = next((rd for rd in rack_devices if rd["box"][1] - 0.3 * (dbox[3] - dbox[1]) <= dbox[1] and dbox[3] <= rd["box"][3] + 0.3 * (dbox[3] - dbox[1])
                          and abs((rd["box"][3] - rd["box"][1]) - (dbox[3] - dbox[1])) < 0.3 * (dbox[3] - dbox[1])), None)
        poly, pfrom, shaped = device_polygon(d, rows, fa, dbox); npoly = len(shaped)
        poly = clip_to_units(poly, dbox, len(d))        # a device never reaches past its own units; the frame boxes include cables and bezels
        scored = device_frame_scores([f for f in regs_used if inside_frame(f, poly, hw)], poly, imgs)[:TOP_FRAMES]
        ranked = [f for _, f, _ in scored]; why_rank = {f["index"]: (sc, parts) for sc, f, parts in scored}
        best_f = ranked[0] if ranked else None
        over = [rd for rd in rack_devices if min(rd["box"][3], dbox[3]) - max(rd["box"][1], dbox[1]) > 0.5 * min(rd["box"][3] - rd["box"][1], dbox[3] - dbox[1])]
        def by_text(rd):
            b = rd.get("decided_by") or ""
            return "its seams by the detector's boxes" if b == "model" else "its seams by the logic" if b == "logic" else "the only occupied unit in its run"
        why_image = ("The rack image alone drew " + " and ".join(f"{rd['label']} over its units {rd['top'].upper()}{'–' + rd['bottom'].upper() if rd['height'] > 1 else ''} ({by_text(rd)})" for rd in over) + " here."
                     if over else "The rack image alone drew no device here: its units there were free, or its ladder had no unit here.")
        inner = [by_below[rows[k]["unit"]] for k in d[1:] if rows[k]["unit"] in by_below]
        why = []
        if len(d) > 1:
            why.append(f"{len(d)} units stay together: " + "; ".join(
                f"{sm['above'].upper()}/{sm['below'].upper()} the same device in {sm['frames_voting']} frames" if sm["frames_voting"] >= VOTE_MIN
                else f"{sm['above'].upper()}/{sm['below'].upper()} the same device by the rack image" for sm in inner) + ".")
        why.append(edge(by_below.get(top["unit"]), "Top", d[0] - 1 if d[0] > 0 else None))
        why.append(edge(by_below.get(rows[d[-1] + 1]["unit"]) if d[-1] + 1 < len(rows) else None, "Bottom", d[-1] + 1 if d[-1] + 1 < len(rows) else None))
        if unsure:
            why.append("Every unit of this device is uncertain: a person should check it.")
        why.append(f"Shape: the detector's box in {npoly} frames ({lst(shaped)}), averaged corner by corner." if pfrom == "frames" else "Shape: its unit rows, no frame box owns it.")
        why.append(f"Best frames: {len(ranked)} of the mapped frames hold the whole polygon; ranked by sharpness and match, the best is frame {best_f['index']} (quality {best_f['quality']:.2f})." if best_f else "Best frame: none holds the whole polygon.")
        out.append({"index": i, "units": [rows[k]["unit"] for k in d], "top": top["unit"], "bottom": bot["unit"], "height": len(d), "box": dbox,
                    "polygon": [[round(x, 1), round(y, 1)] for x, y in poly], "polygon_from": pfrom, "reason": " ".join(why), "why_image": why_image, "frames_shape": shaped,
                    "top_frames": [dict({"rank": k + 1, "frame": f["index"], "quality": round(f["quality"], 2), "inliers": f["inliers"], "score": round(why_rank[f["index"]][0], 3)},
                                         **(why_rank[f["index"]][1] or {})) for k, f in enumerate(ranked)],
                    "type": ty, "type_hint": hint, "type_confidence": round(tconf, 3), "type_votes": tn, "rack_type": rack_ty, "rack_type_confidence": round(rack_tconf, 3),
                    "same_span_as_rack": rack_same is not None, "best_frame": best_f["index"] if best_f else None,
                    "uncertain": any(rows[k]["status"] == "uncertain" for k in d), "unsure": unsure,
                    "label": f"D{i} · {len(d)}U"})          # no device type is shown anywhere (user, 2026-10-15)
    return out


# ---------------------------------------------------------------- the experiment
def run(rack_bgr, video, approach, out_dir, progress=None, stride=STRIDE, truth_csv=None, stitch=None, layouts=LAYOUTS, on_phase1=None):
    out_dir = pathlib.Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    say = progress or (lambda m: None)
    T = {}
    t = time.perf_counter()
    # 0. both rails straight, from the rails' own holes or the device boxes' edges (user, 2026-10-30)
    rails = None
    try:
        say("rack image: the rails")
        _, r_info0 = P.units_from_photo(rack_bgr)
        pitch_r = float(r_info0["pitch"]) if r_info0.get("pitch") else rack_bgr.shape[0] / 42.0
        boxes_r = DM.detect(np.ascontiguousarray(rack_bgr[:, :, ::-1])) if P.detector_available() else []
        frames_r, _ = read_frames(video, stride); regs_r = register(frames_r, rack_bgr, say); fr_r = dict(frames_r)
        rails = RAILS.from_frames([(fr_r[g["index"]], g["H"]) for g in regs_r if g["H"] is not None], pitch_r, boxes_r, rack_bgr.shape[:2]) or RAILS.find_rails(rack_bgr, pitch_r, boxes_r)
        if rails:
            rack_bgr, rails["moved_px"] = RAILS.straighten(rack_bgr, rails); cv2.imwrite(str(out_dir / "rack.png"), rack_bgr)
            rails.pop("left"); rails.pop("right")
    except Exception as ex:  # noqa: BLE001
        rails = {"error": f"{type(ex).__name__}: {ex}"}
    # 1. the rack image alone, as in the photo pipeline
    say("rack image: units")
    r_units, r_info = P.units_from_photo(rack_bgr)
    rack_pil = Image.fromarray(cv2.cvtColor(rack_bgr, cv2.COLOR_BGR2RGB))
    say("rack image: occupancy")
    rack_types = occ.detect_devices(rack_pil) if occ.device_finder_available() else []
    rack_rows = P.occupancy_rows(rack_pil, r_units, approach, devices=rack_types)
    say("rack image: devices")
    rack_devices, rack_seams, how = P.device_rows(rack_bgr, rack_rows)
    # the detector's weaker boxes on the rack picture (CANDIDATE_SCORE to SCORE): a unit the classifiers call free under
    # such a box is "not sure, check" instead of free, and its device is kept for a person (user, 2026-10-27: no device may be missed)
    rack_candidates = ([b for b in DM.detect(np.ascontiguousarray(rack_bgr[:, :, ::-1])) if CANDIDATE_SCORE <= b[4]] if P.detector_available() else [])
    rack_boxes = [b for b in DM.detect(np.ascontiguousarray(rack_bgr[:, :, ::-1])) if b[4] >= DM.SCORE] if P.detector_available() else []
    r_by_unit = {r["unit"]: r for r in rack_rows}
    for rd in rack_devices:
        rty, rc, _ = type_votes(rd["box"], [], rack_types)
        rd["type"], rd["type_confidence"] = rty, round(rc, 3)
        rd["label"] = f"D{rd['index']} · {rd['height']}U"
        oa, ob = owner_by_height(r_by_unit[rd["top"]]["box"], rack_boxes), owner_by_height(r_by_unit[rd["bottom"]]["box"], rack_boxes)
        if oa >= 0 and oa == ob:
            x1, y1, x2, y2 = [float(v) for v in rack_boxes[oa][:4]]
            rd["polygon"], rd["polygon_from"] = [[round(x1, 1), round(y1, 1)], [round(x2, 1), round(y1, 1)], [round(x2, 1), round(y2, 1)], [round(x1, 1), round(y2, 1)]], "rack image"
        else:
            x1, y1, x2, y2 = rd["box"]
            rd["polygon"], rd["polygon_from"] = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]], "units"
    rack_occ = occ.summarize(rack_rows) if rack_rows else None
    rack_lines = sorted({r["box"][1] for r in rack_rows} | {r["box"][3] for r in rack_rows})
    T["rack"] = time.perf_counter() - t; t = time.perf_counter()
    # 2. frames, registered to the rack
    say("reading the video")
    frames, n_total = read_frames(video, stride)
    say(f"registering {len(frames)} frames")
    regs = register(frames, rack_bgr, say)
    by_idx = {r["index"]: r for r in regs}
    fr_by_idx = dict(frames)
    mapped = [r for r in regs if r["H"] is not None]
    T["register"] = time.perf_counter() - t; t = time.perf_counter()
    # 3. the best frames per band, each run as a photo
    H_rack = rack_bgr.shape[0]
    pitch0 = float(r_info["pitch"]) if r_info.get("pitch") else H_rack / 42.0
    top0, bot0 = (rack_lines[0] - pitch0, rack_lines[-1] + pitch0) if rack_lines else (0.0, float(H_rack))
    chosen = set()
    y = top0
    while y < bot0:
        for f in frames_for_band(y, y + pitch0, regs):
            chosen.add(f["index"])
        y += pitch0
    say(f"running the photo pipeline on {len(chosen)} frames")
    for n, idx in enumerate(sorted(chosen)):
        by_idx[idx]["an"] = analyse_frame(fr_by_idx[idx], by_idx[idx]["H"], approach)
        a = by_idx[idx]["an"]
        say(f"frame {idx}: {a['n_units'] if a else 0} units ({n + 1}/{len(chosen)})")
    fa = [by_idx[idx] for idx in sorted(chosen) if by_idx[idx].get("an")]
    T["frames"] = time.perf_counter() - t; t = time.perf_counter()
    # 4. fusion: ladder, occupancy, seams, devices
    say("fusing")
    lad = fuse_ladder(fa, rack_lines)
    if lad is None:
        raise ValueError("no frame gave a ladder")
    xl = float(np.median([u["x"][0] for f in fa for u in f["an"]["units"]])); xr = float(np.median([u["x"][1] for f in fa for u in f["an"]["units"]]))
    n_u = len(lad["lines"]) - 1
    fused = [{"unit": f"u{n_u - j:02d}", "box": (int(xl), int(lad["lines"][j]), int(xr), int(lad["lines"][j + 1])),
              "source": "detected" if lad["voters"][j] and lad["voters"][j + 1] else "inferred",
              "det_conf": min(1.0, min(lad["voters"][j], lad["voters"][j + 1]) / FRAMES_PER_BAND)} for j in range(n_u)]
    rows = vote_units(fused, fa, rack_rows, rack_candidates)
    rows, trimmed = trim_frame_rows(rows, lad["pitch"])     # the rack's frame above the top rail and below the bottom rail is no unit
    views_ = {g["index"]: (fr_by_idx[g["index"]], g["H"]) for g in mapped}
    tall_ = [b for b in rack_boxes if b[3] - b[1] < 8 * lad["pitch"]]
    rails_x = (float(np.median([b[0] for b in tall_])), float(np.median([b[2] for b in tall_]))) if len(tall_) >= 2 else None
    off_rail, base_top = RAILS.off_rail_rows(rack_bgr, rows, lad["pitch"], views_, rails_x)      # a unit counts only if most of it is on the rail, not on the base frame
    if rails and "error" not in rails:
        rails.update(base_top=base_top, off_rail=off_rail)
    if off_rail:
        rows = rows[:-off_rail]; trimmed["bottom"] += off_rail
        for i_, r_ in enumerate(rows):
            r_["unit"] = f"u{len(rows) - i_:02d}"
    say("reading text on the units")
    try:                                                     # text, labels and make and model, checked across frames
        text_changed = read_units_text(rows, {g["index"]: (fr_by_idx[g["index"]], g["H"]) for g in mapped})
    except Exception as ex:  # noqa: BLE001
        text_changed = [("error", f"{type(ex).__name__}: {ex}")]
    fused = [{k: r[k] for k in ("unit", "box", "source", "det_conf")} for r in rows]; n_u = len(rows)
    seams, new_dev, taken = vote_seams(rows, fa, rack_rows, rack_seams)
    regs_used = [by_idx[idx] for idx in sorted(chosen)]
    out_devices = devices_from(rows, new_dev, taken, fa, rack_types, rack_devices, mapped, frames[0][1].shape[:2], seams, fr_by_idx)
    T["fuse"] = time.perf_counter() - t; t = time.perf_counter()
    say("best frames per device (every frame of the video)")
    views = best_device_frames(video, rack_bgr, out_devices, mapped, by_idx, fr_by_idx, say)
    try:                                                    # the outlines from the device detector in each device's best frames
        refine_outlines(out_devices, views)
    except Exception:
        pass
    T["best_frames"] = time.perf_counter() - t; t = time.perf_counter()
    # 5. pictures
    say("drawing")
    pics = {}
    def save(im, name):
        im.save(out_dir / name, quality=85); return name
    pics["rack_units"] = save(P.draw_units(rack_bgr, fused, {"x_left": xl, "x_right": xr}), "rack_units.jpg")
    pics["rack_units_rackonly"] = save(P.draw_units(rack_bgr, r_units, r_info), "rack_units_rackonly.jpg")
    pics["rack_occupancy"] = save(P.draw_occupancy(rack_pil, rack_rows), "rack_occupancy.jpg")
    pics["rack_occupancy_frames"] = save(P.draw_occupancy(rack_pil, rows), "rack_occupancy_frames.jpg")
    pics["rack_devices_rackonly"] = save(draw_polygons(rack_pil, rack_rows, rack_devices), "rack_devices_rackonly.jpg")
    pics["rack_devices"] = save(draw_polygons(rack_pil, rows, out_devices), "rack_devices.jpg")
    im = rack_pil.copy(); d_ = ImageDraw.Draw(im, "RGBA"); n_poly = 0
    for i, f in enumerate(fa):
        col = P.PALETTE[i % len(P.PALETTE)]
        for b in f["an"]["boxes"]:
            d_.polygon(b["poly"], outline=col + (210,), width=3); n_poly += 1
    pics["rack_frame_boxes"] = save(im, "rack_frame_boxes.jpg")
    sections = []
    for dv in out_devices:
        for tf in dv["top_frames"]:
            fr, H = views[dv["index"]][tf["rank"]]
            poly = back_project_poly(H, dv["polygon"]); xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
            pad = 0.25 * (max(ys) - min(ys)); h, w = fr.shape[:2]
            x1, y1, x2, y2 = int(max(0, min(xs) - pad)), int(max(0, min(ys) - pad)), int(min(w, max(xs) + pad)), int(min(h, max(ys) + pad))
            crop = Image.fromarray(fr[y1:y2, x1:x2, ::-1].copy()); dc = ImageDraw.Draw(crop)
            dc.polygon([(p[0] - x1, p[1] - y1) for p in poly], outline=(29, 95, 207), width=3)
            name = f"section_D{dv['index']}.jpg" if tf["rank"] == 1 else f"section_D{dv['index']}_r{tf['rank']}.jpg"
            crop.save(out_dir / name, quality=88); tf["file"] = name
            if tf["rank"] == 1:
                sections.append({"device": dv["index"], "frame": tf["frame"], "file": name, "label": dv["label"]})
    say("classifying devices")
    t_c = time.perf_counter(); classify_devices(out_devices, views, rack_pil); T["classify"] = time.perf_counter() - t_c
    try:                                                    # how the video was recorded: what limits reading small print
        cap = cv2.VideoCapture(str(video)); fps = cap.get(cv2.CAP_PROP_FPS) or 0; nfr = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        vw, vh = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)); cap.release()
        secs = nfr / fps if fps else 0
        mids = sorted((r["index"], (r["y0"] + r["y1"]) / 2) for r in mapped if r.get("y0") is not None)
        steps_ = [abs(b[1] - a[1]) / (b[0] - a[0]) for a, b in zip(mids, mids[1:]) if b[0] > a[0]]
        capture = {"width": vw, "height": vh, "fps": round(fps, 1), "seconds": round(secs, 1),
                   "mbit_s": round(pathlib.Path(video).stat().st_size * 8 / secs / 1e6, 1) if secs else None,
                   "motion_px_per_frame": round(float(np.median(steps_)), 1) if steps_ else None}
    except Exception:
        capture = {}
    show = sorted(chosen)[:: max(1, len(chosen) // 8)][:8]
    projected = []
    for idx in show:
        f = by_idx[idx]; im = Image.fromarray(fr_by_idx[idx][:, :, ::-1].copy()); d_ = ImageDraw.Draw(im, "RGBA"); font = occ._font(26)
        for dv in out_devices:
            if not inside_frame(f, dv["polygon"], im.size[::-1], margin=-im.size[1] // 4):   # drawn when at least mostly in the picture
                continue
            col = P.PALETTE[(dv["index"] - 1) % len(P.PALETTE)]
            poly = back_project_poly(f["H"], dv["polygon"]); d_.polygon(poly, fill=col + (55,), outline=col + (230,), width=4)
            tx, ty_ = max(8, min(p[0] for p in poly) + 8), max(6, min(p[1] for p in poly) + 6)
            tw, th = d_.textbbox((0, 0), dv["label"], font=font)[2:]
            d_.rounded_rectangle([tx - 4, ty_ - 2, tx + tw + 6, ty_ + th + 4], 4, fill=col + (235,)); d_.text((tx, ty_), dv["label"], fill="white", font=font)
        im.thumbnail((1400, 1400)); name = f"frame_{idx:04d}.jpg"; im.save(out_dir / name, quality=84)
        projected.append({"frame": idx, "file": name, "rack_y": [round(f["y0"]), round(f["y1"])], "inliers": f["inliers"], "sharpness": round(f["sharpness"])})
    T["draw"] = time.perf_counter() - t
    heights = [r["box"][3] - r["box"][1] for r in rows]
    r_heights = [r["box"][3] - r["box"][1] for r in rack_rows]
    result = {
        "rack": {"units": len(rack_rows), "pitch_px": None if r_info.get("pitch") is None else round(float(r_info["pitch"]), 1), "occupancy": rack_occ,
                 "unit_height_px": [int(min(r_heights)), int(max(r_heights))] if r_heights else None,
                 "rows": [P.public_row(r) for r in rack_rows], "devices": rack_devices, "seams": rack_seams, "method": how},
        "capture": capture, "frame_locator": FL.settings(), "stitch": stitch,
        "ladder": {"rails": rails, "text_changed": text_changed, "units": n_u, "pitch_px": round(lad["pitch"], 1), "unit_height_px": [int(min(heights)), int(max(heights))],
                   "frames": len(fa), "votes": lad["votes"], "kept": lad["kept"], "merged": lad["merged"], "inserted": lad["inserted"], "trimmed": trimmed,
                   "detected": sum(r["source"] == "detected" for r in rows)},
        "occupancy_frames": {"rows": [dict(P.public_row(r), rack_unit=r["rack_unit"], rack_status=r["rack_status"], frames_voting=r["frames_voting"],
                                           frames_device=r["frames_device"], frames_empty=r["frames_empty"], changed=r["changed"], why_image=r["why_image"],
                                           frames_seen=r["frames_seen"], frames_box=r["frames_box"], frames_emptyslot=r["frames_emptyslot"]) for r in rows],
                             "summary": occ.summarize(rows) if rows else None, "changed": sum(r["changed"] for r in rows)},
        "video": {"frames_total": n_total, "frames_read": len(frames), "stride": stride, "frames_mapped": len(mapped),
                  "frames_used": sorted(chosen), "frame_px_per_rack_px": round(float(np.median([r["scale"] for r in mapped])), 2) if mapped else None,
                  "map": [{"frame": r["index"], "rack_y": [round(r["y0"]), round(r["y1"])], "inliers": r["inliers"], "sharpness": round(r["sharpness"]), "quality": round(r["quality"], 2)} for r in mapped]},
        "frames": [{"frame": f["index"], "quality": round(f["quality"], 2), "rack_y": [round(f["y0"]), round(f["y1"])], "units": f["an"]["n_units"],
                    "pitch_on_rack": round(f["an"]["pitch"], 1), "occupied": f["an"]["occupied"], "free": f["an"]["free"], "uncertain": f["an"]["uncertain"],
                    "boxes": len(f["an"]["boxes"])} for f in fa],
        "devices": out_devices, "seams": seams, "changed_seams": sum(s["changed"] for s in seams),
        "sections": sections, "projected": projected, "frame_polygons": n_poly, "pictures": pics, "times_s": {k: round(v, 1) for k, v in T.items()},
    }
    if truth_csv:
        truth = NT.load(truth_csv)
        result["truth"] = {"file": pathlib.Path(truth_csv).name, "devices": truth,
                           "frames": NT.score(truth, result["occupancy_frames"]["rows"], out_devices),
                           "rack": NT.score(truth, result["rack"]["rows"], rack_devices)}
    try:                                                     # every unit is the same size: tall rows split, wide gaps filled, close rows merged
        reg = regularise_result(result, out_dir)
        if any(reg.values()):
            say(f"units: {reg['split']} rows split, {reg['inserted']} inserted, {reg['merged']} merged (equal-size count rule)")
    except Exception as ex:  # noqa: BLE001
        result["ladder"]["regularise_error"] = f"{type(ex).__name__}: {ex}"
    try:                                                     # and the same height: the picture is corrected along y with the rows as the ruler
        fixed = rectify_result(result, rack_bgr, views)
        if fixed is not None:
            rack_bgr = fixed; cv2.imwrite(str(out_dir / "rack.png"), rack_bgr)
            rack_pil = Image.fromarray(cv2.cvtColor(rack_bgr, cv2.COLOR_BGR2RGB)); rows_ = result["occupancy_frames"]["rows"]
            fused = [{k: x[k] for k in ("unit", "box", "source", "det_conf")} for x in rows_]
            P.draw_units(rack_bgr.copy(), fused, {"x_left": rows_[0]["box"][0], "x_right": rows_[0]["box"][2]}).save(out_dir / "rack_units.jpg", quality=85)
            P.draw_occupancy(rack_pil, rows_).save(out_dir / "rack_occupancy_frames.jpg", quality=85)
            draw_polygons(rack_pil, rows_, out_devices).save(out_dir / "rack_devices.jpg", quality=85)
            say(f"units: picture corrected so every unit is {result['ladder']['pitch_px']} px tall")
    except Exception as ex:  # noqa: BLE001
        result["ladder"]["rectify_error"] = f"{type(ex).__name__}: {ex}"
    lev = None
    try:                                                     # the lean of the picture, from the device outlines
        lev = deshear_result(result, rack_bgr, views)
        if lev is not None:
            rack_bgr = lev; cv2.imwrite(str(out_dir / "rack.png"), rack_bgr)
            say(f"picture levelled: lean up to {result['ladder']['desheared']['max_lean_pct']} % taken out")
    except Exception as ex:  # noqa: BLE001
        result["ladder"]["deshear_error"] = f"{type(ex).__name__}: {ex}"
    try:                                                     # the overlay from the same box as each device's crop, neighbours never overlapping
        done_ = outlines_from_crops(out_devices, views, result["ladder"].get("pitch_px"))
        snap_outlines_to_units(out_devices, result["occupancy_frames"]["rows"])
        fr_ = {t_["frame"]: v_[t_["rank"]][0] for d_ in out_devices for t_ in d_.get("top_frames") or [] for v_ in [views.get(d_["index"]) or {}] if t_["rank"] in v_}
        al_ = align_to_devices(result, rack_bgr, fr_, views)          # the picture moved so each device sits on its units
        if al_ is not None:
            rack_bgr = al_; cv2.imwrite(str(out_dir / "rack.png"), rack_bgr)
            snap_outlines_to_units(out_devices, result["occupancy_frames"]["rows"])
        result["occupancy_frames"]["summary"] = occ.summarize(result["occupancy_frames"]["rows"])
        if True:
            rack_pil = Image.fromarray(cv2.cvtColor(rack_bgr, cv2.COLOR_BGR2RGB)); rows_ = result["occupancy_frames"]["rows"]
            fused = [{k: x[k] for k in ("unit", "box", "source", "det_conf")} for x in rows_]
            P.draw_units(rack_bgr.copy(), fused, {"x_left": rows_[0]["box"][0], "x_right": rows_[0]["box"][2]}).save(out_dir / "rack_units.jpg", quality=85)
            P.draw_occupancy(rack_pil, rows_).save(out_dir / "rack_occupancy_frames.jpg", quality=85)
            draw_polygons(rack_pil, rows_, out_devices).save(out_dir / "rack_devices.jpg", quality=85)
            say(f"devices: {len(done_)} outlines taken from their crop boxes")
    except Exception as ex:  # noqa: BLE001
        result["outline_error"] = f"{type(ex).__name__}: {ex}"
    json.dump(result, open(out_dir / "result.json", "w"), indent=1)
    if on_phase1:                                            # phase 1 is complete: units, occupancy, devices, kinds, pictures
        on_phase1(result)
    if layouts:                                              # phase 2: make and model, port layouts (the slow part)
        say("ports: reading every switch, router, firewall and gateway")
        t_c = time.perf_counter()
        try:
            switch_layouts(out_devices, views, out_dir, rack_bgr, progress=say)
        except Exception as ex:  # noqa: BLE001  (the phase-1 result is already shown: a port-phase failure must not fail the run)
            result["layout_error"] = f"{type(ex).__name__}: {ex}"
            say(f"ports: failed ({type(ex).__name__})")
        T["layout"] = time.perf_counter() - t_c
        result["times_s"] = {k: round(v, 1) for k, v in T.items()}
        if truth_csv:
            result["truth"]["frames"] = NT.score(truth, result["occupancy_frames"]["rows"], out_devices)
        json.dump(result, open(out_dir / "result.json", "w"), indent=1)
    return result


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--retrim":       # python3 src/rack_video.py --retrim data/runs/<id> ... [--top N] [--bottom N]: frame rows off an old run, its report rebuilt
        sys.path.insert(0, str(ROOT / "scripts" / "video")); import build_report
        args = sys.argv[2:]; top = int(args[args.index("--top") + 1]) if "--top" in args else 0; bottom = int(args[args.index("--bottom") + 1]) if "--bottom" in args else 0
        for run_dir in [a for a in args if a.startswith("data/") or "/" in a]:
            d = pathlib.Path(run_dir); st = json.loads((d / "status.json").read_text()) if (d / "status.json").exists() else {}
            video = next((p for p in d.glob("video*") if p.suffix in (".avi", ".mp4", ".mov", ".webm")), None)
            print(d.name, "trimmed", retrim(d, top, bottom))
            build_report.build_from(d, d / "report", d / "rack.png", video, st.get("title") or "Your rack", st.get("name") or d.name)
        return
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("video")
    ap.add_argument("--out", required=True); ap.add_argument("--stride", type=int, default=STRIDE)
    ap.add_argument("--layouts", action="store_true", help="also run device + port layout of the switches (about 10 min)")
    ap.add_argument("--approach", default="ensemble"); ap.add_argument("--truth", default=None, help="NetBox netbox_devices.csv of this rack")
    args = ap.parse_args()
    approach = {a.key: a for a in occ.build_approaches("v2")}[args.approach]
    import rack_stitch
    say = lambda m: print(m, file=sys.stderr)
    pathlib.Path(args.out).mkdir(parents=True, exist_ok=True)
    video, turned = rack_stitch.upright(args.video, args.out)          # a phone guide take lies on its side
    st = dict(rack_stitch.stitch(video, pathlib.Path(args.out) / "rack.png", progress=say), upright=turned)
    rack = cv2.imread(str(pathlib.Path(args.out) / "rack.png"))
    res = run(rack, video, approach, args.out, progress=say, stride=args.stride, truth_csv=args.truth, stitch=st, layouts=args.layouts)
    brief = {k: res[k] for k in ("ladder", "changed_seams", "times_s")}
    brief["rack"] = {k: res["rack"][k] for k in ("units", "unit_height_px", "occupancy")}
    brief["occupancy_frames"] = res["occupancy_frames"]["summary"]
    brief["devices"] = [(d["top"], d["bottom"], d["height"], d["type"]) for d in res["devices"]]
    brief["rack_devices"] = [(d["top"], d["bottom"], d["height"], d.get("type")) for d in res["rack"]["devices"]]
    if "truth" in res:
        brief["truth"] = {k: {m: res["truth"][k][m] for m in ("units", "seams", "devices")} for k in ("frames", "rack")}
    print(json.dumps(brief, indent=1))


if __name__ == "__main__":
    main()
