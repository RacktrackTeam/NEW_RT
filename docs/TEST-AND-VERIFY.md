# Test set, ground truth and the zero-issue rule (owner, 2 Nov 2026)

## The test set: three videos
/Volumes/Racktrack/one_racktrack_sources/samples/video/ holds vdo1.mp4, "WhatsApp Video 2026-10-26 at 21.43.13.mp4" and "WhatsApp Video 2026-10-26 at 23.19.19.mp4", each with its stitched rack picture. vdo1's rack is SP-HYD-RM01-R01-R1 (confirmed from its label on 30 Oct) and its stored run is samples/video/run_of_vdo1. Every feature is tested with all three videos, not only vdo1.

## Ground truth, in all three databases
For each video there is one ground truth file: rack label, unit count, every device with its units, type, make, model, device id or label, serial and asset tag where readable, and its ports and cables where known. vdo1 starts from the NetBox export /Volumes/Racktrack/racktrack-app/data/ground_truth/SP-HYD-RM01-R01-R1/netbox_devices.csv (read only; 18 devices). For the two WhatsApp videos no ground truth exists: create it from the stitched picture and the run, check every row against the picture by eye, and mark the file "created 2 Nov 2026 from the video, to be confirmed by the owner".

The same ground truth is loaded into all three databases of the test organization: the Excel workbook (the manager's template, one row per rack, device, port and cable), the mock NetBox (sites, racks, devices with device types, positions, serials, asset tags) and the mock ServiceNow CMDB (rack and device CIs with classes, models, serials, units). A test organization with three sites, one per video, and a fixture script that loads all of it in one command. Then every test can plant a difference in a database and expect it as drift.

## The zero-issue rule
The owner will go through every page and every step and expects to find no issue: in function, in code, in the UI, anywhere. So after the build is complete end to end:
1. Full checks run again and again, by fresh eyes each time: a functional pass (every feature on all three videos against all three databases), a code pass (every route guarded, every table scoped, no secret, no fixed path, no dead code, no long dash), and a UI pass (every screen at 1280 and 400 px, the RT-EOS look, plain English).
2. Every issue found is fixed and the whole pass runs again. A pass that finds nothing new, twice in a row, ends the round.
3. The report lists every pass, what it checked and what it found, so the owner can see the proof.
