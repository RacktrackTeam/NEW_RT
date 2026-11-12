# Port mapping agents: switch, router, firewall / load balancer / gateway

Three agents that map the network ports on one photo of a device. Each reads the make and model off
the panel (OCR), finds a template image of that model (a saved template, the vendor's site, or a
web search), fits it to the photo, and returns every port with its type, number, cable status and
outline on the photo. When no template fits, the ports are boxed from the photo alone ("auto mode").

Package date: 28 October 2026 (second build of the day: every result now carries the device's specification). All three agents include the fixes from the step-2 code review
(see `REVIEW_FIXES.md`) and from the Port Agents Review of 26 October (see `REVIEW2_FIXES.md`). Everything each agent needs is inside its own folder; nothing outside
this package is read.

## What is in the package

| Folder / file | What it is |
|---|---|
| `run_agent.py` | The entry point for all three agents (command line and Python). Starts each agent in its own process |
| `switch_agent/` | Switches. Entry `map_any_switch.py`; includes the RackTrack switch OCR (`RT-EOS/pipeline/switch_ocr`), spec scrapers (`RT-EOS/switch_specs`) and spec database (`spec_data/`) |
| `router_agent/` | Routers. Entry `router_mapper` |
| `appliance_agent/` | Firewalls, load balancers and gateways. Entry `appliance_mapper` |
| `requirements.txt` | Python packages, tested on Python 3.10 and 3.11 |
| `REVIEW_FIXES.md` | What was changed for each finding of the step-2 code review |
| `REVIEW2_FIXES.md` | What was changed for the Port Agents Review (26 Oct) |
| `CHANGES_SINCE_2026-10-25.md` | **Already integrated the 25 Oct package? Read this first**: what to replace and which result fields changed |
| `CHANGES_SINCE_2026-10-27.md` | **Already integrated the 27 Oct package? Read this**: the switch catalogue is now an Excel file |
| `CHANGES_SINCE_2026-10-28.md` | **Already integrated the first 28 Oct package? Read this**: the new `spec` field |
| `*/switch_catalog/Switch_Vendors_and_Models.xlsx` | The switch catalogue (117 vendors, 6,955 models matched) the agents read at run time - see "Switch catalogue" below |

The three agents share module names (`panel_ocr`, `yolo_ports`, `switch_fit`, `switch_identify`,
`template_agent`) with different contents. **Never import two agents into one Python process**:
always go through `run_agent.py`, which runs each agent in its own process.

## Setup

```bash
cd port_agents
python3 -m venv .venv            # Python 3.10 or 3.11
source .venv/bin/activate
pip install -r requirements.txt
```

macOS with the python.org Python: that Python has no root certificates. `run_agent.py` points it at
the `certifi` bundle automatically; if you run an agent directly, set
`export SSL_CERT_FILE=$(python -c "import certifi; print(certifi.where())")` first.

## Run

```bash
python run_agent.py switch    photo.jpg --out out/
python run_agent.py router    photo.jpg --out out/
python run_agent.py appliance photo.jpg --out out/ --device firewall      # or load_balancer, gateway
```

```python
from run_agent import map_switch, map_router, map_appliance
r = map_appliance("photo.jpg", device="Load Balancer", out_dir="out/")   # any case
r = map_router("photo.jpg", out_dir="out/", vendor="Cisco", model="ISR4321")   # vendor/model optional
r = map_switch("photo.jpg", out_dir="out/")
```

Options: `vendor` / `model` skip the OCR; `learn=True` saves a template that fits for the next
photo of that model (all three agents); `refresh=True` (router, appliance) ignores saved templates;
`timeout` (seconds) stops a run that takes too long.

## The result

Each call returns the dict written to `<out>/result.json`. Fields always present:

| Field | Meaning |
|---|---|
| `status` | `mapped_template` (a template was fitted), `mapped_auto` (ports boxed from the photo alone), `needs_back` (no network ports on this face: ask for a photo of the back), `not_mapped`, `error` |
| `mapped` | true when ports were mapped |
| `error` | null, or what went wrong (the run never raises; a crash is reported here) |
| `vendor`, `model` | what was identified (null when not read) |
| `ports` | one entry per port: `id` (number, or a name for side ports: Console, Mgmt, USB), `type` (rj45, sfp, console, management_port, usb ...), `status` (connected, empty, not seen, unknown), `polygon` (4 corners in photo pixels), `source` (template or photo) |
| `identify_confidence` | `vendor`, `model` (0-1: how well each is read on the photo) and `vendor_doubt` (another brand on the panel); all three agents |
| `identified_by` | how the make and model were found: OCR, a visual match, or `given` (typed by the caller and not confirmed), `given, confirmed by the template fit` / `... by the panel text` |
| `spec` | the identified model's specification: ports by type, rack units, data sheet link, and whether the mapped ports agree with it - see "Specification" below (`found` false with a `reason` when there is none; null when the run failed) |
| `out_dir` | the folder with the result and images |

Router and appliance results also carry `device` and `suggested_device` (appliance: the label
reads a model of another device type), `learned`, `from_local`, `needs_back`.

**Cable status** (`connected` / `empty`) comes from the port status model `port_status.pt`, shipped
in each agent folder; a port it cannot see is `unknown`, never assumed connected. `not seen` = a
template port with no port detected in the photo (usually under a cable).

**A make / model passed in is a hint**: the agent checks it against the template fit and the panel
text, and says so in `identified_by`. Several photos of one device: pass the model read on one
photo as `model` for the others.
`reason` is a sentence for people; the UI should use `status`, not parse `reason`.

Images in `<out>/`: `1_photo.jpg` (input), `3_template.png` (the template), `4_overlay.png` or
`5_overlay.png` (the photo with the ports drawn), plus the agent's log in `agent_log.txt`.

When `status` is `needs_back`, run the agent again on a photo of the back with the `vendor` and
`model` from the first result (backs rarely carry the label).

## Settings (environment variables)

| Variable | Default | Effect |
|---|---|---|
| `PORT_AGENT_SEARCH_ENGINES` | off | `1` lets the agents search Bing and Yandex image results through a headless browser for template images. Off, they use saved templates, the vendors' own sites, and the search APIs below |
| `BRAVE_SEARCH_API_KEY` (or `GOOGLE_CSE_KEY` + `GOOGLE_CSE_CX`) | blank | Search API keys, also readable from `search_keys.env` in each agent folder |
| `PORT_AGENT_CACHE_DIR` | each run's own `out/_cache` (removed after) | A writable folder for vendor-page, sitemap and datasheet caches kept between runs (faster repeat lookups) |
| `PORT_AGENT_SPEC` | on | `0` skips the specification lookup (`spec` then says so) |
| `PORT_STATUS_MODEL` | `port_status.pt` in the agent folder | Another cable-status model (classes Connected_port / Empty_port) |
| `RACKTRACK_REPO` / `SWITCH_FIT_MODELS` | unset | Only set these to use the RT-EOS two-model port detector instead of the shipped one (not tested in this package) |

**What is written where.** By default a run writes only into its `out` folder: the agent folders
are read, never changed (working copies and caches live in `out` and are removed at the end).
With `learn=True` a template that fits is saved in the agent folder for the next photo of that
model (`switch_agent/templates/` - catalogue models only -, `router_agent/router_templates/`,
`appliance_agent/appliance_templates/`, and the template stores), so the agent folders must then
be writable.

## Specification

After mapping, each agent looks up the specification of the make and model it identified (or was
given) and adds it to the result as `spec` - the same step in all three agents (`device_spec.py`):

```json
"spec": {
  "found": true, "vendor": "Fortinet", "model": "FortiGate 60F", "device": "firewall",
  "ports": {"rj45": 10, "console": 1, "usb": 1},
  "data_ports": 10, "total_ports": 12,
  "port_config": "10x RJ45 + 1x console + 1x USB",
  "rack_units": null,
  "datasheet_url": "https://www.fortinet.com/.../fortigate-fortiwifi-60f-series.pdf",
  "source": "vendor data sheet (PDF)", "confidence": 0.8,
  "spec_rows": ["GE RJ45 WAN / DMZ Ports: 2 / 1", "GE RJ45 Internal Ports: 5", "..."],
  "matches_photo": {"agrees": true, "photo": {"rj45": 10, "sfp": 0}, "spec": {"rj45": 10, "sfp": 0}}
}
```

- `ports` uses the result's port types: `rj45`, `sfp` (SFP / SFP+ / SFP28), `qsfp`, `combo` (an RJ45
  jack and an SFP cage sharing one port), `console`, `usb`, `management_port`, `other` (serial, DSL,
  module slots). `data_ports` = rj45 + sfp + qsfp + combo.
- `spec_rows` are the data-sheet lines the counts were read from, to check by eye.
- `matches_photo.agrees` false (with a `note`) means the mapped ports differ from the spec - e.g. a
  template that missed two SFP cages. Worth a look; the mapping itself is not changed.
- `confidence`: 0.9 the vendor's own PDF, 0.85 the vendor's page, 0.8 the switch catalogue, lower
  for other sites or a series data sheet; +0.05 for each other source giving the same ports.

Where it comes from, first answer wins - **no search key or paid service is needed**:
1. switches: the switch catalogue's port counts (`Ports (workbook)` / `Total ports`, about 5,700 of
   7,164 switches; read only), then RackTrack's spec database (`switch_agent/spec_data/switches.db`);
2. all devices: the model's own page or data sheet on the vendor's site:
   - pages and data sheets the template step opened in the same run (the vendor's product pages,
     sitemaps, Fortinet / Cisco documents ...), read from the copy the agent already has, and
   - fixed addresses and the vendors' own site search: MikroTik, Ubiquiti, TP-Link, NETGEAR,
     D-Link, Fortinet, Palo Alto Networks.
   The port rows of their spec tables are read (a series data sheet in the model's column).
3. optional, later: a search key (`BRAVE_SEARCH_API_KEY`, or `GOOGLE_CSE_KEY` + `GOOGLE_CSE_CX`) in
   `search_keys.env` adds a web data-sheet search for vendors the rules above do not cover. Not needed.

No model identified: `found` false - pass `model` to get the spec. Switch agent in auto mode: the
result's `model` stays null (no template fitted), but a model read clearly on the panel still gets
its spec, marked `model_from`.
The lookup adds a few seconds per run with the web (pages are cached for the run); it never stops
a run - a failure is reported in `reason`.

## Switch catalogue

The vendor and model names the agents match the text on a photo against are in
`switch_catalog/Switch_Vendors_and_Models.xlsx` (one copy in each agent folder). The switch agent reads
sheet `Switches` (rows with `Used by agent` = Yes) and sheet `Vendors` (column `OCR spellings (used by
agent)`); the router and appliance agents read the switch vendor spellings from it. Edit the file to add or
remove models - there is no build step; sheet `Notes` explains every column. Keep sheet and column names.

## Accuracy (tested 22-24 October 2026, clear-text photos, every overlay checked by eye)

| Agent | Photos | Vendor correct | Model correct | Usable overlay | Wrong overlays (all photos) |
|---|---|---|---|---|---|
| Router | 32 clear of 50 | 100% | 100% | 97% | 2 |
| Switch (models in the catalogue) | 43 clear of 87 | 98% | 84% | 95% | 0 |
| Firewall | 35 clear of 48 | 100% | 74% | 80% | 3 |
| Load balancer | 34 clear of 49 | 97% | 76% | 65% | 4 |
| Gateway | 19 clear of 21 | 84% | 79% | 47% | 3 |

"Usable overlay" = the template lands on every port (small issues allowed) or a correct
back-side request. Measured with search engines ON; with them off (the default) fewer new models
find a template online and fall back to auto mode. Typical run time 10-60 s per photo; a first
lookup of a new model can take a few minutes.

## Known limitations

- A model not in the catalogue and with no findable image falls back to auto mode: ports are
  boxed from the photo and numbered left to right, not with the vendor's numbering.
- Dark or angled ports can be missed by the port detector; on a face with fewer than 2 detected
  ports the agent asks for the back side (Barracuda LB 540, Quantum QN-S-50, Stormshield SN200 in
  the test set).
- Port numbering on some saved switch templates follows the default (1 on top) where the panel
  prints otherwise and the labels cannot be read (Cisco SG112-24).
- The specification is found without a search key for catalogue switches, the vendors with rules
  (MikroTik, Ubiquiti, TP-Link, NETGEAR, D-Link, Fortinet, Palo Alto) and models whose page the
  template step opens; other vendors (Cisco, Juniper, SonicWall, F5 ... with a saved template) and
  sites that block scripts or build pages by script (Peplink) can give none. Modular routers (Cisco
  ISR / ASR) list fixed ports and module slots, not the installed modules, so `matches_photo` can
  disagree there.
- Load balancers have no class in the device classifier; the caller must pass `device`.
- There is no overall time limit inside the agents (use `timeout` on the call) and no offline
  switch: with no saved template a run looks online (review finding #3, not changed).
