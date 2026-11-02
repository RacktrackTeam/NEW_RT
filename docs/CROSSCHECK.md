# Cross-check of the databases and the drift, live (2 Nov 2026, afternoon)

What was run: the engine and the two mocks were stopped (their processes were started from this folder) and started fresh with
./engine.sh (through scripts/with-model-lock.sh) and ./sources.sh. Then, in headless Chromium (chrome-headless-shell 1228 via PW_CHROMIUM):

- `tools/proof_today.py` live: it failed on the Excel source at first (no rows), because the workbook now gets a report before Import and
  the proof never pressed Import. Fixed in the proof; rerun: Excel shows the planted Wrong model, NetBox the planted Moved, the CMDB the
  planted Wrong make, at 1280 and 400 px. Screenshots replaced in docs/proof/today.
- `tools/proof_afternoon.py` live (never run live before): Connections NetBox Connected and ServiceNow Not connected, no secret on the page,
  the broken workbook report, the three planted port facts, the copies list and Delete. Screenshots replaced in docs/proof/afternoon.
- `tools/proof_crosscheck.py`: the whole flow walked as a person would, at 1280 and 400 px. Screenshots in docs/proof/crosscheck.

The screenshots in docs/proof/crosscheck show the screens as they were during this cross-check, before the new flow (docs/proof/flow)
replaced the Drift tab. tools/proof_crosscheck.py, proof_today.py and proof_afternoon.py drive the old Drift tab and are kept as the
record of that check; tools/proof_flow.py is the proof of the screens as they are now.

## Screen by screen

| Screen | What I did | What I saw | What I fixed |
| --- | --- | --- | --- |
| Sign in (signin_*) | Typed a name, Continue | Home opens | nothing |
| Home (home_*) | Looked | The racktrack.ai hero, one button, a tally line; no list of racks | Replaced in the new flow by the list of racks (docs/proof/flow/home_*) |
| My racks (history_*) | Looked | Every run titled "Aasritha's rack, 27 Oct ..." (the uploader's name), not the rack | Home now titles each run by its rack id (SP-HYD-RM01-R01-R1) |
| Result, rack (result_rack_*) | Opened the stored run of our rack | Unit ranges written "U11–10" with an en dash, top unit first | Ranges now read low to high with a plain hyphen, "U10-11", as the drift writes them (all 7 places in mobile/app.js) |
| Result, devices (result_devices_*) | Opened D1 | D1 at U20 said Switch while the drift said Router | One kind everywhere, computed in src/source_db.py device_kind: a correction, then the faceplate, then the picture, with the source in words ("Router, from the faceplate; the picture said Switch"); used by the view, the status, the port reader and the drift. Proof: docs/proof/flow/device_D1_*, device_D2_* (Firewall, from the faceplate) |
| Result, layout and report (result_elevation_*, result_report_*) | Looked | Report fine; en dashes in the report builder's placeholders and ranges | Long dashes removed from scripts/video/build_report.py, src/rack_video.py, src/netbox_truth.py, src/occupancy.py |
| Drift, Excel (drift_excel_report_1280, drift_excel_*) | Read modules/sources/ground_truth/vdo1.xlsx, Import | 346 rows of the same warning listed one by one (first 100); the record list said "R9 - 1 devices"; the read port type in lower case ("sfp") | Repeated problems are one line with their rows ("PORT_INVENTORY rows 2-347, Warning on 346 rows"); "1 device"; port type in capitals |
| Drift, Excel race | Read a second workbook straight after the first | The old report's Import button stayed until the new report came, so the afternoon proof imported the broken test workbook into the live copies | A new read clears the report first; the leftover test copy deleted through Profile > Database copies (copies_after_delete_1280) |
| Drift, NetBox and CMDB (drift_netbox_*, drift_cmdb_*) | Copy in from each, then each source | The CMDB copy-in failed with a 502 (the afternoon proof had saved a ServiceNow login on 127.0.0.1:5399, where nothing listens) and the screen kept showing the older copy with the planted Cisco, without saying so | The failure now says "Nothing was copied; the copy from before is still the one used"; after saving the mock's address under Connections the CMDB copy holds no planted change. Clean sources show only the PDU at U24 (the video covers U1 to U20) and, on Excel, the 8 real SFP port rows |
| Profile (profile_*) | Looked | Fine | nothing |
| Connections (connections_*) | Saved both mock addresses with a test login, Test connection | NetBox Connected (NetBox 4.0.0-mock), ServiceNow Connected (the Table API answered with 1 row) | nothing |
| Database copies (copies_*) | Looked, deleted the test workbook copy | Versions, times, counts, Active and Older right | nothing |

Errors in the browser during the walk: one 502 (the CMDB copy-in above), none after the fix. During the flow proof two more were found
and fixed: the CMDB copy-in threw a 500 once the rack's location became a reference (it now reads the reference), and a run copied in
from racktrack-app kept its video path pointing into /Volumes/Racktrack/racktrack-app; the server now only takes a run's video from
inside this folder. Still open: a device's second and third "Seen in the video" views answer 404 for D1 and D2 when no frame holds the
whole box (the picture then simply does not show).

## Tests
`_cache/venv/bin/python -m pytest -q tests`: 104 passed (was 102 before the identification tests).
