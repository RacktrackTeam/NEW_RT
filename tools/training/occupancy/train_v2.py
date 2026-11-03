"""Train every approach on the v2 dataset (data/datasets/occupancy) and score it on v2 test.

    python3 train_v2.py classical | clip | dinov2 | teammate | yolo11s | yolov8m | ensemble

Data: data/datasets/occupancy_224/{train,val,test}/{occupied,unoccupied} - v2 crops squashed to 224x224.
  train (12,800) fits the model, val (1,600) picks hyper-parameters / the best YOLO epoch,
  test (1,600) is scored once at the end. No rack photo is shared between the three splits.
Output: models/v2/<key>.{pt,joblib} and models/v2/<key>_eval.json (same format as v1).
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))   # project root: src/occupancy.py and friends
import json
import pathlib
import shutil
import sys
import time

import numpy as np
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = ROOT / "data/datasets/occupancy_224"
OUT = ROOT / "models" / "v2"
OUT.mkdir(parents=True, exist_ok=True)
LAB = ["unoccupied", "occupied"]          # occupied = 1
KEYS = {"classical": "classical", "clip": "embed_clip", "dinov2": "embed_dinov2", "teammate": "teammate",
        "yolo11s": "yolo11s_cls", "yolov8m": "yolov8m_cls", "ensemble": "ensemble"}


def files(split):
    fs, y = [], []
    for label, cls in enumerate(LAB):
        f = sorted((DATA / split / cls).glob("*.jpg"))
        fs += f
        y += [label] * len(f)
    return fs, np.array(y)


def write_eval(key, name, test_files, y, p, extra=None):
    pred = (p >= 0.5).astype(int)
    cm = {a: {b: 0 for b in LAB} for a in LAB}
    errors = []
    for f, yt, yp, pp in zip(test_files, y, pred, p):
        cm[LAB[yt]][LAB[yp]] += 1
        if yt != yp:
            errors.append({"file": f.name, "true": LAB[yt], "pred": LAB[yp], "p_occupied": float(pp)})
    acc = float((pred == y).mean())
    json.dump({"approach": name, "dataset": "v2", "val_accuracy": acc, "n_val": int(len(y)), "confusion": cm,
               "errors": errors, **(extra or {})}, open(OUT / f"{key}_eval.json", "w"), indent=1)
    print(f"[v2] {name}: test accuracy {acc:.4f}  confusion={cm}", flush=True)


def embed_all(embed, fs, bs=128):
    return np.concatenate([embed([Image.open(f).convert("RGB") for f in fs[i:i + bs]]) for i in range(0, len(fs), bs)])


def fit_head(Xtr, ytr, Xva, yva, cands):
    """Pick the head with the best VALID accuracy, then refit on train."""
    best, best_acc = None, -1
    for n, make in cands.items():
        acc = (make().fit(Xtr, ytr).predict(Xva) == yva).mean()
        print(f"  {n}: valid {acc:.4f}", flush=True)
        if acc > best_acc:
            best, best_acc = n, acc
    return best, best_acc, cands[best]().fit(Xtr, ytr)


def run_embed(kind):
    import joblib
    from sklearn.linear_model import LogisticRegression
    from sklearn.neural_network import MLPClassifier
    from train_embed import load_backbone
    key = KEYS[kind]
    _, embed = load_backbone(kind)
    (ftr, ytr), (fva, yva), (fte, yte) = files("train"), files("val"), files("test")
    t = time.time()
    Xtr, Xva, Xte = embed_all(embed, ftr), embed_all(embed, fva), embed_all(embed, fte)
    print(f"  features {Xtr.shape} in {time.time()-t:.0f}s", flush=True)
    cands = {f"LogReg C={C}": (lambda C=C: LogisticRegression(C=C, max_iter=5000)) for C in [1, 3, 10, 30]}
    cands["MLP 512"] = lambda: MLPClassifier((512,), alpha=1e-3, early_stopping=True, max_iter=300, random_state=0)
    head, vacc, clf = fit_head(Xtr, ytr, Xva, yva, cands)
    # joblib pickle of our own freshly trained sklearn head - trusted, local artefact.
    joblib.dump({"backbone": kind, "head": head, "clf": clf}, OUT / f"{key}_clf.joblib")
    write_eval(key, f"{kind.upper() if kind == 'clip' else 'DINOv2'} embeddings + {head}", fte, yte,
               clf.predict_proba(Xte)[:, 1], {"head": head, "valid_accuracy": float(vacc)})


def run_teammate():
    """The teammate's exact recipe, retrained on v2: their ResNet50 embedding (teammate_approach.get_embedding)
    + LogisticRegression(C=1, class_weight='balanced', max_iter=5000), as stored in their pickle."""
    import joblib
    from sklearn.linear_model import LogisticRegression
    from teammate_approach import get_embedding
    (ftr, ytr), (fva, yva), (fte, yte) = files("train"), files("val"), files("test")
    emb = lambda fs: np.stack([get_embedding(Image.open(f).convert("RGB")) for f in fs])
    t = time.time()
    Xtr, Xva, Xte = emb(ftr), emb(fva), emb(fte)
    print(f"  features {Xtr.shape} in {time.time()-t:.0f}s", flush=True)
    clf = LogisticRegression(C=1.0, class_weight="balanced", max_iter=5000).fit(Xtr, ytr)
    vacc = float((clf.predict(Xva) == yva).mean())
    print(f"  valid {vacc:.4f}", flush=True)
    # joblib pickle of our own freshly trained sklearn model - trusted, local artefact.
    joblib.dump({"recipe": "teammate ResNet50 + LogReg", "clf": clf}, OUT / "teammate_clf.joblib")
    write_eval("teammate", "ResNet50 + LogReg (teammate recipe, retrained on v2)", fte, yte,
               clf.predict_proba(Xte)[:, 1], {"valid_accuracy": vacc})


def run_classical():
    import cv2
    import joblib
    from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
    from train_classical import features
    feat = lambda fs: np.stack([features(cv2.imread(str(f))) for f in fs])
    (ftr, ytr), (fva, yva), (fte, yte) = files("train"), files("val"), files("test")
    Xtr, Xva, Xte = feat(ftr), feat(fva), feat(fte)
    cands = {"RandomForest": lambda: RandomForestClassifier(n_estimators=600, n_jobs=-1, random_state=0),
             "HistGradientBoosting": lambda: HistGradientBoostingClassifier(max_iter=400, learning_rate=0.08,
                                                                            random_state=0)}
    head, vacc, clf = fit_head(Xtr, ytr, Xva, yva, cands)
    # joblib pickle of our own freshly trained sklearn model - trusted, local artefact.
    joblib.dump({"model": head, "clf": clf}, OUT / "classical_clf.joblib")
    write_eval("classical", f"Classical features + {head}", fte, yte, clf.predict_proba(Xte)[:, 1],
               {"valid_accuracy": float(vacc)})


def run_yolo(arch):
    from ultralytics import YOLO
    key = KEYS[arch]
    weights = {"yolo11s": "yolo11s-cls.pt", "yolov8m": "yolov8m-cls.pt"}[arch]
    YOLO(weights).train(data=str(DATA), imgsz=224, epochs=15, patience=4, batch=32, device="mps", workers=2,
                        erasing=0.0, scale=0.3, fliplr=0.5, project=str(ROOT / "runs"), name=f"v2_{arch}",
                        exist_ok=True, plots=False)
    finish_yolo(arch)


def finish_yolo(arch):
    """Install the best-on-valid epoch and score it on test (also used if a run is stopped early)."""
    from ultralytics import YOLO
    key = KEYS[arch]
    best = ROOT / "runs" / f"v2_{arch}" / "weights" / "best.pt"
    shutil.copy(best, OUT / f"{key}.pt")
    m = YOLO(str(best))
    occ = [k for k, v in m.names.items() if v == "occupied"][0]
    fte, yte = files("test")
    p = np.concatenate([[float(r.probs.data[occ]) for r in
                         m.predict([str(f) for f in fte[i:i + 128]], imgsz=224, device="mps", verbose=False)]
                        for i in range(0, len(fte), 128)])
    write_eval(key, f"{'YOLOv8m' if arch == 'yolov8m' else 'YOLO11s'}-cls (fine-tuned CNN)", fte, yte, p)


def run_ensemble():
    import occupancy as occ
    ens = [a for a in occ.build_approaches("v2") if a.key == "ensemble"][0]
    fte, yte = files("test")
    crops = [Image.open(f).convert("RGB") for f in fte]
    probs = {m.short: np.concatenate([m.predict(crops[i:i + 256]) for i in range(0, len(crops), 256)])
             for m in ens.members if m.available()}
    for k, v in probs.items():
        print(f"  {k:22s} {((v >= 0.5) == yte).mean():.4f}", flush=True)
    write_eval("ensemble", f"Ensemble of {len(probs)}", fte, yte, np.mean(list(probs.values()), axis=0),
               {"members": list(probs)})


if __name__ == "__main__":
    what = sys.argv[1]
    t0 = time.time()
    {"classical": run_classical, "clip": lambda: run_embed("clip"), "dinov2": lambda: run_embed("dinov2"),
     "teammate": run_teammate, "yolo11s": lambda: run_yolo("yolo11s"), "yolov8m": lambda: run_yolo("yolov8m"),
     "finish_yolo11s": lambda: finish_yolo("yolo11s"), "finish_yolov8m": lambda: finish_yolo("yolov8m"),
     "ensemble": run_ensemble}[what]()
    print(f"[v2] {what} done in {time.time()-t0:.0f}s", flush=True)
