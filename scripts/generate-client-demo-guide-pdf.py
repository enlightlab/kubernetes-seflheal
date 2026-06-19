#!/usr/bin/env python3
"""Generate CLIENT-DEMO-EXPLAINED.pdf - plain-language guide for every demo."""
from pathlib import Path

from fpdf import FPDF

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "CLIENT-DEMO-EXPLAINED.pdf"


class GuidePDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(120, 120, 120)
            self.cell(0, 8, "Enlight Lab - Client Demo Explained", align="R")
            self.ln(4)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, f"Page {self.page_no()}", align="C")

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

    def h3(self, text: str):
        self.set_text_color(50, 50, 50)
        self._mc(text, h=5.5, style="B", size=9)

    def body(self, text: str):
        self.set_text_color(30, 30, 30)
        self._mc(text)

    def bullet(self, text: str):
        self.set_text_color(30, 30, 30)
        self._mc(f"  - {text}", indent=2)

    def say(self, text: str):
        self.set_text_color(30, 64, 120)
        self._mc(f'Say to client: "{text}"', style="I", size=9)

    def flow_box(self, lines: list[str]):
        self.set_font("Courier", "", 8)
        self.set_fill_color(245, 247, 250)
        self.set_text_color(20, 20, 20)
        for line in lines:
            self.set_x(self.l_margin)
            self.multi_cell(self.epw, 4.8, line, fill=True, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def step(self, num: str, text: str):
        self.set_text_color(30, 30, 30)
        self._mc(f"  {num}. {text}", style="B", size=9)


def build() -> None:
    pdf = GuidePDF()
    pdf.set_auto_page_break(auto=True, margin=14)

    # Title
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(30, 64, 120)
    pdf.multi_cell(0, 12, "Enlight Lab\nClient Demo Explained")
    pdf.ln(3)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(50, 50, 50)
    pdf.multi_cell(
        0,
        6,
        "Plain-language guide for every button in the Live Demo.\n"
        "Written for presenters - share only http://localhost:30900 with clients.\n"
        "Project: D:\\enlight-lab-platform",
    )

    # Intro
    pdf.add_page()
    pdf.h1("How to use this guide")
    pdf.body(
        "Each section follows the same pattern: what the demo proves, what happens when "
        "you click, what the client sees, and a short script you can say out loud."
    )
    pdf.h2("Before the client joins (hide terminal)")
    pdf.flow_box(
        [
            "cd D:\\enlight-lab-platform",
            ".\\scripts\\go-live.bat",
            ".\\start-demo-control.bat",
            "Open http://localhost:30900 and click Refresh",
        ]
    )
    pdf.h2("One URL for the client")
    pdf.bullet("Live Demo: http://localhost:30900 (only screen to share)")
    pdf.bullet("Optional tabs for you: Deployments :8082, Build pipelines on GitHub")
    pdf.bullet("Do NOT open :4566 - that is background plumbing, not a website")
    pdf.h2("Demo order on screen (top to bottom)")
    pdf.step("1", "Release safety")
    pdf.step("2", "Outage response")
    pdf.step("3", "Cloud config guard")
    pdf.step("4", "Code review security")
    pdf.step("5", "New service onboarding (main platform story)")

    # Demo 2
    pdf.add_page()
    pdf.h1("Release safety")
    pdf.h3("What this proves")
    pdf.body("Bad releases never reach production. Automated checks block unsafe deploys.")
    pdf.h3("Buttons")
    pdf.h2("Block unsafe release")
    pdf.body("What happens: A deliberately bad deployment is sent to your CI pipeline.")
    pdf.bullet("Behind the scenes: GitHub Actions runs policy checks")
    pdf.bullet("Checks fail on purpose: bad image tag, missing limits, wrong registry")
    pdf.bullet("Client sees: Latest outcome shows checks failed - that is success for this demo")
    pdf.bullet("Show them: Build pipelines tab on GitHub - red X on policy step")
    pdf.say(
        "Our pipeline stops unsafe releases before they touch the cluster. "
        "Bad config never gets deployed."
    )
    pdf.h2("Approve safe release")
    pdf.body("What happens: A clean, compliant deployment is sent to the same pipeline.")
    pdf.bullet("Client sees: Checks pass - green pipeline")
    pdf.say(
        "When everything meets policy, the release is approved automatically. "
        "Same gate, opposite result."
    )

    # Demo 1
    pdf.add_page()
    pdf.h1("Outage response")
    pdf.h3("What this proves")
    pdf.body("When an app breaks, AI explains why and the platform rolls back automatically.")
    pdf.h2("1. Simulate outage")
    pdf.body("What happens: Staging app is broken on purpose (bad container image).")
    pdf.bullet("Client sees: Status may flicker; Deployments tab shows Degraded")
    pdf.bullet("Open: http://localhost:8082 - fastapi-staging turns red")
    pdf.say("We have a live incident in staging - on purpose - so you can see how we respond.")
    pdf.h2("2. Explain with AI")
    pdf.body("What happens: AI reads cluster signals and summarizes the failure.")
    pdf.bullet("Client sees: Findings in What just happened panel on the right")
    pdf.say("AI triages the incident in seconds - root cause without digging through logs.")
    pdf.h2("3. Auto-fix app")
    pdf.body("What happens: GitOps rolls back to the last good version and restarts the app.")
    pdf.bullet("Client sees: App healthy again; click Refresh if dashboard still shows fail")
    pdf.bullet("Note: After heal, Refresh restores the browser tunnel - pod was already fine")
    pdf.say("Recovery is automated. We restore the last known good deployment - no manual kubectl.")

    # Demo 4 - detailed like user's favorite explanation
    pdf.add_page()
    pdf.h1("Cloud config guard")
    pdf.h3("What this proves")
    pdf.body(
        "Git is the contract for cloud settings. We detect when someone changes the cloud "
        "outside that process, and we restore the approved state automatically."
    )
    pdf.h3("The two boxes on screen")
    pdf.bullet("Left - What we want (in Git): approved secure settings")
    pdf.bullet("Right - What's running now: actual live settings")
    pdf.bullet("MATCHES GIT = good | OUT OF SYNC = someone drifted from Git")

    pdf.h2("1. Set secure baseline")
    pdf.body("One sentence: Apply the approved configuration from Git so live matches the rules.")
    pdf.h3("What happens when you click")
    pdf.bullet("Local cloud sandbox starts (background - client never sees this)")
    pdf.bullet("Terraform apply runs from foundation/terraform/demo4")
    pdf.bullet("Creates bucket enlight-demo: Private + encrypted (AES256)")
    pdf.h3("What client sees")
    pdf.bullet("Latest outcome: Secure baseline set")
    pdf.bullet("Both boxes show Private (secure)")
    pdf.bullet("Badge: MATCHES GIT (green)")
    pdf.say(
        "Our secure storage rules live in Git. This applies that approved definition. "
        "Now Git and the live environment agree."
    )

    pdf.h2("2. Catch config drift")
    pdf.body("One sentence: Simulate someone breaking the rules outside Git.")
    pdf.h3("What happens when you click")
    pdf.bullet("Script changes bucket ACL to public-read (like a rogue console edit)")
    pdf.bullet("Terraform plan detects live no longer matches Git")
    pdf.h3("What client sees")
    pdf.bullet("Left: Private (secure)  |  Right: Public (unsafe)")
    pdf.bullet("Badge: OUT OF SYNC (red)")
    pdf.bullet("Extra cost risk may appear (~$15/mo demo estimate)")
    pdf.say(
        "Someone changed cloud settings outside our approved process. "
        "We caught it immediately - that is drift, and it is a security and cost risk."
    )

    pdf.h2("3. Fix drift automatically")
    pdf.body("One sentence: Put live settings back to what Git says - one click.")
    pdf.h3("What happens when you click")
    pdf.bullet("Terraform apply runs again")
    pdf.bullet("Bucket returns to private + encrypted")
    pdf.h3("What client sees")
    pdf.bullet("Latest outcome: Restored to match Git")
    pdf.bullet("Both boxes Private (secure) again")
    pdf.bullet("Badge: MATCHES GIT (green)")
    pdf.say(
        "We do not fix this by hand in the console. We reconcile from Git automatically."
    )

    pdf.h3("All three steps as a story")
    pdf.flow_box(
        [
            "Step 1  Git: Private   Live: Private   -> MATCHES GIT",
            "Step 2  Git: Private   Live: Public    -> OUT OF SYNC (drift)",
            "Step 3  Git: Private   Live: Private   -> MATCHES GIT (fixed)",
        ]
    )
    pdf.say("Git is the contract. We detect drift and restore it automatically.")

    # Demo 5
    pdf.add_page()
    pdf.h1("Code review security")
    pdf.h3("What this proves")
    pdf.body("Every change request is scanned before merge. Secrets and unsafe config cannot slip through.")
    pdf.h2("Open risky change request")
    pdf.body("What happens: Opens a new GitHub pull request with hardcoded secrets + bad S3 settings.")
    pdf.bullet("Client sees: PR opened; security checks fail (red)")
    pdf.bullet("Normal: ONE pull request, TWO check workflows (service CI + compliance bot)")
    pdf.say("Every PR is scanned. This one fails on purpose - secrets and unsafe config are blocked.")
    pdf.h2("Open safe change request")
    pdf.body("What happens: Opens a new PR with clean, compliant configuration.")
    pdf.bullet("Client sees: Checks pass (green)")
    pdf.say("Clean changes pass the same gates. Merge is optional - the checks are the proof.")

    # Demo 3
    pdf.add_page()
    pdf.h1("New service onboarding (main story)")
    pdf.h3("What this proves")
    pdf.body(
        "A developer can spin up a full production-ready service in minutes - "
        "with monitoring, pipelines, and deployment already wired. This is the platform vision."
    )
    pdf.h2("1. Create new app")
    pdf.body("What happens: Platform scaffolds a brand-new service (svc-TIMESTAMP each time).")
    pdf.bullet("Bundle includes: Kubernetes manifests, CI workflow, Terraform, ArgoCD app, monitoring")
    pdf.bullet("Client sees: App being built shows new svc- name")
    pdf.say(
        "From the portal we provision a new service with all platform defaults - "
        "not a blank repo, a golden path."
    )
    pdf.h2("2. Submit for review")
    pdf.body("What happens: Opens a GitHub PR so the team can review every file.")
    pdf.bullet("Client sees: Review request # opened; CI runs on GitHub")
    pdf.say("Nothing goes live without a reviewable pull request - full audit trail.")
    pdf.h2("3. Go live")
    pdf.body("What happens: Registers the app in GitOps and deploys to the cluster.")
    pdf.bullet("Client sees: New app in Deployments at http://localhost:8082")
    pdf.bullet("Tip: Merge PR on GitHub first for the full GitOps story (optional)")
    pdf.say(
        "One click from approved code to a running service with monitoring already attached."
    )

    # Guided + troubleshooting
    pdf.add_page()
    pdf.h1("Guided walkthrough (10 steps)")
    pdf.body("Use the purple Guided walkthrough card - same demos, scripted order:")
    steps = [
        "Health check - verify all status cards green",
        "Block unsafe release",
        "Approve safe release",
        "Simulate outage",
        "Explain with AI",
        "Auto-fix app",
        "Open risky change request",
        "Create new app",
        "Catch config drift",
        "Fix drift automatically",
    ]
    for i, s in enumerate(steps, 1):
        pdf._mc(f"  {i}. {s}", size=9)

    pdf.h1("Quick fixes")
    fixes = [
        ("Dashboard shows fail but ArgoCD healthy", "Click Refresh on Live Demo"),
        ("Cloud guard shows NOT STARTED", "Click Set secure baseline"),
        ("Nothing loads", "Run go-live.bat then Refresh"),
        ("ArgoCD blank", "Use http://localhost:8082 not 8080"),
    ]
    pdf.h2("If you see this...")
    for problem, fix in fixes:
        pdf.bullet(f"{problem} -> {fix}")

    pdf.h1("Likely client questions")
    pdf.h2("Why not real AWS?")
    pdf.body("Local sandbox = $0 today. Same architecture on EKS in production.")
    pdf.h2("What is the main demo?")
    pdf.body("New service onboarding - everything else proves safety gates around it.")
    pdf.h2("Two GitHub workflows on one PR?")
    pdf.body("Normal - service CI plus org compliance bot. Both gates on every change.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(OUT))
    print(f"Created: {OUT}")


if __name__ == "__main__":
    build()
