"""Fill a copy of the manager's source-of-truth template with rows.

    from pipeline.workbook_write import write_workbook
    write_workbook(out_path, {"A_Z_CONNECTION_MATRIX": [{"Connection_ID": "CONN-1", ...}, ...]})

The template (reference/RackTrack_A_Z_Source_of_Truth_Upload_Matrix.xlsx)
is copied, so its README, FIELD_DICTIONARY, LOOKUPS, column widths and
drop-down lists stay as the manager made them. For each sheet named, the
template's own data rows are cleared and the given rows written under the
header, each row a dict keyed by the template's column names. An empty dict
leaves a blank row. A column name the sheet does not have is an error, so a
typo never drops a value silently.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "reference" / "RackTrack_A_Z_Source_of_Truth_Upload_Matrix.xlsx"


def write_workbook(out_path, sheets: dict, template=TEMPLATE) -> Path:
    wb = load_workbook(str(template))
    for name, rows in sheets.items():
        if name not in wb.sheetnames:
            raise KeyError(f"The template has no sheet named {name}")
        ws = wb[name]
        headers = [c.value for c in ws[1]]
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
            for cell in row:
                cell.value = None
        for i, r in enumerate(rows, start=2):
            for k, v in r.items():
                if k not in headers:
                    raise KeyError(f"{name} has no column named {k}")
                ws.cell(row=i, column=headers.index(k) + 1, value=v)
    out = Path(out_path)
    wb.save(str(out))
    return out
