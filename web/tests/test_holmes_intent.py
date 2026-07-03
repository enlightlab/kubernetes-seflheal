"""Regression tests for Holmes intent routing and degraded-mode consistency."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

# web/ is the package root when running from repo root
_WEB_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _WEB_ROOT not in sys.path:
    sys.path.insert(0, _WEB_ROOT)

from actions import (  # noqa: E402
    _classify_chat_action,
    _classify_error_mode,
    _classify_gemini_failure,
    _extract_holmes_answer,
    _intent_query,
    _is_greeting,
    _is_root_cause_question,
    _pick_degraded_fallback,
    holmes_chat,
)


class TestHolmesAnswerExtraction(unittest.TestCase):
    def test_strips_cli_noise_keeps_greeting(self) -> None:
        raw = """Using selected model: gemini/gemini-2.5-flash
Toolset bash: /bin/sh: 1: az: not found
Metrics API not available
error: unknown command "lineage" for "kubectl"
Hello there! How can I help you today?"""
        answer = _extract_holmes_answer(raw)
        self.assertIn("Hello there", answer)
        self.assertNotIn("az: not found", answer)
        self.assertNotIn("Toolset", answer)


class TestIntentNormalization(unittest.TestCase):
    def test_filler_stripped(self) -> None:
        self.assertEqual(_intent_query("what actually broke?"), "what broke?")
        self.assertEqual(_intent_query("what exactly broke?"), "what broke?")

    def test_greeting_variants(self) -> None:
        for q in ("hi", "hiii", "hey!!", "hello there"):
            self.assertTrue(_is_greeting(q), msg=q)
        self.assertFalse(_is_greeting("what broke?"))
        for q in (
            "what actually broke?",
            "what broke?",
            "what happened?",
            "what went wrong?",
            "root cause?",
        ):
            self.assertTrue(_is_root_cause_question(q), msg=q)


class TestGeminiFailureClassification(unittest.TestCase):
    def test_quota(self) -> None:
        info = _classify_gemini_failure(1, "HTTP 429: quota exceeded", "gemini/gemini-2.5-flash")
        self.assertEqual(info["label"], "quota_exceeded")
        self.assertIn("quota", info["user_message"])

    def test_invalid_key(self) -> None:
        info = _classify_gemini_failure(1, "401 API key not valid", "gemini/gemini-2.5-flash")
        self.assertEqual(info["label"], "invalid_api_key")


class TestDegradedRootCauseParity(unittest.TestCase):
    _CTX = {
        "image": "bom.ocir.io/ns/bad:does-not-exist",
        "pod_line": "fastapi-abc 0/1 ErrImagePull",
        "pod_name": "fastapi-abc",
        "pod_reason": "ErrImagePull",
        "pod_message": "manifest unknown",
        "ready_replicas": 0,
        "replicas": 1,
        "pods": [{"name": "fastapi-abc", "reason": "ErrImagePull", "ready": "0/1", "phase": "Pending", "status": "ErrImagePull"}],
    }
    _TREE = {"sync_status": "Synced", "health_status": "Degraded", "tree_summary": ""}
    _ERR = {"user_message": "quota/rate limit exceeded", "label": "quota_exceeded"}

    def _fallback_shape(self, message: str) -> dict:
        body = _pick_degraded_fallback(message, self._CTX, self._TREE, "en", self._ERR)
        return {
            "has_preamble": "Gemini is currently unavailable" in body,
            "has_status": "**Status:**" in body,
            "has_root": "**Root cause:**" in body,
            "has_pod": "fastapi-abc" in body,
        }

    def test_three_phrasings_same_fallback_shape(self) -> None:
        shapes = [
            self._fallback_shape(q)
            for q in ("what actually broke?", "what broke?", "what happened?")
        ]
        self.assertEqual(shapes[0], shapes[1])
        self.assertEqual(shapes[1], shapes[2])

    @patch("actions._holmes_gemini_reply", return_value=(False, "", "HTTP 429 quota"))
    @patch("actions._cluster_reachable", return_value=(True, ""))
    @patch("actions._incident_context")
    @patch("actions._argocd_app_tree")
    @patch("actions._staging_is_healthy", return_value=False)
    @patch("actions.cfg.HOLMES_ENABLED", True)
    def test_holmes_chat_degraded_same_source(
        self,
        _healthy,
        mock_tree,
        mock_ctx,
        _reach,
        _gemini,
    ) -> None:
        mock_ctx.return_value = self._CTX
        mock_tree.return_value = self._TREE

        results = [holmes_chat(q) for q in ("what actually broke?", "what broke?", "what happened?")]
        for r in results:
            self.assertTrue(r["degraded"])
            self.assertEqual(r["source"], "telemetry")
            self.assertIn("Gemini is currently unavailable", r["reply"])
            self.assertIn("**Root cause:**", r["reply"])

        sources = {r["source"] for r in results}
        self.assertEqual(sources, {"telemetry"})
        degraded_flags = {r["degraded"] for r in results}
        self.assertEqual(degraded_flags, {True})


class TestChatActionClassification(unittest.TestCase):
    def test_open_links_plural_fastapi(self) -> None:
        action, target = _classify_chat_action("Open links for fastapi")
        self.assertEqual(action, "links")
        self.assertEqual(target, "fastapi")

    def test_open_links_nginx(self) -> None:
        action, target = _classify_chat_action("Open links for nginx")
        self.assertEqual(action, "links")
        self.assertEqual(target, "nginx")

    def test_stimulate_outrage_typo(self) -> None:
        action, target = _classify_chat_action("can you stimulate a outrage?")
        self.assertEqual(action, "outage")
        self.assertIsNone(target)

    def test_stimulate_outrage_nginx(self) -> None:
        action, target = _classify_chat_action("stimulate an outrage on nginx")
        self.assertEqual(action, "outage")
        self.assertEqual(target, "nginx")

    def test_deploy_both_of_them(self) -> None:
        action, target = _classify_chat_action("deploy both of them")
        self.assertEqual(action, "deploy")
        self.assertEqual(target, "all")

    def test_deploy_them_from_history(self) -> None:
        history = [
            {"role": "user", "content": "how many apps"},
            {"role": "assistant", "content": "You have FastAPI and Nginx demo apps"},
        ]
        action, target = _classify_chat_action("deploy them", history)
        self.assertEqual(action, "deploy")
        self.assertEqual(target, "all")

    def test_self_heal_all(self) -> None:
        action, target = _classify_chat_action("self heal everything")
        self.assertEqual(action, "heal")
        self.assertEqual(target, "all")

    def test_crash_loop_mode(self) -> None:
        self.assertEqual(_classify_error_mode("simulate a crash loop on nginx"), "crash")

    def test_no_default_fastapi_outage(self) -> None:
        action, target = _classify_chat_action("simulate an outrage")
        self.assertEqual(action, "outage")
        self.assertIsNone(target)


if __name__ == "__main__":
    unittest.main()
