"""Rebuild data/datasets/occupancy_224/ (the 224x224 training copy of the v2 dataset) from data/datasets/occupancy/.

    python3 scripts/occupancy/make_cls_data_v2.py [out_dir]       (default: data/datasets/occupancy_224)

Each crop is squashed to 224x224 (bilinear) and saved as JPEG quality 92; the "valid" split is written as "val"
(the folder name Ultralytics expects). Needed before retraining (scripts/occupancy/train_v2.py) or re-scoring
(scripts/occupancy/dump_probs_v2.py, scripts/occupancy/dump_probs_train_v2.py).
"""
import pathlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "data/datasets/occupancy"
OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data/datasets/occupancy_224"

jobs = []
for split, name in (("train", "train"), ("valid", "val"), ("test", "test")):
    for cls in ("occupied", "unoccupied"):
        (OUT / name / cls).mkdir(parents=True, exist_ok=True)
        jobs += [(f, OUT / name / cls / f.name) for f in sorted((SRC / split / cls).glob("*.jpg"))]


def work(job):
    src, dst = job
    if not dst.exists():
        Image.open(src).convert("RGB").resize((224, 224), Image.BILINEAR).save(dst, quality=92)


t = time.time()
with ThreadPoolExecutor(8) as ex:
    list(ex.map(work, jobs))
print("built", len(jobs), "in", round(time.time() - t, 1), "s ->", OUT)
