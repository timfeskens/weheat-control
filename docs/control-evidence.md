# Control contracts and evidence

Artifact: Weheat Android app 4.5.14 (248), SHA-256
`434de5040f0fc1848fe3c8d2b591a1739ca31a8162659819b225a0f89bacf7a6`.
REA package evidence:
`ev_073a02bcbb9b55fe86561f3aa83acc1fa70c55754f80a6df7f4d331c0f563f52`.
The function references below are **supplemental Hermes disassembly**, not
runtime observations or REA JavaScript evidence. Saved analysis:
`weheat-rea-BfINXk/functions/<id>.hasm` in the original analysis workspace.

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
