"""Tests for fit_advisor. Run: python3 -m pytest test_fit_advisor.py -q"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "engine"))   # project root: engine/occupancy.py and friends
import pytest

import fit_advisor as fa
from fit_advisor import advise, free_space_summary, lookup_device, suggest_placements


def make_rows(pattern, x1=10, x2=210, top=100, h=20):
    """Rows TOP to BOTTOM from a pattern string: 'X' = occupied, '.' = free.

    The first character is the top of the rack, so it gets the highest label;
    the last character is u01 (bottom), as in the real pipeline.
    """
    n = len(pattern)
    rows = []
    for i, ch in enumerate(pattern):
        occ = ch == "X"
        rows.append({
            "unit": f"u{n - i:02d}",
            "box": (x1 + i, top + i * h, x2 - i, top + (i + 1) * h),  # x varies so union is non-trivial
            "status": "occupied" if occ else "unoccupied",
            "p_occupied": 0.95 if occ else 0.05,
        })
    return rows


# ── lookup_device ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("make,model", [
    ("Dell", "PowerEdge R750"),
    ("", "poweredge r750"),
    ("", "R750"),
    ("Dell", "Dell PowerEdge R-750"),
    ("DELL", "  poweredge   R 750 "),
])
def test_exact_lookup_forgives_case_spaces_hyphens_prefixes(make, model):
    r = lookup_device(make, model)
    assert r is not None
    assert (r["make"], r["model"], r["units"]) == ("Dell", "PowerEdge R750", 2)
    assert r["source"] == "catalog:exact"
    assert set(r) == {"make", "model", "units", "category", "source", "notes"}


@pytest.mark.parametrize("make,model,units,category", [
    ("HPE", "ProLiant DL380 Gen10", 2, "server"),
    ("HP", "DL360 Gen11", 1, "server"),
    ("Hewlett Packard Enterprise", "DL580 Gen10", 4, "server"),
    ("Dell", "R940", 3, "server"),
    ("Lenovo", "ThinkSystem SR650 V2", 2, "server"),
    ("Cisco", "Catalyst 9300-48P", 1, "switch"),
    ("Cisco", "C9300-48U", 1, "switch"),          # '48U' is a PoE flavour, not a height
    ("Cisco", "ISR4451-X", 2, "router"),
    ("Cisco", "ASR1006-X", 6, "chassis"),
    ("Cisco", "UCS C240 M6", 2, "server"),
    ("Juniper", "MX480", 8, "chassis"),
    ("Arista", "7280SR3-48YC8", 1, "switch"),
    ("Palo Alto Networks", "PA-5220", 3, "firewall"),
    ("Fortinet", "FortiGate 600F", 1, "firewall"),
    ("Ubiquiti", "UniFi Dream Machine Pro", 1, "router"),
    ("Ubiquiti", "UNVR Pro", 2, "nas"),
    ("MikroTik", "CCR2004-16G-2S+", 1, "router"),
    ("Synology", "RackStation RS1221+", 2, "nas"),
    ("QNAP", "TS-h1886XU-RP", 3, "nas"),
    ("NetApp", "DS460C", 4, "storage"),
    ("APC", "Smart-UPS SMT1500RM2U", 2, "ups"),
    ("Eaton", "9PX3000RT", 2, "ups"),
    ("CyberPower", "OR1500LCDRM1U", 1, "ups"),
    ("APC", "AP7900B", 1, "pdu"),
    ("", "48-port patch panel", 2, "patch-panel"),
    ("Leviton", "patch panel 24 port", 1, "patch-panel"),  # generic items match any make
    ("", "blanking panel 1U", 1, "blanking-panel"),
])
def test_exact_lookup_across_vendors(make, model, units, category):
    r = lookup_device(make, model)
    assert r is not None, (make, model)
    assert r["units"] == units
    assert r["category"] == category
    assert r["source"] == "catalog:exact"


def test_zero_u_vertical_pdu():
    r = lookup_device("APC", "AP8941")
    assert r["units"] == 0 and r["category"] == "pdu"


@pytest.mark.parametrize("make,model,expected_model,units", [
    ("Dell", "PowerEgde R750", "PowerEdge R750", 2),     # typo in the family name
    ("Cisco", "C9300-48P-E", "C9300-48P", 1),           # license suffix
    ("QNAP", "TS-873AU-RP-4G", "TS-873AU", 2),          # RAM suffix
])
def test_fuzzy_lookup(make, model, expected_model, units):
    r = lookup_device(make, model)
    assert r is not None
    assert r["model"] == expected_model and r["units"] == units
    assert r["source"].startswith("catalog:fuzzy(")
    score = float(r["source"][len("catalog:fuzzy("):-1])
    assert fa.FUZZY_CUTOFF <= score < 1.0


@pytest.mark.parametrize("make,model", [
    ("Dell", "R999"),          # no such model
    ("Cisco", "C9301-48P"),    # close in spelling, but the numbers differ
    ("", "R7600"),
    ("HPE", "DL388 Gen10"),
    ("Dell", "C9300-48P"),     # recognised make must match the product's make
])
def test_fuzzy_is_conservative(make, model):
    assert lookup_device(make, model) is None


@pytest.mark.parametrize("model,units", [
    ("Widget 2U", 2), ("Mystery box 1RU", 1), ("Thing 4 U", 4), ("Smart-UPS 1500 RM 2U", 2),
    ("5PX1500IRT1UG2", 1),     # SKU suffix encodes the height
])
def test_parsed_from_name(model, units):
    r = lookup_device("Acme", model)
    assert r is not None and r["units"] == units
    assert r["source"] == "parsed-from-name"


def test_unknown_device():
    assert lookup_device("Foo", "Bar 9000") is None
    assert lookup_device("", "") is None


def test_catalog_is_sane():
    assert len(fa.CATALOG) >= 120
    for e in fa.CATALOG:
        assert isinstance(e["units"], int) and 0 <= e["units"] <= 24, e
        assert e["category"] in fa.CATEGORIES, e
    # no normalised name maps to two products with different heights
    for index in (fa._EXACT, fa._UNORDERED):
        for key, ids in index.items():
            assert len({fa.CATALOG[i]["units"] for i in ids}) == 1, key
    makes = {e["make"] for e in fa.CATALOG}
    for v in ["Dell", "HPE", "Lenovo", "Supermicro", "Cisco", "Juniper", "Arista", "Fortinet",
              "Palo Alto", "Ubiquiti", "MikroTik", "Netgear", "Synology", "QNAP", "NetApp", "APC",
              "Eaton", "CyberPower"]:
        assert v in makes


def test_lookup_makes_no_network_calls(monkeypatch):
    import socket

    def boom(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    assert lookup_device("Dell", "R650")["units"] == 1


# ── free runs / summary ────────────────────────────────────────────────────
def test_free_runs_including_top_and_bottom():
    #            u10 ............................... u01
    rows = make_rows("..XX...X..")
    s = free_space_summary(rows)
    assert s["total_units"] == 10
    assert s["occupied_units"] == 3 and s["free_units"] == 7
    assert s["free_pct"] == 70.0
    assert s["largest_contiguous_free"] == 3
    runs = s["free_runs"]
    assert [r["units"] for r in runs] == [["u10", "u09"], ["u06", "u05", "u04"], ["u02", "u01"]]
    assert [r["range_label"] for r in runs] == ["U09-U10", "U04-U06", "U01-U02"]
    assert [r["size"] for r in runs] == [2, 3, 2]


def test_whole_rack_free_and_whole_rack_full():
    s = free_space_summary(make_rows("....."))
    assert s["largest_contiguous_free"] == 5 and s["free_runs"][0]["range_label"] == "U01-U05"
    s = free_space_summary(make_rows("XXX"))
    assert s["free_units"] == 0 and s["free_runs"] == [] and s["largest_contiguous_free"] == 0
    assert free_space_summary([])["free_pct"] == 0.0


def test_missing_row_breaks_contiguity():
    # Detector skipped u08: u09 and u07 are adjacent in the list but not in the rack.
    rows = make_rows("....")
    rows = [dict(r, unit=u) for r, u in zip(rows, ["u10", "u09", "u07", "u06"])]
    runs = free_space_summary(rows)["free_runs"]
    assert [r["units"] for r in runs] == [["u10", "u09"], ["u07", "u06"]]
    assert suggest_placements(rows, 3) == []


# ── suggest_placements ─────────────────────────────────────────────────────
def test_run_at_very_top_and_very_bottom_are_found():
    rows = make_rows("..XXXXXX..")
    got = {s["range_label"] for s in suggest_placements(rows, 2, prefer="any")}
    assert got == {"U09-U10", "U01-U02"}


def test_label_order_and_range_label():
    rows = make_rows("XXXXX..XXX")      # free rows are u05, u04
    (s,) = suggest_placements(rows, 2)
    assert s["units"] == ["u05", "u04"]  # top to bottom, like the rows
    assert s["range_label"] == "U04-U05"  # low to high, rack convention


def test_single_unit_label():
    (s,) = suggest_placements(make_rows("XX.X"), 1)
    assert s["units"] == ["u02"] and s["range_label"] == "U02"


def test_bbox_is_union_of_rows():
    rows = make_rows("X...X")
    s = [s for s in suggest_placements(rows, 3) if s["range_label"] == "U02-U04"][0]
    boxes = [r["box"] for r in rows[1:4]]
    assert s["bbox"] == (min(b[0] for b in boxes), min(b[1] for b in boxes),
                         max(b[2] for b in boxes), max(b[3] for b in boxes))
    assert s["bbox"] == (11, 120, 209, 180)


def test_bottom_vs_top_preference():
    rows = make_rows("..XXXXXX..")
    assert suggest_placements(rows, 2, prefer="bottom")[0]["range_label"] == "U01-U02"
    assert suggest_placements(rows, 2, prefer="top")[0]["range_label"] == "U09-U10"


def test_avoids_one_u_orphan_and_takes_the_edge_of_a_gap():
    rows = make_rows("XXXXXX....")      # 4U gap at the bottom
    labels = [s["range_label"] for s in suggest_placements(rows, 2, prefer="bottom")]
    assert labels[0] == "U01-U02"
    # U02-U03 strands 1U on both sides; it must rank last
    assert labels[-1] == "U02-U03"


def test_prefers_exact_size_gap_over_splitting_a_larger_one():
    rows = make_rows(".....X..XX")      # 5U gap (u06-u10) and exact 2U gap (u03-u04)
    best = suggest_placements(rows, 2, prefer="any")[0]
    assert best["range_label"] == "U03-U04"
    assert "exactly" in best["reason"]


def test_at_most_five_suggestions_and_sorted_scores():
    sugg = suggest_placements(make_rows("." * 20), 1)
    assert len(sugg) == 5
    scores = [s["score"] for s in sugg]
    assert scores == sorted(scores, reverse=True)
    assert set(sugg[0]) == {"units", "range_label", "bbox", "score", "reason"}


def test_nothing_fits_returns_empty():
    assert suggest_placements(make_rows("X..X.X"), 3) == []
    assert suggest_placements(make_rows("..."), 4) == []
    assert suggest_placements([], 1) == []


def test_uncertain_free_row_is_penalised_and_flagged():
    rows = make_rows("XX..XX..")
    rows[7]["p_occupied"] = 0.45            # u01 is a close call
    best = suggest_placements(rows, 2, prefer="any")
    assert best[0]["range_label"] == "U05-U06"
    flagged = [s for s in best if s["range_label"] == "U01-U02"][0]
    assert "U01" in flagged["reason"]


def test_bad_units_needed():
    with pytest.raises(ValueError):
        suggest_placements(make_rows(".."), -1)
    with pytest.raises(TypeError):
        suggest_placements(make_rows(".."), 1.5)


# ── advise ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("make,model,expected_label,prefer", [
    ("Dell", "PowerEdge R750", "U01-U02", "bottom"),       # 2U server: low
    ("APC", "SMT1500RM2U", "U01-U02", "bottom"),           # UPS: low
    ("NetApp", "DS224C", "U01-U02", "bottom"),             # storage: low
    ("Cisco", "C9300-48P", "U10", "top"),                  # switch: high
    ("", "24-port patch panel", "U10", "top"),             # patch panel: high
])
def test_advise_places_by_category(make, model, expected_label, prefer):
    rows = make_rows("..XXXXXX..")
    out = advise(rows, make, model)
    assert out["prefer"] == prefer
    assert out["suggestions"][0]["range_label"] == expected_label
    assert expected_label in out["message"]
    assert out["needs_units_input"] is False


def test_advise_unknown_device_asks_for_height():
    out = advise(make_rows("...."), "Foo", "Bar 9000")
    assert out["device"] is None and out["units_needed"] is None
    assert out["needs_units_input"] is True
    assert out["suggestions"] == []
    assert "rack units" in out["message"] and "(U)" in out["message"]


def test_advise_override_for_unknown_device():
    out = advise(make_rows("XX...."), "Foo", "Bar 9000", units_override=3)
    assert out["units_needed"] == 3 and out["needs_units_input"] is False
    assert out["suggestions"][0]["range_label"] == "U01-U03"


def test_advise_not_enough_space():
    out = advise(make_rows("XX.XXX.XXX"), "Dell", "R940")    # needs 3U, 2U free in total
    assert out["suggestions"] == []
    assert "only 2U is free" in out["message"]
    assert "enough" in out["message"]


def test_advise_fragmented_space():
    out = advise(make_rows("..X..X..X."), "Dell", "R940")    # 7U free, largest run 2U
    assert out["free_space"]["free_units"] == 7
    assert out["suggestions"] == []
    assert "fragmented" in out["message"]
    assert "largest contiguous run is 2U" in out["message"]
    assert "U09-U10" in out["message"]


def test_advise_zero_u_pdu():
    out = advise(make_rows("XXXX"), "APC", "AP8941")
    assert out["units_needed"] == 0 and out["suggestions"] == []
    assert "0U" in out["message"]


def test_advise_fuzzy_hit_asks_to_confirm():
    out = advise(make_rows("...."), "Cisco", "C9300-48P-E")
    assert out["units_needed"] == 1 and "confirm" in out["message"]


def test_placement_preference_rules():
    pp = fa.placement_preference
    assert pp("ups", 2) == "bottom"
    assert pp("server", 2) == "bottom"
    assert pp("server", 1) == "middle"
    assert pp("switch", 1) == "top"
    assert pp("patch-panel", 2) == "top"
    assert pp("chassis", 13) == "bottom"
    assert pp(None, 3) == "bottom"
    assert pp("blanking-panel", 1) == "any"
