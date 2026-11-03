"""Proof that the Report's compare works for Excel, NetBox and the CMDB as the platform owner, an organization owner and a technician,
with a visible status line and a one-sentence error, and that the compare takes the top of the Report.
  PW_CHROMIUM=<headless_shell> _cache/venv/bin/python tools/proof_compare_sources.py [engine url]
Signs in with the seed password (never printed); run fx1hyd000001 (Test Organization). Screenshots go to docs/proof/tabbar/.
The copy-in path is forced (the run is shown as having no NetBox or CMDB copy) so the import runs for owner and orgowner;
a technician may not copy in, so there the one-sentence refusal is what must show (never a blank area)."""
import os, pathlib, sys
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proof" / "tabbar"
ENGINE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8700"
SIGNIN = "http://127.0.0.1:8700"      # the sign-in service allows the main engine's origin; the session cookie is shared by every port of 127.0.0.1
RUN = "fx1hyd000001"                          # the Test Organization's run: proofs never change the Demo Organization
USERS = ("owner", "orgownert", "techt")
import proof_guard  # noqa: E402
for _u in USERS:
    proof_guard.check(_u, RUN)


def main():
    pw = os.environ.get("RT_PROOF_PASSWORD") or (ROOT / "data" / "auth" / "seed-password.txt").read_text().strip()
    OUT.mkdir(parents=True, exist_ok=True); bad = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
        for user in USERS:
            ctx = b.new_context(viewport={"width": 1280, "height": 900}); pg = ctx.new_page()
            pg.on("pageerror", lambda e, u=user: bad.append(f"{u}: page error: {e}"))
            pg.goto(SIGNIN + "/app/#signin"); pg.wait_for_selector("#si-user", state="visible")
            pg.fill("#si-user", user); pg.fill("#si-pass", pw); pg.click("#signin button[type=submit]")
            pg.wait_for_selector('[data-screen="home"]:not([hidden])', timeout=20000)
            pg.goto(ENGINE + "/app/#home"); pg.wait_for_timeout(1500)

            def no_copy(route):
                r = route.fetch(); d = r.json(); d["imported"]["netbox"] = None; d["imported"]["cmdb"] = None; route.fulfill(response=r, json=d)

            def slow(route):
                pg.wait_for_timeout(1200); route.continue_()
            for src in ("excel", "netbox", "cmdb"):
                forced = src != "excel"
                if forced:
                    pg.route("**/drift*", no_copy); pg.route("**/api/sources/*/import*", slow)
                pg.goto(ENGINE + "/app/#result/" + RUN + "/report"); pg.reload(); pg.wait_for_timeout(3500)
                tag = f"{user}_{src}"
                pg.click("#cmp-btn"); pg.evaluate("window.scrollTo(0, 400)"); pg.click(f'[data-cmpsrc="{src}"]')
                pg.wait_for_timeout(100)
                if pg.evaluate("scrollY") > 5 or (forced and not pg.is_visible("#cmp-status")) or pg.is_visible("#rp-top") or not pg.is_visible("#cmp-back"):
                    bad.append(f"{tag}: compare did not take over the top within 100 ms")
                if forced:
                    pg.wait_for_timeout(300)
                    pg.screenshot(path=str(OUT / f"compare_{tag}_1_loading.png"))
                if forced and user == "techt":
                    pg.wait_for_timeout(2500)
                    t = pg.inner_text("#cmp-status").strip(); print(tag, "line:", t)
                    if "organization admin" not in t: bad.append(f"{tag}: expected the refusal sentence, got {t!r}")
                    pg.screenshot(path=str(OUT / f"compare_{tag}_2_refused.png"))
                else:
                    pg.wait_for_selector("#cmp-continue", timeout=30000)
                    pg.screenshot(path=str(OUT / f"compare_{tag}_2_confirm.png"))
                    pg.click("#cmp-continue"); pg.wait_for_selector("#diff-top .card", timeout=30000); pg.wait_for_timeout(500)
                    print(tag, "drift:", pg.inner_text("#diff-top .card p")[:80].replace("\n", " "))
                    pg.screenshot(path=str(OUT / f"compare_{tag}_3_drift.png"))
                blank = pg.evaluate("!document.getElementById('cmp-status').textContent.trim() && !document.getElementById('df-top').innerHTML.trim() && !document.getElementById('diff-top').innerHTML.trim()")
                if blank: bad.append(f"{tag}: the compare area is blank")
                pg.unroute("**/drift*"); pg.unroute("**/api/sources/*/import*")
            ctx.close()
        # an unexpected server failure is one sentence too
        ctx = b.new_context(viewport={"width": 1280, "height": 900}); pg = ctx.new_page()
        pg.goto(SIGNIN + "/app/#signin"); pg.wait_for_selector("#si-user", state="visible")
        pg.fill("#si-user", "owner"); pg.fill("#si-pass", pw); pg.click("#signin button[type=submit]")
        pg.wait_for_selector('[data-screen="home"]:not([hidden])', timeout=20000)
        pg.goto(ENGINE + "/app/#home"); pg.wait_for_timeout(1500)
        def no_copy(route):
            r = route.fetch(); d = r.json(); d["imported"]["netbox"] = None; route.fulfill(response=r, json=d)
        pg.route("**/drift*", no_copy)
        pg.route("**/api/sources/netbox/import*", lambda r: r.fulfill(status=502, json={"error": "NetBox did not answer"}))
        pg.goto(ENGINE + "/app/#result/" + RUN + "/report"); pg.reload(); pg.wait_for_timeout(3500)
        pg.click("#cmp-btn"); pg.click('[data-cmpsrc="netbox"]'); pg.wait_for_timeout(1000)
        t = pg.inner_text("#cmp-status"); print("error line:", t)
        if "NetBox did not answer" not in t: bad.append(f"no error line: {t!r}")
        pg.screenshot(path=str(OUT / "compare_error.png"))
    print("PROBLEMS:", bad or "none"); sys.exit(1 if bad else 0)


main()
