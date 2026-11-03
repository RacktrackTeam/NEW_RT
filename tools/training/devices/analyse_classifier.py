#!/usr/bin/env python3
"""Resolution analysis of the device classifier on the test split -> resolution.json in the model folder.

1. Geometry: how each test crop goes into the 512 x 128 canvas (letterbox: scale to fit, keep the shape, pad grey).
   Scale, share of the canvas the device fills, share of its own pixels kept, and accuracy by band
   (accuracy from test_predictions.csv, written by calibrate_other.py).
3. Canvas comparison (when the runs exist): models/device_cls/convnext_tiny_canvas_<W>x<H>/, trained from the same
   model with the same data and rounds, only the canvas size differs. Each is scored on the same test crops, with
   McNemar's test against the 512x128 run (is the difference more than chance?) -> canvas_compare.json.
2. Resolution test: the same test crops with the canvas made coarser on purpose (rendered at a fraction of
   512 x 128, then scaled back up). If accuracy holds at a lower resolution, the canvas keeps more detail than the
   model uses; if it falls at once, fine detail matters and a larger canvas is worth a training run.

    python3 scripts/devices/analyse_classifier.py [--run models/device_cls/convnext_tiny]
"""
import argparse, csv, json, pathlib, sys

import numpy as np
import torch
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
from sklearn.metrics import f1_score

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import device_classifier as dc  # noqa: E402

FACTORS = (1.0, 0.75, 0.5, 0.35, 0.25)


def bands(x, ok, edges, labels):
    return [dict(name=n, n=int(((x >= a) & (x < b)).sum()), acc=float(ok[(x >= a) & (x < b)].mean()) if ((x >= a) & (x < b)).any() else None)
            for (a, b), n in zip(zip(edges[:-1], edges[1:]), labels)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=str(dc.RUN))
    ap.add_argument("--canvas-only", action="store_true", help="only the canvas comparison")
    ap.add_argument("--stress", action="store_true", help="only the stress test: harder versions of the test crops")
    ap.add_argument("--look-alikes-only", action="store_true", help="only the look-alikes of the wrong answers")
    ap.add_argument("--look-alikes-second", action="store_true",
                    help="only the look-alikes of the second model's wrong answers (models/device_cls/dinov2_b), in this model's features")
    ap.add_argument("--photo-size", action="store_true", help="only: accuracy on test crops from photos under 1 MP (not used in training)")
    a = ap.parse_args(); run = pathlib.Path(a.run).resolve()
    P = list(csv.DictReader(open(run / "test_predictions.csv")))
    if a.photo_size:
        return photo_size(run)
    if a.stress:
        return stress(run, P)
    if a.look_alikes_only:
        return look_alikes(run, P)
    if a.look_alikes_second:
        d2 = run.parent / "dinov2_b"
        B = {r["crop"]: r for r in csv.DictReader(open(d2 / "test_predictions.csv"))}
        return look_alikes(run, [dict(crop=p["crop"], **{"class": p["class"]}, pred=B[p["crop"]]["pred"]) for p in P if p["crop"] in B], d2 / "look_alikes.json")
    if a.canvas_only:
        names = json.load(open(run / "results.json"))["classes"]
        return canvas_compare(P, np.array([names.index(r["class"]) for r in P]))
    W, H = map(int, json.load(open(run / "results.json"))["canvas"])
    w = np.array([int(r["crop_w"]) for r in P]); h = np.array([int(r["crop_h"]) for r in P])
    ok = np.array([r["class"] == r["pred"] for r in P]); cls = np.array([r["class"] for r in P])
    s = np.minimum(W / w, H / h); fill = (w * s) * (h * s) / (W * H); kept = np.minimum(1, s ** 2)
    out = dict(canvas=[W, H], n=len(P), shrunk=float((s < 1).mean()), enlarged=float((s > 1).mean()),
               width_limited=float((W / w < H / h).mean()), median_scale=float(np.median(s)), median_fill=float(np.median(fill)),
               median_kept=float(np.median(kept)), kept_under_half=float((kept < .5).mean()), kept_under_quarter=float((kept < .25).mean()),
               median_aspect=float(np.median(w / h)),
               by_scale=bands(s, ok, [0, .25, .5, 1, 99], ["shrunk below 1/4", "shrunk 1/4 to 1/2", "shrunk 1/2 to 1", "enlarged"]),
               by_fill=bands(fill, ok, [0, .4, .6, .8, 1.01], ["under 40 %", "40 to 60 %", "60 to 80 %", "80 % or more"]),
               per_class={c: dict(n=int((cls == c).sum()), median_h=float(np.median(h[cls == c])), median_scale=float(np.median(s[cls == c])),
                                  median_fill=float(np.median(fill[cls == c])), acc=float(ok[cls == c].mean())) for c in sorted(set(cls))})

    look_alikes(run, P)
    clf = dc.DeviceClassifier(run=run)
    clf.cut, clf.bank = None, None
    y = np.array([clf.classes.index(c) for c in cls]); test = []
    plain = dc.letterbox
    for f in FACTORS:
        def coarse(im, canvas, f=f):
            x = plain(im, canvas)
            if f == 1.0:
                return x
            small = x.resize((max(1, round(canvas[0] * f)), max(1, round(canvas[1] * f))), Image.BICUBIC)
            return small.resize(canvas, Image.BICUBIC)
        dc.letterbox = coarse
        pred = []
        for i in range(0, len(P), 64):
            crops = [Image.open(ROOT / "data" / "device_crops" / r["crop"]).convert("RGB") for r in P[i:i + 64]]
            pred.extend(clf.scores(crops)[0].argmax(1).tolist())
        pred = np.array(pred)
        test.append(dict(factor=f, canvas=[round(W * f), round(H * f)], acc=float((pred == y).mean()),
                         macro_f1=float(f1_score(y, pred, average="macro")),
                         per_class={c: float((pred[y == i] == i).mean()) for i, c in enumerate(clf.classes)}))
        print(f"canvas {round(W * f)} x {round(H * f)}: acc {100 * test[-1]['acc']:.1f} %  macro-F1 {100 * test[-1]['macro_f1']:.1f} %", flush=True)
    dc.letterbox = plain
    out["resolution_test"] = test
    if (run / "resolution.json").exists():                 # keep the small-photo check from --photo-size
        old = json.load(open(run / "resolution.json"))
        if "small_photos" in old:
            out["small_photos"] = old["small_photos"]
    json.dump(out, open(run / "resolution.json", "w"), indent=1)
    canvas_compare(P, y)


def look_alikes(run, P, out_file=None):
    """For every wrong test answer, the training crops of the answered type that the model finds most similar
    (cosine on its features, against train_features.npz) -> look_alikes.json {test crop: {crop, sim, near: [10 nearest]}}.
    P can hold another model's answers (crop, class, pred); answers of a type this model was not trained on are skipped."""
    clf = dc.DeviceClassifier(run=run)
    train = [r for r in csv.DictReader(open(ROOT / "data" / "device_crops" / "manifest.csv"))
             if r["split"] == "train" and r["crop"] and r["class"] in clf.classes and int(r["image_w"]) * int(r["image_h"]) >= 1_000_000]
    assert clf.bank is not None and len(train) == clf.bank.shape[0], "train_features.npz does not match the training crops"
    tcls = np.array([r["class"] for r in train])
    W = [p for p in P if p["class"] != p["pred"] and p["pred"] in set(tcls)]
    out = {}
    for i in range(0, len(W), 32):
        f = clf.scores([Image.open(ROOT / "data" / "device_crops" / p["crop"]).convert("RGB") for p in W[i:i + 32]])[1].to(clf.bank.device)
        sims = (f / f.norm(dim=1, keepdim=True)) @ clf.bank.T
        for p, row in zip(W[i:i + 32], sims.cpu().numpy()):
            idx = np.flatnonzero(tcls == p["pred"]); top = idx[np.argsort(-row[idx])[:10]]
            out[p["crop"]] = {"crop": train[top[0]]["crop"], "sim": float(row[top[0]]),
                              "near": [{"crop": train[j]["crop"], "sim": float(row[j])} for j in top]}
    json.dump(out, open(out_file or run / "look_alikes.json", "w"), indent=1)
    print("look-alikes for", len(out), "wrong answers ->", out_file or run / "look_alikes.json")


STRESS = {   # one controlled change at a time, applied to every test crop
    "normal": lambda im, r: im,
    "darker": lambda im, r: ImageEnhance.Brightness(im).enhance(0.45),
    "glare": lambda im, r: ImageEnhance.Brightness(im).enhance(1.7),
    "blurred": lambda im, r: im.filter(ImageFilter.GaussianBlur(radius=max(1.5, im.height / 60))),
    "tilted": lambda im, r: im.rotate(r.choice((-8, 8)), resample=Image.BICUBIC, expand=True),
    "cut off": lambda im, r: im.crop((int(im.width * .3), 0, im.width, im.height)) if r.random() < .5 else im.crop((0, 0, int(im.width * .7), im.height)),
    "covered": lambda im, r: _cover(im, r),
}


def _cover(im, r):
    """A grey band over a third of the device, like cables or a door hanging in front of it."""
    im = im.copy(); w, h = im.size; x = int(r.uniform(0, .67) * w)
    ImageDraw.Draw(im).rectangle([x, 0, x + w // 3, h], fill=(70, 70, 70))
    return im


def stress(run, P):
    """Accuracy and the Other answer when the test crops are made harder on purpose -> stress.json."""
    import random
    clf = dc.DeviceClassifier(run=run)
    out = []
    for name, f in STRESS.items():
        r = random.Random(0); n = right = other = ans_right = 0
        for i in range(0, len(P), 64):
            rows = P[i:i + 64]
            res = clf.classify([f(Image.open(ROOT / "data" / "device_crops" / p["crop"]).convert("RGB"), r) for p in rows])
            for p, q in zip(rows, res):
                n += 1; right += q["class"] == p["class"]
                if q["label"] == "Other":
                    other += 1
                else:
                    ans_right += q["label"] == p["class"]
        out.append(dict(name=name, n=n, acc=right / n, other=other / n, answers_right=ans_right / max(1, n - other), wrong_answers=n - other - ans_right))
        print(f"{name:8} acc {100 * right / n:5.1f} %  Other {100 * other / n:5.1f} %  answers right {100 * ans_right / max(1, n - other):5.1f} %  wrong {n - other - ans_right}", flush=True)
    json.dump(out, open(run / "stress.json", "w"), indent=1)


def photo_size(run):
    """The model is trained on photos of 1 MP or more. How does it do on the test crops from smaller photos?"""
    clf = dc.DeviceClassifier(run=run); clf.cut, clf.bank = None, None
    R = [r for r in csv.DictReader(open(ROOT / "data" / "device_crops" / "manifest.csv"))
         if r["split"] == "test" and r["crop"] and r["class"] in clf.classes and int(r["image_w"]) * int(r["image_h"]) < 1_000_000]
    y, pr = np.array([clf.classes.index(r["class"]) for r in R]), []
    for i in range(0, len(R), 64):
        pr.extend(clf.scores([Image.open(ROOT / "data" / "device_crops" / r["crop"]).convert("RGB") for r in R[i:i + 64]])[0].argmax(1).tolist())
    pr = np.array(pr); out = json.load(open(run / "resolution.json"))
    out["small_photos"] = dict(n=len(R), acc=float((pr == y).mean()), macro_f1=float(f1_score(y, pr, average="macro")))
    json.dump(out, open(run / "resolution.json", "w"), indent=1)
    print("photos under 1 MP:", out["small_photos"])


def canvas_compare(P, y):
    from scipy.stats import binomtest
    runs = sorted((ROOT / "models" / "device_cls").glob("convnext_tiny_canvas_*"), key=lambda d: [int(v) for v in d.name.split("_")[-1].split("x")])
    runs = [d for d in runs if (d / "best.pt").exists() and (d / "results.json").exists()]
    if not runs:
        return
    preds = {}
    for d in runs:
        clf = dc.DeviceClassifier(run=d); clf.cut, clf.bank = None, None
        pr = []
        for i in range(0, len(P), 32):
            pr.extend(clf.scores([Image.open(ROOT / "data" / "device_crops" / r["crop"]).convert("RGB") for r in P[i:i + 32]])[0].argmax(1).tolist())
        preds[d.name.split("_")[-1]] = np.array(pr)
        print(d.name, "acc", round(float((preds[d.name.split('_')[-1]] == y).mean()), 4), flush=True)
    base = preds.get("512x128")
    rows = []
    for c, pr in preds.items():
        r = dict(canvas=c, acc=float((pr == y).mean()), macro_f1=float(f1_score(y, pr, average="macro")),
                 per_class={k: float((pr[y == i] == i).mean()) for i, k in enumerate(clf.classes)},
                 secs_per_epoch=float(np.mean([h["secs"] for h in json.load(open(ROOT / "models/device_cls" / f"convnext_tiny_canvas_{c}" / "results.json"))["history"]])))
        if base is not None and c != "512x128":
            b, w = int(((base == y) & (pr != y)).sum()), int(((base != y) & (pr == y)).sum())
            r.update(fixed=w, broke=b, p=float(binomtest(min(b, w), b + w, .5).pvalue) if b + w else 1.0)
        rows.append(r)
    json.dump(rows, open(dc.RUN / "canvas_compare.json", "w"), indent=1)


if __name__ == "__main__":
    main()
