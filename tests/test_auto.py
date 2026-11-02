from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from test_controller import ControllerTests

class AutomaticTests(ControllerTests):
    def auto_control(self):
        c,h,s=self.make_control()
        c.manager=SimpleNamespace(auto_enabled=True)
        c.set_enabled=AsyncMock()
        async def call(domain, service, data, blocking):
            h.states.set(c.spec.power,'on' if service=='turn_on' else 'off')
        h.services.async_call.side_effect=call
        return c,h,s

    async def test_high_requires_continuous_time_and_starts_real_control(self):
        c,h,_=self.auto_control()
        h.states.set(c.spec.power,'off');h.states.set(c.spec.pm,'20')
        await c.auto_tick();h.services.async_call.assert_not_awaited()
        c.auto_since-=301
        await c.auto_tick()
        self.assertFalse(c.observe)
        c.set_enabled.assert_awaited_once_with(True)
        self.assertEqual(h.states.get(c.spec.power).state,'on')

    async def test_low_stops_control_and_device(self):
        c,h,_=self.auto_control();c.enabled=True;c.observe=False
        h.states.set(c.spec.pm,'7')
        await c.auto_tick();c.auto_since-=601
        await c.auto_tick()
        self.assertFalse(c.enabled)
        self.assertEqual(h.states.get(c.spec.power).state,'off')

    async def test_equal_threshold_and_invalid_reading_reset_timer(self):
        c,h,_=self.auto_control();h.states.set(c.spec.power,'off')
        h.states.set(c.spec.pm,'20');await c.auto_tick()
        h.states.set(c.spec.pm,'15');await c.auto_tick()
        self.assertIsNone(c.auto_since)
        h.states.set(c.spec.pm,'20');await c.auto_tick()
        h.states.set(c.spec.pm,'unavailable');await c.auto_tick()
        self.assertIsNone(c.auto_since)
        h.services.async_call.assert_not_awaited()

    async def test_disabled_auto_never_commands_and_parameters_persist(self):
        c,h,s=self.auto_control();c.manager.auto_enabled=False
        h.states.set(c.spec.pm,'200');await c.auto_tick()
        h.services.async_call.assert_not_awaited()
        await c.set_auto_param('on_minutes',12)
        self.assertEqual(s.value['auto_params']['on_minutes'],12)

    async def test_target_change_restarts_countdown(self):
        c,h,_=self.auto_control();h.states.set(c.spec.power,'off');h.states.set(c.spec.pm,'100')
        await c.auto_tick();c.auto_since-=301;c.params['target']=20
        await c.auto_tick()
        h.services.async_call.assert_not_awaited()
