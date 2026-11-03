"""The occupancy models load in this environment, from inside this folder, with no request leaving the machine.

Pass 2 found every scan failing (two models pickled under numpy 2) and the model load reaching huggingface.co and
github.com; the suite passed anyway because nothing loaded a model. These tests load them. Run the suite under
tools/with-model-lock.sh: the Ensemble test loads five models (about a minute on this Mac)."""
import os
import pathlib
import pickle

import joblib
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
V2 = ROOT / "models" / "v2"


def test_hub_switches_point_inside_the_folder():
    import local_models as L
    assert os.environ["HF_HUB_OFFLINE"] == "1" and os.environ["TRANSFORMERS_OFFLINE"] == "1" and os.environ["YOLO_OFFLINE"] == "1"
    for k in ("HF_HOME", "TORCH_HOME", "EASYOCR_MODULE_PATH"):
        assert pathlib.Path(os.environ[k]).resolve().is_relative_to(ROOT), k
    assert (L.TORCH_HOME / "hub" / "facebookresearch_dinov2_main" / "hubconf.py").exists()
    assert (L.TORCH_HOME / "hub" / "checkpoints" / "dinov2_vitl14_pretrain.pth").exists()


@pytest.mark.parametrize("name", ["classical_clf.joblib", "embed_clip_clf.joblib", "embed_dinov2_clf.joblib",
                                  "teammate_clf.joblib", "side_angle_clf.joblib"])
def test_sklearn_models_load_under_this_numpy(name):
    d = joblib.load(V2 / name)              # our own locally trained models - trusted artefacts
    assert hasattr(d["clf"] if isinstance(d, dict) and "clf" in d else d, "predict_proba") or isinstance(d, dict)


def test_knn_gallery_loads_under_this_numpy():
    with open(V2 / "knn_v2_gallery.pkl", "rb") as f:
        d = pickle.load(f)                   # numpy arrays + numbers only (inspected)
    assert d["embeddings"].shape[0] == len(d["labels"])


def test_the_ensemble_predicts_offline():
    from PIL import Image
    import occupancy as occ
    ens = {a.key: a for a in occ.build_approaches("v2")}["ensemble"]
    members = [m for m in ens.members if m.available()]
    assert len(members) == 5, [m.key for m in ens.members]
    p = ens.predict([Image.new("RGB", (400, 40), (60, 60, 60)), Image.new("RGB", (400, 40), (200, 200, 200))])
    assert p.shape == (2,) and ((p >= 0) & (p <= 1)).all()
