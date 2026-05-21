# Quickstart: Data Validation

## Prerequisites

- Phase 1 complete: raw-data, glue-scripts S3 buckets + `etl-glue-validation-role` provisioned
- AWS CLI configured
- Terraform ≥ 1.6
- uv installed

---

## 1. Upload Validation Job Script to S3

```bash
BUCKET=$(terraform -chdir=terraform output -raw glue_scripts_bucket_id)
aws s3 cp glue_jobs/validation/validate_files.py s3://${BUCKET}/validate_files.py
```

---

## 2. Apply Terraform (Glue job resource)

```bash
cd terraform
terraform init
terraform plan
terraform apply
```

Expected output: 1 Glue job created (`etl-validate-files`).

---

## 3. Run Unit Tests (moto — no real AWS)

```bash
uv sync
uv run pytest glue_jobs/validation/ -v
```

Expected: 8+ tests, all pass.

---

## 4. Trigger Validation Job Manually (real AWS)

```bash
RAW_BUCKET=$(terraform -chdir=terraform output -raw raw_bucket_id)

aws glue start-job-run \
  --job-name etl-validate-files \
  --arguments '{
    "--raw_bucket": "'${RAW_BUCKET}'",
    "--listening_prefix": "listening-activity/",
    "--songs_prefix": "song-catalog/",
    "--users_prefix": "user-profiles/"
  }'
```

---

## 5. Check CloudWatch Logs

```bash
# Get the most recent log stream
LOG_GROUP=/aws-glue/jobs/output
aws logs describe-log-streams \
  --log-group-name $LOG_GROUP \
  --order-by LastEventTime \
  --descending \
  --max-items 1

# Tail logs from that stream
aws logs get-log-events \
  --log-group-name $LOG_GROUP \
  --log-stream-name <stream-name-from-above>
```

Successful run: JSON log line with `"status": "PASS"` for each file.
Failed run: JSON log line with `"status": "FAIL"`, `"missing_fields": [...]`.

---

## 6. Test Failure Path

Upload a malformed CSV (missing required field) then trigger job:

```bash
# Create CSV with missing track_id
echo "user_id,listened_at" > /tmp/bad_listening.csv
echo "u001,2026-05-20T10:00:00Z" >> /tmp/bad_listening.csv

aws s3 cp /tmp/bad_listening.csv \
  s3://${RAW_BUCKET}/listening-activity/bad_test.csv

# Run job — should fail
aws glue start-job-run \
  --job-name etl-validate-files \
  --arguments '{
    "--raw_bucket": "'${RAW_BUCKET}'",
    "--listening_prefix": "listening-activity/",
    "--songs_prefix": "song-catalog/",
    "--users_prefix": "user-profiles/"
  }'
```

Expected: Glue job run status = `FAILED`. CloudWatch log contains:
```json
{"level": "ERROR", ..., "status": "FAIL", "missing_fields": ["track_id"], "reason": "field-error"}
```

---

## 7. Credential Scan

```bash
grep -rEn "AKIA[0-9A-Z]{16}|aws_secret_access_key\s*=" \
  glue_jobs/ terraform/ pyproject.toml && echo "FAIL" || echo "OK: no credentials"
```
