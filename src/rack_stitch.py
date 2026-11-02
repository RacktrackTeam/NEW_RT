"""Step 0 of a rack video: stitch the video into one picture of the whole rack (the stitched rack image every later
step is placed on). The video is the only input; everything after this step (src/rack_video.py) is unchanged.

The stitching is the teammate's rack_stitcher (src/rack_stitcher, used as delivered): the sharpest frames, aligned
on the rack plane, joined along invisible seams, straightened and cropped to the cabinet, with fixed settings for every
video. It also checks the filming (speed, overlap, blur, resolution, drift, ...) and reports coded warnings.

    stitch(video_path, out_path, progress=None) -> dict   (writes the picture; returns what the stitcher measured)
"""
import os
import pathlib

import cv2

from rack_stitcher import WARNING_CATALOG, RackStitchError, stitch_rack

if os.environ.get("OMP_NUM_THREADS"):             # this Mac overheats: OpenCV keeps to the same thread cap
    cv2.setNumThreads(int(os.environ["OMP_NUM_THREADS"]))


class NotReady(RuntimeError):
    """The video cannot be stitched (unreadable, too short, frames that do not align)."""


def available():
    return True


def sweep_motion(video_path, samples=24, width=320):
    """Which way the picture moves while the camera sweeps: the median shift (dx, dy) between a frame and the frame
    about a sixth of a second later, at `samples` points through the video, by phase correlation on small grey frames.
    Filming top to bottom moves the picture up (dy < 0)."""
    import numpy as np
    cap = cv2.VideoCapture(str(video_path))
    n, fps = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0), cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(fps / 6))
    if n < step + 2:
        cap.release()
        return 0.0, 0.0
    def grey(i):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, fr = cap.read()
        return cv2.cvtColor(cv2.resize(fr, (width, int(fr.shape[0] * width / fr.shape[1]))), cv2.COLOR_BGR2GRAY).astype(np.float32) if ok else None
    shifts = []
    for i in sorted({int(i) for i in np.linspace(0, n - 1 - step, samples)}):
        a, b = grey(i), grey(i + step)
        if a is not None and b is not None and a.shape == b.shape:
            (dx, dy), resp = cv2.phaseCorrelate(a, b)
            if resp > 0.05:
                shifts.append((dx, dy))
    cap.release()
    if not shifts:
        return 0.0, 0.0
    m = np.array(shifts)
    return float(np.median(m[:, 0])), float(np.median(m[:, 1]))


def upright(video_path, out_dir, from_guide=False):
    """The video to analyse, upright. The phone capture guide saves its takes turned a quarter turn (the scene on its
    side, the sweep running sideways); such a video is turned back, so the sweep runs top to bottom, and written as
    out_dir/video_upright.avi (FFV1, lossless: the turn adds nothing to the picture). The guide always turns anticlockwise, so its takes
    (from_guide) are turned back clockwise; any other video is turned so its picture moves up. Returns (path,
    {"turned": None | "clockwise" | "anticlockwise", "motion": [dx, dy]}); a video that already sweeps up or down is
    returned as it is."""
    dx, dy = sweep_motion(video_path)
    info = {"turned": None, "motion": [round(dx, 2), round(dy, 2)]}
    if abs(dx) <= 1.5 * abs(dy) or abs(dx) < 0.5:          # already a vertical sweep (or no clear motion): keep it
        return pathlib.Path(video_path), info
    # the picture moving left means the scene was turned anticlockwise: turn it back clockwise, and the other way round,
    # so that the picture moves up, as in a top-to-bottom sweep
    turn, info["turned"] = (cv2.ROTATE_90_CLOCKWISE, "clockwise") if (from_guide or dx < 0) else (cv2.ROTATE_90_COUNTERCLOCKWISE, "anticlockwise")
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    out = pathlib.Path(out_dir) / "video_upright.avi"
    vw = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"FFV1"), fps, (h, w))
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        vw.write(cv2.rotate(fr, turn))
    cap.release(); vw.release()
    return out, info


def stitch(video_path, out_path, progress=None):
    """Stitch `video_path` into `out_path` (PNG). progress(text) gets e.g. "stitching: aligning the frames (40 %)".
    Returns the stitcher's report: size, frames read and used, alignment strength, straightening, time, the capture
    measurements and the warnings ({code, name, text})."""
    out_path = pathlib.Path(out_path); out_path.parent.mkdir(parents=True, exist_ok=True)
    say = (lambda stage, frac: progress(f"stitching: {stage} ({frac:.0%})")) if progress else None
    try:
        r = stitch_rack(str(video_path), out_path.parent, output_name=out_path.name, progress=say)
    except (RackStitchError, FileNotFoundError) as ex:
        raise NotReady(str(ex)) from ex
    d = r.as_dict()
    warnings = []
    for code, line in zip(r.warning_codes, r.warnings):
        name = WARNING_CATALOG.get(code, (code, ""))[0]
        text = line.split(":", 1)[1].strip() if ":" in line else line
        warnings.append({"code": code, "name": name, "text": text})
    keep = ("image_size", "frames_read", "keyframes_used", "min_pair_inliers", "vertical_status", "vertical_straightened",
            "horizontal_correction_deg", "residual_tilt_deg", "seconds", "capture")
    return dict({k: d.get(k) for k in keep}, warnings=warnings, file=out_path.name)
