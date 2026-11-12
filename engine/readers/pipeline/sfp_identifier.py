"""
SFP / QSFP / OSFP cage identifier (v2), from the port identifier the model
team handed over on 1 Oct 2026 (port_identifier_v2_handover).

Two YOLO11s detectors find the transceiver cages on a device crop and score
each SFP, QSFP or OSFP: one fine-tuned on enlarged image tiles (ports 5 to 8
times larger than on a squeezed faceplate strip), and the original run as its
ensemble partner. A rule layer then settles the types:

  1. shape gate        drop boxes whose shape cannot be a cage
  2. one box per cage  class-agnostic NMS over both models
  3. size groups       cages of one size form a group from confident seeds
  4. group vote        one type per group: confidence-weighted vote times a
                       box-shape prior (log width/height per class)
  5. opening rule      a measured cage opening of 2.25 or wider proves QSFP
                       (SFP/QSFP/OSFP visual identification guide)
  6. row recovery      weaker boxes in line with a group join it
  7. isolation filter  a lone weak box is dropped; a group whose vote share
                       is under margin_min is marked uncertain
  8. fallback          a faint image with nothing else keeps its strongest
                       group, marked low confidence

It is loaded through pipeline.detection.load_model from a JSON spec (config
models.sfp_type -> pipeline/sfp_type_v2.json) that names the two weights and
the tuned parameters, so every caller that ran the old single model
(worker, runner, zone_read) gets this one unchanged. port_pattern calls
identify() on it instead of a plain YOLO pass.

Results on the model team's held-out test set (38 images, 424 ports): the
right port type on 89.5% of images (the original model 60.5%), port
precision 64.7%, recall 76.7%.
"""
import json
import os
from collections import Counter
from dataclasses import dataclass, fields, replace

import cv2
import numpy as np
import torch
import torchvision
from ultralytics import YOLO
from ultralytics.data.augment import LetterBox
from ultralytics.utils import ops

NAMES = ["SFP", "QSFP", "OSFP"]

# log(width/height) of labelled cage boxes in the training split: mean, std per class.
ASPECT_PRIOR = {0: (np.log(1.15), 0.18), 1: (np.log(1.53), 0.17), 2: (np.log(1.62), 0.15)}

# Tiling: a faceplate strip (median 9.5 times wider than tall) is cut along its
# long side into overlapping near-square windows, the scale the tiled model was
# trained at.
TILE_RATIO = 2.0
TILE_OVERLAP = 0.30


@dataclass
class Params:
    tiled: bool = False         # sliced inference (very large or panoramic crops)
    full_pass: bool = True      # also run the whole image once
    ensemble: bool = True       # also run the partner model and pool its boxes
    flip_tta: bool = False      # also run a horizontally flipped copy
    fuse_iou: float = 0.0       # > 0: a kept box takes the mean scores of all overlapping boxes
    cand_conf: float = 0.20
    seed_conf: float = 0.45
    nms_iou: float = 0.50
    ar_min: float = 0.60
    ar_max: float = 2.80
    size_tol: float = 0.25
    single_conf: float = 0.60
    min_group_mean: float = 0.25
    prior_w: float = 1.0
    qsfp_open_min: float = 2.25
    open_min_n: int = 3
    margin_min: float = 0.50
    fallback_conf: float = 0.18


def tile_windows(w, h, ratio=TILE_RATIO, overlap=TILE_OVERLAP):
    """(x1, y1, x2, y2) windows covering the image; a near-square image is one window."""
    long_, short = max(w, h), min(w, h)
    t = int(round(ratio * short))
    if long_ <= t * 1.15:
        return [(0, 0, w, h)]
    step = max(1, int(t * (1 - overlap)))
    starts = list(range(0, long_ - t + 1, step))
    if starts[-1] + t < long_:
        starts.append(long_ - t)
    if w >= h:
        return [(s, 0, s + t, h) for s in starts]
    return [(0, s, w, s + t) for s in starts]


def opening_aspect(img, box, pad=0.08):
    """Width/height of the dark cage opening inside a box, or None. The guide's
    nominal openings: SFP 14.0x9.2 (1.52), QSFP 18.8x9.0 (2.09), OSFP 22.88x13.3 (1.72)."""
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    X1, Y1 = int(max(0, x1 - pad * w)), int(max(0, y1 - pad * h))
    X2, Y2 = int(min(img.shape[1], x2 + pad * w)), int(min(img.shape[0], y2 + pad * h))
    crop = img[Y1:Y2, X1:X2]
    if crop.size == 0 or crop.shape[0] < 6 or crop.shape[1] < 6:
        return None
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    g = cv2.GaussianBlur(g, (3, 3), 0)
    _, th = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    n, _lab, stats, cent = cv2.connectedComponentsWithStats(th, 8)
    if n <= 1:
        return None
    cx, cy = crop.shape[1] / 2, crop.shape[0] / 2
    best, best_score = None, -1
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < 0.12 * crop.shape[0] * crop.shape[1]:
            continue
        d = np.hypot(cent[i][0] - cx, cent[i][1] - cy) / max(crop.shape[:2])
        score = area * (1 - d)
        if score > best_score:
            best, best_score = (bw, bh), score
    if best is None or best[1] == 0:
        return None
    return best[0] / best[1]


def _shape_prior(x):
    pr = np.array([np.exp(-0.5 * ((x - mu) / sd) ** 2) / sd for mu, sd in (ASPECT_PRIOR[c] for c in range(3))])
    return pr / pr.sum()


class SfpIdentifier:
    """identify(img) -> [{class_name, confidence, bbox, uncertain, rule, group, group_share}]."""

    def __init__(self, weights, partner=None, params=None, imgsz=640):
        self.p = params or Params()
        self.imgsz = imgsz
        self.net = YOLO(str(weights)).model.eval()
        self._partner_path = partner
        self._partner = None

    @property
    def partner(self):
        if self._partner is None and self._partner_path:
            self._partner = YOLO(str(self._partner_path)).model.eval()
        return self._partner

    # ── A. detection ──────────────────────────────────────────────
    def _forward(self, crop, net):
        lb = LetterBox((self.imgsz, self.imgsz), auto=False, stride=32)(image=crop)
        t = torch.from_numpy(lb[..., ::-1].transpose(2, 0, 1).copy()).float()[None] / 255
        with torch.no_grad():
            y = net(t)[0][0].T
        y = y[y[:, 4:].max(1).values >= 0.05]
        boxes = ops.scale_boxes(lb.shape[:2], ops.xywh2xyxy(y[:, :4]), crop.shape[:2])
        return boxes.numpy(), y[:, 4:].numpy()

    def _tiles(self, img, net):
        h, w = img.shape[:2]
        wins = tile_windows(w, h) if self.p.tiled else [(0, 0, w, h)]
        if self.p.full_pass and wins != [(0, 0, w, h)]:
            wins = wins + [(0, 0, w, h)]
        all_b, all_s = [], []
        for (x1, y1, x2, y2) in wins:
            b, s = self._forward(img[y1:y2, x1:x2], net)
            if len(b) == 0:
                continue
            b[:, [0, 2]] += x1
            b[:, [1, 3]] += y1
            if (x1, y1, x2, y2) != (0, 0, w, h):
                # A box touching an inner tile edge is seen whole by the overlapping neighbour.
                m = np.ones(len(b), bool)
                e = 2
                if x1 > 0:
                    m &= b[:, 0] > x1 + e
                if x2 < w:
                    m &= b[:, 2] < x2 - e
                if y1 > 0:
                    m &= b[:, 1] > y1 + e
                if y2 < h:
                    m &= b[:, 3] < y2 - e
                b, s = b[m], s[m]
            all_b.append(b)
            all_s.append(s)
        return (np.concatenate(all_b), np.concatenate(all_s)) if all_b else (np.zeros((0, 4)), np.zeros((0, 3)))

    def raw(self, img):
        runs = [(self.net, False)]
        if self.p.flip_tta:
            runs.append((self.net, True))
        if self.p.ensemble and self.partner is not None:
            runs.append((self.partner, False))
        parts = []
        for net, flip in runs:
            src = img[:, ::-1].copy() if flip else img
            b, s = self._tiles(src, net)
            if flip and len(b):
                w = img.shape[1]
                b = b.copy()
                b[:, [0, 2]] = w - b[:, [2, 0]]
            parts.append((b, s))
        return np.concatenate([b for b, _ in parts]), np.concatenate([s for _, s in parts])

    # ── B. rule layer ─────────────────────────────────────────────
    def refine(self, boxes, scores, img):
        memo = {}
        res = self._refine(boxes, scores, img, memo, self.p)
        if res or self.p.fallback_conf <= 0:
            return res
        fp = replace(self.p, cand_conf=min(self.p.cand_conf, self.p.fallback_conf), seed_conf=self.p.fallback_conf,
                     single_conf=self.p.fallback_conf, min_group_mean=0.0)
        res = self._refine(boxes, scores, img, memo, fp)
        if not res:
            return res
        strength = Counter()
        for d in res:
            strength[d["group"]] += d["confidence"]
        g = strength.most_common(1)[0][0]
        return [dict(d, rule="fallback: strongest group (low confidence)") for d in res if d["group"] == g]

    def _refine(self, boxes, scores, img, memo, p):
        H, W = img.shape[:2]
        if len(boxes) == 0:
            return []
        conf = scores.max(1)
        m = conf >= p.cand_conf
        boxes, scores, conf = boxes[m], scores[m], conf[m]
        if len(boxes) == 0:
            return []

        # 1. shape gate
        bw, bh = boxes[:, 2] - boxes[:, 0], boxes[:, 3] - boxes[:, 1]
        ar = bw / np.maximum(bh, 1e-6)
        m = (ar >= p.ar_min) & (ar <= p.ar_max) & (bw > 0.004 * W) & (bh > 0.004 * H)
        boxes, scores, conf, ar = boxes[m], scores[m], conf[m], ar[m]
        if len(boxes) == 0:
            return []

        # 2. one box per cage
        tb = torch.from_numpy(boxes).float()
        k = torchvision.ops.nms(tb, torch.from_numpy(conf).float(), p.nms_iou).numpy()[:800]
        if p.fuse_iou > 0:
            ov = (torchvision.ops.box_iou(tb[k], tb) >= p.fuse_iou).numpy() * conf[None]
            scores = (ov @ scores) / np.maximum(ov.sum(1, keepdims=True), 1e-9)
            boxes, conf, ar = boxes[k], scores.max(1), ar[k]
        else:
            boxes, scores, conf, ar = boxes[k], scores[k], conf[k], ar[k]
        bw, bh = boxes[:, 2] - boxes[:, 0], boxes[:, 3] - boxes[:, 1]
        cx, cy = (boxes[:, 0] + boxes[:, 2]) / 2, (boxes[:, 1] + boxes[:, 3]) / 2
        tol = np.log1p(p.size_tol)
        lw, lh = np.log(bw), np.log(bh)
        same_size = (np.abs(lw[:, None] - lw[None]) < tol) & (np.abs(lh[:, None] - lh[None]) < tol)
        mh, mw = np.minimum(bh[:, None], bh[None]), np.minimum(bw[:, None], bw[None])
        aligned = (np.abs(cy[:, None] - cy[None]) < 0.5 * mh) | (np.abs(cx[:, None] - cx[None]) < 0.5 * mw)

        # 3. size groups from confident seeds
        seeds = [int(i) for i in np.argsort(-conf) if conf[i] >= p.seed_conf]
        parent = {i: i for i in seeds}

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for a, i in enumerate(seeds):
            for j in seeds[a + 1:]:
                if same_size[i, j]:
                    parent[find(i)] = find(j)
        groups = {}
        for i in seeds:
            groups.setdefault(find(i), []).append(i)
        groups = list(groups.values())

        # 6. row recovery
        link = same_size & aligned
        for i in np.argsort(-conf):
            if conf[i] >= p.seed_conf:
                continue
            for g in groups:
                if link[i, g].any():
                    g.append(int(i))
                    break

        # 4. group vote and shape prior, 5. opening rule, 7. isolation and uncertainty
        results = []
        for gid, g in enumerate(groups):
            g = np.array(g)
            if len(g) == 1 and conf[g[0]] < p.single_conf:
                continue
            if conf[g].mean() < p.min_group_mean:
                continue
            vote = (scores[g] * conf[g, None]).sum(0)
            post = vote / vote.sum() * _shape_prior(np.log(np.median(ar[g]))) ** p.prior_w
            post = post / post.sum()
            c = int(post.argmax())
            share = float(post[c])
            rule = "model vote"
            if c in (1, 2):
                opens = []
                for i in g[np.argsort(-conf[g])][:25]:
                    kb = tuple(np.round(boxes[i], 1))
                    if kb not in memo:
                        memo[kb] = opening_aspect(img, boxes[i])
                    if memo[kb]:
                        opens.append(memo[kb])
                if len(opens) >= p.open_min_n and np.median(opens) >= p.qsfp_open_min:
                    if c == 2:
                        rule = f"opening {np.median(opens):.2f} -> QSFP (overrides model)"
                    c, share = 1, max(share, 0.9)
            for i in g:
                results.append({
                    "class_name": NAMES[c],
                    "confidence": round(float(conf[i]), 3),
                    "bbox": [float(v) for v in boxes[i].round(1)],
                    "group": gid,
                    "group_share": round(share, 3),
                    "uncertain": share < p.margin_min,
                    "rule": rule,
                })
        return results

    def identify(self, img):
        if img is None or getattr(img, "size", 0) == 0:
            return []
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        boxes, scores = self.raw(img)
        return self.refine(boxes, scores, img)


def overall_type(dets):
    """The device's port type: the type most of its sure cages carry, or None."""
    top = Counter(d["class_name"] for d in dets if not d.get("uncertain")).most_common(1)
    return top[0][0] if top else None


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_identifier(spec_path):
    """The identifier a JSON spec names: {"weights", "partner", "params"}, the
    weight paths relative to the repo root like every model in config.json."""
    with open(spec_path, encoding="utf-8") as f:
        spec = json.load(f)
    at = lambda p: p if not p or os.path.isabs(p) else os.path.join(REPO_ROOT, p)  # noqa: E731
    known = {f.name for f in fields(Params)}
    params = Params(**{k: v for k, v in (spec.get("params") or {}).items() if k in known})
    return SfpIdentifier(at(spec["weights"]), partner=at(spec.get("partner")), params=params)
