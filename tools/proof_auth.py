"""Proof for sign-in and organizations: the RT-EOS sign-in service (auth.sh) in front of the engine (engine.sh).

  1 No engine route answers without a session: every route in server/app.py is called with no cookie (and with a token
    signed by the wrong key); only /api/health and /api/auth-service answer.
  2 A technician sees only the runs of their own site; another site's run, another organization's run and a run from
    before sign-in answer 404 (history, view, report, delete). The organization admin sees the organization's runs, the
    platform owner every run.
  3 Two organizations cannot see each other's database copies or connections.
  4 A new organization (signed up with the code from the local mail outbox) waits: the engine answers 403 until the
    platform owner approves it, then the same session works.
  5 Forgot password by a code from the outbox, and an invite link accepted, both end in a working engine session.
  6 Screenshots at 1280 and 400 px in docs/proof/auth/: sign-in, forgot password, waiting, the technician's Home and
    Profile, the admin's Profile, RT-EOS's Organization, Sites and people and Settings screens, the owner's console.

Before: ./auth.sh (seeds owner, admin, tech; the password is in data/auth/seed-password.txt), then the test fixtures
(--fixtures: the extra organization and site with admin2 / tech2 and admin3 / tech3, and four stored runs placed in
data/runs/<org>/<site>/), then the engine on 8701 (RACKTRACK_PORT=8701 ./engine.sh).

  _cache/venv/bin/python tools/proof_auth.py [--auth http://127.0.0.1:3302] [--engine http://127.0.0.1:8701] [--fixtures] [--no-shots]
"""
import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

import jwt
import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
AUTH_DIR = ROOT / "data" / "auth"
OUT = ROOT / "docs" / "proof" / "auth"
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    return ok


def password():
    return (AUTH_DIR / "seed-password.txt").read_text().strip()


def settings():
    out = {}
    for line in (AUTH_DIR / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class Who:
    """One signed-in person: a requests session holding the service's cookies (sent to the engine too: same host)."""

    def __init__(self, auth, engine, username=None, pw=None):
        self.auth, self.engine, self.s = auth, engine, requests.Session()
        self.s.headers["Origin"] = auth
        if username:
            r = self.s.post(auth + "/api/auth/login", json={"username": username, "password": pw or password()})
            r.raise_for_status()
            self.user = r.json()["user"]

    def a(self, method, path, **kw):
        return self.s.request(method, self.auth + path, **kw)

    def e(self, method, path, **kw):
        return self.s.request(method, self.engine + path, headers={"Origin": self.engine}, **kw)


# ------------------------------------------------------------------ fixtures
def fixtures(auth):
    """admin2 / tech2 in a second organization (Pune DC), admin3 / tech3 in a second site of the first (Chennai DC), and the
    four stored runs: one per site of the first organization, one in the second organization, one from before sign-in."""
    env = dict(os.environ, RACKTRACK_AUTH_DB=settings()["RACKTRACK_AUTH_DB"], RT_SEED_PASSWORD=password(), RT_ENV_FILE=str(AUTH_DIR / ".env"))
    for org, site, suf in (("Second Organization", "Pune DC", "2"), ("Demo Organization", "Chennai DC", "3")):
        subprocess.run(["node", "scripts/seed-auth.js", "--org", org, "--site", site, "--suffix", suf], cwd=ROOT / "rt_eos" / "server", env=env,
                       check=True, capture_output=True)
    ids = {}
    for u in ("tech", "tech2", "tech3"):
        w = Who(auth, None, u)
        ids[u] = (w.user["organization_id"], w.user["tenant_id"])
    src = ROOT / "samples" / "video" / "run_of_vdo1"
    runs = ROOT / "data" / "runs"
    for rid, who, title in (("fx1hyd000001", "tech", "Hyderabad DC rack (Demo Organization)"), ("fx1chn000001", "tech3", "Chennai DC rack (Demo Organization)"),
                            ("fx2pun000001", "tech2", "Pune DC rack (Second Organization)"), ("fxlegacy0001", None, "Run from before sign-in")):
        org, site = ids[who] if who else (None, None)
        d = runs / rid if org is None else runs / str(org) / str(site) / rid
        if d.exists():
            continue
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, d)
        st = json.loads((d / "status.json").read_text()) if (d / "status.json").exists() else {"status": "done", "progress": "done", "phase": "done"}
        st.update(id=rid, title=title, org=org, site=site, status="done", started=st.get("started") or "2026-11-02T10:00:00")
        (d / "status.json").write_text(json.dumps(st, indent=1))
    print("fixtures ready; start (or restart) the engine now so it loads the runs")


# ------------------------------------------------------------------ 1 no route without a session
def routes():
    """Every route declared in server/app.py, with its path parameters filled."""
    src = (ROOT / "server" / "app.py").read_text()
    out = []
    for m in re.finditer(r'@app\.(get|post|delete|put|api_route)\("([^"]+)"(?:,\s*methods=\[([^\]]+)\])?', src):
        verb, path, methods = m.group(1), m.group(2), m.group(3)
        verbs = [x.strip(' "\'') for x in methods.split(",")] if methods else [verb.upper()]
        path = re.sub(r"\{(\w+)(:path)?\}", lambda g: {"index": "1", "nth": "1", "cid": "1", "unit": "1"}.get(g.group(1), "fx1hyd000001"), path)
        out += [(v if v != "HEAD" else "GET", path) for v in verbs if v != "HEAD"]
    return out


def test_no_session(engine):
    open_ok = {"/api/health", "/api/auth-service", "/", "/manifest.webmanifest", "/sw.js", "/favicon.ico", "/logo.jpg"}
    bad = []
    forged = jwt.encode({"sub": 1, "role": "owner", "iss": "racktrack", "aud": "racktrack-app", "exp": int(time.time()) + 600}, "a-different-key-that-the-service-never-used-0123456789", algorithm="HS256")
    n = 0
    for method, path in routes():
        if path in open_ok:
            continue
        n += 1
        for cookies in ({}, {"rt_access": forged}):
            r = requests.request(method, engine + path, cookies=cookies, allow_redirects=False, timeout=30)
            # an /api route answers 401; a page (report, capture) sends the browser to the sign-in screen
            if not (r.status_code == 401 or (r.status_code in (302, 307) and "signin" in r.headers.get("location", ""))):
                bad.append(f"{method} {path} -> {r.status_code}")
    check("no engine route answers without a session (no cookie, and a token signed with another key)", not bad,
          f"{n} routes called twice" + (": " + "; ".join(bad[:6]) if bad else ""))
    r = requests.get(engine + "/api/health")
    check("health stays open", r.status_code == 200)


# ------------------------------------------------------------------ 2 runs per organization and site
FX = ["fx1hyd000001", "fx1chn000001", "fx2pun000001", "fxlegacy0001"]


def seen(w):
    return {r["id"] for r in w.e("GET", "/api/video/history").json()} & set(FX)


def test_runs(auth, engine):
    tech, admin, admin2, owner = (Who(auth, engine, u) for u in ("tech", "admin", "admin2", "owner"))
    check("a technician sees only their site's runs", seen(tech) == {"fx1hyd000001"}, f"tech sees {sorted(seen(tech))}")
    codes = {r: [tech.e("GET", f"/api/video/{r}").status_code, tech.e("GET", f"/api/video/{r}/view").status_code,
                 tech.e("GET", f"/video-report/{r}/index.html").status_code, tech.e("GET", f"/api/video/{r}/drift").status_code] for r in FX}
    check("the technician's own run opens (status, view, report, drift)", codes["fx1hyd000001"][:3] == [200, 200, 200], str(codes["fx1hyd000001"]))
    strangers = {r: c for r, c in codes.items() if r != "fx1hyd000001"}
    check("another site's, another organization's and a pre-sign-in run answer 404", all(set(c) == {404} for c in strangers.values()), str(strangers))
    d = tech.e("DELETE", "/api/video/fx2pun000001")
    check("deleting another organization's run answers 404", d.status_code == 404, str(d.status_code))
    check("the organization admin sees both sites of the organization", seen(admin) == {"fx1hyd000001", "fx1chn000001"}, f"admin sees {sorted(seen(admin))}")
    check("the second organization's admin sees only its own run", seen(admin2) == {"fx2pun000001"}, f"admin2 sees {sorted(seen(admin2))}")
    check("the platform owner sees every run", seen(owner) == set(FX), f"owner sees {sorted(seen(owner))}")
    me = tech.e("GET", "/api/me").json()
    check("the engine knows the person from the session", me.get("name") == "tech" and me.get("role") == "member" and me.get("site_name") == "Hyderabad DC", json.dumps(me))


# ------------------------------------------------------------------ 3 copies and connections per organization
def test_copies_and_connections(auth, engine):
    admin, admin2 = Who(auth, engine, "admin"), Who(auth, engine, "admin2")
    wb = ROOT / "modules" / "sources" / "ground_truth" / "vdo1.xlsx"
    with open(wb, "rb") as f:
        rep = admin.e("POST", "/api/sources/excel", files={"workbook": ("vdo1.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}).json()
    imp = admin.e("POST", "/api/sources/excel/import", json={"upload": rep.get("upload")})
    check("the first organization imports a workbook", imp.status_code == 200, str(imp.status_code))
    stolen = admin2.e("POST", "/api/sources/excel/import", json={"upload": rep.get("upload")})
    check("the second organization cannot import the first one's upload", stolen.status_code == 404, str(stolen.status_code))
    a1 = admin.e("GET", "/api/sources/copies").json()["copies"]
    a2 = admin2.e("GET", "/api/sources/copies").json()["copies"]
    check("database copies are per organization", len(a1) >= 1 and len(a2) == 0, f"first {len(a1)}, second {len(a2)}")
    if a1:
        d = admin2.e("DELETE", f"/api/sources/copies/{a1[0]['id']}")
        check("the second organization cannot delete the first one's copy", d.status_code == 404 and len(admin.e("GET", "/api/sources/copies").json()["copies"]) == len(a1), str(d.status_code))
    s = admin.e("POST", "/api/connections/netbox", json={"address": "http://127.0.0.1:9/", "token": "proof-token-not-real"})
    c1 = admin.e("GET", "/api/connections").json()["connections"]["netbox"]
    c2 = admin2.e("GET", "/api/connections").json()["connections"]["netbox"]
    check("connections are per organization", s.status_code == 200 and c1["saved"] and not c2["saved"], f"first saved {c1['saved']}, second saved {c2['saved']}")
    check("a connection never sends its secret back", "proof-token-not-real" not in json.dumps(c1))
    admin.e("DELETE", "/api/connections/netbox")


# ------------------------------------------------------------------ 4 a new organization waits for the owner
def outbox_code(email, after):
    for _ in range(30):
        for f in sorted((AUTH_DIR / "outbox").glob("*.json"), reverse=True):
            j = json.loads(f.read_text())
            if j.get("to") == email and f.stat().st_mtime >= after:
                m = re.search(r"\b(\d{6})\b", j.get("text") or "")
                if m:
                    return m.group(1)
        time.sleep(0.3)
    return None


def test_pending(auth, engine):
    stamp = str(int(time.time()))[-6:]
    email, user = f"neworg{stamp}@gmail.com", f"neworg{stamp}"
    w = Who(auth, engine)
    t0 = time.time() - 1
    r = w.a("POST", "/api/auth/signup", json={"email": email, "username": user, "password": password(), "company": f"New Organization {stamp}"})
    code = outbox_code(email, t0)
    check("sign-up sends its code to the local outbox (no real mail)", r.status_code == 200 and code is not None, str(r.status_code))
    v = w.a("POST", "/api/auth/verify", json={"email": email, "code": code})
    org = (v.json().get("user") or {}).get("organization") or {}
    check("the new organization starts as pending", v.status_code == 200 and org.get("status") == "pending", json.dumps(org))
    blocked = [w.e("GET", p).status_code for p in ("/api/me", "/api/video/history", "/api/sources/copies")]
    check("the engine refuses a pending organization", blocked == [403, 403, 403], str(blocked))
    owner = Who(auth, engine, "owner")
    waiting = [o["id"] for o in owner.a("GET", "/api/orgs/pending").json().get("pending", [])]
    ok = owner.a("POST", f"/api/orgs/{org.get('id')}/approve")
    check("the owner sees it waiting and approves it", org.get("id") in waiting and ok.status_code == 200, f"waiting {waiting}, approve {ok.status_code}")
    after = w.e("GET", "/api/me")
    check("after approval the same session opens the engine", after.status_code == 200, str(after.status_code))
    return user


# ------------------------------------------------------------------ 5 forgot password and invite
def test_forgot_and_invite(auth, engine):
    w = Who(auth, engine)
    t0 = time.time() - 1
    w.a("POST", "/api/auth/forgot-password", json={"email": "tech3@demo.local"})
    code = outbox_code("tech3@demo.local", t0)
    r = w.a("POST", "/api/auth/reset-password", json={"email": "tech3@demo.local", "code": code, "password": password()})
    check("forgot password: the code from the outbox resets the password", code is not None and r.status_code == 200, str(r.status_code))
    try:
        Who(auth, engine, "tech3")
        check("the reset password signs in", True)
    except requests.HTTPError as e:
        check("the reset password signs in", False, str(e))
    admin = Who(auth, engine, "admin")
    stamp = str(int(time.time()))[-6:]
    inv = admin.a("POST", f"/api/sites/{Who(auth, engine, 'tech').user['tenant_id']}/invites", json={"email": f"invited{stamp}@demo.local", "role": "member"})
    code = (inv.json().get("invite") or {}).get("code")
    j = Who(auth, engine)
    look = j.a("GET", f"/api/invites/{code}")
    acc = j.a("POST", f"/api/invites/{code}/accept", json={"username": f"invited{stamp}", "password": password()})
    me = j.e("GET", "/api/me")
    check("an invite link is accepted and opens the engine on the invited site", inv.status_code == 200 and look.status_code == 200 and acc.status_code == 200
          and me.status_code == 200 and me.json().get("site_name") == "Hyderabad DC", f"{inv.status_code} {look.status_code} {acc.status_code} {me.status_code}")
    prov = j.a("GET", "/api/auth/providers").json()
    check("Google, Apple and Facebook stay hidden without keys (the service lists no provider)", prov.get("providers") == [], json.dumps(prov))


# ------------------------------------------------------------------ 6 screenshots
def shots(auth, engine, pending_user, new_admin=None):
    from playwright.sync_api import sync_playwright
    OUT.mkdir(parents=True, exist_ok=True)
    pw = password()
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("PW_CHROMIUM") or None)
        for width, height in ((1280, 860), (400, 860)):
            def page_for():
                ctx = b.new_context(viewport={"width": width, "height": height}, device_scale_factor=1)
                return ctx, ctx.new_page()

            def sign_in(pg, user, pw_=pw):
                pg.goto(engine + "/app/#signin"); pg.wait_for_selector("#si-user", state="visible")
                pg.fill("#si-user", user); pg.fill("#si-pass", pw_); pg.click("#signin button[type=submit]")

            def snap(pg, name):
                pg.wait_for_timeout(1300); pg.screenshot(path=str(OUT / f"{name}_{width}.png"), full_page=False)

            ctx, pg = page_for()
            pg.goto(engine + "/app/"); pg.wait_for_selector("#si-user", state="visible"); snap(pg, "signin")
            social_hidden = pg.is_hidden("#si-social")
            pg.goto(engine + "/app/#forgot"); pg.wait_for_selector("#fg-email", state="visible"); snap(pg, "forgot")
            ctx.close()

            ctx, pg = page_for(); sign_in(pg, "tech"); pg.wait_for_selector('[data-screen="home"]:not([hidden])'); snap(pg, "tech_home")
            pg.goto(engine + "/app/#history"); snap(pg, "tech_history")
            hist = pg.inner_text('[data-screen="history"]')
            pg.goto(engine + "/app/#more"); pg.wait_for_selector("#org-links:not([hidden])"); snap(pg, "tech_profile")
            tech_links = pg.inner_text("#org-links"); ctx.close()

            ctx, pg = page_for(); sign_in(pg, "admin"); pg.wait_for_selector('[data-screen="home"]:not([hidden])')
            pg.goto(engine + "/app/#more"); pg.wait_for_selector("#org-links:not([hidden])"); snap(pg, "admin_profile")
            admin_links = pg.inner_text("#org-links")
            for path, name in (("/org/settings", "org_organization"), ("/org/site", "org_sites_people"), ("/org/you", "org_settings")):
                pg.goto(auth + path); pg.wait_for_load_state("networkidle"); snap(pg, name)
            ctx.close()

            ctx, pg = page_for(); sign_in(pg, "owner"); pg.wait_for_selector('[data-screen="home"]:not([hidden])')
            pg.goto(auth + "/org/console"); pg.wait_for_load_state("networkidle"); snap(pg, "owner_console")
            console_text = pg.inner_text("body")
            pg.goto(auth + "/org/settings"); pg.wait_for_load_state("networkidle")
            pg.get_by_text("Every organization on this server").click(); pg.wait_for_load_state("networkidle"); snap(pg, "owner_organizations")
            orgs_text = pg.inner_text("body"); ctx.close()
            if width == 1280:
                check("the owner's console opens (logs and dashboard served by the sign-in service)", "Not found" not in console_text)
                check("the owner's organizations screen lists the organization waiting for approval", "Waiting Organization" in orgs_text, orgs_text[:200].replace("\n", " "))

            if new_admin:        # an approved new organization's admin: sent to RT-EOS's first-run setup (a technician never is)
                ctx, pg = page_for(); sign_in(pg, new_admin); pg.wait_for_url(re.compile(r".*/org/setup.*"), timeout=20000); pg.wait_for_load_state("networkidle")
                snap(pg, "admin_first_run_setup"); setup_url = pg.url; ctx.close()
                if width == 1280:
                    check("a new organization's admin is sent to RT-EOS's first-run setup", "/org/setup" in setup_url, setup_url)

            if pending_user:     # a second new organization, left waiting, for the waiting screen
                ctx, pg = page_for(); sign_in(pg, pending_user); pg.wait_for_selector('[data-screen="pending"]:not([hidden])', timeout=15000); snap(pg, "pending"); ctx.close()

            if width == 1280:
                check("sign-in screen hides the Google, Apple and Facebook buttons without keys", social_hidden)
                check("the technician's history lists only their site's run", "Hyderabad DC rack" in hist and "Chennai" not in hist and "Pune" not in hist)
                check("Profile: the technician gets Settings only; the admin gets Organization, Sites and people, Settings",
                      "Organization" not in tech_links and "Settings" in tech_links and all(x in admin_links for x in ("Organization", "Sites and people", "Settings")),
                      f"tech: {tech_links.split(chr(10))[0]}")
        b.close()


def waiting_org(auth, engine):
    """A new organization left pending, for the waiting screen."""
    stamp = str(int(time.time()))[-6:]
    email, user = f"waiting{stamp}@gmail.com", f"waiting{stamp}"
    w = Who(auth, engine); t0 = time.time() - 1
    w.a("POST", "/api/auth/signup", json={"email": email, "username": user, "password": password(), "company": f"Waiting Organization {stamp}"})
    w.a("POST", "/api/auth/verify", json={"email": email, "code": outbox_code(email, t0)})
    return user


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--auth", default="http://127.0.0.1:3302")
    ap.add_argument("--engine", default="http://127.0.0.1:8701")
    ap.add_argument("--fixtures", action="store_true")
    ap.add_argument("--no-shots", action="store_true")
    a = ap.parse_args()
    if a.fixtures:
        return fixtures(a.auth)
    test_no_session(a.engine)
    test_runs(a.auth, a.engine)
    test_copies_and_connections(a.auth, a.engine)
    new_admin = test_pending(a.auth, a.engine)
    test_forgot_and_invite(a.auth, a.engine)
    if not a.no_shots:
        shots(a.auth, a.engine, waiting_org(a.auth, a.engine), new_admin)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps({"when": time.strftime("%Y-%m-%d %H:%M"), "checks": RESULTS}, indent=1))
    bad = [r for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS) - len(bad)} of {len(RESULTS)} checks pass")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
