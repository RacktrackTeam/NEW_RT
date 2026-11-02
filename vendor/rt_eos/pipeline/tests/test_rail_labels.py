"""Reading the name a rack wears on its own rails.

Everything here runs without a model and without an OCR engine: a stand-in
reader hands back the readings a real one produced on the office rack, so the
part that matters - voting several uncertain readings into one answer, and
refusing the ones that are not worth offering - is tested on its own.
"""

import numpy as np

from pipeline.rail_labels import (
    is_plausible,
    read_rails,
    resolve_number_tails,
    vote,
    wide_enough,
)


class Reader:
    """Answers with a fixed list of readings, as easyocr does."""

    def __init__(self, texts, conf=0.6):
        self.texts = texts
        self.conf = conf
        self.calls = 0

    def readtext(self, image, detail=1, paragraph=False, **kwargs):
        self.calls += 1
        i = (self.calls - 1) % len(self.texts)
        return [([[0, 0], [1, 0], [1, 1], [0, 1]], self.texts[i], self.conf)]


# What the reader actually returned for the tape across the office rack's top
# rail, at seven different sizes and treatments.
OFFICE = [
    "SP-HyB-RMOI-ROL-RI",
    "SP-HyB AMOI-ROL-RI",
    "Sp-HyB-RMQI-Rof-AI",
    "SP HYD-AMOI-ROL-RI",
    "SP-HyB RMOI-ROL-RI",
    "Sp+hyd-AMOI-ROL-RI",
    "SP-HyB-RMOI-ROL-RI",
]


def test_many_uncertain_readings_vote_into_one_answer():
    text, agreement = vote(OFFICE)
    assert text.startswith("SP-HYB-")
    assert text.endswith("-R01-R1")
    assert agreement > 0.7
    # Upper case, hyphens, and the letters inside the numbers resolved.
    assert " " not in text and "." not in text
    assert "O" not in text.split("-")[-2]


def test_one_reading_stands_on_its_own():
    assert vote(["RACK-07"]) == ("RACK-07", 1.0)


def test_the_reader_failing_is_not_a_label():
    assert vote(["IIIIIIIIII"])[0] == ""
    assert vote(["----"])[0] == ""
    assert vote([""])[0] == ""


def test_a_word_with_no_number_is_not_a_rack_identifier():
    # "CAT.6" is printed on every patch panel in the office rack.
    assert not is_plausible("Gigabit")
    assert is_plausible("RACK-07")


def test_letters_inside_a_number_are_resolved_only_at_the_end():
    assert resolve_number_tails("SP-HYB-RMOI-ROL-RI") == "SP-HYB-RM01-R01-R1"
    assert resolve_number_tails("SOPHOS") == "SOPHOS"  # not a tail
    assert resolve_number_tails("RACK-IO") == "RACK-10"


def test_a_sliver_of_frame_is_not_read():
    assert wide_enough([0, 0, 200, 40])
    assert not wide_enough([0, 0, 12, 40])
    assert not wide_enough([0, 0, 200, 3])


def test_a_rail_with_a_label_is_read_and_one_without_is_not():
    image = np.zeros((400, 800, 3), np.uint8)
    image[20:60, 100:600] = 255  # a bright strip of tape
    found = read_rails(image, [[0, 10, 800, 80]], Reader(OFFICE))
    assert len(found) == 1
    assert found[0]["text"].startswith("SP-HYB-")
    assert found[0]["conf"] > 0.7
    assert found[0]["readings"] > 1

    noise = read_rails(image, [[0, 10, 800, 80]], Reader(["IIII", "----"]))
    assert noise == []


def test_readings_that_disagree_badly_are_not_offered():
    image = np.zeros((400, 800, 3), np.uint8)
    image[20:60, 100:600] = 255
    disagree = ["RACK-01", "ZONE-97", "DB-PROD-44", "SWX-12"]
    assert read_rails(image, [[0, 10, 800, 80]], Reader(disagree), min_agreement=0.9) == []


# ── what the rest of the scan does with a rail reading ──────────────────────


def test_a_rack_id_of_any_shape_is_kept_when_it_was_read_off_the_rail():
    from pathlib import Path

    from pipeline.physical_layer import rack_identity

    side = {
        "labels": [
            {"text": "SP-HYB-RM01-R01-R1", "side": "rail", "conf": 0.94, "yPct": 12},
            {"text": "SWHOME", "side": "right", "conf": 0.9, "yPct": 40},
            {"text": "RACK-07", "side": "right", "conf": 0.8, "yPct": 60},
        ]
    }
    out = rack_identity("RK-TEST0001", None, side, Path("/nonexistent"))
    texts = {c["text"]: c["source"] for c in out["candidates"]}
    # The rail names the rack whatever shape the customer's id is...
    assert texts.get("SP-HYB-RM01-R01-R1") == "rack rail"
    # ...while a chip on the side margin still has to look like a rack id, so a
    # device's own name is not mistaken for the rack's.
    assert "SWHOME" not in texts
    assert texts.get("RACK-07") == "rail chip"
    assert out["label"]["text"] == "SP-HYB-RM01-R01-R1"
