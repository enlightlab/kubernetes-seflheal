# Scaffolded infra for svc-20260611105645 — extend for production AWS
terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

resource "aws_s3_bucket" "svc_20260611105645_artifacts" {
  bucket = "enlight-svc-20260611105645-artifacts"
}

resource "aws_s3_bucket_acl" "svc_20260611105645_artifacts" {
  bucket = aws_s3_bucket.svc_20260611105645_artifacts.id
  acl    = "private"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "svc_20260611105645_artifacts" {
  bucket = aws_s3_bucket.svc_20260611105645_artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
