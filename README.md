# Shelly BLU TRV Schedule

Home Assistant integration to view and edit the **schedule stored on Shelly BLU TRV
thermostats** — without the Shelly app or cloud. The rules live on the thermostat
itself, so the schedule keeps working when Home Assistant or Wi-Fi is down.

The TRVs are reached through a **Shelly BLU Gateway Gen3** (local RPC
`BluTrv.Call` → `TRV.ListScheduleRules` / `AddScheduleRule` /
`UpdateScheduleRule` / `RemoveScheduleRule`).

![Schedule card for two BLU TRVs: weekly overview and rule editor](https://raw.githubusercontent.com/Flautz/ha-shelly-blu-trv-schedule/main/docs/screenshot.png)

## Features

- **Lovelace card** with a weekly overview (colour-coded by temperature) and an
  editor: time, weekdays, temperature or valve position, on/off per rule,
  add/delete, save. It can also copy a schedule to another TRV.
- Per TRV:
  - `climate.<trv>_thermostat`: thermostat with the modes **auto** (the schedule
    on the TRV runs), **heat** (manual: schedule paused, target as set) and
    **off** (schedule paused, minimum target of 4 °C as frost protection; switching
    back to heat restores the previous target). Switching to auto applies the
    current schedule period right away. Changing the target in auto lasts until the
    next switching time, like on Homematic thermostats. Use it with the
    `thermostat` card and the `climate-hvac-modes` feature. Temperatures come from
    the Shelly climate entity of the TRV, so they update without delay.
  - `sensor.<trv>_schedule`: next scheduled change (timestamp). The attributes
    hold the rules (`time`, `days`, `target_C`/`pos`, `enable`, `rule_id`).
  - `switch.<trv>_schedule_active`: pauses the schedule by disabling all rules,
    and restores the previously active rules when turned back on (e.g. for holidays).
- Services for automations: `get_schedule` (returns the rules), `set_schedule`,
  `add_rule`, `update_rule`, `remove_rule`, `copy_schedule`, `refresh`.
- The entities are added to the existing TRV devices of the Shelly integration.
  Services and the card also accept the Shelly climate entity of a TRV.
- Bluetooth traffic is kept low: the gateway is polled every 60 s, and rules are
  only re-read from the TRV when its `schedule_rev` changes (for example after
  editing in the Shelly app).

## Installation

**HACS:** *HACS → ⋮ → Custom repositories* → add
`https://github.com/Flautz/ha-shelly-blu-trv-schedule` as type *Integration*, then install
**Shelly BLU TRV Schedule** and restart Home Assistant.

**Manual:** copy `custom_components/shelly_blu_trv_schedule` into your
`config/custom_components/` folder and restart Home Assistant.

Then add it under *Settings → Devices & services → Add integration → Shelly BLU TRV Schedule*.
Gateways already set up in the Shelly integration are offered automatically,
including their credentials. You can also enter the address manually.

The card is registered automatically, so you don't need to add a Lovelace resource:

```yaml
type: custom:shelly-trv-schedule-card
entity: sensor.schlafzimmer_zeitplan
# title: Bedroom   # optional
```

Thermostat card with mode buttons:

```yaml
type: thermostat
entity: climate.schlafzimmer_thermostat
features:
  - type: climate-hvac-modes
    hvac_modes: [auto, heat, "off"]
```

## Service examples

```yaml
# Replace the whole schedule
action: shelly_blu_trv_schedule.set_schedule
data:
  entity_id: sensor.schlafzimmer_zeitplan
  rules:
    - { time: "06:30", days: [mon, tue, wed, thu, fri], target_C: 21 }
    - { time: "08:00", days: weekend, target_C: 21 }
    - { time: "22:00", days: all, target_C: 17 }

# Add a single rule
action: shelly_blu_trv_schedule.add_rule
data:
  entity_id: climate.shellyblutrv_f84477000001   # climate entity works too
  time: "17:00"
  days: [sat, sun]
  target_C: 20.5

# Read the rules
action: shelly_blu_trv_schedule.get_schedule
data:
  entity_id: sensor.schlafzimmer_zeitplan
response_variable: schedule
```

`days` accepts `mon`…`sun`, `all`, `weekdays` and `weekend`. Rules use either
`target_C` (°C, within the TRV's `min_target_C`/`max_target_C`) or `pos` (valve
position 0–100 %). Advanced Shelly cron expressions can be given as `timespec`
(`"0 30 6 * * MON-FRI"`); the card shows such rules as raw text.

## Notes

- A TRV stores at most **10 rules**.
- Writing goes over Bluetooth via the gateway. The first call after a pause can take
  10–20 s while the gateway connects to the TRV.
- If a write fails halfway (TRV out of range, for example), the card and sensor
  show the schedule as it is on the TRV after the failure.
- The thermostat shows **off** whenever the schedule is paused and the target is at
  the TRV's minimum; turning the ring above it switches to heat.
- Timespecs are written the same way the Shelly app writes them
  (`0 30 6 * * MON,TUE,…`), so the app still displays them correctly.

## Development

```bash
pip install pytest-homeassistant-custom-component
pytest
```

## License

[MIT](LICENSE)
