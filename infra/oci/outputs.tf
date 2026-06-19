# ──────────────────────────────────────────────
# Outputs — used after apply
# ──────────────────────────────────────────────

output "cluster_id" {
  description = "OKE Cluster OCID"
  value       = oci_containerengine_cluster.main.id
}

output "cluster_name" {
  value = oci_containerengine_cluster.main.name
}

output "kubeconfig_command" {
  description = "Run this to configure kubectl"
  value       = "oci ce cluster create-kubeconfig --cluster-id ${oci_containerengine_cluster.main.id} --file $HOME/.kube/config --region ${var.region} --token-version 2.0.0 --kube-endpoint PUBLIC_ENDPOINT"
}

output "ocir_selfheal_ui" {
  description = "OCIR image path for selfheal-ui"
  value       = "bom.ocir.io/${data.oci_objectstorage_namespace.ns.namespace}/selfheal-ui"
}

output "ocir_enlight_fastapi" {
  description = "OCIR image path for enlight-fastapi"
  value       = "bom.ocir.io/${data.oci_objectstorage_namespace.ns.namespace}/enlight-fastapi"
}

output "docker_login_command" {
  description = "Docker login to OCIR (use auth token as password)"
  value       = "docker login bom.ocir.io -u ${data.oci_objectstorage_namespace.ns.namespace}/kirti@enlightlab.com"
  sensitive   = false
}

output "vcn_id" {
  value = oci_core_vcn.main.id
}

output "lb_subnet_id" {
  value = oci_core_subnet.lb.id
}
