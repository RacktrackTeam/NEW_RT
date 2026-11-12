"""Other ways to find rack units from the photo alone, each graded like the main ladder.

Every method here returns rows in the unit_grid format (u01 at the bottom) plus an info
dict, exactly like unit_logic.find_units, so scripts/units/eval_unit_logic.py can grade them
and the report can show them side by side. They share the rack width and the device
lines from unit_logic where that makes sense, and differ in the core idea:

  holes      the rail's mounting holes (three per unit) found as small square blobs in the
             rail strips; the unit boundary is the middle of the short gap in each triplet
  lsd        device lines from OpenCV's Line Segment Detector instead of the edge-density
             mask, then the same grid fit
  dp         boundaries chosen by dynamic programming over the edge profile: the chain of
             rows one unit apart (plus or minus 12 %) with the most edge energy
  autocorr   the periodicity idea with the physical prior: pitch from the autocorrelation
             of the edge profile, searched only near 0.0953 x width; phase by vote
  rectify    perspective first: the two rails found as long vertical segments, the image
             warped so they are vertical and parallel, then the main ladder
  ocr        the unit numbers printed on many rails read with EasyOCR (a general OCR
             library, not our model) and a line fitted through them
  grid+holes the main ladder's grid with the mounting space taken from the hole column
  vote       several methods run; the ladder that the most device edges agree with wins
"""
import pathlib

import cv2
import numpy as np

import unit_logic as ul

DP_STEP_TOL = 0.07           # a unit may be this much taller or shorter than the fitted pitch, step to step


# ---------------------------------------------------------------- shared pieces


def rows_from_lines(lines, x_left, x_right, f, shape):
    """Unit lines (work-copy pixels, top to bottom) -> rows in the original image, u01 at the bottom."""
    H, W = shape[:2]
    count = len(lines) - 1
    out = []
    for i in range(count):
        y1 = int(round(max(0, lines[i] * f)))
        y2 = int(round(min(H, lines[i + 1] * f)))
        if y2 - y1 <= 0:
            continue
        xl, xr = int(round(x_left * f)), int(round(min(W - 1, x_right * f)))
        out.append({"label": f"u{count - i:02d}", "box": [xl, y1, xr, y2],
                    "center": [(xl + xr) // 2, (y1 + y2) // 2], "center_y": float((y1 + y2) / 2)})
    return out


# ---------------------------------------------------------------- 1. rail holes
HOLE_SIZE_U = (0.08, 0.40)      # a mounting hole, cage nut or screw head is this big, in units
HOLE_STRIP = (-0.09, 0.05)      # strip over the rail, relative to the rail x, in rack widths


# ---------------------------------------------------------------- 2. line segment detector


# ---------------------------------------------------------------- 3. dynamic programming


def row_score(small, mask, x_left, x_right):
    """A richer per-row boundary score (after Prasanna's step-3 idea): horizontal-edge
    density, brightness step and colour step across the rack opening, each scaled to its
    own 95th percentile, weighted 0.6 / 0.25 / 0.15."""
    lab = cv2.cvtColor(small[:, x_left:x_right], cv2.COLOR_BGR2LAB).astype(np.float32)
    rows = lab.mean(axis=1)                      # per row: L, a, b
    edge = mask[:, x_left:x_right].mean(axis=1).astype(np.float64)
    bright = np.abs(np.gradient(rows[:, 0]))
    colour = np.hypot(np.gradient(rows[:, 1]), np.gradient(rows[:, 2]))

    def norm(v):
        s = np.percentile(v, 95)
        return np.clip(v / s, 0, 1) if s > 1e-6 else v * 0

    E = 0.6 * norm(edge) + 0.25 * norm(bright) + 0.15 * norm(colour)
    return np.convolve(E, np.ones(3) / 3, mode="same")


def _dp_chain(E, lo, hi, q0, tol=0.12, bonus=0.08):
    """The chain of rows one unit apart (within tol) between lo and hi with the most score."""
    step_lo, step_hi = int(round((1 - tol) * q0)), int(round((1 + tol) * q0))
    n = hi - lo + 1
    dp = E[lo:hi + 1].copy()
    back = np.full(n, -1, int)
    for i in range(n):
        j0, j1 = max(0, i - step_hi), i - step_lo
        if j1 < 0:
            continue
        seg = dp[j0:j1 + 1]
        j = int(np.argmax(seg)) + j0
        if dp[j] + bonus + E[lo + i] > dp[i]:
            dp[i] = dp[j] + bonus + E[lo + i]
            back[i] = j
    i = int(np.argmax(dp))
    chain = []
    while i >= 0:
        chain.append(lo + i)
        i = back[i]
    return chain[::-1]


# ---------------------------------------------------------------- 4. autocorrelation with the prior


# ---------------------------------------------------------------- 5. perspective first


# ---------------------------------------------------------------- 6. printed unit numbers (EasyOCR)


# ---------------------------------------------------------------- 7. combinations


# ---------------------------------------------------------------- 8. combined pipeline, rule by rule


# ---------------------------------------------------------------- 9. straighten + rack/rail boxes from the units model
def model_frame(bgr):
    """Only the 'Rack' and 'rail' boxes of weights/rack_unit_finder.pt (its unit boxes are NOT used), in image pixels."""
    import unit_grid as ug
    model = ug.load_model(str(pathlib.Path(__file__).resolve().parent.parent / "models/weights/rack_unit_finder.pt"))
    small, f = ug.shrink_for_inference(bgr)
    res = model.predict(small, conf=0.25, imgsz=768, verbose=False)
    _, racks, rails = ug._unit_rows_from_result(res[0], model.names)
    return [[v * f for v in r] for r in racks], [[v * f for v in r] for r in rails]


# ---------------------------------------------------------------- 10. smooth-pitch dynamic programming (follows perspective)
FLEX_STEP = 0.03        # the unit height may change by at most this share from one unit to the next
FLEX_RANGE = 0.25       # ... and by at most this share overall from the first guess


def _dp_flex(E, lo, hi, q0, step=FLEX_STEP, rng=FLEX_RANGE, bonus=0.08):
    """Chain of rows with the most score where consecutive gaps change SMOOTHLY: the gap after a
    row is within +-step of the gap before it, and every gap within +-rng of q0. State = (row, gap)."""
    gaps = np.unique(np.round(q0 * np.exp(np.linspace(np.log(1 - rng), np.log(1 + rng), 61))).astype(int))
    gaps = gaps[gaps >= 3]
    G = len(gaps)
    n = hi - lo + 1
    NEG = -1e9
    best = np.full((n, G), NEG)
    back = np.full((n, G, 2), -1, int)
    seg = E[lo:hi + 1]
    # a chain may start at any row with any gap
    for g in range(G):
        best[:, g] = seg
    # allowed previous gaps for each gap: within +-step
    prev_g = [np.flatnonzero(np.abs(gaps / gaps[g] - 1) <= step + 1e-9) for g in range(G)]
    for i in range(n):
        for g in range(G):
            j = i - gaps[g]
            if j < 0:
                continue
            cand = best[j, prev_g[g]]
            m = int(np.argmax(cand))
            # the bonus pays per unit of HEIGHT covered, not per step, so small gaps gain nothing
            v = cand[m] + bonus * gaps[g] / q0 + seg[i]
            if v > best[i, g]:
                best[i, g] = v
                back[i, g] = (j, prev_g[g][m])
    i, g = np.unravel_index(int(np.argmax(best)), best.shape)
    chain = []
    while i >= 0:
        chain.append(lo + i)
        i, g = back[i, g]
    return chain[::-1]


