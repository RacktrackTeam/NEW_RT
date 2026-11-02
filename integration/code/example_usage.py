"""Minimal example: stitch one video from integration/sample_vdo into integration/output.

Run from this folder:   python example_usage.py [video_name]
"""
import logging
import sys

from rack_stitcher import RackStitchError, stitch_rack

logging.basicConfig(level=logging.INFO, format="%(message)s")   # the main app normally configures logging itself

video = sys.argv[1] if len(sys.argv) > 1 else "vdo3_60fps.MOV"

try:
    result = stitch_rack(
        video,                                                     # a name inside integration/sample_vdo, or a path
        progress=lambda stage, fraction: print(f"  {fraction:4.0%}  {stage}"),
    )
except FileNotFoundError as exc:
    sys.exit(f"Input problem: {exc}")
except RackStitchError as exc:
    sys.exit(f"Could not build the rack image: {exc}")

print("\nSaved:", result.output_path)
print("Size :", result.image_size, "px (width x height)")
print("Info :", f"{result.keyframes_used} keyframes, horizontal tilt left {result.residual_tilt_deg:+.2f} deg, {result.seconds}s")
for w in result.warnings:
    print("Warning:", w)
