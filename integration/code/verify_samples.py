"""Check that the four sample videos still stitch cleanly with the single, fixed setting.

Run from this folder:  python verify_samples.py
Exit code 0 = all checks pass.  Output goes to a temporary folder (integration/output is untouched).

Checks per video: no processing warnings (capture-quality warnings W01-W12 are expected for the weaker samples), verticals handled, remaining horizontal tilt under 0.5 degrees,
at least 20 matched features between every pair of neighbouring keyframes, a plausible rack shape.
"""
import sys
import tempfile

from rack_stitcher import list_videos, stitch_rack

bad = 0
with tempfile.TemporaryDirectory() as tmp:
    for video in list_videos():
        r = stitch_rack(video.name, tmp)
        w, h = r.image_size
        checks = {
            "processing warnings absent (W13-W16, W19)": not (set(r.warning_codes) & {"W13", "W14", "W15", "W16", "W19"}),
            "verticals handled": r.vertical_status in ("corrected", "parallel"),
            "horizontals level (<0.5 deg)": abs(r.residual_tilt_deg) < 0.5,
            "alignment (>=20 matches)": r.min_pair_inliers >= 20,
            "rack shape (0.35 < w/h < 0.95)": 0.35 < w / h < 0.95,
        }
        failed = [name for name, ok in checks.items() if not ok]
        bad += bool(failed)
        print(f"{video.name:16s} {w}x{h}  {'OK' if not failed else 'FAILED: ' + ', '.join(failed)}  ({r.seconds}s)")
sys.exit(1 if bad else 0)
