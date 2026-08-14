"""Tests for K8s failure mode catalog."""
from __future__ import annotations

import os
import sys
import unittest

_WEB_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _WEB_ROOT not in sys.path:
    sys.path.insert(0, _WEB_ROOT)

from failure_modes import (  # noqa: E402
    CHAOS_LAB_MODE_IDS,
    FAILURE_MODES,
    classify_failure_mode,
    classify_failure_modes,
    describe_expected_failure,
    expected_signals_for_modes,
    list_chaos_lab_modes,
    list_demo_scenarios,
    list_failure_modes,
)


class TestFailureCatalog(unittest.TestCase):
    def test_catalog_size(self) -> None:
        modes = list_failure_modes()
        self.assertEqual(len(modes), 40)
        self.assertEqual(len(FAILURE_MODES), 40)

    def test_every_mode_has_blurb_and_signals(self) -> None:
        from failure_modes import FAILURE_MODE_BLURBS, failure_mode_blurb
        for m in FAILURE_MODES:
            self.assertIn(m.id, FAILURE_MODE_BLURBS, msg=m.id)
            self.assertTrue(failure_mode_blurb(m.id), msg=m.id)
            signals = expected_signals_for_modes([m.id])
            self.assertTrue(signals, msg=m.id)

    def test_every_mode_classifies_by_id(self) -> None:
        for m in FAILURE_MODES:
            got = classify_failure_mode(f"simulate {m.id} on fastapi")
            self.assertEqual(got, m.id, msg=m.id)

    def test_chaos_mesh_priority_rules(self) -> None:
        self.assertEqual(classify_failure_mode("http abort chaos 500"), "http_abort")
        self.assertEqual(classify_failure_mode("http delay 5s"), "http_delay")
        self.assertEqual(classify_failure_mode("chaos cpu stress"), "stress_chaos_cpu")
        self.assertEqual(classify_failure_mode("chaos memory stress"), "stress_chaos_memory")

    def test_chaos_lab_includes_privileged_and_readonly(self) -> None:
        lab = list_chaos_lab_modes(chaos_mesh=False)
        ids = {m["id"] for m in lab}
        self.assertIn("privileged", ids)
        self.assertIn("readonly_root", ids)
        self.assertEqual(len(ids), 31)

    def test_failure_catalog_request(self) -> None:
        from failure_modes import is_failure_catalog_request, failure_mode_layman_explain
        self.assertTrue(is_failure_catalog_request("Can you send 40 failure list"))
        self.assertTrue(is_failure_catalog_request("list all failure modes"))
        self.assertFalse(is_failure_catalog_request("What caused this outage on fastapi"))
        _, _, simple = failure_mode_layman_explain("image", "FastAPI")
        self.assertIn("Example", simple)

    def test_categories_present(self) -> None:
        cats = {m["category"] for m in list_failure_modes()}
        for expected in ("pod", "deployment", "network", "node", "storage", "application"):
            self.assertIn(expected, cats)

    def test_multi_failure_classify(self) -> None:
        modes = classify_failure_modes("crash loop and network policy block")
        self.assertIn("crash", modes)
        self.assertIn("network_policy", modes)

    def test_network_policy(self) -> None:
        self.assertEqual(classify_failure_mode("simulate network policy firewall block"), "network_policy")

    def test_bad_rollout(self) -> None:
        self.assertEqual(classify_failure_mode("bad rollout new version crash"), "bad_rollout")

    def test_http_500(self) -> None:
        self.assertEqual(classify_failure_mode("http 500 flood on nginx"), "http_500")

    def test_volume_mount(self) -> None:
        self.assertEqual(classify_failure_mode("push a volume mount failure"), "volume")

    def test_readiness(self) -> None:
        self.assertEqual(classify_failure_mode("simulate readiness probe failure"), "readiness")

    def test_init_container(self) -> None:
        self.assertEqual(classify_failure_mode("break with init container crash"), "init")

    def test_configmap(self) -> None:
        self.assertEqual(classify_failure_mode("missing configmap env"), "configmap")

    def test_liveness(self) -> None:
        self.assertEqual(classify_failure_mode("liveness probe failing"), "liveness")

    def test_chaos_lab_excludes_unverified_mesh_modes(self) -> None:
        lab = list_chaos_lab_modes(chaos_mesh=False)
        ids = {m["id"] for m in lab}
        self.assertIn("crash", ids)
        self.assertIn("volume", ids)
        self.assertNotIn("pod_kill", ids)
        self.assertNotIn("stress_chaos_cpu", ids)

    def test_demo_scenarios_hide_dns_without_mesh(self) -> None:
        shown = {s["id"] for s in list_demo_scenarios(include_chaos_mesh=False)}
        self.assertIn("network_nightmare", shown)
        self.assertNotIn("dns_delay_chaos", shown)

    def test_demo_scenarios_show_dns_with_mesh(self) -> None:
        shown = {s["id"] for s in list_demo_scenarios(include_chaos_mesh=True)}
        self.assertIn("dns_delay_chaos", shown)

    def test_user_plus_vague_agent_desc(self) -> None:
        self.assertEqual(
            classify_failure_mode("simulate volume mount failure on nginx volume mount"),
            "volume",
        )

    def test_runcontainererror_is_bad_command_not_privileged(self) -> None:
        prompt = (
            "apply a RunContainerError / command not found error to both apps"
        )
        self.assertEqual(classify_failure_mode(prompt), "bad_command")

    def test_privileged_explicit(self) -> None:
        self.assertEqual(
            classify_failure_mode("simulate privileged container security policy denial"),
            "privileged",
        )

    def test_bad_command_mode_id(self) -> None:
        self.assertEqual(classify_failure_mode("use bad_command on nginx"), "bad_command")

    def test_crash_loop(self) -> None:
        self.assertEqual(classify_failure_mode("simulate crash loop on fastapi"), "crash")

    def test_cpu_stress_expected_not_scheduling(self) -> None:
        signals = expected_signals_for_modes(["cpu_stress"])
        self.assertIn("demo-cpu-stress", signals)
        self.assertIn("Degraded", signals)
        self.assertNotIn("FailedScheduling", signals)

    def test_argocd_degradation_when_pods_still_ready(self) -> None:
        from unittest.mock import patch
        from failure_modes import _ensure_argocd_visible_degradation, inject_failure_modes

        app = {"deployment": "fastapi", "pod_label": "app=fastapi", "container": "api"}
        with patch("failure_modes._pods_fully_ready", return_value=True), patch(
            "failure_modes._inject_readiness", return_value="readiness broken",
        ) as inj, patch("failure_modes._stamp_injected_modes"), patch(
            "failure_modes.inject_failure_mode", return_value="cpu stress",
        ), patch("failure_modes._kubectl"):
            logs = inject_failure_modes(["cpu_stress"], app)
        inj.assert_called_once()
        self.assertTrue(any("Argo CD" in x for x in logs))

    def test_dns_failure_expected_signals(self) -> None:
        signals = expected_signals_for_modes(["dns_failure"])
        self.assertTrue(any(s in ("DNSChaos", "lookup", "nameserver") for s in signals))

    def test_app_chaos_combo_expected(self) -> None:
        modes = ["http_500", "memory_leak", "cpu_stress"]
        desc = describe_expected_failure(modes)
        self.assertIn("HTTP 500", desc)
        self.assertIn("Memory leak", desc)
        self.assertIn("CPU spike", desc)
        self.assertNotIn("Insufficient cpu", desc)

    def test_chaos_mesh_delay_not_failed_scheduling(self) -> None:
        signals = expected_signals_for_modes(["network_delay"])
        self.assertIn("NetworkChaos", signals)
        self.assertNotIn("FailedScheduling", signals)

    def test_pvc_pending_expected_signals(self) -> None:
        signals = expected_signals_for_modes(["pvc_pending"])
        self.assertTrue(any("PersistentVolumeClaim" in s or "unbound" in s for s in signals))

    def test_storage_storm_combo_expected(self) -> None:
        desc = describe_expected_failure(["pvc_pending", "volume"])
        self.assertIn("PVC pending", desc)
        self.assertIn("Volume mount", desc)
        self.assertNotIn("node-does-not-exist", desc)


if __name__ == "__main__":
    unittest.main()
