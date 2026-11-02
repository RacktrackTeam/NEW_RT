"""Proof for the afternoon's four pieces, through the screens in headless Chromium at 1280 and 400 px, on vdo1's stored run
(91af02c09bbc, the office rack SP-HYD-RM01-R01-R1). Writes docs/proof/afternoon/.

  1 Connections   save a NetBox login (address of the local NetBox mock, a token) and a ServiceNow login (an address where nothing
                  listens), Test connection on both: NetBox Connected, ServiceNow Not connected; the page never holds a secret
  2 Excel report  a broken workbook (a device in an unknown rack, a duplicate id, a device past the rack height, no PORT_INVENTORY)
                  shows every problem with counts; vdo1's workbook shows its report and is imported with Import
  3 Port drift    vdo1's workbook with three planted port facts on the router at U20 (port 1 expected empty, port 2 expected
                  occupied, port 3 expected capped) gives Extra cable, Missing cable and Dust cap missing on the Drift tab
  4 Copies        the Database copies page lists every copy (Excel v1, v2, NetBox through the saved login), the active ones marked;
                  Delete on the newest Excel copy brings v1 back

Two ways to run it:
  live (default)  every request goes to the engine on 127.0.0.1:8700 (engine.sh) with the mocks on 3301 and 5301 (sources.sh)
  --shim          the engine running on 8700 is older than these routes and could not be restarted from this session, so the new
                  routes (connections, sources, copies, drift) are answered in this process by the same modules the engine calls
                  (src/connections.py, src/source_db.py) on a scratch database; the screens, sign-in and the run's view still come from
                  the live engine, and NetBox and ServiceNow are still read over HTTP from the local mocks.

  _cache/venv/bin/python tools/proof_afternoon.py [--shim]
"""
import json
import os
import pathlib
import re
import shutil
import sys

import openpyxl
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "tests"))
OUT = ROOT / "docs" / "proof" / "afternoon"
APP = "http://127.0.0.1:8700"
RUN = "91af02c09bbc"
SHIM = "--shim" in sys.argv
WORK = ROOT / "data" / "proof_afternoon_work"; shutil.rmtree(WORK, ignore_errors=True); WORK.mkdir(parents=True)
SECRETS = ("tok-PROOF-7731", "pw-PROOF-5512")
if SHIM:                                              # a scratch database and key: the live data/sources.db is not touched
    os.environ["RACKTRACK_SOURCES_DB"] = str(WORK / "sources.db"); os.environ["RACKTRACK_CONN_KEY_FILE"] = str(WORK / "connections.key")
import connections as CONN  # noqa: E402
import source_db as SDB  # noqa: E402
from test_port_drift import plant_ports  # noqa: E402
from test_workbook_checks import DEVS, RACKS, book  # noqa: E402


# ------------------------------------------------------------------ the shim: the engine's new routes, answered by the same modules
class Shim:
    def __init__(self):
        self.status = json.loads((ROOT / "data" / "runs" / RUN / "status.json").read_text())
        self.result = json.loads((ROOT / "data" / "runs" / RUN / "result.json").read_text())
        self.db = {}

    def drift_view(self):                         # server/app.py drift_view, on this run
        rid = (self.status.get("rack_id") or {}).get("id"); have = SDB.racks(); db = self.db
        source = db.get("source") or next((s for s in SDB.SOURCES if any(r["source"] == s for r in have)), None)
        out = {"rack_id": rid, "source": source, "sources": SDB.SOURCES, "imported": {s: next((r["imported_at"] for r in have if r["source"] == s), None) for s in SDB.SOURCES},
               "candidates": [r for r in have if r["source"] == source], "match": None, "record": [], "drift": None}
        if not source:
            return out
        if db.get("rack_key") and db.get("source") == source and any(r["rack_key"] == db["rack_key"] for r in out["candidates"]):
            out["match"] = {"rack_key": db["rack_key"], "how": "picked", "why": "Picked by Proof."}
        else:
            out["match"] = SDB.match(source, rid)
        if out["match"]:
            out["record"] = SDB.devices(source, out["match"]["rack_key"])
            out["drift"] = SDB.drift(self.result, out["record"], SDB.ports(source, out["match"]["rack_key"]) if source == "excel" else None)
        return out

    def answer(self, method, path, body, headers):
        org = "default"
        if path == "/api/connections" and method == "GET":
            return 200, {"connections": CONN.meta(org)}
        m = re.fullmatch(r"/api/connections/(netbox|cmdb)(/test)?", path)
        if m and method == "POST" and m.group(2):
            CONN.test(org, m.group(1), SDB._get); return 200, {"connections": CONN.meta(org)}
        if m and method == "POST":
            b = json.loads(body or b"{}")
            try:
                CONN.save(org, m.group(1), b.get("address", ""), b.get("username", ""), b.get("password", ""), b.get("token", ""), by="Proof")
            except ValueError as e:
                return 400, {"error": str(e)}
            return 200, {"connections": CONN.meta(org)}
        if m and method == "DELETE":
            CONN.remove(org, m.group(1)); return 200, {"connections": CONN.meta(org)}
        if path == "/api/sources/excel" and method == "POST":
            # Chromium hands a route no body for a file upload, so the shim takes the file the proof itself chose (it is in WORK)
            fname = self.next_upload.name
            f = WORK / ("workbook_0000beef_" + fname); shutil.copy(self.next_upload, f)
            r = SDB.validate_excel(f); r["upload"] = f.name; return 200, r
        if path == "/api/sources/excel/import":
            return 200, SDB.import_excel(WORK / json.loads(body)["upload"])
        m = re.fullmatch(r"/api/sources/(netbox|cmdb)/import", path)
        if m:
            return 200, (SDB.import_netbox if m.group(1) == "netbox" else SDB.import_cmdb)(org)
        if path == "/api/sources/copies":
            return 200, {"sources": SDB.SOURCES, "copies": SDB.copies()}
        m = re.fullmatch(r"/api/sources/copies/(\d+)", path)
        if m and method == "DELETE":
            SDB.delete_copy(int(m.group(1))); return 200, {"sources": SDB.SOURCES, "copies": SDB.copies()}
        if path == f"/api/video/{RUN}/drift":
            return 200, self.drift_view()
        if path == f"/api/video/{RUN}/database":
            b = json.loads(body); self.db = {"source": b.get("source"), "rack_key": b.get("rack_key")}; return 200, self.drift_view()
        return None

    def route(self, route, request):
        path = request.url.split("127.0.0.1:8700", 1)[1].split("?")[0]
        got = self.answer(request.method, path, request.post_data_buffer, request.headers)
        if got is None:
            return route.continue_()
        route.fulfill(status=got[0], content_type="application/json", body=json.dumps(got[1]))


# ------------------------------------------------------------------ the run through the screens
def main():
    OUT.mkdir(parents=True, exist_ok=True)
    broken = book(WORK / "broken_workbook.xlsx", {"RACK_INVENTORY": RACKS, "DEVICE_INVENTORY": DEVS})
    plain = WORK / "vdo1.xlsx"; shutil.copy(ROOT / "modules" / "sources" / "ground_truth" / "vdo1.xlsx", plain)
    planted = plant_ports(plain, WORK / "vdo1_ports_planted.xlsx")
    shim, seen = Shim() if SHIM else None, {}
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
        for width, height in ((1280, 900), (400, 860)):
            pg = b.new_page(viewport={"width": width, "height": height})
            pg.on("dialog", lambda d: d.accept())
            if shim:
                pg.route("**/api/**", shim.route)
            pg.goto(APP + "/app/#signin"); pg.wait_for_timeout(800)
            pg.evaluate("fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'Proof'})})")
            pg.wait_for_timeout(400)
            def up(path):
                pg.set_input_files("#df-file", str(path))
                if shim:
                    shim.next_upload = path
            shot = lambda name: pg.wait_for_timeout(1200) or pg.screenshot(path=str(OUT / f"{name}_{width}.png"), full_page=True)
            # 1 Connections
            pg.goto(APP + "/app/#more"); pg.reload(); pg.wait_for_timeout(900); shot("profile")
            pg.goto(APP + "/app/#connections"); pg.wait_for_selector("form[data-cn=netbox]", timeout=15000)
            if width == 1280:
                shot("connections_empty")
                f = 'form[data-cn="netbox"]'
                pg.fill(f + ' input[name=address]', "http://127.0.0.1:3303"); pg.fill(f + ' input[name=token]', SECRETS[0]); pg.click(f + ' button[type=submit]'); pg.wait_for_timeout(700)
                f = 'form[data-cn="cmdb"]'
                pg.fill(f + ' input[name=address]', "http://127.0.0.1:3399"); pg.fill(f + ' input[name=username]', "proof.user"); pg.fill(f + ' input[name=password]', SECRETS[1])
                pg.click(f + ' button[type=submit]'); pg.wait_for_timeout(700)
                for t in ("netbox", "cmdb"):
                    pg.click(f'form[data-cn="{t}"] [data-cntest]'); pg.wait_for_timeout(1500)
            html = pg.content()
            seen[(width, "connections")] = [pg.inner_text(f'form[data-cn="{t}"] .cnstate') for t in ("netbox", "cmdb")]
            seen[(width, "secret on the page")] = [s for s in SECRETS if s in html]
            shot("connections")
            # 2 and 3 the Drift tab: the report, then Import
            pg.goto(f"{APP}/app/#result/{RUN}/drift"); pg.wait_for_selector("#df-top .dfsrc", timeout=20000)
            up(broken); pg.click("#df-xlsx"); pg.wait_for_selector("#df-report .wbrep", timeout=15000)
            seen[(width, "broken workbook")] = pg.inner_text("#df-report .wbkinds").split("\n")
            pg.locator("#df-report").scroll_into_view_if_needed(); shot("excel_report_broken")
            if width == 1280:
                for wb in (plain, planted):
                    up(wb); pg.click("#df-xlsx"); pg.wait_for_selector("#df-import", timeout=15000)
                    if wb is planted:
                        shot("excel_report_vdo1")
                    pg.click("#df-import"); pg.wait_for_timeout(1800)
                pg.click('[data-dfimp="netbox"]'); pg.wait_for_timeout(1800)       # through the saved NetBox login (the mock's address)
            pg.click('[data-dfsrc="excel"]'); pg.wait_for_timeout(1500)
            rows = pg.eval_on_selector_all("#df-top tr[data-dftype]", "rs => rs.map(r => r.dataset.dftype + ': ' + r.querySelector('p').textContent)")
            seen[(width, "drift rows")] = [r for r in rows if not r.startswith("port_type")] + [f"... and {sum(r.startswith('port_type') for r in rows)} port_type rows"]
            seen[(width, "drift summary")] = pg.inner_text("#df-top .card >> nth=2")
            pg.locator("#df-top table.dftable").scroll_into_view_if_needed(); shot("drift_ports")
            # 4 Copies
            pg.goto(APP + "/app/#copies"); pg.wait_for_selector("#cp-list table, #cp-list .card", timeout=15000); pg.wait_for_timeout(500)
            seen[(width, "copies")] = pg.eval_on_selector_all("#cp-list tr[data-copy]", "rs => rs.map(r => [...r.querySelectorAll('td')].slice(0, 8).map(t => t.textContent).join(' | '))")
            shot("copies")
            if width == 400:
                pg.click("#cp-list [data-cpdel] >> nth=1"); pg.wait_for_timeout(900)       # the newest Excel copy (the NetBox copy is first)
                seen[(width, "copies after delete")] = pg.eval_on_selector_all("#cp-list tr[data-copy]", "rs => rs.map(r => [...r.querySelectorAll('td')].slice(0, 8).map(t => t.textContent).join(' | '))")
                shot("copies_after_delete")
            pg.close()
        b.close()
    for k, v in seen.items():
        print(k, *(v if isinstance(v, list) else [v]), sep="\n  ")
    print("mode:", "shim (new routes answered by src/ modules in this process)" if SHIM else "live engine")


if __name__ == "__main__":
    main()
