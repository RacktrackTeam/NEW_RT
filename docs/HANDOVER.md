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
