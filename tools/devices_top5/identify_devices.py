#!/usr/bin/env python3
"""Find the devices in a rack video and keep the five clearest frames of each: the same steps RackTrack runs.

    python identify_devices.py <video or folder> [more] [--out out] [--stride 5] [--score 0.5] [--no-deim] [--device cpu]

For every video (one slow sweep of the rack front, top to bottom; a video filmed sideways is turned upright first):
    1. Frames: the video is stitched into one picture of the whole rack, and every 5th frame (--stride) is placed on
       that picture with image features (a homography per frame).
    2. Device detection: on every placed frame and on the rack picture itself, two detectors run, as in the app: the
       device detector (DEIMv2, one class, boxes from --score up) and the typed finder (YOLO, the device kind). Each
       box is carried onto the rack picture through its frame's homography.
    3. Devices: boxes that land on the same place from different frames are one device (vertical overlap of at least
       half). A device needs two frames, or the rack picture and one frame. Devices are numbered from the top: D1.
    4. Top 5 frames: for each device, every frame of the video is matched around the device, gated (the whole device
       inside the frame, a sound match) and ranked by the sharpness of the device itself; the five best, at least ten
       frames apart, are kept (the app's frame locator, unchanged). A device the locator finds in no frame keeps the
       best of the placed frames instead.

You get, in out/:
    <name>_rack.png              the stitched rack picture
    <name>_devices.jpg           the rack picture with every device outlined and labelled: D1 · kind · how many frames saw it
    <name>_devices.json          the devices (outline on the rack, kind, votes) and each one's five frames (frame number,
                                 time, score, where the device sits in that frame)
    <name>_frames/D01_top5.jpg   the five crops of D1 side by side, rank 1 first
    <name>_frames/D01_r1.jpg     the crop of D1 from its best frame (r2 .. r5 the next four)
    <name>_frames/D01_r1_frame.jpg   that whole frame with the device outlined
and one line per device in the terminal.
"""
import argparse
import contextlib
import json
import os
import pathlib
import shutil
import sys
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]                                   # in the RackTrack checkout (tools/devices_top5/) the code and the models come from engine/ and models/
sys.path.insert(0, str(HERE))
if not (HERE / "rack_stitch.py").exists():
    sys.path.insert(0, str(ROOT / "engine"))
import rack_stitch  # noqa: E402
import unit_grid as ug  # noqa: E402
import frame_locator as FL  # noqa: E402

W_DIR = HERE / "weights" if (HERE / "weights").exists() else ROOT / "models" / "weights"
DEIM_WEIGHTS = W_DIR / "device_detector_deimv2.pth"
FINDER_WEIGHTS = W_DIR / "device_finder.pt"
DEIM_DIR = HERE / "third_party" / "DEIMv2" if (HERE / "third_party" / "DEIMv2").exists() else ROOT / "engine" / "third_party" / "DEIMv2"
VIDEOS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".3gp"}
MIN_INLIERS = 40            # a frame is placed on the rack picture when its homography has this many inliers (as in the app)
TYPED_CONF = 0.2            # the typed finder's confidence cut (as in the app)
TYPED_AS_BOX = 0.4          # without the device detector, a typed sighting from this confidence up is a device box
OVERLAP = 0.5               # boxes whose vertical overlap is at least this share of the smaller one are the same device
TOP_K = 5


# ---------------------------------------------------------------- the two detectors
class DeviceDetector:
    """The app's device detector (DEIMv2, DINOv3 vit_tinyplus backbone, one class). det(rgb) -> [(x1, y1, x2, y2, score)]."""

    def __init__(self, weights=DEIM_WEIGHTS, deim_dir=DEIM_DIR, size=640, device="cpu"):
        import torch
        deim = pathlib.Path(deim_dir)
        if not (deim / "engine").is_dir():
            raise FileNotFoundError(f"DEIMv2 code not found at {deim}")
        if not pathlib.Path(weights).exists():
            raise FileNotFoundError(f"detector weights missing: {weights}")
        sys.path.insert(0, str(deim))
        from engine.core import YAMLConfig
        cfg = YAMLConfig(str(deim / "configs" / "deimv2" / "deimv2_dinov3_m_coco.yml"), num_classes=1,
                         DINOv3STAs={"name": "vit_tinyplus", "embed_dim": 256, "weights_path": None,
                                     "interaction_indexes": [3, 7, 11], "num_heads": 4})
        ck = torch.load(weights, map_location="cpu", weights_only=False)
        state = ck["ema"]["module"] if ck.get("ema") else ck["model"]
        model = cfg.model
        model.load_state_dict(state, strict=False)
        self.model = (model.deploy() if hasattr(model, "deploy") else model).eval().to(device)
        post = cfg.postprocessor
        self.post = (post.deploy() if hasattr(post, "deploy") else post).eval()
        self.size, self.device = size, device

    def __call__(self, bgr):
        import torch
        import torchvision.transforms.functional as TF
        img = Image.fromarray(np.ascontiguousarray(bgr[:, :, ::-1])).convert("RGB")
        w, h = img.size
        x = TF.to_tensor(img.resize((self.size, self.size), Image.BILINEAR))
        x = TF.normalize(x, [0.485, 0.456, 0.406], [0.229, 0.224, 0.225]).unsqueeze(0).to(self.device)
        with torch.no_grad():
            res = self.post(self.model(x), torch.tensor([[w, h]], dtype=torch.float32))
        labels, boxes, scores = (res[0]["labels"], res[0]["boxes"], res[0]["scores"]) if isinstance(res, list) else res
        if isinstance(res, tuple):
            labels, boxes, scores = labels[0], boxes[0], scores[0]
        return [(float(b[0]), float(b[1]), float(b[2]), float(b[3]), float(s)) for b, s in zip(boxes, scores)]


class TypedFinder:
    """The app's typed device finder (YOLO device_finder.pt): [{class_name, confidence, box}] in picture pixels."""

    def __init__(self, weights=FINDER_WEIGHTS, device=None):
        self.model = ug.load_model(str(weights)); self.kw = {"device": device} if device else {}

    def __call__(self, bgr):
        small, factor = ug.shrink_for_inference(bgr)
        res = self.model.predict(small, conf=TYPED_CONF, iou=0.5, verbose=False, **self.kw)
        if not res or res[0].boxes is None:
            return []
        names = getattr(self.model, "names", {}) or {}
        out = []
        for b, c, s in zip(res[0].boxes.xyxy.tolist(), res[0].boxes.cls.tolist(), res[0].boxes.conf.tolist()):
            raw = str(names.get(int(c), c)).strip()
            out.append({"class_name": {"pdu": "PDU", "ups": "UPS", "psu": "PSU"}.get(raw.lower(), raw.title()), "confidence": float(s), "box": [v * factor for v in b]})
        return out


def load_detectors(a):
    typed = TypedFinder(device=a.device)
    deim = None
    if not a.no_deim:
        try:
            with contextlib.redirect_stdout(sys.stderr):          # the detector's code prints its build notes
                deim = DeviceDetector(device=a.device or "cpu")
            print("detectors: the device detector (DEIMv2) and the typed finder (YOLO)", flush=True)
        except Exception as ex:  # noqa: BLE001
            print(f"the device detector could not be loaded ({type(ex).__name__}: {ex}); the typed finder alone is used, boxes from {TYPED_AS_BOX} up", flush=True)
    else:
        print("detectors: the typed finder (YOLO) alone (--no-deim)", flush=True)
    return deim, typed


# ---------------------------------------------------------------- frames placed on the rack picture
def read_frames(video, stride):
    cap = cv2.VideoCapture(str(video)); out, k = [], 0
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
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY); g = cv2.resize(g, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def register(frames, rack_bgr, progress=None):
    """A homography (frame -> rack) per frame with its inliers, sharpness and the rack area it shows (as in the app)."""
    sift = cv2.SIFT_create(nfeatures=8000); kr, dr = sift.detectAndCompute(cv2.cvtColor(rack_bgr, cv2.COLOR_BGR2GRAY), None)
    bf = cv2.BFMatcher(cv2.NORM_L2); H_rack, W_rack = rack_bgr.shape[:2]; out = []
    for n, (idx, fr) in enumerate(frames):
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY); kf, df = sift.detectAndCompute(g, None)
        rec = {"index": idx, "sharpness": sharpness(fr), "inliers": 0, "H": None, "y0": None, "y1": None}
        if df is not None and len(kf) >= 12 and dr is not None:
            good = [m for m, s in bf.knnMatch(df, dr, k=2) if m.distance < 0.75 * s.distance]
            if len(good) >= 12:
                src = np.float32([kf[m.queryIdx].pt for m in good]); dst = np.float32([kr[m.trainIdx].pt for m in good])
                H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0); inl = int(mask.sum()) if mask is not None else 0
                if H is not None and inl >= MIN_INLIERS:
                    h, w = g.shape; q = cv2.perspectiveTransform(np.float32([[[0, 0], [w, 0], [w, h], [0, h]]]), H)[0]
                    pw, ph = float(np.max(q[:, 0]) - np.min(q[:, 0])), float(np.max(q[:, 1]) - np.min(q[:, 1]))
                    if pw > 0.3 * W_rack and 0.2 < ph / max(1, pw) < 3:
                        rec.update(inliers=inl, H=H, y0=float(np.min(q[:, 1])), y1=float(np.max(q[:, 1])))
        out.append(rec)
        if progress and (n + 1) % 10 == 0:
            progress(f"  placing frame {idx} ({n + 1} of {len(frames)})")
    med = float(np.median([r["sharpness"] for r in out])) or 1.0
    for r in out:
        r["quality"] = (min(1.0, r["inliers"] / 150.0) * min(1.5, r["sharpness"] / med)) if r["H"] is not None else 0.0
    return out


def poly_of(H, box):
    x1, y1, x2, y2 = box[:4]
    q = cv2.perspectiveTransform(np.float32([[(x1, y1), (x2, y1), (x2, y2), (x1, y2)]]), H)[0]
    return [(float(a), float(b)) for a, b in q]


def span_of(poly):
    """A polygon's span on the rack: the mean of its top edge and of its bottom edge, not its bounding box."""
    ys = sorted(p[1] for p in poly); xs = [p[0] for p in poly]
    return [min(xs), (ys[0] + ys[1]) / 2, max(xs), (ys[2] + ys[3]) / 2]


def back_project(H, poly):
    return [(float(a), float(b)) for a, b in cv2.perspectiveTransform(np.float32([poly]), np.linalg.inv(H))[0]]


# ---------------------------------------------------------------- devices from the boxes
def v_overlap(a, b):
    ov = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return ov / max(1.0, min(a[3] - a[1], b[3] - b[1]))


def devices_from_boxes(sightings, rack_hw):
    """sightings: [{span, score, weight, source ('frame' index or 'rack'), kind, kind_conf}] on the rack picture.
    Boxes overlapping by OVERLAP vertically are one device; its span is the weighted mean. Returns devices top to bottom."""
    sightings = sorted(sightings, key=lambda s: (s["span"][1] + s["span"][3]) / 2)
    groups = []
    for s in sightings:
        best, best_ov = None, 0.0
        for g in groups:
            ov = v_overlap(g["span"], s["span"])
            if ov >= OVERLAP and ov > best_ov:
                best, best_ov = g, ov
        if best is None:
            groups.append({"span": list(s["span"]), "w": s["weight"], "items": [s]})
        else:
            w = best["w"] + s["weight"]
            best["span"] = [(best["span"][k] * best["w"] + s["span"][k] * s["weight"]) / w for k in range(4)]; best["w"] = w; best["items"].append(s)
    H, W = rack_hw; out = []
    for g in groups:
        frames = sorted({s["source"] for s in g["items"] if s["source"] != "rack"}); on_rack = any(s["source"] == "rack" for s in g["items"])
        if len(frames) < 2 and not (on_rack and len(frames) >= 1) and not (on_rack and max(s["score"] for s in g["items"]) >= 0.7):
            continue                                              # one frame alone, or a weak rack-only box: not a device
        kinds = {}
        for s in g["items"]:
            if s.get("kind"):
                kinds[s["kind"]] = kinds.get(s["kind"], 0.0) + s["kind_conf"]
        kind = max(kinds, key=kinds.get) if kinds else None
        x0, y0, x1, y1 = [max(0, min(v, lim)) for v, lim in zip(g["span"], (W, H, W, H))]
        if y1 - y0 < 4 or x1 - x0 < 4:
            continue
        out.append({"span": [round(x0), round(y0), round(x1), round(y1)], "polygon": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
                    "score": round(max(s["score"] for s in g["items"]), 3), "frames_seen": frames, "on_rack_picture": on_rack,
                    "kind": kind, "kind_confidence": round(max((s["kind_conf"] for s in g["items"] if s.get("kind") == kind), default=0.0), 3)})
    out.sort(key=lambda d: d["span"][1])
    for i, d in enumerate(out, 1):
        d["index"] = i; d["label"] = f"D{i}"
    return out


def sightings_on(bgr, H, source, deim, typed, score_min, quality=1.0):
    """The two detectors on one picture (a frame with its homography, or the rack picture with H = identity)."""
    out = []
    boxes = [b for b in deim(bgr) if b[4] >= score_min] if deim else []
    types = typed(bgr)
    if deim is None:
        boxes = [tuple(t["box"]) + (t["confidence"],) for t in types if t["confidence"] >= TYPED_AS_BOX]
    for b in boxes:
        poly = poly_of(H, b); span = span_of(poly)
        kind, kc = None, 0.0
        for t in types:                                           # the typed sighting over this box, if any
            if v_overlap(span_of(poly_of(H, t["box"])), span) >= OVERLAP and t["confidence"] > kc:
                kind, kc = t["class_name"], t["confidence"]
        out.append({"span": span, "score": float(b[4]), "weight": quality * float(b[4]), "source": source, "kind": kind, "kind_conf": kc})
    return out


# ---------------------------------------------------------------- the five frames of each device
def device_frame_scores(cands, poly, imgs):
    """The app's fallback ranking over the placed frames: the device's own sharpness, how central it sits, the placement."""
    rec = []
    for f in cands:
        pts = np.array(back_project(f["H"], poly), dtype=np.float32); img = imgs[f["index"]]; h, w = img.shape[:2]
        x1, y1 = max(0, int(pts[:, 0].min())), max(0, int(pts[:, 1].min())); x2, y2 = min(w, int(pts[:, 0].max()) + 1), min(h, int(pts[:, 1].max()) + 1)
        sharp = sharpness(img[y1:y2, x1:x2]) if x2 - x1 > 8 and y2 - y1 > 8 else 0.0
        cx, cy = pts[:, 0].mean(), pts[:, 1].mean()
        off = min(1.0, float(np.hypot((cx - w / 2) / (w / 2), (cy - h / 2) / (h / 2))) / np.sqrt(2) * 2)
        rec.append((f, sharp, 1.0 - 0.5 * off, min(1.0, f["inliers"] / 150.0), pts))
    med = float(np.median([r[1] for r in rec])) if rec else 1.0
    out = [((min(1.5, sharp / med) if med else 0.0) * central * place, f, pts) for f, sharp, central, place, pts in rec]
    return sorted(out, key=lambda t: -t[0])


def inside(f, poly, hw, margin=10):
    h, w = hw
    return all(margin <= x <= w - margin and margin <= y <= h - margin for x, y in back_project(f["H"], poly))


def _font(size):
    for name in ("Helvetica.ttc", "Arial.ttf", "DejaVuSans-Bold.ttf", "arial.ttf"):
        for folder in ("/System/Library/Fonts", "/Library/Fonts", "/usr/share/fonts/truetype/dejavu", "C:/Windows/Fonts"):
            try:
                return ImageFont.truetype(str(pathlib.Path(folder) / name), size)
            except OSError:
                pass
    return ImageFont.load_default()


def draw_devices(rack_bgr, devices):
    im = Image.fromarray(cv2.cvtColor(rack_bgr, cv2.COLOR_BGR2RGB)); d = ImageDraw.Draw(im, "RGBA")
    W = im.size[0]; lw = max(2, W // 400); f = _font(max(14, W // 45))
    for dv in devices:
        x0, y0, x1, y1 = dv["span"]
        d.rectangle([x0, y0, x1, y1], outline=(255, 230, 0, 240), width=lw)
        text = f"{dv['label']} · {dv['kind'] or 'device'} · {len(dv['frames_seen'])} frames"
        tw = d.textlength(text, font=f) + 16; th = f.size * 1.5
        d.rectangle([x0 + lw, y0 + lw, x0 + lw + tw, y0 + lw + th], fill=(17, 22, 20, 215))
        d.text((x0 + lw + 8, y0 + lw + th * 0.12), text, fill=(255, 255, 255, 255), font=f)
    return im


def crop_around(frame_bgr, pts, pad=0.12):
    h, w = frame_bgr.shape[:2]; pts = np.asarray(pts, np.float32)
    x1, y1, x2, y2 = pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max()
    py = (y2 - y1) * pad; px = (x2 - x1) * pad * 0.5
    a, b, c, e = max(0, int(x1 - px)), max(0, int(y1 - py)), min(w, int(x2 + px) + 1), min(h, int(y2 + py) + 1)
    return frame_bgr[b:e, a:c]


def sheet(crops, labels, width=360):
    tiles = []
    for crop, label in zip(crops, labels):
        h, w = crop.shape[:2]; s = width / max(1, w); t = cv2.resize(crop, (width, max(1, int(h * s))))
        bar = np.full((28, width, 3), 20, np.uint8); cv2.putText(bar, label, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        tiles.append(np.vstack([bar, t]))
    hmax = max(t.shape[0] for t in tiles)
    tiles = [np.vstack([t, np.full((hmax - t.shape[0], width, 3), 255, np.uint8)]) if t.shape[0] < hmax else t for t in tiles]
    gap = np.full((hmax, 8, 3), 255, np.uint8); out = tiles[0]
    for t in tiles[1:]:
        out = np.hstack([out, gap, t])
    return out


def small(bgr, max_w=1280):
    h, w = bgr.shape[:2]
    return bgr if w <= max_w else cv2.resize(bgr, (max_w, int(h * max_w / w)), interpolation=cv2.INTER_AREA)


# ---------------------------------------------------------------- one video
def do_video(p, out, a, deim, typed):
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
    rack = cv2.imread(str(rack_png))
    frames, n_all = read_frames(up, a.stride)
    print(f"  {len(frames)} frames read (every {a.stride}th of {n_all}); placing them on the rack picture", flush=True)
    regs = register(frames, rack, progress=lambda m: print(m + " " * 10, end="\r", flush=True)); print(" " * 70, end="\r")
    imgs = {idx: fr for idx, fr in frames}; placed = [r for r in regs if r["H"] is not None]
    print(f"  {len(placed)} of {len(frames)} frames placed; detecting devices on each", flush=True)
    sightings = sightings_on(rack, np.eye(3), "rack", deim, typed, a.score)
    for n, r in enumerate(placed):
        sightings += sightings_on(imgs[r["index"]], r["H"], r["index"], deim, typed, a.score, quality=max(0.2, r["quality"]))
        if (n + 1) % 5 == 0:
            print(f"  detecting: frame {r['index']} ({n + 1} of {len(placed)})" + " " * 10, end="\r", flush=True)
    print(" " * 70, end="\r")
    devices = devices_from_boxes(sightings, rack.shape[:2])
    draw_devices(rack, devices).save(out / f"{p.stem}_devices.jpg", "JPEG", quality=90)
    print(f"  {len(devices)} devices from {len(sightings)} boxes on {len(placed)} frames and the rack picture -> {p.stem}_devices.jpg", flush=True)
    if not devices:
        (out / f"{p.stem}_devices.json").write_text(json.dumps({"input": p.name, "devices": []}, indent=1)); shutil.rmtree(work, ignore_errors=True); return True
    # the five clearest frames of each device: the app's frame locator over every frame of the video
    near = {}
    for r in placed:
        for k in range(r["index"] - 2, r["index"] + 3):
            if k not in near or abs(k - r["index"]) < abs(k - near[k][0]):
                near[k] = (r["index"], r["H"])
    print(f"  looking through every frame for the five clearest views of each device", flush=True)
    found, chosen = FL.locate_devices(up, rack, {dv["index"]: dv["polygon"] for dv in devices}, hint=lambda n: near[n][1] if n in near else None,
                                      progress=lambda m: print("  " + m + " " * 10, end="\r", flush=True))
    print(" " * 70, end="\r")
    fdir = out / f"{p.stem}_frames"; fdir.mkdir(exist_ok=True)
    fps = cv2.VideoCapture(str(up)).get(cv2.CAP_PROP_FPS) or 30.0
    for dv in devices:
        r = found.get(dv["index"]) or {"frames": [], "scanned": 0, "eligible": 0}
        tops = []
        if r["frames"]:
            for h in r["frames"]:
                fr = chosen[h["frame"]]; pts = np.asarray(h["polygon_px"], np.float32).reshape(-1, 2)
                tops.append({"rank": h["rank"], "frame": h["frame"], "time_s": round(h["frame"] / fps, 2), "score": round(float(h["score"]), 3), "inliers": h["inliers"],
                             "by": "frame locator", "polygon_px": [[round(float(x), 1), round(float(y), 1)] for x, y in pts], "_img": fr, "_pts": pts})
            how = f"{r['eligible']} of {r['scanned']} frames show the whole device; the {len(tops)} sharpest, at least 10 frames apart"
        else:
            cands = [f for f in placed if inside(f, dv["polygon"], imgs[f["index"]].shape[:2])] or placed
            for rank, (sc, f, pts) in enumerate(device_frame_scores(cands, dv["polygon"], imgs)[:TOP_K], 1):
                tops.append({"rank": rank, "frame": f["index"], "time_s": round(f["index"] / fps, 2), "score": round(float(sc), 3), "inliers": f["inliers"],
                             "by": "placed frames", "polygon_px": [[round(float(x), 1), round(float(y), 1)] for x, y in pts], "_img": imgs[f["index"]], "_pts": pts})
            how = f"no frame showed the whole device to the locator; the {len(tops)} best placed frames are used"
        crops, labels = [], []
        for t in tops:
            crop = crop_around(t["_img"], t["_pts"]); name = f"{dv['label'].replace('D', 'D0') if dv['index'] < 10 else dv['label']}_r{t['rank']}"
            if crop.size:
                cv2.imwrite(str(fdir / f"{name}.jpg"), crop, [cv2.IMWRITE_JPEG_QUALITY, 92]); crops.append(crop); labels.append(f"r{t['rank']}  frame {t['frame']}  {t['time_s']} s  score {t['score']}")
            whole = small(t["_img"].copy()); s = whole.shape[1] / t["_img"].shape[1]
            cv2.polylines(whole, [np.int32(t["_pts"] * s)], True, (0, 230, 255), max(2, whole.shape[1] // 400), cv2.LINE_AA)
            cv2.imwrite(str(fdir / f"{name}_frame.jpg"), whole, [cv2.IMWRITE_JPEG_QUALITY, 85])
            t["file"] = f"{p.stem}_frames/{name}.jpg"; t["frame_file"] = f"{p.stem}_frames/{name}_frame.jpg"; t.pop("_img"); t.pop("_pts")
        if crops:
            cv2.imwrite(str(fdir / f"{dv['label'].replace('D', 'D0') if dv['index'] < 10 else dv['label']}_top5.jpg"), sheet(crops, labels), [cv2.IMWRITE_JPEG_QUALITY, 88])
        dv["top_frames"] = tops; dv["frame_search"] = how
        print(f"  {dv['label']}: {dv['kind'] or 'device'} ({dv['score']:.2f}), seen in {len(dv['frames_seen'])} placed frames; top frames {', '.join(str(t['frame']) for t in tops)} ({how})", flush=True)
    (out / f"{p.stem}_devices.json").write_text(json.dumps({"input": p.name, "turned": info.get("turned"), "rack_picture": f"{p.stem}_rack.png",
                                                             "rack_size": [rack.shape[1], rack.shape[0]], "frames_read": len(frames), "frames_placed": len(placed),
                                                             "detectors": ["DEIMv2 device detector", "YOLO typed finder"] if deim else ["YOLO typed finder"],
                                                             "device_score_min": a.score, "devices": devices}, indent=1))
    shutil.rmtree(work, ignore_errors=True)
    print(f"  done in {time.time() - t0:.0f} s", flush=True)
    return True


def inputs_of(paths):
    for p in paths:
        p = pathlib.Path(p)
        if p.is_dir():
            yield from sorted(q for q in p.iterdir() if q.suffix.lower() in VIDEOS and not q.name.startswith("."))
        elif p.suffix.lower() in VIDEOS:
            yield p
        else:
            print(f"skipped {p}: not a video", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="Find the devices in rack videos and keep the five clearest frames of each.")
    ap.add_argument("inputs", nargs="+", help="videos or folders of videos")
    ap.add_argument("--out", default="out", help="where the results go (default: out)")
    ap.add_argument("--stride", type=int, default=5, help="place every Nth frame on the rack picture for detection (default 5, as in RackTrack)")
    ap.add_argument("--score", type=float, default=0.5, help="the device detector's confidence cut (default 0.5, as in RackTrack)")
    ap.add_argument("--no-deim", action="store_true", help="skip the DEIMv2 device detector and use the typed finder alone")
    ap.add_argument("--device", default=None, help="cpu, mps or cuda (default: cpu for the device detector, whatever the machine has for the typed finder)")
    a = ap.parse_args()
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    if not FINDER_WEIGHTS.exists():
        sys.exit(f"the typed finder's model file is missing: {FINDER_WEIGHTS}")
    deim, typed = load_detectors(a)
    n = 0
    for p in inputs_of(a.inputs):
        n += bool(do_video(p, out, a, deim, typed))
    if not n:
        sys.exit("nothing was read: give a video of a rack")
    print(f"results in {out.resolve()}")


if __name__ == "__main__":
    main()
