resource "aws_s3_bucket" "enlight_demo" {
  bucket = "enlight-demo"
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
