"""Second teammate's approach (delivered as rack_occupancy/, folded in here): three votes per unit, fused.

  a) YOLO vote  - the device detector's class for the object covering the unit:
                  Closed Unit / Empty -> 0.5 + 0.5*conf empty, any device -> 0.5 - 0.5*conf empty
  b) CLIP probe - ViT-B-32-quickgelu (OpenAI) embedding -> linear 3-class probe (open / closed / occupied)
  c) Edge logic - 10 edge/texture features inside the unit -> logistic regression
  P(empty) = (0.8*yolo + 0.1*clip + 0.1*edge) / 1.0   (their weights, tuned on their valid split);
  a unit no object covers (>= 40 % of its area) is judged by CLIP + edge alone.

Their helpers (edge features, softmax, sigmoid, box cover) are folded in below, unchanged from their
core.py; their folder is no longer needed. Their probes live in models/v2/fusion_t2_probes.npz.
Adaptations, none of which changes their logic:
  * Units come from our RT-EOS ladder, like every other approach.
  * The YOLO vote uses RT-EOS Models/device_finder.pt (their device_annotation_v2_seg_best.pt lives on a
    D:\\ drive that is not here). Its class ids match theirs: 0 = Closed Unit, 1 = Empty.
  * Their "closed" (blank panel) counts as empty, as in the v2 dataset - so it is a v2 approach.
fusion_t2_probes.npz is plain arrays (loaded with allow_pickle=False).
"""
import local_models  # noqa: F401  (model caches inside this folder, hub lookups off)
import json
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROBES = ROOT / "models" / "v2" / "fusion_t2_probes.npz"
EVAL_FILE = ROOT / "models" / "v2" / "fusion_t2_eval.json"
REFERENCE_EVAL = ROOT / "models" / "v2" / "fusion_t2_reference_eval.json"   # their own results, as delivered

DEFAULT_WEIGHTS = (0.4, 0.4, 0.2)   # yolo, clip, edge (replaced by the tuned weights in the probes file)
SLOT_COVER = 0.4                    # min fraction of a unit an object must cover to vote


# ---------------------------------------------------------------- their helpers (from core.py, unchanged)
def edge_features(rgb_crop, mask_crop=None):
    """Edge/texture descriptors computed inside the unit boundary.

    The outer ~8% of the unit (rails, unit outline, neighbour edges) is trimmed and
    the mask is eroded so only the unit's interior is measured.
    """
    import cv2
    h, w = rgb_crop.shape[:2]
    scale = 96 / max(h, 1)
    nw = int(np.clip(w * scale, 32, 768))
    gray = cv2.cvtColor(cv2.resize(rgb_crop, (nw, 96), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY)

    interior = np.zeros_like(gray, dtype=bool)
    my, mx = int(96 * 0.08), int(nw * 0.08)
    interior[my:96 - my, mx:nw - mx] = True
    if mask_crop is not None:
        m = cv2.resize(mask_crop.astype(np.uint8), (nw, 96), interpolation=cv2.INTER_NEAREST)
        m = cv2.erode(m, np.ones((5, 5), np.uint8))
        if m.sum() > 50:
            interior &= m.astype(bool)
    if interior.sum() < 20:
        interior[:] = True

    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(blur, 50, 150) > 0
    gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3)
    lap = cv2.Laplacian(blur, cv2.CV_32F)

    e_in = edges & interior
    n_cc, _, stats, _ = cv2.connectedComponentsWithStats(e_in.astype(np.uint8), connectivity=8)
    small = ((stats[1:, cv2.CC_STAT_AREA] >= 3) & (stats[1:, cv2.CC_STAT_AREA] <= 200)).sum() if n_cc > 1 else 0
    cols = e_in.sum(0)[interior.any(0)] / max(interior.sum(0).max(), 1)
    ax, ay = np.abs(gx[interior]).sum(), np.abs(gy[interior]).sum()
    g = gray[interior].astype(np.float32)

    return np.array([
        e_in.sum() / interior.sum(),                    # how much of the interior is edge
        ax / (ax + ay + 1e-6),                          # vertical edges (ports, vents) vs horizontal (panel lines)
        np.hypot(gx, gy)[interior].mean() / 255,
        np.log1p(lap[interior].var()),                  # fine texture
        g.std() / 255,
        small / (interior.sum() / 1000),                # small edge blobs per 1k px (ports, LEDs, screws)
        cols.std() if cols.size else 0.0,               # alternating column structure (port rows)
        g.mean() / 255,                                 # deep open slots are dark
        (g > 220).mean(),                               # LEDs / labels
        np.log(w / max(h, 1)),
    ], dtype=np.float32)


def softmax(z):
    z = z - z.max(1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(1, keepdims=True)


def sigmoid(z):
    return 1 / (1 + np.exp(-z))


def cover(slot, obj):
    """Fraction of the slot's area covered by the object box."""
    ix = max(0, min(slot[2], obj[2]) - max(slot[0], obj[0]))
    iy = max(0, min(slot[3], obj[3]) - max(slot[1], obj[1]))
    return ix * iy / ((slot[2] - slot[0]) * (slot[3] - slot[1]) + 1e-6)


class FusionApproach:
    key = "fusion_t2"
    name = "YOLO + CLIP + Edge fusion (teammate 2)"
    short = "Fusion (teammate 2)"
    description = ("Teammate 2's approach: three votes per unit - the device detector's class, a CLIP "
                   "probe (open / closed / occupied) and edge-texture logic - fused 0.8 / 0.1 / 0.1. "
                   "Units come from the same RT-EOS ladder as every other approach.")

    def __init__(self, version="v2"):
        self.version = version
        self.weights = PROBES
        self.eval_file = EVAL_FILE
        self._ready = False

    def unload(self):
        """Drop loaded models so their memory can be reused."""
        for attr in ("_clf", "clip"):
            if getattr(self, attr, None) is not None:
                setattr(self, attr, None)
        if hasattr(self, "_ready"):
            self._ready = False

    def available(self):
        return self.version == "v2" and PROBES.exists()

    def metrics(self):
        return json.load(open(self.eval_file)) if self.eval_file.exists() else None

    # ---- their models
    def _load(self):
        if self._ready:
            return
        import open_clip
        import torch
        p = np.load(PROBES, allow_pickle=False)
        self.clip_W, self.clip_b = p["clip_W"], p["clip_b"]
        self.edge_w, self.edge_b = p["edge_w"], float(p["edge_b"])
        self.edge_mu, self.edge_sd = p["edge_mu"], p["edge_sd"]
        self.wts = tuple(float(v) for v in p["fusion_weights"]) if "fusion_weights" in p.files else DEFAULT_WEIGHTS
        self.dev = "mps" if torch.backends.mps.is_available() else "cpu"
        self.clip, _, self.pre = open_clip.create_model_and_transforms("ViT-B-32-quickgelu", pretrained="openai")
        self.clip = self.clip.to(self.dev).eval()
        self._ready = True

    def _clip_probs(self, rgb_crops):
        import torch
        from PIL import Image
        out = []
        with torch.no_grad():
            for i in range(0, len(rgb_crops), 64):
                x = torch.stack([self.pre(Image.fromarray(c)) for c in rgb_crops[i:i + 64]]).to(self.dev)
                e = self.clip.encode_image(x).float()
                out.append((e / e.norm(dim=-1, keepdim=True)).cpu().numpy())
        return softmax(np.vstack(out) @ self.clip_W + self.clip_b)      # open, closed, occupied

    def _edge_probs(self, rgb_crops):
        F = np.stack([edge_features(c) for c in rgb_crops])
        return sigmoid(((F - self.edge_mu) / self.edge_sd) @ self.edge_w + self.edge_b)

    # ---- crop-only (no photo context): CLIP + edge, as their pipeline does for a unit no object covers
    def predict(self, crops):
        self._load()
        if not crops:
            return np.zeros(0)
        rgb = [np.array(c.convert("RGB")) for c in crops]
        cp, pe = self._clip_probs(rgb), self._edge_probs(rgb)
        _, wc, we = self.wts
        p_empty = (wc * (cp[:, 0] + cp[:, 1]) + we * pe) / (wc + we)
        return 1 - p_empty

    # ---- full approach on a rack photo: all three votes
    def predict_with_context(self, img, units, devices):
        """P(occupied) per unit plus what their votes say is there (open slot / closed panel / device)."""
        self._load()
        if not units:
            return np.zeros(0), []
        rgb_img = np.array(img.convert("RGB"))
        H, W = rgb_img.shape[:2]
        crops = []
        for u in units:
            x1, y1, x2, y2 = (int(v) for v in u["box"])
            crops.append(rgb_img[max(0, y1):min(H, y2), max(0, x1):min(W, x2)])
        cp, pe = self._clip_probs(crops), self._edge_probs(crops)
        wy, wc, we = self.wts
        p_occ, kinds = [], []
        for u, c, e in zip(units, cp, pe):
            obj, best = None, 0.0
            for d in devices or []:
                cov = cover(u["box"], d["box"])
                if cov > best:
                    obj, best = d, cov
            p_clip = float(c[0] + c[1])
            if obj is not None and best >= SLOT_COVER:
                empty_cls = obj["class_name"].lower() in ("closed unit", "empty")
                p_yolo = 0.5 + 0.5 * obj["confidence"] if empty_cls else 0.5 - 0.5 * obj["confidence"]
                p_empty = (wy * p_yolo + wc * p_clip + we * float(e)) / (wy + wc + we)
            else:
                p_empty = (wc * p_clip + we * float(e)) / (wc + we)
            p_occ.append(1 - p_empty)
            kinds.append(None if p_empty < 0.5 else ("closed panel" if c[1] > c[0] else "open slot"))
        return np.array(p_occ), kinds
