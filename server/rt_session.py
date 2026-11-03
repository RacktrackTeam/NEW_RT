"""rt_session.py - the engine trusts the RT-EOS sign-in service (server/auth/auth_service.js).

The service signs an access token (HS256, issuer "racktrack", audience "racktrack-app") and sets it as the rt_access
cookie; the native app sends it as "Authorization: Bearer". This module checks it exactly as RT-EOS's requireAuth does:
the same JWT_SECRET (from the environment, else from the service's settings file data/auth/.env), the algorithm pinned,
issuer and audience asserted, then the account read from the service's database (read only): not revoked, still active,
the same token_version, and an organization that the platform owner has approved. The answer is the principal:
user id, username, role, organization id and site id.

Who sees which run (RT-EOS lib/rack_access, the same rule):
  owner          every run (the platform owner)
  org_admin      every run of the organization
  site_manager   the runs of the site
  member         the runs of the site (a technician)
Anyone else is told the run is not there (404, never 403).
"""
import contextvars
import os
import pathlib
import re
import sqlite3
import sys

import jwt

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV_FILE = pathlib.Path(os.environ.get("RT_AUTH_ENV", ROOT / "data" / "auth" / ".env"))
ISSUER, AUDIENCE = "racktrack", "racktrack-app"
CURRENT = contextvars.ContextVar("rt_principal", default=None)     # the signed-in person of the request being served


def _settings():
    """KEY=VALUE lines of the service's settings file (the same format as RT-EOS's server/lib/load_env.js)."""
    out = {}
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                v = v.strip()
                if len(v) > 1 and v[0] in "\"'" and v.endswith(v[0]):
                    v = v[1:-1]
                if v:
                    out[k.strip()] = v
    except OSError:
        pass
    return out


def _setting(key):
    return os.environ.get(key) or _settings().get(key)


def auth_base():
    """Where the screens reach the sign-in service: RT_AUTH_URL, else the same host on the service's PORT."""
    return os.environ.get("RT_AUTH_URL") or None, int(_setting("PORT") or 3302)


def _account(uid):
    """The account row and its organization's status from the service's database (read only), or None."""
    path = _setting("RACKTRACK_AUTH_DB")
    if not path or not pathlib.Path(path).exists():
        return None
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    try:
        c.row_factory = sqlite3.Row
        u = c.execute("SELECT id, username, role, organization_id, tenant_id, active, token_version FROM users WHERE id = ?", (uid,)).fetchone()
        if u is None:
            return None
        org = c.execute("SELECT status, name FROM organizations WHERE id = ?", (u["organization_id"],)).fetchone() if u["organization_id"] else None
        site = c.execute("SELECT name FROM tenants WHERE id = ? AND organization_id IS NOT NULL", (u["tenant_id"],)).fetchone() if u["tenant_id"] else None
        u = dict(u, org_name=org["name"] if org else None, site_name=site["name"] if site else None)
        return u, (org["status"] if org else None), c
    except sqlite3.Error:
        c.close()
        return None


def verify(token):
    """The principal for an access token, or None (with the reason in 'blocked' when the organization waits)."""
    secret = _setting("JWT_SECRET")
    if not token or not secret:
        return None
    try:
        # RT-EOS puts the numeric user id in "sub"; PyJWT 2.10 would refuse a subject that is not a string
        p = jwt.decode(token, secret, algorithms=["HS256"], issuer=ISSUER, audience=AUDIENCE, options={"verify_sub": False, "require": ["exp", "sub"]})
    except jwt.PyJWTError:
        return None
    got = _account(p.get("sub"))
    if got is None:
        return None
    u, org_status, c = got
    try:
        if p.get("jti") and c.execute("SELECT 1 FROM revoked_tokens WHERE jti = ?", (str(p["jti"]),)).fetchone():
            return None
    except sqlite3.Error as ex:                     # fail closed: a token whose sign-out cannot be checked is not let through
        print(f"[racktrack] sign-out check failed, session refused: {ex}", file=sys.stderr)
        return None
    finally:
        c.close()
    if u["active"] == 0 or int(p.get("tv") or 0) != int(u["token_version"] or 0):
        return None
    out = {"user": u["id"], "username": u["username"], "role": u["role"] or "member",
           "org": u["organization_id"], "site": u["tenant_id"], "org_status": org_status, "org_name": u["org_name"], "site_name": u["site_name"]}
    if out["role"] != "owner" and out["org"] and org_status != "active":
        out["blocked"] = "Your organization is awaiting approval"
    return out


_NAMES = {"at": 0.0, "orgs": {}, "sites": {}}


def place_names():
    """({organization id: name}, {site id: name}) from the service's database (read only), kept for 30 seconds."""
    import time
    if time.time() - _NAMES["at"] < 30:
        return _NAMES["orgs"], _NAMES["sites"]
    path = _setting("RACKTRACK_AUTH_DB")
    orgs, sites = {}, {}
    if path and pathlib.Path(path).exists():
        try:
            c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            try:
                orgs = {str(r[0]): r[1] for r in c.execute("SELECT id, name FROM organizations")}
                sites = {str(r[0]): r[1] for r in c.execute("SELECT id, name FROM tenants WHERE organization_id IS NOT NULL")}
            finally:
                c.close()
        except sqlite3.Error as ex:
            print(f"[racktrack] organization and site names not read: {ex}")
    _NAMES.update(at=time.time(), orgs=orgs, sites=sites)
    return orgs, sites


def org_online_lookups(org):
    """The organization's online lookups setting (RT-EOS lib/org_settings.js, organizations.online_lookups), read only.
    Off when the setting or the organization cannot be read."""
    path = _setting("RACKTRACK_AUTH_DB")
    if not org or not path or not pathlib.Path(path).exists():
        return False
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            row = c.execute("SELECT online_lookups FROM organizations WHERE id = ?", (org,)).fetchone()
        finally:
            c.close()
    except sqlite3.Error:
        return False
    return bool(row and row[0] is not None and int(row[0]) == 1)


def from_request(request):
    """The rt_access cookie first (browsers), then the Bearer header (the native app), as requireAuth reads them."""
    bearer = re.match(r"^Bearer\s+(.+)$", request.headers.get("authorization") or "", re.I)
    return verify(request.cookies.get("rt_access") or (bearer.group(1) if bearer else None))


def current():
    return CURRENT.get()


def name(request=None):
    """The signed-in person's username, for the 'by' of an edit (was the name typed at the old sign-in)."""
    p = (getattr(request.state, "principal", None) if request is not None else None) or CURRENT.get()
    return (p or {}).get("username") or "user"


def org_key(p):
    """The folder and database name of the principal's organization ('platform' for the owner, who has none)."""
    return str(p["org"]) if p and p.get("org") else "platform"


def site_key(p):
    return str(p["site"]) if p and p.get("site") else "all"


def run_dir(runs_root, job, p=None):
    """data/runs/<org>/<site>/<run> for a new run of this principal."""
    p = p or CURRENT.get()
    return pathlib.Path(runs_root) / org_key(p) / site_key(p) / job


def can_see(p, run):
    """May this principal open this run (or anything else carrying "org" and "site", such as a camera-guide take)?"""
    if p is None:
        return False
    if p["role"] == "owner":
        return True
    if str(run.get("id") or "").startswith("example-"):     # the stored demo is the owner's office rack: the platform owner's only (rule 11)
        return False
    if run.get("org") is None or str(run.get("org")) != str(p.get("org")):
        return False
    if p["role"] in ("org_admin", "org_owner"):
        return True
    return run.get("site") is not None and str(run.get("site")) == str(p.get("site"))
