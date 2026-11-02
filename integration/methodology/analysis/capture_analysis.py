"""How were the sample videos captured?  Tracks the camera across every consecutive frame pair.

For each video it estimates, frame to frame, how the rack plane moved in the image (a similarity
transform from SIFT matches): vertical shift (camera speed), scale (camera moving closer/away),
horizontal shift (sideways drift) and rotation (roll). It also records per-frame sharpness and
brightness. Results go to capture_metrics.json (arrays + summary) for the figures and the report.

Run from this folder:  python capture_analysis.py
"""
import json
import sys
from multiprocessing import Pool
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                                   # .../integration
sys.path.insert(0, str(ROOT / "code"))
from rack_stitcher.video_io import sharpness, work_scale  # same sharpness measure the module uses

VIDEOS = ["vdo1.mp4", "vdo2.MOV", "vdo3_60fps.MOV", "vdo4_30fps.MOV"]
TRACK_SIDE = 640


def analyse(name: str) -> dict:
    cap = cv2.VideoCapture(str(ROOT / "sample_vdo" / name))
    sift = cv2.SIFT_create(1500)
    clahe = cv2.createCLAHE(2.0, (8, 8))
    bf = cv2.BFMatcher()
    t, sharp, bright = [], [], []
    tx, ty, sc, rot, inl = [], [], [], [], []
    prev = None
    W0 = H0 = None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        H0, W0 = frame.shape[:2]
        t.append(cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0)
        sharp.append(sharpness(frame, work_scale(frame.shape)))
        g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        bright.append(float(g[H0 // 5 : -H0 // 5, W0 // 5 : -W0 // 5].mean()))
        s = TRACK_SIDE / max(H0, W0)
        small = cv2.resize(g, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        kp, des = sift.detectAndCompute(clahe.apply(small), None)
        if prev is not None and des is not None and prev[1] is not None and len(kp) > 8 and len(prev[0]) > 8:
            m = [a for a, b in (z for z in bf.knnMatch(des, prev[1], k=2) if len(z) == 2) if a.distance < 0.75 * b.distance]
            if len(m) >= 8:
                p_now = np.float32([kp[a.queryIdx].pt for a in m])
                p_prev = np.float32([prev[0][a.trainIdx].pt for a in m])
                A, mask = cv2.estimateAffinePartial2D(p_now, p_prev, method=cv2.RANSAC, ransacReprojThreshold=3.0)
                if A is not None and mask.sum() >= 12:          # maps current -> previous frame
                    tx.append(float(A[0, 2]) / small.shape[1]); ty.append(float(A[1, 2]) / small.shape[0])
                    sc.append(float(np.hypot(A[0, 0], A[1, 0]))); rot.append(float(np.degrees(np.arctan2(A[1, 0], A[0, 0]))))
                    inl.append(int(mask.sum())); prev = (kp, des); continue
            tx.append(np.nan); ty.append(np.nan); sc.append(np.nan); rot.append(np.nan); inl.append(0)
        elif prev is not None:
            tx.append(np.nan); ty.append(np.nan); sc.append(np.nan); rot.append(np.nan); inl.append(0)
        prev = (kp, des)
    cap.release()
    t = np.array(t)
    # the transform maps current -> previous, so camera motion = -(translation of content)
    arr = lambda v: np.array([np.nan] + v)                      # frame 0 has no pair
    return dict(name=name, width=W0, height=H0, frames=len(t), t=t.tolist(),
                dx=arr(tx).tolist(), dy=arr(ty).tolist(), scale=arr(sc).tolist(), roll=arr(rot).tolist(),
                inliers=([0] + inl), sharpness=sharp, brightness=bright)


def summarise(d: dict) -> dict:
    t = np.array(d["t"]); dt = np.diff(t, prepend=t[0] - np.median(np.diff(t)))
    dy, dx = np.array(d["dy"]), np.array(d["dx"]); sc, roll = np.array(d["scale"]), np.array(d["roll"])
    ok = np.isfinite(dy)
    fill = lambda a, v=0.0: np.where(np.isfinite(a), a, v)
    cam_down = np.cumsum(fill(dy))                               # transform maps current->previous: dy>0 when content moves up, i.e. the camera moves down
    speed = np.abs(dy) / dt                                      # frame-heights per second
    speed = np.where(ok, speed, np.nan)
    cum_scale = np.exp(np.cumsum(np.log(fill(sc, 1.0))))
    cum_roll = np.cumsum(fill(roll)); cum_x = np.cumsum(fill(dx))
    sharp = np.array(d["sharpness"]); bright = np.array(d["brightness"])
    travel = float(cam_down[-1] - cam_down[0])
    sgn = np.sign(travel) or 1.0
    moving_back = float(np.nanmean(np.where(ok, (np.sign(dy) * sgn) < 0, np.nan)))      # share of frame pairs going the wrong way
    # smoothed speed (1 s median) to describe steadiness
    k = max(3, int(round(len(t) / (t[-1] - t[0]))))
    sm = np.array([np.nanmedian(speed[max(0, i - k // 2): i + k // 2 + 1]) for i in range(len(speed))])
    px_per_frame_1080 = np.abs(dy) * 1080                          # vertical shift per frame, in 1080-px-high frame units
    return dict(
        name=d["name"], resolution=f'{d["width"]}x{d["height"]}', frames=d["frames"], duration_s=float(t[-1] - t[0]),
        mean_fps=float((len(t) - 1) / (t[-1] - t[0])),
        travel_frame_heights=float(abs(travel)), direction="downwards" if travel > 0 else "upwards",
        speed_median_fh_s=float(np.nanmedian(speed)), speed_p95_fh_s=float(np.nanpercentile(speed, 95)),
        speed_steadiness_cv=float(np.nanstd(sm) / np.nanmean(sm)),
        px_per_frame_median=float(np.nanmedian(px_per_frame_1080)), px_per_frame_p95=float(np.nanpercentile(px_per_frame_1080, 95)),
        wrong_way_share=float(moving_back),
        scale_end_over_start=float(cum_scale[-1]), scale_max_over_min=float(cum_scale.max() / cum_scale.min()),
        roll_range_deg=float(cum_roll.max() - cum_roll.min()), roll_end_deg=float(cum_roll[-1]),
        sideways_range_frame_widths=float(cum_x.max() - cum_x.min()),
        blur_share_60=float(np.mean(sharp < 0.6 * np.median(sharp))), blur_share_50=float(np.mean(sharp < 0.5 * np.median(sharp))),
        sharp_median=float(np.median(sharp)), sharp_p10=float(np.percentile(sharp, 10)), sharp_p90=float(np.percentile(sharp, 90)),
        bright_median=float(np.median(bright)), bright_range_pct=float((np.percentile(bright, 95) - np.percentile(bright, 5)) / np.median(bright) * 100),
        pair_failure_share=float(np.mean(~ok[1:])), inliers_median=float(np.median(np.array(d["inliers"])[1:])),
        series=dict(t=t.tolist(), progress=(cam_down).tolist(), speed_smooth=sm.tolist(), scale=cum_scale.tolist(),
                    roll=cum_roll.tolist(), sideways=cum_x.tolist(), sharpness=sharp.tolist(), brightness=bright.tolist()),
    )


if __name__ == "__main__":
    with Pool(4) as pool:
        raw = pool.map(analyse, VIDEOS)
    out = [summarise(d) for d in raw]
    (HERE / "capture_metrics.json").write_text(json.dumps(out))
    keys = ["resolution", "frames", "duration_s", "mean_fps", "travel_frame_heights", "direction", "speed_median_fh_s", "speed_p95_fh_s",
            "speed_steadiness_cv", "px_per_frame_median", "px_per_frame_p95", "wrong_way_share", "scale_end_over_start", "scale_max_over_min",
            "roll_range_deg", "sideways_range_frame_widths", "sharp_median", "sharp_p10", "blur_share_60", "blur_share_50", "bright_range_pct", "pair_failure_share", "inliers_median"]
    print(f"{'metric':32s}" + "".join(f"{o['name'][:14]:>16s}" for o in out))
    for k in keys:
        print(f"{k:32s}" + "".join(f"{(o[k] if isinstance(o[k], str) else round(o[k], 3)):>16}" for o in out))
