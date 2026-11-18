# Weheat Control

Unofficial Home Assistant integration for Weheat heat pumps, available as a
HACS custom repository. Control hot-water boost, quiet presets and schedules,
and supported temperature settings directly from Home Assistant.

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=timfeskens&repository=weheat-control&category=integration)
[![Checks](https://github.com/timfeskens/weheat-control/actions/workflows/check.yml/badge.svg)](https://github.com/timfeskens/weheat-control/actions/workflows/check.yml)

> **Experimental:** installation and cloud reads have been verified in a running
> Home Assistant instance; not every control has been tested against a real device.
> Do not rely on this integration for unattended control. This project is not
> affiliated with or supported by Weheat.

## Use alongside the official Weheat integration

Keep Home Assistant's built-in [Weheat integration](https://www.home-assistant.io/integrations/weheat/)
for measured temperatures, power, COP, energy totals, compressor/pump states,
and cooling diagnostics. It uses Weheat's official `weheat` Python client and
`/third_party` API. This custom integration complements it with controls the
built-in integration does not provide; it does not replace it or duplicate its
telemetry polling.

Set up each integration separately under **Settings → Devices & services**.
Their credentials, tokens, polling, and device entries remain independent;
do not copy refresh tokens between them. No dependency on the built-in
integration's private runtime objects is required.

The custom readback/diagnostic sensors are retained to confirm requested
settings and track commands, not to replace official telemetry. In particular,
the configured fixed hot-water target is not necessarily the current active
target reported by the official integration. Existing entity IDs are preserved.

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
YAML configuration; the only action is the optional hot-water schedule editor.

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
| Hot-water mode | The app's three choices: Fixed temperature, Manual schedule, Weheat Intelligence. Follows readback; Manual schedule requires at least one period |
| Hot-water schedule per weekday | Seven readback sensors, e.g. `11:00–16:00 50 °C`, with the periods as attributes |
| Action `weheat_control.set_dhw_schedule` | Replace the periods of one or more weekdays (quarter hours, 40–60 °C in 0.5 °C steps); always submits the complete week; does not change the hot-water mode |
| Hot-water Intelligence comfort | Normal or Eco, as in the app (safety temperature 40 or 10 °C plus the app's comfort code); available only in Intelligence mode |
| Heating-curve points | 18–60 °C, in 1 °C steps, for Heating curve and On/off thermostat control methods |
| Hot-water Intelligence plan | Read-only. *Planned heating*, *Planned legionella* or *Standby*; its history is a coloured timeline |
| Hot-water Intelligence plan yesterday / today / tomorrow | Planned heating moments per day, e.g. `22:00–00:00, 03:15–05:15`; *Not yet published* until Weheat publishes tomorrow's plan (observed around 16:00) |
| Hot-water Intelligence next planned heating | Start of the next planned moment |
| Hot-water target readback | Requested target temperature, not measured water temperature |
| Hot-water boost end | Reported boost end time |
| Heating control method and curve | Current method and curve values |
| Settings request status | Tracks command progress, failures, and readback verification |
| Acknowledge expired uncertain boost | Explicit local recovery for an expired boost with no request ID; never resends it |

Entities are enabled by default, but availability depends on the settings and
capabilities returned by your installation. Heating-curve entities are added
when a supported control method is detected.

### Manual hot-water schedule

The Weheat app's manual hot-water schedule stores 96 quarter-hour slots per
weekday. Each slot holds a target temperature, or none; outside the scheduled
periods only the (installer) safety temperature applies. Edit it with the
**Set hot-water schedule** action, for example:

```yaml
action: weheat_control.set_dhw_schedule
data:
  config_entry_id: <your Weheat Control entry>
  days: [monday, tuesday, wednesday, thursday, friday]
  periods:
    - start: "06:00"
      end: "07:30"
      temperature: 52.5
    - start: "17:00"
      end: "00:00"   # 00:00 as end means midnight
      temperature: 50
```

The selected weekdays get exactly these periods. Every write submits the
complete week: the other weekdays are re-sent unchanged from a fresh readback,
and the write is refused if that readback is incomplete. An empty `periods`
list clears the selected weekdays. Overlapping or non-quarter-hour periods and
temperatures outside 40–60 °C are rejected, never rounded. Times are
device-local. Editing the schedule does not switch to it: choose **Manual
schedule** in the **Hot-water mode** select. That sends mode 2 together with
the complete current schedule, and is refused when the schedule has no periods.
**Weheat Intelligence** sends mode 3 with the comfort property, like the
app's Intelligence toggle; **Fixed temperature** sends only mode 0. Selecting the
mode that is already active sends nothing. All schedule and mode writes use the same request
journal, no-retry protection and exact readback verification as other controls.

### Hot-water Intelligence plan

In Intelligence mode the integration reads the plan the app shows under
*Insights*, every 15 minutes, for yesterday through tomorrow
(`GET /installations/{id}/dhw-schedules`). It is read-only and never changes a
setting. Each planned moment lasts from its start for the plan's hold time
(currently two hours). The plan sensor switches exactly at those boundaries, so
Home Assistant's history shows the plan as coloured blocks and keeps it for as
long as your recorder keeps history. History starts when the integration is
installed; earlier days cannot be filled in.

To spot extra, unplanned heating, put the plan next to the actual heat-pump
state from the official Weheat integration in a History card:

```yaml
type: history-graph
hours_to_show: 48
entities:
  - entity: sensor.weheat_<installation>_hot_water_intelligence_plan
  - entity: sensor.<your official weheat heat pump state sensor>
```

The plan's `reported_target_temperature` attribute is the `targetTemperatureC`
value the plan reports. Its meaning is not established and it can differ from
the hot-water temperature you see elsewhere, so it is not a sensor of its own.
Weheat publishes the next day's plan in the afternoon (observed around 16:00);
until then the tomorrow sensor shows *Not yet published*, which is different from
*No planned heating*. Outside Intelligence mode the plan sensors are unavailable.

### Operating-mode restrictions

- **Weheat Intelligence or scheduled hot water:** fixed hot-water target control
  is unavailable. Readback remains visible.
- **Smart OpenTherm and other unsupported heating methods:** heating-curve
  editing is unavailable.
- **Quiet schedule:** editing requires all weekday hour/minute fields to be
  present and valid. Times are not converted between timezones.
- **Night presets:** “Schedule disabled” and “Off during scheduled hours” are
  different settings. The model-specific “Minimum” preset is not exposed.

The integration never automatically changes operating modes to make another control available.
The explicit **Hot-water mode** select is the exception you control: Weheat
Intelligence selects DHW mode 3, Fixed temperature selects mode 0 (not the
previous manual schedule). It never sends a target-temperature or space-heating
change. After fixed mode is confirmed, the hot-water temperature control becomes
available. Choosing Intelligence preserves a valid reported DHW comfort code,
otherwise uses the official app's Balanced default. Unknown DHW availability or
mode makes the select unavailable. Its state follows readback, not the submitted
command; the same durable request gate and no-retry protection apply.

The former **Hot-water Weheat Intelligence** switch has been removed because the
Hot-water mode select covers it. Automations that used the switch should call
`select.select_option` on the Hot-water mode entity with `Weheat Intelligence`
or `Fixed temperature` instead. Home Assistant keeps the removed switch in the
entity registry as unavailable; delete it there.
The **Hot-water Intelligence comfort** select mirrors the app's Normaal/Zuinig
choice. The app stores it in two places: the safety temperature in the settings
(Normal 40 °C, Eco 10 °C) and a comfort code in the installation overview
(Normal 6, Eco 5). The select sends both in one request, never a mode or target
change, and never enables Intelligence automatically. Its state follows the
comfort code, like the app's label, falling back to the temperature only when no
code is reported; unknown values are shown as unknown. The overview is read only
in Intelligence mode. Success is only marked verified when both halves read back.
Cooling controls, heating-mode changes, whole-system power switching, room
thermostats, and installer/protection settings are not supported.
The official app's inspected Intelligence toggle writes hot-water mode only.
A whole-system Intelligence switch is not exposed: its write/restore contract
has not been verified. Heating control method and DHW Intelligence are distinct.
Although app cooling payloads can be traced, enabling cooling requires separate
system-suitability and condensation-risk acknowledgements in the app. An
unguarded cooling switch or cooling-curve editor is intentionally not exposed.

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
| HTTP 505/version error | The cloud rejected the API protocol version. The integration uses the app's `app_consumer_v4.0.1` protocol header, not its `4.5.14` release number, and never retries commands automatically. |

Request intent is saved before sending a command. A journal storage failure
blocks submission. An unresolved request has no automatic reset or override.

**Expired ID-less boost recovery:** after checking the official app, you may
press **Acknowledge expired uncertain boost** in the device's diagnostic section.
This is an explicit acknowledgement of an unknown outcome, not verified success
or upstream cancellation. It is limited to a boost-only intent of at most one
hour, with its deadline expired by at least ten minutes, no request ID, the
same control board, a readback showing no active boost, and an explicitly empty
cloud request listing. Missing data, cloud/storage failures, active or listed
requests, and other settings remain blocked. The button sends no settings
command. The original intent/outcome remains in the journal and is archived
there when a later command is submitted; the status is
`expired_outcome_unknown`. An empty listing cannot establish whether the original
request was ever accepted. Do not use this as a general reset mechanism.

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
