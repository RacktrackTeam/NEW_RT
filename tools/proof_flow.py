"""Proof of the one flow, through the screens in headless Chromium at 1280 and 400 px, on the live engine (8700) and the local mocks.
Writes docs/proof/flow/ and prints what each screen showed.

  Profile > Connections   both logins point at the local mocks, Test connection
  Profile > Database copies  read vdo1's workbook with one planted model (its report, then Import), copy in NetBox (one device moved
                          in the mock) and the CMDB (one make changed in the mock), as in tools/proof_today.py
  Home                    the racks with their state, one button Scan a rack; Scan
  Result of our rack      1 the rack picture, 2 devices read (D1 router and D2 firewall: the kind and where it came from),
                          3 the record: for each source, Compare, the steps that name the rack with their proof and confidence,
                          Continue; 4 the differences, short and detailed; 5 the report

  _cache/venv/bin/python tools/proof_flow.py
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
OUT = ROOT / "docs" / "proof" / "flow"
APP = os.environ.get("RT_APP", "http://127.0.0.1:8700")
NETBOX = os.environ.get("RACKTRACK_NETBOX_URL", "http://127.0.0.1:3303")
SNOW = os.environ.get("RACKTRACK_SERVICENOW_URL", "http://127.0.0.1:3304")
RUN = "91af02c09bbc"
PW_FILE = ROOT / "data" / "auth" / "seed-password.txt"     # made by auth.sh, mode 600; read, never printed
LOGIN = os.environ.get("RT_PROOF_LOGIN") or (("owner:" + PW_FILE.read_text().strip()) if PW_FILE.exists() else None)   # the platform owner sees the stored runs from before sign-in


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
        if row[head.index("Device_ID")].value == "SP-R1-U15-SW02":
            row[head.index("Model")].value = "TL-SG3428"
    wb.save(wb_path)
    return wb_path


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    wb = plant()
    seen, errors = {}, []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
        for width, height in ((1280, 900), (400, 860)):
            pg = b.new_page(viewport={"width": width, "height": height})
            pg.on("dialog", lambda d: d.accept())
            pg.on("pageerror", lambda e, w=width: errors.append(f"{w}: page error: {e}"))
            pg.on("response", lambda r, w=width: errors.append(f"{w}: {r.status} {r.request.method} {r.url.split('8700', 1)[-1]}") if r.status >= 400 else None)
            shot = lambda name, wait=900: pg.wait_for_timeout(wait) or pg.screenshot(path=str(OUT / f"{name}_{width}.png"), full_page=True)  # noqa: E731
            text = lambda sel: pg.inner_text(sel) if pg.locator(sel).count() else ""  # noqa: E731

            def secshot(name, key, part, wait=900):            # one step of the result page: its heading down to the end of its part
                pg.wait_for_timeout(wait)
                top = pg.locator(f"#sec-{key}").bounding_box(); bot = pg.locator(f"#{part}").bounding_box()
                sy = pg.evaluate("window.scrollY")
                pg.screenshot(path=str(OUT / f"{name}_{width}.png"), full_page=True,
                              clip={"x": 0, "y": top["y"] + sy - 8, "width": width, "height": bot["y"] + bot["height"] - top["y"] + 16})

            pg.goto(APP + "/app/#signin"); pg.wait_for_timeout(800)
            if LOGIN:
                email, pw = LOGIN.split(":", 1)
                pg.fill("#si-user", email); pg.fill("#si-pass", pw)
            else:
                pg.fill("#si-name", "Aasritha")
            pg.click("#signin button[type=submit]"); pg.wait_for_selector('[data-screen="home"]:not([hidden])', timeout=15000)
            if width == 1280:                       # Profile: logins to the mocks, then the copies
                pg.goto(APP + "/app/#connections"); pg.wait_for_selector("form[data-cn=netbox]", timeout=15000)
                for t, addr in (("netbox", NETBOX), ("cmdb", SNOW)):
                    f = f'form[data-cn="{t}"]'
                    pg.fill(f + " input[name=address]", addr); pg.fill(f + " input[name=username]", "proof"); pg.fill(f + " input[name=password]", "not-a-real-password")
                    pg.click(f + " button[type=submit]"); pg.wait_for_timeout(700); pg.click(f + " [data-cntest]"); pg.wait_for_timeout(1500)
                seen["connections"] = [text(f'form[data-cn="{t}"] .cnstate') for t in ("netbox", "cmdb")]
                shot("connections")
                pg.goto(APP + "/app/#copies"); pg.wait_for_selector("#cp-in", timeout=15000)
                pg.set_input_files("#df-file", str(wb)); pg.click("#df-xlsx"); pg.wait_for_selector("#df-import", timeout=20000)
                shot("copies_workbook_report")
                pg.click("#df-import"); pg.wait_for_timeout(1800)
                for src in ("netbox", "cmdb"):
                    pg.click(f'[data-dfimp="{src}"]'); pg.wait_for_timeout(1800)
                seen["copies"] = pg.eval_on_selector_all("#cp-list tr[data-copy]", "rs => rs.slice(0, 3).map(r => r.innerText.replace(/\\s+/g, ' '))")
            pg.goto(APP + "/app/#copies"); pg.wait_for_selector("#cp-list table, #cp-list .card", timeout=15000); shot("copies")
            # Home and Scan
            pg.goto(APP + "/app/#home"); pg.wait_for_selector("#home-racks a.rkrow", timeout=15000); shot("home", 1200)
            seen[(width, "home")] = pg.eval_on_selector_all("#home-racks a.rkrow", "as => as.map(a => a.innerText.replace(/\\s+/g, ' '))")
            pg.goto(APP + "/app/#sweep"); shot("scan")
            # the result of our rack, from Home
            pg.goto(APP + "/app/#home"); pg.wait_for_selector(f'#home-racks a[href="#result/{RUN}"]', timeout=15000)
            pg.click(f'#home-racks a[href="#result/{RUN}"]'); pg.wait_for_selector("#res-rack .rackshot img", timeout=20000); pg.wait_for_selector("#cmp-src", timeout=20000)
            shot("result_page", 2500); secshot("rack_picture", "rack", "res-rack", 300)
            seen[(width, "headings")] = pg.eval_on_selector_all(".rsh", "hs => hs.map(h => h.innerText.replace(/\\n+/g, ' | '))")
            for i in (1, 2):                         # D1 router, D2 firewall: one kind, its source in words
                if i == 1:
                    pg.goto(f"{APP}/app/#result/{RUN}/devices")
                else:
                    pg.click("#dv-another")                       # Pick another device, as a person would
                pg.wait_for_selector("#dv-list .prow", timeout=15000); pg.wait_for_timeout(800)
                pg.click(f'#dv-list [data-dev="{i}"]'); pg.wait_for_selector("#dv-one:not([hidden]) .onet", timeout=15000)
                seen[(width, f"D{i}")] = [text("#dv-one .onet"), text("#dv-one .dvsum"), text("#dv-one .dfacts")[:200]]
                secshot(f"device_D{i}", "devices", "res-devices", 1200)
            pg.click("#dv-another"); pg.wait_for_selector("#dv-list .prow", timeout=15000)
            seen[(width, "devices list")] = pg.eval_on_selector_all("#dv-list .prow", "rs => rs.slice(0, 3).map(r => r.innerText.replace(/\\s+/g, ' '))")
            # 3 the record and 4 the differences, for each source
            pg.goto(f"{APP}/app/#result/{RUN}/record"); pg.wait_for_selector("#cmp-src", timeout=20000)
            for src in ("excel", "netbox", "cmdb"):
                pg.select_option("#cmp-src", src); pg.wait_for_timeout(400)
                pg.click("#cmp-go"); pg.wait_for_selector(".idcard", timeout=15000); pg.wait_for_timeout(800)
                seen[(width, f"{src} identify")] = [text(".idcard .verdict")] + pg.eval_on_selector_all(".idsteps li", "ls => ls.map(l => l.innerText.replace(/\\n+/g, ' | '))")
                secshot(f"record_{src}", "record", "res-record")
                pg.click("#cmp-continue"); pg.wait_for_selector("#diff-top .seg2", timeout=15000); pg.wait_for_timeout(800)
                seen[(width, f"{src} differences")] = pg.eval_on_selector_all("#diff-top .dflines li", "ls => ls.map(l => l.dataset.dftype + ': ' + l.querySelector('p').textContent)")
                secshot(f"differences_{src}", "diff", "res-diff")
                if src == "netbox":
                    pg.click('[data-dfmode="detailed"]'); secshot("differences_detailed_netbox", "diff", "res-diff", 1500)
                    pg.click('[data-dfmode="short"]'); pg.wait_for_timeout(300)
            secshot("report", "report", "res-report", 1200)
            seen[(width, "report compare line")] = text("#rp-cmp")
            pg.goto(APP + "/app/#more"); shot("profile", 1200)
            pg.close()
        b.close()
    for k, v in seen.items():
        print(k, *(v if isinstance(v, list) else [v]), sep="\n  ")
    print("errors on the page:", *(errors or ["none"]), sep="\n  ")


if __name__ == "__main__":
    main()
