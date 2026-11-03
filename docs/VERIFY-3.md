# Verification pass 3 (3 Nov 2026, 13:23 to 13:48)

Find and fix in one pass, main flow only, as the owner uses it. Branch rt-eos-integration, from commit 7be821a.

## How it was run

- The owner's sign-in service (3302) and mocks (3303, 3304) were already running and were not stopped or changed. Nothing listened on 8700.
- My own copy: data/ copied into my scratch folder (without datasets, device_crops, test_sets and eval), a second sign-in service on 3312 on the scratch copy of auth.db (its own .env: port 3312, the 8708 origin allowed), and the engine started by `./start.sh` with RACKTRACK_PORT=8708, RACKTRACK_DATA and RT_AUTH_ENV pointing into the scratch folder, and every outside HTTP request sent to a closed port (HTTP_PROXY and HTTPS_PROXY at 127.0.0.1:9). start.sh left 3302 and the mocks as they were and started only the engine. Stopped at the end with `./stop.sh` (engine) and by its pid (3312).
- The owner asked during the pass to keep the Mac cool: no model was loaded, no video run was made, one browser and one process at a time, OMP_NUM_THREADS=2.
- Browser: headless Chromium (headless shell 1228) at 1280 and 400 px; Test Organization accounts only (orgownert, techt, managert) and Second Organization's tech2 as a stranger; run fx1hyd000001 (SP-HYD-RM01-R01-R1, the stored vdo1 run). The scripts are in my scratch folder (not committed): every screen is checked for a page error, an API answer of 400 or more, a long dash, the banned words, sideways scroll and its height.

## Checks

Format: check - verdict - fault and fix commit.

1. `./start.sh` from cold, with services already running - pass after a fix. Fault: start.sh and stop.sh kept logs and process ids in data/ and engine.sh always read data/auth/.env, so a second engine wrote into the shared data. Fix 1f249e6: they take RACKTRACK_DATA and RT_AUTH_ENV from the environment. The engine answered in 6 s; start.sh said "already running, left as it is" for the sign-in service and the mocks; stop.sh stopped only the engine it started.
2. Sign in, Home, open the run from its Home row, then Rack, Devices, Find a port, Connections, Cables, Layout, Report in that order, each its own screen and tab marked, at 1280 and 400 for orgownert, techt and managert - pass after two fixes. No page error, no refused call, no long dash, no banned word, no sideways scroll.
   - Fault: the Report's free space pill read "2UU21-22" (the flex pill dropped the space). Fix 42f9539.
   - Fault: Connections listed the same problem four times per switch ("Wrong port type, port 25", 26, 27, 28), which made the cards uneven and the tab longer. Fix 3cec9ea: one line, "Wrong port type, ports 25 to 28 - Ports 25 to 28 of SP-R1-U17-SW03 are RJ45 in the record; the video reads SFP." The tab went from 2.5 to 2.2 screens at 1280 and 4.6 to 4.3 at 400.
   - Fault: Cables with nothing to check said it three times ("0 ends checked", "0 to look at", "The matrix has no cable in this rack." and the card "No cable to check"). Fix 3cec9ea: only the card.
3. Report compare - pass after a fix.
   - "Compare against the database" top right; the dropdown holds Excel workbook, NetBox and ServiceNow CMDB.
   - All three auto-accept SP-HYD-RM01-R01-R1: "identified with high confidence (the rack label matches this record exactly)" on the first compare of NetBox and CMDB, "(confirmed for this rack before)" after that; Show how opens the steps; Not this rack is there.
   - All three show the same 8 differences, all "Wrong port type": ports 25 to 28 of SP-R1-U17-SW03 and SP-R1-U15-SW02 (RJ45 in the record, SFP in the video).
   - Not this rack opens the picker. The Excel copy holds only this rack, so the wrong rack was picked in the NetBox copy (NETWORK RACK 1): the confirm panel opens.
   - Fault: that panel showed "100 % overall" and "Confirmed before" for NETWORK RACK 1; those were the found rack's numbers. Fix 50375c5: for a picked record that is not the one the steps found, the panel says "Picked by orgownert." and "The steps below found SP-HYD-RM01-R01-R1, not this record. Confirm only if you are sure."
   - Reopened in a fresh page, the accepted rack is remembered (auto, no confirm step), also after an unconfirmed wrong pick.
   - Fault (wording): the remembered step read "Confirmed by orgownert (accepted by itself: identified for certain)". Fix aa83b2e: "Accepted by itself, identified for certain, when orgownert compared on 3 Nov 2026 ...". Old and new records both read this way; no data changed.
4. Profile, Sign out, technician and site manager - pass. Profile and Sign out at both widths for all three roles, no 401. Through the API: techt and managert open their site's run, its drift and the compare (200); another site's run, another organization's run and the Demo run answer 404, also for delete; copying a database in answers 403 "Only an organization admin can copy a database in."; techt removing the run answers 403 "Only a site manager or an organization admin can remove a scan."; tech2 (Second Organization) gets 404 for the run's view, report and compare.
5. Text - pass. No long dash on any screen, in web/, server/, engine/, tools/, server/auth, web/org/src or the docs. No banned word on any screen. In web/app the three comments that still named removed things were reworded (7ad5162). Light theme and the #1f2a3a accent are unchanged.
6. Code - pass. The outside path grep over server, engine, web, tools, start.sh and stop.sh finds only tools/strip_outside_paths.py. Python tests: 133 pass (tests/test_models_offline.py was skipped because it loads every model: not run, the owner asked to keep the Mac cool). Sign-in service: 118 pass. Organization pages: 285 pass (two workers). All with RACKTRACK_AUTH_DB and TMPDIR inside my scratch folder.
7. Second walk after the fixes - nothing found. Checks 2 to 4 again at both widths for all three roles, after restarting the engine with stop.sh and start.sh.

## Pass 1 leftovers

- 49 - Fixed: the two old "default" logins (127.0.0.1:3301 and 5301) were removed from data/sources.db (a copy from before is in my scratch folder). Only the platform's two mock logins (3303, 3304) remain.
- 30 - Left as it is, as the owner asked: the two WhatsApp runs are the platform owner's only.
- 22 - Left for the owner to decide: database copies are kept per organization, not per site.

## Found and not changed

- The banned words are still in web/ outside what any screen shows: web/org/src (RT-EOS's client source: its Topology tab, which tools/rt-topology builds into web/app/rt/rt-topology.js for the approved Layout drawing; WorklistPage, which the organization-only build never reaches; and tests that check the Google, Apple and Facebook sign-in is gone) and the React licence lines ("Copyright (c) Facebook") in the built bundles, which must stay. Removing them means cutting the RT-EOS client source and its tests: the owner's call.
- Connections is still about 2 screens at 1280 and 4 at 400: it is one card for each of the ten devices with its port grid. Making it shorter (three columns, or folding the port grids) changes the tab's design, so it waits for the owner.
- Not run: the model-loading test, a fresh video run, the macOS WebKit check and HawkScan (it calls outside).

## Shared data

data/sources.db lost the two old logins (finding 49). Nothing else in data/, data/auth/db/auth.db or the mocks changed: every sign-in, compare and pick went to my scratch copy.
