"""Capture-quality diagnostics: did the video give the stitcher what it needs?

Everything here is computed from data the pipeline already has (frame timestamps and sharpness, the keyframe
transforms, the join statistics), so it costs nothing extra. Each check produces a coded warning with a
plain-language message that says what was measured and what to do next time. The codes and thresholds are the
ones documented in the capture guide (integration/methodology).

Thresholds are starting values. They were set from the four sample videos and from synthetic degradations of
them (see tests/test_warnings.py), keeping a margin around the videos that stitched well. Change them in
`Thresholds` if your footage differs (for example a lower overlap limit if you always film slowly).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .compose import ComposeStats
from .video_io import VideoScan


@dataclass(frozen=True)
class Thresholds:
    min_duration_s: float = 6.0          # W01  shorter than this = rushed sweep
    min_short_side_px: int = 900         # W02  below this the small port numbers/labels are not readable
    max_speed_fh_s: float = 0.25         # W03  frame-heights per second (median); 0.25 = 4 s per screen height
    min_overlap: float = 0.35            # W04  vertical overlap between neighbouring keyframes
    max_blur_share: float = 0.08         # W05  share of frames below 60% of the video's median sharpness
    min_sharpness: float = 30.0          # W18  median sharpness (mean gradient, long side <= 1280 px); samples: 46 to 60
    max_scale_ratio: float = 2.0         # W06  rack size in the frame, largest / smallest across the video
    max_sideways_fw: float = 0.4         # W07  sideways travel in frame widths (samples: 0.04 to 0.08)
    max_roll_deg: float = 25.0           # W08  apparent rotation range in degrees (samples: 8 to 14)
    max_exposure_clip_share: float = 0.2  # W09  share of neighbour pairs needing more brightness correction than allowed (samples: up to 0.09)
    min_pair_inliers: int = 30           # W10  matched features between neighbouring keyframes
    min_edge_margin: float = 0.01        # W11/W12  free space above/below the cabinet, as a share of the stitched height (samples: 0.0 to 0.06)


THRESHOLDS = Thresholds()

# code -> (name, what to do). Messages are formatted with the measured values.
CATALOG = {
    "W01": ("VIDEO_TOO_SHORT", "The video is only {duration:.1f} s long. Sweeping the whole rack this quickly blurs the frames. Take 15 to 30 s."),
    "W02": ("LOW_RESOLUTION", "The video is {width}x{height}. Small port numbers and labels need at least 1080p. Film at 1080p or higher."),
    "W03": ("MOVING_TOO_FAST", "The camera moved at {speed:.2f} screen-heights per second (limit {limit:.2f}). Slow down: aim for 0.15 or less (the best sample videos ran at 0.07 to 0.09)."),
    "W04": ("LOW_OVERLAP", "Neighbouring sharp frames overlap by only {overlap:.0%} at the weakest point (limit {limit:.0%}). Move more slowly or more smoothly so every part of the rack is seen in several frames."),
    "W05": ("BLURRY_FRAMES", "{share:.0%} of the frames are much blurrier than the rest (limit {limit:.0%}). Slow down, hold the phone steadier and use good light."),
    "W06": ("DISTANCE_CHANGED", "The rack grew or shrank by a factor of {ratio:.1f} in the frame (limit {limit:.1f}). Keep a steadier distance from the rack."),
    "W07": ("SIDEWAYS_DRIFT", "The camera drifted sideways by {drift:.0%} of the frame width (limit {limit:.0%}). Stand centred in front of the rack and move straight down."),
    "W08": ("PHONE_ROLLED", "The phone rotated by {roll:.0f} degrees during the video (limit {limit:.0f}). Keep the phone upright."),
    "W09": ("EXPOSURE_CHANGED", "Brightness jumped between neighbouring frames: {share:.0%} of the pairs needed more correction than allowed (limit {limit:.0%}, worst {gain:.0%}). Lock exposure before filming and avoid pointing at windows or bright lights."),
    "W10": ("WEAK_ALIGNMENT", "Two neighbouring frames matched on only {inliers} features (limit {limit}). The stitch may be fragile; film more slowly, with more light and sharper frames."),
    "W11": ("RACK_TOP_CUT", "The top of the cabinet is at the edge of the stitched area. Start filming higher, with the lid and a little wall above it in view."),
    "W12": ("RACK_BOTTOM_CUT", "The bottom of the cabinet is at the edge of the stitched area. Keep filming until the base of the cabinet is well in view."),
    "W13": ("ALIGNMENT_RETRIED", "Aligning the frames only worked after using more of them (every {window} frames). The movement is probably too fast or jerky."),
    "W14": ("NO_VERTICAL_REFERENCE", "No reliable vertical edges were found, so the vertical straightening was skipped. Make sure the cabinet sides and rails are visible and in focus."),
    "W15": ("TILT_NOT_CORRECTED", "The horizontal tilt could not be measured or fully removed ({detail}). Check that the rack shelves and device edges are visible."),
    "W16": ("CABINET_NOT_FOUND", "The cabinet side panels were not found, so the image was cropped to the dark region instead (it may include the door or background). Make sure the whole cabinet front is in view and well lit."),
    "W19": ("PART_OF_VIDEO_IGNORED", "The first {start:.1f} s and the last {end:.1f} s of the {total:.1f} s video could not be aligned (almost no texture, such as plain wall or floor) and were left out. Keep the rack in view from start to end."),
    "W18": ("SOFT_FOCUS", "The video is soft overall (sharpness {sharp:.0f}, limit {limit:.0f}): out of focus, a dirty lens, heavy compression or too little light. Tap the rack to focus, clean the lens and add light."),
    "W17": ("IMAGE_DOWNSCALED", "The result was reduced to {factor:.0%} of full size to fit the memory budget. Raise max_canvas_megapixels if you have the memory, or lower the video resolution."),
}


@dataclass
class QualityWarning:
    code: str
    name: str
    message: str

    def __str__(self) -> str:
        return f"{self.code} {self.name}: {self.message}"


def warn(code: str, **values) -> QualityWarning:
    name, template = CATALOG[code]
    return QualityWarning(code, name, template.format(**values))


def blur_share(scores: np.ndarray, times: np.ndarray, window_s: float = 1.0, ratio: float = 0.6) -> float:
    """Share of frames much blurrier than their own neighbours.

    Each frame is compared only with the sharpest frames within +-window_s/2 seconds, which see the same
    content, so plain wall or floor (few edges) is not mistaken for blur the way a whole-video median would.
    """
    out = np.zeros(len(scores), bool)
    lo = 0
    hi = 0
    for i, t in enumerate(times):
        while times[lo] < t - window_s / 2:
            lo += 1
        while hi < len(times) and times[hi] <= t + window_s / 2:
            hi += 1
        out[i] = scores[i] < ratio * np.percentile(scores[lo:hi], 90)
    return float(out.mean())


def keyframe_motion(transforms: list[np.ndarray], frame_shape: tuple[int, int]):
    """Per keyframe: where the frame centre lands (x, y), the scale and the roll of its transform."""
    h, w = frame_shape[:2]

    def at(dx: float, dy: float):
        out = []
        for T in transforms:
            q = T @ np.array([w / 2 + dx, h / 2 + dy, 1.0])
            out.append(q[:2] / q[2])
        return np.array(out)

    centre = at(0, 0)
    jx = (at(1, 0) - at(-1, 0)) / 2
    jy = (at(0, 1) - at(0, -1)) / 2
    scale = np.sqrt(np.abs(jx[:, 0] * jy[:, 1] - jx[:, 1] * jy[:, 0]))
    roll = np.degrees(np.arctan2(jx[:, 1], jx[:, 0]))
    return centre, scale, roll


def capture_metrics(
    scan: VideoScan,
    keys: list[int],
    transforms: list[np.ndarray],
    frame_shape: tuple[int, int],
    pair_inliers: list[int],
    stats: ComposeStats,
) -> dict:
    """Numbers that describe how the video was captured (all dimensionless or in s, px, degrees)."""
    h, w = frame_shape[:2]
    centre, scale, roll = keyframe_motion(transforms, frame_shape)
    t = scan.times[np.asarray(keys)]
    mid = (scale[:-1] + scale[1:]) / 2
    step_y = np.abs(np.diff(centre[:, 1])) / (h * mid)            # vertical step in frame heights
    dt = np.maximum(np.diff(t), 1e-6)
    speed = step_y / dt
    med = float(np.median(scan.scores))
    return {
        "duration_s": float(scan.times[-1]),
        "frames": int(len(scan.scores)),
        "fps": float((len(scan.scores) - 1) / max(scan.times[-1], 1e-6)),
        "width": int(scan.original_shape[1]),
        "height": int(scan.original_shape[0]),
        "speed_median_fh_s": float(np.median(speed)),
        "speed_p95_fh_s": float(np.percentile(speed, 95)),
        "overlap_min": float(1 - step_y.max()),
        "overlap_median": float(1 - np.median(step_y)),
        "blur_share": blur_share(scan.scores, scan.times),
        "scale_ratio": float(scale.max() / scale.min()),
        "sideways_fw": float((centre[:, 0].max() - centre[:, 0].min()) / (w * np.median(scale))),
        "roll_range_deg": float(roll.max() - roll.min()),
        "min_pair_inliers": int(min(pair_inliers)),
        "exposure_max_gain": float(stats.max_gain_deviation),
        "exposure_clipped_pairs": int(stats.clipped_pairs),
        "exposure_clipped_share": float(stats.clipped_pairs / max(len(transforms) - 1, 1)),
        "sharpness_median": med,
    }


def check_capture(m: dict, th: Thresholds = THRESHOLDS) -> list[QualityWarning]:
    """Warnings about the footage itself (W01 to W10)."""
    out: list[QualityWarning] = []
    if m["duration_s"] < th.min_duration_s:
        out.append(warn("W01", duration=m["duration_s"]))
    if min(m["width"], m["height"]) < th.min_short_side_px:
        out.append(warn("W02", width=m["width"], height=m["height"]))
    if m["speed_median_fh_s"] > th.max_speed_fh_s:
        out.append(warn("W03", speed=m["speed_median_fh_s"], limit=th.max_speed_fh_s))
    if m["overlap_min"] < th.min_overlap:
        out.append(warn("W04", overlap=max(m["overlap_min"], 0.0), limit=th.min_overlap))
    if m["blur_share"] > th.max_blur_share:
        out.append(warn("W05", share=m["blur_share"], limit=th.max_blur_share))
    if m["scale_ratio"] > th.max_scale_ratio:
        out.append(warn("W06", ratio=m["scale_ratio"], limit=th.max_scale_ratio))
    if m["sideways_fw"] > th.max_sideways_fw:
        out.append(warn("W07", drift=m["sideways_fw"], limit=th.max_sideways_fw))
    if m["roll_range_deg"] > th.max_roll_deg:
        out.append(warn("W08", roll=m["roll_range_deg"], limit=th.max_roll_deg))
    if m["exposure_clipped_share"] > th.max_exposure_clip_share:
        out.append(warn("W09", share=m["exposure_clipped_share"], limit=th.max_exposure_clip_share, gain=m["exposure_max_gain"]))
    if m["sharpness_median"] < th.min_sharpness:
        out.append(warn("W18", sharp=m["sharpness_median"], limit=th.min_sharpness))
    if m["min_pair_inliers"] < th.min_pair_inliers:
        out.append(warn("W10", inliers=m["min_pair_inliers"], limit=th.min_pair_inliers))
    return out
