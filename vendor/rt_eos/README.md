# RT-EOS

A 30-day pilot of physical-layer rack scanning for a rack, stack and cable vendor.

A technician photographs a rack with the phone or web app. The scan engine finds the devices, the rack units they sit in, the ports and whether each port has a cable, and reads the labels it can see. Close-ups of each run of ports (zones) read the label on every fibre and number the ports. The site's Excel patching matrix is the record: every scan is checked against it (device in the right unit, cables missing or not in the sheet), and an A-end / Z-end check takes the labels read in two racks and says, cable by cable, whether each end is in the port the sheet says.

Physical layer only. RT-EOS never connects to a device: no SNMP, no SSH, no network access to anything in the rack. The specifications of a model are looked up from the maker's public pages.

## What is in the tree

```
client/                 React + Vite web app, wrapped with Capacitor for iOS and Android
server/                 Node/Express API (app.js), routes/ and lib/ (see below), test/
pipeline/               Python scan engine: YOLO detectors, cable classifier, OCR, zone close-ups
  switch_ocr/           Faceplate OCR library the engine uses for make and model
switch_specs/           Switch specification base and datasheet lookup (device cards)
Models/                 Model weights (not in git; config.json names the files)
reference/              Reference sheets: the vendor names the OCR matches against
                        (Switch_Vendors_Websites.xlsx) and the A-Z upload matrix template
samples/                Photo sets for local checks (not in git)
data/                   Live pilot data: organizations, sites, scans, imports (not in git)
config.json             Model files and detection thresholds
requirements*.txt       Python dependencies (loose floors, pinned lock, dev tools)
pyproject.toml          Ruff, mypy and Bandit settings for the pipeline
package.json            Root scripts that install and run client and server together
```

The server's own parts, by job:

| Job | Where |
| --- | --- |
| Sign-in, organizations, sites, invites | `server/auth.js`, `routes/setup.js`, `lib/estate.js` |
| Scanning and results | `server/app.js` (`/api/analyze`, `/api/scan/:id`, corrections) |
| A scan's files (photo, annotated, thumb, crops, close-ups) | `GET /api/scans/:scanId/files/...` in `server/app.js` |
| Home and the site overview | `routes/home.js` |
| Patching matrix import | `routes/matrix.js`, `lib/matrix.js`, `pipeline/matrix_read.py` |
| Zone close-ups, sheet check, uncapped ports, A-end / Z-end check, worklist, device cards, optics, reports | `routes/physical.js` with `lib/scan_view.js`, `lib/sheet_check.js`, `lib/checks.js`, `lib/worklist.js`, `lib/device_lookup.js`, `lib/sfp_advice.js`, `lib/reports.js`, `pipeline/zone_read.py` |
| Organization data settings (never train, retention, online lookups, delete pilot data) | `routes/org_data.js`, `lib/org_settings.js` |
| Where each hardware detail came from, and how sure | `lib/field_sources.js` (used by `lib/scan_view.js`, the device card and the scan detail) |
| The rack's name, labels and height after the first answer | `settleRack` in `server/app.js`, polled at `GET /api/scan/:rackId/settle` |

## The data folder

Everything the app keeps sits in one folder, `data/` at the repo root (never committed). One setting moves all of it: `RACKTRACK_AUTH_DB`, the path of the accounts database; the rest of the tree is the folder above it. `server/lib/store.js` is the only module that builds a path into it, and its header is the full layout:

```
data/
  db/                               auth.db (accounts, organizations, sites), index.db (scan index), logs.db
  orgs/<organization>/
    org.json
    training/                       the organization's correction memory
    sites/<site>/
      site.json
      matrix/                       the imported patching matrix: the file, cables.json, history.json
      checks/<id>.json              A-end / Z-end label checks
      racks/<rack>/
        rack.json
        scans/<date_time>_<id>/
          photo.jpg  annotated.jpg  thumb.jpg
          result.json  meta.json
          corrections.jsonl         every correction a person made, append only
          uncapped.json             ports marked uncapped
          zones/<zone>.jpg + .json  zone close-ups and what was read on them
          crops/  engine/  backups/
  cache/                            OCR cache, in-flight uploads, public specification answers
```

The organization owner can switch off training on the organization's data, set how many days pilot data is kept, switch off the online lookups of specifications, and delete all of it (Organization settings; the delete asks for the organization's name, typed exactly). A new organization starts with never train on, 90 days kept (`RT_DEFAULT_RETENTION_DAYS` changes the number) and online lookups on. With online lookups off, no request leaves the server for datasheets or search.

## The one settings file

Every secret and setting lives in `.env` at the repo root. Copy `.env.example`, which lists every key, and fill in what you need. The server loads it on start (`server/lib/load_env.js`) and Vite reads its `VITE_*` keys. Nothing secret is committed, and the tests never read it.

## Run it on a Mac

You need Node 20 (see `.nvmrc`), Python 3.10 and the model weights in `Models/` as named in `config.json`.

```bash
# Python scan engine (and the device lookups)
python3.10 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Client and server
npm run install:all

# Start both
npm run dev
```

Open https://localhost:5173 (a self-signed certificate: accept it once). Vite forwards `/api` to the server on port 3001. The server finds `.venv/bin/python` by itself and keeps a warm Python worker with the models loaded; zone close-ups are read in that worker too.

To run the two halves by hand, as the pilot does:

```bash
cd server && PORT=3001 node app.js
cd client && npx vite --port 5173 --strictPort
```

## The local pilot accounts

`server/scripts/seed-local-pilot.js` builds the pilot roster into a database file you name: the platform owner, one organization and one site with first-run setup complete, Barry as the site manager and the technicians. It never prints a password; the roster and its passwords are in the script.

```bash
cd server
node scripts/seed-local-pilot.js --db ../data/db/auth.db --org "EOS Pilot" --site "Rack Build Facility"
```

It is safe to run twice. It refuses a database that holds anybody else, unless you pass `--reset` (which backs the file up first).

## Tests

```bash
cd server && node --check app.js && npm test        # runs on a throwaway data folder, never the live one
cd server && npm run lint
cd client && npm run build && npx vitest run && npm run lint
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest pipeline/tests -q
.venv/bin/python -c "import pipeline.worker"
```

The server tests cover every route with a caller who may and one who may not; the zone reader and the web lookups are replaced by stubs there, so the suite needs no model weights and no network. `pipeline/tests/test_zone_read.py` tests how close-up labels are paired with ports on a drawn image.

## Scoring a two-rack test (E3, E4)

`server/scripts/score-check.js` scores an A-end / Z-end label check against a hand-checked answer file, the day the client's data arrives. It only reads.

```bash
cd server
# a check already run in the app (its id is in the check's address, /checks/<id>)
node scripts/score-check.js --check 3f2a9c1b7e --answers ~/answers.csv
# or two scans: the check is run now against the site's Excel dataset, and not saved
node scripts/score-check.js --a RK-XXXXXXXX --z RK-YYYYYYYY --answers ~/answers.csv --verbose
```

The answer file is a CSV, one row per cable, a header row optional:

```
cable label,A device,A port,Z device,Z port,present
CAB-0103,SW-CORE-01,Gi1/0/3,PP-B-01,3,yes
CAB-0104,SW-CORE-01,Gi1/0/4,PP-B-01,4,no
```

`present` is yes or no: whether the cable is really there. The devices and ports are where a person found each end. It prints matched (label read on the right device and port), mismatched (read on another device or port, or read where no cable is), missed (cable there, no label read), false flags (a wrong port or missing cable raised where the cable is really where the sheet says, or a label read that is on no cable at all) and the label read rate (ends read / ends present). `--json` prints the same as JSON. Set `RACKTRACK_AUTH_DB` to score against another data folder.

Labels are compared with the camera's usual misreadings allowed for (`server/lib/label_match.js`): case and separators aside, O/0, I/1/L, S/5, B/8, Z/2 and G/6 taken as one, and one character off on labels of 6 or more when only one dataset label is that close. A near-tie is refused. Every match says how it matched, and screens and reports say "matched after correcting the reading" when it was not exact.

## Ownership

Sprintpark LLC. All rights reserved. No open-source licence is granted.
