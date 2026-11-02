# Sign-in and organizations

racktrack-app now has one sign-in: RT-EOS's. A small Node service (the sign-in service) runs RT-EOS's own sign-in, organizations, sites, people, invites, settings and platform owner code. The video engine (server/app.py) trusts the session that service signs. Nothing was rebuilt: the code is RT-EOS's, cut down and wired.

## How to start

1. `./auth.sh` - the sign-in service on http://127.0.0.1:3302.
   On the first start it:
   - makes `data/auth/.env` (a new JWT_SECRET, the database path `data/auth/db/auth.db`, the mail outbox `data/auth/outbox`, the engine's addresses as allowed origins),
   - builds the RT-EOS screens into `rt_eos/client/dist-org` with the base path `/org/`,
   - seeds the accounts below.
   Set `RT_SEED_PASSWORD` before the first start to choose the password. Without it a strong one is generated. Either way it is written to `data/auth/seed-password.txt` (mode 600, never committed, never printed).
2. `./engine.sh` - the engine on http://127.0.0.1:8700 (set `RACKTRACK_PORT` for another port). It reads `JWT_SECRET` and the database path from `data/auth/.env`.
3. Open http://127.0.0.1:8700/app/ and sign in.

Use the same host name for both (127.0.0.1 or localhost, not one of each). The session cookie belongs to the host, so the engine and the service share it across their two ports.

Stop either with `kill $(lsof -ti TCP:<port>)`.

## Seeded accounts

All five use the password in `data/auth/seed-password.txt`.

| Username | Role | Organization | Site |
|---|---|---|---|
| owner | platform owner | - | - |
| orgowner | organization owner | Demo Organization | Hyderabad DC (setup complete) |
| admin | admin | Demo Organization | Hyderabad DC |
| manager | site manager | Demo Organization | Hyderabad DC |
| tech | technician | Demo Organization | Hyderabad DC |

The proof adds a second organization (admin2, tech2, Pune DC) and a second site of the first organization (admin3, tech3, Chennai DC) with `node rt_eos/server/scripts/seed-auth.js --org ... --site ... --suffix 2`.

## The five roles

The roles are exactly five. The value stored for each stays RT-EOS's, so its checks keep working; every screen shows the name in the first column. `requireRole` in rt_eos/server/auth.js holds the list (ROLES) and lets an organization owner through wherever an admin may go.

| Role | Stored as | Who | What they may do |
|---|---|---|---|
| Platform owner | `owner` | RackTrack | Approve or reject organizations, see every organization, site and run, the platform console (logs, dashboard), edit any admin or organization owner. Never sent into setup. |
| Organization owner | `org_owner` | The first person of an approved organization (approval promotes the organization's first admin; the seed makes `orgowner`) | Everything an admin may do, and add, edit or remove the organization's admins. Only the platform owner edits an organization owner. |
| Admin | `org_admin` | Runs one organization; the role that works with portal.racktrack.ai | The organization's settings, sites, people and invites, every run, copy and connection of the organization, first-run setup. Cannot edit another admin or the organization owner. |
| Site manager | `site_manager` | Runs one site | The site's people (technicians: invite, password, on or off), the site's runs. |
| Technician | `member` (shown as Technician everywhere) | Scans racks | Scan, see the runs of their own site, compare with the organization's copies. Never gated by setup. |

Checked by name in: auth.js (requireRole, canManageOrg, canAccessSite, member edit and remove), lib/estate.js, lib/estate_profile.js, lib/rack_access.js, lib/scan_site.js, lib/site_scope.js, lib/tenant.js, routes/home.js, routes/org_data.js; the engine's server/rt_session.py (which runs a person sees); the screens' utils/format.js, utils/setupGuard.js, SitePage.jsx, SetupPage.jsx and mobile/app.js (the Profile entries and the setup redirect).

## Removed by the owner's order (2 Nov 2026)

- Google, Apple and Facebook sign-in: rt_eos/server/socialAuth.js, lib/oauthProviders.js, their routes in auth_service.js and app.js, the buttons on the engine's sign-in screen and RT-EOS's invite page, AuthCallbackPage, SocialSignIn and utils/socialSession.js, and the provider settings in rt_eos/.env and .env.example. The social_identities and oauth_states tables stay in the schema, empty; no code path offers them. GET /api/auth/providers answers 404.
- Logical network features: a search of the served code and screens (mobile/, server/, rt_eos/server/auth_service.js and the routes it mounts, rt_eos/client/src) for firmware, marketplace, EVE-NG, Lab, Netdisco and SSH found none (only words like "hasShelf"). The switch_specs lookups stay. RT-EOS's full server (rt_eos/server/app.js) is not started by this app.

## What a person sees

- **Sign in** (engine app): email or username and password, Forgot password? (a code by mail, then a new password), Create an organization (opens RT-EOS's own sign-up at /org/login). There is no Google, Apple or Facebook sign-in (removed, see below).
- **Invite link**: `/app/#invite/<code>` shows the organization and site and asks for a username and password. RT-EOS's own link `/org/invite/<code>` works too.
- **Waiting**: someone whose organization is not yet approved sees "Waiting for approval". The engine answers 403 to every call until the platform owner approves.
- **First-run setup**: an organization admin whose organization is not set up is sent to RT-EOS's four-step setup popup at /org/setup. A technician is never sent there (RT-EOS decides this, as before).
- **Profile** (engine app), by role, as RT-EOS's own routes allow:
  - Platform console (owner only)
  - Organization (owner and organization admin)
  - Sites and people (owner, organization admin, site manager)
  - Settings (everyone)

  Each entry opens the RT-EOS screen at `<service>/org/...` with the same cookie.

## What the engine checks

- Every route except `/api/health` and `/api/auth-service` (which tells the screens where the service is) needs the RT-EOS session: the `rt_access` cookie, or `Authorization: Bearer` for the native app.
- The token is checked as RT-EOS's requireAuth checks it: HS256 only, issuer `racktrack`, audience `racktrack-app`. The account is then read from the service's database, read only: not signed out, still active, same token version, and an organization that is approved.
- The name-only `/api/login` is gone. `/api/me` answers with the person's name, role, organization and site.
- Runs are stored in `data/runs/<org>/<site>/<run>`.
  - A technician or site manager sees the runs of their site.
  - An organization admin sees all runs of the organization.
  - The platform owner sees every run.
  - Anyone else gets 404 for the run, its view, its report, its drift and its delete.
  - A run from before sign-in (`data/runs/<run>`) has no organization, so only the platform owner sees it.
- Database copies are per organization (`data/sources_by_org/<org>/sources.db`). So are workbook uploads (`data/sources/orgs/<org>/`) and connections (already keyed by organization; now by the signed-in person's organization). A run's drift is checked against its own organization's copies.

## Mail

With no SMTP settings, every message (sign-up code, reset code) is written as a JSON file to `data/auth/outbox/`. Nothing is sent. Add `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` and `SMTP_FROM` to `data/auth/.env` to send for real.

## Proof

`_cache/venv/bin/python tools/proof_auth.py --engine http://127.0.0.1:8700` needs the service on 3302 and the engine (8701 by default; 8700 since the merge). Run it with `--fixtures` first, which seeds the second organization and site and places four stored runs.

On 2 Nov 2026 all 31 checks passed (`docs/proof/auth/results.json`):

- 47 engine routes, each called without a cookie and with a token signed by another key, all refused.
- The technician sees only their site's run. The other three runs answer 404.
- The admin sees both sites of the organization. The second organization's admin sees only its own run. The owner sees all four.
- Copies, uploads and connections stay inside their organization.
- A Gmail sign-up gets its code from the outbox. The organization stays pending and the engine answers 403 until the owner approves it, then the same session works.
- Forgot password and an invite both end in a working engine session.
- The screens at 1280 and 400 px are in `docs/proof/auth/`.

RT-EOS's own server tests for these parts still pass: smoke, setup, setup mount, tenant isolation, org data, home and site manager access (64 tests, `npm test -- test/<file>`).

## Reused from RT-EOS

- **Unchanged**: `auth.js` (sign-in, sign-up, codes, refresh, invites, organizations, sites, members, approval), `routes/setup.js`, `routes/home.js`, `routes/org_data.js`, `routes/scan_sites.js`, `lib/estate.js` and the libraries they need, and the React screens for sign-in, setup, sites, people, invites, settings and the console.
- **Changed**:
  - `rt_eos/server/auth_service.js` (new): app.js's security headers, CORS, CSRF check, credential rate limit and the routers above. Nothing else is mounted, and the /org/ screens are served from it.
  - `auth.js`: an outbox mail provider when `RT_MAIL_OUTBOX` is set and no SMTP is configured.
  - `routes/owner_console.js` (new): the logs and dashboard routes moved unchanged out of app.js, so the service can serve the console too. app.js mounts them where they were.
  - `scripts/seed-auth.js` (new, from seed-local-pilot.js): the four accounts, no password in the code.
  - Client:
    - `App.jsx`: the router takes its base path from the build (`/org/`).
    - `AuthContext.jsx`: a protected screen waits for the first session check before sending a person to sign in. This is needed when the screens are opened with only the cookie.

## Not done yet

- The camera-guide takes (`/api/captures`), close-ups and photos now need a session but are not yet split by organization.
- The native app reaches the service at the engine's host on port 3302. A phone on another network needs `RT_AUTH_URL`, which the engine passes to the screens.
- Social sign-in was not tried with real keys (no outside call allowed).

## After the merge into rt-eos-integration (2 Nov 2026, evening)

- The NetBox and ServiceNow mocks moved to 3303 and 3304 (sources.sh, src/source_db.py, the tests and proofs), so the sign-in service keeps 3302 and nothing uses 3301 or 5301.
- tools/proof_auth.py rerun on the merged branch with the engine on 8700: 31 checks passed (the provider check now asserts the provider route is gone).
- RT-EOS server tests for these parts: 76 passed (smoke, setup, setup mount, setup profile, tenant isolation, org data, home, site manager access).
