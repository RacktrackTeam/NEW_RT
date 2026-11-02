"""Data + pictures for the Ensemble error-analysis portal (data/eval/ensemble_portal/).

    python3 scripts/occupancy/ensemble_portal_data.py

Goal of the experiment: every unit gets ONE of two outcomes - a classification (occupied / unoccupied) or
"uncertain" (the Ensemble is less than 65 % sure, a person checks it). This writes:
  data.json  dataset sizes, the outcome funnel, per-class numbers, a cut-off sweep and every test unit that is
             a mistake or uncertain, with all six models' votes;
  img/       for each of those units: the cut-out the model saw, the unit in its rack photo (context) and the
             whole photo with the unit outlined.
The by-eye verdict for every mistake lives in data/datasets/occupancy/labels/ensemble_error_review.json and is merged in.
"""
import json
import pathlib

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "eval" / "ensemble_portal"
IMG = OUT / "img"
PROBS = ROOT / "results" / "occupancy" / "probs"
REVIEW = ROOT / "data" / "datasets" / "occupancy" / "labels" / "ensemble_error_review.json"
REMOVED = ROOT / "data" / "datasets" / "occupancy" / "labels" / "test_removed.csv"   # units taken out of the test set (scripts/occupancy/apply_label_fixes.py)
CUT = 0.65
MEMBERS = {"yolov8m_cls": "YOLOv8m", "yolo11s_cls": "YOLO11s", "embed_dinov2": "DINOv2", "embed_clip": "CLIP",
           "teammate": "ResNet50"}                     # Classical left the ensemble (user, 2026-10-19)


def font(n):
    try:
        return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", n)
    except OSError:
        return ImageFont.load_default()


def label_polygon(source_image, line, W, H):
    """The unit's hand-drawn polygon in pixels, from its YOLO label line (a plain box label becomes its 4 corners)."""
    lab = pathlib.Path(str(source_image).replace("/images/", "/labels/")).with_suffix(".txt")
    v = list(map(float, lab.read_text().split("\n")[int(line)].split()[1:]))
    if len(v) == 4:
        cx, cy, w, h = v
        v = [cx - w / 2, cy - h / 2, cx + w / 2, cy - h / 2, cx + w / 2, cy + h / 2, cx - w / 2, cy + h / 2]
    return [(v[i] * W, v[i + 1] * H) for i in range(0, len(v) - 1, 2)]


def draw_polygon(img, pts, dx=0, dy=0, width=3):
    q = [(x - dx, y - dy) for x, y in pts]
    ImageDraw.Draw(img).line(q + [q[0]], fill=(255, 196, 0), width=width, joint="curve")


def pictures(row, stem, crop_split="test"):
    """The cut-out the model saw, the unit in its photo and the whole photo, the unit outlined by its label polygon."""
    src = ImageOps.exif_transpose(Image.open(row.source_image)).convert("RGB")
    x1, y1, x2, y2 = int(row.x1), int(row.y1), int(row.x2), int(row.y2)
    poly = label_polygon(row.source_image, row.label_line, src.width, src.height)
    crop = Image.open(ROOT / "data/datasets/occupancy" / crop_split / row.folder / row.crop).convert("RGB")
    crop.thumbnail((640, 240)); crop.save(IMG / f"{stem}_crop.jpg", quality=86)
    h = y2 - y1; w = x2 - x1
    cx1, cy1 = max(0, x1 - int(0.15 * w)), max(0, y1 - int(1.2 * max(h, 40)))
    cx2, cy2 = min(src.width, x2 + int(0.15 * w)), min(src.height, y2 + int(1.2 * max(h, 40)))
    ctx = src.crop((cx1, cy1, cx2, cy2)).copy()
    draw_polygon(ctx, poly, cx1, cy1, max(3, ctx.width // 150))
    ctx.thumbnail((640, 420)); ctx.save(IMG / f"{stem}_ctx.jpg", quality=84)
    full = src.copy()
    draw_polygon(full, poly, 0, 0, max(4, full.width // 120))
    full.thumbnail((520, 700)); full.save(IMG / f"{stem}_full.jpg", quality=80)


# Clear unoccupied examples, picked by eye: random ones too often show a wrong label (a device on a shelf).
UNOCCUPIED_EXAMPLES = ["Closed_Unit__train__Device_annotation_Trained_2511_V1_valid_4055__1.jpg",
                       "Empty__train__Device_Annotation_Not_traned_667_v2_train_1395__7.jpg",
                       "Empty__valid__Device_annotation_Not_trained_370_V4_test_453__10.jpg",
                       "Empty__train__Device_annotation_Trained_2511_V1_train_2853__3.jpg"]


def kept(files):
    """False for test units taken out of the test set; they are left out of every count. Unit ids keep their index."""
    gone = set(pd.read_csv(REMOVED).crop) if REMOVED.exists() else set()
    return ~np.isin(files, list(gone))


def samples(man, n=4, seed=7):
    """A few training cut-outs per class for the Summary page ("what the models learned from")."""
    out = {}
    for folder in ("occupied", "unoccupied"):
        m = man[(man.split == "train") & (man.folder == folder)]
        if folder == "unoccupied" and m.crop.isin(UNOCCUPIED_EXAMPLES).sum() == n:
            m = m[m.crop.isin(UNOCCUPIED_EXAMPLES)]
        # one per hand-label class first, so the examples are not all the same kind of unit
        pick = m.groupby("class", group_keys=False).apply(lambda g: g.sample(1, random_state=seed))
        pick = pick.sample(min(n, len(pick)), random_state=seed)
        if len(pick) < n:   # unoccupied has only two hand-label classes: top up with other units
            pick = pd.concat([pick, m.drop(pick.index).sample(n - len(pick), random_state=seed)])
        out[folder] = []
        for i, row in enumerate(pick.itertuples()):
            im = Image.open(ROOT / "data/datasets/occupancy" / "train" / folder / row.crop).convert("RGB")
            im.thumbnail((360, 140))
            name = f"img/sample_{folder}_{i}.jpg"
            im.save(OUT / name, quality=86)
            out[folder].append(name)
    return out


def main():
    IMG.mkdir(parents=True, exist_ok=True)
    man = pd.read_csv(ROOT / "data/datasets/occupancy" / "manifest.csv")
    ens = np.load(PROBS / "ensemble.npz", allow_pickle=False)
    files, y, p, cls = ens["files_test"].astype(str), ens["y_test"], ens["p_test"], ens["class_test"].astype(str)
    votes = {}
    for k, name in MEMBERS.items():
        m = np.load(PROBS / f"{k}.npz", allow_pickle=False)
        assert (m["files_test"].astype(str) == files).all(), k
        votes[name] = m["p_test"]
    conf = np.maximum(p, 1 - p)
    pred = (p >= 0.5).astype(int)
    unc = conf < CUT
    right = pred == y
    K = kept(files)
    acc_member = {name: float(((v >= 0.5).astype(int) == y)[K].mean()) for name, v in votes.items()}

    # datasets
    sets = {}
    for split in ("train", "valid", "test"):
        m = man[man.split == split]
        sets[split] = {"units": int(len(m)), "occupied": int((m.folder == "occupied").sum()),
                       "unoccupied": int((m.folder == "unoccupied").sum()), "photos": int(m.source_image.nunique()),
                       "by_class": {c: int(n) for c, n in m["class"].value_counts().items()}}

    # funnel: the two outcomes
    funnel = {"units": int(K.sum()), "right": int((K & right).sum()), "wrong": int((K & ~right).sum()),
              "uncertain": int((K & unc).sum()), "uncertain_wrong": int((K & unc & ~right).sum()), "uncertain_right": int((K & unc & right).sum()),
              "decided": int((K & ~unc).sum()), "decided_right": int((K & ~unc & right).sum()), "decided_wrong": int((K & ~unc & ~right).sum()),
              "cutoff": CUT, "removed": int((~K).sum())}
    by_class = []
    for c in sorted(set(cls)):
        k = (cls == c) & K
        by_class.append({"class": c, "truth": "occupied" if y[k][0] == 1 else "unoccupied", "units": int(k.sum()),
                         "decided_right": int((k & ~unc & right).sum()), "uncertain": int((k & unc).sum()),
                         "decided_wrong": int((k & ~unc & ~right).sum())})
    sweep = []
    for c in np.round(np.arange(0.50, 0.951, 0.05), 2):
        u = conf < c
        sweep.append({"cutoff": float(c), "uncertain": float(u[K].mean()), "uncertain_n": int((u & K).sum()),
                      "wrong_unflagged": int((~u & ~right & K).sum()), "accuracy_decided": float(right[~u & K].mean()) if (~u & K).any() else None})

    review = json.loads(REVIEW.read_text()) if REVIEW.exists() else {}
    units = []
    for i in np.where((~right | unc) & K)[0]:
        row = man[(man.crop == files[i]) & (man.split == "test")].iloc[0]
        stem = f"u{i:04d}"
        pictures(row, stem)
        rec = {"id": stem, "file": files[i], "class": cls[i], "truth": "occupied" if y[i] == 1 else "unoccupied",
               "p_occupied": float(p[i]), "sure": float(conf[i]),
               "outcome": "uncertain" if unc[i] else "wrong",
               "leaning": "occupied" if p[i] >= 0.5 else "unoccupied", "leaning_right": bool(right[i]),
               "votes": {n: float(v[i]) for n, v in votes.items()},
               "photo": pathlib.Path(row.source_image).name, "box": [int(row.x1), int(row.y1), int(row.x2), int(row.y2)],
               "crop": f"img/{stem}_crop.jpg", "ctx": f"img/{stem}_ctx.jpg", "full": f"img/{stem}_full.jpg"}
        rec.update(review.get(files[i], {}))
        units.append(rec)
    data = {"datasets": sets, "funnel": funnel, "by_class": by_class, "sweep": sweep,
            "members": [{"name": n, "accuracy": a} for n, a in acc_member.items()],
            "ensemble_accuracy": float(right[K].mean()), "units": units, "samples": samples(man),
            "reviewed": sum(1 for u in units if u.get("verdict"))}
    (OUT / "data.json").write_text(json.dumps(data, indent=1))
    print(json.dumps(funnel), "| units with pictures:", len(units), "| reviewed:", data["reviewed"])


if __name__ == "__main__":
    main()
