#!/usr/bin/env python3
"""Fine-tune a classifier on the high-resolution device crops (the device classes with at least 20 training crops: 9 today).

Data: data/device_crops/manifest.csv (from crop_classes.py), keeping crops whose source image is
1 MP or more, split by the manifest's split column. Each crop is letterboxed into a wide
canvas (512x128, or 518x126 for DINOv2) instead of being squashed into a square.
Training samples classes with inverse-sqrt frequency against the class imbalance. Classes with fewer than
--min-class training crops are left out (too few to learn; they are reported). Label lines listed in
--exclude (photo,label_line) are left out too (labels found wrong). Selection is on
validation macro-F1 over the classes present; the test split is scored once, with the best (EMA) weights.

    python3 scripts/devices/train_device_cls.py --model convnext_tiny
    python3 scripts/devices/train_device_cls.py --model dinov2_b --epochs 20
    python3 scripts/devices/train_device_cls.py --model convnext_tiny --epochs 1 --limit 600   # smoke test

Writes models/device_cls/<model>/: log.txt, history.json, best.pt, last.pt (resume), results.json.
"""
import argparse, csv, json, math, pathlib, random, time

import numpy as np
import timm
import torch
import torch.nn as nn
from PIL import Image
from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score
from timm.optim import create_optimizer_v2
from timm.utils import ModelEmaV3
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler
from torchvision import transforms as T

ROOT = pathlib.Path(__file__).resolve().parents[2]
CROPS = ROOT / "data" / "device_crops"
RUNS = ROOT / "models" / "device_cls"
CLASSES = ["Firewall", "Gateway", "Load Balancer", "PDU", "Patch Panel", "Router", "Server", "Storage unit", "Switch", "UPS"]
MIN_PIXELS = 1_000_000
PAD = (114, 114, 114)
MEAN, STD = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)

MODELS = {
    "convnext_tiny":    dict(name="convnext_tiny.fb_in22k_ft_in1k",   bs=64, lr=2e-4,   drop_path=0.1),
    "efficientnetv2_s": dict(name="tf_efficientnetv2_s.in21k_ft_in1k", bs=64, lr=3e-4,   drop_path=0.2),
    "convnext_small":   dict(name="convnext_small.fb_in22k_ft_in1k",  bs=48, lr=1.5e-4, drop_path=0.2),
    "dinov2_b":         dict(name="vit_base_patch14_dinov2.lvd142m",  bs=32, lr=5e-5,   drop_path=0.1,
                             layer_decay=0.75, canvas=(518, 126)),   # 37 x 9 patches of 14 px
}


def letterbox(im, canvas, scale=1.0, dx=0.5, dy=0.5):
    """Fit `im` inside a canvas of (W, H), keeping aspect, padding with grey."""
    W, H = canvas
    w, h = im.size
    s = min(W / w, H / h) * scale
    nw, nh = max(1, round(w * s)), max(1, round(h * s))
    im = im.resize((nw, nh), Image.BICUBIC)
    out = Image.new("RGB", (W, H), PAD)
    out.paste(im, (int((W - nw) * dx), int((H - nh) * dy)))
    return out


def load_items(split, classes, exclude=frozenset(), limit=None, min_pixels=MIN_PIXELS, small_photo_classes=()):
    """[(path, class index)] for the crops of one split from photos of at least `min_pixels`, classes in `classes` only;
    classes in `small_photo_classes` also take their crops from smaller photos."""
    items = []
    for r in csv.DictReader(open(CROPS / "manifest.csv")):
        if (r["split"] == split and r["crop"] and r["class"] in classes
                and (int(r["image_w"]) * int(r["image_h"]) >= min_pixels or r["class"] in small_photo_classes)
                and (r["source_image"], r["label_line"]) not in exclude):
            items.append((str(CROPS / r["crop"]), classes.index(r["class"])))
    if limit and len(items) > limit:
        items = random.Random(0).sample(items, limit)
    return items


class Crops(Dataset):
    def __init__(self, items, canvas, train):
        self.items, self.canvas, self.train = items, canvas, train
        self.jitter = T.ColorJitter(0.3, 0.3, 0.2, 0.02)
        self.norm = T.Compose([T.ToTensor(), T.Normalize(MEAN, STD)])

    def __len__(self):
        return len(self.items)

    def __getitem__(self, k):
        path, y = self.items[k]
        im = Image.open(path).convert("RGB")
        if self.train:
            # simulate detector box jitter: trim up to 8 % off each edge
            w, h = im.size
            l, t = random.uniform(0, 0.08), random.uniform(0, 0.08)
            r, b = random.uniform(0, 0.08), random.uniform(0, 0.08)
            im = im.crop((int(w * l), int(h * t), max(int(w * l) + 1, int(w * (1 - r))),
                          max(int(h * t) + 1, int(h * (1 - b)))))
            if random.random() < 0.5:
                im = im.transpose(Image.FLIP_LEFT_RIGHT)
            im = self.jitter(im)
            im = letterbox(im, self.canvas, random.uniform(0.8, 1.0), random.random(), random.random())
        else:
            im = letterbox(im, self.canvas)
        return self.norm(im), y


def summarize(y_true, y_pred, classes):
    labels = list(range(len(classes)))
    present = sorted(set(y_true))   # macro-F1 over the classes that have crops in this split
    return dict(
        acc=float((np.asarray(y_true) == np.asarray(y_pred)).mean()),
        macro_f1=float(f1_score(y_true, y_pred, labels=present, average="macro", zero_division=0)),
        classes_scored=[classes[i] for i in present],
        recall=[float(r) for r in recall_score(y_true, y_pred, labels=labels, average=None, zero_division=0)],
        precision=[float(r) for r in precision_score(y_true, y_pred, labels=labels, average=None, zero_division=0)],
        f1=[float(r) for r in f1_score(y_true, y_pred, labels=labels, average=None, zero_division=0)],
        confusion=confusion_matrix(y_true, y_pred, labels=labels).tolist(),
        n=int(len(y_true)))


@torch.no_grad()
def evaluate(model, loader, dev, classes):
    model.eval()
    yt, yp = [], []
    for x, y in loader:
        yp.extend(model(x.to(dev)).argmax(1).cpu().tolist())
        yt.extend(y.tolist())
    return summarize(yt, yp, classes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, choices=MODELS)
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--patience", type=int, default=8)
    ap.add_argument("--bs", type=int)
    ap.add_argument("--lr", type=float)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, help="subsample train (and valid by 1/8): smoke test")
    ap.add_argument("--tag", default="")
    ap.add_argument("--fresh", action="store_true", help="ignore last.pt and start over")
    ap.add_argument("--throttle", type=float, default=0.0,
                    help="after each step, rest this many times the step's duration (runs cooler, same result)")
    ap.add_argument("--min-class", type=int, default=20, help="leave out classes with fewer training crops than this")
    ap.add_argument("--exclude", help="CSV with photo,label_line of label lines to leave out of training (labels found wrong); valid and test stay whole")
    ap.add_argument("--all-photos", action="store_true", help="train on the crops of every photo, not only 1 MP or more; valid and test stay 1 MP or more")
    ap.add_argument("--canvas", help="W x H the crops are letterboxed into, e.g. 1024x128 (default: the model's, 512x128)")
    ap.add_argument("--small-photos-for", default="", help="comma-separated classes that also train on crops from photos under 1 MP, e.g. Firewall,Router")
    ap.add_argument("--init", help="start from this best.pt instead of the ImageNet weights; it is also the best to beat")
    args = ap.parse_args()

    cfg = MODELS[args.model]
    bs, lr = args.bs or cfg["bs"], args.lr or cfg["lr"]
    canvas = tuple(int(v) for v in args.canvas.lower().split("x")) if args.canvas else cfg.get("canvas", (512, 128))
    dev = torch.device("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
    torch.manual_seed(0); random.seed(0); np.random.seed(0)

    out = RUNS / (args.model + args.tag)
    out.mkdir(parents=True, exist_ok=True)
    log = open(out / "log.txt", "a")

    def say(*a):
        s = " ".join(str(x) for x in a)
        print(s, flush=True); log.write(s + "\n"); log.flush()

    exclude = frozenset()
    if args.exclude:
        exclude = frozenset((r["photo"], r["label_line"]) for r in csv.DictReader(open(args.exclude)))
    tr_px = 0 if args.all_photos else MIN_PIXELS
    all_train = load_items("train", CLASSES, exclude, min_pixels=tr_px)
    n_all = np.bincount([y for _, y in all_train], minlength=len(CLASSES))
    classes = [c for c, n in zip(CLASSES, n_all) if n >= args.min_class]
    dropped = {c: int(n) for c, n in zip(CLASSES, n_all) if n < args.min_class}
    if args.init:
        classes = torch.load(args.init, map_location="cpu", weights_only=True)["classes"]
        dropped = {c: int(n) for c, n in zip(CLASSES, n_all) if c not in classes}
    spc = tuple(c.strip() for c in args.small_photos_for.split(",") if c.strip())
    tr = Crops(load_items("train", classes, exclude, args.limit, tr_px, spc), canvas, True)
    va = Crops(load_items("valid", classes, limit=args.limit and args.limit // 8), canvas, False)
    te = Crops(load_items("test", classes), canvas, False)
    counts = np.bincount([y for _, y in tr.items], minlength=len(classes))
    w_item = [1 / math.sqrt(counts[y]) for _, y in tr.items]
    sampler = WeightedRandomSampler(w_item, num_samples=len(tr), replacement=True)
    dl = dict(num_workers=args.workers, persistent_workers=args.workers > 0)
    tl = DataLoader(tr, bs, sampler=sampler, drop_last=True, **dl)
    vl = DataLoader(va, bs * 2, shuffle=False, **dl)
    el = DataLoader(te, bs * 2, shuffle=False, **dl)
    say(f"model {args.model} ({cfg['name']}) canvas {canvas} bs {bs} lr {lr} device {dev}")
    say(f"train {len(tr)} valid {len(va)} test {len(te)} | train counts {dict(zip(classes, counts.tolist()))}")
    say(f"classes left out (fewer than {args.min_class} training crops): {dropped or 'none'}; excluded label lines: {len(exclude)}")

    kw = dict(pretrained=True, num_classes=len(classes), drop_path_rate=cfg["drop_path"])
    if args.model.startswith("dinov2"):
        kw["img_size"] = (canvas[1], canvas[0])
    model = timm.create_model(cfg["name"], **kw).to(dev)
    say(f"params {sum(p.numel() for p in model.parameters()) / 1e6:.1f}M")
    if args.init:
        model.load_state_dict(torch.load(args.init, map_location="cpu", weights_only=True)["state_dict"])
        say(f"started from {args.init}")
    ema = ModelEmaV3(model, decay=0.999, use_warmup=not args.init)
    opt = create_optimizer_v2(model, opt="adamw", lr=lr, weight_decay=0.05, layer_decay=cfg.get("layer_decay"))
    steps, warm = args.epochs * len(tl), 2 * len(tl)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda i: i / warm if i < warm else
        0.5 * (1 + math.cos(math.pi * (i - warm) / max(1, steps - warm))) * (1 - 0.005) + 0.005)
    crit = nn.CrossEntropyLoss(label_smoothing=0.1)

    best, best_ep, bad, hist, start, finished = -1.0, -1, 0, [], 0, False
    if args.init and not (out / "last.pt").exists():
        v = evaluate(model, vl, dev, classes)
        best = v["macro_f1"]
        torch.save(dict(model=cfg["name"], arch=args.model, classes=classes, canvas=canvas,
                        state_dict=model.state_dict(), epoch=-1, val=v), out / "best.pt")
        say(f"starting model: val acc {v['acc']:.4f} macroF1 {best:.4f} (the score to beat)")
    last = out / "last.pt"
    if last.exists() and not args.fresh:
        ck = torch.load(last, map_location="cpu", weights_only=True)
        model.load_state_dict(ck["model"]); ema.load_state_dict(ck["ema"])
        if ck.get("ema_updates") is not None:
            ema.num_updates = ck["ema_updates"]
        opt.load_state_dict(ck["opt"]); sched.load_state_dict(ck["sched"])
        torch.set_rng_state(ck["rng_torch"]); random.setstate(ck["rng_py"])
        best, best_ep, bad, hist = ck["best"], ck["best_ep"], ck["bad"], ck["hist"]
        start, finished = ck["epoch"] + 1, ck["finished"]
        say(f"resumed from last.pt: next epoch {start}, best {best:.4f} @ {best_ep}")

    for ep in range(start, args.epochs):
        if finished:
            break
        model.train(); t0 = time.time(); tot = n = 0
        for i, (x, y) in enumerate(tl):
            ts = time.time()
            x, y = x.to(dev), y.to(dev)
            loss = crit(model(x), y)
            opt.zero_grad(set_to_none=True); loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            opt.step(); sched.step(); ema.update(model)
            tot += loss.item() * len(y); n += len(y)
            if args.throttle:
                time.sleep((time.time() - ts) * args.throttle)
            if i % 50 == 0:
                say(f"  ep {ep} it {i}/{len(tl)} loss {loss.item():.3f} {(time.time() - t0) / (i + 1):.2f}s/it")
        v = evaluate(ema.module, vl, dev, classes)
        rec = dict(epoch=ep, train_loss=tot / n, val_acc=v["acc"], val_macro_f1=v["macro_f1"],
                   lr=opt.param_groups[0]["lr"], secs=time.time() - t0)
        hist.append(rec); json.dump(hist, open(out / "history.json", "w"), indent=1)
        say(f"ep {ep}: loss {rec['train_loss']:.3f} val acc {v['acc']:.4f} macroF1 {v['macro_f1']:.4f} ({rec['secs'] / 60:.1f} min)")
        if v["macro_f1"] > best:
            best, best_ep, bad = v["macro_f1"], ep, 0
            torch.save(dict(model=cfg["name"], arch=args.model, classes=classes, canvas=canvas,
                            state_dict=ema.module.state_dict(), epoch=ep, val=v), out / "best.pt")
        else:
            bad += 1
            if bad >= args.patience:
                say(f"early stop at epoch {ep} (best {best:.4f} @ {best_ep})"); finished = True
        torch.save(dict(model=model.state_dict(), ema=ema.state_dict(), ema_updates=getattr(ema, "num_updates", None),
                        opt=opt.state_dict(), sched=sched.state_dict(), rng_torch=torch.get_rng_state(),
                        rng_py=random.getstate(), best=best, best_ep=best_ep, bad=bad, hist=hist, epoch=ep,
                        finished=finished or ep == args.epochs - 1), last)
        if finished:
            break

    ck = torch.load(out / "best.pt", map_location="cpu", weights_only=True)
    ema.module.load_state_dict(ck["state_dict"])
    t = evaluate(ema.module, el, dev, classes)
    json.dump(dict(arch=args.model, model=cfg["name"], classes=classes, dropped=dropped, excluded=len(exclude),
                   canvas=canvas, best_epoch=ck["epoch"], epochs_run=len(hist), train_counts=counts.tolist(),
                   val=ck["val"], test=t, history=hist),
              open(out / "results.json", "w"), indent=1)
    say(f"TEST acc {t['acc']:.4f} macroF1 {t['macro_f1']:.4f} (best epoch {ck['epoch']})")
    say("per-class recall: " + ", ".join(f"{c} {r:.2f}" for c, r in zip(classes, t["recall"])))


if __name__ == "__main__":
    main()
