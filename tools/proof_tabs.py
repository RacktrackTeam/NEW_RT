"""Proof that each result tab shows only its own section (1280 and 400 px). Needs auth.sh and engine.sh running.
  PW_CHROMIUM=<headless_shell> _cache/venv/bin/python tools/proof_tabs.py [engine url]
The password is read from RT_PROOF_PASSWORD or data/auth/seed-password.txt; it is never printed."""
import os, pathlib, sys
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proof" / "tabs"
ENGINE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8700"
RUN = os.environ.get("RT_PROOF_RUN", "fx1hyd000001")   # a stored run of the signed-in user's own site
import proof_guard  # noqa: E402
proof_guard.check(os.environ.get("RT_PROOF_USER", "admin3"), RUN)
PARTS = {"rack": "res-rack", "devices": "res-devices", "layout": "res-layout", "report": "res-report"}


def main():
    pw = os.environ.get("RT_PROOF_PASSWORD") or (ROOT / "data" / "auth" / "seed-password.txt").read_text().strip()
    OUT.mkdir(parents=True, exist_ok=True)
    bad = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
        for width, height in ((1280, 900), (400, 860)):
            pg = b.new_page(viewport={"width": width, "height": height})
            pg.on("pageerror", lambda e, w=width: bad.append(f"{w}: page error: {e}"))
            pg.goto(ENGINE + "/app/#signin"); pg.wait_for_selector("#si-user", state="visible")
            pg.fill("#si-user", os.environ.get("RT_PROOF_USER", "admin3")); pg.fill("#si-pass", pw); pg.click("#signin button[type=submit]")
            pg.wait_for_selector('[data-screen="home"]:not([hidden])', timeout=20000)
            for tab in PARTS:
                pg.goto(ENGINE + "/app/#result/" + RUN + ("" if tab == "rack" else "/" + tab))
                pg.wait_for_timeout(2500)
                vis = {t: pg.is_visible("#" + i) for t, i in PARTS.items()}
                heads = pg.locator(".rsn").count()
                nav = pg.is_visible("#rnav")
                if [t for t, v in vis.items() if v] != [tab] or heads or not nav:
                    bad.append(f"{width} {tab}: visible={vis} numbered headings={heads} bar={nav}")
                print(width, tab, [t for t, v in vis.items() if v], "scrollY", pg.evaluate("scrollY"), "bar top", round(pg.evaluate("document.getElementById('rnav').getBoundingClientRect().top")))
                pg.screenshot(path=str(OUT / f"{tab}_{width}.png"), full_page=False)
            # a tab change scrolls to the top: scroll down on Report, press Rack
            pg.goto(ENGINE + "/app/#result/" + RUN + "/report"); pg.wait_for_timeout(2000)
            pg.evaluate("window.scrollTo(0, 600)"); pg.click('#rnav a[data-rtab="devices"]'); pg.wait_for_timeout(600)
            if pg.evaluate("scrollY") != 0:
                bad.append(f"{width}: tab change did not scroll to top")
            pg.close()
    print("PROBLEMS:", bad or "none")
    sys.exit(1 if bad else 0)


main()
