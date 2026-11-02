from homeassistant.components.select import SelectEntity
from homeassistant.helpers.entity import Entity
from . import DOMAIN
from .entity import ControlEntity


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    manager = hass.data[DOMAIN]
    async_add_entities([DeviceSelect(manager)] + [ProfileSelect(c) for c in manager.controls.values()])


class DeviceSelect(SelectEntity):
    _attr_should_poll = False
    _attr_name = "空净 选择净化器"
    _attr_unique_id = "air_purifier_smooth_device"
    _attr_icon = "mdi:air-purifier"

    def __init__(self, manager):
        self.manager = manager
        self._attr_options = [c.spec.label for c in manager.controls.values()]
        self.entity_id = "select.air_purifier_device"

    async def async_added_to_hass(self):
        self.manager.listeners.add(self.async_write_ha_state)
        self.async_on_remove(lambda: self.manager.listeners.discard(self.async_write_ha_state))

    @property
    def current_option(self):
        return self.manager.selected

    async def async_select_option(self, option):
        await self.manager.choose_device(option)


class ProfileSelect(ControlEntity, SelectEntity):
    def __init__(self, control):
        super().__init__(control, "select", "profile", "参数组", "mdi:folder-cog")
        self._attr_options = list(control.profiles)

    @property
    def current_option(self):
        return self.control.profile

    async def async_select_option(self, option):
        await self.control.choose_profile(option)
