"""DINOv2-B rack-device detector, trained the RackTrack way (Model Training Standard).

Model: DINOv2-B (86.6 M) backbone; the last 4 layers' patch features, upsampled 2x to a stride-7 grid; an
anchor-free head (classes, box, centre-ness) trained with focal, GIoU and centre-ness losses. A stride-7 grid keeps
thin 1U devices visible.

Data and split (standard, section 01):
  - photos and labels of the shared dataset; every photo checked against manifest.csv by md5
  - the shared photo split, splits.csv: train to learn, valid to choose, test scored once at the end
  - the 10 device types only; Empty and Closed Unit are left out
  - photos turned upright (EXIF), as the labels are
Input (section 02): the photo keeps its shape. Photos larger than --max_side (long side) are shrunk to it, smaller
  ones stay as they are; then zero-padded right and bottom to a multiple of 14. Without a cap the biggest photos give
  up to 250,000 patch tokens, far beyond any GPU.
Memory: at the 1344 cap the largest photos give about 7,000 patch tokens; one training step on the largest one took
  12 GB on an Apple M-series GPU. On a smaller GPU use --max_side 1036 or --batch_size 1 --accum 16.
Training (section 03): AdamW, lr 3e-5 backbone with layer decay 0.8 and 3e-4 head, weight decay 0.05 (none on
  norms, biases, embeddings), 1 epoch warm-up then cosine to 1 %, gradient clip 1.0, bf16/fp16 on CUDA, gradient
  checkpointing, fixed seed; photos with rare types repeated more often (repeat-factor sampling); left-right flip and
  colour jitter (no up-down flip).
Choosing (section 04): after every epoch, valid mAP50-95; best epoch kept; stop after 8 epochs without gain, at most 30.
Testing: once, with the best weights: mAP and, per labelled device, the detection that overlaps it most
  (IoU >= 0.3, score >= 0.3) -> accuracy and macro-F1, on all test photos and on those of 1 MP or more.
Hand-over (section 06), in --out: best.pt (weights only), config.json, log.csv, train_photos.txt, results.json,
  test_predictions.csv, README.md.

    pip install -r requirements.txt
    python train_dinov2.py --data data --images /path/to/images --smoke          # 2 minutes: checks the setup
    python train_dinov2.py --data data --images /path/to/images                  # the real run
    python train_dinov2.py --data data --images /path/to/images --resume         # continue after a stop
--data holds labels/, data.yaml, manifest.csv, splits.csv. --images holds the photos (merged names, or the
original export folders).
"""
import os
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")   # Apple GPUs miss a few backward ops; they run on the CPU

import argparse
import csv
import datetime
import hashlib
import json
import math
import pathlib
import random
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import yaml
from PIL import Image, ImageOps
from torch.utils.data import DataLoader, Dataset, Sampler
from torchvision import transforms as T
from torchvision.ops import batched_nms, box_iou, generalized_box_iou_loss, sigmoid_focal_loss
from transformers import Dinov2Config, Dinov2Model

FORMAT = "racktrack-dinov2-fcos/1"
TYPES = ["Firewall", "Gateway", "Load Balancer", "PDU", "Patch Panel", "Router", "Server", "Storage unit", "Switch", "UPS"]
PATCH, STRIDE = 14, 7                      # backbone patch size, output-grid stride (after 2x upsample)
MEAN, STD = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)
MATCH_SCORE, MATCH_IOU = 0.3, 0.3          # how a labelled device is matched to a detection in the test scoring


# ------------------------------------------------------------------ data
def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def find_image(images, row):
    for p in (images / row["photo"], images / row["export_image"], images / row["export_image"].replace(" ", "_")):
        if p.exists():
            return p
    return None


def load_split(data, images, check_md5, say):
    """{split: [photo dicts]} from splits.csv; labels mapped from data.yaml to TYPES (other types dropped)."""
    names = yaml.safe_load(open(data / "data.yaml"))["names"]
    names = [names[k] for k in sorted(names)] if isinstance(names, dict) else list(names)
    to_type = {i: TYPES.index(n) for i, n in enumerate(names) if n in TYPES}
    man = {r["photo"]: r for r in csv.DictReader(open(data / "manifest.csv"))}
    out, missing, bad = {"train": [], "valid": [], "test": []}, [], []
    for r in csv.DictReader(open(data / "splits.csv")):
        row = man[r["photo"]]
        path = find_image(images, row)
        if path is None:
            missing.append(r["photo"]); continue
        if check_md5 and md5(path) != row["image_md5"]:
            bad.append(r["photo"]); continue
        out[r["split"]].append({"photo": r["photo"], "path": str(path), "label": str(data / "labels" / (pathlib.Path(r["photo"]).stem + ".txt")),
                                "w": int(row["width"]), "h": int(row["height"]), "mp": float(row["megapixels"]), "to_type": to_type})
    if missing:
        say(f"WARNING: {len(missing)} photos of splits.csv are not in --images, e.g. {missing[:3]}")
    if bad:
        raise SystemExit(f"{len(bad)} photos differ from the shared dataset (md5), e.g. {bad[:3]}. Use the shared images, or --no_md5.")
    tr, te = {p["photo"] for p in out["train"]}, {p["photo"] for p in out["test"]}
    assert not tr & te, "a training photo is also a test photo"
    return out


def read_boxes(path, w, h, to_type):
    """Label lines 'cls x1 y1 ... xn yn' (or 'cls cx cy w h'), normalised -> xyxy pixels and type index."""
    cls, boxes = [], []
    if os.path.exists(path):
        for ln in open(path):
            p = ln.split()
            if len(p) < 5 or int(p[0]) not in to_type:
                continue
            v = np.array(p[1:], dtype=np.float32)
            if len(v) == 4:
                cx, cy, bw, bh = v; x0, y0, x1, y1 = cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2
            else:
                x0, x1, y0, y1 = v[0::2].min(), v[0::2].max(), v[1::2].min(), v[1::2].max()
            b = [x0 * w, y0 * h, x1 * w, y1 * h]
            if b[2] - b[0] > 1 and b[3] - b[1] > 1:
                cls.append(to_type[int(p[0])]); boxes.append(b)
    return torch.tensor(boxes, dtype=torch.float32).reshape(-1, 4), torch.tensor(cls, dtype=torch.long)


def fit(w, h, max_side):
    s = min(1.0, max_side / max(w, h))
    return s, max(1, round(w * s)), max(1, round(h * s))


class Photos(Dataset):
    def __init__(self, items, train, max_side):
        self.items, self.train, self.max_side = items, train, max_side
        self.jitter = T.ColorJitter(0.3, 0.3, 0.3, 0.02)
        self.norm = T.Compose([T.ToTensor(), T.Normalize(MEAN, STD)])
        self.sizes = [fit(it["w"], it["h"], max_side)[1:][::-1] for it in items]       # (h, w) for bucketing

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        it = self.items[i]
        img = ImageOps.exif_transpose(Image.open(it["path"])).convert("RGB")
        W, H = img.size
        boxes, cls = read_boxes(it["label"], W, H, it["to_type"])
        s, w, h = fit(W, H, self.max_side)
        if s < 1:
            img = img.resize((w, h), Image.BICUBIC, reducing_gap=2.0); boxes = boxes * s
        if self.train:
            if random.random() < 0.5:                       # left-right flip; never up-down: rack order matters
                img = img.transpose(Image.FLIP_LEFT_RIGHT)
                if len(boxes):
                    boxes = torch.stack([w - boxes[:, 2], boxes[:, 1], w - boxes[:, 0], boxes[:, 3]], 1)
            img = self.jitter(img)
        return self.norm(img), boxes, cls, (h, w), s, i


def collate(batch):
    """Zero-pad (= the mean colour after normalisation) right and bottom to the batch max, a multiple of 14."""
    H = math.ceil(max(b[0].shape[1] for b in batch) / PATCH) * PATCH
    W = math.ceil(max(b[0].shape[2] for b in batch) / PATCH) * PATCH
    x = torch.zeros(len(batch), 3, H, W)
    for i, b in enumerate(batch):
        x[i, :, : b[0].shape[1], : b[0].shape[2]] = b[0]
    return x, [b[1] for b in batch], [b[2] for b in batch], [b[3] for b in batch], [b[4] for b in batch], [b[5] for b in batch]


def repeat_factors(items, t=0.3, cap=3.0):
    """Repeat-factor sampling (LVIS): a photo comes up more often when it holds a rare type. With t = 0.3 a type found
    in 10 % of the photos (Firewall) comes up 1.7 times as often; the cap keeps a type in 8 photos from dominating."""
    types = [set(read_boxes(it["label"], 10000, 10000, it["to_type"])[1].tolist()) for it in items]   # big scale: keep every box
    share = {}
    for ts in types:
        for c in ts:
            share[c] = share.get(c, 0) + 1
    r = {c: min(cap, max(1.0, math.sqrt(t / (n / len(items))))) for c, n in share.items()}
    per = [max([r[c] for c in ts], default=1.0) for ts in types]
    return per, {TYPES[c]: round(v, 2) for c, v in sorted(r.items())}


class BucketSampler(Sampler):
    """Batches of photos of similar size (little padding); with repeat factors, rare-type photos come up more often."""

    def __init__(self, sizes, bs, shuffle, repeat=None):
        self.sizes, self.bs, self.shuffle, self.repeat = sizes, bs, shuffle, repeat

    def _indices(self):
        if not self.repeat:
            return list(range(len(self.sizes)))
        return [i for i, r in enumerate(self.repeat) for _ in range(int(r) + (random.random() < r - int(r)))]

    def __iter__(self):
        idx = self._indices()
        if self.shuffle:
            random.shuffle(idx)
        batches, chunk = [], self.bs * 50
        for s in range(0, len(idx), chunk):
            c = sorted(idx[s:s + chunk], key=lambda i: self.sizes[i])
            batches += [c[j:j + self.bs] for j in range(0, len(c), self.bs)]
        if self.shuffle:
            random.shuffle(batches)
        return iter(batches)

    def __len__(self):
        n = sum(self.repeat) if self.repeat else len(self.sizes)
        return math.ceil(n / self.bs)


# ------------------------------------------------------------------ model
def conv_gn(i, o):
    return nn.Sequential(nn.Conv2d(i, o, 3, padding=1), nn.GroupNorm(32, o), nn.ReLU(inplace=True))


class Detector(nn.Module):
    def __init__(self, name, nc, grad_ckpt=False, pretrained=True):
        super().__init__()
        if name == "tiny-random":                                          # smoke tests only
            self.backbone = Dinov2Model(Dinov2Config(hidden_size=64, num_hidden_layers=4, num_attention_heads=2, patch_size=PATCH))
        elif pretrained:
            self.backbone = Dinov2Model.from_pretrained(name, attn_implementation="sdpa")
        else:
            self.backbone = Dinov2Model(Dinov2Config.from_pretrained(name))
        if grad_ckpt:
            self.backbone.gradient_checkpointing_enable()
        D, C = self.backbone.config.hidden_size, 256
        self.neck = nn.Sequential(nn.Conv2d(4 * D, C, 1), nn.GroupNorm(32, C), nn.ReLU(inplace=True),
                                  nn.ConvTranspose2d(C, C, 2, 2), nn.GroupNorm(32, C), nn.ReLU(inplace=True))
        self.cls_t, self.reg_t = nn.Sequential(conv_gn(C, C), conv_gn(C, C)), nn.Sequential(conv_gn(C, C), conv_gn(C, C))
        self.cls_o, self.reg_o, self.ctr_o = nn.Conv2d(C, nc, 3, padding=1), nn.Conv2d(C, 4, 3, padding=1), nn.Conv2d(C, 1, 3, padding=1)
        nn.init.constant_(self.cls_o.bias, -math.log(99))                  # focal-loss prior 0.01

    def forward(self, x):
        B, _, H, W = x.shape
        hs = self.backbone(pixel_values=x, output_hidden_states=True).hidden_states[-4:]
        f = torch.cat([self.backbone.layernorm(h)[:, 1:] for h in hs], -1)
        f = self.neck(f.transpose(1, 2).reshape(B, -1, H // PATCH, W // PATCH))
        c, r = self.cls_t(f), self.reg_t(f)
        cls = self.cls_o(c).flatten(2).transpose(1, 2)                                     # (B, h*w, nc)
        ltrb = torch.exp(self.reg_o(r).clamp(-4, 6)).flatten(2).transpose(1, 2) * STRIDE    # (B, h*w, 4) pixels
        ctr = self.ctr_o(r).flatten(2)[:, 0]                                                # (B, h*w)
        return cls.float(), ltrb.float(), ctr.float(), (f.shape[2], f.shape[3])


def grid_points(h, w, device):
    ys, xs = torch.meshgrid(torch.arange(h, device=device), torch.arange(w, device=device), indexing="ij")
    return torch.stack([xs.flatten(), ys.flatten()], 1).float() * STRIDE + STRIDE / 2


def ltrb_to_box(pts, ltrb):
    return torch.stack([pts[:, 0] - ltrb[:, 0], pts[:, 1] - ltrb[:, 1], pts[:, 0] + ltrb[:, 2], pts[:, 1] + ltrb[:, 3]], 1)


def assign(pts, boxes):
    """Positive = grid point in the central part of a box; the smallest box wins. Returns the box index or -1."""
    if len(boxes) == 0:
        return torch.full((len(pts),), -1, dtype=torch.long, device=pts.device)
    cx, cy = (boxes[:, 0] + boxes[:, 2]) / 2, (boxes[:, 1] + boxes[:, 3]) / 2
    hw = ((boxes[:, 2] - boxes[:, 0]) * 0.25).clamp(min=1.5 * STRIDE)
    hh = ((boxes[:, 3] - boxes[:, 1]) * 0.25).clamp(min=1.5 * STRIDE)
    px, py = pts[:, 0:1], pts[:, 1:2]
    inside = (px > boxes[:, 0]) & (px < boxes[:, 2]) & (py > boxes[:, 1]) & (py < boxes[:, 3])
    inside &= ((px - cx).abs() <= hw) & ((py - cy).abs() <= hh)
    area = ((boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])).expand_as(inside).clone()
    area[~inside] = float("inf")
    a, idx = area.min(1)
    idx[torch.isinf(a)] = -1
    return idx


def compute_loss(out, boxes_l, cls_l):
    cls, ltrb, ctr, (h, w) = out
    pts = grid_points(h, w, cls.device)
    tcls = torch.zeros_like(cls)
    pr_box, pos_gt, pos_ctr, pr_ctr, npos = [], [], [], [], 0
    for b in range(cls.shape[0]):
        gb, gc = boxes_l[b].to(cls.device), cls_l[b].to(cls.device)
        idx = assign(pts, gb); pos = idx >= 0
        if pos.any():
            g = gb[idx[pos]]; tcls[b, pos, gc[idx[pos]]] = 1; p = pts[pos]
            l, t, r, bt = p[:, 0] - g[:, 0], p[:, 1] - g[:, 1], g[:, 2] - p[:, 0], g[:, 3] - p[:, 1]
            c = torch.sqrt((torch.min(l, r) / torch.max(l, r)).clamp(0) * (torch.min(t, bt) / torch.max(t, bt)).clamp(0))
            pr_box.append(ltrb_to_box(p, ltrb[b, pos])); pos_gt.append(g); pos_ctr.append(c); pr_ctr.append(ctr[b, pos])
            npos += int(pos.sum())
    n = max(npos, 1)
    l_cls = sigmoid_focal_loss(cls, tcls, reduction="sum") / n
    if npos:
        l_box = generalized_box_iou_loss(torch.cat(pr_box), torch.cat(pos_gt), reduction="sum") / n
        l_ctr = F.binary_cross_entropy_with_logits(torch.cat(pr_ctr), torch.cat(pos_ctr), reduction="sum") / n
    else:
        l_box = l_ctr = cls.sum() * 0
    return l_cls + 2.0 * l_box + l_ctr, (l_cls.item(), l_box.item(), l_ctr.item())


@torch.no_grad()
def decode(out, sizes, score_thr=0.05, topk=1000, nms_iou=0.6, max_det=200):
    """Detections per photo, clipped to its real (unpadded) size: boxes, scores, type indices."""
    cls, ltrb, ctr, (h, w) = out
    pts = grid_points(h, w, cls.device)
    res = []
    for b in range(cls.shape[0]):
        s = torch.sqrt(cls[b].sigmoid() * ctr[b].sigmoid()[:, None])
        s, i = s.flatten().topk(min(topk, s.numel()))
        keep = s > score_thr; s, i = s[keep], i[keep]
        loc, c = i // cls.shape[2], i % cls.shape[2]
        bx = ltrb_to_box(pts[loc], ltrb[b, loc])
        H, W = sizes[b]
        bx[:, 0::2] = bx[:, 0::2].clamp(0, W); bx[:, 1::2] = bx[:, 1::2].clamp(0, H)
        k = batched_nms(bx, s, c, nms_iou)[:max_det]
        res.append((bx[k].cpu(), s[k].cpu(), c[k].cpu()))
    return res


# ------------------------------------------------------------------ scoring
def ap_one(dets, gts, thr):
    """dets: [(photo, score, box)]; gts: {photo: tensor(n, 4)}. All-point interpolated AP, or None without labels."""
    npos = sum(len(v) for v in gts.values())
    if npos == 0:
        return None
    if not dets:
        return 0.0
    used = {k: torch.zeros(len(v), dtype=torch.bool) for k, v in gts.items()}
    tp = []
    for img, _, box in sorted(dets, key=lambda d: -d[1]):
        g, ok = gts.get(img), 0
        if g is not None and len(g):
            iou = box_iou(box[None], g)[0]; iou[used[img]] = -1
            m, j = iou.max(0)
            if m >= thr:
                used[img][j] = True; ok = 1
        tp.append(ok)
    tp = np.array(tp); fp = 1 - tp; tp, fp = np.cumsum(tp), np.cumsum(fp)
    rec, prec = tp / npos, tp / np.maximum(tp + fp, 1e-9)
    rec, prec = np.concatenate([[0], rec, [1]]), np.concatenate([[0], prec, [0]])
    for i in range(len(prec) - 2, -1, -1):
        prec[i] = max(prec[i], prec[i + 1])
    idx = np.where(rec[1:] != rec[:-1])[0]
    return float(((rec[idx + 1] - rec[idx]) * prec[idx + 1]).sum())


def device_scores(true, pred):
    """Accuracy, share found, macro-F1 and per type, over labelled devices matched to detections."""
    if not true:
        return {"n": 0}
    present = sorted(set(true)); per = {}
    for c in present:
        tp = sum(t == p == c for t, p in zip(true, pred)); fp = sum(p == c != t for t, p in zip(true, pred)); n = sum(t == c for t in true)
        pr, rc = tp / max(1, tp + fp), tp / max(1, n)
        per[c] = {"n": n, "right": tp, "precision": pr, "recall": rc, "f1": 2 * pr * rc / max(1e-9, pr + rc)}
    cols = present + ["none"]
    return {"n": len(true), "accuracy": float(np.mean([t == p for t, p in zip(true, pred)])), "found": float(np.mean([p != "none" for p in pred])),
            "macro_f1": float(np.mean([per[c]["f1"] for c in present])), "per_type": per,
            "confusion": {"rows": present, "cols": cols, "cells": [[sum(t == r and p == c for t, p in zip(true, pred)) for c in cols] for r in present]}}


@torch.no_grad()
def evaluate(model, loader, items, device, amp, amp_dtype, rows=False):
    """mAP50 and mAP50-95 per type, valid loss, and the per-device matching (in original photo pixels)."""
    model.eval()
    nc = len(TYPES)
    dets, gts = [[] for _ in range(nc)], [dict() for _ in range(nc)]
    vloss, n, true, pred, out_rows = 0.0, 0, [], [], []
    for x, bl, cl, sizes, scales, ids in loader:
        with torch.autocast(device.type, dtype=amp_dtype, enabled=amp):
            out = model(x.to(device))
        vloss += compute_loss(out, bl, cl)[0].item(); n += 1
        for (bx, sc, c), gb, gc, k, s in zip(decode(out, sizes), bl, cl, ids, scales):
            for b_, s_, c_ in zip(bx, sc, c):
                dets[int(c_)].append((k, float(s_), b_))
            for c_ in range(nc):
                m = gc == c_
                if m.any():
                    gts[c_][k] = gb[m]
            keep = sc >= MATCH_SCORE                                    # per labelled device: the detection over it
            kb, kc, ks = bx[keep], c[keep], sc[keep]
            iou = box_iou(gb, kb) if len(gb) and len(kb) else torch.zeros(len(gb), 0)
            for i in range(len(gb)):
                j = int(iou[i].argmax()) if iou.shape[1] else -1
                hit = j >= 0 and float(iou[i, j]) >= MATCH_IOU
                t, p = TYPES[int(gc[i])], TYPES[int(kc[j])] if hit else "none"
                true.append(t); pred.append(p)
                if rows:
                    g = (gb[i] / s).tolist()
                    out_rows.append({"photo": items[k]["photo"], "mp_1_or_more": int(items[k]["mp"] >= 1.0), "x1": round(g[0]), "y1": round(g[1]),
                                     "x2": round(g[2]), "y2": round(g[3]), "class": t, "pred": p, "score": round(float(ks[j]), 4) if hit else 0.0})
    thrs = np.arange(0.5, 0.96, 0.05); per = {}
    for c in range(nc):
        aps = [ap_one(dets[c], gts[c], t) for t in thrs]
        if aps[0] is not None:
            per[TYPES[c]] = {"AP50": aps[0], "AP50_95": float(np.mean(aps))}
    r = {"loss": vloss / max(n, 1), "mAP50": float(np.mean([v["AP50"] for v in per.values()])) if per else 0.0,
         "mAP": float(np.mean([v["AP50_95"] for v in per.values()])) if per else 0.0, "per_type_ap": per, "devices": device_scores(true, pred)}
    return (r, out_rows) if rows else r


# ------------------------------------------------------------------ optimiser
def make_param_groups(model, lr_backbone, lr_head, wd, layer_decay):
    L = model.backbone.config.num_hidden_layers
    groups = {}
    for n, p in model.named_parameters():
        if not p.requires_grad:
            continue
        if n.startswith("backbone."):
            lid = int(n.split(".encoder.layer.")[1].split(".")[0]) + 1 if ".encoder.layer." in n else 0 if ".embeddings." in n else L + 1
            lr = lr_backbone * layer_decay ** (L + 1 - lid)
        else:
            lid, lr = -1, lr_head
        no_wd = p.ndim <= 1 or any(k in n for k in ("position_embeddings", "cls_token", "mask_token", "register_tokens"))
        groups.setdefault((lid, no_wd), dict(params=[], lr=lr, base_lr=lr, weight_decay=0.0 if no_wd else wd))["params"].append(p)
    return list(groups.values())


def lr_factor(step, total, warmup, min_ratio):
    if step < warmup:
        return (step + 1) / warmup
    t = (step - warmup) / max(1, total - warmup)
    return min_ratio + (1 - min_ratio) * 0.5 * (1 + math.cos(math.pi * t))


def init_from(model, path, say):
    """Start from earlier weights of THIS model; refuse when most of the backbone does not match (a different model)."""
    sd = torch.load(path, map_location="cpu", weights_only=True)
    sd = sd.get("model", sd)
    own = model.state_dict()
    ok = {k: v for k, v in sd.items() if k in own and own[k].shape == v.shape}
    bb = [k for k in own if k.startswith("backbone.")]
    got = sum(k in ok for k in bb) / max(1, len(bb))
    if got < 0.9:
        raise SystemExit(f"--init_ckpt matches only {got:.0%} of the backbone: it is a different model (for example the "
                         "Faster R-CNN checkpoint). Train without it.")
    model.load_state_dict(ok, strict=False)
    say(f"init_ckpt: {len(ok)} of {len(own)} tensors loaded ({len(own) - len(ok)} new, e.g. the head when the type list changed)")


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data", help="labels/, data.yaml, manifest.csv, splits.csv")
    ap.add_argument("--images", default=None, help="the photos (default: <data>/images)")
    ap.add_argument("--out", default="runs/dinov2b")
    ap.add_argument("--model", default="facebook/dinov2-base")
    ap.add_argument("--max_side", type=int, default=1344, help="long side cap in pixels; larger photos are shrunk to it")
    ap.add_argument("--init_ckpt", default="", help="earlier best.pt of this same model, to start from")
    ap.add_argument("--resume", action="store_true", help="continue from <out>/last.pt")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--patience", type=int, default=8, help="stop after this many epochs without a better valid mAP50-95")
    ap.add_argument("--batch_size", type=int, default=2)
    ap.add_argument("--accum", type=int, default=8, help="effective batch = batch_size x accum")
    ap.add_argument("--lr_backbone", type=float, default=3e-5)
    ap.add_argument("--lr_head", type=float, default=3e-4)
    ap.add_argument("--layer_decay", type=float, default=0.8)
    ap.add_argument("--weight_decay", type=float, default=0.05)
    ap.add_argument("--warmup_epochs", type=float, default=1.0)
    ap.add_argument("--min_lr_ratio", type=float, default=0.01)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no_grad_ckpt", action="store_true", help="faster, needs much more GPU memory")
    ap.add_argument("--no_md5", action="store_true", help="skip the check that the photos match the shared dataset")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--smoke", action="store_true", help="20 photos per split, 1 epoch, tiny random backbone: checks the setup only")
    a = ap.parse_args()
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    logf = open(out / "train.log", "a")

    def say(*m):
        line = " ".join(str(x) for x in m); print(line, flush=True); logf.write(line + "\n"); logf.flush()

    random.seed(a.seed); np.random.seed(a.seed); torch.manual_seed(a.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
    amp = device.type == "cuda"
    amp_dtype = torch.bfloat16 if amp and torch.cuda.is_bf16_supported() else torch.float16
    data = pathlib.Path(a.data)
    items = load_split(data, pathlib.Path(a.images) if a.images else data / "images", not a.no_md5, say)
    if a.smoke:
        items = {k: v[:20] for k, v in items.items()}; a.epochs, a.model, a.max_side, a.accum = 1, "tiny-random", 518, 2
    tr, va, te = items["train"], items["valid"], items["test"]
    rf, rf_types = repeat_factors(tr)
    say(f"{datetime.datetime.now():%Y-%m-%d %H:%M}  train {len(tr)}  valid {len(va)}  test {len(te)} photos on {device}; "
        f"long side cap {a.max_side}; repeat factors {rf_types}")
    (out / "train_photos.txt").write_text("\n".join(sorted(p["photo"] for p in tr)) + "\n")

    dtr, dva, dte = Photos(tr, True, a.max_side), Photos(va, False, a.max_side), Photos(te, False, a.max_side)
    kw = dict(collate_fn=collate, num_workers=a.workers, persistent_workers=a.workers > 0)
    ltr = DataLoader(dtr, batch_sampler=BucketSampler(dtr.sizes, a.batch_size, True, rf), pin_memory=amp, **kw)
    lva = DataLoader(dva, batch_size=1, **kw)

    model = Detector(a.model, len(TYPES), not a.no_grad_ckpt).to(device)
    if a.init_ckpt:
        init_from(model, a.init_ckpt, say)
    say(f"params: {sum(p.numel() for p in model.backbone.parameters()) / 1e6:.1f} M backbone, {sum(p.numel() for p in model.parameters()) / 1e6:.1f} M total")
    opt = torch.optim.AdamW(make_param_groups(model, a.lr_backbone, a.lr_head, a.weight_decay, a.layer_decay), betas=(0.9, 0.999))
    scaler = torch.amp.GradScaler("cuda", enabled=amp and amp_dtype == torch.float16)
    steps_per_epoch = math.ceil(len(ltr) / a.accum)
    total, warm = steps_per_epoch * a.epochs, max(1, int(steps_per_epoch * a.warmup_epochs))
    config = dict(vars(a), format=FORMAT, types=TYPES, patch=PATCH, stride=STRIDE, match={"score": MATCH_SCORE, "iou": MATCH_IOU},
                  photos={k: len(v) for k, v in items.items()}, repeat_factors=rf_types, steps_per_epoch=steps_per_epoch,
                  schedule="warm-up, then cosine", choose_by="valid mAP50-95", grad_clip=1.0,
                  augmentation="left-right flip 0.5; ColorJitter(0.3, 0.3, 0.3, 0.02)", device=str(device),
                  versions={"torch": torch.__version__, "transformers": __import__("transformers").__version__})
    config = json.loads(json.dumps(config, default=str))                 # plain values only, so best.pt loads with weights_only
    json.dump(config, open(out / "config.json", "w"), indent=1)

    start, step, best, best_ep = 0, 0, -1.0, -1
    if a.resume and (out / "last.pt").exists():
        ck = torch.load(out / "last.pt", map_location="cpu", weights_only=True)
        model.load_state_dict(ck["model"]); opt.load_state_dict(ck["opt"]); scaler.load_state_dict(ck["scaler"])
        start, step, best, best_ep = ck["epoch"] + 1, ck["step"], ck["best"], ck["best_ep"]
        random.setstate(ck["rng_py"]); torch.set_rng_state(ck["rng_torch"])
        say(f"resumed after epoch {start}, best valid mAP50-95 {best:.4f} at epoch {best_ep + 1}")
    new_log = not (out / "log.csv").exists() or start == 0
    log = open(out / "log.csv", "w" if new_log else "a")
    if new_log:
        log.write("epoch,train_loss,valid_loss,valid_mAP50,valid_mAP50_95,valid_accuracy,valid_macro_f1,valid_found,lr_head,minutes\n")
    for ep in range(start, a.epochs):
        if best_ep >= 0 and ep - best_ep > a.patience:
            say(f"early stop: no better valid mAP50-95 for {a.patience} epochs (best epoch {best_ep + 1})"); break
        model.train(); t0 = time.time(); run = np.zeros(4); cnt = 0
        opt.zero_grad(set_to_none=True)
        for i, (x, bl, cl, *_) in enumerate(ltr):
            with torch.autocast(device.type, dtype=amp_dtype, enabled=amp):
                outp = model(x.to(device, non_blocking=True))
            loss, parts = compute_loss(outp, bl, cl)
            if not math.isfinite(loss.item()):
                say(f"  skipped a batch with loss {loss.item()}"); opt.zero_grad(set_to_none=True); continue
            scaler.scale(loss / a.accum).backward()
            run += [loss.item(), *parts]; cnt += 1
            if (i + 1) % a.accum == 0 or i + 1 == len(ltr):
                f = lr_factor(step, total, warm, a.min_lr_ratio)
                for g in opt.param_groups:
                    g["lr"] = g["base_lr"] * f
                scaler.unscale_(opt); nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                scaler.step(opt); scaler.update(); opt.zero_grad(set_to_none=True); step += 1
            if (i + 1) % 50 == 0:
                say(f"  epoch {ep + 1} it {i + 1}/{len(ltr)} loss {run[0] / cnt:.3f} (cls {run[1] / cnt:.3f} box {run[2] / cnt:.3f} ctr {run[3] / cnt:.3f})")
        r = evaluate(model, lva, va, device, amp, amp_dtype)
        dv, mins = r["devices"], (time.time() - t0) / 60
        say(f"== epoch {ep + 1}  train {run[0] / max(cnt, 1):.3f}  valid loss {r['loss']:.3f}  mAP50 {r['mAP50']:.4f}  mAP50-95 {r['mAP']:.4f}  "
            f"devices right {dv.get('accuracy', 0):.4f}  ({mins:.0f} min)")
        for k, v in r["per_type_ap"].items():
            say(f"     {k:13s} AP50 {v['AP50']:.3f}  AP50-95 {v['AP50_95']:.3f}")
        log.write(f"{ep + 1},{run[0] / max(cnt, 1):.4f},{r['loss']:.4f},{r['mAP50']:.4f},{r['mAP']:.4f},{dv.get('accuracy', 0):.4f},"
                  f"{dv.get('macro_f1', 0):.4f},{dv.get('found', 0):.4f},{opt.param_groups[0]['lr']:.2e},{mins:.1f}\n"); log.flush()
        if r["mAP"] > best:
            best, best_ep = r["mAP"], ep
            torch.save({"format": FORMAT, "types": TYPES, "model_name": a.model, "max_side": a.max_side, "epoch": ep + 1,
                        "valid": {k: r[k] for k in ("mAP50", "mAP")} | {k: dv.get(k) for k in ("n", "accuracy", "macro_f1", "found")},
                        "config": config, "model": model.state_dict()}, out / "best.pt")
            say(f"   saved best.pt (valid mAP50-95 {best:.4f})")
        torch.save({"model": model.state_dict(), "opt": opt.state_dict(), "scaler": scaler.state_dict(), "epoch": ep, "step": step,
                    "best": best, "best_ep": best_ep, "rng_py": random.getstate(), "rng_torch": torch.get_rng_state()}, out / "last.pt")

    # ---- the test photos, once, with the best weights
    ck = torch.load(out / "best.pt", map_location="cpu", weights_only=True)
    model.load_state_dict(ck["model"])
    rt, rows = evaluate(model, DataLoader(dte, batch_size=1, **kw), te, device, amp, amp_dtype, rows=True)
    hi = [r_ for r_ in rows if r_["mp_1_or_more"]]
    results = {"best_epoch": ck["epoch"], "epochs_run": ep + 1, "valid": ck["valid"],
               "test": {k: rt[k] for k in ("mAP50", "mAP", "per_type_ap")} | {"devices_all_photos": rt["devices"],
                        "devices_1mp_or_more": device_scores([x["class"] for x in hi], [x["pred"] for x in hi])}}
    json.dump(results, open(out / "results.json", "w"), indent=1)
    with open(out / "test_predictions.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["photo"]); w.writeheader(); w.writerows(rows)
    d1 = results["test"]["devices_1mp_or_more"]
    (out / "README.md").write_text(
        f"DINOv2-B device detector (stride-7 anchor-free head), trained {datetime.date.today()}.\n\n"
        f"Changed from the 518 x 518 Faster R-CNN run: photo shape kept (long side up to {a.max_side}), finer stride-7 grid, cosine decay, "
        f"weight decay {a.weight_decay}, best epoch by valid mAP50-95 (epoch {ck['epoch']} of {ep + 1}), 10 device types only.\n"
        f"Test: mAP50 {rt['mAP50']:.3f}, mAP50-95 {rt['mAP']:.3f}; devices named right on photos of 1 MP or more "
        f"{d1.get('accuracy', 0):.3f} ({d1.get('n', 0)} devices).\n"
        "Verdict against the model in use: filled in by the RackTrack AI team after the item-by-item comparison.\n")
    if not a.smoke:
        (out / "last.pt").unlink(missing_ok=True)                        # finished: only best.pt is handed over
    say(f"done. best epoch {ck['epoch']}; test mAP50-95 {rt['mAP']:.4f}, devices right (1 MP or more) {d1.get('accuracy', 0):.4f}. "
        f"Send back the folder {out}.")


if __name__ == "__main__":
    main()
