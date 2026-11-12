# Evaluation: what each stored video run read, against the ground truth

Made by `_cache/venv/bin/python tools/evaluate.py --md` from the stored runs' result.json files (no model loaded, no new run). The ground truth is modules/sources/ground_truth: vdo1 is the NetBox export of the office rack; the two WhatsApp racks were written from the videos on 2 Nov and still need the owner's confirmation.

Checkable means a record device with a unit and a kind the video's models can tell apart: cable managers, shelves and blank panels are left out (the models have no class for them), and so is a device with no unit in the record. A record device and a run device are paired when their units overlap most. Units right: same start unit and same height. Kind right counts only where the video gave a kind (a correction, then the faceplate read, then the picture); a device left Not sure is counted under kind read as not read. Make and model right: both read and both the same as the record.

| | vdo1.mp4 (office rack SP-HYD-RM01-R01-R1) | WhatsApp 21.43.13 (NETWORK RACK 1) | WhatsApp 23.19.19 (NETWORK RACK 2) |
| --- | --- | --- | --- |
| in the record (checkable) | 11 | 9 | 14 |
| in the video | 10 | 16 | 12 |
| found | 10 | 6 | 6 |
| units right | 10 | 2 | 1 |
| kind read | 10 | 3 | 3 |
| kind right | 10 | 1 | 2 |
| make and model in the record | 10 | 0 | 0 |
| make and model read | 4 | 0 | 0 |
| make and model right | 4 | 0 | 0 |
| missing | 1 | 3 | 8 |
| extra | 0 | 10 | 6 |
| drift compare agrees | yes | no | no |

## vdo1.mp4 (office rack SP-HYD-RM01-R01-R1) (run 91af02c09bbc)

- Missing: SP-R1-U24-PDU at U24.
- Extra: none.
- Units off: none.
- Kind wrong: none.
- Kind not sure: none.
- Drift compare on the same record: found 10 in the drift, 10 here, missing 1 in the drift, 1 here, extra 0 in the drift, 0 here, units off 0 in the drift, 0 here, kind wrong 0 in the drift, 0 here.

## WhatsApp 21.43.13 (NETWORK RACK 1) (run 28c3c52cd91e)

- Missing: NR1-U13-PP02 at U13; NR1-U33-SW04 at U33-34; NR1-U8-PP03 at U8-9.
- Extra: D1 (Patch Panel) at U42-43; D10 (not sure) at U28; D12 (Patch Panel) at U21; D14 (Patch Panel) at U16; D15 (not sure) at U14; D4 (Switch) at U37; D6 (not sure) at U32; D7 (Switch) at U31; D8 (Switch) at U30; D9 (Switch) at U29.
- Units off: NR1-U18-PP01: record U18-19, video U19-20; NR1-U27-SW05: record U27-29, video U26-27; NR1-U36-SW03: record U36-37, video U36; NR1-U38-SW02: record U38-39, video U39.
- Kind wrong: NR1-U11-PWR01: record PDU, video Patch Panel; NR1-U40-SW01: record Switch, video Patch Panel.
- Kind not sure: NR1-U27-SW05 (record Switch); NR1-U36-SW03 (record Switch); NR1-U38-SW02 (record Switch).
- Drift compare on the same record: found 8 in the drift, 6 here, missing 1 in the drift, 3 here, extra 8 in the drift, 10 here, units off 6 in the drift, 4 here, kind wrong 2 in the drift, 2 here.

## WhatsApp 23.19.19 (NETWORK RACK 2) (run 501bcd981b64)

- Missing: NR2-U10-ASA01 at U10; NR2-U21-SRV02 at U21; NR2-U22-SRV01 at U22; NR2-U28-FW02 at U28; NR2-U29-FW01 at U29; NR2-U34-FIBRE02 at U34; NR2-U36-PP01 at U36-37; NR2-U38-FIBRE at U38-40.
- Extra: D11 (Router) at U11; D3 (not sure) at U31; D5 (not sure) at U24-25; D6 (Router) at U23; D7 (Server) at U20; D8 (Server) at U19.
- Units off: NR2-U3-STOR01: record U3-5, video U3-6; NR2-U17-SRV03: record U17-19, video U16-18; NR2-U15-SRV04: record U15-16, video U14-15; NR2-U33-SW01: record U33, video U33-34; NR2-U35-RF01: record U35, video U35-36.
- Kind wrong: NR2-U35-RF01: record Other, video Patch Panel.
- Kind not sure: NR2-U3-STOR01 (record Storage); NR2-U27-CEDGE (record Router); NR2-U33-SW01 (record Switch).
- Drift compare on the same record: found 9 in the drift, 6 here, missing 5 in the drift, 8 here, extra 3 in the drift, 6 here, units off 8 in the drift, 5 here, kind wrong 3 in the drift, 1 here.

## What the numbers say, and the known causes

- vdo1 (our rack): every device the video's models can tell apart is found, at the right units and with the right kind (10 of 10). The one missing device is the PDU at U24: the video covers U1 to U20 only. Make and model are read on 4 of the 10 (the four switches with a fitted vendor layout); the patch panels have no make and model reader, the router at U20 has only "CTS" read on its panel, and the firewall at U19 has no model read.
- vdo1 kinds: the router at U20 and the firewall at U19 are right only because the kind now comes from one place (src/source_db.py device_kind): a person's correction, then the faceplate read, then the picture. The picture alone said Switch for U20 and could not tell U19.
- The two WhatsApp videos: the unit numbering is off by one or two units over parts of the rack (for example NR1-U18-PP01 read at U19-20, NR2-U17-SRV03 at U16-18), heights differ, and several devices are left Not sure. Their ground truth was written from the videos and needs the owner's confirmation, so these numbers are a first look, not a score.
- No serials or asset tags are compared: the office rack's export has none and the video reads none.

## Why the drift and the evaluation differ on the WhatsApp videos

On vdo1 the two agree on every count. On the WhatsApp videos they pair devices differently, by design: the drift pairs a record device with a video device whose start unit is within one unit (so it can say Moved), the evaluation pairs devices whose units overlap. Where the numbering is off by one, a record device at U13 and a video device at U14 do not overlap: the evaluation counts one missing and one extra, the drift one Moved. Neither is a mistake in the code; it is the off-by-one numbering of those runs showing up two ways. Once the unit numbering of those runs is right, the two will agree there as well.
