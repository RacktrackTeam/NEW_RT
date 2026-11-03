"""Proof of the result tab bar and the Find a port tab (1280 and 400 px). Needs auth.sh and engine.sh running.
  PW_CHROMIUM=<headless_shell> _cache/venv/bin/python tools/proof_tabbar.py [engine url]
The password is read from RT_PROOF_PASSWORD or data/auth/seed-password.txt; it is never printed."""
import os, pathlib, sys
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proof" / "tabbar"
ENGINE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8700"
RUN = os.environ.get("RT_PROOF_RUN", "fx1hyd000001")
import proof_guard  # noqa: E402
proof_guard.check(os.environ.get("RT_PROOF_USER", "orgownert"), RUN)
ORDER = ["Rack", "Devices", "Find a port", "Connections", "Cables", "Layout", "Report"]


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
            pg.fill("#si-user", os.environ.get("RT_PROOF_USER", "orgownert")); pg.fill("#si-pass", pw); pg.click("#signin button[type=submit]")
            pg.wait_for_selector('[data-screen="home"]:not([hidden])', timeout=20000)
            for name, hash_ in (("rack", ""), ("find", "find"), ("find_port", "find/DEV-PORT"), ("report", "report")):
                if hash_ == "find/DEV-PORT":
                    hash_ = os.environ.get("RT_PROOF_FIND", "find/5-3")
                if name == "report":
                    url = "#result/" + RUN + "/report"
                elif name == "rack":
                    url = "#result/" + RUN
                else:
                    url = "#tools/" + RUN + "/" + hash_
                pg.goto(ENGINE + "/app/" + url); pg.wait_for_timeout(3000)
                labels = pg.locator("#rnav a span").all_inner_texts()
                if labels != ORDER:
                    bad.append(f"{width} {name}: tabs {labels}")
                tops = pg.evaluate("[...document.querySelectorAll('#rnav a')].map(a=>Math.round(a.getBoundingClientRect().top))")
                if width == 1280 and len(set(tops)) != 1:
                    bad.append(f"{width} {name}: tabs on more than one line {tops}")
                print(width, name, labels == ORDER, "tops", sorted(set(tops)))
                pg.screenshot(path=str(OUT / f"{name}_{width}.png"), full_page=(name == "find_port"))
            pg.close()
    print("PROBLEMS:", bad or "none")
    sys.exit(1 if bad else 0)


main()
