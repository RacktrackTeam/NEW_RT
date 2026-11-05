"""RackTrack engine: the API behind the app (web/app/) and the video runs, a private service behind the sign-in
service (./auth.sh, server/auth/auth_service.js).

    ./engine.sh                                # http://127.0.0.1:8700 (RACKTRACK_PORT to change it)

What it does
  sign-in        none of its own: every route but the open ones needs the RT-EOS session (server/rt_session.py), and the
                 writes check the role (ROLE_RULES, docs/AUTH.md)
  sweeps         a rack video (an upload or a camera-guide take) runs the 15 steps in a background thread;
                 every run lives in data/runs/<org>/<site>/<id>/ (video, rack picture, result.json, report/) and survives a restart
  close-ups      a close-up of one device is read and kept with its run; the report is rebuilt
  databases      the organization's Excel, NetBox and ServiceNow CMDB copies, the rack match and the drift (engine/source_db.py)
  reports        each run's 15-step report (engine/video_report.py), served uncached
Everything heavy runs one job at a time on 2 threads (this Mac overheats otherwise); the engine is engine/.
"""
import datetime
import hashlib

import cv2
import io
import json
import os
import pathlib
import re
import secrets
import shutil
import socket
import sys
import tempfile
import threading
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
import local_models  # noqa: E402,F401  (first: model caches inside this folder, hub lookups off)
os.environ.setdefault("OMP_NUM_THREADS", "2")

import huggingface_hub  # noqa: E402,F401  (import order: before torch-based modules)
import open_clip  # noqa: E402,F401
import timm  # noqa: E402,F401
import uvicorn  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402
from PIL import Image, ImageOps, UnidentifiedImageError  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

import captures as CAP  # noqa: E402
import occupancy as occ  # noqa: E402
import pipeline as pipe  # noqa: E402
import rack_stitch  # noqa: E402
import rack_video  # noqa: E402
import rack_label  # noqa: E402
import patch_panels  # noqa: E402
import video_report  # noqa: E402
import source_db as SDB  # noqa: E402
import connections as CONN  # noqa: E402
import rt_session  # noqa: E402  (racktrack-app sign-in: the RT-EOS session)

# ------------------------------------------------------------------ settings
PORT = int(os.environ.get("RACKTRACK_PORT", "8700"))          # never 8600, the live app's port
HOST = os.environ.get("RACKTRACK_HOST", "127.0.0.1")           # this machine only: the sign-in service is the front door
MOBILE = ROOT / "web" / "app"
DATA = pathlib.Path(os.environ.get("RACKTRACK_DATA") or ROOT / "data")   # runtime data (runs, copies, checks, uploads); a test engine sets its own
RUNS = pathlib.Path(os.environ.get("RACKTRACK_RUNS") or DATA / "runs"); RUNS.mkdir(parents=True, exist_ok=True)
MAX_PHOTO = 25 * 1024 * 1024
MAX_VIDEO = 600 * 1024 * 1024
DISPLAY_MAX = 2000
NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0"}

class ModelLock:
    """One model call at a time in this server, and on the whole machine: the same lock directory tools/with-model-lock.sh uses
    (RT_MODEL_LOCK_DIR, default /tmp/one_racktrack_model.lock), taken only around a model run, so an idle server never holds it."""
    def __init__(self):
        self._t = threading.Lock()
        self._dir = pathlib.Path(os.environ.get("RT_MODEL_LOCK_DIR", "/tmp/one_racktrack_model.lock"))

    def locked(self):
        return self._t.locked()

    def __enter__(self):
        self._t.acquire()
        try:
            while True:
                try:
                    self._dir.mkdir()
                    (self._dir / "pid").write_text(str(os.getpid()))
                    return self
                except FileExistsError:
                    try:
                        holder = int((self._dir / "pid").read_text().strip() or 0)
                        os.kill(holder, 0)
                    except (OSError, ValueError):            # the holder is gone: take the lock over, as the shell script does
                        shutil.rmtree(self._dir, ignore_errors=True); continue
                    time.sleep(2)
        except BaseException:
            self._t.release(); raise

    def __exit__(self, *exc):
        shutil.rmtree(self._dir, ignore_errors=True)
        self._t.release()
        return False


MODEL_LOCK = ModelLock()              # one model call at a time, here and on the machine
STATE_LOCK = threading.Lock()
_RUN_LOCKS, _RUN_LOCKS_GUARD = {}, threading.Lock()


def run_lock(job):
    """One lock per run around every read-change-write of its result.json (verify pass 2, finding 19): a person's edit and a
    background read never overwrite each other. A device read holds it only to merge its own answer, not while it reads."""
    with _RUN_LOCKS_GUARD:
        return _RUN_LOCKS.setdefault(job, threading.RLock())


def _reading(j):
    """A device read or a read again is going on this run."""
    return any(v == "running" for v in (j.get("ports") or {}).values()) or (j.get("read_again") or {}).get("state") == "running"


def edits_run(fn):
    """A route that changes a run's result.json: it runs holding the run's lock."""
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        with run_lock(kw.get("job") or (a[0] if a else "")):
            return fn(*a, **kw)
    return wrapper
app = FastAPI(title="RackTrack", docs_url="/api/docs", redoc_url=None)
APPROACHES = {a.key: a for a in occ.build_approaches("v2")}
ENSEMBLE = APPROACHES["ensemble"]


def keep_only(chosen):
    """Unload every occupancy approach except the one in use (and the Ensemble's members)."""
    keep = {id(chosen)} | {id(m) for m in getattr(chosen, "members", [])}
    freed = False
    for x in APPROACHES.values():
        unload = getattr(x, "unload", None)
        if id(x) not in keep and unload is not None and x.available():
            unload(); freed = True
    if freed:
        occ.free_memory()


# ------------------------------------------------------------------ sign-in: the RT-EOS session (server/rt_session.py)
# racktrack-app sign-in (auth merge): the name-only /api/login is gone. Every /api route except health needs the session
# the sign-in service (auth.sh, server/auth/auth_service.js) signs; a run of another organization or site is not there.
OPEN_PATHS = ("/", "/app", "/app/", "/manifest.webmanifest", "/sw.js", "/api/health", "/api/auth-service", "/favicon.ico", "/logo.jpg", "/api/contact",
              "/api/auth/state")
RUN_PATH = re.compile(r"^/(?:api/video|video-report)/([A-Za-z0-9_-]+)(?:/|$)")
# Who may write what (docs/AUTH.md, the five roles). A role outside the list gets 403 with the reason in words; the platform-only
# pages answer 404 to everyone but the platform owner, as if they were not there (rule 11). Every other route is open to anyone
# signed in, and a run's own routes are then checked by _run_guard (organization and site).
ADMINS = frozenset({"owner", "org_owner", "org_admin"})
ROLE_RULES = (
    (("POST", "DELETE"), re.compile(r"^/api/connections/[a-z]+(?:/test)?/?$"), ADMINS, 403, "Only an organization admin can change the organization's logins."),
    (("POST",), re.compile(r"^/api/sources/(?:excel|excel/import|[a-z]+/import)/?$"), ADMINS, 403, "Only an organization admin can copy a database in."),
    (("DELETE",), re.compile(r"^/api/sources/copies/[^/]+/?$"), ADMINS, 403, "Only an organization admin can remove a database copy."),
    (("DELETE",), re.compile(r"^/api/video/[A-Za-z0-9_-]+/?$"), ADMINS | {"site_manager"}, 403, "Only a site manager or an organization admin can remove a scan."),
)


def role_refusal(p, method, path):
    """(status, words) when this person's role may not make this call, else None."""
    for methods, pat, roles, code, words in ROLE_RULES:
        if (methods is None or method in methods) and pat.match(path) and (p or {}).get("role") not in roles:
            return code, words
    return None


def _run_guard(p, path):
    """A path naming a run another organization or site owns answers 404, as if the run were not there."""
    m = RUN_PATH.match(path)
    if not m or m.group(1) == "history":
        return None
    j = JOBS.get(m.group(1))
    if j is not None and not rt_session.can_see(p, j):
        return JSONResponse({"error": "No such run."}, status_code=404)
    if j is not None and j.get("org") is not None:
        SDB.ORG.set(str(j["org"]))                       # the run is compared with its own organization's copies
    return None


@app.middleware("http")
async def require_session(request: Request, call_next):
    p = request.url.path
    if p in OPEN_PATHS or p.startswith("/app/") or p.startswith("/icons/"):
        resp = await call_next(request)
        if p in ("/app", "/app/") or p.endswith((".html", ".js", ".css")):
            resp.headers["Cache-Control"] = "no-cache"     # the phone app re-checks the screens every time, so a change shows at once
        return resp
    who = rt_session.from_request(request)
    if who is None or who.get("blocked"):
        if p.startswith("/api/") or p.startswith("/capture/upload"):
            return JSONResponse({"error": (who or {}).get("blocked") or "sign in first", "org_status": (who or {}).get("org_status")},
                                status_code=403 if who else 401)
        return RedirectResponse("/app/#signin")
    if who.get("role") == "owner" and not who.get("org") and (p.startswith("/api/sources") or p.startswith("/api/connections")):
        rj = JOBS.get(request.query_params.get("run") or "")      # the platform owner has no organization: for the compare, copies and logins they act in the run's own
        if rj is not None and rj.get("org") is not None:
            who = dict(who, org=rj.get("org"), site=rj.get("site"), acting_in_run=rj.get("id"))
    request.state.principal = who
    rt_session.CURRENT.set(who)
    SDB.ORG.set(rt_session.org_key(who))                   # each organization has its own database copies
    blocked = _run_guard(who, p)                           # a stranger's run is not there (404) before any role is weighed
    if blocked is not None:
        return blocked
    refused = role_refusal(who, request.method, p)
    if refused is not None:
        return JSONResponse({"error": refused[1]}, status_code=refused[0])
    return await call_next(request)


class Contact(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    organization: str = Field(default="", max_length=120)
    message: str = Field(min_length=1, max_length=2000)


CONTACT_SEEN = {}                          # sender (address and email) -> the times of their messages in the last minute


@app.post("/api/contact")
def api_contact(body: Contact, request: Request):
    """Contact us from the sign-in screen (public): kept as a JSON file in data/contact for RackTrack to answer. Nothing is sent
    anywhere. Each sender may send 3 messages a minute, and all senders together 60, so one sender cannot block the others and
    the open route cannot fill the disk."""
    now = time.time()
    for k in list(CONTACT_SEEN):
        CONTACT_SEEN[k] = [t for t in CONTACT_SEEN[k] if now - t < 60]
        if not CONTACT_SEEN[k]:
            del CONTACT_SEEN[k]
    who = f"{request.client.host if request.client else '-'}|{body.email.lower()}"
    if len(CONTACT_SEEN.get(who, [])) >= 3 or sum(len(v) for v in CONTACT_SEEN.values()) >= 60:
        raise HTTPException(429, "Too many messages just now. Try again in a minute.")
    CONTACT_SEEN.setdefault(who, []).append(now)
    d = DATA / "contact"; d.mkdir(parents=True, exist_ok=True)
    (d / f"{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}.json").write_text(json.dumps(body.model_dump(), indent=1))
    return {"ok": True}


@app.get("/api/auth-service")
def api_auth_service():
    """Where the screens reach the sign-in service (open: the sign-in screen needs it before anyone is signed in)."""
    url, port = rt_session.auth_base()
    return {"url": url, "port": port}


@app.get("/api/auth/state")
def api_auth_state(request: Request):
    """Public: whether this browser holds a valid session, or only the sign-in service's renewal cookie (sent here too: the
    cookie belongs to the host and its path is /api/auth). The app asks this first, so a signed-out browser opens the
    sign-in screen without a refused call in its console (verify pass 2, finding 30). It says nothing about who."""
    who = rt_session.from_request(request)
    return {"signed_in": bool(who and not who.get("blocked")), "waiting": bool(who and who.get("blocked")),
            "can_renew": bool(request.cookies.get("rt_refresh"))}


@app.get("/api/me")
def api_me(request: Request):
    p = request.state.principal
    return {"name": p["username"], "user": p["user"], "role": p["role"], "organization": p["org"], "site": p["site"],
            "org_name": p.get("org_name"), "site_name": p.get("site_name") or p.get("org_name"), "host": socket.gethostname()}


@app.get("/api/health")
def api_health():
    return {"ok": True, "runs": len(JOBS), "busy": MODEL_LOCK.locked(), "time": datetime.datetime.now().isoformat(timespec="seconds")}


# ------------------------------------------------------------------ runs: every job in data/runs/<id>/, status.json kept current
JOBS = {}                                   # id -> {"status", "progress", "result", "dir", "error", "started", "title", "name", ...}


def _save(j):
    """status.json beside the run: what the history and the app need, never the big result."""
    keep = {k: j.get(k) for k in ("id", "status", "progress", "error", "started", "finished", "title", "name", "from_guide", "stride", "summary", "phase", "partial", "ports", "ports_error", "video_file", "rack_id", "video_md5", "video_probe", "video_fp", "stage", "db", "position", "org", "site")}
    try:
        (pathlib.Path(j["dir"]) / "status.json").write_text(json.dumps(keep, indent=1))
    except OSError as ex:
        print(f"[racktrack] status of run {j.get('id')} not saved: {ex}", file=sys.stderr)


def _summary(res):
    """The few numbers the history shows for a finished run."""
    try:
        s = res["occupancy_frames"]["summary"]; devs = res.get("devices") or []
        kinds = sum(1 for d in devs if SDB.device_kind(d)["kind"] not in (None, "Other"))       # the one kind every screen shows
        # devices whose ports were read (a template fitted, the agent's auto mode, the video fallback or a close-up), as the
        # device pages show them (verify pass 2, finding 36: "5 port layouts" while all ten pages list ports)
        fitted = sum(1 for d in devs if (d.get("layout") or {}).get("ports") or (d.get("closeup") or {}).get("ports"))
        return {"units": res["ladder"]["units"], "occupied": s["occupied"], "free": s["free"], "uncertain": s["uncertain"],
                "devices": len(devs), "named": kinds, "layouts": fitted, "warnings": len((res.get("stitch") or {}).get("warnings") or [])}
    except Exception as ex:  # noqa: BLE001  a result without its summary still opens; the reason goes to the log
        print(f"[racktrack] run summary not made: {type(ex).__name__}: {ex}", file=sys.stderr)
        return None


def run_status_files():
    """racktrack-app sign-in: runs live in data/runs/<org>/<site>/<run>; a run from before sign-in (data/runs/<run>) has no
    organization and only the platform owner sees it."""
    if not RUNS.exists():
        return []
    return sorted(RUNS.glob("*/status.json")) + sorted(RUNS.glob("*/*/*/status.json"))


def load_runs():
    """Runs left by earlier starts: finished ones come back into the history; one cut off by a restart is marked failed."""
    for f in run_status_files():
        d = f.parent
        try:
            j = json.loads(f.read_text())
        except ValueError as ex:
            print(f"[racktrack] run at {f.parent} left out: its status.json does not read ({ex})", file=sys.stderr)
            continue
        vf = pathlib.Path(j["video_file"]) if j.get("video_file") else None
        if vf is not None and ROOT not in vf.resolve().parents:            # a path into another folder (a run copied in): its file in this run folder instead
            vf = d / vf.name
        vp = vf if vf is not None and vf.exists() else None
        j.update(dir=d, result=None, rack_path=d / "rack.png", video_path=vp or next((p for p in d.glob("video*") if p.suffix in (".mp4", ".mov", ".webm", ".avi")), None))
        if j.get("status") == "running":
            j.update(status="failed", error="The server was restarted while this run was going.", progress="failed"); _save(j)
        if j.get("ports"):                                      # a device read that was going when the server stopped never finished
            j["ports"] = {k: v for k, v in j["ports"].items() if v != "running"}
        if (d / "result.json").exists() and j.get("status") == "done":
            try:
                j["result"] = json.loads((d / "result.json").read_text())
                j["summary"] = _summary(j["result"])                          # from the result itself, so an edit after the run counts
            except ValueError as ex:
                print(f"[racktrack] run {j.get('id')}: result.json does not read ({ex}); it shows without its result", file=sys.stderr)
        JOBS[j["id"]] = j


def job_or_none(job):
    """A run the signed-in person may open, or None."""
    j, me = JOBS.get(job), rt_session.current()          # a background thread has no principal and sees every run
    return j if j is None or me is None or rt_session.can_see(me, j) else None     # a stranger's run is not there


def report_dir(j):
    return pathlib.Path(j["report"]) if j.get("report") else pathlib.Path(j["dir"]) / "report"


# The run page's stage (1 to 15, the STEPS of web/app/app.js) from the engine's progress sentence, more specific words first.
# The page used to guess the stage from the words itself and six sentences sent it back a step (testers, 4 Nov 2026);
# now the server numbers it and the number never goes down within a run.
STAGE_WORDS = (("done", 15), ("building the report", 14), ("ports: failed", 13), ("ports:", 11), ("devices:", 8), ("picture levelled", 8),
               ("units:", 8), ("classifying", 8), ("drawing", 7), ("best frames", 7), ("rack image: devices", 7), ("rack image: occupancy", 6),
               ("rack image: units", 5), ("rack image", 4), ("reading text", 6), ("fusing", 6), ("frame ", 5), ("running the photo pipeline", 5),
               ("registered frame", 4), ("registering", 4), ("reading the video", 4), ("stitch", 3), ("checking which way", 1), ("queued", 1))


def stage_of(msg):
    """The stage a progress sentence belongs to, or None for a sentence that says nothing about the stage."""
    m = str(msg or "").lower()
    return next((n for k, n in STAGE_WORDS if k in m), None)


def run_job(job, video_path, truth_path=None):
    import cv2
    j = JOBS[job]; d = pathlib.Path(j["dir"]); rack_path = d / "rack.png"
    j["rack_path"], j["video_path"] = rack_path, video_path
    j["stage"] = 0
    def say(m):
        j["progress"] = m; j["stage"] = max(j.get("stage") or 0, stage_of(m) or 0); _save(j)
    try:
        say("checking which way the video runs")
        video_path, turned = rack_stitch.upright(video_path, d, from_guide=j.get("from_guide", False)); j["video_path"] = video_path; j["video_file"] = str(video_path); _save(j)
        say("stitching the video into one rack picture")
        try:
            j["stitch"] = dict(rack_stitch.stitch(video_path, rack_path, progress=say), upright=turned)
        except rack_stitch.NotReady as ex:
            raise ValueError(f"the video could not be stitched into one rack picture: {ex}") from ex
        rack = cv2.imread(str(rack_path))
        if rack is None:
            raise ValueError("the stitched rack picture could not be read")
        with MODEL_LOCK:
            keep_only(ENSEMBLE)
            # units, occupancy, devices and kinds; the ports of a device are read when the user selects it (api_read_ports)
            res = rack_video.run(rack, video_path, ENSEMBLE, d, progress=say, stride=j.get("stride") or rack_video.STRIDE,
                                 truth_csv=truth_path if truth_path and pathlib.Path(truth_path).exists() else None, stitch=j.get("stitch"), layouts=False)
            say("building the report")
            video_report.build_from(d, d / "report", rack_path, video_path, j.get("title") or "Your rack", j.get("name") or job)
        j.update(result=res, status="done", progress="done", phase="done", partial=False, finished=datetime.datetime.now().isoformat(timespec="seconds"), summary=_summary(res))
    except Exception as ex:  # noqa: BLE001
        msg = str(ex)
        print(f"[racktrack] run {job} failed: {type(ex).__name__}: {msg}", file=sys.stderr)      # the technical detail goes to the log, not the screen
        traceback.print_exc(file=sys.stderr)        # where it failed, so a fault on our side can be found from the log
        if "no frame gave a ladder" in msg:
            msg = "No rack was found in this video: no frame showed rack units. Film the front of the rack from top to bottom, with the rack filling the picture."
        elif msg.startswith("the video could not be stitched"):
            msg = "The video could not be joined into one rack picture. Film the rack slowly from top to bottom, with the whole width in view."
        elif msg == "the stitched rack picture could not be read":
            msg = "The joined rack picture could not be opened. Try again; if it stops again, film the rack again slowly from top to bottom."
        else:                                       # a fault in the engine, not in the video: say so, the detail is in the log
            msg = "The reading stopped because of a fault on our side, not because of your video. Try again; if it stops again, tell your admin."
        # failed_at keeps the step the run was on, so the run screen keeps the ticks of the steps that finished
        j.update(status="failed", error=msg, failed_at=j.get("progress"), progress="failed", finished=datetime.datetime.now().isoformat(timespec="seconds"))
    _save(j)
    if j.get("status") == "done":
        start_rack_label(job)                       # the rack's own label (ID and name), read after the run in the background
        start_ports_all(job)                        # then the ports of every port-bearing device, one at a time


# ------------------------------------------------------------------ the rack's label: its ID and name, read once per run (engine/rack_label.py)
RACK_LABEL_RUNNING = set()


def rack_label_of(j):
    f = pathlib.Path(j["dir"]) / "rack_label.json"
    return json.loads(f.read_text()) if f.exists() else None


def _rack_label_thread(job):
    try:
        j = job_or_none(job)
        with MODEL_LOCK:                            # one model at a time: OCR waits for a sweep that is running
            out = rack_label.read_rack_label(j["dir"])
        out.pop("reads", None)
        (pathlib.Path(j["dir"]) / "rack_label.json").write_text(json.dumps(out, indent=1))
    except Exception as ex:  # noqa: BLE001
        print(f"[racktrack] rack label for {job} failed: {type(ex).__name__}: {ex}")
    finally:
        RACK_LABEL_RUNNING.discard(job)


def start_rack_label(job):
    j = job_or_none(job)
    if j is None or job in RACK_LABEL_RUNNING or rack_label_of(j) is not None:
        return
    RACK_LABEL_RUNNING.add(job)
    threading.Thread(target=_rack_label_thread, args=(job,), daemon=True).start()


# ------------------------------------------------------------------ the rack ID the user confirms (the key for the network layer, 29 Oct 2026)
# The user is always asked: the ID read off the label (if any) is offered first to verify, then the IDs of the other racks
# scanned here, the next IDs in a known sequence, or a typed ID. The answer is kept in status.json as "rack_id".
def rack_id_options(j):
    """[{"id", "why"}]: the label read on this run, the IDs confirmed or read on the other runs, and the next in sequence."""
    opts, seen = [], set()
    def add(i, why):
        i = (i or "").strip()
        if i and i.upper() not in seen:
            seen.add(i.upper()); opts.append({"id": i, "why": why})
    RL = rack_label_of(j) or {}
    add(((RL.get("id") or {}).get("text")), "read off the label in this video")
    me = rt_session.current()
    # "here" is the run's own organization and site, and only runs the signed-in person may open: one organization never
    # sees another's rack ids (rule 11)
    here = [o for o in JOBS.values() if o is not j and str(o.get("org")) == str(j.get("org"))
            and str(o.get("site")) == str(j.get("site")) and (me is None or rt_session.can_see(me, o))]
    for o in sorted(here, key=lambda x: x.get("started") or "", reverse=True):
        rid = (o.get("rack_id") or {}).get("id")
        if rid:
            add(rid, "confirmed on another rack scanned here")
    for o in here:
        ol = rack_label_of(o) or {}
        add(((ol.get("id") or {}).get("text")), "read on another rack scanned here")
    for o in list(opts):                                                  # the next IDs in a sequence: ...-R1 -> ...-R2, ...-R3
        m = re.match(r"^(.*?)(\d+)$", o["id"])
        if m:
            for k in (1, 2):
                add(f"{m.group(1)}{int(m.group(2)) + k:0{len(m.group(2))}d}", "next in the sequence")
    return opts[:8]


@app.post("/api/video/{job}/rack-id")
def api_rack_id(job: str, body: dict = Body(...)):
    """Save the rack ID the user confirmed ({"id": ...}; {"skip": true} = asked, left for later; {"clear": true} = ask again)."""
    j = JOBS.get(job)
    if j is None:
        raise HTTPException(404, "No such run.")
    if body.get("clear"):
        j["rack_id"] = None
    elif body.get("skip"):
        j["rack_id"] = {"id": None, "skipped": True, "on": datetime.datetime.now().isoformat(timespec="minutes")}
    else:
        rid = str(body.get("id") or "").strip()
        if not rid:
            raise HTTPException(400, "Give the rack ID.")
        RL = rack_label_of(j) or {}; read = ((RL.get("id") or {}).get("text") or "").strip()
        j["rack_id"] = {"id": rid, "source": "verified from the label" if read and read.upper() == rid.upper() else ("typed" if body.get("typed") else "chosen"),
                        "on": datetime.datetime.now().isoformat(timespec="minutes")}
    _save(j)
    return {"rack_id": j.get("rack_id")}


@app.post("/api/video/{job}/rack-label")
def api_rack_label(job: str):
    """Read the rack's label for a finished run, in the background (once; the view carries the answer)."""
    j = job_or_none(job)
    if j is None:
        raise HTTPException(404, "No such run.")
    start_rack_label(job)
    return {"state": "done" if rack_label_of(j) else "running"}


def _when_words(t=None):
    """The one date style every screen uses (web/app/app.js when()): 3 Nov 2026, 09:41."""
    t = t or datetime.datetime.now()
    return f"{t.day} {t.strftime('%b %Y, %H:%M')}"


def public_job(j, with_result=False):
    out = {k: j.get(k) for k in ("id", "status", "progress", "stage", "failed_at", "error", "started", "finished", "title", "name", "from_guide", "summary", "phase", "partial", "ports")}
    out["report"] = f"/video-report/{j['id']}/index.html" if (j.get("status") == "done" or j.get("partial")) else None
    try:                                                   # the pictures made so far, so the run screen can show each step as it lands
        out["files"] = sorted(f.name for f in pathlib.Path(j["dir"]).iterdir() if f.suffix in (".jpg", ".png") and not f.name.startswith("closeup"))
    except OSError:
        out["files"] = []
    if with_result and j.get("result") is not None:
        r = j["result"]; st = r.get("stitch") or {}
        out["stitch"] = {k: st.get(k) for k in ("image_size", "keyframes_used", "seconds", "warnings", "upright")}
        out["devices"] = [{"index": d["index"], "top": d["top"], "bottom": d["bottom"], "height": d["height"],
                           "kind": SDB.device_kind(d)["kind"], "layout": bool((d.get("layout") or {}).get("fitted") or (d.get("closeup") or {}).get("fitted"))}
                          for d in r.get("devices") or []]
    return out


def _video_md5(j):
    """The checksum of a run's uploaded video, computed once and kept in status.json."""
    if j.get("video_md5"):
        return j["video_md5"]
    f = pathlib.Path(j["dir"]) / "video.mp4"
    if not f.exists():
        f = next(iter(pathlib.Path(j["dir"]).glob("video.*")), None)
    if f is None or not f.exists():
        return None
    h = hashlib.md5()
    with open(f, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    j["video_md5"] = h.hexdigest(); _save(j)
    return j["video_md5"]


def _video_probe(path):
    """(frames, width, height) of a video, or None."""
    try:
        c = cv2.VideoCapture(str(path)); n = int(c.get(cv2.CAP_PROP_FRAME_COUNT)); w = int(c.get(cv2.CAP_PROP_FRAME_WIDTH)); h = int(c.get(cv2.CAP_PROP_FRAME_HEIGHT)); c.release()
        return [n, w, h] if n > 0 and w > 0 else None
    except Exception:
        return None


def _video_fingerprint(path):
    """Size, duration and the look of three frames (10, 50 and 90 % of the way): a phone re-encodes a video it hands over
    (another frame rate, another byte count) but keeps these (testers, 4 Nov 2026: the same video read again from the start)."""
    try:
        c = cv2.VideoCapture(str(path)); n = int(c.get(cv2.CAP_PROP_FRAME_COUNT)); fps = float(c.get(cv2.CAP_PROP_FPS) or 0)
        w = int(c.get(cv2.CAP_PROP_FRAME_WIDTH)); h = int(c.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if n <= 0 or w <= 0 or fps <= 0:
            c.release(); return None
        hashes = []
        for q in (0.1, 0.5, 0.9):
            c.set(cv2.CAP_PROP_POS_FRAMES, int(n * q)); ok, fr = c.read()
            if not ok:
                c.release(); return None
            g = cv2.resize(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), (8, 8), interpolation=cv2.INTER_AREA).astype(float)
            hashes.append("".join("1" if v > g.mean() else "0" for v in g.flatten()))
        c.release()
        return {"w": w, "h": h, "dur": round(n / fps, 2), "hashes": hashes}
    except Exception:  # noqa: BLE001  an unreadable video simply has no fingerprint
        return None


def _run_fingerprint(j):
    if j.get("video_fp"):
        return j["video_fp"]
    f = pathlib.Path(j["dir"]) / "video.mp4"
    if not f.exists():
        f = next(iter(pathlib.Path(j["dir"]).glob("video.*")), None)
    fp = _video_fingerprint(f) if f else None
    if fp:
        j["video_fp"] = fp; _save(j)
    return fp


def _same_look(a, b):
    """The same footage: same size, duration within 0.3 s, and each of the three sampled frames within 10 bits of 64."""
    return bool(a and b and a["w"] == b["w"] and a["h"] == b["h"] and abs(a["dur"] - b["dur"]) <= 0.3
                and all(sum(x != y for x, y in zip(p, q)) <= 10 for p, q in zip(a["hashes"], b["hashes"])))


def _run_probe(j):
    if j.get("video_probe"):
        return j["video_probe"]
    f = pathlib.Path(j["dir"]) / "video.mp4"
    if not f.exists():
        f = next(iter(pathlib.Path(j["dir"]).glob("video.*")), None)
    pr = _video_probe(f) if f else None
    if pr:
        j["video_probe"] = pr; _save(j)
    return pr


def _same_video_run(path):
    """The id of a finished run made from this video: the same bytes, or a re-export of it (the phone's photo library
    re-encodes a video it hands over) with the same frame count and size. Else None."""
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    want = h.hexdigest()
    done = [o for o in sorted(JOBS.values(), key=lambda x: x.get("started") or "", reverse=True) if o.get("status") == "done" and o.get("result") is not None
            and rt_session.can_see(rt_session.current(), o)]                       # only the runs this person may open
    for o in done:
        if _video_md5(o) == want:
            return o["id"]
    pr = _video_probe(path)
    if pr:
        for o in done:
            if _run_probe(o) == pr:
                return o["id"]
    fp = _video_fingerprint(path)
    if fp:
        for o in done:
            if _same_look(_run_fingerprint(o), fp):
                return o["id"]
    return None


@app.post("/api/video")
async def api_video(request: Request, video: UploadFile | None = File(None), capture: str | None = Form(None),
                    stride: int = Form(rack_video.STRIDE), lat: float | None = Form(None), lng: float | None = Form(None),
                    accuracy: float | None = Form(None)):   # wt/features: the phone's accuracy in metres (location.js)
    """Start a sweep: a video upload or a take from the camera guide (capture = "<source>/<folder>")."""
    with STATE_LOCK:                                   # one run at a time, stitching included; a device's port read holds the model lock, and that is no
        running = [v for v in JOBS.values() if v.get("status") == "running" and not v.get("partial")]   # reason to refuse: the run waits for the lock itself (testers, 4 Nov 2026)
        busy = bool(running) or RUN_STARTING["n"]
        if not busy:
            RUN_STARTING["n"] = 1
    if busy:
        r = running[0] if running else None
        raise HTTPException(409, f"Another rack is being read, at step {r.get('stage') or 1} of 15. Yours can start when it is done; one runs at a time on this server." if r
                            else "Another video is just arriving. Try again in a moment; one runs at a time on this server.")
    try:
        return await _start_video(request, video, capture, stride, lat, lng, accuracy)
    finally:
        RUN_STARTING["n"] = 0


VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".3gp"}   # an upload that is none of these, and not sent as video/*, is refused
RUN_STARTING = {"n": 0}                    # a run being set up (its upload still arriving), so a second one waits


async def _start_video(request, video, capture, stride, lat, lng, accuracy):
    who = rt_session.name(request)
    job = hashlib.sha1(f"{datetime.datetime.now().isoformat()}{who}{secrets.token_hex(4)}".encode()).hexdigest()[:12]
    me = rt_session.current()
    d = rt_session.run_dir(RUNS, job, me); d.mkdir(parents=True, exist_ok=True)      # data/runs/<org>/<site>/<run>
    truth_path, title, name, from_guide = None, None, None, False
    if capture:
        src, _, folder = capture.partition("/")
        td = CAP.take_dir(src, folder); t = CAP.read_take(src, td) if td else None
        if t is None or not t["video"] or not _take_visible(td):
            shutil.rmtree(d, ignore_errors=True)
            raise HTTPException(404, "That take is not there, or its video has not arrived yet.")
        video_path = d / t["video"]; shutil.copy(td / t["video"], video_path)
        if (td / "log.json").is_file():
            shutil.copy(td / "log.json", d / "capture_log.json")
        pt = CAP.public(t); title, name, from_guide = f'{pt["tester"]}\'s rack' + (f', {pt["when"]}' if pt["when"] else ""), "take", True
        if lat is None and (td / "position.json").is_file():          # wt/features: the position the camera guide sent with the take
            try:
                q = json.loads((td / "position.json").read_text()); lat, lng, accuracy = q.get("lat"), q.get("lng"), q.get("accuracy")
            except ValueError as ex:
                print(f"[racktrack] capture position not read ({ex}); the run has no position", file=sys.stderr)
    elif video is not None and video.filename:
        if not (str(video.content_type or "").startswith("video/") or pathlib.Path(video.filename).suffix.lower() in VIDEO_SUFFIXES):
            shutil.rmtree(d, ignore_errors=True)
            raise HTTPException(400, f"{pathlib.Path(video.filename).name} is not a video. Send an MP4 or MOV video of the rack.")
        video_path = d / ("video" + (pathlib.Path(video.filename).suffix.lower() or ".mp4"))
        video_path.write_bytes(await video.read(MAX_VIDEO + 1))
        same = _same_video_run(video_path)                 # a video read before opens its result at once (user, 2026-10-30)
        if same:
            shutil.rmtree(d, ignore_errors=True)
            _features_keep_position(same, lat, lng, accuracy)            # wt/features: a position sent with a video read before is kept on it
            return {"job": same, "reused": True}
        title, name = f"{who}'s rack, {_when_words()}", "upload"
    else:
        shutil.rmtree(d, ignore_errors=True)                # a refused start leaves no empty run folder
        raise HTTPException(400, "Send a rack video or a take.")
    j = {"id": job, "status": "running", "progress": "queued", "result": None, "dir": d, "error": "", "started": datetime.datetime.now().isoformat(timespec="seconds"),
         "title": title, "name": name, "from_guide": from_guide, "stride": max(1, min(30, int(stride))), "by": who,
         "position": _features_position(lat, lng, accuracy),   # the phone's position when the scan started (optional); it only narrows the racks
         "org": me.get("org"), "site": me.get("site")}
    with STATE_LOCK:
        JOBS[job] = j
    _save(j)
    threading.Thread(target=run_job, args=(job, video_path, truth_path), daemon=True).start()
    return {"job": job}


@app.get("/api/video/history")
def api_video_history():
    """Every run on this server, newest first."""
    orgs, sites = rt_session.place_names()
    rows = [dict(public_job(j), **_rack_state(j), org=j.get("org"), site=j.get("site"), org_name=orgs.get(str(j.get("org"))), site_name=sites.get(str(j.get("site"))))
            for j in JOBS.values() if rt_session.can_see(rt_session.current(), j)]       # a rack is one rack within its organization and site
    rows.sort(key=lambda x: x["started"] or "", reverse=True)
    return rows


def _rack_state(j):
    """What Home shows for a run: the rack id, the record it was compared with and how many differences (only once a person compared)."""
    out = {"rack_id": (j.get("rack_id") or {}).get("id"), "record": None, "differences": None}
    if j.get("result") is not None and (j.get("db") or {}).get("source"):
        try:
            dv = drift_view(j)
        except Exception as ex:  # noqa: BLE001  a broken copy must not break Home
            print(f"[racktrack] compare of run {j.get('id')} for Home failed: {type(ex).__name__}: {ex}", file=sys.stderr)
            return out
        if dv.get("match") and dv.get("confirmed"):            # only a compare a person confirmed counts on Home
            out["record"] = {"source": SDB.SOURCES[dv["source"]], "rack_key": dv["match"]["rack_key"], "how": dv["match"]["how"]}
            out["differences"] = len((dv.get("drift") or {}).get("rows") or [])
    return out


@app.get("/api/video/{job}")
def api_video_status(job: str):
    j = job_or_none(job)
    if j is None:
        raise HTTPException(404, "No such run.")
    return public_job(j, with_result=True)


@app.get("/api/video/{job}/view")
def api_video_view(job: str):
    """Everything the result screen draws: the rack picture, the units top to bottom, every device with its kind, make and
    model, ports and pictures, the step pictures and the times. Ports arrive when the background phase ends (phase = done)."""
    j = job_or_none(job)
    if j is None or j.get("result") is None:
        raise HTTPException(404, "No result yet.")
    r = SDB.with_corrections(_run_org(j), (j.get("rack_id") or {}).get("id"), j["result"]); base = f"/api/video/{job}/file/"   # corrections made on earlier scans of this rack
    ubox = {u["unit"]: u["box"] for u in r["occupancy_frames"]["rows"] if u.get("box")}
    def lay_of(d):
        C = d.get("closeup") or {}; L = d.get("layout") or {}
        sp = SDB.screen_ports(d, ubox)                     # the one reading of the ports every screen and tool uses (engine/source_db.py)
        src, used, used_c, pd = sp["src"], sp["used"], sp["used_c"], sp["pd"]
        keep = ("id", "type", "status", "cable_type", "cable_colour", "cable_connector", "polygon", "cap_state", "cable_views")   # polygon: on the cut picture (pictures.cut)
        ports = [{k: q.get(k) for k in keep} for q in sp["ports"]]
        combo = [dict({k: q.get(k) for k in keep}, of=q["combo_of"]) for q in sp["combo"]]     # the SFP face of a combo port (49F to 52F), not counted again
        conf = src.get("identify_confidence") or {}
        T = L.get("typed") or {}
        return {"kind": L.get("kind"), "mode": L.get("mode"), "panel": L.get("panel"), "fitted": bool(src.get("fitted")), "auto": bool(src.get("auto")) and not src.get("fitted"), "vendor": (src.get("vendor") if used else None) or T.get("vendor"),
                "model": (src.get("model") if src.get("fitted") else None) or T.get("model"), "vendor_read": L.get("vendor_read") or L.get("vendor"), "model_read": L.get("model_read"), "status": src.get("status"), "typed": L.get("typed"),
                "confidence": conf or None, "confirm": bool(src.get("fitted")) and conf.get("model") is not None and float(conf.get("model") or 0) < 0.6,
                "needs_back": bool(src.get("needs_back")), "suggested_device": src.get("suggested_device"),
                "from": "close-up" if used_c else ("frames" if used else None), "ports": ports, "combo": combo, "counts": pd.get("counts"),
                "n_ports": src.get("n_ports") if src.get("mode") == "patchpanel" else len(ports), "size_known": src.get("size_known", True), "spec": src.get("spec") if used else None, "port_detector": src.get("port_detector") or L.get("port_detector"), "frames_voted": pd.get("frames_voted"), "connected": sum(1 for p in ports if p.get("status") == "connected"),   # a panel's size counts its hidden ports
                "pictures": {k: base + v for k, v in ((src.get("pictures") or {}) if used else {}).items() if isinstance(v, str)},
                "portmap": base + pd["picture"] if pd.get("picture") else None, "mapped": bool(d.get("layout")),
                "ports_state": (j.get("ports") or {}).get(str(d["index"])) or ("done" if d.get("layout") else None),
                "seconds": d.get("layout_seconds")}
    devices = [{"index": d["index"], "top": d["top"], "bottom": d["bottom"], "height": d["height"], "units": d.get("units"), "polygon": d.get("polygon"), "box": _detector_box(d),
                **_kind_of(d, j.get("finished") or j.get("started")),
                "prob": None if d.get("kind_override") else (d.get("classification") or {}).get("prob"), "unsure": bool(d.get("unsure")), "kind_model": (d.get("classification") or {}).get("label"), "corrected_by": (d.get("kind_override") or {}).get("by"), "crop": f"/api/video/{job}/crop/{d['index']}" + _crop_ver(j, d["index"]),   # the crop with nothing drawn on it (api_device_crop); a new cut gets a new link
                "frames": [base + t["file"] for t in d.get("top_frames") or [] if t.get("file")],
                "views": [f"/api/video/{job}/crop/{d['index']}" + _crop_ver(j, d["index"])] + [f"/api/video/{job}/crop/{d['index']}/{n}" for n in range(1, min(3, len(d.get("top_frames") or [])))],   # clean cuts, nothing drawn
                "layout": lay_of(d), "ids": _features_ids_of(d)} for d in r.get("devices") or []]
    units = [{"unit": u["unit"], "status": u["status"], "confidence": u.get("confidence"), "uncertain": u.get("uncertain"), "box": u.get("box"), "decided_by": u.get("decided_by"), "reason": u.get("reason")} for u in r["occupancy_frames"]["rows"]]   # box: where the unit is on the rack picture
    st = r.get("stitch") or {}
    return {"id": job, "title": j.get("title"), "status": j.get("status"), "phase": j.get("phase") or ("done" if j.get("status") == "done" else None),
            "pending_ports": any(v == "running" for v in (j.get("ports") or {}).values()),
            "rack": {"picture": base + "rack.png" + _file_ver(j, "rack.png"), "size": st.get("image_size"), "units": r["ladder"]["units"], "summary": r["occupancy_frames"]["summary"]},
            "quality": {"warnings": st.get("warnings") or [], "frames_joined": st.get("keyframes_used"), "video": r.get("capture")},
            "pictures": {k: base + v for k, v in (r.get("pictures") or {}).items()},
            "units": units, "devices": devices, "times_s": r.get("times_s"), "report": f"/video-report/{job}/index.html",
            "rack_label": rack_label_of(j), "rack_label_running": job in RACK_LABEL_RUNNING,
            "rack_id": j.get("rack_id"), "rack_id_options": rack_id_options(j), "record_passive": _record_passive(j)}


def _detector_box(d):
    """The device's own outline on the rack picture before it was snapped to its unit rows (polygon_box, the detector's box
    carried onto the rack), as a box; the unit band when there is none. The zoom uses it to tell whether the band between
    the unit lines holds the whole device (testers, 4 Nov 2026: the band cut a switch in half)."""
    q = d.get("polygon_box") or d.get("polygon_old")
    if q and len(q) >= 3:
        xs = [p[0] for p in q]; ys = [p[1] for p in q]
        return [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)]
    return d.get("box")


def _record_passive(j):
    """The units the confirmed record fills with something the video has no class for (a cable manager, a blank panel, a
    shelf): the screens must not offer them as free space (verify pass 2, finding 15). Empty until a person confirmed the
    compare."""
    if j.get("result") is None or not (j.get("db") or {}).get("source"):
        return []
    try:
        dv = drift_view(j)
    except Exception as ex:  # noqa: BLE001  a broken copy must not break the result screen
        print(f"[racktrack] record of run {j.get('id')} not read for the free space: {type(ex).__name__}: {ex}", file=sys.stderr)
        return []
    if not (dv.get("match") and dv.get("confirmed")):
        return []
    out = []
    for r in dv.get("record") or []:
        if (r.get("kind") or "").lower() in SDB.NOT_SEEN_KINDS and r.get("ru_start"):
            for u in range(int(r["ru_start"]), int(r["ru_start"]) + int(r.get("ru_height") or 1)):
                out.append({"unit": f"u{u:02d}", "kind": r["kind"], "name": r.get("name"), "source": SDB.SOURCES.get(dv["source"], dv["source"])})
    return out


def _kind_of(d, when=None):
    """The device's one kind for the screens (engine/source_db.py device_kind) and where it came from, in plain words."""
    k = SDB.device_kind(d, when)                 # when: the run's date, for a kind recorded when the ports were mapped
    return {"kind": k["kind"], "kind_from": k["from"], "kind_words": k["words"]}


@app.delete("/api/video/{job}")
def api_video_delete(job: str):
    """Remove a run and its folder (not the stored demo run, not a run that is going)."""
    j = JOBS.get(job)
    if j is None:
        raise HTTPException(404, "No such run, or it cannot be removed.")
    if j.get("status") == "running":
        raise HTTPException(409, "That run is still going.")
    with STATE_LOCK:
        JOBS.pop(job, None)
    shutil.rmtree(j["dir"], ignore_errors=True)
    return {"ok": True}


@app.api_route("/video-report/{job}/{path:path}", methods=["GET", "HEAD"])
def video_report_file(job: str, path: str):
    j = job_or_none(job)
    if j is None:
        raise HTTPException(404, "No such run.")
    base = report_dir(j); f = (base / (path or "index.html")).resolve()
    if not str(f).startswith(str(base.resolve())) or not f.is_file():
        raise HTTPException(404, "No such file.")
    if f.suffix == ".html":
        return themed_page(f)
    return FileResponse(f, headers={"Cache-Control": "no-cache"})


def themed_page(f: pathlib.Path):
    """A report page in the app's look: web/app/report-theme.css is added after the page's own styles, so old reports get it too."""
    html = f.read_text(encoding="utf-8") + '<link rel="stylesheet" href="/app/report-theme.css?v=11">'
    return Response(html, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})


def _file_ver(j, name):
    """?v=<mtime> on a run file's link: a rewritten picture is fetched again (the app caches files for a day)."""
    try:
        return f"?v={int((pathlib.Path(j['dir']) / name).stat().st_mtime)}"
    except OSError:
        return ""


def _crop_ver(j, index):
    f = pathlib.Path(j["dir"]) / f"section_D{index}_clean.jpg" if j.get("dir") else None
    return f"?v={int(f.stat().st_mtime)}" if f is not None and f.exists() else ""


@app.get("/api/video/{job}/crop/{index}")
def api_device_crop(job: str, index: int):
    """One device's crop with nothing drawn on it, cut from its best video frame (the pipeline's section crop carries
    the detector's outline, which the app must not show: user, 2026-10-27). Made once, then kept beside the run."""
    j = job_or_none(job)
    if j is None or j.get("result") is None:
        raise HTTPException(404, "No result yet.")
    d = pathlib.Path(j["dir"]); f = d / f"section_D{index}_clean.jpg"
    if not f.exists():
        dv = next((x for x in j["result"].get("devices") or [] if x["index"] == index), None)
        if dv is None:
            raise HTTPException(404, "No such device.")
        rack_video.clean_crop(dv, j.get("video_path"), d / "rack.png", f, (j.get("result") or {}).get("devices"))
    return FileResponse(f, headers={"Cache-Control": "max-age=86400"})


@app.get("/api/video/{job}/crop/{index}/{nth}")
def api_device_view(job: str, index: int, nth: int):
    """The device's nth other view (1, 2 ...): the same clean cut from a later frame that holds it, nothing drawn on it
    ("Seen in the video" on the web, user 2026-10-29: no outlines). 404 when no such frame."""
    j = job_or_none(job)
    if j is None or j.get("result") is None:
        raise HTTPException(404, "No result yet.")
    if nth < 1:
        return api_device_crop(job, index)
    d = pathlib.Path(j["dir"]); f = d / f"section_D{index}_clean_{nth}.jpg"
    if not f.exists():
        dv = next((x for x in j["result"].get("devices") or [] if x["index"] == index), None)
        if dv is None:
            raise HTTPException(404, "No such device.")
        if not rack_video.clean_crop(dv, j.get("video_path"), d / "rack.png", f, (j.get("result") or {}).get("devices"), nth=nth):
            raise HTTPException(404, "No other view.")
    return FileResponse(f, headers={"Cache-Control": "max-age=86400"})


PDF_LOCK = threading.Lock()


def _print_pdf(page, pdf):
    """The self-contained report page printed to PDF with headless Chromium, every step in order with its name, every
    switch card and every folded section open (as tools/run_videos.py prints the testers' reports)."""
    from playwright.sync_api import sync_playwright
    shell = os.environ.get("PW_CHROMIUM") or next(iter(sorted(pathlib.Path.home().glob(
        "Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-mac-arm64/chrome-headless-shell"))[::-1]), None)
    with sync_playwright() as p:
        b = p.chromium.launch(**({"executable_path": str(shell)} if shell else {}))
        pg = b.new_page(viewport={"width": 1280, "height": 900})
        pg.goto(pathlib.Path(page).resolve().as_uri(), wait_until="load")
        pg.evaluate("""() => {
            document.querySelectorAll('.step').forEach((s, i) => {
                s.hidden = false;
                const a = document.querySelector('.rail a[href="#' + s.id + '"]');
                const h = document.createElement('h2'); h.className = 'pdfh'; h.textContent = a ? a.textContent : (i + 1) + ''; s.prepend(h);
            });
            document.querySelectorAll('.lsw[data-sw]').forEach(c => c.hidden = false);
            document.querySelectorAll('details').forEach(d => d.open = true);
        }""")
        pg.add_style_tag(content=".rail,.snav,.stick{display:none!important} .step{display:block!important;break-before:page} .step:first-of-type{break-before:auto} .pdfh{font-size:1.3rem;margin:0 0 12px;border-bottom:1px solid #dbe1ea;padding-bottom:6px} img{break-inside:avoid}")
        pg.emulate_media(media="print")
        pg.pdf(path=str(pdf), format="A4", print_background=True, margin={"top": "10mm", "bottom": "10mm", "left": "8mm", "right": "8mm"})
        b.close()


@app.get("/api/video/{job}/report.pdf")
def api_report_pdf(job: str):
    """The whole report as one PDF to download (testers, 4 Nov 2026). Printed once per version of the report, then kept
    beside the run; a report rebuilt after a port read or a close-up is printed again on the next download."""
    j = job_or_none(job)
    if j is None or j.get("result") is None:
        raise HTTPException(404, "No report yet.")
    rep = report_dir(j); d = pathlib.Path(j["dir"])
    if not (rep / "index.html").exists():
        raise HTTPException(404, "No report yet.")
    pdf, page = d / "report.pdf", d / "report_print.html"
    stamp = max(f.stat().st_mtime for f in rep.rglob("*") if f.is_file())
    with PDF_LOCK:
        if not pdf.exists() or pdf.stat().st_mtime < stamp:
            try:
                video_report.self_contained(rep, page); _print_pdf(page, pdf)
            except ImportError:
                raise HTTPException(503, "The PDF cannot be made on this server: no browser to print with. Open the report and use Print instead.")
            except Exception as ex:  # noqa: BLE001
                print(f"[racktrack] report of run {job} not printed: {type(ex).__name__}: {ex}", file=sys.stderr)
                raise HTTPException(500, "The PDF could not be made. Try again in a moment.")
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", str((j.get("rack_id") or {}).get("id") or j.get("title") or job)).strip("-")
    return FileResponse(pdf, media_type="application/pdf", filename=f"RackTrack-{name}.pdf", headers={"Cache-Control": "no-store"})


@app.get("/api/video/{job}/file/{name}")
def api_video_file(job: str, name: str):
    j = job_or_none(job)
    if j is None or "/" in name or name.startswith(".") or not (pathlib.Path(j["dir"]) / name).exists():
        raise HTTPException(404, "No such file.")
    return FileResponse(pathlib.Path(j["dir"]) / name, headers={"Cache-Control": "max-age=86400"})


# ------------------------------------------------------------------ the camera guide (mobile/capture/, the team's Rack Sweep Guide) and its takes
CAPTURE_PAGE = MOBILE / "capture" / "index.html"


@app.get("/capture")
def capture_root():
    return RedirectResponse("/capture/")


@app.api_route("/capture/", methods=["GET", "HEAD"])
@app.api_route("/capture/index.html", methods=["GET", "HEAD"])
def capture_page():
    return FileResponse(CAPTURE_PAGE, media_type="text/html", headers=NO_CACHE)


@app.get("/capture/upload-info")
def capture_upload_info():
    return JSONResponse({"parts": True}, headers=NO_CACHE)


@app.post("/capture/upload")
async def capture_upload(request: Request):
    """One file of one take (video.mp4 / video.webm / log.json), whole or in pieces, saved in data/captures/ with the
    guide's own checks (engine/captures.py)."""
    q = request.query_params
    try:
        length = int(request.headers.get("content-length") or 0)
        parts, index, offset = int(q.get("parts", "1")), int(q.get("part", "0")), int(q.get("offset", "0"))
    except ValueError:
        return JSONResponse({"error": "bad part numbers"}, status_code=400)
    try:
        folder, part, final = CAP.upload_target(q.get("take", ""), q.get("tester", "unknown"), q.get("file", ""), length, parts, index, offset)
    except CAP.UploadError as ex:
        return JSONResponse({"error": str(ex)}, status_code=ex.status)
    me = request.state.principal; mine = {"org": me.get("org"), "site": me.get("site"), "by": me.get("username")}
    own = folder / "owner.json"
    if own.exists() and (_take_owner(folder).get("org"), _take_owner(folder).get("site")) != (mine["org"], mine["site"]):
        return JSONResponse({"error": "That take name is already in use. Send the take again."}, status_code=409)
    if not own.exists():
        own.write_text(json.dumps(mine))                 # the take belongs to the sender's organization and site
    from starlette.requests import ClientDisconnect
    got = 0
    try:
        with open(part, "wb" if index == 0 else "ab") as f:
            async for chunk in request.stream():
                got += len(chunk)
                if got > length:
                    break
                f.write(chunk)
    except ClientDisconnect:
        got = -1
    if got != length:
        part.unlink(missing_ok=True)
        return JSONResponse({"error": "upload incomplete"}, status_code=400)
    if index == parts - 1:
        part.replace(final)
        pos = _features_position(*(_features_float(q.get(k)) for k in ("lat", "lng", "accuracy")))     # wt/features: optional lat, lng, accuracy
        if pos:
            (folder / "position.json").write_text(json.dumps(pos))
    return {"ok": True, "saved": f"{folder.name}/{final.name}", "take": f"app/{folder.name}"}


def _take_owner(td):
    """Who sent a camera-guide take: {"org", "site", "by"} from its owner.json (written at upload). A take without one (the team
    folder, takes from before sign-in) has no organization, so only the platform owner sees it."""
    try:
        return json.loads((pathlib.Path(td) / "owner.json").read_text())
    except (OSError, ValueError):
        return {"org": None, "site": None}


def _take_visible(td):
    return rt_session.can_see(rt_session.current(), dict(_take_owner(td), id="take"))


@app.get("/api/captures")
def api_captures():
    takes = [t for t in CAP.list_takes() if (td := CAP.take_dir(t.get("source") or "", t.get("folder") or "")) and _take_visible(td)]
    return {"takes": takes, "team_folder": CAP.SOURCES["team"].is_dir() and rt_session.current().get("role") == "owner"}


def _take_file(source, folder):
    td = CAP.take_dir(source, folder); t = CAP.read_take(source, td) if td else None
    if t is None or not t["video"] or not _take_visible(td):              # another organization's take is not there
        raise HTTPException(404, "No such take.")
    return td, t


@app.get("/api/captures/{source}/{folder}/video")
def api_capture_video(source: str, folder: str):
    td, t = _take_file(source, folder)
    return FileResponse(td / t["video"], media_type="video/mp4" if t["video"].endswith(".mp4") else "video/webm")


@app.get("/api/captures/{source}/{folder}/thumb.jpg")
def api_capture_thumb(source: str, folder: str):
    import cv2
    td, t = _take_file(source, folder)
    cache = pathlib.Path(tempfile.gettempdir()) / "racktrack_take_thumbs" / f"{source}__{folder}.jpg"
    if not cache.exists():
        cap = cv2.VideoCapture(str(td / t["video"])); n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, n // 2)); ok, fr = cap.read(); cap.release()
        if not ok:
            raise HTTPException(404, "The take's video cannot be read.")
        dx, dy = rack_stitch.sweep_motion(td / t["video"], samples=8)
        if abs(dx) > 1.5 * abs(dy) and abs(dx) >= 0.5:
            fr = cv2.rotate(fr, cv2.ROTATE_90_CLOCKWISE)
        fr = cv2.resize(fr, (int(fr.shape[1] * 360 / fr.shape[0]), 360))
        cache.parent.mkdir(parents=True, exist_ok=True); cv2.imwrite(str(cache), fr, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return FileResponse(cache, media_type="image/jpeg")


# ------------------------------------------------------------------ a close-up photo of one device, kept with its run
CLOSEUPS = {}


def jpeg_bytes(img, max_side=DISPLAY_MAX):
    im = img.copy(); im.thumbnail((max_side, max_side)); buf = io.BytesIO(); im.convert("RGB").save(buf, "JPEG", quality=86); return buf.getvalue()


PORT_KINDS = set(pipe.MAPPER_KIND)


def _read_panel_thread(job, index):
    """A patch panel's type and ports (engine/patch_panels.py, the team's package), kept as the device's layout."""
    j = JOBS[job]
    try:
        with MODEL_LOCK:
            d = pathlib.Path(j["dir"]); crop = d / f"section_D{index}_clean.jpg"
            rf = d / "result.json"; r = json.loads(rf.read_text())
            dv = next(x for x in r["devices"] if x["index"] == index)
            if not crop.exists():
                rack_video.clean_crop(dv, j.get("video_path"), d / "rack.png", crop, r["devices"])
            lay = patch_panels.read_patch_panel(d, index, crop)
            with run_lock(job):                         # merged into the result as it is now: an edit made meanwhile stays
                r = json.loads(rf.read_text()); dv = next((x for x in r["devices"] if x["index"] == index), None)
                if dv is not None:
                    dv["layout"] = dict(lay, typed=(dv.get("layout") or {}).get("typed"))
                    rf.write_text(json.dumps(r, indent=1))
                j["result"] = r; j["summary"] = _summary(r)
            j["ports"][str(index)] = "done"
    except Exception as ex:  # noqa: BLE001
        print(f"[racktrack] panel D{index} of {job} not read: {type(ex).__name__}: {ex}", file=sys.stderr)
        j["ports"][str(index)] = "failed"; j.setdefault("ports_error", {})[str(index)] = "The panel could not be read."
    _save(j)


def _read_ports_thread(job, index, hint=None):
    import cv2
    j = JOBS[job]
    try:
        with MODEL_LOCK:
            rack = cv2.imread(str(j["rack_path"]))
            dv = rack_video.read_device_ports(j["dir"], j["video_path"], rack, index, hint=hint, write_lock=run_lock(job))
            r = json.loads((pathlib.Path(j["dir"]) / "result.json").read_text()); j["result"] = r; j["summary"] = _summary(r)
            j["ports"][str(index)] = "done" if dv.get("layout") else "none"
        _closeup_rebuild(j)
    except Exception as ex:  # noqa: BLE001
        print(f"[racktrack] D{index} of {job} not read: {type(ex).__name__}: {ex}", file=sys.stderr)
        j["ports"][str(index)] = "failed"; j.setdefault("ports_error", {})[str(index)] = "The device could not be read."
    _save(j)


PORTS_ALL_RUNNING = set()


def start_ports_all(job):
    """After a run: the ports of every switch, router, firewall, gateway and patch panel are read on their own, one at a
    time in the background, so Find a port and the Connections list every device (testers, 4 Nov 2026: a device nobody
    opened had no ports and was missing from the list). A device a person opens meanwhile is read then, as before."""
    if job in PORTS_ALL_RUNNING:
        return
    PORTS_ALL_RUNNING.add(job)
    threading.Thread(target=_ports_all_thread, args=(job,), daemon=True).start()


def _ports_all_thread(job):
    try:
        j = JOBS.get(job)
        if j is None or j.get("status") != "done":
            return
        devs = sorted((j.get("result") or {}).get("devices") or [], key=lambda d: -(SDB._unum(d.get("top")) or 0))   # top of the rack first
        for dv in devs:
            if JOBS.get(job) is not j or j.get("status") != "done":
                return                                  # the run was removed or restarted meanwhile
            kind = SDB.device_kind(dv)["kind"]          # the one kind: a correction, then the faceplate, then the picture
            if kind not in PORT_KINDS and not (kind == "Patch Panel" and patch_panels.available()):
                continue
            ports = j["ports"] = j.get("ports") or {}
            if ports.get(str(dv["index"])):
                continue                                # read already, or being read because a person opened it
            ports[str(dv["index"])] = "running"; _save(j)
            (_read_panel_thread if kind == "Patch Panel" else _read_ports_thread)(job, dv["index"])
    except Exception as ex:  # noqa: BLE001
        print(f"[racktrack] ports of run {job} not read on their own: {type(ex).__name__}: {ex}", file=sys.stderr)
    finally:
        PORTS_ALL_RUNNING.discard(job)


KINDS = ("Switch", "Router", "Firewall", "Gateway", "Patch Panel", "Server", "Storage unit", "PDU", "UPS", "Other")


# ------------------------------------------------------------------ specifications of an identified model (RT-EOS's switch_specs agent: local knowledge base, then its web lookup)
SPECS_CLI = pathlib.Path(os.environ.get("RACKTRACK_SPECS_CLI", ROOT / "engine" / "readers" / "switch_specs" / "cli.py"))
SPECS_CACHE = DATA / "specs_cache.json"
SPEC_FIELDS = (("port_config", "Ports", ""), ("uplink_config", "Uplinks", ""), ("port_speed_max_gbps", "Max port speed", " Gbps"), ("switching_capacity_gbps", "Switching capacity", " Gbps"),
               ("poe_standard", "PoE", ""), ("poe_budget_w", "PoE budget", " W"), ("power_max_w", "Power max", " W"), ("layer", "Layer", ""), ("rack_units", "Height", "U"), ("nos", "Network OS", ""))


def _spec_same_model(asked, rec):
    q = re.sub(r"[^a-z0-9]", "", str(asked).lower())
    for f in ("model", "sku"):
        r = re.sub(r"[^a-z0-9]", "", str(rec.get(f) or "").lower())
        if len(q) >= 4 and len(r) >= 4 and (q == r or q in r or r in q):
            return True
    return False


def online_lookups(p=None):
    """May a lookup leave this machine (the makers' pages)? Only when the engine allows it (RACKTRACK_ONLINE_LOOKUPS=1; engine.sh
    leaves it off and the tests never set it) and the person's organization has its online lookups setting on (RT-EOS's
    organization settings). The platform owner has no organization, so never."""
    if os.environ.get("RACKTRACK_ONLINE_LOOKUPS") != "1":
        return False
    p = p or rt_session.current()
    return bool(p and p.get("org")) and rt_session.org_online_lookups(p["org"])


@app.get("/api/specs")
def api_specs(vendor: str = "", model: str = ""):
    """The main specifications of a make and model (user, 2026-10-27): ports, uplinks, speed, capacity, PoE, power, layer,
    height. Only a record that really is the asked model counts; the answer is kept in data/specs_cache.json. The local knowledge
    base first; the web only when online lookups are allowed (online_lookups)."""
    import subprocess
    if not model.strip():
        raise HTTPException(400, "A model is needed.")
    key = re.sub(r"[^a-z0-9]", "", (vendor + " " + model).lower())
    try:
        cache = json.loads(SPECS_CACHE.read_text()) if SPECS_CACHE.exists() else {}
    except ValueError as ex:
        print(f"[racktrack] specs cache does not read ({ex}); starting an empty one", file=sys.stderr)
        cache = {}
    if key in cache:
        hit = cache[key]
        if (hit.get("specs") or {}).get("Ports"):            # answers kept before the ports line was cleaned are cleaned on the way out
            hit = dict(hit, specs=dict(hit["specs"], Ports=_spec_ports_line(hit["specs"]["Ports"])))
        return hit
    online = online_lookups()
    out = {"ok": False, "specs": {}, "note": "No specifications found for this model." if online else "This model is not in the local knowledge base, and online lookups are off."}
    if SPECS_CLI.exists():
        try:
            rec, resp = None, {}
            for extra in (["--no-live"], ["--deadline", "8"]) if online else (["--no-live"],):   # the local knowledge base at once; the web only when allowed and it has nothing
                p = subprocess.run([RTF.RTEOS_PYTHON, str(SPECS_CLI), "--json", *extra, f"{vendor} {model}".strip()], cwd=SPECS_CLI.parent, capture_output=True, text=True, timeout=30)
                line = next((ln for ln in reversed(p.stdout.splitlines()) if ln.startswith("{")), "{}")
                resp = (json.loads(line).get("response") or {})
                rec = resp.get("result") or (resp.get("results") or [None])[0]
                if resp.get("type") == "spec" and rec and _spec_same_model(model, rec):
                    break
            if resp.get("type") == "spec" and rec and _spec_same_model(model, rec):
                specs = {}
                for f, label, unit in SPEC_FIELDS:
                    v = rec.get(f)
                    if v not in (None, "", []):
                        specs[label] = (", ".join(str(x) for x in v) if isinstance(v, list) else str(v)) + unit
                if specs.get("Ports"):
                    specs["Ports"] = _spec_ports_line(specs["Ports"])
                if "Ports" not in specs and rec.get("port_count"):
                    specs["Ports"] = str(rec["port_count"])
                out = {"ok": True, "specs": specs, "datasheet": rec.get("datasheet_url"), "vendor": rec.get("vendor"), "model": rec.get("model")}
        except Exception as ex:  # noqa: BLE001
            print(f"[racktrack] specs lookup for {vendor} {model} failed: {type(ex).__name__}: {ex}", file=sys.stderr)
            out["note"] = "The specifications could not be looked up just now."
    if out["ok"] or online:                                  # misses after a web lookup are kept too: the web is not asked again for the same name
        cache[key] = out
        try:
            SPECS_CACHE.write_text(json.dumps(cache, indent=1))
        except OSError as ex:
            print(f"[racktrack] specs cache not saved: {ex}", file=sys.stderr)
    return out


def _spec_ports_line(text):
    """A page read live lists the same ports twice: one entry per port count, joined with " + " ("24 x 1G RJ45 + 4 x 1G SFP");
    the "+" between two entries is not part of either (verify pass 2, finding 20: "1G RJ45 + · 4 x 1G SFP")."""
    seen, parts = set(), []
    for n, what in re.findall(r"(\d+)\s*x\s*([A-Za-z0-9/\-\+\.]+(?: [A-Za-z0-9/\-\+\.]+)*?)(?=\s*\+?\s+\d+\s*x|$)", text.replace("·", " ").strip()):
        what = re.sub(r"\s*\+\s*$", "", what.replace(" ports", "")).strip()
        if n not in seen and what and "interface" not in what.lower():
            seen.add(n); parts.append(f"{n} x {what}")
    return " + ".join(parts) if parts else text


class NewDevice(BaseModel):
    top: str = Field(max_length=6)
    bottom: str = Field(max_length=6)
    kind: str | None = Field(default=None, max_length=20)


@app.post("/api/video/{job}/device")
@edits_run
def api_add_device(job: str, body: NewDevice, request: Request):
    if JOBS.get(job) is not None and _reading(JOBS[job]):
        raise HTTPException(409, "A device of this rack is being read. Add the device when the reading has ended.")
    """A device the sweep missed, added by a person over the units it fills (user, 2026-10-27: Network Rack 2, U37-39 holds
    one device the detector never boxed). Its outline is the units' rows rail to rail; its crop is cut from the rack picture;
    its units become in use. Kept in result.json like any other device, with who added it."""
    j = JOBS.get(job)
    if j is None or j.get("status") != "done":
        raise HTTPException(404, "No such finished run.")
    if body.kind is not None and body.kind not in KINDS:
        raise HTTPException(400, "Not a kind the app knows.")
    rf = pathlib.Path(j["dir"]) / "result.json"; r = json.loads(rf.read_text())
    rows = r["occupancy_frames"]["rows"]; names = [x["unit"] for x in rows]
    try:
        a, b = names.index(body.top.lower()), names.index(body.bottom.lower())
    except ValueError:
        raise HTTPException(400, "No such unit.")
    a, b = min(a, b), max(a, b); units = names[a: b + 1]
    taken = {u for d in r["devices"] for u in (d.get("units") or [])}
    if any(u in taken for u in units):
        raise HTTPException(409, "A device already sits on one of these units.")
    boxes = [rows[k]["box"] for k in range(a, b + 1)]
    x1, y1, x2, y2 = min(bx[0] for bx in boxes), min(bx[1] for bx in boxes), max(bx[2] for bx in boxes), max(bx[3] for bx in boxes)
    i = max([d["index"] for d in r["devices"]] + [0]) + 1
    who = rt_session.name(request); when = datetime.datetime.now().isoformat(timespec="seconds")
    r["devices"].append({"index": i, "units": units, "top": units[0], "bottom": units[-1], "height": len(units), "box": [x1, y1, x2, y2],
                         "polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]], "polygon_from": "person", "reason": f"Added by {who}: the sweep missed it.",
                         "why_image": "", "frames_shape": [], "top_frames": [], "type": None, "type_hint": None, "type_confidence": 0, "type_votes": 0,
                         "rack_type": None, "rack_type_confidence": 0, "same_span_as_rack": False, "best_frame": None, "uncertain": False, "unsure": False,
                         "label": f"D{i} · {len(units)}U", "classification": {"model": None, "label": body.kind, "prob": None, "source": "person", "frames": [], "rack": None},
                         "kind_override": {"kind": body.kind, "by": who, "when": when} if body.kind else None, "added_by": {"by": who, "when": when}})
    for k in range(a, b + 1):
        rows[k].update(status="occupied", p_occupied=1.0, confidence=1.0, decided_by="person", attention=None, device=i, reason=f"A person marked this unit in use ({who}).")
    r["occupancy_frames"]["summary"] = occ_summary(rows)
    rf.write_text(json.dumps(r, indent=1)); j["result"] = r; j["summary"] = _summary(r); _save(j)
    threading.Thread(target=_closeup_rebuild, args=(j,), daemon=True).start()
    return {"ok": True, "index": i}


class UnitMark(BaseModel):
    status: str
    join: int | None = None


def given_to(dev):
    """What the devices had made of a unit before a person answered it, for the report's before and after table."""
    if not dev:
        return "No device"
    us = dev.get("units") or []; U = lambda u: u.upper().replace("U0", "U"); n = len(us)
    where = U(us[0]) if n == 1 else f"{U(us[0])}-{U(us[-1])}"
    return (f"A 1U device on {where}" if n == 1 else f"In a {n}U device on {where}") + f" (then D{dev['index']})" + (", not sure" if dev.get("unsure") else "")


@app.post("/api/video/{job}/unit/{unit}")
@edits_run
def api_mark_unit(job: str, unit: str, body: UnitMark, request: Request):
    """Step 2, answered by a person (user, 2026-10-30: "give options for me to clarify whether that unit is occupied or
    unoccupied"): {"status": "occupied" | "unoccupied"}. Free: a device on the unit gives it up, and a device left
    with no unit is removed (the devices after it are renumbered). In use: the unit is in use; when no device sits on
    it the answer says so, and the app offers to add one over it."""
    j = JOBS.get(job)
    if j is None or j.get("status") != "done":
        raise HTTPException(404, "No such finished run.")
    if body.status not in ("occupied", "unoccupied"):
        raise HTTPException(400, "status is occupied or unoccupied.")
    rf = pathlib.Path(j["dir"]) / "result.json"; r = json.loads(rf.read_text()); rows = r["occupancy_frames"]["rows"]
    row = next((x for x in rows if x["unit"] == unit.lower()), None)
    if row is None:
        raise HTTPException(404, "No such unit.")
    on = _when_words()
    if "before" not in row:                                              # what the frames said, kept the first time a person answers (for the report)
        row["before"] = {k: row.get(k) for k in ("status", "p_occupied", "frames_voting", "frames_device", "frames_empty", "decided_by")}
    row.update(status=body.status, p_occupied=1.0 if body.status == "occupied" else 0.0, confidence=1.0, decided_by="person", attention=None,
               candidate_box=None, reason=f"Marked {'in use' if body.status == 'occupied' else 'free'} on {on}.")
    dev = next((d for d in r["devices"] if row["unit"] in (d.get("units") or [])), None)
    dev_before = (dev["index"], len(dev.get("units") or [])) if dev else None
    given = given_to(dev); given_units = list(dev.get("units") or []) if dev else []
    removed = None
    if body.status == "unoccupied" and dev is not None:
        left = [u for u in dev["units"] if u != row["unit"]]
        if left:                                                         # the device keeps its other units: its outline shrinks to them
            boxes = [x["box"] for x in rows if x["unit"] in left]; xs = [q[0] for q in dev["polygon"]]
            x1, x2 = min(xs), max(xs); y1, y2 = min(b[1] for b in boxes), max(b[3] for b in boxes)
            dev.update(units=left, top=left[0], bottom=left[-1], height=len(left), label=f"D{dev['index']} · {len(left)}U",
                       polygon=[[x1, y1], [x2, y1], [x2, y2], [x1, y2]], box=[round(x1), round(y1), round(x2), round(y2)])
            for f in pathlib.Path(j["dir"]).glob(f"section_D{dev['index']}_clean*.jpg"):
                f.unlink()
        else:
            removed = dev["index"]
    joined = None
    if body.status == "occupied" and dev is None and body.join is not None:   # in use, as part of the device above or below it
        nb = next((d for d in r["devices"] if d["index"] == body.join), None)
        names = [x["unit"] for x in sorted(rows, key=lambda x: x["box"][1])]; k = names.index(row["unit"])
        if nb is None or not any(abs(names.index(u) - k) == 1 for u in nb.get("units") or [] if u in names):
            raise HTTPException(409, "That device is not next to this unit.")
        units = sorted(set(nb["units"]) | {row["unit"]}, key=names.index); boxes = [x["box"] for x in rows if x["unit"] in units]
        xs = [q[0] for q in nb["polygon"]]; x1, x2 = min(xs), max(xs); y1, y2 = min(b[1] for b in boxes), max(b[3] for b in boxes)
        nb.update(units=units, top=units[0], bottom=units[-1], height=len(units), label=f"D{nb['index']} · {len(units)}U",
                  polygon=[[x1, y1], [x2, y1], [x2, y2], [x1, y2]], box=[round(x1), round(y1), round(x2), round(y2)])
        row["device"] = nb["index"]; joined = nb["index"]
        for f in pathlib.Path(j["dir"]).glob(f"section_D{nb['index']}_clean*.jpg"):
            f.unlink()
    U = lambda u: u.upper().replace("U0", "U")
    if removed is not None:
        effect = f"D{removed} had no unit left and was taken out; the devices below it moved up one number"
    elif dev is not None and body.status == "unoccupied":
        effect = f"D{dev['index']} went from {dev_before[1]}U to {len(dev['units'])}U"
    elif joined is not None:
        nb = next(d for d in r["devices"] if d["index"] == joined); effect = f"D{joined} went from {len(nb['units']) - 1}U to {len(nb['units'])}U"
    elif body.status == "occupied" and dev is None:
        effect = "in use, no device on it yet"
    else:
        effect = f"D{dev['index']} stays as it is" if dev else "free"
    r.setdefault("answers", []).append({"unit": row["unit"], "answer": "in use" if body.status == "occupied" else "free", "join": body.join, "on": on,
                                        "before": row["before"], "given_to": given, "before_units": given_units, "effect": effect})
    r["occupancy_frames"]["summary"] = occ_summary(rows)
    rf.write_text(json.dumps(r, indent=1)); j["result"] = r; j["summary"] = _summary(r); _save(j)
    if removed is not None:                                              # nothing left of it: taken out like any box that is no device
        api_remove_device(job, removed, request)
    return {"ok": True, "unit": row["unit"], "status": body.status, "device": joined or (dev["index"] if dev and removed is None else None), "removed": removed,
            "needs_device": body.status == "occupied" and dev is None and joined is None}


@app.post("/api/video/{job}/device/{index}/remove")
@edits_run
def api_remove_device(job: str, index: int, request: Request):
    if JOBS.get(job) is not None and _reading(JOBS[job]):
        raise HTTPException(409, "A device of this rack is being read. Remove the device when the reading has ended.")
    """A box that is not a device, taken out by a person (user, 2026-10-27: Network Rack 1 'there is no D6'). The devices
    after it move up one number, with their files, layouts, panel reads and port states, so the rack reads D1..Dn without
    a hole; its units become free. The removal is kept in result.json with who did it."""
    j = JOBS.get(job)
    if j is None or j.get("status") != "done":
        raise HTTPException(404, "No such finished run.")
    d = pathlib.Path(j["dir"]); rf = d / "result.json"; r = json.loads(rf.read_text())
    gone = next((x for x in r["devices"] if x["index"] == index), None)
    if gone is None:
        raise HTTPException(404, "No such device.")
    who = rt_session.name(request); when = datetime.datetime.now().isoformat(timespec="seconds")
    import shutil
    for f in list(d.glob(f"section_D{index}*.jpg")) + list(d.glob(f"layout_D{index}_*.jpg")) + list(d.glob(f"panel_D{index}_overlay.jpg")) + list((d / "switch_layouts").glob(f"D{index}_*")) + [d / "patch_panels" / f"D{index}"]:
        shutil.rmtree(f, ignore_errors=True) if f.is_dir() else (f.unlink() if f.exists() else None)
    ledger = d / "switch_layouts" / "_agree.txt"
    lines = ledger.read_text().splitlines(keepends=True) if ledger.exists() else []
    ports = dict(j.get("ports") or {}); ports.pop(str(index), None)
    rows = r["occupancy_frames"]["rows"]
    for row in rows:
        if row.get("device") == index:
            row.update(status="unoccupied", p_occupied=0.0, confidence=1.0, decided_by="person", attention=None, device=None, reason=f"Marked free on {_when_words()}.")
    for u in gone.get("units") or []:                                   # units the device sat on with no row of their own
        for row in rows:
            if row["unit"] == u and row.get("device") in (None, index) and row["status"] != "unoccupied":
                row.update(status="unoccupied", p_occupied=0.0, confidence=1.0, decided_by="person", attention=None, device=None, reason=f"Marked free on {_when_words()}.")
    r["devices"] = [x for x in r["devices"] if x["index"] != index]
    for a in r.get("answers") or []:                                    # earlier answers name the devices by their new numbers
        a["effect"] = re.sub(r"(?<!then )\bD(\d+)\b", lambda m: f"D{int(m.group(1)) - 1}" if int(m.group(1)) > index else m.group(0), a.get("effect") or "")
    for x in sorted(r["devices"], key=lambda x: x["index"]):            # ascending: each rename lands on a number just freed
        k = x["index"]
        if k < index:
            continue
        n = k - 1
        for f in list(d.glob(f"section_D{k}*.jpg")) + list(d.glob(f"layout_D{k}_*.jpg")) + list(d.glob(f"panel_D{k}_overlay.jpg")) + list((d / "switch_layouts").glob(f"D{k}_*")) + [d / "patch_panels" / f"D{k}"]:
            if f.exists():
                f.rename(f.with_name(f.name.replace(f"D{k}", f"D{n}", 1)))
        lines = [ln.replace(f"D{k}\t", f"D{n}\t", 1) if ln.startswith(f"D{k}\t") else ln for ln in lines]
        if str(k) in ports:
            ports[str(n)] = ports.pop(str(k))
        for row in rows:
            if row.get("device") == k:
                row["device"] = n
        x["index"] = n; _renumber(x, k, n)
    for x in r["rack"].get("devices") or []:                             # the rack picture's own device list and the frame sections
        if x.get("index") == index:
            x["removed"] = True
        elif x.get("index", 0) > index:
            x["index"] -= 1; _renumber(x, x["index"] + 1, x["index"])
    r["rack"]["devices"] = [x for x in r["rack"].get("devices") or [] if not x.get("removed")]
    r["sections"] = [sc for sc in r.get("sections") or [] if sc.get("device") != index]
    for sc in r["sections"]:
        if sc.get("device", 0) > index:
            sc["device"] -= 1; _renumber(sc, sc["device"] + 1, sc["device"])
    if (d / "closeups").exists():
        for k in sorted(int(f.name[1:]) for f in (d / "closeups").glob("D*") if f.name[1:].isdigit()):
            if k == index:
                shutil.rmtree(d / "closeups" / f"D{k}", ignore_errors=True)
            elif k > index:
                (d / "closeups" / f"D{k}").rename(d / "closeups" / f"D{k - 1}")
    if ledger.exists():
        ledger.write_text("".join(ln for ln in lines if not ln.startswith(f"D{index}\t")))
    j["ports"] = ports
    r.setdefault("removed", []).append({"index": index, "units": gone.get("units"), "by": who, "when": when})
    r["occupancy_frames"]["summary"] = occ_summary(rows)
    rf.write_text(json.dumps(r, indent=1)); j["result"] = r; j["summary"] = _summary(r); _save(j)
    threading.Thread(target=_closeup_rebuild, args=(j,), daemon=True).start()
    return {"ok": True, "devices": len(r["devices"])}


def _renumber(o, k, n):
    """Every 'D<k>' in the strings of a device record (labels, section and layout file names) becomes 'D<n>'."""
    import re
    pat = re.compile(rf"\bD{k}(?=[\s_.·]|$)")
    if isinstance(o, dict):
        for key, v in o.items():
            o[key] = pat.sub(f"D{n}", v) if isinstance(v, str) else (_renumber(v, k, n) or v)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            o[i] = pat.sub(f"D{n}", v) if isinstance(v, str) else (_renumber(v, k, n) or v)
    return o


def occ_summary(rows):
    import occupancy
    try:
        return occupancy.summarize(rows)
    except Exception as ex:  # noqa: BLE001
        print(f"[racktrack] unit summary not made: {type(ex).__name__}: {ex}", file=sys.stderr)
        return None


class ModelFix(BaseModel):
    vendor: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=40)


@app.post("/api/video/{job}/device/{index}/model")
@edits_run
def api_set_model(job: str, index: int, body: ModelFix, request: Request):
    """A person types the make and model of a switch, router, firewall or gateway (user, 2026-10-27: the reader cannot
    see the model on a 576 px video): the mapper fits that model's vendor layout to the device's crops, in the background,
    and the result is kept as typed by that person."""
    j = JOBS.get(job)
    if j is None or j.get("status") != "done":
        raise HTTPException(404, "No such finished run.")
    dv = next((d for d in (j.get("result") or {}).get("devices") or [] if d["index"] == index), None)
    if dv is None:
        raise HTTPException(404, "No such device.")
    hint = {"vendor": body.vendor.strip(), "model": body.model.strip(), "by": rt_session.name(request),
            "when": datetime.datetime.now().isoformat(timespec="seconds")}
    SDB.save_correction(_run_org(j), (j.get("rack_id") or {}).get("id"), SDB._unum(min(dv.get("units") or ["u0"], key=lambda u: SDB._unum(u) or 0)), hint["by"],
                        vendor=hint["vendor"] or None, model=hint["model"] or None)
    if SDB.device_kind(dv)["kind"] not in PORT_KINDS:
        # no reader for this kind (a patch panel, a server...): the name is kept as typed, nothing is mapped
        rf = pathlib.Path(j["dir"]) / "result.json"; r = json.loads(rf.read_text())
        for d in r["devices"]:
            if d["index"] == index:
                d["layout"] = dict(d.get("layout") or {}, typed=hint, mode="typed", ok=True)
        rf.write_text(json.dumps(r, indent=1)); j["result"] = r
        return {"state": "done"}
    ports = j["ports"] = j.get("ports") or {}
    if ports.get(str(index)) == "running":
        raise HTTPException(409, "This device is being read; try again when it is done.")
    ports[str(index)] = "running"; _save(j)
    threading.Thread(target=_read_ports_thread, args=(job, index, hint), daemon=True).start()
    return {"state": "running"}


class KindFix(BaseModel):
    kind: str | None = Field(default=None, max_length=20)


@app.post("/api/video/{job}/device/{index}/kind")
@edits_run
def api_fix_kind(job: str, index: int, body: KindFix, request: Request):
    """The person corrects what a device is (the classifier is wrong now and then). Kept in result.json as kind_override with
    who and when; the model's own answer stays in classification. kind null removes the correction."""
    j = job_or_none(job)
    if j is None:
        raise HTTPException(404, "No such run." if j is None else "The stored demo cannot be changed.")
    if body.kind is not None and body.kind not in KINDS:
        raise HTTPException(400, "Unknown kind.")
    rf = pathlib.Path(j["dir"]) / "result.json"; r = json.loads(rf.read_text())
    dev = next((d for d in r.get("devices") or [] if d["index"] == index), None)
    if dev is None:
        raise HTTPException(404, "No such device.")
    if body.kind is None:
        dev.pop("kind_override", None)
    else:
        dev["kind_override"] = {"kind": body.kind, "by": rt_session.name(request), "when": datetime.datetime.now().isoformat(timespec="seconds")}
        SDB.save_correction(_run_org(j), (j.get("rack_id") or {}).get("id"), SDB._unum(min(dev.get("units") or ["u0"], key=lambda u: SDB._unum(u) or 0)), dev["kind_override"]["by"], kind=body.kind)
    rf.write_text(json.dumps(r, indent=1))
    if job in JOBS:
        JOBS[job]["result"] = r
    return {"ok": True, "kind": body.kind}


@app.post("/api/video/{job}/ports/{index}")
def api_read_ports(job: str, index: int):
    """Read the make, model and ports of ONE device of a finished run (OCR, agent, overlay, fallback), in the background.
    Called by the app when the user selects a device; the view shows the answer when it is there. Idempotent."""
    j = JOBS.get(job)
    if j is None:
        raise HTTPException(404, "No such run." if job_or_none(job) is None else "The stored demo cannot be re-read.")
    if j.get("status") != "done":
        raise HTTPException(409, "The run is not finished yet.")
    dv = next((d for d in (j.get("result") or {}).get("devices") or [] if d["index"] == index), None)
    if dv is None:
        raise HTTPException(404, "No such device.")
    kind = SDB.device_kind(dv)["kind"]   # the one kind (a correction, then the faceplate, then the picture)
    if kind not in PORT_KINDS and not (kind == "Patch Panel" and patch_panels.available()):
        return {"state": "none", "detail": "Not a switch, router, firewall, gateway or patch panel: no ports to read."}
    ports = j["ports"] = j.get("ports") or {}             # status.json keeps "ports": null until the first read
    st = ports.get(str(index))
    if st in ("running", "done"):
        return {"state": st}
    ports[str(index)] = "running"; _save(j)
    threading.Thread(target=_read_panel_thread if kind == "Patch Panel" else _read_ports_thread, args=(job, index), daemon=True).start()
    return {"state": "running"}


def _closeup_rebuild(j):
    try:
        video_report.build_from(j["dir"], pathlib.Path(j["dir"]) / "report", j["rack_path"], j["video_path"], j.get("title") or "Your rack", j.get("name") or j["id"])
    except Exception as ex:  # noqa: BLE001
        print(f"[racktrack] report of run {j.get('id')} not rebuilt: {type(ex).__name__}: {ex}", file=sys.stderr)


@app.post("/api/closeup")
async def api_closeup(request: Request, file: UploadFile = File(...), job: str | None = Form(None), device: int | None = Form(None),
                      ids_only: str | None = Form(None)):    # wt/features: ids_only reads the serial and asset tag and leaves the port layout alone
    """A close-up of one device's front (from the phone camera or the gallery): make, model, layout and ports are read
    from it; with a run and a device number the answer is kept with that device and the run's report is rebuilt."""
    import cv2
    raw = await file.read(MAX_PHOTO + 1)
    if len(raw) > MAX_PHOTO:
        raise HTTPException(413, "The photo is too large (25 MB at most).")
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    except (UnidentifiedImageError, OSError):
        raise HTTPException(400, "That file is not a photo.")
    cid = hashlib.sha1(raw).hexdigest()[:12]
    jd = job_or_none(job) if job else None
    from starlette.concurrency import run_in_threadpool
    if jd and device and jd.get("rack_path") and pathlib.Path(jd["rack_path"]).exists():
        work = pathlib.Path(jd["dir"]) / "closeups" / f"D{device}"
        ids = await run_in_threadpool(_features_closeup_ids, jd, device, raw, cid, rt_session.name(request))   # wt/features: serial, asset tag, labels (kept first)
        if ids_only:                                       # a photo of the label, not of the faceplate: its ports are not read from it
            return {"id": cid, "ok": bool(ids and ids.get("ok")), "ids_only": True, "shows_device": True, "ids": ids}
        rf = pathlib.Path(jd["dir"]) / "result.json"; r = json.loads(rf.read_text())
        switches = {dv["index"]: {"polygon": dv["polygon"], "units": dv["units"], "kind": (dv.get("layout") or {}).get("kind")}
                    for dv in r.get("devices") or [] if dv.get("polygon")}
        mapped = {dv["index"] for dv in r.get("devices") or [] if dv.get("layout")}
        rack = cv2.imread(str(jd["rack_path"]))
        with MODEL_LOCK:
            lays = await run_in_threadpool(rack_video.closeup_layouts, img, rack, switches, work, device, mapped)
        with run_lock(jd["id"]):                          # merged into the result as it is now
            r = json.loads(rf.read_text())
            for dv in r.get("devices") or []:
                if dv["index"] in lays:
                    dv["closeup"] = rack_video.closeup_record(lays[dv["index"]], jd["dir"], dv["index"])
            rf.write_text(json.dumps(r, indent=1))
        if job in JOBS:
            JOBS[job]["result"] = r
        threading.Thread(target=_closeup_rebuild, args=(jd,), daemon=True).start()
        mine = next((dv["closeup"] for dv in r["devices"] if dv["index"] == device and device in lays), None)
        if mine is None:
            return {"id": cid, "ok": False, "shows_device": False, "also": sorted(lays), "saved_to_report": bool(lays),
                    "detail": f"This photo does not show D{device}." + (f" It was kept for {', '.join(f'D{i}' for i in sorted(lays))}." if lays else ""), "ids": ids}
        with STATE_LOCK:
            CLOSEUPS[cid] = {"pictures": {k: str(pathlib.Path(jd["dir"]) / f) for k, f in mine["pictures"].items()}}
        return dict(mine, id=cid, ok=True, shows_device=True, pictures={k: f"/api/closeup/{cid}/{k}" for k in mine["pictures"]},
                    also=sorted(i for i in lays if i != device), saved_to_report=True, ids=ids)
    work = pathlib.Path(tempfile.gettempdir()) / "racktrack_closeups" / cid
    with MODEL_LOCK:                                      # no run given: the photo is read as a switch
        res = await run_in_threadpool(pipe.agent_layouts, [{"key": "closeup", "image": img, "origin": (0, 0), "kind": "switch"}], work)
        lay = res.get("closeup") or {"ok": False, "error": "no result"}
        lay["fitted"] = fitted = lay.get("mode") == "template"; lay["auto"] = auto = lay.get("mode") == "auto"; used = fitted or auto
        await run_in_threadpool(pipe.add_port_details, lay, img, work, "closeup")
    pics = {k: v for k, v in (lay.get("pictures") or {}).items() if (used and k not in ("trace", "overlay")) or k in ("cut", "portmap")}
    with STATE_LOCK:
        CLOSEUPS[cid] = {"pictures": pics}
    return {"id": cid, "ok": lay.get("ok", False), "vendor": lay.get("vendor") if used else None, "model": lay.get("model") if fitted else None, "fitted": fitted, "auto": auto,
            "status": lay.get("status"), "confidence": lay.get("identify_confidence"), "needs_back": bool(lay.get("needs_back")),
            "n_ports": lay.get("n_ports") if used else 0, "by_type": lay.get("by_type") if used else {}, "connected": lay.get("connected") if used else 0,
            "pictures": {k: f"/api/closeup/{cid}/{k}" for k in pics}, "port_details": {k: v for k, v in (lay.get("port_details") or {}).items() if k != "picture"}}


@app.get("/api/closeup/{cid}/{name}")
def api_closeup_picture(cid: str, name: str):
    with STATE_LOCK:
        c = CLOSEUPS.get(cid)
    if c is None or name not in c["pictures"]:
        raise HTTPException(404, "No such close-up picture.")
    return Response(jpeg_bytes(Image.open(c["pictures"][name]).convert("RGB")), media_type="image/jpeg")


@app.get("/")
def index():
    return RedirectResponse("/app/")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(MOBILE / "manifest.webmanifest", media_type="application/manifest+json", headers=NO_CACHE)


@app.get("/sw.js")
def service_worker():
    return FileResponse(MOBILE / "sw.js", media_type="application/javascript", headers=NO_CACHE)


@app.get("/favicon.ico")
def favicon():
    return FileResponse(MOBILE / "icons" / "icon-192.png", media_type="image/png")


# ------------------------------------------------------------------ the site's database and the drift (engine/source_db.py)
@app.get("/api/sources")
def api_sources():
    """What has been copied in from each source: its racks, device counts and when."""
    return {"sources": SDB.SOURCES, "racks": SDB.racks()}


@app.post("/api/sources/excel")
async def api_sources_excel(workbook: UploadFile = File(...)):
    """The manager's source-of-truth workbook (.xlsx): read and checked, nothing imported yet. The answer is the validation
    report (every problem, with counts) and the upload's name for the Import step (RT-EOS's matrix upload, then import)."""
    if not (workbook.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "Send the manager's workbook as an .xlsx file.")
    d = _uploads(); d.mkdir(parents=True, exist_ok=True)
    f = d / ("workbook_" + secrets.token_hex(4) + "_" + re.sub(r"[^A-Za-z0-9_.-]", "_", workbook.filename)[-80:])
    f.write_bytes(await workbook.read(50 * 1024 * 1024))
    try:
        report = SDB.validate_excel(f)
    except Exception as e:                      # a broken or foreign file: plain words on screen, the detail in the log
        f.unlink(missing_ok=True)
        print(f"[racktrack] workbook {workbook.filename} not read: {type(e).__name__}: {e}", file=sys.stderr)
        raise HTTPException(400, "The workbook could not be read. Send the manager's source-of-truth workbook (.xlsx) with its RACK_INVENTORY and DEVICE_INVENTORY sheets.")
    report["upload"] = f.name
    return report


@app.post("/api/sources/excel/import")
def api_sources_excel_import(body: dict = Body(...)):
    """Import a workbook already read and checked (its upload name from POST /api/sources/excel) as a new Excel copy."""
    name = str(body.get("upload") or "")
    f = _uploads() / name
    if not re.fullmatch(r"workbook_[0-9a-f]{8}_[A-Za-z0-9_.-]+\.xlsx", name) or not f.is_file():
        raise HTTPException(404, "That upload is not here any more. Read the workbook again.")
    try:
        return SDB.import_excel(f)
    except Exception as e:
        print(f"[racktrack] workbook {name} not imported: {type(e).__name__}: {e}", file=sys.stderr)
        raise HTTPException(400, "The workbook could not be imported. Read it again and check the report first.")


@app.get("/api/sources/copies")
def api_sources_copies():
    """Every copy of every source (version, time, racks, devices, ports), newest first, the active ones marked."""
    return {"sources": SDB.SOURCES, "copies": SDB.copies()}


@app.delete("/api/sources/copies/{cid}")
def api_sources_copy_delete(cid: int):
    if not SDB.delete_copy(cid):
        raise HTTPException(404, "No such copy.")
    return {"sources": SDB.SOURCES, "copies": SDB.copies()}


@app.post("/api/sources/{source}/import")
def api_sources_import(source: str):
    """Copy NetBox or the ServiceNow CMDB in (today: the local mocks)."""
    fn = {"netbox": SDB.import_netbox, "cmdb": SDB.import_cmdb}.get(source)
    if fn is None:
        raise HTTPException(404, "No such source.")
    try:
        return fn(_org())
    except (ConnectionError, ValueError, KeyError) as e:
        raise HTTPException(502, str(e))


# ------------------------------------------------------------------ the logins vault (engine/connections.py): per organization
def _run_org(j):
    """The organization a run belongs to, for what people confirmed on it (kept in that organization's copies file)."""
    return str((j or {}).get("org") or "default")


def _org():
    """racktrack-app sign-in: the signed-in person's organization ('platform' for the platform owner)."""
    return rt_session.org_key(rt_session.current())


def _uploads():
    """Workbook uploads, one folder per organization, so one organization cannot import another's upload."""
    return DATA / "sources" / "orgs" / _org()



@app.get("/api/connections")
def api_connections():
    """The organization's NetBox and ServiceNow logins: address and state only, never a secret."""
    return {"connections": CONN.meta(_org())}


@app.post("/api/connections/{kind}")
def api_connections_save(kind: str, request: Request, body: dict = Body(...)):
    if kind not in CONN.TYPES:
        raise HTTPException(404, "No such connection.")
    try:
        CONN.save(_org(), kind, str(body.get("address") or ""), str(body.get("username") or ""), str(body.get("password") or ""), str(body.get("token") or ""),
                  by=rt_session.name(request))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"connections": CONN.meta(_org())}


@app.post("/api/connections/{kind}/test")
def api_connections_test(kind: str):
    if kind not in CONN.TYPES:
        raise HTTPException(404, "No such connection.")
    try:
        CONN.test(_org(), kind, SDB._get)
    except LookupError as e:
        raise HTTPException(400, str(e))
    return {"connections": CONN.meta(_org())}


@app.delete("/api/connections/{kind}")
def api_connections_remove(kind: str):
    if kind not in CONN.TYPES:
        raise HTTPException(404, "No such connection.")
    CONN.remove(_org(), kind)
    return {"connections": CONN.meta(_org())}


def _copies_of(j):
    """The organization whose database copies a run is compared with: its own (the platform's for a run from before sign-in)."""
    return str(j["org"]) if j.get("org") is not None else "platform"


def drift_view(j):
    """The run against its record, in the run's own organization's copies, whoever asks (Home as the platform owner included)."""
    tok = SDB.ORG.set(_copies_of(j))
    try:
        return _drift_view(j)
    finally:
        SDB.ORG.reset(tok)


def _drift_view(j):
    """The run against its record: the chosen source, the matched rack (or the one a person picked), and the drift."""
    rid = (j.get("rack_id") or {}).get("id")
    have = SDB.racks()
    db = j.get("db") or {}
    source = db.get("source") or next((s for s in SDB.SOURCES if any(r["source"] == s for r in have)), None)
    out = {"rack_id": rid, "source": source, "sources": SDB.SOURCES, "imported": {s: next((r["imported_at"] for r in have if r["source"] == s), None) for s in SDB.SOURCES},
           "candidates": [r for r in have if r["source"] == source], "match": None, "record": [], "drift": None}
    if not source:
        return out
    out["label"] = rack_label_of(j)
    out["rack_id_how"] = (j.get("rack_id") or {}).get("source")
    result = SDB.with_corrections(_run_org(j), rid, j.get("result"))
    out["identify"] = SDB.identify(source, rid, result, j.get("position"), out["label"], out["rack_id_how"], SDB.confirmed(_run_org(j), rid, source),
                                   sites=_features_sites(j))     # wt/features: the sites' positions for step 1     # the ladder: location, label, device labels, layout
    if db.get("rack_key") and db.get("source") == source and any(r["rack_key"] == db["rack_key"] for r in out["candidates"]):
        out["match"] = {"rack_key": db["rack_key"], "how": "picked", "why": f"Picked by {db.get('by') or 'a person'}."}
    elif out["identify"]["rack_key"]:
        k = out["identify"]["rack_key"]
        out["match"] = (SDB.match(source, rid) if out["identify"]["named_by"] == 2 else
                        {"rack_key": k, "how": "confirmed", "why": out["identify"]["steps"][0]["proof"]} if out["identify"]["named_by"] == 0 else
                        {"rack_key": k, "how": "ladder", "why": out["identify"]["words"]})
    out["chosen"] = bool(db.get("source"))
    out["confirmed"] = bool(db.get("confirmed")) and db.get("source") == source
    out["auto"] = bool(db.get("auto")) and out["confirmed"]          # accepted by itself (identified for certain), not by a person
    if out["match"]:
        out["record"] = SDB.devices(source, out["match"]["rack_key"])
        out["drift"] = SDB.drift(result, out["record"], SDB.ports(source, out["match"]["rack_key"]))      # every source's copy holds its ports now
    return out


@app.get("/api/video/{job}/rack-label.jpg")
def api_rack_label_strip(job: str):
    """The proof of the rack id: the strip of the stitched picture above the first unit, where the label reader looks first
    (engine/rack_label.py read_rack_label)."""
    j = job_or_none(job)
    if j is None or j.get("result") is None:
        raise HTTPException(404, "No such run.")
    rows = ((j["result"].get("occupancy_frames") or {}).get("rows")) or []
    f = pathlib.Path(j["dir"]) / "rack.png"
    if not rows or not f.exists():
        raise HTTPException(404, "No label strip for this run.")
    img = Image.open(f).convert("RGB"); y1 = int(rows[0]["box"][1])
    return Response(jpeg_bytes(img.crop((0, max(0, y1 - 220), img.size[0], min(img.size[1], y1 + 30)))), media_type="image/jpeg", headers={"Cache-Control": "max-age=300"})


@app.get("/api/video/{job}/drift")
def api_video_drift(job: str):
    j = job_or_none(job)
    if j is None or j.get("result") is None:
        raise HTTPException(404, "No such run.")
    return drift_view(j)


@app.post("/api/video/{job}/database")
def api_video_database(job: str, request: Request, body: dict = Body(...)):
    """Choose the source for this run's rack, and optionally the record (rack_key) instead of the automatic match."""
    j = job_or_none(job)
    if j is None or j.get("result") is None:
        raise HTTPException(404, "No such run.")
    source = body.get("source")
    if source not in SDB.SOURCES:
        raise HTTPException(400, "Choose Excel, NetBox or CMDB.")
    if body.get("rack_key") and not any(r["rack_key"] == body["rack_key"] for r in SDB.racks(source)):
        raise HTTPException(400, f"No record called {body['rack_key']} in {SDB.SOURCES[source]}.")
    auto = bool(body.get("auto")) and not body.get("rack_key")
    if auto:          # the owner's rule (3 Nov 2026): a rack identified for certain is accepted without the confirm step
        prev = j.get("db")
        j["db"] = {"source": source, "rack_key": None, "confirmed": False}
        ident = (drift_view(j).get("identify") or {})
        if not ident.get("certain"):
            j["db"] = prev
            raise HTTPException(409, "The rack is not certain enough to accept by itself. Confirm it or pick another.")
    if body.get("confirmed") and body.get("rack_key") and not (j.get("rack_id") or {}).get("id"):   # a run with no rack id keeps the record a person
        j["rack_id"] = {"id": body["rack_key"], "source": "picked", "on": datetime.datetime.now().isoformat(timespec="minutes")}   # confirmed as its id, so a reopen remembers it
    j["db"] = {"source": source, "rack_key": body.get("rack_key") or None, "confirmed": bool(body.get("confirmed")) or auto,
               "by": rt_session.name(request) + (" (accepted by itself: identified for certain)" if auto else ""),
               "auto": auto, "on": datetime.datetime.now().isoformat(timespec="minutes")}
    _save(j)
    dv = drift_view(j)
    if (body.get("confirmed") or auto) and dv.get("match"):        # kept for the next scan of this rack (step 0 of the identification)
        before = SDB.confirmed(_run_org(j), dv["rack_id"], source) if auto else None
        if not (before and before["rack_key"] == dv["match"]["rack_key"]):   # an acceptance by itself never overwrites an earlier one of the same record (a person's stays)
            SDB.confirm_rack(_run_org(j), dv["rack_id"], source, dv["match"]["rack_key"], j["db"]["by"])
    return dv


# ====================================================================== wt/features: the remaining RT-EOS functions on a run (engine/rt_features.py)
# Serials and asset tags from close-ups, the phone's position and the sites', the cable checks, find a port, connections, worklist,
# read again, the physical layer document, SFP advice and the topology. Every route below is under /api/video/<run>/ (the run guard
# applies) or keeps to the signed-in person's organization.
import rt_features as RTF  # noqa: E402

CHECKS = DATA / "checks"


def _features_float(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _features_position(lat, lng, accuracy=None):
    """The phone's position when the scan started (optional; it only narrows the racks), with its accuracy in metres."""
    if lat is None or lng is None or not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return {"lat": lat, "lng": lng, "accuracy": accuracy if accuracy is not None and accuracy >= 0 else None}


def _features_keep_position(job, lat, lng, accuracy):
    j = JOBS.get(job)
    pos = _features_position(lat, lng, accuracy)
    if j is not None and pos:
        j["position"] = pos; _save(j)


def _features_sites(j):
    """The run's own site and its organization's other sites with their positions (the sign-in service's tenants, read only)."""
    try:
        return RTF.site_positions(rt_session._setting("RACKTRACK_AUTH_DB"), j.get("org"), j.get("site"))
    except Exception as ex:  # noqa: BLE001  no sites, no location step from them
        print(f"[racktrack] site positions not read: {type(ex).__name__}: {ex}", file=sys.stderr)
        return None, []


def _features_ids_of(d):
    x = d.get("ids") or {}
    return {"serial": x.get("serial") or d.get("serial"), "asset_tag": x.get("asset_tag") or d.get("asset_tag"), "labels": x.get("labels") or [],
            "cables": x.get("cables") or [], "from": x.get("from"), "by": x.get("by"), "when": x.get("when"), "picture": x.get("picture")} if (x or d.get("serial") or d.get("asset_tag")) else None


def _features_closeup_ids(jd, device, raw, cid, by):
    """RT-EOS's close-up OCR on the photo a person took for one device: its serial, asset tag, device labels and cable labels are kept on
    that device (the person named the device), the run's result is written and the next scan of the rack is told by the drift."""
    folder = pathlib.Path(jd["dir"]) / "closeups" / f"D{device}"; folder.mkdir(parents=True, exist_ok=True)
    pic = folder / f"photo_{cid}.jpg"
    try:
        ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB").save(pic, quality=92)
    except (UnidentifiedImageError, OSError):
        return None
    with MODEL_LOCK:
        ids = RTF.read_closeup_ids(pic)
    with run_lock(jd["id"]):
        rf = pathlib.Path(jd["dir"]) / "result.json"; r = json.loads(rf.read_text())
        dv = next((x for x in r.get("devices") or [] if x["index"] == device), None)
        if dv is None:
            return dict(ids, kept=False)
        kept = RTF.keep_ids(dv, ids, str(pic.relative_to(jd["dir"])), by)
        rf.write_text(json.dumps(r, indent=1))
    if jd["id"] in JOBS:
        JOBS[jd["id"]]["result"] = r
    return dict(kept, kept=True, ok=ids.get("ok"), error=ids.get("error"))


def _features_job(job):
    j = job_or_none(job)
    if j is None or j.get("result") is None:
        raise HTTPException(404, "No such run.")
    return j


def _features_record(j):
    """The run's record in its own organization's Excel copy (the matrix lives there)."""
    tok = SDB.ORG.set(_copies_of(j))
    try:
        return _features_record_in(j)
    finally:
        SDB.ORG.reset(tok)


def _features_record_in(j):
    """The run's record in the Excel copy (the matrix lives there): the rack matched by the run's rack id, its devices and its cables."""
    rid = (j.get("rack_id") or {}).get("id")
    db = j.get("db") or {}
    m = SDB.match("excel", rid) if rid else None
    key = db.get("rack_key") if db.get("source") == "excel" and db.get("rack_key") else (m or {}).get("rack_key")
    result = SDB.with_corrections(_run_org(j), rid, j["result"])
    if not key:
        return rid, None, result, [], []
    return rid, key, result, SDB.devices("excel", key), RTF.cables_for([key])


@app.get("/api/video/{job}/cables")
def api_features_cables(job: str):
    """The Excel A_Z_CONNECTION_MATRIX rows of this rack against the ports the video read: missing cable, extra cable, wrong port."""
    j = _features_job(job)
    rid, key, result, record, cables = _features_record(j)
    if not key:
        return {"rack_id": rid, "rack_key": None, "rows": [], "cables": 0, "words": "No Excel copy holds this rack. Copy the workbook in under Profile > Database copies."}
    out = RTF.cable_check(result, record, cables, key)
    return dict(out, rack_id=rid, rack_key=key, words=f"{RTF.SDB.pl(len(cables), 'cable')} of the matrix have an end in {key}." if cables else "The matrix has no cable in this rack.")


@app.post("/api/cables/az")
def api_features_az(request: Request, body: dict = Body(...)):
    """The A-end / Z-end check between two runs (the same run at both ends is allowed), kept per organization; its CSV below."""
    a, z = job_or_none(str(body.get("a") or "")), job_or_none(str(body.get("z") or body.get("a") or ""))
    if a is None or z is None or a.get("result") is None or z.get("result") is None:
        raise HTTPException(404, "No such run.")
    views, keys, port_rows = {}, [], []
    for side, j in (("a", a), ("z", z)):
        rid, key, result, record, rack_cables = _features_record(j)
        views[side] = dict(RTF.run_view(result, record, key or rid), scanId=j["id"]); keys.append(key or rid)
        if key and rack_cables and (side == "a" or z is not a):
            port_rows += RTF.cable_check(result, record, rack_cables, key)["rows"]     # the same verdict as the Cables list above
    tok = SDB.ORG.set(_copies_of(a))
    try:
        cables = RTF.cables_for(keys)
    finally:
        SDB.ORG.reset(tok)
    if not cables:
        raise HTTPException(409, "The Excel matrix has no cable in these racks. Copy the workbook with its A_Z_CONNECTION_MATRIX in first.")
    check = RTF.az_check(views["a"], views["z"], cables, rt_session.name(request), port_check=port_rows)
    d = CHECKS / rt_session.org_key(rt_session.current()); d.mkdir(parents=True, exist_ok=True)
    (d / f"{check['id']}.json").write_text(json.dumps(check, indent=1))
    return check


@app.get("/api/cables/az/{cid}.csv")
def api_features_az_csv(cid: str):
    if not re.fullmatch(r"[a-f0-9]{8,16}", cid):
        raise HTTPException(404, "No such check.")
    f = CHECKS / rt_session.org_key(rt_session.current()) / f"{cid}.json"
    if not f.exists():
        raise HTTPException(404, "No such check.")
    check = json.loads(f.read_text())
    return Response(RTF.az_csv(check), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="az-check-{cid}.csv"'})


@app.get("/api/video/{job}/find-port")
def api_features_find_port(job: str, device: int, port: int):
    j = _features_job(job)
    rid, key, result, record, cables = _features_record(j)
    out = RTF.find_port(result, record, cables, key, device, port)
    if out is None:
        raise HTTPException(404, "No such device.")
    if port < 1 or (out.get("ports") and port > max(out["ports"])):
        raise HTTPException(404, f"D{device} has no port {port}.")
    return dict(out, picture=f"/api/video/{job}/find-port.jpg?device={device}&port={port}")


@app.get("/api/video/{job}/find-port.jpg")
def api_features_find_port_jpg(job: str, device: int, port: int):
    """The rack picture around the device with the port marked (a ring and an arrow), and the device outlined."""
    from PIL import ImageDraw
    j = _features_job(job)
    rid, key, result, record, cables = _features_record(j)
    fp = RTF.find_port(result, record, cables, key, device, port)
    if fp is not None and fp.get("placed") == "crop":                      # the device's own crop with the port boxed: the only place this port was read
        cf = pathlib.Path(j["dir"]) / pathlib.Path(fp["crop_file"]).name
        if cf.exists():
            img = Image.open(cf).convert("RGB"); dr = ImageDraw.Draw(img); x0, y0, x1, y1 = fp["crop_box"]; m = 3
            dr.rectangle([x0 - m, y0 - m, x1 + m, y1 + m], outline=(220, 38, 38), width=max(2, img.size[0] // 220))
            sc = max(1, 900 // img.size[0]); img = img.resize((img.size[0] * sc, img.size[1] * sc), Image.LANCZOS) if sc > 1 else img
            return Response(jpeg_bytes(img), media_type="image/jpeg", headers={"Cache-Control": "no-store"})
    f = pathlib.Path(j["dir"]) / "rack.png"
    if fp is None or not f.exists() or not fp.get("device_box"):
        raise HTTPException(404, "No picture for this port.")
    img = Image.open(f).convert("RGB"); W, H = img.size
    dx0, dy0, dx1, dy1 = fp["device_box"]; pad = max(40, (dy1 - dy0) * 1.2)
    x0, y0, x1, y1 = 0, max(0, int(dy0 - pad)), W, min(H, int(dy1 + pad))
    dr = ImageDraw.Draw(img)
    dr.rectangle([dx0, dy0, dx1, dy1], outline=(37, 99, 235), width=3)
    if fp.get("placed") == "rack":
        poly = [tuple(q) for q in fp["read"]["rack_polygon"]]; bx = fp["box"]; cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
        dr.line(poly + [poly[0]], fill=(220, 38, 38), width=4)               # the port itself outlined, from the polygon the port finder placed
        rr = max(18, (bx[3] - bx[1]) * 1.4)
        dr.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=(220, 38, 38), width=3)
    return Response(jpeg_bytes(img.crop((x0, y0, x1, y1))), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/video/{job}/connections")
def api_features_connections(job: str):
    j = _features_job(job)
    rid, key, result, record, cables = _features_record(j)
    drift = SDB.drift(result, record, SDB.ports("excel", key)) if key else {"rows": []}
    cab = RTF.cable_check(result, record, cables, key) if key else {"rows": []}
    return {"rack_id": rid, "rack_key": key, "devices": RTF.connections(result, record, drift["rows"], cab["rows"])}


@app.get("/api/video/{job}/sfp-advice")
def api_features_sfp(job: str):
    j = _features_job(job)
    rid, key, result, record, cables = _features_record(j)
    return RTF.sfp_advice(result, record, cables)


@app.exception_handler(StarletteHTTPException)          # also the router's own 404 and 405, so every error has the same shape
async def http_error(_, exc: StarletteHTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def bad_request(_, exc: RequestValidationError):
    """A malformed request answers one sentence in the app's own shape, not FastAPI's list (verify pass 2, finding 34)."""
    names = sorted({str(e.get("loc", ["", ""])[-1]) for e in exc.errors() if not isinstance(e.get("loc", ["", ""])[-1], int)})   # not a JSON position
    return JSONResponse({"error": "This request is not complete or has a wrong value" + (f" ({', '.join(names)})." if names else ".")}, status_code=400)


@app.get("/logo.jpg")
def logo_jpg():
    """RT-EOS's components ask for /logo.jpg (the Analysing screen's mark)."""
    return FileResponse(MOBILE / "img" / "logo.jpg", headers={"Cache-Control": "max-age=86400"})


app.mount("/icons", StaticFiles(directory=MOBILE / "icons"), name="icons")
app.mount("/app", StaticFiles(directory=MOBILE, html=True), name="app")

load_runs()

if __name__ == "__main__":
    print(f"[racktrack] RackTrack server on http://{HOST}:{PORT}  (this Mac: http://{socket.gethostbyname(socket.gethostname()) if HOST == '0.0.0.0' else HOST}:{PORT}/app/)")
    cert, key = os.environ.get("RACKTRACK_CERT"), os.environ.get("RACKTRACK_KEY")      # https: phones allow the camera only on https (or localhost)
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning", ssl_certfile=cert or None, ssl_keyfile=key or None)
