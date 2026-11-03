"""Make the Excel fixtures for the matrix tests.

    .venv/bin/python server/test/fixtures/make_matrix_xlsx.py

manager-sample.xlsx      the manager's workbook, every sheet: ten cables (one
                         with no A port, one label used twice, one Planned,
                         one Spare), two racks, six devices (one with no
                         cables), four ports, one label rule
manager-sample-v2.xlsx   a second revision: five cables, a port written 1.0
matrix-sample.xlsx       an Excel in any other layout (a cover sheet and a
                         "Patching" sheet), which the import refuses
"""

import datetime as dt
import sys
from pathlib import Path

from openpyxl import Workbook

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent.parent))
from pipeline.workbook_write import write_workbook  # noqa: E402


def cable(n, label, a_dev, a_port, a_rack, a_ru, z_dev, z_port, z_rack, z_ru, media="Fiber", mode="MMF-OM4", **extra):
    row = {
        "Record_Status": "Active", "Connection_ID": f"CONN-{n:03d}", "Cable_ID": f"CBL-{n:03d}",
        "Cable_Label_Primary": label, "Cable_Label_A_End": f"{label}-A", "Cable_Label_Z_End": f"{label}-Z",
        "A_Site": "HALL", "A_Rack_ID": a_rack, "A_Rack_Name": a_rack, "A_Device_ID": a_dev, "A_Device_Name": a_dev,
        "A_RU_Start": a_ru, "A_Port_Name": a_port, "A_Port_Type": "SFP+", "A_Expected_Occupied": "Yes", "A_Expected_Dust_Cap": "N/A",
        "Z_Site": "HALL", "Z_Rack_ID": z_rack, "Z_Rack_Name": z_rack, "Z_Device_ID": z_dev, "Z_Device_Name": z_dev,
        "Z_RU_Start": z_ru, "Z_Port_Name": z_port, "Z_Expected_Occupied": "Yes",
        "Media_Type": media, "Fiber_Mode": mode, "Connector": None, "Expected_State": "Connected",
        "Source_Revision": "Rev-1", "Change_Ticket_ID": "CHG-1",
    }
    row.pop("Connector")
    row.update(extra)
    return row


cables = [
    cable(1, "FC-001", "SW-CORE-01", "Gi1/0/1", "R01", 42, "PP-A01", "1", "R02", 40, Effective_Date=dt.datetime(2026, 10, 1)),
    cable(2, "FC-002", "SW-CORE-01", "Gi1/0/2", "R01", 42, "PP-A01", "2", "R02", 40, Approval_Date=46294),
    cable(3, "FC-003", "SW-CORE-01", "Gi1/0/3", "R01", 42, "PP-A01", "3", "R02", 40),
    cable(4, "FC-004", "SW-CORE-01", None, "R01", 42, "PP-A01", "4", "R02", 40, Notes="port not written on the sheet"),
    {},
    cable(5, "FC-005", "SW-CORE-02", "Gi1/0/1", "R01", 40, "PP-A02", "1", "R02", 38, mode="SMF"),
    cable(6, "FC-006", "SW-CORE-02", "Gi1/0/2", "R01", 40, "PP-A02", "2", "R02", 38, mode="SMF"),
    cable(7, "FC-006", "SW-CORE-02", "Gi1/0/3", "R01", 40, "PP-A02", "3", "R02", 38, mode="SMF", Notes="same label twice"),
    cable(8, "FC-008", "FW-01", "eth0", "R01", 38, "PP-A02", "4", "R02", 38, mode="SMF", Notes="Uplink", Cable_Color="Yellow"),
    cable(9, "FC-009", "SW-CORE-02", "Gi1/0/9", "R01", 40, "PP-A02", "9", "R02", 38, Record_Status="Planned"),
    cable(10, "FC-010", "SW-CORE-02", "Gi1/0/10", "R01", 40, "PP-A02", "10", "R03", 38, Expected_State="Spare"),
]
racks = [
    {"Record_Status": "Active", "Site": "HALL", "Rack_ID": "R01", "Rack_Name": "R01", "Rack_Height_RU": 42, "Expected_Rack_Label": "R01"},
    {"Record_Status": "Active", "Site": "HALL", "Rack_ID": "R02", "Rack_Name": "R02", "Rack_Height_RU": 42, "Expected_Rack_Label": "R-02"},
]


def device(dev_id, rack, ru, height=1, label=None):
    return {"Record_Status": "Active", "Site": "HALL", "Rack_ID": rack, "Device_ID": dev_id, "Device_Name": dev_id,
            "RU_Start": ru, "RU_Height": height, "Expected_Device_Label": label or dev_id}


devices = [
    device("SW-CORE-01", "R01", 42, label="CORE01"),
    device("SW-CORE-02", "R01", 40),
    device("FW-01", "R01", 38),
    device("SPARE-SW", "R01", 30, height=2),
    device("PP-A01", "R02", 40),
    device("PP-A02", "R02", 38),
]
ports = [
    {"Record_Status": "Active", "Rack_ID": "R01", "Device_ID": "SW-CORE-01", "Port_ID": "P-1", "Port_Name": "Gi1/0/1", "Port_Number": "1",
     "Port_Type": "SFP+", "Expected_Occupied": "Yes", "Expected_Dust_Cap": "N/A"},
    {"Record_Status": "Active", "Rack_ID": "R01", "Device_ID": "SW-CORE-01", "Port_ID": "P-24", "Port_Name": "Gi1/0/24", "Port_Number": "24",
     "Port_Type": "SFP+", "Expected_Occupied": "no", "Expected_Dust_Cap": "Yes"},
    {"Record_Status": "Active", "Rack_ID": "R01", "Device_ID": "SW-CORE-02", "Port_ID": "P-2-1", "Port_Name": "Gi1/0/1", "Port_Number": "1",
     "Port_Type": "SFP-X", "Expected_Occupied": "Yes"},
    {"Record_Status": "Active", "Rack_ID": "R01", "Device_ID": "SW-GONE", "Port_ID": "P-9", "Port_Name": "Gi1/0/1", "Port_Number": "1"},
]
rules = [{"Rule_ID": "LBL-001", "Object_Type": "Cable", "Applies_To": "A and Z end labels", "Expected_Pattern": "<CableID>-A / <CableID>-Z",
          "Example": "FC-001-A", "Case_Sensitive": "No", "OCR_Normalization": "Uppercase; trim whitespace", "Required": "Yes"}]
write_workbook(HERE / "manager-sample.xlsx", {
    "A_Z_CONNECTION_MATRIX": cables, "RACK_INVENTORY": racks, "DEVICE_INVENTORY": devices,
    "PORT_INVENTORY": ports, "LABEL_RULES": rules,
})

v2 = [cable(100 + i, f"F-10{i}", "CORE-A", str(i + 1), "R01", 42, "EDGE-1", 49 + i, "R02", 40, Source_Revision="Rev-2") for i in range(4)]
v2.append(cable(104, "F-104", "CORE-B", "3", "R01", 40, "EDGE-3", 1.0, "R02", 38, Source_Revision="Rev-2"))
write_workbook(HERE / "manager-sample-v2.xlsx", {
    "A_Z_CONNECTION_MATRIX": v2, "RACK_INVENTORY": racks, "DEVICE_INVENTORY": [], "PORT_INVENTORY": [], "LABEL_RULES": rules,
})

# Any other layout: the import refuses it.
wb = Workbook()
cover = wb.active
cover.title = "Cover"
cover["A1"] = "Rack build patching - pilot"
ws = wb.create_sheet("Patching")
ws.append(["Patching schedule, hall 1"])
ws.append(["Fibre ID", "From", "From Port", "To", "To Port", "Media", "Remarks"])
ws.append(["F-100", "CORE-A", 1, "EDGE-1", 49, "SMF", dt.datetime(2026, 9, 28)])
wb.save(HERE / "matrix-sample.xlsx")
