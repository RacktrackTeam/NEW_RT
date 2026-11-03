# Verification pass 2 (3 Nov 2026, 09:38 to 10:15)

Second fresh-eyes pass on branch rt-eos-integration at commit 7cd6203. This pass finds and lists; it changed no code.

How it was run
- Services: nothing was listening on 3302, 3303, 3304 or 8700 at the start. I started the mocks with ./sources.sh (3303, 3304) and the sign-in service with ./auth.sh (3302, the live data/auth/.env and auth.db; CORS_ALLOWED_ORIGINS given in the environment with 127.0.0.1:8707 added to the four in .env so a browser on my engine could sign in and out). They are still running (pids 7117, 7125, 7127): stop with kill $(lsof -ti TCP:3302) $(lsof -ti TCP:3303) $(lsof -ti TCP:3304). No request went to 8700; the 8700 engine was never started or touched.
- My engine: ./engine.sh's environment with RACKTRACK_PORT=8707 and RACKTRACK_RUNS, RACKTRACK_SOURCES_DB and RACKTRACK_CONN_KEY_FILE pointing at a copy of data/runs, data/sources.db, data/sources_by_org and data/connections.key in my scratch folder. Stopped at the end.
- Every organization already holds a run of vdo1, and an upload whose bytes match a run the person can open reopens that run. For one real run I moved Second Organization's copy of the run (fx2pun000001) aside in my copy only and uploaded samples/video/vdo1.mp4 as tech2 (Second Organization, Pune DC) through the Scan screen. That one run failed after 33 s (finding 1).
- Function pass: headless Chromium (headless shell 1228) at 1280 and 400 px. Sign in and out as owner, orgowner, admin, tech; Home, Scan, Profile and every Profile page per role, including the links to the sign-in service's /org/ screens; the stored run dm1hyd000001 (Demo Organization) as orgowner at both widths: Rack, Devices (all ten device pages), Find a port (every device, ports 1, middle and last), Connections, Cables, Layout, Report, the compare with Excel, NetBox and ServiceNow CMDB (menu, status line, confirm panel, Change and the picker, Confirm, Short, Detailed, Back to the report); fx1hyd000001 as owner (acting in Test Organization) at 1280; the earlier scans list as owner and admin3; Cables with the planted workbook (tools/plant_features_workbook.py, imported as admin into my copy) and the A-end / Z-end check with its CSV. The compare also ran in the macOS WebKit through a WKWebView runner at 1280 and 400. The runner named in the brief (scratchpad/swk/swk.swift) was not in any scratch folder, so I wrote a small one in my own scratch (not committed).
- Numbers checked against the view JSON, the drift JSON, data/ground_truth/SP-HYD-RM01-R01-R1/netbox_devices.csv, the mock's answers and the copies.
- Code pass: every route and the middleware in server/app.py and server/rt_session.py, src/rt_features.py, the SQL in src/source_db.py, src/rt_features.py and src/connections.py, searches for outside paths, secrets, long dashes, swallowed errors and dead code, the Python tests (132, run under the model lock with HF_HUB_OFFLINE=1).
- Left behind in shared data, then removed by me: data/checks/1/86f2cce0b0.json, data/checks/1/92c813830c.json (A-end / Z-end checks) and data/sources/orgs/1/workbook_2fd09f57_vdo1_planted.xlsx (the planted upload); the engine writes these outside RACKTRACK_RUNS and RACKTRACK_SOURCES_DB (finding 41). The sign-ins added sessions to data/auth/db/auth.db, as any sign-in does.
- My run made the engine call the Hugging Face Hub while loading a model (finding 2). That call came from the engine itself; I could not stop it without changing engine.sh.

## Pass 1 findings marked fixed: re-checked

Format: pass 1 number - verdict - what I saw.

- 1 - Fixed - tech import, tech copy delete, manager import, manager login save and tech2 run delete all answer 403 with the reason. The failed-run screen still offers Remove to a technician (finding 5).
- 2 - Fixed - rack_id_options for dm1hyd000001 (orgowner) and fx1hyd000001 (admin3) hold only the label read and the next ids in sequence.
- 3 - Fixed - /racks-report/racks_report.html answers 404 to admin and 200 to owner; /models-overview 200 to owner.
- 4 - Fixed as stated (specs from the local knowledge base, web search off) - but the engine still leaves the machine when it loads the occupancy models (finding 2).
- 5 - Fixed for port states - D4 is 19 connected, 8 empty, 1 not sure on its page and in Connections; Layout 254 cabled. The device card's cable line still comes from the other reading (finding 11).
- 6 - Fixed - physical layer units_count 22, topology unitsTotal 22.
- 7 - Fixed - SP-R1-U24-PDU is a note "not checked: the video read U1 to U22 only".
- 8 - Fixed - step 3 lists DGS-1024C, TL-SG2428P, TL-SG2428P, DGS-1210-52, each paired once.
- 9 - Not fully fixed - the page and the Report show "CTS ACX-09CW2A(SM-10)-EL read, not confirmed", the Devices list still shows D1 as "D1" with no make (finding 9).
- 10 - Not fully fixed - the page sentence is right, the rest of D2 is not (finding 13).
- 11 - Not fully fixed - D6 counts 52 with Combo 4, the specifications block on the same page says 56 (finding 12).
- 12 - Not fully fixed - Needs a look lists devices only, no port (finding 10).
- 13 - Fixed - owner and admin3 see the same compare state for fx1hyd000001; owner and orgowner the same for dm1hyd000001.
- 14 - Fixed - Home rows per organization and site; admin3 sees no earlier scan on fx1hyd000001.
- 15 - Fixed, with a new small gap (finding 36).
- 16 - Fixed - history summary "10 devices, 10 named".
- 17 - Fixed - by code reading (owner.json on upload, _take_visible on list, video, thumbnail and run); GET /api/captures is empty for tech, team_folder false.
- 18 - Fixed - example-vdo1 answers 404 to admin, 200 to owner.
- 19 - Fixed - no Site position entry for tech; #tools/site says only a site manager or an organization admin can set it.
- 20 - Fixed by code reading - not exercised live (the one run failed in 33 s).
- 21 - Fixed for the stated case - a latent failure remains (finding 18).
- 23 - Not fully fixed - the listed places log now; other silent swallows remain (finding 40).
- 24 - Fixed - seed-local-pilot.js and create-org.js read RT_SEED_PASSWORD.
- 25 - Fixed in code - no /Volumes or /Users path in server, src, mobile, scripts, tools, tests, port_agents code, rt_eos server or pipeline; but the copied runs' status.json still name the live app's folder (finding 16).
- 26 - Fixed - rate_limit.js line 64 "Too many ... just now. Try again in N s."
- 27 - Fixed for auth.db - the same kind of proof leftovers remain in the database copies (finding 8).
- 28 - Fixed - tests/test_engine_routes.py passes; but no test loads the occupancy models (finding 42).
- 29 - Fixed - dm1hyd000001 opens for orgowner, admin and tech.
- 31 - Fixed - no RT-EOS name or schema id on the Read again, Physical layer, Topology or SFP screens.
- 32 - Fixed for the wording - but the failure sentence blames the video for a server fault (finding 4).
- 33 - Fixed - seven tabs fit at 1280; the phone bar scrolls sideways.
- 34 - Fixed - device facts one per row.
- 35 - Mostly fixed - two other date styles remain (finding 31).
- 36 - Fixed in part, as pass 1 said - the label step text is now cut off with "..." and the crop at 400 px is still a thin strip (finding 26).
- 37 - Fixed - on a phone the compare takes the top of the Report.
- 38 - Fixed - "47 RJ45 + 1 hidden".
- 40 - Fixed - tech's Profile has Account with Settings and Sign out, no empty Organization heading.
- 41 - Fixed - "1 port".
- 42 - Fixed - "the local test copy".
- 43 - Fixed - Cables said "No cable to check" before the planted workbook.
- 44 - Fixed - by code (Home counts only a confirmed compare).
- 45 - Fixed - "D-Link 48-Port Patch Panel" with no repeated make.
- 46 - Fixed in part, as pass 1 said - more dead code is there now (finding 38).
- 47 - Fixed - by code (127.0.0.1:8700 default).
- 48 - Fixed - AUTH.md matches the users in auth.db.
- 50 - Fixed - by code (3 a minute per sender, 60 in all).
- 51 - Fixed for the files it named - long dashes remain elsewhere (finding 39).
- Left by pass 1 and still open: 22 (copies per organization, not per site), 30 (WhatsApp runs only the platform owner's), 49 (the two "default" logins on 127.0.0.1:3301 and 5301 are still in data/sources.db), 52 (now reproduced: finding 30). 39 was not a fault.

## Findings

Format: where - what I did - what I saw - what is wrong - severity.

1. /Volumes/Racktrack/racktrack-app-integration/models/v2/classical_clf.joblib and models/v2/embed_clip_clf.joblib, loaded by src/occupancy.py lines 192 and 217 - uploaded samples/video/vdo1.mp4 as tech2 and watched the run page; then loaded each models/v2/*.joblib alone under scripts/with-model-lock.sh - the run stopped after 33 s at Units; the engine log says "ValueError: <class 'numpy.random._mt19937.MT19937'> is not a known BitGenerator module"; classical_clf fails the same way with PCG64, embed_dinov2, side_angle and teammate_clf load - the two files were pickled under numpy 2 (7 Oct) and _cache/venv has numpy 1.26.4 (installed 2 Nov), so the Ensemble cannot load and no video can be read in this build: every scan fails - blocks.
2. /Volumes/Racktrack/racktrack-app-integration/engine.sh, src/teammate2_approach.py line 141 and src/device_grouping.py line 201 (open_clip pretrained="openai"), src/teammate1v2_approach.py line 48 (torch.hub.load("facebookresearch/dinov2", ...)) - read my engine's log during the run and the installed torch/hub.py - the log shows "You are sending unauthenticated requests to the HF Hub"; torch/hub.py _parse_repo_info opens https://github.com/<owner>/<repo>/tree/main/ on every load when no branch is given; engine.sh sets no HF_HUB_OFFLINE or TRANSFORMERS_OFFLINE; the weights are read from ~/.cache/huggingface and ~/.cache/torch, outside this folder - every run reaches huggingface.co and github.com (hard rule 2) and the engine depends on files outside the folder (brief: "this folder stands on its own") - blocks.
3. Profile links to 127.0.0.1:3302/org/ (Platform console, Organization settings, Sites and people, Settings; rt_eos/client/dist-org) - opened each as owner, orgowner and tech at 1280 and 400, then /org/, /org/scan, /org/map, /org/history as tech - every page is a second app with its own Home, Scan, Map and History bar and no way back to /app/; its Home says "Check a rack in one photo", Scan offers Photo, Stitch and Video, Map offers "Map two racks" from photos, History says "Not found"; Sites and people says "Demo Organization. 4 people, 0 racks", "No rack has been read at this site yet" and "No scans yet" for tech, while Hyderabad DC holds dm1hyd000001 - a person leaves the app into the photo pipeline this version removed, and that app contradicts the engine's own racks - wrong.
4. /Volumes/Racktrack/racktrack-app-integration/server/app.py lines 338-346 (run_job) and the run screen (mobile/app.js) - watched the failed run at 1280 and 400 - "The reading stopped", red sentence "The rack could not be read from this video. Try again, or film the rack again slowly from top to bottom."; the stage list loses its ticks and marks "Reading the video" as the failed step although stitching had finished - a server fault is blamed on the person's filming and placed at the wrong step - wrong.
5. /Volumes/Racktrack/racktrack-app-integration/mobile/index.html line 248 (#job-del) and mobile/app.js line 381 - opened the failed run as tech2 and called the same DELETE - "Remove this run" shows; the server answers 403 "Only a site manager or an organization admin can remove a scan." - the button is offered to a role that may not use it - wrong.
6. /Volumes/Racktrack/racktrack-app-integration/src/source_db.py lines 326-345 (import_netbox) and 347-372 (import_cmdb), against modules/sources/mock/netbox.js lines 11-12 and 102-103 - read Profile > Database copies and the drift JSON - all 26 NetBox and CMDB copies of Demo Organization have Ports 0; the NetBox and CMDB compares say "10 devices compared" with no ports compared, so the 8 real differences on ports 25 to 28 of both TL-SG2428P show only against Excel; the mock serves /api/dcim/interfaces/, front-ports, rear-ports and cables but the import never reads them - the brief's copies hold racks, devices, ports and cables per source; port drift and cable checks exist only for Excel - wrong.
7. /Volumes/Racktrack/racktrack-app-integration/data/sources_by_org/1/sources.db (read only) and the mock on 3303 - compared Demo Organization's active copies with today's mock and with Test Organization's fresh copies made by the owner in my walk - Demo's active NetBox v14 has SP-R1-U7-PP3 at U8 and CMDB v12 has SP-R1-U18-SW04 made by Cisco (planted by a proof on 2 Nov, 19:57); today's mock has PP3 at U7 and SW04 by D-Link, and Test Organization's fresh copies give 0 differences; orgowner, admin and tech see "1 moved" and "1 wrong make" as real drift with nothing saying they are planted, and the list holds 26 NetBox and CMDB copies from proof runs - the demo accounts show fake drift - wrong.
8. Devices tab list (mobile/app.js, the dv-list rows) - opened the list at 1280 and 400 - D1 shows the title "D1" and no make, while its page says "Make CTS read, not confirmed" and the Report "CTS ACX-09CW2A(SM-10)-EL read, not confirmed" - pass 1 finding 9 is not fixed on the Devices list - wrong.
9. Side bar at 1280 (mobile/index.html #site2) - screenshots as tech and tech2 - the role and site line wraps over two lines ("Technician · Pune" / "DC", "Technician ·" / "Hyderabad DC") next to the brand - text broken in the side bar - nit.
10. Report > Needs a look, /Volumes/Racktrack/racktrack-app-integration/mobile/app.js lines 892-895 - read the Report and GET /api/video/dm1hyd000001/worklist - Needs a look lists "D1 · needs a close photo" and "D2 · model not read" only; the worklist holds 25 ports ("Is there a cable in port 2 of D3?" and 24 more) and the code has no port part - pass 1 finding 12 claimed the not-sure ports; they are not there - wrong.
11. Device pages D3, D5, D6 (mobile/app.js device card) against server/app.py line 655 ("connected") and "counts" - read the pages and the view JSON - D3 "12 in use · cables: 11 white RJ45", D5 "10 in use · cables: 8 white RJ45, 1 black RJ45", D6 "20 in use · cables: 13 white RJ45, 3 black RJ45"; the view sends layout.connected 12/10/20 and layout.counts.connected 11/9/16 - two readings of the same ports still sit on one card - wrong.
12. Device page D6 (D-Link DGS-1210-52) - read the facts and The model block - "Ports 52 (52 RJ45), Combo 4" and below "Specifications: Ports 52x RJ45 + 4x SFP, All ports 56"; the model has 48 RJ45 and 4 combo ports - the specifications contradict the count above them and the model - wrong.
13. Device page D2 (Firewall, U19), the drift notes and the physical layer - read all three - the list and page pill say "Ports found" for 1 port where the record has 9; "Empty: all 1"; How it was read "found and typed from the photo" (it is a video); the drift note says "SP-R1-U19-FW has 9 ports in PORT_INVENTORY but the video read none of its ports"; the physical layer counts 332 ports and 22 empty where the device pages add up to 333 and 23 - D2's one port is shown on its page and dropped everywhere else - wrong.
14. Cables screen, src/rt_features.py cable_check against az_check - ran Cables with the planted workbook, then Check the cable labels - the cable check says "Wrong port: CBL-PL-032 should be on port 21 of SP-R1-U18-SW04; port 21 is empty and port 24 has a cable no row names, so it is likely in port 24"; the A-end / Z-end table on the same screen says "CBL-PL-032 Missing cable" - one cable, two verdicts on one screen - wrong.
15. Rack tab and Report (free space) against the ground truth - read both - "8 free", "Free space 1U U16, U14, U12, U9, U6, U3"; the record has cable managers at exactly U3, U6, U9, U12, U14 and U16; the drift lists them under Not checked, the Rack and Report never say so - six units holding cable managers are offered as free space - wrong.
16. Device pages, "Seen in the video" (server/app.py lines 790-806, src/rack_video.py lines 1170-1193) - walked all ten device pages and fetched /api/video/<run>/crop/<i>/1 - every other view answers 404 "No other view." for dm1hyd000001 and the owner's 91af02c09bbc; 60 console 404s on one walk (each image retried three times); the card says "The sharpest views" with one picture; the copied runs hold only video.mp4 (landscape 1280x720) while the frame boxes belong to the upright copy that was not copied, and their status.json video_file still names /Volumes/Racktrack/racktrack-app/data/runs/91af02c09bbc/video.mp4 (the live app) - broken pictures on every device page and an outside path in the run data - wrong.
17. /Volumes/Racktrack/racktrack-app-integration/server/rt_session.py lines 93-96 - read the code - a database error while checking revoked_tokens is passed over and the token accepted - a signed-out token is let through when that check fails (fails open) - wrong.
18. /Volumes/Racktrack/racktrack-app-integration/server/app.py lines 1887-1912 (_features_read_again_thread) - read the code - j.setdefault("ports", {}) returns None when status.json held "ports": null (load_runs keeps the key), so read again on such a run fails at once without reading; and each device failure is caught inside _read_ports_thread, so read again ends "done" with every device failed - the screen has no link today (finding 38) - nit.
19. /Volumes/Racktrack/racktrack-app-integration/server/app.py api_add_device, api_mark_unit, api_remove_device, api_set_model, api_fix_kind, the worklist answer and the read and close-up threads - read the code - each reads result.json, changes it and writes it back with no lock; remove renumbers device files and port states with no check that a device read is running - a person's edit and a background read at the same time can lose one of them or put a read on the wrong device - wrong.
20. Device pages D4 and D5 (TL-SG2428P), /api/specs - read the page and the JSON - "Ports 24 x 1G RJ45 + · 4 x 1G SFP" - garbled text from the ports clean-up in server/app.py lines 1034-1041 - ugly.
21. Every screen at 1280 (Home, Scan, run, result tabs, tools, compare) - measured the visible content - left gutter 32 px, right gutter 83 px (content ends at 1197 of 1280) - the standard and the owner want one 32 px gutter and the full width - ugly.
22. Report at 400 px, the device table (mobile/app.js line 887, .rptable) - screenshot - words broken mid-word ("Rout er", "Swit ch", "Fire wall", "confi rme d", "port s", "D 1") and "U10-11" cut to "U10-1" - text cut and broken on a phone - ugly.
23. Profile > Database copies at 400 px - screenshot - "ServiceNo" / "w CMDB" broken mid-word in every CMDB row, each date over four lines - ugly.
24. Primary actions on the left: Scan "Read the rack" (left of its block), device page "Take a close photo" and "Pick another device" (left of the secondary), the run page's only actions are secondary ("Try again" right, "Remove this run" left), Home's "Scan a rack" full width at 400 px - screenshots at both widths - the standard puts the primary button at the right of its block - ugly.
25. Rack tab, device list beside the picture at 1280 - screenshot - kinds cut to "Ro...", "Fire...", "S...", "P..." - text cut at laptop width on an approved screen - ugly.
26. Confirm panel, Rack label step (mobile/app.js firstSentence, line 943) - read at 1280 and 400 - "the label reader read SP-HYD-RM01-R01-R1, 52 % sure; a person confirmed it, so this step counts 95 %, exact..."; at 400 px the label crop is still a thin strip where the label is hard to read - a sentence cut off mid-word, and pass 1 finding 36's crop left as it was - ugly.
27. Tool screens on a phone (Find a port, Connections, Cables) - screenshots at 400 - the header says "Result" with no rack name, while the Report's header shows it - the standard keeps the rack name in the header on every result screen - ugly.
28. Find a port, port facts - opened D4 port 14 - "Cable plugged in" above "Cable none, Label none, Other end none, Cable type none"; "Connector RJ-45" where every other screen writes RJ45 - "none" reads as no cable; it means not in the record - ugly.
29. Find a port and Cables at 400 px - measured - the device and port pickers run to 390 px (10 px right gutter) - nit.
30. Sign-in screen and Sign out, every role, both widths - watched the console - two 401 errors when the sign-in screen loads (/api/me and /api/auth/refresh) and one after Sign out - pass 1 finding 52, reproduced on 8707 - nit.
31. Dates - read Home, the run title and the unit answers - a new upload is titled "tech2's rack, 03 Nov 09:41" (server/app.py line 573); unit answers say "Marked free on 3 Nov" (lines 1129, 1207); everything else "27 Oct 2026, 12:53" - three date styles - nit.
32. Home - read the rows - "Not compared yet" in Compared with and a "Not compared" pill on the same row - the same words twice - nit.
33. Screen words - read every screen - "the label reader read ..." starts in lower case; "4 of 4 reads found in this rack"; "No cable type for this device in the sheet"; the A-end / Z-end check "0 not in the sheet" and its CSV columns "Sheet device", "Sheet port"; a Why column that is "-" on every row; copies "From http://127.0.0.1:3304" (a raw address); a "TAP" badge on the laptop and "Click a picture" on the phone; SFP advice for 1 Gbps ports "(up to 300 m at 10G)"; "47 RJ45 + 1 hidden · 48 in use" counts the hidden port as in use - plain words and small contradictions - nit.
34. /Volumes/Racktrack/racktrack-app-integration/server/app.py find-port routes - called with device=abc, port=-1 and port=9999 - device=abc answers FastAPI's raw 422 {"detail": [...]} (not the {"error"} sentence the app shows); port -1 and 9999 answer 200 with found false instead of "no such port" - nit.
35. Report > Earlier scans of this rack, as owner on 91af02c09bbc and fxlegacy0001 - opened both - each lists the other as an earlier scan (same date and time) - nit.
36. Profile as owner - read the numbers - "6 racks read" while Home lists 7 rows (the failed run is a row on Home, not a rack in Profile); History says "5 port layouts ready" while all ten devices have ports on their pages - nit.
37. Sign in at 400 px - screenshot - "Forgot password" and "Contact us" sit off centre (their middle at about 155 px of 400) - nit.
38. Dead code - read server/app.py, mobile/app.js, mobile/features.js, src, port_agents - GET /api/video/{job}/worklist and POST /worklist/{item}, read-again, physical-layer and physical-layer.html, topology and their screens (#tools/<run>/again, physical, topology; fillResult, toolList, #sec-tools, the #more-link handler) have no link left; POST /api/video sample=vdo1 is called by no screen; /racks-report, /api/racks-report (three hard-coded legacy runs) and /models-overview stay; src/more_datailed_1.py is imported by nothing; port_agents/*/switch_template_agent/template_agent/vision/ocr.py claude_read (the Anthropic API) has no caller - nit.
39. Long dashes - searched with Python for U+2013 and U+2014 - 95 files: mostly comments, among them src/unit_grid.py line 1, src/teammate2_approach.py lines 105-106 (a description string), src/more_datailed_1.py line 512 (builds the range "U4", en dash, "U5"), rt_eos/server/app.js (161), rt_eos/pipeline (comments and help texts), scripts/occupancy/build_walkthrough_report.py line 246 (puts an em dash on a page), docs/CROSSCHECK.md line 24 (quoted) - none on a screen I captured - nit.
40. Silent swallows - read the code - server/app.py line 265 (_summary returns None, no log), line 283 (a run whose status.json does not parse disappears with no log), lines 298 and 568; src/connections.py line 66 (unseal returns None on any error, so a login reads as not saved with no reason); sqlite connections opened with "with sqlite3.connect()" in src/source_db.py _db and src/connections.py _db are committed but never closed - nit.
41. /Volumes/Racktrack/racktrack-app-integration/server/app.py lines 219, 980, 1551, 1685 - ran an engine with RACKTRACK_RUNS and RACKTRACK_SOURCES_DB set to a scratch copy - workbook uploads still went to data/sources/orgs/<org>, A-end / Z-end checks to data/checks/<org>; data/contact and data/specs_cache.json are fixed too - a test engine writes into the live data folder whatever the settings say (I removed the three files mine made) - nit.
42. /Volumes/Racktrack/racktrack-app-integration/tests and tests/conftest.py - ran all 132 tests under the model lock - 132 pass in 9 s; none loads the occupancy Ensemble or runs a video, so finding 1 passes the suite; conftest.py turns the port agents' web search off but not the Hugging Face or torch hub lookups - the suite cannot catch the two blockers - wrong.

Checked and fine
- A stranger to a run gets 404 on every run route tried, the new ones included: view, drift, find-port, find-port.jpg, connections, cables, worklist, read-again, physical-layer and .html, sfp-advice, topology, crop, file, rack-label.jpg, video-report, and the writes rack-id, database, read-again, worklist answer, device kind and run delete (tech2, admin3, orgowner2 against Demo Organization's run). Unsigned calls get 401. POST /api/cables/az across organizations answers 404; the A-end / Z-end CSV opens for the same organization and answers 404 to another.
- The copies and connections routes with ?run=: only the platform owner without an organization acts in the run's organization (owner sees Demo's 30 copies with ?run=dm1hyd000001 and the platform's 18 without); admin2, admin3 and tech always get their own organization's copies, ?run= or not. The owner's NetBox and CMDB copy-in from fx1hyd000001 landed in Test Organization's copies.
- All SQL runs on the organization's own database file (src/source_db.py db_path, src/rt_features.py through SDB._db); site positions read the sign-in database filtered by organization; no secret or password in code; no outside path in code.
- The compare: the confirm panel's steps match the drift JSON and the ground truth (rack id SP-HYD-RM01-R01-R1; layout 10 of 10 in Excel and CMDB, 9 of 10 in NetBox with its PP3 move); Change opens the picker with the copy's racks; Confirm shows the drift; Short and Detailed agree; Back to the report restores the report. In WebKit at 1280 and 400 every section shows (the Safari fix of 543a842 holds).
- The planted workbook: the cable check finds the three planted faults (missing cable on U20 port 2, wrong port SW04 21/24, extra cable SW03 port 23); the workbook report shows 32 cables, 18 devices, 346 ports and the 346 LOOKUPS warnings RT-EOS also gives.
- tests/test_device_routing.py and tests/test_read_device_ports.py: not reproduced as failing at collection. Both collect (10 tests) and pass 10 of 10 with _cache/venv from the repo root and from tests/, and they collect with the system python3 too. Their port agents are stubbed (by design), so they say nothing about the real models.
- No long dash in any captured screen text; no page error in the browser; white background on every screen including the /org/ pages.

## Count by severity

- blocks: 2 (1, 2)
- wrong: 16 (3 to 8, 10 to 17, 19, 42)
- ugly: 9 (20 to 28)
- nit: 15 (9, 18, 29 to 41)
- total: 42

## Pass 1 items not really fixed

- 9 (D1's make and model missing on the Devices list: finding 8)
- 10 (D2 still "Ports found", "from the photo", and dropped from the drift note and physical layer: finding 13)
- 11 (D6 specifications say 56 ports: finding 12)
- 12 (Needs a look has no ports: finding 10)
- 23 (more silent swallows: finding 40)
- 36 (crop at 400 px still a strip; the step text now cut: finding 26)
- 52 (marked left as not reproduced; reproduced here: finding 30)
- Partly: 1 (Remove offered to a technician: finding 5), 4 (outside calls at model load: finding 2), 5 (cable line on the card: finding 11), 25 (run data names the live app: finding 16), 27 (proof leftovers in the copies: finding 7), 28 (no model test: finding 42), 32 (failure blamed on the video: finding 4).

## The five to fix first

1. Finding 1: re-save classical_clf.joblib and embed_clip_clf.joblib under numpy 1.26 (or align the environment) so a video can be read at all, and add one test that loads the Ensemble.
2. Finding 2: set HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1 in engine.sh and the tests, load DINOv2 from a local copy (torch.hub with source="local" or a saved checkpoint) and move the weights inside the folder.
3. Finding 3: keep Profile inside the app (or strip the /org/ client to the settings pages with a way back) so no one lands in the photo pipeline or sees "0 racks".
4. Finding 7 with 6: replace Demo Organization's planted NetBox and CMDB copies with clean ones and copy NetBox and CMDB ports and cables in, so the demo accounts see only real drift and the port compare works for all three sources.
5. Finding 4 with 5: say plainly when the server failed (not the filming), keep the ticks of the steps that finished, and hide Remove from roles that cannot remove.
