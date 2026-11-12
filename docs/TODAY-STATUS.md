# Today: databases, rack match and drift on a video run (2 Nov 2026)

Everything runs inside racktrack-app's own server (server/app.py) and screens (mobile/). There is no photo pipeline. RT-EOS is not running.

## How to start it

    ./auth.sh        # the sign-in service on 127.0.0.1:3302 (first start: data/auth/.env, the /org/ screens, the seeded accounts)
    ./engine.sh      # the engine and the screens on http://127.0.0.1:8700/app/ (it takes the machine-wide model lock only around a model run)
    ./sources.sh     # the NetBox mock on 127.0.0.1:3303 and the ServiceNow mock on 127.0.0.1:3304 (no model, no lock)

Open http://127.0.0.1:8700/app/ and sign in (accounts in docs/AUTH.md; the password is in data/auth/seed-password.txt). The stored runs from
before sign-in are seen by the platform owner (owner). Open a rack from Home; the compare is in its Report.
Stop them by their real pids: `kill $(lsof -ti TCP:8700 -sTCP:LISTEN) $(lsof -ti TCP:3302 -sTCP:LISTEN) $(lsof -ti TCP:3303) $(lsof -ti TCP:3304)`.

The sections below are the morning and afternoon record; the evening section at the end says what changed since (the Drift tab is
gone: the compare now lives in the Report).

## What works (each one run today)

| Piece | State | What proves it |
| --- | --- | --- |
| Database: Excel | done | The manager's workbook is uploaded on the Drift tab and read with openpyxl by the RT-EOS column names (RACK_INVENTORY: Rack_ID, Rack_Name, Expected_Rack_Label, Rack_Height_RU; DEVICE_INVENTORY: Rack_ID, Device_ID, Device_Name, Device_Role, Manufacturer, Model, Serial_Number, Asset_Tag, RU_Start, RU_Height). Sample rows are skipped. Screenshots drift_excel_*.png |
| Database: NetBox | done | Read through NetBox's REST API (dcim/racks, dcim/devices) from the local mock. Screenshots drift_netbox_*.png |
| Database: CMDB | done | Read through ServiceNow's Table API (cmdb_ci_rack, cmdb_rel_ci, cmdb_ci with display values) from the local mock. Screenshots drift_cmdb_*.png |
| One table set | done | data/sources.db: src_racks (source, rack_key, name, label, height_ru, imported_at, file) and src_devices (source, rack_key, device_key, name, role, kind, manufacturer, model, serial, asset_tag, ru_start, ru_height, imported_at). A new copy of a rack replaces the old one. Nothing is written back to a source. |
| Rack match | done | The run's confirmed rack id (status.json rack_id) against the record's label, id or name: exact first, then with spaces, dashes and case ignored. The tab says which record matched and why, and a list lets a person pick another record (kept in status.json as db). Proved: vdo1 matched exactly; "Network rack 1" and "NETWORK-RACK-2" typed on the two WhatsApp runs matched NETWORK RACK 1 and 2 with the separators ignored. |
| Drift | done | Missing device, extra device, moved (one unit tolerance), wrong kind, wrong make, wrong model, wrong serial, wrong height. One row per difference with the record value, the read value and a sentence. JSON: GET /api/video/<run>/drift (needs the session cookie, as every route). Tests: tests/test_source_db.py, 2 passed. |
| Proof on vdo1 | done | The stored run of vdo1 (data/runs/91af02c09bbc, rack SP-HYD-RM01-R01-R1, the same files as samples/video/run_of_vdo1) against all three sources, each loaded with the rack's record from data/ground_truth/SP-HYD-RM01-R01-R1/netbox_devices.csv (through the sources module's ground_truth/vdo1.json and vdo1.xlsx) and one planted difference each. tools/proof_today.py does it all through the screens in headless Chromium. |
| Other two videos | done, with a caveat | Rack ids confirmed by hand on runs 28c3c52cd91e (NETWORK RACK 1) and 501bcd981b64 (NETWORK RACK 2) and checked against NetBox. Screenshots drift_network_rack_*. Many rows there are real reading problems of those runs (their unit numbers are off by one or two and the heights differ), not mistakes of the compare. |

## The planted differences and what the Drift tab showed

| Source | Planted | Shown |
| --- | --- | --- |
| Excel | SP-R1-U15-SW02 model TL-SG3428 in a copy of vdo1.xlsx | Wrong model: "SP-R1-U15-SW02 is a TL-SG3428 in the record; the video reads TL-SG2428P." |
| NetBox | SP-R1-U7-PP3 moved to U8 in the mock (PATCH) | Moved: "SP-R1-U7-PP3 is at U8-9 in the record and at U7-8 in the video." |
| CMDB | SP-R1-U18-SW04 manufacturer Cisco in the mock (PATCH) | Wrong make: "SP-R1-U18-SW04 is made by Cisco in the record; the video reads D-Link." |

Two more rows show on every source because they are real: the PDU at U24 is missing (the video covers U1 to U20 only), and the device at U20 is a Router in the record while the video reads a Switch.

## What is not checked, and why
- Cable managers (and blank panels, shelves, trays) are listed under Not checked: the video's models have no class for them (the same rule RT-EOS has).
- A device with no unit in the record (the Bharat fibre box) is listed under Not checked.
- A kind the video left as Other or not sure is not compared. Make and model are compared only where the video read them.
- Serials and asset tags are empty in the office rack's CSV and the video reads none, so no serial row can appear today.

## Changes to the plan and why
- The two mocks used ports 3301 and 5301 in the morning; since the sign-in merge they run on 3303 and 3304 (RACKTRACK_NETBOX_URL and RACKTRACK_SERVICENOW_URL set them; only 127.0.0.1 is ever called).
- The Database choice lives at the top of the Drift tab rather than on a screen of its own: one place per run, the least new code.
- No new video run was made: the stored vdo1 run is the same video (a new upload of the same bytes reopens the stored run in the engine).

## Done earlier today (before the scope change)
- RT-EOS moved to rt_eos/ (server tests 399 run, 392 pass, 7 skipped; client tests 285 pass). Not used today.
- The engine's fixed outside paths now read environment variables with defaults inside this folder; engine.sh sets them.

# Afternoon: connections, the workbook report, port drift, copies (2 Nov 2026)

## What works

| Piece | State | What proves it |
| --- | --- | --- |
| Drift kind fix | done | The drift now takes a device's kind as the result screen shows it: a person's correction, then the kind the make and model step identified (layout.kind: router, switch, firewall, gateway), then the classifier. On vdo1 the router at U20 and the firewall at U19 no longer show as a wrong kind on any of the three sources (checked through src/source_db.py against Excel, NetBox and the CMDB copied from the mocks). Test: tests/test_source_db.py test_drift_uses_the_kind_the_result_screen_shows. |
| 1 Connections | done | src/connections.py, copied in shape from dark_mobile's connection_profiles.js (org-scoped half) and netbox/secrets.js: one NetBox and one ServiceNow login per organization (one organization today, "default") in data/sources.db; username, password and token sealed with AES-256-GCM (v1:iv:tag:body) under data/connections.key, made with mode 600 and ignored by git; the screen only gets the address, which fields are held and the state. Test connection: NetBox GET /api/status/, ServiceNow one row of the Table API (cmdb_ci_rack). Copy in from NetBox or CMDB uses the saved login, else the local mock as before. Profile > Connections. Tests: tests/test_connections.py (5). Screens: connections_empty_1280, connections_1280, connections_400 (NetBox Connected against the mock, ServiceNow Not connected on an address where nothing listens; the page text holds neither secret). |
| 2 Excel report | done | src/source_db.py read_workbook(), the inventory checks of rt_eos/server/lib/workbook.js: missing sheet, missing column, a device whose rack is not in RACK_INVENTORY, duplicate ids, a device outside the rack height, overlapping devices, a port of an unknown device, values outside LOOKUPS, Yes or No fields. Read workbook now only checks; the Drift tab shows counts by kind, every problem (first 100 listed) and Import. A missing RACK_INVENTORY or DEVICE_INVENTORY sheet, or a column the import needs, blocks Import. Tests: tests/test_workbook_checks.py. Screens: excel_report_broken_1280/400, excel_report_vdo1_1280. |
| 3 Port drift | done | When the source is Excel and PORT_INVENTORY has rows for a matched device, each port the video read is compared by number (RT-EOS's sheet_check.js rules): Port_Type by family (port_type, Wrong port type), Expected_Occupied against a read state only (port_occupied, Missing cable or Extra cable), Expected_Dust_Cap (dust_cap, Dust cap missing: No is never a fault, Yes counts on any port, blank only on an optical port). The run's ports are taken as the result screen takes them (close-up, fitted layout, else port details). Tests: tests/test_port_drift.py. Screens: drift_ports_1280/400. |
| 4 Copies | done | Every import is a copy (src_copies) with its own version per source; the newest copy holding a rack is the active one; deleting a copy brings the one before it back. Profile > Database copies lists source, version, time, racks, devices, ports, where from, Active or Older, Delete. Tests: tests/test_workbook_checks.py test_copies_versions_active_and_delete. Screens: copies_1280/400, copies_after_delete_400. |

## The planted port facts on vdo1 and what showed

vdo1's workbook with three changes on the router at U20 (SP-R1-U20-ACT): port 1 Expected_Occupied No, port 2 Expected_Occupied Yes, port 3 Expected_Dust_Cap Yes.
- Extra cable: "Port 1 of SP-R1-U20-ACT should be empty; the video shows a cable in it."
- Missing cable: "Port 2 of SP-R1-U20-ACT should have a cable; the video shows it empty."
- Dust cap missing: "Port 3 of SP-R1-U20-ACT is empty and has no dust cap; the record expects one."
- 332 ports compared. Eight real Wrong port type rows show too: ports 25 to 28 of the two TL-SG2428P switches (U15, U17) are SFP cages, and the office rack's port list (from the NetBox CSV) calls them RJ45. The record is wrong there, not the video.

## How the screenshots were made, and what is still owed

The engine on 8700 had been started before this work and could not be stopped from this session, so it still runs the morning's code. tools/proof_afternoon.py has two modes:
- live (default): everything through the engine on 8700 and the mocks. Not run yet: it needs the engine restarted (engine.sh) so the new routes load.
- --shim (what made docs/proof/afternoon/): the screens, sign-in and the run's view come from the live engine (mobile/ is served from disk, so the screens are today's), and only the new routes are answered in the proof's own process by the same modules the engine calls (src/connections.py, src/source_db.py) on a scratch database in data/proof_afternoon_work. NetBox and ServiceNow are still read over HTTP from the local mocks. Chromium gives a route no body for a file upload, so the shim takes the file the proof chose.
Owed: restart the engine, run `_cache/venv/bin/python tools/proof_afternoon.py` (live) and tools/proof_today.py again, and replace the screenshots.

## Notes
- The old data/sources.db layout (no copies) is dropped and rebuilt the first time the new code opens it; its copies must be read in again.
- The office workbook's own LOOKUPS list Port_Type without RJ45, so its 346 port rows each get a "value not in LOOKUPS" warning. RT-EOS does the same.
- The A_Z_CONNECTION_MATRIX rows are counted but not checked: this app copies no cables yet.
- Organizations: one today. The copies are not yet kept per organization (the logins are).

# Evening: cross-check, one flow, rack identification, evaluation, sign-in merged (2 Nov 2026)

| Piece | State | What proves it |
| --- | --- | --- |
| Cross-check, live | done | docs/CROSSCHECK.md: proof_today and proof_afternoon run live for the first time (proof_today fixed to press Import), the whole flow walked at 1280 and 400 px (tools/proof_crosscheck.py, docs/proof/crosscheck), every fix listed there. Those three scripts drive the old Drift tab and name-only sign-in and are kept as the record of that check; tools/proof_flow.py replaces them. |
| One kind per device | done | src/source_db.py device_kind: a correction, then the faceplate, then the picture, with the source in words; used by the view, status, port reader and drift. D1 "Router, from the faceplate; the picture said Switch", D2 "Firewall, from the faceplate" (docs/proof/flow/device_D1_*, device_D2_*). |
| The flow | done | Home lists each rack once (its latest run, the record it was compared with, the number of differences, how many scans). Scan is record or upload, then the run. The result has three parts: Rack, Devices, Report. The Report holds the compare: the database dropdown and Compare against the database, the rack identification with proof and confidence, Continue, the differences short or detailed (record value, video value, unit, device crop, what to check), and the earlier scans of the rack in a closed dropdown at the bottom. Copy-in and logins live under Profile. tools/proof_flow.py at 1280 and 400 px on our rack with all three sources (docs/proof/flow); tools/proof_widths.py every screen at 1800, 1280 and 400 px (docs/proof/widths). |
| Rack identification | done | src/source_db.py identify, the rungs and thresholds of the two rack_identity.js ladders: 0 confirmed before (kept per rack label in the organization's copies), 1 location (optional phone position on the upload; site positions copied in from NetBox sites and ServiceNow cmn_location; test positions in the ground truth), 2 the rack label with the strip above the first unit as proof, 3 serials, asset tags and models read on faceplates, 4 rack height (narrows only), 5 rack number in device labels, 6 layout. The first step that leaves one rack names it; each step has a confidence and a reason; overall confidence combined as the ladder scores, Verified at 70 % or more; top 3 or "Which rack is this?" when no step names one. A person's kind, make or model correction is kept by rack label and unit and applied to the next scan of that rack. tests/test_source_db.py (5 tests). |
| Evaluation | done | tools/evaluate.py, docs/EVALUATION.md. vdo1: 10 of 11 checkable devices found, units 10 of 10, kind 10 of 10, make and model 4 of 4 read (10 in the record), missing 1 (PDU at U24, outside the video), extra 0; the drift agrees on every count. The two WhatsApp runs: 6 of 9 and 6 of 14 found, units off by one or two; drift and evaluation pair them differently there (start within one unit against overlap), explained in the file. |
| Sign-in merged | done | wt/auth merged (--no-ff); Google, Apple and Facebook sign-in removed; no self sign-up (the platform owner creates organizations, Contact us on the sign-in screen, kept in data/contact); five roles by name (docs/AUTH.md); test runs in a Test Organization only. tools/proof_auth.py on 8700: 24 of 24 checks without screenshots after the last change, 29 of 30 with screenshots before it (the one failure was the invite site name, fixed). RT-EOS server tests for these parts: 76 of 76. |
| Model lock | changed | engine.sh starts the server without the wrapper; server/app.py ModelLock takes the same machine-wide lock directory only around a model run, so an idle server never holds it. |

Still open: the second and third "Seen in the video" views of D1 and D2 answer 404 (no frame holds the whole box); the location step has never seen a real phone position (no run carries one, the site positions are test values); the report's device table at 400 px is cramped; copies made by the platform owner live in the owner's own copies file, so the owner's compare of a pre-sign-in run uses those; the Chennai DC test site created by the first proof run remains in the Demo Organization (its fixture runs were removed and its two test accounts moved to the Test Organization).
