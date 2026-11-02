# rack_stitcher

Turns a **top-to-bottom video of a server rack** into **one straight, full-height image of the rack**.
No single frame shows the whole rack; the function finds the sharp frames, aligns them, joins them
along invisible seams, straightens the result and crops to the rack.

## Folder layout

```
integration/
  code/            <- this package (rack_stitcher/), requirements.txt, example_usage.py
  sample_vdo/      <- default INPUT folder (videos)
  output/          <- default OUTPUT folder (rack images)
```

## One setting for every video

There is a single pipeline with fixed settings: nothing is tuned per video. The number of keyframes, the
vertical and horizontal corrections and the crop are all worked out from the video itself, so a new video
is processed exactly like the samples.

**Relation to the report images.** The report images were made with a few hand-picked values per video
(start frame, keyframe spacing, crop window, levelling amount). The single-setting output is very close but
not pixel-identical. Measured on the four samples, on the area both images cover: correlation 0.83 to 0.94
(structural similarity 0.67 to 0.80), and the crops overlap 72% to 100%. The remaining difference is mostly the
crop: the module crops from the cabinet's left side panel to its right side panel, so an open door is left out,
whereas some hand crops kept a slice of it. `vdo2` differs most (its report image skipped the first 45 frames).
The scripts that made the report images are still in the project root (`rack_stitch3.py`, `rectify.py`,
`finalize.py`) if an exact replay is ever needed.

`python tests/run_tests.py` runs 26 self-checks on synthetic videos with a known camera path (about 10 minutes). The methodology and filming guide, including every warning, is in `integration/methodology/Methodology_Report.html`.

`python verify_samples.py` stitches the four samples and checks the quality indicators (no warnings, verticals
straightened, horizontals level, enough feature matches).

## Quick start

```python
import sys
sys.path.insert(0, "/path/to/integration/code")   # or install/copy the rack_stitcher folder

from rack_stitcher import stitch_rack

result = stitch_rack("vdo3_60fps.MOV")   # reads integration/sample_vdo/vdo3_60fps.MOV
print(result.output_path)                # integration/output/vdo3_60fps_rack.png
```

Command line (from the `code` folder):

```bash
python -m rack_stitcher vdo3_60fps.MOV        # one video
python -m rack_stitcher --all                 # every video in sample_vdo
python verify_samples.py                      # check the four samples still stitch cleanly
python example_usage.py vdo1.mp4              # example with a progress printout
```

## The function

```python
stitch_rack(video, output_dir=None, *, input_dir=None, output_name=None, start_frame=0,
            target_keyframes=36, max_frame_side=2560, auto_crop=True, straighten=True,
            level=True, max_canvas_megapixels=40, thresholds=THRESHOLDS, progress=None) -> StitchResult
```

| Argument | Meaning |
|---|---|
| `video` | A file name inside `sample_vdo`, or any path to a video. |
| `output_dir` | Where to save. Default `integration/output` (created if missing). |
| `input_dir` | Folder used to look up a bare file name. Default `integration/sample_vdo`. |
| `output_name` | Output file name. Default `<video name>_rack.png` (`.png` / `.jpg`). |
| `start_frame` | Skip this many frames at the start. Default 0 (usually not needed). |
| `target_keyframes` | Roughly how many sharp frames to stitch (default 36). More = more robust, slower. |
| `max_frame_side` | Shrink frames longer than this on read, to bound memory (default 2560 px; `None` = never). |
| `auto_crop` | Crop to the rack (cabinet side panel to side panel, lid to just below the base). `False` returns the whole mosaic with wall and floor. |
| `straighten`, `level` | Switch off the vertical / horizontal geometry corrections (not recommended). |
| `max_canvas_megapixels` | Memory budget for the stitched canvas (default 40; peak RAM is about 200 bytes per pixel). Larger results are shrunk and warn with W17. |
| `thresholds` | Limits for the capture-quality warnings (`rack_stitcher.Thresholds`). |
| `progress` | Optional `callback(stage_name, fraction_0_to_1)` for a progress bar. |

### What it returns: `StitchResult`

`output_path`, `video_path`, `image_size` (width, height), `frames_read`, `keyframes_used`,
`min_pair_inliers` (weakest alignment between neighbouring frames; higher is safer. The samples gave 27 to
158, and the lowest, 27, was also the weakest result), `vertical_straightened`, `horizontal_correction_deg`, `residual_tilt_deg`
(should be close to 0), `seconds`, `warnings` (readable lines such as `W03 MOVING_TOO_FAST: ...`), `warning_codes` (`["W03", ...]`), `capture` (measured speed, overlap, blur, rack size, drift, rotation, alignment strength), `vertical_status` (`corrected`, `parallel`, `not_found`, `off`). `residual_tilt_deg` is `None` when the tilt could not be measured.
`result.as_dict()` gives a JSON-friendly dict.

### Errors

| Exception | When |
|---|---|
| `FileNotFoundError` | The video does not exist (checked as a path, then inside `input_dir`). |
| `RackStitchError` | Cannot open or read the video, too few frames, frames cannot be aligned, canvas diverged, or the image cannot be written. The message says what to do. |

Anything else is a bug. The module logs through the standard `logging` logger named `rack_stitcher`
(it never configures logging itself).

## Integration notes

* **Blocking call.** It takes about 10 to 20 s per video on a laptop (measured: 11 s for the 720p
  sample, 19 s for the 1080p samples). Run it in a worker thread/process or a job queue, not on a UI thread.
  Use `progress=` to report status.
* **Memory.** Peak RAM measured at 1.0 to 2.0 GB for the samples; roughly 200 bytes per mosaic pixel.
  4K video is shrunk to `max_frame_side` on read to keep this bounded.
* **Thread safety.** No global state; separate calls can run in parallel (each needs its own RAM).
* **Dependencies.** Only `opencv-python` and `numpy` (see `requirements.txt`). Tested with Python 3.12,
  OpenCV 4.13, NumPy 2.5.

## How it works (one line per stage)

1. `video_io`: score every frame for sharpness (low memory), keep the sharpest frame per time window.
2. `registration`: SIFT features between neighbouring keyframes, homography on the rack plane, chained.
   If two neighbours cannot be aligned it retries with denser keyframes.
3. `compose`: each pixel comes from one frame; seams follow the minimum-difference path (no ghosting).
4. `crop`: find the cabinet (large, dark, neutral). A rough crop first; after straightening, the final crop runs
   from its left to its right side panel (found from the dark-pixel profile of each column, so an open door is
   excluded) and from the lid to just below the base.
5. `geometry`: send the vertical vanishing point to infinity, then level the horizontals with a shear.

## Filming guidance (what gives the best result)

Keep the phone upright and facing the rack, and lower it **straight down** (for example by bending the
knees) at a constant distance of about 2 to 3 m, slowly and smoothly, with the whole rack width in view,
good light, the door open, and focus/exposure locked. Fast moves (motion blur) and walking toward the rack
(changing scale) give weaker results.

## Known limits

* The crop follows the cabinet's side panels; if that structure cannot be found (unusual rack or lighting) it
  falls back to the dark region with a warning, which may include an open door and some wall/floor.
* The **lid and top label** sit in front of the rack plane, so they can tilt slightly after the
  equipment is levelled. The equipment itself is correct.
* Cables in front of the panels can show a small step (a few pixels) where a seam crosses them.
* Needs a roughly top-to-bottom camera motion with the rack visible throughout; a video that is blank,
  static or has no overlap between frames raises `RackStitchError`.
* Small port numbers and labels are readable only if the source video is sharp enough (1080p or better
  and a slow, steady pass). 720p video reads the large label but not the small port numbers.
