"""connections: the logins vault (AES-256-GCM at rest, key file mode 600, never a secret in what a screen gets) and Test connection."""
import json
import pathlib
import stat
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))


@pytest.fixture()
def cn(tmp_path, monkeypatch):
    import connections
    import source_db
    monkeypatch.setattr(connections, "DB", tmp_path / "sources.db")
    monkeypatch.setattr(source_db, "DB", tmp_path / "sources.db")
    monkeypatch.setattr(connections, "KEY_FILE", tmp_path / "connections.key")
    return connections


def test_secrets_sealed_at_rest_and_never_in_meta(cn, tmp_path):
    assert cn.meta("default")["netbox"]["state"] == "mock"            # no login saved: the mock stays the default
    cn.save("default", "netbox", "http://127.0.0.1:3303/", token="tok-SECRET-123", by="Proof")
    cn.save("default", "cmdb", "http://127.0.0.1:3304", username="admin", password="pw-SECRET-456")
    assert stat.S_IMODE((tmp_path / "connections.key").stat().st_mode) == 0o600
    raw = (tmp_path / "sources.db").read_bytes()
    assert b"SECRET" not in raw and b"admin" not in raw                # nothing readable in the database file
    m = json.dumps(cn.meta("default"))
    assert "SECRET" not in m and "admin" not in m
    assert cn.meta("default")["netbox"]["token"] is True and cn.meta("default")["netbox"]["address"] == "http://127.0.0.1:3303"
    assert cn.resolve("default", "cmdb")["secret"] == {"username": "admin", "password": "pw-SECRET-456"}
    cn.save("default", "cmdb", "http://127.0.0.1:3304", password="")   # blank keeps what was saved
    assert cn.resolve("default", "cmdb")["secret"]["password"] == "pw-SECRET-456"
    assert cn.meta("other-org")["cmdb"]["saved"] is False              # another organization sees nothing


def test_blob_opens_only_with_its_key(cn, tmp_path):
    s = cn.seal("hello")
    assert s.startswith("v1:") and cn.unseal(s) == "hello"
    (tmp_path / "connections.key").write_text("00" * 32)
    assert cn.unseal(s) is None


def test_validation(cn):
    with pytest.raises(ValueError):
        cn.save("default", "netbox", "127.0.0.1:3303", token="x")
    with pytest.raises(ValueError):
        cn.save("default", "cmdb", "http://127.0.0.1:3304", username="a")


def test_test_connection_states_and_auth_headers(cn):
    seen = []

    def ok(base, path, headers):
        seen.append((base, path, headers))
        return {"netbox-version": "4.0.0-mock"} if "status" in path else {"result": [{}]}

    def down(base, path, headers):
        raise ConnectionError(f"{base} does not answer.")
    cn.save("default", "netbox", "http://127.0.0.1:3303", token="t1")
    cn.save("default", "cmdb", "http://127.0.0.1:3304", username="u", password="p")
    assert cn.test("default", "netbox", ok)["state"] == "ok"
    assert cn.test("default", "cmdb", ok)["state"] == "ok"
    assert seen[0][1] == "/api/status/" and seen[0][2]["Authorization"] == "Token t1"
    assert seen[1][1].startswith("/api/now/table/") and seen[1][2]["Authorization"].startswith("Basic ")
    r = cn.test("default", "netbox", down)
    assert r["state"] == "failed" and "does not answer" in r["state_words"]
    with pytest.raises(LookupError):
        cn.remove("default", "netbox"); cn.test("default", "netbox", ok)


def test_import_uses_the_saved_login(cn, monkeypatch):
    import source_db
    calls = []

    def fake(base, path, headers=None):
        calls.append((base, headers))
        return {"results": []}
    monkeypatch.setattr(source_db, "_get", fake)
    source_db.import_netbox("default")
    assert calls[0] == (source_db.NETBOX_URL, None)                    # no login: the mock, no header
    cn.save("default", "netbox", "http://localhost:3303", token="t9")
    source_db.import_netbox("default")
    assert calls[-1][0] == "http://localhost:3303" and calls[-1][1]["Authorization"] == "Token t9"
