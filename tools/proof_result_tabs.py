"""Open every tab of every finished run a person can see, at phone and laptop width, and fail on any error.

    tools/proof_result_tabs.py [--base http://127.0.0.1:8700] [--user techt] [--only <run id>]

Signs in as the person (password from RT_PROOF_PASSWORD or data/auth/seed-password.txt, never printed), reads their
history, and for each finished run opens Rack, Devices, Find a port, Connections, Cables, Layout and Report in headless
Chromium. A tab counts as broken when the page leaves it (the result page falls back to the run page when its code
throws), when a promise handler receives an error (the screens catch their own errors and show a toast, so those are
hooked too), on any console error, or on any response of 400 or more. Exit 1 when anything broke. Written on 4 Nov 2026
after a tester's run with a make read but no model bounced every tab back to the run page.
"""
import argparse
import os
import pathlib
import sys

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
TABS = ["", "/devices", "/findport", "/connections", "/cables", "/layout", "/report"]
HOOK = ("(function () { var orig = Promise.prototype.catch; Promise.prototype.catch = function (h) { return orig.call(this, "
        "function (e) { try { if (e && e.stack) window.__errs = (window.__errs || []).concat([String(e.stack)]); } catch (_) {} "
        "return h(e); }); }; })();")


def main():
    ap = argparse.ArgumentParser(description="Open every tab of every run and fail on any error.")
    ap.add_argument("--base", default="http://127.0.0.1:" + os.environ.get("RACKTRACK_PORT", "8700"))
    ap.add_argument("--user", default="techt")
    ap.add_argument("--only", default=None, help="one run id")
    a = ap.parse_args()
    pw = os.environ.get("RT_PROOF_PASSWORD") or (ROOT / "data/auth/seed-password.txt").read_text().strip()
    shell = os.environ.get("PW_CHROMIUM") or next(iter(sorted(pathlib.Path.home().glob(
        "Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-mac-arm64/chrome-headless-shell"))[::-1]), None)
    broken = 0
    with sync_playwright() as p:
        b = p.chromium.launch(**({"executable_path": str(shell)} if shell else {}))
        for w, h in ((412, 915), (1280, 900)):
            ctx = b.new_context(viewport={"width": w, "height": h}, extra_http_headers={"ngrok-skip-browser-warning": "1"})
            ctx.add_init_script(HOOK)
            pg = ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append("page error: " + str(e)))
            pg.on("console", lambda m: errs.append("console error: " + m.text) if m.type == "error" else None)
            pg.on("response", lambda r: errs.append("HTTP %s %s" % (r.status, r.url)) if r.status >= 400 else None)
            pg.goto(a.base + "/app/#signin", wait_until="load")
            pg.wait_for_timeout(1200)
            pg.fill("#si-user, input[name=username], input[type=text]", a.user)
            pg.fill("input[type=password]", pw)
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(2500)
            if pg.evaluate("location.hash") == "#signin":
                raise SystemExit("sign-in as %s refused: is ./start.sh running and the account in docs/AUTH.md?" % a.user)
            rows = pg.evaluate("fetch('/api/video/history').then(r => r.json())")
            runs = [r["id"] for r in rows if r.get("status") == "done" or r.get("partial")]
            if a.only:
                runs = [r for r in runs if r == a.only]
            print("%dx%d as %s: %d finished runs: %s" % (w, h, a.user, len(runs), " ".join(runs)))
            errs.clear()
            for run in runs:
                for t in TABS:
                    want = "#result/" + run + t
                    pg.goto(a.base + "/app/" + want, wait_until="load")
                    pg.wait_for_timeout(2500)
                    here = pg.evaluate("location.hash")
                    caught = pg.evaluate("(window.__errs || []).splice(0)")
                    if here != want or caught or errs:
                        broken += 1
                        print("  BROKEN %s %s: the page is at %s" % (run, t or "/rack", here))
                        for e in caught + errs:
                            print("     " + e.replace("\n", "\n     ")[:600])
                        errs.clear()
            ctx.close()
        b.close()
    print("broken tabs: %d" % broken)
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
