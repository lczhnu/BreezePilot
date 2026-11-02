# BreezePilot

English | [中文](README.zh-CN.md)

BreezePilot is a Home Assistant custom integration for smooth, PM2.5-driven proportional–integral (PI) control of compatible Xiaomi air purifiers. It aims to balance air quality and noise while reducing frequent speed changes. It cannot guarantee a particular concentration and is not a medical device. There is no derivative term. It supports multiple devices, observation mode, saved profiles, and optional automatic power control.

## Architecture and behavior

```text
Xiaomi/compatible integration → HA PM2.5, fault, power, mode and level entities
                                      ↓
                          filter → PI + deadband/hysteresis → one-step rate limiter
                                      ↓
                          HA fan/switch/select services → purifier
```

Only new reports update the filter and control calculation; a five-second loop checks execution. Defaults: target 10 μg/m³, Kp 0.35, Ki 0, filter 60 s, and at least 30/60 s between upward/downward steps. Integral clamping, deadband, rounding hysteresis, two high reports, and one-step output limiting reduce oscillation. Invalid or stale readings, faults, and mismatched entities pause commands; manual level/mode/power changes stop active control. HA state confirmation does not verify physical RPM. Parameters are stored per device. Active control starts disabled after restart, while the global automatic power switch is restored.

## Requirements and compatibility

Home Assistant must already expose a PM2.5 sensor, power fan/switch, mode fan/select, 15-option level select, and fault sensor via a compatible integration. Not every model or integration exposes them. Inspect **Developer Tools → States**: level options must exactly match all 15 actual options in low-to-high order; favorite mode and clear-fault strings must match the actual states. Set `favorite` for non-Chinese mode labels.

PM2.5 must update regularly. Automatic startup cannot work reliably if the sensor stops reporting when the purifier is off. Freshness uses HA `last_reported`; this requires an HA version supporting it and cannot prove the device sensor itself is healthy. The original implementation was used with `zhimi.airp.mea1` and `zhimi.airp.rma3`; this generalized public version has not yet been revalidated on physical hardware or a full HA installation. Firmware, integration and language differences matter.

## Installation

1. Back up your HA configuration. Copy `custom_components/air_purifier_smooth` from this repository into the same path under your HA configuration directory.
2. Inspect the entities and options in HA. Add the example below to `configuration.yaml`, replacing every placeholder and **all 15 illustrative level options** with your actual values. Add further entries under `devices` for more purifiers. Use a unique lowercase `id` containing letters, digits and underscores; changing it creates new entities and storage.
3. Check the HA configuration and restart. Find the new entities in Developer Tools and add them to a native Entities card. This version uses YAML and has no UI setup flow or one-click HACS installation.

```yaml
air_purifier_smooth:
  devices:
    - id: purifier
      name: Air purifier
      pm: sensor.example_pm25
      power: fan.example_purifier
      mode: fan.example_purifier
      level: select.example_favorite_level
      fault: sensor.example_fault
      favorite: "最爱"
      clear_faults: ["No Faults", "0", "无故障", "无"]
      options: ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15"]
```

4. Turn the purifier on, enable observation (`仅观察`), then enable smooth control (`启用平滑控制`). Check the simulated level, status and command count. For real control, turn observation off and enable control again. Start with a low maximum level and supervise the device. The current HA entity labels are Chinese.
5. Automatic power control is off by default. When enabled globally, the default is to start real control after five continuous minutes above target +5, and stop and power off after ten continuous minutes below target −2. Its switch is restored on restart, but timers restart. Confirm off-state PM reporting and unattended operation are appropriate before enabling it.

## Tuning and removal

Entities expose target, Kp, Ki per minute, integral limit, deadband, filter time, up/down intervals, maximum level, rounding hysteresis and report timeout. Start with Ki=0; if needed, increase it slowly while observing noise and oscillation. Six profiles can be overwritten: selecting a profile does not apply it; use the save/load buttons. Changing observation mode stops control. Stopping control leaves the device in its current state. Devices run independently; the automatic power switch applies to all devices.

To remove, turn off automatic and active control, remove YAML configuration, check configuration and restart, then remove the integration directory. Stored parameters are not automatically deleted.

## Verification and privacy

The release excludes the original household entity IDs, IP addresses, tokens, dashboards, and deployment scripts. It uses existing HA entities and services and does not extract Xiaomi tokens. Run `python -m unittest discover -s tests -v` for algorithm and simulated-controller tests (controller tests require a Home Assistant Python environment). Do not commit your HA configuration, logs, or `.storage`.
