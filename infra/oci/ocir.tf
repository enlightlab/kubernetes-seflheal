# ──────────────────────────────────────────────
# Oracle Container Registry (OCIR) repos
# ──────────────────────────────────────────────

resource "oci_artifacts_container_repository" "selfheal_ui" {
  compartment_id = var.compartment_ocid
  display_name   = "selfheal-ui"
  is_public      = false
}

resource "oci_artifacts_container_repository" "enlight_fastapi" {
  compartment_id = var.compartment_ocid
  display_name   = "enlight-fastapi"
  is_public      = false
}
