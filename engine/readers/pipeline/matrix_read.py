"""Read a patching matrix (Excel or CSV) into plain rows of text.

    python -m pipeline.matrix_read <file> [--header-row N] [--max-rows N] [--sheet NAME]

Prints one JSON object on stdout:

    { "sheets": [ { "name": str, "header_row": int, "headers": [str],
                    "rows": [[str]], "row_count": int } ],
      "error": null | str }

- .xlsx and .xlsm through openpyxl (read only, values only: formulas are read
  as their last saved value and macros are never loaded). .csv through the csv
  module, trying utf-8 (with or without a byte order mark) and then cp1252.
- .xls is not read: the answer is the error "Save the sheet as .xlsx first".
- header_row is the 0-based index of the header row in the sheet. By default it
  is the first row with at least two non-empty cells. `rows` are the rows after
  it (all of them, or the first --max-rows), `row_count` counts every row after
  it. Row i of `rows` is sheet row header_row + 2 + i (1-based, as Excel shows).
- Every cell comes back as trimmed text: a date as an ISO date (with the time
  when it has one), a whole number without ".0", an empty cell as "".

Nothing is ever written.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import io
import json
import sys
from pathlib import Path

MAX_COLUMNS = 200


def cell_text(v) -> str:
    """One cell as trimmed text."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, _dt.datetime):
        if v.hour == 0 and v.minute == 0 and v.second == 0 and v.microsecond == 0:
            return v.date().isoformat()
        return v.replace(microsecond=0).isoformat()
    if isinstance(v, (_dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        return repr(v)
    return str(v).strip()


def default_header_row(rows) -> int:
    for i, r in enumerate(rows):
        if sum(1 for c in r if c) >= 2:
            return i
    return 0


def shape_sheet(name: str, rows, header_row, max_rows) -> dict:
    rows = [[cell_text(c) for c in r][:MAX_COLUMNS] for r in rows]
    # Drop blank rows at the very end, a spreadsheet's usual tail.
    while rows and not any(rows[-1]):
        rows.pop()
    hr = default_header_row(rows) if header_row is None else max(0, int(header_row))
    headers = rows[hr] if hr < len(rows) else []
    body = rows[hr + 1 :] if hr < len(rows) else []
    width = 0
    for r in [headers] + body:
        for i in range(len(r) - 1, -1, -1):
            if r[i]:
                width = max(width, i + 1)
                break
    pad = lambda r: (list(r) + [""] * width)[:width]  # noqa: E731
    shown = body if max_rows is None else body[: max(0, int(max_rows))]
    return {
        "name": name,
        "header_row": hr,
        "headers": pad(headers),
        "rows": [pad(r) for r in shown],
        "row_count": len(body),
    }


def read_csv(path: Path):
    raw = path.read_bytes()
    text = None
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("latin-1")
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return list(csv.reader(io.StringIO(text), dialect))


def read_xlsx(path: Path):
    from openpyxl import load_workbook

    wb = load_workbook(str(path), read_only=True, data_only=True, keep_vba=False)
    try:
        out = []
        for ws in wb.worksheets:
            out.append((ws.title, [list(r) for r in ws.iter_rows(values_only=True)]))
        return out
    finally:
        wb.close()


def read(path, header_row=None, max_rows=None, sheet=None) -> dict:
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".xls":
        return {"sheets": [], "error": "Save the sheet as .xlsx first"}
    if not p.is_file():
        return {"sheets": [], "error": "The file was not found"}
    try:
        if ext == ".csv":
            sheets = [(p.stem or "Sheet1", read_csv(p))]
        elif ext in (".xlsx", ".xlsm"):
            sheets = read_xlsx(p)
        else:
            return {"sheets": [], "error": "Only .xlsx, .xlsm and .csv files can be read"}
    except Exception as e:  # a damaged or password-protected file
        return {"sheets": [], "error": f"The file could not be read: {str(e)[:200]}"}
    if sheet is not None:
        sheets = [s for s in sheets if s[0] == sheet]
        if not sheets:
            return {"sheets": [], "error": f"There is no sheet named {sheet}"}
    return {
        "sheets": [shape_sheet(name, rows, header_row, max_rows) for name, rows in sheets],
        "error": None,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Read a patching matrix into rows of text.")
    ap.add_argument("file")
    ap.add_argument("--header-row", type=int, default=None)
    ap.add_argument("--max-rows", type=int, default=None)
    ap.add_argument("--sheet", default=None)
    a = ap.parse_args(argv)
    out = read(a.file, header_row=a.header_row, max_rows=a.max_rows, sheet=a.sheet)
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
