"""The raw two-class output of an approach for ONE unit, straight from the model library.

Used by the web app's "Proof" panel to show that P(occupied) and P(unoccupied) come from the model itself
and always add up to 100%:
  * YOLO classifiers: the softmax vector ultralytics returns (`result.probs.data`).
  * DINOv2 / CLIP / ResNet50 / Classical: scikit-learn `predict_proba`, one column per class.
  * DINOv2-L k-NN: how the 25 nearest neighbours voted, and their weighted vote share per class.
  * Fusion: the fused score, plus the CLIP probe's own 3-class softmax.
  * Ensemble: every member's pair, then their average.
"""
import numpy as np

import occupancy as occ


def _pair(model, occupied, unoccupied, how, detail=None):
    return {"model": model, "occupied": float(occupied), "unoccupied": float(unoccupied),
            "sum": float(occupied) + float(unoccupied), "how": how, "detail": detail or {}}


def _yolo(a, crop):
    a.predict([crop])                                     # loads the network if needed
    r = a._m.predict([crop], imgsz=224, device=occ.DEVICE, verbose=False)[0]
    vec = {a._m.names[i]: float(v) for i, v in enumerate(r.probs.data.tolist())}
    return _pair(a.short, vec["occupied"], vec["unoccupied"], "softmax output of the network (result.probs)",
                 {"raw vector": vec})


def _sklearn(a, X, clf, occ_label, short):
    p = clf.predict_proba(X)[0]
    # name each column by what it means; the model's own class label is kept in brackets
    vec = {(f"occupied (class {c})" if str(c) == str(occ_label) else f"unoccupied (class {c})"): float(v)
           for c, v in zip(clf.classes_, p)}
    o = float(p[[str(c) for c in clf.classes_].index(str(occ_label))])
    return _pair(short, o, float(p.sum() - o), "scikit-learn predict_proba, one column per class",
                 {"raw vector": vec})


def _embed(a, crop):
    a.predict([crop])
    return _sklearn(a, a._embed([crop]), a._clf, 1, a.short)


def _classical(a, crop):
    import cv2
    from train_classical import features
    a.predict([crop])
    X = np.stack([features(cv2.cvtColor(np.array(crop.convert("RGB")), cv2.COLOR_RGB2BGR))])
    return _sklearn(a, X, a._clf, 1, a.short)


def _teammate(a, crop):
    from teammate_approach import get_embedding
    clf = a._classifier()
    return _sklearn(a, np.stack([get_embedding(crop)]), clf, 1, a.short)


def _knn(a, native_crop):
    from teammate1v2_approach import embed_batch
    clf = a._classifier()
    E = embed_batch([native_crop])
    q = clf.predict(E)[0]
    sims = (E @ clf.embeddings.T)[0]
    top = np.argsort(-sims)[:clf.k]
    labels = clf.labels[top]
    votes = {c: int((labels == c).sum()) for c in clf.classes}
    return _pair(a.short, q.scores.get("Occupied", 0.0), q.scores.get("Empty", 0.0),
                 f"weighted vote of the {clf.k} most similar labelled units",
                 {"neighbours voting": {"Occupied": votes.get("Occupied", 0), "Empty": votes.get("Empty", 0)},
                  "weighted share": {str(k): round(float(v), 4) for k, v in q.scores.items()}})


def _fusion(a, native_crop):
    a._load()
    rgb = [np.array(native_crop.convert("RGB"))]
    cp = a._clip_probs(rgb)[0]
    p = float(a.predict([native_crop])[0])
    return _pair(a.short, p, 1 - p, "fused score of CLIP probe + edge logic (crop only, no device vote)",
                 {"CLIP probe softmax": {"open slot": float(cp[0]), "closed panel": float(cp[1]),
                                         "occupied": float(cp[2])}})


def _one(a, crop, native_crop):
    key = a.key
    if key in ("yolov8m_cls", "yolo11s_cls"):
        return _yolo(a, crop)
    if key in ("embed_dinov2", "embed_clip"):
        return _embed(a, crop)
    if key == "classical":
        return _classical(a, crop)
    if key == "teammate":
        return _teammate(a, crop)
    if key == "knn_v2":
        return _knn(a, native_crop)
    if key == "fusion_t2":
        return _fusion(a, native_crop)
    p = float(a.predict([crop])[0])
    return _pair(a.short, p, 1 - p, "approach output")


def raw_outputs(img, box, approach):
    """{'rows': [per-model pair], 'final': pair, 'note': str} for the unit at `box` in `img`."""
    crop = occ.crop_unit(img, box)
    native = img.convert("RGB").crop(tuple(int(v) for v in box))
    members = [m for m in getattr(approach, "members", []) if m.available()]
    if members:
        rows = [_one(m, crop, native) for m in members]
        o = float(np.mean([r["occupied"] for r in rows]))
        u = float(np.mean([r["unoccupied"] for r in rows]))
        final = _pair("Ensemble (average)", o, u, f"average of the {len(rows)} models above")
        return {"rows": rows, "final": final,
                "note": "Each model's two numbers add up to 100%, so their average does too."}
    r = _one(approach, crop, native)
    return {"rows": [], "final": r, "note": "The two numbers come out of the model together and add up to 100%."}
