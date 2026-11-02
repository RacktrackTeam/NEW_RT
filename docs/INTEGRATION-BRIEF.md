# RT-EOS into racktrack-app: the integration brief

Read this and docs/plan/plan.html before you touch anything. The plan page is the owner's approved plan: its sections 01 to 06 say what to build, section 08 the steps, section 09 the decisions taken.

## What this version is
A video is the only way to scan. racktrack-app is the base and keeps its engine and port agents. RT-EOS comes in whole: sign-in, organizations, sites, people, settings, its readers, Excel checks, screens and reports. dark_mobile gives only its NetBox and ServiceNow CMDB code. Every one of the 56 features in plan section 06 must work on a video scan. The pipeline is plan section 01: video, read, the ranked metadata card, the site's database copied into ours, the 5 closest racks, the exact match, the compare, the drift, the remaining features.

## Where things are
- This work folder: /Volumes/Racktrack/racktrack-app-integration, branch rt-eos-integration, a copy of racktrack-app at tag before-rt-eos. Commit here. Never commit on main.
- RT-EOS code to bring in, read only: /Volumes/Racktrack/one_racktrack_sources/rt_eos (commit c53b6177). Its node_modules are cached at _cache/rt_eos_node_modules/{server,client}, its Python packages at _cache/venv (python 3.10, torch 2.14, the same versions racktrack-app runs on today), its models at _cache/rt_eos_Models.
- dark_mobile code for NetBox and CMDB only, read only: /Volumes/Racktrack/one_racktrack_sources/dark_mobile (server/lib/netbox, server/lib/rack_identity.js, server/lib/connection_profiles.js, servicenow/, server/mock_routes.js, client pages DriftPage, ConnectionsPage).
- Sample videos and photos: /Volumes/Racktrack/one_racktrack_sources/samples (video/vdo1.mp4 with its stored run in video/run_of_vdo1, datacenter-rack-photos, cable-flag-test-photos). racktrack-app's own sample run is data/runs/91af02c09bbc in this folder.
- The three live project folders /Volumes/Racktrack/racktrack-app, RT-EOS and dark_mobile are never edited. The pilot runs on RT-EOS.

## Hard rules
1. Never write inside /Volumes/Racktrack/racktrack-app, RT-EOS, dark_mobile, racktrack_portal, one_racktrack or racktrack_docs. Never read a .secrets folder, a CREDENTIALS file, a TESTER file or another project's .env. port_agents/*/search_keys.env holds API keys: never print it, never commit it (it is ignored).
2. Never call a real outside system: no real NetBox, ServiceNow, switch, Netdisco, EVE-NG, Teams, Outlook, mail, image search, makers' pages, Firebase, GitHub or demo.racktrack.ai. Use local mocks and fixtures. The port agents' online image search stays off in tests (use their saved templates). Mail goes to a local outbox folder.
3. No deploy, no git push, no store or Firebase upload, no ngrok. No OS-level keystrokes or clicks (no osascript, CGEvent, cliclick); headless Playwright and simctl only.
4. This Mac overheats and the live pilot may be running models. Everything that loads a model, including a server boot that preloads models, runs through scripts/with-model-lock.sh with OMP_NUM_THREADS=2, one at a time. A video run takes about 10 minutes; plan for it, do not start several.
5. Ports: the integration server listens on 3301 (RT-EOS side) and 8700 (video engine), the dev client on 5301. Never use 3001, 8600 or 5173, which belong to the live apps. Stop a server you started by its real pid (lsof -ti TCP:<port>).
6. Run server tests with npm test and set RACKTRACK_AUTH_DB to a folder inside this work area first, so nothing writes into another project's data.
7. Never put a password or key in the code. Seed scripts read them from the environment or generate them.
8. Commits: plain sentence messages, no Co-Authored-By or any AI attribution. Commit in logical steps.
9. All screen text and documents: plain simple English, the plain hyphen only, never a long dash. No AI phrasing.
10. UI: the RT-EOS minimal look, pure white, light theme only. Pages span the full device width and work at phone width. Every screen is checked at 1280 and 400 px wide in headless Playwright.
11. Security: every route needs sign-in unless it is deliberately public (sign-in, invite, health). A stranger to a scan or an organization gets 404, not 403. Two organizations never see each other's scans, racks or database copies.

## Decisions taken (plan section 09)
- The RT-EOS Node server is the front door, inside this repo, and the racktrack-app engine is a private service behind it, started and owned by the Node server. The name-only sign-in goes.
- The chosen database (Excel, NetBox or ServiceNow CMDB) is read into our own database per site and every check runs on that copy. Nothing is ever written back. No live network layer.
- The Excel compare also covers Manufacturer, Model, Serial_Number and Asset_Tag.
- Serials and asset tags are read from the video's best frames and from a device close-up photo when the video is too small.
- The phone sends its position when a sweep starts; a site gets coordinates when it is set up (typed, or looked up once from the address through a provider the owner approves; until then typed). Location only shortlists racks, it never decides.
- Every screen uses the RT-EOS look. Video screens are rebuilt in it.
- Run time is accepted for now: about 10 minutes a video on this Mac, one at a time.

## Layout
- server/ becomes the RT-EOS Node server (its app.js, auth.js, lib/, routes/) plus a video module that drives the Python engine. Keep RT-EOS's file layout so its tests keep passing; add, do not rewrite.
- engine/ holds the racktrack-app Python engine (today's src/, port_agents/, patchpanel_classify_overlay/) and the RT-EOS pipeline/ side by side, under one Python environment (_cache/venv, which already runs both). The racktrack-app FastAPI server (server/app.py) becomes an internal service with no sign-in of its own, reachable only from the Node server on 127.0.0.1.
- client/ is the RT-EOS React client; the racktrack-app mobile/ screens are rebuilt inside it. native/ keeps the app id com.racktrack.sweep.
- Data: one SQLite database (RT-EOS's auth.db and index.db) and the RT-EOS file store (organization, site, rack, scan). A video run's files go into the scan's folder. data/runs is gone.
- The database copies: one table set for racks, devices, ports and cables per site and source (excel, netbox, cmdb), with the source, version and time of the copy. The Excel import already fills such a set in RT-EOS; NetBox and CMDB fill the same set.

## Report format at the end of any step
For each step and each feature touched: done, partly or blocked, what test or run proves it, what is still missing. Then every change to the plan you had to make and why, and anything only the owner can supply. Never claim more than you ran.
