import time
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from . import DOMAIN
from .entity import ControlEntity


SENSORS = {
    "auto_status": ("自动启停状态", "mdi:autorenew"),
    "status": ("控制状态", "mdi:state-machine"),
    "filtered": ("滤波 PM2.5", "mdi:blur"),
    "requested": ("请求档位", "mdi:stairs-up"),
    "actual": ("设备配置档位", "mdi:fan"),
    "simulated": ("观察模拟档位", "mdi:eye"),
    "integral": ("当前积分补偿", "mdi:math-integral"),
    "profile_status": ("参数组保存状态", "mdi:content-save-check"),
    "report_age": ("PM2.5 距上次 HA 报告", "mdi:timer-sand"),
    "current_pm": ("控制使用的 PM2.5", "mdi:air-filter"),
    "commands": ("本次启动设备命令数", "mdi:counter"),
}


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    async_add_entities([ControlSensor(c, k, *v) for c in hass.data[DOMAIN].controls.values()
                        for k, v in SENSORS.items()])


class ControlSensor(ControlEntity, SensorEntity):
    def __init__(self, control, key, name, icon):
        super().__init__(control, "sensor", key, name, icon)
        if key in ("filtered", "current_pm"):
            self._attr_native_unit_of_measurement = "μg/m³"
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif key in ("requested", "actual", "simulated", "integral"):
            self._attr_native_unit_of_measurement = "档"
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_suggested_display_precision = 2 if key == "integral" else 0
        elif key == "report_age":
            self._attr_native_unit_of_measurement = "s"

    @property
    def native_value(self):
        c = self.control
        return {
            "auto_status": lambda: c.auto_status,
            "status": lambda: c.status,
            "filtered": lambda: round(c.engine.filtered, 2) if c.engine.filtered is not None else None,
            "requested": lambda: c.engine.requested,
            "actual": c.current_level,
            "simulated": lambda: c.virtual_level,
            "integral": lambda: round(c.engine.integral, 3),
            "profile_status": lambda: "未保存到所选组" if c.params != c.profiles[c.profile] else "与所选组一致",
            "commands": lambda: c.command_count,
            "current_pm": lambda: c.reading()[0],
            "report_age": lambda: max(0, round(time.time() - c.hass.states.get(c.spec.pm).last_reported.timestamp())) if c.hass.states.get(c.spec.pm) else None,
        }[self.key]()

    @property
    def extra_state_attributes(self):
        c = self.control
        if self.key == "profile_status":
            return {"selected_profile": c.profile, "message": c.notice,
                    "stored_parameters": c.profiles[c.profile]}
        if self.key == "status":
            return {"device": c.spec.label, "enabled": c.enabled, "observe_only": c.observe,
                    "last_command": c.last_command,
                    "confirmation": "HA 实体回读；不代表物理 RPM 已确认",
                    "freshness": "last_reported 为 HA 状态报告时间，不保证设备内部采样正常"}
        return {}
