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
import proof_guard  # noqa: E402
proof_guard.check(os.environ.get("RT_PROOF_USER", "admin3"), RUN)


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
            if vis("#df-top > *") or vis("#diff-top > *") or vis("#cmp-menu") or not vis("#cmp-btn"):
                bad.append(f"{width}: the report is not clean before the button")
            shot("1_before")
            pg.click("#cmp-btn"); pg.wait_for_selector("#cmp-menu", state="visible"); shot("2_dropdown")
            pg.click(f'[data-cmpsrc="{SRC}"]'); pg.wait_for_selector("#cmp-continue, .idauto", timeout=20000)
            if vis(".idauto"):                         # identified for certain: no confirm step, the drift at once (owner, 3 Nov 2026)
                pg.wait_for_selector("#diff-top .card", timeout=20000)
                if vis("#cmp-continue"): bad.append(f"{width}: a certain rack still asked to confirm")
                shot("3_auto")
                pg.click(".idhow summary"); pg.wait_for_timeout(300)
                if not vis(".idhow .idlines li"): bad.append(f"{width}: Show how opened no steps")
                shot("3_auto_how")
                pg.click("#cmp-notthis"); pg.wait_for_selector("#cmp-type", timeout=10000)   # Not this rack: the picker, so the person can change it
            shot("3_confirm")
            pg.click("#cmp-continue"); pg.wait_for_selector("#diff-top .card", timeout=20000)
            if vis(".idcard"): bad.append(f"{width}: confirm panel still shown after Confirm")
            shot("4_drift")
            pg.click('[data-dfmode="detailed"]'); pg.wait_for_timeout(300)
            if not vis("#diff-top .card"): bad.append(f"{width}: the detailed drift is not shown")
            shot("5_detailed")
            pg.close()
    print("PROBLEMS:", bad or "none"); sys.exit(1 if bad else 0)


main()
