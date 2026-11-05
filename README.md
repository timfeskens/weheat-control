# Weheat Control

Unofficial Home Assistant integration for Weheat heat pumps, available as a
HACS custom repository. Control hot-water boost, quiet presets and schedules,
and supported temperature settings directly from Home Assistant.

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=timfeskens&repository=weheat-control&category=integration)
[![Checks](https://github.com/timfeskens/weheat-control/actions/workflows/check.yml/badge.svg)](https://github.com/timfeskens/weheat-control/actions/workflows/check.yml)

> **Experimental:** cloud reads have been verified, but installation in a running
> Home Assistant instance and real device commands have not yet been verified.
> Do not rely on this integration for unattended control. This project is not
> affiliated with or supported by Weheat.

## Requirements

- **Home Assistant 2026.10 or newer**.
- A Weheat account with access to an installation and its heat-pump control board.
- Internet access to the Weheat cloud. This is not a local/LAN integration.
- HACS, if using the recommended installation method.

## Installation

### HACS (recommended)

1. Click **Add to HACS** above and open your Home Assistant instance.
2. Download **Weheat Control** in HACS.
3. Restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration** and search for
   **Weheat Control**.

If the button does not work, add `https://github.com/timfeskens/weheat-control`
under **HACS → Custom repositories**, selecting **Integration** as the category.
Then download the integration and restart Home Assistant. This repository is
not included in the default HACS catalog.

### Manual installation

1. Download this repository.
2. Copy `custom_components/weheat_control` into your Home Assistant configuration
   directory, so that `config/custom_components/weheat_control/manifest.json` exists.
3. Restart Home Assistant, then add **Weheat Control** under
   **Settings → Devices & services → Add integration**.

## Setup

1. Open the **Weheat login** link shown in the setup dialog.
2. Sign in with your Weheat account in the browser.
3. At the browser's open-app prompt, copy the **complete `weheat://auth` callback
   URL** without opening the app, and paste it into the setup dialog.
4. Select your installation if prompted. A single eligible installation is
   selected automatically.

Some browsers do not expose the callback link. If you cannot copy it, try
another browser. If login fails, use the new link shown by the setup dialog;
callback URLs are one-use and must belong to the current login attempt.

Setup and polling do not change device settings. **Supported controls are ready
immediately after login; using one sends a real settings request.** There is no
YAML configuration or separate service to run.

The integration does not collect your password. Login tokens are stored in Home
Assistant's config entry; protect your configuration and backups. Do not share
tokens or callback URLs in issues, screenshots, or logs. When login expires,
complete the reauthentication flow offered by Home Assistant.

## Supported entities

| Entity | Description |
| --- | --- |
| Hot-water boost buttons | Request 30 or 60 minutes of boost, or stop boost |
| Day quiet preset | Normal, Background chatter, Light rain, Library, Whisper |
| Scheduled-night quiet preset | Separate night presets, including **Schedule disabled** and **Off during scheduled hours** |
| Quiet schedule times | Start and stop for each weekday: 14 entities using device-local time |
| Hot-water target temperature | 40–60 °C, in 1 °C steps, when DHW is available and uses fixed-setpoint mode |
| Heating-curve points | 18–60 °C, in 1 °C steps, for Heating curve and On/off thermostat control methods |
| Hot-water target readback | Requested target temperature, not measured water temperature |
| Hot-water boost end | Reported boost end time |
| Heating control method and curve | Current method and curve values |
| Settings request status | Tracks command progress, failures, and readback verification |

Entities are enabled by default, but availability depends on the settings and
capabilities returned by your installation. Heating-curve entities are added
when a supported control method is detected.

### Operating-mode restrictions

- **Weheat Intelligence or scheduled hot water:** fixed hot-water target control
  is unavailable. Readback remains visible.
- **Smart OpenTherm and other unsupported heating methods:** heating-curve
  editing is unavailable.
- **Quiet schedule:** editing requires all weekday hour/minute fields to be
  present and valid. Times are not converted between timezones.
- **Night presets:** “Schedule disabled” and “Off during scheduled hours” are
  different settings. The model-specific “Minimum” preset is not exposed.

The integration never changes operating modes to make another control available.
Cooling controls, heating-mode changes, whole-system power switching, room
thermostats, and installer/protection settings are not supported.

## Command status and troubleshooting

Settings are polled every 30 seconds. Commands are tracked by request ID and
checked against settings readback; submitting a request does not prove that the
heat pump applied it. There are no automatic command retries or optimistic
state updates.

| Symptom | What to check |
| --- | --- |
| A temperature control is unavailable | Check the operating-mode restrictions above and your current mode in the official app. |
| Login fails | Start with the new login link and paste its complete callback URL. Never share that URL. |
| Discovery fails during setup | Retry the installation step; the setup flow retains the exchanged login. |
| A command remains unresolved or has an uncertain outcome | Check the official app and **Settings request status**. Further commands are blocked, even after a restart, to prevent accidental repeats. Do not delete storage or retry blindly. Use the official app if an urgent change is needed. |
| Status shows a mismatch | The reported settings differ from the request; this is not verified success. |
| HTTP 505/version error | The cloud rejected the recovered app version. Current command/status route compatibility is not verified; the integration does not replay the command with a different version header. |

Request intent is saved before sending a command. A journal storage failure
blocks submission. An unresolved request has no automatic reset or override.

`submission_uncertain` means a submission was attempted but its outcome could
not be established, not that the sensor is missing data. Older unresolved
`unknown` records are displayed this way too, without releasing the write block.
New failures retain a safe `error_code` and, when available, `http_status` in
the status sensor and diagnostics. Raw responses and exception text are never
logged. Older requests cannot recover error details that were not recorded.

### Device-page layout

Home Assistant controls the device page's ordering and built-in sections; the
integration cannot insert a custom quiet-schedule section there. For exact
ordering, use a dashboard with the day and scheduled-night preset controls
together and the 14 schedule times in a separate section. Existing entity IDs
do not need to change.

For help, [open an issue](https://github.com/timfeskens/weheat-control/issues)
with your Home Assistant version, heat-pump model, operating mode, and sanitized
error details. Integration diagnostics omit tokens and device identifiers.

## Updates and removal

Install updates through HACS and restart Home Assistant. For manual installs,
replace the integration folder with the updated version and restart.

To uninstall, remove the integration under **Settings → Devices & services**,
then remove its files through HACS or delete `custom_components/weheat_control`.
Removing the integration does not undo settings already applied to the heat pump.

## Development

Use Python 3.14 and the pinned Home Assistant development dependencies:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/ruff check custom_components tests
.venv/bin/ruff format --check custom_components tests
.venv/bin/python -m pytest
```

Tests use Home Assistant libraries with mocked cloud responses and do not send
real device commands. CI tests Home Assistant **2026.10.0** and **2026.11.0**;
the development dependencies use the minimum supported version. These offline
checks do not replace testing setup and controls in a running instance.
API contracts, recovered app mappings, and known
limitations are documented in [control evidence](docs/control-evidence.md).
