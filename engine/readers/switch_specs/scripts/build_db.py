"""
Build the switch specifications database from seed_data.json.

This DESTROYS any existing data/switches.db (and the scraped models it
contains).  Run this once to initialize the DB; afterwards prefer
`run_scrapers.py` to add data incrementally.

To rebuild on purpose, pass --force.
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "switches.db"
SCHEMA_PATH = ROOT / "schema.sql"
SEED_PATH = ROOT / "seed_data.json"


def build(force: bool = False):
    DB_PATH.parent.mkdir(exist_ok=True)
    if DB_PATH.exists():
        if not force:
            print(
                f"Refusing to overwrite existing {DB_PATH}.\n"
                "Pass --force to wipe and rebuild from seed (this destroys\n"
                "all scraped switches).",
                file=sys.stderr,
            )
            sys.exit(2)
        DB_PATH.unlink()

    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA_PATH.read_text())

    seed = json.loads(SEED_PATH.read_text())
    now = datetime.now(timezone.utc).isoformat()
    rows = 0
    for rec in seed:
        rec = {**rec}
        # features list -> JSON string
        if isinstance(rec.get("features"), list):
            rec["features"] = json.dumps(rec["features"])
        rec["last_updated"] = now
        keys = ",".join(rec.keys())
        qs = ",".join("?" * len(rec))
        con.execute(f"INSERT INTO switches ({keys}) VALUES ({qs})", list(rec.values()))
        rows += 1
    con.commit()

    # Quick stats
    by_vendor = con.execute(
        "SELECT vendor, COUNT(*) FROM switches GROUP BY vendor ORDER BY vendor"
    ).fetchall()
    con.close()

    print(f"Built {DB_PATH}")
    print(f"Inserted {rows} switches across {len(by_vendor)} vendors:")
    for v, c in by_vendor:
        print(f"  {v:20s} {c}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true",
                    help="Wipe existing data/switches.db and rebuild from seed.")
    build(force=ap.parse_args().force)
