# Phase 0 Research: Metrics Store Load

## R1 — Choice of fast-lookup data store

**Decision**: AWS DynamoDB, table `MusicKPIs`, PK=`genre` (S), SK=`date` (S, `YYYY-MM-DD`), PAY_PER_REQUEST billing.

**Rationale**:
- Spec FR-003 requires single-lookup retrieval by `(genre, date)` with no scan/filter — DynamoDB `GetItem` on a composite key is the canonical fit (p95 < 10 ms typical, well under SC-001's 50 ms bound).
- Constitution Phase 4 already names DynamoDB `MusicKPIs` as the target store with this exact key shape.
- PAY_PER_REQUEST: run volume ≤ 100 records/day — provisioned capacity would be over-engineered.
- Encryption at rest (AWS-managed key) and PITR satisfy constitution Security Requirements at zero config cost.

**Alternatives considered**:
- **S3 + Athena**: rejected — query path scans, no single-lookup semantics; latency violates SC-001.
- **RDS / Aurora**: rejected — requires VPC + connection management for a key-value access pattern that DynamoDB serves natively; cost floor too high for ≤ 100 records/day.
- **ElastiCache (Redis)**: rejected — adds VPC + node management for a non-hot dataset; no durable atomic batch primitive matching `TransactWriteItems`.
- **DocumentDB**: rejected — same VPC overhead, no advantage over DynamoDB for the `(genre, date)` key pattern.

---

## R2 — Atomic per-run publish

**Decision**: Single `DynamoDB:TransactWriteItems` call containing one `Put` per `(genre, date)` record produced by the run.

**Rationale**:
- DynamoDB transactions are ACID across up to **100 items / 4 MB** in a single `TransactWriteItems` call. Spec clarifications cap run volume at ≤ 100 records/day, so the entire run fits in one transaction.
- All-or-nothing semantics directly satisfy FR-005 (atomic visibility) and FR-006 (no partial state on failure) without bespoke staging tables.
- Idempotent: each item is a `Put` keyed on `(genre, date)` — re-running overwrites prior values (FR-004, SC-002).
- Average record size estimate: 6 scalar fields + `top_3_songs` (3 × ~120 B) + `top_5_genres` (5 × ~120 B) ≈ 1.2 KB. 100 × 1.2 KB = 120 KB, far under the 4 MB transaction limit.

**Alternatives considered**:
- **Staging table + alias swap**: rejected — DynamoDB has no native rename/alias; would require GSI tricks or app-level indirection. Overkill given native transaction fits run volume.
- **BatchWriteItem**: rejected — not atomic; partial failures leave half-written state, violating FR-006.
- **Per-item PutItem with `ConditionExpression`**: rejected — not atomic across the set; visibility staggers as items commit.

**Failure mode validated**: spec acknowledges (Edge Cases) that record size exceeding the store's per-item limit must fail loudly. `TransactWriteItems` rejects the whole batch on any per-item validation error → matches the loud-fail requirement (FR-009).

---

## R3 — Source format & read strategy

**Decision**: Read Phase 3 Parquet output under `s3://<processed-bucket>/output/date=<run_date>/` using `pyarrow.parquet.ParquetDataset` with the partition filter applied at the prefix level (the writer job receives `--run_date` and reads only that day's partitions).

**Rationale**:
- Phase 3 already writes Parquet partitioned by `date` and `genre`. Reading per-run-date keeps the writer pure (one run = one date's worth of records) and avoids ever touching unrelated dates.
- pyarrow is available in the Glue Python Shell runtime without extra packaging.
- One-shot read (≤ 100 small records) — no Spark needed; Python Shell (`pythonshell` job_type, `--MaxCapacity 0.0625`) is cheaper than `glueetl`.

**Alternatives considered**:
- **Spark/glueetl**: rejected — overkill for ≤ 100 records; cold-start cost dwarfs the actual work.
- **boto3 + S3 Select**: rejected — Parquet partitioned layout already filters efficiently; S3 Select adds parse complexity with no benefit.

---

## R4 — Top-N entry serialization

**Decision**: Store `top_3_songs` and `top_5_genres` as DynamoDB `List` of `Map`:

```
top_3_songs: [
  {song_id: "S", song_name: "S", listen_count: N},
  ...
]
top_5_genres: [
  {genre_id: "S", genre_name: "S", listen_count: N},
  ...
]
```

**Rationale**:
- Spec FR-011 requires `song_id` + `song_name` + ranking metric value per song entry, and `genre_id` + `genre_name` + ranking metric value per genre entry — consumers must render lists with no secondary lookup.
- DynamoDB `List` of `Map` is the native representation; preserves order (rank); no extra index needed.
- Ranking metric chosen by Phase 3 upstream — this feature stores it verbatim (Assumption in spec).

**Alternatives considered**:
- **Serialize as JSON string**: rejected — opaque to DynamoDB; consumers must parse; loses native typing.
- **Separate items per song (denormalised)**: rejected — violates FR-003 (single-lookup retrieval); forces secondary queries.

---

## R5 — Read access control

**Decision**: Create a single managed IAM policy `metrics-reader-policy` granting `dynamodb:GetItem` and `dynamodb:Query` on `MusicKPIs` ARN only. Consumer principals (downstream service roles) attach this policy individually. No anonymous access. No `dynamodb:Scan`.

**Rationale**:
- FR-012: IAM-restricted reads, named principals, least privilege.
- Excluding `Scan` enforces the single-lookup contract at the IAM layer — consumers cannot accidentally regress to a table scan.
- One policy reused across consumers keeps the surface auditable.

**Alternatives considered**:
- **Resource-based policy on the table**: rejected — DynamoDB resource-based policies are recent; identity-based scales better with multiple consumer accounts and integrates with existing role provisioning.
- **VPC endpoint + private access**: deferred — orthogonal hardening; can be layered later without changing the IAM contract.

---

## R6 — Zero-listen (genre, date) handling

**Decision**: Writer omits any `(genre, date)` not present in the Phase 3 Parquet output. Lookups for absent pairs return DynamoDB's native "item not found" (empty response) which the consumer interprets as "no activity that day."

**Rationale**:
- Spec clarification: zero-listen → omit, "not found" is the canonical signal (FR-010).
- Phase 3 aggregation only emits rows for genres with ≥ 1 listen, so this is a natural property of the pipeline — no extra filter needed in the writer.
- Avoids unbounded blank rows for the cartesian product of all genres × all dates.

---

## R7 — Idempotency contract

**Decision**: Use `TransactWriteItems` with `Put` operations (no `ConditionExpression`). Each `Put` on `(genre, date)` unconditionally replaces the prior item.

**Rationale**:
- Spec FR-004 + SC-002 mandate exact-replacement semantics — `Put` (not `Update`) discards prior attributes wholesale.
- No condition needed: replacement is intended, not opportunistic.
- Pairs with R2: the whole batch commits or rolls back, so a retried run either fully overwrites or leaves prior state untouched.

**Alternatives considered**:
- **`Update` with attribute merge**: rejected — would leak stale attributes from prior runs if record shape ever shrinks.
- **Delete + Put**: rejected — doubles transaction-item count, halving the per-run record budget for no benefit (Put already replaces).

---

## R8 — Key encoding (deterministic, edge cases)

**Decision**: Store `genre` as the raw string emitted by Phase 3 (no encoding/normalisation in the writer). Store `date` as `YYYY-MM-DD` (already the Phase 3 partition format).

**Rationale**:
- FR-007 requires deterministic encoding — passing through the upstream string is deterministic by construction (Phase 3 already normalises).
- Edge case (spaces/slashes/unicode): DynamoDB string keys accept any UTF-8 ≤ 2048 bytes for PK, ≤ 1024 bytes for SK. `YYYY-MM-DD` is 10 bytes. Genre names in this dataset are short canonical labels (`acoustic`, `hip-hop`, `r-n-b`) — well within limits.
- Consumer lookup contract: `GetItem(genre=<verbatim Phase-3 string>, date=<YYYY-MM-DD>)`.

**Alternatives considered**:
- **Lowercase / slugify at writer**: rejected — splits the source of truth; consumers would need the same transformation to look up. Upstream normalisation (if needed) belongs in Phase 3, not here.
