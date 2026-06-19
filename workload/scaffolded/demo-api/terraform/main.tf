# Scaffolded infra for demo-api — extend for production AWS
terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

resource "aws_s3_bucket" "demo_api_artifacts" {
  bucket = "enlight-demo-api-artifacts"
}

resource "aws_s3_bucket_acl" "demo_api_artifacts" {
  bucket = aws_s3_bucket.demo_api_artifacts.id
  acl    = "private"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "demo_api_artifacts" {
  bucket = aws_s3_bucket.demo_api_artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
