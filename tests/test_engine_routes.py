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


def test_platform_reports_are_owner_only(eng):
    app, client, _ = eng
    before = dict(app.RACKS_REBUILD)
    for who in ("orgowner", "admin", "manager", "tech", "tech2"):
        c = _as(client, who)
        for method, path in (("GET", "/racks-report/racks_report.html"), ("GET", "/racks-report/rack_nr1_report.html"), ("GET", "/models-overview"),
                             ("POST", "/api/racks-report/rebuild"), ("GET", "/api/racks-report/status")):
            assert c.request(method, path).status_code == 404, (who, path)
    assert app.RACKS_REBUILD == before                            # nobody started a rebuild
    assert _as(client, "owner").get("/api/racks-report/status").status_code == 200


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
