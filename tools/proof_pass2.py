"""The verify pass 2 fixes that a browser shows, at 1280 and 400 px. Writes docs/proof/pass2/.

    PW_CHROMIUM=<headless_shell> _cache/venv-engine/bin/python tools/proof_pass2.py [engine url] [failed run id]

1 sign-in screen and sign out: no refused call (no 401) in the console (finding 30)
2 Profile links for orgownert and techt: each /org/ page has Back to RackTrack, no Home, Scan, Map or History, no rack
  count, and Back goes to this app (finding 3); /org/ and /org/scan go back to the app
3 a failed run opened by a technician: the stage it stopped at, the ticks before it, no Remove (findings 4, 5)
4 find-port with a wrong device or port answers one sentence (finding 34)
Test Organization accounts only (tools/proof_guard.py). The failed run is one the engine holds for Test Hyderabad DC.
"""
import os
import pathlib
import re
import sys

import requests
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proof" / "pass2"
ENGINE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8700"
FAILED = sys.argv[2] if len(sys.argv) > 2 else None
RUN = "fx1hyd000001"
import proof_guard  # noqa: E402
for _u in ("orgownert", "techt"):
    proof_guard.check(_u, RUN)


def main():
    pw = os.environ.get("RT_PROOF_PASSWORD") or (ROOT / "data" / "auth" / "seed-password.txt").read_text().strip()
    OUT.mkdir(parents=True, exist_ok=True); bad = []

    def ok(cond, words):
        print(("PASS " if cond else "FAIL ") + words)
        if not cond:
            bad.append(words)

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
        for w, h in ((1280, 900), (400, 860)):
            for user in ("orgownert", "techt"):
                ctx = b.new_context(viewport={"width": w, "height": h}); pg = ctx.new_page(); errs = []
                pg.on("console", lambda m, errs=errs: errs.append(m.text) if m.type == "error" else None)
                pg.on("response", lambda r, errs=errs: errs.append(f"{r.status} {r.url}") if r.status == 401 else None)
                pg.goto(ENGINE + "/app/#signin"); pg.wait_for_selector("#si-user", state="visible"); pg.wait_for_timeout(800)
                ok(not errs, f"{w} {user}: sign-in screen opens with no refused call {errs[:2]}")
                pg.fill("#si-user", user); pg.fill("#si-pass", pw); pg.click("#signin button[type=submit]")
                pg.wait_for_selector('[data-screen="home"]:not([hidden])', timeout=20000)
                pg.goto(ENGINE + "/app/#more"); pg.wait_for_timeout(800)
                links = pg.eval_on_selector_all("#org-links a, #acct-settings a", "els => els.map(e => [e.querySelector('span').textContent, e.href])")
                for name, href in links:
                    op = ctx.new_page(); op.goto(href); op.wait_for_timeout(2500)
                    text = op.inner_text("body"); back = op.query_selector("text=Back to RackTrack")
                    nav = [x for x in ("Home", "Scan", "Map", "History") if op.query_selector(f"nav >> text={x}")]
                    ok(back is not None and not nav, f"{w} {user}: {name} has Back to RackTrack and no {nav or 'Home, Scan, Map, History'}")
                    ok(not re.search(r"\b\d+ racks?\b|No rack has been read|No scans yet", text), f"{w} {user}: {name} shows no rack count or scans")
                    op.screenshot(path=str(OUT / f"org_{name.replace(' ', '_')}_{user}_{w}.png"))
                    for path in ("/org/", "/org/scan"):        # the same tab knows the way back (given as ?back= by this app)
                        op.goto(href.split("/org/")[0] + path); op.wait_for_timeout(2000)
                        ok(op.url.startswith(ENGINE + "/app/"), f"{w} {user}: {path} goes back to this app")
                    op.goto(href); op.wait_for_timeout(2000); back = op.query_selector("text=Back to RackTrack")
                    if back is not None:
                        back.click(); op.wait_for_timeout(1500)
                        ok(op.url.startswith(ENGINE + "/app/"), f"{w} {user}: Back from {name} lands in this app ({op.url.split('#')[0]})")
                    op.close()
                if FAILED and user == "techt":
                    pg.goto(ENGINE + "/app/#job/" + FAILED); pg.wait_for_timeout(2500)
                    pg.screenshot(path=str(OUT / f"failed_run_{w}.png"))
                    ok(pg.is_hidden("#job-del"), f"{w} techt: the failed run offers no Remove")
                    ticks = pg.eval_on_selector_all("#run-stages li.done", "els => els.length"); badst = pg.eval_on_selector_all("#run-stages li.bad span:nth-child(2)", "els => els.map(e => e.textContent)")
                    ok(ticks >= 2 and badst == ["Units"], f"{w} techt: {ticks} steps ticked, stopped at {badst}")
                    ok("fault on our side" in pg.inner_text("#job-err"), f"{w} techt: the failure says it was our fault, not the video")
                errs.clear()
                pg.goto(ENGINE + "/app/#more"); pg.wait_for_timeout(500); pg.click("#signout2" if pg.is_visible("#signout2") else "#signout")
                pg.wait_for_selector("#si-user", state="visible"); pg.wait_for_timeout(1200)
                ok(not errs, f"{w} {user}: sign out makes no refused call {errs[:2]}")
                ctx.close()
        b.close()
    s = requests.Session()
    s.post(ENGINE.rsplit(":", 1)[0] + ":" + str(requests.get(ENGINE + "/api/auth-service").json().get("port", 3302)) + "/api/auth/login", json={"username": "orgownert", "password": pw})
    auth = requests.get(ENGINE + "/api/auth-service").json().get("url")
    if auth:
        s.post(auth + "/api/auth/login", json={"username": "orgownert", "password": pw})
    r1 = s.get(ENGINE + f"/api/video/{RUN}/find-port", params={"device": "abc", "port": 1})
    r2 = s.get(ENGINE + f"/api/video/{RUN}/find-port", params={"device": 4, "port": 9999})
    ok(r1.status_code == 400 and isinstance(r1.json().get("error"), str), f"find-port device=abc answers {r1.status_code}: {r1.json().get('error')}")
    ok(r2.status_code == 404 and "no port" in r2.json().get("error", ""), f"find-port port=9999 answers {r2.status_code}: {r2.json().get('error')}")
    print("PROBLEMS:", bad or "none")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
