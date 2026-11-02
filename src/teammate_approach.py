"""Teammate 1, version 1: ResNet50 (ImageNet) embedding of a unit crop, squashed to 224x224
-> LogisticRegression -> empty / occupied, "uncertain" below 65 %.

Their code is folded in here (the embedding below is their common.get_embedding, unchanged); their
folder is no longer needed.
  * Their exact recipe (same embedding, same LogisticRegression settings) retrained on the v2
    dataset by train_v2.py -> models/v2/teammate_clf.joblib.
  * Units come from the RT-EOS ladder (src/unit_grid.py) like every other approach.

teammate_unit_embeddings.pkl was inspected before loading: it references only numpy arrays and
sklearn.linear_model.LogisticRegression.
"""
import json
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent

_resnet = None


def _backbone():
    """ResNet50 (ImageNet weights) with the classification head removed, plus their transform."""
    global _resnet
    if _resnet is None:
        import torch
        from torchvision import models, transforms
        dev = torch.device("mps" if torch.backends.mps.is_available()
                           else "cuda" if torch.cuda.is_available() else "cpu")
        m = models.resnet50(weights=models.ResNet50_Weights.DEFAULT)
        m.fc = torch.nn.Identity()
        # Rack-unit crops are wide strips: resize the whole crop (no centre crop) to 224x224.
        tf = transforms.Compose([transforms.Resize((224, 224)), transforms.ToTensor(),
                                 transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])])
        _resnet = (m.to(dev).eval(), tf, dev)
    return _resnet


def get_embedding(image):
    """L2-normalised 2048-d ResNet50 embedding of one PIL image (their common.get_embedding)."""
    import torch
    m, tf, dev = _backbone()
    with torch.no_grad():
        e = m(tf(image.convert("RGB")).unsqueeze(0).to(dev)).squeeze().cpu().numpy()
    e /= np.linalg.norm(e)
    return e.astype(np.float32)


def unload_backbone():
    global _resnet
    _resnet = None


class TeammateApproach:
    key = "teammate"
    name = "ResNet50 + LogReg (teammate)"
    short = "ResNet50 (teammate)"
    description = ("Teammate's recipe: a ResNet50 ImageNet embedding of each unit and a logistic-regression "
                   "classifier. Units below 65 % confidence are flagged as needing review.")
    uncertain_below = 0.65   # their "needs review" threshold

    def __init__(self, version="v2"):
        self.version = version
        mdir = ROOT / "models" / version
        self.weights = mdir / "teammate_clf.joblib"
        self.eval_file = mdir / "teammate_eval.json"
        self._clf = None

    def unload(self):
        """Drop loaded models so their memory can be reused."""
        self._clf = None
        unload_backbone()

    def available(self):
        return self.weights.exists()

    def metrics(self):
        return json.load(open(self.eval_file)) if self.eval_file.exists() else None

    def _classifier(self):
        if self._clf is None:
            import joblib
            # joblib pickle of our own retrain of their recipe — trusted, local artefact.
            self._clf = joblib.load(self.weights)["clf"]
        return self._clf

    def predict(self, crops):
        """P(occupied) for a list of PIL crops, using their embedding + the classifier."""
        if not crops:
            return np.zeros(0)
        clf = self._classifier()
        X = np.stack([get_embedding(c) for c in crops])
        return clf.predict_proba(X)[:, 1]
