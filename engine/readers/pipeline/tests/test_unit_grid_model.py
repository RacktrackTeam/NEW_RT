"""The rack's units as the model reads them, rather than a uniform grid.

Every rule that decides where a unit boundary falls is checked here without
loading weights, because the weights are not in git and a test that needs them
would simply be skipped everywhere it mattered.

The behaviour being pinned was measured on 24 real rack photos before it was
written: the model's boundaries sit 0.094 of a unit from the device seams they
should land on, where the inferred grid sits 0.220 away, and the model wins on
22 of the 24. The rules below are what turns raw detections into that ladder.
"""

import pytest

from pipeline.detection import _drop_duplicate_units, ladder_from_unit_boxes


def u(y1, y2, conf=0.8, x1=0, x2=500):
    return {"box": [x1, y1, x2, y2], "confidence": conf}


def tops(ladder):
    return [row["box"][1] for row in ladder]


def labels(ladder):
    return [row["label"] for row in ladder]


def test_nothing_in_gives_nothing_out():
    # The caller falls back to the inferred grid on an empty list, so this must
    # be an empty list and never an exception.
    assert ladder_from_unit_boxes([], [], 600, 500) == []


def test_unit_one_is_the_bottom_row():
    # The rack convention, and the one place the model's order and the rack's
    # numbering disagree: the model returns boxes top to bottom, U1 is the floor.
    ladder = ladder_from_unit_boxes([u(0, 100), u(100, 200), u(200, 300)], [], 400, 500)
    assert labels(ladder) == ["u03", "u02", "u01"]
    assert ladder[-1]["box"][1] == 200, "u01 is the lowest box on the image"


def test_overlapping_rows_are_split_down_the_middle():
    # The model's boxes overlap by a few pixels. Snapping each row's top to the
    # previous row's bottom would push every later row down by the accumulated
    # overlap, which is the drift this whole change exists to remove. The
    # boundary goes in the middle instead, so neither row is moved twice.
    ladder = ladder_from_unit_boxes([u(0, 110), u(90, 210), u(190, 300)], [], 400, 500)
    assert tops(ladder) == [0, 100, 200]
    for row in ladder:
        assert row["box"][1] < row["box"][3]
    # contiguous: no gap and no overlap left anywhere
    ordered = sorted(ladder, key=lambda r: r["box"][1])
    for a, b in zip(ordered, ordered[1:]):
        assert a["box"][3] == b["box"][1]


def test_rows_keep_their_own_height_because_a_photo_has_perspective():
    # A camera is never square on to a rack, so the near rows really are taller
    # in the image than the far ones. Forcing one height is what made the old
    # grid cut through panels, so heights must NOT be averaged away.
    ladder = ladder_from_unit_boxes([u(0, 60), u(60, 150), u(150, 270)], [], 400, 500)
    heights = sorted(row["box"][3] - row["box"][1] for row in ladder)
    assert heights == [60, 90, 120]


def test_a_missed_row_is_filled_in():
    # A dark or cable covered row the model did not see leaves a hole the width
    # of a unit. That is a row, and the devices in it need somewhere to live.
    ladder = ladder_from_unit_boxes([u(0, 100), u(100, 200), u(300, 400)], [], 500, 500)
    assert len(ladder) == 4, f"the 200-300 hole becomes a row, got {tops(ladder)}"
    assert 200 in tops(ladder)
    assert labels(ladder) == ["u04", "u03", "u02", "u01"]


def test_two_missed_rows_become_two_rows_not_one():
    ladder = ladder_from_unit_boxes([u(0, 100), u(300, 400)], [], 500, 500)
    assert len(ladder) == 4, f"a two unit hole is two rows, got {tops(ladder)}"


def test_a_sliver_between_rows_is_not_a_row():
    # Detection edges do not line up to the pixel. A few pixels of daylight
    # between two rows is the edge of a box, not a rack unit.
    ladder = ladder_from_unit_boxes([u(0, 100), u(108, 208)], [], 400, 500)
    assert len(ladder) == 2, f"an 8px sliver is not a unit, got {tops(ladder)}"


def test_the_same_row_found_twice_is_one_row():
    # Two boxes sharing most of their height are one physical row; the more
    # confident one wins. Counting both would invent a unit and shift every
    # number above it.
    kept = _drop_duplicate_units([u(100, 200, conf=0.4), u(104, 196, conf=0.9)])
    assert len(kept) == 1
    assert kept[0]["confidence"] == 0.9


def test_a_box_several_rows_tall_does_not_set_the_pitch():
    # If the frame, or a mis-fired unit, comes back as one tall box it must not
    # become the median height - everything below it would then be spaced wrong.
    ladder = ladder_from_unit_boxes(
        [u(0, 100), u(100, 200), u(200, 300), u(300, 400), u(0, 400, conf=0.3)],
        [],
        500,
        500,
    )
    assert len(ladder) == 4, f"the full height box is not a row, got {tops(ladder)}"


def test_the_rack_frame_sets_the_left_and_right_edge():
    ladder = ladder_from_unit_boxes(
        [u(200, 300, x1=210, x2=390)], [[100, 150, 900, 400]], 500, 1000
    )
    assert ladder[0]["box"][0] == 100
    assert ladder[0]["box"][2] == 900


def test_frame_carrying_on_below_the_last_row_means_more_rows():
    # A dark PDU at the floor is exactly this: the frame plainly continues, the
    # model saw no row there, and something is in it.
    ladder = ladder_from_unit_boxes([u(100, 200), u(200, 300)], [[0, 100, 500, 500]], 600, 500)
    assert len(ladder) == 4, f"two more rows fit below, got {tops(ladder)}"
    assert max(row["box"][3] for row in ladder) <= 500, "and they stop at the frame"


def test_frame_carrying_on_above_the_first_row_means_more_rows():
    ladder = ladder_from_unit_boxes([u(300, 400)], [[0, 100, 500, 400]], 600, 500)
    assert len(ladder) == 3, f"two more rows fit above, got {tops(ladder)}"
    assert min(row["box"][1] for row in ladder) >= 100, "and they stop at the frame"


def test_a_frame_only_a_little_taller_than_the_rows_adds_nothing():
    # Rack frame above the top unit is usually just frame. Only a gap worth
    # most of a row is a row.
    ladder = ladder_from_unit_boxes([u(100, 200), u(200, 300)], [[0, 80, 500, 320]], 400, 500)
    assert len(ladder) == 2, f"20px of frame is not a unit, got {tops(ladder)}"


def test_every_row_carries_the_shape_the_rest_of_the_pipeline_expects():
    # assign_devices_to_units and the annotators read these keys by name.
    ladder = ladder_from_unit_boxes([u(0, 100), u(100, 200)], [], 300, 500)
    for row in ladder:
        assert set(row) >= {"label", "box", "center", "center_y"}
        x1, y1, x2, y2 = row["box"]
        assert row["center"] == [(x1 + x2) // 2, (y1 + y2) // 2]
        assert row["center_y"] == pytest.approx((y1 + y2) / 2)
        assert all(isinstance(v, int) for v in row["box"])


def test_rows_never_run_off_the_image():
    ladder = ladder_from_unit_boxes([u(-40, 60), u(60, 260)], [[0, -60, 500, 400]], 200, 500)
    for row in ladder:
        assert row["box"][1] >= 0
        assert row["box"][3] <= 200


def test_a_cross_rail_below_the_rows_stops_the_ladder_at_the_rail():
    # The cabinet box runs on down to the castors; the bottom cross rail
    # closes the mounting space at 320. Nothing mounts below it.
    ladder = ladder_from_unit_boxes([u(100, 200), u(200, 300)], [[0, 100, 500, 600]], 700, 500,
                                    rails=[[0, 320, 500, 380]])
    assert len(ladder) == 2
    assert ladder[-1]["label"] == "u01"


def test_a_cross_rail_above_the_rows_stops_the_ladder_at_the_rail():
    ladder = ladder_from_unit_boxes([u(300, 400), u(400, 500)], [[0, 0, 500, 500]], 600, 500,
                                    rails=[[0, 150, 500, 190]])
    # 190 to 300 is a row's worth and more of frame inside the rail: one row.
    assert len(ladder) == 3


def test_an_upright_rail_says_nothing_about_where_the_rows_stop():
    ladder = ladder_from_unit_boxes([u(100, 200), u(200, 300)], [[0, 100, 500, 500]], 600, 500,
                                    rails=[[0, 100, 40, 500]])
    assert len(ladder) == 4, "the frame still carries on two rows below the last one"
