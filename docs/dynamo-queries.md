# Sample DynamoDB Queries for MusicKPIs

The `MusicKPIs` table uses a composite primary key:
- **Partition Key**: `genre` (String)
- **Sort Key**: `date` (String, format: `YYYY-MM-DD`)

GSI **`date-index`**: partition key `date`, sort key `genre` (projection ALL). Query this index for every genre on a given day.

## Deploying `date-index` (when you are ready)

The index is defined in Terraform. It is **not** created by a pull-request CI
run. `terraform-plan` on the PR is read-only; **`terraform apply` on merge to
`main`** adds the GSI in place on the existing `MusicKPIs` table.

Chicken-and-egg, solved:

1. You do **not** create the GSI in the console first.
2. You do **not** `terraform apply` from the Windows CLI account (`024893220675`).
   CI uses account `559050223770` (GitHub vars `AWS_ACCOUNT_ID` / `BUCKET_SUFFIX`,
   secrets `AWS_PLAN_ROLE_ARN` / `AWS_DEPLOY_ROLE_ARN`).
3. Merge the PR. Wait until `describe-table` shows `date-index` **ACTIVE**.
4. Then run the Query examples below.

Until ACTIVE, `GetItem` by `(genre, date)` still works; Query on `date-index` does not.

```bash
aws dynamodb describe-table --table-name MusicKPIs --region eu-west-1 \
  --query "Table.GlobalSecondaryIndexes[*].{Name:IndexName,Status:IndexStatus}"
```

---

## 1. Get All Metrics for a Genre on a Specific Date

```bash
aws dynamodb get-item \
  --table-name MusicKPIs \
  --key '{"genre":{"S":"Pop"},"date":{"S":"2024-06-25"}}'
```

---

## 2. Query All Dates for a Genre (Time Series)

```bash
aws dynamodb query \
  --table-name MusicKPIs \
  --key-condition-expression "genre = :g" \
  --expression-attribute-values '{":g":{"S":"Pop"}}'
```

---

## 3. Query Genre Metrics for a Date Range

```bash
aws dynamodb query \
  --table-name MusicKPIs \
  --key-condition-expression "genre = :g AND #d BETWEEN :start AND :end" \
  --expression-attribute-names '{"#d":"date"}' \
  --expression-attribute-values '{
    ":g":{"S":"Pop"},
    ":start":{"S":"2024-06-01"},
    ":end":{"S":"2024-06-30"}
  }'
```

---

## 4. Get Top 3 Songs for a Genre/Date

```bash
aws dynamodb get-item \
  --table-name MusicKPIs \
  --key '{"genre":{"S":"Pop"},"date":{"S":"2024-06-25"}}' \
  --projection-expression "top_3_songs"
```

**Result:**
```json
{
  "Item": {
    "top_3_songs": {
      "L": [
        {"M": {"song_id": {"S": "4dBa8T7oDV9WvGr7kVS4Ez"}, "song_name": {"S": "Song A"}, "listen_count": {"N": "42"}}},
        {"M": {"song_id": {"S": "6nVcjD1..."}, "song_name": {"S": "Song B"}, "listen_count": {"N": "38"}}},
        {"M": {"song_id": {"S": "8mLzqP2..."}, "song_name": {"S": "Song C"}, "listen_count": {"N": "27"}}}
      ]
    }
  }
}
```

---

## 5. Get Top 5 Genres of the Day

```bash
aws dynamodb get-item \
  --table-name MusicKPIs \
  --key '{"genre":{"S":"Pop"},"date":{"S":"2024-06-25"}}' \
  --projection-expression "top_5_genres"
```

---

## 6. Query All Genres for a Date (Full Daily Report)

Use the `date-index` GSI (`date` partition key, `genre` sort key). Do not Scan.

```bash
aws dynamodb query \
  --table-name MusicKPIs \
  --index-name date-index \
  --key-condition-expression "#d = :date" \
  --expression-attribute-names '{"#d":"date"}' \
  --expression-attribute-values '{":date":{"S":"2024-06-25"}}'
```

Requires `metrics-reader-policy` (GetItem/Query on the table ARN **and** `.../index/date-index`). Scan is not granted.

---

## 7. Get High-Level Summary for a Date

Project only the scalar metrics (exclude nested lists):

```bash
aws dynamodb query \
  --table-name MusicKPIs \
  --index-name date-index \
  --key-condition-expression "#d = :date" \
  --expression-attribute-names '{"#d":"date"}' \
  --expression-attribute-values '{":date":{"S":"2024-06-25"}}' \
  --projection-expression "genre, #d, listen_count, unique_listener_count, total_listening_time, avg_listening_time_per_user"
```

---

## 8. Python SDK Example (Boto3)

```python
import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table("MusicKPIs")

# Get single item
response = table.get_item(Key={"genre": "Pop", "date": "2024-06-25"})
item = response.get("Item")

# Query all dates for a genre
response = table.query(
    KeyConditionExpression=Key("genre").eq("Pop")
)
items = response.get("Items", [])

# Query with date range
response = table.query(
    KeyConditionExpression=
        Key("genre").eq("Pop") &
        Key("date").between("2024-06-01", "2024-06-30")
)

# Get only specific attributes
response = table.get_item(
    Key={"genre": "Pop", "date": "2024-06-25"},
    ProjectionExpression="listen_count, unique_listener_count, top_3_songs"
)
```

---

## 9. Common Aggregations (Client-Side)

DynamoDB doesn't support aggregation queries natively. Query `date-index`, then reduce client-side:

```python
import boto3
from functools import reduce
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table("MusicKPIs")

# Get total listens across all genres on a date (client-side sum after GSI Query)
def total_listens_for_date(date: str) -> int:
    response = table.query(
        IndexName="date-index",
        KeyConditionExpression=Key("date").eq(date),
        ProjectionExpression="listen_count",
    )
    return reduce(
        lambda acc, item: acc + int(item.get("listen_count", 0)),
        response.get("Items", []),
        0
    )

print(total_listens_for_date("2024-06-25"))
```

---

## Item Structure Reference

```json
{
  "genre": "Pop",
  "date": "2024-06-25",
  "listen_count": 1500,
  "unique_listener_count": 800,
  "total_listening_time": 540000,
  "avg_listening_time_per_user": 675.0,
  "top_3_songs": [
    {"song_id": "abc123", "song_name": "Hit Song", "listen_count": 42}
  ],
  "top_5_genres": [
    {"genre_id": "Pop", "genre_name": "Pop", "listen_count": 1500}
  ]
}
```
