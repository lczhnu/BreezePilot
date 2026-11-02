from homeassistant.components.number import NumberEntity, NumberMode
from . import DOMAIN
from .engine import PARAMS
from .entity import ControlEntity


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    async_add_entities([Parameter(c, key, spec) for c in hass.data[DOMAIN].controls.values()
                        for key, spec in PARAMS.items()] +
                       [AutoParameter(c, k, s) for c in hass.data[DOMAIN].controls.values()
                        for k, s in AUTO_PARAMS.items()])


from .auto import AUTO_PARAMS


class AutoParameter(ControlEntity, NumberEntity):
    def __init__(self, control, key, spec):
        low, high, step, unit, name = spec
        super().__init__(control, "number", "auto_" + key, name, "mdi:timer-cog")
        self.param_key = key
        self._attr_native_min_value = low
        self._attr_native_max_value = high
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        self._attr_mode = NumberMode.BOX

    @property
    def native_value(self):
        return self.control.auto_params[self.param_key]

    async def async_set_native_value(self, value):
        await self.control.set_auto_param(self.param_key, value)


class Parameter(ControlEntity, NumberEntity):
    def __init__(self, control, key, spec):
        name, _, low, high, step, unit = spec
        super().__init__(control, "number", key, name, "mdi:tune-vertical")
        self._attr_native_min_value = low
        self._attr_native_max_value = high
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        self._attr_mode = NumberMode.BOX

    @property
    def native_value(self):
        return self.control.params[self.key]

    async def async_set_native_value(self, value):
        await self.control.set_param(self.key, value)


