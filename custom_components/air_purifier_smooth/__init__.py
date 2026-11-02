"""Local HA controller; no extra credentials or third-party dependencies."""
import asyncio
from copy import deepcopy
from datetime import timedelta
import logging
import math
import time

import voluptuous as vol
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv, discovery
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.exceptions import HomeAssistantError

from .engine import DEFAULTS, PARAMS, PROFILES, Engine, validated, next_level
from .devices import from_config

DOMAIN = "air_purifier_smooth"
_LOGGER = logging.getLogger(__name__)
DEVICE_SCHEMA = vol.Schema({
    vol.Required("id"): vol.All(str, vol.Match(r"^[a-z][a-z0-9_]*$")),
    vol.Required("name"): cv.string,
    vol.Required("pm"): cv.entity_id, vol.Required("power"): cv.entity_id,
    vol.Required("level"): cv.entity_id, vol.Required("mode"): cv.entity_id,
    vol.Required("fault"): cv.entity_id,
    vol.Required("options"): vol.All(cv.ensure_list, [cv.string], vol.Length(min=15, max=15)),
    vol.Optional("favorite", default="最爱"): cv.string,
    vol.Optional("clear_faults", default=["No Faults", "0", "无故障", "无"]): vol.All(cv.ensure_list, [cv.string], vol.Length(min=1)),
})
def validate_devices(items):
    for field in ("id", "name"):
        if len({x[field] for x in items}) != len(items):
            raise vol.Invalid("Device IDs and names must be unique")
    for item in items:
        if not item["power"].startswith(("fan.", "switch.")) or not item["mode"].startswith(("fan.", "select.")) or not item["level"].startswith("select."):
            raise vol.Invalid("power must be fan/switch, mode fan/select and level select")
        if len(set(item["options"])) != 15:
            raise vol.Invalid("Level options must be unique")
    return items
CONFIG_SCHEMA = vol.Schema({vol.Required(DOMAIN): vol.Schema({
    vol.Required("devices"): vol.All(cv.ensure_list, [DEVICE_SCHEMA], vol.Length(min=1), validate_devices)
})}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass, config):
    manager = Manager(hass, [from_config(item) for item in config[DOMAIN]["devices"]])
    await manager.load()
    hass.data[DOMAIN] = manager
    for platform in ("number", "switch", "select", "button", "sensor"):
        hass.async_create_task(discovery.async_load_platform(hass, platform, DOMAIN, {}, config))

    @callback
    def start(_event=None):
        for control in manager.controls.values():
            control.unsub_timer = async_track_time_interval(hass, control.tick, timedelta(seconds=5))
            control.unsub_state = async_track_state_change_event(
                hass, list({control.spec.power, control.spec.mode, control.spec.level}), control.device_changed)
            hass.async_create_task(control.tick())

    if hass.is_running:
        start()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, start)
    return True


class Manager:
    """View selection is separate from independently running controllers."""
    def __init__(self, hass, specs):
        self.controls = {spec.key: Controller(hass, spec) for spec in specs}
        self.selected = specs[0].label
        self.store = Store(hass, 1, DOMAIN + "_view")
        self.listeners = set()
        self.auto_enabled = False
        for control in self.controls.values():
            control.manager = self

    async def load(self):
        for control in self.controls.values():
            await control.load()
        data = await self.store.async_load()
        if data and data.get("selected") in [c.spec.label for c in self.controls.values()]:
            self.selected = data["selected"]
        self.auto_enabled = bool(data and data.get("auto_enabled", False))

    async def set_auto_enabled(self, value):
        self.auto_enabled = value
        await self.store.async_save({"selected": self.selected, "auto_enabled": value})
        for control in self.controls.values():
            control.auto_since = None
            control.auto_status = "等待条件" if value else "自动模式已关闭"
            control.publish()
        for listener in tuple(self.listeners):
            listener()

    async def choose_device(self, label):
        if label not in [c.spec.label for c in self.controls.values()]:
            raise HomeAssistantError("未知净化器")
        await self.store.async_save({"selected": label, "auto_enabled": self.auto_enabled})
        self.selected = label
        for listener in tuple(self.listeners):
            listener()


class Controller:
    def __init__(self, hass, spec):
        self.hass = hass
        self.spec = spec
        # Keep the original Store key and entity IDs: no migration or overwriting.
        self.storage_key = DOMAIN + "_" + spec.key
        self.store = Store(hass, 1, self.storage_key)
        self.params = dict(DEFAULTS)
        self.profiles = deepcopy(PROFILES)
        self.profile = "日常"
        self.enabled = False
        self.observe = True
        self.status = "已关闭；参数已保留"
        self.notice = "尚未保存参数组"
        self.engine = Engine()
        self.listeners = set()
        self.lock = asyncio.Lock()
        self.storage_lock = asyncio.Lock()
        self.report_stamp = None
        self.last_step = time.monotonic()
        self.virtual_level = None
        self.expected = {}
        self.failures = 0
        self.retry_after = 0
        self.saturated_since = None
        self.command_count = 0
        self.last_command = "本次启动尚未发出设备命令"
        self.active_real = False
        self.last_problem = None
        self.auto_params = {"on_minutes": 5, "on_delta": 5, "off_minutes": 10, "off_delta": 2}
        self.auto_since = None
        self.auto_signature = None
        self.auto_busy = False
        self.auto_status = "自动模式已关闭"

    async def set_auto_param(self, key, value):
        from .auto import AUTO_PARAMS
        low, high, step, unit, name = AUTO_PARAMS[key]
        value = float(value)
        if not math.isfinite(value) or not low <= value <= high:
            raise HomeAssistantError("自动控制参数超出范围")
        self.auto_params[key] = value
        self.auto_since = None
        await self.persist()
        self.publish()

    async def auto_tick(self):
        if self.auto_busy:
            return
        if not getattr(getattr(self, "manager", None), "auto_enabled", False):
            self.auto_since = None
            return
        raw, _ = self.reading()
        power = self.hass.states.get(self.spec.power)
        fault = self.hass.states.get(self.spec.fault)
        if raw is None or not power or power.state not in ("on", "off") or not fault or fault.state not in self.spec.clear_faults:
            self.auto_since = None
            self.auto_status = "读数/设备/故障状态无效；计时清零"
            return
        direction = "off" if power.state == "on" else "on"
        if power.state == "on" and (not self.enabled or self.observe) and raw > self.params["target"] + self.auto_params["on_delta"]:
            direction = "on"
        target = self.params["target"]
        threshold = target + self.auto_params["on_delta"] if direction == "on" else target - self.auto_params["off_delta"]
        signature = (direction, threshold, self.auto_params[direction + "_minutes"])
        if signature != self.auto_signature:
            self.auto_since = None
            self.auto_signature = signature
        qualifies = raw > threshold if direction == "on" else raw < threshold
        if not qualifies:
            self.auto_since = None
            self.auto_status = "等待开机条件" if direction == "on" else "等待关机条件"
            return
        now = time.monotonic()
        if self.auto_since is None:
            self.auto_since = now
        duration = self.auto_params[direction + "_minutes"] * 60
        self.auto_status = f"{'开机' if direction == 'on' else '关机'}计时 {int(now - self.auto_since)}/{int(duration)} 秒"
        if now - self.auto_since < duration:
            return
        self.auto_busy = True
        self.auto_since = None
        try:
            if direction == "off":
                self.disable("自动达标关机")
            domain = self.spec.power.split('.')[0]
            await asyncio.wait_for(self.hass.services.async_call(domain, "turn_" + direction,
                {"entity_id": self.spec.power}, blocking=True), timeout=12)
            for _ in range(25):
                state = self.hass.states.get(self.spec.power)
                if state and state.state == direction:
                    break
                await asyncio.sleep(.2)
            else:
                raise HomeAssistantError("自动电源命令确认超时")
            if direction == "on":
                self.observe = False
                await self.persist()
                await self.set_enabled(True)
            self.auto_status = "已自动启动实控" if direction == "on" else "已自动关机"
        except Exception as err:
            self.auto_status = f"自动操作失败：{err}；重新计时"
            _LOGGER.exception("Automatic power control failed")
        finally:
            self.auto_busy = False
            self.publish()

    async def load(self):
        data = await self.store.async_load()
        if not data:
            return
        try:
            self.params = validated(data.get("params", DEFAULTS))
            for name in self.profiles:
                if name in data.get("profiles", {}):
                    self.profiles[name] = validated(data["profiles"][name])
            if data.get("profile") in self.profiles:
                self.profile = data["profile"]
            self.observe = bool(data.get("observe", True))
            for key, value in data.get("auto_params", {}).items():
                if key in self.auto_params and math.isfinite(float(value)) and (1 if key.endswith("minutes") else 0) <= float(value) <= (1440 if key.endswith("minutes") else 500):
                    self.auto_params[key] = float(value)
            self.notice = "已恢复参数与参数组；控制默认关闭"
        except (TypeError, ValueError):
            _LOGGER.exception("Invalid stored control parameters; using safe defaults")
            self.params = dict(DEFAULTS)

    async def persist(self):
        async with self.storage_lock:
            await self.store.async_save({"params": dict(self.params),
                                         "profiles": deepcopy(self.profiles),
                                         "profile": self.profile, "observe": self.observe,
                                         "auto_params": dict(self.auto_params)})

    @callback
    def publish(self):
        for listener in tuple(self.listeners):
            listener()

    def current_level(self):
        state = self.hass.states.get(self.spec.level)
        return self.spec.options.index(state.state) + 1 if state and state.state in self.spec.options else None

    def current_mode(self):
        state = self.hass.states.get(self.spec.mode)
        if not state:
            return None
        return state.attributes.get("preset_mode") if self.spec.mode_is_fan else state.state

    async def set_favorite(self):
        if self.spec.mode_is_fan:
            await self.command("fan", "set_preset_mode", self.spec.mode,
                               {"preset_mode": self.spec.favorite}, self.spec.favorite)
        else:
            await self.command("select", "select_option", self.spec.mode,
                               {"option": self.spec.favorite}, self.spec.favorite)

    def disable(self, reason):
        self.enabled = False
        self.active_real = False
        self.expected.clear()
        self.saturated_since = None
        self.engine.reset_control(self.current_level() or 1)
        self.status = reason
        self.publish()

    async def set_param(self, key, value):
        values = validated({**self.params, key: value})
        self.params = values
        self.engine.reset_control(self.current_level() or 1)
        self.report_stamp = None
        self.notice = "当前参数已自动保存；可再保存到所选参数组"
        await self.persist()
        self.publish()

    async def choose_profile(self, name):
        if name not in self.profiles:
            raise HomeAssistantError("未知参数组")
        self.profile = name
        self.notice = f"已选择「{name}」；点击载入应用，或点击保存覆盖该组"
        await self.persist()
        self.publish()

    async def profile_action(self, action):
        if action == "save":
            self.profiles[self.profile] = dict(self.params)
            self.notice = f"已保存当前参数到「{self.profile}」"
        elif action == "load":
            self.params = dict(self.profiles[self.profile])
            self.engine.reset_control(self.current_level() or 1)
            self.report_stamp = None
            self.notice = f"已载入「{self.profile}」全部参数"
        await self.persist()
        self.publish()

    async def set_observe(self, value):
        self.disable("模式已切换；请重新启用控制")
        self.observe = value
        await self.persist()
        self.publish()

    async def set_enabled(self, value):
        if not value:
            self.disable("已关闭；设备保持当前状态")
            return
        async with self.lock:
            current = self.current_level()
            problem = self.problem()
            if problem or current is None:
                self.disable(problem or "15 档状态无效")
                raise HomeAssistantError(self.status)
            self.expected.clear()
            self.engine.reset_control(current)
            self.virtual_level = current
            self.report_stamp = None
            self.last_step = time.monotonic()
            self.retry_after = 0
            self.failures = 0
            self.enabled = True
            self.active_real = not self.observe
            if not self.observe:
                if self.current_mode() != self.spec.favorite:
                    # The favorite configuration is already the confirmed current option.
                    # Switching modes can still expose a firmware-specific transient.
                    try:
                        await self.set_favorite()
                    except Exception as err:
                        self.disable(f"接管失败：{err}")
                        raise HomeAssistantError(self.status) from err
            self.status = "观察模式运行中" if self.observe else "平滑控制已启用"
            self.publish()
        await self.tick()

    def reading(self):
        pm = self.hass.states.get(self.spec.pm)
        try:
            if not pm or time.time() - pm.last_reported.timestamp() > self.params["report_timeout"]:
                return None, None
            value = float(pm.state)
            if not math.isfinite(value) or not 0 <= value <= 1000:
                return None, None
            return value, ("ha", pm.last_reported)
        except (ValueError, TypeError):
            return None, None

    def problem(self):
        pm = self.hass.states.get(self.spec.pm)
        fan = self.hass.states.get(self.spec.power)
        level = self.hass.states.get(self.spec.level)
        if not fan or fan.state != "on":
            return "设备未开启/不可用；不会自动开机"
        if not level or list(self.spec.options) != level.attributes.get("options", []):
            return "15 档选项不完整或不可用"
        mode = self.hass.states.get(self.spec.mode)
        choices = mode.attributes.get("preset_modes" if self.spec.mode_is_fan else "options", []) if mode else []
        if not mode or self.current_mode() in (None, "unknown", "unavailable") or self.spec.favorite not in choices:
            return "工作模式不可用或缺少最爱选项"
        if self.reading()[0] is None:
            return "PM2.5 读数无效或上报超时；暂停调档"
        fault = self.hass.states.get(self.spec.fault)
        if not fault or fault.state not in self.spec.clear_faults:
            return "故障状态异常或不可用；暂停调档"
        return None

    @callback
    def device_changed(self, event):
        if not self.enabled or not self.active_real:
            return
        entity = event.data["entity_id"]
        if entity not in (self.spec.power, self.spec.mode, self.spec.level):
            return
        state = event.data.get("new_state")
        if not state or state.state in ("unknown", "unavailable"):
            self.disable("设备状态不可用；控制已停止")
            return
        if entity == self.spec.power and state.state != "on":
            self.disable("用户关机或设备停止；控制已退出")
            return
        if entity == self.spec.power and entity != self.spec.mode:
            return
        if entity == self.spec.mode:
            value = state.attributes.get("preset_mode") if self.spec.mode_is_fan else state.state
        else:
            value = state.state
        expected = self.expected.get(entity)
        if expected and time.monotonic() <= expected[1] and value == expected[0]:
            return
        if entity == self.spec.mode and value == self.spec.favorite:
            return
        # Attribute-only changes do not count as manual level changes.
        old = event.data.get("old_state")
        if entity == self.spec.level and old and old.state == state.state:
            return
        self.disable("检测到外部调档/模式变化；手动接管，控制已退出")

    async def command(self, domain, service, entity, data, expected):
        self.expected[entity] = (expected, time.monotonic() + 20)
        self.command_count += 1
        self.last_command = f"{domain}.{service} → {expected}"
        await asyncio.wait_for(self.hass.services.async_call(
            domain, service, {"entity_id": entity, **data}, blocking=True), timeout=12)
        # Wait for HA entity confirmation, never for physical RPM confirmation.
        for _ in range(25):
            if not self.enabled:
                raise HomeAssistantError("控制已停止")
            state = self.hass.states.get(entity)
            value = state.attributes.get("preset_mode") if state and entity == self.spec.mode and self.spec.mode_is_fan else (
                state.state if state else None)
            if value == expected:
                return
            await asyncio.sleep(.2)
        raise HomeAssistantError("实体状态确认超时；物理转速未确认")

    async def tick(self, _now=None):
        if self.lock.locked():
            return
        await self.auto_tick()
        async with self.lock:
            try:
                await self._tick()
            except Exception:
                _LOGGER.exception("Unexpected controller error")
                self.disable("控制器异常；已停止，请查看 HA 日志")

    async def _tick(self):
        now = time.monotonic()
        current = self.current_level()
        problem = self.problem()
        if problem or current is None:
            self.engine.reset_control(current or 1)
            self.report_stamp = None
            self.last_step = now
            self.status = problem or "15 档状态无效"
            self.last_problem = self.status
            self.publish()
            return
        if self.last_problem:
            self.last_problem = None
            if not self.enabled:
                self.status = "已关闭；参数已保留"
        if self.enabled and not self.observe:
            if self.current_mode() != self.spec.favorite:
                self.disable("设备已离开最爱模式；控制已退出")
                return
        raw, stamp = self.reading()
        if self.observe and self.enabled:
            current = self.virtual_level or current
        if stamp != self.report_stamp:
            # Different reports, not repeated polling of the same state.
            self.engine.sample(raw, now, self.params, current)
            self.report_stamp = stamp
        if not self.enabled:
            self.publish()
            return
        if now < self.retry_after:
            self.status = f"命令失败，退避等待（{self.failures}/3）"
            self.publish()
            return
        # Recompute ceiling even when no new report arrived.
        self.engine.requested = min(self.engine.requested, int(self.params["max_level"]))
        desired = self.engine.requested
        step = next_level(current, desired, now - self.last_step, self.params)
        if step != current:
            if self.observe:
                self.virtual_level = step
            else:
                try:
                    await self.command("select", "select_option", self.spec.level,
                                       {"option": self.spec.options[step - 1]}, self.spec.options[step - 1])
                    if not self.enabled:
                        return
                    self.failures = 0
                except Exception as err:
                    self.failures += 1
                    # Timed-out commands may still arrive: stop rather than retry stale work.
                    if self.failures >= 3 or isinstance(err, (TimeoutError, HomeAssistantError)):
                        self.disable(f"命令失败，已停止：{err}")
                    else:
                        self.retry_after = time.monotonic() + 30 * self.failures
                        self.status = f"命令失败，稍后重新核对：{err}"
                        self.publish()
                    return
            self.last_step = time.monotonic()
        prefix = "观察 · " if self.observe else "实控 · "
        if desired == 1:
            self.status = prefix + ("达标，逐档降至最低" if current > 1 else "达标/死区，最低档")
        elif desired > current:
            self.status = prefix + "逐档升速"
        elif desired < current:
            self.status = prefix + "逐档降速"
        else:
            self.status = prefix + "维持档位"
        if current >= self.params["max_level"] and raw > self.params["target"] + self.params["deadband"]:
            self.saturated_since = self.saturated_since or now
            if now - self.saturated_since > 1800:
                self.status = prefix + "能力受限：最高允许档持续 30 分钟仍高于目标"
        else:
            self.saturated_since = None
        self.publish()

