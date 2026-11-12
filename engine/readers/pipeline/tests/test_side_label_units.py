"""The unit numbers printed on a rack's rails, as side_labels reads them."""

from pipeline.side_labels import rail_unit_numbers


def _raw(text, y, conf=0.9):
    return {"text": text, "raw_conf": conf, "y_mid": y, "x_mid": 5, "h": 10}


def test_numbers_and_u_numbers_are_read_top_first():
    got = rail_unit_numbers([_raw("1", 950), _raw("U42", 20), _raw("u 21", 480)], "left", 1000)
    assert [g["n"] for g in got] == [42, 21, 1]
    assert got[0]["side"] == "left"
    assert got[0]["yPct"] == 2.0


def test_what_is_not_a_unit_number_is_left_out():
    got = rail_unit_numbers(
        [_raw("SW-01", 10), _raw("99", 20), _raw("0", 30), _raw("7", 40, conf=0.2), _raw("1044248", 50)],
        "right",
        1000,
    )
    assert got == []
