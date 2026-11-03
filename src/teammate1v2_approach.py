"""Teammate 1, version 2: DINOv2 ViT-L/14 + cosine k-NN, with its uncertainty features.

Their code is folded in here, logic unchanged (from their embedder.py, knn_classifier.py, config.py); their
folder is no longer needed. Their labelled gallery lives in models/v2/knn_v2_gallery.pkl (classifier_dino.pkl).
  * each unit cut-out is resized whole to 112 x 448 and embedded with DINOv2 ViT-L/14 (patch-token mean);
  * cosine k-NN against their labelled gallery (k = 25, temperature 0.02, stored in the gallery file);
  * in-photo consistency: units in the same photo with embedding similarity >= 0.90 share their votes;
  * "needs review" below 70 % confidence  -> our status "uncertain";
  * device second opinion: when the classifier says empty but the device detector sees a device, the unit is
    NOT overruled; it is flagged ("warn") -> our status "uncertain" with the device named.
One alignment: their gallery labels blank panels (Closed_Unit, 233 examples) Occupied, the v1 rule; this app and
the v2 dataset count blank panels as free. k-NN has no training step, so the 233 gallery labels are switched to
Empty at load time (the same thing their own "Mark empty" correction does). Set ALIGN_BLANK_PANELS = False to
run their labels exactly as delivered.
Units come from our RT-EOS ladder, like every other approach. The gallery pickle was inspected before
loading: it holds only numpy arrays and plain numbers.
"""
import local_models  # noqa: F401  (model caches inside this folder, hub lookups off)
import json
import pathlib
import pickle
from dataclasses import dataclass

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
GALLERY = ROOT / "models" / "v2" / "knn_v2_gallery.pkl"
EVAL_FILE = ROOT / "models" / "v2" / "knn_v2_eval.json"
ALIGN_BLANK_PANELS = True

# Their settings (config.py)
DINO_MODEL = "dinov2_vitl14"
IMAGE_SIZE = (112, 448)            # (height, width), multiples of 14; 1:4 squashes thin units far less than a square
REVIEW_THRESHOLD = 0.70            # confidence below this -> "needs review"
CONSISTENCY_SIMILARITY = 0.90      # units in one photo this similar share their votes (None = off)


# ---------------------------------------------------------------- DINOv2 embedder (their embedder.py)
_dino = None


def _model():
    global _dino
    if _dino is None:
        import torch
        from torchvision import transforms
        dev = torch.device("mps" if torch.backends.mps.is_available()
                           else "cuda" if torch.cuda.is_available() else "cpu")
        from local_models import dinov2_hub
        m = dinov2_hub(DINO_MODEL).to(dev).eval()
        # Rack units are wide strips: resize the whole unit (no centre crop) so DINOv2 sees every part of it.
        tf = transforms.Compose([transforms.Resize(IMAGE_SIZE), transforms.ToTensor(),
                                 transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])])
        _dino = (m, tf, dev)
    return _dino


def embed_batch(images):
    """(N, 1024) float32 L2-normalised DINOv2 embeddings: mean of the patch tokens."""
    import torch
    m, tf, dev = _model()
    with torch.no_grad():
        out = m.forward_features(torch.stack([tf(im.convert("RGB")) for im in images]).to(dev))
        feats = torch.nn.functional.normalize(out["x_norm_patchtokens"].mean(dim=1), dim=1)
    return feats.cpu().numpy().astype(np.float32)


# ---------------------------------------------------------------- cosine k-NN (their knn_classifier.py)
@dataclass
class Prediction:
    label: str                 # "Empty" or "Occupied"
    confidence: float          # share of the weighted vote won by `label`
    scores: dict               # vote share per class, sums to 1
    nearest_type: str          # device type of the single most similar gallery image
    nearest_similarity: float


class CosineKNN:
    """k nearest neighbours by cosine similarity; each neighbour votes with weight exp(similarity / temperature)."""

    def __init__(self, k=5, temperature=None):
        self.k, self.temperature = k, temperature

    def fit(self, embeddings, labels, device_types):
        self.embeddings = np.asarray(embeddings, dtype=np.float32)    # already L2-normalised: dot = cosine
        self.labels = np.asarray(labels)
        self.device_types = np.asarray(device_types)
        self.classes = sorted(set(self.labels))
        return self

    def _weights(self, sims):
        if self.temperature:
            return np.exp((sims - sims.max()) / self.temperature)    # shift for numerical stability
        return np.clip(sims, 0, None)

    def predict(self, queries):
        queries = np.atleast_2d(queries).astype(np.float32)
        sims = queries @ self.embeddings.T
        k = min(self.k, sims.shape[1])
        top = np.argpartition(-sims, k - 1, axis=1)[:, :k]
        top = np.take_along_axis(top, np.argsort(-np.take_along_axis(sims, top, axis=1), axis=1), axis=1)
        out = []
        for row, idx in enumerate(top):
            w = self._weights(sims[row, idx])
            total = w.sum()
            scores = {c: float(w[self.labels[idx] == c].sum() / total) if total > 0 else 1 / len(self.classes)
                      for c in self.classes}
            label = max(scores, key=scores.get)
            out.append(Prediction(label, scores[label], scores, str(self.device_types[idx[0]]),
                                  float(sims[row, idx[0]])))
        return out

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            data = pickle.load(f)          # numpy arrays + numbers only (inspected)
        return cls(k=data["k"], temperature=data.get("temperature")).fit(
            data["embeddings"], data["labels"], data["device_types"])


def harmonize(embeddings, p_occupied, min_similarity):
    """In-photo consistency: each unit's P(occupied) is averaged with that of every other unit in the photo
    whose embedding similarity is >= min_similarity, weighted by that similarity.
    Returns (new P(occupied), index of the most similar such unit or -1)."""
    E = np.asarray(embeddings, dtype=np.float32)
    p = np.asarray(p_occupied, dtype=np.float64)
    if len(p) < 2:
        return p.copy(), np.full(len(p), -1)
    S = E @ E.T
    np.fill_diagonal(S, 0)
    W = np.where(S >= min_similarity, S, 0)
    smoothed = (p + W @ p) / (1 + W.sum(1))
    partner = np.where(W.max(1) > 0, W.argmax(1), -1)
    return smoothed, partner


# ---------------------------------------------------------------- the approach
class KnnV2Approach:
    key = "knn_v2"
    name = "DINOv2-L k-NN (teammate 1, v2)"
    short = "DINOv2-L k-NN (teammate)"
    description = ("Teammate 1's version 2: each unit is embedded with DINOv2 ViT-L/14 and compared with "
                   "~5,000 labelled examples (25 nearest neighbours vote). Units that look alike in the same photo "
                   "share their votes. Below 70 % confidence a unit is uncertain, and when the device detector "
                   "disagrees with an 'empty' answer the unit is flagged instead of overruled.")
    uncertain_below = REVIEW_THRESHOLD
    device_disagreement = "warn"    # their 'second opinion': flag, don't overrule

    def __init__(self, version="v2", align_blank_panels=None):
        self.version = version
        self.align_blank_panels = ALIGN_BLANK_PANELS if align_blank_panels is None else align_blank_panels
        self.weights = GALLERY
        self.eval_file = EVAL_FILE
        self._clf = None

    def available(self):
        return self.version == "v2" and self.weights.exists()

    def metrics(self):
        return json.load(open(self.eval_file)) if self.eval_file.exists() else None

    def unload(self):
        global _dino
        self._clf = None
        _dino = None

    def _classifier(self):
        if self._clf is None:
            clf = CosineKNN.load(self.weights)
            if self.align_blank_panels:
                labels = clf.labels.copy()
                labels[clf.device_types == "Closed_Unit"] = "Empty"
                clf.fit(clf.embeddings, labels, clf.device_types)
            self._clf = clf
        return self._clf

    def _embed(self, crops):
        return np.concatenate([embed_batch(crops[i:i + 32]) for i in range(0, len(crops), 32)])

    def predict(self, crops):
        """Crop-only scoring (no photo, so no in-photo consistency): P(occupied) per crop."""
        if not crops:
            return np.zeros(0)
        return np.array([p.scores.get("Occupied", 0.0) for p in self._classifier().predict(self._embed(crops))])

    def predict_with_context(self, img, units, devices):
        """Full approach on a rack photo: native cut-outs, k-NN, then in-photo consistency."""
        if not units:
            return np.zeros(0), [], []
        rgb = img.convert("RGB")
        E = self._embed([rgb.crop(tuple(int(v) for v in u["box"])) for u in units])
        preds = self._classifier().predict(E)
        p = np.array([q.scores.get("Occupied", 0.0) for q in preds])
        partner = np.full(len(p), -1)
        if CONSISTENCY_SIMILARITY and len(p) > 1:
            p, partner = harmonize(E, p, CONSISTENCY_SIMILARITY)
        notes = []
        for i, q in enumerate(preds):
            note = {"nearest": q.nearest_type.replace("_", " "), "similarity": round(q.nearest_similarity, 3)}
            if partner[i] >= 0 and ((p[i] >= 0.5) != (q.label == "Occupied")):
                note["matched_with"] = units[partner[i]]["unit"].upper()
            notes.append(note)
        return p, [None] * len(units), notes
