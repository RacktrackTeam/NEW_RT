"""rack_stitcher: turn a top-to-bottom video of a server rack into one image of the rack.

    from rack_stitcher import stitch_rack
    result = stitch_rack("vdo3_60fps.MOV")      # reads integration/sample_vdo, writes integration/output
    print(result.output_path)
"""
from .paths import DEFAULT_INPUT_DIR, DEFAULT_OUTPUT_DIR, list_videos
from .pipeline import RackStitchError, StitchResult, stitch_rack
from .quality import CATALOG as WARNING_CATALOG, THRESHOLDS, Thresholds

__all__ = [
    "stitch_rack",
    "StitchResult",
    "RackStitchError",
    "list_videos",
    "Thresholds",
    "THRESHOLDS",
    "WARNING_CATALOG",
    "DEFAULT_INPUT_DIR",
    "DEFAULT_OUTPUT_DIR",
]
__version__ = "1.0.0"
