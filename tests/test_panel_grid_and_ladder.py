"""Two engine rules from the testers' findings of 4 Nov 2026, pinned without the models: a patch panel's grid never puts a
port past the picture's edge (finding 13 and 15), and the unit ladder adds at most two rows past the model's outermost
box at each end, never down to the floor (finding 23)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "engine"))
import patch_panels as PP  # noqa: E402
import unit_grid as ug  # noqa: E402


def _row(n, x0=20, pitch=30, y=50, w=24, h=30):
    return [{"type": "rj45", "status": "connected", "polygon": [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]} for x in (x0 + k * pitch for k in range(n))]


def test_grid_slots_past_the_picture_edge_are_not_ports():
    ports = _row(20)                                            # 20 of a 24-port row seen, the last box ends at x = 614
    full = PP.complete_grid(ports, 24)
    assert len(full) == 24                                      # without the picture's width the grid runs to 24 slots
    clipped = PP.complete_grid(ports, 24, img_w=640)
    assert 20 <= len(clipped) < 24
    assert all(max(q[0] for q in p["polygon"]) <= 640 + 12 for p in clipped)
    assert [p["id"] for p in clipped] == list(range(1, len(clipped) + 1))   # numbered without the dropped slots
    assert sum(p.get("seen", True) for p in clipped) == 20


def test_ladder_adds_at_most_two_rows_at_each_end():
    units = [{"box": [0, 300, 500, 400], "confidence": .9}, {"box": [0, 400, 500, 500], "confidence": .9}, {"box": [0, 500, 500, 600], "confidence": .9}]
    rows = ug.ladder_from_unit_boxes(units, [[0, 0, 500, 1400]], 1400, 500, None)   # the frame carries on 300 px above and 800 px below
    assert len(rows) == 3 + 2 + 2
    assert rows[0]["box"][1] >= 100 and rows[-1]["box"][3] <= 800
