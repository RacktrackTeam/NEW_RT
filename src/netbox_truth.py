"""Ground truth for one rack from a NetBox device export (netbox_devices.csv: Position, U Height, Role, Rack face)
and the scoring of a rack result against it. Units are numbered from the bottom, as in NetBox and in our ladders
(u01 = U1). A rack face is scored on its own; the rear face's devices are left out.
"""
import csv

ROLE_TO_TYPE = {"network switch": "Switch", "switch": "Switch", "patch panel": "Patch Panel", "firewall": "Firewall",
                "router": "Router", "pdu": "PDU", "ups": "UPS", "server": "Server", "storage": "Storage unit",
                "load balancer": "Load Balancer", "gateway": "Gateway"}
NO_CLASS = {"cable manager"}     # holds a unit in NetBox; none of our models has a class for it (the classifier calls it free)


def load(path, face="front"):
    """The devices with a position on one face, bottom-up: name, role, model, our type name (None when our models have
    no class for the role), u_from, u_to, height."""
    devices = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            pos = (r.get("Position") or "").strip()
            if not pos:
                continue
            rf = (r.get("Rack face") or "").strip().lower()
            if face and rf and rf != face:
                continue
            u = int(float(pos))
            h = max(1, int(float((r.get("U Height") or "1").strip() or 1)))
            role = (r.get("Role") or "").strip()
            devices.append({"name": (r.get("Name") or "").strip(), "role": role, "model": (r.get("Type") or "").strip(),
                            "type": ROLE_TO_TYPE.get(role.lower()), "no_class": role.lower() in NO_CLASS,
                            "u_from": u, "u_to": u + h - 1, "height": h})
    devices.sort(key=lambda d: d["u_from"])
    return devices


def _unum(label):
    return int(str(label).lower().lstrip("u"))


def _tally(items):
    c = {"right": 0, "wrong": 0, "uncertain": 0, "missing": 0}
    for it in items:
        c[it["verdict"]] = c.get(it["verdict"], 0) + 1
    c["n"] = len(items)
    c["pct"] = round(100.0 * c["right"] / len(items), 1) if items else None
    return c


def _dtally(items):
    typed = [r for r in items if r["type_right"] is not None]
    found = sum(r["found"] for r in items)
    return {"n": len(items), "found": found, "pct": round(100.0 * found / len(items), 1) if items else None,
            "typed": len(typed), "type_right": sum(r["type_right"] for r in typed)}


def score(truth, rows, devices):
    """Score a rack answer against the truth. rows: [{"unit": "u01", "status"}]; devices: [{"index", "units", "height",
    "type"}]. Three scores, each also without the units that hold a device our models have no class for:
      units   - occupied / free per unit (uncertain and missing units are counted apart, never as right);
      seams   - every seam between two occupied units: new device or the same device;
      devices - every device of the truth: found with exactly its span, and its type when our models have the class."""
    top_truth = max([d["u_to"] for d in truth], default=0)
    ours = {_unum(r["unit"]): r["status"] for r in rows}
    top = max([top_truth] + list(ours))
    at = {}
    for d in truth:
        for u in range(d["u_from"], d["u_to"] + 1):
            at[u] = d
    dev_of = {}
    for d in devices:
        for lab in d["units"]:
            dev_of[_unum(lab)] = d
    unit_rows = []
    for u in range(1, top + 1):
        t, s = at.get(u), ours.get(u)
        if s is None:
            v = "missing"
        elif s == "uncertain":
            v = "uncertain"
        elif (s == "occupied") == (t is not None):
            v = "right"
        else:
            v = "wrong"
        unit_rows.append({"unit": u, "truth": "occupied" if t else "free", "truth_device": t["name"] if t else None,
                          "truth_role": t["role"] if t else None, "no_class": bool(t and t["no_class"]),
                          "ours": s or "missing", "verdict": v})
    seam_rows = []
    for u in range(1, top):
        a, b = at.get(u), at.get(u + 1)          # a below, b above
        if a is None or b is None:
            continue
        truth_new = a is not b
        oa, ob = dev_of.get(u), dev_of.get(u + 1)
        if oa is None or ob is None:
            ours_new, v = None, "wrong"           # we see no device on one side of the seam
        else:
            ours_new = oa is not ob
            v = "right" if ours_new == truth_new else "wrong"
        seam_rows.append({"below": u, "above": u + 1, "truth": "new device" if truth_new else "same device",
                          "ours": None if ours_new is None else ("new device" if ours_new else "same device"),
                          "no_class": a["no_class"] or b["no_class"], "verdict": v})
    dev_rows = []
    for t in truth:
        span = list(range(t["u_from"], t["u_to"] + 1))
        m = next((d for d in devices if sorted(_unum(x) for x in d["units"]) == span), None)
        ours_type = m.get("type") if m else None
        dev_rows.append({"name": t["name"], "role": t["role"], "units": f"U{t['u_from']}" + (f"–U{t['u_to']}" if t["height"] > 1 else ""),
                         "height": t["height"], "truth_type": t["type"], "no_class": t["no_class"], "found": m is not None,
                         "ours": None if m is None else f"D{m['index']} · {m['height']}U" + (f" · {ours_type}" if ours_type else ""),
                         "type_right": None if (m is None or t["type"] is None) else ours_type == t["type"]})
    return {"top": top, "ours_units": len(ours), "ours_devices": len(devices),
            "units": {"all": _tally(unit_rows), "without_no_class": _tally([r for r in unit_rows if not r["no_class"]])},
            "seams": {"all": _tally(seam_rows), "without_no_class": _tally([r for r in seam_rows if not r["no_class"]])},
            "devices": {"all": _dtally(dev_rows), "without_no_class": _dtally([r for r in dev_rows if not r["no_class"]])},
            "unit_rows": unit_rows, "seam_rows": seam_rows, "device_rows": dev_rows}
