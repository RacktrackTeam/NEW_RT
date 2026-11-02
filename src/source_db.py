"""source_db.py - the site's database copied into ours, the rack match and the drift for a video run.

Three sources fill one table set in data/sources.db (racks and devices, each row with its source and import time):
  excel   the manager's source-of-truth workbook (RT-EOS column names: RACK_INVENTORY, DEVICE_INVENTORY), uploaded
  netbox  NetBox's REST API, read from the local NetBox mock (modules/sources/mock/netbox.js)
  cmdb    ServiceNow's Table API, read from the local ServiceNow mock (modules/sources/mock/servicenow.js)
Nothing is ever written back to a source. Nothing here calls an outside system.
"""
import datetime
import json
import os
import pathlib
import re
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB = pathlib.Path(os.environ.get("RACKTRACK_SOURCES_DB", ROOT / "data" / "sources.db"))
SOURCES = {"excel": "Excel workbook", "netbox": "NetBox", "cmdb": "ServiceNow CMDB"}
NOT_SEEN_KINDS = {"cable manager", "blank panel", "blank", "shelf", "tray"}   # the video's models have no class for these (as in RT-EOS)
# Every import is a copy (src_copies) with its own version per source; the rows keep the copy's id. For each source and rack the
# newest copy that holds the rack is the active one; deleting a copy brings the one before it back.
SCHEMA = """
CREATE TABLE IF NOT EXISTS src_copies (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, version INTEGER, imported_at TEXT, file TEXT,
  racks INTEGER, devices INTEGER, ports INTEGER);
CREATE TABLE IF NOT EXISTS src_racks (copy_id INTEGER, source TEXT, rack_key TEXT, name TEXT, label TEXT, height_ru INTEGER, imported_at TEXT, file TEXT,
  PRIMARY KEY (copy_id, rack_key));
CREATE TABLE IF NOT EXISTS src_devices (copy_id INTEGER, source TEXT, rack_key TEXT, device_key TEXT, name TEXT, role TEXT, kind TEXT, manufacturer TEXT,
  model TEXT, serial TEXT, asset_tag TEXT, ru_start INTEGER, ru_height INTEGER, imported_at TEXT, PRIMARY KEY (copy_id, rack_key, device_key));
CREATE TABLE IF NOT EXISTS src_ports (copy_id INTEGER, source TEXT, rack_key TEXT, device_key TEXT, port_number INTEGER, port_name TEXT, port_type TEXT,
  expected_occupied TEXT, expected_dust_cap TEXT);
"""
ACTIVE = "copy_id = (SELECT MAX(x.copy_id) FROM src_racks x WHERE x.source = {t}.source AND x.rack_key = {t}.rack_key)"


def _db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='src_racks'").fetchone() and \
            "copy_id" not in [r[1] for r in c.execute("PRAGMA table_info(src_racks)")]:
        c.executescript("DROP TABLE src_racks; DROP TABLE IF EXISTS src_devices;")   # the first layout had no copies: its rows are copied in again
    c.executescript(SCHEMA)
    return c


def _int(v):
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


def _s(v):
    v = None if v is None else str(v).strip()
    return v or None


def _kind(role):
    """A record's role in the video's kind words (Switch, Router, Firewall, Patch Panel, PDU, ...)."""
    r = (role or "").lower()
    for k, words in (("Patch Panel", ("patch",)), ("Cable Manager", ("cable manager", "cable mgmt")), ("Switch", ("switch",)), ("Router", ("router",)),
                     ("Firewall", ("firewall",)), ("PDU", ("pdu", "power distribution")), ("UPS", ("ups",)), ("Server", ("server",)), ("Storage unit", ("storage", "nas"))):
        if any(w in r for w in words):
            return k
    return _s(role)


def _store(source, racks, devices, file, ports=()):
    """A new copy of this source: its own version, its racks, devices and ports. It becomes the active copy of each rack it holds."""
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with _db() as c:
        version = (c.execute("SELECT MAX(version) FROM src_copies WHERE source=?", (source,)).fetchone()[0] or 0) + 1
        cid = c.execute("INSERT INTO src_copies (source, version, imported_at, file, racks, devices, ports) VALUES (?,?,?,?,?,?,?)",
                        (source, version, now, file, len(racks), len(devices), len(ports))).lastrowid
        for r in racks:
            c.execute("INSERT OR REPLACE INTO src_racks VALUES (?,?,?,?,?,?,?,?)", (cid, source, r["rack_key"], r.get("name"), r.get("label"), r.get("height_ru"), now, file))
        for d in devices:
            c.execute("INSERT OR REPLACE INTO src_devices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (cid, source, d["rack_key"], d["device_key"], d.get("name"), d.get("role"), d.get("kind") or _kind(d.get("role")), d.get("manufacturer"), d.get("model"),
                       d.get("serial"), d.get("asset_tag"), d.get("ru_start"), d.get("ru_height"), now))
        for q in ports:
            c.execute("INSERT INTO src_ports VALUES (?,?,?,?,?,?,?,?,?)", (cid, source, q["rack_key"], q["device_key"], q.get("port_number"), q.get("port_name"),
                                                                        q.get("port_type"), q.get("expected_occupied"), q.get("expected_dust_cap")))
    return {"source": source, "copy_id": cid, "version": version, "racks": len(racks), "devices": len(devices), "ports": len(ports), "imported_at": now, "file": file}


def copies():
    """Every copy, newest first, with whether it is the active copy of at least one rack."""
    with _db() as c:
        rows = [dict(x) for x in c.execute("SELECT * FROM src_copies ORDER BY id DESC")]
        live = {x[0] for x in c.execute("SELECT DISTINCT copy_id FROM src_racks r WHERE " + ACTIVE.format(t="r"))}
    for r in rows:
        r["active"] = r["id"] in live
    return rows


def delete_copy(cid):
    with _db() as c:
        if not c.execute("DELETE FROM src_copies WHERE id=?", (cid,)).rowcount:
            return False
        for t in ("src_racks", "src_devices", "src_ports"):
            c.execute(f"DELETE FROM {t} WHERE copy_id=?", (cid,))
    return True


# ------------------------------------------------------------------ the three readers
# The manager's workbook: the checks are RT-EOS's (rt_eos/server/lib/workbook.js parse(), the inventory part; this app copies
# no cables yet, so the A_Z_CONNECTION_MATRIX rows are counted, not checked).
YES_NO = ("Yes", "No", "N/A")
DEFAULT_LOOKUPS = {"Record_Status": ["Active", "Planned", "Decommissioned", "Superseded"],
                   "Port_Type": ["OSFP", "QSFP-DD", "QSFP28", "QSFP+", "SFP28", "SFP+", "SFP", "RJ45"]}
NEEDED = {"RACK_INVENTORY": ("Rack_ID", "Rack_Height_RU"), "DEVICE_INVENTORY": ("Rack_ID", "Device_ID", "RU_Start", "RU_Height"),
          "PORT_INVENTORY": ("Device_ID", "Port_Number", "Port_Type", "Expected_Occupied", "Expected_Dust_Cap")}
OPTIONAL_SHEETS = ("PORT_INVENTORY", "A_Z_CONNECTION_MATRIX", "LABEL_RULES", "LOOKUPS")


def _key(h):
    return re.sub(r"[^a-z0-9]", "", str(h or "").lower())


def _yes_no(v):
    v = _s(v)
    return next((x for x in YES_NO if _key(x) == _key(v)), v) if v else None


def read_workbook(path):
    """Read every sheet and check it: (racks, devices, ports, report). The report lists every problem with its sheet and row,
    and counts; `blocking` is true when the import cannot go on (a sheet or column it needs is missing)."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    names = {_key(n): n for n in wb.sheetnames}
    problems, counts = [], {}

    def note(level, sheet, row, reason, kind):
        problems.append({"level": level, "sheet": sheet, "row_no": row, "reason": reason, "kind": kind})

    def rows(name):
        if _key(name) not in names:
            return None
        it = wb[names[_key(name)]].iter_rows(values_only=True)
        head = [_s(h) for h in next(it, [])]
        out = []
        for i, row in enumerate(it, start=2):
            rec = {_key(h): v for h, v in zip(head, row) if h}
            if any(_s(v) for v in rec.values()):
                out.append((i, rec))
        for col in NEEDED.get(name, ()):
            if _key(col) not in {_key(h) for h in head if h}:
                note("error", name, 1, f"The {name} sheet has no {col} column.", "missing column")
        return out

    for name in ("RACK_INVENTORY", "DEVICE_INVENTORY"):
        if _key(name) not in names:
            note("error", name, None, f"The {name} sheet is missing. This is not the manager's workbook, or the sheet was renamed.", "missing sheet")
    for name in OPTIONAL_SHEETS:
        if _key(name) not in names:
            note("warning", name, None, f"The {name} sheet is missing. The import goes on without it.", "missing sheet")
    look = dict(DEFAULT_LOOKUPS)
    lk = rows("LOOKUPS")
    if lk is not None:
        it = wb[names[_key("LOOKUPS")]].iter_rows(values_only=True); head = next(it, [])
        cols = list(it)
        for j, h in enumerate(head):
            vals = [_s(r[j]) for r in cols if j < len(r) and _s(r[j])]
            if h and vals:
                look[str(h).strip()] = vals
    status = lambda rec: _s(rec.get("recordstatus")) or "Active"
    live = lambda rec: status(rec).lower() == "active"
    in_list = lambda lst, v: not v or any(_key(x) == _key(v) for x in lst or [])

    rk_rows, dv_rows, pt_rows = rows("RACK_INVENTORY") or [], rows("DEVICE_INVENTORY") or [], rows("PORT_INVENTORY") or []
    az = rows("A_Z_CONNECTION_MATRIX")
    counts["cables"] = len(az or [])

    def unique(lst, field, sheet, what):
        seen = {}
        for n, rec in lst:
            v = _s(rec.get(field))
            if not v:
                note("warning", sheet, n, f"{what} is empty.", "empty id"); continue
            if not live(rec):
                continue
            if _key(v) in seen:
                note("error", sheet, n, f"{what} {v} is also on row {seen[_key(v)]}. An id is never used twice.", "duplicate id")
            else:
                seen[_key(v)] = n
    unique(rk_rows, "rackid", "RACK_INVENTORY", "Rack_ID")
    unique(dv_rows, "deviceid", "DEVICE_INVENTORY", "Device_ID")
    unique(pt_rows, "portid", "PORT_INVENTORY", "Port_ID")
    for sheet, lst in (("RACK_INVENTORY", rk_rows), ("DEVICE_INVENTORY", dv_rows), ("PORT_INVENTORY", pt_rows)):
        for n, rec in lst:
            if not in_list(look.get("Record_Status"), status(rec)):
                note("warning", sheet, n, f'Record_Status "{status(rec)}" is not one of the LOOKUPS values.', "value not in LOOKUPS")
            if "illustrative sample" in str(rec.get("notes") or "").lower():
                note("warning", sheet, n, "This is the template's sample row. It is skipped.", "sample row")

    height = {_key(rec.get("rackid")): _int(rec.get("rackheightru")) for n, rec in rk_rows if _s(rec.get("rackid"))}
    dev_ids = {_key(rec.get("deviceid")) for n, rec in dv_rows if _s(rec.get("deviceid"))}
    by_rack = {}
    for n, rec in dv_rows:
        name, rid = _s(rec.get("devicename")) or _s(rec.get("deviceid")) or "This device", _s(rec.get("rackid"))
        start, h = _int(rec.get("rustart")), _int(rec.get("ruheight")) or 1
        if rid and rk_rows and _key(rid) not in height:
            note("error", "DEVICE_INVENTORY", n, f"{name} is in rack {rid}, which is not in RACK_INVENTORY.", "rack not in RACK_INVENTORY")
        if start is None:
            note("warning", "DEVICE_INVENTORY", n, f"{name} has no RU_Start, so its unit cannot be checked.", "no unit")
            continue
        top = height.get(_key(rid))
        if start < 1 or (top and start + h - 1 > top):
            note("error", "DEVICE_INVENTORY", n, f"{name} at U{start}" + (f" to U{start + h - 1}" if h > 1 else "") + f" is outside the rack's {top or '?'} units.",
                 "outside the rack height")
        if live(rec) and rid:
            by_rack.setdefault(_key(rid), []).append((start, h, name, n))
    for lst in by_rack.values():
        lst.sort()
        for (s0, h0, n0, _), (s1, h1, n1, r1) in zip(lst, lst[1:]):
            if s1 <= s0 + h0 - 1:
                note("warning", "DEVICE_INVENTORY", r1, f"{n1} at U{s1} overlaps {n0} (U{s0} to U{s0 + h0 - 1}).", "devices overlap")
    for n, rec in pt_rows:
        did = _s(rec.get("deviceid"))
        if did and dv_rows and _key(did) not in dev_ids:
            note("warning", "PORT_INVENTORY", n, f"Device_ID {did} is not in DEVICE_INVENTORY.", "device not in DEVICE_INVENTORY")
        pt = _s(rec.get("porttype"))
        if pt and not in_list(look.get("Port_Type"), pt):
            note("warning", "PORT_INVENTORY", n, f'Port_Type "{pt}" is not one of the LOOKUPS values.', "value not in LOOKUPS")
        for f, col in (("expectedoccupied", "Expected_Occupied"), ("expecteddustcap", "Expected_Dust_Cap")):
            v = _yes_no(rec.get(f))
            if v and v not in YES_NO:
                note("warning", "PORT_INVENTORY", n, f'{col} should be Yes, No or N/A, not "{v}".', "not Yes or No")

    keep = lambda rec: status(rec).lower() not in ("sample", "retired", "inactive", "decommissioned", "superseded") and "illustrative sample" not in str(rec.get("notes") or "").lower()
    racks = [{"rack_key": _s(r.get("rackid")), "name": _s(r.get("rackname")) or _s(r.get("rackid")), "label": _s(r.get("expectedracklabel")),
              "height_ru": _int(r.get("rackheightru"))} for n, r in rk_rows if _s(r.get("rackid")) and keep(r)]
    devices = [{"rack_key": _s(r.get("rackid")), "device_key": _s(r.get("deviceid")) or _s(r.get("devicename")), "name": _s(r.get("devicename")) or _s(r.get("deviceid")),
                "role": _s(r.get("devicerole")), "manufacturer": _s(r.get("manufacturer")), "model": _s(r.get("model")), "serial": _s(r.get("serialnumber")),
                "asset_tag": _s(r.get("assettag")), "ru_start": _int(r.get("rustart")), "ru_height": _int(r.get("ruheight"))}
               for n, r in dv_rows if _s(r.get("rackid")) and (_s(r.get("deviceid")) or _s(r.get("devicename"))) and keep(r)]
    rack_of = {_key(d["device_key"]): d["rack_key"] for d in devices}
    ports = [{"rack_key": _s(r.get("rackid")) or rack_of.get(_key(r.get("deviceid"))), "device_key": _s(r.get("deviceid")), "port_number": _int(r.get("portnumber")),
              "port_name": _s(r.get("portname")), "port_type": _s(r.get("porttype")), "expected_occupied": _yes_no(r.get("expectedoccupied")),
              "expected_dust_cap": _yes_no(r.get("expecteddustcap"))} for n, r in pt_rows if _s(r.get("deviceid")) and keep(r)]
    known = {r["rack_key"] for r in racks}
    racks += [{"rack_key": k, "name": k, "label": None, "height_ru": None} for k in sorted({d["rack_key"] for d in devices} - known)]
    by_kind = {}
    for x in problems:
        by_kind[x["kind"]] = by_kind.get(x["kind"], 0) + 1
    errors = sum(x["level"] == "error" for x in problems)
    blocking = any(x["kind"] in ("missing sheet", "missing column") and x["level"] == "error" and x["sheet"] != "PORT_INVENTORY" for x in problems)
    counts.update(racks=len(racks), devices=len(devices), ports=len(ports), problems=len(problems), errors=errors, warnings=len(problems) - errors)
    report = {"file": pathlib.Path(path).name, "sheets": [{"name": n, "rows": max(0, (wb[n].max_row or 1) - 1)} for n in wb.sheetnames],
              "counts": counts, "by_kind": by_kind, "problems": problems, "blocking": blocking}
    return racks, devices, ports, report


def validate_excel(path):
    return read_workbook(path)[3]


def import_excel(path):
    """The manager's workbook into a new Excel copy: RACK_INVENTORY, DEVICE_INVENTORY and PORT_INVENTORY, by RT-EOS's column names."""
    racks, devices, ports, report = read_workbook(path)
    if report["blocking"]:
        raise ValueError(" ".join(x["reason"] for x in report["problems"] if x["level"] == "error" and x["kind"] in ("missing sheet", "missing column")))
    out = _store("excel", racks, devices, pathlib.Path(path).name, ports)
    out["problems"] = report["counts"]["problems"]
    return out


NETBOX_URL = os.environ.get("RACKTRACK_NETBOX_URL", "http://127.0.0.1:3301")          # the local NetBox mock (modules/sources/mock/netbox.js)
SERVICENOW_URL = os.environ.get("RACKTRACK_SERVICENOW_URL", "http://127.0.0.1:5301")  # the local ServiceNow mock (modules/sources/mock/servicenow.js)
CMDB_KIND = {"cmdb_ci_ip_switch": "Switch", "cmdb_ci_netgear": "Patch Panel", "cmdb_ci_server": "Server", "cmdb_ci_firewall_network": "Firewall",
             "cmdb_ci_router": "Router", "cmdb_ci_pdu": "PDU", "cmdb_ci_storage_device": "Storage unit"}


def _get(base, path, headers=None):
    """GET a JSON page from a local mock. Only this machine is ever called: never a real NetBox or ServiceNow."""
    import urllib.parse, urllib.request
    host = urllib.parse.urlparse(base).hostname
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("Only the local mocks are read today, never a real system.")
    try:
        with urllib.request.urlopen(urllib.request.Request(base.rstrip("/") + path, headers=headers or {}), timeout=15) as r:
            return json.loads(r.read().decode())
    except OSError as e:
        why = getattr(e, "reason", e)
        why = getattr(why, "strerror", None) or str(why)
        raise ConnectionError(f"{base} does not answer ({why.lower()}).")


def _login(org, type_, default):
    """The organization's saved login (src/connections.py), else the local mock with no login."""
    import connections
    lg = connections.resolve(org, type_) if org else None
    return (lg["address"], connections.headers(type_, lg["secret"])) if lg else (default, None)


def import_netbox(org=None):
    """NetBox's REST API (dcim/racks and dcim/devices), through the organization's saved login, else the local NetBox mock."""
    base, hd = _login(org, "netbox", NETBOX_URL)
    rk = _get(base, "/api/dcim/racks/?limit=1000", hd)["results"]
    racks = [{"rack_key": r["name"], "name": r["name"], "label": r.get("facility_id") or r["name"], "height_ru": _int(r.get("u_height"))} for r in rk]
    devices = []
    for d in _get(base, "/api/dcim/devices/?limit=10000", hd)["results"]:
        if not d.get("rack"):
            continue
        ty = d.get("device_type") or {}
        devices.append({"rack_key": d["rack"]["name"], "device_key": d["name"], "name": d["name"], "role": (d.get("role") or {}).get("name"),
                        "manufacturer": _s((ty.get("manufacturer") or {}).get("name")), "model": _s(ty.get("model")), "serial": _s(d.get("serial")),
                        "asset_tag": _s(d.get("asset_tag")), "ru_start": _int(d.get("position")), "ru_height": _int(ty.get("u_height"))})
    return _store("netbox", racks, devices, base)


def import_cmdb(org=None):
    """ServiceNow's Table API (cmdb_ci_rack, cmdb_rel_ci and the device classes in cmdb_ci), through the saved login, else the local mock."""
    base, hd = _login(org, "cmdb", SERVICENOW_URL)
    rk = _get(base, "/api/now/table/cmdb_ci_rack?sysparm_limit=10000", hd)["result"]
    racks = [{"rack_key": r["name"], "name": r["name"], "label": r["name"], "height_ru": _int(r.get("rack_units"))} for r in rk]
    rack_of = {r["sys_id"]: r["name"] for r in rk}
    child_rack = {x["child"]["value"]: rack_of[x["parent"]["value"]] for x in _get(base, "/api/now/table/cmdb_rel_ci?sysparm_limit=100000", hd)["result"]
                  if x["parent"]["value"] in rack_of}
    devices = []
    for d in _get(base, "/api/now/table/cmdb_ci?sysparm_limit=100000&sysparm_display_value=true", hd)["result"]:
        if d.get("sys_id") not in child_rack:
            continue
        devices.append({"rack_key": child_rack[d["sys_id"]], "device_key": d["name"], "name": d["name"], "role": _s(d.get("short_description")),
                        "kind": CMDB_KIND.get(d.get("sys_class_name")) or _kind(d.get("short_description")), "manufacturer": _s(d.get("manufacturer")),
                        "model": _s(d.get("model_id")), "serial": _s(d.get("serial_number")), "asset_tag": _s(d.get("asset_tag")),
                        "ru_start": _int(d.get("u_position")), "ru_height": _int(d.get("u_height"))})
    return _store("cmdb", racks, devices, base)


def racks(source=None):
    """The active copy of every rack."""
    with _db() as c:
        q = ("SELECT r.*, (SELECT COUNT(*) FROM src_devices d WHERE d.copy_id=r.copy_id AND d.rack_key=r.rack_key) AS devices FROM src_racks r WHERE "
             + ACTIVE.format(t="r"))
        return [dict(x) for x in (c.execute(q + " AND source=? ORDER BY rack_key", (source,)) if source else c.execute(q + " ORDER BY source, rack_key"))]


def devices(source, rack_key):
    with _db() as c:
        return [dict(x) for x in c.execute("SELECT * FROM src_devices d WHERE source=? AND rack_key=? AND " + ACTIVE.format(t="d") + " ORDER BY ru_start DESC",
                                           (source, rack_key))]


def ports(source, rack_key):
    """PORT_INVENTORY rows of the rack's active copy, by device: {device_key: {port_number: row}}."""
    out = {}
    with _db() as c:
        for x in c.execute("SELECT * FROM src_ports p WHERE source=? AND rack_key=? AND " + ACTIVE.format(t="p"), (source, rack_key)):
            if x["port_number"] is not None:
                out.setdefault(x["device_key"], {})[x["port_number"]] = dict(x)
    return out


# ------------------------------------------------------------------ the rack match
def _norm(s):
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def match(source, rack_id):
    """The record for the run's confirmed rack id: exact on label, id or name first, then with separators and case ignored."""
    if not rack_id:
        return None
    rows = racks(source)
    for field in ("label", "rack_key", "name"):
        for r in rows:
            if r.get(field) and r[field] == rack_id:
                return {"rack_key": r["rack_key"], "how": "exact", "why": f"The rack id {rack_id} is the record's {_FIELD[field]} exactly."}
    for field in ("label", "rack_key", "name"):
        for r in rows:
            if r.get(field) and _norm(r[field]) == _norm(rack_id):
                return {"rack_key": r["rack_key"], "how": "close", "why": f"The rack id {rack_id} is the record's {_FIELD[field]} {r[field]} once spaces, dashes and case are ignored."}
    return None


_FIELD = {"label": "rack label", "rack_key": "rack id", "name": "rack name"}


# ------------------------------------------------------------------ the drift
def _unum(u):
    m = re.search(r"\d+", str(u or ""))
    return int(m.group()) if m else None


LAYOUT_KIND = {"router": "Router", "switch": "Switch", "firewall": "Firewall", "gateway": "Gateway"}


def layout_kind(L):
    """The kind the make and model step identified (what the result screen shows), when that step read the device."""
    if not L or not L.get("ok") or L.get("error"):
        return None
    if not (L.get("fitted") or L.get("auto") or L.get("vendor_read") or L.get("model_read") or str(L.get("status") or "").startswith("mapped")):
        return None
    return LAYOUT_KIND.get(str(L.get("kind") or "").strip().lower())


def run_devices(result):
    """The run's devices as start unit, height, kind, make, model and serial (None where not read)."""
    out = []
    for d in (result or {}).get("devices") or []:
        nums = [n for n in (_unum(u) for u in d.get("units") or []) if n is not None]
        if not nums:
            continue
        L = d.get("layout") or {}
        kind = (d.get("kind_override") or {}).get("kind") or layout_kind(L) or d.get("type") or (d.get("classification") or {}).get("label")
        out.append({"index": d.get("index"), "ru_start": min(nums), "ru_height": len(nums), "kind": None if kind in (None, "", "Other", "Not sure") else kind,
                    "manufacturer": _s(L.get("vendor")), "model": _s(L.get("model")), "serial": _s(L.get("serial") or d.get("serial")), "ports": run_ports(d)})
    return out


def run_ports(d):
    """The device's ports as the result screen takes them (server/app.py lay_of: a close-up first, the fitted layout's ports, else the
    port details), each as number, type, state and the dust cap where the cap model read one."""
    C, L = d.get("closeup") or {}, d.get("layout") or {}
    src = C if (C.get("fitted") or C.get("auto")) else L
    pd = ((C.get("port_details") if src is C else None) or L.get("port_details") or {})
    used = src.get("fitted") or src.get("auto") or src.get("mode") == "patchpanel"
    raw = (src.get("ports") or []) if used else (pd.get("ports") or [])
    cap = {p.get("id"): p.get("cap_state") for p in pd.get("ports") or [] if p.get("cap_state")}
    out = []
    for p in raw:
        n = _int(p.get("id")) if str(p.get("id") or "").strip().isdigit() else None
        if n is not None:
            out.append({"n": n, "type": _s(p.get("type")), "state": p.get("status"), "cap": p.get("cap_state") or cap.get(p.get("id"))})
    return out


def port_family(t):
    """RT-EOS's portFamily (rt_eos/server/lib/workbook.js): a read "sfp" fits the sheet's "SFP+", "qsfp" its "QSFP28"."""
    k = _key(t)
    for fam, words in (("OSFP", ("osfp",)), ("QSFP", ("qsfp",)), ("SFP", ("sfp",)), ("RJ45", ("rj45", "8p8c", "copper", "ethernet"))):
        if any(w in k for w in words):
            return fam
    return None


COPPER = re.compile(r"RJ45|RJ-45|CONSOLE|AUX|MANAGEMENT|USB", re.I)


def port_drift(name, dev, want):
    """RT-EOS's port rules (rt_eos/server/lib/sheet_check.js check()) for one matched device against its PORT_INVENTORY rows:
    Port_Type against the read cage, Expected_Occupied against the read state (missing or extra cable), Expected_Dust_Cap against
    the cap model (No is never a fault, Yes counts on any port, blank only on an optical port). Unread states are not judged."""
    rows, checked = [], 0
    at = _units(dev["ru_start"], dev["ru_height"])
    for p in dev["ports"]:
        x = want.get(p["n"])
        if not x:
            continue
        checked += 1
        base = {"unit": at, "device": f"{name} / D{dev['index']}", "port": p["n"]}
        wf, gf = port_family(x.get("port_type")), port_family(p["type"])
        if wf and gf and wf != gf:
            rows.append({**base, "type": "port_type", "what": "Wrong port type", "record": x["port_type"], "read": p["type"],
                         "words": f"Port {p['n']} of {name} is {x['port_type']} in the record; the video reads {p['type'].upper()}."})
        occ = x.get("expected_occupied")
        if occ == "Yes" and p["state"] == "empty":
            rows.append({**base, "type": "port_occupied", "what": "Missing cable", "record": "occupied", "read": "empty",
                         "words": f"Port {p['n']} of {name} should have a cable; the video shows it empty."})
        elif occ == "No" and p["state"] == "connected":
            rows.append({**base, "type": "port_occupied", "what": "Extra cable", "record": "empty", "read": "cable",
                         "words": f"Port {p['n']} of {name} should be empty; the video shows a cable in it."})
        capw = x.get("expected_dust_cap")
        optical = bool(p["type"]) and not COPPER.search(p["type"])
        if capw != "No" and (capw == "Yes" or optical) and p["state"] == "empty" and p.get("cap") == "empty":
            rows.append({**base, "type": "dust_cap", "what": "Dust cap missing", "record": "cap" if capw == "Yes" else "cap on an empty optical port", "read": "no cap",
                         "words": f"Port {p['n']} of {name} is empty and has no dust cap" + ("; the record expects one." if capw == "Yes" else "; an empty optical port needs one.")})
    return rows, checked


def _same(a, b):
    a, b = _norm(a), _norm(b)
    return bool(a and b and (a == b or a in b or b in a))


def _units(start, height):
    return f"U{start}" if not height or height == 1 else f"U{start}-{start + height - 1}"


def drift(result, record, ports=None):
    """One row per difference between the run and the record, in plain words. `ports` ({device_key: {port_number: row}}, from an Excel
    copy's PORT_INVENTORY) adds the port rows of each matched device."""
    rows, notes, ports_checked = [], [], 0
    seen = run_devices(result)
    top = max([_unum(r.get("unit")) or 0 for r in ((result or {}).get("rack") or {}).get("rows") or []] + [0]) or None
    free = list(seen)
    wanted = []
    for r in record:
        if r.get("ru_start") is None:
            notes.append(f"{r.get('name')} has no unit in the record, so it is not checked.")
        elif (r.get("kind") or "").lower() in NOT_SEEN_KINDS:
            notes.append(f"{r.get('name')} ({r.get('kind')}, {_units(r['ru_start'], r.get('ru_height'))}) is not checked: the video cannot tell this kind apart.")
        else:
            wanted.append(r)
    pairs = []
    for tol in (0, 1):
        for r in [w for w in wanted if not any(p[0] is w for p in pairs)]:
            hit = next((s for s in free if abs(s["ru_start"] - r["ru_start"]) <= tol), None)
            if hit:
                free.remove(hit); pairs.append((r, hit))
    for r in wanted:
        if not any(p[0] is r for p in pairs):
            where = _units(r["ru_start"], r.get("ru_height"))
            extra = f" The video covers U1 to U{top} only." if top and r["ru_start"] > top else ""
            rows.append({"type": "missing", "what": "Missing device", "unit": where, "device": r.get("name"), "record": f"{r.get('kind') or 'Device'} at {where}", "read": "nothing seen",
                         "words": f"The record has {r.get('name')} at {where}; the video shows no device there.{extra}"})
    for s in free:
        where = _units(s["ru_start"], s["ru_height"])
        rows.append({"type": "extra", "what": "Extra device", "unit": where, "device": f"D{s['index']}", "record": "nothing", "read": f"{s['kind'] or 'a device'} at {where}",
                     "words": f"The video shows {s['kind'] or 'a device'} (D{s['index']}) at {where}; the record has nothing there."})
    for r, s in pairs:
        name, rec_at, read_at = r.get("name"), _units(r["ru_start"], r.get("ru_height")), _units(s["ru_start"], s["ru_height"])
        base = {"unit": read_at, "device": f"{name} / D{s['index']}"}
        if s["ru_start"] != r["ru_start"]:
            rows.append({**base, "type": "moved", "what": "Moved", "record": rec_at, "read": read_at, "words": f"{name} is at {rec_at} in the record and at {read_at} in the video."})
        if r.get("ru_height") and s["ru_height"] != r["ru_height"]:
            rows.append({**base, "type": "height", "what": "Wrong height", "record": f"{r['ru_height']}U", "read": f"{s['ru_height']}U",
                         "words": f"{name} is {r['ru_height']}U in the record; the video shows {s['ru_height']}U."})
        if s["kind"] and r.get("kind") and _norm(s["kind"]) != _norm(r["kind"]):
            rows.append({**base, "type": "kind", "what": "Wrong kind", "record": r["kind"], "read": s["kind"], "words": f"{name} is a {r['kind']} in the record; the video reads a {s['kind']}."})
        if s["manufacturer"] and r.get("manufacturer") and not _same(s["manufacturer"], r["manufacturer"]):
            rows.append({**base, "type": "make", "what": "Wrong make", "record": r["manufacturer"], "read": s["manufacturer"],
                         "words": f"{name} is made by {r['manufacturer']} in the record; the video reads {s['manufacturer']}."})
        if s["model"] and r.get("model") and not _same(s["model"], r["model"]):
            rows.append({**base, "type": "model", "what": "Wrong model", "record": r["model"], "read": s["model"], "words": f"{name} is a {r['model']} in the record; the video reads {s['model']}."})
        if s["serial"] and r.get("serial") and _norm(s["serial"]) != _norm(r["serial"]):
            rows.append({**base, "type": "serial", "what": "Wrong serial", "record": r["serial"], "read": s["serial"], "words": f"{name} has serial {r['serial']} in the record; the video reads {s['serial']}."})
        want = (ports or {}).get(r.get("device_key")) or {}
        if want:
            pr, n = port_drift(name, s, want)
            rows += pr; ports_checked += n
            if not n:
                notes.append(f"{name} has {len(want)} ports in PORT_INVENTORY but the video read none of its ports, so they are not checked.")
    rows.sort(key=lambda x: (-(_unum(x["unit"]) or 0), x.get("port") or 0))
    return {"rows": rows, "checked": len(pairs), "record_devices": len(record), "video_devices": len(seen), "notes": notes, "ports_checked": ports_checked}
