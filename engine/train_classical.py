"""Approach 3: hand-crafted color/edge/texture features + Random Forest (no deep learning)."""
import json
import pathlib
import time

import cv2
import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.model_selection import cross_val_score

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data/cls_data"
OUT = ROOT / "models"
OUT.mkdir(exist_ok=True)


def features(bgr):
    """~90-dim descriptor of a 224x224 BGR crop."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    f = []
    for ch, bins in [(0, 16), (1, 8), (2, 8)]:
        h = cv2.calcHist([hsv], [ch], None, [bins], [0, 180 if ch == 0 else 256]).ravel()
        f += list(h / h.sum())
    g = gray.astype(np.float32)
    f += [g.mean(), g.std(), *np.percentile(g, [10, 50, 90]),
          (g < 40).mean(), (g > 200).mean()]
    edges = cv2.Canny(gray, 50, 150)
    f.append(edges.mean() / 255)
    f.append(cv2.Laplacian(gray, cv2.CV_32F).var() / 1e4)
    sx = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0)); sy = np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1))
    f += [sx.mean() / 255, sy.mean() / 255]
    # brightness / edge profiles capture horizontal device structure (LED rows, port rows, bezels)
    f += list(cv2.resize(g.mean(axis=1, keepdims=True), (1, 16)).ravel() / 255)
    f += list(cv2.resize(g.mean(axis=0, keepdims=True).T, (1, 16)).ravel() / 255)
    f += list(cv2.resize(edges.astype(np.float32).mean(axis=1, keepdims=True), (1, 8)).ravel() / 255)
    rg = bgr[..., 2].astype(np.float32) - bgr[..., 1]; yb = 0.5 * (bgr[..., 2] + bgr[..., 1]) - bgr[..., 0]
    f.append((np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)) / 255)
    return np.array(f, dtype=np.float32)


def extract(split):
    X, y, files = [], [], []
    for label, cls in enumerate(["unoccupied", "occupied"]):
        for f in sorted((DATA / split / cls).glob("*.jpg")):
            X.append(features(cv2.imread(str(f)))); y.append(label); files.append(f.name)
    return np.stack(X), np.array(y), files


if __name__ == "__main__":
    t = time.time()
    Xtr, ytr, _ = extract("train")
    Xva, yva, fva = extract("val")
    print(f"features {Xtr.shape} in {time.time()-t:.0f}s")
    cands = {
        "RandomForest": RandomForestClassifier(n_estimators=600, n_jobs=-1, random_state=0),
        "HistGradientBoosting": HistGradientBoostingClassifier(max_iter=400, learning_rate=0.08, random_state=0),
    }
    best_name, best_cv = None, -1
    for n, c in cands.items():
        cv = cross_val_score(c, Xtr, ytr, cv=3, n_jobs=1).mean()
        print(f"{n}: cv acc {cv:.4f}")
        if cv > best_cv:
            best_name, best_cv = n, cv
    clf = cands[best_name].fit(Xtr, ytr)
    joblib.dump({"model": best_name, "clf": clf}, OUT / "classical_clf.joblib")

    p = clf.predict_proba(Xva)[:, 1]
    pred = (p >= 0.5).astype(int)
    acc = float((pred == yva).mean())
    lab = ["unoccupied", "occupied"]
    cm = {a: {b: 0 for b in lab} for a in lab}
    errors = []
    for f, yt, yp, pp in zip(fva, yva, pred, p):
        cm[lab[yt]][lab[yp]] += 1
        if yt != yp:
            errors.append({"file": f, "true": lab[yt], "pred": lab[yp], "p_occupied": float(pp)})
    json.dump(
        {"approach": f"Classical features + {best_name}", "val_accuracy": acc, "n_val": len(yva),
         "confusion": cm, "errors": errors, "cv_accuracy": best_cv},
        open(OUT / "classical_eval.json", "w"), indent=1,
    )
    print(f"Classical val accuracy: {acc:.4f}  confusion={cm}  ({time.time()-t:.0f}s total)")
