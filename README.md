# RackTrack

Film a rack with a phone; the server stitches the video into one rack picture, numbers every unit, marks what is full and
free, finds and names every device, reads makes and models, and maps the ports. One application: a mobile app
(`mobile/`, installable from the browser) and a server (`server/`) that runs the engine (`src/`).

## Run it

```
pip install -r requirements.txt            # Python 3.10, tested on macOS Apple Silicon
./server/run.sh                            # http://<this machine>:8600/app/
```

There is no access code: phones on the same network open `http://<this-mac-ip>:8600/app/`, type their name,
and add the page to the home screen (Safari: Share, Add to Home Screen; Chrome:
Install app).

Phone cameras work on https only. For the camera guide on a phone: `./server/make_cert.sh` once, then `./server/run.sh --https`
and accept the certificate on the phone. Over http the app still takes videos and photos from the phone's gallery.

The engine runs one job at a time on 2 threads (`OMP_NUM_THREADS`), on purpose: this Mac overheats with more. On a server
with a GPU the same code is several times faster with no change.

`run.sh` starts the server under `caffeinate`, so the Mac does not sleep while a run is going: a closed lid otherwise
stops the run until the next wake (seen on 26 October: a 7-minute port step stretched over 85 minutes of sleep).

## What the app does

| Screen | What happens |
|---|---|
| Sign in | your name only; a signed session cookie for 30 days |
| Home | greeting, Sweep a rack, the run going now, the last report, how it works |
| History | every run on this server, newest first, with its rack picture and state |
| New sweep | the camera guide (the team's Rack Sweep Guide, inside the app) records one slow sweep from the top of the rack to the bottom and sends it; the run starts by itself. Or a video from the gallery |
| Run | the 15 steps with the one running, the elapsed time; the run keeps going if the phone leaves; a finished or failed run can be removed |
| Result | two tabs: Main (the rack drawn from the data: numbers, the picture with the devices, the elevation, the device card, how it was read) and Report (the 15-step report inside the app) |
| More | the demo video, install help, the server, sign out |
| More | models and accuracy, the demo run, install help, sign out |

Every run lives in `data/runs/<id>/` (video, stitched rack picture, `result.json`, `status.json`, `report/`) and survives
a server restart; the history is read from there. The stored demo run is `results/video_experiment/vdo1` with its report
in `reports/video/vdo1`.

## The 15 steps

Input, quality check, stitched rack picture, frames, unit detection, unit classification, device detection, device
classification, OCR (make and model), agents (vendor templates: the switch mapper, the router mapper, the firewall and
gateway mapper), overlay (the port layout on the device), fallback (the RT-EOS port model when no template fits), port
review, rack report, models and accuracy. About 9.5 minutes per 13 s video on this Mac; the port steps are 80 % of it.

## Layout

```
server/        app.py (the API and the app's pages), run.sh, make_cert.sh, config.json (made on first start, keep private)
mobile/        the app: index.html, app.js, app.css, manifest.webmanifest, sw.js, icons/; capture/ = the camera guide
src/           the engine: pipeline.py (photo), rack_video.py (video), rack_stitch.py + rack_stitcher/ (stitching),
               frame_locator.py (best frames), occupancy.py, device_*.py, port_agents_batch.py (runs one of the
               port agents on a list of crops), port_details.py (RT-EOS port models)
port_agents/   the team's three port agents (switch, router, appliance), used as delivered, never edited here
data/catalogues/  the switch workbook and its json (models_by_vendor.*), site checks
scripts/       video/build_report.py (the 15-step report), video/add_closeups.py, switches/build_catalog.py, evaluations
models/ weights/   the trained models (device classifier, occupancy ensemble, detectors, port models)
data/          video/ (demo video), ground_truth/, captures/ (takes from the guide), runs/ (every run), closeups/,
               datasets/, device_crops/, eval/ (numbers the reports quote)
results/ reports/  the stored demo run and the built reports; tests/ (python3 -m pytest -q tests)
native/        the iOS and Android app (Capacitor): a start page that connects to a server and opens mobile/; see native/README.md
deliveries/    the teammates' packages as received (srikanth_ocr, integration) and the retired desktop portal
docs/          DEVICE_LABEL_REVIEW.md
```

## API

All under a session cookie except `/api/login` and `/api/health`.

| Call | Does |
|---|---|
| `POST /api/login {name}` | signs in; `POST /api/logout`; `GET /api/me` (`{name: null}` when signed out) |
| `POST /api/video` (video file, or `capture=app/<take>`, or `sample=vdo1`; `stride`) | starts a run, returns `{job}` |
| `GET /api/video/history` | every run, newest first |
| `GET /api/video/{job}` | state, progress, summary, devices |
| `DELETE /api/video/{job}` | removes a finished or failed run and its folder |
| `GET /video-report/{job}/index.html` | the run's report |
| `POST /api/closeup` (file, job, device) | reads a close-up of one device and keeps it with the run |
| `POST /api/photo` (file) | the photo pipeline on one rack photo |
| `/capture/`, `/capture/upload` | the camera guide and its upload (pieces, resumable) |
