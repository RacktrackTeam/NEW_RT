"""source_db.py - the site's database copied into ours, the rack match and the drift for a video run.

Three sources fill one table set in data/sources.db (racks and devices, each row with its source and import time):
  excel   the manager's source-of-truth workbook (RT-EOS column names: RACK_INVENTORY, DEVICE_INVENTORY), uploaded
  netbox  NetBox's REST API, read from the local NetBox mock (modules/sources/mock/netbox.js)
  cmdb    ServiceNow's Table API, read from the local ServiceNow mock (modules/sources/mock/servicenow.js)
Nothing is ever written back to a source. Nothing here calls an outside system.
"""
import contextlib
import contextvars
import datetime
import json
import os
import pathlib
import re
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = pathlib.Path(os.environ.get("RACKTRACK_DATA") or ROOT / "data")   # the runtime data root: a test engine points it at its own copy
DB = pathlib.Path(os.environ.get("RACKTRACK_SOURCES_DB") or DATA / "sources.db")
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
CREATE TABLE IF NOT EXISTS src_confirmed (org TEXT, rack_label TEXT, source TEXT, rack_key TEXT, by TEXT, on_ TEXT, PRIMARY KEY (org, rack_label, source));
CREATE TABLE IF NOT EXISTS src_corrections (org TEXT, rack_label TEXT, ru_start INTEGER, kind TEXT, vendor TEXT, model TEXT, by TEXT, on_ TEXT,
  PRIMARY KEY (org, rack_label, ru_start));
"""
ACTIVE = "copy_id = (SELECT MAX(x.copy_id) FROM src_racks x WHERE x.source = {t}.source AND x.rack_key = {t}.rack_key)"


# racktrack-app sign-in: each organization has its own copies. The engine sets ORG for the request being served
# (server/app.py); with no organization set (tests, scripts) the one shared file is used, as before.
ORG = contextvars.ContextVar("source_db_org", default=None)


def db_path():
    org = ORG.get()
    return DB if org is None else DB.parent / "sources_by_org" / re.sub(r"[^A-Za-z0-9_-]", "_", str(org)) / DB.name


@contextlib.contextmanager
def _db():
    """The organization's copy database for one block: committed when the block ends without an error, always closed."""
    c = _open()
    try:
        with c:
            yield c
    finally:
        c.close()


def _open():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path); c.row_factory = sqlite3.Row
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='src_racks'").fetchone() and \
            "copy_id" not in [r[1] for r in c.execute("PRAGMA table_info(src_racks)")]:
        c.executescript("DROP TABLE src_racks; DROP TABLE IF EXISTS src_devices;")   # the first layout had no copies: its rows are copied in again
    c.executescript(SCHEMA)
    have = [r[1] for r in c.execute("PRAGMA table_info(src_racks)")]
    for col, ty in (("site", "TEXT"), ("lat", "REAL"), ("lng", "REAL")):          # the site and its position, for the location step
        if col not in have:
            c.execute(f"ALTER TABLE src_racks ADD COLUMN {col} {ty}")
    return c


def _int(v):
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _port_no(v):
    """The port number in a port name: the last run of digits (Eth25 -> 25, 1/0/25 -> 25)."""
    m = re.search(r"(\d+)(?!.*\d)", str(v or ""))
    return int(m.group(1)) if m else None


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
            c.execute("INSERT OR REPLACE INTO src_racks (copy_id, source, rack_key, name, label, height_ru, imported_at, file, site, lat, lng) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (cid, source, r["rack_key"], r.get("name"), r.get("label"), r.get("height_ru"), now, file, r.get("site"), _num(r.get("lat")), _num(r.get("lng"))))
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
    import rt_features as RTF                                    # wt/features: its cables go with it
    RTF.delete_cables(cid)
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
    import rt_features as RTF                                    # wt/features: the A_Z_CONNECTION_MATRIX rows kept with the copy
    out["cables"] = RTF.store_cables(out["copy_id"], path)
    return out


NETBOX_URL = os.environ.get("RACKTRACK_NETBOX_URL", "http://127.0.0.1:3303")          # the local NetBox mock (modules/sources/mock/netbox.js)
SERVICENOW_URL = os.environ.get("RACKTRACK_SERVICENOW_URL", "http://127.0.0.1:3304")  # the local ServiceNow mock (modules/sources/mock/servicenow.js)
CMDB_KIND = {"cmdb_ci_ip_switch": "Switch", "cmdb_ci_netgear": "Patch Panel", "cmdb_ci_server": "Server", "cmdb_ci_firewall_network": "Firewall",
             "cmdb_ci_router": "Router", "cmdb_ci_pdu": "PDU", "cmdb_ci_storage_device": "Storage unit"}


def _get(base, path, headers=None):
    """GET a JSON page from a local mock. Only this machine is ever called: never a real NetBox or ServiceNow."""
    import urllib.parse, urllib.request
    host = urllib.parse.urlparse(base).hostname
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("Only the local test copies are read today, never a real system.")
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


# NetBox interface and port types (dcim choices) and ServiceNow port types to the PORT_INVENTORY words the port compare reads
# (port_family: a read "sfp" fits SFP, SFP+, SFP28; "rj45" fits RJ45).
def _port_word(t):
    v = str(t or "").lower()
    if not v:
        return None
    if "qsfp" in v:
        return "QSFP28" if "28" in v or "100g" in v else "QSFP+"
    if "sfp" in v or v.endswith("-x") or "base-x" in v:
        return "SFP+" if ("sfpp" in v or "sfp+" in v or v.startswith("10g")) else "SFP"
    if "base-t" in v or v in ("8p8c", "rj45", "rj-45") or "rj45" in v:
        return "RJ45"
    if v in ("lc", "lc-upc", "lc-apc", "sc", "sc-upc", "mpo", "st", "fc"):
        return v.split("-")[0].upper()
    if "usb" in v:
        return "USB"
    if "console" in v or "rj-11" in v:
        return "Console"
    return str(t)


def _cabled(p):
    """Whether NetBox says a port has a cable: Yes or No when the answer carries the cable field, else not known."""
    if "cable" not in p and "mark_connected" not in p:
        return None
    return "Yes" if (p.get("cable") or p.get("mark_connected")) else "No"


def _netbox_end(terms):
    """The first termination of one end of a NetBox cable: (device name, port name)."""
    for t in terms or []:
        o = t.get("object") or t
        dev = (o.get("device") or {}).get("name")
        if dev:
            return dev, o.get("name")
    return None, None


def import_netbox(org=None):
    """NetBox's REST API (dcim/racks, dcim/devices, the interfaces, front and rear ports, and dcim/cables), through the
    organization's saved login, else the local NetBox mock."""
    base, hd = _login(org, "netbox", NETBOX_URL)
    rk = _get(base, "/api/dcim/racks/?limit=1000", hd)["results"]
    sites = {x["id"]: x for x in _get(base, "/api/dcim/sites/?limit=1000", hd)["results"]}
    racks = [{"rack_key": r["name"], "name": r["name"], "label": r.get("facility_id") or r["name"], "height_ru": _int(r.get("u_height")),
              "site": (r.get("site") or {}).get("name"), "lat": sites.get((r.get("site") or {}).get("id"), {}).get("latitude"),
              "lng": sites.get((r.get("site") or {}).get("id"), {}).get("longitude")} for r in rk]
    devices = []
    for d in _get(base, "/api/dcim/devices/?limit=10000", hd)["results"]:
        if not d.get("rack"):
            continue
        ty = d.get("device_type") or {}
        devices.append({"rack_key": d["rack"]["name"], "device_key": d["name"], "name": d["name"], "role": (d.get("role") or {}).get("name"),
                        "manufacturer": _s((ty.get("manufacturer") or {}).get("name")), "model": _s(ty.get("model")), "serial": _s(d.get("serial")),
                        "asset_tag": _s(d.get("asset_tag")), "ru_start": _int(d.get("position")), "ru_height": _int(ty.get("u_height"))})
    rack_of = {d["device_key"]: d["rack_key"] for d in devices}
    ports = []
    for path in ("interfaces", "front-ports", "rear-ports"):
        for p in _get(base, f"/api/dcim/{path}/?limit=100000", hd)["results"]:
            dev = (p.get("device") or {}).get("name")
            if dev not in rack_of:
                continue
            ports.append({"rack_key": rack_of[dev], "device_key": dev, "port_number": _port_no(p.get("name")), "port_name": _s(p.get("name")),
                          "port_type": _port_word((p.get("type") or {}).get("value") if isinstance(p.get("type"), dict) else p.get("type")),
                          "expected_occupied": _cabled(p), "expected_dust_cap": None})
    cables = []
    for n, c in enumerate(_get(base, "/api/dcim/cables/?limit=100000", hd)["results"], start=1):
        (ad, ap), (zd, zp) = _netbox_end(c.get("a_terminations")), _netbox_end(c.get("b_terminations"))
        if ad in rack_of or zd in rack_of:
            cables.append({"row_no": n, "cable_id": _s(c.get("id")), "cable_label": _s(c.get("label")) or _s(c.get("id")), "a_rack": rack_of.get(ad),
                           "a_device": ad, "a_port": _s(ap), "z_rack": rack_of.get(zd), "z_device": zd, "z_port": _s(zp),
                           "cable_type": _s((c.get("type") or {}).get("value") if isinstance(c.get("type"), dict) else c.get("type")),
                           "cable_color": _s(c.get("color"))})
    out = _store("netbox", racks, devices, base, ports)
    import rt_features as RTF
    out["cables"] = RTF.store_source_cables(out["copy_id"], "netbox", cables)
    return out


def import_cmdb(org=None):
    """ServiceNow's Table API (cmdb_ci_rack, cmdb_rel_ci and the device classes in cmdb_ci), through the saved login, else the local mock."""
    base, hd = _login(org, "cmdb", SERVICENOW_URL)
    rk = _get(base, "/api/now/table/cmdb_ci_rack?sysparm_limit=10000", hd)["result"]
    try:
        locs = {x["name"]: x for x in _get(base, "/api/now/table/cmn_location?sysparm_limit=10000", hd)["result"]}
    except ConnectionError:
        locs = {}
    loc = lambda r: (r.get("location") or {}).get("display_value") or (r.get("location") or {}).get("value") if isinstance(r.get("location"), dict) else r.get("location")  # noqa: E731
    racks = [{"rack_key": r["name"], "name": r["name"], "label": r["name"], "height_ru": _int(r.get("rack_units")), "site": loc(r),
              "lat": (locs.get(loc(r)) or {}).get("latitude"), "lng": (locs.get(loc(r)) or {}).get("longitude")} for r in rk]
    rack_of = {r["sys_id"]: r["name"] for r in rk}
    rels = _get(base, "/api/now/table/cmdb_rel_ci?sysparm_limit=100000", hd)["result"]
    ref = lambda v: (v or {}).get("value") if isinstance(v, dict) else v  # noqa: E731
    child_rack = {ref(x["child"]): rack_of[ref(x["parent"])] for x in rels if ref(x["parent"]) in rack_of}
    devices = []
    for d in _get(base, "/api/now/table/cmdb_ci?sysparm_limit=100000&sysparm_display_value=true", hd)["result"]:
        if d.get("sys_id") not in child_rack:
            continue
        devices.append({"rack_key": child_rack[d["sys_id"]], "device_key": d["name"], "name": d["name"], "role": _s(d.get("short_description")),
                        "kind": CMDB_KIND.get(d.get("sys_class_name")) or _kind(d.get("short_description")), "manufacturer": _s(d.get("manufacturer")),
                        "model": _s(d.get("model_id")), "serial": _s(d.get("serial_number")), "asset_tag": _s(d.get("asset_tag")),
                        "ru_start": _int(d.get("u_position")), "ru_height": _int(d.get("u_height"))})
    # ports: cmdb_ci_network_adapter (switches, routers, servers) and cmdb_ci_port (patch panels), each naming its device
    dev_of = {}                                       # device sys_id -> (rack, name)
    for d in _get(base, "/api/now/table/cmdb_ci?sysparm_limit=100000", hd)["result"]:
        if d.get("sys_id") in child_rack:
            dev_of[d["sys_id"]] = (child_rack[d["sys_id"]], d["name"])
    ports, port_of = [], {}
    for table in ("cmdb_ci_network_adapter", "cmdb_ci_port"):
        try:
            rows = _get(base, f"/api/now/table/{table}?sysparm_limit=100000", hd)["result"]
        except ConnectionError:
            rows = []
        for p in rows:
            hit = dev_of.get(ref(p.get("cmdb_ci")))
            if hit is None:
                continue
            port_of[p.get("sys_id")] = (hit[0], hit[1], _s(p.get("name")))
            ports.append({"rack_key": hit[0], "device_key": hit[1], "port_number": _int(p.get("u_port_number")) or _port_no(p.get("name")),
                          "port_name": _s(p.get("name")), "port_type": _port_word(p.get("u_port_type")),
                          "expected_occupied": _yes_no(p.get("u_expected_occupied")) if p.get("u_expected_occupied") not in (None, "") else None,
                          "expected_dust_cap": None})
    # cables: a cmdb_rel_ci between two ports (Connects to / Connected by)
    cables = []
    for n, x in enumerate(rels, start=1):
        a, z = port_of.get(ref(x.get("parent"))), port_of.get(ref(x.get("child")))
        if a and z:
            cables.append({"row_no": n, "cable_id": _s(x.get("sys_id")), "cable_label": _s(x.get("u_cable_label")) or _s(x.get("sys_id")),
                           "a_rack": a[0], "a_device": a[1], "a_port": a[2], "z_rack": z[0], "z_device": z[1], "z_port": z[2],
                           "cable_type": _s(x.get("u_cable_type")), "cable_color": None})
    out = _store("cmdb", racks, devices, base, ports)
    import rt_features as RTF
    out["cables"] = RTF.store_source_cables(out["copy_id"], "cmdb", cables)
    return out


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


def _day(iso):
    try:
        return datetime.datetime.fromisoformat(str(iso)[:19]).strftime("%-d %b %Y")
    except ValueError:
        return None


def device_kind(d, when=None):
    """The one kind of a device everywhere in the app (Devices list, device page, rack view, report, drift, the JSON routes), in this
    order: a person's correction, then the kind the port step used (layout.kind), then the classifier (the picture). The words say
    where it came from truly: "from the faceplate" only when a layout was fitted or a make and model were read; otherwise the layout's
    kind is the one recorded when its ports were mapped (`when`, the run's date); every other source that disagrees is said.
    Returns {"kind", "from" (corrected, faceplate, ports, picture or None), "picture" (the classifier's label), "words"}."""
    cl = (d or {}).get("classification") or {}
    pic = cl.get("label") or (d or {}).get("type")
    pic_known = pic if pic not in (None, "", "Other", "Not sure") else None
    prob = cl.get("prob")
    pic_words = f"from the picture ({round(100 * prob)} %)" if prob is not None else "from the picture"
    ov = ((d or {}).get("kind_override") or {})
    L = (d or {}).get("layout") or {}
    lk = layout_kind(L)
    read = bool(L.get("fitted") or (L.get("vendor") and L.get("model")))     # a text read alone (vendor_read, model_read) is not the faceplate
    lk_words = None
    if lk:
        day = _day(when) if when else None
        lk_words = "from the faceplate" if read else "as read when its ports were mapped" + (f" on {day}" if day else "")
    if ov.get("kind"):
        kind, src, words = ov["kind"], "corrected", f"{ov['kind']}, your correction" + (f" ({ov['by']})" if ov.get("by") else "")
    elif lk:
        kind, src, words = lk, "faceplate" if read else "ports", f"{lk}, {lk_words}"
    elif pic_known and not ((d or {}).get("unsure") and (prob or 0) < 0.7):
        return {"kind": pic_known, "from": "picture", "picture": pic, "words": f"{pic_known}, {pic_words}"}
    else:
        return {"kind": None, "from": None, "picture": pic, "words": "Not sure: the picture could not tell" + (f" (best guess {pic})" if pic_known else "")}
    if src == "corrected" and lk and _norm(lk) != _norm(kind):
        words += f"; {lk} {lk_words}"
    if pic_known and _norm(pic_known) != _norm(kind):
        words += f"; {pic_known} {pic_words}"
    elif not pic_known and cl.get("class"):
        words += f"; the picture was not sure ({cl['class']} {round(100 * (prob or 0))} %)"
    return {"kind": kind, "from": src, "picture": pic, "words": words}


def run_devices(result):
    """The run's devices as start unit, height, kind, make, model and serial (None where not read)."""
    out, ubox = [], unit_boxes(result)
    for d in (result or {}).get("devices") or []:
        nums = [n for n in (_unum(u) for u in d.get("units") or []) if n is not None]
        if not nums:
            continue
        L = d.get("layout") or {}
        kind = device_kind(d)["kind"]
        out.append({"index": d.get("index"), "ru_start": min(nums), "ru_height": len(nums), "kind": kind,
                    "manufacturer": _s(L.get("vendor") or (L.get("typed") or {}).get("vendor")), "model": _s(L.get("model") or (L.get("typed") or {}).get("model")), "serial": _s(L.get("serial") or d.get("serial")), "ports": run_ports(d, ubox),
                    "asset_tag": _s(d.get("asset_tag") or (d.get("ids") or {}).get("asset_tag"))})     # wt/features: the asset tag a close-up read
    return out


def units_read(result):
    """How many units the video counted: the ladder's count (what the Rack, Layout and Report tabs show), else the highest unit read."""
    r = result or {}
    n = _int((r.get("ladder") or {}).get("units"))
    if n:
        return n
    return max([_unum(u.get("unit")) or 0 for u in ((r.get("occupancy_frames") or {}).get("rows") or [])] + [0])


def unit_boxes(result):
    """{unit: box on the rack picture} from the run's unit rows, for screen_ports."""
    return {u["unit"]: u["box"] for u in (((result or {}).get("occupancy_frames") or {}).get("rows") or []) if u.get("box")}


def screen_ports(d, ubox=None):
    """The ONE reading of a device's ports that every screen uses (the device page, the drift, the rack tools, the physical layer):
    a close-up first, the fitted layout's (or the agent's auto mode's) ports, else the port details; a port boxed outside the
    device's own outline or rows belongs to the neighbour and is dropped (user 2026-10-27); the state is RT-EOS's
    port_connected_or_empty from the port details first (user 2026-10-27), else the layout's own when it is connected or empty;
    the cable and the dust cap come from the port details. Returns {"ports", "src", "used", "used_c", "pd", "dropped"}; ubox is
    unit_boxes(result) (no neighbour check without it)."""
    C, L = d.get("closeup") or {}, d.get("layout") or {}
    used_c = bool(C.get("fitted") or C.get("auto")); src = C if used_c else L
    used = bool(src.get("fitted") or src.get("auto") or src.get("mode") == "patchpanel")
    pd = (C.get("port_details") if used_c else None) or L.get("port_details") or {}
    raw = (src.get("ports") or []) if used else (pd.get("ports") or [])
    ubox = ubox or {}
    rows_y = [ubox[u] for u in (d.get("units") or []) if u in ubox]           # the device's own unit rows (its polygon can leak into the next unit)
    poly = d.get("polygon") or []
    if len(poly) >= 3 and raw and any(p.get("rack_polygon") for p in raw):   # inside the device's own outline, grown 30 % up and down (a tilted
        import numpy as _np; import cv2                                       # device's port row slopes across the unit rows: 29 Oct 2026)
        cy = sum(q[1] for q in poly) / len(poly)
        grown = _np.array([[float(q[0]), cy + (float(q[1]) - cy) * 1.6] for q in poly], dtype=_np.float32)
        inside = lambda p: cv2.pointPolygonTest(grown, (float(sum(q[0] for q in p["rack_polygon"]) / len(p["rack_polygon"])), float(sum(q[1] for q in p["rack_polygon"]) / len(p["rack_polygon"]))), False) >= 0
        keep = [p for p in raw if not p.get("rack_polygon") or inside(p)]
    elif rows_y and raw and any(p.get("rack_polygon") for p in raw):
        y0, y1 = min(b[1] for b in rows_y), max(b[3] for b in rows_y); inset = 0.1 * (y1 - y0) / len(rows_y)
        keep = [p for p in raw if not p.get("rack_polygon") or (y0 + inset <= sum(q[1] for q in p["rack_polygon"]) / len(p["rack_polygon"]) <= y1 - inset)]
    else:
        keep = raw
    dropped = len(keep) < len(raw)
    renumber = bool(src.get("auto")) and dropped                          # auto mode after a drop: the numbered ports count on without gaps
    if dropped:
        raw = keep; src = dict(src, pictures={k: v for k, v in (src.get("pictures") or {}).items() if k == "cut"})   # the mapper's picture still draws the neighbour's ports
        if len(keep) < 2 and src.get("auto"):                # (almost) nothing of its own was found: no auto layout, the crop only
            used = False; src = dict(src, auto=False, fitted=False); raw = []
    known = {p.get("id"): p.get("status") for p in (pd.get("ports") or []) if p.get("status") in ("connected", "empty")}
    detail = {p.get("id"): p for p in (pd.get("ports") or [])}
    ports = []
    for p in raw:
        e = detail.get(p.get("id")) or {}
        ports.append({"id": p.get("id"), "type": p.get("type"), "status": known.get(p.get("id"), p.get("status") if p.get("status") in ("connected", "empty") else None),
                      "cable_type": p.get("cable_type") or e.get("cable_type"), "cable_colour": p.get("cable_colour") or e.get("cable_colour"),
                      "cable_connector": p.get("cable_connector") or e.get("cable_connector"), "polygon": p.get("polygon"), "rack_polygon": p.get("rack_polygon"),
                      "cap_state": e.get("cap_state") or p.get("cap_state"), "cable_views": e.get("cable_views"), "state_by": p.get("state_by") or e.get("state_by"),
                      "read_id": p.get("id")})
    if renumber and used:
        n = 0
        for q in ports:
            if isinstance(q.get("id"), int):
                n += 1; q["id"] = n
    combo = _combo_faces(src, ports) if src.get("fitted") else []
    return {"ports": [q for q in ports if not q.get("combo_of")], "combo": combo, "src": src, "used": used, "used_c": used_c, "pd": pd, "dropped": dropped}


def _combo_faces(src, ports):
    """Combo ports: a model named for its port count (D-Link DGS-1210-52: 52 ports, of which the last 4 are combo) whose template
    lists that many RJ45 ports AND the SFP cages numbered after them has counted the combo ports twice (56). The cages are the
    second face of the last RJ45 ports: they are named 49F to 52F, marked combo_of, and left out of the port count."""
    m = re.search(r"-(\d{2})[A-Z]{0,3}$", str(src.get("model") or "").strip(), re.I)
    if not m:
        return []
    n = int(m.group(1))
    rj = [q for q in ports if str(q.get("type") or "").lower() == "rj45" and isinstance(q.get("id"), int)]
    cages = sorted((q for q in ports if str(q.get("type") or "").lower().startswith("sfp") and isinstance(q.get("id"), int)), key=lambda q: q["id"])
    if not cages or len(rj) != n or max(q["id"] for q in rj) != n or any(q["id"] <= n for q in cages) or len(cages) >= n:
        return []
    for k, q in enumerate(cages):
        q["combo_of"] = n - len(cages) + 1 + k; q["id"] = f"{q['combo_of']}F"
    return cages


def run_ports(d, ubox=None):
    """The device's ports as the result screen shows them (screen_ports), each as number, type, state and the dust cap where the cap
    model read one."""
    out, sp = [], screen_ports(d, ubox)
    for p in sp["ports"]:
        n = _int(p.get("id")) if str(p.get("id") or "").strip().isdigit() else None
        if n is not None:
            out.append({"n": n, "type": _s(p.get("type")), "state": p.get("status"), "cap": p.get("cap_state")})
    for p in sp["combo"]:                                  # a combo port's SFP face: the same number, kept for SFP advice, never judged twice
        out.append({"n": p["combo_of"], "type": _s(p.get("type")), "state": p.get("status"), "cap": p.get("cap_state"), "combo": True})
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
        if not x or p.get("combo"):
            continue
        checked += 1
        base = {"unit": at, "device": f"{name} / D{dev['index']}", "port": p["n"]}
        wf, gf = port_family(x.get("port_type")), port_family(p["type"])
        if wf and gf and wf != gf:
            rows.append({**base, "type": "port_type", "what": "Wrong port type", "record": x["port_type"], "read": p["type"].upper(),
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
    top = units_read(result) or None                      # the units the video counted (the ladder), as the Rack tab shows them
    free = list(seen)
    wanted = []
    for r in record:
        if r.get("ru_start") is None:
            notes.append(f"{r.get('name')} has no unit in the record, so it is not checked.")
        elif top and r["ru_start"] > top:                 # above the units the video reached: not seen, so not judged
            notes.append(f"{r.get('name')} ({_units(r['ru_start'], r.get('ru_height'))}) is not checked: the video read U1 to U{top} only.")
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
            rows.append({"type": "missing", "what": "Missing device", "unit": where, "device": r.get("name"), "record_name": r.get("name"), "record": f"{r.get('kind') or 'Device'} at {where}", "read": "nothing seen",
                         "words": f"The record has {r.get('name')} at {where}; the video shows no device there."})
    for s in free:
        where = _units(s["ru_start"], s["ru_height"])
        rows.append({"type": "extra", "what": "Extra device", "index": s["index"], "unit": where, "device": f"D{s['index']}", "record": "nothing", "read": f"{s['kind'] or 'a device'} at {where}",
                     "words": f"The video shows {s['kind'] or 'a device'} (D{s['index']}) at {where}; the record has nothing there."})
    for r, s in pairs:
        name, rec_at, read_at = r.get("name"), _units(r["ru_start"], r.get("ru_height")), _units(s["ru_start"], s["ru_height"])
        base = {"unit": read_at, "device": f"{name} / D{s['index']}", "index": s["index"], "record_name": name}
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
        # ---- wt/features: the asset tag read on a close-up against the record's (RT-EOS wrong_asset_tag)
        if s.get("asset_tag") and r.get("asset_tag") and _norm(s["asset_tag"]) != _norm(r["asset_tag"]):
            rows.append({**base, "type": "asset_tag", "what": "Wrong asset tag", "record": r["asset_tag"], "read": s["asset_tag"],
                         "words": f"{name} has asset tag {r['asset_tag']} in the record; the close-up reads {s['asset_tag']}."})
        want = (ports or {}).get(r.get("device_key")) or {}
        if want:
            pr, n = port_drift(name, s, want)
            rows += pr; ports_checked += n
            if not n and s.get("ports"):
                k = len(s["ports"])
                notes.append(f"{name} has {len(want)} ports in PORT_INVENTORY; the video read {k} port{'s' if k != 1 else ''} of it, "
                             f"none numbered as in the record, so its ports are not checked.")
            elif not n:
                notes.append(f"{name} has {len(want)} ports in PORT_INVENTORY but the video read none of its ports, so they are not checked.")
    rows.sort(key=lambda x: (-(_unum(x["unit"]) or 0), x.get("port") or 0))
    return {"rows": rows, "checked": len(pairs), "record_devices": len(record), "video_devices": len(seen), "notes": notes, "ports_checked": ports_checked}


# ------------------------------------------------------------------ naming the rack: the ladder, strongest evidence first
# The rungs and thresholds of the rack identity ladders in rt_eos/server/lib/rack_identity.js and vendor/dark_mobile/server/lib/rack_identity.js
# (identifyFromEvidence: a location narrows, a label names, the devices rung needs MIN_DEVICES, a MIN_SHARE and no rack within TIE_MARGIN).
# Each step leaves a set of racks; the first step that leaves exactly one names the rack; the later steps only agree or disagree.
MIN_DEVICES, MIN_SHARE, TIE_MARGIN, NEAR_M, VERIFY_AT = 3, 0.6, 0.1, 300, 0.7


def _metres(a, b):
    import math
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(h))


def _share_rung(seen, rows, fits, whole=None):
    """dark_mobile's devicesRung on a list of read things: the share each rack holds, the rack it decides (or None) and why.
    whole(r), when given, pairs every read thing of one rack at once (one record device per read thing) and returns the pairs."""
    ranked = sorted(({"rack_key": r["rack_key"], "which": whole(r) if whole else [w for w in (fits(x, r) for x in seen) if w]} for r in rows), key=lambda x: -len(x["which"]))
    for x in ranked:
        x["fits"] = len(x["which"]); x["share"] = round(x["fits"] / len(seen), 2) if seen else 0.0
    top, second = (ranked + [None, None])[:2]
    tied = bool(top and second and top["share"] - second["share"] < TIE_MARGIN)
    decides = bool(top and len(seen) >= MIN_DEVICES and top["share"] >= MIN_SHARE and not tied)
    return ranked, (top["rack_key"] if decides else None), tied


def identify(source, rack_id, result, position=None, label=None, rack_id_how=None, confirmed=None, sites=None):
    """The rack for a run in one source's copy, step by step. Returns {"steps": [...], "rack_key", "state", "words"}; each step has a
    title, its proof in words, the racks it leaves and its outcome."""
    rows, steps = racks(source), []
    # 0 a person confirmed the record for this rack label before (this run or an earlier one)
    if confirmed and any(r["rack_key"] == confirmed["rack_key"] for r in rows):
        steps.append({"n": 0, "title": "Confirmed before", "proof": f"Confirmed by {confirmed['by']} on {_day(confirmed['on_']) or str(confirmed['on_'])[:10]}: the rack labelled {rack_id} is the record {confirmed['rack_key']}.",
                      "left": [confirmed["rack_key"]], "outcome": "one"})
    # 1 location: the run's phone position against each rack's site position
    # ---- wt/features: a rack with no position of its own takes its site's (the sign-in service's sites, by name, else the run's own
    # site); the radius and the accuracy rule are location.js's (300 m, or twice the phone's accuracy; a reading worse than 2 km says nothing)
    import rt_features as RTF
    own, others = sites or (None, [])
    by_name = {_norm(x["name"]): x for x in ([own] if own else []) + list(others) if x and x.get("lat") is not None}
    for r in rows:
        if r.get("lat") is None:
            st = by_name.get(_norm(r.get("site"))) or (own if own and own.get("lat") is not None else None)
            if st:
                r["lat"], r["lng"], r["pos_from"] = st["lat"], st["lng"], f"site {st['name']}"
    acc = (position or {}).get("accuracy")
    verdict = RTF.judge(position, own, others) if position and position.get("lat") is not None and own else None
    placed = [r for r in rows if r.get("lat") is not None and r.get("lng") is not None]
    if not position or position.get("lat") is None:
        steps.append({"n": 1, "title": "Location", "proof": "No position was sent with this run.", "left": None, "outcome": "skipped"})
    elif acc is not None and float(acc) > RTF.MAX_USEFUL_ACCURACY_M:
        steps.append({"n": 1, "title": "Location", "proof": f"The phone's location was only good to {RTF.said(float(acc))}, too rough to say which site this is.", "left": None, "outcome": "skipped"})
    elif not placed:
        steps.append({"n": 1, "title": "Location", "proof": "No rack in this copy and no site has a position." + (f" {verdict['note']}" if verdict else ""), "left": None, "outcome": "skipped"})
    else:
        here = (float(position["lat"]), float(position["lng"]))
        radius = max(NEAR_M, 2 * float(acc or 0))
        near = [r for r in placed if _metres(here, (r["lat"], r["lng"])) <= radius]
        rows = near or rows
        steps.append({"n": 1, "title": "Location", "proof": f"{pl(len(near), 'rack')} within {radius:.0f} m of the phone's position" + (f" (accuracy {float(acc):.0f} m)" if acc is not None else "")
                      + ("" if near else "; none near, so every rack stays in") + (f". {verdict['note']}" if verdict else ""),
                      "left": [r["rack_key"] for r in rows], "outcome": "one" if len(near) == 1 else "narrowed" if near else "none", "verdict": verdict})
    keys = {r["rack_key"] for r in rows}
    # 2 the rack id or label read from the video, exact, then separators ignored
    m = match(source, rack_id) if rack_id else None
    m = m if m and m["rack_key"] in keys else None
    lab = (label or {}).get("id") or (label or {}).get("name") or {}
    seen_txt = f'"{lab["text"]}" read in {pl(lab.get("seen") or 0, "frame")}, confidence {round(100 * (lab.get("confidence") or 0))} %' if lab else "no label text read"
    steps.append({"n": 2, "title": "Rack label", "proof": f"Rack id {rack_id or 'not confirmed'} ({seen_txt}). " + (m["why"] if m else "No record in this copy has that id."),
                  "left": [m["rack_key"]] if m else None, "outcome": "one" if m else "none", "how": m["how"] if m else None})
    # 3 device names, serials and asset tags read on the devices; then the makes and models read on their faceplates
    seen = run_devices(result)
    ids = [x for s_ in seen for x in (s_.get("serial"), s_.get("asset_tag")) if x]
    models = [s_["model"] for s_ in seen if s_.get("model")]
    recs = {r["rack_key"]: devices(source, r["rack_key"]) for r in rows}
    things = ids + models
    def same_as(t, d):
        return any(_same(t, d.get(k)) for k in ("name", "serial", "asset_tag", "model") if d.get(k))
    def text_fits(r):
        """Each read device to one record device of this rack (two read devices never share one): among the record devices its
        serial, asset tag or model fits, the one nearest its unit. Each of its texts that fits that device counts once."""
        used, out = set(), []
        for s_ in seen:
            texts = [x for x in (s_.get("serial"), s_.get("asset_tag"), s_.get("model")) if x]
            hits = [d for d in recs[r["rack_key"]] if id(d) not in used and any(same_as(t, d) for t in texts)]
            if hits:
                d = min(hits, key=lambda d: abs((d.get("ru_start") or 0) - (s_.get("ru_start") or 0)))
                used.add(id(d)); out += [f"{d.get('name')} ({t})" for t in texts if same_as(t, d)]
        return out
    ranked3, one3, _ = _share_rung(things, rows, None, whole=text_fits)
    t3 = ranked3[0] if ranked3 else None
    steps.append({"n": 3, "title": "Device labels", "proof": ("Serial and asset tag: not read on any device. " if not ids else f"{pl(len(ids), 'serial or asset tag')} read. ")
                  + (f"{pl(len(models), 'model')} read on faceplates ({', '.join(models)}); {t3['fits']} of them are in {t3['rack_key']}." if models and t3 else "No model read on a faceplate."),
                  "left": [one3] if one3 else None, "outcome": "one" if one3 else "none", "ranked": ranked3[:3]})
    # 4 the rack height: the units the video counted cannot be more than the rack holds
    n_units = int(((result or {}).get("ladder") or {}).get("units") or 0)
    fits_h = [r for r in rows if r.get("height_ru") and r["height_ru"] >= n_units]
    if not n_units or not any(r.get("height_ru") for r in rows):
        steps.append({"n": 4, "title": "Rack height", "proof": "No unit count or no rack heights to compare.", "left": None, "outcome": "skipped"})
    else:
        steps.append({"n": 4, "title": "Rack height", "proof": f"The video counted {n_units} units; {pl(len(fits_h), 'rack')} in this copy can hold that many.",
                      "left": [r["rack_key"] for r in fits_h], "outcome": "narrowed" if fits_h else "none"})   # the height only narrows, it never names
    # 5 the rack number inside device labels read on the video (R1 in SP-R1-U17-SW03; rack_identity.js rackNumberInLabel and rackNumberOf)
    dev_labels = [x for d in ((result or {}).get("devices") or []) for x in (d.get("label_read"), (d.get("layout") or {}).get("label_read")) if x]   # device labels the OCR read (none kept by the runs today)
    nums = {n for n in (_rack_number_in_label(t) for t in dev_labels) if n is not None}
    if not dev_labels:
        steps.append({"n": 5, "title": "Rack number in device labels", "proof": "No device label was read in the video.", "left": None, "outcome": "skipped"})
    else:
        hit = [r for r in rows if len(nums) == 1 and next(iter(nums)) in (_rack_number_of(r.get("label")), _rack_number_of(r.get("name")))]
        steps.append({"n": 5, "title": "Rack number in device labels", "proof": f"Device labels read: {', '.join(dev_labels[:5])}; rack number {', '.join(map(str, sorted(nums))) or 'none'}.",
                      "left": [r["rack_key"] for r in hit] or None, "outcome": "one" if len(hit) == 1 else "narrowed" if hit else "none"})
    # 6 the layout: devices at their units with the same kind (and model where both are read), a last resort and a tie-breaker
    kinded = [x for x in seen if x["kind"]]
    def lay_fit(x, r):
        hit = next((d for d in recs[r["rack_key"]] if d.get("ru_start") == x["ru_start"] and d.get("kind") and _norm(d["kind"]) == _norm(x["kind"])
                    and not (x["model"] and d.get("model") and not _same(x["model"], d["model"]))), None)
        return f"{hit.get('name')} at U{x['ru_start']}" if hit else None
    ranked4, one4, tied4 = _share_rung(kinded, rows, lay_fit)
    steps.append({"n": 6, "title": "Layout", "proof": "; ".join(f"{x['rack_key']}: {x['fits']} of {len(kinded)}" for x in ranked4[:3]) + " devices at the same units with the same kind",
                  "left": [one4] if one4 else None, "outcome": "one" if one4 else "tied" if tied4 else "none", "ranked": ranked4[:3]})
    # the first step that leaves exactly one rack names it; the others agree or disagree
    first = next((st for st in steps if st["outcome"] == "one" and st["left"] and len(st["left"]) == 1), None)
    key = first["left"][0] if first else None
    others = [st for st in steps if st is not first and st["outcome"] == "one" and st["n"] > 1 and st["n"] != 4]   # the height only narrows; the location only narrows unless first
    against = [st for st in others if st["left"][0] != key]
    lay = next((x for x in ranked4 if x["rack_key"] == key), None)
    top = []
    if not key:                                  # no step named one rack: the top 3 by the steps that did fire, else ask
        left = set.intersection(*[set(st["left"]) for st in steps if st.get("left") and st["outcome"] in ("narrowed", "one")] or [keys]) or keys
        score = {k: 0.0 for k in left}
        for st in steps:
            for x in st.get("ranked") or []:
                if x["rack_key"] in score:
                    score[x["rack_key"]] += x["share"]
        for k in sorted(score, key=lambda k: -score[k])[:3]:
            why = [f"step {st['n']}: {x['fits']} of {len(kinded) if st['n'] == 6 else len(things)}" for st in steps for x in st.get("ranked") or [] if x["rack_key"] == k and x["fits"]]
            top.append({"rack_key": k, "why": "; ".join(why) or "left in by the steps above"})
        if top and any(score.values()):
            state, words = "pick", "No step named one rack. The three closest are below: pick one."
        else:
            top, state, words = [], "ask", "Which rack is this? No rack in this copy fits. Search the copy's racks, or type the rack's name."
    else:
        words = f"Step {first['n']} named one rack: {key}."
        for st in others:
            got = next((x for x in st.get("ranked") or [] if x["rack_key"] == key), None)
            n_of = f", {got['fits']} of {len(kinded) if st['n'] == 6 else len(things)}" if got else ""
            words += f" Step {st['n']} " + (f"agrees{n_of}." if st["left"][0] == key else f"points to {st['left'][0]}.")
        if lay and not any(st["n"] == 6 for st in others) and first["n"] != 6:
            words += f" Step 6: {lay['fits']} of {len(kinded)} devices fit."
    # each step's confidence that this is the rack, with its reason; the overall one as the RT-EOS ladder scores: the strongest step
    # decides, each agreeing step raises it (1 - (1 - a)(1 - b)), a step that points elsewhere lowers it
    overall = None
    if key:
        person = bool(rack_id_how) and any(w in str(rack_id_how).lower() for w in ("verified", "confirmed", "typed", "picked"))
        for st in steps:
            st["conf"], st["agrees"] = None, None
            if st["outcome"] in ("skipped",) or st["n"] == 4:
                continue
            if st["n"] == 0:
                st["conf"], st["agrees"], st["why"] = 1.0, True, st["proof"]
            elif st["n"] == 1 and st["left"]:
                inn = key in st["left"]; st["agrees"] = inn
                st["conf"] = (0.6 / len(st["left"])) if inn else 0.6
                st["why"] = f"{pl(len(st['left']), 'rack')} near the phone" + ("" if inn else f", and {key} is not one of them")
            elif st["n"] == 2 and st["left"]:
                c = 0.95 if person else max(0.5, float(lab.get("confidence") or 0.5)) if lab else 0.5
                st["conf"] = round(c * (1.0 if st.get("how") == "exact" else 0.9), 2); st["agrees"] = st["left"][0] == key
                st["why"] = (f"The label reader read {lab['text']}, {round(100 * float(lab.get('confidence') or 0))} % sure" if lab else "No label text was read") \
                    + (f"; a person confirmed it, so this step counts {round(100 * st['conf'])} %" if person else "") \
                    + (", exact match" if st.get("how") == "exact" else ", match with separators ignored") + ", no other rack has this label"
            elif st.get("ranked"):
                own = next((x for x in st["ranked"] if x["rack_key"] == key), None); nxt = next((x for x in st["ranked"] if x["rack_key"] != key), None)
                n_all = len(kinded) if st["n"] == 6 else len(things)
                if own and n_all:
                    st["conf"] = round(own["share"] * (1.0 if st["n"] == 6 else 0.8), 2)          # a model read is not unique to one rack
                    st["agrees"] = st["outcome"] != "one" or st["left"][0] == key
                    if not st["agrees"] and nxt:
                        st["conf"] = round(nxt["share"] * (1.0 if st["n"] == 6 else 0.8), 2)    # how strongly it points elsewhere
                    st["why"] = f"{own['fits']} of {n_all} " + ("devices at the same units" if st["n"] == 6 else "names read on the devices are in this rack") + (f"; the next best rack fits {nxt['fits']} of {n_all}" if nxt else "; no other rack to compare")
                    if st["agrees"] is False:
                        st["why"] = f"points to {st['left'][0]}: " + st["why"]
        lead = max([st for st in steps if st.get("conf") and st.get("agrees")], key=lambda st: st["conf"], default=None)
        if lead:
            miss = 1.0
            for st in steps:
                if st.get("conf") and st.get("agrees") and st["outcome"] == "one":
                    miss *= 1 - st["conf"]
            overall = 1 - min(miss, 1 - lead["conf"])
            for st in steps:
                if st.get("conf") and st.get("agrees") is False:
                    overall *= 1 - st["conf"]
            overall = round(1.0 if first["n"] == 0 and not against else min(overall, 0.99), 2)
        state = "verified" if overall is not None and overall >= VERIFY_AT and not against else "confirm"
        words += f" Verified, {round(100 * overall)} %." if state == "verified" else f" {round(100 * (overall or 0))} %: please confirm the record or pick another."
    return {"steps": steps, "rack_key": key, "state": state, "words": words, "named_by": first["n"] if first else None, "top": top, "confidence": overall}


def _rack_number_of(name):
    m = re.search(r"(?:\brack|r)[\s_-]?0*(\d+)\s*$", str(name or ""), re.I)
    return int(m.group(1)) if m else None


def _rack_number_in_label(text):
    found = [int(m.group(1)) for m in re.finditer(r"(?:^|[^A-Z0-9])R0*(\d{1,3})(?=$|[^0-9])", str(text or ""), re.I)]
    return found[0] if len(found) == 1 else None


def pl(n, w):
    return f"{n} {w}{'' if n == 1 else 's'}"


# ------------------------------------------------------------------ what people confirmed, kept for the next scan of the same rack
def confirm_rack(org, rack_label, source, rack_key, by):
    if not rack_label or not rack_key:
        return
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO src_confirmed VALUES (?,?,?,?,?,?)", (org, rack_label, source, rack_key, by, datetime.datetime.now().isoformat(timespec="minutes")))


def confirmed(org, rack_label, source):
    if not rack_label:
        return None
    with _db() as c:
        x = c.execute("SELECT * FROM src_confirmed WHERE org=? AND rack_label=? AND source=?", (org, rack_label, source)).fetchone()
        return dict(x) if x else None


def save_correction(org, rack_label, ru_start, by, **what):
    """A person's correction of a device's kind, make or model, kept by rack label and unit for the next run of that rack."""
    if not rack_label or ru_start is None:
        return
    with _db() as c:
        old = c.execute("SELECT * FROM src_corrections WHERE org=? AND rack_label=? AND ru_start=?", (org, rack_label, ru_start)).fetchone()
        row = dict(old) if old else {"kind": None, "vendor": None, "model": None}
        row.update({k: v for k, v in what.items() if k in ("kind", "vendor", "model")})
        c.execute("INSERT OR REPLACE INTO src_corrections VALUES (?,?,?,?,?,?,?,?)", (org, rack_label, ru_start, row["kind"], row["vendor"], row["model"], by,
                                                                                    datetime.datetime.now().isoformat(timespec="minutes")))


def with_corrections(org, rack_label, result):
    """The run with the corrections people made on earlier runs of the same rack, where this run has none of its own (a copy)."""
    if not rack_label or not result:
        return result
    with _db() as c:
        corr = {x["ru_start"]: dict(x) for x in c.execute("SELECT * FROM src_corrections WHERE org=? AND rack_label=?", (org, rack_label))}
    if not corr:
        return result
    out = dict(result, devices=[])
    for d in result.get("devices") or []:
        nums = [n for n in (_unum(u) for u in d.get("units") or []) if n is not None]
        k = corr.get(min(nums)) if nums else None
        if k:
            d = dict(d)
            if k["kind"] and not d.get("kind_override"):
                d["kind_override"] = {"kind": k["kind"], "by": f"{k['by']} (an earlier scan)", "when": k["on_"], "carried": True}
            if (k["vendor"] or k["model"]) and not ((d.get("layout") or {}).get("vendor") or (d.get("layout") or {}).get("typed")):
                d["layout"] = dict(d.get("layout") or {}, typed={"vendor": k["vendor"], "model": k["model"], "by": f"{k['by']} (an earlier scan)", "when": k["on_"]})
        out["devices"].append(d)
    return out
