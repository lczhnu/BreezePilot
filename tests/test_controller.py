"""Run with the Raspberry Pi HA virtualenv; uses in-memory HA state and fake services."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from custom_components.air_purifier_smooth import Controller, Manager
from custom_components.air_purifier_smooth.devices import from_config
OPTIONS = [str(i) for i in range(1, 16)]
PM, FAN, LEVEL, FAULT = "sensor.pm", "fan.purifier", "select.level", "sensor.fault"
DEVICES = [from_config(dict(id="sample", name="Sample", pm=PM, power=FAN, mode=FAN, level=LEVEL, fault=FAULT, clear_faults=["No Faults"], options=OPTIONS, favorite="最爱"))]


class FakeStates:
    def __init__(self):
        self.values = {}
        for spec in DEVICES:
            self.set(spec.pm, "10")
            self.set(spec.power, "on", {"preset_mode": "最爱", "preset_modes": ["自动", "睡眠", "最爱"]} if spec.mode_is_fan else {})
            if not spec.mode_is_fan:
                self.set(spec.mode, "自动", {"options": ["自动", "睡眠", "最爱"]})
            self.set(spec.level, spec.options[5], {"options": list(spec.options)})
            self.set(spec.fault, spec.clear_faults[0])

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, attributes=None):
        if key == FAN:
            attributes = {"preset_modes": ["自动", "睡眠", "最爱"], **(attributes or {})}
        self.values[key] = SimpleNamespace(state=value, attributes=attributes or {},
                                           last_reported=datetime.now(timezone.utc))


class FakeStore:
    def __init__(self):
        self.value = None

    async def async_load(self):
        return deepcopy(self.value)

    async def async_save(self, value):
        self.value = deepcopy(value)


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    def make_control(self):
        hass = SimpleNamespace(states=FakeStates(), services=SimpleNamespace(async_call=AsyncMock()))
        store = FakeStore()
        with patch("custom_components.air_purifier_smooth.Store", return_value=store):
            c = Controller(hass, DEVICES[0])
        return c, hass, store

    async def test_profile_round_trip_and_restart_disabled(self):
        c, hass, store = self.make_control()
        await c.choose_profile("自定义 1")
        await c.set_param("target", 17)
        await c.set_param("up_seconds", 55)
        await c.profile_action("save")
        await c.set_param("target", 9)
        await c.profile_action("load")
        self.assertEqual(c.params["target"], 17)
        self.assertEqual(c.params["up_seconds"], 55)
        c.enabled = True
        with patch("custom_components.air_purifier_smooth.Store", return_value=store):
            restored = Controller(hass, DEVICES[0])
        await restored.load()
        self.assertEqual(restored.params, c.params)
        self.assertEqual(restored.profiles, c.profiles)
        self.assertEqual(restored.profile, "自定义 1")
        self.assertFalse(restored.enabled)

    async def test_observation_never_calls_hardware(self):
        c, hass, _ = self.make_control()
        await c.set_enabled(True)
        c.last_step -= 100
        await c.tick()
        self.assertEqual(c.virtual_level, 5)
        self.assertEqual(c.current_level(), 6)
        hass.services.async_call.assert_not_awaited()

    async def test_same_report_not_counted_twice(self):
        c, hass, _ = self.make_control()
        hass.states.set(PM, "80")
        await c.set_enabled(True)
        await c.tick()
        await c.tick()
        self.assertEqual(c.engine.high_reports, 1)

    async def test_disabled_never_calls_hardware(self):
        c, hass, _ = self.make_control()
        for _ in range(3):
            await c.tick()
        hass.services.async_call.assert_not_awaited()

    async def test_real_control_one_step_and_confirmation(self):
        c, hass, _ = self.make_control()
        c.observe = False
        async def command(domain, service, data, blocking):
            hass.states.set(LEVEL, data["option"], {"options": OPTIONS})
        hass.services.async_call.side_effect = command
        await c.set_enabled(True)
        c.last_step -= 100
        await c.tick()
        self.assertEqual(c.current_level(), 5)
        self.assertEqual(hass.services.async_call.await_count, 1)
        await c.tick()
        self.assertEqual(hass.services.async_call.await_count, 1)

    async def test_manual_change_and_off_exit(self):
        for entity, value, attrs in [(LEVEL, OPTIONS[9], {}), (FAN, "off", {}),
                                      (FAN, "on", {"preset_mode": "自动"})]:
            c, hass, _ = self.make_control()
            c.observe = False
            await c.set_enabled(True)
            old = hass.states.get(entity)
            hass.states.set(entity, value, attrs)
            c.device_changed(SimpleNamespace(data={"entity_id": entity,
                "old_state": old, "new_state": hass.states.get(entity)}))
            self.assertFalse(c.enabled)

    async def test_own_command_echo_does_not_exit(self):
        c, hass, _ = self.make_control()
        c.observe = False
        await c.set_enabled(True)
        c.expected[LEVEL] = (OPTIONS[4], time.monotonic() + 20)
        old = hass.states.get(LEVEL)
        hass.states.set(LEVEL, OPTIONS[4], {"options": OPTIONS})
        c.device_changed(SimpleNamespace(data={"entity_id": LEVEL, "old_state": old,
            "new_state": hass.states.get(LEVEL)}))
        self.assertTrue(c.enabled)

    async def test_invalid_stale_and_fault_stop_commands(self):
        for mode in ["invalid", "stale", "fault"]:
            c, hass, _ = self.make_control()
            c.observe = False
            await c.set_enabled(True)
            if mode == "invalid":
                hass.states.set(PM, "nan")
            elif mode == "stale":
                hass.states.get(PM).last_reported = datetime.fromtimestamp(time.time() - 4000, timezone.utc)
            else:
                hass.states.set(FAULT, "颗粒物传感器异常")
            c.last_step -= 100
            await c.tick()
            hass.services.async_call.assert_not_awaited()
            self.assertEqual(c.engine.integral, 0)

    async def test_power_off_cannot_enable_or_turn_on(self):
        c, hass, _ = self.make_control()
        c.observe = False
        hass.states.set(FAN, "off")
        with self.assertRaises(Exception):
            await c.set_enabled(True)
        self.assertFalse(c.enabled)
        hass.services.async_call.assert_not_awaited()

    async def test_storage_contains_no_enabled_flag(self):
        c, _, store = self.make_control()
        c.enabled = True
        await c.persist()
        self.assertNotIn("enabled", store.value)

    async def test_startup_unavailable_status_clears_when_device_returns(self):
        c, hass, _ = self.make_control()
        hass.states.set(FAN, "unavailable")
        await c.tick()
        self.assertIn("不可用", c.status)
        hass.states.set(FAN, "on", {"preset_mode": "最爱"})
        await c.tick()
        self.assertEqual(c.status, "已关闭；参数已保留")


if __name__ == "__main__":
    unittest.main()
