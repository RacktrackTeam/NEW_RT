"""Correct the test-set labels that the Ensemble mistake review found wrong.

    python3 scripts/occupancy/apply_label_fixes.py           # apply: move each cut-out to its right folder, update manifest.csv
    python3 scripts/occupancy/apply_label_fixes.py --undo    # put every file and manifest row back as it was
    python3 scripts/occupancy/apply_label_fixes.py --sync    # undo, then apply again from the current verdicts

A label counts as wrong when the by-eye verdict for that mistake is "label wrong, the model is right": the review in
data/datasets/occupancy/labels/ensemble_error_review.json, overridden by the person's own calls in data/datasets/occupancy/labels/ensemble_review_user.json
(made in the portal at /ensemble). Every change is logged in data/datasets/occupancy/labels/label_fixes.csv with the original folder,
so --undo restores exactly what was there. The model outputs in results/occupancy/probs/ are left untouched: they keep the
original labels, which is what the portal's "day before" numbers are measured against.

Training and validation labels are fixed from data/datasets/occupancy/labels/label_review_trainvalid.csv: every row with verdict
"wrong" (checked by eye; candidates were the model's disagreements and the not_trained_429_v6 batch, whose UPS and
Empty labels were swapped) is moved to the other folder of its split.

Units listed in data/datasets/occupancy/labels/test_removed.csv (crop, reason) are taken out of the test set: the cut-out moves to
removed_from_test/<folder>/ and its manifest row gets split "removed_from_test". The portal leaves them out of every
count. --undo puts them back too.
"""
import argparse
import datetime
import json
import pathlib
import shutil

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
CROPS = ROOT / "data/datasets/occupancy"
MANIFEST = CROPS / "manifest.csv"
DATA = ROOT / "webapp" / "static" / "ensemble_portal" / "data.json"
USER = ROOT / "data" / "datasets" / "occupancy" / "labels" / "ensemble_review_user.json"
LOG = ROOT / "data" / "datasets" / "occupancy" / "labels" / "label_fixes.csv"
REMOVED = ROOT / "data" / "datasets" / "occupancy" / "labels" / "test_removed.csv"
TRAINVALID = ROOT / "data" / "datasets" / "occupancy" / "labels" / "label_review_trainvalid.csv"   # by-eye review of train/valid candidates
GONE = "removed_from_test"


def wrong_labels():
    units = json.loads(DATA.read_text())["units"]
    user = json.loads(USER.read_text()) if USER.exists() else {}
    verdict = lambda u: (user.get(u["id"]) or {}).get("verdict") or u.get("verdict")  # noqa: E731
    return [u for u in units if not u["leaning_right"] and verdict(u) == "label"]


def apply():
    if LOG.exists():
        raise SystemExit(f"{LOG.name} already exists: the fixes are applied. Run --undo first to apply them again.")
    man = pd.read_csv(MANIFEST)
    user = json.loads(USER.read_text()) if USER.exists() else {}
    shutil.copy(MANIFEST, MANIFEST.with_name("manifest.before_label_fixes.csv"))
    rows, now = [], datetime.datetime.now().isoformat(timespec="seconds")
    for u in wrong_labels():
        k = (man.crop == u["file"]) & (man.split == "test")
        old, new = man.loc[k, "folder"].item(), u["leaning"]
        src, dst = CROPS / "test" / old / u["file"], CROPS / "test" / new / u["file"]
        if dst.exists():
            raise SystemExit(f"{dst} already exists; nothing more was moved. Run --undo to roll back.")
        shutil.move(src, dst)
        man.loc[k, "folder"] = new
        rows.append({"action": "fix", "crop": u["file"], "split": "test", "from": old, "to": new, "review_no": u["review_no"],
                     "decided_by": "person" if u["id"] in user else "review",
                     "applied": now})
    for r in (pd.read_csv(TRAINVALID).query("verdict == 'wrong'").to_dict("records") if TRAINVALID.exists() else []):
        k = (man.crop == r["crop"]) & (man.split == r["split"])
        old = man.loc[k, "folder"].item()
        new = "unoccupied" if old == "occupied" else "occupied"
        src, dst = CROPS / r["split"] / old / r["crop"], CROPS / r["split"] / new / r["crop"]
        if dst.exists():
            raise SystemExit(f"{dst} already exists; run --undo to roll back.")
        shutil.move(src, dst)
        man.loc[k, "folder"] = new
        rows.append({"action": "fix", "crop": r["crop"], "split": r["split"], "from": old, "to": new, "review_no": None,
                     "decided_by": "review", "applied": now})
    for r in (pd.read_csv(REMOVED).to_dict("records") if REMOVED.exists() else []):
        k = (man.crop == r["crop"]) & (man.split == "test")
        folder = man.loc[k, "folder"].item()
        (CROPS / GONE / folder).mkdir(parents=True, exist_ok=True)
        shutil.move(CROPS / "test" / folder / r["crop"], CROPS / GONE / folder / r["crop"])
        man.loc[k, "split"] = GONE
        rows.append({"action": "remove", "crop": r["crop"], "split": "test", "from": folder, "to": folder,
                     "review_no": r.get("review_no"), "decided_by": "person", "applied": now})
    man.to_csv(MANIFEST, index=False)
    pd.DataFrame(rows).to_csv(LOG, index=False)
    df = pd.DataFrame(rows)
    print(f"fixed {(df.action == 'fix').sum()} labels:", df[df.action == "fix"].groupby(["split", "from", "to"]).size().to_dict(),
          f"| removed from test: {(df.action == 'remove').sum()}")


def undo():
    log, man = pd.read_csv(LOG), pd.read_csv(MANIFEST)
    for r in reversed(log.to_dict("records")):
        if r.get("action", "fix") == "remove":
            shutil.move(CROPS / GONE / r["from"] / r["crop"], CROPS / "test" / r["from"] / r["crop"])
            man.loc[(man.crop == r["crop"]) & (man.split == GONE), "split"] = "test"
            continue
        sp = r.get("split", "test")
        shutil.move(CROPS / sp / r["to"] / r["crop"], CROPS / sp / r["from"] / r["crop"])
        man.loc[(man.crop == r["crop"]) & (man.split == sp), "folder"] = r["from"]
    man.to_csv(MANIFEST, index=False)
    LOG.replace(LOG.with_suffix(".undone.csv"))
    print(f"restored {len(log)} labels")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--undo", action="store_true")
    ap.add_argument("--sync", action="store_true")
    a = ap.parse_args()
    if a.undo or a.sync:
        undo()
    if not a.undo:
        apply()
