"""Device adapters supplied by the Home Assistant configuration."""
from dataclasses import dataclass

@dataclass(frozen=True)
class DeviceSpec:
    key: str
    label: str
    prefix: str
    pm: str
    power: str
    level: str
    mode: str
    fault: str
    mode_is_fan: bool
    clear_faults: tuple[str, ...]
    options: tuple[str, ...]
    favorite: str

def from_config(item):
    return DeviceSpec(item["id"], item["name"], "breezepilot_" + item["id"],
                      item["pm"], item["power"], item["level"], item["mode"],
                      item["fault"], item["mode"].startswith("fan."),
                      tuple(item["clear_faults"]), tuple(item["options"]), item["favorite"])
