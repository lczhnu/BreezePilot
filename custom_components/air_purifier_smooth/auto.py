"""Independent automatic power thresholds (outside tuning profiles)."""
AUTO_PARAMS = {
    "on_minutes": (1, 1440, 1, "min", "自动开机持续时间"),
    "on_delta": (0, 500, 1, "μg/m³", "自动开机高于目标"),
    "off_minutes": (1, 1440, 1, "min", "自动关机持续时间"),
    "off_delta": (0, 500, 1, "μg/m³", "自动关机低于目标"),
}
