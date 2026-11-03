"""The dust-cap check (pipeline/cap_check.py) on drawn ports.

Every port here is drawn: a dark hole with gold pins, a flat red disc, a flat
grey face, a black rubber cap, an open SFP cage with metal springs, a cable
leaving a port. What is tested is each rule of the check and the shape of
its answer, not a model. status is never changed by the check.
"""

import cv2
import numpy as np

from pipeline.cap_check import (
    CONNECTED_SURE,
    MIN_PORT_WIDTH,
    cable_leaves,
    check_caps,
    group_ports,
    kmeans2,
    port_features,
    port_kind,
    silhouette,
    split_group,
    summarize,
)

FACE = (150, 150, 150)  # the faceplate, a mid grey
GOLD = (40, 190, 230)  # BGR; OpenCV hue about 24
RED = (40, 40, 220)
BLUE = (220, 90, 30)
BLACK = (18, 18, 18)


def plate(w=600, h=160):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = FACE
    return img


def draw_open_rj45(img, x, y, w=50, h=50, pins=True, seed=0):
    """A jack as the port model boxes it: a lighter plastic housing with the
    dark hole inside it; gold pins across the top of the hole when pins is
    True, else a few dull grey ridges so the hole still has edges inside."""
    cv2.rectangle(img, (x, y), (x + w, y + h), (120, 120, 120), -1)
    ix, iy = x + w // 6, y + h // 6
    iw, ih = w - w // 3, h - h // 3
    cv2.rectangle(img, (ix, iy), (ix + iw, iy + ih), (30, 30, 30), -1)
    rng = np.random.default_rng(seed)
    noise = rng.integers(-6, 7, size=(ih, iw, 1), dtype=np.int16)
    block = img[iy : iy + ih, ix : ix + iw].astype(np.int16) + noise
    img[iy : iy + ih, ix : ix + iw] = np.clip(block, 0, 255).astype(np.uint8)
    for k in range(8):
        px = ix + 3 + k * (iw - 6) // 8
        colour = GOLD if pins else (95, 95, 95)
        cv2.line(img, (px, iy + 4), (px, iy + ih // 2), colour, 2)
    return [x, y, x + w, y + h]


def draw_flat(img, x, y, colour, w=50, h=50):
    cv2.rectangle(img, (x, y), (x + w, y + h), colour, -1)
    return [x, y, x + w, y + h]


def draw_open_sfp(img, x, y, w=90, h=50, seed=1):
    """An open cage: dark inside, bright metal springs and rails, no colour."""
    cv2.rectangle(img, (x, y), (x + w, y + h), (40, 40, 40), -1)
    rng = np.random.default_rng(seed)
    for k in range(10):
        px = x + 5 + k * (w - 10) // 10
        cv2.line(img, (px, y + 4), (px, y + h - 4), (200, 200, 200), 1)
    for k in range(3):
        py = y + 8 + k * (h - 16) // 3
        cv2.line(img, (x + 3, py), (x + w - 3, py), (170, 170, 170), 1)
    noise = rng.integers(-8, 9, size=(h, w, 1), dtype=np.int16)
    block = img[y : y + h, x : x + w].astype(np.int16) + noise
    img[y : y + h, x : x + w] = np.clip(block, 0, 255).astype(np.uint8)
    return [x, y, x + w, y + h]


def port(box, status="empty", cls="RJ45", conf=0.5, **extra):
    return {"box": box, "status": status, "class_name": cls, "confidence": conf, **extra}


# ── Rules on one port ─────────────────────────────────────────────────────


def test_gold_pins_mean_an_open_port():
    img = plate()
    p = port(draw_open_rj45(img, 40, 50))
    check_caps(img, [p])
    assert p["cap_state"] == "empty"
    assert p["cap_reason"] == "gold pins found"
    assert p["status"] == "empty", "status is never changed"


def test_a_red_disc_is_a_red_cap():
    img = plate()
    p = port(draw_flat(img, 40, 50, RED))
    check_caps(img, [p])
    assert (p["cap_state"], p["cap_reason"]) == ("capped", "red cap")


def test_a_blue_disc_is_a_blue_cap():
    img = plate()
    p = port(draw_flat(img, 40, 50, BLUE))
    check_caps(img, [p])
    assert (p["cap_state"], p["cap_reason"]) == ("capped", "blue cap")


def test_a_flat_grey_face_on_a_grey_plate_is_capped():
    img = plate()
    p = port(draw_flat(img, 40, 50, (140, 140, 140)))
    check_caps(img, [p])
    assert p["cap_state"] == "capped"
    assert p["cap_reason"] == "flat face, no hole"


def test_a_black_cap_alone_is_uncertain_not_a_guess():
    # Dark like a hole, flat like a cap, no pins: on its own the photo
    # cannot say, and the check says so.
    img = plate()
    p = port(draw_flat(img, 40, 50, BLACK))
    check_caps(img, [p])
    assert p["cap_state"] == "uncertain"
    assert p["cap_reason"] == "too few ports in row 1 to compare"


def test_a_port_under_the_minimum_width_is_too_small():
    img = plate()
    p = port(draw_open_rj45(img, 40, 50, w=MIN_PORT_WIDTH - 1, h=MIN_PORT_WIDTH - 1))
    check_caps(img, [p])
    assert p["cap_state"] == "too_small"
    assert p["cap_reason"] == f"port under {MIN_PORT_WIDTH} px"


def test_a_sure_connected_port_is_not_looked_at():
    img = plate()
    p = port(draw_open_rj45(img, 40, 50), status="connected", conf=CONNECTED_SURE)
    check_caps(img, [p])
    assert (p["cap_state"], p["cap_reason"]) == ("connected", "cable in port")
    assert p["status"] == "connected"


def test_a_port_with_no_box_is_uncertain():
    img = plate()
    p = {"status": "empty", "class_name": "RJ45", "confidence": 0.4}
    check_caps(img, [p])
    assert p["cap_state"] == "uncertain"
    assert check_caps(img, []) == []
    assert check_caps(None, [p]) == [p]


# ── Rule C: the group ─────────────────────────────────────────────────────


def test_a_row_splits_into_open_ports_and_flat_caps():
    img = plate(700)
    ports = []
    for k in range(4):
        ports.append(port(draw_open_rj45(img, 30 + k * 70, 50, pins=False, seed=k)))
    for k in range(2):
        ports.append(port(draw_flat(img, 30 + (4 + k) * 70, 50, (140, 140, 140))))
    check_caps(img, ports)
    states = [p["cap_state"] for p in ports]
    assert states == ["empty"] * 4 + ["capped"] * 2
    assert ports[0]["cap_reason"] == "grouped with open ports in row 1"
    assert ports[5]["cap_reason"] == "grouped with flat ports in row 1"


def test_a_row_of_black_caps_that_all_look_the_same_is_not_split():
    img = plate(700)
    rng = np.random.default_rng(7)
    ports = []
    for k in range(6):
        box = draw_flat(img, 30 + k * 70, 50, BLACK)
        x1, y1, x2, y2 = box
        noise = rng.integers(-3, 4, size=(y2 - y1, x2 - x1, 1), dtype=np.int16)
        block = img[y1:y2, x1:x2].astype(np.int16) + noise
        img[y1:y2, x1:x2] = np.clip(block, 0, 255).astype(np.uint8)
        ports.append(port(box))
    check_caps(img, ports)
    assert {p["cap_state"] for p in ports} == {"uncertain"}
    assert ports[0]["cap_reason"] == "no clear difference in row 1"


def test_split_group_refuses_a_group_that_is_too_small_or_too_alike():
    same = [{"darkness": 0.3, "edge_density": 0.1, "spread": 30.0}] * 4
    assert split_group(same) is None
    assert split_group(same[:3]) is None
    mixed = [{"darkness": 0.3, "edge_density": 0.12, "spread": 30.0}] * 3 + [
        {"darkness": 0.95, "edge_density": 0.0, "spread": 1.0}
    ] * 2
    assert split_group(mixed) == ["empty"] * 3 + ["capped"] * 2


def test_kmeans_and_silhouette_on_two_clear_clusters():
    X = np.array([[0.0, 0.0], [0.1, 0.0], [0.0, 0.1], [5.0, 5.0], [5.1, 5.0], [5.0, 5.1]])
    labels, centres = kmeans2(X)
    assert len(set(labels[:3].tolist())) == 1 and len(set(labels[3:].tolist())) == 1
    assert labels[0] != labels[3]
    assert silhouette(X, labels) > 0.9
    assert silhouette(X, np.zeros(6, dtype=int)) == 0.0


# ── Step 6: a cable leaving a low-confidence "connected" port ─────────────


def test_a_cable_leaving_the_box_keeps_a_low_confidence_port_connected():
    img = plate(600, 300)
    box = draw_flat(img, 40, 50, BLUE)
    x1, y1, x2, y2 = box
    cv2.rectangle(img, (x1 + 15, y2), (x2 - 15, y2 + 120), BLUE, -1)  # the cable hangs down
    p = port(box, status="connected", conf=0.4)
    check_caps(img, [p])
    assert (p["cap_state"], p["cap_reason"]) == ("connected", "cable leaves the port")
    assert p["status"] == "connected"
    assert cable_leaves(img, box) is True


def test_a_flat_face_with_nothing_leaving_is_a_cap_on_a_low_confidence_port():
    img = plate(600, 300)
    p = port(draw_flat(img, 40, 50, BLACK), status="connected", conf=0.4)
    check_caps(img, [p])
    assert p["cap_state"] == "capped"
    assert p["cap_reason"] == "no cable leaves the port, flat face"
    assert p["status"] == "connected"
    assert cable_leaves(img, p["box"]) is False


def test_an_unknown_status_port_with_gold_pins_is_open():
    img = plate()
    p = port(draw_open_rj45(img, 40, 50), status="unknown")
    check_caps(img, [p])
    assert p["cap_state"] == "empty" and p["status"] == "unknown"


# ── Step 7: SFP cages ─────────────────────────────────────────────────────


def test_an_open_cage_is_empty_and_a_flat_plug_is_capped():
    img = plate(600, 160)
    open_cage = port(draw_open_sfp(img, 40, 50), cls="SFP")
    plug = port(draw_flat(img, 200, 50, BLACK, w=90, h=50), cls="SFP")
    check_caps(img, [open_cage, plug])
    assert (open_cage["cap_state"], open_cage["cap_reason"]) == ("empty", "open cage, metal edges inside")
    assert (plug["cap_state"], plug["cap_reason"]) == ("capped", "flat plug face")


# ── Grouping and kinds ────────────────────────────────────────────────────


def test_ports_group_by_row_and_by_kind():
    ports = [
        port([0, 0, 50, 50]),
        port([60, 2, 110, 52]),
        port([0, 70, 50, 120]),
        port([200, 0, 290, 50], cls="SFP"),
        port([300, 1, 380, 45], cls="synthesized"),  # wide box, no type: a cage by shape
    ]
    groups = group_ports(ports)
    assert [(g["row"], g["kind"], g["members"]) for g in groups] == [
        (1, "rj45", [0, 1]),
        (1, "sfp", [3, 4]),
        (2, "rj45", [2]),
    ]
    assert port_kind(port([0, 0, 40, 40], cls="synthesized")) == "rj45"
    assert port_kind(port([0, 0, 40, 40], cls="LC")) == "sfp"


def test_features_tell_a_hole_from_a_face():
    img = plate()
    pins = port_features(img, draw_open_rj45(img, 40, 50))
    hole = port_features(img, draw_open_rj45(img, 120, 50, pins=False))
    face = port_features(img, draw_flat(img, 200, 50, (140, 140, 140)))
    assert pins["gold_ratio"] > 0.03 > face["gold_ratio"]
    assert hole["darkness"] < 0.5 < face["darkness"]
    assert hole["edge_density"] > face["edge_density"]
    assert hole["spread"] > face["spread"]
    assert port_features(img, (700, 300, 800, 400)) is None


def test_summary_counts_states():
    ports = [{"cap_state": "empty"}, {"cap_state": "empty"}, {"cap_state": "capped"}, {}]
    assert summarize(ports) == {"empty": 2, "capped": 1}
