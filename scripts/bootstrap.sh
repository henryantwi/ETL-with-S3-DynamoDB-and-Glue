#!/usr/bin/env bash
# One-time bootstrap of Terraform remote state backend.
# Creates terraform-state-{account_id} S3 bucket + terraform-locks DynamoDB table.
# Run ONCE per AWS account before `terraform init`.

set -euo pipefail

REGION="${AWS_REGION:-ap-southeast-2}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
STATE_BUCKET="terraform-state-${ACCOUNT_ID}"
LOCK_TABLE="terraform-locks"

echo "Account: ${ACCOUNT_ID}"
echo "Region:  ${REGION}"
echo "State bucket: ${STATE_BUCKET}"
echo "Lock table:   ${LOCK_TABLE}"

# Create state bucket (idempotent: ignore BucketAlreadyOwnedByYou)
if aws s3api head-bucket --bucket "${STATE_BUCKET}" 2>/dev/null; then
  echo "State bucket already exists — skipping."
else
  aws s3 mb "s3://${STATE_BUCKET}" --region "${REGION}"
  aws s3api put-bucket-versioning \
    --bucket "${STATE_BUCKET}" \
    --versioning-configuration Status=Enabled
  aws s3api put-bucket-encryption \
    --bucket "${STATE_BUCKET}" \
    --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
  aws s3api put-public-access-block \
    --bucket "${STATE_BUCKET}" \
    --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
fi

# Create lock table (idempotent)
if aws dynamodb describe-table --table-name "${LOCK_TABLE}" --region "${REGION}" >/dev/null 2>&1; then
  echo "Lock table already exists — skipping."
else
  aws dynamodb create-table \
    --table-name "${LOCK_TABLE}" \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --region "${REGION}"
fi

echo "Bootstrap complete."
