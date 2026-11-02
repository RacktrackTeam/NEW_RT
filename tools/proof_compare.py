"""Proof of the Report's compare flow, four states at 1280 and 400 px: before the button, the dropdown, the confirm panel, the drift.
Needs auth.sh and engine.sh. PW_CHROMIUM=<headless_shell> _cache/venv/bin/python tools/proof_compare.py [engine url] [source]
Password from RT_PROOF_PASSWORD or data/auth/seed-password.txt (never printed)."""
import os, pathlib, sys
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proof" / "tabs"
ENGINE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8700"
SRC = sys.argv[2] if len(sys.argv) > 2 else "excel"
RUN = os.environ.get("RT_PROOF_RUN", "fx1hyd000001")


def main():
    pw = os.environ.get("RT_PROOF_PASSWORD") or (ROOT / "data" / "auth" / "seed-password.txt").read_text().strip()
    bad = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
        for width, height in ((1280, 900), (400, 860)):
            pg = b.new_page(viewport={"width": width, "height": height})
            pg.on("pageerror", lambda e, w=width: bad.append(f"{w}: page error: {e}"))
            pg.goto(ENGINE + "/app/#signin"); pg.wait_for_selector("#si-user", state="visible")
            pg.fill("#si-user", os.environ.get("RT_PROOF_USER", "admin3")); pg.fill("#si-pass", pw); pg.click("#signin button[type=submit]")
            pg.wait_for_selector('[data-screen="home"]:not([hidden])', timeout=20000)
            pg.goto(ENGINE + "/app/#result/" + RUN + "/report"); pg.wait_for_timeout(3000)

            def shot(name):
                pg.wait_for_timeout(500); pg.screenshot(path=str(OUT / f"compare_{name}_{width}.png"), full_page=True)
            vis = lambda s: pg.is_visible(s)
            if vis("#df-top > *") or vis("#diff-top > *") or vis("#sec-tools") or vis("#cmp-menu") or not vis("#cmp-btn"):
                bad.append(f"{width}: the report is not clean before the button")
            shot("1_before")
            pg.click("#cmp-btn"); pg.wait_for_selector("#cmp-menu", state="visible"); shot("2_dropdown")
            pg.click(f'[data-cmpsrc="{SRC}"]'); pg.wait_for_selector(".idcard", timeout=20000); shot("3_confirm")
            pg.click("#cmp-continue"); pg.wait_for_selector("#diff-top .card", timeout=20000)
            if vis(".idcard"): bad.append(f"{width}: confirm panel still shown after Confirm")
            shot("4_drift")
            pg.click('[data-dfmode="detailed"]'); pg.wait_for_timeout(300)
            pg.click("#more-link"); pg.wait_for_selector("#res-tools .grow", timeout=5000)
            pg.close()
    print("PROBLEMS:", bad or "none"); sys.exit(1 if bad else 0)


main()
