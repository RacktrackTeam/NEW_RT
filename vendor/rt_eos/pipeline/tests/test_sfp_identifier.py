"""The SFP / QSFP / OSFP cage identifier v2 (pipeline/sfp_identifier.py).

The rule layer is checked on boxes and scores made up here, so it needs no
model; the last test runs both models on two of the model team's sample
photos when the weights are present.
"""

import os

import cv2
import numpy as np
import pytest

from pipeline import sfp_identifier as sid
from pipeline.port_pattern import sfp_detections

ROOT = sid.REPO_ROOT
SPEC = os.path.join(ROOT, "pipeline", "sfp_type_v2.json")
HAVE_WEIGHTS = all(os.path.exists(os.path.join(ROOT, p)) for p in ("Models/sfp_cage_finder_tiles.pt", "Models/sfp_cage_finder_full_photo.pt"))


class RulesOnly(sid.SfpIdentifier):
    """The rule layer alone: no weights loaded."""

    def __init__(self, params=None):
        self.p = params or sid.Params()


def row(n, w, h, x0=10, y0=20, gap=6):
    return np.array([[x0 + i * (w + gap), y0, x0 + i * (w + gap) + w, y0 + h] for i in range(n)], float)


def scores(n, s):
    return np.tile(np.array(s, float), (n, 1))


def test_tiles_cut_a_strip_into_near_square_windows_and_keep_a_square_whole():
    assert sid.tile_windows(640, 600) == [(0, 0, 640, 600)]
    wins = sid.tile_windows(1900, 100)
    assert wins[0] == (0, 0, 200, 100) and wins[-1][2] == 1900
    assert all(x2 - x1 == 200 for x1, _, x2, _ in wins)


def test_a_row_of_like_cages_gets_one_type_from_the_vote():
    img = np.full((80, 400, 3), 200, np.uint8)
    b = row(8, 30, 26)
    s = scores(8, [0.8, 0.15, 0.05])
    s[2] = [0.3, 0.6, 0.1]  # one cage the model alone would call QSFP
    out = RulesOnly().refine(b, s, img)
    assert len(out) == 8
    assert {d["class_name"] for d in out} == {"SFP"}
    assert not any(d["uncertain"] for d in out)
    assert sid.overall_type(out) == "SFP"


def test_a_lone_weak_box_and_a_box_no_cage_could_be_are_dropped():
    img = np.full((80, 400, 3), 200, np.uint8)
    b = np.vstack([row(4, 30, 26), [[300, 10, 310, 70]], [[350, 20, 372, 42]]])  # a tall sliver, a lone box
    s = np.vstack([scores(4, [0.8, 0.1, 0.1]), [[0.9, 0.05, 0.05]], [[0.5, 0.3, 0.2]]])
    out = RulesOnly().refine(b, s, img)
    assert len(out) == 4
    assert all(d["bbox"][0] < 300 for d in out)


def test_a_split_vote_is_marked_uncertain_and_left_out_of_the_port_type():
    img = np.full((80, 400, 3), 200, np.uint8)
    b = row(6, 36, 24)
    out = RulesOnly(sid.Params(prior_w=0.0)).refine(b, scores(6, [0.34, 0.33, 0.33]), img)
    assert out and all(d["uncertain"] for d in out)
    assert sid.overall_type(out) is None

    class Fake:
        def identify(self, img):
            return out + [{"class_name": "QSFP", "confidence": 0.9, "bbox": [0, 0, 40, 20], "uncertain": False}]

    kept = sfp_detections(Fake(), img)
    assert [d["class_name"] for d in kept] == ["QSFP"], "only sure cages reach the port types"


def test_a_wide_dark_opening_proves_qsfp():
    img = np.full((60, 200, 3), 230, np.uint8)
    cv2.rectangle(img, (40, 20), (110, 45), (10, 10, 10), -1)  # opening 71 x 26, ratio 2.7
    assert sid.opening_aspect(img, (30, 10, 120, 55)) > 2.25
    assert sid.opening_aspect(img, (0, 0, 3, 3)) is None


def test_the_spec_names_the_weights_and_the_tuned_params():
    import json

    spec = json.load(open(SPEC))
    assert spec["weights"] == "Models/sfp_cage_finder_tiles.pt" and spec["partner"] == "Models/sfp_cage_finder_full_photo.pt"
    assert spec["params"]["ensemble"] is True and spec["params"]["qsfp_open_min"] == 2.25


@pytest.mark.skipif(not HAVE_WEIGHTS, reason="the v2 weights are not in Models/")
def test_both_models_on_the_teams_samples():
    from pipeline.detection import load_model

    pid = load_model(SPEC)
    assert hasattr(pid, "identify")
    assert pid.identify(np.zeros((0, 0, 3), np.uint8)) == []
    here = os.path.join(os.path.dirname(__file__), "fixtures", "sfp_v2")
    for name, want in (("sfp_strip.jpg", "SFP"), ("qsfp_panel.jpg", "QSFP")):
        img = cv2.imread(os.path.join(here, name))
        assert img is not None, name
        assert sid.overall_type(pid.identify(img)) == want, name


def test_a_weak_or_fallback_cage_only_retypes_a_port_the_port_model_found():
    from pipeline.port_pattern import refine_sfp_types

    class Fake:
        def identify(self, img):
            return [
                {"class_name": "SFP", "confidence": 0.82, "bbox": [0, 0, 30, 20], "uncertain": False, "rule": "model vote"},
                {"class_name": "SFP", "confidence": 0.42, "bbox": [100, 0, 130, 20], "uncertain": False, "rule": "model vote"},
                {"class_name": "OSFP", "confidence": 0.9, "bbox": [200, 0, 240, 20], "uncertain": False, "rule": "fallback: strongest group (low confidence)"},
                {"class_name": "QSFP", "confidence": 0.45, "bbox": [300, 0, 340, 20], "uncertain": False, "rule": "model vote"},
            ]

    typed = [{"class_name": "SFP", "confidence": 0.7, "bbox": [301, 0, 341, 20]}]
    refine_sfp_types(typed, sfp_detections(Fake(), np.zeros((40, 400, 3), np.uint8)))
    assert [(t["class_name"], t["bbox"][0]) for t in typed] == [("QSFP", 301), ("SFP", 0)], "the weak QSFP retypes, only the sure SFP is added"
