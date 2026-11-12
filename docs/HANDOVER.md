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

## State at 10 Nov evening (the tester's third list, 35 items)

The tester's third list came at 15:00 on 10 Nov (35 items in six groups: Home and Scan, recording and upload, Profile, results
and devices, layout and navigation, report and History). Every item was built or answered the same afternoon, local only
(branch rt-eos-integration), and a page for her to verify each one was published: "Third Round Fixes" (one note box per
item, Copy all notes; the URL is in the session's final message and in the memory file). App script version 170.
- Home lists only racks that were read; a scan that stopped stays in History; the last column is the compare result alone (1).
- A done bar under the header (DONEBAR in app.js): a reading started here that finishes while the person is elsewhere; kept
  in localStorage rt_done / rt_watch until that result is opened or dismissed (2).
- A scroll bar of the app's own at the right edge on every page that scrolls (#sbar) (3). Tighter rows on Profile and Scan (4).
- Scan: How to film is a chevron dropdown (5); Record is the filled card, Upload the outlined one (6); once a video is picked
  the title says Upload a video and the line says what to check (7); Back to Record or upload and a Choose another video button (8, 12).
- The background port reads (_ports_all_thread) wait while a rack is being read (_a_run_is_reading): on 10 Nov six re-read
  results' port reads took turns with a new reading at every step, so each stage took minutes (9).
- The rack ID card: every choice is a button, Type the ID and Not now too (10); the Next strip opens the question it names:
  scrolls, rings it (.nudge), opens Type the ID with the cursor when there is no ID to pick (30).
- The device page: Details is a bordered dropdown row (21) with Change next to the type (a select of the kinds, POST
  /device/<i>/kind; the correction is remembered for the rack) (11, 22); the arrows are buttons that name the device above and
  below, and the same bar sits fixed at the bottom of a phone (#dv-bar) (20); Not a device opens one question with Remove
  and Keep it, no timer (25); the Devices list ends with A device missing? Choose a unit (22); the serial block says Take a
  close photo of the label, a photo not a video (24).
- The rack check (13): the frame rule is six rows or two sure rows in some frame (RACK_CHECK_MIN 6, RACK_CHECK_SURE 2, eight
  frames), measured on seven rack videos and the two non-rack clips (scratchpad rackcheck_probe.py: the desk gave 4 rows,
  the airport 2; the rack videos reach 8 rows or 2 sure rows); and a second net on the stitched ladder: fewer than eight
  detected rows and no unit in use is no rack (stitched_looks_like_rack), because a copy of the desk video with its first
  second cut passed the frame rule and came out as a 1U rack. The check's numbers are kept on the run (rack_check).
- A technician's Connections screen shows the facts, no boxes (14); Database copies opens with The three sources and the copy
  each compare uses, for everyone (18); the accuracy page names the steps in plain words, no model names, and explains
  "not measured yet" (17); Profile rows have a chevron in a circle (16).
- The result tab row on a phone wraps to two rows, nothing scrolls sideways (19); the header's second line never wraps and
  the header is re-measured when its text changes (a run's name with its date wrapped and pushed the tab row over the
  content) (28, 29); a legend "Colour = device type" on the Layout tab, under the report's drawing and on the Rack tab's
  Types caption (23); the Layout controls say Zoom in, Zoom out, Reset (the built rt-topology.js's "Fit" label was renamed
  with sed, no rebuild) (31); the Find a port key is drawn with the cells (26); Connections has a Key box, Ports of D<n>
  labels and a tapped port answers under the cells (27); the back arrow goes one level up, never through browser history (32);
  Download PDF is a bordered button (33); Refresh has an icon, spins and reports (34); Install on the phone opens the steps
  and the browser's install prompt where offered (35). Profile colours: all white, red only on Sign out (15).
- Tests: 28 in test_engine_routes.py (the rack rules, the port-read wait) and the panel tests pass. Proof: every changed screen
  at 412 and 1280 px as techt (scratchpad proof3/shots.py and shots2.py), and tools/proof_result_tabs.py on every run.
- Not changed: the models (a wrong type on her rack is corrected by hand and remembered); the camera guide; no confirm
  step before a recorded video is read (the check does it).

## State at 10 Nov night (the tester's fourth list, 12 items)

Her second list of the day came in the evening ("Application Testing - Fixes to Be Done", 12 items in six groups); the owner's
own summary: scroll bars, alignment and the highlighting of nav links and buttons remained, everything else done. Built the
same night, local only (branch rt-eos-integration), and a page for her to verify each item was published: "Fourth Round Fixes"
(same form as the third: your words, what you will see now, how to check, a tick and a note box, Copy all notes; the URL is in
the session's final message and in the memory file). App script version 171; flow.css 21, features.css 8, features.js 13.
- Find a port (1): the rack picture is centred on a phone, the empty blocks between the Port box and the key take no space
  (#fp-grid:empty, #fp-out:empty), the tool page gap is 12 px.
- The device arrows (2) are bold outlined buttons on a pale ground (.dvarrow); on a phone they have their own full-width row
  at the top of the card (.dvtop, rendered next to the row inside .dvinfo, which is hidden on a phone): inside the text column
  the row was wider than the column on a 360 px phone and ran past the card, dragging the page sideways (5). The app's scroll
  bar (#sbar) is 6 px and darker (5).
- Every text-styled action is a bold outlined pill (button.link, summary.link): Enter the make and model (with a turning chevron),
  Not a device? Remove it (3), Change next to the type, Change ID and Zoom in on the Rack tab, Not this rack, Sign out, Back to
  Record or upload, Specifications (11). The camera guide's own back button keeps its black look (#sw-guide .swback). Links to
  other pages (a.link, Datasheet) stay underlined.
- The result tabs on a phone are a six-column grid, each tab spanning two columns: three to a row, three rows for seven tabs,
  the odd last one spanning the row (:last-child:nth-child(3n+1)), two leftovers half each (4).
- The key on Connections and Find a port is three plain swatches, no numbers (.ft-cell.sw): the filled "1" had looked like a
  chosen port (6).
- The Layout tab's zoom controls (7): the device bar (#dv-bar) had stayed on the other tabs because renderOne re-ran on every
  view refresh and unhid it; it now shows only when RES.tab is devices. The 2D controls (.xi) float fixed at the bottom right
  on a phone; the drawing frame's entrance animation (.fr, keyframes gr, fill both) left an identity transform on the frame,
  which made it the containing block of the fixed controls, so the frame's animation is off on a phone.
- Every arrow that opens something is a navy chevron in a 28 px ring: .prow/.lrow/.irow .arr (Devices list, Needs a look rows),
  every .chev (History and Home rows), Profile rows; dropdown chevrons (How to film, Details, details.card, .idhow) are navy
  and 2.5 px; the Next strip's arrow and the back arrow are bolder (8, 10).
- Upload a video has a 2 px navy frame, a navy icon tile and a tinted ground (9).
- Item 12 was her note that everything earlier is fixed; nothing to build.
- Proof: scratchpad proof4/before.py and after.py (412 px at 2x, 360 px, 1280 px as techt), measure.py (the device row's
  overflow at 360: nav 236 px in a 184 px column, document 379 px wide; after: no overflow), tools/proof_result_tabs.py on every
  run, the 28 tests pass.

## State at 10 Nov late night (the mobile pass: every screen on an iPhone)

The owner's words after the fourth list: "go through all ui screens of mobile in simulator and test, the ui in mobile is
horrible". Every screen and state was opened on Safari's engine (Playwright WebKit, iPhone 17 Pro viewport 402 x 874 at 3x,
mobile user agent, touch) as techt and as orgownert: 62 screenshots each in scratchpad mobile/wk, the after pictures in
mobile/wk2 (scripts shoot.py to shoot6.py). The native shell on the booted simulator (com.racktrack.sweep) still remembers an
old server address (10.10.1.13:8600) and the engine listens on 127.0.0.1 only; changing the address there needs typing on the
simulator, which is not done on this Mac, so the simulator's own Safari was opened at the sign-in only (simctl openurl) and
every signed-in screen was tested on the same engine through Playwright. What was wrong and what changed (flow.css 22,
app.js 172, features.js 14):
- The result tab rows took 330 px of a 874 px screen (three rows of 38 px pills with 6 px gaps inside a padded bar). The
  same three rows are 119 px now: 32 px cells, 4 px gaps, 12.5 px text.
- A device or unit page: the rack locator was a column taller than the screen (height:auto, aspect ratio of the rack), the
  facts beside it ended a third of the way down and the rest was white. On a phone the locator is a strip of at most 34vh
  beside the facts (body:not(.web) #dv-one .loc).
- The device page opened with four rows of "not read" (serial, asset tag, labels, cable labels) above the device's own
  picture. When nothing was read the block is one line, and the block sits under the picture (idsBlock inserted after the
  first figure in renderOne).
- Home rows: the name was squeezed into a third of the width next to the chip and "None yet" sat under a chip that already
  said Not compared yet. The row is name, chip, date; the compare source shows only when there is one; the Scan a rack
  button spans the width; the lead is one sentence.
- A stopped run showed an empty "The picture of the rack appears here" frame above seven stage rows with the reason at the
  bottom. The reason now sits under the title and the frame is hidden when there is no picture.
- The Rack tab's ID choices scrolled sideways off the screen; they stack now. The caption under the rack picture wraps
  instead of cutting off. The Scan page's grey disabled Read the rack button under the two cards is hidden until a video is
  chosen. The 2D layout drawing fills the width (svg.Ua width 100%). The Find a port picture is at most 42vh so the Device
  box is in view. The Database copies Every copy table is one block per copy on a phone (the nine-column table had wrapped
  the date one word per line). The header avatar is a ringed circle. The Details dropdown's hint ellipsizes instead of
  wrapping to two lines.
- Proof: tests pass, tools/proof_result_tabs.py on every run, the wk2 screenshots.

## State at 10 Nov, late (the tester's feedback document and the compact phone view)

The tester sent "UI_Feedback_and_Reference_Images 1.docx" (7 points with her Android screenshots; the file sits in the repo
root, untracked). The owner, the same night, after the mobile pass: the UI, alignment, fonts and spaces looked clumsy on
Android and iOS, with information users do not need; Profile should not have Install on the phone or the accuracy page; Home
should be a title, a subtitle and a button; the two Scan choices should look alike and appealing; How to film should look
different. Built together (flow.css 23, features.css 9, app.js 173, features.js 15); page for her: "Fifth Round Fixes"
(scratchpad tester6/build.py, her screenshot before and ours after on each point; URL in the session's final message).
- Find a port: the dashed placeholder card is gone (fp-out stays empty until a device and a port are chosen); the hint line
  is shorter; the picture at most 42vh (1, 6).
- Connections key: keyHtml(true) renders each state as a button (data-key); a tap writes what the state means and how many
  ports are in it, outlines them (.khl) and offers "Show port N of Dx", which scrolls to and clicks the first; no such port
  says so (2).
- The header's second line falls back to the run's title on the result tabs, as the tool tabs already did (3).
- The accuracy page lost its two "not measured yet" rows and the explaining sentence (4); then the whole RackTrack group
  (How accurate it is, Install on the phone) left Profile along with the three counters and the footer line (owner). The
  #models screen still exists at its hash; the install handler is guarded for a page without the row.
- How to film is a row of three numbered tiles (.filmrow/.filmsteps), no dropdown (7).
- The compact phone view (max-width 760): mono labels become the app font in sentence case (eyebrow, glabel, fx-eye);
  cards 14 px padding; Home on a phone (body:not(.web)) shows only RackTrack, one line and Scan a rack (#home-racks hidden;
  .web-only / .phone-only spans in the Home head); the two Scan choices are one navy gradient card style with an icon tile and
  a chevron; the rack ID question is a plain heading; every long explanatory sentence in index.html, app.js and features.js
  cut to one line.
- Proof: tests pass, every tab of every run (tools/proof_result_tabs.py), every screen shot on WebKit at iPhone size
  (scratchpad mobile/shoot7.py, wk3) and on Chromium at 412 (cr3).

## State at 11 Nov, small hours (the owner's night pass: standard type, Home, Profile, Report, Layout, the result bar, motion, the rack ID)

After the compact view the owner asked, in one burst: a rack drawing on Home; every font, size and alignment checked letter by
letter on every screen with a second verification; Profile simpler and appealing; animations and effects on every page and
button; the Report as a document that opens nothing; the Layout tab without the colour legend, fitted to the screen, with motion;
the Connections key in one row without the note; the result page's tabs as a bottom bar of four and a More button; the rack ID
not asked when we are sure; the header naming the open tab instead of "Result". Built together (flow.css 24, features.css 10,
app.js 174, features.js 16, server/app.py).
- Type audit (scratchpad mobile/audit.py): every visible text element on 16 screens measured on WebKit at iPhone size
  (family, size, weight, transform, spacing, left edge), before and after. After: one family (Inter) on every screen, the
  drawing bundle's own --font (Geist) overridden inside .rteos; nav labels 11 px; page titles 22 px; titles and bold at 600;
  mono labels gone on a phone; the audit files audit.json / audit2.txt / audit3.txt are the two verifications.
- Home: an inline SVG rack (.homeart) between the line and the button, LEDs blinking (rt-blink), the button with a slow glow.
- Profile: a centred hero (dark avatar disc, name, role · site), no group labels, no counters, Sign out centred and red.
- Report: renderReport builds an <article class="doc"> with a ruled header, five numbered sections (Summary, The rack,
  Devices, Free space, Needs a look) and a footer; the device rows and the elevation open nothing (the three listeners are
  gone, the rows are divs, the table rows carry no data-dev).
- Layout: #rt-legend hidden; on a phone the 2D drawing's svg is height calc(100svh - 268px), width auto, so the whole rack is
  on screen (zoom in enlarges it); the "Tap a device to open it" line is hidden; the drawing grows in (rt-grow).
- Connections: the key is three equal cells in one row, no note; a tap still outlines the ports in that state.
- The result bar: on a phone #rnav is fixed at the bottom with Rack, Devices, Layout, Report and More; moreBar() in app.js
  moves Find a port, Connections and Cables into a sheet (.rmsheet) that More opens, and moves them back on a laptop; the
  header title is the open tab's name (TABNAME); the device bar and the Layout zoom controls sit above the bar.
- The rack ID: server auto_rack_id(j) sets rack_id {id, source "read from the label", auto true} when the label read off
  the video agrees on at least 3 reads at 0.5 or better, or 2 at 0.7 (RACK_ID_SURE); it runs after the label read and when a
  view is built, never over an ID a person set or a skip. The Rack tab then shows the ID with Change ID and no question.
  On the current runs the two with a label (fx1hyd000001, 785fb92f6137) already had person-set IDs, so the rule is proven by
  the unit test only.
- Motion (flow.css, prefers-reduced-motion aware): screens rise in with a stagger (fill-mode backwards, never both: a kept
  transform pins fixed children), buttons and rows press, chevrons slide on press, the active tab pops, the filming tiles and
  the report's sections stagger in, the done bar and toasts slide.
- Proof: the audit twice, every screen shot on WebKit (mobile/wk4) and Chromium (mobile/cr4), tests, tools/proof_result_tabs.py.

## State at 12 Nov morning (four fixes from the owner's phone)

- Home sits in the middle of the screen: the home screen is a flex column of the viewport's height with its content centred.
- The Scan page no longer moves: .scanpage had a min-height 17 px taller than the room under the header, so every phone
  could scroll a little; on a phone it has no min-height now.
- The camera guide starts with the view turned the other way on Android (flipView defaults to true there; a saved choice
  from the Flip button still wins), because the owner and the testers hold the phone with its bottom to the right and the
  guide assumed the other way. iPhone keeps the old default.
- The History page is called My racks everywhere, as the Profile row calls it (DETAIL and TT in app.js, the h1).
- The tunnel: after the Mac restarted only the engine, the mocks and the front door could be brought up from here; starting
  ngrok is refused for this session, so the owner runs `tools/boot.sh tunnel`.
