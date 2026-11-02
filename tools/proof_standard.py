"""Every screen at 1280 and 400 px, side by side in one contact sheet (docs/proof/standard/sheet.png).
  PW_CHROMIUM=<headless_shell> _cache/venv/bin/python tools/proof_standard.py [engine url]
Signs in as orgowner with the seed password (never printed); run dm1hyd000001."""
import os, pathlib, sys
from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proof" / "standard"
ENGINE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8700"
RUN = "dm1hyd000001"
SCREENS = [("Sign in", "signin"), ("Home", "home"), ("Scan", "sweep"), ("History", "history"), ("Run page", "job/" + RUN), ("Rack", "result/" + RUN),
           ("Devices", "result/" + RUN + "/devices"), ("Layout", "result/" + RUN + "/layout"), ("Report", "result/" + RUN + "/report"),
           ("Find a port", "tools/" + RUN + "/find/5-3"), ("Connections tab", "tools/" + RUN + "/connections"), ("Cables", "tools/" + RUN + "/cables"),
           ("Profile", "more"), ("Models", "models"), ("Logins", "connections"), ("Database copies", "copies"), ("Compare confirm", "CMP"), ("Drift", "DRIFT")]


def main():
    pw = os.environ.get("RT_PROOF_PASSWORD") or (ROOT / "data" / "auth" / "seed-password.txt").read_text().strip()
    OUT.mkdir(parents=True, exist_ok=True); shots = {}; bad = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
        for w, h in ((1280, 900), (400, 860)):
            anon = b.new_context(viewport={"width": w, "height": h}); a = anon.new_page(); a.goto(ENGINE + "/app/#signin"); a.wait_for_timeout(1500)
            a.screenshot(path=str(OUT / f"signin_{w}.png")); anon.close()
            ctx = b.new_context(viewport={"width": w, "height": h}); pg = ctx.new_page()
            pg.on("pageerror", lambda e, w=w: bad.append(f"{w}: {e}"))
            pg.goto(ENGINE + "/app/#signin"); pg.wait_for_selector("#si-user", state="visible")
            pg.fill("#si-user", "orgowner"); pg.fill("#si-pass", pw); pg.click("#signin button[type=submit]")
            pg.wait_for_selector('[data-screen="home"]:not([hidden])', timeout=20000)
            for name, hsh in SCREENS:
                if hsh == "signin":
                    continue
                if hsh in ("CMP", "DRIFT"):
                    if hsh == "CMP":
                        pg.goto(ENGINE + "/app/#result/" + RUN + "/report"); pg.reload(); pg.wait_for_timeout(2500)
                        pg.click("#cmp-btn"); pg.click('[data-cmpsrc="netbox"]'); pg.wait_for_selector("#cmp-continue", timeout=30000); pg.wait_for_timeout(500)
                    else:
                        pg.click("#cmp-continue"); pg.wait_for_selector("#diff-top .card", timeout=30000); pg.wait_for_timeout(500)
                else:
                    pg.goto(ENGINE + "/app/#" + hsh); pg.wait_for_timeout(2200)
                f = OUT / f"{name.replace(' ', '_')}_{w}.png"; pg.screenshot(path=str(f)); shots[(name, w)] = f
            shots[("Sign in", w)] = OUT / f"signin_{w}.png"
            ctx.close()
    names = [n for n, _ in SCREENS]; cols = 3; bw, bh, sw_, gap, lab = 560, 394, 180, 16, 24
    cellw = bw + sw_ + 8; cellh = bh + lab; rows = (len(names) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (cellw + gap), rows * (cellh + gap)), "white"); d = ImageDraw.Draw(sheet)
    for i, n in enumerate(names):
        x, y = (i % cols) * (cellw + gap), (i // cols) * (cellh + gap)
        d.text((x + 2, y + 6), n + "  (1280 px, 400 px)", fill="#1f2a3a")
        for im_, w_, h_, dx in ((Image.open(shots[(n, 1280)]), bw, bh, 0), (Image.open(shots[(n, 400)]), sw_, bh, bw + 8)):
            im_ = im_.convert("RGB"); im_ = im_.resize((w_, int(im_.height * w_ / im_.width)))
            c = Image.new("RGB", (w_, h_), "white"); c.paste(im_.crop((0, 0, w_, min(h_, im_.height))), (0, 0))
            d.rectangle((x + dx, y + lab, x + dx + w_ - 1, y + lab + h_ - 1), outline="#d5dbe3"); sheet.paste(c, (x + dx, y + lab))
    sheet.save(OUT / "sheet.png"); print("sheet", sheet.size, "PROBLEMS:", bad or "none")


main()
