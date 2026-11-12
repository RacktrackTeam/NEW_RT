"""
video_frames.py - step 2 of reading the make and model: the device's best video frames.

Step 1 reads the panel text off the device photo. When that gives no known model, the
top frames of the rack video are read instead: the same device is usually closer, or
sharper, in one of them. If they give no known model either, step 3 (a close-up photo
of the label, --label-photo) is next.

One folder per device:

  switch_location.json     {"rack_image": "...png", "bbox_xyxy": [x1, y1, x2, y2], ...}
                           the device's box in rack-image pixels
  <rack_image>             the rack image stitched from the video (here or one folder up;
                           without it the device photo itself is looked for in the frames)
  rank1_frame00182.png     the top frames of the video, rank1 first
  ...

The frames carry no coordinates of their own, so the device is found in each one: SIFT
matches of the rack crop around the device (with its neighbours, so a twin switch one
unit up is not taken for it) give the scale and rough position; level template matching
of the device itself then places the box. A transform fitted to the matches alone tilts
the box, because the cables in front of the panel move between views (parallax).
"""
import json
import re
from pathlib import Path

import cv2
import numpy as np

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
MIN_MATCHES = 12          # SIFT matches needed to place the device in a frame at all
MIN_PLACEMENT = 0.25      # template-match score below which the placement is not trusted
OCR_WIDTH = 2000          # device crops are read at this width (the model text is tiny)


def load_folder(folder):
    """The device box, the rack image and the ranked frames of one device folder."""
    folder = Path(folder)
    meta_path = next((p for p in sorted(folder.glob("*.json"))
                      if "bbox_xyxy" in p.read_text(encoding="utf-8", errors="replace")), None)
    if meta_path is None:
        raise ValueError(f"no device location json (with bbox_xyxy) in {folder}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    name = Path(meta.get("rack_image") or "").name
    # the frames often sit in a subfolder of the rack's own folder
    rack_path = next((d / name for d in (folder, folder.parent) if name and (d / name).is_file()), None)
    frames = [p for p in folder.iterdir()
              if p.suffix.lower() in IMAGE_EXT and re.search(r"frame", p.stem, re.I) and p != rack_path]

    def rank(p):
        m = re.search(r"rank(\d+)", p.stem, re.I)
        return (int(m.group(1)) if m else 999, p.name)
    frames.sort(key=rank)
    if not frames:
        raise ValueError(f"no video frames (*frame*.png) in {folder}")
    return {"meta": meta, "rack": rack_path, "frames": frames,
            "box": [int(round(v)) for v in meta["bbox_xyxy"]]}


def device_from_rack(folder):
    """The device cut from the rack image: the main photo when none is given."""
    info = load_folder(folder)
    if info["rack"] is None:
        raise ValueError(f"no rack image {info['meta'].get('rack_image')!r} in {folder} or the folder above it")
    rack = cv2.imread(str(info["rack"]), cv2.IMREAD_COLOR)
    x1, y1, x2, y2 = info["box"]
    return rack[max(0, y1):y2, max(0, x1):x2].copy()


class Locator:
    """Places the device's rack-image box in video frames."""

    def __init__(self, rack_bgr, box):
        x1, y1, x2, y2 = box
        self.w, self.h = x2 - x1, y2 - y1
        gray = cv2.cvtColor(rack_bgr, cv2.COLOR_BGR2GRAY)
        # context around the device: the units above and below tell it from a twin
        mx, my = int(self.w * 0.10), int(self.h * 0.8)
        cx1, cy1 = max(0, x1 - mx), max(0, y1 - my)
        cx2, cy2 = min(gray.shape[1], x2 + mx), min(gray.shape[0], y2 + my)
        self.center = ((x1 + x2) / 2 - cx1, (y1 + y2) / 2 - cy1)
        self.device = gray[y1:y2, x1:x2]
        self.sift = cv2.SIFT_create(nfeatures=8000)
        self.kp, self.des = self.sift.detectAndCompute(gray[cy1:cy2, cx1:cx2], None)
        self.matcher = cv2.BFMatcher()

    def locate(self, frame_bgr):
        """(x1, y1, x2, y2) of the device in the frame and the placement score, or None."""
        g = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        kf, df = self.sift.detectAndCompute(g, None)
        if df is None or self.des is None or len(kf) < 2:
            return None
        good = [m for m, n in (p for p in self.matcher.knnMatch(self.des, df, k=2) if len(p) == 2)
                if m.distance < 0.75 * n.distance]
        if len(good) < MIN_MATCHES:
            return None
        A, _ = cv2.estimateAffinePartial2D(np.float32([self.kp[m.queryIdx].pt for m in good]),
                                           np.float32([kf[m.trainIdx].pt for m in good]),
                                           method=cv2.RANSAC, ransacReprojThreshold=6.0)
        if A is None:
            return None
        s = float(np.sqrt(abs(np.linalg.det(A[:, :2]))))
        cx, cy = A @ np.array([self.center[0], self.center[1], 1.0])
        best = None
        for k in np.linspace(0.85, 1.15, 13):
            tw, th = int(self.w * s * k), int(self.h * s * k)
            if tw < 20 or th < 8:
                continue
            sx1, sy1 = int(max(0, cx - 0.65 * tw)), int(max(0, cy - 1.7 * th))
            sx2, sy2 = int(min(g.shape[1], cx + 0.65 * tw)), int(min(g.shape[0], cy + 1.7 * th))
            win = g[sy1:sy2, sx1:sx2]
            if win.shape[0] < th or win.shape[1] < tw:
                continue
            t = cv2.resize(self.device, (tw, th), interpolation=cv2.INTER_AREA)
            _, score, _, loc = cv2.minMaxLoc(cv2.matchTemplate(win, t, cv2.TM_CCOEFF_NORMED))
            if best is None or score > best[1]:
                best = ((sx1 + loc[0], sy1 + loc[1], sx1 + loc[0] + tw, sy1 + loc[1] + th), float(score))
        if best is None or best[1] < MIN_PLACEMENT:
            return None
        return best


def sharpness(img):
    """Variance of the Laplacian at a fixed width, so crops of different sizes compare."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (1000, max(1, int(g.shape[0] * 1000 / g.shape[1]))), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def device_crops(folder, log=print, device=None):
    """The device cut out of every frame it can be found in, best first.

    The rack image and the device's box in it are the reference: the units around the
    device tell it from a twin. Without the rack image, `device` (the cropped device
    photo of step 1) is looked for instead, with no surroundings to go by.
    Best = sharpest at the most pixels: the model text is a few pixels tall, so both
    focus and how close the camera was decide whether it can be read."""
    info = load_folder(folder)
    if info["rack"] is not None:
        loc = Locator(cv2.imread(str(info["rack"]), cv2.IMREAD_COLOR), info["box"])
    elif device is not None:
        log("  no rack image; looking for the device photo itself in the frames")
        loc = Locator(device, [0, 0, device.shape[1], device.shape[0]])
    else:
        raise ValueError(f"no rack image {info['meta'].get('rack_image')!r} in {folder} or the folder above it")
    crops = []
    for i, f in enumerate(info["frames"]):
        frame = cv2.imread(str(f), cv2.IMREAD_COLOR)
        if frame is None:
            log(f"  {f.name}: not readable, skipped")
            continue
        found = loc.locate(frame)
        if found is None:
            log(f"  {f.name}: device not found in this frame, skipped")
            continue
        (x1, y1, x2, y2), placed = found
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)
        crop = frame[y1:y2, x1:x2]
        sharp = sharpness(crop)
        crops.append({"frame": f.name, "rank": i + 1, "box": [x1, y1, x2, y2], "placed": round(placed, 2),
                      "width": x2 - x1, "sharpness": round(sharp), "crop": crop,
                      "quality": sharp * min(1.0, (x2 - x1) / 1500) ** 0.5})
    crops.sort(key=lambda c: (-c["quality"], c["rank"]))
    return crops


def _ocr_views(crop):
    """The crop as read: enlarged to OCR_WIDTH and sharpened (the model text is tiny)."""
    k = OCR_WIDTH / crop.shape[1]
    big = cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC) if k > 1 else crop
    blur = cv2.GaussianBlur(big, (0, 0), 2)
    return [crop, cv2.addWeighted(big, 1.6, blur, -0.6, 0)]


def _squash(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def read_frames(folder, identify, is_known, log=print, out_dir=None, device=None):
    """Make and model from the device's video frames.

    identify(img) -> (vendor, model, tokens, engine) and is_known(vendor, model) -> bool
    are the pipeline's own. Frames are read best first and the first known model stops
    the search. Without one, the vendor most frames agree on is returned, and a model
    only when two frames read the very same text (a single garbled read is not used).
    Returns {"vendor", "model", "confident", "frame", "readings"}."""
    crops = device_crops(folder, log, device=device)
    log(f"  device found in {len(crops)} of {len(load_folder(folder)['frames'])} frames")
    if out_dir is not None:
        d = Path(out_dir) / "1_frames"
        d.mkdir(parents=True, exist_ok=True)
        for c in crops:
            cv2.imwrite(str(d / f"{Path(c['frame']).stem}.jpg"), c["crop"])
    readings, result = [], None
    for c in crops:
        for view in _ocr_views(c["crop"]):
            try:
                v, m, _, _ = identify(view)
            except Exception as e:          # one bad frame must not end the step
                log(f"  {c['frame']}: OCR failed ({e})")
                continue
            known = bool(v and m and is_known(v, m))
            readings.append({"frame": c["frame"], "vendor": v, "model": m, "known": known})
            if known:
                result = {"vendor": v, "model": m, "confident": True, "frame": c["frame"]}
                break
        log(f"  {c['frame']} (sharpness {c['sharpness']}, {c['width']} px wide): "
            + ", ".join(f"{r['vendor'] or '?'} {r['model'] or '?'}" for r in readings if r["frame"] == c["frame"]))
        if result:
            break
    if result is None:
        vendors = [r["vendor"] for r in readings if r["vendor"]]
        vendor = max(set(vendors), key=vendors.count) if vendors else None
        models = {}
        for r in readings:
            if r["model"]:
                models.setdefault(_squash(r["model"]), (r["model"], set()))[1].add(r["frame"])
        agreed = [m for m, frames in models.values() if len(frames) >= 2]
        result = {"vendor": vendor, "model": agreed[0] if agreed else None, "confident": False,
                  "frame": crops[0]["frame"] if crops else None}
    result["readings"] = readings
    if out_dir is not None:
        summary = {k: v for k, v in result.items()}
        summary["frames"] = [{k: v for k, v in c.items() if k not in ("crop", "quality")} for c in crops]
        (Path(out_dir) / "1_frames.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return result
