"""Run videos through the engine one after another and hand back one report per video.

    tools/run_videos.py tech1 tech2.mp4 videos/tech9.mov [--out reports/2026-11-04] [--ports] [--pdf] [--zip]
                        [--engine http://127.0.0.1:8700] [--auth http://127.0.0.1:3302] [--user techt]

Start the app first (./start.sh). A bare name is looked up in videos/. For every video, in order: upload to the engine
as the given person, wait for the run and the rack label read, read every device's ports when --ports is given, then
build the rack report and write it into the out folder as one self-contained page <name>.html with the pictures inlined,
the stitched rack picture as <name>-rack.jpg, with --pdf the report printed as <name>.pdf (headless Chromium), and with
--zip one <name>.zip holding the video, the page, the PDF and the picture. INDEX.md in the out folder gets one row per
video: rack label, units, in use, devices, run id, time, status. A video whose bytes the engine already ran for this
person reopens that run (no second run) and the row says so. A failed run gets a row with the engine's error and the
next video still runs. Password from RT_PROOF_PASSWORD or data/auth/seed-password.txt (never printed).
"""
import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import zipfile

import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

VIDEO_EXT = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm")


def find_video(name):
    p = pathlib.Path(name).expanduser()
    if p.is_file():
        return p.resolve()
    for cand in (ROOT / "videos" / name, *(ROOT / "videos" / f"{name}{e}" for e in VIDEO_EXT + tuple(e.upper() for e in VIDEO_EXT))):
        if cand.is_file():
            return cand.resolve()
    raise SystemExit(f"no such video: {name} (looked in videos/)")


def main():
    ap = argparse.ArgumentParser(description="Run videos one after another and write one report each.")
    ap.add_argument("videos", nargs="+", help="file paths, or names in videos/")
    ap.add_argument("--out", default=None, help="out folder (default reports/<today>)")
    ap.add_argument("--engine", default="http://127.0.0.1:" + os.environ.get("RACKTRACK_PORT", "8700"))
    ap.add_argument("--auth", default="http://127.0.0.1:3302")
    ap.add_argument("--user", default="techt")
    ap.add_argument("--data", default=os.environ.get("RACKTRACK_DATA") or str(ROOT / "data"), help="the engine's data root")
    ap.add_argument("--ports", action="store_true", help="read every device's ports after the run (slow, about a minute a device)")
    ap.add_argument("--pdf", action="store_true", help="also print each report to PDF")
    ap.add_argument("--zip", action="store_true", help="also pack video, page, PDF and picture into one zip per video")
    ap.add_argument("--timeout", type=int, default=2400, help="seconds to wait for one run")
    a = ap.parse_args()
    out = pathlib.Path(a.out) if a.out else ROOT / "reports" / time.strftime("%Y-%m-%d")
    out.mkdir(parents=True, exist_ok=True)
    videos = [find_video(v) for v in a.videos]

    try:
        requests.get(a.engine + "/api/health", timeout=10).raise_for_status()
    except Exception:
        raise SystemExit(f"the engine does not answer at {a.engine}: start the app with ./start.sh first")
    pw = os.environ.get("RT_PROOF_PASSWORD") or (ROOT / "data/auth/seed-password.txt").read_text().strip()
    s = requests.Session()
    r = s.post(a.auth + "/api/auth/login", json={"username": a.user, "password": pw}, timeout=30)
    if r.status_code != 200:
        raise SystemExit(f"sign-in as {a.user} refused ({r.status_code}): is ./start.sh running and the account in docs/AUTH.md?")

    def get(path):
        for _ in range(12):                   # the engine is busy with the models: a poll may be dropped, ask again
            try:
                return s.get(a.engine + path, timeout=60).json()
            except requests.ConnectionError:
                time.sleep(5)
        raise SystemExit(f"the engine did not answer {path}")

    rows = []
    t_all = time.time()
    for video in videos:
        name = video.stem
        print(f"\n== {video.name}", flush=True)
        t0 = time.time()
        row = {"video": video.name, "name": name, "status": "", "label": "", "units": "", "in_use": "", "devices": "", "run": "", "time_s": 0, "note": ""}
        rows.append(row)
        try:
            with open(video, "rb") as f:
                r = s.post(a.engine + "/api/video", files={"video": (video.name, f, "video/mp4")}, timeout=600)
            r.raise_for_status()
            job = r.json()["job"]
            row["run"] = job
            if r.json().get("reused"):
                row["note"] = "the engine already had a run of these bytes for this person; reopened, not run again"
                print("  " + row["note"], flush=True)
            last = None
            while time.time() - t0 < a.timeout:
                j = get(f"/api/video/{job}")
                if j.get("progress") != last:
                    last = j.get("progress"); print(f"  {int(time.time() - t0):4d} s  {last}", flush=True)
                if j.get("status") in ("done", "failed"):
                    break
                time.sleep(5)
            if j.get("status") != "done":
                row["status"] = "failed"; row["note"] = (row["note"] + "; " if row["note"] else "") + str(j.get("error") or j.get("status") or "timed out")
                row["time_s"] = int(time.time() - t0)
                print("  FAILED: " + row["note"], flush=True)
                write_index(out, rows, a)
                continue
            lab = {}
            for _ in range(120):              # the rack label is read after the run
                v = get(f"/api/video/{job}/view")
                lab = v.get("rack_label") or {}
                if v.get("rack_label") is not None and not v.get("rack_label_running"):
                    break
                time.sleep(5)
            if a.ports:
                n = len(v.get("devices") or [])
                for i in range(1, n + 1):
                    s.post(f"{a.engine}/api/video/{job}/ports/{i}", timeout=60)
                t1 = time.time()
                st = {}
                while time.time() - t1 < 1800:
                    st = get(f"/api/video/{job}").get("ports") or {}
                    if not any(x == "running" for x in st.values()):
                        break
                    time.sleep(10)
                print(f"  ports read: {st} ({int(time.time() - t1)} s)", flush=True)
            run_dir = next(pathlib.Path(a.data, "runs").glob(f"**/{job}/result.json")).parent
            res = json.loads((run_dir / "result.json").read_text())
            summ = res["occupancy_frames"]["summary"]
            row["label"] = ((lab or {}).get("id") or {}).get("text") or "no label read"
            row["units"] = res["ladder"]["units"]; row["in_use"] = summ.get("occupied")
            row["devices"] = len(res["devices"])
            files = write_report(run_dir, out, name, video, a)
            row["status"] = "done"; row["time_s"] = int(time.time() - t0)
            print(f"  done in {row['time_s']} s: label {row['label']}, {row['units']} units, {row['in_use']} in use, {row['devices']} devices -> {', '.join(f.name for f in files)}", flush=True)
        except Exception as e:                # one video failing must not stop the next
            row["status"] = "failed"; row["note"] = (row["note"] + "; " if row["note"] else "") + f"{type(e).__name__}: {e}"
            row["time_s"] = int(time.time() - t0)
            print("  FAILED: " + row["note"], flush=True)
        write_index(out, rows, a)
    print(f"\n{sum(r['status'] == 'done' for r in rows)} of {len(rows)} done in {int(time.time() - t_all)} s; index at {out / 'INDEX.md'}")
    return 0 if all(r["status"] == "done" for r in rows) else 1


def write_report(run_dir, out, name, video, a):
    import video_report
    st = json.loads((run_dir / "status.json").read_text()) if (run_dir / "status.json").exists() else {}
    vid = next((p for p in run_dir.glob("video*") if p.suffix.lower() in (".mp4", ".mov")), None)
    video_report.build_from(run_dir, run_dir / "report", run_dir / "rack.png", vid, st.get("title") or "Your rack", st.get("name") or run_dir.name)
    page = out / f"{name}.html"
    video_report.self_contained(run_dir / "report", page)
    files = [page]
    rack = out / f"{name}-rack.jpg"
    if (run_dir / "rack.png").exists():
        from PIL import Image
        Image.open(run_dir / "rack.png").convert("RGB").save(rack, "JPEG", quality=85)
        files.append(rack)
    if a.pdf:
        pdf = out / f"{name}.pdf"
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            shell = os.environ.get("PW_CHROMIUM") or next(iter(sorted(pathlib.Path.home().glob(
                "Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-mac-arm64/chrome-headless-shell"))[::-1]), None)
            kw = {"executable_path": str(shell)} if shell else {}
            b = p.chromium.launch(**kw)
            pg = b.new_page(viewport={"width": 1280, "height": 900})
            pg.goto(page.resolve().as_uri(), wait_until="load")
            # the page shows one step at a time; the PDF shows every step in order, each starting on a new page with its name
            pg.evaluate("""() => {
                document.querySelectorAll('.step').forEach((s, i) => {
                    s.hidden = false;
                    const a = document.querySelector('.rail a[href="#' + s.id + '"]');
                    const h = document.createElement('h2'); h.className = 'pdfh'; h.textContent = a ? a.textContent : (i + 1) + ''; s.prepend(h);
                });
                document.querySelectorAll('.lsw[data-sw]').forEach(c => c.hidden = false);   // every switch card, not just the picked one
                document.querySelectorAll('details').forEach(d => d.open = true);
            }""")
            pg.add_style_tag(content=".rail,.snav,.stick{display:none!important} .step{display:block!important;break-before:page} .step:first-of-type{break-before:auto} .pdfh{font-size:1.3rem;margin:0 0 12px;border-bottom:1px solid #dbe1ea;padding-bottom:6px} img{break-inside:avoid}")
            pg.emulate_media(media="print")
            pg.pdf(path=str(pdf), format="A4", print_background=True, margin={"top": "10mm", "bottom": "10mm", "left": "8mm", "right": "8mm"})
            b.close()
        files.append(pdf)
    if a.zip:
        z = out / f"{name}.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(video, video.name)
            for f in files:
                zf.write(f, f.name)
        files.append(z)
    return files


def write_index(out, rows, a):
    lines = [f"# Video runs, {time.strftime('%d %b %Y %H:%M')}", "",
             f"Run as {a.user} on the engine at {a.engine}. One row per video; the report is <name>.html beside this file.", "",
             "| Video | Status | Rack label | Units | In use | Devices | Run | Time | Note |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        lines.append(f"| {r['video']} | {r['status']} | {r['label']} | {r['units']} | {r['in_use']} | {r['devices']} | {r['run']} | {r['time_s']} s | {r['note']} |")
    (out / "INDEX.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
