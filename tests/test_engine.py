import importlib.util
from pathlib import Path
import sys
import unittest

spec = importlib.util.spec_from_file_location("smooth_engine", Path(__file__).parents[1] / "custom_components/air_purifier_smooth/engine.py")
engine_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = engine_module
spec.loader.exec_module(engine_module)
Engine, DEFAULTS, next_level, validated = (getattr(engine_module, k) for k in ("Engine", "DEFAULTS", "next_level", "validated"))


class ControlLawTests(unittest.TestCase):
    def test_high_pm_requires_two_reports_and_correct_direction(self):
        e = Engine()
        p = dict(DEFAULTS)
        self.assertEqual(e.sample(60, 0, p, 1), 1)
        self.assertEqual(e.sample(60, 15, p, 1), 15)

    def test_low_raw_cancels_filtered_high_and_integral(self):
        e = Engine(filtered=70, sample_time=0, integral=3, requested=15, at_target=False)
        self.assertEqual(e.sample(5, 15, DEFAULTS, 15), 1)
        self.assertGreater(e.filtered, DEFAULTS["target"])
        self.assertEqual(e.integral, 0)

    def test_one_step_rate_limit_in_both_directions(self):
        self.assertEqual(next_level(1, 15, 29, DEFAULTS), 1)
        self.assertEqual(next_level(1, 15, 30, DEFAULTS), 2)
        self.assertEqual(next_level(15, 1, 59, DEFAULTS), 15)
        self.assertEqual(next_level(15, 1, 60, DEFAULTS), 14)

    def test_filter_uses_elapsed_sample_time(self):
        e = Engine()
        e.sample(10, 0, DEFAULTS, 1)
        e.sample(30, 60, DEFAULTS, 1)
        self.assertAlmostEqual(e.filtered, 22.6424, places=3)

    def test_deadband_prevents_chatter(self):
        e = Engine()
        for i, value in enumerate([9, 11, 10, 12, 11, 9, 11]):
            self.assertEqual(e.sample(value, i * 60, DEFAULTS, 1), 1)

    def test_ceiling_and_no_windup_during_ramp(self):
        e = Engine()
        p = {**DEFAULTS, "ki": .1, "max_level": 6}
        for t in range(0, 600, 15):
            e.sample(50, t, p, 1)
        self.assertEqual(e.requested, 6)
        self.assertEqual(e.integral, 0)

    def test_lowering_ceiling_cannot_jump_executed_output(self):
        e = Engine(filtered=80, requested=15, at_target=False)
        p = {**DEFAULTS, "max_level": 4}
        self.assertEqual(e.sample(80, 60, p, 15), 4)
        self.assertEqual(next_level(15, e.requested, 60, p), 14)

    def test_invalid_values_rejected(self):
        for value in [float("nan"), float("inf"), -1, 1001]:
            with self.assertRaises(ValueError):
                Engine().sample(value, 0, DEFAULTS, 1)
        for key, value in [("up_seconds", 0), ("kp", float("nan")), ("target", 101)]:
            with self.assertRaises(ValueError):
                validated({**DEFAULTS, key: value})

    def test_full_ramp_respects_timing_and_eventually_minimum(self):
        e = Engine()
        p = dict(DEFAULTS)
        current, last_step = 1, 0
        for t in range(0, 601, 5):
            if t % 15 == 0:
                e.sample(80, t, p, current)
            new = next_level(current, e.requested, t - last_step, p)
            self.assertLessEqual(abs(new - current), 1)
            if new != current:
                self.assertGreaterEqual(t - last_step, 30)
                current, last_step = new, t
        self.assertEqual(current, 15)
        for t in range(605, 1501, 5):
            if t % 15 == 0:
                e.sample(5, t, p, current)
            new = next_level(current, e.requested, t - last_step, p)
            if new != current:
                self.assertGreaterEqual(t - last_step, 60)
                current, last_step = new, t
        self.assertEqual(current, 1)


if __name__ == "__main__":
    unittest.main()
