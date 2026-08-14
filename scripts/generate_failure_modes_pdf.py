#!/usr/bin/env python3
"""Generate a PDF guide of all 40 Self-Heal failure modes for QA testing."""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from fpdf import FPDF
except ImportError:
    print("Installing fpdf2...", file=sys.stderr)
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "fpdf2", "-q"])
    from fpdf import FPDF

FAILURES: list[dict[str, str]] = [
    {
        "num": "1", "id": "init", "name": "InitContainerCrashLoopBackOff",
        "category": "Pod", "prompt": "Simulate init container crash on fastapi",
        "what": "The init container (runs before the main app) keeps crashing.",
        "pod": "Pod stays Not Ready. Status shows Init:CrashLoopBackOff or Init:Error.",
        "app": "App never starts — users see 502 or connection errors.",
        "cluster": "Deployment looks unhealthy; Argo CD shows Degraded. Other apps unaffected.",
    },
    {
        "num": "2", "id": "startup", "name": "Startup probe failure",
        "category": "Pod", "prompt": "Simulate startup probe failure on fastapi",
        "what": "Kubernetes startup health check never passes, so the pod is killed and restarted.",
        "pod": "Pod cycles through restarts; may show Running but not Ready.",
        "app": "Intermittent 502 or timeouts while probes fail.",
        "cluster": "Replica count may flap; Argo CD shows Progressing or Degraded.",
    },
    {
        "num": "3", "id": "readiness", "name": "Readiness probe failure",
        "category": "Pod", "prompt": "Simulate readiness probe failure on nginx",
        "what": "Pod runs but Kubernetes marks it Not Ready — no traffic sent to it.",
        "pod": "Shows 0/1 Ready or Running but excluded from Service endpoints.",
        "app": "Browser shows 502 — load balancer has no healthy backend.",
        "cluster": "Service endpoints empty for that app; cluster itself is fine.",
    },
    {
        "num": "4", "id": "liveness", "name": "Liveness probe failure / restart loop",
        "category": "Pod", "prompt": "Simulate liveness probe failure on fastapi",
        "what": "Liveness check fails, so Kubernetes keeps restarting the container.",
        "pod": "CrashLoopBackOff or frequent restarts; pod age resets often.",
        "app": "502 or brief outages while container restarts.",
        "cluster": "One workload degraded; rest of namespace normal.",
    },
    {
        "num": "5", "id": "bad_command", "name": "RunContainerError / command not found",
        "category": "Pod", "prompt": "Simulate bad command / RunContainerError on fastapi",
        "what": "Container tries to run a command that does not exist or cannot execute.",
        "pod": "Status RunContainerError or CrashLoopBackOff immediately on start.",
        "app": "App never serves traffic — 502 at the URL.",
        "cluster": "Deployment stuck with failing pods; easy to spot in kubectl describe.",
    },
    {
        "num": "6", "id": "privileged", "name": "Security policy / privileged denied",
        "category": "Pod", "prompt": "Simulate privileged container / security policy block on fastapi",
        "what": "Pod spec requests privileged mode that cluster policy blocks.",
        "pod": "Pod may stay Pending or fail CreateContainerConfigError.",
        "app": "No healthy pods — 502 or site down for that app.",
        "cluster": "Shows policy enforcement; good demo for Pod Security / admission control.",
    },
    {
        "num": "7", "id": "oom", "name": "OOMKilled (memory limit)",
        "category": "Pod", "prompt": "Simulate OOM / out of memory on fastapi",
        "what": "Container uses more RAM than its limit; Linux kills it (OOMKilled).",
        "pod": "Last state OOMKilled; restarts in a loop if memory pressure continues.",
        "app": "Random 502s or slow responses before each kill.",
        "cluster": "Node memory fine; only that container’s limit matters.",
    },
    {
        "num": "8", "id": "cpu_throttle", "name": "CPU throttling (extreme CPU limit)",
        "category": "Pod", "prompt": "Simulate CPU throttling on fastapi",
        "what": "CPU limit set so low the app can barely run.",
        "pod": "Pod stays Running but extremely slow; high throttling in metrics.",
        "app": "Very slow pages or timeouts; may look like hang.",
        "cluster": "Pod not crashed but SLA broken; Argo may still show Healthy.",
    },
    {
        "num": "9", "id": "crash", "name": "CrashLoopBackOff",
        "category": "Pod", "prompt": "Trigger CrashLoopBackOff for the FastAPI workload",
        "what": "Main container exits with error; Kubernetes backs off and retries.",
        "pod": "CrashLoopBackOff — classic restart loop.",
        "app": "502 Bad Gateway; app URL fails until fixed.",
        "cluster": "Deployment Degraded; classic demo failure.",
    },
    {
        "num": "10", "id": "image", "name": "ErrImagePull / ImagePullBackOff",
        "category": "Pod", "prompt": "Simulate image pull failure on fastapi",
        "what": "Pod references a container image that cannot be pulled (bad tag/registry).",
        "pod": "ErrImagePull then ImagePullBackOff; pod never becomes Ready.",
        "app": "502 — no working container.",
        "cluster": "Common real-world failure; Argo CD shows Degraded.",
    },
    {
        "num": "11", "id": "deadlock", "name": "Deadlock / hang (liveness kills pod)",
        "category": "Pod", "prompt": "Simulate deadlock / hung app on fastapi",
        "what": "App process hangs; liveness probe fails and restarts it.",
        "pod": "Running then restart loop when probe times out.",
        "app": "Freezes or 502 during hang/restart cycles.",
        "cluster": "Looks like flaky app until you inspect probes.",
    },
    {
        "num": "12", "id": "instant", "name": "Zero replicas / scaled to zero",
        "category": "Deployment", "prompt": "Scale fastapi to zero replicas / instant outage",
        "what": "Deployment replica count set to 0 — no pods running.",
        "pod": "No pods exist for that deployment.",
        "app": "Complete outage — 502 or connection refused.",
        "cluster": "Cleanest total outage; Argo shows missing pods.",
    },
    {
        "num": "13", "id": "configmap", "name": "Wrong / missing ConfigMap",
        "category": "Deployment", "prompt": "Simulate missing ConfigMap on fastapi",
        "what": "Pod references a ConfigMap that does not exist or wrong keys.",
        "pod": "CreateContainerConfigError or CrashLoopBackOff on start.",
        "app": "App does not start — 502.",
        "cluster": "Config drift demo; fix restores good manifest.",
    },
    {
        "num": "14", "id": "secret_env", "name": "Wrong / missing Secret",
        "category": "Deployment", "prompt": "Simulate missing Secret env on fastapi",
        "what": "Environment variables from a Secret that is missing or invalid.",
        "pod": "Fails to create container or crashes on missing credentials.",
        "app": "App down or errors on startup.",
        "cluster": "Typical misconfiguration after secret rotation.",
    },
    {
        "num": "15", "id": "bad_rollout", "name": "Bad rollout (new pods crash)",
        "category": "Deployment", "prompt": "Simulate bad rollout on fastapi",
        "what": "New deployment revision has a broken image or config; new pods fail.",
        "pod": "Mix of old (maybe OK) and new crashing pods during rollout.",
        "app": "Partial or full outage depending on rollout strategy.",
        "cluster": "Argo CD Progressing/Degraded; rollback fixes it.",
    },
    {
        "num": "16", "id": "rollout_stuck", "name": "Rollout stuck / paused",
        "category": "Deployment", "prompt": "Simulate rollout stuck / paused on fastapi",
        "what": "Deployment paused mid-update — old and new pods coexist stuck.",
        "pod": "Unexpected pod generations; rollout does not finish.",
        "app": "Unpredictable behavior or mixed versions.",
        "cluster": "GitOps shows out-of-sync or paused rollout.",
    },
    {
        "num": "17", "id": "service_selector", "name": "Service unreachable (selector mismatch)",
        "category": "Network", "prompt": "Simulate service selector mismatch on nginx",
        "what": "Service label selector does not match any pod labels.",
        "pod": "Pods may be Running and healthy.",
        "app": "502 anyway — Service has zero endpoints.",
        "cluster": "Classic networking misconfig; pod looks fine in isolation.",
    },
    {
        "num": "18", "id": "port_mismatch", "name": "Port mismatch (wrong targetPort)",
        "category": "Network", "prompt": "Simulate port mismatch on nginx",
        "what": "Service forwards to wrong container port.",
        "pod": "Pod Running; app listens on correct port internally.",
        "app": "502 or connection reset from ingress/load balancer.",
        "cluster": "Traffic never reaches the app process.",
    },
    {
        "num": "19", "id": "network_policy", "name": "NetworkPolicy blocks all traffic",
        "category": "Network", "prompt": "Simulate network policy block on fastapi",
        "what": "NetworkPolicy denies ingress/egress to the pod.",
        "pod": "Pod often still Running 1/1.",
        "app": "502 external; health checks from other namespaces may fail.",
        "cluster": "Teaches zero-trust / firewall-style rules in Kubernetes.",
    },
    {
        "num": "20", "id": "ingress_bad", "name": "Ingress misconfiguration",
        "category": "Network", "prompt": "Simulate ingress misconfiguration on nginx",
        "what": "Ingress points to wrong service or path.",
        "pod": "Backend pod may be healthy.",
        "app": "404 or 502 at public URL despite healthy pod.",
        "cluster": "Edge routing broken; internal kubectl may still work.",
    },
    {
        "num": "21", "id": "pending", "name": "Pending forever (impossible nodeSelector)",
        "category": "Node", "prompt": "Simulate pending / unschedulable pod on fastapi",
        "what": "Pod cannot be placed on any node (impossible nodeSelector).",
        "pod": "Stays Pending forever; events say failed scheduling.",
        "app": "Outage — no running instance.",
        "cluster": "Scheduler cannot find a node; cluster capacity is not the issue.",
    },
    {
        "num": "22", "id": "affinity", "name": "Node affinity / unschedulable",
        "category": "Node", "prompt": "Simulate node affinity failure on fastapi",
        "what": "Affinity rules require a node label that does not exist.",
        "pod": "Pending; scheduling events mention affinity.",
        "app": "No pods — app unavailable.",
        "cluster": "Demonstrates placement constraints.",
    },
    {
        "num": "23", "id": "toleration", "name": "Taint toleration mismatch",
        "category": "Node", "prompt": "Simulate taint toleration mismatch on fastapi",
        "what": "Pod lacks toleration for node taints so it cannot run.",
        "pod": "Pending; events mention taints/tolerations.",
        "app": "App not deployed to any node.",
        "cluster": "Common when dedicated nodes use taints.",
    },
    {
        "num": "24", "id": "volume", "name": "Volume mount failure",
        "category": "Storage", "prompt": "Simulate volume mount failure on fastapi",
        "what": "Pod mounts a volume path or name that does not exist.",
        "pod": "CreateContainerConfigError or FailedMount; may not start.",
        "app": "App down until volume fixed.",
        "cluster": "Storage/config issue isolated to that workload.",
    },
    {
        "num": "25", "id": "hostpath", "name": "HostPath volume failure",
        "category": "Storage", "prompt": "Simulate HostPath volume failure on fastapi",
        "what": "HostPath points to missing or wrong path on the node.",
        "pod": "FailedMount or crash reading files.",
        "app": "Startup failure or missing data.",
        "cluster": "Node-local storage risk demo.",
    },
    {
        "num": "26", "id": "pvc_pending", "name": "PVC pending (volume never binds)",
        "category": "Storage", "prompt": "Simulate PVC pending on fastapi",
        "what": "PersistentVolumeClaim never binds to a volume.",
        "pod": "Pod Pending waiting for PVC.",
        "app": "App never starts.",
        "cluster": "Storage class / capacity issue pattern.",
    },
    {
        "num": "27", "id": "readonly_root", "name": "Read-only volume / filesystem",
        "category": "Storage", "prompt": "Simulate read-only filesystem on fastapi",
        "what": "App cannot write to disk (read-only root or mount).",
        "pod": "May crash on write or log permission denied.",
        "app": "Errors on requests needing temp files or caches.",
        "cluster": "Security-hardened images sometimes hit this.",
    },
    {
        "num": "28", "id": "memory_leak", "name": "Memory leak simulation",
        "category": "Application", "prompt": "Simulate memory leak on fastapi",
        "what": "App gradually consumes memory until OOM or slowdown.",
        "pod": "Memory rises; may end in OOMKilled.",
        "app": "Slow then failing responses.",
        "cluster": "Application-level chaos; infra looks OK until limit hit.",
    },
    {
        "num": "29", "id": "cpu_stress", "name": "CPU spike / stress",
        "category": "Application", "prompt": "Simulate CPU stress on fastapi",
        "what": "Workload burns CPU intentionally (stress sidecar or script).",
        "pod": "High CPU usage; may stay Running.",
        "app": "Very slow API responses.",
        "cluster": "No crash required — performance degradation demo.",
    },
    {
        "num": "30", "id": "http_500", "name": "HTTP 500 errors",
        "category": "Application", "prompt": "Simulate HTTP 500 errors on fastapi",
        "what": "App returns Internal Server Error for requests.",
        "pod": "Pod often still Running and Ready.",
        "app": "Users see 500 in browser or API client.",
        "cluster": "Service-level failure — good for monitoring demos.",
    },
    {
        "num": "31", "id": "high_latency", "name": "High latency / slow responses",
        "category": "Application", "prompt": "Simulate high latency on nginx",
        "what": "Every response delayed several seconds.",
        "pod": "Pod healthy; probes may still pass.",
        "app": "Pages load very slowly or timeout.",
        "cluster": "SLO/latency alert scenario without pod crash.",
    },
    {
        "num": "32", "id": "dns_failure", "name": "DNS failure (Chaos Mesh)",
        "category": "Network / Chaos Mesh", "prompt": "Simulate DNS failure on fastapi",
        "what": "Pod cannot resolve service names (CoreDNS broken for that pod).",
        "pod": "Running but outbound calls fail.",
        "app": "Errors calling databases or other services.",
        "cluster": "Requires Chaos Mesh; falls back if not installed.",
    },
    {
        "num": "33", "id": "network_delay", "name": "Network latency injection (Chaos Mesh)",
        "category": "Network / Chaos Mesh", "prompt": "Simulate network delay on fastapi",
        "what": "Artificial delay on network packets to/from pod.",
        "pod": "Pod Running; network path slowed.",
        "app": "Slow or timing-out requests.",
        "cluster": "Chaos Mesh experiment; scoped to target pods.",
    },
    {
        "num": "34", "id": "network_loss", "name": "Packet loss (Chaos Mesh)",
        "category": "Network / Chaos Mesh", "prompt": "Simulate packet loss on fastapi",
        "what": "Percentage of packets dropped on the network path.",
        "pod": "Running; flaky connectivity.",
        "app": "Intermittent 502, retries, broken APIs.",
        "cluster": "Chaos Mesh; simulates bad network conditions.",
    },
    {
        "num": "35", "id": "network_partition", "name": "Network partition (Chaos Mesh)",
        "category": "Network / Chaos Mesh", "prompt": "Simulate network partition on fastapi",
        "what": "Pod isolated from other services as if network split.",
        "pod": "Running but unreachable from peers.",
        "app": "Split-brain / isolation symptoms.",
        "cluster": "Advanced chaos; tests resilience patterns.",
    },
    {
        "num": "36", "id": "pod_kill", "name": "Random pod kill storm (Chaos Mesh)",
        "category": "Pod / Chaos Mesh", "prompt": "Simulate pod kill chaos on fastapi",
        "what": "Chaos Mesh randomly kills pods on a schedule.",
        "pod": "Pods terminate and recreate repeatedly.",
        "app": "Brief outages and 502 during kills.",
        "cluster": "Tests restart policy and replica recovery.",
    },
    {
        "num": "37", "id": "http_abort", "name": "HTTP 500 abort (Chaos Mesh)",
        "category": "Application / Chaos Mesh", "prompt": "Simulate HTTP abort 500 on fastapi",
        "what": "Chaos Mesh injects HTTP 500 responses at the network layer.",
        "pod": "Pod may look healthy in kubectl.",
        "app": "Clients see 500 errors.",
        "cluster": "Service-mesh style failure without code change.",
    },
    {
        "num": "38", "id": "http_delay", "name": "HTTP latency injection (Chaos Mesh)",
        "category": "Application / Chaos Mesh", "prompt": "Simulate HTTP delay on fastapi",
        "what": "Chaos Mesh adds delay to HTTP traffic.",
        "pod": "Running normally.",
        "app": "Slow API/website responses.",
        "cluster": "Latency SLO testing via chaos.",
    },
    {
        "num": "39", "id": "stress_chaos_cpu", "name": "CPU stress (Chaos Mesh)",
        "category": "Application / Chaos Mesh", "prompt": "Simulate Chaos Mesh CPU stress on fastapi",
        "what": "Chaos Mesh burns CPU inside the target pod.",
        "pod": "High CPU; may throttle.",
        "app": "Performance collapse under load.",
        "cluster": "Infrastructure chaos at container level.",
    },
    {
        "num": "40", "id": "stress_chaos_memory", "name": "Memory stress (Chaos Mesh)",
        "category": "Application / Chaos Mesh", "prompt": "Simulate Chaos Mesh memory stress on fastapi",
        "what": "Chaos Mesh allocates memory pressure in the pod.",
        "pod": "Memory spikes; risk of OOMKilled.",
        "app": "Slow or failed requests.",
        "cluster": "Memory pressure testing via chaos.",
    },
]


class FailureGuidePDF(FPDF):
    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(100, 100, 100)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")


def _ascii_safe(text: str) -> str:
    return (
        text.replace("\u2014", "-")
        .replace("\u2013", "-")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )


def build_pdf(out_path: Path) -> None:
    pdf = FailureGuidePDF()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    # Cover
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(0, 14, "Enlight Lab Self-Heal", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "40 Failure Modes - QA Test Guide", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(60, 60, 60)
    intro = (
        "This document lists all 40 failure types the Self-Heal chat agent can inject "
        "on the demo cluster (FastAPI + Nginx in enlight-staging on Oracle OKE).\n\n"
        "For each failure: what it means, what happens to the pod, the app (user URL), "
        "and the cluster. Use the sample chat prompt to test. Say auto-fix fastapi or "
        "auto-fix nginx to recover.\n\n"
        "Chaos Mesh modes (32-40) need Chaos Mesh installed; otherwise kubectl fallback may apply."
    )
    pdf.multi_cell(0, 6, _ascii_safe(intro))
    pdf.ln(6)

    for item in FAILURES:
        if pdf.get_y() > 240:
            pdf.add_page()

        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(20, 60, 120)
        pdf.cell(
            0, 8,
            f"{item['num']}. {item['name']}  [{item['category']}]  id={item['id']}",
            new_x="LMARGIN", new_y="NEXT",
        )
        pdf.set_text_color(30, 30, 30)
        pdf.set_font("Helvetica", "I", 10)
        pdf.cell(0, 6, _ascii_safe(f"Chat prompt: {item['prompt']}"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

        fields = [
            ("What it is", item["what"]),
            ("Pod", item["pod"]),
            ("App (user sees)", item["app"]),
            ("Cluster", item["cluster"]),
        ]
        for label, text in fields:
            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(0, 6, f"{label}:", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 5, _ascii_safe(text))
            pdf.ln(1)
        pdf.ln(3)
        pdf.set_draw_color(220, 220, 220)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(4)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(out_path))
    print(f"Created: {out_path}")


if __name__ == "__main__":
    default = Path.home() / "Downloads" / "Self-Heal-40-Failure-Modes-QA-Guide.pdf"
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else default
    build_pdf(out)
