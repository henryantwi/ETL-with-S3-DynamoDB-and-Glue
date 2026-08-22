# Contract: DynamoDB `MusicKPIs` record + lookup

Authoritative shape of the data exchanged between the writer (`etl-metrics-writer`) and downstream consumers.

## Table

- **Name**: `MusicKPIs`
- **Region**: as deployed (see `terraform/variables.tf:aws_region`)
- **Billing**: PAY_PER_REQUEST
- **Encryption at rest**: AWS-managed key
- **PITR**: enabled
- **Deletion protection**: enabled
- **Stream**: disabled (not required by this feature)

## Key

| Position | Attribute | Type | Format |
|----------|-----------|------|--------|
| Partition key | `genre` | S | Verbatim from Phase 3 output |
| Sort key | `date` | S | `YYYY-MM-DD` |

## GSI `date-index`

| Position | Attribute | Type | Format |
|----------|-----------|------|--------|
| Partition key | `date` | S | `YYYY-MM-DD` |
| Sort key | `genre` | S | Verbatim from Phase 3 output |

Projection: ALL. DynamoDB maintains the index on every table Put; the writer does not write a separate rank attribute.

## Item shape

```json
{
  "genre":                       "acoustic",
  "date":                        "2024-06-25",
  "listen_count":                1200,
  "unique_listener_count":       340,
  "total_listening_time":        864000,
  "avg_listening_time_per_user": 2541.18,
  "top_3_songs": [
    { "song_id": "trk_001", "song_name": "Song A", "listen_count": 80 },
    { "song_id": "trk_002", "song_name": "Song B", "listen_count": 65 },
    { "song_id": "trk_003", "song_name": "Song C", "listen_count": 50 }
  ],
  "top_5_genres": [
    { "genre_id": "pop",      "genre_name": "Pop",      "listen_count": 9800 },
    { "genre_id": "rock",     "genre_name": "Rock",     "listen_count": 7400 },
    { "genre_id": "hip-hop",  "genre_name": "Hip-Hop",  "listen_count": 6100 },
    { "genre_id": "acoustic", "genre_name": "Acoustic", "listen_count": 1200 },
    { "genre_id": "jazz",     "genre_name": "Jazz",     "listen_count":  950 }
  ]
}
```

## Field contract

| Field | Type | Required | Constraint |
|-------|------|----------|------------|
| `genre` | S | yes | non-empty; partition key |
| `date` | S | yes | `YYYY-MM-DD`; sort key |
| `listen_count` | N | yes | integer ≥ 1 |
| `unique_listener_count` | N | yes | integer ≥ 1 |
| `total_listening_time` | N | yes | integer seconds ≥ 0 |
| `avg_listening_time_per_user` | N | yes | decimal seconds ≥ 0 |
| `top_3_songs` | L of M | yes | length ∈ [0, 3]; each map: `{song_id S, song_name S, listen_count N}` |
| `top_5_genres` | L of M | yes | length ∈ [0, 5]; each map: `{genre_id S, genre_name S, listen_count N}` |

## Consumer lookup contract

### Single record retrieval (the primary API)

```python
import boto3
ddb = boto3.client("dynamodb")
resp = ddb.get_item(
    TableName="MusicKPIs",
    Key={"genre": {"S": "acoustic"}, "date": {"S": "2024-06-25"}},
    ConsistentRead=False,  # eventually-consistent reads suffice for SC-001 latency
)
item = resp.get("Item")  # absent => no activity that day (FR-010)
```

- **One call, no scan, no filter** (FR-003).
- **Latency**: p95 < 50 ms (SC-001) — inherent DynamoDB `GetItem` behavior.
- **"Not found"**: response has no `Item` key. Consumers MUST treat as "no activity that day" (FR-010, R6).

### Listing all dates for a genre (secondary)

```python
resp = ddb.query(
    TableName="MusicKPIs",
    KeyConditionExpression="genre = :g",
    ExpressionAttributeValues={":g": {"S": "acoustic"}},
)
```

Enabled by the table key shape.

### All genres for a date (GSI `date-index`)

```python
resp = ddb.query(
    TableName="MusicKPIs",
    IndexName="date-index",
    KeyConditionExpression="#d = :date",
    ExpressionAttributeNames={"#d": "date"},
    ExpressionAttributeValues={":date": {"S": "2024-06-25"}},
)
```

- **Index**: `date-index` — partition key `date`, sort key `genre`, projection ALL.
- **One Query, no Scan** — returns every `(genre, date)` item for that day.

## Writer contract

- Writer issues **one `TransactWriteItems` call per run** containing one `Put` per `(genre, date)` produced for that run's `run_date`.
- Per-run record count: 1 ≤ N ≤ 100.
- All items commit atomically; on any per-item validation failure the entire batch rolls back (FR-005, FR-006).
- Idempotency: re-running the writer with the same input replaces each `(genre, date)` item; no duplicates, no accumulation (FR-004, SC-002).

## Access control

- Read: `metrics-reader-policy` managed IAM policy grants `dynamodb:GetItem` and `dynamodb:Query` on the `MusicKPIs` table ARN and `.../index/date-index`. Attached to named consumer roles. No `Scan` granted to any principal (FR-012).
- Write: `etl-glue-writer-role` only, with `PutItem`, `TransactWriteItems`, `DescribeTable`.
- Anonymous / public access: denied (no resource-based policy; identity-based only).

## Backwards-compatibility rules

- Adding a new attribute: non-breaking; consumers ignore unknown fields.
- Removing or renaming an attribute: breaking; constitution MAJOR-version bump required (per constitution Governance: MAJOR for breaking change to DynamoDB schema).
- Changing key schema (`genre`, `date`): breaking; requires table rebuild + consumer coordination.
