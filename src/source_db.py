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
SCHEMA = """
CREATE TABLE IF NOT EXISTS src_racks (source TEXT, rack_key TEXT, name TEXT, label TEXT, height_ru INTEGER, imported_at TEXT, file TEXT,
  PRIMARY KEY (source, rack_key));
CREATE TABLE IF NOT EXISTS src_devices (source TEXT, rack_key TEXT, device_key TEXT, name TEXT, role TEXT, kind TEXT, manufacturer TEXT,
  model TEXT, serial TEXT, asset_tag TEXT, ru_start INTEGER, ru_height INTEGER, imported_at TEXT, PRIMARY KEY (source, rack_key, device_key));
"""


def _db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; c.executescript(SCHEMA)
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


def _store(source, racks, devices, file):
    """Replace this source's copy of these racks with the new rows."""
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with _db() as c:
        for r in racks:
            c.execute("DELETE FROM src_devices WHERE source=? AND rack_key=?", (source, r["rack_key"]))
            c.execute("INSERT OR REPLACE INTO src_racks VALUES (?,?,?,?,?,?,?)", (source, r["rack_key"], r.get("name"), r.get("label"), r.get("height_ru"), now, file))
        for d in devices:
            c.execute("INSERT OR REPLACE INTO src_devices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (source, d["rack_key"], d["device_key"], d.get("name"), d.get("role"), d.get("kind") or _kind(d.get("role")), d.get("manufacturer"), d.get("model"),
                       d.get("serial"), d.get("asset_tag"), d.get("ru_start"), d.get("ru_height"), now))
    return {"source": source, "racks": len(racks), "devices": len(devices), "imported_at": now, "file": file}


# ------------------------------------------------------------------ the three readers
def import_excel(path):
    """The manager's workbook: RACK_INVENTORY and DEVICE_INVENTORY, by RT-EOS's column names."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    missing = [s for s in ("RACK_INVENTORY", "DEVICE_INVENTORY") if s not in wb.sheetnames]
    if missing:
        raise ValueError("This is not the manager's workbook: it has no " + " or ".join(missing) + " sheet.")

    def rows(name):
        it = wb[name].iter_rows(values_only=True); head = [_s(h) for h in next(it, [])]
        for row in it:
            rec = {h: v for h, v in zip(head, row) if h}
            if any(_s(v) for v in rec.values()) and (_s(rec.get("Record_Status")) or "Active").lower() not in ("sample", "retired", "inactive"):
                yield rec
    racks = [{"rack_key": _s(r.get("Rack_ID")), "name": _s(r.get("Rack_Name")) or _s(r.get("Rack_ID")), "label": _s(r.get("Expected_Rack_Label")), "height_ru": _int(r.get("Rack_Height_RU"))}
             for r in rows("RACK_INVENTORY") if _s(r.get("Rack_ID"))]
    devices = [{"rack_key": _s(r.get("Rack_ID")), "device_key": _s(r.get("Device_ID")) or _s(r.get("Device_Name")), "name": _s(r.get("Device_Name")), "role": _s(r.get("Device_Role")),
                "manufacturer": _s(r.get("Manufacturer")), "model": _s(r.get("Model")), "serial": _s(r.get("Serial_Number")), "asset_tag": _s(r.get("Asset_Tag")),
                "ru_start": _int(r.get("RU_Start")), "ru_height": _int(r.get("RU_Height"))}
               for r in rows("DEVICE_INVENTORY") if _s(r.get("Rack_ID")) and (_s(r.get("Device_ID")) or _s(r.get("Device_Name")))]
    known = {r["rack_key"] for r in racks}
    racks += [{"rack_key": k, "name": k, "label": None, "height_ru": None} for k in sorted({d["rack_key"] for d in devices} - known)]
    return _store("excel", racks, devices, pathlib.Path(path).name)


NETBOX_URL = os.environ.get("RACKTRACK_NETBOX_URL", "http://127.0.0.1:3301")          # the local NetBox mock (modules/sources/mock/netbox.js)
SERVICENOW_URL = os.environ.get("RACKTRACK_SERVICENOW_URL", "http://127.0.0.1:5301")  # the local ServiceNow mock (modules/sources/mock/servicenow.js)
CMDB_KIND = {"cmdb_ci_ip_switch": "Switch", "cmdb_ci_netgear": "Patch Panel", "cmdb_ci_server": "Server", "cmdb_ci_firewall_network": "Firewall",
             "cmdb_ci_router": "Router", "cmdb_ci_pdu": "PDU", "cmdb_ci_storage_device": "Storage unit"}


def _get(base, path):
    """GET a JSON page from a local mock. Only this machine is ever called: never a real NetBox or ServiceNow."""
    import urllib.parse, urllib.request
    host = urllib.parse.urlparse(base).hostname
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("Only the local mocks are read today, never a real system.")
    try:
        with urllib.request.urlopen(base.rstrip("/") + path, timeout=15) as r:
            return json.loads(r.read().decode())
    except OSError as e:
        raise ConnectionError(f"The mock at {base} does not answer ({e}). Start it with sources.sh.")


def import_netbox():
    """NetBox's REST API (dcim/racks and dcim/devices), from the local NetBox mock."""
    rk = _get(NETBOX_URL, "/api/dcim/racks/?limit=1000")["results"]
    racks = [{"rack_key": r["name"], "name": r["name"], "label": r.get("facility_id") or r["name"], "height_ru": _int(r.get("u_height"))} for r in rk]
    devices = []
    for d in _get(NETBOX_URL, "/api/dcim/devices/?limit=10000")["results"]:
        if not d.get("rack"):
            continue
        ty = d.get("device_type") or {}
        devices.append({"rack_key": d["rack"]["name"], "device_key": d["name"], "name": d["name"], "role": (d.get("role") or {}).get("name"),
                        "manufacturer": _s((ty.get("manufacturer") or {}).get("name")), "model": _s(ty.get("model")), "serial": _s(d.get("serial")),
                        "asset_tag": _s(d.get("asset_tag")), "ru_start": _int(d.get("position")), "ru_height": _int(ty.get("u_height"))})
    return _store("netbox", racks, devices, NETBOX_URL)


def import_cmdb():
    """ServiceNow's Table API (cmdb_ci_rack, cmdb_rel_ci and the device classes in cmdb_ci), from the local ServiceNow mock."""
    rk = _get(SERVICENOW_URL, "/api/now/table/cmdb_ci_rack?sysparm_limit=10000")["result"]
    racks = [{"rack_key": r["name"], "name": r["name"], "label": r["name"], "height_ru": _int(r.get("rack_units"))} for r in rk]
    rack_of = {r["sys_id"]: r["name"] for r in rk}
    child_rack = {x["child"]["value"]: rack_of[x["parent"]["value"]] for x in _get(SERVICENOW_URL, "/api/now/table/cmdb_rel_ci?sysparm_limit=100000")["result"]
                  if x["parent"]["value"] in rack_of}
    devices = []
    for d in _get(SERVICENOW_URL, "/api/now/table/cmdb_ci?sysparm_limit=100000")["result"]:
        if d.get("sys_id") not in child_rack:
            continue
        devices.append({"rack_key": child_rack[d["sys_id"]], "device_key": d["name"], "name": d["name"], "role": _s(d.get("short_description")),
                        "kind": CMDB_KIND.get(d.get("sys_class_name")) or _kind(d.get("short_description")), "manufacturer": _s(d.get("manufacturer")),
                        "model": _s(d.get("model_id")), "serial": _s(d.get("serial_number")), "asset_tag": _s(d.get("asset_tag")),
                        "ru_start": _int(d.get("u_position")), "ru_height": _int(d.get("u_height"))})
    return _store("cmdb", racks, devices, SERVICENOW_URL)


def racks(source=None):
    with _db() as c:
        q = "SELECT r.*, (SELECT COUNT(*) FROM src_devices d WHERE d.source=r.source AND d.rack_key=r.rack_key) AS devices FROM src_racks r"
        return [dict(x) for x in (c.execute(q + " WHERE source=? ORDER BY rack_key", (source,)) if source else c.execute(q + " ORDER BY source, rack_key"))]


def devices(source, rack_key):
    with _db() as c:
        return [dict(x) for x in c.execute("SELECT * FROM src_devices WHERE source=? AND rack_key=? ORDER BY ru_start DESC", (source, rack_key))]


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


def run_devices(result):
    """The run's devices as start unit, height, kind, make, model and serial (None where not read)."""
    out = []
    for d in (result or {}).get("devices") or []:
        nums = [n for n in (_unum(u) for u in d.get("units") or []) if n is not None]
        if not nums:
            continue
        L = d.get("layout") or {}
        kind = (d.get("kind_override") or {}).get("kind") or d.get("type") or (d.get("classification") or {}).get("label")
        out.append({"index": d.get("index"), "ru_start": min(nums), "ru_height": len(nums), "kind": None if kind in (None, "", "Other", "Not sure") else kind,
                    "manufacturer": _s(L.get("vendor")), "model": _s(L.get("model")), "serial": _s(L.get("serial") or d.get("serial"))})
    return out


def _same(a, b):
    a, b = _norm(a), _norm(b)
    return bool(a and b and (a == b or a in b or b in a))


def _units(start, height):
    return f"U{start}" if not height or height == 1 else f"U{start}-{start + height - 1}"


def drift(result, record):
    """One row per difference between the run and the record, in plain words."""
    rows, notes = [], []
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
    rows.sort(key=lambda x: -(_unum(x["unit"]) or 0))
    return {"rows": rows, "checked": len(pairs), "record_devices": len(record), "video_devices": len(seen), "notes": notes}
