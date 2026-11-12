# The remaining RT-EOS functions on a run (branch wt/features, 2 Nov 2026)

One rack: SP-HYD-RM01-R01-R1, the stored run of vdo1 (run 91af02c09bbc, copied from samples/video/run_of_vdo1 into data/runs/1/2/ of this worktree, organization 1, site 2 Hyderabad DC). Engine on 8702 and the sign-in service on 3305, both of this worktree.

Code: `src/rt_features.py` (copied from RT-EOS, see its header for each function's source), the block marked `wt/features` in `server/app.py`, a few marked lines in `src/source_db.py`, `mobile/features.js` and `mobile/features.css` with marked lines in `mobile/index.html` and `mobile/app.js`.

Proof: `tests/test_rt_features.py` (7 tests, no model; the whole suite 111 passed after the merge), `tools/proof_features.py` (API answers into `docs/proof/features/results.json`, screens at 1280 and 400 px into `docs/proof/features/`), `tools/plant_features_workbook.py` (the planted workbook), `tools/make_synthetic_closeup.py` (the synthetic close-ups).

## 1 Serials and asset tags from close-ups

- POST /api/closeup with a run and a device now also runs RT-EOS's close-up OCR (`rt_eos/pipeline/ocr_closeup.py`, as a child process of the engine, which holds the model lock) and reads the text with model_read.js's rules: a serial after S/N, SN or Serial, or in the serial shape (three letters, four or five digits, two to five more, no dash); an asset tag after Asset or Asset Tag, or in the asset-tag shape (a name ending in SW, PP, FW, RTR, PDU or ACT and a number, three parts or more), which is also kept as a device label; cable labels in the cable shape (CBL-..., FBR-...).
- Kept on the device (`ids`, `serial`, `asset_tag`, `label_read`) with the picture, who and when. The device page shows them with a Read from a close-up button (it sends `ids_only`, so a label photo does not replace the device's port layout).
- The drift: Wrong serial (existing) and Wrong asset tag (new, type `asset_tag`). The identification: step 3 counts serials and asset tags, step 5 reads the rack number in the device label.
- Proof: a real crop of D5 from the vdo1 run (section_D5_clean.jpg) read "Pip-link AM": no serial on any picture of the video, as expected. Then the synthetic close-up `docs/proof/features/synthetic_closeup_D5.jpg` read serial TPL2231X9ZZ, asset tag SP-AT-00415, label SP-R1-U15-SW02 and cable label CBL-PLANT-L1-A; D4's read SGP2231A0BC. The drift showed "SP-R1-U15-SW02 has serial PLANTED0001 in the record; the video reads TPL2231X9ZZ." and the asset tag row; step 3 "3 serial or asset tags read", step 5 "Device labels read: SP-R1-U15-SW02; rack number 1". Screens device_ids_*.png, record_location_*.png.
- Note: the first close-ups were sent before `ids_only` existed, so the existing close-up reader also re-laid D4's and D5's ports from them; those layouts were taken out of the run by hand (the ids stayed). After the merge, D4's synthetic close-up sent again with `ids_only` read SGP2231A0BC and left the port layout alone (checked on the engine).

## 2 Location

- POST /api/video takes optional lat, lng and accuracy; the camera guide's upload takes them as query values and keeps them with the take (position.json), and a run from a take uses them. A video read before that is uploaded again with a position keeps the position on the earlier run.
- Profile > Site position sets the site's latitude and longitude through the sign-in service's PUT /api/setup/:tenantId/datacentre.
- Step 1 of the identification: a rack with no position of its own takes its site's (by name, else the run's own site); racks within 300 m or twice the phone's accuracy stay in; a reading worse than 2 km says nothing; location.js's verdict is added in words.
- Proof: site 2 set to 17.4486, 78.3908; vdo1.mp4 uploaded again with 17.4489, 78.3911, accuracy 35. Step 1: "1 rack within 300 m of the phone's position (accuracy 35 m). Taken at Hyderabad DC, 50 m from its position." Tests cover far away (none), too rough (skipped), another site (elsewhere). Screens site_position_*.png, record_location_*.png.

## 3 Cables

- The A_Z_CONNECTION_MATRIX is copied with every Excel copy (`src_cables`, deleted with the copy). GET /api/video/<run>/cables: every row with an end in the rack against the ports the video read (drift.js): Missing cable (the port read empty), Extra cable (a device of the matrix with a cable on a port no row names), Wrong port (the cable's label read on another device, or a missing cable and an extra one on the same device). Ports the video could not read are not checked.
- POST /api/cables/az {a, z}: checks.js's A-end / Z-end check of two runs (the same run at both ends is allowed); GET /api/cables/az/<id>.csv, checks.js's CSV.
- Proof with the planted workbook (19 rows, every one marked PLANTED in Notes, Z ends in a rack not filmed): Missing cable on port 2 of SP-R1-U20-ACT, Extra cable on port 23 of SP-R1-U17-SW03, Wrong port 21 to 24 on SP-R1-U18-SW04, Wrong port for CBL-PLANT-L1 (its label read on the D5 close-up). A-Z check: 1 wrong port, 2 missing cable, 16 label not found, CSV in docs/proof/features/az_check.csv. Screens cables_*.png, cables_az_*.png.

## 4 The run's tools (a section More on this rack inside the Report, each tool a screen at #tools/<run>/<tool>)

| Tool | Route | Screens |
| --- | --- | --- |
| Find a port | GET find-port, find-port.jpg (the rack picture, device outlined, port ringed) | find_port_* (D4 port 9: cable plugged in, white RJ-45, cable CBL-PLANT-L1 to SP-R2-U1-PP1 port 8) |
| Connections | GET connections | connections_* |
| Worklist | GET worklist, POST worklist/<item> (kind, or connected or empty) | worklist_* (32 items) |
| Read again | POST and GET read-again | read_again_* |
| Physical layer | GET physical-layer (JSON), physical-layer.html (to print) | physical_layer_*, physical_layer_page_* |
| SFP advice | GET sfp-advice | sfp_advice_* |
| Topology | GET topology (RT-EOS RackTopology detail with the matrix's cables) | topology_* |

Read again ran on the engine before the merge: devices 1 to 6 (the switches, router and firewall) were read again with the current models, 4 to 7 port states changed per switch (D1 port 2 no longer reads empty, so the planted Missing cable goes away after a read again); devices 7 to 10 (patch panels) did not finish: the patch panel reader needs patchpanel_classify_overlay/port_model.pt, an untracked model file that is in the main checkout but was not linked into this worktree. The proof's run was put back to its state before the read again. A read again on 8700 (main checkout) has the file. SFP advice takes the matrix's cable type as RT-EOS does, so a Cat6 row is offered as the cable for an SFP port.

## 5 The phone app

See docs/PHONE.md.
