"""Is this rack photo taken from a side angle? Used to make the occupancy answer stricter on such photos.

A DINOv2 ViT-B/14 embedding of the whole photo (the same backbone as the Ensemble's DINOv2 member) and a logistic
regression trained on the test photos marked by eye (models/v2/side_angle_photos.json; scripts/occupancy/side_angle_eval.py).
Measured with 5-fold cross-validation it finds about 7 in 10 side-angle photos and also flags some front-facing
ones, so it only makes the model more careful (SIDE_SURE), it never changes an answer by itself.

Straight-line geometry (converging device edges, leaning posts) was tried first and was weaker: cables and
shelves are long slanted lines too.
"""
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
CLF = ROOT / "models" / "v2" / "side_angle_clf.joblib"
SIDE_P = 0.5       # P(side angle) at or above this -> side-angle photo
SIDE_SURE = 0.90   # on a side-angle photo the classifier must be this sure to answer (otherwise uncertain)
_state = {}


def available():
    return CLF.exists()


def p_side(img):
    """P(the photo is taken from a side angle), 0..1. img: PIL image of the whole rack photo."""
    if "clf" not in _state:
        import joblib
        from train_embed import load_backbone
        _state["clf"] = joblib.load(CLF)          # our own locally trained sklearn model - trusted artefact
        _state["embed"] = load_backbone("dinov2")[1]
    return float(_state["clf"].predict_proba(_state["embed"]([img.convert("RGB")]))[0, 1])


def is_side_angle(img):
    p = p_side(img)
    return p >= SIDE_P, p
