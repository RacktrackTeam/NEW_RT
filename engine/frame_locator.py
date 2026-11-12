"""The lead's frame locator (integrated 2026-10-21): for every device, the 5 clearest video frames that show it whole.

Given the video and where each device sits in the stitched rack image, every frame of the video (not only the
frames kept for stitching) is registered to the rack around the device, gated and scored:

1. Register: SIFT on the frame shrunk to 1280 px (CLAHE), ratio test 0.75, MAGSAC homography frame -> rack, against
   the SIFT of the rack image around the device (its box plus 400 px).
2. Gate: >= 30 inliers; the projected box is convex and 0.5-2x its size on the rack; it lies >= 20 px inside the frame.
3. Score: the device region warped onto the rack box's own grid, sharpness = mean(Sobel_x^2 + Sobel_y^2) / grey
   variance (contrast-normalised Tenengrad): edge crispness, not brightness.
4. Rank by score (ties: the earlier frame) and keep 5, never two closer than 10 frames, so they are five views.
5. Refine the 5 at full resolution (a re-fit may move the box corners at most 25 px).

Classical computer vision only, deterministic, nothing tuned per video. All settings are in Config; change one only
on purpose and bump ALGORITHM_VERSION. The video is read once for all devices: the frame's SIFT does not depend on the
device, so it is computed once per frame and matched against each device's rack reference. `hint` (optional) skips a
device in frames where a nearby mapped frame already puts it far outside the picture; such frames fail gate 2 anyway.

"""
from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass

import cv2
import numpy as np

ALGORITHM_VERSION = "1.0.0"
if os.environ.get("OMP_NUM_THREADS"):          # this Mac overheats: OpenCV keeps to the same thread cap
    cv2.setNumThreads(int(os.environ["OMP_NUM_THREADS"]))


@dataclass(frozen=True)
class Config:
    top_k: int = 5
    scan_stride: int = 2             # pass 1 looks at every scan_stride-th frame (the kept frames are min_frame_gap apart anyway)
    min_frame_gap: int = 10          # chosen frames are >= this many frames apart (~0.33 s at 30 fps)
    min_inliers: int = 30            # gate: RANSAC inliers of the frame -> rack homography
    border_margin_px: int = 20       # gate: the projected box sits this far inside the frame on every side
    min_scale: float = 0.5           # gate: device size in the frame / on the rack lies in [min, max]
    max_scale: float = 2.0
    match_width: int = 960           # coarse matching: every frame shrunk to this width (never enlarged); 1280 until 27 Oct 2026
    rack_roi_pad_px: int = 400       # rack region around the device used for matching
    sift_features: int = 3000        # 6000 until 27 Oct 2026: with 960 px and every second frame the scan is 4x lighter, same chosen frames within 3
    sift_contrast: float = 0.02
    ratio_test: float = 0.75
    ransac_threshold_px: float = 3.0
    ransac_max_iters: int = 5000
    ransac_confidence: float = 0.999
    refine: bool = True              # fine matching: the chosen frames only, full resolution
    refine_roi_pad_px: int = 350
    refine_sift_features: int = 12000
    refine_sift_contrast: float = 0.015
    refine_ransac_threshold_px: float = 2.0
    refine_ransac_max_iters: int = 10000
    refine_ransac_confidence: float = 0.9999
    refine_max_corner_shift_px: float = 25.0
    hint_skip_px: float = 80.0       # with a hint: skip a device lying this far outside the frame


def _features(gray, n_features, contrast):
    clahe = cv2.createCLAHE(2.0, (8, 8))         # per call: a shared CLAHE is not safe across worker threads
    kp, desc = cv2.SIFT_create(n_features, contrastThreshold=contrast).detectAndCompute(clahe.apply(gray), None)
    return np.float32([k.pt for k in kp]).reshape(-1, 2), desc


class RackReference:
    """SIFT of the rack image around one device, computed once and matched against every frame."""

    def __init__(self, rack_bgr, bbox, *, pad, n_features, contrast, ratio, ransac_thr, ransac_iters, ransac_conf):
        x1, y1, x2, y2 = bbox
        h, w = rack_bgr.shape[:2]
        x0, y0, xe, ye = max(0, x1 - pad), max(0, y1 - pad), min(w, x2 + pad), min(h, y2 + pad)
        pts, self._desc = _features(cv2.cvtColor(rack_bgr[y0:ye, x0:xe], cv2.COLOR_BGR2GRAY), n_features, contrast)
        self._pts = pts + np.float32([x0, y0])
        self._bf = cv2.BFMatcher()
        self._ratio, self._thr, self._iters, self._conf = ratio, ransac_thr, ransac_iters, ransac_conf
        self.n_features = len(pts)

    def match(self, frame_pts, frame_desc, scale=1.0):
        """The frame's features (found on a frame shrunk by `scale`) -> (H full-resolution frame -> rack, inliers), or None."""
        if self._desc is None or self.n_features < 12 or frame_desc is None or len(frame_pts) <= 20:
            return None
        good = [a for a, b in (m for m in self._bf.knnMatch(frame_desc, self._desc, k=2) if len(m) == 2) if a.distance < self._ratio * b.distance]
        if len(good) < 12:
            return None
        pf = frame_pts[[a.queryIdx for a in good]] / scale
        pr = self._pts[[a.trainIdx for a in good]]
        H, inl = cv2.findHomography(pf, pr, cv2.USAC_MAGSAC, self._thr, maxIters=self._iters, confidence=self._conf)
        if H is None or inl is None or not _invertible(H):     # a degenerate fit cannot be turned back onto the frame
            return None
        return H, int(inl.ravel().astype(bool).sum())


def _invertible(H):
    """True when the homography can be inverted (a fit on a featureless or blurred frame can come back singular)."""
    try:
        np.linalg.inv(np.asarray(H, np.float64))
        return True
    except np.linalg.LinAlgError:
        return False


def project(H_frame2rack, rack_pts):
    """Rack points -> frame points. (N, 2) -> (N, 2)."""
    return cv2.perspectiveTransform(np.asarray(rack_pts, np.float32).reshape(-1, 1, 2), np.linalg.inv(H_frame2rack)).reshape(-1, 2)


def rectify_to_bbox(frame_bgr, H_frame2rack, bbox):
    """The device cut out of the frame onto the rack box's own pixel grid, so every frame is judged on the same grid."""
    x1, y1, x2, y2 = bbox
    T = np.array([[1, 0, -x1], [0, 1, -y1], [0, 0, 1]], dtype=np.float64)
    return cv2.warpPerspective(frame_bgr, T @ H_frame2rack, (int(x2 - x1), int(y2 - y1)), flags=cv2.INTER_LANCZOS4)


def sharpness(device_bgr):
    """Mean squared Sobel gradient (Tenengrad) / grey-level variance: edge crispness with brightness and contrast removed."""
    g = cv2.cvtColor(device_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    var = float(g.var())
    if var < 1e-6:
        return 0.0
    gx, gy = cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1)
    return float(np.mean(gx * gx + gy * gy)) / var


def gate(quad, fw, fh, rack_area, cfg):
    """Geometry checks on the projected box: (reason or None, scale)."""
    scale = float(np.sqrt(max(cv2.contourArea(quad.astype(np.float32)), 0.0) / rack_area))
    margin = float(min(quad[:, 0].min(), quad[:, 1].min(), fw - quad[:, 0].max(), fh - quad[:, 1].max()))
    if not cv2.isContourConvex(quad.astype(np.float32)) or not (cfg.min_scale <= scale <= cfg.max_scale):
        return "degenerate geometry", scale
    if margin < cfg.border_margin_px:
        return "not fully inside the frame", scale
    return None, scale


def pick_top(records, k, min_gap):
    """Best score first (ties: the earlier frame), never two frames closer than min_gap."""
    chosen = []
    for r in sorted((r for r in records if r["eligible"]), key=lambda r: (-r["score"], r["frame"])):
        if all(abs(r["frame"] - c["frame"]) >= min_gap for c in chosen):
            chosen.append(r)
            if len(chosen) == k:
                break
    return chosen


def device_box(polygon, rack_hw):
    """A rack polygon -> its integer box (x1, y1, x2, y2), inside the rack image."""
    p = np.asarray(polygon, np.float64)
    h, w = rack_hw
    return (int(max(0, np.floor(p[:, 0].min()))), int(max(0, np.floor(p[:, 1].min()))),
            int(min(w, np.ceil(p[:, 0].max()))), int(min(h, np.ceil(p[:, 1].max()))))


def locate_devices(video, rack_bgr, devices, cfg=Config(), hint=None, progress=None):
    """devices: {key: rack polygon [[x, y], ...]}. One pass over the video for all of them.

    hint(frame_index) -> an approximate H frame -> rack, or None (no skipping for that frame).
    Returns ({key: {"frames": [{rank, frame, time_s, score, inliers, scale, refined, H, polygon_px}], "scanned",
    "eligible", "reasons", "warnings"}}, {frame_index: bgr of every chosen frame})."""
    cv2.setNumThreads(int(os.environ.get("RACKTRACK_CV_THREADS", os.environ.get("OMP_NUM_THREADS", "2"))))   # set here, not at import: other libraries reset it (the Mac ran on all cores)
    say = progress or (lambda m: None)
    devs = {}
    for key, poly in devices.items():
        bbox = device_box(poly, rack_bgr.shape[:2])
        if bbox[2] - bbox[0] < 4 or bbox[3] - bbox[1] < 4:
            continue
        x1, y1, x2, y2 = bbox
        devs[key] = {"bbox": bbox, "quad": np.float32([[x1, y1], [x2, y1], [x2, y2], [x1, y2]]), "poly": np.float32(poly).reshape(-1, 2),
                     "ref": RackReference(rack_bgr, bbox, pad=cfg.rack_roi_pad_px, n_features=cfg.sift_features, contrast=cfg.sift_contrast,
                                          ratio=cfg.ratio_test, ransac_thr=cfg.ransac_threshold_px, ransac_iters=cfg.ransac_max_iters,
                                          ransac_conf=cfg.ransac_confidence),
                     "records": [], "H": {}}
    n_workers = max(1, int(os.environ.get("RACKTRACK_CV_THREADS", "4")))
    prev_threads = cv2.getNumThreads()
    cv2.setNumThreads(1)        # OpenCV on this Mac runs on Grand Central Dispatch, which takes every core and ignores any count above 1:
    try:                        # so OpenCV works single-threaded here and the frames are shared over n_workers threads of our own
        return _locate(video, rack_bgr, devs, cfg, hint, say, n_workers)
    finally:
        cv2.setNumThreads(prev_threads)


def _locate(video, rack_bgr, devs, cfg, hint, say, n_workers):
    from concurrent.futures import ThreadPoolExecutor
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise ValueError(f"cannot open video {video}")
    fw, fh = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
    n_hint = max(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), 1)
    S = min(1.0, cfg.match_width / max(fw, fh))
    keys = list(devs)

    def scan(n, frame):
        """One frame against every device: the records for this frame (by device key) and the homographies that passed."""
        Hh = hint(n) if hint else None
        if Hh is not None and not _invertible(Hh):
            Hh = None
        recs, Hs, todo = {}, {}, []
        for key in keys:
            d = devs[key]
            rec = {"frame": n, "inliers": 0, "eligible": False, "reason": "no homography", "score": 0.0, "scale": 0.0}
            recs[key] = rec
            if Hh is not None:
                q = project(Hh, d["quad"])
                if min(q[:, 0].min(), q[:, 1].min(), fw - q[:, 0].max(), fh - q[:, 1].max()) < -cfg.hint_skip_px:
                    rec["reason"] = "outside the frame"
                    continue
            todo.append((key, d, rec))
        if todo:
            small = cv2.resize(frame, None, fx=S, fy=S, interpolation=cv2.INTER_AREA) if S < 1 else frame
            pts, desc = _features(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), cfg.sift_features, cfg.sift_contrast)
            for key, d, rec in todo:
                m = d["ref"].match(pts, desc, S)
                if m is None:
                    continue
                H, rec["inliers"] = m
                if rec["inliers"] < cfg.min_inliers:
                    rec["reason"] = "too few inliers"
                    continue
                x1, y1, x2, y2 = d["bbox"]
                reason, rec["scale"] = gate(project(H, d["quad"]), fw, fh, (x2 - x1) * (y2 - y1), cfg)
                rec["reason"] = reason or ""
                if reason is None:
                    rec["eligible"], rec["score"] = True, sharpness(rectify_to_bbox(frame, H, d["bbox"]))
                    Hs[key] = H
        return n, recs, Hs

    # pass 1: every frame, every device; frames are read in order and scanned n_workers at a time
    results = {}; pending = []; n = 0
    def drain(block):
        nonlocal pending
        keep = []
        for f in pending:
            if block or f.done():
                fn, recs, Hs = f.result(); results[fn] = (recs, Hs)
            else:
                keep.append(f)
        pending = keep
    stride = max(1, int(os.environ.get("RACKTRACK_SCAN_STRIDE", cfg.scan_stride)))
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        while True:
            if n % stride:                          # a skipped frame is only advanced past, not decoded in full
                if not cap.grab():
                    break
                n += 1; continue
            ok, frame = cap.read()
            if not ok:
                break
            pending.append(pool.submit(scan, n, frame)); n += 1
            if len(pending) >= 2 * n_workers:
                drain(False)
                if len(pending) >= 2 * n_workers:
                    pending[0].result(); drain(False)
            if n % 25 == 0:
                say(f"best frames: scanned {n}/{n_hint} frames")
        drain(True)
    for fn in sorted(results):                     # the records in frame order, as before
        recs, Hs = results[fn]
        for key in keys:
            devs[key]["records"].append(recs[key])
            if key in Hs:
                devs[key]["H"][fn] = Hs[key]
    cap.release()
    out, want = {}, {}
    for key, d in devs.items():
        d["chosen"] = pick_top(d["records"], cfg.top_k, cfg.min_frame_gap)
        for r in d["chosen"]:
            want.setdefault(r["frame"], []).append(key)
    # pass 2: re-read the chosen frames, refine at full resolution
    imgs = {}
    if want:
        cap = cv2.VideoCapture(str(video))
        for idx in range(max(want) + 1):
            if idx not in want:
                cap.grab()
                continue
            ok, frame = cap.read()
            if ok:
                imgs[idx] = frame
        cap.release()
    fine_feats = {}
    if cfg.refine and imgs:                        # the full-size features of every chosen frame, n_workers at a time
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            for idx, feats in zip(sorted(imgs), pool.map(lambda i: _features(cv2.cvtColor(imgs[i], cv2.COLOR_BGR2GRAY), cfg.refine_sift_features, cfg.refine_sift_contrast), sorted(imgs))):
                fine_feats[idx] = feats
    for key, d in devs.items():
        fine = (RackReference(rack_bgr, d["bbox"], pad=cfg.refine_roi_pad_px, n_features=cfg.refine_sift_features, contrast=cfg.refine_sift_contrast,
                              ratio=cfg.ratio_test, ransac_thr=cfg.refine_ransac_threshold_px, ransac_iters=cfg.refine_ransac_max_iters,
                              ransac_conf=cfg.refine_ransac_confidence) if cfg.refine else None)
        hits, warn = [], []
        for rank, r in enumerate(d["chosen"], 1):
            idx = r["frame"]
            if idx not in imgs:
                warn.append(f"could not re-read frame {idx}")
                continue
            H, refined = d["H"][idx], False
            if fine is not None:
                if idx not in fine_feats:
                    fine_feats[idx] = _features(cv2.cvtColor(imgs[idx], cv2.COLOR_BGR2GRAY), cfg.refine_sift_features, cfg.refine_sift_contrast)
                mf = fine.match(*fine_feats[idx], 1.0)
                if mf is not None and mf[1] >= cfg.min_inliers:
                    if np.linalg.norm(project(mf[0], d["quad"]) - project(H, d["quad"]), axis=1).max() <= cfg.refine_max_corner_shift_px:
                        H, refined = mf[0], True
            hits.append({"rank": rank, "frame": idx, "time_s": round(idx / fps, 3), "score": round(r["score"], 4), "inliers": r["inliers"],
                         "scale": round(r["scale"], 3), "refined": refined, "H": H, "polygon_px": project(H, d["poly"]).round(1).tolist()})
        eligible = sum(r["eligible"] for r in d["records"])
        if not eligible:
            warn.append("no frame shows the whole device inside the frame")
        elif len(hits) < cfg.top_k:
            warn.append(f"only {len(hits)} frame(s) satisfy the criteria (asked for {cfg.top_k})")
        reasons = {}
        for r in d["records"]:
            if not r["eligible"]:
                reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
        out[key] = {"frames": hits, "scanned": n, "eligible": eligible, "reasons": reasons, "warnings": warn}
    return out, imgs


def settings(cfg=Config()):
    return dict(asdict(cfg), algorithm_version=ALGORITHM_VERSION)



if __name__ == "__main__":
    print(__doc__)
