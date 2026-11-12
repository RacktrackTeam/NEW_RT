# Fixes for the step-2 code review (28 findings)

All findings were fixed except #3 (not requested) and #20 (left on purpose). Every change was
tested on the full photo sets (50 router, 87 switch, 48 firewall, 49 load balancer, 21 gateway
photos) and each changed overlay was checked by eye.

| # | Finding | Fix |
|---|---|---|
| 1 | Brand taken from a sticker | Brand names of 4 letters or fewer must match a whole printed word; serial / MAC / part-number text is ignored for the brand; a near-tie goes to the larger printed brand; `vendor_doubt` reported. Same 181 of 198 vendors right as before, false matches gone |
| 2 | Ports mapped on LED-only fronts | The visible-ports check runs before auto mode in every case |
| 3 | No offline mode / time limit | Not changed (not requested). Use `timeout` in `run_agent.py` |
| 4 | Unsafe in one process | Each agent in its own folder; `run_agent.py` runs each in its own process |
| 5 | Another model file changes the code path | No default RT-EOS folder: the shipped port model is always used unless `RACKTRACK_REPO` / `SWITCH_FIT_MODELS` is set on purpose |
| 6 | Failures leave no result | Bad photo is a normal error; every failure writes result.json with `error`; the F5 empty-list crash is fixed; the exit no longer aborts after a good result (exit code 134 on macOS) |
| 7 | Switch saves templates without a catalogue check | Saved only for catalogue models; an existing template is replaced only by a better fit (old one kept in `templates_replaced/`) |
| 8 | Switch fits a look-alike model | A reading that is a catalogue model is never renamed; the detector's port count has a 25% margin |
| 9 | Device type must be lowercase | Any case and spelling ("Firewall", "load balancer", "LB"); clear error otherwise |
| 10 | Small devices not snapped | Snapping minimum 3 ports for routers and appliances (8 for switches) |
| 11 | Stale router templates | 15 rebuilt with the current code; the rebuilt ASR1001 was worse and the old one kept; 6 without a test photo or fit unchanged |
| 12 | Writes outside the output folder | All three agents: with `learn` off (the default) nothing outside `out` is written: candidates and caches live in `out` and are removed, the template store is read-only; 227 leftover try folders removed; store rows no longer auto-"preferred" |
| 13 | Variant skips the base template | The base model's saved template is tried before going online |
| 14 | Heavy compute | Port detections cached per image within a run |
| 15 | No make confidence | `identify_confidence` in the result |
| 16 | Two drifted router copies | One router copy (the newest); it no longer imports the appliance package |
| 17 | Security gateways in the router catalogue | Checked: all 625 are in the firewall catalogue as well, so the firewall route reaches them; no change |
| 18 | Result text for people | Fixed `status` codes and `suggested_device`; `reason` stays for people only |
| 19 | requirements need Python 3.11 | Ranges tested on Python 3.10 and 3.11 |
| 20 | Switch web search on by default | Not changed on purpose: it is the same question as #3, and turning it off lowers switch accuracy. The scraping part is covered by #27 |
| 21 | Failed pages cached forever | Empty pages are not cached; pages expire after 30 days, searches after 14 |
| 22 | Negative ignore regions | Regions clipped in code; 13 old templates whose region never took effect were cleared to keep their tested behaviour |
| 23 | Result fields differ from the docs | Fields always present; port status in a fixed set; every port has `source` and `chassis` |
| 24 | Stale README counts | READMEs rewritten (switch catalogue 110 vendors / 6,558 models; router 131 / 2,791; appliance firewall 1,481, load balancer 498, gateway 1,176) |
| 25 | Hard-coded paths | `F:\` and personal paths removed from code and 39 templates; local image sources stored as file names |
| 26 | Dead code and leftovers | Not in this package: UIs, eval and tool folders, catalogue builders, backups, duplicate model files |
| 27 | Scraped content shipped; Bing / Yandex scraping | Web caches and reseller images are not in the package; search-engine scraping is off unless `PORT_AGENT_SEARCH_ENGINES=1` |
| 28 | Docs describe missing folders | Corrected |

Also fixed while testing: a template accepted with no port lining up; a model named from a
look-alike panel on too few agreeing ports (now 60% needed, and never from a face with no ports);
the serial filter catching model text; a switch crash after a this-photo-only template.
