"""source_db: the workbook reader, the rack match and the drift words (no model, no server)."""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture()
def sdb(tmp_path, monkeypatch):
    import source_db
    monkeypatch.setattr(source_db, "DB", tmp_path / "sources.db")
    return source_db


def test_workbook_match_and_no_drift_on_the_office_rack(sdb):
    r = sdb.import_excel(ROOT / "modules" / "sources" / "ground_truth" / "vdo1.xlsx")
    assert r["devices"] == 18
    assert sdb.match("excel", "SP-HYD-RM01-R01-R1")["how"] == "exact"
    assert sdb.match("excel", "sp hyd rm01 r01 r1")["how"] == "close"
    assert sdb.match("excel", "SOME OTHER RACK") is None


def test_drift_rows(sdb):
    result = {"ladder": {"units": 20}, "occupancy_frames": {"rows": [{"unit": f"u{n:02d}"} for n in range(1, 21)]}, "devices": [
        {"index": 1, "units": ["u10"], "type": "Switch", "layout": {"vendor": "D-Link", "model": "DGS-1210-52"}},
        {"index": 2, "units": ["u06", "u05"], "type": "Patch Panel", "layout": {}},
        {"index": 3, "units": ["u15"], "type": "Router", "layout": {}},
        {"index": 4, "units": ["u18"], "classification": {"label": "Other"}, "layout": {}}]}
    record = [
        {"name": "SW", "kind": "Switch", "manufacturer": "TP-Link", "model": "DGS-1210-52", "ru_start": 10, "ru_height": 1},
        {"name": "PP", "kind": "Patch Panel", "ru_start": 6, "ru_height": 2},
        {"name": "FW", "kind": "Firewall", "ru_start": 15, "ru_height": 1},
        {"name": "PDU", "kind": "PDU", "ru_start": 24, "ru_height": 1},
        {"name": "CM", "kind": "Cable Manager", "ru_start": 3, "ru_height": 1}]
    d = sdb.drift(result, record)
    kinds = sorted(x["type"] for x in d["rows"])
    assert kinds == ["extra", "kind", "make", "moved"]                 # the PDU at U24 sits above the 20 units the video read: not judged
    assert any("PDU" in n and "U1 to U20 only" in n for n in d["notes"])
    assert any("CM" in n for n in d["notes"])
    json.dumps(d)


def test_drift_uses_the_kind_the_result_screen_shows(sdb):
    """The make and model step's kind (layout.kind) wins over the classifier, as on the result screen; a person's correction wins over both."""
    result = {"devices": [
        {"index": 1, "units": ["u20"], "classification": {"label": "Switch"}, "layout": {"ok": True, "kind": "router", "status": "not_mapped", "model_read": "ACX-09CW2A"}},
        {"index": 2, "units": ["u19"], "classification": {"label": "Other"}, "layout": {"ok": True, "kind": "firewall", "status": "mapped_auto"}},
        {"index": 3, "units": ["u18"], "classification": {"label": "Switch"}, "layout": {"ok": True, "kind": "router", "status": "not_mapped"}},
        {"index": 4, "units": ["u17"], "kind_override": {"kind": "Server"}, "layout": {"ok": True, "kind": "switch", "fitted": True}}]}
    kinds = {d["index"]: d["kind"] for d in sdb.run_devices(result)}
    assert kinds == {1: "Router", 2: "Firewall", 3: "Switch", 4: "Server"}
    record = [{"name": "R", "kind": "Router", "ru_start": 20, "ru_height": 1}, {"name": "F", "kind": "Firewall", "ru_start": 19, "ru_height": 1}]
    assert not [r for r in sdb.drift(result, record)["rows"] if r["type"] == "kind"]


def _three_racks(sdb):
    """The three ground truth racks as one copy, with their sites' test positions (as the NetBox mock serves them)."""
    racks, devices = [], []
    for f in ("vdo1", "whatsapp_2143", "whatsapp_2319"):
        g = json.loads((ROOT / "modules" / "sources" / "ground_truth" / f"{f}.json").read_text())
        racks.append({"rack_key": g["rack"]["label"], "name": g["rack"]["label"], "label": g["rack"]["label"], "height_ru": 42,
                      "site": g["site"]["name"], "lat": g["site"].get("lat"), "lng": g["site"].get("lng")})
        devices += [{"rack_key": g["rack"]["label"], "device_key": d["name"], "name": d["name"], "kind": d["kind"], "manufacturer": d.get("manufacturer"),
                     "model": d.get("model"), "ru_start": d.get("position"), "ru_height": d.get("height")} for d in g["devices"]]
    sdb._store("netbox", racks, devices, "test")


def test_identify_ladder_on_the_office_run(sdb):
    _three_racks(sdb)
    run = ROOT / "data" / "runs" / "91af02c09bbc"
    result, label = json.loads((run / "result.json").read_text()), json.loads((run / "rack_label.json").read_text())
    out = sdb.identify("netbox", "SP-HYD-RM01-R01-R1", result, None, label)
    assert [s["outcome"] for s in out["steps"]] == ["skipped", "one", "one", "narrowed", "skipped", "one"]
    assert out["rack_key"] == "SP-HYD-RM01-R01-R1" and out["named_by"] == 2 and out["state"] == "verified"
    assert "Step 6 agrees, 10 of 10." in out["words"]
    # a phone position at the office leaves one rack at step 1; the label then agrees
    near = sdb.identify("netbox", "SP-HYD-RM01-R01-R1", result, {"lat": 17.4487, "lng": 78.3909}, label)
    assert near["named_by"] == 1 and near["steps"][0]["proof"].startswith("1 rack within 300 m")
    # no label read: the device labels (models read on faceplates) name it, and the layout agrees
    blind = sdb.identify("netbox", None, result, None, None)
    assert blind["named_by"] == 3 and blind["rack_key"] == "SP-HYD-RM01-R01-R1"
    # a wrong label that names another rack: the later steps disagree and the person is asked
    wrong = sdb.identify("netbox", "NETWORK RACK 2", result, None, None)
    assert wrong["named_by"] == 2 and wrong["state"] == "confirm" and "points to SP-HYD-RM01-R01-R1" in wrong["words"]
    # nothing named: the WhatsApp run of NETWORK RACK 1 with no label and units off by one gets the top matches, or a question
    r2 = json.loads((ROOT / "data" / "runs" / "28c3c52cd91e" / "result.json").read_text())
    none = sdb.identify("netbox", None, r2, None, None)
    assert none["rack_key"] is None and none["state"] in ("pick", "ask")
    assert sdb._rack_number_in_label("SP-R1-U17-SW03") == 1 and sdb._rack_number_in_label("SP-HYD-RM01-R01-R2") is None
    assert sdb._rack_number_of("SP-HYD-RM01-R01-R1") == 1


def test_confirmations_and_corrections_are_kept(sdb):
    _three_racks(sdb)
    run = ROOT / "data" / "runs" / "91af02c09bbc"
    result = json.loads((run / "result.json").read_text())
    sdb.confirm_rack("default", "SP-HYD-RM01-R01-R1", "netbox", "SP-HYD-RM01-R01-R1", "Aasritha")
    out = sdb.identify("netbox", "SP-HYD-RM01-R01-R1", result, None, None, None, sdb.confirmed("default", "SP-HYD-RM01-R01-R1", "netbox"))
    assert out["named_by"] == 0 and out["confidence"] == 1.0 and out["steps"][0]["proof"].startswith("Confirmed by Aasritha on ")
    sdb.save_correction("default", "SP-HYD-RM01-R01-R1", 13, "Aasritha", kind="Router", vendor="Cisco", model="C1111")
    fixed = sdb.with_corrections("default", "SP-HYD-RM01-R01-R1", result)
    d6 = next(d for d in fixed["devices"] if d["index"] == 6)
    assert sdb.device_kind(d6)["kind"] == "Router" and "an earlier scan" in sdb.device_kind(d6)["words"]
    assert next(d for d in result["devices"] if d["index"] == 6).get("kind_override") is None       # the stored run is not changed
