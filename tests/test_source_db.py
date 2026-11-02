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
    result = {"rack": {"rows": [{"unit": f"u{n:02d}"} for n in range(1, 21)]}, "devices": [
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
    assert kinds == ["extra", "kind", "make", "missing", "moved"]
    assert any("U1 to U20 only" in x["words"] for x in d["rows"])
    assert any("CM" in n for n in d["notes"])
    json.dumps(d)
