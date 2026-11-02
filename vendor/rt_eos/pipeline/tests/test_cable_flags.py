"""Cable flags: the repair of a reading, the parse of a line into its device,
port and position, the merge of the same print read twice, and the grouping
of two lines on one tag. The boxes are hand-built; the reader is not run."""

from pipeline.cable_flags import (
    group_flags,
    merge_lines,
    parse_end,
    port_number,
    repair_line,
    same_flag,
    tiles_of,
)


def test_a_clean_line_is_left_as_printed():
    assert repair_line("nvs-sgkc-sis-sw05b Gi1-0-14 B12") == "nvs-sgkc-sis-sw05b Gi1-0-14 B12"
    assert repair_line("nvs-sgkc-dcm-02-a10-c002 Twe1/0/8 A10") == "nvs-sgkc-dcm-02-a10-c002 Twe1/0/8 A10"


def test_confusions_are_repaired_only_where_a_digit_must_stand():
    assert repair_line("s0dc-sis-5w05b G12-0-3 B11") == "s0dc-sis-sw05b Gi2-0-3 B11"
    assert repair_line("nvs-sgkc-sis-waf01el P4-0") == "nvs-sgkc-sis-waf01e1 P4-0"
    assert repair_line("nvs-sgkc-dc-sis-pg09i1 Eth1-13809") == "nvs-sgkc-dc-sis-pg09i1 Eth1-13 B09"
    assert repair_line("nvs-sgkc-sis-mt02 TE0 807") == "nvs-sgkc-sis-mt02 TE0 B07"
    # A letter that is really a letter stays: Mod3-4B, the s of tpips04, the B of a serial.
    assert repair_line("ys-sgkc-dc-sis-tpips04 Mod3-4B B12") == "ys-sgkc-dc-sis-tpips04 Mod3-4B B12"
    assert repair_line("S/N: FDO2203B0WU") == "S/N FDO2203B0WU"


def test_glued_tokens_are_split():
    assert repair_line("sgkc-dcm-02-a10-b1002Eth1-8A10") == "sgkc-dcm-02-a10-b1002 Eth1-8 A10"
    assert repair_line("01cETH1-16B07") == "01c ETH1-16 B07"


def test_parse_end_gives_device_port_and_position():
    e = parse_end("nvs-sgkc-sis-sw05b Gi1-0-14 B12")
    assert e == {"device": "nvs-sgkc-sis-sw05b", "port": "Gi1-0-14", "position": "B12", "portNo": 14}
    assert parse_end("nvs-sgkc-sis-sg01e2 LOM B12")["portNo"] is None
    assert parse_end("sw03b E1-48 B9")["device"] == "sw03b"
    # The device cut off: still an end, with no device.
    assert parse_end("1 HA1-A B09") == {"device": None, "port": "HA1-A", "position": "B09", "portNo": 1}


def test_what_is_not_a_flag_line_has_no_end():
    for text in ["2001703", "SG3 LOC Rack A08 RU27", "Cisco ASA 5585-X SFR SSP-40", "R5-232 Comor", "paloalto", "Name-e s102"]:
        assert parse_end(repair_line(text)) is None, text


def test_port_number_is_the_last_run_of_digits():
    assert port_number("Gi1-0-14") == 14
    assert port_number("Twe1/0/15") == 15
    assert port_number("Mod3-4B") == 4
    assert port_number("LOM") is None


def _line(x1, y1, x2, y2, text, conf=0.9, orientation="vertical"):
    end = parse_end(repair_line(text))
    return {"box": [x1, y1, x2, y2], "text": text, "raw": text, "conf": conf, "orientation": orientation, "turn": 0, "end": end}


def test_the_same_print_read_twice_is_kept_once_the_fuller_first():
    full = _line(100, 100, 130, 300, "nvs-sgkc-sis-sw05b Gi1-11 B12", 0.93)
    part = _line(100, 180, 130, 300, "sw05b Gi1-11 B12", 0.99)
    other = _line(400, 100, 430, 300, "nvs-sgkc-sis-sw05a Gi1-11 B11", 0.9)
    kept = merge_lines([part, full, other])
    assert [k["text"] for k in kept] == [full["text"], other["text"]]


def test_two_lines_side_by_side_are_one_flag():
    a = _line(100, 100, 128, 300, "nvs-sgkc-sis-sw05b Gi1-0-14 B12")
    b = _line(130, 105, 158, 305, "nvs-sgkc-sis-sg01e2 LOM B12")
    far = _line(400, 100, 428, 300, "nvs-sgkc-sis-sw05a Gi1-11 B11")
    assert same_flag(a, b) and not same_flag(a, far)
    flags = group_flags([a, b, far])
    assert len(flags) == 2
    assert flags[0]["lines"] == [0, 1] and flags[0]["ends"] == 2
    assert a["flag"] == b["flag"] != far["flag"]


def test_tiles_cover_the_image_with_overlap():
    tiles = tiles_of(1859, 1395)
    assert tiles[0] == (0, 0, 800, 800)
    assert any(t[2] == 1859 for t in tiles) and any(t[3] == 1395 for t in tiles)
    xs = sorted({t[0] for t in tiles})
    assert xs[1] - xs[0] == 540  # 800 side, 260 overlap
    # A photo taken at full size gets larger tiles, so the reader sees text at the same size.
    big = tiles_of(4032, 3024)
    assert big[0][2] > 1600


def test_the_t_of_eth_read_as_a_one_is_put_back():
    assert repair_line("09020 E1h1-24 A04") == "09020 Eth1-24 A04"
    assert parse_end(repair_line("nvs-sgkc-dc-sis-pg03i2 Elh0-24 A04"))["port"] == "Eth0-24"


def _piece(x, y, w, h, text, conf=0.9):
    return {"box": [x, y, x + w, y + h], "poly": [[x, y], [x + w, y], [x + w, y + h], [x, y + h]], "text": text, "conf": conf, "turn": 0, "scale": 2.0, "orientation": "horizontal"}


def test_pieces_of_one_line_are_joined_on_their_baseline():
    from pipeline.cable_flags import join_pieces
    pieces = [
        _piece(100, 100, 220, 20, "nvs-sgkc-sis-waf01e2"),
        _piece(330, 101, 30, 20, "FO"),          # a gap of half a height
        _piece(372, 99, 40, 20, "B11"),
        _piece(100, 130, 220, 20, "nvs-sgkc-sis-waf01e1"),   # the next line, a height below: not joined
        _piece(900, 100, 60, 20, "2001703"),      # far along the same baseline: not joined
    ]
    out = join_pieces(pieces)
    texts = sorted(o["text"] for o in out)
    assert texts == ["2001703", "nvs-sgkc-sis-waf01e1", "nvs-sgkc-sis-waf01e2 FO B11"]
    joined = next(o for o in out if o["pieces"] == 3 if "pieces" in o)
    assert joined["box"] == [100, 99, 412, 121]


def test_vertical_pieces_join_in_reading_order():
    from pipeline.cable_flags import join_pieces
    # Text running down the photo: the polygon's long edge is vertical.
    def vp(x, y, w, h, text):
        return {"box": [x, y, x + w, y + h], "poly": [[x, y], [x, y + h], [x + w, y + h], [x + w, y]], "text": text, "conf": 0.9, "turn": 90, "scale": 2.0, "orientation": "vertical"}
    out = join_pieces([vp(100, 300, 20, 40, "B11"), vp(101, 100, 20, 190, "sw05b Gi1-7")])
    assert [o["text"] for o in out] == ["sw05b Gi1-7 B11"]


def test_a_box_is_straightened_along_its_own_angle():
    import numpy as np
    from pipeline.cable_flags import straighten_poly
    img = np.full((400, 400, 3), 255, np.uint8)
    # A box 200 long and 20 thick, lying at 30 degrees.
    import math
    c = math.cos(math.radians(30)); s_ = math.sin(math.radians(30))
    p0 = np.array([100.0, 100.0]); d = np.array([c, s_]) * 200; n = np.array([-s_, c]) * 20
    poly = [p0, p0 + d, p0 + d + n, p0 + n]
    crop = straighten_poly(img, poly, height=40, pad=0.1)
    assert crop is not None
    h, w = crop.shape[:2]
    assert h == 40 + 8 and abs(w - (400 + 80)) <= 2  # text height 40 plus the margin, ten times as long
    assert straighten_poly(img, [[0, 0], [1, 0], [1, 1], [0, 1]]) is None


def test_the_photo_vocabulary_repairs_a_cut_or_misread_name_only_when_one_name_fits():
    from pipeline.cable_flags import photo_vocabulary, repair_against
    lines = [
        {"text": "nvs-sgkc-sis-rt02", "conf": 0.99, "end": None},          # a faceplate name
        {"text": "nvs-sgkc-sis-rt01", "conf": 0.97, "end": None},
        {"text": "CISCO", "conf": 0.99, "end": None},                      # not a name
        {"text": "nvs-sgkc-sis-sw03b", "conf": 0.6, "end": None},          # not sure enough
        _line(0, 0, 10, 10, "nvs-sgkc-dc-sis-pg09i1 Eth1-10 B09", 0.98),   # a flag end of four segments, sure
    ]
    names = photo_vocabulary(lines)
    assert sorted(names) == ["nvs-sgkc-dc-sis-pg09i1", "nvs-sgkc-sis-rt01", "nvs-sgkc-sis-rt02"]
    assert repair_against("nvs-sgkc-sis-nt02", names) == "nvs-sgkc-sis-rt02"     # one character off
    assert repair_against("sis-pg09i1", names) == "nvs-sgkc-dc-sis-pg09i1"       # the tail
    assert repair_against("nvs-sgkc-sis-rt0", names) is None                      # fits rt01 and rt02
    assert repair_against("t02", names) is None                                    # too short to say
    assert repair_against("nvs-sgkc-sis-rt02", names) is None                     # already right


def test_the_second_chance_reads_a_poor_line_again_and_keeps_only_a_further_reading(monkeypatch):
    """handwriting_pass: a line read poorly is read again from contrast and
    black-and-white variants and from a longer box; the reading that parses
    further, and whose device the names known repair, replaces it. A reading
    that goes no further leaves the line alone. The reader is stubbed."""
    import numpy as np
    from pipeline import cable_flags as cf

    img = np.full((300, 300, 3), 230, np.uint8)
    poly = [[50.0, 100.0], [150.0, 100.0], [150.0, 112.0], [50.0, 112.0]]
    cut = {"poly": poly, "box": [50, 100, 150, 112], "text": "nvs-50kc", "raw": "nvs-50kc", "conf": 0.6, "end": None}
    short = [[50.0, 200.0], [80.0, 200.0], [80.0, 212.0], [50.0, 212.0]]
    noise = {"poly": short, "box": [50, 200, 80, 212], "text": "CLASS 1 LASER", "raw": "CLASS 1 LASER", "conf": 0.9, "end": None}
    calls = {"n": 0}

    def fake(crop):
        calls["n"] += 1
        # Only the longest box (the cut line grown along its text) shows the whole flag line.
        return ("nvs-sgkc-sis-sw03b E1-48 B9", 0.83) if crop.shape[1] > 1500 else ("nvs-5gkc", 0.7)

    monkeypatch.setattr(cf, "_recognise", fake)
    n = cf.handwriting_pass(img, [cut, noise], names=["nvs-sgkc-sis-sw03b"])
    assert n == 1
    assert cut["text"] == "nvs-sgkc-sis-sw03b E1-48 B9"
    assert cut["end"]["device"] == "nvs-sgkc-sis-sw03b" and cut["end"]["portNo"] == 48
    assert cut["hand"].startswith("longer")
    assert noise["text"] == "CLASS 1 LASER" and "hand" not in noise

    # A reading that parses no further is not taken.
    good = {"poly": poly, "box": [50, 100, 150, 112], "text": "sw05b Gi1-7 B12", "raw": "sw05b Gi1-7 B12", "conf": 0.7,
            "end": parse_end("sw05b Gi1-7 B12")}
    monkeypatch.setattr(cf, "_recognise", lambda crop: ("sw05b Gi1-7", 0.99))
    assert cf.handwriting_pass(img, [good]) == 0
    assert good["text"] == "sw05b Gi1-7 B12"


def test_reading_score_prefers_a_device_the_names_known_repair():
    from pipeline.cable_flags import reading_score
    names = ["nvs-sgkc-sis-sw05b"]
    assert reading_score("nvs-sgkc-sis-sw05b Gi1-7 B12", names) == (3, 2)
    assert reading_score("nvs-sgkc-sis-sw05x Gi1-7 B12", []) == (3, 1)
    assert reading_score("CLASS 1 LASER", names) == (0, 0)


def test_a_box_grows_along_its_own_text_direction():
    import numpy as np
    from pipeline.cable_flags import extend_poly
    q = extend_poly([[100, 100], [200, 100], [200, 110], [100, 110]], 0.5, 1000, 1000)
    assert np.allclose(q, [[50, 100], [250, 100], [250, 110], [50, 110]])
    # Kept inside the image.
    q = extend_poly([[10, 100], [110, 100], [110, 110], [10, 110]], 1.0, 150, 1000)
    assert q[:, 0].min() == 0 and q[:, 0].max() == 149
