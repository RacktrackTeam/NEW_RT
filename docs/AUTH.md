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

All three use the password in `data/auth/seed-password.txt`.

| Username | Role | Organization | Site |
|---|---|---|---|
| owner | platform owner | - | - |
| admin | organization admin | Demo Organization | Hyderabad DC (setup complete) |
| tech | technician | Demo Organization | Hyderabad DC |

The proof adds a second organization (admin2, tech2, Pune DC) and a second site of the first organization (admin3, tech3, Chennai DC) with `node rt_eos/server/scripts/seed-auth.js --org ... --site ... --suffix 2`.

## What a person sees

- **Sign in** (engine app): email or username and password, Forgot password? (a code by mail, then a new password), Create an organization (opens RT-EOS's own sign-up at /org/login). Google, Apple and Facebook buttons appear only when the service has their keys. Today it has none, so they are hidden.
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

`_cache/venv/bin/python tools/proof_auth.py` needs the service on 3302 and the engine on 8701. Run it with `--fixtures` first, which seeds the second organization and site and places four stored runs.

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

- **Unchanged**: `auth.js` (sign-in, sign-up, codes, refresh, invites, organizations, sites, members, approval), `socialAuth.js`, `routes/setup.js`, `routes/home.js`, `routes/org_data.js`, `routes/scan_sites.js`, `lib/estate.js` and the libraries they need, and the React screens for sign-in, setup, sites, people, invites, settings and the console.
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
