"""Score teammate 1 v2 (DINOv2-L k-NN) on the part of our v2 test split it has never seen.

Their gallery (models/v2/knn_v2_gallery.pkl) contains 376 of our 1,600 test crops and crops from 182 of the 412 test
rack photos, so only test crops whose rack photo has no crop in their gallery are scored. Native crops are
used (their embedder resizes each whole crop to 112 x 448). Their own reported result is kept for reference.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))   # project root: src/occupancy.py and friends
import json
import pathlib
import pickle

import numpy as np
import pandas as pd
from PIL import Image

from teammate1v2_approach import KnnV2Approach, GALLERY

ROOT = pathlib.Path(__file__).resolve().parents[2]
LAB = ["unoccupied", "occupied"]
with open(GALLERY, "rb") as f:     # inspected: numpy arrays only
    gal = {pathlib.Path(p).name for p in pickle.load(f)["paths"]}
man = pd.read_csv(ROOT / "data/datasets/occupancy" / "manifest.csv")
seen_photos = set(man[man.crop.isin(gal)].source_image)
test = man[(man.split == "test") & ~man.source_image.isin(seen_photos)]
y = (test.folder == "occupied").astype(int).to_numpy()
a = KnnV2Approach("v2")                          # blank panels aligned to v2 (as used in the app)
raw = KnnV2Approach("v2", align_blank_panels=False)  # exactly as delivered
files = [ROOT / "data/datasets/occupancy" / "test" / fo / cr for fo, cr in zip(test.folder, test.crop)]
p = np.concatenate([a.predict([Image.open(f).convert("RGB") for f in files[i:i + 64]]) for i in range(0, len(files), 64)])
p_raw = np.concatenate([raw.predict([Image.open(f).convert("RGB") for f in files[i:i + 64]]) for i in range(0, len(files), 64)])
pred = (p >= 0.5).astype(int)
cls = test["class"].to_numpy().astype(str)
not_blank = cls != "Closed Unit"
conf = np.maximum(p, 1 - p)
cm = {t: {q: 0 for q in LAB} for t in LAB}
errors = []
for f, yt, yp, pp in zip(files, y, pred, p):
    cm[LAB[yt]][LAB[yp]] += 1
    if yt != yp:
        errors.append({"file": f.name, "true": LAB[yt], "pred": LAB[yp], "p_occupied": float(pp)})
sure = conf >= a.uncertain_below
out = {"approach": a.name, "dataset": "v2 (unseen part)", "val_accuracy": float((pred == y).mean()), "n_val": int(len(y)),
       "confusion": cm, "errors": errors,
       "scored_on": f"{len(y)} of 1,600 v2 test units: those from the {test.source_image.nunique()} test rack photos with no crop in the teammate's gallery",
       "uncertain_pct": float(100 * (~sure).mean()), "accuracy_when_sure": float((pred[sure] == y[sure]).mean()),
       "as_delivered_accuracy": float(((p_raw >= .5).astype(int) == y).mean()),
       "as_delivered_accuracy_without_blank_panels": float((((p_raw >= .5).astype(int) == y)[not_blank]).mean()),
       "reported_by_teammate": {"accuracy": 0.963, "units": 575, "needs_review_pct": 4.9, "accuracy_when_confident": 0.976}}
a.eval_file.parent.mkdir(parents=True, exist_ok=True)
json.dump(out, open(a.eval_file, "w"), indent=1)
np.savez(ROOT / "results" / "occupancy" / "probs" / "knn_v2_unseen.npz", p=p, p_raw=p_raw, y=y,
         files=np.array([f.name for f in files]).astype(str), cls=cls)
print(f"DINOv2-L k-NN on {len(y)} unseen v2 test units: {out['val_accuracy']:.4f} | uncertain (<70%) {out['uncertain_pct']:.1f}% "
      f"| right when sure {out['accuracy_when_sure']:.4f} | {cm}\n"
      f"as delivered: {out['as_delivered_accuracy']:.4f} | as delivered, blank panels excluded: {out['as_delivered_accuracy_without_blank_panels']:.4f}")
