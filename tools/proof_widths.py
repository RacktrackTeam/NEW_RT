"""Every engine screen at 1800, 1280 and 400 px wide, signed out and signed in as the platform owner (headless Chromium).
Writes docs/proof/widths/<screen>_<width>.png and prints, per screen and width, how wide the main content is against the window,
so a narrow column stuck to one side shows as a number.

  _cache/venv/bin/python tools/proof_widths.py
"""
import os
import pathlib

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proof" / "widths"
APP = os.environ.get("RT_APP", "http://127.0.0.1:8700")
PW = (ROOT / "data" / "auth" / "seed-password.txt").read_text().strip()     # read, never printed
RUN = "91af02c09bbc"
SCREENS = [("home", "#home"), ("scan", "#sweep"), ("result", f"#result/{RUN}"), ("history", "#history"), ("profile", "#more"),
           ("connections", "#connections"), ("copies", "#copies"), ("models", "#models")]
MEASURE = """() => { const s = document.querySelector('.screen:not([hidden])'); if (!s) return null;
  const kids = [...s.querySelectorAll(':scope > *')].filter(e => e.offsetParent !== null);
  const r = kids.map(e => e.getBoundingClientRect()); const L = Math.min(...r.map(x => x.left)), R = Math.max(...r.map(x => x.right));
  return { left: Math.round(L), right: Math.round(R), window: innerWidth, bg: getComputedStyle(document.body).backgroundColor }; }"""


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
        for width in (1800, 1280, 400):
            pg = b.new_page(viewport={"width": width, "height": 1000})
            pg.goto(APP + "/app/#signin"); pg.wait_for_selector("#si-user", state="visible", timeout=15000); pg.wait_for_timeout(600)
            pg.screenshot(path=str(OUT / f"signin_{width}.png"), full_page=True); rows.append(("signin", width, pg.evaluate(MEASURE)))
            pg.fill("#si-user", "owner"); pg.fill("#si-pass", PW); pg.click("#signin button[type=submit]")
            pg.wait_for_selector('[data-screen="home"]:not([hidden])', timeout=15000)
            for name, h in SCREENS:
                pg.goto(APP + "/app/" + h); pg.wait_for_timeout(2200 if name == "result" else 1200)
                pg.screenshot(path=str(OUT / f"{name}_{width}.png"), full_page=name != "result")
                rows.append((name, width, pg.evaluate(MEASURE)))
            pg.close()
        b.close()
    for name, width, m in rows:
        print(f"{name:12s} {width:5d}  content {m['left']}-{m['right']} of {m['window']}  page {m['bg']}" if m else f"{name} {width} no screen")


if __name__ == "__main__":
    main()
