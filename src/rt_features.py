"""The remaining RT-EOS functions on a video run, copied from RT-EOS and altered only where a video run differs from a photo scan.

    ids_from_text           serials and asset tags in a close-up's text      rt_eos/server/lib/model_read.js (SERIAL_RE, ASSET_TAG_RE, the context words)
    read_closeup_ids        RT-EOS's close-up OCR on one picture             rt_eos/pipeline/ocr_closeup.py, run as a child process
    judge                   where the phone was against the sites            rt_eos/server/lib/location.js judge()
    site_positions          the organization's sites with lat and lng        the sign-in service's tenants table, read only
    read_cables/store_cables  A_Z_CONNECTION_MATRIX rows into the copy       rt_eos/server/lib/workbook.js (the matrix part)
    cable_check             the rack's matrix rows against the video's ports rt_eos/server/lib/drift.js compare() and referenceFor()
    az_check, az_csv        the A-end / Z-end check of two runs, its CSV     rt_eos/server/lib/checks.js run() and toCsv()
    find_port               a device's port on the rack picture              rt_eos client FindPort
    connections             per device its problems and a port card         rt_eos client Connections
    worklist                the least sure readings                         rt_eos/server/lib/worklist.js
    physical_layer          the physical layer document                     rt_eos/pipeline/physical_layer.py build()
    sfp_advice              optics and cables for fibre ports               rt_eos/server/lib/sfp_advice.js advise()
Nothing here writes to a source; only the run's own files and this app's copies change.
"""
import datetime
import json
import math
import os
import pathlib
import re
import sqlite3
import subprocess
import sys

import source_db as SDB

ROOT = pathlib.Path(__file__).resolve().parents[1]
RT_EOS = ROOT / "rt_eos"


# ------------------------------------------------------------------ 1 serials and asset tags (model_read.js)
SERIAL_RE = re.compile(r"^[A-Z]{3}\d{4,5}[A-Z0-9]{2,5}$")
ASSET_TAG_RE = re.compile(r"(^|[-_. ])(?:SW|PP|FW|RTR|PDU|ACT)\d{0,3}$", re.I)
SERIAL_WORDS = {"s/n", "s/n:", "sn", "sn:", "n:", "b/n", "b/n:", "serial", "serial:", "serial#", "s/n#"}
ASSET_WORDS = {"asset", "asset:", "asset#", "tag:", "tag"}
CABLE_RE = re.compile(r"^(?:CBL|FBR|CAB|CBL\d|PC|LC|CU|MPO)[-_][A-Z0-9][A-Z0-9_-]{2,}$", re.I)     # checks.js cableShaped, in short


def _clean(tok):
    return re.sub(r"^[,;:()\[\]{}'\"|*]+|[,;:()\[\]{}'\"|*]+$", "", str(tok or "")).lstrip("-.")


def ids_from_text(text):
    """The serial, the asset tag, the device labels and the cable labels in a close-up's text, by RT-EOS's shapes: a value after
    S/N, SN or Serial (or one in the serial shape, three letters, four or five digits and two to five more, with no dash); a value
    after Asset or Asset Tag (or one in the asset-tag shape, a name ending in SW, PP, FW, RTR, PDU or ACT and a number, of three
    parts or more, which is also a device label); a cable label in the cable shape (CBL-..., FBR-...)."""
    raw = str(text or "").split()
    out = {"serial": None, "asset_tag": None, "labels": [], "cables": []}
    for i, tok in enumerate(raw):
        t = _clean(tok)
        if not t:
            continue
        prev = raw[i - 1].lower() if i > 0 else ""
        prev2 = raw[i - 2].lower() if i > 1 else ""
        glued = re.match(r"^(?:S/?N|SERIAL)[:#](.+)$", t, re.I)
        if glued and len(glued.group(1)) >= 5:
            out["serial"] = out["serial"] or glued.group(1).upper()
            continue
        if t.lower() in SERIAL_WORDS | ASSET_WORDS or t.lower() in ("no", "no.", "no:", "number", "number:", "tag:"):
            continue
        after_serial = prev in SERIAL_WORDS or (prev2 in ("serial", "s/n") and prev in ("no", "no.", "no:", "number", "number:"))
        after_asset = prev in ("asset", "asset:", "asset#") or (prev2 in ("asset",) and prev in ("tag", "tag:", "no", "no:", "no."))
        if after_serial and len(t) >= 5 and re.search(r"\d", t):
            out["serial"] = out["serial"] or t.upper()
        elif after_asset and len(t) >= 4:
            out["asset_tag"] = out["asset_tag"] or t.upper()
        elif len(t) >= 10 and SERIAL_RE.match(t.upper()) and "-" not in t:
            out["serial"] = out["serial"] or t.upper()
        elif CABLE_RE.match(t):
            out["cables"].append(t.upper())
        elif ASSET_TAG_RE.search(t) and len(re.split(r"[-_. ]", t)) >= 3:
            out["labels"].append(t.upper())
    if not out["asset_tag"] and out["labels"]:
        out["asset_tag"] = out["labels"][0]
    return out


def read_closeup_ids(image_path, timeout=240):
    """RT-EOS's close-up OCR (pipeline/ocr_closeup.py, its own process: RT-EOS's `pipeline` package and this engine's pipeline.py
    share a name) on one picture, then the shapes above. The caller holds the model lock (the engine does)."""
    env = dict(os.environ, PYTHONPATH=str(RT_EOS), OMP_NUM_THREADS=os.environ.get("OMP_NUM_THREADS", "2"))
    try:
        p = subprocess.run([sys.executable, "-m", "pipeline.ocr_closeup", str(image_path)], cwd=str(RT_EOS), env=env,
                           capture_output=True, text=True, timeout=timeout)
        line = (p.stdout or "").strip().splitlines()[-1] if (p.stdout or "").strip() else "{}"
        res = json.loads(line)
    except (subprocess.TimeoutExpired, ValueError, OSError) as ex:
        return {"ok": False, "error": f"{type(ex).__name__}: {ex}", "serial": None, "asset_tag": None, "labels": [], "cables": [], "raw_text": ""}
    ids = ids_from_text(res.get("raw_text"))
    return dict(ids, ok=bool(res.get("ok")), raw_text=res.get("raw_text") or "", make=res.get("make"), model=res.get("model"),
                ocr_conf=res.get("ocr_conf"), error=res.get("error"))


def keep_ids(dev, ids, picture, by):
    """A close-up's serial, asset tag and labels kept on the device (what was read, from which picture, by whom); a value read before
    stays unless this picture read one."""
    old = dev.get("ids") or {}
    dev["ids"] = {"serial": ids.get("serial") or old.get("serial"), "asset_tag": ids.get("asset_tag") or old.get("asset_tag"),
                  "labels": sorted(set((old.get("labels") or []) + (ids.get("labels") or []))),
                  "cables": sorted(set((old.get("cables") or []) + (ids.get("cables") or []))),
                  "raw_text": ids.get("raw_text") or old.get("raw_text"), "from": "close-up", "picture": picture, "by": by,
                  "when": datetime.datetime.now().isoformat(timespec="seconds")}
    if dev["ids"]["serial"]:
        dev["serial"] = dev["ids"]["serial"]
    if dev["ids"]["asset_tag"]:
        dev["asset_tag"] = dev["ids"]["asset_tag"]
    if dev["ids"]["labels"]:
        dev["label_read"] = dev["ids"]["labels"][0]
    return dev["ids"]


# ------------------------------------------------------------------ 2 location (location.js)
SITE_RADIUS_M = 300
MAX_USEFUL_ACCURACY_M = 2000


def distance_m(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371008.8 * math.asin(min(1.0, math.sqrt(h)))


def said(m):
    return f"{round(m / 10) * 10:.0f} m" if m < 1000 else f"{m / 1000:.1f} km" if m < 10000 else f"{round(m / 1000)} km"


def _valid(p):
    try:
        return p is not None and abs(float(p["lat"])) <= 90 and abs(float(p["lng"])) <= 180
    except (KeyError, TypeError, ValueError):
        return False


def judge(capture, site, others=()):
    """location.js judge(): was the video taken at the run's own site, at another site of the organization, or nowhere near one."""
    if not _valid(capture):
        return {"verdict": "unknown", "note": "The phone did not share where the video was taken."}
    acc = capture.get("accuracy")
    acc = float(acc) if acc not in (None, "") else None
    if acc is not None and acc > MAX_USEFUL_ACCURACY_M:
        return {"verdict": "unknown", "accuracy_m": acc, "note": f"The phone's location was only good to {said(acc)}, too rough to say which site this is."}
    radius = max(SITE_RADIUS_M, 2 * (acc or 0))
    at = (float(capture["lat"]), float(capture["lng"]))
    took = {"lat": at[0], "lng": at[1], "accuracy_m": acc}
    here = distance_m(at, (float(site["lat"]), float(site["lng"]))) if _valid(site) else None
    if here is not None and here <= radius:
        return {"verdict": "here", "distance_m": round(here), "accuracy_m": acc, "at": took, "site": site["name"], "radius_m": radius,
                "note": f"Taken at {site['name']}, {said(here)} from its position."}
    near = sorted(((o, distance_m(at, (float(o["lat"]), float(o["lng"])))) for o in others if _valid(o)), key=lambda x: x[1])
    near = [x for x in near if x[1] <= radius]
    if near:
        o, d = near[0]
        return {"verdict": "elsewhere", "distance_m": round(here) if here is not None else None, "accuracy_m": acc, "at": took, "radius_m": radius,
                "site": site and site.get("name"), "nearest": {"id": o["id"], "name": o["name"], "distance_m": round(d)},
                "note": f"Taken at {o['name']}, not {site['name'] if site else 'the site this run is filed under'}" + (f", which is {said(here)} away" if here is not None else "")
                + ". Check which site this run belongs to before trusting a match."}
    if here is None:
        return {"verdict": "unknown", "accuracy_m": acc, "at": took, "note": f"{site['name'] if site else 'This site'} has no position set, so where the video was taken cannot be checked against it."}
    return {"verdict": "away", "distance_m": round(here), "accuracy_m": acc, "at": took, "site": site["name"], "radius_m": radius,
            "note": f"Taken {said(here)} from {site['name']}, and not at any other site of this organization."}


def site_positions(auth_db, org, site_id):
    """The run's own site and the organization's other sites, with lat and lng, from the sign-in service's tenants table (read only)."""
    if not auth_db or not pathlib.Path(auth_db).exists() or org is None:
        return None, []
    c = sqlite3.connect(f"file:{auth_db}?mode=ro", uri=True, timeout=5)
    try:
        c.row_factory = sqlite3.Row
        rows = [dict(r) for r in c.execute("SELECT id, name, lat, lng FROM tenants WHERE organization_id = ?", (org,))]
    finally:
        c.close()
    own = next((r for r in rows if str(r["id"]) == str(site_id)), None)
    return own, [r for r in rows if r is not own]


# ------------------------------------------------------------------ 3 cables: the A_Z_CONNECTION_MATRIX (workbook.js, matrix part)
CABLES_SQL = """CREATE TABLE IF NOT EXISTS src_cables (copy_id INTEGER, source TEXT, row_no INTEGER, connection_id TEXT, cable_id TEXT, cable_label TEXT,
  label_a TEXT, label_z TEXT, label_alias TEXT, a_rack TEXT, a_device TEXT, a_port TEXT, z_rack TEXT, z_device TEXT, z_port TEXT, cable_type TEXT,
  cable_color TEXT, notes TEXT)"""
SKIP_STATUS = ("sample", "retired", "inactive", "decommissioned", "superseded")


def _key(h):
    return re.sub(r"[^a-z0-9]", "", str(h or "").lower())


def read_cables(path):
    """One row per expected cable of the manager's workbook's A_Z_CONNECTION_MATRIX, with RT-EOS's field names."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    name = next((n for n in wb.sheetnames if _key(n) == "azconnectionmatrix"), None)
    if not name:
        return []
    it = wb[name].iter_rows(values_only=True)
    head = [_key(h) for h in next(it, [])]
    out = []
    for n, row in enumerate(it, start=2):
        rec = {h: v for h, v in zip(head, row) if h}
        s = lambda k: SDB._s(rec.get(k))
        if not any(v not in (None, "") for v in rec.values()) or (s("recordstatus") or "").lower() in SKIP_STATUS or "illustrative sample" in (s("notes") or "").lower():
            continue
        if not (s("cablelabelprimary") or s("cableid") or s("connectionid")):
            continue
        port = lambda e: s(f"{e}portnumber") or s(f"{e}portname") or s(f"{e}portid")
        out.append({"row_no": n, "connection_id": s("connectionid"), "cable_id": s("cableid"), "cable_label": s("cablelabelprimary") or s("cableid"),
                    "label_a": s("cablelabelaend"), "label_z": s("cablelabelzend"), "label_alias": s("cablelabelalias"),
                    "a_rack": s("arackid") or s("arackname"), "a_device": s("adeviceid") or s("adevicename") or s("apatchpanelid"), "a_port": port("a"),
                    "z_rack": s("zrackid") or s("zrackname"), "z_device": s("zdeviceid") or s("zdevicename") or s("zpatchpanelid"), "z_port": port("z"),
                    "cable_type": s("mediatype") or s("aconnectortype"), "cable_color": s("cablecolor"), "notes": s("notes")})
    return out


def store_cables(copy_id, path):
    """The matrix rows of a new Excel copy, kept with the copy (deleted with it)."""
    rows = read_cables(path)
    with SDB._db() as c:
        c.execute(CABLES_SQL)
        for r in rows:
            c.execute("INSERT INTO src_cables VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (copy_id, "excel", r["row_no"], r["connection_id"], r["cable_id"], r["cable_label"],
                      r["label_a"], r["label_z"], r["label_alias"], r["a_rack"], r["a_device"], r["a_port"], r["z_rack"], r["z_device"], r["z_port"],
                      r["cable_type"], r["cable_color"], r["notes"]))
        c.execute("UPDATE src_copies SET file = file WHERE id = ?", (copy_id,))
    return len(rows)


def delete_cables(copy_id):
    with SDB._db() as c:
        c.execute(CABLES_SQL)
        c.execute("DELETE FROM src_cables WHERE copy_id = ?", (copy_id,))


def cables_for(rack_keys):
    """The matrix rows of the active Excel copy of each rack, the rows with either end in one of the racks."""
    keys = [k for k in rack_keys if k]
    out, seen = [], set()
    with SDB._db() as c:
        c.execute(CABLES_SQL)
        for k in keys:
            cid = c.execute("SELECT MAX(copy_id) FROM src_racks WHERE source='excel' AND rack_key=?", (k,)).fetchone()[0]
            if cid is None:
                continue
            for x in c.execute("SELECT * FROM src_cables WHERE copy_id=? AND (a_rack=? OR z_rack=?) ORDER BY row_no", (cid, k, k)):
                if (cid, x["row_no"]) not in seen:
                    seen.add((cid, x["row_no"])); out.append(dict(x))
    return out


def port_no(v):
    m = re.search(r"(\d+)(?!.*\d)", str(v or ""))
    return int(m.group(1)) if m else None


def pair_devices(result, record):
    """The record's devices paired with the run's, as SDB.drift pairs them (same unit, then one unit either way)."""
    seen, pairs = SDB.run_devices(result), []
    free = list(seen)
    wanted = [r for r in record if r.get("ru_start") is not None and (r.get("kind") or "").lower() not in SDB.NOT_SEEN_KINDS]
    for tol in (0, 1):
        for r in [w for w in wanted if not any(p[0] is w for p in pairs)]:
            hit = next((s for s in free if abs(s["ru_start"] - r["ru_start"]) <= tol), None)
            if hit:
                free.remove(hit); pairs.append((r, hit))
    return pairs


def _devkey(r):
    return {SDB._norm(r.get("device_key")), SDB._norm(r.get("name"))} - {""}


def _labels_read(result):
    """Cable labels read on each device (close-ups): {index: [label, ...]}."""
    return {d["index"]: list((d.get("ids") or {}).get("cables") or []) for d in (result or {}).get("devices") or []}


def cable_check(result, record, cables, rack_key):
    """drift.js on every port the matrix names in this rack: Connected expected and the video reads the port empty is a missing cable,
    a device of the matrix with a cable on a port no row names is an extra cable; a cable label read on another device or port, or a
    missing cable and an extra one on the same device, is a wrong port. A port the video could not read is not checked."""
    pairs = pair_devices(result, record)
    by_key = {}
    for r, s in pairs:
        for k in _devkey(r):
            by_key[k] = (r, s)
    labels = _labels_read(result)
    rows, per_dev, checked, not_read, outside = [], {}, 0, 0, 0
    named_ports = {}
    for c in cables:
        for e, o in (("a", "z"), ("z", "a")):
            if c.get(f"{e}_rack") and SDB._norm(c[f"{e}_rack"]) != SDB._norm(rack_key):
                continue
            hit = by_key.get(SDB._norm(c.get(f"{e}_device")))
            if not hit:
                outside += 1
                continue
            r, s = hit
            n = port_no(c.get(f"{e}_port"))
            named_ports.setdefault(s["index"], set()).add(n)
            st = next((p["state"] for p in s["ports"] if p["n"] == n), None)
            other = f"{c.get(f'{o}_device') or 'no device'}" + (f" port {c.get(f'{o}_port')}" if c.get(f"{o}_port") else "") + (f" in {c.get(f'{o}_rack')}" if c.get(f"{o}_rack") and SDB._norm(c.get(f"{o}_rack")) != SDB._norm(rack_key) else "")
            base = {"cable": c.get("cable_label"), "cable_id": c.get("cable_id"), "connection_id": c.get("connection_id"), "row_no": c.get("row_no"), "end": e.upper(),
                    "device": f"{r.get('name')} / D{s['index']}", "index": s["index"], "unit": SDB._units(s["ru_start"], s["ru_height"]), "port": n, "other": other,
                    "type_record": c.get("cable_type"), "colour_record": c.get("cable_color")}
            wants = {SDB._norm(x) for x in (c.get("cable_label"), c.get(f"label_{e}"), c.get("label_alias")) if x}
            seen_on = [i for i, ls in labels.items() if wants & {SDB._norm(x) for x in ls}]
            if seen_on and s["index"] not in seen_on:
                rows.append({**base, "type": "wrong_port", "what": "Wrong port", "record": f"{r.get('name')} port {n}", "read": f"label on D{seen_on[0]}",
                             "words": f"Cable {c.get('cable_label')} should be on port {n} of {r.get('name')}; its label was read on D{seen_on[0]}."})
                checked += 1
                continue
            if st == "empty":
                rows.append({**base, "type": "missing_cable", "what": "Missing cable", "record": "cable", "read": "empty",
                             "words": f"Cable {c.get('cable_label')} ({e.upper()}-end, to {other}) should be on port {n} of {r.get('name')}; the video shows the port empty."})
                per_dev.setdefault(s["index"], {"missing": [], "extra": []})["missing"].append(rows[-1])
                checked += 1
            elif st == "connected":
                checked += 1
            else:
                not_read += 1
    for r, s in pairs:
        if s["index"] not in named_ports:
            continue
        for p in s["ports"]:
            if p["state"] == "connected" and p["n"] not in named_ports[s["index"]]:
                rows.append({"type": "extra_cable", "what": "Extra cable", "device": f"{r.get('name')} / D{s['index']}", "index": s["index"], "unit": SDB._units(s["ru_start"], s["ru_height"]),
                             "port": p["n"], "record": "no cable", "read": "cable", "words": f"Port {p['n']} of {r.get('name')} has a cable; no row of the matrix puts one there."})
                per_dev.setdefault(s["index"], {"missing": [], "extra": []})["extra"].append(rows[-1])
                checked += 1
    for idx, g in per_dev.items():                    # a missing cable and an extra one on the same device: the cable is in another port
        for m, x in zip(list(g["missing"]), list(g["extra"])):
            rows.remove(m); rows.remove(x)
            rows.append({**m, "type": "wrong_port", "what": "Wrong port", "record": f"port {m['port']}", "read": f"port {x['port']}",
                         "words": f"Cable {m['cable']} should be on port {m['port']} of {m['device'].split(' / ')[0]}; port {m['port']} is empty and port {x['port']} has a cable no row names, so it is likely in port {x['port']}."})
    rows.sort(key=lambda x: (-(SDB._unum(x["unit"]) or 0), x.get("port") or 0))
    return {"rows": rows, "cables": len(cables), "ends_checked": checked, "ends_not_read": not_read, "ends_elsewhere": outside}


def run_view(result, record, rack_label):
    """A run as checks.js sees a rack view: its devices with the record's name and the ports the video read."""
    pairs = pair_devices(result, record)
    name = {s["index"]: r.get("name") for r, s in pairs}
    vis = []
    for s in SDB.run_devices(result):
        vis.append({"deviceId": s["index"], "sheetName": name.get(s["index"]), "ports": [{"portNo": p["n"], "state": p["state"]} for p in s["ports"]]})
    return {"rackLabel": rack_label, "visible": vis, "labels": _labels_read(result)}


def az_check(a, z, cables, by):
    """checks.js run() on two runs (the same run at both ends is allowed, as RT-EOS allows it). a and z are run views (run_view) with
    scanId. Per cable end: matched (its label read on the device the sheet says), wrong_port (read on another device), missing_cable (no
    label read and the port empty), not_found (no label read); a cable's status is the worst of its ends."""
    same = a["scanId"] == z["scanId"]
    racks = {"a": a["rackLabel"], "z": z["rackLabel"]}
    views = {"a": a, "z": z}

    def side_of(c, e):
        r = c.get(f"{e}_rack")
        if r:
            if SDB._norm(r) == SDB._norm(racks["a"]):
                return "a"
            if SDB._norm(r) == SDB._norm(racks["z"]):
                return "z"
            return None
        dev = SDB._norm(c.get(f"{e}_device"))
        for sd in ("a", "z"):
            if any(SDB._norm(d["sheetName"]) == dev for d in views[sd]["visible"]):
                return sd
        return None

    found = {sd: [(d_idx, SDB._norm(t), t) for d_idx, ls in views[sd]["labels"].items() for t in ls] for sd in ("a", "z")}
    sheet = {SDB._norm(t) for c in cables for t in (c.get("cable_label"), c.get("label_a"), c.get("label_z"), c.get("label_alias")) if t}
    out, read, expected = [], 0, 0
    RANK = {"wrong_port": 4, "missing_cable": 3, "not_found": 2, "matched": 1}
    for c in cables:
        entry = {"cableLabel": c.get("cable_label"), "rowNo": c.get("row_no"), "connectionId": c.get("connection_id"), "cableId": c.get("cable_id"),
                 "labels": {"a": c.get("label_a"), "z": c.get("label_z")}}
        statuses = []
        for e, o in (("a", "z"), ("z", "a")):
            exp = {"device": c.get(f"{e}_device"), "port": c.get(f"{e}_port"), "rack": c.get(f"{e}_rack")}
            sd = side_of(c, e)
            if not sd:
                entry[e] = {"expected": exp, "found": None, "status": None}
                continue
            expected += 1
            want = {SDB._norm(x) for x in (c.get("cable_label"), c.get(f"label_{e}"), c.get("label_alias")) if x}
            name_of = {d["deviceId"]: d["sheetName"] for d in views[sd]["visible"]}
            other_dev = SDB._norm(c.get(f"{o}_device"))
            hits = [h for h in found[sd] if h[1] in want]
            hit = next((h for h in hits if SDB._norm(name_of.get(h[0])) == SDB._norm(exp["device"])), None) or \
                next((h for h in hits if not other_dev or SDB._norm(name_of.get(h[0])) != other_dev), None)
            if hit:
                read += 1
                dev = name_of.get(hit[0]) or f"D{hit[0]}"
                st = "matched" if SDB._norm(dev) == SDB._norm(exp["device"]) else "wrong_port"
                f = {"device": dev, "port": None, "source": "close-up", "read": hit[2], "match": "exact"}
                if st == "wrong_port":
                    entry[f"{e}Explained"] = f"Label {hit[2]} found on {dev}, the Excel says {exp['device'] or 'no device'}" + (f" port {exp['port']}" if exp["port"] else "")
            else:
                d = next((x for x in views[sd]["visible"] if SDB._norm(x["sheetName"]) == SDB._norm(exp["device"])), None)
                p = next((x for x in (d or {}).get("ports") or [] if x["portNo"] == port_no(exp["port"])), None)
                st, f = ("missing_cable" if p and p["state"] == "empty" else "not_found"), None
            entry[e] = {"expected": exp, "found": f, "status": st}
            statuses.append(st)
        if not statuses:
            continue
        entry["status"] = "wrong_port" if "wrong_port" in statuses else "matched" if "matched" in statuses else "missing_cable" if "missing_cable" in statuses \
            else sorted(statuses, key=lambda s: -RANK.get(s, 0))[0]
        out.append(entry)
    for sd in (("a",) if same else ("a", "z")):
        done = set()
        for idx, k, t in found[sd]:
            if k in sheet or k in done:
                continue
            done.add(k)
            dev = next((d["sheetName"] for d in views[sd]["visible"] if d["deviceId"] == idx), None) or f"D{idx}"
            other = "z" if sd == "a" else "a"
            out.append({"cableLabel": t, "rowNo": None, sd: {"expected": None, "found": {"device": dev, "port": None, "source": "close-up", "read": t, "match": None}, "status": "not_in_sheet"},
                        other: {"expected": None, "found": None, "status": None}, "status": "not_in_sheet"})
    counts = {"matched": 0, "wrongPort": 0, "notInSheet": 0, "notFound": 0, "missingCable": 0}
    KEY = {"matched": "matched", "wrong_port": "wrongPort", "not_in_sheet": "notInSheet", "not_found": "notFound", "missing_cable": "missingCable"}
    for c in out:
        counts[KEY[c["status"]]] += 1
    order = {"wrong_port": 0, "missing_cable": 1, "not_found": 2, "not_in_sheet": 3, "matched": 5}
    out.sort(key=lambda c: (order[c["status"]], str(c["cableLabel"])))
    return {"id": os.urandom(5).hex(), "at": datetime.datetime.now().isoformat(timespec="seconds"), "by": by, "aScanId": a["scanId"], "zScanId": z["scanId"],
            "aRack": racks["a"], "zRack": racks["z"], "counts": counts, "accuracy": {"labelsRead": read, "labelsExpected": expected,
            "readRate": round(read / expected, 3) if expected else None}, "cables": out}


def _csv_cell(v):
    s = "" if v is None else str(v)
    if re.match(r"^[=+\-@]", s):
        s = "'" + s
    return '"' + s.replace('"', '""') + '"' if re.search(r'[",\n\r]', s) or s.startswith("'") else s


def az_csv(check):
    """checks.js toCsv(): one row per cable end."""
    head = ["Cable label", "Connection ID", "Cable ID", "Cable status", "End", "Rack", "Sheet device", "Sheet port", "Label expected", "Found device",
            "Found port", "Found by", "End status", "Label read", "How the label matched"]
    rows = [head]
    for c in check.get("cables") or []:
        for e in ("a", "z"):
            end = c.get(e) or {}
            if not end.get("expected") and not end.get("found"):
                continue
            ex, fd = end.get("expected") or {}, end.get("found") or {}
            rows.append([c.get("cableLabel"), c.get("connectionId") or "", c.get("cableId") or "", c.get("status"), "A-end" if e == "a" else "Z-end",
                         ex.get("rack") or (check["aRack"] if e == "a" else check["zRack"]) or "", ex.get("device") or "", ex.get("port") or "",
                         (c.get("labels") or {}).get(e) or "", fd.get("device") or "", fd.get("port") or "", fd.get("source") or "",
                         end.get("status") or "not checked", fd.get("read") or "", "exact" if fd.get("match") else ""])
    return "\r\n".join(",".join(_csv_cell(v) for v in r) for r in rows) + "\r\n"


# ------------------------------------------------------------------ 4 the run's own functions
def _dev(result, index):
    return next((d for d in (result or {}).get("devices") or [] if d.get("index") == index), None)


def _detail_ports(d):
    """Every port of a device with what the video read on it: number, type, state, cable colour and connector, where on the rack picture."""
    C, L = d.get("closeup") or {}, d.get("layout") or {}
    src = C if (C.get("fitted") or C.get("auto")) else L
    pd = {str(p.get("id")): p for p in ((C.get("port_details") if src is C else None) or L.get("port_details") or {}).get("ports") or []}
    used = src.get("fitted") or src.get("auto") or src.get("mode") == "patchpanel"
    raw = (src.get("ports") or []) if used else list(pd.values())
    out = []
    for p in raw:
        x = pd.get(str(p.get("id"))) or {}
        out.append({"n": SDB._int(p.get("id")) if str(p.get("id") or "").strip().isdigit() else None, "id": p.get("id"), "type": SDB._s(p.get("type")),
                    "state": p.get("status") or x.get("status"), "colour": p.get("cable_colour") or x.get("cable_colour"),
                    "connector": p.get("cable_connector") or x.get("cable_connector"), "rack_polygon": p.get("rack_polygon"), "by": p.get("state_by")})
    return out


def find_port(result, record, cables, rack_key, index, port):
    """A device and a port number: where it is on the rack picture, what the video read on it, and the matrix's cable and label there."""
    d = _dev(result, index)
    if d is None:
        return None
    ports = _detail_ports(d)
    p = next((x for x in ports if x["n"] == port), None)
    rec = next((r for r, s in pair_devices(result, record) if s["index"] == index), None)
    cab = None
    if rec:
        for c in cables:
            for e, o in (("a", "z"), ("z", "a")):
                if SDB._norm(c.get(f"{e}_device")) in _devkey(rec) and port_no(c.get(f"{e}_port")) == port and (not c.get(f"{e}_rack") or SDB._norm(c[f"{e}_rack"]) == SDB._norm(rack_key)):
                    cab = {"label": c.get(f"label_{e}") or c.get("cable_label"), "cable": c.get("cable_label"), "end": e.upper(),
                           "other": {"device": c.get(f"{o}_device"), "port": c.get(f"{o}_port"), "rack": c.get(f"{o}_rack")}, "type": c.get("cable_type"), "colour": c.get("cable_color")}
    kind = SDB.device_kind(d)["kind"]
    return {"index": index, "device": (rec or {}).get("name") or f"D{index}", "kind": kind, "unit": SDB._units(*_span(d)), "port": port,
            "found": p is not None, "read": p, "ports": [x["n"] for x in ports if x["n"] is not None], "cable": cab,
            "box": _bbox(p["rack_polygon"]) if p and p.get("rack_polygon") else _bbox(d.get("polygon")) if d.get("polygon") else None,
            "device_box": _bbox(d.get("polygon")) if d.get("polygon") else None}


def _span(d):
    nums = [n for n in (SDB._unum(u) for u in d.get("units") or []) if n is not None]
    return (min(nums), len(nums)) if nums else (None, None)


def _bbox(poly):
    if not poly:
        return None
    xs, ys = [q[0] for q in poly], [q[1] for q in poly]
    return [min(xs), min(ys), max(xs), max(ys)]


def connections(result, record, drift_rows, cable_rows):
    """Per device: its problems (the drift and the cable rows that name it) and a port card (each port's state)."""
    name = {s["index"]: r.get("name") for r, s in pair_devices(result, record)}
    out = []
    for d in (result or {}).get("devices") or []:
        i = d["index"]
        ports = _detail_ports(d)
        probs = [x for x in (drift_rows or []) + (cable_rows or []) if x.get("index") == i]
        st = {"connected": 0, "empty": 0, "unknown": 0}
        for p in ports:
            st[p["state"] if p["state"] in st else "unknown"] += 1
        out.append({"index": i, "name": name.get(i) or f"D{i}", "kind": SDB.device_kind(d)["kind"], "unit": SDB._units(*_span(d)), "problems": probs,
                    "ports": [{"n": p["n"], "id": p["id"], "type": p["type"], "state": p["state"], "colour": p["colour"]} for p in ports], "counts": st,
                    "serial": (d.get("ids") or {}).get("serial") or d.get("serial"), "asset_tag": (d.get("ids") or {}).get("asset_tag") or d.get("asset_tag")})
    out.sort(key=lambda x: -(SDB._unum(x["unit"]) or 0))
    return out


LOW_DEVICE, PORTS_PER_DEVICE = 0.65, 8


def worklist(result):
    """worklist.js on one run: a device whose kind was read below 65 per cent (and no person or faceplate settled it), and up to 8 ports
    per device whose state the video could not tell. Least sure first."""
    items = []
    for d in (result or {}).get("devices") or []:
        k = SDB.device_kind(d)
        cl = d.get("classification") or {}
        prob = float(cl.get("prob") or 0)
        if k["from"] not in ("corrected", "faceplate") and prob < LOW_DEVICE:
            items.append({"id": f"d{d['index']}", "kind": "device", "index": d["index"], "unit": SDB._units(*_span(d)), "read": k["kind"] or cl.get("class") or "Not sure",
                          "confidence": round(prob, 2), "question": f"What is the device at {SDB._units(*_span(d))}?",
                          "why": f"The picture read {cl.get('class') or 'nothing sure'} at {round(100 * prob)} %."})
        n = 0
        for p in _detail_ports(d):
            if p["n"] is None or p["state"] in ("connected", "empty"):
                continue
            items.append({"id": f"p{d['index']}-{p['n']}", "kind": "port", "index": d["index"], "port": p["n"], "unit": SDB._units(*_span(d)), "read": "not sure",
                          "confidence": 0.0, "question": f"Is there a cable in port {p['n']} of D{d['index']}?", "why": "The video could not tell whether a cable is plugged in."})
            n += 1
            if n >= PORTS_PER_DEVICE:
                break
    items.sort(key=lambda x: (x["confidence"], x["kind"] != "device"))
    return items


def set_port_state(result, index, port, state, by):
    """A person's answer for a port's state, kept on the port (and in the run's answers, so a read again keeps it)."""
    d = _dev(result, index)
    if d is None:
        return False
    hit = False
    for holder in (d.get("closeup") or {}, d.get("layout") or {}):
        for lst in (holder.get("ports") or [], (holder.get("port_details") or {}).get("ports") or []):
            for p in lst:
                if str(p.get("id")) == str(port):
                    p["status"], p["state_by"] = state, by; hit = True
    if hit:
        result.setdefault("port_answers", {})[f"{index}-{port}"] = {"state": state, "by": by, "when": datetime.datetime.now().isoformat(timespec="seconds")}
    return hit


KEEP_ON_READ_AGAIN = ("kind_override", "ids", "serial", "asset_tag", "label_read", "closeup")


def kept_before_read_again(result):
    """What a person gave or a close-up read, per device, to put back after the read again."""
    return {d["index"]: {k: d[k] for k in KEEP_ON_READ_AGAIN if k in d} | {"typed": (d.get("layout") or {}).get("typed")} for d in (result or {}).get("devices") or []}


def put_back(result, kept, answers):
    for d in (result or {}).get("devices") or []:
        k = kept.get(d["index"]) or {}
        for f in KEEP_ON_READ_AGAIN:
            if f in k:
                d[f] = k[f]
        if k.get("typed"):
            d["layout"] = dict(d.get("layout") or {}, typed=k["typed"])
    for key, a in (answers or {}).items():
        i, _, p = key.partition("-")
        set_port_state(result, int(i), p, a["state"], a["by"])
    return result


def physical_layer(result, rack_id, label, record):
    """physical_layer.py build() from a video run: the rack's id and label, each unit's devices with their label, make, model, serial and
    asset tag, every port with its state and the cable on it, the cables and the counts. Nothing invented: a device with no label read
    carries label null; a port the video did not read stays unknown."""
    name = {s["index"]: r.get("name") for r, s in pair_devices(result, record)}
    lab = (label or {}).get("id") or (label or {}).get("name") or {}
    counts = {"ports": 0, "connected": 0, "empty": 0, "unknown": 0, "cables_by_color": {}}
    units, cables, labels_seen = {}, [], []
    for d in (result or {}).get("devices") or []:
        start, h = _span(d)
        key = ",".join(f"u{start + i:02d}" for i in range(h)) if start else "unplaced"
        k = SDB.device_kind(d)
        L = d.get("layout") or {}
        ids = d.get("ids") or {}
        lt = ids.get("labels")[0] if ids.get("labels") else None
        if lt:
            labels_seen.append(lt)
        entry = {"device": f"D{d['index']}", "device_type": k["kind"] or "Unidentified", "confidence": round(float((d.get("classification") or {}).get("prob") or 0), 3),
                 "kind_from": k["words"], "record_name": name.get(d["index"]),
                 "label": {"text": lt, "source": "close-up" if lt else None},
                 "ocr": {"make": L.get("vendor") or (L.get("typed") or {}).get("vendor"), "model": L.get("model") or (L.get("typed") or {}).get("model"),
                         "raw_text": ids.get("raw_text"), "source": "person" if L.get("typed") and not L.get("vendor") else ("faceplate" if L.get("vendor") else "not read")},
                 "serial": ids.get("serial") or d.get("serial"), "asset_tag": ids.get("asset_tag") or d.get("asset_tag")}
        ports = []
        for p in _detail_ports(d):
            if p["n"] is None:
                continue
            stt = p["state"] if p["state"] in ("connected", "empty") else "unknown"
            q = {"port_number": p["n"], "port_type": (p["type"] or "port").upper(), "status": stt}
            if stt == "connected" and (p["colour"] or p["connector"]):
                q["cable"] = {"connector": p["connector"], "color": p["colour"]}
                cables.append({"unit": key, "device_type": entry["device_type"], "port_type": q["port_type"], "port_number": p["n"], "connector": p["connector"], "color": p["colour"]})
                counts["cables_by_color"][str(p["colour"])] = counts["cables_by_color"].get(str(p["colour"]), 0) + 1
            counts["ports"] += 1; counts[stt] += 1
            ports.append(q)
        entry["port_count"] = len(ports); entry["ports"] = ports
        units.setdefault(key, []).append(entry)
    return {"schema": "racktrack-physical-layer/1", "rack_id": rack_id, "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "video", "units_count": int(((result or {}).get("rack") or {}).get("units") or 0), "devices_count": len((result or {}).get("devices") or []),
            "rack": {"id": rack_id, "label": {"text": lab.get("text"), "conf": lab.get("confidence"), "source": "rack label read on the video" if lab else "not read"}, "name": lab.get("text")},
            "labels": {"devices": labels_seen},
            "units": [{"unit": k, "devices": v} for k, v in sorted(units.items(), key=lambda kv: -(SDB._unum(kv[0].split(",")[-1]) or 0))],
            "cables": cables, "counts": counts}


SFP_STANDARDS = {"SFP": ("1 Gbps", "1000BASE-SX (multimode) or 1000BASE-LX (single mode)"), "SFP+": ("10 Gbps", "10GBASE-SR (multimode) or 10GBASE-LR (single mode)"),
                 "SFP28": ("25 Gbps", "25GBASE-SR (multimode) or 25GBASE-LR (single mode)"), "QSFP+": ("40 Gbps", "40GBASE-SR4 (multimode, MPO) or 40GBASE-LR4 (single mode, LC)"),
                 "QSFP28": ("100 Gbps", "100GBASE-SR4 (multimode, MPO) or 100GBASE-LR4 (single mode, LC)"), "QSFP-DD": ("400 Gbps", "400GBASE-DR4 or SR8 (MPO)"),
                 "OSFP": ("400 or 800 Gbps", "400GBASE-DR4 or FR4, or 800G DR8 (MPO)")}
CABLE_STANDARDS = {"multimode": "LC-LC duplex, OM3 or OM4 multimode (up to 300 m at 10G)", "singlemode": "LC-LC duplex, OS2 single mode (up to 10 km)",
                   "mpo": "MPO-12 trunk, OM4 multimode (up to 150 m)"}
FIBRE_PORTS = {"SFP", "QSFP", "OSFP", "LC", "SC", "FC"}
SWITCH_GEAR = {"switch", "router", "firewall", "gateway", "load balancer"}


def slot_from_model(make, model):
    s = f"{make or ''} {model or ''}"
    for pat, slot in ((r"400G|QSFP-DD", "QSFP-DD"), (r"100G|QSFP28", "QSFP28"), (r"40G|QSFP\+?(?!28|DD)", "QSFP+"), (r"25G|SFP28", "SFP28"), (r"10G|SFP\+|XG", "SFP+"), (r"\dS\+", "SFP+")):
        if re.search(pat, s, re.I):
            return slot
    return None


def fibre_mode(text):
    t = str(text or "").upper()
    return "singlemode" if re.search(r"OS1|OS2|SMF|SINGLE", t) else "multimode" if re.search(r"OM[1-5]|MMF|MULTI", t) else "mpo" if re.search(r"MPO|MTP", t) else None


def sfp_advice(result, record, cables):
    """sfp_advice.js advise() on a video run: for each switch, router, firewall or gateway with fibre or SFP ports, which optic and which
    cable fit them, from the port type the video read, the model read on the device and the cable types the matrix lists for it."""
    name = {s["index"]: r for r, s in pair_devices(result, record)}
    items = []
    for s in SDB.run_devices(result):
        if (s["kind"] or "").lower() not in SWITCH_GEAR:
            continue
        groups = {}
        for p in s["ports"]:
            t = (p["type"] or "").upper()
            k = "SFP" if t.startswith("SFP") else "QSFP" if t.startswith("QSFP") else t if t in FIBRE_PORTS else None
            if k:
                groups[k] = groups.get(k, 0) + 1
        if not groups:
            continue
        rec = name.get(s["index"])
        types = {}
        for c in cables:
            if rec and (SDB._norm(c.get("a_device")) in _devkey(rec) or SDB._norm(c.get("z_device")) in _devkey(rec)) and c.get("cable_type"):
                types[c["cable_type"]] = types.get(c["cable_type"], 0) + 1
        ranked = sorted(types.items(), key=lambda x: -x[1])
        sheet = ranked[0][0] if ranked else None
        mode = fibre_mode(sheet)
        for pt, count in groups.items():
            if pt in ("LC", "SC", "FC"):
                optic, cable = "No optic: a passive fibre port", sheet or f"{pt}-{pt} duplex patch cord, the same fibre type as the link"
                note = f"The sheet lists {sheet} for {ranked[0][1]} {'cable' if ranked[0][1] == 1 else 'cables'} on this device." if sheet else "No cable type for this device in the sheet."
            else:
                slot = "OSFP" if pt == "OSFP" else (slot_from_model(s["manufacturer"], s["model"]) or ("QSFP+" if pt == "QSFP" else "SFP"))
                speed, optics = SFP_STANDARDS.get(slot, SFP_STANDARDS["SFP"])
                pick = optics.split(" or ")[1] if mode == "singlemode" and " or " in optics else optics.split(" or ")[0] if mode == "multimode" else optics
                optic = f"{slot} {speed}: {pick}"
                cable = sheet or (CABLE_STANDARDS[mode] if mode else f"{CABLE_STANDARDS['multimode']}; {CABLE_STANDARDS['singlemode']}")
                note = " ".join(x for x in (None if s["model"] else "The model was not read, so the speed is assumed from the port type.",
                                            f"The sheet lists {sheet} for this device, so the optic follows that fibre." if sheet else "No cable type for this device in the sheet.") if x)
            items.append({"index": s["index"], "device": (rec or {}).get("name") or f"D{s['index']}", "model": " ".join(x for x in (s["manufacturer"], s["model"]) if x) or None,
                          "unit": SDB._units(s["ru_start"], s["ru_height"]), "port_type": pt, "count": count, "suggestion": {"optic": optic, "cable": cable, "note": note}})
    return {"items": items}
