# Navien NaviLink Water Heater Custom Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub last commit](https://img.shields.io/github/last-commit/Rocontento/hass_navien_water_heater)](https://github.com/Rocontento/hass_navien_water_heater/commits/main)

Control and monitor Navien water heaters connected to the Navien cloud through a
[NaviLink or NaviLink Lite](https://www.navieninc.com/accessories/navilink) gateway.

**One config entry covers your whole NaviLink account.** Every water heater on the
account is discovered automatically and each one becomes its own device in Home
Assistant, addressed by MAC address. Adding a heater in the NaviLink app and
reloading the integration is enough to pick it up.

## What you get, per water heater

| Platform | Entity | Notes |
| --- | --- | --- |
| `water_heater` | The heater itself | Target temperature, on/off, away mode, current temperature |
| `switch` | Power | Same on/off as the water heater entity, handy in automations |
| `switch` | Recirculation | Only when the gateway reports Hot Button / on demand support |
| `number` | Target temperature | The setpoint as a plain slider |
| `sensor` | Hot water temperature, inlet temperature | |
| `sensor` | Hot water flow | Volume flow rate; Home Assistant converts to your units |
| `sensor` | Burner output | Instantaneous heat output in watts |
| `sensor` | Gas used | Total increasing, works with the Energy dashboard |
| `sensor` | Heating capacity | Burner modulation, in percent |
| `sensor` | Target temperature, error code, last update | Diagnostic |
| `binary_sensor` | Hot water in use, Burner | Derived from flow and modulation |
| `binary_sensor` | Problem | On whenever the heater reports a non-zero error code |
| `button` | Refresh | Ask a heater for a fresh status straight away |

Cascade installations get one set of per-unit sensors for every physical unit plus
channel-wide totals.

Fields your gateway reports that the integration has no curated name for are still
exposed, as diagnostic sensors disabled by default. Enable them from the device
page if you need them, and please open an issue so they can be named properly.

Diagnostics are supported: **Settings → Devices & Services → Navien → ⋮ →
Download diagnostics** gives a redacted dump of everything the gateway reports,
which is the fastest way to get a problem looked at.

## Installation

Requires Home Assistant 2024.6 or newer.

**HACS**
1. HACS → three-dot menu → Custom repositories
   **URL:** `https://github.com/rocontento/hass_navien_water_heater`
   **Category:** Integration
2. Install, then restart Home Assistant.
3. Settings → Devices & Services → Add Integration → Navien.

**Manual**
1. Copy `custom_components/navien_water_heater` into your `config/custom_components`
   directory.
2. Restart Home Assistant and add the integration as above.

## Configuration

You are asked for your NaviLink username, password and a polling interval. The
polling interval can be changed later without re-adding the integration through
**Configure** on the integration card. Between polls the integration also reacts
to whatever the gateway pushes on its own, so a longer interval costs less than
you might expect; 30 seconds is a good default.

If Navien ever rejects the stored password, Home Assistant raises a re-auth prompt
instead of silently going dead.

## Upgrading from 1.x

Version 1 stored **one config entry per water heater** and remembered which heater
it belonged to by its *position* in the NaviLink device list. Adding a heater to
the account reshuffled that list, so an existing entry silently started pointing at
a different heater, and the original one stopped responding in Home Assistant while
continuing to work fine in the NaviLink app.

On first start, 2.0 migrates automatically:

- Old entries are converted to a single account-wide entry. Their devices and
  entities are handed over to the surviving entry first, so entity ids, names and
  area assignments are kept.
- Heaters are matched by MAC address from now on, so the device list order no
  longer matters.
- Entity unique ids are unchanged, so history carries over.

Three sensors did change units, because they now carry proper device classes and
let Home Assistant do the conversion for your locale. Long-term statistics for
these may report a unit change once:

| Sensor | Before | Now |
| --- | --- | --- |
| Burner output | `kcal/hr` or `BTU/hr` | watts |
| Hot water flow | `liters/min` or `gal/min` | L/min, converted for display |
| Gas used | m³ or ft³ | m³, converted for display |

## Troubleshooting

Turn on debug logging to see the raw traffic:

```yaml
logger:
  default: warning
  logs:
    custom_components.navien_water_heater: debug
```

The integration keeps a single MQTT connection open for the whole account and
rebuilds it with exponential backoff whenever the cloud drops it, so transient
"connection lost" messages in the log are normal and self-healing.

## Development

```bash
pip install -r requirements_test.txt
pytest
```

The test suite runs the integration inside a real Home Assistant against a
simulated NaviLink cloud (`tests/fake_cloud.py`), covering unit conversion, the
multi-heater addressing, migration from 1.x, service calls and reconnection.

## Preheat/Recirculation "Hot Button" functionality
For water heaters with hot water recirculation functionality (e.g. A or A2) via dedicated External Loop or [NaviCirc](https://www.navieninc.com/accessories/navicirc), you will also be able to directly control [Hot Button](https://www.navieninc.com/accessories/hotbutton) functionality if a Hot Button PCB is also installed. This PCB is pre-installed on A2 models, but must be [installed](https://www.navieninc.com/downloads/hotbutton-installation-instructions-en) as an [add-on kit](https://www.navieninc.com/products/npe-240a/accessories) for the older A models. Before purchasing this add-on kit, check your main PCB's model version (P20 or newer) IAW Step 2.2.4. The physical buttons _do not_ need to be connected to gain hot button functionality through this integration. _Check the NaviLink app to ensure everything is configured properly (Hot Button instead of Schedule options)_. If you install this integration without Hot Button functionality and later enable it, just Reload the integration to add the Recirculation switch entity. **You will have a recirculation switch entity that can be manually controlled via dashboard, voice commands, and/or automation!**

The preheat/recirculation settings (i.e. parameters) are inconsistently named between the A and A2 models. They can also be misleading (e.g. interval time is duration, but sample time is the interval time). This table should help you decode these settings to more effectively configure and troubleshoot your water heater. Since the Hot Button function is not standard on A models, better descriptions of these parameters can be found under Step 7.5.1.2 on pg 99 and Category 4 of Step 7.5.1.3 on pg 102 of the [A2 installation manual](https://www.navieninc.com/downloads/npe-2-installation-and-operation-manual-en):

|    A (See Note 1)     |    A2   | Description | Notes |
| -------- | ------- | ------- |------- |
| Preheat Pump Output Time (P.12) | Recirc Interval Time | Recirculation Duration | See Note 2. Hot Button 5 mins |
| Preheat Interval Time (P.14) | Recirc Sample Time | Recirculation Interval | Hot Button N/A (not mentioned in manuals) |
| Preheat Off Offset Temp (P.15) | Recirc off Diff. Temp | Recirculation Off Delta | See Note 2. Hot Button --- |
| On-demand Pipe Target Length (P.16) | Fixture Distance | Recirculation Off without External Temp Sensor | See Note 3 |

1. For the A model, set IAW Step 6.5 on pgs 65-68 of the [A installation manual](https://www.navieninc.com/downloads/npe-a-s-manuals-installation-manual-en).
2. There are DIP switches on the Hot Button PCB (see pg 34 of the A2 installation manual). [Some users](https://community.home-assistant.io/t/navien-hot-water-heater-navilink/330044/47) find they must set DIP switch 2 to ON for the recirculation function to heat to the setpoint. This setting changes the behavior of both of these settings as described on pg 102 of the [A2 installation manual](https://www.navieninc.com/downloads/npe-2-installation-and-operation-manual-en).
3. If recirculation does not stay on long enough after Hot Button activation, try increasing to a much higher pipe length.

## Sample card

> The entity ids below come from a 1.x install. On a fresh 2.x setup they are
> named after the heater, for example `switch.<heater name>_recirculation` and
> `sensor.<heater name>_hot_water_flow`. Adjust them to match your own entities.

Here is an example of a card that can be used to monitor and control a water heater including recirculation. It uses the following custom cards:
- [ApexCharts](https://github.com/RomRider/apexcharts-card)
- [Mushroom](https://github.com/piitaya/lovelace-mushroom)
- [Card Mod](https://github.com/thomasloven/lovelace-card-mod)
- [Stack In Card](https://github.com/custom-cards/stack-in-card)

![image](https://github.com/GitHubGoody/hass_navien_water_heater/assets/46235745/42b851b3-7a48-4b8e-89b0-b4285a8396bb)

<details><summary>Code</summary>
   
```
- type: custom:stack-in-card
  mode: vertical
  cards:
    - type: custom:stack-in-card
      mode: horizontal
      cards:
        - type: tile
          entity: water_heater.house
          features:
            - type: target-temperature
          tap_action:
            action: toggle
          icon_tap_action:
            action: toggle
        - type: custom:mushroom-entity-card
          entity: switch.water_heater_recirculation
          icon_color: lime
          name: Recirculation
          layout: vertical
          card_mod:
            style: |
              mushroom-shape-icon {
                {% if states('switch.water_heater_recirculation') == 'on' %}
                  --shape-animation: spin 1.5s linear infinite;
                {% else %}
                {% endif %}       
              }
          tap_action:
            action: call-service
            service: switch.turn_on
            target:
              entity_id: switch.water_heater_recirculation
          icon_tap_action:
            action: call-service
            service: switch.turn_on
            target:
              entity_id: switch.water_heater_recirculation
        - type: custom:stack-in-card
          mode: vertical
          cards:
           - type: custom:apexcharts-card
             graph_span: 3h
             header:
               show: true
               title: Water Heater
             yaxis:
               - id: Temperature
                 opposite: true
                 decimals: 0
                 min: 70
                 max: 140
                 apex_config:
                   tickAmount: 7
                   title:
                     text: Temperature
                     style:
                       color: blue
               - id: Flow Rate
                 opposite: true
                 decimals: 0
                 min: 0
                 max: 4
                 apex_config:
                   tickAmount: 4
                   title:
                     text: Flow Rate
                     style:
                       color: green
             apex_config:
               labels:
                 useSeriesColors: true
               chart:
                 height: 300px
               annotations:
                 position: front
                 yaxis:
                   - y: 114 # Adjust this...
                     y2: 116 # ...and this according to your water heater's setpoint. If anyone can make this automatically update, please share ;-)
                     strokeDashArray: 0
                     borderColor: 'red'
                     borderWidth: 0
                     fillColor: 'red'
                     opacity: 0.1
                     offsetX: 0
                     offsetY: -3
                     width: '100%'
                     yAxisIndex: 0
                     label:
                       text: 'Setpoint'
                       borderColor: '#c2c2c2'
                       borderWidth: 1
                       borderRadius: 2
                       textAnchor: 'start'
                       position: 'left'
                       offsetX: 0
                       offsetY: 0
                       style:
                         background: 'red'
                         color: '#c2c2c2'
                         fontSize: '12px'
                         fontWeight: 400
                         padding:
                           left: 5
                           right: 5
                           top: 0
                           bottom: 0
             series:
               - entity: switch.water_heater_power
                 name: Power
                 yaxis_id: Temperature
                 type: area
                 curve: stepline
                 extend_to: now
                 color: green
                 stroke_width: 0
                 opacity: 0.25
                 transform: 'return x === ''on'' ? 72 : 70;'
                 show:
                   in_header: false
                   legend_value: false
               - entity: sensor.water_heater_flow_rate
                 name: Flow Rate
                 yaxis_id: Flow Rate
                 color: green
                 stroke_width: 1
                 curve: stepline
               - entity: switch.water_heater_recirculation
                 name: Recirculation
                 yaxis_id: Temperature
                 type: area
                 curve: stepline
                 extend_to: now
                 color: lime
                 stroke_width: 0
                 opacity: 1.00
                 transform: 'return x === ''on'' ? 74 : 70;'
                 show:
                   in_header: false
                   legend_value: false
               - entity: sensor.water_heater_inlet_temperature
                 name: Inlet
                 yaxis_id: Temperature
                 color: lightblue
                 stroke_width: 2
                 curve: straight
               - entity: sensor.water_heater_outlet_temperature
                 name: Outlet
                 yaxis_id: Temperature
                 color: red
                 stroke_width: 2
                 curve: straight
```

</details>

## Sample automation
A simple automation that will run recirculation at the top and bottom of each hour from 5 AM to 10 PM.

```
alias: Water Heater Recirculation
description: "Water heater recirculation at the top and bottom of each hour from 5 AM to 10 PM."
trigger:
  - platform: time_pattern
    minutes: "0"
  - platform: time_pattern
    minutes: "30"
condition:
  - condition: time
    after: "04:55:00"
    before: "22:05:00"
    enabled: true
action:
  - service: switch.turn_on
    target:
      entity_id: switch.water_heater_recirculation
mode: single
```

https://www.buymeacoffee.com/nikshriv
