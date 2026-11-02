"""One reading of the ports and units for every screen (verify pass 1 findings 5, 6, 7, 8, 11), on the stored vdo1 run, no model loaded."""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture()
def run():
    f = ROOT / "samples" / "video" / "run_of_vdo1" / "result.json"
    if not f.exists():
        pytest.skip("the stored vdo1 run is not in this folder")
    return json.loads(f.read_text())


def test_tools_read_the_ports_the_result_screen_shows(run):
    import rt_features as RTF
    import source_db as SDB
    ub = SDB.unit_boxes(run)
    for d in run["devices"]:
        screen = SDB.screen_ports(d, ub)["ports"]
        tools = RTF._detail_ports(d, ub)
        assert [(p["status"]) for p in screen] == [p["state"] for p in tools], f"D{d['index']}"
        drift = {p["n"]: p["state"] for p in SDB.run_ports(d, ub) if not p.get("combo")}
        assert drift == {int(p["id"]): p["status"] for p in screen if str(p["id"]).isdigit()}
    con = {x["index"]: x["counts"] for x in RTF.connections(run, [], [], [])}
    pl = RTF.physical_layer(run, "R", None, [])
    assert sum(c["connected"] for c in con.values()) == pl["counts"]["connected"]
    assert pl["units_count"] == SDB.units_read(run) == run["ladder"]["units"]


def test_combo_ports_are_counted_once():
    import source_db as SDB
    ports = [{"id": i, "type": "rj45", "status": "empty"} for i in range(1, 53)] + [{"id": i, "type": "sfp", "status": "empty"} for i in range(53, 57)]
    d = {"index": 6, "units": ["u13"], "layout": {"fitted": True, "vendor": "D-Link", "model": "DGS-1210-52", "ports": ports}}
    sp = SDB.screen_ports(d)
    assert len(sp["ports"]) == 52 and [c["id"] for c in sp["combo"]] == ["49F", "50F", "51F", "52F"]
    d["layout"]["model"] = "TL-SG2428P"                     # a model not named for its port count keeps every port
    d["layout"]["ports"] = [{"id": i, "type": "rj45"} for i in range(1, 25)] + [{"id": i, "type": "sfp"} for i in range(25, 29)]
    assert len(SDB.screen_ports(d)["ports"]) == 28


def test_device_labels_pair_one_record_device_each(tmp_path, monkeypatch, run):
    import source_db as SDB
    monkeypatch.setattr(SDB, "DB", tmp_path / "sources.db")
    SDB.import_excel(ROOT / "modules" / "sources" / "ground_truth" / "vdo1.xlsx")
    st3 = next(s for s in SDB.identify("excel", None, run)["steps"] if s["n"] == 3)
    names = [w.split(" (")[0] for w in (st3["ranked"][0]["which"] if st3["ranked"] else [])]
    assert len(names) == len(set(names)), names
