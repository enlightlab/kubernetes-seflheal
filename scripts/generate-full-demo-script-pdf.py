#!/usr/bin/env python3
"""Generate FULL-DEMO-SCRIPT.pdf - complete client demo guide."""
from pathlib import Path

from fpdf import FPDF

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "FULL-DEMO-SCRIPT.pdf"


class DemoScriptPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(120, 120, 120)
            self.cell(0, 8, "Enlight Lab - Full Demo Script", align="R")
            self.ln(4)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, f"Page {self.page_no()}", align="C")

    def title_page(self):
        self.add_page()
        self.set_font("Helvetica", "B", 20)
        self.set_text_color(30, 64, 120)
        self.multi_cell(0, 11, "Enlight Lab\nFull Client Demo Script")
        self.ln(4)
        self.set_font("Helvetica", "", 10)
        self.set_text_color(50, 50, 50)
        self.multi_cell(
            0,
            6,
            "Unified DevOps platform demo - browser UI only.\n"
            "Share: http://localhost:30900 (Demo Control Center)\n"
            "Project: D:\\enlight-lab-platform",
        )

    def _mc(self, text: str, h: float = 5.5, style: str = "", size: int = 10, indent: float = 0):
        self.set_x(self.l_margin + indent)
        self.set_font("Helvetica", style, size)
        self.multi_cell(self.epw - indent, h, text, new_x="LMARGIN", new_y="NEXT")

    def h1(self, text: str):
        self.ln(2)
        self.set_text_color(30, 64, 120)
        self._mc(text, h=8, style="B", size=13)

    def h2(self, text: str):
        self.ln(1)
        self.set_text_color(40, 40, 40)
        self._mc(text, h=6, style="B", size=10)

    def body(self, text: str):
        self.set_text_color(30, 30, 30)
        self._mc(text)

    def bullet(self, text: str):
        self.set_text_color(30, 30, 30)
        self._mc(f"  - {text}", indent=2)

    def step(self, num: str, text: str):
        self.set_text_color(30, 30, 30)
        self._mc(f"  {num}. {text}", style="B", size=9)

    def cmd(self, text: str):
        self.set_font("Courier", "", 7.5)
        self.set_fill_color(245, 247, 250)
        self.set_text_color(20, 20, 20)
        for line in text.split("\n"):
            self.set_x(self.l_margin)
            self.multi_cell(self.epw, 4.5, line, fill=True, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def table2(self, rows: list[tuple[str, str]], header: tuple[str, str] | None = None):
        w = [55, 135]
        if header:
            self.set_font("Helvetica", "B", 8)
            for i, col in enumerate(header):
                self.cell(w[i], 6, col, border=1)
            self.ln()
        self.set_font("Helvetica", "", 8)
        for a, b in rows:
            self.cell(w[0], 6, a[:30], border=1)
            self.cell(w[1], 6, b[:75], border=1)
            self.ln()


def build() -> None:
    pdf = DemoScriptPDF()
    pdf.set_auto_page_break(auto=True, margin=14)
    pdf.title_page()

    # PREP
    pdf.add_page()
    pdf.h1("1. BEFORE THE DEMO (15 min - hide from client)")
    pdf.cmd(
        "cd D:\\enlight-lab-platform\n"
        ".\\scripts\\go-live.bat\n"
        ".\\start-demo-control.bat"
    )
    pdf.body("Keep PowerShell minimized. Do NOT share terminal.")
    pdf.h2("Quick checks")
    pdf.bullet("http://localhost:30900 - Demo Control opens")
    pdf.bullet("Click Refresh - Dashboard / Health / IDP should be green")
    pdf.bullet("http://localhost:8082 - ArgoCD (NOT 8080 - that is k8sgpt)")
    pdf.bullet("http://localhost:30800/health - shows {\"status\":\"ok\"}")

    pdf.h1("2. URLS TO KNOW")
    pdf.table2(
        [
            ("PRIMARY (share this)", "http://localhost:30900"),
            ("App dashboard", "http://localhost:30800"),
            ("Health check", "http://localhost:30800/health"),
            ("IDP portal", "http://localhost:30800/idp"),
            ("ArgoCD", "http://localhost:8082"),
            ("Grafana", "http://localhost:3000 (admin / enlight-admin)"),
            ("GitHub Actions", "github.com/kirtiprasad2003/enlight-lab-platform/actions"),
        ],
        header=("What", "URL"),
    )

    pdf.h1("3. PLATFORM STORY (30 sec opening)")
    pdf.body(
        "Enlight Lab unifies CI policy, GitOps, monitoring, AI incident response, "
        "IDP golden-path scaffolding, drift detection, and PR compliance on one platform. "
        "Runs locally at zero cloud cost; same architecture on EKS in production."
    )

    # GUIDED 10 STEPS
    pdf.add_page()
    pdf.h1("4. GUIDED DEMO - 10 STEPS IN DEMO CONTROL")
    pdf.body("Open http://localhost:30900. Use the numbered guided steps OR the buttons below.")
    pdf.ln(1)
    steps = [
        ("1", "Check platform", "Click Refresh. All status cards green."),
        ("2", "Demo 2 BLOCK", "Block bad deploy. Show GitHub Actions RED / policy-check failed."),
        ("3", "Demo 2 PASS", "Pass good deploy. Show GitHub Actions GREEN."),
        ("4", "Demo 1 Inject", "Inject failure. ArgoCD fastapi-staging goes Degraded."),
        ("5", "Demo 1 Explain", "AI explain. Show k8sgpt findings in Live activity."),
        ("6", "Demo 1 Heal", "Heal / rollback. Then click Refresh (restores browser tunnel)."),
        ("7", "Demo 5 BLOCK", "Block bad PR. New GitHub PR fails compliance checks."),
        ("8", "Demo 3 Scaffold", "Scaffold new. Creates NEW service svc-YYYYMMDDHHMMSS."),
        ("9", "Demo 4 Drift", "Detect drift. Demo 4 panel turns RED - DRIFT DETECTED."),
        ("10", "Demo 4 Reconcile", "Reconcile. Panel turns green - IN SYNC again."),
    ]
    for num, title, desc in steps:
        pdf.step(num, f"{title}")
        pdf.body(f"     {desc}")

    # DEMO 2
    pdf.add_page()
    pdf.h1("5. DEMO 2 - POLICY GATE (CI blocks bad deploy)")
    pdf.body("Story: Bad manifest never reaches the cluster.")
    pdf.h2("UI buttons")
    pdf.bullet("Block bad deploy - dispatches chat-to-deploy non-compliant")
    pdf.bullet("Pass good deploy - dispatches chat-to-deploy compliant")
    pdf.h2("Show client")
    pdf.bullet("Live activity - workflow result")
    pdf.bullet("Open GitHub link - policy-check step RED (block) or GREEN (pass)")
    pdf.h2("What policy catches")
    pdf.bullet("Forbidden :latest image tag")
    pdf.bullet("Unapproved registry")
    pdf.bullet("Missing CPU/memory limits")

    # DEMO 1
    pdf.h1("6. DEMO 1 - INCIDENT RESPONSE")
    pdf.body("Story: App breaks -> AI explains -> GitOps heals.")
    pdf.h2("Flow")
    pdf.step("1", "Inject failure - bad image on fastapi (on purpose)")
    pdf.step("2", "Show ArgoCD http://localhost:8082 -> fastapi-staging RED")
    pdf.step("3", "AI explain - k8sgpt findings in console")
    pdf.step("4", "Heal / rollback - restores good image + selfHeal")
    pdf.step("5", "Click Refresh - Dashboard/Health/IDP green again")
    pdf.h2("Important")
    pdf.body(
        "After Inject, Dashboard/Health/IDP may show FAIL while ArgoCD still shows "
        "Synced - that means port-forward died, not the cluster. Heal fixes the pod; "
        "Refresh fixes the browser tunnel."
    )
    pdf.h2("Grafana (optional)")
    pdf.body(
        "fastapi has no custom dashboard. Use Grafana K8s pod views in enlight-staging, "
        "or show ArgoCD as primary incident signal."
    )

    # DEMO 5
    pdf.h1("7. DEMO 5 - PR COMPLIANCE")
    pdf.body("Story: Every PR scanned - bad config cannot merge.")
    pdf.h2("UI buttons")
    pdf.bullet("Block bad PR - opens NEW GitHub PR with secrets + bad S3")
    pdf.bullet("Pass good PR - opens NEW GitHub PR with clean config")
    pdf.h2("Show client")
    pdf.bullet("ONE PR, TWO workflow runs (service CI + PR Compliance Bot) - normal")
    pdf.bullet("PR Checks tab - RED (block) or GREEN (pass)")
    pdf.body("Merge is optional for demo; checks are the proof.")

    # DEMO 3
    pdf.add_page()
    pdf.h1("8. DEMO 3 - IDP GOLDEN PATH (MAIN DEMO)")
    pdf.body(
        "Story: Developer portal scaffolds a new service -> PR for review -> "
        "ArgoCD deploys with monitoring pre-wired. This is the platform vision demo."
    )
    pdf.h2("What each click does")
    pdf.table2(
        [
            ("Scaffold new", "Creates svc-TIMESTAMP with K8s, CI, Terraform, ArgoCD app"),
            ("Create PR", "Opens GitHub PR; 2 CI checks run on that PR"),
            ("Deploy", "Registers app in ArgoCD + applies manifests"),
        ],
        header=("Button", "Action"),
    )
    pdf.h2("Client flow (recommended)")
    pdf.step("1", "Open IDP portal http://localhost:30800/idp")
    pdf.step("2", "Demo Control -> Scaffold new (note svc- name in Current service)")
    pdf.step("3", "Create PR -> open GitHub Checks link")
    pdf.step("4", "Merge PR on GitHub (optional for full GitOps story)")
    pdf.step("5", "Deploy -> show NEW app in ArgoCD http://localhost:8082")
    pdf.h2("Key points for client")
    pdf.bullet("Each scaffold = NEW app (not same demo-api every time)")
    pdf.bullet("Every change is a reviewable PR - not manual kubectl")
    pdf.bullet("Bundle includes catalog, K8s, ServiceMonitor, alerts, Grafana dashboard")
    pdf.bullet("Local equivalent of Backstage - same story, not full Backstage install")

    # DEMO 4
    pdf.h1("9. DEMO 4 - DRIFT & COST SENTINEL")
    pdf.body(
        "Story: Terraform is source of truth. Someone changes AWS in console -> "
        "drift detected -> cost exposure -> reconcile back to Git."
    )
    pdf.h2("UI (all in Demo Control - no terminal)")
    pdf.step("1", "Reset baseline - S3 private + encrypted (Floci local AWS)")
    pdf.step("2", "Detect drift - simulates public S3 ACL; panel turns RED")
    pdf.step("3", "Reconcile - Terraform apply; panel turns green IN SYNC")
    pdf.h2("Show client")
    pdf.bullet("Demo 4 drift panel - desired private vs actual public-read")
    pdf.bullet("Live activity - DRIFT DETECTED / RECONCILED highlighted")
    pdf.body("NOT on GitHub - runs locally via Floci + Terraform.")

    # TROUBLESHOOTING
    pdf.add_page()
    pdf.h1("10. TROUBLESHOOTING")
    pdf.table2(
        [
            ("Dashboard/Health/IDP fail", "go-live.bat then Refresh in Demo Control"),
            ("ArgoCD 8082 not loading", "port-forward-all.ps1 (8080 is k8sgpt)"),
            ("After Demo 1 Heal still fail", "Click Refresh - tunnel not pod"),
            ("Demo 3 ArgoCD path error", "Merge PR first, or Deploy uses PR branch"),
            ("Demo 5 both workflows", "1 PR + 2 checks = normal, not 2 PRs"),
            ("Cluster dead after reboot", "go-live.bat (may take 10-15 min)"),
        ],
        header=("Problem", "Fix"),
    )

    pdf.h1("11. PRIVATE COMMANDS (if not using UI)")
    pdf.cmd(
        "cd D:\\enlight-lab-platform\n"
        ".\\scripts\\go-live.bat\n"
        ".\\start-demo-control.bat\n"
        "# Demo 2: use Demo Control buttons or GitHub dispatch\n"
        "# Demo 1: Inject / Explain / Heal buttons\n"
        "# Demo 3: Scaffold new -> Create PR -> Deploy\n"
        "# Demo 4: Reset baseline -> Detect drift -> Reconcile\n"
        "# Demo 5: Block bad PR -> Pass good PR"
    )

    pdf.h1("12. CLOSING")
    pdf.body(
        "Five integrated demos on one platform. Demo 3 (IDP) is the main growth story; "
        "Demos 1, 2, and 5 prove safety gates; Demo 4 proves infra governance. "
        "Zero cloud cost today - production-ready architecture on EKS."
    )

    pdf.h1("13. LIKELY QUESTIONS")
    pdf.h2("Why not AWS?")
    pdf.body("Local kind = $0. EKS Terraform scaffold ready for production.")
    pdf.h2("Is Backstage installed?")
    pdf.body("IDP portal + scripts simulate Backstage golden path locally.")
    pdf.h2("Why 2 GitHub workflows on one PR?")
    pdf.body("Service CI + org compliance bot - both gates on every PR.")
    pdf.h2("What does AI do?")
    pdf.body("Explains incidents (k8sgpt). ArgoCD/GitOps performs rollback.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(OUT))
    print(f"Created: {OUT}")


if __name__ == "__main__":
    build()
