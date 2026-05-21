# Data Model: Data Validation

**Phase 1 output** | Branch: `002-data-validation`

---

## File Schemas (hardcoded, Phase 2)

### ListeningActivitySchema

| Field | Validated As |
|-------|-------------|
| `user_id` | Required — presence in CSV header |
| `track_id` | Required — presence in CSV header |
| `listened_at` | Required — presence in CSV header |

**S3 prefix**: `listening-activity/`

---

### SongCatalogSchema

| Field | Validated As |
|-------|-------------|
| `track_id` | Required — presence in CSV header |
| `song_name` | Required — presence in CSV header |
| `artist_name` | Required — presence in CSV header |
| `genre` | Required — presence in CSV header |
| `duration` | Required — presence in CSV header |

**S3 prefix**: `song-catalog/`

---

### UserProfileSchema

| Field | Validated As |
|-------|-------------|
| `user_id` | Required — presence in CSV header |
| `username` | Required — presence in CSV header |
| `country` | Required — presence in CSV header |

**S3 prefix**: `user-profiles/`

---

## Runtime Entities (Python dataclasses / dicts)

### ValidationResult

Represents the outcome of validating a single file.

| Attribute | Type | Description |
|-----------|------|-------------|
| `file_key` | str | Full S3 object key (e.g., `listening-activity/2026-05-20.csv`) |
| `file_type` | str | One of: `listening-activity`, `song-catalog`, `user-profiles` |
| `status` | str | `"PASS"` or `"FAIL"` |
| `missing_fields` | list[str] | Empty on pass; list of absent header names on fail |
| `failure_reason` | str \| None | `None` on pass; one of: `"field-error"`, `"empty"`, `"missing"`, `"unreadable"` on fail |

---

### ValidationSummary

Aggregates results after all three files are checked.

| Attribute | Type | Description |
|-----------|------|-------------|
| `results` | list[ValidationResult] | One entry per file type |
| `passed` | bool | True only if all three results have `status = "PASS"` |
| `failures` | list[ValidationResult] | Filtered subset where `status = "FAIL"` |

---

### FileSchema

Internal mapping used to drive validation loop.

| Attribute | Type | Description |
|-----------|------|-------------|
| `prefix` | str | S3 key prefix for this file type |
| `file_type` | str | Canonical name for logging |
| `required_fields` | frozenset[str] | Fields that must appear in the CSV header |

---

## Glue Job Parameters

Injected by Step Functions at execution time; accessed via `getResolvedOptions`.

| Parameter | Glue arg name | Example value |
|-----------|--------------|---------------|
| Raw S3 bucket name | `--raw_bucket` | `raw-data-etl-dev-abc123` |
| Listening-activity prefix | `--listening_prefix` | `listening-activity/` |
| Song catalog prefix | `--songs_prefix` | `song-catalog/` |
| User profiles prefix | `--users_prefix` | `user-profiles/` |

---

## Terraform Module Inputs/Outputs

### `modules/glue`

**Variables**:

| Name | Type | Description |
|------|------|-------------|
| `job_name` | string | Glue job name |
| `script_location` | string | `s3://glue-scripts-.../validate_files.py` |
| `role_arn` | string | IAM role ARN (etl-glue-validation-role) |
| `timeout` | number | Max run time in minutes (5) |
| `max_retries` | number | 0 — Step Functions handles retry |
| `default_arguments` | map(string) | Default Glue job params (can be overridden at StartJobRun) |

**Outputs**:

| Name | Description |
|------|-------------|
| `job_name` | Glue job name (for Step Functions state machine reference) |
