# Robusta + HolmesGPT Install

## 1. Download from Robusta UI (Install and Verify step)

Save these two files **into this folder** (`foundation/robusta/`):

- `robusta-secrets.yaml`
- `generated_values.yaml`

## 2. Install (PowerShell)

```powershell
cd D:\enlight-lab-platform
.\foundation\robusta\install-robusta.ps1
```

## 3. Verify

```powershell
kubectl get pods -n robusta
```

Open Robusta UI — platform.robusta.dev — cluster should show **Connected**.

## Notes

- Uses existing Prometheus (`enablePrometheusStack: false` in generated values).
- Cluster: `kind-enlight-lab` / name `enlight-lab-kind`
- Do NOT commit `robusta-secrets.yaml` or `generated_values.yaml` (contain tokens).
