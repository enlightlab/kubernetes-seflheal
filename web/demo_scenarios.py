"""Re-export — scenarios live in failure_modes.py for single-file deploy overlay."""
from __future__ import annotations

from failure_modes import (  # noqa: F401
    DEMO_SCENARIOS,
    DemoScenario,
    list_demo_scenarios,
    scenario_by_id,
)
