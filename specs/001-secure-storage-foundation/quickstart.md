# Quickstart: Secure Storage Foundation

## Prerequisites

- AWS CLI configured (`aws configure` or role-based credentials)
- Terraform ≥ 1.6 installed
- uv installed (`pip install uv` or `brew install uv`)
- An AWS account ID handy

---

## 1. Bootstrap Remote State (once per account)

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=ap-southeast-2  # change as needed

aws s3 mb s3://terraform-state-${ACCOUNT_ID} --region $REGION
aws dynamodb create-table \
  --table-name terraform-locks \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region $REGION
```

---

## 2. Configure Terraform Variables

```bash
cp terraform/variables.tf terraform/terraform.tfvars  # then edit
```

Set in `terraform.tfvars`:
```hcl
project_name   = "etl"
environment    = "dev"
bucket_suffix  = "abc123"   # any short unique string
aws_region     = "ap-southeast-2"
aws_account_id = "123456789012"
```

Update `terraform/backend.tf` with your state bucket name:
```hcl
bucket = "terraform-state-123456789012"
region = "ap-southeast-2"
```

---

## 3. Apply Infrastructure

```bash
cd terraform
terraform init
terraform plan
terraform apply
```

Expected output: 3 S3 buckets + 2 IAM roles created.

---

## 4. Verify Buckets

```bash
# Confirm public access blocked
aws s3api get-public-access-block --bucket raw-data-etl-dev-abc123

# Confirm encryption
aws s3api get-bucket-encryption --bucket raw-data-etl-dev-abc123

# Confirm versioning
aws s3api get-bucket-versioning --bucket raw-data-etl-dev-abc123
```

---

## 5. Run Smoke Tests

```bash
uv sync
uv run pytest scripts/smoke_test_s3.py -v
```

All tests mock AWS via moto — no real AWS calls made.

---

## 6. Verify IAM (optional, requires real AWS)

```bash
ROLE_ARN=$(terraform -chdir=terraform output -raw glue_validation_role_arn)

# Should succeed
aws iam simulate-principal-policy \
  --policy-source-arn $ROLE_ARN \
  --action-names s3:GetObject \
  --resource-arns "arn:aws:s3:::raw-data-etl-dev-abc123/test.csv"

# Should be denied
aws iam simulate-principal-policy \
  --policy-source-arn $ROLE_ARN \
  --action-names s3:PutObject \
  --resource-arns "arn:aws:s3:::archive-etl-dev-abc123/test.csv"
```

---

## 7. Security Audit (manual)

```bash
# (a) Confirm no wildcard actions or resources in plan output
cd terraform
terraform validate
terraform plan -out tfplan
terraform show -json tfplan | grep -E '"\*"' || echo "OK: no wildcards"

# (b) Scan for hardcoded credentials (also asserted by smoke test)
grep -rEn "AKIA[0-9A-Z]{16}|aws_secret_access_key\s*=" \
  terraform/ scripts/ pyproject.toml && echo "FAIL" || echo "OK: no credentials"
```

Expected: zero matches for wildcards; zero matches for credential patterns.
