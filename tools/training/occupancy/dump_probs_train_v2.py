"""Add P(occupied) on the v2 TRAINING split (12,800 units) to results/occupancy/probs/<key>.npz (p_train, y_train).

    python3 dump_probs_train_v2.py

Every single approach is scored; the Ensemble's training scores are the mean of its six members
(exactly what EnsembleApproach.predict computes), so it is not run a second time.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))   # project root: src/occupancy.py and friends
import pathlib

import numpy as np
from PIL import Image

import occupancy as occ

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = ROOT / "data/datasets/occupancy_224"
NATIVE = ROOT / "data/datasets/occupancy"
PROBS = ROOT / "results" / "occupancy" / "probs"
LAB = ["unoccupied", "occupied"]

files, y = [], []
for label, cls in enumerate(LAB):
    f = sorted((DATA / "train" / cls).glob("*.jpg"))
    files += f
    y += [label] * len(f)
y = np.array(y)


def score(a):
    out = []
    for i in range(0, len(files), 128):
        batch = files[i:i + 128]
        if a.key == "fusion_t2":   # edge features need the native crop shape
            ims = [Image.open(NATIVE / "train" / LAB[yy] / f.name).convert("RGB") for f, yy in zip(batch, y[i:i + 128])]
        else:
            ims = [Image.open(f).convert("RGB") for f in batch]
        out.append(a.predict(ims))
    return np.concatenate(out)


apps = occ.build_approaches("v2")
member_train = {}
for a in apps:
    if a.key == "ensemble" or not a.available():
        continue
    npz = PROBS / f"{a.key}.npz"
    d = dict(np.load(npz, allow_pickle=False))
    if "p_train" not in d:
        d["p_train"], d["y_train"] = score(a), y
        np.savez(npz, **d)
    member_train[a.key] = d["p_train"]
    print(f"{a.short:22s} train acc {((d['p_train'] >= .5) == y).mean():.4f}", flush=True)
    getattr(a, "unload", lambda: None)()
    occ.free_memory()

ens = next(a for a in apps if a.key == "ensemble")
p = np.mean([member_train[m.key] for m in ens.members if m.key in member_train], axis=0)
d = dict(np.load(PROBS / "ensemble.npz", allow_pickle=False))
d["p_train"], d["y_train"] = p, y
np.savez(PROBS / "ensemble.npz", **d)
print(f"{'Ensemble':22s} train acc {((p >= .5) == y).mean():.4f}")
