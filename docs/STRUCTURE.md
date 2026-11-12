# RackTrack: the layout of this folder

One application: a video of a rack in, the rack read, compared with the organization's database copies, the drift and the report out. Everything it needs is inside this folder. This page says where each part lives and what is kept out of git.

## The layout

```
server/            the API and the services it needs
  app.py           the FastAPI engine API (127.0.0.1:8700): runs, results, compare, copies, logins, tools
  rt_session.py    checks the session the sign-in service signs (same JWT secret, pinned algorithm)
  auth/            the sign-in service (Node, 127.0.0.1:3302): RT-EOS's sign-in, organizations, sites, people,
                   invites, settings and the platform console, cut down to what the service uses; its tests in auth/test
  mocks/           local NetBox (3303) and ServiceNow (3304) for testing, loaded from mocks/ground_truth;
                   started only when no real login is saved
engine/            the video engine (Python, imported by server/app.py)
  *.py             stitching, units, occupancy, devices, kinds, ports, the compare, the RT-EOS features
  video_report.py  the rack report of a run
  port_agents/     the switch, router and appliance port agents
  patchpanel/      the patch panel reader
  fa_device_logic/ the team's device joining logic (device_join.py uses it)
  rack_stitcher/   the stitcher package rack_stitch.py calls
  report_data/     the unit and device evaluation tables the rack report reads
  readers/         the RT-EOS readers the engine calls as child processes in _cache/venv (close-up OCR, the
                   specifications lookup) and dark_mobile's cable reader
  catalogues/      the vendor and switch catalogues the readers look up
  third_party/     DEIMv2 (the device detector's model code)
web/               the screens
  app/             the app (served by the engine at /app/, also the phone shell's web content)
  org/             the organization pages (React; built into web/org/dist-org, served by the sign-in service at /org/)
native/            the phone shell (Capacitor, app id com.racktrack.sweep), built from web/app
models/            every model weight (not in git; models/MANIFEST.txt lists each file with its size and SHA-256)
samples/           the test videos, the stored run of vdo1 and the owner's rack record (not in git; listed in models/MANIFEST.txt)
data/              runtime only (not in git): runs, database copies, uploads, checks, the sign-in database and its settings
tests/             the Python tests (pytest)
tools/             proofs (proof_*.py), the scripts that build or repair the environment and data, and tools/training
                   (how the models were trained and evaluated)
.claude/           the RackTrack page and report standard (a Claude Code skill)
docs/              this page, the handover, the brief, the verification passes, the plan, the screen standard, proofs
start.sh, stop.sh  start and stop the whole application
engine.sh, auth.sh, sources.sh   start one service each (start.sh calls them)
requirements.txt   the engine's Python packages, pinned
```

## Moved from

| Was | Now |
|---|---|
| src/ | engine/ |
| scripts/video/build_report.py | engine/video_report.py |
| scripts/devices/train_dinov2.py (the second device model's code) | engine/train_dinov2.py |
| port_agents/ | engine/port_agents/ |
| patchpanel_classify_overlay/ | engine/patchpanel/ |
| rt_eos/pipeline/, rt_eos/switch_specs/ | engine/readers/pipeline/, engine/readers/switch_specs/ |
| vendor/dark_mobile/pipeline/cable.py | engine/readers/dark_mobile_cable.py |
| data/catalogues/ | engine/catalogues/ |
| src/third_party/ | engine/third_party/ |
| rt_eos/server/ (the parts the sign-in service uses) | server/auth/ |
| rt_eos/client/ | web/org/ |
| mobile/ | web/app/ |
| modules/sources/ | server/mocks/ |
| weights/ | models/weights/ |
| rt_eos/Models, vendor/dark_mobile/Models (links) | models/rt_eos/, models/dark_mobile/ |
| data/ground_truth/, data/video/ | samples/ground_truth/, videos/ (the videos; samples/video/ keeps the three stored runs) |
| scripts/occupancy, devices, units, switches | tools/training/ |

## Removed

- racktrack-app's leftovers, copies of what /Volumes/Racktrack/racktrack-app still holds: deliveries/, results/, reports/, integration/, "untitled folder", videos_and_stitched_racks.zip, data/runs_archive and the old app's runs in data/runs (91af02c09bbc, 28c3c52cd91e, 501bcd981b64).
- vendor/rt_eos and vendor/dark_mobile: the read-only copies the work was taken from; every piece in use now lives in server/ or engine/.
- rt_eos/ itself: its full server (app.js and the photo pipeline routes), its reference folder and its own settings files; the sign-in service keeps only the files it requires.
- The routes and screens of the removed tabs: Worklist, Topology, Physical layer and Read again (and their tests), the racks report built from three old runs, the old sample upload and the port agents' Anthropic reader.
- The proofs that no longer run (proof_today, proof_afternoon, proof_crosscheck drive the old Drift tab and name-only sign-in) and the ones the screen standard proof now covers (proof_widths, proof_tabs, proof_tabbar), and proof_flow, which planted differences into the shared mocks.

## Rules that keep it this way

- Every path is relative to this folder; nothing reads another folder. `grep -rn "/Volumes\|/Users" server engine web tools tests *.sh` finds nothing in code.
- Runtime data goes under RACKTRACK_DATA (default data/); a test or proof engine points it at its own copy.
- Models load from models/ with every model hub offline (engine/local_models.py, engine.sh).
- One way to do each thing: one start script, one data root, one screen standard (docs/design/STANDARD.md), one proof of the screens (tools/proof_standard.py).
