"""Save P(occupied) for every v2 valid + test crop, per approach, for the probability / threshold report.

    python3 dump_probs_v2.py [key ...]      (default: every ready v2 approach except the ensemble)

Writes results/occupancy/probs/<key>.npz with p_valid, y_valid, p_test, y_test, files_test, class_test.
Crop-level, like every held-out score: no rack photo, so the RT-EOS device cross-check is not in play.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))   # project root: src/occupancy.py and friends
import pathlib
import sys

import numpy as np
import pandas as pd
from PIL import Image

import occupancy as occ

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = ROOT / "data/datasets/occupancy_224"
OUT = ROOT / "results" / "occupancy" / "probs"
OUT.mkdir(parents=True, exist_ok=True)
LAB = ["unoccupied", "occupied"]
NATIVE = ROOT / "data/datasets/occupancy"          # fusion's edge features need the native crop shape
man = pd.read_csv(NATIVE / "manifest.csv").set_index("crop")


def split_files(split):
    fs, y = [], []
    for label, cls in enumerate(LAB):
        f = sorted((DATA / split / cls).glob("*.jpg"))
        fs += f
        y += [label] * len(f)
    return fs, np.array(y)


def probs(a, fs, native_split):
    out = []
    for i in range(0, len(fs), 128):
        batch = fs[i:i + 128]
        if a.key == "fusion_t2":
            ims = [Image.open(NATIVE / native_split / LAB[0 if "unoccupied" in str(f) else 1] / f.name).convert("RGB")
                   for f in batch]
        else:
            ims = [Image.open(f).convert("RGB") for f in batch]
        out.append(a.predict(ims))
    return np.concatenate(out)


keys = sys.argv[1:]
for a in occ.build_approaches("v2"):
    if (a.key == "ensemble" and "ensemble" not in keys) or not a.available() or (keys and a.key not in keys):
        continue
    fv, yv = split_files("val")
    ft, yt = split_files("test")
    pv = probs(a, fv, "valid")
    pt = probs(a, ft, "test")
    cls = np.array([man.loc[f.name, "class"] if f.name in man.index else "?" for f in ft])
    np.savez(OUT / f"{a.key}.npz", p_valid=pv, y_valid=yv, p_test=pt, y_test=yt,
             files_test=np.array([f.name for f in ft]), class_test=cls)
    print(f"{a.short:22s} valid acc {((pv >= .5) == yv).mean():.4f}  test acc {((pt >= .5) == yt).mean():.4f}", flush=True)
    getattr(a, "unload", lambda: None)()
    occ.free_memory()
