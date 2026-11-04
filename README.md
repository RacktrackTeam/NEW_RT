# RackTrack

Film a rack from top to bottom. RackTrack stitches the video into one rack picture, numbers every unit, finds and names
every device, reads makes, models and ports, matches the rack with the organization's database copy (Excel workbook,
NetBox or ServiceNow CMDB), shows the drift and writes the report. One application: the app's screens (`web/app`, also
the phone shell's content), an engine API (`server/app.py` with `engine/`) and a sign-in service (`server/auth`) for
organizations, sites and people. The layout of the folder is in [docs/STRUCTURE.md](docs/STRUCTURE.md).

## Start and stop

```
./start.sh        # the sign-in service (3302), the local NetBox and ServiceNow (3303, 3304) when no real login is saved,
                  # then the engine (8700); one line per service. Open http://127.0.0.1:8700/app/
./stop.sh         # stops what start.sh started (by the process ids in data/pids)
```

Each service also starts on its own: `./auth.sh`, `./sources.sh`, `./engine.sh` (`RACKTRACK_PORT` for another engine
port, `RACKTRACK_DATA` for another data folder). Logs are in `data/logs/`. Use the same host name for the app and the
sign-in service (127.0.0.1, or localhost for both): the session cookie belongs to the host.

The engine reads one video at a time (about 2 to 10 minutes on this Mac; the device port reads come after, per device)
and takes the machine-wide model lock only around a model run, with two threads (`OMP_NUM_THREADS=2`): this Mac overheats
with more. Nothing leaves the machine: every model is read from `models/`, the model hubs are switched off, and NetBox
and ServiceNow are the local mocks until an organization admin saves a real login under Profile > Connections.

## Accounts

`./auth.sh` seeds them on its first start. The password is in `data/auth/seed-password.txt` (set `RT_SEED_PASSWORD`
before the first start to choose it). Details and what each role may do: [docs/AUTH.md](docs/AUTH.md).

| Username | Role | Organization |
|---|---|---|
| owner | platform owner | - |
| orgowner, admin, manager, tech | organization owner, admin, site manager, technician | Demo Organization (Hyderabad DC) |
| orgownert, admint, managert, techt | the same four | Test Organization (Test Hyderabad DC), for proofs and tests |

The Demo Organization is the owner's demo: one clean copy of each database and one stored run of the owner's rack
(SP-HYD-RM01-R01-R1). Proofs never write to it (`tools/proof_guard.py`); planted differences live in the Test
Organization. `tools/reset_demo_copies.py` puts the Demo Organization's copies back to the clean record.

## Install on a clean Mac

1. Python 3.10 (python.org installer), Node 20, git.
2. The engine's environment, from the pinned list:
   `python3.10 -m venv _cache/venv-engine && _cache/venv-engine/bin/python -m pip install -r requirements.txt`
3. The RT-EOS readers' environment (the engine calls them as child processes):
   `python3.10 -m venv _cache/venv && _cache/venv/bin/python -m pip install -r engine/readers/requirements.txt`
4. The Node packages: `cd server/auth && npm ci`, `cd web/org && npm ci`, `cd server/mocks && npm ci`. On this Mac they
   are kept in `_cache/rt_eos_node_modules` and linked in by `auth.sh`.
5. The models and samples are not in git. Copy `models/` and `samples/` from a Mac that has them, then check the copy:
   `_cache/venv-engine/bin/python tools/make_manifest.py --check` compares every file with `models/MANIFEST.txt`
   (size and SHA-256; the manifest says where each folder comes from).
6. `./start.sh`.

On this Mac `_cache/venv-engine` was made offline from the packages already installed in its Python 3.10
(`tools/make_engine_env.py`), so nothing was downloaded.

## The test videos

`videos/`: every video we have, in one folder at the root (`videos/VIDEOS.md` lists each one with where it came from).
`vdo1.mp4` is the owner's rack, SP-HYD-RM01-R01-R1, its record in `samples/ground_truth`; the two WhatsApp videos of
26 Oct are NETWORK RACK 1 and 2; tech1 to tech10 are the technicians' videos of 3 Nov. The stored runs of the first three
stay in `samples/video/` (`run_of_vdo1`, `run_of_whatsapp_2143`, `run_of_whatsapp_2319`). Upload one under Scan, or run
`tools/proof_video_run.py`.

## Tests and proofs

```
tools/with-model-lock.sh _cache/venv-engine/bin/python -m pytest -q tests        # the engine (loads the models)
(cd server/auth && npm test)                                                     # the sign-in service
(cd web/org && npx vitest run)                                                   # the organization pages
```

Proofs, against a running engine (`PW_CHROMIUM` names a headless Chromium for the browser ones):

| Proof | What it shows |
|---|---|
| `tools/proof_video_run.py` | one fresh video run end to end, compared with the stored run |
| `tools/proof_standard.py` | every screen at 1280 and 400 px, against docs/design/STANDARD.md |
| `tools/proof_pass2.py` | sign-in without refused calls, the /org/ pages, the failed-run screen, find-port answers |
| `tools/proof_auth.py` | sign-in, roles, organizations and 404 for strangers (31 checks) |
| `tools/proof_compare.py`, `tools/proof_compare_sources.py` | the compare with Excel, NetBox and ServiceNow |
| `tools/proof_features.py` | the RT-EOS functions on a run (cables, A-end / Z-end, close-ups, site position) |
| `tools/evaluate.py` | the three videos against their records |

## The phone app

`native/` is the Capacitor shell (app id com.racktrack.sweep). It opens a small start page, finds the RackTrack server
and loads the server's app (`web/app`), so a screen change needs no new build. See [native/README.md](native/README.md).

## Rules

Plain English and the plain hyphen on every screen, white light theme, full device width, accent #1f2a3a
(docs/design/STANDARD.md). Every path stays inside this folder. Commits are plain sentences.
