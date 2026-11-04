"""The public entry point: stitch_rack()."""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from . import crop, geometry, paths, quality
from .compose import DEFAULT_BUDGET_PIXELS, ComposeStats, CompositionError, compose, fit_budget
from .registration import RegistrationError, register_chain
from .video_io import VideoReadError, read_keyframes, scan_video, select_keyframes

log = logging.getLogger("rack_stitcher")

ProgressCallback = Callable[[str, float], None]


class RackStitchError(RuntimeError):
    """The video could not be turned into a rack image (message explains why)."""


@dataclass
class StitchResult:
    """What stitch_rack() returns."""

    output_path: Path                  # the saved rack image
    video_path: Path                   # the input video that was used
    image_size: tuple[int, int]        # (width, height) of the saved image
    frames_read: int                   # frames considered (from start_frame on)
    keyframes_used: int                # sharp frames that were stitched
    min_pair_inliers: int              # weakest alignment between neighbouring keyframes (higher is safer)
    vertical_straightened: bool        # False if no reliable vertical reference was found (see W14)
    vertical_status: str               # "corrected" (edges converged and were straightened), "parallel" (already straight), "not_found" or "off"
    horizontal_correction_deg: float   # shear applied to level the horizontals
    residual_tilt_deg: float | None    # remaining horizontal tilt (close to 0 is good; None = could not be measured)
    seconds: float
    warnings: list[str] = field(default_factory=list)        # one readable line each, e.g. "W03 MOVING_TOO_FAST: ..."
    warning_codes: list[str] = field(default_factory=list)   # the same, as codes: ["W03", ...]
    capture: dict = field(default_factory=dict)              # numbers describing how the video was captured

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["output_path"], d["video_path"] = str(self.output_path), str(self.video_path)
        return d


def stitch_rack(
    video: str | Path,
    output_dir: str | Path | None = None,
    *,
    input_dir: str | Path | None = None,
    output_name: str | None = None,
    start_frame: int = 0,
    target_keyframes: int = 36,
    max_frame_side: int | None = 2560,
    max_canvas_megapixels: float = DEFAULT_BUDGET_PIXELS / 1e6,
    auto_crop: bool = True,
    straighten: bool = True,
    level: bool = True,
    thresholds: quality.Thresholds = quality.THRESHOLDS,
    progress: ProgressCallback | None = None,
) -> StitchResult:
    """Turn a top-to-bottom video of a rack into one straight, full-height image of the rack.

    Parameters
    ----------
    video:
        File name inside the input folder (default ``videos/``), or a path.
    output_dir:
        Where to save the image (default ``data/stitched``; created if missing).
    input_dir:
        Folder used to look up a bare file name (default ``videos/``).
    output_name:
        Output file name. Default ``<video name>_rack.png``. ``.png``, ``.jpg`` are accepted.
    start_frame:
        Skip this many frames at the start (usually not needed).
    target_keyframes:
        Roughly how many sharp frames to stitch (8 or more). More = more robust, slower.
    max_frame_side:
        Frames longer than this (pixels) are shrunk on read to bound memory. ``None`` = never.
    max_canvas_megapixels:
        Memory budget for the stitched canvas. Bigger canvases are shrunk to fit (warning W17).
        Peak RAM is roughly 200 bytes per canvas pixel.
    auto_crop:
        Crop to the rack (cabinet side panel to side panel, lid to base) instead of returning the whole mosaic.
    straighten, level:
        Switch off the vertical / horizontal geometry corrections (not recommended).
    thresholds:
        Limits used for the capture-quality warnings (see ``rack_stitcher.quality.Thresholds``).
    progress:
        Optional ``callback(stage_name, fraction_0_to_1)`` for a progress bar. An exception raised by the
        callback is logged and ignored; it never stops the stitching.

    Returns
    -------
    StitchResult
        The output path, quality indicators, capture measurements and any warnings (coded W01 to W17).

    Raises
    ------
    FileNotFoundError  the video does not exist
    ValueError         an argument is out of range
    RackStitchError    the video cannot be read or stitched
    """
    if start_frame < 0:
        raise ValueError("start_frame must be 0 or more")
    if target_keyframes < 8:
        raise ValueError("target_keyframes must be at least 8")
    if max_frame_side is not None and max_frame_side < 320:
        raise ValueError("max_frame_side must be at least 320 pixels (or None)")
    if max_canvas_megapixels < 1:
        raise ValueError("max_canvas_megapixels must be at least 1")

    t0 = time.time()

    def report(stage: str, fraction: float) -> None:
        if progress is None:
            return
        try:
            progress(stage, fraction)
        except Exception:                                  # a broken progress bar must not break the stitch
            log.exception("progress callback failed (ignored)")

    video_path = paths.resolve_input(video, input_dir)
    out_path = paths.prepare_output(video_path, output_dir, output_name)
    found: list[quality.QualityWarning] = []
    log.info("Stitching %s", video_path)

    try:
        # 1. score every frame (low memory) and choose sharp keyframes
        report("scoring frames", 0.02)
        scan = scan_video(video_path, start_frame, max_frame_side)
        window = max(2, math.ceil(len(scan.scores) / target_keyframes))

        # 2. align + compose, retrying with denser keyframes if two neighbours cannot be aligned
        mosaic = valid = stats = None
        last_error: Exception | None = None
        for attempt in range(3):
            all_keys = select_keyframes(scan.scores, window)
            keys = all_keys
            report("reading keyframes", 0.10)
            frames = read_keyframes(video_path, keys, start_frame, max_frame_side)
            try:
                report("aligning frames", 0.25)
                chain = register_chain(frames)
                partial = not chain.complete
                if partial and window > 2 and attempt < 2:       # try denser keyframes before giving anything up
                    raise RegistrationError(chain.start or chain.stop, 0)
                keys = all_keys[chain.start : chain.stop]
                frames = frames[chain.start : chain.stop]
                transforms, inliers = chain.transforms, chain.inliers
                frame_shape = frames[0].shape[:2]
                frames, transforms, canvas_scale = fit_budget(frames, transforms, int(max_canvas_megapixels * 1e6))
                report("stitching", 0.45)
                mosaic, valid, stats = compose(frames, transforms)
                stats.canvas_scale = canvas_scale
                if attempt:
                    found.append(quality.warn("W13", window=window))
                if partial:
                    kept = scan.times[keys]
                    found.append(quality.warn("W19", start=float(kept[0]), end=float(scan.times[-1] - kept[-1]),
                                              total=float(scan.times[-1])))
                break
            except (RegistrationError, CompositionError) as exc:
                last_error = exc
                log.warning("attempt %d (every %d frames) failed: %s", attempt + 1, window, exc)
                if window <= 2:
                    break
                window = max(2, window // 2)
            finally:
                del frames
        if mosaic is None:
            raise RackStitchError(
                "Could not align the video frames. Make sure the camera moves smoothly down the rack "
                f"and the rack stays in view. Last error: {last_error}"
            )

        # capture-quality measurements and warnings (from data we already have)
        metrics = quality.capture_metrics(
            scan, keys, _unscaled(transforms, stats.canvas_scale), frame_shape, inliers, stats
        )
        found += quality.check_capture(metrics, thresholds)
        if stats.canvas_scale < 1.0:
            found.append(quality.warn("W17", factor=stats.canvas_scale))

        # 3. rough crop around the rack (with margin so the corrections below lose nothing)
        report("straightening", 0.75)
        if auto_crop:
            x0, y0, x1, y1 = crop.find_rack_bbox(mosaic, valid, margin=0.12)
            mosaic, valid = mosaic[y0:y1, x0:x1], valid[y0:y1, x0:x1]

        vertical_status = "off"
        if straighten:
            mosaic, valid, info = geometry.straighten_verticals(mosaic, valid)
            vertical_status = info["status"] if info else "not_found"
            if info is None:
                found.append(quality.warn("W14"))
        applied, residual = 0.0, geometry.horizontal_tilt(mosaic, valid)
        if level:
            report("levelling", 0.85)
            mosaic, valid, applied, residual = geometry.level_horizontals(mosaic, valid)
        if residual is None:
            found.append(quality.warn("W15", detail="too few straight edges to measure it"))
        elif abs(residual) > 1.0:
            found.append(quality.warn("W15", detail=f"{residual:+.1f} degrees remain"))

        # 4. final crop on the rack itself (side panel to side panel), trimmed of empty (black) corners
        if auto_crop:
            extent: dict = {}
            box = crop.find_rack_crop(mosaic, valid, extent)
            if box is None:
                found.append(quality.warn("W16"))
                box = crop.find_rack_bbox(mosaic, valid, margin=0.015)
            else:
                if extent["top_margin"] < thresholds.min_edge_margin:
                    found.append(quality.warn("W11"))
                if extent["bottom_margin"] < thresholds.min_edge_margin:
                    found.append(quality.warn("W12"))
            x0, y0, x1, y1 = crop.trim_to_valid(valid, box)
            mosaic = mosaic[y0:y1, x0:x1]

        report("saving", 0.95)
        if not cv2.imwrite(str(out_path), mosaic):
            raise RackStitchError(f"Could not write the output image: {out_path}")
    except VideoReadError as exc:
        raise RackStitchError(str(exc)) from exc
    except (cv2.error, MemoryError) as exc:
        raise RackStitchError(f"Image processing failed ({type(exc).__name__}): {exc}") from exc

    report("done", 1.0)
    found.sort(key=lambda w: w.code)
    result = StitchResult(
        output_path=out_path,
        video_path=video_path,
        image_size=(mosaic.shape[1], mosaic.shape[0]),
        frames_read=len(scan.scores),
        keyframes_used=len(keys),
        min_pair_inliers=int(min(inliers)),
        vertical_straightened=vertical_status in ("corrected", "parallel"),
        vertical_status=vertical_status,
        horizontal_correction_deg=round(applied, 2),
        residual_tilt_deg=None if residual is None else round(float(residual), 2),
        seconds=round(time.time() - t0, 1),
        warnings=[str(w) for w in found],
        warning_codes=[w.code for w in found],
        capture={k: (round(v, 3) if isinstance(v, float) else v) for k, v in metrics.items()},
    )
    for w in found:
        log.warning("%s", w)
    log.info("Saved %s (%dx%d) in %.1fs", out_path, *result.image_size, result.seconds)
    return result


def _unscaled(transforms: list[np.ndarray], canvas_scale: float) -> list[np.ndarray]:
    """Undo the budget shrink so measurements are in original frame pixels."""
    if canvas_scale == 1.0:
        return transforms
    S = np.diag([canvas_scale, canvas_scale, 1.0])
    return [np.linalg.inv(S) @ t @ S for t in transforms]
