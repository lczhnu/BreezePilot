from homeassistant.components.button import ButtonEntity
from . import DOMAIN
from .entity import ControlEntity


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    async_add_entities([ProfileButton(c, action, name, icon)
                        for c in hass.data[DOMAIN].controls.values()
                        for action, name, icon in [("load", "载入所选参数组", "mdi:folder-open"),
                                                   ("save", "保存到所选参数组", "mdi:content-save")]])


class ProfileButton(ControlEntity, ButtonEntity):
    def __init__(self, control, action, name, icon):
        super().__init__(control, "button", f"profile_{action}", name, icon)
        self.action = action

    async def async_press(self):
        await self.control.profile_action(self.action)
