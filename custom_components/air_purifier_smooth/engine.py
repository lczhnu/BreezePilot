"""Pure, independently testable control law. All times are monotonic seconds."""
from dataclasses import dataclass
import math

# key: (label, default, minimum, maximum, step, unit)
PARAMS = {
    "target": ("目标 PM2.5", 10, 1, 100, 1, "μg/m³"),
    "kp": ("比例强度 Kp", .35, .05, 2, .05, None),
    "ki": ("积分强度 Ki（每分钟）", 0, 0, .2, .005, None),
    "integral_max": ("积分最大补偿档数", 3, 0, 8, .5, None),
    "deadband": ("浓度死区", 2, 0, 10, 1, "μg/m³"),
    "filter_seconds": ("滤波时间常数", 60, 10, 300, 5, "s"),
    "up_seconds": ("每升一档最短间隔", 30, 15, 300, 5, "s"),
    "down_seconds": ("每降一档最短间隔", 60, 15, 300, 5, "s"),
    "max_level": ("最高允许档位", 15, 1, 15, 1, None),
    "hysteresis": ("档位取整滞回", .3, 0, .45, .05, None),
    "report_timeout": ("上报超时暂停阈值", 900, 120, 3600, 30, "s"),
}
DEFAULTS = {k: v[1] for k, v in PARAMS.items()}
PROFILES = {
    "日常": dict(DEFAULTS),
    "睡眠": {**DEFAULTS, "kp": .2, "max_level": 6, "filter_seconds": 90,
             "up_seconds": 60, "down_seconds": 90},
    "快速": {**DEFAULTS, "kp": .6, "filter_seconds": 30,
             "up_seconds": 15, "down_seconds": 45},
    **{f"自定义 {n}": dict(DEFAULTS) for n in range(1, 4)},
}


def validated(values):
    result = {}
    for key, (_, default, low, high, step, _) in PARAMS.items():
        value = float(values.get(key, default))
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{key} outside {low}..{high}")
        result[key] = round(low + round((value - low) / step) * step, 6)
    return result


@dataclass
class Engine:
    filtered: float | None = None
    sample_time: float | None = None
    integral: float = 0
    requested: int = 1
    high_reports: int = 0
    at_target: bool = True

    def reset_control(self, current=1):
        self.integral = 0
        self.high_reports = 0
        self.requested = current
        self.at_target = True

    def sample(self, concentration, now, params, current):
        if not math.isfinite(concentration) or not 0 <= concentration <= 1000:
            raise ValueError("invalid PM2.5")
        dt = 0 if self.sample_time is None else max(0, now - self.sample_time)
        if self.filtered is None:
            self.filtered = concentration
        else:
            self.filtered += (1 - math.exp(-dt / params["filter_seconds"])) * (
                concentration - self.filtered)
        self.sample_time = now
        target = params["target"]
        # A low raw reading always cancels demand, even while the filter trails.
        if concentration <= target or self.filtered <= target:
            self.integral = 0
            self.high_reports = 0
            self.requested = 1
            self.at_target = True
            return self.requested
        error = max(0, self.filtered - target - params["deadband"])
        self.high_reports = self.high_reports + 1 if error > 0 else 0
        if self.at_target:
            if self.high_reports < 2:
                self.requested = 1
                return self.requested
            self.at_target = False
        proportional = 1 + params["kp"] * error
        # Freeze integration while rate limited or at the output ceiling.
        if (dt > 0 and self.requested <= current
                and proportional + self.integral < params["max_level"]):
            self.integral = min(params["integral_max"], self.integral +
                                params["ki"] * error * min(dt, 60) / 60)
        self.integral = min(self.integral, params["integral_max"])
        output = min(params["max_level"], max(1, proportional + self.integral))
        margin = .5 + params["hysteresis"]
        if output > self.requested + margin or output < self.requested - margin:
            self.requested = max(1, min(int(params["max_level"]),
                                        math.floor(output + .5)))
        # Changing the ceiling never bypasses the final one-step rate limiter.
        self.requested = min(self.requested, int(params["max_level"]))
        return self.requested


def next_level(current, requested, elapsed, params):
    if requested == current:
        return current
    interval = params["up_seconds"] if requested > current else params["down_seconds"]
    if elapsed < interval:
        return current
    return current + (1 if requested > current else -1)
