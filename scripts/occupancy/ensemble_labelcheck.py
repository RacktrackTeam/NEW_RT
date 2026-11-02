"""Data for the portal's "Label check" tab: the wrong labels found in train, valid and test, and what was fixed.

    python3 scripts/occupancy/ensemble_labelcheck.py      (after scripts/occupancy/apply_label_fixes.py)

Test: every unit the Ensemble disagreed with was checked by eye (the Ensemble review). Train and valid: the
candidates in data/datasets/occupancy/labels/label_review_trainvalid.csv (the model's disagreements, plus the not_trained_429_v6 batch
whose UPS and Empty labels were swapped), each checked by eye. Writes `label_check` into data.json and a small
picture of every candidate (the unit outlined in its rack photo) into img/.
"""
import json
import pathlib

import pandas as pd
import sys

from PIL import Image, ImageOps

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "webapp" / "static" / "ensemble_portal"
DATA = OUT / "data.json"
REVIEW = ROOT / "data" / "datasets" / "occupancy" / "labels" / "label_review_trainvalid.csv"
FIXES = ROOT / "data" / "datasets" / "occupancy" / "labels" / "label_fixes.csv"
USER = ROOT / "data" / "datasets" / "occupancy" / "labels" / "ensemble_review_user.json"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ensemble_portal_data as portal  # noqa: E402


def picture(r, name):
    """The unit outlined by its label polygon, in its rack photo."""
    path = OUT / "img" / name
    if path.exists():
        return f"img/{name}"
    src = ImageOps.exif_transpose(Image.open(r["source_image"])).convert("RGB")
    x1, y1, x2, y2 = (int(r[k]) for k in ("x1", "y1", "x2", "y2"))
    h, w = y2 - y1, x2 - x1
    ox, oy = max(0, x1 - int(.1 * w)), max(0, y1 - int(1.5 * max(h, 30)))
    c = src.crop((ox, oy, min(src.width, x2 + int(.1 * w)), min(src.height, y2 + int(1.5 * max(h, 30))))).copy()
    portal.draw_polygon(c, portal.label_polygon(r["source_image"], r["label_line"], src.width, src.height), ox, oy, max(3, c.width // 120))
    c.thumbnail((360, 240))
    c.save(path, quality=82)
    return f"img/{name}"


def batch_agreement(before):
    """How often the Ensemble agrees with each class label, inside batch not_trained_429_v6 and in all other batches
    (original labels, every split): the two swapped classes stand out."""
    import numpy as np
    ens = np.load(ROOT / "results" / "occupancy" / "probs" / "ensemble.npz", allow_pickle=False)
    parts = []
    for sp, key in (("train", "p_train"), ("valid", "p_valid"), ("test", "p_test")):
        m = before[before.split == sp]
        f = sorted(m[m.folder == "unoccupied"].crop) + sorted(m[m.folder == "occupied"].crop)
        mm = m.set_index("crop").loc[f].copy()
        mm["agree"] = (ens[key] >= .5) == (mm.folder == "occupied")
        parts.append(mm)
    d = pd.concat(parts)
    d["batch"] = d.source_image.str.contains("429_v6")
    out = []
    for c, g in d.groupby("class"):
        b, o = g[g.batch], g[~g.batch]
        out.append({"class": c, "batch_units": int(len(b)), "batch_agree": float(b.agree.mean()) if len(b) else None,
                    "other_units": int(len(o)), "other_agree": float(o.agree.mean())})
    return sorted(out, key=lambda r: (r["batch_agree"] is None, r["batch_agree"] if r["batch_agree"] is not None else 1))


def main():
    data = json.loads(DATA.read_text())
    before = pd.read_csv(ROOT / "data/datasets/occupancy" / "manifest.before_label_fixes.csv")
    after = pd.read_csv(ROOT / "data/datasets/occupancy" / "manifest.csv")
    tv = pd.read_csv(REVIEW).merge(before[["crop", "split", "label_line"]], on=["crop", "split"], how="left")
    items = []
    for i, r in tv.iterrows():
        items.append({"split": r.split, "crop": r.crop, "before": r.label,
                      "after": r.new_label if r.verdict == "wrong" else r.label, "verdict": r.verdict,
                      "found_by": r.found_by, "img": picture(r, f"lc_{r.split}_{i:04d}.jpg")})
    # test: every Ensemble mistake, with the by-eye verdict (the person's own call when there is one)
    user = json.loads(USER.read_text()) if USER.exists() else {}
    fixes = pd.read_csv(FIXES)
    removed = set(fixes[fixes.action == "remove"].crop)
    unchecked = []
    for u in data["units"]:
        if u["leaning_right"]:
            continue
        v = (user.get(u["id"]) or {}).get("verdict") or u.get("verdict")
        if v is None:                     # a unit the ensemble newly disagrees with (five members since 2026-10-19), not checked by eye yet
            unchecked.append(u["file"]); continue
        verdict = {"label": "wrong", "model": "right", "unclear": "unclear"}[v]
        items.append({"split": "test", "crop": u["file"], "before": u["truth"],
                      "after": u["leaning"] if verdict == "wrong" else u["truth"], "verdict": verdict,
                      "found_by": "model disagrees", "img": u["ctx"], "review_no": u["review_no"]})
    splits = {}
    for sp in ("train", "valid", "test"):
        b, a = before[before.split == sp], after[after.split == sp]
        its = [x for x in items if x["split"] == sp]
        splits[sp] = {"units": int(len(b)), "checked": len(its),
                      "wrong": sum(x["verdict"] == "wrong" for x in its), "unclear": sum(x["verdict"] == "unclear" for x in its),
                      "right": sum(x["verdict"] == "right" for x in its),
                      "removed": int(sum(c in removed for c in b.crop)) if sp == "test" else 0,
                      "before": {"occupied": int((b.folder == "occupied").sum()), "unoccupied": int((b.folder == "unoccupied").sum())},
                      "after": {"occupied": int((a.folder == "occupied").sum()), "unoccupied": int((a.folder == "unoccupied").sum())},
                      "batch": sum(x["verdict"] == "wrong" and "429_v6" in x["crop"] for x in its)}
    data["label_check"] = {"splits": splits, "items": items, "batch_agreement": batch_agreement(before), "unchecked": unchecked}
    print(f"{len(unchecked)} test units the ensemble disagrees with are not checked by eye yet:", unchecked)
    DATA.write_text(json.dumps(data, indent=1))
    print(json.dumps(splits, indent=1))


if __name__ == "__main__":
    main()
