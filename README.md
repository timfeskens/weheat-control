# Weheat Control for Home Assistant

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=timfeskens&repository=weheat-control&category=integration)

## Home Assistant / HACS integration (experimental)

The intended product is now a **native Python custom integration** for Home
Assistant 2026.11+, in
`custom_components/weheat_control/`. Home Assistant talks directly to the Weheat
cloud using its managed async HTTP session; **the TypeScript server is not a
runtime dependency**. The original TypeScript API below remains a development
and troubleshooting tool.

For local testing, copy only `custom_components/weheat_control/` into your Home
Assistant `config/custom_components/` directory, restart Home Assistant, and add
**Weheat Control** under Settings → Devices & services → Add integration.
The UI provides a browser-login link, validates your pasted mobile callback
with PKCE/state, discovers installations, and selects the sole eligible device
or prompts when there are several. Discovery can be retried without exchanging
the same one-use authorization code again.

Sign in separately in Home Assistant. **Do not copy `.env`, `.state/`, or tokens
from the Node.js tool**: sharing rotating refresh tokens across processes is
unsafe. Tokens are saved in Home Assistant's config entry, not `.env`; protect
your HA storage and backups. No account password is collected by this integration.
Supported controls work immediately after login; there is no write-enable toggle.
Using a control sends a real device-settings request. Login and polling alone
do not change settings. There are no automatic retries or optimistic state changes.
An unresolved settings request remains blocked across HA restarts. Check the
official app if a command's outcome is uncertain; do not delete HA storage.

### Included Home Assistant entities

| Entity | Behavior |
| --- | --- |
| Boost buttons | Request 30/60 minutes of hot-water boost, or stop boost |
| Day quiet preset | Normal, Background chatter, Light rain, Library, Whisper |
| Scheduled-night preset | Separate night enum, including schedule disabled and off during scheduled hours |
| Per-day quiet start/stop times | 14 entities; require all schedule fields; device-local time |
| Hot-water target number | 40–60 °C, only with DHW present and fixed-setpoint mode |
| Heating-curve point numbers | 18–60 °C, methods 1/2 only; preserve the entire curve and control method |
| Readback/diagnostic sensors | Hot-water target, boost end, heating method/curve, settings-request status |

In **Smart OpenTherm (4)** or DHW **Weheat Intelligence (3)**, the corresponding
heating-curve or fixed hot-water target controls are unavailable. The integration
does not change operating modes for you. Device writes are not yet live-verified.
There is no invented whole-system on/off switch or generic room thermostat.
Cooling writes and installer/protection settings remain unsupported.
See [control evidence](docs/control-evidence.md) for recovered mappings and limits.

### Install with HACS

Use the **Add to HACS** button above, or add
`https://github.com/timfeskens/weheat-control` as a HACS custom repository of
type **Integration**. Download **Weheat Control**, restart Home Assistant, then
add it under Settings → Devices & services. The button requires HACS to already
be installed and your Home Assistant URL to be configured in My Home Assistant.

This is a custom repository, not an entry in the default HACS catalog. It has
not yet been tested in a running Home Assistant instance. It is unofficial, not
affiliated with Weheat, and not yet ready for unattended control.

### Python development checks

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/ruff check custom_components tests
.venv/bin/python -m pytest
```

Home Assistant tests use the real HA libraries with mocked cloud responses.
They do not prove device compatibility or constitute a live HA deployment.

---

## TypeScript development API

Private local HTTP API for Weheat's **consumer cloud API**, based on offline
inspection of Android app 4.5.14. Strict TypeScript on Node.js 22+, using `ky`
for upstream and local HTTP requests and Zod at external JSON/configuration
boundaries. No explicit `any`/`unknown` types or unchecked type assertions.

**Device writes are not live-verified.** Login, installation discovery, and a
boost preview have worked against the live service. No device settings have
been changed during development.
This TypeScript API remains boost-only. The additional controls above are
implemented in the native Home Assistant integration, not duplicated into a
second HTTP bridge. This is not LAN control.

## 1. Local setup

```sh
cd weheat-control
npm ci
npm run check
npm run login
```

Login creates a mode-0600 `.env` with a random local bearer key if it is missing.
The optional `npm run setup` command only creates this file, without signing in;
it will not overwrite an existing file. Tests use mocks, not upstream requests.
`.env` and `.state/` are gitignored. Tokens are stored unencrypted with owner-only
permissions in `.state/tokens.json`; protect this computer and its backups.

## 2. Interactive login (read-only)

Run this yourself in an interactive terminal, with the service stopped:

```sh
npm run login
```

The CLI opens your default browser on macOS/Linux and prints the authorization
URL as a fallback. If it cannot open the browser, open that URL manually. Use your normal
Weheat account; **do not share your password, tokens, or callback URL in chat**.
The CLI uses authorization code + PKCE and verifies OAuth state before exchange.

The redirect is `weheat://auth`, recovered from the app scheme and
`makeRedirectUri({path: 'auth'})`; this handoff has worked in a live login.
A localhost web callback cannot simply be substituted
for a registered mobile callback. This CLI does not install a protocol handler.
At the browser's "open app" prompt, copy the callback URL without launching the
app, then paste it into the CLI's visible prompt. The callback may remain in
terminal scrollback; do not share screenshots or recordings containing it.
Some browsers do not expose the link. If the login reports an invalid redirect or you cannot copy the callback,
stop and report that symptom (not the URL/code). Do not weaken state/PKCE checks.
Login flow will need adaptation if this handoff is unavailable.

After you paste the callback, login automatically:

1. Saves tokens with owner-only permissions.
2. Fetches installations and filters for an accessible control board.
3. Selects the sole eligible installation, or asks for a numbered choice when
   several exist (`q` cancels).
4. Saves `WEHEAT_INSTALLATION_ID` in `.env`,
   preserving your API key, comments, and unrelated values. The saved file is
   mode 0600. Stop the server first so it cannot retain an older configuration.
5. Prints the start and preview commands. No device settings are changed.

You no longer need to run `devices` or manually copy the installation ID.
If discovery or selection fails, tokens remain saved. Finish setup without
another browser login (also useful if you have already signed in):

```sh
npm run login -- --resume
```

An expired/revoked refresh token may still require a fresh `npm run login`.
`npm run devices` remains a read-only troubleshooting command. Run login with
`--resume` to choose another installation. Duplicate
installation keys or multiline control values in `.env` are rejected
without overwriting the file; resolve them before retrying.

Installation, heat-pump, and control-board IDs are distinct. The service
re-resolves the selected board from installations accessible to your account
before writes. Per-device boost capability and write/status response envelopes
remain unverified; unrecognized schemas fail closed instead of guessing a
device or a request ID.

## 3. Start and preview

Start the API. Preview is read-only; `on` and `off` send real commands:

```sh
npm start
```

In another terminal:

```sh
npm run boost -- preview 30
```

Preview reads the installation but **does not send a settings change**. It prints
the control-board ID and the proposed request, for example:

```json
{
  "settings": { "DhwBoostEndTime": "<Unix seconds: now + 30 minutes>" },
  "updateImmediately": false
}
```

The production route recovered from the app is:
`PATCH https://api.weheat.nl/api/v1/control-boards/{boardId}/settings`.
The flag `updateImmediately:false` mirrors the app; its backend scheduling
semantics are not established. There is no generic power or compressor command.

## 4. Explicit live boost test

Controls are ready after login and starting the API; no environment toggle is
required. Only run these commands when you intend to change your installation.
The prototype does not yet verify DHW hardware capability from a live schema;
confirm that hot-water boost exists and works in your normal Weheat app first.

```sh
# Real command: request 30 minutes of hot-water boost.
npm run boost -- on 30

# Real command: request stopping hot-water boost.
npm run boost -- off
```

The 1–120 minute duration limit is a **conservative local design choice**, not a
recovered Weheat safety limit. "On" always requires a duration; it is not an
indefinite mode. "Off" sends the current Unix timestamp as a string, matching
the app. These commands change only `DhwBoostEndTime`, not target temperatures,
legionella schedules, installer settings, or protection parameters.

Both commands track the exact returned request ID and poll every 2 seconds for
up to 2 minutes. They read settings back after upstream success. A mismatch or
unrecognized readback is reported, not silently treated as verified success.
An HTTP 202 only means a local operation was recorded, **not that boost changed**.
Upstream device-disconnected and wrong-state errors are preserved.

If a write times out, fails ambiguously, or returns no recognized request ID,
the operation remains `unknown` and further writes are blocked, including after
restarting the service. **Do not repeat it blindly.** Check the Weheat app,
upstream settings/history, and the operation before reconciling local state.
There is intentionally no automatic override/reset endpoint in this first
version. Stop the service before any manual state repair. Manual repair should
record the verified outcome rather than deleting the history. Boost-off is also
blocked while an earlier request is unresolved; use the official app if needed.

## HTTP API

Bound to `127.0.0.1:8787` only. All routes except `/health` require:

```text
Authorization: Bearer <WEHEAT_LOCAL_API_KEY from .env>
```

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/health` | Local service health, **not** heat-pump connectivity |
| GET | `/devices` | Account installations and device IDs |
| GET | `/settings` | Typed projection of the selected installation's boost settings |
| POST | `/boost` | Boost on/off or preview |
| GET | `/operations/{id}` | Refresh operation status/readback |

Example request bodies:

```json
{"enabled":true,"durationMinutes":30,"dryRun":true}
```

```json
{"enabled":true,"durationMinutes":30}
```

```json
{"enabled":false}
```

No arbitrary settings passthrough, remote bind, CORS, or unauthenticated writes.
Requests with browser `Origin` headers or foreign hostnames are rejected. Do not
publish this prototype directly on the internet. Use the CLI instead of putting
API keys in shell command history.

## State and shutdown

Only one login/devices/server process may own `.state/process.lock` at a time,
so rotating refresh tokens cannot race across those processes. Stop `npm start`
with Ctrl-C before login or `npm run devices`. A crashed process may leave the
lock behind: inspect its recorded PID and verify it is no longer running before
removing **only that lock file**. Never delete token/operation state as a shortcut.

## Evidence and limitations

Source: supplied `com.weheat.dashboard.apk`, version 4.5.14 (248).
APK SHA-256:
`434de5040f0fc1848fe3c8d2b591a1739ca31a8162659819b225a0f89bacf7a6`.
REA/JADX package evidence:
`ev_073a02bcbb9b55fe86561f3aa83acc1fa70c55754f80a6df7f4d331c0f563f52`.
Supplemental P1sec Hermes disassembly, not REA JavaScript evidence, established:

- Function 21094: boost end time = `String(now.add(minutes, 'minutes').unix())`.
- Function 21049: stop boost = `String(now.unix())`.
- Functions 21053/21070: nested `settings`, PATCH board settings, immediate=false.
- Functions 21060/21075/21076: exact request polling route and numeric statuses.
- Functions 33057/33062/33282: browser login, PKCE, token exchange.
- Functions 34511/34524: refresh tokens and bearer authorization.

Read-only public OIDC discovery was checked after the first login reported
`Unexpected OIDC issuer`. The bundled legacy discovery URL under `/auth/realms/Weheat`
now declares `https://auth.weheat.nl/realms/Weheat` as its issuer. The canonical
discovery URL without `/auth` was checked too and advertises authorization and
token endpoints under `/realms/Weheat/protocol/openid-connect/`. The client uses
that exact canonical issuer and still rejects other origins, realms, and HTTP.
This public metadata check does not sign in or change device settings.

Live read-only device discovery was subsequently checked using the saved login:
`GET /installations/fully-expanded` returned HTTP 505 with `x-version: 4.5.14`,
but HTTP 200 when that header was omitted. The app explicitly excludes this route
and `/users/me` from version headers; the client now mirrors those exclusions.
Other routes still send the recovered version header. Their current version
compatibility remains unverified; a 505 reports a version error without retries.

A later read-only check also found installation settings return 505 with the
old version header and 200 without it. Both clients now omit the header for
that exact GET route too. The boost-end read value is an ISO-8601 timestamp;
the TypeScript boundary normalizes it to Unix seconds and native HA decodes it
to an aware datetime. Writes still use the app's string Unix-seconds format.

The supplied app may be outdated relative to the current backend. Backend
write authorization, write/status response envelopes, current P60 capabilities,
and physical application of boost still require live validation. Tests use mock
upstream responses; they are not evidence that the real backend accepts a write.

## Remaining work

1. Install and verify the native integration in a test Home Assistant instance.
2. Verify write-route/version compatibility, exact returned request IDs/status,
   readback, and physical response with explicitly authorized device tests.
3. Validate HACS installation and publish a release before unattended use.
4. Broaden central-heating controls only after verifying thermostat interactions.
   Never assume a room-setpoint or generic on/off command.
