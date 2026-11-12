"""A patch panel's units: the best run of adjacent rows, the higher one on a tie."""
from pipeline.detection import assign_devices_to_units


def row(label, y1, y2):
    return {"label": label, "box": [0, y1, 500, y2], "center_y": (y1 + y2) / 2}


# u05 at the top of the photo, u01 at the bottom, 50 px a row.
ROWS = [row("u05", 0, 50), row("u04", 50, 100), row("u03", 100, 150),
        row("u02", 150, 200), row("u01", 200, 250)]


def dev(cls, y1, y2):
    return {"class_name": cls, "box": [0, y1, 500, y2], "center": [250, (y1 + y2) // 2]}


def placed(d, others=()):
    # Two switches set the 1U height the unit count is measured against: 60 px,
    # so a panel of about 120 px measures as 2U.
    ref = [dev("Switch", 0, 60), dev("Switch", 60, 120)]
    return assign_devices_to_units([d, *ref, *others], ROWS)[0]["units"]


def test_a_panel_whose_cords_drag_its_box_down_is_placed_at_its_top():
    # 30 px into u05, 31 into u03: the cords hang below, so the top wins.
    assert placed(dev("Patch Panel", 20, 131)) == ["u04", "u05"]


def test_a_panel_plainly_lower_is_still_placed_lower():
    assert placed(dev("Patch Panel", 60, 160)) == ["u03", "u04"]


def test_a_panel_takes_adjacent_rows_only():
    assert placed(dev("Patch Panel", 100, 200)) == ["u02", "u03"]


def test_a_switch_is_not_moved_by_the_panel_rule():
    got = assign_devices_to_units([dev("Switch", 110, 160), dev("Switch", 0, 50)], ROWS)[0]["units"]
    assert got == ["u03"]
