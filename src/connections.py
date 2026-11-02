"""connections.py - the logins vault for NetBox and ServiceNow, one per organization and type, in data/sources.db.

Copied in shape from dark_mobile's server/lib/connection_profiles.js (the org-scoped half: one active login per
organization and type, write-only, merged on update) and server/lib/netbox/secrets.js (AES-256-GCM, a 32-byte key in a
file with mode 600, sealed as v1:iv:tag:body). The username, password and token live only in the sealed blob; the address
is kept in clear so the screen can show it. Nothing here ever returns a secret to a screen: meta() is all a route sends.
Test connection reads NetBox GET /api/status/ or one row of ServiceNow's Table API. Only this machine is called today.
"""
import base64
import datetime
import json
import os
import pathlib
import secrets as _rand
import sqlite3

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB = pathlib.Path(os.environ.get("RACKTRACK_SOURCES_DB", ROOT / "data" / "sources.db"))
KEY_FILE = pathlib.Path(os.environ.get("RACKTRACK_CONN_KEY_FILE", ROOT / "data" / "connections.key"))
TYPES = {"netbox": "NetBox", "cmdb": "ServiceNow CMDB"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS connection_profiles (organization_id TEXT NOT NULL, type TEXT NOT NULL, address TEXT NOT NULL, secret_blob TEXT NOT NULL,
  state TEXT, state_words TEXT, tested_at TEXT, updated_by TEXT, updated_at TEXT, PRIMARY KEY (organization_id, type));
"""


def _db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; c.executescript(SCHEMA)
    return c


def _key():
    """The 32-byte key from the key file, made on first use with mode 600 (as secrets.js does)."""
    if KEY_FILE.exists():
        k = bytes.fromhex(KEY_FILE.read_text().strip())
        if len(k) != 32:
            raise ValueError(f"{KEY_FILE.name} must hold a 32-byte hex key.")
        return k
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    k = _rand.token_bytes(32)
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(k.hex())
    os.chmod(KEY_FILE, 0o600)
    return k


def seal(plain):
    iv = _rand.token_bytes(12)
    ct = AESGCM(_key()).encrypt(iv, plain.encode(), None)          # body followed by the 16-byte tag
    b = lambda x: base64.b64encode(x).decode()
    return ":".join(("v1", b(iv), b(ct[-16:]), b(ct[:-16])))


def unseal(sealed):
    """None when the blob cannot be opened (a new key file): the login then reads as not saved."""
    try:
        v, iv, tag, body = sealed.split(":")
        if v != "v1":
            return None
        d = base64.b64decode
        return AESGCM(_key()).decrypt(d(iv), d(body) + d(tag), None).decode()
    except Exception:
        return None


def _check(type_, address, secret):
    if type_ not in TYPES:
        raise ValueError("Choose NetBox or ServiceNow.")
    if not address.lower().startswith(("http://", "https://")):
        raise ValueError("The address must start with http:// or https://.")
    if type_ == "netbox" and not (secret.get("token") or (secret.get("username") and secret.get("password"))):
        raise ValueError("NetBox needs a token, or a username and password.")
    if type_ == "cmdb" and not (secret.get("username") and secret.get("password")):
        raise ValueError("ServiceNow needs a username and password.")


def save(org, type_, address, username="", password="", token="", by=None):
    """Save or update the organization's login for this type. Blank fields keep what was saved before."""
    address = (address or "").strip().rstrip("/")
    old = resolve(org, type_)
    secret = dict((old or {}).get("secret") or {})
    for k, v in (("username", username), ("password", password), ("token", token)):
        if (v or "").strip():
            secret[k] = v.strip()
    _check(type_, address, secret)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO connection_profiles VALUES (?,?,?,?,?,?,?,?,?)",
                  (org, type_, address, seal(json.dumps(secret)), "untested", "Saved, not tested yet.", None, by, now))
    return meta(org)


def remove(org, type_):
    with _db() as c:
        return c.execute("DELETE FROM connection_profiles WHERE organization_id=? AND type=?", (org, type_)).rowcount > 0


def meta(org):
    """What a screen may see: address, which fields are held (never their values), the state."""
    out = {}
    with _db() as c:
        rows = {r["type"]: r for r in c.execute("SELECT * FROM connection_profiles WHERE organization_id=?", (org,))}
    for t, label in TYPES.items():
        r = rows.get(t)
        if not r:
            out[t] = {"type": t, "label": label, "saved": False, "state": "mock", "state_words": "No login saved: the local test copy is used."}
            continue
        s = json.loads(unseal(r["secret_blob"]) or "{}")
        out[t] = {"type": t, "label": label, "saved": True, "address": r["address"], "username": bool(s.get("username")), "password": bool(s.get("password")),
                  "token": bool(s.get("token")), "state": r["state"], "state_words": r["state_words"], "tested_at": r["tested_at"], "updated_at": r["updated_at"],
                  "updated_by": r["updated_by"]}
    return out


def resolve(org, type_):
    """Server side only: the address and the opened secret, or None when no login is saved."""
    with _db() as c:
        r = c.execute("SELECT address, secret_blob FROM connection_profiles WHERE organization_id=? AND type=?", (org, type_)).fetchone()
    if not r:
        return None
    s = unseal(r["secret_blob"])
    return {"address": r["address"], "secret": json.loads(s) if s else {}}


def headers(type_, secret):
    """NetBox takes its token; ServiceNow basic auth."""
    if type_ == "netbox" and secret.get("token"):
        return {"Authorization": "Token " + secret["token"], "Accept": "application/json"}
    if secret.get("username"):
        raw = f"{secret['username']}:{secret.get('password', '')}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode(), "Accept": "application/json"}
    return {"Accept": "application/json"}


TEST_PATH = {"netbox": "/api/status/", "cmdb": "/api/now/table/cmdb_ci_rack?sysparm_limit=1"}


def test(org, type_, get):
    """Test connection: one read through `get(base, path, headers)`; the state is kept with the login."""
    login = resolve(org, type_)
    if not login:
        raise LookupError("Save a login first.")
    try:
        body = get(login["address"], TEST_PATH[type_], headers(type_, login["secret"]))
        if type_ == "netbox":
            words = f"Connected. NetBox {body.get('netbox-version', '')} answered.".replace("  ", " ")
        else:
            words = f"Connected. The Table API answered with {len(body.get('result') or [])} row."
        state = "ok"
    except Exception as e:                                  # a refused, wrong or foreign address says why, in words
        state, words = "failed", f"Not connected: {e}"
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with _db() as c:
        c.execute("UPDATE connection_profiles SET state=?, state_words=?, tested_at=? WHERE organization_id=? AND type=?", (state, words, now, org, type_))
    return meta(org)[type_]
