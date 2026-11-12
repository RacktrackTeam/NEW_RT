"""SFP cages come in pairs, so a device never reports an odd number of them."""

from pipeline.port_pattern import even_out_sfp


def port(x, y=10, w=30, h=30, conf=0.8):
    return {
        "box": [x, y, x + w, y + h],
        "center": [x + w // 2, y + h // 2],
        "status": "empty",
        "class_name": "SFP",
        "confidence": conf,
        "port_category": "sfp",
    }


def test_an_even_count_is_left_exactly_as_it_was_read():
    four = [port(x) for x in (600, 640, 680, 720)]
    assert even_out_sfp(four, img_width=800) == four
    assert even_out_sfp([], img_width=800) == []


def test_three_in_a_row_with_a_gap_get_the_missing_one_put_back_in_the_gap():
    out = even_out_sfp([port(600), port(640), port(720)], img_width=800)
    assert len(out) == 4
    added = [p for p in out if p.get("inferred")]
    assert len(added) == 1 and 670 <= added[0]["center"][0] <= 705
    assert added[0]["confidence"] == 0.0
    assert [p["index"] for p in out] == [1, 2, 3, 4]


def test_three_evenly_spaced_are_extended_where_there_is_room_and_no_other_port():
    main = [[560, 10, 590, 40]]  # a copper port sits to the left
    out = even_out_sfp([port(600), port(640), port(680)], img_width=800, taken=main)
    added = [p for p in out if p.get("inferred")][0]
    assert added["box"][0] > 680  # so it goes on the right


def test_one_cage_alone_becomes_a_pair():
    out = even_out_sfp([port(700)], img_width=800)
    assert len(out) == 2 and sum(1 for p in out if p.get("inferred")) == 1


def test_nothing_is_drawn_off_the_device_to_make_the_number_even():
    boxed_in = [[0, 10, 28, 40]]
    out = even_out_sfp([port(30, w=30)], img_width=62, taken=boxed_in)
    assert len(out) == 1
