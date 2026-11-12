"""Device-type classifier for rack device crops, ready for the pipeline.

Loads the trained ConvNeXt-Tiny (models/device_cls/convnext_tiny/best.pt, 9 device classes) and answers one of the
classes or "Other". A crop is the device polygon's bounding rectangle with the outside painted black, exactly as in
training (crop_polygon does this from a photo and a polygon). Each crop is letterboxed into the 512 x 128 canvas.

"Other" is not a trained class (no Other examples exist yet). It is a rule on top of the model, calibrated on the
valid split by scripts/devices/calibrate_other.py -> other_cutoffs.json:
  - energy score  -log sum exp(logits)  above its cut-off  (the model is unsure), or
  - distance to the k-th nearest training crop in feature space above its cut-off  (unlike anything seen), or
  - gap between the top two answers' probabilities below its cut-off  (close call: a likely mistake).
Any one flags the crop as Other. Without the calibration file only the class is returned.

A second model (models/device_cls/dinov2_b/best.pt, the runner-up architecture) is used when it exists: the crop is
answered only when both models name the same type, otherwise it is Other ("the two models disagree"). Which of the
other checks stay on next to it is read from models/device_cls/agreement.json (scripts/devices/agreement.py chooses
it on the test crops); until that file exists, disagreement replaces the close-call check.

    from device_classifier import DeviceClassifier, crop_polygon
    clf = DeviceClassifier()                       # cuda / mps / cpu
    crop = crop_polygon(photo, [(x1, y1), (x2, y2), ...])   # pixel coordinates
    r = clf.classify([crop])[0]
    r["label"]        # "Switch" ... or "Other"
    r["class"]        # best device class even when label is Other
    r["prob"]         # its softmax probability
    r["energy"], r["distance"], r["other_reason"]   # why it was called Other, if it was
"""
import json
import pathlib

import numpy as np
import timm
import torch
from PIL import Image, ImageDraw

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUN = ROOT / "models" / "device_cls" / "convnext_tiny"
SECOND = ROOT / "models" / "device_cls" / "dinov2_b"                 # second opinion, when trained
AGREE = ROOT / "models" / "device_cls" / "agreement.json"            # which checks stay on next to it
PAD = (114, 114, 114)
MEAN, STD = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)


def crop_polygon(photo, points):
    """The crop the model was trained on: bounding rectangle of the polygon, outside painted black."""
    im = photo.convert("RGB")
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    x1, y1 = max(0, int(min(xs))), max(0, int(min(ys)))
    x2, y2 = min(im.width, int(round(max(xs)))), min(im.height, int(round(max(ys))))
    x2, y2 = max(x2, x1 + 1), max(y2, y1 + 1)
    mask = Image.new("L", (x2 - x1, y2 - y1), 0)
    ImageDraw.Draw(mask).polygon([(x - x1, y - y1) for x, y in points], fill=255)
    out = Image.new("RGB", mask.size)
    out.paste(im.crop((x1, y1, x2, y2)), mask=mask)
    return out


def letterbox(im, canvas):
    W, H = canvas
    w, h = im.size
    s = min(W / w, H / h)
    nw, nh = max(1, round(w * s)), max(1, round(h * s))
    im = im.resize((nw, nh), Image.BICUBIC)
    out = Image.new("RGB", (W, H), PAD)
    out.paste(im, ((W - nw) // 2, (H - nh) // 2))
    return out


def _load(run):
    """The model saved by train_device_cls.py, ready to run: (timm model, classes, canvas)."""
    ck = torch.load(run / "best.pt", map_location="cpu", weights_only=True)
    classes, canvas = list(ck["classes"]), tuple(ck["canvas"])
    kw = dict(pretrained=False, num_classes=len(classes))
    if ck["model"].startswith("vit_"):                                # ViTs are built for one input size
        kw["img_size"] = (canvas[1], canvas[0])
    model = timm.create_model(ck["model"], **kw)
    model.load_state_dict(ck["state_dict"]); model.eval()
    return model, classes, canvas


class DeviceClassifier:
    def __init__(self, run=RUN, device=None, k=5, second=SECOND):
        self.dev = torch.device(device or ("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"))
        self.model, self.classes, self.canvas = _load(run); self.model.to(self.dev)
        self.k, self.cut, self.bank = k, None, None
        if (run / "other_cutoffs.json").exists():
            self.cut = json.load(open(run / "other_cutoffs.json"))
            if (run / "train_features.npz").exists():
                f = np.load(run / "train_features.npz")["features"].astype(np.float32)
                self.bank = torch.from_numpy(f / np.linalg.norm(f, axis=1, keepdims=True)).to(self.dev)
        self.second, self.checks = None, {"unsure", "unlike", "close"}
        if second and (second / "best.pt").exists():
            self.second, classes2, self.canvas2 = _load(second); self.second.to(self.dev)
            self.map2 = [classes2.index(c) for c in self.classes]      # its scores in our class order
            rule = json.load(open(AGREE))["chosen"] if AGREE.exists() else "agree"
            self.checks = {"disagree"} | ({"unsure", "unlike"} if "unfamiliar" in rule else set()) | ({"close"} if "close" in rule else set())

    def _tensor(self, crops, canvas):
        arr = [np.asarray(letterbox(c.convert("RGB"), canvas), dtype=np.float32) / 255 for c in crops]
        x = torch.from_numpy(np.stack(arr)).permute(0, 3, 1, 2)
        return ((x - torch.tensor(MEAN).view(1, 3, 1, 1)) / torch.tensor(STD).view(1, 3, 1, 1)).to(self.dev)

    @torch.no_grad()
    def scores(self, crops, batch=64):
        """logits, pooled features, energy and k-NN distance for every crop (distance is NaN without a feature bank)."""
        L, F = [], []
        for i in range(0, len(crops), batch):
            x = self._tensor(crops[i:i + batch], self.canvas)
            feats = self.model.forward_head(self.model.forward_features(x), pre_logits=True)
            L.append(self.model.get_classifier()(feats).float().cpu()); F.append(feats.float())
        logits, feats = torch.cat(L), torch.cat(F)
        energy = -torch.logsumexp(logits, dim=1)
        dist = torch.full((len(crops),), float("nan"))
        if self.bank is not None:
            fn = feats / feats.norm(dim=1, keepdim=True)
            d = 1 - fn @ self.bank.T                                   # cosine distance to every training crop
            dist = d.topk(self.k, dim=1, largest=False).values[:, -1].cpu()   # k-th nearest
        return logits, feats.cpu(), energy, dist

    @torch.no_grad()
    def second_probs(self, crops, batch=32):
        """The second model's probabilities for every crop, in this classifier's class order."""
        out = []
        for i in range(0, len(crops), batch):
            out.append(self.second(self._tensor(crops[i:i + batch], self.canvas2)).float().cpu().softmax(1)[:, self.map2])
        return torch.cat(out)

    def classify(self, crops):
        logits, _, energy, dist = self.scores(crops)
        probs2 = self.second_probs(crops) if self.second is not None else None
        return [self._decide(logits[i], energy[i], dist[i], None if probs2 is None else probs2[i]) for i in range(len(crops))]

    @torch.no_grad()
    def classify_views(self, views, weights=None):
        """One answer for one device from several pictures of it (for example its 5 best video frames): each picture's
        features (the 768 numbers the last layer reads) are averaged, weighted by `weights` (default equal), and the last
        layer reads the average once. The Other rule then runs on that one result. Because the last layer is linear, this
        equals averaging the pictures' scores before the softmax: one answer from everything seen, not a vote."""
        _, feats, _, _ = self.scores(views)
        w = torch.ones(len(views)) if weights is None else torch.as_tensor(weights, dtype=torch.float32)
        f = ((feats * (w / w.sum())[:, None]).sum(0, keepdim=True)).to(self.dev)
        logits = self.model.get_classifier()(f).float().cpu()
        energy = -torch.logsumexp(logits, dim=1)
        dist = torch.full((1,), float("nan"))
        if self.bank is not None:
            fn = f / f.norm(dim=1, keepdim=True)
            dist = (1 - fn @ self.bank.T).topk(self.k, dim=1, largest=False).values[:, -1].cpu()
        r = self._decide(logits[0], energy[0], dist[0], None)
        r["views"] = len(views)
        return r

    def _decide(self, logit, energy, dist, prob2):
        """The answer for one result: the best type, its probability, and Other with the reason when a check fires."""
        probs = logit.softmax(0)
        p, c = probs.max(0)
        r = dict(label=self.classes[c], **{"class": self.classes[c]}, prob=float(p), energy=float(energy),
                 distance=None if torch.isnan(dist) else float(dist), other_reason=None,
                 probs={n: float(v) for n, v in zip(self.classes, probs)})
        if prob2 is not None:
            p2, c2 = prob2.max(0)
            r["second_class"], r["second_prob"] = self.classes[c2], float(p2)
        if self.cut:
            reasons = []
            if "unsure" in self.checks and r["energy"] > self.cut["energy"]:
                reasons.append("unsure")
            if "unlike" in self.checks and r["distance"] is not None and r["distance"] > self.cut["distance"]:
                reasons.append("unlike any training crop")
            if "close" in self.checks and "margin" in self.cut:
                top2 = probs.topk(2).values
                if float(top2[0] - top2[1]) < self.cut["margin"]:
                    reasons.append("close call")
            if "disagree" in self.checks and prob2 is not None and int(c2) != int(c):
                reasons.append("the two models disagree")
            if reasons:
                r["label"], r["other_reason"] = "Other", " and ".join(reasons)
        return r
