# Scaffolded infra for {{SERVICE_NAME}} — extend for production AWS
terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

resource "aws_s3_bucket" "{{SERVICE_NAME_TF}}_artifacts" {
  bucket = "enlight-{{SERVICE_NAME}}-artifacts"
}

resource "aws_s3_bucket_acl" "{{SERVICE_NAME_TF}}_artifacts" {
  bucket = aws_s3_bucket.{{SERVICE_NAME_TF}}_artifacts.id
  acl    = "private"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "{{SERVICE_NAME_TF}}_artifacts" {
  bucket = aws_s3_bucket.{{SERVICE_NAME_TF}}_artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
