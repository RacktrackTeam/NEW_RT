"""Model 1 (ConvNeXt-Tiny crop classifier), Model 2 (the team's DINOv2-B detector) and the two together, on the same test crops.

Needs the test predictions of both models:
    python3 scripts/devices/calibrate_other.py --run models/device_cls/convnext_tiny     # Model 1, with its Other rule
    python3 scripts/devices/second_detector.py --split valid                              # Model 2 on valid (for its cut-off)
    python3 scripts/devices/second_detector.py --split test                               # Model 2 on test
    python3 scripts/devices/agreement.py
Writes models/device_cls/agreement.json, read by the report and by DeviceClassifier ("chosen").

Rows, each scored on the same crops:
  model 1 / model 2                answer every crop (Model 2 with no box over a crop = wrong)
  model 1 + Other                  Model 1's Other rule (unsure, unlike any training crop, close call)
  model 2 + Other                  no box, or box score below a cut-off set on valid so that Model 2 says Other as often
                                   there as Model 1 does (same Other budget, so the two are comparable)
  both agree (+ ...)               Model 1's answer, given only when Model 2 names the same type; otherwise Other
For every Other row: how many Others would have been right and how many would have been wrong.
The rule chosen for the two models together gives up the fewest right answers with no more wrong than Model 1 + Other.
"""
import csv
import json
import pathlib
import sys
from collections import Counter

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
D = ROOT / "models" / "device_cls"
A, B = D / "convnext_tiny", D / "dinov2_b"
OUT = D / "agreement.json"
MP = 1_000_000


def load(f):
    if not f.exists():
        sys.exit(f"missing {f}: see the steps at the top of this script")
    return {r["crop"]: r for r in csv.DictReader(open(f))}


def tally(rows, other, answer):
    """Right / wrong among the answers, and the Others split by whether the answer would have been right."""
    t = Counter(right=0, wrong=0, other=0, other_right=0, other_wrong=0)
    for a, b in rows:
        ok = a["class"] == answer(a, b)
        if other(a, b):
            t["other"] += 1; t["other_right" if ok else "other_wrong"] += 1
        else:
            t["right" if ok else "wrong"] += 1
    t = dict(t); t["n"] = len(rows)
    t["answers_right"] = t["right"] / max(1, t["right"] + t["wrong"])
    t["lost"], t["caught"] = t["other_right"], t["other_wrong"]          # names the report already uses
    return t


def main():
    sys.path.insert(0, str(ROOT / "scripts" / "devices"))
    from second_detector import unseen_crops
    clean_test, clean_valid = unseen_crops("test"), unseen_crops("valid")       # photos neither model trained on
    a, b = load(A / "test_predictions.csv"), load(B / "test_predictions.csv")
    rows = [(a[k], b[k]) for k in a if k in b and k in clean_test]
    # Model 2's cut-off: on the valid crops of 1 MP photos, the box score below which it says Other as often as Model 1 does
    big = {r["crop"] for r in csv.DictReader(open(ROOT / "data" / "device_crops" / "manifest.csv"))
           if r["crop"] and r["split"] == "valid" and int(r["image_w"]) * int(r["image_h"]) >= MP and r["crop"] in clean_valid}
    bv = [r for k, r in load(B / "valid_predictions.csv").items() if k in big]
    share = json.load(open(A / "other_stats.json"))["splits"]["valid"]["tally_after"]
    share = share["other"] / share["n"]
    sc = np.array([-1.0 if r["pred"] == "none" else float(r["prob"]) for r in bv])
    cut2 = float(np.quantile(sc, share))
    m1, m2 = (lambda a, b: a["pred"]), (lambda a, b: b["pred"])
    unfamiliar = lambda a: "unsure" in a["reason"] or "unlike" in a["reason"]
    never = lambda a, b: False
    rules = [
        ("model 1", never, m1),
        ("model 2", never, m2),
        ("model 1 + Other", lambda a, b: a["other"] != "0", m1),
        ("model 2 + Other", lambda a, b: b["pred"] == "none" or float(b["prob"]) < cut2, m2),
        ("both agree", lambda a, b: a["pred"] != b["pred"], m1),
        ("both agree + unfamiliar", lambda a, b: a["pred"] != b["pred"] or unfamiliar(a), m1),
        ("both agree + unfamiliar + close", lambda a, b: a["pred"] != b["pred"] or a["other"] != "0", m1),
    ]
    res = []
    for name, f, ans in rules:
        t = tally(rows, f, ans); t["name"] = name; res.append(t)
    R = {t["name"]: t for t in res}
    pair = [t for t in res if t["name"].startswith("both")]
    ok = [t for t in pair if t["wrong"] <= R["model 1 + Other"]["wrong"]]
    chosen = min(ok, key=lambda t: (t["lost"], t["wrong"])) if ok else min(pair, key=lambda t: (t["wrong"], t["lost"]))
    dis = Counter(); dis_wrong = Counter()
    for x, y in rows:
        if x["pred"] != y["pred"]:
            k = " vs ".join(sorted((x["pred"], y["pred"]))); dis[k] += 1; dis_wrong[k] += x["class"] != x["pred"]
    out = {"a": A.name, "b": B.name, "n": len(rows), "photos": len({x["crop"].rsplit("/", 1)[-1].rsplit("__", 1)[0] for x, _ in rows}),
           "model2_cut": cut2, "model2_cut_share_valid": share, "valid_n": len(bv),
           "rules": res, "chosen": chosen["name"],
           "disagreements": [{"pair": k, "n": n, "wrong": dis_wrong[k]} for k, n in dis.most_common()]}
    json.dump(out, open(OUT, "w"), indent=1)
    print(f"Model 2 Other cut-off {cut2:.3f} (box score), set on {len(bv)} valid crops at Model 1's Other share {share:.1%}")
    print(f"{'rule':32} {'right':>6} {'wrong':>6} {'Other':>6} {'Other right':>12} {'Other wrong':>12} {'answers right':>14}")
    for t in res:
        print(f"{t['name']:32} {t['right']:6} {t['wrong']:6} {t['other']:6} {t['other_right']:12} {t['other_wrong']:12} {t['answers_right']:14.1%}")
    print(f"chosen: {chosen['name']} -> {OUT}")


if __name__ == "__main__":
    main()
