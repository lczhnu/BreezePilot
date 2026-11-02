"""Shared event-driven entity base."""
from homeassistant.helpers.entity import Entity


class ControlEntity(Entity):
    _attr_should_poll = False

    def __init__(self, control, platform, key, name, icon):
        self.control = control
        self.key = key
        self.entity_id = f"{platform}.{control.spec.prefix}_{key}"
        self._attr_unique_id = f"{control.storage_key}_{key}"
        self._attr_name = f"{control.spec.label} {name}"
        self._attr_icon = icon

    async def async_added_to_hass(self):
        self.control.listeners.add(self.async_write_ha_state)
        self.async_on_remove(lambda: self.control.listeners.discard(self.async_write_ha_state))
