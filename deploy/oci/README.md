# Oracle Cloud (OKE) deployment

Deploy the self-heal UI **inside your OKE cluster** so it uses in-cluster credentials (no laptop, no port-forward).

## Prerequisites

1. Oracle Cloud free trial / paid tenancy with **OKE** cluster running
2. **ArgoCD** + **enlight-staging** namespace + **fastapi-staging** app (from enlight-lab-platform or equivalent)
3. **OCIR** (Oracle Container Registry) for images
4. **k8sgpt** AI key — OpenAI or other backend supported by k8sgpt

## Architecture

```text
Client browser
    → OCI Load Balancer (selfheal-ui Service type LoadBalancer)
        → selfheal-ui pod (FastAPI + kubectl + k8sgpt)
            → Kubernetes API (in-cluster ServiceAccount)
                → enlight-staging / argocd
```

## Step 1 — Build and push image to OCIR

```bash
# Login to OCIR (region key + tenancy namespace from OCI Console)
docker login iad.ocir.io

export OCIR=iad.ocir.io/YOUR_TENANCY_NAMESPACE/selfheal
docker build -f deploy/Dockerfile -t $OCIR/selfheal-ui:latest .
docker push $OCIR/selfheal-ui:latest
```

Replace `iad` with your region code (e.g. `lhr`, `phx`, `bom`).

## Step 2 — Configure manifests

Edit `deploy/k8s/selfheal-ui.yaml`:

1. Set `image: YOUR_OCIR/selfheal-ui:latest` on the Deployment
2. Update ConfigMap `PUBLIC_*` URLs to your load balancer / ingress hostnames
3. Set `GOOD_IMAGE` / `BAD_IMAGE` to full OCIR paths if staging app uses registry images

Create k8sgpt secret (optional but needed for Explain step):

```bash
kubectl create namespace selfheal --dry-run=client -o yaml | kubectl apply -f -
kubectl -n selfheal create secret generic k8sgpt-ai \
  --from-literal=openai-api-key='YOUR_KEY'
```

Configure k8sgpt inside the cluster (once):

```bash
kubectl -n selfheal exec deploy/selfheal-ui -- k8sgpt auth add --backend openai --password "$OPENAI_API_KEY"
```

Or bake auth into an init step / custom entrypoint if preferred.

## Step 3 — Deploy to OKE

```bash
kubectl apply -f deploy/k8s/selfheal-ui.yaml
kubectl -n selfheal get svc selfheal-ui -w
```

Copy the **EXTERNAL-IP** (or hostname) from the LoadBalancer service — that is your demo URL:

```text
http://EXTERNAL-IP/
```

## Step 4 — Verify

1. Open demo URL in browser
2. Status cards should show cluster + app state (not kubectl error spam)
3. Run: Simulate outage → Explain with AI → Auto-fix

## Environment variables

See `deploy/oci/env.example` for all settings. Key ones:

| Variable | Purpose |
|----------|---------|
| `DEPLOY_TARGET=oci` | Oracle mode (default) |
| `IN_CLUSTER=true` | Use pod ServiceAccount |
| `USE_PORT_FORWARD=false` | No localhost tunnels |
| `GOOD_IMAGE` / `BAD_IMAGE` | OCIR image refs for heal / break |
| `PUBLIC_*_URL` | Links shown in the UI |

## Local laptop mode (optional)

For development on kind:

```bat
set DEPLOY_TARGET=local
set USE_PORT_FORWARD=true
go-live.bat
start-selfheal-ui.bat
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Cluster offline | OKE cluster stopped? Check `kubectl get nodes` |
| Explain empty | k8sgpt auth + `OPENAI_API_KEY` secret |
| Auto-fix fails | RBAC on ServiceAccount; check `GOOD_IMAGE` exists in OCIR |
| Public links 404 | Update `PUBLIC_*` URLs in ConfigMap |
