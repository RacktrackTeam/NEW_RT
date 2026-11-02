"""Approach 2/3: frozen foundation-model image embeddings + a small head.

    python3 src/train_embed.py clip     # CLIP ViT-B/32 (semantic, web-scale)
    python3 src/train_embed.py dinov2   # DINOv2 ViT-B/14 (self-supervised, fine visual structure)
"""
import json
import pathlib
import sys
import time

import joblib
import numpy as np
import torch
from PIL import Image
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score
from sklearn.neural_network import MLPClassifier

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data/cls_data"
OUT = ROOT / "models"
OUT.mkdir(exist_ok=True)
DEV = "mps" if torch.backends.mps.is_available() else "cpu"
IMAGENET = ([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])


def load_backbone(kind="clip"):
    """Return (name, embed_fn) where embed_fn maps a list of PIL images -> (N, D) L2-normalised array."""
    if kind == "clip":
        import open_clip
        model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai")
        model.eval().to(DEV)

        def embed(ims):
            x = torch.stack([preprocess(im) for im in ims]).to(DEV)
            with torch.no_grad():
                e = model.encode_image(x)
            return torch.nn.functional.normalize(e.float(), dim=-1).cpu().numpy()

        return "clip_vit_b32", embed

    if kind == "dinov2":
        import timm
        import torchvision.transforms as T
        model = timm.create_model("vit_base_patch14_dinov2.lvd142m", pretrained=True, num_classes=0, img_size=224)
        model.eval().to(DEV)
        tf = T.Compose([T.Resize((224, 224)), T.ToTensor(), T.Normalize(*IMAGENET)])

        def embed(ims):
            x = torch.stack([tf(im.convert("RGB")) for im in ims]).to(DEV)
            with torch.no_grad():
                e = model(x)
            return torch.nn.functional.normalize(e.float(), dim=-1).cpu().numpy()

        return "dinov2_vit_b14", embed

    raise ValueError(kind)


def extract(split, embed):
    X, y, files = [], [], []
    for label, cls in enumerate(["unoccupied", "occupied"]):  # occupied = 1
        fs = sorted((DATA / split / cls).glob("*.jpg"))
        for i in range(0, len(fs), 128):
            ims = [Image.open(f).convert("RGB") for f in fs[i:i + 128]]
            X.append(embed(ims))
            y += [label] * len(ims)
            files += [f.name for f in fs[i:i + 128]]
    return np.concatenate(X), np.array(y), files


if __name__ == "__main__":
    kind = sys.argv[1] if len(sys.argv) > 1 else "clip"
    t = time.time()
    name, embed = load_backbone(kind)
    print("backbone:", name, "device:", DEV)
    Xtr, ytr, _ = extract("train", embed)
    Xva, yva, fva = extract("val", embed)
    print(f"features {Xtr.shape} extracted in {time.time()-t:.0f}s")
    np.savez(OUT / f"embed_{kind}_features.npz", Xtr=Xtr, ytr=ytr, Xva=Xva, yva=yva)

    cands = {f"LogReg C={C}": LogisticRegression(C=C, max_iter=5000) for C in [3, 10, 30, 100]}
    cands["MLP 512"] = MLPClassifier((512,), alpha=1e-3, early_stopping=True, max_iter=300, random_state=0)
    best_name, best_cv = None, -1
    for n, c in cands.items():
        cv = cross_val_score(c, Xtr, ytr, cv=3).mean()
        print(f"{n}: cv acc {cv:.4f}")
        if cv > best_cv:
            best_name, best_cv = n, cv
    clf = cands[best_name].fit(Xtr, ytr)
    # joblib pickle of our own freshly-trained sklearn model — trusted, local artefact.
    joblib.dump({"backbone": kind, "head": best_name, "clf": clf}, OUT / f"embed_{kind}_clf.joblib")

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
        {"approach": f"{name} embeddings + {best_name}", "val_accuracy": acc, "n_val": len(yva),
         "confusion": cm, "errors": errors, "head": best_name, "cv_accuracy": best_cv},
        open(OUT / f"embed_{kind}_eval.json", "w"), indent=1,
    )
    print(f"{name} val accuracy: {acc:.4f}  confusion={cm}  ({time.time()-t:.0f}s total)")
