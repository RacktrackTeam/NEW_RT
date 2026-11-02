"""pipeline.matrix_read on the two fixtures the server tests use.

Runs under pytest, or on its own:
    .venv/bin/python -m unittest pipeline.tests.test_matrix_read
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pipeline import matrix_read

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "server" / "test" / "fixtures"


class MatrixReadTest(unittest.TestCase):
    def test_csv_header_rows_and_counts(self):
        out = matrix_read.read(FIX / "matrix-sample.csv")
        self.assertIsNone(out["error"])
        self.assertEqual(len(out["sheets"]), 1)
        sh = out["sheets"][0]
        self.assertEqual(sh["header_row"], 0)
        self.assertEqual(sh["headers"][:5], ["Cable Label", "A-End Device", "A Port", "B-End Device", "B Port"])
        # Nine rows after the header: eight cables and one blank line.
        self.assertEqual(sh["row_count"], 9)
        self.assertEqual(sh["rows"][3][2], "", "the missing port stays an empty cell")
        self.assertEqual(sh["rows"][4], [""] * len(sh["headers"]), "a blank row is kept, so row numbers hold")

    def test_csv_max_rows(self):
        sh = matrix_read.read(FIX / "matrix-sample.csv", max_rows=2)["sheets"][0]
        self.assertEqual(len(sh["rows"]), 2)
        self.assertEqual(sh["row_count"], 9)

    def test_xlsx_finds_the_header_under_a_title(self):
        out = matrix_read.read(FIX / "matrix-sample.xlsx")
        self.assertIsNone(out["error"])
        names = [s["name"] for s in out["sheets"]]
        self.assertEqual(names, ["Cover", "Patching"])
        sh = out["sheets"][1]
        self.assertEqual(sh["header_row"], 1)
        self.assertEqual(sh["headers"][0], "Fibre ID")
        self.assertEqual(sh["row_count"], 5)
        first = sh["rows"][0]
        self.assertEqual(first[2], "1", "a whole number has no .0")
        self.assertEqual(first[6], "2026-09-28", "a date is an ISO date")
        self.assertEqual(sh["rows"][4][4], "1")

    def test_explicit_header_row_and_sheet(self):
        out = matrix_read.read(FIX / "matrix-sample.xlsx", header_row=0, sheet="Patching")
        sh = out["sheets"][0]
        self.assertEqual(sh["header_row"], 0)
        self.assertEqual(sh["headers"][0], "Patching schedule, hall 1")
        self.assertEqual(sh["row_count"], 6)

    def test_xls_is_refused_plainly(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "old.xls"
            p.write_bytes(b"\xd0\xcf\x11\xe0")
            self.assertEqual(matrix_read.read(p)["error"], "Save the sheet as .xlsx first")

    def test_cli_prints_one_json_object(self):
        r = subprocess.run(
            [sys.executable, "-m", "pipeline.matrix_read", str(FIX / "matrix-sample.csv"), "--max-rows", "1"],
            cwd=str(ROOT), capture_output=True, text=True, check=True,
        )
        out = json.loads(r.stdout)
        self.assertEqual(len(out["sheets"][0]["rows"]), 1)


if __name__ == "__main__":
    unittest.main()
