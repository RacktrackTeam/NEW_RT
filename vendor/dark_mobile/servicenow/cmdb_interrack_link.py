#!/usr/bin/env python3
"""
Push the SYNTHESIZED rack-to-rack uplinks of a rack-group into the ServiceNow
CMDB as `cmdb_rel_ci` "Connects to" relationships between the two racks'
uplink switch CIs.

There is no live network between two independently-photographed racks, so —
exactly like the single-rack cabling that synth.py fabricates — these links are
dummy but consistent. This script is self-sufficient: it ensures each rack CI
and its uplink-switch CI exist (creating them if the per-rack push hasn't run),
then wires the "Connects to" relationship. Every CI/rel it creates is tagged
`synthetic_data=true` so a future "promote to real" step can find them.

Usage:
    python cmdb_interrack_link.py --group-id GRP-XXXXXXXXXXXX [--json]

Best-effort: invoked fire-and-forget by the server. Exits non-zero on failure
(logged by the caller) but never blocks the app — the combined-topology view
renders from the synthesized links regardless of ServiceNow's state.
"""
import argparse
import json
import os
import sqlite3
import sys
import time

import requests
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
AUTH_DB = os.path.join(ROOT, "server", "data", "auth.db")
OUTPUTS = os.path.join(ROOT, "outputs")

from servicenow import escape_query_value as _q  # noqa: E402 (needs sys.path above)

CONNECTS_TO_REL_TYPE = "5599a965c0a8010e00da3b58b113d70e"  # Connects to::Connected by
SYNTH_TAG = "synthetic_data=true; provenance=inter-rack-synth"

# This script fabricates "Connects to" relationships between switches that were
# never physically cabled — there is no live network between two independently
# photographed racks. Writing those into a customer's production CMDB corrupts
# their system of record, so it is OFF unless the operator explicitly opts in.
ALLOW_ENV = "CMDB_ALLOW_SYNTHETIC_LINKS"

LINK_ROLES = ["Primary uplink", "Redundant uplink", "Cross-connect", "Backup link"]


# ── Local data: group members + each rack's synthesized topology ──────────
def load_group_members(group_id):
    if not os.path.exists(AUTH_DB):
        raise RuntimeError(f"auth.db not found at {AUTH_DB}")
    con = sqlite3.connect(AUTH_DB)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT rack_id, position, label FROM rack_group_members "
            "WHERE group_id = ? ORDER BY position ASC",
            (group_id,),
        ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def read_topology(rack_id):
    p = os.path.join(OUTPUTS, rack_id, "topology.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def uplink_switch(topo):
    """The rack's uplink end: the out-of-rack aggregation/core switch if present,
    else the top-most in-rack switch. Returns {name, model, role} or None."""
    if not topo or not isinstance(topo.get("devices"), list):
        return None
    switches = [d for d in topo["devices"] if d.get("class") == "switch"]
    if not switches:
        return None

    def sort_key(d):
        core_first = 1 if d.get("in_rack") is False else 0
        return (core_first, d.get("u_position") or 0)

    sw = sorted(switches, key=sort_key, reverse=True)[0]
    role = "core" if sw.get("in_rack") is False else "tor"
    ports = sw.get("ports") or []
    uplinks = [p for p in ports if p.get("is_uplink")]
    pool = uplinks or ports
    port = (pool[0].get("label") or pool[0].get("name")) if pool else "Up1"
    return {"name": sw.get("name"), "model": sw.get("model"), "role": role, "port": port}


# ── ServiceNow REST helpers (mirror bootstrap_cmdb_full.py) ───────────────
class SN:
    def __init__(self, instance, user, password):
        self.base = f"https://{instance}.service-now.com/api/now"
        self.auth = (user, password)
        self.headers = {"Accept": "application/json", "Content-Type": "application/json"}

    def _retry(self, fn):
        last = None
        for attempt in range(4):
            try:
                return fn()
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
                last = e
                time.sleep(2 * (attempt + 1))
        raise last

    def find(self, table, query):
        def _do():
            r = requests.get(f"{self.base}/table/{table}",
                             params={"sysparm_query": query, "sysparm_limit": 1},
                             auth=self.auth, headers=self.headers, timeout=90)
            r.raise_for_status()
            return r.json().get("result", [])
        rows = self._retry(_do)
        return rows[0] if rows else None

    def create(self, table, payload):
        def _do():
            r = requests.post(f"{self.base}/table/{table}", json=payload,
                              auth=self.auth, headers=self.headers, timeout=90)
            r.raise_for_status()
            return r.json()["result"]
        return self._retry(_do)

    def upsert(self, table, name, extra_query, payload):
        query = f"name={_q(name)}"
        if extra_query:
            query += f"^{extra_query}"
        ex = self.find(table, query)
        if ex:
            return ex, False
        created = self.create(table, {"name": name, **payload})
        return created, True

    def ensure_rel(self, parent_sys_id, child_sys_id, rel_type):
        existing = self.find(
            "cmdb_rel_ci",
            f"parent={_q(parent_sys_id)}^child={_q(child_sys_id)}"
            f"^type={_q(rel_type)}")
        if existing:
            return False
        # Tag the relationship itself, not just the CIs it joins, so a fabricated
        # "Connects to" row is unmistakable in the customer's CMDB. `comments`
        # is standard on cmdb_rel_ci; if a given instance lacks it the field is
        # simply ignored by the Table API.
        self.create("cmdb_rel_ci",
                    {"parent": parent_sys_id, "child": child_sys_id,
                     "type": rel_type, "comments": SYNTH_TAG})
        return True


def ensure_rack_ci(sn, rack_id, topo):
    rack_name = (topo or {}).get("rackName") or f"RACK-{rack_id.replace('RK-', '')}"
    rack, _ = sn.upsert("cmdb_ci_rack", rack_name,
                        f"u_racktrack_scan_id={_q(rack_id)}",
                        {"u_racktrack_scan_id": rack_id,
                         "u_racktrack_rack_id": rack_id, "comments": SYNTH_TAG})
    return rack, rack_name


def ensure_switch_ci(sn, rack, rack_id, sw):
    # Scope the switch upsert to its rack (u_racktrack_rack_id). Two racks can
    # have identically-named uplink switches, so a bare name match would collide
    # across racks the same way cmdb_apply's device upsert did.
    switch, made = sn.upsert("cmdb_ci_ip_switch", sw["name"],
                             f"u_racktrack_rack_id={_q(rack_id)}",
                             {"model_id": sw.get("model") or "",
                              "u_racktrack_rack_id": rack_id,
                              "comments": SYNTH_TAG})
    # Contain the switch in its rack (Contains::Contained by) — best-effort.
    contains = sn.find("cmdb_rel_type", "name=Contains::Contained by")
    if contains:
        sn.ensure_rel(rack["sys_id"], switch["sys_id"], contains["sys_id"])
    return switch


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group-id", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    load_dotenv()
    load_dotenv(os.path.join(HERE, ".env"))

    def out(obj, code):
        if args.json:
            print(json.dumps(obj))
        else:
            print(obj)
        return code

    # Safety gate: refuse to write fabricated inter-rack links into the live
    # CMDB unless the operator explicitly opted in. Exit 0 so the fire-and-
    # forget caller treats this as a clean no-op, not a failure to alert on.
    if os.environ.get(ALLOW_ENV) != "1":
        return out({"ok": True, "skipped": True, "count": 0, "links": [],
                    "reason": f"synthetic inter-rack CMDB writes are disabled; "
                              f"set {ALLOW_ENV}=1 to enable"}, 0)

    instance = os.environ.get("SN_INSTANCE")
    user = os.environ.get("SN_USER")
    password = os.environ.get("SN_PASSWORD")
    if not (instance and user and password):
        return out({"ok": False, "error": "ServiceNow not configured (SN_INSTANCE/SN_USER/SN_PASSWORD)"}, 2)

    members = load_group_members(args.group_id)
    if len(members) < 2:
        return out({"ok": False, "error": f"group {args.group_id} has < 2 members"}, 3)

    # Attach each member's uplink switch (from its synthesized topology).
    enriched = []
    for m in members:
        topo = read_topology(m["rack_id"])
        up = uplink_switch(topo)
        enriched.append({**m, "topo": topo, "uplink": up})

    sn = SN(instance, user, password)
    pushed = []
    for i in range(len(enriched) - 1):
        a, b = enriched[i], enriched[i + 1]
        if not a["uplink"] or not b["uplink"]:
            continue
        rack_a, name_a = ensure_rack_ci(sn, a["rack_id"], a["topo"])
        rack_b, name_b = ensure_rack_ci(sn, b["rack_id"], b["topo"])
        sw_a = ensure_switch_ci(sn, rack_a, a["rack_id"], a["uplink"])
        sw_b = ensure_switch_ci(sn, rack_b, b["rack_id"], b["uplink"])
        # The inter-rack uplink itself: Connects to between the two switches.
        made = sn.ensure_rel(sw_a["sys_id"], sw_b["sys_id"], CONNECTS_TO_REL_TYPE)
        pushed.append({
            "role": LINK_ROLES[0],
            "src": {"rack": name_a, "switch": a["uplink"]["name"], "port": a["uplink"]["port"]},
            "dst": {"rack": name_b, "switch": b["uplink"]["name"], "port": b["uplink"]["port"]},
            "created": made,
        })

    return out({"ok": True, "groupId": args.group_id, "links": pushed, "count": len(pushed)}, 0)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 — best-effort; surface the reason and exit non-zero
        msg = {"ok": False, "error": str(e)}
        print(json.dumps(msg))
        sys.exit(1)
