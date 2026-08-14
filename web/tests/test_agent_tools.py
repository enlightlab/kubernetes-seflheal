"""Tests for Engineer mode (Gemini agent + tools)."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

_WEB_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _WEB_ROOT not in sys.path:
    sys.path.insert(0, _WEB_ROOT)

from agent_tools import (  # noqa: E402
    effective_chat_mode,
    execute_agent_tool,
    should_use_demo_fast_path,
)


class TestChatModeRouting(unittest.TestCase):
    def test_effective_mode(self) -> None:
        self.assertEqual(effective_chat_mode("agent"), "agent")
        self.assertEqual(effective_chat_mode("demo"), "demo")
        self.assertEqual(effective_chat_mode(None), "agent")
        self.assertEqual(effective_chat_mode("invalid"), "agent")

    def test_agent_mode_uses_demo_for_cluster_mutations(self) -> None:
        self.assertTrue(
            should_use_demo_fast_path("simulate outage on nginx", None, "agent"),
        )
        self.assertTrue(
            should_use_demo_fast_path("Auto-fix any issues in the cluster", None, "agent"),
        )

    def test_agent_mode_open_question_uses_agent(self) -> None:
        self.assertFalse(
            should_use_demo_fast_path("why is my cluster slow?", None, "agent"),
        )

    def test_hybrid_uses_demo_for_explicit_outage(self) -> None:
        self.assertTrue(
            should_use_demo_fast_path("simulate crash loop on nginx", None, "hybrid"),
        )

    def test_hybrid_agent_for_open_question(self) -> None:
        self.assertFalse(
            should_use_demo_fast_path("why is my cluster slow?", None, "hybrid"),
        )

    def test_demo_mode_always_fast_path(self) -> None:
        self.assertTrue(
            should_use_demo_fast_path("what broke?", None, "demo"),
        )


class TestAgentTools(unittest.TestCase):
    @patch("actions._kubectl", return_value=(0, "pod/nginx-demo 1/1 Running"))
    def test_get_resources(self, _mock) -> None:
        r = execute_agent_tool("get_resources", {"resource": "pods"})
        self.assertTrue(r["ok"])
        self.assertIn("nginx-demo", r["output"])

    def test_rejects_unknown_tool(self) -> None:
        r = execute_agent_tool("delete_cluster", {})
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()
