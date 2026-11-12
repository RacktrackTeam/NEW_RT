#!/usr/bin/env python3
"""Identify the rack units from a video or a picture of a rack: the same unit step RackTrack runs.

    python identify_units.py <video, picture or folder> [more] [--out out] [--stride 5] [--no-video] [--conf 0.25] [--device cpu]

A VIDEO (a slow sweep of the rack front, top to bottom) is first stitched into one picture of the whole rack, as the
app does; a video filmed sideways is turned upright first. Then the units are identified on that picture. You get:
    <name>_rack.png     the stitched rack picture
    <name>_units.jpg    the stitched picture with the ladder drawn: a white line at every unit boundary, the number on the left
    <name>_units.mp4    the video with the unit lines and numbers drawn on every 5th frame (--stride), the frames that could
                        be placed on the rack picture; a frame that could not be placed is left as it is
    <name>_units.json   the units top to bottom with their boxes, the rack box and the cross rails
and one line in the terminal: how many units, how many the model saw, how many the ladder rules added.

A PICTURE (a photo of the rack front, or a stitched picture) skips the stitching and gives the same jpg and json.

Units are numbered from the bottom: U1 is the lowest row. A black number is a row the model saw; an amber number is a
row the ladder rules added (a row missed between two others, or a row at an end where the rack frame carries on).
"""
import argparse
import json
import pathlib
import shutil
import sys
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]                                   # in the RackTrack checkout (tools/units_only/) the code and the model come from engine/ and models/
sys.path.insert(0, str(HERE))
if not (HERE / "unit_grid.py").exists():
    sys.path.insert(0, str(ROOT / "engine"))
import unit_grid as ug  # noqa: E402
import rack_stitch  # noqa: E402

WEIGHTS = HERE / "weights" / "rack_unit_finder.pt"
if not WEIGHTS.exists():
    WEIGHTS = ROOT / "models" / "weights" / "rack_unit_finder.pt"
PICTURES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
VIDEOS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".3gp"}
MIN_INLIERS = 40            # a frame is placed on the rack picture when its homography has this many inliers (as in the app)


# ---------------------------------------------------------------- the units of one picture
def identify(bgr, conf=0.25, device=None):
    """The units of one BGR picture, top to bottom: [{unit, box, source, det_conf}], the rack box and the rails."""
    model = ug.load_model(str(WEIGHTS))
    small, factor = ug.shrink_for_inference(bgr)
    kw = {"device": device} if device else {}
    res = model.predict(small, conf=conf, imgsz=768, verbose=False, **kw)
    if not res:
        return [], None, []
    raw, racks, rails = ug._unit_rows_from_result(res[0], getattr(model, "names", {}) or {})
    if factor != 1:
        for u in raw:
            u["box"] = [v * factor for v in u["box"]]
        racks = [[v * factor for v in r] for r in racks]
        rails = [[v * factor for v in r] for r in rails]
    if not raw:
        return [], (racks[0] if racks else None), rails
    seen = ug._drop_duplicate_units([dict(u) for u in raw])
    ladder = ug.ladder_from_unit_boxes(raw, racks, bgr.shape[0], bgr.shape[1], rails)
    out = []
    for r in ladder:
        y1, y2 = r["box"][1], r["box"][3]
        best, best_ov = None, 0.0
        for u in seen:
            ov = max(0.0, min(y2, u["box"][3]) - max(y1, u["box"][1])) / max(1.0, y2 - y1)
            if ov > best_ov:
                best, best_ov = u, ov
        detected = best is not None and best_ov >= 0.5
        out.append({"unit": r["label"].upper().replace("U0", "U"), "box": [int(round(v)) for v in r["box"]],
                    "source": "detected" if detected else "inferred", "det_conf": round(float(best["confidence"]), 3) if detected else None})
    rack = [int(round(v)) for v in racks[0]] if racks else None
    return out, rack, [[int(round(v)) for v in r] for r in rails]


def _font(size):
    for name in ("Helvetica.ttc", "Arial.ttf", "DejaVuSans-Bold.ttf", "arial.ttf"):
        for folder in ("/System/Library/Fonts", "/Library/Fonts", "/usr/share/fonts/truetype/dejavu", "C:/Windows/Fonts"):
            try:
                return ImageFont.truetype(str(pathlib.Path(folder) / name), size)
            except OSError:
                pass
    return ImageFont.load_default()


def draw(bgr, units):
    """The picture with the ladder: a white line at every unit boundary across the rack, the number on the left; a row
    the rules added (the model did not see it) gets an amber number."""
    im = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)).convert("RGB")
    if not units:
        return im
    d = ImageDraw.Draw(im, "RGBA")
    x0 = min(u["box"][0] for u in units); x1 = max(u["box"][2] for u in units)
    pitch = float(np.median([u["box"][3] - u["box"][1] for u in units]))
    lw = max(2, int(pitch * 0.06)); fs = max(12, int(pitch * 0.55)); f = _font(fs)
    for i, u in enumerate(units):
        y1, y2 = u["box"][1], u["box"][3]
        d.line([(x0, y1), (x1, y1)], fill=(255, 255, 255, 230), width=lw)
        if i == len(units) - 1:
            d.line([(x0, y2), (x1, y2)], fill=(255, 255, 255, 230), width=lw)
        label = u["unit"]; tw = d.textlength(label, font=f) + fs * 0.9; th = fs * 1.4
        cy = (y1 + y2) / 2; lx = x0 + lw * 2
        fill = (17, 22, 20, 215) if u["source"] == "detected" else (196, 110, 10, 225)
        d.rounded_rectangle([lx, cy - th / 2, lx + tw, cy + th / 2], radius=th / 2, fill=fill)
        d.text((lx + fs * 0.45, cy - fs * 0.62), label, fill=(255, 255, 255, 255), font=f)
    return im


# ---------------------------------------------------------------- the units drawn back on the video's frames
def overlay_video(video, rack_bgr, units, out_path, stride=5, progress=None):
    """Every stride-th frame placed on the rack picture (SIFT features, a homography, as the app does), the unit lines
    and numbers carried onto it. Returns (frames written, frames placed)."""
    sift = cv2.SIFT_create(nfeatures=8000)
    kr, dr = sift.detectAndCompute(cv2.cvtColor(rack_bgr, cv2.COLOR_BGR2GRAY), None)
    bf = cv2.BFMatcher(cv2.NORM_L2)
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0; W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n_all = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    vw = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), max(2.0, fps / stride), (W, H))
    x0 = min(u["box"][0] for u in units); x1 = max(u["box"][2] for u in units)
    pitch = float(np.median([u["box"][3] - u["box"][1] for u in units]))
    edges = [u["box"][1] for u in units] + [units[-1]["box"][3]]
    k = written = placed = 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if k % stride:
            k += 1; continue
        k += 1
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY); kf, df = sift.detectAndCompute(g, None); M = None
        if df is not None and len(kf) >= 12 and dr is not None:
            good = [a for a, b in bf.knnMatch(dr, df, k=2) if a.distance < 0.75 * b.distance]   # rack -> frame
            if len(good) >= 12:
                src = np.float32([kr[a.queryIdx].pt for a in good]); dst = np.float32([kf[a.trainIdx].pt for a in good])
                Hm, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
                if Hm is not None and mask is not None and int(mask.sum()) >= MIN_INLIERS:
                    M = Hm
        if M is not None:
            pts = np.float32([[[x0, y], [x1, y]] for y in edges]).reshape(-1, 1, 2)
            q = cv2.perspectiveTransform(pts, M).reshape(-1, 2, 2)
            lw = max(2, int(H / 400)); fs = max(0.5, H / 1600); drawn = 0
            for i, (a, b) in enumerate(q):
                if not (np.isfinite(a).all() and np.isfinite(b).all()):
                    continue
                ya, yb = a[1], b[1]
                if max(ya, yb) < -pitch or min(ya, yb) > H + pitch:
                    continue                                      # this boundary is not in the frame
                cv2.line(fr, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), (255, 255, 255), lw, cv2.LINE_AA); drawn += 1
                if i < len(units):
                    cyx = cv2.perspectiveTransform(np.float32([[[x0 + 0.04 * (x1 - x0), (edges[i] + edges[i + 1]) / 2]]]), M)[0][0]
                    if 0 <= cyx[1] <= H and -50 <= cyx[0] <= W:
                        label = units[i]["unit"]; (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, fs, 2)
                        cx, cy = int(cyx[0]), int(cyx[1])
                        col = (20, 22, 17) if units[i]["source"] == "detected" else (10, 110, 196)
                        cv2.rectangle(fr, (cx - 6, cy - th - 6), (cx + tw + 6, cy + 6), col, -1)
                        cv2.putText(fr, label, (cx, cy), cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), 2, cv2.LINE_AA)
            if drawn:
                placed += 1
        vw.write(fr); written += 1
        if progress and written % 20 == 0:
            progress(f"  drawing the units on the video: frame {k} of {n_all}")
    cap.release(); vw.release()
    return written, placed


# ---------------------------------------------------------------- one input
def do_picture(p, out, a):
    bgr = cv2.imread(str(p))
    if bgr is None:
        print(f"{p.name}: could not be opened", file=sys.stderr); return False
    finish(p.name, p.stem, bgr, out, a, None, None)
    return True


def do_video(p, out, a):
    t0 = time.time(); work = out / f"{p.stem}_work"; work.mkdir(parents=True, exist_ok=True)
    print(f"{p.name}: checking which way the video runs", flush=True)
    try:
        up, info = rack_stitch.upright(p, work)
        if info.get("turned"):
            print(f"  the video was filmed sideways: turned {info['turned']}", flush=True)
        print("  stitching the frames into one rack picture (a few minutes)", flush=True)
        rack_png = out / f"{p.stem}_rack.png"
        st = rack_stitch.stitch(up, rack_png, progress=lambda m: print("  " + m + " " * 20, end="\r", flush=True))
        print(" " * 70, end="\r")
        print(f"  stitched: {st['image_size'][0]} x {st['image_size'][1]} px from {st['keyframes_used']} key frames in {st['seconds']:.0f} s", flush=True)
        for w in st.get("warnings") or []:
            print(f"  filming note {w['code']}: {w['text']}", flush=True)
    except rack_stitch.NotReady as ex:
        print(f"  the video could not be stitched into one rack picture: {ex}", file=sys.stderr)
        shutil.rmtree(work, ignore_errors=True); return False
    bgr = cv2.imread(str(rack_png))
    units = finish(p.name, p.stem, bgr, out, a, up, work)
    if units and not a.no_video:
        print("  drawing the units on the video", flush=True)
        written, placed = overlay_video(up, bgr, units, out / f"{p.stem}_units.mp4", a.stride, progress=lambda m: print(m + " " * 10, end="\r", flush=True))
        print(" " * 70, end="\r")
        print(f"  video written: {written} frames (every {a.stride}th), the units drawn on {placed} of them -> {p.stem}_units.mp4", flush=True)
    shutil.rmtree(work, ignore_errors=True)
    print(f"  done in {time.time() - t0:.0f} s", flush=True)
    return True


def finish(name, stem, bgr, out, a, up, work):
    units, rack, rails = identify(bgr, a.conf, a.device)
    n_det = sum(u["source"] == "detected" for u in units); n_inf = len(units) - n_det
    draw(bgr, units).save(out / f"{stem}_units.jpg", "JPEG", quality=90)
    (out / f"{stem}_units.json").write_text(json.dumps({"input": name, "width": bgr.shape[1], "height": bgr.shape[0], "units": len(units),
                                                         "detected": n_det, "inferred": n_inf, "rack_box": rack, "rails": rails, "rows": units}, indent=1))
    if units:
        print(f"{name}: {len(units)} units ({n_det} seen by the model, {n_inf} added by the rules), top {units[0]['unit']}, bottom {units[-1]['unit']} -> {stem}_units.jpg", flush=True)
    else:
        print(f"{name}: no unit found. Is the whole rack front in the picture?", flush=True)
    return units


def inputs_of(paths):
    for p in paths:
        p = pathlib.Path(p)
        if p.is_dir():
            yield from sorted(q for q in p.iterdir() if q.suffix.lower() in PICTURES | VIDEOS and not q.name.startswith("."))
        elif p.suffix.lower() in PICTURES | VIDEOS:
            yield p
        else:
            print(f"skipped {p}: not a video or a picture", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="Identify the rack units from videos or pictures of racks.")
    ap.add_argument("inputs", nargs="+", help="videos, pictures or folders of them")
    ap.add_argument("--out", default="out", help="where the results go (default: out)")
    ap.add_argument("--stride", type=int, default=5, help="draw the units on every Nth frame of the video (default 5)")
    ap.add_argument("--no-video", action="store_true", help="skip the video with the units drawn on it")
    ap.add_argument("--conf", type=float, default=0.25, help="the model's confidence cut (default 0.25, as in RackTrack)")
    ap.add_argument("--device", default=None, help="cpu, mps or cuda (default: whatever the machine has)")
    a = ap.parse_args()
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    if not WEIGHTS.exists():
        sys.exit(f"the model file is missing: {WEIGHTS}")
    n = 0
    for p in inputs_of(a.inputs):
        ok = do_video(p, out, a) if p.suffix.lower() in VIDEOS else do_picture(p, out, a)
        n += bool(ok)
    if not n:
        sys.exit("nothing was read: give a video or a picture of a rack")
    print(f"results in {out.resolve()}")


if __name__ == "__main__":
    main()
