package enlight.terraform

deny[msg] {
    input.resource_changes[_].type == "aws_s3_bucket"
    change := input.resource_changes[_]
    change.change.after.acl == "public-read"
    msg := "S3 bucket must not be public-read (control S3-001)"
}

deny[msg] {
    input.resource_changes[_].type == "aws_s3_bucket"
    change := input.resource_changes[_]
    not change.change.after.server_side_encryption_configuration
    msg := "S3 bucket must have encryption enabled (control S3-002)"
}
