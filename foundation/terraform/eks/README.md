# EKS Foundation

Pre-provision **before** demo day (15-20 min). Never apply live on stage.

```powershell
cd foundation\terraform\eks
terraform init
terraform apply
terraform output configure_kubectl
```

Teardown after demo:

```powershell
terraform destroy
```
