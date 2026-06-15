terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region                      = "us-east-1"
  access_key                  = "test"
  secret_key                  = "test"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  s3_use_path_style           = true

  endpoints {
    s3 = "http://localhost:4566"
  }
}

resource "aws_s3_bucket" "enlight_demo" {
  bucket = "enlight-demo"
}

resource "aws_s3_bucket_acl" "enlight_demo" {
  bucket = aws_s3_bucket.enlight_demo.id
  acl    = "private"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "enlight_demo" {
  bucket = aws_s3_bucket.enlight_demo.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
