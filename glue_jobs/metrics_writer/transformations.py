import decimal
import json


def parquet_rows_to_ddb_items(rows: list) -> list:
    """Convert Phase 3 Parquet row dicts to DynamoDB item dicts."""
    items = []
    for row in rows:
        if int(row.get("listen_count", 0)) < 1:
            continue

        top_3 = [
            {
                "song_id": str(s["song_id"]),
                "song_name": str(s["song_name"]),
                "listen_count": int(s["listen_count"]),
            }
            for s in (row.get("top_3_songs") or [])
        ]

        top_5 = [
            {
                "genre_id": str(g["genre_id"]),
                "genre_name": str(g["genre_name"]),
                "listen_count": int(g["listen_count"]),
            }
            for g in (row.get("top_5_genres") or [])
        ]

        avg_raw = row.get("avg_listening_time_per_user", 0)
        avg_val = decimal.Decimal(str(avg_raw))

        item_size = len(
            json.dumps(
                {
                    "genre": row["genre"],
                    "date": row["date"],
                    "listen_count": int(row["listen_count"]),
                    "unique_listener_count": int(row["unique_listener_count"]),
                    "total_listening_time": int(row["total_listening_time"]),
                    "avg_listening_time_per_user": float(avg_val),
                    "top_3_songs": top_3,
                    "top_5_genres": top_5,
                }
            ).encode("utf-8")
        )
        if item_size > 400 * 1024:
            raise ValueError(
                f"Item for (genre={row['genre']}, date={row['date']}) exceeds 400 KB "
                f"({item_size} bytes). Upstream Phase 3 regression."
            )

        items.append(
            {
                "genre": str(row["genre"]),
                "date": str(row["date"]),
                "listen_count": int(row["listen_count"]),
                "unique_listener_count": int(row["unique_listener_count"]),
                "total_listening_time": int(row["total_listening_time"]),
                "avg_listening_time_per_user": avg_val,
                "top_3_songs": top_3,
                "top_5_genres": top_5,
            }
        )
    return items


def build_transact_batch(items: list, table_name: str) -> list:
    """Build list of TransactWriteItems Put entries for a single atomic run."""
    if len(items) > 100:
        raise ValueError(
            f"Record count {len(items)} exceeds DynamoDB TransactWriteItems limit of 100. "
            "Upstream run-volume bound violated."
        )

    def _to_ddb_value(v):
        if isinstance(v, str):
            return {"S": v}
        if isinstance(v, bool):
            return {"BOOL": v}
        if isinstance(v, (int, float, decimal.Decimal)):
            return {"N": str(v)}
        if isinstance(v, list):
            return {"L": [_to_ddb_value(i) for i in v]}
        if isinstance(v, dict):
            return {"M": {k: _to_ddb_value(vv) for k, vv in v.items()}}
        return {"S": str(v)}

    def _item_to_ddb(item: dict) -> dict:
        return {k: _to_ddb_value(v) for k, v in item.items()}

    return [
        {
            "Put": {
                "TableName": table_name,
                "Item": _item_to_ddb(item),
            }
        }
        for item in items
    ]
