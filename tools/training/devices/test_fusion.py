"""Several pictures of one device, one answer: Model 1 with feature fusion (classify_views) against one picture and a vote.

Part 1, real: the 10 devices of the vdo1 video, each with its 5 best frames (frame locator), cut along the device
polygon exactly as the pipeline does. Ground truth: the rack list in the run's result.json.
Part 2, simulated: each of the test devices (photos of 1 MP or more) seen 5 ways: the photo itself, darker, with glare,
blurred and tilted (the stress-test changes), to see what fusion does when some views are poor; and the 4 harder views
alone (no clean view), to see whether fusing imperfect views beats each of them.

Methods compared: one picture (the best frame / the photo itself), a majority vote of the 5 answers, and fusion
(features averaged, one answer). With the Other rule on, as in use.

    python scripts/devices/test_fusion.py      -> results/devices/fusion_test.json
"""
import csv
import json
import pathlib
import random
import sys
from collections import Counter

import cv2
import numpy as np
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts" / "devices"))
from device_classifier import DeviceClassifier, crop_polygon  # noqa: E402
from analyse_classifier import STRESS  # noqa: E402

RUN = ROOT / "data" / "runs" / "64f74f741eb4"
VIDEO = ROOT / "data" / "video" / "vdo1.mp4"
OUT = ROOT / "results" / "devices" / "fusion_test.json"
VIEWS = ["normal", "darker", "glare", "blurred", "tilted"]


def vote(answers):
    """Majority of the 5 answers (Other counts as an answer); a tie goes to the type with the highest summed probability."""
    c = Counter(a["label"] for a in answers); top = max(c.values())
    tied = [k for k, v in c.items() if v == top]
    return max(tied, key=lambda k: sum(a["prob"] for a in answers if a["label"] == k))


def tally(rows, key):
    t = Counter(right=0, wrong=0, other=0)
    for r in rows:
        a = r[key]
        t["other" if a == "Other" else "right" if a == r["truth"] else "wrong"] += 1
    t = dict(t); t["n"] = len(rows); t["answers_right"] = t["right"] / max(1, t["right"] + t["wrong"])
    return t


def real(clf):
    r = json.load(open(RUN / "result.json"))
    truth = r["truth"]["devices"]
    def true_type(dv):
        us = [int(u[1:]) for u in dv["units"]]
        hit = [t for t in truth if t["u_from"] <= min(us) and max(us) <= t["u_to"]] or [t for t in truth if t["u_from"] <= max(us) and min(us) <= t["u_to"]]
        return hit[0]["type"] if hit else None, hit[0]["name"] if hit else None
    want = {tf["frame"] for dv in r["devices"] for tf in dv["top_frames"]}
    cap = cv2.VideoCapture(str(VIDEO)); imgs = {}
    for n in range(max(want) + 1):
        if n in want:
            ok, fr = cap.read()
            if ok:
                imgs[n] = fr
        else:
            cap.grab()
    cap.release()
    rows = []
    for dv in r["devices"]:
        t, name = true_type(dv)
        tfs = sorted(dv["top_frames"], key=lambda x: x["rank"])
        crops, w = [], []
        for tf in tfs:
            H = np.asarray(tf["H"], np.float64)
            q = cv2.perspectiveTransform(np.float32([dv["polygon"]]), np.linalg.inv(H))[0]
            crops.append(crop_polygon(Image.fromarray(imgs[tf["frame"]][:, :, ::-1]), [(float(a), float(b)) for a, b in q]))
            w.append(float(tf.get("score") or 1.0))
        each = clf.classify(crops)
        fused = clf.classify_views(crops); fused_w = clf.classify_views(crops, w)
        rows.append({"device": dv["label"], "name": name, "truth": t if t else "Other", "best_frame": each[0]["label"], "vote": vote(each),
                     "fused": fused["label"], "fused_weighted": fused_w["label"], "frames": [(a["label"], round(a["prob"], 3)) for a in each],
                     "fused_prob": round(fused["prob"], 3), "best_prob": round(each[0]["prob"], 3)})
    return rows


def simulated(clf):
    P = [p for p in csv.DictReader(open(ROOT / "models" / "device_cls" / "convnext_tiny" / "test_predictions.csv"))]
    fix = {r["crop"]: r["new_label"] for r in csv.DictReader(open(ROOT / "results" / "devices" / "label_fixes_test.csv"))}
    rows = []
    for i, p in enumerate(P):
        im = Image.open(ROOT / "data" / "device_crops" / p["crop"]).convert("RGB")
        rng = random.Random(i)
        views = [STRESS[k](im, rng) for k in VIEWS]
        each = clf.classify(views); fused = clf.classify_views(views)
        poor = clf.classify_views(views[1:])                 # no clean view: only the 4 harder ones
        rows.append({"truth": fix.get(p["crop"], p["class"]), **{f"one_{k}": a["label"] for k, a in zip(VIEWS, each)},
                     "vote": vote(each), "fused": fused["label"], "vote_poor": vote(each[1:]), "fused_poor": poor["label"]})
        if i % 200 == 0:
            print(f"  simulated {i}/{len(P)}", flush=True)
    return rows


def main():
    clf = DeviceClassifier()
    R = real(clf)
    S = simulated(clf)
    out = {"real": {"rows": R, **{k: tally(R, k) for k in ("best_frame", "vote", "fused", "fused_weighted")}},
           "simulated": {"n": len(S), "views": VIEWS, **{k: tally(S, k) for k in [f"one_{v}" for v in VIEWS] + ["vote", "fused", "vote_poor", "fused_poor"]}}}
    json.dump(out, open(OUT, "w"), indent=1)
    print("REAL (vdo1, 10 devices, 5 frames each)")
    for r in R:
        print(f"  {r['device']:9} truth {r['truth']:12} best {r['best_frame']:12} vote {r['vote']:12} fused {r['fused']:12} ({r['fused_prob']}) frames {r['frames']}")
    for k in ("best_frame", "vote", "fused", "fused_weighted"):
        t = out["real"][k]; print(f"  {k:15} right {t['right']} wrong {t['wrong']} Other {t['other']}")
    print(f"SIMULATED ({len(S)} test devices, 5 views each)")
    for k in [f"one_{v}" for v in VIEWS] + ["vote", "fused", "vote_poor", "fused_poor"]:
        t = out["simulated"][k]; print(f"  {k:12} right {t['right']:5} wrong {t['wrong']:4} Other {t['other']:4}  answers right {t['answers_right']:.3f}")


if __name__ == "__main__":
    main()
