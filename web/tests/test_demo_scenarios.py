"""Tests for demo scenario presets."""
from __future__ import annotations

import os
import sys
import unittest

_WEB_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _WEB_ROOT not in sys.path:
    sys.path.insert(0, _WEB_ROOT)

from demo_scenarios import list_demo_scenarios, scenario_by_id  # noqa: E402
from failure_modes import classify_failure_mode, classify_failure_modes  # noqa: E402


class TestDemoScenarios(unittest.TestCase):
    def test_scenario_count(self) -> None:
        self.assertGreaterEqual(len(list_demo_scenarios()), 6)

    def test_pod_meltdown_modes(self) -> None:
        sc = scenario_by_id("pod_meltdown")
        self.assertIsNotNone(sc)
        assert sc is not None
        self.assertIn("crash", sc.modes)
        self.assertIn("oom", sc.modes)

    def test_dns_scenario_classifies(self) -> None:
        sc = scenario_by_id("dns_delay_chaos")
        assert sc is not None
        modes = classify_failure_modes(sc.prompt)
        self.assertIn("dns_failure", modes)

    def test_dns_plain_english(self) -> None:
        self.assertEqual(classify_failure_mode("DNS failure on nginx"), "dns_failure")


if __name__ == "__main__":
    unittest.main()
