"""The dust-cap classifier's slot: which ports it looks at, how its answer
is worded, and that a missing weights file leaves the ports alone. The
model is a stand-in; no weights are loaded."""
import pytest


import numpy as np

from pipeline.cap_model import apply_cap_model, classify_caps, padded_box, state_of


def _img(w=400, h=200):
    return np.full((h, w, 3), 128, np.uint8)


def test_the_answer_in_the_pipelines_words():
    assert state_of("capped", 0.93) == ("capped", "dust cap model: cap on (0.93)")
    assert state_of("uncapped", 0.81) == ("empty", "dust cap model: no cap (0.81)")
    assert state_of("uncapped", 0.55) == ("uncertain", "dust cap model not sure (uncapped 0.55)")


def test_the_crop_is_padded_and_kept_inside_the_image():
    assert padded_box([100, 50, 140, 90], 400, 200) == (97, 47, 143, 93)
    assert padded_box([0, 0, 40, 40], 400, 200) == (0, 0, 43, 43)
    assert padded_box([390, 190, 400, 200], 400, 200) == (389, 189, 400, 200)
    assert padded_box([10, 10, 10, 30], 400, 200) is None


def test_only_empty_ports_are_classified_and_connected_ones_are_left_alone():
    seen = []

    def predict(crop):
        seen.append(crop.shape)
        return ("capped", 0.9) if len(seen) == 1 else ("uncapped", 0.7)

    ports = [
        {"box": [100, 50, 140, 90], "status": "empty"},
        {"box": [150, 50, 190, 90], "status": "connected", "cap_state": "connected"},
        {"box": [200, 50, 240, 90], "status": "empty"},
        {"box": [250, 50, 258, 58], "status": "empty"},   # too small for the model
    ]
    n = classify_caps(_img(), ports, predict)
    assert n == 2
    assert ports[0]["cap_state"] == "capped" and ports[0]["cap_source"] == "model"
    assert ports[0]["cap_model"] == {"label": "capped", "conf": 0.9}
    assert ports[1]["cap_state"] == "connected" and "cap_source" not in ports[1]
    assert ports[2]["cap_state"] == "empty"
    assert "cap_state" not in ports[3]
    assert seen[0] == (46, 46, 3)


def test_a_bad_crop_does_not_stop_the_rest():
    calls = []

    def predict(crop):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("model hiccup")
        return ("capped", 0.95)

    ports = [{"box": [100, 50, 140, 90], "status": "empty"}, {"box": [200, 50, 240, 90], "status": "empty"}]
    assert classify_caps(_img(), ports, predict) == 1
    assert "cap_state" not in ports[0] and ports[1]["cap_state"] == "capped"


def test_without_the_weights_file_the_slot_stays_open():
    ports = [{"box": [100, 50, 140, 90], "status": "empty", "cap_state": "uncertain", "cap_reason": "no clear difference in row 1"}]
    n = apply_cap_model(_img(), ports, {"models": {"cap_classifier": "Models/no-such-file.pth"}})
    assert n == 0
    assert ports[0]["cap_state"] == "uncertain" and "cap_source" not in ports[0]


def test_checkpoint_layouts_from_the_model_team():
    from pipeline.cap_model import normalize_classes, checkpoint_state, infer_model_name
    assert normalize_classes(["Cap", " uncap ", "capped", "uncapped"]) == ["capped", "uncapped", "capped", "uncapped"]
    assert checkpoint_state({"model_state_dict": {"a": 1}}) == {"a": 1}
    assert checkpoint_state({"state_dict": {"b": 2}}) == {"b": 2}
    with pytest.raises(ValueError):
        checkpoint_state({"classes": []})
    assert infer_model_name({"layer1.0.conv3.weight": 0}) == "resnet50"
    assert infer_model_name({"layer1.0.conv2.weight": 0}) == "resnet18"
