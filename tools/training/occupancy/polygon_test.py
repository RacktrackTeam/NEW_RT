"""Score the Ensemble on cut-outs made from the hand-drawn polygons instead of the boxes around them.

    python3 scripts/occupancy/polygon_test.py

Each unit's polygon (YOLO segmentation label, almost always four corners) is cut along its own corners and
straightened into a flat rectangle (perspective warp), so on an angled photo the model sees only that unit and not
the neighbours that an upright box takes in. A plain box label is cut as before. The cut-out then goes through the
same preparation as the training data (224 x 224, JPEG 92). Both test sets of the "Two test sets" tab (everything; photos of at least 1 megapixel) are scored
against the same (fixed) labels; results go to data/test_sets/occupancy/extra/polygon_units.csv and data/test_sets/occupancy/extra/polygon_results.json.
"""
import io
import json
import pathlib
import sys

import cv2
import numpy as np
import pandas as pd
from PIL import Image, ImageOps

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import occupancy as occ  # noqa: E402

X = ROOT / "data/test_sets/occupancy/extra"
CUT = 0.65
HIRES_MP = 1.0          # set 2 of the "Two test sets" tab: photos of at least 1 megapixel


def corners(xy, W, H):
    """Four corners (TL, TR, BR, BL) in pixels from a YOLO label's numbers, or None for a plain box."""
    if len(xy) == 4:
        return None
    p = np.array(xy, dtype=np.float32).reshape(-1, 2) * [W, H]
    keep = [p[0]]
    for q in p[1:]:
        if np.linalg.norm(q - keep[-1]) > 1 and np.linalg.norm(q - keep[0]) > 1:
            keep.append(q)
    p = np.array(keep, dtype=np.float32)
    if len(p) != 4:
        p = cv2.boxPoints(cv2.minAreaRect(p)).astype(np.float32)
    s, d = p.sum(1), np.diff(p, axis=1).ravel()
    return np.array([p[s.argmin()], p[d.argmin()], p[s.argmax()], p[d.argmax()]], dtype=np.float32)


def polygon_crop(img, xy):
    W, H = img.size
    c = corners(xy, W, H)
    if c is None:                                              # plain box: cx cy w h
        cx, cy, w, h = xy
        return img.crop((int((cx - w / 2) * W), int((cy - h / 2) * H), int((cx + w / 2) * W), int((cy + h / 2) * H)))
    tl, tr, br, bl = c
    w = int(max(4, (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2))
    h = int(max(4, (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2))
    M = cv2.getPerspectiveTransform(c, np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32))
    return Image.fromarray(cv2.warpPerspective(np.asarray(img), M, (w, h), flags=cv2.INTER_LINEAR))


def model_input(crop):
    b = io.BytesIO()
    crop.convert("RGB").resize((224, 224), Image.BILINEAR).save(b, "JPEG", quality=92)
    return Image.open(io.BytesIO(b.getvalue())).convert("RGB")


def label_xy(label_file, line):
    return list(map(float, pathlib.Path(label_file).read_text().split("\n")[line].split()[1:]))


def main():
    man = pd.read_csv(ROOT / "data/datasets/occupancy" / "manifest.csv")
    t = man[man.split == "test"]
    ens = np.load(ROOT / "results" / "occupancy" / "probs" / "ensemble.npz", allow_pickle=False)
    p_box = dict(zip(ens["files_test"].astype(str), ens["p_test"]))
    mp = {}

    def megapixels(path):
        if path not in mp:
            w, h = Image.open(path).size
            mp[path] = w * h / 1e6
        return mp[path]
    side = set(json.loads((ROOT / "models" / "v2" / "side_angle_photos.json").read_text())["photos"])
    rows = []
    for r in t.itertuples():
        rows.append({"set": "dataset", "id": r.crop, "photo": r.source_image, "truth": r.folder, "p_box": float(p_box[r.crop]),
                     "label_file": r.source_image.replace("/images/", "/labels/").rsplit(".", 1)[0] + ".txt", "line": r.label_line,
                     "mp": megapixels(r.source_image), "side": pathlib.Path(r.source_image).name in side})
    ex = pd.read_csv(X / "units.csv")
    ex = ex[ex.source.str.startswith("test folder")]
    for r in ex.itertuples():
        rows.append({"set": "new", "id": f"{r.photo}#{r.unit}", "photo": str(ROOT / "data" / "test_sets" / "units" / "images" / r.photo), "truth": r.truth,
                     "p_box": float(r.p_occupied), "label_file": str(ROOT / "data" / "test_sets" / "units" / "device_labels" / (r.photo.rsplit(".", 1)[0] + ".txt")),
                     "line": int(r.unit[1:]), "mp": megapixels(str(ROOT / "data" / "test_sets" / "units" / "images" / r.photo)), "side": False})
    df = pd.DataFrame(rows)
    df["hires"] = df.mp >= HIRES_MP
    a = next(x for x in occ.build_approaches("v2") if x.key == "ensemble")
    p_poly, cache = [], {}
    for i in range(0, len(df), 64):
        crops = []
        for r in df.iloc[i:i + 64].itertuples():
            if r.photo not in cache:
                cache.clear()
                cache[r.photo] = ImageOps.exif_transpose(Image.open(r.photo)).convert("RGB")
            crops.append(model_input(polygon_crop(cache[r.photo], label_xy(r.label_file, r.line))))
        p_poly += list(np.asarray(a.predict(crops), dtype=float))
        print(f"{min(i + 64, len(df))}/{len(df)}", flush=True)
    df["p_poly"] = p_poly
    df.drop(columns=["label_file"]).to_csv(X / "polygon_units.csv", index=False)

    def score(d, col):
        pred = np.where(d[col] >= .5, "occupied", "unoccupied")
        sure = np.maximum(d[col], 1 - d[col])
        ans = sure >= CUT
        return {"units": len(d), "right_alone": int((pred == d.truth).sum()), "answered": int(ans.sum()),
                "wrong_answers": int((ans & (pred != d.truth)).sum()), "uncertain": int((~ans).sum())}
    subsets = {"overall": df, "highres": df[df.hires], "side": df[df.side], "front": df[(df.set == "dataset") & ~df.side]}
    res = {k: {"box": score(d, "p_box"), "polygon": score(d, "p_poly")} for k, d in subsets.items()}
    (X / "polygon_results.json").write_text(json.dumps(res, indent=1))
    for k, v in res.items():
        for kind, s in v.items():
            print(f"{k:8s} {kind:8s} units {s['units']:5d} | alone {100 * s['right_alone'] / s['units']:.2f}% | "
                  f"rule {100 * (s['answered'] - s['wrong_answers']) / s['answered']:.2f}% | wrong {s['wrong_answers']} | to person {s['uncertain']}")


if __name__ == "__main__":
    main()
