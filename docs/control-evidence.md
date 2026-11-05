# Control contracts and evidence

Artifact: Weheat Android app 4.5.14 (248), SHA-256
`434de5040f0fc1848fe3c8d2b591a1739ca31a8162659819b225a0f89bacf7a6`.
REA package evidence:
`ev_073a02bcbb9b55fe86561f3aa83acc1fa70c55754f80a6df7f4d331c0f563f52`.
The function references below are **supplemental Hermes disassembly**, not
runtime observations or REA JavaScript evidence. Saved analysis:
`weheat-rea-BfINXk/functions/<id>.hasm` in the original analysis workspace.

## API version correction and expired-boost recovery

Supplemental function #21762 sets `appVersion` to `app_consumer_v4.0.1`;
its production branch changes API/auth/portal URLs but not that value.
#34499 passes it as the version-check configuration and #34516 sets `x-version`.
The APK release `4.5.14` is not the API protocol header. Read-only live probes
returned HTTP 505 with `4.5.14` and HTTP 200 with `app_consumer_v4.0.1` for both
installation settings and the board request listing. This does not establish
PATCH acceptance or individual-request status behavior; no mutation was probed.

#21065 lists `/control-boards/{id}/settings` with `page=1`. The observed complete
empty response is `metadata: {totalCount: 0, pageSize: 10, currentPage: 1,
totalPages: 0}, data: []`. Backend retention/queue guarantees are unknown, so this
cannot prove rejection or success of a historic ID-less submission. Recovery is
an explicit local acknowledgement limited to a no-longer-active, expired absolute
boost deadline; never an automatic terminal-status inference or command replay.

## Intelligence scope

Screen #32874 reads `hasDhwWeheatIntelligence`. Enable #32878 / #21587 sends
`DhwScheduleEnabled: 3` plus `DhwComfortPreference` in `properties` (Balanced=2
or selected comfort). Disable #32888 sends `DhwScheduleEnabled: 0`, selecting
fixed setpoint rather than restoring a previous mode. It does not write heating
`ControlMethod` or system control. #13192 separately maps
`installationSystem.systemControl === 1` to whole-system Intelligence; the heating
method enum #12741 separately defines SmartOpenTherm=4 and Intelligence=9.
No whole-system setter was established in the saved disassembly. Do not expose
a DHW switch under a whole-system name or assume changing `ControlMethod` is
equivalent. Backend side effects and whole-system restoration remain unknown.

The native **Hot-water Weheat Intelligence** switch implements this DHW-only
contract. It preserves a valid reported comfort preference (1/2/3) or uses
Balanced=2 when absent/invalid, following the app's default. Both settings and
properties are persisted before submission; successful status is verified
against their readback. Missing comfort readback leaves success unverified.
Off sends only mode 0, never a target-temperature or heating-control write.

## Implemented controls

| Control | Read → write | Recovered app behavior / source |
| --- | --- | --- |
| DHW boost | `dhwBoostEndTime` → `DhwBoostEndTime` | String Unix seconds for writes; #21094 (on), #21049 (off). ISO timestamp observed in live read. Buttons request 30/60 minutes or stop. |
| DHW target | `dhwTargetTemperature` → `DhwTargetTemperature` | 40–60 °C, integer steps. Available only with DHW present and mode `dhwScheduleEnabled=0` (fixed). Never switch out of Intelligence/schedule automatically. |
| Day quiet | `performanceModePreset` → `PerformanceModePreset` | #32805:23–43, #32837:22–59, #32827:20–28. Values 0 Normal, 1 Background chatter, 2 Light rain, 3 Library, 4 Whisper. |
| Scheduled night quiet | `silentHoursScheduledPreset` → `SilentHoursScheduledPreset` | #32807:23–55, #32854:23–81, #32819:5–21, #32820:142–147. Values 0 schedule disabled, 1 Normal, 2 Background chatter, 3 Light rain, 4 Library, 5 off during scheduled hours, 6 Whisper, 7 Minimum. Value 7 is model-restricted and not exposed without verified model identity. |
| Quiet schedule | `silentHours/Minutes{Weekday}{Start/Stop}` → same names capitalized | Require all 28 current fields. Edit one hour/minute pair, preserving other weekdays. Device-local time semantics; no inferred timezone conversion. Entities enabled by default. |
| Heating curve | `heatingCurveX/Y`, `controlMethod` → full `HeatingCurveY` plus unchanged `ControlMethod` | #32192:5–15, #32190:179–214. Water values 18–60 °C. Only methods 1/2; preserve full point order/array. Eligible number entities enabled by default. Re-read before modifying. |

Bounds are app UI limits, not verified backend or manufacturer safety limits.
Controls are ready after login. All writes require fresh state validation, an accessible
matching board, and durable serialized operation tracking. No generic settings
passthrough is exposed. Success-with-mismatch (5) is not clean success.

## Heating method enum

Source #12741:23–63 (not the commissioning-step enum):
0 None, 1 HeatingCurve, 2 OnOffThermostat, 3 BaseOpenTherm,
4 SmartOpenTherm, 5 CloudThermostat, 6 ManualPower, 7 AutoInputSelect,
8 ManualSetpoint, 9 WeheatIntelligence.

Do not expose a mode-changing select, generic power switch, or room thermostat
based on these names alone. Smart OpenTherm curve edits, cooling writes,
installer parameters, legionella and protection controls remain unsupported.

## Live read-only observations (2026-11-05)

Authenticated installation settings read returns a bare camelCase object.
With `x-version: 4.5.14` it returns 505; without it, 200. The integration
omits the header **only for explicitly verified reads**. Write and
status route version compatibility remains unverified. Never automatically
replay a mutation with another header.

P60 read inspection confirmed DHW availability, numeric target/preset values,
Intelligence DHW scheduling and Smart OpenTherm control. Heating-curve arrays
and all 28 quiet schedule fields were present. Boost end was a timezone-aware
ISO timestamp, not an epoch string. No tokens or device identifiers are recorded
here. **No device settings write was performed.**

Quiet settings and timed boost are offered directly after login. Fixed DHW
target and heating-curve writes remain unavailable in incompatible operating
modes. Readback remains visible. These are requested
settings, not measured water temperature or proof of physical heat-pump action.
