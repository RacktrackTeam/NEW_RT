"""Model 1, Model 2 and Model 1 + 2 on three test sets, one row per labelled test device.

Test sets (nested; the test photos of splits.csv that neither model trained on; Load Balancers left out, Model 1 does not know it):
  all     every labelled test device                                    4,266
  hi      devices in photos of 1 MP or more                             1,550
  clear   of those, the sharp, front-on, lit ones (sharpness >= 450, device fills >= 50 % of its outline, brightness >= 50)
Sources:
  m1      Model 1 (ConvNeXt-Tiny) on the device crop; Other by its own rule (unsure, unlike training, close call)
  m2      Model 2 (DINOv2-B detector) on the whole photo, the box over the device; Other when no box or a score below
          the cut-off set on valid (agreement.json)
  m12     Model 1's answer when both name the same type; otherwise Other
Labels: the ground truth with the test corrections (results/devices/label_fixes_test.csv).

    python scripts/devices/test_sets.py      -> results/devices/test_rows.csv and results/devices/test_sets.json
Needs models/device_cls/dinov2_b/test_predictions.csv (second_detector.py --split test) and agreement.json.
"""
import csv
import json
import pathlib
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts" / "devices"))
from PIL import Image  # noqa: E402
from device_classifier import DeviceClassifier  # noqa: E402
from build_crop_atlas import SHARP_MIN, look, sharpness  # noqa: E402

CROPS = ROOT / "data" / "device_crops"
OUT = ROOT / "results" / "devices"
D2 = ROOT / "models" / "device_cls" / "dinov2_b"
SETS = {"all": "Every test photo", "hi": "Photos of 1 MP or more", "clear": "Clear devices only"}
SOURCES = {"m1": "Model 1 · ConvNeXt-Tiny", "m2": "Model 2 · DINOv2-B", "m12": "Model 1 + 2"}
HBANDS = [(0, 8), (8, 12), (12, 16), (16, 24), (24, 32), (32, 1e9)]


def main():
    fix = {r["crop"]: r["new_label"] for r in csv.DictReader(open(OUT / "label_fixes_test.csv"))}
    from second_detector import unseen_crops
    clean = unseen_crops("test")                                                 # test photos neither model trained on
    R = [r for r in csv.DictReader(open(CROPS / "manifest.csv")) if r["crop"] in clean and r["class"] != "Load Balancer"]
    B = {r["crop"]: r for r in csv.DictReader(open(D2 / "test_predictions.csv"))}
    ag = json.load(open(ROOT / "models" / "device_cls" / "agreement.json")); cut2 = ag["model2_cut"]
    m2meta = json.load(open(D2 / "detector.json")); cap = m2meta["image_size"]
    clf = DeviceClassifier()
    rows = []
    for i in range(0, len(R), 64):
        part = R[i:i + 64]
        res = clf.classify([Image.open(CROPS / r["crop"]).convert("RGB") for r in part])
        for r, a in zip(part, res):
            b = B[r["crop"]]; W, H = int(r["image_w"]), int(r["image_h"]); h = float(r["y2"]) - float(r["y1"])
            fill, bright = look(r["crop"]); sh = sharpness(r["crop"])
            hi = W * H >= 1_000_000
            m2 = b["pred"]; m2s = float(b["prob"]); m2o = m2 == "none" or m2s < cut2
            rows.append({"crop": r["crop"], "photo": r["source_image"], "class": fix.get(r["crop"], r["class"]),
                         "hi": int(hi), "clear": int(hi and sh >= SHARP_MIN and fill >= .5 and bright >= 50),
                         "photo_mp": round(W * H / 1e6, 2), "device_h": round(h), "model2_h": round(h * min(1.0, cap / max(W, H)), 1),
                         "sharpness": round(sh), "fill": round(fill, 3), "brightness": round(bright),
                         "m1": a["class"], "m1_prob": round(a["prob"], 4), "m1_other": int(a["label"] == "Other"), "m1_reason": a["other_reason"] or "",
                         "m2": m2, "m2_score": round(m2s, 4), "m2_other": int(m2o), "m2_reason": "no box" if m2 == "none" else "low score" if m2o else "",
                         "m12": a["class"], "m12_other": int(a["class"] != m2), "m12_reason": ("no box from Model 2" if m2 == "none" else "the two models disagree") if a["class"] != m2 else ""})
        print(f"  {min(i + 64, len(R))}/{len(R)}", flush=True)
    with open(OUT / "test_rows.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

    def tally(S, k):
        t = Counter(n=len(S))
        for r in S:
            ok = r[k] == r["class"]; t["all_right" if ok else "all_wrong"] += 1
            if r[f"{k}_other"]:
                t["other"] += 1; t["other_right" if ok else "other_wrong"] += 1
            else:
                t["right" if ok else "wrong"] += 1
        t = dict(t); [t.setdefault(x, 0) for x in ("all_right", "all_wrong", "other", "other_right", "other_wrong", "right", "wrong")]
        t["accuracy"] = t["all_right"] / max(1, t["n"]); t["answers_right"] = t["right"] / max(1, t["right"] + t["wrong"])
        return t

    def per_type(S, k):
        out = {}
        for c in sorted({r["class"] for r in S}):
            tp = sum(r["class"] == c == r[k] for r in S); fp = sum(r[k] == c != r["class"] for r in S); n = sum(r["class"] == c for r in S)
            p, rc = tp / max(1, tp + fp), tp / max(1, n)
            out[c] = {"n": n, "right": tp, "precision": p, "recall": rc, "f1": 2 * p * rc / max(1e-9, p + rc)}
        return out

    def confusion(S, k):
        types = sorted({r["class"] for r in S}); cols = types + (["none"] if any(r[k] == "none" for r in S) else [])
        return {"rows": types, "cols": cols, "cells": [[sum(r["class"] == a and r[k] == b for r in S) for b in cols] for a in types]}

    sets = {"all": rows, "hi": [r for r in rows if r["hi"]], "clear": [r for r in rows if r["clear"]]}
    res = {"names": {"sets": SETS, "sources": SOURCES}, "cut2": cut2, "cap": cap, "model2": m2meta, "sets": {}}
    for s, S in sets.items():
        res["sets"][s] = {"n": len(S), "photos": len({r["photo"] for r in S}),
                          "tally": {k: tally(S, k) for k in SOURCES}, "per_type": {k: per_type(S, k) for k in SOURCES},
                          "confusion": {k: confusion(S, k) for k in SOURCES}}
        for k in SOURCES:
            pt = res["sets"][s]["per_type"][k]; res["sets"][s]["tally"][k]["macro_f1"] = sum(v["f1"] for v in pt.values()) / max(1, len(pt))
    # where Model 2 fails: by the device's height as Model 2 sees it, by photo size, and by picture quality
    A = rows
    band = lambda f: [{"lo": lo, "hi": hi, "n": len(S), "found": sum(r["m2"] != "none" for r in S) / max(1, len(S)),
                       "m2_right": sum(r["m2"] == r["class"] for r in S) / max(1, len(S)), "m1_right": sum(r["m1"] == r["class"] for r in S) / max(1, len(S))}
                      for lo, hi in HBANDS for S in [[r for r in A if lo <= f(r) < hi]]]
    res["by_model2_height"] = band(lambda r: r["model2_h"])
    mp = [(0, .5), (.5, 1), (1, 3), (3, 8), (8, 1e9)]
    res["by_photo_mp"] = [{"lo": lo, "hi": hi, "n": len(S), "m2_right": sum(r["m2"] == r["class"] for r in S) / max(1, len(S)),
                           "m1_right": sum(r["m1"] == r["class"] for r in S) / max(1, len(S)), "found": sum(r["m2"] != "none" for r in S) / max(1, len(S))}
                          for lo, hi in mp for S in [[r for r in A if lo <= r["photo_mp"] < hi]]]
    H = sets["hi"]
    q = {"sharp, front-on": [r for r in H if r["clear"]], "blurry or dark": [r for r in H if r["sharpness"] < SHARP_MIN or r["brightness"] < 50],
         "tilted or partly hidden": [r for r in H if r["fill"] < .5]}
    res["by_quality"] = {k: {"n": len(S), "m2_right": sum(r["m2"] == r["class"] for r in S) / max(1, len(S)),
                             "m1_right": sum(r["m1"] == r["class"] for r in S) / max(1, len(S))} for k, S in q.items()}
    res["who_right"] = {s: dict(Counter(("both" if r["m1"] == r["class"] and r["m2"] == r["class"] else "m1 only" if r["m1"] == r["class"]
                                         else "m2 only" if r["m2"] == r["class"] else "neither") for r in S)) for s, S in sets.items()}
    res["m2_mixups"] = {s: [[a, b, n] for (a, b), n in Counter((r["class"], r["m2"]) for r in S if r["m2"] != r["class"]).most_common(12)] for s, S in sets.items()}
    json.dump(res, open(OUT / "test_sets.json", "w"), indent=1)
    for s in sets:
        T = res["sets"][s]["tally"]
        print(f"{s:6} n={res['sets'][s]['n']:5}  " + "  ".join(f"{k}: acc {T[k]['accuracy']:.3f} / with Other {T[k]['right']}-{T[k]['wrong']}-{T[k]['other']} ({T[k]['answers_right']:.3f})" for k in SOURCES))


if __name__ == "__main__":
    main()
