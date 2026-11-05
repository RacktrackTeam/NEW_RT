"""The engine's route guards per role (server/app.py ROLE_RULES, _run_guard, rack_id_options), on a throwaway sign-in database and
runs folder: no model is loaded, nothing outside the test's own folder is read or written, no outside call is made."""
import importlib.util
import json
import pathlib
import sqlite3
import sys
import time

import jwt
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SECRET = "test-secret-for-the-route-guards-0123456789abcdef"
# id, username, role, organization, site
PEOPLE = [(1, "owner", "owner", None, None), (2, "orgowner", "org_owner", 1, 2), (3, "admin", "org_admin", 1, 2),
          (4, "manager", "site_manager", 1, 2), (5, "tech", "member", 1, 2), (6, "tech2", "member", 2, 3), (7, "manager4", "site_manager", 1, 4)]
ADMINS = {"owner", "orgowner", "admin"}


def _auth_db(path):
    c = sqlite3.connect(path)
    c.executescript("""
      CREATE TABLE organizations (id INTEGER PRIMARY KEY, name TEXT, status TEXT, online_lookups INTEGER DEFAULT 1);
      CREATE TABLE tenants (id INTEGER PRIMARY KEY, name TEXT, organization_id INTEGER);
      CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, role TEXT, organization_id INTEGER, tenant_id INTEGER, active INTEGER DEFAULT 1, token_version INTEGER DEFAULT 0);
      CREATE TABLE revoked_tokens (jti TEXT PRIMARY KEY, user_id INTEGER, revoked_at INTEGER, expires_at INTEGER);
      INSERT INTO organizations VALUES (1, 'Org One', 'active', 1), (2, 'Org Two', 'active', 1);
      INSERT INTO tenants VALUES (2, 'Site A', 1), (3, 'Site B', 2), (4, 'Site C', 1);
    """)
    c.executemany("INSERT INTO users (id, username, role, organization_id, tenant_id) VALUES (?, ?, ?, ?, ?)", PEOPLE)
    c.commit(); c.close()


@pytest.fixture(scope="module")
def eng(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("engine")
    _auth_db(tmp / "auth.db")
    mp = pytest.MonkeyPatch()
    mp.setenv("JWT_SECRET", SECRET); mp.setenv("RACKTRACK_AUTH_DB", str(tmp / "auth.db"))
    mp.setenv("RT_AUTH_ENV", str(tmp / "no.env")); mp.setenv("RACKTRACK_RUNS", str(tmp / "runs"))
    mp.setenv("RACKTRACK_SOURCES_DB", str(tmp / "sources.db")); mp.setenv("RACKTRACK_CONN_KEY_FILE", str(tmp / "connections.key"))
    mp.setenv("RACKTRACK_ONLINE_LOOKUPS", "0")
    sys.path.insert(0, str(ROOT / "server"))
    spec = importlib.util.spec_from_file_location("engine_app_under_test", ROOT / "server" / "app.py")
    app = importlib.util.module_from_spec(spec); sys.modules[spec.name] = app
    spec.loader.exec_module(app)
    import connections
    import source_db
    mp.setattr(source_db, "DB", tmp / "sources.db"); mp.setattr(connections, "DB", tmp / "sources.db")
    mp.setattr(connections, "KEY_FILE", tmp / "connections.key")
    mp.setattr(app, "CHECKS", tmp / "checks")
    mp.setattr(source_db, "import_netbox", lambda org=None: {"ok": True, "stub": True})     # never the mock, never the network
    mp.setattr(source_db, "import_cmdb", lambda org=None: {"ok": True, "stub": True})
    mp.setattr(connections, "test", lambda org, kind, get: None)                         # Test connection reaches nothing here
    from fastapi.testclient import TestClient
    yield app, TestClient(app.app), tmp
    mp.undo()


def _token(uid):
    return jwt.encode({"sub": uid, "iss": "racktrack", "aud": "racktrack-app", "exp": int(time.time()) + 600, "tv": 0}, SECRET, algorithm="HS256")


def _as(client, who):
    uid = next(p[0] for p in PEOPLE if p[1] == who)
    client.cookies.clear(); client.cookies.set("rt_access", _token(uid))
    return client


def _run(app, tmp, rid, org, site, rack_id=None):
    d = tmp / "runs" / str(org) / str(site) / rid; d.mkdir(parents=True, exist_ok=True)
    app.JOBS[rid] = {"id": rid, "status": "done", "progress": "done", "dir": d, "result": None, "org": org, "site": site, "started": "2026-11-02T10:00:00",
                     "rack_id": {"id": rack_id} if rack_id else None, "title": rid}
    return app.JOBS[rid]


def test_no_session_is_refused(eng):
    app, client, _ = eng
    client.cookies.clear()
    for method, path in (("GET", "/api/me"), ("DELETE", "/api/connections/netbox"), ("DELETE", "/api/sources/copies/1"), ("GET", "/api/video/history")):
        assert client.request(method, path).status_code == 401, path


@pytest.mark.parametrize("who", [p[1] for p in PEOPLE])
def test_logins_and_copies_are_admin_only(eng, who):
    app, client, _ = eng
    c = _as(client, who)
    calls = [("DELETE", "/api/connections/netbox", None), ("DELETE", "/api/connections/cmdb", None), ("POST", "/api/connections/netbox/test", None),
             ("POST", "/api/connections/netbox", {"address": "http://127.0.0.1:3303", "token": "x"}), ("POST", "/api/sources/netbox/import", None),
             ("POST", "/api/sources/cmdb/import", None), ("POST", "/api/sources/excel/import", {"upload": "nothing.xlsx"}), ("DELETE", "/api/sources/copies/999", None)]
    for method, path, body in calls:
        r = c.request(method, path, json=body)
        if who in ADMINS:
            assert r.status_code != 403, (who, path, r.text)
        else:
            assert r.status_code == 403, (who, path, r.status_code)
            assert "organization admin" in r.json()["error"]
    assert c.get("/api/connections").status_code == 200          # reading the address and state is open to everyone of the organization


def test_run_delete_by_role(eng):
    app, client, tmp = eng
    for who, want in (("tech", 403), ("tech2", 404), ("manager4", 404), ("manager", 200), ("admin", 200), ("orgowner", 200), ("owner", 200)):
        rid = f"del{who}"
        _run(app, tmp, rid, 1, 2)
        r = _as(client, who).delete(f"/api/video/{rid}")
        assert r.status_code == want, (who, r.status_code, r.text)
        assert (rid in app.JOBS) == (want != 200)
        app.JOBS.pop(rid, None)


def test_removed_pages_are_gone(eng):
    """The racks report built from three old runs, the models overview and the removed tabs' routes are not there for anyone."""
    app, client, _ = eng
    for who in ("owner", "orgowner", "admin", "tech"):
        c = _as(client, who)
        for method, path in (("GET", "/racks-report/racks_report.html"), ("GET", "/models-overview"), ("POST", "/api/racks-report/rebuild"),
                             ("GET", "/api/video/x/worklist"), ("GET", "/api/video/x/read-again"), ("GET", "/api/video/x/physical-layer"),
                             ("GET", "/api/video/x/topology")):
            assert c.request(method, path).status_code in (404, 405), (who, path)


def test_runs_seen_by_role(eng):
    app, client, tmp = eng
    for rid, org, site in (("seeA", 1, 2), ("seeC", 1, 4), ("seeB", 2, 3)):
        _run(app, tmp, rid, org, site)
    seen = lambda who: {r["id"] for r in _as(client, who).get("/api/video/history").json() if r["id"].startswith("see")}
    assert seen("tech") == {"seeA"} and seen("manager") == {"seeA"} and seen("manager4") == {"seeC"}
    assert seen("admin") == {"seeA", "seeC"} and seen("tech2") == {"seeB"} and seen("owner") == {"seeA", "seeB", "seeC"}
    assert _as(client, "tech2").get("/api/video/seeA").status_code == 404
    for rid in ("seeA", "seeB", "seeC"):
        app.JOBS.pop(rid)


def test_rack_id_options_stay_in_the_organization_and_site(eng):
    app, client, tmp = eng
    mine = _run(app, tmp, "optA", 1, 2)
    _run(app, tmp, "optA2", 1, 2, "SP-HYD-R1"); _run(app, tmp, "optC", 1, 4, "OTHER-SITE-R1"); _run(app, tmp, "optB", 2, 3, "SECOND-ORG-R1")
    import rt_session
    tok = rt_session.CURRENT.set({"user": 3, "username": "admin", "role": "org_admin", "org": 1, "site": 2})
    try:
        ids = {o["id"] for o in app.rack_id_options(mine)}
    finally:
        rt_session.CURRENT.reset(tok)
    assert "SP-HYD-R1" in ids and "OTHER-SITE-R1" not in ids and "SECOND-ORG-R1" not in ids
    for rid in ("optA", "optA2", "optC", "optB"):
        app.JOBS.pop(rid)


def test_no_lookup_leaves_the_machine(eng):
    app, client, _ = eng
    assert app.online_lookups({"org": 1, "role": "org_admin"}) is False       # RACKTRACK_ONLINE_LOOKUPS is not 1 in tests
    import os
    assert os.environ.get("SWITCH_MAPPER_WEB_SEARCH") == "0"
    sh = (ROOT / "engine.sh").read_text()
    assert "export SWITCH_MAPPER_WEB_SEARCH=0" in sh and "export RACKTRACK_ONLINE_LOOKUPS=0" in sh


def test_stored_demo_is_the_platform_owners_only(eng):
    import rt_session
    tech = {"user": 5, "username": "tech", "role": "member", "org": 1, "site": 2}
    assert rt_session.can_see(tech, {"id": "example-vdo1"}) is False
    assert rt_session.can_see({"user": 1, "role": "owner", "org": None}, {"id": "example-vdo1"}) is True
    app, client, _ = eng
    tok = rt_session.CURRENT.set(tech)
    try:
        assert app.job_or_none("example-vdo1") is None
    finally:
        rt_session.CURRENT.reset(tok)
    assert _as(client, "tech").get("/api/video/example-vdo1/view").status_code == 404


def test_one_run_at_a_time_while_stitching(eng):
    app, client, tmp = eng
    _run(app, tmp, "busy1", 1, 2)["status"] = "running"            # stitching: the model lock is not held yet
    try:
        r = _as(client, "tech").post("/api/video", data={"sample": "vdo1"})
        assert r.status_code == 409 and "one runs at a time" in r.json()["error"]
    finally:
        app.JOBS.pop("busy1")


def test_camera_takes_stay_in_their_organization(eng, monkeypatch):
    app, client, tmp = eng
    import captures
    monkeypatch.setitem(captures.SOURCES, "app", tmp / "captures"); monkeypatch.setitem(captures.SOURCES, "team", tmp / "team")
    for name, owner in (("t1__a", {"org": 1, "site": 2}), ("t2__b", {"org": 2, "site": 3}), ("t3__c", None)):
        d = tmp / "captures" / name; d.mkdir(parents=True)
        (d / "video.mp4").write_bytes(b"0")
        if owner:
            (d / "owner.json").write_text(json.dumps(owner))
    listed = lambda who: {t["folder"] for t in _as(client, who).get("/api/captures").json()["takes"]}
    assert listed("tech") == {"t1__a"} and listed("tech2") == {"t2__b"} and listed("owner") == {"t1__a", "t2__b", "t3__c"}
    assert _as(client, "tech").get("/api/captures/app/t2__b/video").status_code == 404


def test_contact_limit_is_per_sender(eng):
    app, client, _ = eng
    client.cookies.clear()
    app.CONTACT_SEEN.clear()
    import pathlib as _p
    sent = []
    orig = _p.Path.write_text
    try:
        _p.Path.write_text = lambda self, *a, **k: sent.append(self.name)             # nothing is written to data/contact by the test
        msg = lambda email: client.post("/api/contact", json={"name": "A", "email": email, "message": "hello"}).status_code
        assert [msg("one@x.test") for _ in range(4)] == [200, 200, 200, 429]
        assert msg("two@x.test") == 200                                                # another sender is not blocked
    finally:
        _p.Path.write_text = orig
        app.CONTACT_SEEN.clear()


def test_a_file_that_is_not_a_video_is_refused(eng):
    app, client, tmp = eng
    before = {p for p in (tmp / "runs").rglob("*")} if (tmp / "runs").exists() else set()
    r = _as(client, "tech").post("/api/video", files={"video": ("notes.txt", b"not a video", "text/plain")})
    assert r.status_code == 400 and r.json()["error"] == "notes.txt is not a video. Send an MP4 or MOV video of the rack."
    r = _as(client, "tech").post("/api/video")
    assert r.status_code == 400 and r.json()["error"] == "Send a rack video or a take."
    after = {p for p in (tmp / "runs").rglob("*")} if (tmp / "runs").exists() else set()
    assert not [p for p in after - before if p.is_dir() and not any(p.iterdir())]          # no empty run folder is left behind


def test_every_error_has_the_same_shape(eng):
    app, client, _ = eng
    c = _as(client, "tech")
    assert c.get("/api/not-a-route").json() == {"error": "Not Found"}
    r = c.put("/api/video/history")
    assert r.status_code == 405 and "error" in r.json() and r.headers.get("allow") == "GET"
    r = c.post("/api/video/x/database", content=b"{bad", headers={"Content-Type": "application/json"})
    assert r.status_code in (400, 404) and "error" in r.json()


# ---- the testers' findings of 4 Nov 2026 (server side)
def test_the_run_stage_never_goes_down(eng):
    app = eng[0]
    seq = ["checking which way the video runs", "stitching the video into one rack picture", "stitching: aligning the frames (40 %)", "rack image: the rails",
           "rack image: units", "rack image: occupancy", "rack image: devices", "reading the video", "registering 40 frames", "registered frame 5 (1/40)",
           "running the photo pipeline on 12 frames", "frame 12: 20 units (1/12)", "fusing", "reading text on the units", "best frames per device (every frame of the video)",
           "drawing", "classifying devices", "units: 3 rows split, 0 inserted, 0 merged (equal-size count rule)", "units: picture corrected so every unit is 44 px tall",
           "picture levelled: lean up to 1.2 % taken out", "devices: 5 outlines taken from their crop boxes", "ports: reading every switch, router, firewall and gateway",
           "building the report", "done"]
    stage, seen = 0, []
    for m in seq:
        stage = max(stage, app.stage_of(m) or 0); seen.append(stage)
    assert seen == sorted(seen) and seen[0] == 1 and seen[-1] == 15
    assert {3, 5, 8, 11, 14} <= set(seen)


def test_a_re_encoded_video_is_the_same_video(eng, tmp_path):
    import cv2
    import numpy as np
    app = eng[0]
    def write(path, fps, skip_every=0, seed=1):
        rng = np.random.default_rng(seed); vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (160, 120))
        scenes = [cv2.GaussianBlur(rng.integers(0, 255, (120, 160, 3), dtype=np.uint8), (0, 0), 4) for _ in range(6)]
        for i in range(60):
            if skip_every and i % skip_every == 0:
                continue
            vw.write(scenes[i * 6 // 60])
        vw.release()
    write(tmp_path / "a.mp4", 30); write(tmp_path / "b.mp4", 29, skip_every=30); write(tmp_path / "c.mp4", 30, seed=2)
    fa, fb, fc = (app._video_fingerprint(tmp_path / n) for n in ("a.mp4", "b.mp4", "c.mp4"))
    assert fa and fb and fc
    assert app._same_look(fa, fb)                               # the phone's re-encode: other frame rate and count, the same footage
    assert not app._same_look(fa, fc)                           # another video of the same length is not it


def test_a_port_read_does_not_refuse_an_upload(eng):
    app, client, tmp = eng
    with app.MODEL_LOCK:                                        # a device's ports being read holds the model lock
        r = _as(client, "tech").post("/api/video", data={"sample": "vdo1"})
    assert r.status_code == 400 and "video" in r.json()["error"]   # refused only for the missing video, not as "another rack is being read"


def test_a_second_run_is_refused_with_the_step(eng):
    app, client, tmp = eng
    j = _run(app, tmp, "busy2", 1, 2); j["status"] = "running"; j["stage"] = 7
    try:
        r = _as(client, "tech").post("/api/video", data={"sample": "vdo1"})
        assert r.status_code == 409 and "step 7 of 15" in r.json()["error"] and "one runs at a time" in r.json()["error"]
    finally:
        app.JOBS.pop("busy2")
