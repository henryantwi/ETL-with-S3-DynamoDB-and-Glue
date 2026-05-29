# Sample DynamoDB Queries for MusicKPIs

The `MusicKPIs` table uses a composite primary key:
- **Partition Key**: `genre` (String)
- **Sort Key**: `date` (String, format: `YYYY-MM-DD`)

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

## 6. Scan All Genres for a Date (Full Daily Report)

```bash
aws dynamodb query \
  --table-name MusicKPIs \
  --index-name date-index \
  --key-condition-expression "#d = :date" \
  --expression-attribute-names '{"#d":"date"}' \
  --expression-attribute-values '{"date":{"S":"2024-06-25"}}'
```

*Note: Requires a GSI on `date` as partition key. If not present, use Scan with filter:*

```bash
aws dynamodb scan \
  --table-name MusicKPIs \
  --filter-expression "#d = :date" \
  --expression-attribute-names '{"#d":"date"}' \
  --expression-attribute-values '{"date":{"S":"2024-06-25"}}'
```

---

## 7. Get High-Level Summary for a Date

Project only the scalar metrics (exclude nested lists):

```bash
aws dynamodb scan \
  --table-name MusicKPIs \
  --filter-expression "#d = :date" \
  --expression-attribute-names '{"#d":"date"}' \
  --expression-attribute-values '{"date":{"S":"2024-06-25"}}' \
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

DynamoDB doesn't support aggregation queries natively. Use Scan + reduce:

```python
import boto3
from functools import reduce

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table("MusicKPIs")

# Get total listens across all genres on a date
def total_listens_for_date(date: str) -> int:
    response = table.scan(
        FilterExpression=boto3.dynamodb.conditions.Attr("date").eq(date),
        ProjectionExpression="listen_count"
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
