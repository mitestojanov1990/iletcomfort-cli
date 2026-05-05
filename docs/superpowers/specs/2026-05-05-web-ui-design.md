# Web UI for iLetComfort CLI — Design

**Date:** 2026-05-05
**Status:** Approved (brainstorming) — pending implementation plan

## Goal

Provide a small, password-gated, on-demand web dashboard that displays all
read-only data the existing `ILetComfortClient` can fetch from the iLetComfort
/ ITS BTRI cloud API:

- Appliance list and per-device metadata
- Status frame (subtype 0x01) — using the firmware-aware decoder already in
  `iletcomfort_client.py`
- Sensor frame (subtype 0x02) — decoded if spec layout, otherwise raw hex
- Raw hex of both frames, for ongoing reverse-engineering work

Read-only by design. No control commands.

## Non-goals

- No control / SET commands.
- No multi-user accounts. Single-user, single password.
- No internet exposure. Defaults to localhost binding.
- No realtime / push updates. Manual refresh only.
- No JS framework, no build step, no static-asset pipeline.
- No CSRF protection (single-user, very low attack value — deliberate YAGNI).
- No TLS. If LAN-exposed, the operator accepts plaintext password risk.

## Constraint that drives the design: the token war

The Dollin cloud allows exactly one active session per account. Each successful
login (CLI, web, iOS app) immediately invalidates the previous token. This
makes coexistence with the iOS app the primary UX consideration.

**Design choice:** on-demand refresh only. The web UI hits the API only when
the user explicitly requests it (page load or Refresh button). Token is cached
on disk (`~/.iletcomfort_token`) and shared with the CLI, so the iOS app is
disturbed only when the cached JWT actually expires (~24 h) or when the user
takes an action that requires a fresh call after an iOS-app-induced
invalidation.

## Architecture

Single new Flask application, `iletcomfort_web.py`, sitting next to the
existing CLI module. Imports `ILetComfortClient` as a library and reuses every
existing read method untouched.

```
iletcomfort-cli/
├── iletcomfort_client.py     ← unchanged, used as library
├── iletcomfort_web.py        ← new — Flask app
├── templates/                ← new — Jinja2 HTML
│   ├── base.html             ← shared layout, inline CSS
│   ├── login.html            ← password form
│   ├── appliances.html       ← list view at GET /
│   ├── device.html           ← per-device dashboard
│   └── device_raw.html       ← raw-hex emphasis view
├── tests/
│   └── test_iletcomfort_web.py   ← new — pytest with Flask test_client
├── requirements.txt          ← new — flask added alongside requests/cryptography
└── README.md                 ← updated: short web-UI section appended
```

The Flask app instantiates exactly one `ILetComfortClient` at startup, calls
`load_token()` to pick up any cached token, and reuses it across requests. No
per-request client construction.

## Configuration

All configuration via environment variables. If a variable is unset, the app
also tries `~/.iletcomfort_web.env` (one `KEY=value` per line, gitignored,
loaded once at startup, no shell escaping).

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ILETCOMFORT_ACCOUNT` | yes | — | iLetComfort account email |
| `ILETCOMFORT_PASSWORD` | yes | — | iLetComfort account password |
| `WEBUI_PASSWORD` | yes | — | Password the browser must enter |
| `ILETCOMFORT_API_BASE` | no | `https://us.dollin.net` | EU users set to `https://eu.dollin.net` |
| `WEBUI_HOST` | no | `127.0.0.1` | Bind interface |
| `WEBUI_PORT` | no | `8000` | Bind port |
| `WEBUI_SECRET_KEY` | no | auto-generated | Flask session-cookie signing key |

Behaviour on startup:

- If any required variable is missing, print which keys are missing and
  `sys.exit(1)`. Do not start a partial server.
- If `WEBUI_SECRET_KEY` is unset, generate a random one with
  `secrets.token_hex(32)`, persist it to `~/.iletcomfort_web_secret` (chmod
  600), print a one-time warning suggesting the operator set it explicitly,
  and proceed.
- Add `~/.iletcomfort_web.env` and `~/.iletcomfort_web_secret` to the project
  `.gitignore` documentation in the README — the files live in `$HOME`, not
  the repo, so no actual gitignore change is needed. Mentioned for clarity.

## Authentication and sessions

- `GET /login` — single-input form: a password field and submit button. No
  username field (single-user).
- `POST /login` — compares the submitted password against `WEBUI_PASSWORD`
  using `hmac.compare_digest`. On match: set a Flask signed-session cookie
  `{"authed": True}`, redirect to `/`. On mismatch: re-render the form with a
  generic "Wrong password" error.
- `POST /logout` — clear the session, redirect to `/login`.
- All other routes use a `@require_auth` decorator that redirects to `/login`
  if `session.get("authed")` is not true.
- Session cookie configured `HttpOnly`, `SameSite=Lax`, and `Secure=False`
  (we don't assume TLS). Lifetime: browser session.

No CSRF token on `POST /logout` — explicit YAGNI for a single-user dashboard.

## Routes and pages

All read-only. All require auth.

### `GET /`

Calls `client.list_appliances()`. Renders a list of appliance rows: name,
appliance code, type, online status, owner. Each row links to
`/device/<code>`. If the list contains exactly one appliance, redirect
straight to its device page.

### `GET /device/<code>`

The full read view for one device. Renders four cards in a single column:

1. **Metadata.** From `client.get_appliance_info(code)`. Fields: name, SN,
   SN8, model number, online state, owner.
2. **Status.** From `client.query_status(code)`. Renders all fields from the
   `ITSStatus` dataclass that the patched `print_its_status` already shows,
   dispatching on `status.firmware_variant`:
   - `its_short` → live operations bitfield (Heat / DHW / TBH / Fast DHW),
     Zone1 mode + setpoint + room-temp (probable), DHW setpoint, water
     outlet temp (probable), raw bitfield with unidentified-bits warning,
     and the raw body hex.
   - `spec` → the full spec-frame field set.
3. **Sensors.** From `client.query_sensors(code)`. Same dispatch:
   - `its_short` → "decode unavailable for this firmware" notice + raw body
     hex.
   - `spec` → full sensor decode.
4. **Last updated** timestamp (server-side `datetime.now()` at the time of
   the API calls), shown at the top of the page.

A **Refresh** button at the top of the page is a plain link to the same URL.
One click = one round of API calls.

### `GET /device/<code>/raw`

Same data as `/device/<code>` but emphasises the raw frame hex for both
status and sensors — useful for the ongoing reverse-engineering work the
operator is doing. Page header links between the two views.

## Data flow per request

Every authed page that needs device data follows the same pattern:

1. Call the relevant `ILetComfortClient` method.
2. If it raises an "auth invalid" error (cloud `code: 14005`), call
   `client.login(account, password)` once, save the new token, retry the
   call.
3. If the retry succeeds, render normally.
4. If the retry fails, or the original error was something else (network,
   1214 transient device error, anything else), render an inline error card
   for that specific section. Other cards on the page that succeeded still
   render. The server never returns 5xx for an upstream failure.

The auto-relogin step happens at most once per request to avoid login loops.

## Error handling matrix

| Failure | Response |
|---|---|
| Required env var missing at startup | Print missing keys, exit 1 |
| Wrong password on `/login` | Re-render `/login` with generic error |
| Session cookie missing/expired/tampered | Redirect to `/login` |
| API returns `code 14005` (token invalid) | Auto re-login once, retry. If still fails, inline error card. |
| API returns `code 1214` (device transient) | Inline "Device temporarily unreachable" card with a hint to retry. No automatic retry. |
| `requests` exception (network) | Inline error card with the message |
| Unknown appliance code in URL | 404 page |
| Login itself fails (bad creds) | Inline error card on whatever page triggered it. The operator must fix `ILETCOMFORT_PASSWORD` and restart. |

## Visual treatment

- Pure HTML with a small inline `<style>` block in `base.html`. No external
  CSS, no JS, no fonts.
- Single-column card layout; cards are `<section>` elements with a thin
  border and a header.
- Monospace font for hex bodies and raw bitfield values.
- One colour accent for the Refresh button. No images.

## Testing

- `tests/test_iletcomfort_web.py` using pytest and Flask's `app.test_client()`.
- The `ILetComfortClient` is replaced with a mock fixture so tests do not hit
  the API. Mocks return canned `ITSStatus` and `ITSSensors` dataclasses,
  including both `firmware_variant="spec"` and `firmware_variant="its_short"`.
- Cases covered:
  - `GET /` while unauthed → redirect to `/login`.
  - `POST /login` with wrong password → 200 with error message rendered.
  - `POST /login` with correct password → 302 to `/`, cookie set.
  - `GET /` while authed with one appliance → redirect to `/device/<code>`.
  - `GET /` while authed with two appliances → renders both rows.
  - `GET /device/<code>` with `its_short` status → renders short-variant
    fields (live operations, Zone1, DHW, water outlet) and does not render
    the spec-only fields.
  - `GET /device/<code>` when the mocked client raises an auth error → mock
    `client.login` is called once and the call is retried.
- Manual smoke test against the live unit is the final acceptance check, in
  the style of the rest of this project.

No live-API integration tests in CI: would burn the operator's iOS-app
session on every run and require real credentials in CI.

## Security notes (intentional limits)

- Default bind `127.0.0.1:8000`. Operator can override to `0.0.0.0` for LAN
  access; doing so without putting a TLS-terminating reverse proxy in front
  leaks `WEBUI_PASSWORD` over the network in plaintext on each login.
- No rate-limiting or brute-force protection on `/login`. Mitigation: single
  user, expected to bind to localhost.
- No CSRF protection on `POST /logout`. Single-user, low value. Skipping
  deliberately.
- The session cookie is signed but not encrypted (Flask default). Contains
  only `{"authed": True}` — no sensitive data.
- iLetComfort credentials live only in env vars / `~/.iletcomfort_web.env`,
  never in the session cookie, never in URLs, never logged.

## Out of scope (future work)

- Control commands (SET frames). Blocked at the firmware-variant detection
  level until SET layout is verified for the short-frame variant.
- MQTT push subscription for live updates.
- Multiple user accounts.
- TLS termination inside the app.
- Historical charts / metric storage.
