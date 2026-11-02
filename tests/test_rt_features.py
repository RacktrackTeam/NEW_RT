"""wt/features: the RT-EOS functions on the stored vdo1 run (src/rt_features.py), with no model loaded."""
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
RACK = "SP-HYD-RM01-R01-R1"


def _vdo1():
    for f in [ROOT / "samples" / "video" / "run_of_vdo1" / "result.json", *sorted((ROOT / "data" / "runs").glob("**/91af02c09bbc/result.json"))]:
        if f.exists():
            r = json.loads(f.read_text())
            for d in r.get("devices") or []:          # the stored run as the video read it: no close-up of this proof
                for k in ("ids", "serial", "asset_tag", "label_read", "closeup"):
                    d.pop(k, None)
            return r
    pytest.skip("the stored vdo1 run is not in this folder")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import source_db
    import rt_features
    monkeypatch.setattr(source_db, "DB", tmp_path / "sources.db")
    wb = tmp_path / "vdo1_planted.xlsx"
    subprocess.run([sys.executable, str(ROOT / "tools" / "plant_features_workbook.py"), str(wb)], check=True, capture_output=True)
    out = source_db.import_excel(wb)
    assert out["cables"] == 19
    return source_db, rt_features, _vdo1()


def test_serial_and_asset_tag_shapes():
    import rt_features as RTF
    x = RTF.ids_from_text("TP-Link JetStream TL-SG2428P S/N: TPL2231X9ZZ Asset Tag: SP-AT-00415 SP-R1-U15-SW02 CBL-PLANT-L1-A")
    assert (x["serial"], x["asset_tag"], x["labels"], x["cables"]) == ("TPL2231X9ZZ", "SP-AT-00415", ["SP-R1-U15-SW02"], ["CBL-PLANT-L1-A"])
    assert RTF.ids_from_text("FOC2231X0AB")["serial"] == "FOC2231X0AB"                  # the serial shape alone
    assert RTF.ids_from_text("SN:FOC22310")["serial"] == "FOC22310"                     # glued to its word
    assert RTF.ids_from_text("SP-R1-U17-SW03")["asset_tag"] == "SP-R1-U17-SW03"         # the asset-tag shape
    assert RTF.ids_from_text("Ptp-link")["serial"] is None


def test_location_judge():
    import rt_features as RTF
    site, other = {"id": 2, "name": "Hyderabad DC", "lat": 17.4486, "lng": 78.3908}, {"id": 3, "name": "Pune DC", "lat": 18.5204, "lng": 73.8567}
    assert RTF.judge({"lat": 17.4489, "lng": 78.3911, "accuracy": 35}, site, [other])["verdict"] == "here"
    assert RTF.judge({"lat": 18.5205, "lng": 73.8568}, site, [other])["verdict"] == "elsewhere"
    assert RTF.judge({"lat": 12.97, "lng": 77.59}, site, [other])["verdict"] == "away"
    assert RTF.judge({"lat": 17.4489, "lng": 78.3911, "accuracy": 5000}, site, [other])["verdict"] == "unknown"
    assert RTF.judge({"lat": 17.4520, "lng": 78.3908, "accuracy": 400}, site)["verdict"] == "here"      # 380 m away, inside twice the accuracy


def test_location_step_uses_the_site_position(env):
    SDB, RTF, r = env
    sites = ({"id": 2, "name": "Hyderabad DC", "lat": 17.4486, "lng": 78.3908}, [])
    near = SDB.identify("excel", RACK, r, {"lat": 17.4489, "lng": 78.3911, "accuracy": 35}, sites=sites)["steps"][0]
    far = SDB.identify("excel", RACK, r, {"lat": 12.97, "lng": 77.59}, sites=sites)["steps"][0]
    rough = SDB.identify("excel", RACK, r, {"lat": 17.4489, "lng": 78.3911, "accuracy": 3000}, sites=sites)["steps"][0]
    assert (near["n"], near["outcome"]) == (1, "one") and "Taken at Hyderabad DC" in near["proof"]
    assert far["outcome"] == "none" and rough["outcome"] == "skipped"


def test_cable_check_on_the_planted_rows(env):
    SDB, RTF, r = env
    rec, cab = SDB.devices("excel", RACK), RTF.cables_for([RACK])
    got = {(x["type"], x["device"].split(" / ")[0], x["port"]) for x in RTF.cable_check(r, rec, cab, RACK)["rows"]}
    assert got == {("missing_cable", "SP-R1-U20-ACT", 2), ("extra_cable", "SP-R1-U17-SW03", 23), ("wrong_port", "SP-R1-U18-SW04", 21)}


def test_close_up_ids_reach_the_drift_the_identification_and_the_az_check(env):
    SDB, RTF, r = env
    d5, d4 = (next(d for d in r["devices"] if d["index"] == i) for i in (5, 4))
    RTF.keep_ids(d5, RTF.ids_from_text("S/N: TPL2231X9ZZ Asset Tag: SP-AT-00415 SP-R1-U15-SW02 CBL-PLANT-L1-A"), "closeups/D5/p.jpg", "admin")
    RTF.keep_ids(d4, RTF.ids_from_text("S/N: SGP2231A0BC"), "closeups/D4/p.jpg", "admin")
    rec, cab = SDB.devices("excel", RACK), RTF.cables_for([RACK])
    kinds = {x["type"] for x in SDB.drift(r, rec)["rows"]}
    assert {"serial", "asset_tag"} <= kinds
    st3 = next(s for s in SDB.identify("excel", None, r)["steps"] if s["n"] == 3)
    assert st3["proof"].startswith("3 serial") and st3["outcome"] == "one"
    st5 = next(s for s in SDB.identify("excel", None, r)["steps"] if s["n"] == 5)
    assert "SP-R1-U15-SW02" in st5["proof"]
    rows = RTF.cable_check(r, rec, cab, RACK)["rows"]
    assert any(x["type"] == "wrong_port" and x["cable"] == "CBL-PLANT-L1" for x in rows)
    v = dict(RTF.run_view(r, rec, RACK), scanId="run1")
    az = RTF.az_check(v, v, cab, "admin")                                  # the same run at both ends
    assert az["counts"]["wrongPort"] == 1 and az["counts"]["missingCable"] == 2
    csv = RTF.az_csv(az).splitlines()
    assert csv[0].startswith("Cable label,Connection ID") and any("CBL-PLANT-L1" in x and "wrong_port" in x for x in csv)


def test_find_port_connections_worklist_physical_sfp(env):
    SDB, RTF, r = env
    rec, cab = SDB.devices("excel", RACK), RTF.cables_for([RACK])
    fp = RTF.find_port(r, rec, cab, RACK, 4, 9)
    assert fp["found"] and fp["read"]["state"] == "connected" and fp["cable"]["cable"] == "CBL-PLANT-L1" and fp["box"]
    conn = RTF.connections(r, rec, SDB.drift(r, rec, SDB.ports("excel", RACK))["rows"], RTF.cable_check(r, rec, cab, RACK)["rows"])
    sw03 = next(c for c in conn if c["name"] == "SP-R1-U17-SW03")
    assert {p["what"] for p in sw03["problems"]} >= {"Extra cable", "Wrong port type"} and sw03["ports"]
    wl = RTF.worklist(r)
    assert wl and all(i["confidence"] < 0.65 for i in wl) and sum(i["index"] == 6 for i in wl) <= 8
    port = next(i for i in wl if i["kind"] == "port")
    assert RTF.set_port_state(r, port["index"], port["port"], "empty", "admin")
    assert port["id"] not in {i["id"] for i in RTF.worklist(r)}
    kept = RTF.kept_before_read_again(r)
    assert RTF.put_back(json.loads(json.dumps(r)), kept, r["port_answers"])
    doc = RTF.physical_layer(r, RACK, None, rec)
    assert doc["schema"] == "racktrack-physical-layer/1" and doc["counts"]["ports"] == sum(len(d["ports"]) for u in doc["units"] for d in u["devices"])
    sfp = RTF.sfp_advice(r, rec, cab)["items"]
    assert {x["device"] for x in sfp} >= {"SP-R1-U17-SW03", "SP-R1-U15-SW02"} and all(x["port_type"] == "SFP" for x in sfp)


def test_kind_words_say_the_true_source():
    import source_db as SDB
    r = _vdo1()
    d1, d2, d3 = r["devices"][:3]
    assert SDB.device_kind(d1, "2026-10-27T12:54:50")["words"] == "Router, as read when its ports were mapped on 27 Oct 2026; Switch from the picture (83 %)"
    assert SDB.device_kind(d2, "2026-10-27T12:54:50")["words"].startswith("Firewall, as read when its ports were mapped on 27 Oct 2026; the picture was not sure")
    assert SDB.device_kind(d3)["words"] == "Switch, from the faceplate"
    assert SDB.device_kind(dict(d1, kind_override={"kind": "Router", "by": "admin"}))["words"].startswith("Router, your correction (admin); Switch from the picture")


def test_find_port_places_a_port_on_the_crop_when_the_layout_was_not_fitted():
    import rt_features as RTF
    fs = sorted((ROOT / "data" / "runs").glob("**/fx1hyd000001/result.json"))
    if not fs:
        pytest.skip("the stored run fx1hyd000001 is not in this folder")
    r = json.loads(fs[0].read_text())
    d1 = RTF.find_port(r, [], [], None, 1, 3)
    assert d1["found"] and d1["placed"] == "crop" and d1["crop_box"] and d1["crop_file"].endswith("_cut.jpg")
    d4 = RTF.find_port(r, [], [], None, 4, 25)
    assert d4["found"] and d4["placed"] == "crop" and d4["read"]["type"] == "sfp" and len(d4["read"]["rack_polygon"]) == 4 and d4["port_boxes"]["25"]
    assert RTF.find_port(r, [], [], None, 1, 99)["placed"] is None
