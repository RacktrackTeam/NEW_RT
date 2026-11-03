# What changed since the first 28 Oct package (every result now carries the device's specification)

Package: `port_agents_2026-10-28_spec.zip`. Same entry point, same calls; every existing result field
is unchanged. One field is added: `spec`. (Coming from an older package? Read
`CHANGES_SINCE_2026-10-25.md` and `CHANGES_SINCE_2026-10-27.md` first, then this.)

## 1. How to update

Replace the three folders `switch_agent/`, `router_agent/`, `appliance_agent/` and `run_agent.py`
with the ones in this zip. `requirements.txt` lists the same packages (`beautifulsoup4` and
`pymupdf` were already required; only their comments changed) - make sure `pymupdf` is installed.

No search key or paid service is needed. (`search_keys.env` can later take a Brave or Google key,
which adds a web search for vendors the built-in rules do not cover; it is optional.)

## 2. What changed

| | First 28 Oct package | This package |
|---|---|---|
| Result | port map, make / model, template | the same, plus `spec`: the identified model's ports by type, rack units, data sheet link, and whether the mapped ports agree with it |
| Where the spec comes from | - | switches: the switch catalogue's port counts (read only), then RackTrack's spec database; all devices: the model's page or data sheet on the vendor's own site - the pages the template step opened in the run, and built-in rules for MikroTik, Ubiquiti, TP-Link, NETGEAR, D-Link, Fortinet, Palo Alto |
| New code | - | `device_spec.py` in each agent folder (one file, the same in all three); `switch_agent/spec_data/switches.db`; the template step's page readers pass the pages they open to `device_spec` |
| Settings | - | `PORT_AGENT_SPEC=0` skips the lookup |

The `spec` field (full description in the README, section "Specification"):

| Field | Meaning |
|---|---|
| `found` | true when a spec was found; false with `reason` (no model identified, no page of the model with port details on the vendor's site) |
| `ports` | counts by type: `rj45`, `sfp`, `qsfp`, `combo`, `console`, `usb`, `management_port`, `other` |
| `data_ports`, `total_ports` | network ports (rj45 + sfp + qsfp + combo); all ports |
| `port_config` | the same as one line, e.g. `24x RJ45 + 4x SFP + 1x console` |
| `rack_units` | 1, 2 ... or `desktop`; null when the source does not say |
| `datasheet_url`, `source`, `confidence`, `spec_rows` | where it came from, how far to trust it, and the data-sheet lines it was read from |
| `model_from` | only when the spec is for a model the result leaves out: the switch agent in auto mode read the model clearly but fitted no template |
| `matches_photo` | `agrees` true / false, the RJ45 / SFP counts of the photo and of the spec, and a `note` when they differ |

`spec` is null when the run failed (`status` error). `matches_photo.agrees` false does not change the
mapping; it marks a result worth a look (for example a template that missed two SFP cages).

## 3. Effect on results

The port maps are unchanged: the spec is looked up after mapping. Checked on 11 test photos of all
three agents (same status, make, model and port count as before) and through `run_agent.py` on the
package (10 cases; the package folder is not written to). A run takes about 1-3 s longer.

Specs on the test photos, with no search key: Palo Alto PA-200 `4x RJ45 + 1x console + 1x
management` (vendor data sheet; agrees), MikroTik RB3011UiAS-RM `10x RJ45 + 1x SFP + 1x console +
1x USB` (vendor page; agrees), TP-Link TL-SG3424 and TL-SG2424 `24x RJ45 + 4x combo` (vendor pages;
agree), MikroTik CRS328-24P-4S+RM `24x RJ45 + 4x SFP + 1x console` (catalogue; the photo was mapped
with only 2 SFP - flagged), Arista 7050SX3-48YC8 `48x SFP + 8x QSFP` (catalogue). No spec: Peplink
Balance 305 (page built by script), Dell PowerConnect 2824 (end of life), FS S3900-24T4S; and the
photos where no model was read.

Also checked directly on the vendors' sites: Ubiquiti ER-4, ER-6P, UDM-Pro; TP-Link ER7206,
ER8411, TL-SG1016; NETGEAR GS724T, GS110TP, GS752TP; Fortinet FortiGate 90G and 60F; Palo Alto PA-220.

## 4. Limits

- Without a search key, vendors without a built-in rule (Cisco, Juniper, SonicWall, F5, ...) get a
  spec only when the template step opens the model's page in that run - not when a saved template
  is used straight away.
- Sites that block scripts or build their pages by script give no spec; nor does a data sheet
  whose port table is an image.
- Modular routers list fixed ports and module slots, not the installed modules.
