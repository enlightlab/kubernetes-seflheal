# ──────────────────────────────────────────────
# OKE Cluster + Node Pool
# ──────────────────────────────────────────────

resource "oci_containerengine_cluster" "main" {
  compartment_id     = var.compartment_ocid
  kubernetes_version = var.kubernetes_version
  name               = var.cluster_name
  vcn_id             = oci_core_vcn.main.id

  endpoint_config {
    is_public_ip_enabled = true
    subnet_id            = oci_core_subnet.k8s_api.id
  }

  options {
    service_lb_subnet_ids = [oci_core_subnet.lb.id]

    kubernetes_network_config {
      pods_cidr     = "10.244.0.0/16"
      services_cidr = "10.96.0.0/16"
    }
  }
}

data "oci_containerengine_node_pool_option" "opts" {
  node_pool_option_id = "all"
  compartment_id      = var.compartment_ocid
}

locals {
  # Pick the latest Oracle Linux 8 image for the chosen shape
  node_image_id = var.node_image_id != "" ? var.node_image_id : [
    for src in data.oci_containerengine_node_pool_option.opts.sources :
    src.image_id
    if length(regexall("Oracle-Linux-8", src.source_name)) > 0
  ][0]
}

resource "oci_containerengine_node_pool" "workers" {
  compartment_id     = var.compartment_ocid
  cluster_id         = oci_containerengine_cluster.main.id
  kubernetes_version = var.kubernetes_version
  name               = "${var.cluster_name}-pool"

  node_shape = var.node_shape

  node_shape_config {
    ocpus         = var.node_ocpus
    memory_in_gbs = var.node_memory_gb
  }

  node_source_details {
    source_type = "IMAGE"
    image_id    = local.node_image_id
  }

  node_config_details {
    size = var.node_count

    dynamic "placement_configs" {
      for_each = data.oci_identity_availability_domains.ads.availability_domains
      content {
        availability_domain = placement_configs.value.name
        subnet_id           = oci_core_subnet.workers.id
      }
    }
  }

  initial_node_labels {
    key   = "app"
    value = "selfheal-demo"
  }

  ssh_public_key = var.ssh_public_key != "" ? var.ssh_public_key : null
}
