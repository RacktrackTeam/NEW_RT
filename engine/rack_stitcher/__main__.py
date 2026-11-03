"""Command line:  python -m rack_stitcher VIDEO [--all] [--output-dir DIR] [--input-dir DIR]"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from . import list_videos, stitch_rack
from .pipeline import RackStitchError


def summary(r) -> str:
    c = r.capture
    lines = [
        f"{r.video_path.name}  ->  {r.output_path}",
        f"  image {r.image_size[0]}x{r.image_size[1]} px, {r.seconds} s, {r.keyframes_used} keyframes, vertical: {r.vertical_status}, tilt left: {r.residual_tilt_deg}",
        f"  captured: {c['width']}x{c['height']}, {c['duration_s']:.1f} s, speed {c['speed_median_fh_s']:.2f} screen-heights/s, "
        f"blur {c['blur_share']:.0%}, rack size change x{c['scale_ratio']:.2f}, weakest alignment {c['min_pair_inliers']} matches",
    ]
    if r.warnings:
        lines.append(f"  {len(r.warnings)} warning(s):")
        lines += [f"    {w}" for w in r.warnings]
    else:
        lines.append("  no warnings: the capture looks good")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rack_stitcher", description="Stitch a rack video into one rack image.")
    p.add_argument("video", nargs="?", help="file name in the input folder (default samples/video) or a path")
    p.add_argument("--all", action="store_true", help="process every video in the input folder")
    p.add_argument("--input-dir", help="input folder (default samples/video)")
    p.add_argument("--output-dir", help="output folder (default data/stitched)")
    p.add_argument("--output-name", help="output file name (single video only)")
    p.add_argument("--start-frame", type=int, default=0)
    p.add_argument("--keyframes", type=int, default=36, help="target number of stitched keyframes")
    p.add_argument("--no-crop", action="store_true", help="return the whole mosaic instead of the rack only")
    p.add_argument("--json", action="store_true", help="print the full result as JSON instead of a readable summary")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(message)s")

    if a.all:
        videos = [v.name for v in list_videos(a.input_dir)]
    elif a.video:
        videos = [a.video]
    else:
        p.error("give a video name or use --all")

    failed = 0
    for v in videos:
        try:
            r = stitch_rack(v, a.output_dir, input_dir=a.input_dir, output_name=None if a.all else a.output_name,
                            start_frame=a.start_frame, target_keyframes=a.keyframes, auto_crop=not a.no_crop)
            print(json.dumps(r.as_dict(), indent=2) if a.json else summary(r))
        except (RackStitchError, FileNotFoundError) as exc:
            failed += 1
            print(f"{v}: FAILED - {exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
