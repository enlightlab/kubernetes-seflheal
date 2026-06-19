# Object Storage namespace (= tenancy namespace for OCIR)
data "oci_objectstorage_namespace" "ns" {
  compartment_id = var.tenancy_ocid
}
