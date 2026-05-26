# Quickstart: Metrics Store Load (Feature 004)

End-to-end path to run the writer locally against a moto-mocked DynamoDB, then deploy to AWS.

## Prerequisites

- Python 3.9+ with `pip install boto3 pyarrow pytest moto`
- AWS account access (deployment only) with Terraform state backend already provisioned (constitution Phase 1)
- Phase 3 (`etl-genre-metrics`) has produced Parquet at `s3://<processed-bucket>/output/date=<run_date>/...` for the target run date

## Local development & test

```powershell
# from repo root
cd C:\Users\HenryNanaAntwi\Development\Project-1

# unit tests (pure transformation logic)
pytest tests/unit/metrics_writer/test_transformations.py -v

# integration tests (moto: DynamoDB + S3 atomicity / idempotency)
pytest tests/unit/metrics_writer/test_pipeline_integration.py -v
```

Integration tests cover:
1. Happy path: 5 input records → 5 items in `MusicKPIs` after one transaction.
2. Idempotency: re-run with same input → still 5 items, latest values (SC-002).
3. Atomicity: simulated mid-transaction failure → 0 items written, prior state intact (FR-005, FR-006, SC-003).
4. Volume bound: 101 input records → writer fails loudly with structured error log (R7).
5. Zero-input: 0 input records → writer logs warn, exits 0, no transaction issued.

## Deploy

```powershell
cd terraform
terraform init   # picks up new dynamodb module
terraform plan   # expect: 1 ddb table, 1 glue job, 1 iam role, 1 managed policy, 1 alarm
terraform apply
```

Verify:

```powershell
aws dynamodb describe-table --table-name MusicKPIs `
  --query "Table.{Encryption:SSEDescription.Status,Billing:BillingModeSummary.BillingMode}"
aws dynamodb describe-continuous-backups --table-name MusicKPIs `
  --query "ContinuousBackupsDescription.PointInTimeRecoveryDescription.PointInTimeRecoveryStatus"
# expected: ENABLED, PAY_PER_REQUEST, ENABLED
```

## Trigger a run

```powershell
aws glue start-job-run --job-name etl-metrics-writer --arguments '{
  "--processed_bucket": "<your-processed-bucket>",
  "--run_date":         "2024-06-25"
}'
```

Tail logs:

```powershell
aws logs tail /aws-glue/jobs/etl-metrics-writer --follow
```

Confirm an item landed:

```powershell
aws dynamodb get-item --table-name MusicKPIs `
  --key '{"genre":{"S":"acoustic"},"date":{"S":"2024-06-25"}}'
```

## Consumer lookup (single-call contract)

```python
import boto3
ddb = boto3.client("dynamodb")
resp = ddb.get_item(
    TableName="MusicKPIs",
    Key={"genre": {"S": "acoustic"}, "date": {"S": "2024-06-25"}},
)
record = resp.get("Item")
if record is None:
    print("no activity that day")
else:
    print(record)  # all six metric fields populated
```

## Operations

- **Re-run a date**: invoke the job again with the same `--run_date`. Existing items for that date are replaced atomically (no duplicates, no accumulation).
- **Mid-run failure**: CloudWatch alarm `etl-metrics-writer-failures` fires within the run cycle (SC-005). Re-run the job to retry — partial state is impossible because `TransactWriteItems` is all-or-nothing.
- **Item size exceeded** (>400 KB, very large top-N): writer fails with a structured error log naming the offending `(genre, date)`. Investigation: upstream Phase 3 should not produce > 100 KB records under normal data; spike indicates a top-N ranking-criterion regression.
- **Read access for a new consumer**: attach the `metrics-reader-policy` managed policy to the consumer's IAM role. No table change needed.
