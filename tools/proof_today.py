"""Proof for the Drift tab: vdo1's run (the office rack SP-HYD-RM01-R01-R1) against the three databases, each holding the
rack's NetBox CSV record with one planted difference:
  Excel   SP-R1-U15-SW02's model changed to TL-SG3428 in a copy of the vdo1 workbook   -> Wrong model
  NetBox  SP-R1-U7-PP3 moved from U7 to U8 in the NetBox mock                         -> Moved
  CMDB    SP-R1-U18-SW04's manufacturer changed to Cisco in the ServiceNow mock       -> Wrong make
Everything goes through the screens (headless Chromium): the workbook upload, the two Copy in buttons, the source buttons.
Needs the engine on 8700 (engine.sh) and the mocks on 3301 and 5301 (sources.sh). Writes docs/proof/today/.

  _cache/venv/bin/python tools/proof_today.py [run id]
"""
import json
import os
import pathlib
import shutil
import sys
import urllib.request

import openpyxl
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proof" / "today"
APP = "http://127.0.0.1:8700"
NETBOX, SNOW = "http://127.0.0.1:3301", "http://127.0.0.1:5301"
RUN = sys.argv[1] if len(sys.argv) > 1 else "91af02c09bbc"


def call(method, url, body=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def plant():
    call("POST", NETBOX + "/__mock/reset"); call("POST", SNOW + "/__mock/reset")
    dev = call("GET", NETBOX + "/api/dcim/devices/?name=SP-R1-U7-PP3")["results"][0]
    call("PATCH", f"{NETBOX}/api/dcim/devices/{dev['id']}/", {"position": 8})
    sw = call("GET", SNOW + "/api/now/table/cmdb_ci_ip_switch?sysparm_query=name=SP-R1-U18-SW04")["result"][0]
    call("PATCH", f"{SNOW}/api/now/table/cmdb_ci_ip_switch/{sw['sys_id']}", {"manufacturer": "Cisco"})
    wb_path = ROOT / "data" / "sources" / "vdo1_planted.xlsx"; wb_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "modules" / "sources" / "ground_truth" / "vdo1.xlsx", wb_path)
    wb = openpyxl.load_workbook(wb_path); ws = wb["DEVICE_INVENTORY"]; head = [c.value for c in ws[1]]
    for row in ws.iter_rows(min_row=2):
        if row[head.index("Device_Name")].value == "SP-R1-U15-SW02" or row[head.index("Device_ID")].value == "SP-R1-U15-SW02":
            row[head.index("Model")].value = "TL-SG3428"
    wb.save(wb_path)
    return wb_path


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    wb_path = plant()
    found = {}
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)   # PW_CHROMIUM: an installed headless shell when this Playwright's own is missing
        for width, height in ((1280, 900), (400, 860)):
            pg = b.new_page(viewport={"width": width, "height": height})
            pg.goto(APP + "/app/#signin"); pg.wait_for_timeout(800)
            pg.evaluate("fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'Proof'})})")
            pg.wait_for_timeout(500)
            pg.goto(f"{APP}/app/#result/{RUN}/drift"); pg.reload(); pg.wait_for_selector("#df-top .dfsrc", timeout=20000)
            for src in ("excel", "netbox", "cmdb"):
                if width == 1280:                                   # copy each database in once, through the screen
                    if src == "excel":
                        pg.set_input_files("#df-file", str(wb_path)); pg.click("#df-xlsx")
                    else:
                        pg.click(f'[data-dfimp="{src}"]')
                    pg.wait_for_timeout(1500)
                pg.click(f'[data-dfsrc="{src}"]'); pg.wait_for_timeout(1200)
                found[(width, src)] = pg.eval_on_selector_all("#df-top tr[data-dftype]", "rs => rs.map(r => r.dataset.dftype + ': ' + r.querySelector('p').textContent)")
                pg.screenshot(path=str(OUT / f"drift_{src}_{width}.png"), full_page=True)
            pg.close()
        b.close()
    for k, v in found.items():
        print(k, *v, sep="\n  ")


if __name__ == "__main__":
    main()
