#!/usr/bin/env python3
"""Evaluate the device classifier on the test split with every standard metric, and calibrate its "Other" rule.

Metrics (test split, written to other_stats.json["metrics"]): accuracy, balanced accuracy, macro and weighted
precision / recall / F1, top-2 accuracy, Cohen's kappa, expected calibration error, and per class precision,
recall, F1, support and mean confidence, plus the confusion matrix in counts and row shares.

"Other" (unknown) is a rule on top of the model with three checks, all set on the valid split:
  - strength and distance: the score strength (= -energy) and the cosine distance to the k-th nearest training
    crop (train_features.npz). Both cut-offs sit at the same rank so that FALSE_OTHER % of the valid crops trip
    one or the other. They catch gear unlike the 9 classes. 5 % was chosen on the ground-truth racks: it caught
    9 of 13 real unknown devices against 3 of 13 at 1 %.
  - close call: the gap between the top two answers' probabilities. The cut-off is set so that CLOSE_CALL % of
    the valid crops have a smaller gap. This turns likely mistakes into Other instead of a wrong answer (user,
    2026-10-22: about 10 %).
gap_cuts in other_stats.json holds the close-call cut-off for other shares (5 to 70 %), for the report's trade-off view.
Each split also gets right / Other / wrong counts before the rule, with the first two checks only, and with all
three. Also writes test_predictions.csv: one row per test crop with the true class, the answer, its probability,
the runner-up, all raw scores and the Other reason (used by the report's "Why not 100 %" view).
Test labels checked by hand and found wrong are corrected from results/devices/label_fixes_test.csv (crop, old_label,
new_label) before scoring; a crop whose new label is not one of the classes is left out of the test.
Writes other_cutoffs.json and other_stats.json into the model folder.

    python3 scripts/devices/calibrate_other.py [--run models/device_cls/convnext_tiny_all]
"""
import argparse, csv, json, pathlib, sys

import numpy as np
import torch
from PIL import Image
from sklearn.metrics import (balanced_accuracy_score, cohen_kappa_score, confusion_matrix,
                             precision_recall_fscore_support)

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from device_classifier import DeviceClassifier, RUN  # noqa: E402

MIN_PIXELS = 1_000_000
FALSE_OTHER = 5.0       # % of valid crops flagged by strength or distance
CLOSE_CALL = 10.0       # % of valid crops whose top-two gap is smallest


FIXES = ROOT / "results" / "devices" / "label_fixes_test.csv"


def items(split, classes, rows=False):
    fix = {r["crop"]: r["new_label"] for r in csv.DictReader(open(FIXES))} if split == "test" and FIXES.exists() else {}
    out = []
    for r in csv.DictReader(open(ROOT / "data" / "device_crops" / "manifest.csv")):
        if r["split"] == split and r["crop"] and r["class"] in classes and int(r["image_w"]) * int(r["image_h"]) >= MIN_PIXELS:
            r = dict(r, **{"class": fix.get(r["crop"], r["class"])})
            if r["class"] in classes:
                out.append(r if rows else (ROOT / "data" / "device_crops" / r["crop"], r["class"]))
    return out


def score(clf, its, batch=64):
    L, E, D, F = [], [], [], []
    for i in range(0, len(its), batch):
        crops = [Image.open(p) for p, _ in its[i:i + batch]]
        logits, feats, energy, dist = clf.scores(crops)
        L.append(logits); E.append(energy); D.append(dist); F.append(feats)
    return torch.cat(L), torch.cat(E), torch.cat(D), torch.cat(F)


def full_metrics(y, logits, classes, bins=10):
    """Every standard metric from true labels and logits."""
    y = y.numpy(); P = logits.softmax(1).numpy(); pred = P.argmax(1); conf = P.max(1)
    labels = list(range(len(classes)))
    p, r, f, sup = precision_recall_fscore_support(y, pred, labels=labels, zero_division=0)
    mp, mr, mf, _ = precision_recall_fscore_support(y, pred, labels=labels, average="macro", zero_division=0)
    wp, wr, wf, _ = precision_recall_fscore_support(y, pred, labels=labels, average="weighted", zero_division=0)
    top2 = float(np.mean([y[i] in np.argsort(-P[i])[:2] for i in range(len(y))]))
    edges = np.linspace(0, 1, bins + 1); ece = 0.0; rel = []
    for a, b in zip(edges[:-1], edges[1:]):
        m = (conf > a) & (conf <= b)
        if m.any():
            acc_b, conf_b = float((pred[m] == y[m]).mean()), float(conf[m].mean())
            ece += m.mean() * abs(acc_b - conf_b); rel.append([float(a), float(b), int(m.sum()), acc_b, conf_b])
    cm = confusion_matrix(y, pred, labels=labels)
    return dict(
        n=int(len(y)), accuracy=float((pred == y).mean()), balanced_accuracy=float(balanced_accuracy_score(y, pred)),
        macro_precision=float(mp), macro_recall=float(mr), macro_f1=float(mf),
        weighted_precision=float(wp), weighted_recall=float(wr), weighted_f1=float(wf),
        top2_accuracy=top2, kappa=float(cohen_kappa_score(y, pred)), ece=float(ece), reliability=rel,
        mean_confidence=float(conf.mean()),
        per_class={c: dict(precision=float(p[i]), recall=float(r[i]), f1=float(f[i]), support=int(sup[i]),
                           predicted=int(cm[:, i].sum()), correct=int(cm[i, i]),
                           mean_conf=float(conf[y == i].mean()) if (y == i).any() else 0.0) for i, c in enumerate(classes)},
        confusion=cm.tolist(), confusion_share=(cm / np.maximum(1, cm.sum(1, keepdims=True))).round(4).tolist(), classes=classes)


def tally(right, other):
    """Right / Other / wrong counts: a wrong answer is wrong, Other is neither."""
    return dict(right=int((right & ~other).sum()), other=int(other.sum()), wrong=int((~right & ~other).sum()),
                caught=int((~right & other).sum()), lost=int((right & other).sum()), n=int(len(right)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=str(RUN), help="model folder holding best.pt; every output is written there")
    run = pathlib.Path(ap.parse_args().run).resolve()
    clf = DeviceClassifier(run=run, k=5)
    clf.cut, clf.bank = None, None
    tr = items("train", clf.classes)
    print(f"feature bank: {len(tr)} training crops")
    _, _, _, feats = score(clf, tr)
    np.savez_compressed(run / "train_features.npz", features=feats.numpy().astype(np.float16))
    f = feats.numpy().astype(np.float32)
    clf.bank = torch.from_numpy(f / np.linalg.norm(f, axis=1, keepdims=True)).to(clf.dev)

    stats = {"k": clf.k, "false_other_target": FALSE_OTHER, "close_call_target": CLOSE_CALL, "splits": {},
             "test_label_fixes": sum(1 for _ in csv.DictReader(open(FIXES))) if FIXES.exists() else 0}
    cut = None
    for split in ("valid", "test"):
        its = items(split, clf.classes)
        logits, energy, dist, _ = score(clf, its)
        y = torch.tensor([clf.classes.index(c) for _, c in its])
        P = logits.softmax(1); top2 = P.topk(2, dim=1)
        pred, gap = top2.indices[:, 0], top2.values[:, 0] - top2.values[:, 1]
        if split == "valid":
            e, d = energy.numpy(), dist.numpy()
            both = np.maximum(np.argsort(np.argsort(e)), np.argsort(np.argsort(d))) / (len(e) - 1)
            t = float(np.percentile(both, 100 - FALSE_OTHER))
            cut = {"energy": float(np.quantile(e, t)), "distance": float(np.quantile(d, t)), "k": clf.k,
                   "false_other_target": FALSE_OTHER, "rank": t,
                   "margin": float(np.percentile(gap.numpy(), CLOSE_CALL)), "close_call_target": CLOSE_CALL}
            json.dump(cut, open(run / "other_cutoffs.json", "w"), indent=1)
            stats["gap_cuts"] = {str(sh): float(np.percentile(gap.numpy(), sh)) for sh in range(5, 75, 5)}   # close-call cut-off per share
        flag_e, flag_d, flag_m = energy > cut["energy"], dist > cut["distance"], gap < cut["margin"]
        old = flag_e | flag_d
        flagged = old | flag_m
        right = (pred == y).numpy()
        wrong = pred != y
        stats["splits"][split] = dict(
            n=len(its), acc_before=float((pred == y).float().mean()),
            flagged=int(flagged.sum()), flagged_energy=int(flag_e.sum()), flagged_distance=int(flag_d.sum()),
            flagged_margin=int(flag_m.sum()), flagged_share=float(flagged.float().mean()),
            wrong_among_flagged=int((wrong & flagged).sum()), wrong_total=int(wrong.sum()),
            acc_on_kept=float((pred[~flagged] == y[~flagged]).float().mean()),
            tally_before=tally(right, np.zeros(len(right), bool)), tally_unfamiliar=tally(right, old.numpy()),
            tally_after=tally(right, flagged.numpy()),
            energy_hist=np.histogram(energy.numpy(), bins=30)[0].tolist(), energy_edges=np.histogram(energy.numpy(), bins=30)[1].tolist(),
            distance_hist=np.histogram(dist.numpy(), bins=30)[0].tolist(), distance_edges=np.histogram(dist.numpy(), bins=30)[1].tolist(),
            flagged_by_class={c: int(flagged[y == i].sum()) for i, c in enumerate(clf.classes)},
            n_by_class={c: int((y == i).sum()) for i, c in enumerate(clf.classes)})
        if split == "test":
            stats["metrics"] = full_metrics(y, logits, clf.classes)
            with open(run / "test_predictions.csv", "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["crop", "class", "pred", "prob", "second", "second_prob", "crop_w", "crop_h", "energy", "distance",
                            "gap", "other", "reason", "scores"])
                for j, r in enumerate(items("test", clf.classes, rows=True)):
                    why = [n for n, fl in (("unsure", flag_e), ("unlike any training crop", flag_d), ("close call", flag_m)) if fl[j]]
                    w.writerow([r["crop"], clf.classes[int(y[j])], clf.classes[int(pred[j])], round(float(top2.values[j, 0]), 4),
                                clf.classes[int(top2.indices[j, 1])], round(float(top2.values[j, 1]), 4), r["crop_w"], r["crop_h"],
                                round(float(energy[j]), 4), round(float(dist[j]), 4), round(float(gap[j]), 4), int(flagged[j]),
                                " and ".join(why), " ".join(f"{v:.3f}" for v in logits[j].tolist())])
        s = stats["splits"][split]
        for k, t_ in (("before", "tally_before"), ("unfamiliar only", "tally_unfamiliar"), ("all three", "tally_after")):
            t = s[t_]
            print(f"{split} {k:16} right {t['right']:5}  Other {t['other']:4}  wrong {t['wrong']:4}  "
                  f"accuracy of answers {100 * t['right'] / max(1, t['right'] + t['wrong']):.1f} %")
    stats["cutoffs"] = cut
    json.dump(stats, open(run / "other_stats.json", "w"), indent=1)


if __name__ == "__main__":
    main()
