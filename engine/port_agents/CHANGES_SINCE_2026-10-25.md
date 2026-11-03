# What changed since port_agents_2026-10-25 (for teams that integrated that version)

Package: `port_agents_2026-10-27.zip` (the 28 Oct package adds one more change: `CHANGES_SINCE_2026-10-27.md`). Same entry point, same calls, same requirements - the
integration code does not have to change how it calls the agents. What changes is in the
results: read sections 2 and 3.

## 1. How to update

1. Replace the three folders `switch_agent/`, `router_agent/`, `appliance_agent/` and the file
   `run_agent.py` with the ones in this zip. Replace whole folders (do not copy single files over):
   each agent folder has two new files that the code needs.
2. Nothing else: `requirements.txt` is unchanged, `map_switch()` / `map_router()` / `map_appliance()`
   and the command line take the same arguments, the output folder layout is the same.

New files (in each of the three agent folders):

| File | What it is |
|---|---|
| `port_status.pt` (104 MB) | Cable-status model (connected / empty per port). The package is about 310 MB larger because of it |
| `port_recovery.py` | Finds ports the port model missed (see 3.4) |

Changed code: `yolo_ports.py` (all three), `router_mapper/pipeline.py` + `ocr.py`,
`appliance_mapper/pipeline.py` + `ocr.py`, `switch_agent/map_any_switch.py`, `map_switch.py`,
`racktrack.py`, `run_agent.py`. Changed data: 4 saved switch templates (MikroTik CRS328-24P-4S,
MikroTik CSS610-8G-2S+IN, TP-Link TL-SG3424, Zyxel GS1100-16).

## 2. Changes in result.json the integration should check

| Field | Before (25 Oct) | Now (27 Oct) | What to do |
|---|---|---|---|
| `ports[].status` | Almost every port `connected` (the port model could not tell empty from cabled) | `connected` / `empty` from the new cable-status model; `unknown` where it cannot tell; `not seen` unchanged | If the UI counts connected ports, expect much lower, realistic counts and many `empty` / `unknown` |
| `ports[].id` / `type` (switch) | Data ports numbered; console only when the model named it | Also side ports beside the block: `id` `Mgmt` (`Mgmt 2` ...), `type` `management_port`; a lone SFP is a numbered port | Accept string ids and `management_port` from the switch agent too (the other agents already returned them) |
| Port numbers (switch) | 1 top-left on most switches, even where the panel prints 2 top-left | The panel's numbering when it can be read (TP-Link TL-SG3424, MikroTik CSS610, Zyxel GS1100-16: 2, 4, 6 on top) | Port numbers of those models change; re-map stored results if they keep port numbers |
| `identify_confidence` (switch) | Missing | `{vendor, model, vendor_doubt}` 0-1, as the router and appliance agents | Can now be read for all three agents |
| `identified_by` (all) | `typed` when vendor / model were passed in | `given, confirmed by the template fit`, `given, confirmed by the panel text`, or `given` (not confirmed); OCR / visual values unchanged | Treat `given` as unconfirmed; `identify_confidence.vendor_doubt` names the brand the panel shows |
| `vendor` (all) | Sometimes a brand that is not on the photo (from a garbled reading) | `null` unless the brand name, a product-line word or the exact model is on the photo | Expect `null` more often; ask the user for the make |
| `status` (switch) | Not written by the switch agent itself (run_agent.py added it) | Written by the switch agent too, same values | No change if you use run_agent.py |
| Bad photo (all) | Switch: no result.json; router / appliance: error text `ValueError: cannot read ...`; a tiny image gave `not_mapped` / `needs_back` | All three: result.json with `status: error`, exit code 1, `error` = `could not read the photo <path>` or `the photo is too small to map (W x H px)` | Use `status == "error"` for all three; do not parse the error text |
| `needs_back` / `status: needs_back` | Asked for the back side on some fronts whose ports are dark or angled | Those fronts are mapped when enough ports are found again (Barracuda LB 540, Quantum QN-S-50) | Fewer back-side requests |

## 3. Behaviour changes (no field changes)

1. Template names: a model read without its prefix (SG2428P) uses the catalogue name (TL-SG2428P),
   so one model no longer gets two templates.
2. Switch side ports are named from the label printed next to them (CONSOLE, MGT, USB).
3. More ports in some results: side ports (FS S3900-24T4S now 30 ports with Mgmt) and recovered ports.
4. Ports the model missed are found again (`port_recovery.py`) only under strict checks: same row as
   2+ found ports, directly beside one at the port spacing, same size and look, no overlap - so no box
   on LEDs, labels, logos, fan grilles or screens, and no duplicates.

## 4. New settings (environment variables, all optional)

| Variable | Default | Effect |
|---|---|---|
| `PORT_STATUS_MODEL` | `port_status.pt` in the agent folder | Use another cable-status model |
| `PORT_RECOVERY` | on | `0` turns the missed-port recovery off |
| `PYTHONDONTWRITEBYTECODE` | set to 1 by run_agent.py | Nothing (not even .pyc files) is written into the agent folders |

## 5. Not in this version

Template recall (compare saved templates when the make is read and the model is not) and the
switch catalogue rebuilt from the team workbook are not changed yet. Details of every fix:
`REVIEW2_FIXES.md`.
