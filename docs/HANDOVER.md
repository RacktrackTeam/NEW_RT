# Handover for a new chat in this folder

Read this first, then docs/INTEGRATION-BRIEF.md, docs/TODAY-STATUS.md and docs/plan/plan.html.

## What this folder is
racktrack-app is the core: its video engine reads a rack from a sweep video. We are adding the databases (Excel, NetBox, ServiceNow CMDB), the rack match, the drift and then the RT-EOS functions (sign-in, organizations, sites, people, Excel checks, reports) into this one app. No photo pipeline. Nothing in here may depend on another folder: the RT-EOS code is at rt_eos/, the dark_mobile NetBox and CMDB code at vendor/dark_mobile, models at models/, samples at samples/, the Python environment and node packages at _cache/. Install Python packages only with _cache/venv/bin/python -m pip.

## Rules from the owner (2 Nov 2026)
- Alter and integrate, never rebuild: every function already exists in one of the three apps; copy it and wire it.
- Deadline for the complete integration: Wednesday 4 Nov. Tokens are a weekly budget: one agent at a time, narrow scopes, no fleets.
- Test with all three sample videos in samples/video. The owner's own rack is SP-HYD-RM01-R01-R1 (vdo1); its record is data/ground_truth/SP-HYD-RM01-R01-R1/netbox_devices.csv and it sits in all three databases through modules/sources (ground truth, mocks, filled workbooks).
- After the build: fresh verification passes (function, code, UI) until a pass finds nothing twice; the owner expects zero issues.
- Plain English, plain hyphen only, white light theme, full device width, commits as plain sentences with no AI attribution. Never touch /Volumes/Racktrack/RT-EOS, dark_mobile or racktrack-app (the live apps), never call a real outside system, never push without the owner (the owner runs scripts/push-to-github.sh).

## State at 2 Nov 15:10
- Done: Database section (Excel upload, NetBox and CMDB copied in from the mocks), rack match on the confirmed rack id, Drift tab with planted-difference proof on vdo1 (docs/proof/today). Start: ./engine.sh and ./sources.sh, then http://127.0.0.1:8700/app/.
- Also done this afternoon (commits to 2b254e1): Profile > Connections (NetBox and ServiceNow logins encrypted with data/connections.key, Test connection, the mocks stay the default when no login is saved); the Excel report before import (RT-EOS workbook checks, Import button); port drift from PORT_INVENTORY (wrong port type, missing or extra cable, dust cap missing); Profile > Database copies (versions per source, active one marked, delete); the drift now uses the kind the result screen shows (a person's correction, then the make and model step, then the classifier), so the router at U20 and the firewall at U19 of vdo1 are no longer false drift. 14 tests pass. The engine must be restarted with ./engine.sh to serve the new routes, then run _cache/venv/bin/python tools/proof_afternoon.py and tools/proof_today.py (live mode never run yet; set PW_CHROMIUM to an installed headless shell such as chromium_headless_shell-1228).
- Real finds on our rack: ports 25 to 28 of the two TL-SG2428P switches are SFP cages but the record says RJ45 (the record is wrong); the workbook's LOOKUPS sheet has no RJ45 under Port_Type, so every port row gets a warning, as in RT-EOS.
- Small debts: copies are not yet per organization (logins are); cables from A_Z_CONNECTION_MATRIX are counted, not checked; en dashes remain in older unit-range code in mobile/app.js around lines 481 to 980.
- Known: device kinds at U20 and U19 of vdo1 differ between classifier and make-and-model step; the two WhatsApp videos' unit numbering is off by one or two; their ground truth needs the owner's confirmation; the CSV has no serials.
- Owner's feedback on the UI: the flow is not understood and the experience is poor. Tomorrow's first job is one clear flow: Home (racks and their state), Scan (record, upload, run progress), Result in order (rack picture, devices read, the record and the matched rack, the differences, the report), each step saying what comes next, in the RT-EOS minimal look.
- Not done: sign-in and organizations, the other RT-EOS functions, serials and asset tags, the other two videos end to end, the GitHub push (remote set, token needed).

## State at 2 Nov 16:10 (evening)
- Start: ./auth.sh (sign-in on 3302), ./engine.sh (8700, takes the model lock only around a model run), ./sources.sh (mocks on 3303 and 3304). Sign in with the accounts in docs/AUTH.md; the stored runs from before sign-in show for the platform owner.
- The result is four parts: Rack, Devices, Layout (the rack elevation drawn from the data), Report; the compare (rack identification steps 0 to 6 with proof and confidence, then the differences) lives in the Report. Copy-in and logins are under Profile. Proof: tools/proof_flow.py, tools/proof_widths.py.
- docs/CROSSCHECK.md (the live cross-check), docs/EVALUATION.md (tools/evaluate.py), docs/AUTH.md (sign-in, five roles, removals), docs/TODAY-STATUS.md (evening table and what is still open).
- tools/proof_today.py, proof_afternoon.py and proof_crosscheck.py drive the old Drift tab and name-only sign-in: kept as the record, no longer runnable as they are.

## Owner's UI decisions, 2 Nov evening
- Keep the Rack, Devices and Layout tabs exactly as they are now ("I love them"). Do not change them.
- Accent: subtle black (#1f2a3a), not green and not teal. Green, amber and red only as states.
- Each tab is its own screen, no long scrolling, nothing repeated across tabs. The Report holds only the report, with "Compare against the database" top right: dropdown of three, confirm the verified rack id, then the drift.
- Sign-in: simple, centred in the middle of the window. "Reading the rack" is like RT-EOS's analysing page plus racktrack-app's run page.
- The mockup in docs/design/ is the reference for Home, Scan, the run, Sign in and the Report; it must be updated to the subtle black accent before the rebuild.
- 3 Nov: when the rack id is certain, no confirmation step; the person can still change it.
- Later the same evening: the owner also likes the Report tab as it is now, and the Compare against the database flow on it. The remaining pages are good. So the result screens (Rack, Devices, Layout, Report with its compare flow) are APPROVED and must not be redesigned; the mockup applies only to Sign in, Home, Scan and the run screen, with the subtle black accent.

## State at 3 Nov (pass 2 fixed, the folder restructured)
- Start everything with `./start.sh` (sign-in 3302, mocks 3303 and 3304 when no real login is saved, engine 8700), stop with `./stop.sh`. The layout is in docs/STRUCTURE.md; README.md has install, accounts, tests and proofs.
- The engine runs in `_cache/venv-engine` (racktrack-app's own packages, numpy 2, made offline by tools/make_engine_env.py); the RT-EOS readers stay in `_cache/venv`. Every model loads from `models/` with the hubs offline; `models/MANIFEST.txt` lists every model and sample file.
- docs/VERIFY-2.md ends with "Fixed in pass 2", one line per finding. The fresh run of vdo1 matches the stored run on units, devices and label but not on three kinds and three not-sure units (the stored run is older than this Mac's torch and the engine's code; see finding 1 there).
- Demo Organization holds one clean copy per source; proofs use Test Organization accounts only (tools/proof_guard.py).

## State at 3 Nov afternoon (pass 3)
- docs/VERIFY-3.md: the main flow walked twice at 1280 and 400 for orgownert, techt and managert on a scratch copy; six faults fixed (start scripts honour RACKTRACK_DATA and RT_AUTH_ENV, the free space pill space, Connections groups a problem across ports, Cables says nothing to check once, a picked other rack no longer shows the found rack's confidence, the auto acceptance wording), pass 1 item 49 fixed (two old logins removed from data/sources.db); 133 Python tests (model test skipped to keep the Mac cool), 118 sign-in, 285 organization page tests pass; the second walk found nothing.

## State at 3 Nov (pass 4)
- docs/VERIFY-4.md: a fresh-eyes walk on a scratch copy (engine 8708, sign-in 3312) at 1280 and 400 for orgownert, techt and managert; ten faults fixed (tab bar marks Report on a phone, Change keeps the rack ID until a new one, Find a port picker follows a rack tap, Cables names scans by site and time, port differences name their device, the auto acceptance wording and a person's confirmation kept, the You page 404, the rename hint, a refused organization page says so, AUTH.md); 133 Python (model test skipped), 118 sign-in, 285 organization page tests pass; the second walk found nothing; no shared data changed.

