"""Model 2: the DINOv2-B device detector (train_dinov2.py), as the second opinion for agreement.py and classify_photo.py.

It looks at the whole photo (shape kept, long side capped as in training) and finds and names devices. For every
labelled device crop of a split, the detector box that overlaps it most (IoU >= 0.3, score >= 0.3) gives its answer;
no such box -> "none".

    python3 scripts/devices/second_detector.py --split valid      # sets Model 2's Other cut-off (agreement.py)
    python3 scripts/devices/second_detector.py --split test
writes models/device_cls/dinov2_b/<split>_predictions.csv (crop, class, pred, prob, other, reason) and detector.json.
"""
import argparse
import csv
import json
import pathlib
import time

import torch
from PIL import Image, ImageOps

ROOT = pathlib.Path(__file__).resolve().parents[2]
CROPS, DATA = ROOT / "data" / "device_crops", ROOT / "data" / "datasets" / "devices"
OUT = ROOT / "models" / "device_cls" / "dinov2_b"
DET = OUT / "detector.pt"                    # Model 2: best.pt of a train_dinov2.py run
SCORE, IOU = 0.3, 0.3
DROP = {"Empty", "Closed Unit"}              # not device types: never reported


def seen_photos():
    """The photos Model 2 trained on (its train_photos.txt), under every name the crop manifest uses for them.
    A test or valid crop counts for Model 2 only when its photo is not in this set."""
    f = OUT / "train_photos.txt"
    if not f.exists():
        return set()
    tr = {l.strip() for l in open(f) if l.strip()}
    exp = {r["photo"]: r["export_image"].replace(" ", "_") for r in csv.DictReader(open(DATA / "manifest.csv"))}
    return tr | {exp[p] for p in tr if p in exp}


def unseen_crops(split):
    """Crops of `split` whose photo neither model trained on."""
    seen = seen_photos()
    return {r["crop"] for r in csv.DictReader(open(CROPS / "manifest.csv")) if r["crop"] and r["split"] == split and r["source_image"] not in seen}


def load(ckpt, device):
    """A best.pt from train_dinov2.py: the model, its type names and the facts the report shows."""
    import train_dinov2 as td
    ck = torch.load(ckpt, map_location="cpu", weights_only=True)
    assert ck.get("format", "").startswith("racktrack-dinov2-fcos"), f"{ckpt} is not a train_dinov2.py checkpoint"
    m = td.Detector(ck["model_name"], len(ck["types"]), pretrained=False); m.load_state_dict(ck["model"])
    m.td, m.max_side = td, ck["max_side"]
    meta = {"ckpt": str(ckpt), "epoch": ck["epoch"], "valid": ck["valid"], "class_names": list(ck["types"]),
            "image_size": ck["max_side"], "patch_size": td.PATCH, "params_m": round(sum(v.numel() for v in ck["model"].values()) / 1e6, 1),
            "backbone_params_m": round(sum(v.numel() for k, v in ck["model"].items() if k.startswith("backbone")) / 1e6, 1),
            "format": ck["format"], "config": {k: ck["config"].get(k) for k in ("max_side", "epochs", "patience", "batch_size", "accum", "lr_backbone",
            "lr_head", "layer_decay", "weight_decay", "warmup_epochs", "repeat_factors", "photos", "augmentation", "choose_by", "device", "stride")}}
    return m.eval().to(device), list(ck["types"]), meta


@torch.no_grad()
def detect(m, im, device):
    """Boxes in photo pixels, type ids (1-based) and scores for one photo: long side capped, padded, boxes mapped back."""
    td = m.td; s, w, h = td.fit(im.width, im.height, m.max_side)
    x = td.T.Compose([td.T.ToTensor(), td.T.Normalize(td.MEAN, td.STD)])(im.resize((w, h), Image.BICUBIC) if s < 1 else im)
    xb = td.collate([(x, None, None, (h, w), s, 0)])[0].to(device)
    boxes, scores, labels = td.decode(m(xb), [(h, w)])[0]
    return boxes / s, labels + 1, scores


def iou(a, b):
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    i = max(0, x2 - x1) * max(0, y2 - y1)
    return i / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i + 1e-9)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default=str(DET))
    ap.add_argument("--split", default="test")
    a = ap.parse_args()
    dev = torch.device("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
    m, names, meta = load(a.ckpt, dev); epoch = meta["epoch"]
    rows = [r for r in csv.DictReader(open(CROPS / "manifest.csv")) if r["crop"] and r["split"] == a.split]
    ds = {r["photo"] for r in csv.DictReader(open(DATA / "manifest.csv"))}
    by_exp = {r["export_image"].replace(" ", "_"): r["photo"] for r in csv.DictReader(open(DATA / "manifest.csv"))}
    photos = sorted({r["source_image"] for r in rows})
    print(f"detector epoch {epoch}, {len(photos)} {a.split} photos, {len(rows)} crops, {dev}")
    OUT.mkdir(parents=True, exist_ok=True)
    json.dump(meta, open(OUT / "detector.json", "w"), indent=1)
    t0 = time.time(); out = []
    for n, ph in enumerate(photos, 1):
        src = ph if ph in ds else by_exp.get(ph.replace(" ", "_"))
        im = ImageOps.exif_transpose(Image.open(DATA / "images" / src)).convert("RGB")
        boxes, labels, scores = detect(m, im, dev)
        keep = (scores > SCORE) & torch.tensor([names[int(l) - 1] not in DROP for l in labels], dtype=torch.bool)
        for r in [r for r in rows if r["source_image"] == ph]:
            gt = [float(r[k]) for k in ("x1", "y1", "x2", "y2")]
            best = max(((iou(gt, b.tolist()), int(l), float(s)) for b, l, s in zip(boxes[keep], labels[keep], scores[keep])), default=(0, 0, 0))
            pred, prob = (names[best[1] - 1], best[2]) if best[0] >= IOU else ("none", 0.0)
            out.append({"crop": r["crop"], "class": r["class"], "pred": pred, "prob": f"{prob:.4f}", "other": "0", "reason": ""})
        if n % 25 == 0:
            print(f"  {n}/{len(photos)} photos, {time.time() - t0:.0f} s")
    with open(OUT / f"{a.split}_predictions.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0])); w.writeheader(); w.writerows(out)
    right = sum(o["class"] == o["pred"] for o in out); none = sum(o["pred"] == "none" for o in out)
    print(f"{len(out)} crops: detector right {right} ({right / len(out):.1%}), no box {none} -> {OUT / (a.split + '_predictions.csv')}")


if __name__ == "__main__":
    main()
