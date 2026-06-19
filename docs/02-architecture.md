# Architecture

```mermaid
flowchart TB
  subgraph human [Human]
    Claude[Claude Desktop + MCP]
  end

  subgraph cicd [CI/CD]
    GHA[GitHub Actions]
    OPA[OPA Conftest]
  end

  subgraph eks [EKS Cluster]
    ArgoCD[ArgoCD self-heal]
    GK[Gatekeeper]
    Prom[Prometheus]
    Graf[Grafana]
    App[FastAPI]
  end

  subgraph ai [AI - read only]
    K8sGPT[k8sgpt]
    Holmes[HolmesGPT]
  end

  Claude -->|workflow_dispatch| GHA
  GHA --> OPA
  OPA -->|pass| ArgoCD
  ArgoCD --> GK --> App
  Prom --> Graf
  K8sGPT -.-> eks
  Holmes -.-> eks
  ArgoCD -->|rollback| App
```

## Component locations

| Component | Path |
|-----------|------|
| EKS Terraform | `foundation/terraform/eks/` |
| OPA Rego | `foundation/policies/opa/` |
| Gatekeeper | `foundation/policies/gatekeeper/` |
| ArgoCD apps | `gitops/argocd/` |
| FastAPI app | `workload/fastapi/` |
| Demo 2 | `demos/demo2-chat-to-deploy/` |
| GitHub Actions | `.github/workflows/` |
