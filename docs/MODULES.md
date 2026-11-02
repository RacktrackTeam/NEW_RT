# Modules built in parallel today (2 Nov 2026), joined this evening

Steps 6 to 10 of the plan are built at the same time as steps 1 to 5, each in its own worktree and its own folder under modules/. They meet through the shapes below. Do not rename a field; add fields if you must and say so in your report. Code only in your own modules/<name>/ folder and docs/<name>.md. The join agent wires the modules into the server, client and engine layout that steps 1 to 5 produce.

Worktrees: /Volumes/Racktrack/racktrack-app-integration-wt/<name>, branch wt/<name>. Shared caches are only in the main work folder: Python at /Volumes/Racktrack/racktrack-app-integration/_cache/venv/bin/python, RT-EOS node_modules at _cache/rt_eos_node_modules/{server,client}, RT-EOS models at _cache/rt_eos_Models, the model lock at /Volumes/Racktrack/racktrack-app-integration/scripts/with-model-lock.sh. RT-EOS source, read only: /Volumes/Racktrack/one_racktrack_sources/rt_eos. dark_mobile source, read only: /Volumes/Racktrack/one_racktrack_sources/dark_mobile. Samples: /Volumes/Racktrack/one_racktrack_sources/samples. Every test writes only inside its own worktree.

## The reading: reading.json (one per scan)
Written by the adapter of step 5 from a video run, extended by modules/reads_on_video.
```
{ "scanId": "RK-...", "siteId": 3, "organizationId": 1,
  "capture": { "lat": 17.4, "lng": 78.4, "accuracyM": 20, "at": "2026-11-02T10:00:00Z" } | null,
  "rack": { "label": "SP-HYD-RM01-R01-R1", "labelConfidence": 0.91, "labelSource": "above the first unit|rail|inferred from device ids|person",
            "heightRu": 42, "candidates": [ { "text": "...", "confidence": 0.6, "source": "rail" } ] },
  "devices": [ { "index": 0, "units": ["u20","u19"], "kind": "Switch", "kindConfidence": 0.97,
                 "make": "Cisco", "model": "C9200L-48P-4X", "makeModelConfidence": 0.8,
                 "ids": [ { "text": "SP-R1-U20-SW01", "confidence": 0.7, "source": "front label|sticker|close-up" } ],
                 "serial": { "text": "FOC...", "confidence": 0.5, "source": "close-up" } | null,
                 "assetTag": { "text": "...", "confidence": 0.5, "source": "..." } | null,
                 "ports": [ { "number": 1, "type": "RJ45|SFP|QSFP|...", "occupied": true, "cap": "capped|empty|unknown", "cableColour": "blue" | null, "cableType": "copper|fibre" | null } ],
                 "pduOutlets": 8 | null, "sfpCages": [ { "number": 49, "type": "SFP+" } ] } ] }
```

## The copy of the site's database: tables in our own database
One row per copy in `source_copies(id, organization_id, site_id, source 'excel'|'netbox'|'cmdb', version, imported_at, note)`. Then, each with `copy_id` and `organization_id`:
- `copy_racks(rack_key, name, label, facility_id, height_ru, site_name, building, floor, room, row)`
- `copy_devices(rack_key, device_key, name, label, role, kind, manufacturer, model, serial, asset_tag, ru_start, ru_height, face)`
- `copy_ports(device_key, port_key, name, number, type, speed, connector, expected_occupied, expected_dust_cap)`
- `copy_cables(cable_key, label, a_device, a_port, a_label, z_device, z_port, z_label, type, colour, state)`
The site's active copy per source is the newest row of source_copies for that site and source. Which source a site uses: `sites.source` ('excel'|'netbox'|'cmdb'), set in settings. Excel fills these tables from the manager's workbook (RT-EOS already parses it in server/lib/workbook.js and sheet_model.js); NetBox and CMDB fill the same tables.

## The metadata card: card.json (one per scan)
`[ { "rank": 2, "kind": "rack_label", "value": "SP-HYD-RM01-R01-R1", "confidence": 0.91, "source": "above the first unit" }, ... ]` with kinds: confirmed_record (1), rack_label (2), device_id (3), serial (4), asset_tag (4), rack_number (5), make_model_at_unit (6), kind_at_unit (7), height_ru (7), position (8), port_pattern (9).

## The match: match.json (one per scan)
`{ "decision": "matched|suggested|unidentified", "rackKey": "...", "copyId": 12, "source": "netbox", "confidence": 0.95,
   "candidates": [ { "rackKey": "...", "name": "...", "score": 0.95, "evidence": [ { "rank": 2, "kind": "rack_label", "read": "...", "record": "...", "tier": "exact" } ] } ],
   "shortlist": [ "rack keys of the 5 closest" ], "confirmedBy": null | { "userId": 5, "at": "..." } }`

## The drift: drift.json (one per scan and source)
`{ "scanId": "...", "copyId": 12, "source": "excel", "comparedAt": "...", "items": [ { "kind": "missing_device|extra_device|moved_device|wrong_kind|wrong_make_model|wrong_serial|wrong_asset_tag|wrong_height|port_type|port_occupied|dust_cap|missing_cable|extra_cable|wrong_port|wrong_label", "unit": "u17", "deviceKey": "...", "field": "model", "record": "C9200L-48P", "read": "C9300-48P", "confidence": 0.8 } ] }`

## Module folders
- modules/reads_on_video (Python): takes a video run folder and reading.json, adds ids, serials, asset tags, make and model for every kind, PDU outlets, SFP cages, cable flags from close-ups. CLI: `python -m modules.reads_on_video.run <run folder> <reading.json> [closeups folder]`.
- modules/sources (Node): importers for NetBox and ServiceNow CMDB into the copy tables, the Connections vault (AES-256-GCM, one key file), Test connection, mock NetBox and mock ServiceNow servers for tests, the ground truth fixtures for the three videos and the one-command loader, the Excel adapter that writes RT-EOS's parsed workbook into the same tables.
- modules/match (Node + React): card builder from reading.json, the shortlist from the phone position and site coordinates, the scorer, the exact rule, routes GET /api/f/match/:scanId and POST /api/f/match/:scanId/confirm, the candidates screen, the site coordinates field for setup, the phone position capture for the sweep screen.
- modules/compare (Node + React): the compare of a reading against a copy for all three sources, drift.json, the drift screen, the drift section of the rack report, and the A-end / Z-end check reusing RT-EOS's checks.js where it fits.
