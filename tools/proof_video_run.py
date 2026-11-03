"""One fresh video run end to end, compared with the stored run of the same video.

    tools/proof_video_run.py [--engine http://127.0.0.1:8708] [--video samples/video/vdo1.mp4] [--user techt]

Signs in at the sign-in service (3302), uploads the video to the engine, waits for the run and the rack label read,
then compares with samples/video/run_of_vdo1: units, occupied and free counts, the devices (their units), the kind the
classifier gives from the picture, and the rack label. With --ports it also asks for every device's ports, as opening a
device does (the stored run had all of them read), and compares the kind the screens show (a correction, then what the
port read or the faceplate says, then the picture). The engine must be one whose data root has no run of this video for that person
(an upload whose bytes match a run they can open reopens it). Password from RT_PROOF_PASSWORD or
data/auth/seed-password.txt (never printed). Exit 0 when everything matches.
"""
import argparse
import json
import os
import pathlib
import sys
import time

import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def facts(res, label):
    import source_db as SDB
    s = res["occupancy_frames"]["summary"]
    return {"units": res["ladder"]["units"], "occupied": s["occupied"], "free": s["free"],
            "devices": [(d["index"], "-".join(u[1:].lstrip("0") for u in (d["top"], d["bottom"]))) for d in res["devices"]],
            "kinds from the picture": [SDB.device_kind(d)["picture"] for d in res["devices"]],
            "kinds on the screens": [SDB.device_kind(d)["kind"] for d in res["devices"]],
            "rack_label": ((label or {}).get("id") or {}).get("text")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="http://127.0.0.1:8708")
    ap.add_argument("--auth", default="http://127.0.0.1:3302")
    ap.add_argument("--video", default=str(ROOT / "samples/video/vdo1.mp4"))
    ap.add_argument("--stored", default=str(ROOT / "samples/video/run_of_vdo1"))
    ap.add_argument("--user", default="techt")
    ap.add_argument("--data", default=os.environ.get("RACKTRACK_DATA") or str(ROOT / "data"), help="the engine's data root")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--job", help="follow a run already started instead of uploading")
    ap.add_argument("--ports", action="store_true", help="read every device's ports before comparing the kinds on the screens")
    a = ap.parse_args()
    import proof_guard
    proof_guard.check(a.user)
    pw = os.environ.get("RT_PROOF_PASSWORD") or (ROOT / "data/auth/seed-password.txt").read_text().strip()
    s = requests.Session()
    r = s.post(a.auth + "/api/auth/login", json={"username": a.user, "password": pw}, timeout=30)
    r.raise_for_status()
    t0 = time.time()

    def get(path):
        for k in range(12):                   # the engine is busy with the models: a poll may be dropped, ask again
            try:
                return s.get(a.engine + path, timeout=60).json()
            except requests.ConnectionError:
                time.sleep(5)
        raise SystemExit(f"the engine did not answer {path}")
    if a.job:
        job = a.job
    else:
        job = upload(s, a)
        if job is None:
            return 1
    last = None
    while time.time() - t0 < a.timeout:
        j = get(f"/api/video/{job}")
        if j.get("progress") != last:
            last = j.get("progress"); print(f"  {int(time.time() - t0):4d} s  {last}", flush=True)
        if j.get("status") in ("done", "failed"):
            break
        time.sleep(5)
    return finish(s, a, job, j, get, t0)


def upload(s, a):
    with open(a.video, "rb") as f:
        r = s.post(a.engine + "/api/video", files={"video": (pathlib.Path(a.video).name, f, "video/mp4")}, timeout=300)
    r.raise_for_status()
    job = r.json()["job"]
    if r.json().get("reused"):
        print(f"the engine reopened run {job} (same bytes): point it at a data root without this run"); return None
    print(f"run {job} started as {a.user}", flush=True)
    return job


def finish(s, a, job, j, get, t0):
    if j.get("status") != "done":
        print("FAILED:", j.get("status"), j.get("error")); return 1
    print(f"run done in {int(time.time() - t0)} s; waiting for the rack label read")
    lab = {}
    for _ in range(120):
        v = get(f"/api/video/{job}/view")
        lab = v.get("rack_label") or {}
        if v.get("rack_label") is not None and not v.get("rack_label_running"):
            break
        time.sleep(5)
    if a.ports:
        n = len(get(f"/api/video/{job}/view").get("devices") or [])
        for i in range(1, n + 1):
            s.post(f"{a.engine}/api/video/{job}/ports/{i}", timeout=60)
        t1 = time.time()
        while time.time() - t1 < 1500:
            st = get(f"/api/video/{job}").get("ports") or {}
            if not any(v == "running" for v in st.values()):
                break
            time.sleep(10)
        print(f"port reads: {st} ({int(time.time() - t1)} s)")
    run_dir = next(pathlib.Path(a.data, "runs").glob(f"**/{job}/result.json")).parent
    res = json.loads((run_dir / "result.json").read_text())
    lab = json.loads((run_dir / "rack_label.json").read_text()) if (run_dir / "rack_label.json").exists() else lab
    stored = pathlib.Path(a.stored)
    want = facts(json.loads((stored / "result.json").read_text()), json.loads((stored / "rack_label.json").read_text()))
    got = facts(res, lab)
    ok = True
    for k in want:
        if k == "kinds on the screens" and not a.ports:
            continue
        same = want[k] == got[k]; ok &= same
        print(f"{'same' if same else 'DIFFERENT'}  {k}: stored {want[k]}" + ("" if same else f"  fresh {got[k]}"))
    print("PASS" if ok else "FAIL", f"({int(time.time() - t0)} s in all)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
