#!/usr/bin/env python3
"""Generate MANAGER-DEMO-CHEATSHEET.pdf for tomorrow's presentation."""
from pathlib import Path

from fpdf import FPDF

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "MANAGER-DEMO-CHEATSHEET.pdf"


class CheatSheetPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(120, 120, 120)
            self.cell(0, 8, "Enlight Lab - Manager Demo Cheat Sheet", align="R")
            self.ln(4)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, f"Page {self.page_no()}", align="C")

    def title_page(self):
        self.add_page()
        self.set_font("Helvetica", "B", 22)
        self.set_text_color(30, 64, 120)
        self.multi_cell(0, 12, "Enlight Lab\nManager Demo Cheat Sheet")
        self.ln(4)
        self.set_font("Helvetica", "", 11)
        self.set_text_color(50, 50, 50)
        self.multi_cell(
            0,
            7,
            "Browser UI demo for your manager.\n"
            "Paste commands to Cursor - AI runs them behind the scenes.\n"
            "Hide PowerShell and kubectl from the screen.",
        )
        self.ln(6)
        self.set_font("Helvetica", "B", 11)
        self.cell(0, 7, "Project: D:\\enlight-lab-platform")
        self.ln(6)
        self.set_font("Helvetica", "", 10)
        self.cell(0, 6, "Repo: github.com/kirtiprasad2003/enlight-lab-platform")

    def _mc(self, text: str, h: float = 5.5, style: str = "", size: int = 10, indent: float = 0):
        self.set_x(self.l_margin + indent)
        self.set_font("Helvetica", style, size)
        self.multi_cell(self.epw - indent, h, text, new_x="LMARGIN", new_y="NEXT")

    def h1(self, text: str):
        self.ln(3)
        self.set_text_color(30, 64, 120)
        self._mc(text, h=8, style="B", size=14)

    def h2(self, text: str):
        self.ln(2)
        self.set_text_color(40, 40, 40)
        self._mc(text, h=7, style="B", size=11)

    def body(self, text: str):
        self.set_text_color(30, 30, 30)
        self._mc(text)

    def quote(self, text: str):
        self.set_text_color(60, 60, 60)
        self._mc(f'"{text}"', style="I", indent=4)

    def cmd(self, text: str):
        self.set_font("Courier", "", 8)
        self.set_fill_color(245, 247, 250)
        self.set_text_color(20, 20, 20)
        w = self.epw
        for line in text.split("\n"):
            self.set_x(self.l_margin)
            self.multi_cell(w, 5, line, fill=True, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def bullet(self, text: str):
        self.set_text_color(30, 30, 30)
        self._mc(f"- {text}", indent=4)

    def table_row(self, cols: list[str], bold: bool = False):
        w = [12, 58, 120]
        self.set_font("Helvetica", "B" if bold else "", 8)
        for i, col in enumerate(cols):
            txt = col[:48] + "..." if len(col) > 51 else col
            self.cell(w[i], 7, txt, border=1)
        self.ln()


def build() -> None:
    pdf = CheatSheetPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.title_page()

    pdf.add_page()
    pdf.h1("BEFORE THE MEETING (you only - hide terminal)")
    pdf.body("Run 15-30 minutes before the call. Minimize PowerShell.")
    pdf.cmd("cd D:\\enlight-lab-platform\n.\\scripts\\go-live.bat")
    pdf.body("Quick check: http://localhost:30800/health must show {\"status\":\"ok\"}")
    pdf.body("If connection refused -> run go-live.bat again.")

    pdf.h1("BROWSER TABS (share screen on these)")
    pdf.table_row(["#", "URL", "Notes"], bold=True)
    tabs = [
        ("1", "http://localhost:30800", "Enlight Lab dashboard"),
        ("2", "http://localhost:30800/health", "JSON health check"),
        ("3", "http://localhost:8082", "ArgoCD -> fastapi-staging"),
        ("4", "http://localhost:3000", "Grafana admin / enlight-admin"),
        ("5", "github.com/.../actions", "GitHub Actions tab"),
        ("6", "platform.robusta.dev", "Robusta (optional)"),
    ]
    for row in tabs:
        pdf.table_row(list(row))
    pdf.ln(2)
    pdf.body("DO NOT share: PowerShell, kubectl, or Cursor terminal.")

    pdf.h1("OPENING LINE (30 seconds)")
    pdf.quote(
        "You saw two earlier PoCs - delivery and policy separately. "
        "Enlight Lab unifies them on one platform: pipeline, policy gates, "
        "GitOps, and monitoring. Today it runs at zero cloud cost locally; "
        "production uses the same design on EKS."
    )

    pdf.add_page()
    pdf.h1("PART 1 - Live platform (~2 min) - BROWSER ONLY")
    pdf.h2("Show:")
    pdf.bullet("http://localhost:30800 - dashboard UI")
    pdf.bullet("http://localhost:8082 - ArgoCD fastapi-staging green/Synced")
    pdf.bullet("http://localhost:30800/health - {\"status\":\"ok\"}")
    pdf.h2("Say:")
    pdf.quote("The app is live on Kubernetes. ArgoCD keeps it in sync with Git.")
    pdf.body("Cursor command: None")

    pdf.h1("PART 2 - Demo 2: BLOCK bad deploy (~2 min)")
    pdf.body("CI policy rejects bad manifest before it reaches the cluster.")
    pdf.h2("Paste to Cursor:")
    pdf.cmd(
        "Dispatch chat-to-deploy non-compliant on "
        "kirtiprasad2003/enlight-lab-platform main"
    )
    pdf.h2("Show in browser:")
    pdf.bullet("GitHub Actions -> Chat to Deploy -> RED failed policy-check")
    pdf.bullet("Violation: :latest tag, bad registry, missing limits")
    pdf.h2("Say:")
    pdf.quote("Bad configuration is stopped in CI. It never touches production.")

    pdf.h1("PART 3 - Demo 2: PASS good deploy (~2 min)")
    pdf.h2("Paste to Cursor:")
    pdf.cmd(
        "Dispatch chat-to-deploy compliant on "
        "kirtiprasad2003/enlight-lab-platform main"
    )
    pdf.h2("Show:")
    pdf.bullet("GitHub Actions -> GREEN run")
    pdf.bullet("ArgoCD still healthy")
    pdf.bullet("/health still ok")
    pdf.h2("Say:")
    pdf.quote("Compliant config passes policy and the app stays healthy.")

    pdf.add_page()
    pdf.h1("PART 4 - Demo 1: Incident response (~4 min)")

    pdf.h2("Step 4a - Break the app (on purpose)")
    pdf.h2("Paste to Cursor:")
    pdf.cmd("Run demo 1: inject failure on fastapi in enlight-staging")
    pdf.h2("Show:")
    pdf.bullet("ArgoCD -> pod RED / degraded")
    pdf.bullet("Robusta alert if visible")
    pdf.h2("Say:")
    pdf.quote("Something broke on purpose. In production this could be a bad image push.")

    pdf.h2("Step 4b - AI explains (do NOT fix yet)")
    pdf.h2("Paste to Cursor:")
    pdf.cmd("Use k8sgpt to explain the fastapi failure in enlight-staging")
    pdf.body("Alternative: Explain why the fastapi pod is failing in enlight-staging")
    pdf.h2("Say:")
    pdf.quote("AI diagnoses the incident. Explanation is separate from the fix.")

    pdf.h2("Step 4c - Heal / rollback")
    pdf.h2("Paste to Cursor:")
    pdf.cmd("Heal demo 1 - rollback fastapi to last good image in enlight-staging")
    pdf.h2("Show:")
    pdf.bullet("ArgoCD recovers to green")
    pdf.bullet("/health ok again")
    pdf.h2("Say:")
    pdf.quote("GitOps rolls back to last good version. AI explains; ArgoCD fixes.")

    pdf.h1("PART 5 - Observability (~1 min) - BROWSER ONLY")
    pdf.body("Show: http://localhost:3000 (Grafana)")
    pdf.quote("Prometheus and Grafana give SLOs and metrics for rollback decisions.")
    pdf.body("Cursor command: None")

    pdf.add_page()
    pdf.h1("PART 6 - Demo 5: PR compliance (optional ~2 min)")
    pdf.h2("Paste to Cursor:")
    pdf.cmd("Show me the latest PR compliance workflow run on enlight-lab-platform")
    pdf.body("Or: Create a demo PR with non-compliant samples and show failed checks")
    pdf.h2("Show:")
    pdf.bullet("GitHub PR -> Checks -> blocked vs passed")
    pdf.h2("Say:")
    pdf.quote("Every PR is scanned for secrets and insecure infra.")
    pdf.body("If no PR ready: say same bot runs on every pull request.")

    pdf.h1("PART 7 - Demo 3: IDP scaffold (~1 min)")
    pdf.h2("Paste to Cursor:")
    pdf.cmd("Run demo 3 scaffold demo-api and show me what was created")
    pdf.h2("Show:")
    pdf.bullet("Folder workload/scaffolded/demo-api in repo")
    pdf.h2("Say:")
    pdf.quote("In production this is Backstage - one click scaffolds a compliant bundle.")

    pdf.h1("PART 8 - Demo 4: Drift (mention only ~30 sec)")
    pdf.body("No browser UI. Say briefly:")
    pdf.quote(
        "Demo 4 detects AWS changes outside Terraform, estimates cost, "
        "and reconciles back to Git. Runs locally with simulated AWS."
    )

    pdf.add_page()
    pdf.h1("CLOSING (~30 sec)")
    pdf.quote(
        "Five demos on one platform: CI policy, GitOps, monitoring, "
        "AI incident response, and PR compliance. Zero cloud cost now - "
        "same architecture on EKS in production."
    )

    pdf.h1("FULL COMMAND LIST (copy-paste in order)")
    commands = [
        "# Part 1 - browser only",
        "",
        "# Part 2 - BLOCK",
        "Dispatch chat-to-deploy non-compliant on kirtiprasad2003/enlight-lab-platform main",
        "",
        "# Part 3 - PASS",
        "Dispatch chat-to-deploy compliant on kirtiprasad2003/enlight-lab-platform main",
        "",
        "# Part 4 - Incident",
        "Run demo 1: inject failure on fastapi in enlight-staging",
        "Use k8sgpt to explain the fastapi failure in enlight-staging",
        "Heal demo 1 - rollback fastapi to last good image in enlight-staging",
        "",
        "# Part 5 - Grafana (browser only)",
        "",
        "# Part 6 - PR compliance (optional)",
        "Show me the latest PR compliance workflow run on enlight-lab-platform",
        "",
        "# Part 7 - IDP scaffold",
        "Run demo 3 scaffold demo-api and show me what was created",
    ]
    pdf.cmd("\n".join(commands))

    pdf.h1("IF SOMETHING BREAKS")
    fixes = [
        (":30800 connection refused", "Run .\\scripts\\go-live.bat"),
        ("ArgoCD blank", "fix-dashboards.ps1 then go-live.bat"),
        ("App broken after Demo 1", "Paste: Heal demo 1 - rollback fastapi"),
        ("Dashboard shows JSON not HTML", "Rebuild UI (see below)"),
    ]
    pdf.table_row(["Problem", "Fix (private)"], bold=True)
    for prob, fix in fixes:
        pdf.table_row([prob, fix])

    pdf.ln(3)
    pdf.h2("Rebuild dashboard UI (before demo, private):")
    pdf.cmd(
        "cd D:\\enlight-lab-platform\n"
        "docker build -t enlight-fastapi:demo-pass workload/fastapi\n"
        "kind load docker-image enlight-fastapi:demo-pass --name enlight-lab\n"
        "kubectl rollout restart deployment/fastapi -n enlight-staging\n"
        ".\\scripts\\go-live.bat"
    )

    pdf.h1('IF ASKED: "Is it complete?"')
    pdf.quote(
        "Foundation and all five demos are runnable locally. Demos 1 and 2 are "
        "production-depth; 3-5 use the same foundation. Remaining work is polish, "
        "not architecture."
    )

    pdf.h1("LIKELY QUESTIONS")
    pdf.h2("Why not AWS today?")
    pdf.body("Local kind = $0. EKS Terraform ready for production.")
    pdf.h2("What does AI do?")
    pdf.body("Explains incidents. ArgoCD performs rollback - separate steps.")
    pdf.h2("What is Floci?")
    pdf.body("Simulated local AWS for Demo 4 drift - not for Kubernetes.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(OUT))
    print(f"Created: {OUT}")


if __name__ == "__main__":
    build()
