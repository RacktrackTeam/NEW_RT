# Device Label Review

The network team's device labels, checked and sent to people for review. This file lists every report of the task, where it is, and how to rebuild it.

## The dataset

`data/datasets/devices/` is the only device dataset. It was built on 2026-10-19 from the network team's seven Roboflow exports, the ground truth, and checked against them file by file before the exports were deleted.

| | |
|---|---|
| Photos | 4,979, every export image byte for byte |
| Labels | polygon lines only, 12 classes |
| Fixes | export 429_v6 renumbered (its class numbers sit one place off its own data.yaml); export YT_455_V3's duplicate box lines left out |
| Files | `images/`, `labels/`, `data.yaml`, `manifest.csv` (per photo: export, split, checksums, size, `in_review`), `provenance/` (the exports' data.yaml and README files, and every original export label file in `export_labels.zip`) |
| Check | `python3 scripts/devices/audit_labels.py verify` |



## The review

Only photos of 1 MP or more are reviewed: 1,778 photos with 16,766 labels. The 3,180 smaller photos stay in the dataset. Each label is one card in one of four tabs, sorted by the RT-EOS device finder (a YOLOv8m segmentation model); the model never changes a label and the cards do not show what it says.

| Tab | Rule | Labels |
|---|---|---|
| To check (red) | a label check flags it: a different class, two labels of different classes on one device (the same class twice is not flagged since 2026-10-21; it was 1,000 before), a device label over an Empty label, or nothing under the label | 845 |
| Not confirmed (yellow) | the model is not at least 70 % sure of the same class | 3,084 |
| No label (purple) | the model is at least 80 % sure of a device where no label is | 207 areas |
| 100 % correct (green) | the model is at least 70 % sure of the same class | 12,837 |

## Reports

| Report | For | Local file | Online | Status |
|---|---|---|---|---|
| Device Dataset Review | THE one report, three tabs (user, 2026-10-26): No label, Device class verification (each: sent to review, results incl. what the unclear notes say, dataset before and after per class), Merge issues (fixes per export, unclear share per export) | `reports/devices/label_review_report/index.html` | https://claude.ai/artifact/T5x2ZP8ZKWYzBBqBGhQHVw | published 2026-10-26 (version 7) |
| Review page, Geethika | reviewer 1: To check 545 + Not confirmed 1,514 (since 2026-10-21 only these two tabs; No label went to the network team, 100 % correct needs no review) | `reports/devices/label_audit/class1/` and `geethika.zip` | https://claude.ai/artifact/8nLAJgC6mctPAiof3gHKo1 (update pending) | sent |
| Review page, Prasanna | reviewer 2: To check 455 + Not confirmed 1,518 (same two tabs) | `reports/devices/label_audit/class2/` and `prasanna.zip` | https://claude.ai/artifact/1VffQYLE8ShBEU7tEnWbhX (update pending) | sent |
| Twin Photos Review | the 36 photos exported twice with different labels: which copy is right | `reports/devices/twin_review/index.html` | after the upload limit resets | ready |
| No Label Review | every device the main detector finds on a rack where no label of any kind is: 2,438 in 1,536 photos, split over two reviewers (1,219 places / 768 photos each, whole photos per reviewer) | `reports/devices/No label review.zip` (both parts + READ ME FIRST.txt) | | answered 2026-10-23: 2,436 of 2,438; 1,631 device (1,055 Other), 229 no device, 575 unclear; files in `results/devices/review_marks/network_team/`, summary `results/devices/nolabel_results.json` (collect_review_marks.py) |
| Label Verification Review | step 2 for the network team: every instance still in doubt that the AI team marked wrong (71, first) or unclear (2,507): 2,578 in 1,071 images, two parts of 1,289 (whole images per part) | `reports/devices/Label verification review.zip` (folders `verify_labels_1/`, `_2/` + READ ME FIRST.txt; built by `build_unclear_review.py`) | | answered 2026-10-26: 2,576 of 2,578; 1,690 right, 262 wrong, 624 unclear; files in `results/devices/review_marks/network_team/verify_part*.csv`, summary `results/devices/verify_results.json` |

Each page is one file you can open in a browser (the reviewer pages need their `img/` folder next to them). Marks stay in the reviewer's browser unless the page is opened in claude.ai; each page has a download button for the marks.

The online artifacts for six reviewers (5gmJLf…, M7UadL…, N7zWsK…, RWE5td…) are from an earlier round and no longer used.

## Results so far

Both reviewers (the AI team) finished. Since 2026-10-21 only the 3,929 instances still in doubt count (`audit_labels.in_doubt_ids`); 103 marks on same-class pairs the model confirms are left out. Marks in `results/devices/review_marks/` (one file each), collected by `scripts/devices/collect_review_marks.py` into `results/devices/review_results.json`; wrong labels in `review_wrong_labels.csv`.

| | Right | Wrong | Unclear | Not marked |
|---|---|---|---|---|
| AI team | 1,345 | 71 | 2,507 | 6 |

## Rebuild

```
python3 scripts/devices/audit_labels.py run              # model boxes for new photos, every check, the two reviewer pages
python3 scripts/devices/build_review_report.py           # the Device Label Review report
python3 scripts/devices/build_twin_review.py             # the Twin Photos Review page
python3 scripts/devices/nolabel_strict.py               # the strict No label list (both detectors, unit ladder, label polygons)
python3 scripts/devices/build_nolabel_review.py          # the No Label Review page
python3 scripts/devices/nolabel_versions.py             # every version of the No label list, for the report
python3 scripts/devices/nolabel_proof.py                # the proofs for the report (detector agreement, sensitivity, samples)
python3 scripts/devices/collect_review_marks.py          # the reviewers' marks -> results, and the report rebuilt
python3 scripts/devices/build_unclear_review.py          # the Label Verification Review zip (network team step 2: wrong + unclear, two parts)
```

Results: `results/devices/label_audit.json` (every flag), `sources.json` (dataset facts), `export_issues.json` (the 429_v6 proof, computed while the exports existed).

## Next

1. Publish the pages online once the daily upload limit resets (00:00 UTC).
2. Collect the remaining marks: the Unclear decisions (now 2,567), and the twin and No Label reviews; save each as a file in `results/devices/review_marks/` named after the reviewer.
3. Add the results to `results/devices/review_results.json` and rebuild the Device Label Review report; its last section then shows them.
4. Apply the corrections to the dataset.
