from homeassistant.components.switch import SwitchEntity
from . import DOMAIN
from .entity import ControlEntity


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    async_add_entities([ControlSwitch(c, key, name, icon)
                        for c in hass.data[DOMAIN].controls.values()
                        for key, name, icon in [("enabled", "启用平滑控制", "mdi:fan-auto"),
                                                ("observe", "仅观察（不调设备）", "mdi:eye")]] +
                       [AutoSwitch(hass.data[DOMAIN])])


class AutoSwitch(SwitchEntity):
    _attr_should_poll = False
    _attr_name = "空净 自动模式总开关"
    _attr_unique_id = "air_purifier_smooth_auto_enabled"
    _attr_icon = "mdi:autorenew"
    entity_id = "switch.air_purifier_auto_enabled"

    def __init__(self, manager):
        self.manager = manager

    @property
    def is_on(self):
        return self.manager.auto_enabled

    async def async_added_to_hass(self):
        self.manager.listeners.add(self.async_write_ha_state)
        self.async_on_remove(lambda: self.manager.listeners.discard(self.async_write_ha_state))

    async def async_turn_on(self, **kwargs):
        await self.manager.set_auto_enabled(True)

    async def async_turn_off(self, **kwargs):
        await self.manager.set_auto_enabled(False)


class ControlSwitch(ControlEntity, SwitchEntity):
    def __init__(self, control, key, name, icon):
        super().__init__(control, "switch", key, name, icon)

    @property
    def is_on(self):
        return getattr(self.control, self.key)

    async def async_turn_on(self, **kwargs):
        await self._set(True)

    async def async_turn_off(self, **kwargs):
        await self._set(False)

    async def _set(self, value):
        if self.key == "enabled":
            await self.control.set_enabled(value)
        else:
            await self.control.set_observe(value)
