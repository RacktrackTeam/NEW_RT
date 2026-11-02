"""The workbook's validation report before import (RT-EOS's checks from rt_eos/server/lib/workbook.js) and the copies list."""
import pathlib
import sys

import openpyxl
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture()
def sdb(tmp_path, monkeypatch):
    import source_db
    monkeypatch.setattr(source_db, "DB", tmp_path / "sources.db")
    return source_db


def book(path, sheets):
    wb = openpyxl.Workbook(); wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    wb.save(path)
    return path


RACKS = [["Rack_ID", "Rack_Name", "Rack_Height_RU"], ["R1", "Rack one", 20]]
DEVS = [["Rack_ID", "Device_ID", "Device_Name", "Device_Role", "RU_Start", "RU_Height"],
        ["R1", "SW1", "SW1", "Switch", 10, 1],
        ["R1", "SW1", "SW1 again", "Switch", 12, 1],          # duplicate id
        ["R9", "FW1", "FW1", "Firewall", 5, 1],                # rack not in RACK_INVENTORY
        ["R1", "SRV1", "SRV1", "Server", 19, 3],               # U19 to U21 in a 20 unit rack
        ["R1", "PP1", "PP1", "Patch Panel", 10, 1]]            # overlaps SW1
PORTS = [["Rack_ID", "Device_ID", "Port_ID", "Port_Number", "Port_Type", "Expected_Occupied", "Expected_Dust_Cap"],
         ["R1", "SW1", "SW1:1", 1, "RJ45", "Yes", "No"], ["R1", "XX9", "XX9:1", 1, "RJ45", "maybe", "No"]]


def test_every_problem_with_counts(sdb, tmp_path):
    r = sdb.validate_excel(book(tmp_path / "w.xlsx", {"RACK_INVENTORY": RACKS, "DEVICE_INVENTORY": DEVS, "PORT_INVENTORY": PORTS}))
    k = r["by_kind"]
    assert k["duplicate id"] == 1 and k["rack not in RACK_INVENTORY"] == 1 and k["outside the rack height"] == 1 and k["devices overlap"] == 1
    assert k["device not in DEVICE_INVENTORY"] == 1 and k["not Yes or No"] == 1 and k["missing sheet"] == 3     # A_Z, LABEL_RULES, LOOKUPS
    assert r["counts"]["errors"] == 3 and r["counts"]["problems"] == len(r["problems"]) and not r["blocking"]
    assert any("SRV1 at U19 to U21 is outside the rack's 20 units." == x["reason"] for x in r["problems"])
    out = sdb.import_excel(tmp_path / "w.xlsx")
    assert out["devices"] == 5 and out["ports"] == 2 and out["version"] == 1


def test_missing_sheet_or_column_blocks_the_import(sdb, tmp_path):
    r = sdb.validate_excel(book(tmp_path / "a.xlsx", {"RACK_INVENTORY": RACKS}))
    assert r["blocking"] and any(x["kind"] == "missing sheet" and x["sheet"] == "DEVICE_INVENTORY" and x["level"] == "error" for x in r["problems"])
    with pytest.raises(ValueError):
        sdb.import_excel(tmp_path / "a.xlsx")
    r = sdb.validate_excel(book(tmp_path / "b.xlsx", {"RACK_INVENTORY": RACKS, "DEVICE_INVENTORY": [[c for c in DEVS[0] if c != "RU_Height"]]}))
    assert r["blocking"] and r["by_kind"]["missing column"] == 1


def test_office_workbook_reads_clean_of_errors(sdb):
    r = sdb.validate_excel(ROOT / "modules" / "sources" / "ground_truth" / "vdo1.xlsx")
    assert r["counts"]["errors"] == 0 and r["counts"]["devices"] == 18 and r["counts"]["ports"] == 346 and not r["blocking"]


def test_copies_versions_active_and_delete(sdb, tmp_path):
    w = book(tmp_path / "w.xlsx", {"RACK_INVENTORY": RACKS, "DEVICE_INVENTORY": DEVS[:2]})
    first = sdb.import_excel(w)
    second = sdb.import_excel(book(tmp_path / "w2.xlsx", {"RACK_INVENTORY": RACKS, "DEVICE_INVENTORY": DEVS[:3]}))
    cs = sdb.copies()
    assert [c["version"] for c in cs] == [2, 1] and [c["active"] for c in cs] == [True, False]
    assert len(sdb.devices("excel", "R1")) == 1                         # SW1 twice in copy 2: one row per id
    assert sdb.racks("excel")[0]["copy_id"] == second["copy_id"]
    assert sdb.delete_copy(second["copy_id"]) and sdb.copies()[0]["active"]   # the copy before it is active again
    assert sdb.racks("excel")[0]["copy_id"] == first["copy_id"]
    assert not sdb.delete_copy(999)
