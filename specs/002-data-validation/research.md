# Research: Data Validation

**Phase 0 output** | Branch: `002-data-validation`

---

## Decision 1: Glue Job Type — Python Shell vs PySpark

**Decision**: AWS Glue Python Shell job.

**Rationale**: Validation reads only the first row (CSV headers) of each file. No distributed
compute needed. Python Shell is faster to start, cheaper per run, and has no Spark overhead.
PySpark would be wasteful for a job that exits after reading 3 header rows.

**Alternatives considered**: PySpark — rejected; overkill for header-only validation.

---

## Decision 2: CSV Parsing — csv Module vs pandas

**Decision**: Python stdlib `csv` module only.

**Rationale**: Reading one header row does not justify importing pandas. The `csv` module
handles this with `csv.DictReader` or `next(csv.reader(...))` in a single line. Keeping
dependencies minimal reduces Glue job startup time and avoids `--additional-python-modules`
complexity.

**Alternatives considered**: pandas — rejected per explicit spec constraint.

---

## Decision 3: S3 Read Strategy — Header-Only Streaming

**Decision**: Stream the first line of each CSV via `boto3.get_object` with `Range` header
(`bytes=0-4095` — enough for any realistic header row) then parse with `csv.reader`.

**Rationale**: Loading the full file into memory is unnecessary and violates the spec
constraint. A 4 KB range request captures any CSV header row without reading data rows.
Falls back to full `get_object` if Range is not supported (it always is on S3).

**Alternatives considered**: Full `get_object` — rejected; wasteful for large files.

---

## Decision 4: Failure Collection — All-Failures-Before-Raise Pattern

**Decision**: Iterate all three file prefixes unconditionally, accumulate `ValidationResult`
objects, then raise a single exception with all failure details after all files are checked.

**Rationale**: Spec clarification Q2: "validate all three files first; collect and report
every failure together before halting." Operators see the full picture on one failed run.

**Alternatives considered**: Fail-fast (raise on first error) — rejected per clarification.

---

## Decision 5: Job Parameters — Step Functions Injection

**Decision**: Glue job receives `--raw_bucket`, `--listening_prefix`, `--songs_prefix`,
`--users_prefix` as Glue job parameters. Accessed via `getResolvedOptions` from
`awsglue.utils` (pre-installed in Glue runtime).

**Rationale**: No hardcoded bucket names or prefixes. Step Functions passes the values at
execution time, keeping the job reusable across environments (dev/prod) without code changes.

**How `getResolvedOptions` works**:
```python
from awsglue.utils import getResolvedOptions
import sys
args = getResolvedOptions(sys.argv, ['raw_bucket', 'listening_prefix', 'songs_prefix', 'users_prefix'])
```

**Alternatives considered**: Environment variables — rejected; Glue Python Shell uses job
parameters natively.

---

## Decision 6: Logging — Python logging → CloudWatch

**Decision**: Standard Python `logging` module with a JSON formatter. Glue Python Shell
automatically ships stdout/stderr to CloudWatch Logs under `/aws-glue/jobs/<job-name>`.

**Rationale**: No extra SDK calls needed. Structured JSON log lines satisfy FR-010 and
constitution Principle V. Log entries include: `file_name`, `file_type`, `status`,
`missing_fields`, `failure_reason`.

**Log line format**:
```json
{"level": "ERROR", "job": "etl-validate-files", "file": "listening-activity/2026-05-20.csv", "type": "listening-activity", "status": "FAIL", "missing_fields": ["track_id"], "reason": "field-error"}
```

---

## Decision 7: Terraform Module Placement

**Decision**: `terraform/modules/glue/` — new module following the Phase 1 pattern.

**Rationale**: Consistent with `modules/s3/` and `modules/iam/`. Glue module accepts
`job_name`, `script_location`, `role_arn`, `timeout`, `max_retries`, `default_arguments`
as inputs. Instantiated in `terraform/main.tf`.

**Alternatives considered**: Inline resource in `terraform/main.tf` — rejected; module
pattern is established and makes Phase 3/4 Glue jobs easier to add.

---

## Decision 8: Test Strategy — moto + pytest

**Decision**: `moto[s3]` mocks all S3 calls. Tests create real in-memory bucket objects.
No real AWS calls in test suite.

**Test file location**: `glue_jobs/validation/test_validate_files.py`
**Run command**: `uv run pytest glue_jobs/validation/`

**Test cases required** (from acceptance scenarios):
1. All three valid files → no exception raised
2. Listening-activity file missing `track_id` → exception raised, failure record present
3. Song-catalog file missing `artist_name` and `genre` → both fields in failure record
4. One of three files fails → no partial advance (exception raised)
5. Multiple files fail → all failures collected in single exception
6. Empty file → exception raised, reason = "empty"
7. Missing file (S3 key does not exist) → exception raised, reason = "missing"
8. Unreadable/corrupt file (non-UTF-8 bytes) → exception raised, reason = "unreadable"
