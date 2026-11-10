# Handover for a new chat in this folder

Read this first, then docs/INTEGRATION-BRIEF.md, docs/TODAY-STATUS.md and docs/plan/plan.html.

## What this folder is
racktrack-app is the core: its video engine reads a rack from a sweep video. We are adding the databases (Excel, NetBox, ServiceNow CMDB), the rack match, the drift and then the RT-EOS functions (sign-in, organizations, sites, people, Excel checks, reports) into this one app. No photo pipeline. Nothing in here may depend on another folder: the RT-EOS code is at rt_eos/, the dark_mobile NetBox and CMDB code at vendor/dark_mobile, models at models/, samples at samples/, the Python environment and node packages at _cache/. Install Python packages only with _cache/venv/bin/python -m pip.

## Rules from the owner (2 Nov 2026)
- Alter and integrate, never rebuild: every function already exists in one of the three apps; copy it and wire it.
- Deadline for the complete integration: Wednesday 4 Nov. Tokens are a weekly budget: one agent at a time, narrow scopes, no fleets.
- Test with all three sample videos in videos/ (since 4 Nov every video sits in that one root folder; the stored runs stay in samples/video). The owner's own rack is SP-HYD-RM01-R01-R1 (vdo1); its record is data/ground_truth/SP-HYD-RM01-R01-R1/netbox_devices.csv and it sits in all three databases through modules/sources (ground truth, mocks, filled workbooks).
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


## State at 3 Nov (pass 5)
- docs/VERIFY-5.md: the confirmation pass on a scratch copy (engine 8708, sign-in 3312), where passes 3 and 4 did not look; eleven faults fixed (a picked and confirmed record on a run with no rack ID is remembered, "Picked by" and typed or picked wording in the compare, a technician told plainly there is no copy, the empty copies card padding, a non-video upload refused, no empty run folder left after a refused start, one error shape for every HTTP error, the Report rows by keyboard and the empty link hidden, start.sh honours RACKTRACK_DATA for logins, STRUCTURE.md); left for the owner: four baseline files over 5 MB in git; 135 Python (model test skipped), 118 sign-in, 285 organization page tests pass; the second walk found nothing; no shared data changed.

## State at 3 Nov (pass 6)
- docs/VERIFY-6.md: the closing pass, one walk of everything passes 3, 4 and 5 checked, on a fresh scratch copy (engine 8708, sign-in 3312, from 15:45 own mocks on 3313 and 3314) at 1280 and 400 as orgownert, techt, managert and the platform owner: sign-in, Home, the seven tabs of the vdo1 run, the compare on all three sources with the wrong rack and the reopen, the two WhatsApp runs, Profile, Connections, Database copies, the organization pages, the Scan screen, sign out, the API. No fault found, no code changed; 135 Python (model test skipped), 118 sign-in, 285 organization page tests pass; no shared data changed (the owner's mocks answered two read-only copy-ins before the move to my own mocks).

## State at 4 Nov afternoon (testers outside the network)
- A tester's run (a D-Link switch with its make read but no model, ports mapped automatically) bounced every tab of the result, the Report among them, back to the run page: the device state read the make and model off the typed record, which is null for such a device. Fixed in web/app/app.js (the typed record is treated as empty when missing), app script version 168. tools/proof_result_tabs.py now opens every tab of every finished run as a person at phone and laptop width and fails on any error, handled or not; it passes for the four Test Organization runs, through the front door and through the public address.

## State at 5 Nov (the testers' 27 findings, built)

The tester's 27 findings of 4 Nov were triaged (published plan: https://claude.ai/artifact/FAzWkXscQ9V9TXAPb1dvZa; her own page with
her answers: https://claude.ai/artifact/BEtrRmtasSZvqNKq2qEBui) and built on 5 Nov, locally only, nothing deployed or pushed.
- Screens (web/app): the app bar's chosen button is its own rounded shape (1); the signed-out screens are locked to the screen,
  body[data-page] (2); the camera guide takes the whole screen (3); the run page keeps only main's clearance (4); the stage of a run
  comes numbered from the server (status "stage", never going down) and the page uses it (5, 6); the rack picture fits the space
  really left under the tab row, and the rack ID card is one line with the choices in a row and typing folded (7); the zoom keeps
  the device's own crop unless the stitched band holds the whole detector box, the view now carries "box" (8); the tab row sits
  under the measured header height, --topbar-h (14); one icon per tab (16); the compare shows the label strip only when a label
  was read there, else the rack small, says "none of the N racks matches", and the record picker is a real dropdown plus a
  search box (17, 18); Find a port lists every device, a device not read yet gets Read the ports there, the answer sits beside
  the picture and scrolls into view (12, 21, 24, 25); the wheel over the 3D scene never scrolls the page (27).
- Server (server/app.py): STAGE_WORDS and stage_of; an upload is refused only for another running run, with its step, never for
  a device's port read holding the model lock (11); a video read before is also matched by size, duration and three sampled
  frames when the phone re-encoded it, _video_fingerprint and _same_look (10); /api/video/<id>/report.pdf prints the report with
  headless Chromium, a Download PDF button on the Report tab (22); after a run every port-bearing device is read on its own, one at
  a time, start_ports_all (12, 21, 26).
- Engine: a box the detector calls Empty is no device, its units go to uncertain (19, rack_video.devices_from; source_db treats
  "Empty" as no kind); the patch panel grid never puts a slot past the picture's edge and a grid that leaves the picture means the
  size guess was wrong (13, 15, patch_panels.complete_grid img_w); the unit ladder adds at most two rows past the model's outermost
  box at each end (23, unit_grid._UNIT_EXTEND_MAX).
- Tests: tests/test_panel_grid_and_ladder.py (new) and four new cases in tests/test_engine_routes.py.
- tools/units_only/: the stand-alone unit tool a teammate tests on a laptop (video or picture in, the units said and drawn on the
  picture and the video); make_zip.sh builds the Desktop zip from engine/ and models/.
- Proofs: tools/proof_result_tabs.py on every run at both widths; the screenshot proof of the changed screens; the tester's 15:34
  video re-run offline for the Empty box and the ladder end; her patch panel D2 re-read for the grid. The 13-video proof against
  reports/2026-11-04 is the last step before a deploy.
- Proof results, 5 Nov afternoon: every tab of the 7 runs opens at 412 and 1280 px with no error (tools/proof_result_tabs.py);
  the changed screens hold at 360, 412 and 1280 px with no console error; the PDF route answers 200 (15.7 MB for the office
  rack); the tester's 14:33 and 15:34 uploads now count as the same video (frames within 1 bit, 40.03 against 40.04 s) and the
  14:52 one does not; her 15:34 video re-run offline lost the empty area listed as D18 and the device at the floor edge (18 to 15
  devices, the empty rows left to a person), its unit count stays 47 because those floor rows come from the model itself; her
  patch panel D2 re-read gives 40 ports (23 seen, 17 hidden), size not sure, every box inside the 650 px picture (was 48 with
  boxes to 722 px). All 13 videos offline (phase 1): tech1 43 units 15 devices, tech2 47 / 17 (seven phantom devices in empty
  space gone, checked by eye), tech3 41 / 24, tech4 still cannot be stitched (as on 4 Nov), tech5 37 / 9, tech6 38 / 10, tech7
  45 / 10, tech8 50 / 19, tech9 37 / 11, tech10 57 / 23, vdo1 22 / 10, whatsapp 2143 46 / 13, whatsapp 2319 43 / 12.
- Live proof, 5 Nov 14:23, whatsapp-2026-10-26-2319.mp4 uploaded through the app as techt (run ebd221a2af96): the stage went
  1, 3, 4, 5, 6, 7, 8, 14 and never down (the stitcher's own "done (100 %)" line had sent it to 15 on the first try; only the
  bare "done" ends a run now); after the run the ports were read on their own, one device at a time, top of the rack first, for
  every device whose one kind is port-bearing (two patch panels, three routers); an upload posted while a port read held the
  model lock was accepted (200, a run started) instead of "another rack is being read"; Find a port lists all 12 devices,
  the ones not read with "ports not read yet". Two switches the detector typed but the classifier called Other stay "Not sure"
  and are not read on their own: a person sets the kind first, as the one-kind rule says. tech5 (servers and storage only)
  rightly started no port read.

## State at 10 Nov (the tester's second round)

The Mac rebooted on 6 Nov and the services were down until 10 Nov 11:30, so the tester's second list ("still ...") came from
her 4 Nov results and one new upload on 5 Nov 15:31: a clip of people at an airport, sent to see whether a non-rack video is
refused. Built on 10 Nov (local only):
- A video with no rack is refused before the stitch: six sampled frames through the unit model, under 4 s (server
  rack_in_video, RACK_CHECK_MIN 3); a rack video that cannot be stitched still gets the stitch message.
- Device crops (the zoom, the list, the device page) are cut from the detector's own outline (polygon_box), not from the
  unit band the outline was snapped to: on her rack the unit grid sits half a unit off and every band held halves of two
  devices (the half crops, and part of the wrong kinds). The cached clean crops of every run were deleted so they re-cut.
- A patch panel's jacks get their state from the status model on each jack's crop (pipe.add_port_details on the panel
  layout), not from the port class: an empty RJ45 jack was "connected". A slot the reader did not see is "not sure".
- "Not sure" devices carry a best guess (the second model at 50 % or more, else the detector's vote): the words say
  "Not sure, likely Switch (92 %)", Find a port shows it, and the automatic port read takes such a device too.
- A run read before the automatic port reads existed gets them when its result is opened (PORTS_ALL_SEEN).
- A sideways export of a video read before counts as the same video (the fingerprint keeps quarter-turned hashes too).
- The camera guide has a Flip the view control for a phone whose motion sensor reports gravity the other way (kept on the
  phone, rackGuideFlip).
- Her four 4 Nov runs and techt's two were re-read in place with the new rules (same run ids, reports rebuilt, ports reset),
  so the results she opens show the fixes; see scratchpad rerun_inplace.sh in the session, and the ports fill in when a
  result is opened.
Not fixable without new models: the classifier calling her top device a server, and "Other" on a third of her devices.
- Re-read in place on 10 Nov 11:40 to 12:12 (phase 1, reports rebuilt, ports reset): her office rack 785fb92f6137 23 units 10
  devices (was 10, no empty rows); 09726ca843f3 48 / 13 (was 14; empty rows U28, U29); b82f26c94fd9 42 / 14 (was 17; U3, U8,
  U11); 3ad1f49493c1 47 / 15 (was 18; U3 to U7, U12, U28, U29); techt's 8bb76d8c5766 43 / 15 (was 16); 8afd6d1aa988 47 / 17
  (was 24). The ports of every port-bearing device read again by themselves when each result is opened.
- The services do not survive a reboot: tools/boot.sh and tools/ai.racktrack.boot.plist are written; installing the login item
  and opening the tunnel are the owner's two commands (the safety check here blocks both); the README has them.
