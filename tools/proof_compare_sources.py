"""Proof that the Report's compare works for Excel, NetBox and the CMDB, with a visible loading line and a one-sentence error.
  PW_CHROMIUM=<headless_shell> _cache/venv/bin/python tools/proof_compare_sources.py [engine url]
Signs in as orgowner with the seed password (never printed); run dm1hyd000001. Screenshots go to docs/proof/tabbar/."""
import os, pathlib, sys
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proof" / "tabbar"
ENGINE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8700"
RUN = "dm1hyd000001"


def main():
    pw = os.environ.get("RT_PROOF_PASSWORD") or (ROOT / "data" / "auth" / "seed-password.txt").read_text().strip()
    OUT.mkdir(parents=True, exist_ok=True); bad = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
        pg = b.new_page(viewport={"width": 1280, "height": 900})
        pg.on("pageerror", lambda e: bad.append(f"page error: {e}"))
        pg.goto(ENGINE + "/app/#signin"); pg.wait_for_selector("#si-user", state="visible")
        pg.fill("#si-user", "orgowner"); pg.fill("#si-pass", pw); pg.click("#signin button[type=submit]")
        pg.wait_for_selector('[data-screen="home"]:not([hidden])', timeout=20000)

        def no_copy(route):                      # the run as seen before any NetBox or CMDB copy: the copy-in path runs
            r = route.fetch(); d = r.json(); d["imported"]["netbox"] = None; d["imported"]["cmdb"] = None; route.fulfill(response=r, json=d)

        def slow(route):
            pg.wait_for_timeout(1500); route.continue_()
        for src in ("excel", "netbox", "cmdb"):
            if src != "excel":
                pg.route("**/drift", no_copy); pg.route("**/api/sources/*/import", slow)
            pg.goto(ENGINE + "/app/#result/" + RUN + "/report"); pg.reload(); pg.wait_for_timeout(3500)
            pg.click("#cmp-btn"); pg.evaluate("window.scrollTo(0, 400)"); pg.click(f'[data-cmpsrc="{src}"]')
            pg.wait_for_timeout(100)
            if pg.evaluate("scrollY") > 5 or (src != "excel" and not pg.is_visible("#cmp-status")) or pg.is_visible("#rp-top") or not pg.is_visible("#cmp-back"):
                bad.append(f"{src}: compare did not take over the top within 100 ms")
            if src != "excel":
                pg.wait_for_timeout(500)
                t = pg.inner_text("#cmp-status")
                if "Copying in" not in t: bad.append(f"{src}: no loading line, got {t!r}")
                pg.screenshot(path=str(OUT / f"compare_{src}_1_loading.png"))
            pg.wait_for_selector("#cmp-continue", timeout=30000)
            if pg.evaluate("document.getElementById('df-top').getBoundingClientRect().top") > 400: bad.append(f"{src}: confirm panel not near the top")
            pg.screenshot(path=str(OUT / f"compare_{src}_2_confirm.png"))
            pg.click("#cmp-continue"); pg.wait_for_selector("#diff-top .card", timeout=30000); pg.wait_for_timeout(500)
            print(src, "drift:", pg.inner_text("#diff-top .card p")[:90].replace("\n", " "))
            pg.screenshot(path=str(OUT / f"compare_{src}_3_drift.png"))
            pg.unroute("**/drift"); pg.unroute("**/api/sources/*/import")
        # an error is one sentence under the button
        pg.route("**/drift", no_copy)
        pg.route("**/api/sources/netbox/import", lambda r: r.fulfill(status=502, json={"error": "NetBox did not answer"}))
        pg.goto(ENGINE + "/app/#result/" + RUN + "/report"); pg.reload(); pg.wait_for_timeout(3500)
        pg.click("#cmp-btn"); pg.click('[data-cmpsrc="netbox"]'); pg.wait_for_timeout(1000)
        t = pg.inner_text("#cmp-status"); print("error line:", t)
        if "NetBox did not answer" not in t: bad.append(f"no error line: {t!r}")
        pg.screenshot(path=str(OUT / "compare_error.png"))
    print("PROBLEMS:", bad or "none"); sys.exit(1 if bad else 0)


main()
