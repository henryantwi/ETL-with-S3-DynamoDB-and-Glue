# Feature Specification: Data Validation

**Feature Branch**: `002-data-validation`
**Created**: 2026-05-19
**Status**: Draft
**Input**: User description: pipeline must reject bad data before any processing begins

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Valid Files Pass Through (Priority: P1)

A data engineer triggers the pipeline when all three expected file types (listening activity, song catalog, user profiles) are present and contain all required fields. The validation step confirms every file passes and hands off to the transformation stage without interruption.

**Why this priority**: This is the happy-path gate; nothing downstream executes until validation passes. Proving the pass case proves the hand-off contract works.

**Independent Test**: Supply three well-formed files. Confirm validation reports all-pass status and signals the pipeline to proceed.

**Acceptance Scenarios**:

1. **Given** three files are present with all required fields, **When** validation runs, **Then** all files are marked valid and the pipeline proceeds to transformation.
2. **Given** a listening-activity file contains `user_id`, `track_id`, and `listened_at`, **When** validation checks it, **Then** the file is accepted.
3. **Given** a song-catalog file contains `track_id`, `song_name`, `artist_name`, `genre`, and `duration`, **When** validation checks it, **Then** the file is accepted.
4. **Given** a user-profile file contains `user_id`, `username`, and `country`, **When** validation checks it, **Then** the file is accepted.

---

### User Story 2 — Missing Fields Halt the Pipeline (Priority: P1)

A data engineer sees that an incoming file is missing one or more required fields. Validation immediately rejects that file, logs exactly which file failed and which fields were absent, stops the pipeline, and leaves all files in place — nothing is moved to archive.

**Why this priority**: Preventing partial processing is the core safety guarantee of this feature. A missed rejection corrupts downstream data.

**Independent Test**: Supply a listening-activity file without `track_id`. Confirm validation rejects with a log entry naming the file and the missing field, and the pipeline does not advance.

**Acceptance Scenarios**:

1. **Given** a listening-activity file is missing `track_id`, **When** validation runs, **Then** the pipeline halts, a failure record names the file and lists `track_id` as missing, and no file is moved to archive.
2. **Given** a song-catalog file is missing both `artist_name` and `genre`, **When** validation runs, **Then** both absent fields are reported in the failure log.
3. **Given** one of three files fails, **When** validation runs, **Then** the two valid files are not advanced either — no partial processing occurs.

---

### User Story 3 — Unreadable, Empty, or Missing Files Are Rejected (Priority: P1)

A data engineer sees that an expected file has not arrived, is completely empty, or cannot be read (corrupt, permission error). Validation treats each of these as a failure: logs the problem, halts the pipeline, and does not move any files.

**Why this priority**: These failure modes are as dangerous as missing fields; allowing an empty file to proceed would produce silent data loss.

**Independent Test**: Supply an empty listening-activity file. Confirm validation logs the file as empty/unreadable, halts the pipeline, and moves nothing to archive.

**Acceptance Scenarios**:

1. **Given** an expected file is not present in the source location, **When** validation runs, **Then** the pipeline halts and the failure log identifies the missing file.
2. **Given** an expected file is present but contains zero records, **When** validation runs, **Then** the pipeline halts and the failure log identifies the file as empty.
3. **Given** an expected file exists but cannot be read (corrupt format or permission error), **When** validation runs, **Then** the pipeline halts and the failure log captures the file name and reason.

---

### Edge Cases

- When more than one file is missing or invalid simultaneously, all failures are collected and reported together in a single halt event.
- What happens when a required field is present but contains only null or empty values for every row?
- How does the system behave if a new, unrecognised file type appears alongside the expected three?
- What if the same file arrives more than once before the pipeline processes the first copy?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST validate all three file types — listening activity, song catalog, and user profiles — before any transformation begins. File type is determined by the S3 key prefix under the raw-data bucket (`listening-activity/`, `song-catalog/`, `user-profiles/`).
- **FR-002**: Listening-activity files MUST contain the fields: user identifier, track identifier, and listening timestamp.
- **FR-003**: Song-catalog files MUST contain the fields: track identifier, song name, artist name, genre, and track duration.
- **FR-004**: User-profile files MUST contain the fields: user identifier, username, and country.
- **FR-005**: The system MUST validate all files before halting. If any file fails, the system MUST collect all failures across all three files and report them together before halting the pipeline — no file may advance to transformation.
- **FR-006**: On rejection, the system MUST produce a failure record that identifies: the file name, the file type, and the list of missing fields.
- **FR-007**: On rejection, files MUST remain in their source location — the system MUST NOT move any file to archive.
- **FR-008**: If a file is absent from the expected source location, the system MUST treat this as a failure and halt the pipeline.
- **FR-009**: If a file is present but contains no readable records (empty or corrupt), the system MUST treat this as a failure and halt the pipeline.
- **FR-012**: If an infrastructure error prevents validation from completing (e.g., S3 unreachable, process crash), the system MUST log the error to CloudWatch Logs and halt the pipeline. Retry decisions are delegated to the Step Functions orchestrator.
- **FR-010**: Failure records MUST be written to CloudWatch Logs under the `/aws-glue/jobs/*` log group, accessible to operators within 60 seconds of the rejection event.
- **FR-011**: When all three files pass validation, the system MUST signal the pipeline to proceed to the transformation stage.

### Key Entities

- **ValidationResult**: Outcome for a single file — file name, file type, pass/fail status, list of missing fields (empty on pass), failure reason (empty, unreadable, missing, or field-error).
- **FileSchema**: The declared set of required field names for a given file type (listening activity / song catalog / user profile).
- **PipelineHaltEvent**: Record emitted after all files are validated and one or more failures exist — references all failing ValidationResults as a collection (never just the first).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A file with any missing required field is rejected 100% of the time — zero false passes.
- **SC-002**: A rejection event always produces a log entry naming the specific file and each missing field — zero unattributed failures.
- **SC-003**: When any file fails, no file advances to transformation — 0% partial-processing rate.
- **SC-004**: An empty or unreadable file is rejected at the same rate as a field-missing file — 100% rejection of structurally invalid files.
- **SC-005**: When all files pass, the pipeline proceeds to transformation without manual intervention — 100% automated hand-off.
- **SC-006**: Operators can retrieve the failure log for a rejected run within 60 seconds of the rejection event.

## Clarifications

### Session 2026-05-19

- Q: How does the system identify which file belongs to which type? → A: S3 key prefix / folder path (e.g., `listening-activity/`, `song-catalog/`, `user-profiles/` sub-paths under the raw-data bucket)
- Q: When multiple files fail, does validation report all failures or only the first? → A: Validate all three files first; collect and report every failure together before halting
- Q: Where are failure records written? → A: CloudWatch Logs, using the existing `/aws-glue/jobs/*` log group
- Q: Infrastructure failure during validation — retry or treat as pipeline halt? → A: Treat as pipeline halt; log the error and stop — orchestrator (Step Functions) handles retry at workflow level
- Q: Are required field schemas fixed or externally configurable? → A: Hardcoded per file type for Phase 2; schema changes require a code update

## Assumptions

- Files arrive in CSV format; column headers in the first row are how required fields are detected.
- File type is determined by S3 key prefix: `listening-activity/` → listening activity schema; `song-catalog/` → song catalog schema; `user-profiles/` → user profile schema.
- All three file types are expected to be present for each pipeline run; the absence of any one type is a failure.
- Field presence is validated by header name only — content/type validation (e.g., timestamp format) is out of scope for this feature.
- Null or blank values in otherwise-present columns are out of scope; a column that exists in the header is considered present.
- The pipeline is orchestrated externally (e.g., Step Functions); this feature defines the validation step that either signals proceed or halt.
- Unrecognised file types beyond the three defined here are ignored (not validated, not rejected).
- Required field schemas are hardcoded for Phase 2; adding or renaming a required field requires a code change.
- Duplicate file arrivals are not handled by this feature — deduplication is a separate concern.
