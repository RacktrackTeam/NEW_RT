# Verification pass 5 (3 Nov 2026, 14:53 to 15:20)

The confirmation pass, looking where passes 3 and 4 did not: data edge cases on the stored runs, Profile > Connections and Database copies end to end, the Scan screen without a model run, keyboard and labels, API hygiene, repository hygiene and the three test suites. Find and fix in one pass. Branch rt-eos-integration, from commit 6d1b1bf.

## How it was run

- As in passes 3 and 4: the owner's sign-in service (3302) and mocks (3303, 3304) were left running and untouched; nothing listened on 8700. data/ was copied into my scratch folder (without datasets, device_crops, test_sets and eval); a second sign-in service ran on 3312 on that copy (its own .env: port 3312, the 8708 origin allowed); the engine ran from `./start.sh` with RACKTRACK_PORT=8708, RACKTRACK_DATA and RT_AUTH_ENV pointing into the scratch folder, and every outside request sent to a closed port (HTTP_PROXY and HTTPS_PROXY at 127.0.0.1:9, 127.0.0.1 excepted for the mocks). Stopped at the end with `./stop.sh` (engine) and by its pid (3312).
- Kept cool: no model loaded, no video run, OMP_NUM_THREADS=2, one browser, one engine and one process at a time.
- The two WhatsApp runs are no longer in data/runs (removed in the pass 2 restructure; their stored copies are samples/video/run_of_whatsapp_2143 and _2319). They were copied into the scratch data/runs only, without video_upright.avi, as platform-owner runs (28c3c52cd91e, 501bcd981b64). To get a run where no rack is certain, the scratch copy of 501bcd981b64 had its rack ID and database choice cleared.
- Headless Chromium (headless shell 1228) at 1280 and 400 px; accounts owner, orgownert, techt, tech2 and admin2. The scripts are in my scratch folder (not committed).

## Checks

Format: check - verdict - fault and fix commit.

1. Stored runs and the compare when no rack is certain - pass after three fixes.
   - 28c3c52cd91e (typed rack ID "Network rack 1") against NetBox: the confirm panel at 85 %, Change opens the picker with focus in the search, Confirm gives 20 differences, a reopen auto-accepts "confirmed for this rack before".
   - Fault (wording, honesty): the Rack label step said "The label reader read NORK RACK, 88 % sure; a person confirmed it", though the person typed "Network rack 1" and confirmed no reading. Fix 510a8b7: "a person typed the rack ID Network rack 1" (or "picked"); "confirmed it" stays where the reading and the ID are the same.
   - 501bcd981b64 with no rack ID: "No single rack found", every layout fit 0 of 7, so the ladder asks directly (no top 3); Confirm is disabled; Use this record with nothing typed says "Type or pick a rack id first."; typing NETWORK RACK 2 and Use this record opens the panel; Confirm gives 19 differences.
   - Fault: on reopen the pick was gone ("No single rack found" again): choosing the source again replaced the pick, and with no rack ID nothing kept it. Fix 510a8b7: a record a person picks and confirms on a run with no rack ID becomes the run's rack ID (source "picked"); the reopen auto-accepts "confirmed for this rack before". Checked at 1280 and 400.
   - Fault (wording): a picked record on a run where the steps found no rack showed no "Picked by" line. Fix 510a8b7: "Picked by owner. The steps below found no single rack. Confirm only if you are sure."
   - The top 3 list: not reachable with the stored runs (the WhatsApp runs fit 0 devices on every record, vdo1's label names one rack), so not seen on screen; the code path was read.
   - No copy for the organization (Second Organization, fx2pun000001): fault - a technician choosing NetBox or CMDB got the copy-in's refusal ("Only an organization admin can copy a database in.", a 403 in the console) and choosing Excel was told to "Copy one in", which a technician cannot. Fix 510a8b7: "No NetBox copy yet, so there is nothing to compare with. An organization admin copies it in under Database copies." with no call made. admin2 choosing Excel lands on Database copies with the toast, as before.
   - Fault (layout): the empty Database copies card had no padding, its text against the border. Fix 510a8b7.
2. Profile > Connections and Database copies, orgownert - pass. NetBox login saved pointing at the mock (Not tested, then Test connection: "Connected. NetBox 4.0.0-mock answered."); Copy in from NetBox made NetBox v1 (3 racks, 43 devices, "The local test copy", Active); vdo1.xlsx read (347 warnings, the known LOOKUPS one), Import made Excel v3 Active and v2 Older; Delete asks "Delete this copy? ..." and brings v2 back as Active; the compare of fx1hyd000001 with the new NetBox copy auto-accepts SP-HYD-RM01-R01-R1 (the label matches exactly) with the 8 known port differences. At 400 the same with v2 of NetBox.
3. Scan screen without a model run - pass after two fixes. Record with the camera, Upload a video and How to film render; Read the rack is disabled until a file is picked (the empty submit cannot be made; the API answers "Send a rack video or a take.").
   - Fault: a text file was accepted ("notes.txt 0 MB", Read the rack enabled) and the server would start a run on it. Fix 510a8b7: "notes.txt is not a video. Pick an MP4 or MOV video of the rack." on the screen (picked or dropped), and the server answers 400 with the same words.
   - Fault: a refused start (no file, or a take that is not there) left an empty run folder. Fix 510a8b7.
   - The "Reading the rack" screen: #job/fx1hyd000001 shows the stored run's steps (all ticked, Port layouts 10 of 10, Took 1 min 28 s, Open the result) at both widths with no sideways scroll; a text file named .mp4 shows "The reading stopped ... The video could not be joined into one rack picture" with Try again (fails before any model).
4. Keyboard and labels on Sign in and Report - pass after a fix. Sign in: Tab reaches username, password, Sign in, Forgot password, Contact us, each with a visible focus ring; every input has a label; signing in by keyboard alone works.
   - Fault: the Report's device rows open the device on a click but could not be reached by keyboard; at 400 the first Tab stop was an empty link with no name (#rep-full). Fix 510a8b7: the rows take focus (role link, a name like "Open D4, Switch, U17") and open with Enter or Space; the empty link stays hidden. The workbook file input on Database copies got a label too.
5. API hygiene - pass after a fix. Every route in server/app.py without a session: 45 answer 401 with an error field, the screens and /video-report redirect to sign-in, only the public ones answer (health, auth-service, auth/state, contact with 400 on an empty body, manifest, sw.js, favicon, logo). As techt, every run route on Second Organization's run answers 404 with an error field. No trace text in any answer.
   - Fault: an unknown route answered {"detail": "Not Found"} and a wrong method {"detail": "Method Not Allowed"}, not the app's {"error": ...}; a malformed JSON body said "(1)". Fix 510a8b7: one handler for every HTTP error (the Allow header kept); the message no longer names a JSON position. A test covers both.
6. Repository hygiene - pass after two fixes, one item left.
   - README start steps match start.sh. .gitignore covers data/, models/ (MANIFEST kept), web/org/dist-org, _cache and .env files. No secret in tracked files (password=, token, api_key, private key and cloud key patterns: only a fake password in tests/test_connections.py). requirements.txt matches _cache/venv-engine (42 of 42) and engine/readers/requirements.txt matches _cache/venv (112 of 112).
   - Fault: start.sh looked for saved real logins in data/ even when RACKTRACK_DATA named another folder. Fix 9aeb5a2.
   - Fault (doc): STRUCTURE.md did not list engine/fa_device_logic, rack_stitcher, report_data or .claude, and said more_datailed_1.py was removed though the units adapter imports it. Fix 42955bf.
   - Left for the owner: four tracked files are over 5 MB, all from racktrack-app's baseline (2991a28) and used at run time: engine/patchpanel/out/cache/gal4_b_224_10-05_mt36.npz (28 MB, the patch panel gallery embeddings; without it the model rebuilds them) and three port agent template photos (22, 5.6 and 5.3 MB PNG). Moving them out of git means handling them like models/ (MANIFEST and a copy step); that is the owner's call, not a fix for a confirmation pass.
7. Code - pass. Python tests: 135 pass (the two new ones included; tests/test_models_offline.py skipped: it loads every model, and the owner asked to keep the Mac cool). Sign-in service: 118 pass. Organization pages: 285 pass (two workers). All with RACKTRACK_AUTH_DB and TMPDIR inside my scratch folder. The outside path grep over server, engine, web, tools, tests and the scripts finds only tools/strip_outside_paths.py. No long dash in the files changed. `git status` clean at the end.
8. Second walk after the fixes - nothing found. Engine restarted; the no-rack-ID run reset; checks 1 to 5 again: the ask path, pick and reopen at 400, the typed wording, the no-copy messages for tech2 and admin2, Connections and copies at 400, the Scan validation, the stored run screen, the keyboard walks at 1280 and 400 (Enter on a device row opens Devices), and both curl sweeps.

## Skipped

- tests/test_models_offline.py, a fresh video run and the macOS WebKit check: each loads models or runs long; the owner asked to keep the Mac cool.
- The top 3 on screen: no stored run reaches it (see check 1).
- HawkScan: the owner said no (it calls outside).

## Shared data

Nothing in data/, data/auth/db/auth.db or the mocks changed: every sign-in, compare, login, copy, upload and rack ID went to my scratch copy (no file in data/ is newer than the start of the pass). web/org/dist-org was not rebuilt (no organization page changed).
