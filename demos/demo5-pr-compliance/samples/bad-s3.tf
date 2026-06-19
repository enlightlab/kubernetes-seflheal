resource "aws_s3_bucket" "enlight_demo_bad" {
  bucket = "enlight-demo-bad"
  acl    = "public-read"
}
