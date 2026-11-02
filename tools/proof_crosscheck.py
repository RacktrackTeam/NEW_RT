"""Cross-check: the whole flow walked as a person would, through the screens in headless Chromium at 1280 and 400 px, on the live
engine (8700) and the local mocks (3301 NetBox, 5301 ServiceNow). Writes docs/proof/crosscheck/ and prints what each screen showed.

  sign in by typing a name, Home, My racks, the stored run of the office rack SP-HYD-RM01-R01-R1 (vdo1, 91af02c09bbc),
  its rack, devices, layout and report, the Drift tab: read modules/sources/ground_truth/vdo1.xlsx, see its report, Import,
  copy in from NetBox and from the CMDB (both mocks reset first, nothing planted), each source's rows,
  Profile, Connections (save both mock addresses, Test connection), Database copies.

  _cache/venv/bin/python tools/proof_crosscheck.py [out folder name]
"""
import json
import os
import pathlib
import sys
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proof" / (sys.argv[1] if len(sys.argv) > 1 else "crosscheck")
APP, NETBOX, SNOW = "http://127.0.0.1:8700", "http://127.0.0.1:3303", "http://127.0.0.1:3304"
RUN = "91af02c09bbc"
WB = ROOT / "modules" / "sources" / "ground_truth" / "vdo1.xlsx"


def reset_mocks():
    for base in (NETBOX, SNOW):
        urllib.request.urlopen(urllib.request.Request(base + "/__mock/reset", method="POST"), timeout=10).read()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    reset_mocks()
    seen, errors = {}, []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
        for width, height in ((1280, 900), (400, 860)):
            pg = b.new_page(viewport={"width": width, "height": height})
            pg.on("dialog", lambda d: d.accept())
            pg.on("pageerror", lambda e, w=width: errors.append(f"{w}: page error: {e}"))
            pg.on("console", lambda m, w=width: errors.append(f"{w}: console {m.type}: {m.text}") if m.type == "error" else None)

            def shot(name, wait=900):
                pg.wait_for_timeout(wait)
                # the web result keeps its tab in a box that scrolls inside the page; open it up so the full-page picture holds all of it
                pg.add_style_tag(content="body.web.result, body.web.result .content, body.web.result main, body.web.result .screen, body.web.result .rpart { height:auto !important; overflow:visible !important; }")
                pg.screenshot(path=str(OUT / f"{name}_{width}.png"), full_page=True)

            def text(sel):
                return pg.inner_text(sel) if pg.locator(sel).count() else ""

            # sign in, as a person: type a name, Continue
            pg.context.clear_cookies()
            pg.goto(APP + "/app/#signin"); pg.wait_for_selector("#si-name", state="visible", timeout=15000); shot("signin", 400)
            pg.fill("#si-name", "Aasritha"); pg.click("#signin button[type=submit]")
            pg.wait_for_selector('[data-screen="home"]:not([hidden])', timeout=15000); shot("home", 1500)
            seen[(width, "home")] = text('[data-screen="home"]')
            # my racks
            pg.goto(APP + "/app/#history"); pg.wait_for_selector("#hist a.run", timeout=15000); shot("history")
            seen[(width, "history")] = pg.eval_on_selector_all("#hist a.run", "as => as.map(a => a.innerText.replace(/\\n+/g, ' | '))")
            # the result of the office rack, tab by tab
            for tab in ("rack", "devices", "layout", "report"):
                pg.goto(f"{APP}/app/#result/{RUN}" + ("" if tab == "rack" else "/" + tab)); shot(f"result_{tab}", 2500)
            seen[(width, "report")] = text("#rp-top")[:1500]
            # the Drift tab: the workbook, its report, Import; NetBox; CMDB
            pg.goto(f"{APP}/app/#result/{RUN}/drift"); pg.wait_for_selector("#df-top .dfsrc", timeout=20000); shot("drift_start", 1200)
            if width == 1280:
                pg.set_input_files("#df-file", str(WB)); pg.click("#df-xlsx"); pg.wait_for_selector("#df-import", timeout=20000)
                seen[(width, "workbook report")] = text("#df-report")[:1200]
                shot("drift_excel_report")
                pg.click("#df-import"); pg.wait_for_timeout(2000)
                for src in ("netbox", "cmdb"):
                    pg.click(f'[data-dfimp="{src}"]'); pg.wait_for_timeout(2000)
            for src in ("excel", "netbox", "cmdb"):
                pg.click(f'[data-dfsrc="{src}"]'); pg.wait_for_timeout(1500)
                seen[(width, f"drift {src}")] = [text("#df-top .card >> nth=1")[:600]] + pg.eval_on_selector_all(
                    "#df-top tr[data-dftype]", "rs => rs.map(r => r.dataset.dftype + ': ' + r.querySelector('p').textContent)")
                shot(f"drift_{src}")
            # Profile, Connections, copies
            pg.goto(APP + "/app/#more"); shot("profile", 1500)
            pg.goto(APP + "/app/#connections"); pg.wait_for_selector("form[data-cn=netbox]", timeout=15000)
            if width == 1280:
                for t, addr in (("netbox", NETBOX), ("cmdb", SNOW)):
                    f = f'form[data-cn="{t}"]'
                    pg.fill(f + " input[name=address]", addr); pg.fill(f + " input[name=username]", "crosscheck"); pg.fill(f + " input[name=password]", "not-a-real-password")
                    pg.click(f + " button[type=submit]"); pg.wait_for_timeout(800)
                    pg.click(f + " [data-cntest]"); pg.wait_for_timeout(1800)
            seen[(width, "connections")] = [text(f'form[data-cn="{t}"] .dfrow') + " / " + text(f'form[data-cn="{t}"] [data-cnwords]') for t in ("netbox", "cmdb")]
            shot("connections")
            pg.goto(APP + "/app/#copies"); pg.wait_for_selector("#cp-list table, #cp-list .card", timeout=15000)
            seen[(width, "copies")] = pg.eval_on_selector_all("#cp-list tr[data-copy]", "rs => rs.map(r => r.innerText.replace(/\\s+/g, ' '))")
            shot("copies")
            if width == 1280:                                   # the test workbook an earlier proof left behind: Delete, as a person would
                while pg.locator("#cp-list tr[data-copy]:has-text('broken_workbook.xlsx') [data-cpdel]").count():
                    pg.click("#cp-list tr[data-copy]:has-text('broken_workbook.xlsx') [data-cpdel]"); pg.wait_for_timeout(900)
                seen[(width, "copies after deleting the test workbook")] = pg.eval_on_selector_all("#cp-list tr[data-copy]", "rs => rs.map(r => r.innerText.replace(/\\s+/g, ' '))")
                shot("copies_after_delete")
            pg.close()
        b.close()
    for k, v in seen.items():
        print(k, *(v if isinstance(v, list) else [v]), sep="\n  ")
    print("errors on the page:", *(errors or ["none"]), sep="\n  ")


if __name__ == "__main__":
    main()
