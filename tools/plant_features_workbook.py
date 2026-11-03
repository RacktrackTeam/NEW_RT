"""Make the planted copy of vdo1's workbook for the features proof (docs/proof/features): the office rack's own workbook
(modules/sources/ground_truth/vdo1.xlsx) with cable rows in its empty A_Z_CONNECTION_MATRIX and a serial and an asset tag on two
devices. Every planted row says so in Notes. The Z ends sit in a second rack (SP-HYD-RM01-R02-R2) that was not filmed.

    _cache/venv/bin/python tools/plant_features_workbook.py <out.xlsx>

What the cable check should then show on the stored vdo1 run:
  SP-R1-U20-ACT  port 1 cabled (seen), port 2 planted on an empty port      -> Missing cable on port 2
  SP-R1-U17-SW03 rows for every cabled port but 23 (the states the device page shows) -> Extra cable on port 23
  SP-R1-U18-SW04 rows for every cabled port but 24, and one on empty 21     -> Wrong port: 21 empty, 24 cabled
  CBL-PLANT-L1   SP-R1-U17-SW03 port 9, A-end label CBL-PLANT-L1-A          -> read on the D5 close-up: Wrong port in the A-Z check
And the device rows: SP-R1-U15-SW02 serial PLANTED0001 and asset tag SP-AT-SW02 (the close-up reads others: Wrong serial,
Wrong asset tag); SP-R1-U17-SW03 serial SGP2231A0BC (the same as the close-up of D4 reads: step 3 names the rack by it).
"""
import pathlib
import shutil
import sys

import openpyxl

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "server" / "mocks" / "ground_truth" / "vdo1.xlsx"
RACK, OTHER = "SP-HYD-RM01-R01-R1", "SP-HYD-RM01-R02-R2"
NOTE = "PLANTED for the features proof, not a real cable"


def rows():
    out, n = [], 0
    def add(dev, port, label=None, a_label=None):
        nonlocal n
        n += 1
        out.append({"Record_Status": "Active", "Connection_ID": f"CONN-PL-{n:03d}", "Cable_ID": f"CBL-PL-{n:03d}", "Cable_Label_Primary": label or f"CBL-PL-{n:03d}",
                    "Cable_Label_A_End": a_label or f"{label or f'CBL-PL-{n:03d}'}-A", "Cable_Label_Z_End": f"{label or f'CBL-PL-{n:03d}'}-Z",
                    "A_Rack_ID": RACK, "A_Device_ID": dev, "A_Device_Name": dev, "A_Port_Number": port, "A_Port_Type": "RJ45",
                    "Z_Rack_ID": OTHER, "Z_Device_ID": "SP-R2-U1-PP1", "Z_Device_Name": "SP-R2-U1-PP1", "Z_Port_Number": n, "Media_Type": "Cat6 UTP",
                    "Cable_Color": "Blue", "Expected_State": "Connected", "Notes": NOTE})
    add("SP-R1-U20-ACT", 1); add("SP-R1-U20-ACT", 2)
    for p in (3, 4, 6, 7, 8, 10, 11, 12, 13, 14, 15, 18, 19, 20, 21, 22, 24):     # D4's cabled ports as every screen reads them (source_db.screen_ports)
        add("SP-R1-U17-SW03", p)
    add("SP-R1-U17-SW03", 9, label="CBL-PLANT-L1", a_label="CBL-PLANT-L1-A")
    for p in (1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 20, 21):                          # D3's cabled ports but 24, and the empty 21
        add("SP-R1-U18-SW04", p)
    return out


def main(out):
    shutil.copy(SRC, out)
    wb = openpyxl.load_workbook(out)
    ws = wb["A_Z_CONNECTION_MATRIX"]
    head = [c.value for c in ws[1]]
    for r in rows():
        ws.append([r.get(h) for h in head])
    dv = wb["DEVICE_INVENTORY"]
    h = [c.value for c in dv[1]]
    for row in dv.iter_rows(min_row=2):
        did = row[h.index("Device_ID")].value
        if did == "SP-R1-U15-SW02":
            row[h.index("Serial_Number")].value, row[h.index("Asset_Tag")].value = "PLANTED0001", "SP-AT-SW02"
            row[h.index("Notes")].value = "Serial and asset tag PLANTED for the features proof"
        if did == "SP-R1-U17-SW03":
            row[h.index("Serial_Number")].value = "SGP2231A0BC"
            row[h.index("Notes")].value = "Serial PLANTED for the features proof"
    wb.save(out)
    print(f"{out}: {len(rows())} planted cable rows")


if __name__ == "__main__":
    main(sys.argv[1])
