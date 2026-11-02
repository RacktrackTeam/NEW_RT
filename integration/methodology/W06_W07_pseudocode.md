# W06 (distance changed) and W07 (sideways drift): reproducible logic

Source: `integration/code/rack_stitcher/quality.py` (`keyframe_motion`, `capture_metrics`, `check_capture`).
Both checks run **after** frame alignment. They need one 3x3 homography per keyframe.

---

## 0. Inputs

```
frames      : list of K sharp keyframes from the video (in time order), all of size w x h (pixels)
H[0..K-1]   : 3x3 homography per keyframe, mapping that keyframe's pixel (x, y, 1)
              into ONE common reference frame (the stitching canvas / first keyframe)
```

How the reference implementation gets `H` (any equivalent method works):

```
H[0] = identity
for i in 1..K-1:
    match SIFT features between frames[i-1] and frames[i]   (ratio test 0.75)
    P = findHomography(points_i -> points_i-1, RANSAC)      (maps frame i into frame i-1)
    H[i] = H[i-1] @ P                                       (chain into frame 0's coordinates)
```

Notes:
- A constant translation of the whole canvas does not change either metric.
- If the canvas was uniformly shrunk (memory budget), undo that first. The reference code uses the unscaled transforms.

---

## 1. Shared step: centre position and scale of each keyframe

```
function project(H, x, y):
    (X, Y, Z) = H @ (x, y, 1)
    return (X / Z, Y / Z)

for each keyframe i:
    cx, cy = w / 2, h / 2

    centre[i] = project(H[i], cx, cy)                    # where the frame centre lands on the canvas

    # local Jacobian at the centre, by central differences of 1 px
    jx = (project(H[i], cx + 1, cy) - project(H[i], cx - 1, cy)) / 2     # image x-axis on canvas (2-vector)
    jy = (project(H[i], cx, cy + 1) - project(H[i], cx, cy - 1)) / 2     # image y-axis on canvas (2-vector)

    scale[i] = sqrt( | jx.x * jy.y  -  jx.y * jy.x | )   # linear size factor (sqrt of local area change)
                                                         # 1.0 = drawn at original size, 0.5 = half size
```

---

## 2. W07 SIDEWAYS_DRIFT

```
xs           = [centre[i].x for i in 0..K-1]
frame_width  = w * median(scale)                         # one frame's width as drawn on the canvas

sideways_fw  = (max(xs) - min(xs)) / frame_width         # horizontal extent of camera path, in frame widths

if sideways_fw > 0.40:
    WARN "W07 SIDEWAYS_DRIFT: camera drifted sideways by {sideways_fw:.0%} of the frame width (limit 40%).
          Stand centred in front of the rack and move straight down."
```

Meaning: it measures the extent (leftmost to rightmost frame centre), not the total path. Swaying back and forth
inside a narrow band is fine; a steady slide in one direction triggers it.

---

## 3. W06 DISTANCE_CHANGED

```
scale_ratio = max(scale) / min(scale)

if scale_ratio > 2.0:
    WARN "W06 DISTANCE_CHANGED: the rack grew or shrank by a factor of {scale_ratio:.1f} in the frame (limit 2.0).
          Keep a steadier distance from the rack."
```

Why scale tracks distance: the canvas holds the rack at one fixed size. Camera closer means each frame sees a smaller
patch of rack, so it is drawn smaller on the canvas (scale goes down). Camera farther away means scale goes up.
A ratio of 2.0 is roughly "twice as close at one point as at another".

---

## 4. Thresholds and reference values

| Metric | Limit | Observed on our videos |
|---|---|---|
| `sideways_fw` (W07) | > 0.40 warns | good samples vdo1 to vdo4: 0.04 to 0.08; User_vdo1: 2.36, User_vdo2: 1.98 (frames were turned 90°, so the downward sweep read as sideways; after rotating the videos the warning disappeared) |
| `scale_ratio` (W06) | > 2.0 warns | no sample video triggered it |

---

## 5. Known limits

- **Keyframes only** (about 36 per video, roughly one per second). A quick jerk that returns before the next keyframe is not seen.
- **Tilting the phone** (pointing up or down instead of moving it) changes the local scale through perspective, so a large tilt adds to `scale_ratio` even at constant distance.
- **The video must be upright.** A video whose frames are rotated 90° with no rotation flag reports the whole sweep as sideways drift (W07). Check the main direction the picture moves in first: in an upright top-to-bottom video, the content moves up.
- **Post-processing only**, because it needs the full alignment. A live version can estimate the same quantities from frame-to-frame shifts (for example `phaseCorrelate`) or from the phone's motion sensors.

---

## 6. Minimal Python reference (numpy)

```python
import numpy as np

def keyframe_motion(H_list, w, h):
    def at(dx, dy):
        out = []
        for H in H_list:
            q = H @ np.array([w / 2 + dx, h / 2 + dy, 1.0])
            out.append(q[:2] / q[2])
        return np.array(out)
    centre = at(0, 0)
    jx = (at(1, 0) - at(-1, 0)) / 2
    jy = (at(0, 1) - at(0, -1)) / 2
    scale = np.sqrt(np.abs(jx[:, 0] * jy[:, 1] - jx[:, 1] * jy[:, 0]))
    return centre, scale

def w06_w07(H_list, w, h, max_sideways=0.40, max_scale_ratio=2.0):
    centre, scale = keyframe_motion(H_list, w, h)
    sideways_fw = (centre[:, 0].max() - centre[:, 0].min()) / (w * np.median(scale))
    scale_ratio = scale.max() / scale.min()
    return {
        "sideways_fw": float(sideways_fw), "W07": bool(sideways_fw > max_sideways),
        "scale_ratio": float(scale_ratio), "W06": bool(scale_ratio > max_scale_ratio),
    }
```
