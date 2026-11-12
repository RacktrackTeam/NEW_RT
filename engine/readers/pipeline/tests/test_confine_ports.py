"""The rule that keeps a device's own ports and drops everything else.

The owner reported on 22 September 2026 that on every device in a rack "the
ports are starting on the panel, not on the port area". The standalone reader
showed why: a device box is a rack-width strip, so the crop took in the cable
plugs hanging in front of the hidden ports and the neighbouring panel's jacks,
and the port model marked those too.

confine_to_device keeps the bands a device's real ports form and drops the
rest. These are the three things it has to get right.
"""

from pipeline.port_pattern import confine_to_device


def port(x, y, w=8, h=10):
    return {
        "box": [x, y, x + w, y + h],
        "center": [x + w / 2, y + h / 2],
        "class_name": "RJ45",
        "status": "connected",
        "confidence": 0.9,
        "port_category": "main",
        "index": 0,
    }


def classified(main):
    return {
        "main_ports": list(main),
        "sfp_ports": [],
        "console_ports": [],
        "other_ports": [],
        "all_boxes": [p["box"] for p in main],
        "pattern_info": {},
    }


def test_one_row_keeps_its_ports_and_drops_the_strays():
    """A switch: eight ports in a row, one plug above it and one below."""
    row = [port(10 + i * 12, 40) for i in range(8)]
    strays = [port(200, 5), port(210, 95)]
    out = confine_to_device(classified(row + strays))
    assert len(out["main_ports"]) == 8
    assert out["pattern_info"]["confined"] == 2
    assert out["pattern_info"]["bands"] == 1
    # And what is left is numbered from one again.
    assert [p["index"] for p in out["main_ports"]] == list(range(1, 9))


def test_a_panel_keeps_both_of_its_rows():
    """Two rows of twelve is what a 24-port patch panel looks like, and the
    sparser second row of a panel must never be read as a stray."""
    rows = [port(10 + i * 12, 20) for i in range(12)] + [port(10 + i * 12, 60) for i in range(12)]
    out = confine_to_device(classified(rows))
    assert len(out["main_ports"]) == 24
    assert out["pattern_info"]["bands"] == 2
    assert out["pattern_info"]["confined"] == 0


def test_a_port_outside_the_crop_is_not_this_device_s():
    """The guard for a crop that was padded outwards: a box whose centre falls
    outside the crop belongs to whatever the padding reached into."""
    row = [port(10 + i * 12, 40) for i in range(8)]
    out = confine_to_device(classified(row + [port(600, 300)]), crop_shape=(60, 400))
    assert len(out["main_ports"]) == 8


def test_too_few_ports_to_judge_are_left_alone():
    """One port is not a band, and a rule that cannot tell must not guess."""
    one = classified([port(10, 40)])
    assert confine_to_device(one)["main_ports"] == one["main_ports"]


def test_the_unit_ladder_refuses_to_be_taller_than_a_rack():
    """A collapsed unit height used to tile a photograph into a thousand
    one-pixel shelves, and a patch panel came out named SP-RI-4015-PP1."""
    from pipeline.detection import MAX_RACK_UNITS, build_contiguous_unit_grid

    devices = [{"class_name": "Switch", "box": [100, 1000, 900, 1040]},
               {"class_name": "Patch Panel", "box": [100, 3000, 900, 3080]}]
    # A sane unit height gives a sane ladder: 100px units over a 4032px
    # photograph is a 38U rack, which is a rack somebody owns.
    good = build_contiguous_unit_grid(devices, 100, img_shape=(4032, 3024, 3))
    assert 0 < len(good) <= MAX_RACK_UNITS
    # So is one that would be 96 rows: a 4032px photo of 40px units is not a
    # rack, it is a bad estimate, and it is refused too.
    assert build_contiguous_unit_grid(devices, 40, img_shape=(4032, 3024, 3)) == []
    # A collapsed one gives nothing at all, rather than thousands of shelves.
    assert build_contiguous_unit_grid(devices, 1, img_shape=(4032, 3024, 3)) == []
