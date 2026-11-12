"""Zone close-ups: numbering the ports and pairing each label with its port.

The boxes are hand-built and the image is drawn here, so what is tested is the
geometry (which port is number 7, which label sits on it), not the models. A
wrong number here means a fibre reported in the wrong port.
"""

import cv2
import numpy as np

from pipeline.zone_read import keep_label, number_zone, order_ports, pair_labels, read_zone


def box(x, y, w=40, h=30):
    return [x, y, x + w, y + h]


def two_rows(n_per_row=6, x0=50, pitch=60, top=100, bottom=220):
    ports = []
    # Given in a scrambled order on purpose: the numbering must not depend on it.
    for i in reversed(range(n_per_row)):
        ports.append({"box": box(x0 + i * pitch, bottom + (i % 2)), "status": "empty"})
        ports.append({"box": box(x0 + i * pitch, top - (i % 2)), "status": "connected"})
    return ports


def test_ports_are_numbered_top_row_first_left_to_right():
    ordered = order_ports(two_rows())
    xs = [p["box"][0] for p in ordered]
    ys = [p["box"][1] for p in ordered]
    assert xs == [50, 110, 170, 230, 290, 350] * 2
    assert all(y < 150 for y in ys[:6]) and all(y > 150 for y in ys[6:])


def test_a_slightly_tilted_row_stays_one_row():
    ports = [{"box": box(50 + i * 60, 100 + i * 3), "status": "empty"} for i in range(8)]
    ordered = order_ports(ports)
    assert [p["box"][0] for p in ordered] == [50 + i * 60 for i in range(8)]


def test_each_label_goes_to_the_nearest_port_one_to_one():
    ports = [{"box": box(50 + i * 60, 100)} for i in range(4)]
    labels = [
        {"box": box(112, 140, 36, 14), "text": "FC-002"},  # just under port 2
        {"box": box(52, 140, 36, 14), "text": "FC-001"},  # just under port 1
        {"box": box(115, 150, 36, 14), "text": "FC-XXX"},  # also near port 2: loses to FC-002
        {"box": box(900, 900, 36, 14), "text": "FAR-AWAY"},  # too far from every port
    ]
    pairing = pair_labels(ports, labels)
    assert pairing[0] == 1 and pairing[1] == 0
    assert 3 not in pairing
    assert pairing.get(2) != 1


def test_number_zone_starts_at_from_and_keeps_to_the_range():
    ports = two_rows()
    labels = [
        {"box": box(52, 135, 36, 12), "text": "FC-013", "conf": 0.91},
        {"box": box(292, 255, 36, 12), "text": "FC-024", "conf": 0.4},
        {"box": box(60, 20, 20, 10), "text": "13", "conf": 0.99},  # faceplate number, not a label
    ]
    out = number_zone(ports, labels, 13, 24)
    assert out["portsFrom"] == "model"
    assert [p["portNo"] for p in out["ports"]] == list(range(13, 25))
    by_text = {lab["text"]: lab for lab in out["labels"]}
    assert "13" not in by_text
    assert by_text["FC-013"]["portNo"] == 13
    assert by_text["FC-024"]["portNo"] == 23  # bottom row, fifth from the left
    assert by_text["FC-024"]["box"] == [292, 255, 36, 12]
    # A port with a label on it has a cable in it, whatever the status model said.
    assert out["ports"][10]["state"] == "connected"
    assert out["ports"][0]["state"] == "connected" and out["ports"][0]["sure"] is True


def test_more_ports_than_the_range_are_counted_not_numbered():
    out = number_zone(two_rows(), [], 1, 6)
    assert [p["portNo"] for p in out["ports"]] == [1, 2, 3, 4, 5, 6]
    assert out["extraPorts"] == 6


def test_with_no_port_found_the_labels_are_numbered_in_reading_order():
    labels = [
        {"box": box(300, 100, 50, 14), "text": "B-2"},
        {"box": box(100, 100, 50, 14), "text": "A-1"},
        {"box": box(100, 200, 50, 14), "text": "C-3"},
    ]
    out = number_zone([], labels, 7, 12)
    assert out["portsFrom"] == "labels"
    assert {lab["text"]: lab["portNo"] for lab in out["labels"]} == {"A-1": 7, "B-2": 8, "C-3": 9}


def test_keep_label():
    assert keep_label("FC-001") and keep_label("AB")
    assert not keep_label("7") and not keep_label("048") and not keep_label("") and not keep_label("x")


def test_read_zone_on_a_synthetic_close_up(tmp_path):
    """A drawn close-up: 2 rows of 4 dark ports, a white label under three of them."""
    img = np.full((400, 600, 3), 180, np.uint8)
    ports = []
    for r, y in enumerate((80, 230)):
        for c in range(4):
            x = 60 + c * 130
            cv2.rectangle(img, (x, y), (x + 60, y + 45), (30, 30, 30), -1)
            ports.append({"box": [x, y, x + 60, y + 45], "status": "connected" if (r + c) % 2 else "empty"})
    texts = {(0, 0): "LBL-A1", (0, 2): "LBL-A3", (1, 3): "LBL-B4"}
    drawn = []
    for (r, c), t in texts.items():
        x, y = 55 + c * 130, (80, 230)[r] + 55
        cv2.rectangle(img, (x, y), (x + 80, y + 24), (255, 255, 255), -1)
        cv2.putText(img, t, (x + 3, y + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
        drawn.append({"box": [x, y, x + 80, y + 24], "text": t, "conf": 0.9})
    path = str(tmp_path / "zone.jpg")
    cv2.imwrite(path, img)

    seen = {}

    def detect(image):
        seen["shape"] = image.shape
        return list(reversed(ports))

    out = read_zone(path, 25, 32, detect=detect, reader=lambda p: drawn)
    assert out["ok"] is True
    assert seen["shape"] == (400, 600, 3)
    assert out["image_size"] == {"w": 600, "h": 400}
    assert {lab["text"]: lab["portNo"] for lab in out["labels"]} == {"LBL-A1": 25, "LBL-A3": 27, "LBL-B4": 32}
    states = {p["portNo"]: p["state"] for p in out["ports"]}
    assert states[25] == "connected"  # the status model said empty, but a label hangs there
    assert states[26] == "connected" and states[29] == "connected"
    assert states[28] == "connected" and states[31] == "connected"
    assert states[30] == "empty"


def test_read_zone_refuses_a_file_that_is_not_an_image(tmp_path):
    p = tmp_path / "not.jpg"
    p.write_bytes(b"hello")
    out = read_zone(str(p), 1, 12, detect=lambda i: [], reader=lambda p: [])
    assert out["ok"] is False


def test_a_label_between_two_ports_is_placed_only_when_clearly_nearer():
    from pipeline.zone_read import is_between

    ports = [{"box": box(50 + i * 60, 100)} for i in range(4)]  # centres x 70, 130, 190, 250
    # Straight under port 2, hanging far down: port 2, however far it hangs.
    assert is_between(ports, {"box": box(112, 160, 36, 14)}, 30) is None
    # Half way between ports 2 and 3: neither.
    assert is_between(ports, {"box": box(142, 140, 36, 14)}, 30) in [(1, 2), (2, 1)]
    # A third of the way: port 2 (20 against 40, under 0.6).
    assert is_between(ports, {"box": box(132, 140, 36, 14)}, 30) is None


def test_number_zone_keeps_a_label_between_two_free_ports_unplaced_with_both_named():
    ports = [{"box": box(50 + i * 60, 100), "status": "connected"} for i in range(4)]
    labels = [
        {"box": box(142, 140, 36, 14), "text": "FC-MID", "conf": 0.9},   # between ports 2 and 3
        {"box": box(52, 140, 36, 14), "text": "FC-001", "conf": 0.9},    # under port 1
    ]
    out = number_zone(ports, labels, 1, 4)
    by = {lab["text"]: lab for lab in out["labels"]}
    assert by["FC-001"]["portNo"] == 1 and "between" not in by["FC-001"]
    assert by["FC-MID"]["portNo"] is None
    assert by["FC-MID"]["between"] == [2, 3]
    # One of the two ports taken by a clear label: the other is its port.
    labels.append({"box": box(172, 140, 36, 14), "text": "FC-003", "conf": 0.9})  # under port 3
    out = number_zone(ports, labels, 1, 4)
    by = {lab["text"]: lab for lab in out["labels"]}
    assert by["FC-003"]["portNo"] == 3
    assert by["FC-MID"]["portNo"] == 2 and "between" not in by["FC-MID"]
