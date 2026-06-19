# ──────────────────────────────────────────────
# Variables — fill in terraform.tfvars
# ──────────────────────────────────────────────

variable "tenancy_ocid" {
  description = "OCI Tenancy OCID"
  type        = string
}

variable "user_ocid" {
  description = "OCI User OCID"
  type        = string
}

variable "fingerprint" {
  description = "API key fingerprint"
  type        = string
}

variable "private_key_path" {
  description = "Path to OCI API private key (.pem)"
  type        = string
}

variable "region" {
  description = "OCI region"
  type        = string
  default     = "ap-mumbai-1"
}

variable "compartment_ocid" {
  description = "Compartment OCID (use tenancy OCID for root compartment)"
  type        = string
}

variable "cluster_name" {
  description = "OKE cluster name"
  type        = string
  default     = "selfheal-demo"
}

variable "kubernetes_version" {
  description = "Kubernetes version for OKE"
  type        = string
  default     = "v1.31.1"
}

variable "node_shape" {
  description = "Compute shape for worker nodes"
  type        = string
  default     = "VM.Standard.E4.Flex"
}

variable "node_ocpus" {
  description = "OCPUs per worker node"
  type        = number
  default     = 1
}

variable "node_memory_gb" {
  description = "Memory in GB per worker node"
  type        = number
  default     = 8
}

variable "node_count" {
  description = "Number of worker nodes"
  type        = number
  default     = 2
}

variable "node_image_id" {
  description = "Node image OCID (leave empty for latest Oracle Linux)"
  type        = string
  default     = ""
}

variable "ssh_public_key" {
  description = "SSH public key for node access (optional)"
  type        = string
  default     = ""
}
