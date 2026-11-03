"""Proof of the remaining RT-EOS functions on the stored vdo1 run (rack SP-HYD-RM01-R01-R1), through the engine and the screens.

    RT_PROOF_PASSWORD=... _cache/venv/bin/python tools/proof_features.py [--engine http://127.0.0.1:8702] [--auth http://127.0.0.1:3305] [--setup]

Needs the sign-in service and the engine running (auth.sh, engine.sh) and the vdo1 run in data/runs/<org>/<site>/91af02c09bbc.
The password is read from RT_PROOF_PASSWORD or data/auth/seed-password.txt; it is never printed.
--setup first plants the data (once): the planted workbook (tools/plant_features_workbook.py) read and imported as an Excel copy,
the run checked against it, the site's position set through the sign-in service, vdo1.mp4 uploaded again with a phone position, a
real crop of D5 sent as a close-up (no serial on it) and the two synthetic close-ups (tools/make_synthetic_closeup.py).
Then every screen at 1280 and 400 px into docs/proof/features/, and the API answers into docs/proof/features/results.json.
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import urllib.request
from http.cookiejar import CookieJar

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proof" / "features"
RUN = "91af02c09bbc"
PY = sys.executable


def opener(auth, user, pw):
    jar = CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    req = urllib.request.Request(auth + "/api/auth/login", data=json.dumps({"username": user, "password": pw}).encode(),
                                 headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1"}, method="POST")
    op.open(req).read()
    return op, jar


def call(op, url, body=None, method=None, form=None, headers=None):
    h = dict(headers or {})
    data = None
    if form is not None:
        boundary = "----rtproof" + os.urandom(6).hex()
        parts = []
        for k, v in form.items():
            if isinstance(v, pathlib.Path):
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{v.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode() + v.read_bytes() + b"\r\n")
            else:
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        data = b"".join(parts) + f"--{boundary}--\r\n".encode()
        h["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode(); h["Content-Type"] = "application/json"
    r = op.open(urllib.request.Request(url, data=data, headers=h, method=method or ("POST" if data else "GET")), timeout=600)
    raw = r.read()
    try:
        return json.loads(raw)
    except ValueError:
        return raw.decode("utf-8", "replace")


def setup(op, engine, auth):
    wb = ROOT / "data" / "proof_features" / "vdo1_planted.xlsx"; wb.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([PY, str(ROOT / "tools" / "plant_features_workbook.py"), str(wb)], check=True)
    rep = call(op, engine + "/api/sources/excel", form={"workbook": wb})
    imp = call(op, engine + "/api/sources/excel/import", {"upload": rep["upload"]})
    call(op, f"{engine}/api/video/{RUN}/database", {"source": "excel", "confirmed": True})
    me = call(op, engine + "/api/me")
    site = call(op, f"{auth}/api/setup/{me['site']}/datacentre", {"lat": 17.4486, "lng": 78.3908}, method="PUT", headers={"Origin": "http://127.0.0.1"})
    up = call(op, engine + "/api/video", form={"video": ROOT / "samples" / "video" / "vdo1.mp4", "lat": "17.4489", "lng": "78.3911", "accuracy": "35"})
    shots = {}
    run_dir = next((ROOT / "data" / "runs").glob(f"*/*/{RUN}"))
    shots["real_crop_D5"] = call(op, engine + "/api/closeup", form={"file": run_dir / "section_D5_clean.jpg", "job": RUN, "device": "5", "ids_only": "1"}).get("ids")
    for d, lines in ((5, ["TP-Link JetStream TL-SG2428P", "S/N: TPL2231X9ZZ", "Asset Tag: SP-AT-00415", "SP-R1-U15-SW02", "CBL-PLANT-L1-A"]), (4, ["TP-Link TL-SG2428P", "S/N: SGP2231A0BC"])):
        pic = OUT / f"synthetic_closeup_D{d}.jpg"
        subprocess.run([PY, str(ROOT / "tools" / "make_synthetic_closeup.py"), str(pic), *lines], check=True)
        shots[f"synthetic_D{d}"] = call(op, engine + "/api/closeup", form={"file": pic, "job": RUN, "device": str(d), "ids_only": "1"}).get("ids")
    return {"import": imp, "site": site.get("datacentre"), "upload": up, "closeups": shots}


def answers(op, engine):
    v = lambda p: call(op, f"{engine}/api/video/{RUN}/{p}")  # noqa: E731
    drift = v("drift")
    out = {"identify": [{k: st.get(k) for k in ("n", "title", "outcome", "proof")} for st in drift["identify"]["steps"]] + [drift["identify"]["words"]],
           "drift_ids": [x["words"] for x in drift["drift"]["rows"] if x["type"] in ("serial", "asset_tag")],
           "cables": v("cables"), "find_port": v("find-port?device=4&port=9"), "worklist_count": len(v("worklist")["items"]),
           "physical_counts": v("physical-layer")["counts"], "sfp": v("sfp-advice")["items"], "topology_cables": v("topology")["cables_in_matrix"]}
    az = call(op, engine + "/api/cables/az", {"a": RUN, "z": RUN})
    out["az"] = {"counts": az["counts"], "accuracy": az["accuracy"], "id": az["id"]}
    csv = call(op, f"{engine}/api/cables/az/{az['id']}.csv")
    (OUT / "az_check.csv").write_text(csv)
    out["az"]["csv_lines"] = len(csv.strip().splitlines())
    return out


def screens(engine, user, pw):
    from playwright.sync_api import sync_playwright
    errors = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
        for width, height in ((1280, 900), (400, 860)):
            pg = b.new_page(viewport={"width": width, "height": height})
            pg.on("pageerror", lambda e, w=width: errors.append(f"{w}: page error: {e}"))
            pg.on("response", lambda r, w=width: errors.append(f"{w}: {r.status} {r.url.split('870', 1)[-1]}") if r.status >= 400 and "/api/auth/me" not in r.url and "refresh" not in r.url else None)
            pg.goto(engine + "/app/#signin"); pg.wait_for_selector("#si-user", state="visible")
            pg.fill("#si-user", user); pg.fill("#si-pass", pw); pg.click("#signin button[type=submit]")
            pg.wait_for_selector('[data-screen="home"]:not([hidden])', timeout=20000)

            def shot(name, hash_, wait_for="#ft-root > *:not(.muted)", wait=1200):
                pg.goto(engine + "/app/#" + hash_); pg.wait_for_timeout(300)
                if wait_for:
                    pg.wait_for_selector(wait_for, timeout=30000)
                pg.wait_for_timeout(wait)
                pg.screenshot(path=str(OUT / f"{name}_{width}.png"), full_page=True)

            shot("tools_list", f"tools/{RUN}", ".ft-list")
            shot("find_port", f"tools/{RUN}/find/4-9", ".ft-found", 2000)
            shot("connections", f"tools/{RUN}/connections", ".ft-dev")
            shot("cables", f"tools/{RUN}/cables", "#az-form")
            pg.click("#az-form button[type=submit]"); pg.wait_for_selector("#az-csv", timeout=30000); pg.wait_for_timeout(600)
            pg.screenshot(path=str(OUT / f"cables_az_{width}.png"), full_page=True)
            shot("worklist", f"tools/{RUN}/worklist", ".ft-wl")
            shot("read_again", f"tools/{RUN}/again", ".ft-again")
            shot("physical_layer", f"tools/{RUN}/physical", ".ft-table")
            pg.goto(f"{engine}/api/video/{RUN}/physical-layer.html"); pg.wait_for_timeout(600)
            pg.screenshot(path=str(OUT / f"physical_layer_page_{width}.png"), full_page=True)
            shot("sfp_advice", f"tools/{RUN}/sfp", ".ft-sfp")
            shot("topology", f"tools/{RUN}/topology", "#ft-topo svg, #ft-topo canvas", 2500)
            shot("site_position", "tools/site", "#st-form")
            pg.goto(f"{engine}/app/#result/{RUN}/devices"); pg.wait_for_selector("#dv-list .prow", timeout=30000); pg.wait_for_timeout(800)
            pg.click('#dv-list .prow[data-dev="5"]'); pg.wait_for_selector(".ft-ids", timeout=15000); pg.wait_for_timeout(1500)
            pg.locator(".ft-ids").scroll_into_view_if_needed(); pg.wait_for_timeout(400)
            pg.locator("#res-dev").screenshot(path=str(OUT / f"device_ids_{width}.png"))          # the device card only (the page is long)
            pg.goto(f"{engine}/app/#result/{RUN}/report"); pg.wait_for_timeout(5000)
            pg.locator("#sec-compare").screenshot(path=str(OUT / f"record_location_{width}.png"))  # the compare in the Report, with the identification steps
            pg.locator("#sec-tools").scroll_into_view_if_needed(); pg.wait_for_timeout(500)
            pg.locator("#sec-tools").screenshot(path=str(OUT / f"report_tools_{width}.png"))       # the rack tools as a section inside the Report
            pg.close()
        b.close()
    return errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="http://127.0.0.1:8702")
    ap.add_argument("--auth", default="http://127.0.0.1:3305")
    ap.add_argument("--user", default="admint")
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--no-screens", action="store_true")
    a = ap.parse_args()
    import proof_guard
    proof_guard.check(a.user)
    pw = os.environ.get("RT_PROOF_PASSWORD") or (ROOT / "data" / "auth" / "seed-password.txt").read_text().strip()
    OUT.mkdir(parents=True, exist_ok=True)
    op, _ = opener(a.auth, a.user, pw)
    res = {}
    if a.setup:
        res["setup"] = setup(op, a.engine, a.auth)
    res["answers"] = answers(op, a.engine)
    if not a.no_screens:
        res["screen_errors"] = screens(a.engine, a.user, pw)
    old = json.loads((OUT / "results.json").read_text()) if (OUT / "results.json").exists() else {}
    old.update(res)
    (OUT / "results.json").write_text(json.dumps(old, indent=1))
    print(json.dumps({k: v for k, v in res.items() if k != "setup"}, indent=1)[:4000])


if __name__ == "__main__":
    main()
