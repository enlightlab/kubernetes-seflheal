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
    _app_has_active_injection,
    _app_is_healthy,
    _apps_status_data,
    _argocd_is_synced_healthy,
    _classify_chat_action,
    _classify_error_mode,
    _classify_gemini_failure,
    _conflicting_instructions_reply,
    _dangerous_operation_reply,
    _deployable_apps_reply,
    _extract_holmes_answer,
    _format_heal_app_summary,
    _has_conflicting_instructions,
    _heal_apply_path_for_app,
    _impossible_deploy_destination,
    _impossible_deploy_reply,
    _inject_summary_item,
    _is_inject_or_outage_intent,
    _inject_commands_reply,
    _intent_query,
    _is_dangerous_operation,
    _is_deployable_apps_question,
    _is_diagnosis_question,
    _is_fix_question,
    _is_greeting,
    _needs_status_disambiguation,
    _outage_target_disambiguation,
    _is_root_cause_question,
    _last_failure_injection_from_history,
    _pause_gitops_for_injection,
    _pick_degraded_fallback,
    _plain_language_explain,
    _kubectl_diagnostic_reply,
    _resolve_app_target,
    _resolve_action_target,
    _scoped_apps_status,
    _unsupported_workload_reply,
    _unsupported_workload_token,
    _wants_repeat_same_outage,
    _wants_inject_commands_explanation,
    _wants_kubectl_check_commands,
    _wants_manual_fix_commands,
    _manual_fix_commands_reply,
    _try_compound_deploy_break,
    _try_curated_info_reply,
    holmes_chat,
)
import config as cfg  # noqa: E402


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
    @patch("actions.cfg.CHAT_ACTIONS_ENABLED", False)
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

    def test_volume_mount_mode(self) -> None:
        self.assertEqual(_classify_error_mode("push a volume mount failure error"), "volume")

    def test_no_default_fastapi_outage(self) -> None:
        action, target = _classify_chat_action("simulate an outrage")
        self.assertEqual(action, "outage")
        self.assertIsNone(target)

    def test_stimulate_outrage_it_from_nginx_history(self) -> None:
        history = [
            {"role": "user", "content": "simulate a failure for nginx"},
            {"role": "assistant", "content": "Simulated ErrImagePull on Nginx Web — pod nginx-demo-abc"},
        ]
        action, target = _classify_chat_action(
            "can you stimulate one more outrage to it?", history,
        )
        self.assertEqual(action, "outage")
        self.assertEqual(target, "nginx")

    def test_one_more_outrage_from_history(self) -> None:
        history = [
            {"role": "user", "content": "break nginx"},
            {"role": "assistant", "content": "Simulated failure on Nginx Web"},
        ]
        action, target = _classify_chat_action("one more outrage please", history)
        self.assertEqual(action, "outage")
        self.assertEqual(target, "nginx")

    def test_chaos_lab_scenario_without_simulate_verb(self) -> None:
        action, target = _classify_chat_action("Crash loop and OOM on fastapi")
        self.assertEqual(action, "outage")
        self.assertEqual(target, "fastapi")

    def test_app_chaos_scenario_prompt(self) -> None:
        action, _ = _classify_chat_action(
            "HTTP 500 and memory leak and CPU stress on fastapi",
        )
        self.assertEqual(action, "outage")


class TestHealManifestPaths(unittest.TestCase):
    def test_fastapi_uses_heal_overlay_when_present(self) -> None:
        app = cfg.demo_app("fastapi")
        path = _heal_apply_path_for_app(app)
        self.assertIsNotNone(path)
        self.assertTrue(path.is_dir())

    def test_nginx_uses_staging_manifest_dir(self) -> None:
        app = cfg.demo_app("nginx")
        path = _heal_apply_path_for_app(app)
        self.assertIsNotNone(path)
        self.assertTrue((path / "deployment.yaml").is_file())


class TestArgocdSyncHealthy(unittest.TestCase):
    def test_synced_healthy_only(self) -> None:
        self.assertTrue(_argocd_is_synced_healthy("Synced/Healthy"))
        self.assertFalse(_argocd_is_synced_healthy("OutOfSync/Healthy"))
        self.assertFalse(_argocd_is_synced_healthy("Synced/Progressing"))
        self.assertFalse(_argocd_is_synced_healthy("Unknown/Unknown"))


class TestInjectionAwareHealth(unittest.TestCase):
    def test_inject_summary_always_outage(self) -> None:
        app = cfg.demo_app("fastapi")
        with patch("actions._app_has_active_injection", return_value=True), patch(
            "actions._pod_detail_for_label",
            return_value={"line": "fastapi-abc 1/1 Running", "container_errors": []},
        ):
            item = _inject_summary_item("fastapi", ["network_policy"], "fastapi-abc 1/1 Running")
        self.assertFalse(item["healthy"])
        self.assertTrue(item["injected"])

    @patch("actions._app_injected_modes", return_value=["network_policy"])
    @patch("actions._app_workloads_exist", return_value=True)
    @patch(
        "actions._pod_detail_for_label",
        return_value={"line": "fastapi-abc 1/1 Running", "container_errors": []},
    )
    @patch("actions._reachable", return_value=True)
    def test_injected_app_not_healthy_even_when_pod_running(
        self, *_mocks: object,
    ) -> None:
        app = cfg.demo_app("fastapi")
        self.assertTrue(_app_has_active_injection(app))
        self.assertFalse(_app_is_healthy(app))

    @patch("actions._app_injected_modes", return_value=["network_policy"])
    @patch("actions._app_workloads_exist", return_value=True)
    @patch(
        "actions._pod_detail_for_label",
        return_value={"line": "fastapi-abc 1/1 Running", "container_errors": []},
    )
    @patch("actions._argocd_status_for_app", return_value="OutOfSync/Progressing")
    def test_apps_status_shows_outage_active_when_injected(self, *_mocks: object) -> None:
        rows = _apps_status_data()
        fastapi = next(r for r in rows if r["id"] == "fastapi")
        self.assertTrue(fastapi["injected"])
        self.assertEqual(fastapi["state"], "Outage active")
        self.assertFalse(fastapi["healthy"])

    @patch("actions._app_has_active_injection", return_value=True)
    def test_heal_summary_shows_outage_when_still_injected(self, *_mocks: object) -> None:
        summary = _format_heal_app_summary("fastapi", {"healthy": True})
        self.assertEqual(summary["status"], "Outage active")
        self.assertEqual(summary["status_key"], "bad")


class TestKubectlDiagnostic(unittest.TestCase):
    def test_cloud_shell_commands_detected(self) -> None:
        self.assertTrue(_wants_kubectl_check_commands(
            "can you give commands to check the excat err in cloud shell?",
        ))

    def test_inject_commands_not_classified_as_outage(self) -> None:
        action, target = _classify_chat_action("which commands you used to inject the error?")
        self.assertEqual(action, "inject_commands")
        self.assertEqual(target, "all")

    def test_inject_commands_wants_explanation(self) -> None:
        self.assertTrue(_wants_inject_commands_explanation(
            "which commands you used to inject the error?",
        ))
        self.assertFalse(_wants_inject_commands_explanation(
            "simulate image pull error on nginx",
        ))

    @patch("actions._app_injected_modes", return_value=["image"])
    def test_inject_commands_reply_shows_set_image(self, _modes: object) -> None:
        reply = _inject_commands_reply("all", None)
        self.assertIn("kubectl set image", reply)
        self.assertIn("deployment/fastapi", reply)
        self.assertIn("deployment/nginx-demo", reply)
        self.assertIn("enlight-lab/injected-modes", reply)
        self.assertNotIn("app=nginx`", reply)

    def test_manual_fix_commands_detected(self) -> None:
        self.assertTrue(_wants_manual_fix_commands(
            "give me the manual commands to fix the error",
        ))
        self.assertFalse(_wants_manual_fix_commands("auto-fix both apps"))

    def test_manual_fix_not_classified_as_outage(self) -> None:
        action, _ = _classify_chat_action("give me commands to fix the error")
        self.assertEqual(action, "manual_fix")

    @patch("actions._app_injected_modes", return_value=["network_policy"])
    def test_manual_fix_network_policy(self, _modes: object) -> None:
        reply = _manual_fix_commands_reply("nginx", None)
        self.assertIn("delete networkpolicy", reply)
        self.assertIn("nginx-demo", reply)
        self.assertIn("app=nginx-demo", reply)
        self.assertIn("deploy/k8s/staging-nginx", reply)

    @patch("actions._app_injected_modes", return_value=["image"])
    def test_manual_fix_image_pull(self, _modes: object) -> None:
        reply = _manual_fix_commands_reply("fastapi", None)
        self.assertIn("kubectl set image deployment/fastapi", reply)
        self.assertIn("demo-pass", reply)
        self.assertIn("deploy/k8s/staging-heal", reply)

    def test_diagnostic_reply_uses_nginx_demo_label(self) -> None:
        reply = _kubectl_diagnostic_reply("nginx")
        self.assertIn("app=nginx-demo", reply)
        self.assertIn("nginx-demo", reply)
        self.assertNotIn("<nginx-pod-name>", reply)
        self.assertIn("POD=$(kubectl get pods", reply)

    def test_diagnostic_reply_covers_both_apps(self) -> None:
        reply = _kubectl_diagnostic_reply("all")
        self.assertIn("FastAPI API", reply)
        self.assertIn("Nginx Web", reply)
        self.assertIn("app=fastapi", reply)
        self.assertIn("app=nginx-demo", reply)


class TestHealAlreadyHealthy(unittest.TestCase):
    @patch("actions._app_workloads_exist", return_value=True)
    @patch("actions._app_is_healthy", return_value=True)
    @patch("actions._app_has_active_injection", return_value=False)
    @patch("actions._apps_status_data")
    def test_already_healthy_reply_all(self, mock_rows, *_mocks: object) -> None:
        mock_rows.return_value = [
            {"id": "fastapi", "label": "FastAPI API", "deployed": True, "state": "Healthy", "pod_line": "fastapi-abc 1/1 Running", "healthy": True},
            {"id": "nginx", "label": "Nginx Web", "deployed": True, "state": "Healthy", "pod_line": "nginx-demo-xyz 1/1 Running", "healthy": True},
        ]
        from actions import _already_healthy_reply
        reply = _already_healthy_reply("all")
        self.assertIsNotNone(reply)
        self.assertIn("already healthy", reply["message"].lower())

    def test_heal_classifies_not_manual_fix_on_healthy_tap(self) -> None:
        action, _ = _classify_chat_action("Are my apps healthy?")
        self.assertEqual(action, "chat")


class TestGitopsPauseOnInject(unittest.TestCase):
    def test_pause_gitops_for_nginx(self) -> None:
        app = cfg.demo_app("nginx")
        with patch("actions._pause_argocd_autosync_named") as pause:
            _pause_gitops_for_injection(app)
            pause.assert_called_once_with(cfg.NGINX_ARGOCD_APP)

    def test_pause_gitops_for_fastapi(self) -> None:
        app = cfg.demo_app("fastapi")
        with patch("actions._pause_argocd_autosync_named") as pause:
            _pause_gitops_for_injection(app)
            pause.assert_called_once_with(cfg.ARGOCD_APP)

    def test_both_apps_target_resolves(self) -> None:
        action, target = _classify_chat_action(
            "Simulate network policy block and port mismatch and high latency on both apps"
        )
        self.assertEqual(action, "outage")
        self.assertEqual(target, "all")


class TestManagerQaFixes(unittest.TestCase):
    def test_deploy_redis_is_unsupported(self) -> None:
        self.assertEqual(_unsupported_workload_token("Deploy Redis"), "redis")
        reply = _unsupported_workload_reply("redis")
        self.assertIn("not available", reply.lower())
        self.assertIn("FastAPI", reply)
        self.assertIn("Nginx", reply)

    def test_deploy_redis_not_resolved_from_history(self) -> None:
        history = [
            {"role": "user", "content": "how many apps"},
            {"role": "assistant", "content": "FastAPI and Nginx demo apps"},
        ]
        action, target = _classify_chat_action("Deploy Redis", history)
        self.assertEqual(action, "deploy")
        self.assertIsNone(target)
        from actions import _resolve_action_target
        self.assertIsNone(_resolve_action_target("deploy", "Deploy Redis", history, target))

    def test_deployable_apps_reply_format(self) -> None:
        self.assertTrue(_is_deployable_apps_question("Which applications can you deploy?"))
        reply = _deployable_apps_reply()
        self.assertIn("- **FastAPI", reply)
        self.assertIn("- **Nginx", reply)
        self.assertEqual(reply.count("- **"), 2)

    def test_api_docs_and_frontend_info(self) -> None:
        api = _try_curated_info_reply("Which application exposes API Docs?")
        self.assertIsNotNone(api)
        self.assertIn("FastAPI", api or "")
        front = _try_curated_info_reply("Which application is the frontend?")
        self.assertIsNotNone(front)
        self.assertIn("Nginx", front or "")

    def test_restart_continuously_classifies_as_outage(self) -> None:
        action, target = _classify_chat_action(
            "Cause the application to restart continuously",
        )
        self.assertEqual(action, "outage")
        self.assertIsNone(target)

    def test_image_pull_shows_separate_error_phases(self) -> None:
        from failure_modes import inject_mode_chips, format_active_failure_headline
        chips = inject_mode_chips(["image"])
        labels = {c["label"] for c in chips}
        self.assertEqual(labels, {"ErrImagePull", "ImagePullBackOff"})
        headline = format_active_failure_headline(
            ["image"], "fastapi-abc 0/1 ImagePullBackOff",
        )
        self.assertIn("ErrImagePull", headline)
        self.assertIn("ImagePullBackOff", headline)
        self.assertNotIn("ErrImagePull / ImagePullBackOff", headline)
        history = [
            {"role": "user", "content": "simulate image pull failure on nginx"},
            {"role": "assistant", "content": "Image pull failure active on Nginx Web"},
        ]
        self.assertTrue(_wants_repeat_same_outage("Inject the same outage again"))
        hist_target, hist_modes = _last_failure_injection_from_history(history)
        self.assertEqual(hist_target, "nginx")
        self.assertEqual(hist_modes, ["image"])

    def test_restart_continuously_classifies_as_crash(self) -> None:
        from failure_modes import classify_failure_mode
        self.assertEqual(
            classify_failure_mode("Cause the application to restart continuously on fastapi"),
            "crash",
        )

    def test_stop_traffic_classifies_readiness(self) -> None:
        from failure_modes import classify_failure_mode
        self.assertEqual(
            classify_failure_mode("Stop the application from receiving traffic without crashing it"),
            "readiness",
        )

    def test_scoped_apps_status_keeps_only_target_app(self) -> None:
        rows = [
            {"id": "fastapi", "label": "FastAPI API"},
            {"id": "nginx", "label": "Nginx Web"},
        ]
        self.assertEqual(_scoped_apps_status(rows, "fastapi"), [rows[0]])
        self.assertEqual(_scoped_apps_status(rows, "nginx"), [rows[1]])
        self.assertEqual(_scoped_apps_status(rows, "all"), rows)

    def test_outage_questions_are_not_failure_catalog(self) -> None:
        from failure_modes import is_failure_catalog_request
        self.assertFalse(is_failure_catalog_request("What caused this outage on fastapi"))
        self.assertFalse(is_failure_catalog_request("What is the root cause of this failure on fastapi"))
        self.assertFalse(is_failure_catalog_request("Why is the FastAPI service not working?"))
        self.assertTrue(is_failure_catalog_request("Can you send 40 failure list"))

    def test_dangerous_cluster_delete_is_blocked(self) -> None:
        self.assertTrue(_is_dangerous_operation("Delete the cluster"))
        self.assertTrue(_is_dangerous_operation("Delete every namespace"))
        self.assertIn("cannot delete", _dangerous_operation_reply().lower())

    def test_conflicting_deploy_instructions(self) -> None:
        self.assertTrue(_has_conflicting_instructions("Deploy FastAPI and don't deploy FastAPI"))
        self.assertIn("conflicting", _conflicting_instructions_reply().lower())

    def test_aws_deploy_is_impossible(self) -> None:
        self.assertEqual(_impossible_deploy_destination("Deploy FastAPI to AWS"), "aws")
        self.assertIn("cannot deploy", _impossible_deploy_reply("aws").lower())

    def test_compound_deploy_fastapi_break_nginx(self) -> None:
        self.assertEqual(_try_compound_deploy_break("Deploy FastAPI and break nginx"), ("fastapi", "nginx"))

    def test_explain_root_cause_is_not_fix_question(self) -> None:
        self.assertFalse(_is_fix_question("Explain the root cause in simple English"))

    def test_scoped_diagnosis_targets_fastapi_only(self) -> None:
        self.assertTrue(_is_diagnosis_question("What caused this outage on fastapi"))
        self.assertEqual(_resolve_app_target("What caused this outage on fastapitapi"), "fastapi")

    def test_startup_probe_layman_not_crashloop(self) -> None:
        from failure_modes import failure_mode_layman_explain
        headline, root, simple = failure_mode_layman_explain("startup", "FastAPI API")
        self.assertIn("startup", root.lower())
        self.assertNotEqual(root, "CrashLoopBackOff")
        self.assertIn("Example", simple)
        ctx = {
            "replicas": 1,
            "ready_replicas": 0,
            "image": "good:tag",
            "pod_reason": "CrashLoopBackOff",
            "pod_message": "back-off restarting",
            "pod_line": "fastapi-abc · 0/1 · CrashLoopBackOff",
            "app_label": "FastAPI API",
            "deployment": "fastapi",
            "injected_modes": ["startup"],
            "injected_summary": "Startup probe failure",
            "events": [],
        }
        _, _, root_cause, simple_para = _plain_language_explain(ctx)
        self.assertIn("Startup probe", root_cause)
        self.assertIn("startup probe", simple_para.lower())

    def test_status_probe_inject_not_status_disambiguation(self) -> None:
        self.assertTrue(_is_inject_or_outage_intent("stimulate status probe failure"))
        self.assertFalse(_needs_status_disambiguation("stimulate status probe failure"))

    def test_crashloop_without_app_does_not_default_both(self) -> None:
        history = [
            {"role": "user", "content": "show status for fastapi and nginx"},
            {"role": "assistant", "content": "FastAPI and Nginx status"},
        ]
        action, target = _classify_chat_action("stimulate crashloopbackoof", history)
        self.assertEqual(action, "outage")
        resolved = _resolve_action_target(action, "stimulate crashloopbackoof", history, target)
        self.assertIsNone(resolved)
        msg, choices = _outage_target_disambiguation("stimulate crashloopbackoof")
        self.assertIn("crash loop", msg.lower())
        self.assertEqual(len(choices), 3)
        self.assertIn("fastapi", choices[0]["prompt"].lower())


if __name__ == "__main__":
    unittest.main()
