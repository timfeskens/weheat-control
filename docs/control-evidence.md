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

## Official integration boundary

Home Assistant Core `2026.10.4` and `2026.11.0` use `weheat==2026.9.3`
and `https://api.weheat.nl/third_party` for telemetry. The built-in integration
loads sensor and binary-sensor platforms, not control platforms. Keep it for
temperatures, power/COP, energy, operating/pump states and cooling diagnostics.
Do not reimplement those through the consumer app API or share its refresh
tokens. The custom integration polls settings only for its controls and durable
command verification; configured-setting readbacks have different semantics
from active/measured telemetry and retain their existing IDs.

Sources: Home Assistant Core `homeassistant/components/weheat/{manifest,
__init__,coordinator,sensor,binary_sensor,const}` at tags `2026.10.4` and
`2026.11.0`; manufacturer client `wefabricate/wh-python`.

## Additional control investigation

### DHW Intelligence comfort preference

Supplemental Hermes #13345 defines None=0, Safe=1, Balanced=2, Eco=3.
#31151 offers Eco/Balanced/Safe in the consumer UI. #31144 submits only
`properties: {DhwComfortPreference: <choice>}`; #21053 copies the absent
settings into an empty object before forwarding, so the board PATCH is
`settings: {}, properties: {...}, updateImmediately: false`.
#23702 reads `dhwComfortPreference` from installation settings; it is not a
measured water temperature. The new select conservatively requires DHW,
Intelligence mode 3 and a valid reported preference. Missing data never becomes
an invented Balanced state. Selection does not change mode or target.
Property-only intent is persisted before submission and requires exact integer
readback to become verified success. Static evidence does not establish current
backend acceptance or a particular installation's property support.

### Cooling: traced but deliberately not exposed

#12765 defines CoolingEnabled Unset=0, Off=1, Enabled=2; #12763 defines
CoolingControlMethod None=0, FixedSetpoint=1, CoolingCurve=2,
WeheatIntelligence=3, OpenTherm=4. #32391 constructs a modern flag-only
write except when enabling an unset method (also selects CoolingCurve=2),
and a legacy method-changing fallback. These are not a universally reversible
boolean: legacy off loses the previous method. #32397/#32406/#32414 gate
enabling on explicit system suitability and condensation-risk acceptance;
#32278 likewise requires risk consent for cooling-curve edits. No unguarded
switch, installer/protection override or guessed restore behavior is added.

## Implemented controls

| Control | Read → write | Recovered app behavior / source |
| --- | --- | --- |
| DHW boost | `dhwBoostEndTime` → `DhwBoostEndTime` | String Unix seconds for writes; #21094 (on), #21049 (off). ISO timestamp observed in live read. Buttons request 30/60 minutes or stop. |
| DHW target | `dhwTargetTemperature` → `DhwTargetTemperature` | 40–60 °C, integer steps. Available only with DHW present and mode `dhwScheduleEnabled=0` (fixed). Never switch out of Intelligence/schedule automatically. |
| DHW Intelligence comfort | `dhwComfortPreference` → `properties.DhwComfortPreference` | Safe=1, Balanced=2, Eco=3; #13345, #31144, #31151. Property-only write; requires valid current readback and Intelligence mode. |
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
