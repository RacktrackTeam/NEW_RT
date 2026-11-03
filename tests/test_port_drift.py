"""Port drift from the Excel copy's PORT_INVENTORY (RT-EOS's rules from RT-EOS's server/lib/sheet_check.js), on vdo1's stored run."""
import json
import pathlib
import shutil
import sys

import openpyxl
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
RUN = ROOT / "samples" / "video" / "run_of_vdo1" / "result.json"
RACK = "SP-HYD-RM01-R01-R1"


@pytest.fixture()
def sdb(tmp_path, monkeypatch):
    import source_db
    monkeypatch.setattr(source_db, "DB", tmp_path / "sources.db")
    return source_db


def plant_ports(src, dst):
    """vdo1's workbook with three planted port facts on the router at U20 (SP-R1-U20-ACT): port 1 expected empty (the video sees a
    cable), port 2 expected occupied (the video sees it empty), port 3 expected capped (empty, no cap read)."""
    shutil.copy(src, dst)
    wb = openpyxl.load_workbook(dst); ws = wb["PORT_INVENTORY"]; head = [c.value for c in ws[1]]
    col = {h: head.index(h) for h in head if h}
    for row in ws.iter_rows(min_row=2):
        if row[col["Device_ID"]].value == "SP-R1-U20-ACT":
            n = row[col["Port_Number"]].value
            if n == 1:
                row[col["Expected_Occupied"]].value = "No"
            if n == 2:
                row[col["Expected_Occupied"]].value = "Yes"
            if n == 3:
                row[col["Expected_Dust_Cap"]].value = "Yes"
    wb.save(dst)
    return dst


def test_rules_on_a_small_case(sdb):
    dev = {"index": 7, "ru_start": 5, "ru_height": 1, "ports": [
        {"n": 1, "type": "sfp", "state": "empty", "cap": "empty"}, {"n": 2, "type": "rj45", "state": "empty", "cap": "empty"},
        {"n": 3, "type": "rj45", "state": "unknown", "cap": None}, {"n": 4, "type": "sfp", "state": "empty", "cap": "empty"}]}
    want = {1: {"port_type": "QSFP28", "expected_occupied": "Yes", "expected_dust_cap": None},
            2: {"port_type": "RJ45", "expected_occupied": None, "expected_dust_cap": None},       # open copper: never a cap fault
            3: {"port_type": "RJ45", "expected_occupied": "Yes", "expected_dust_cap": "Yes"},    # state not read: not judged
            4: {"port_type": "SFP+", "expected_occupied": "No", "expected_dust_cap": "No"}}      # No is never a cap fault
    rows, n = sdb.port_drift("SW", dev, want)
    assert n == 4 and sorted(r["type"] for r in rows) == ["dust_cap", "port_occupied", "port_type"]
    assert all(r["port"] == 1 for r in rows)


@pytest.mark.skipif(not RUN.exists(), reason="vdo1's stored run is not on this machine")
def test_vdo1_port_rows(sdb, tmp_path):
    sdb.import_excel(plant_ports(ROOT / "server" / "mocks" / "ground_truth" / "vdo1.xlsx", tmp_path / "vdo1_ports.xlsx"))
    result = json.loads(RUN.read_text())
    d = sdb.drift(result, sdb.devices("excel", RACK), sdb.ports("excel", RACK))
    by = {(r["type"], r.get("port"), r["device"].split(" / ")[0]): r for r in d["rows"]}
    assert ("port_occupied", 1, "SP-R1-U20-ACT") in by and by[("port_occupied", 1, "SP-R1-U20-ACT")]["what"] == "Extra cable"
    assert by[("port_occupied", 2, "SP-R1-U20-ACT")]["what"] == "Missing cable"
    assert by[("dust_cap", 3, "SP-R1-U20-ACT")]["what"] == "Dust cap missing"
    assert ("port_type", 25, "SP-R1-U17-SW03") in by          # real: the record calls the TL-SG2428P's four SFP cages RJ45
    assert not [r for r in d["rows"] if r["type"] == "kind"]   # the router and firewall read as the result screen shows them
    assert d["ports_checked"] > 300
