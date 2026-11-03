"""Put the Demo Organization's database copies back to the clean record: one copy per source, no planted difference.

    _cache/venv-engine/bin/python tools/reset_demo_copies.py [--org 1]

Verify pass 2 (finding 7) found 30 copies in the Demo Organization, the active NetBox and CMDB ones planted by a proof
(PP3 moved to U8, SW04 made by Cisco), so its accounts saw fake drift. This removes every copy of the organization (and
the cables kept with them), resets the two local mocks to the record they load (POST /__mock/reset), then copies in once:
the filled workbook modules/sources/ground_truth/vdo1.xlsx (the owner's rack, SP-HYD-RM01-R01-R1), the NetBox mock and
the ServiceNow mock. The organization's confirmed racks and a person's corrections stay. Planted differences belong in
the Test Organization only (tools/proof_guard.py). Needs the mocks on 3303 and 3304 (./sources.sh); stop the engine first
or restart it after, so it reads the new copies.
"""
import argparse
import json
import pathlib
import shutil
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
import source_db as SDB  # noqa: E402

WORKBOOK = ROOT / "server" / "mocks" / "ground_truth" / "vdo1.xlsx"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", default="1", help="the Demo Organization's id in the sign-in database")
    a = ap.parse_args()
    SDB.ORG.set(str(a.org))
    gone = [c["id"] for c in SDB.copies()]
    for cid in gone:
        SDB.delete_copy(cid)
    with SDB._db() as c:                                     # versions count from 1 again
        c.execute("DELETE FROM sqlite_sequence WHERE name='src_copies'")
    for base in (SDB.NETBOX_URL, SDB.SERVICENOW_URL):
        urllib.request.urlopen(urllib.request.Request(base + "/__mock/reset", data=b"{}", method="POST",
                                                      headers={"Content-Type": "application/json"}), timeout=10).read()
    up = SDB.DATA / "sources" / "orgs" / str(a.org)
    if up.is_dir():
        for f in up.glob("workbook_*.xlsx"):
            f.unlink()
    up.mkdir(parents=True, exist_ok=True)
    wb = up / "workbook_00000001_vdo1.xlsx"
    shutil.copy2(WORKBOOK, wb)
    made = [SDB.import_excel(wb), SDB.import_netbox(str(a.org)), SDB.import_cmdb(str(a.org))]
    print(f"removed {len(gone)} copies; now:")
    for m in made:
        print(json.dumps({k: m[k] for k in ("source", "version", "racks", "devices", "ports", "cables") if k in m}))


if __name__ == "__main__":
    main()
