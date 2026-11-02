"""Takes recorded with the phone capture guide (mobile/capture/index.html, the team's Rack Sweep Guide).

A take is one folder holding video.mp4 (or video.webm) and log.json, named <UTC time>__<tester>. Takes come from two
places, listed together in Rack Sweep:
    app    data/captures/, written by this app's /capture/upload (the guide page served by this app)
    team   the team's capture server's folder (RACKTRACK_TEAM_CAPTURES, default the shared Rack Sweep Guide folder)

save_upload() follows the guide's own server (nocache_server.py) exactly: the same names, the same file allow-list and
size limit, and long videos in pieces appended in order (a piece whose offset does not match what is on disk is
refused, so a gap or a repeat can never corrupt a video; piece 0 starts the file afresh, which is what a retry does).
check_rows() gives the same five checks, limits and wording as the guide's result screen.
"""
import datetime
import json
import os
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCES = {"app": ROOT / "data" / "captures",
           "team": pathlib.Path(os.environ.get("RACKTRACK_TEAM_CAPTURES", "/Volumes/Sharing_folder/rack-sweep-guide/captures"))}
ALLOWED_FILES = {"video.mp4", "video.webm", "log.json"}
MAX_UPLOAD = 500 * 1024 * 1024          # bytes; a 90 s sweep at 8 Mbit/s is about 90 MB
FOLDER_RE = re.compile(r"[A-Za-z0-9_-]+")


class UploadError(Exception):
    def __init__(self, status, text):
        super().__init__(text)
        self.status = status


def clean_take(take):
    return re.sub(r"[^A-Za-z0-9_-]", "", take or "")[:40]


def clean_tester(tester):
    return re.sub(r"[^A-Za-z0-9 _-]", "", tester or "unknown").strip()[:40] or "unknown"


def upload_target(take, tester, name, length, parts=1, index=0, offset=0):
    """Check one upload request as the guide's server does; returns (folder, part file, final file)."""
    take, tester = clean_take(take), clean_tester(tester)
    if not take or name not in ALLOWED_FILES:
        raise UploadError(400, "bad upload request")
    if length <= 0:
        raise UploadError(411, "missing length")
    if length > MAX_UPLOAD:
        raise UploadError(413, "file too large")
    if not (1 <= parts <= 1000 and 0 <= index < parts and offset >= 0):
        raise UploadError(400, "bad part numbers")
    folder = SOURCES["app"] / f"{take}__{tester.replace(' ', '_')}"
    folder.mkdir(parents=True, exist_ok=True)
    part = folder / (name + ".part")
    if index > 0 and (not part.is_file() or part.stat().st_size != offset):
        raise UploadError(409, "piece out of order - send the take again")
    return folder, part, folder / name


def take_dir(source, folder):
    """The folder of one take, or None when the names are not a real take."""
    if source not in SOURCES or not FOLDER_RE.fullmatch(folder or ""):
        return None
    d = SOURCES[source] / folder
    return d if d.is_dir() else None


def read_take(source, d):
    """One take: who, when, its log and its video."""
    stamp, _, tester = d.name.partition("__")
    log = None
    if (d / "log.json").is_file():
        try:
            log = json.loads((d / "log.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log = None
    video = next((v for v in ("video.mp4", "video.webm") if (d / v).is_file()), None)
    return {"source": source, "folder": d.name, "stamp": stamp, "tester": tester.replace("_", " ") or "unknown", "log": log,
            "video": video, "video_mb": round((d / video).stat().st_size / 1e6, 1) if video else 0.0}


def take_time(t):
    """When the take was recorded (local time), from its log or else its folder's UTC stamp."""
    iso = (t["log"] or {}).get("startedAt")
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except (ValueError, AttributeError):
        pass
    try:
        return datetime.datetime.strptime(t["stamp"], "%Y-%m-%dT%H-%M-%S").replace(tzinfo=datetime.timezone.utc).astimezone()
    except ValueError:
        return None


def check_rows(lg):
    """The guide's five checks, with its limits and wording: [(name, good|warn|bad, text)]."""
    secs = lg.get("issueSeconds") or {}
    dur = float(lg.get("durationSec") or 0)
    t = lambda k: float(secs.get(k) or 0)
    rows = []
    f, st = t("fast"), t("stopped")
    if f == 0 and st == 0:
        rows.append(("Speed", "good", "Steady"))
    elif f >= st:
        rows.append(("Speed", "bad" if f > 1.5 else "warn", "%.1fs too fast" % f))
    else:
        rows.append(("Speed", "warn", "%.1fs paused" % st))
    u = t("up")
    rows.append(("Direction", "bad" if u > 0.5 else "warn" if u > 0 else "good", "%.1fs moved up" % u if u else "Down only"))
    near, side = t("near"), t("side")
    d = near + side
    rows.append(("Distance", "bad" if d > 1.5 else "warn" if d > 0 else "good",
                 "%.1fs %s" % (d, "too close" if near >= side else "off-centre") if d else "Steady & centred"))
    p, r, b = t("pitch"), t("roll"), t("blur")
    blur_bad = b > dur * 0.10
    tier = "bad" if (p > 2 or r > 2 or blur_bad) else "warn" if (p + r + b) > 0 else "good"
    rows.append(("Steadiness", tier, "Level & sharp" if tier == "good" else "Blurry footage" if blur_bad
                 else "%.1fs tilted" % (p + r) if (p + r) > 0 else "%.1fs blurry" % b))
    light = {"too dark": t("dark"), "too bright": t("bright"), "changing light": t("exposure"), "low detail": t("texture")}
    total = sum(light.values())
    rows.append(("Lighting", "bad" if total > dur * 0.15 else "warn" if total > 0 else "good",
                 "%.1fs %s" % (total, max(light, key=light.get)) if total else "Good"))
    return rows


def public(t):
    """A take as the app lists it."""
    lg = t["log"] or {}
    state = "incomplete" if (t["log"] is None or not t["video"]) else ("passed" if lg.get("verdict") == "ok" else "reshoot")
    reasons = list(lg.get("reasons") or [])
    if t["log"] is None:
        reasons = ["The log did not arrive, so the checks cannot be shown. The upload may have been cut off."]
    elif not t["video"]:
        reasons.append('The video did not arrive. The page may have been closed before "Sent to team".')
    when = take_time(t)
    return {"id": f'{t["source"]}/{t["folder"]}', "source": t["source"], "folder": t["folder"], "tester": t["tester"],
            "time": when.isoformat() if when else None, "when": when.strftime("%d %b, %H:%M") if when else "",
            "state": state, "duration_s": lg.get("durationSec"), "corrections": lg.get("issueCount"),
            "checks": [{"name": n, "tier": k, "text": x} for n, k, x in check_rows(lg)] if "issueSeconds" in lg else [],
            "reasons": reasons, "video": t["video"], "video_mb": t["video_mb"],
            "camera": lg.get("camera"), "motion": lg.get("motionAvailable")}


def list_takes():
    """Every take from both places, newest first."""
    out = []
    for source, base in SOURCES.items():
        try:
            folders = [d for d in base.iterdir() if d.is_dir() and FOLDER_RE.fullmatch(d.name)] if base.is_dir() else []
        except OSError:                        # the shared folder is not mounted
            folders = []
        out += [read_take(source, d) for d in folders]
    out.sort(key=lambda t: take_time(t) or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), reverse=True)
    return [public(t) for t in out]
