"""Can the RT-EOS logic improve occupancy accuracy?

Experiment: for every held-out crop, run the RT-EOS device detector (Models/device_finder.pt)
on the crop's source rack photo and measure how much of the crop's row is covered by a detected
device (any class except "empty"). Fuse that signal with our classifiers and score it.

Honesty note: the device detector was trained on the "train" split of the source dataset, so its
signal is evaluated separately on crops whose source photo is in the "test"/"valid" split (unseen
by the detector) — that is the number to trust.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))   # project root: src/occupancy.py and friends
import json
import pathlib
import time

import cv2
import numpy as np
import pandas as pd
from PIL import Image
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_predict

import occupancy as occ
from unit_grid import shrink_for_inference, load_model

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEVICE_FINDER = ROOT / "weights/device_finder.pt"
OUT = ROOT / "models"
LAB = ["unoccupied", "occupied"]

t0 = time.time()
man = pd.read_csv(ROOT / "manifest.csv")
val = man[man.part == "test"].copy()
val["y"] = (val["class"] != "Empty").astype(int)
val["file"] = val["crop"].str.replace(r"\.jpg$", ".jpg", regex=True)

# ── 1. classifier probabilities on the squashed val crops ─────────────────────
apps = {a.key: a for a in occ.build_approaches() if a.key != "ensemble" and a.available()}
crops, order = [], []
for _, r in val.iterrows():
    p = ROOT / "data/cls_data" / "val" / LAB[r.y] / r.file
    crops.append(Image.open(p).convert("RGB")); order.append(r.file)
probs = {k: np.concatenate([a.predict(crops[i:i + 256]) for i in range(0, len(crops), 256)])
         for k, a in apps.items()}
p_ens = np.mean(list(probs.values()), axis=0)
y = val["y"].to_numpy()
print(f"classifiers scored {len(y)} crops in {time.time()-t0:.0f}s; ensemble acc {((p_ens>=.5)==y).mean():.4f}")

# ── 2. device-detector coverage of each crop's row ────────────────────────────
det = load_model(str(DEVICE_FINDER))
names = {int(k): str(v).strip().lower() for k, v in det.names.items()}
print("device_finder classes:", names)
cache = {}
def detections(src):
    if src not in cache:
        img = cv2.imread(src)
        if img is None:
            cache[src] = []
        else:
            small, f = shrink_for_inference(img)
            r = det.predict(small, conf=0.2, iou=0.5, verbose=False)[0]
            cache[src] = [(names[int(c)], [v * f for v in b]) for b, c in
                          zip(r.boxes.xyxy.tolist(), r.boxes.cls.tolist())] if r.boxes is not None else []
    return cache[src]

cov_dev, cov_empty = [], []
for _, r in val.iterrows():
    y1, y2 = r.y1, r.y2
    h = max(1, y2 - y1)
    best_d = best_e = 0.0
    for name, (bx1, by1, bx2, by2) in detections(r.source_image):
        ov = max(0.0, min(y2, by2) - max(y1, by1)) / h
        if name == "empty":
            best_e = max(best_e, ov)
        else:
            best_d = max(best_d, ov)
    cov_dev.append(best_d); cov_empty.append(best_e)
cov_dev, cov_empty = np.array(cov_dev), np.array(cov_empty)
print(f"device detector run on {len(cache)} source photos in {time.time()-t0:.0f}s")

# ── 3. score: detector alone, fixed-rule fusion, learned fusion (5-fold CV) ───
unseen = ~val["split"].str.lower().isin(["train"]).to_numpy()   # detector never saw these photos
def acc(pred, mask=None):
    m = np.ones_like(y, bool) if mask is None else mask
    return float((pred[m] == y[m]).mean())

p_det = np.clip(0.5 + 0.5 * (cov_dev - cov_empty), 0, 1)          # 1 = device covers row, 0 = "empty" box covers row
p_rule = 0.5 * p_ens + 0.5 * p_det
X = np.column_stack([*probs.values(), cov_dev, cov_empty])
p_lr = cross_val_predict(LogisticRegression(max_iter=2000), X, y, cv=5, method="predict_proba")[:, 1]

results = {
    "n_val": int(len(y)), "n_unseen_by_detector": int(unseen.sum()),
    "classifier_ensemble": {"all": acc(p_ens >= .5), "unseen": acc(p_ens >= .5, unseen)},
    "device_detector_only": {"all": acc(p_det >= .5), "unseen": acc(p_det >= .5, unseen)},
    "fusion_fixed_rule": {"all": acc(p_rule >= .5), "unseen": acc(p_rule >= .5, unseen)},
    "fusion_learned_cv5": {"all": acc(p_lr >= .5), "unseen": acc(p_lr >= .5, unseen)},
}
json.dump(results, open(OUT / "fusion_eval.json", "w"), indent=1)
np.savez(OUT / "fusion_signals.npz", y=y, p_ens=p_ens, cov_dev=cov_dev, cov_empty=cov_empty, unseen=unseen,
         files=np.array(order))
for k, v in results.items():
    print(k, v)
print(f"done in {time.time()-t0:.0f}s")
