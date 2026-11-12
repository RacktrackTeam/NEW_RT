"""Step 3, logic + model: Shankar's device detector draws a box around every device it sees, and the
one logic (engine/device_join.py: our seam score mixed with the team's joining model) decides where the detector is silent.

A unit belongs to the detector box that covers at least COVER of it (boxes under SCORE are dropped). At every seam
between two occupied units:
    both units in the same box       -> same device
    both units in different boxes    -> a new device starts
    a unit without a box             -> the logic decides (P(new device) >= 0.5)
SCORE and COVER were fixed before scoring; nothing was tuned on the test photos.

The detector: DEIMv2 with a DINOv3 vit_tinyplus backbone, one class (device), trained by Shankar; weights in
weights/device_detector_deimv2.pth (delivered as best_stg2.pth). Running it needs the DEIMv2 code
(https://github.com/Intellindust-AI-Lab/DEIMv2): export DEIMV2_DIR=/path/to/DEIMv2. Its boxes on the test
photos are cached in results/devices/devmodel_<set>.json, so grading and the portal need neither the code nor the
weights. Grades kept in results/devices/grade_devices_test_*.json (the test photos were removed on 2026-10-22).

Graded once on the user's test photos (942 after the 2026-10-14 clean-up, 932 with graded devices, 5,848 devices), our
unit ladder, occupancy from the labels:
    logic + model 95.3 % of devices found exactly, 85.1 % of photos with every device right;
    the logic alone 88.2 % / 64.2 %; the detector alone 94.8 % / 78.7 %;
    on the 453 photos outside the team's training batch: 93.3 % (logic 85.5 %, detector alone 92.8 %).
The team's own measurement on its unit ladder (6,686 devices): 93.0 % combined, 88.1 % logic only, 92.6 % detector only.
"""
import json
import os
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
import device_join as DJ    # noqa: E402
import device_logic as DL   # noqa: E402

WEIGHTS = ROOT / "models" / "weights" / "device_detector_deimv2.pth"
DEIMV2_DIR = os.environ.get("DEIMV2_DIR") or str(pathlib.Path(__file__).resolve().parent / "third_party" / "DEIMv2")   # the DEIMv2 code, inside this folder
SCORE, COVER = 0.5, 0.4     # a box counts from this confidence; a unit belongs to a box covering this share of it
INPUT = 640                 # the detector sees the photo resized to INPUT x INPUT
CACHE_MIN_SCORE = 0.05      # every box at or above this is kept in the cache, so SCORE can be moved without rerunning
_detector = None


class DeviceDetector:
    """Shankar's device detector. det(rgb) -> [(x1, y1, x2, y2, score), ...] in pixels of the photo: every box the
    model proposes, filter by score yourself. Needs torch and the DEIMv2 code at DEIMV2_DIR."""

    def __init__(self, weights=WEIGHTS, deim_dir=DEIMV2_DIR, size=INPUT, device="cpu"):
        import torch
        deim = pathlib.Path(deim_dir) if deim_dir else None
        if deim is None or not (deim / "engine").is_dir():
            raise FileNotFoundError("DEIMv2 code not found: export DEIMV2_DIR=/path/to/DEIMv2 "
                                    "(https://github.com/Intellindust-AI-Lab/DEIMv2)")
        if not pathlib.Path(weights).exists():
            raise FileNotFoundError(f"detector weights missing: {weights}")
        sys.path.insert(0, str(deim))
        from engine.core import YAMLConfig
        cfg = YAMLConfig(str(deim / "configs" / "deimv2" / "deimv2_dinov3_m_coco.yml"), num_classes=1,
                         DINOv3STAs={"name": "vit_tinyplus", "embed_dim": 256, "weights_path": None,
                                     "interaction_indexes": [3, 7, 11], "num_heads": 4})
        ck = torch.load(weights, map_location="cpu", weights_only=False)
        state = ck["ema"]["module"] if ck.get("ema") else ck["model"]
        model = cfg.model
        model.load_state_dict(state, strict=False)
        self.model = (model.deploy() if hasattr(model, "deploy") else model).eval().to(device)
        post = cfg.postprocessor
        self.post = (post.deploy() if hasattr(post, "deploy") else post).eval()
        self.size, self.device = size, device

    def __call__(self, rgb):
        import torch
        import torchvision.transforms.functional as TF
        from PIL import Image
        img = rgb if isinstance(rgb, Image.Image) else Image.fromarray(np.asarray(rgb))
        img = img.convert("RGB")
        w, h = img.size
        x = TF.to_tensor(img.resize((self.size, self.size), Image.BILINEAR))
        x = TF.normalize(x, [0.485, 0.456, 0.406], [0.229, 0.224, 0.225]).unsqueeze(0).to(self.device)
        with torch.no_grad():
            res = self.post(self.model(x), torch.tensor([[w, h]], dtype=torch.float32))
        labels, boxes, scores = (res[0]["labels"], res[0]["boxes"], res[0]["scores"]) if isinstance(res, list) else res
        if isinstance(res, tuple):
            labels, boxes, scores = labels[0], boxes[0], scores[0]
        return [(float(b[0]), float(b[1]), float(b[2]), float(b[3]), float(s)) for b, s in zip(boxes, scores)]


def detector():
    """The detector, loaded once per process."""
    global _detector
    if _detector is None:
        _detector = DeviceDetector()
    return _detector


def detect(rgb):
    """Every box the detector proposes on an RGB photo (H x W x 3 or PIL), score >= CACHE_MIN_SCORE."""
    return [b for b in detector()(rgb) if b[4] >= CACHE_MIN_SCORE]


def boxes_cache(split):
    """{photo file name: boxes} from results/devices/devmodel_<split>.json, or {} when there is no cache."""
    fp = ROOT / "results" / "devices" / f"devmodel_{split}.json"
    return json.load(open(fp))["photos"] if fp.exists() else {}


def cover(unit, box):
    """Share of the unit box that the detector box covers."""
    ix = max(0.0, min(unit[2], box[2]) - max(unit[0], box[0]))
    iy = max(0.0, min(unit[3], box[3]) - max(unit[1], box[1]))
    return ix * iy / max(1e-6, (unit[2] - unit[0]) * (unit[3] - unit[1]))


def owners(rows, boxes, score=SCORE, cover_min=COVER):
    """Per unit: the index (into the boxes kept at `score`) of the detector box that covers at least `cover_min` of
    it, or -1 when no box does."""
    kept = [b for b in boxes if b[4] >= score]
    out = []
    for r in rows:
        best = max(((cover(r["box"], b), j) for j, b in enumerate(kept)), default=(0.0, -1))
        out.append(best[1] if best[0] >= cover_min else -1)
    return out


def seam_probs(bgr, rows, taken, boxes=None, feats=None, theirs=None, score=SCORE, cover_min=COVER):
    """P(a new device starts) per seam between two occupied units, and who answered: the detector (0 or 1, where both
    units sit in a box) or the logic (its probability, everywhere else). boxes: the detector's boxes for this photo,
    computed from the photo when not given; feats / theirs: the logic's cached inputs (device_join.seam_probs)."""
    if boxes is None:
        boxes = detect(np.ascontiguousarray(bgr[:, :, ::-1]))
    logic = DJ.seam_probs(bgr, rows, taken, feats, theirs)
    own = owners(rows, boxes, score, cover_min)
    probs, source = {}, {}
    for k, p in logic.items():
        if own[k - 1] >= 0 and own[k] >= 0:
            probs[k], source[k] = (0.0 if own[k - 1] == own[k] else 1.0), "model"
        else:
            probs[k], source[k] = p, "logic"
    return probs, source


def group(bgr, rows, taken, boxes=None, feats=None, theirs=None, score=SCORE, cover_min=COVER):
    """Devices as lists of unit indices (top to bottom), the per-seam decisions, the probabilities and who decided
    each seam ("model" or "logic")."""
    probs, source = seam_probs(bgr, rows, taken, boxes, feats, theirs, score, cover_min)
    new_device = {k: p >= 0.5 for k, p in probs.items()}
    return DJ.cut(taken, new_device), new_device, probs, source


def model_only(rows, taken, boxes, score=SCORE, cover_min=COVER):
    """The detector alone, for comparison: two occupied neighbours are one device only when one box covers both."""
    own = owners(rows, boxes, score, cover_min)
    new_device = {k: not (own[k - 1] >= 0 and own[k - 1] == own[k]) for a, b in DL.runs_of(taken) for k in range(a + 1, b + 1)}
    return DJ.cut(taken, new_device), new_device
