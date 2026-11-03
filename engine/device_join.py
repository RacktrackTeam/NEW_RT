"""One device logic for step 3: our seam cues (device_logic.SCORE) and the team's joining model (src/fa_device_logic),
mixed per seam. No device-type model anywhere.

    P(boundary) = sigmoid(MIX_W * logit(1 - P_same_theirs) + (1 - MIX_W) * logit(P_boundary_ours) - MIX_TAU)
    a new device starts at the seam when P(boundary) >= 0.5; where the joining model has no pair for a seam, our
    logic decides alone.

Both parts see the same unit ladder (our rows) and the same occupancy. MIX_W and MIX_TAU were chosen on 509 tuning
photos neither part had seen (merged train photos outside the test set and outside their Trained_2511 batch), by
devices found exactly; then graded once on the user's test photos (942 after the 2026-10-14 clean-up, 932 with graded
devices, 5,848 devices):
    our logic alone 84.0 %, joining model alone 85.4 %, mixed 88.2 % devices exact (all photos; the joining model
    was trained on 479 of them); on the 453 photos neither part had seen: 82.4 / 82.5 / 85.5 % (photos with every
    device right 60.5 %). Stackers with more inputs (logistic, boosted trees) did no better on the unseen photos.
    With Shankar's device detector on top (src/device_model.py): 95.3 / 93.3 %.
Grades kept in results/devices/grade_devices_test_*.json (the test photos were removed on 2026-10-22).

The joining model's package was called device_logic too, so it lives in src/ as fa_device_logic."""
import importlib
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
import device_logic as DL   # noqa: E402

MIX_W, MIX_TAU = 0.5, -0.5
_pipe = None


def _logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return float(np.log(p / (1 - p)))


def their_package():
    """The team's joining model package (src/fa_device_logic)."""
    return importlib.import_module("fa_device_logic")


def pipeline():
    """The joining model (units model, CLIP and the two pair networks), loaded once."""
    global _pipe
    if _pipe is None:
        _pipe = their_package().DevicePipeline()
    return _pipe


def their_p_same(rgb, rows):
    """{k: P(same device)} for the seam above unit k, from the joining model on OUR unit boxes. rgb: H x W x 3."""
    pipe = pipeline()                                   # loads their package first: it registers fa_device_logic
    sp = importlib.import_module("fa_device_logic.slot_pipeline")
    slots = [tuple(int(round(v)) for v in r["box"]) for r in rows]
    keep = [k for k, s in enumerate(slots) if sp.slot_crop(rgb, s).shape[0] >= 4 and sp.slot_crop(rgb, s).shape[1] >= 8]
    f = pipe.describe(rgb, slots, [])
    _, pp = pipe.probabilities(f)
    out = {}
    for (a, b), p in zip(f["pairs"], pp):
        if keep[b] == keep[a] + 1:
            out[keep[b]] = float(p)
    return out


def seam_probs(bgr, rows, taken, feats=None, theirs=None):
    """P(a new device starts) per seam between two occupied units. feats: our cached seam cues ({k: cues});
    theirs: the joining model's cached {k: P(same)}; either is computed from the photo when not given."""
    feats = feats if feats is not None else DL.seam_features(bgr, rows, taken)
    theirs = theirs if theirs is not None else their_p_same(np.ascontiguousarray(bgr[:, :, ::-1]), rows)
    out = {}
    for k, f in feats.items():
        po = DL.seam_prob(f)
        if k in theirs:
            z = MIX_W * _logit(1.0 - theirs[k]) + (1.0 - MIX_W) * _logit(po) - MIX_TAU
            out[k] = float(1.0 / (1.0 + np.exp(-z)))
        else:
            out[k] = po
    return out


def cut(taken, new_device):
    """The occupied runs cut into devices (lists of unit indices, top to bottom) where new_device[k] is true for the
    seam above unit k."""
    devices = []
    for a, b in DL.runs_of(taken):
        cur = [a]
        for k in range(a + 1, b + 1):
            if new_device.get(k, False):
                devices.append(cur); cur = [k]
            else:
                cur.append(k)
        devices.append(cur)
    return devices


def group(bgr, rows, taken, feats=None, theirs=None):
    """Devices as lists of unit indices (top to bottom), the per-seam decisions and the probabilities."""
    probs = seam_probs(bgr, rows, taken, feats, theirs)
    new_device = {k: p >= 0.5 for k, p in probs.items()}
    return cut(taken, new_device), new_device, probs
