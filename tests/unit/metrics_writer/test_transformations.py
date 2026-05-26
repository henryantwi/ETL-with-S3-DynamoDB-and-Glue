import decimal
import pytest

from glue_jobs.metrics_writer.transformations import (
    parquet_rows_to_ddb_items,
    build_transact_batch,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_ROW = {
    "genre": "acoustic",
    "date": "2024-06-25",
    "listen_count": 1200,
    "unique_listener_count": 340,
    "total_listening_time": 864000,
    "avg_listening_time_per_user": 2541.176470588235,
    "top_3_songs": [
        {"song_id": "trk_001", "song_name": "Song A", "listen_count": 80},
        {"song_id": "trk_002", "song_name": "Song B", "listen_count": 65},
    ],
    "top_5_genres": [
        {"genre_id": "pop",      "genre_name": "Pop",      "listen_count": 9800},
        {"genre_id": "acoustic", "genre_name": "Acoustic", "listen_count": 1200},
    ],
}


def _make_rows(n=1, **overrides):
    base = dict(SAMPLE_ROW)
    base.update(overrides)
    return [base] * n


# ---------------------------------------------------------------------------
# T010: parquet_rows_to_ddb_items
# ---------------------------------------------------------------------------

class TestParquetRowsToDdbItems:
    def test_basic_field_presence(self):
        items = parquet_rows_to_ddb_items(_make_rows())
        assert len(items) == 1
        item = items[0]
        for field in ("genre", "date", "listen_count", "unique_listener_count",
                      "total_listening_time", "avg_listening_time_per_user",
                      "top_3_songs", "top_5_genres"):
            assert field in item, f"missing field: {field}"

    def test_string_fields_are_str(self):
        item = parquet_rows_to_ddb_items(_make_rows())[0]
        assert isinstance(item["genre"], str)
        assert isinstance(item["date"], str)

    def test_integer_fields_are_int(self):
        item = parquet_rows_to_ddb_items(_make_rows())[0]
        assert isinstance(item["listen_count"], int)
        assert isinstance(item["unique_listener_count"], int)
        assert isinstance(item["total_listening_time"], int)

    def test_avg_is_decimal(self):
        item = parquet_rows_to_ddb_items(_make_rows())[0]
        assert isinstance(item["avg_listening_time_per_user"], decimal.Decimal)

    def test_top_3_songs_structure(self):
        item = parquet_rows_to_ddb_items(_make_rows())[0]
        songs = item["top_3_songs"]
        assert isinstance(songs, list)
        assert len(songs) <= 3
        for s in songs:
            assert "song_id" in s
            assert "song_name" in s
            assert "listen_count" in s

    def test_top_5_genres_structure(self):
        item = parquet_rows_to_ddb_items(_make_rows())[0]
        genres = item["top_5_genres"]
        assert isinstance(genres, list)
        assert len(genres) <= 5
        for g in genres:
            assert "genre_id" in g
            assert "genre_name" in g
            assert "listen_count" in g

    def test_zero_listen_row_omitted(self):
        rows = [dict(SAMPLE_ROW, listen_count=0)]
        items = parquet_rows_to_ddb_items(rows)
        assert items == []

    def test_empty_top_n_lists_allowed(self):
        row = dict(SAMPLE_ROW, top_3_songs=[], top_5_genres=[])
        item = parquet_rows_to_ddb_items([row])[0]
        assert item["top_3_songs"] == []
        assert item["top_5_genres"] == []

    def test_multiple_rows(self):
        rows = [
            dict(SAMPLE_ROW, genre="acoustic"),
            dict(SAMPLE_ROW, genre="pop"),
        ]
        items = parquet_rows_to_ddb_items(rows)
        assert len(items) == 2
        genres = {i["genre"] for i in items}
        assert genres == {"acoustic", "pop"}

    def test_empty_input_returns_empty(self):
        assert parquet_rows_to_ddb_items([]) == []


# ---------------------------------------------------------------------------
# T011: build_transact_batch
# ---------------------------------------------------------------------------

class TestBuildTransactBatch:
    def _items(self, n=2):
        return parquet_rows_to_ddb_items(
            [dict(SAMPLE_ROW, genre=f"genre_{i}") for i in range(n)]
        )

    def test_returns_list_of_put_entries(self):
        items = self._items(3)
        batch = build_transact_batch(items, "MusicKPIs")
        assert isinstance(batch, list)
        assert len(batch) == 3
        for entry in batch:
            assert "Put" in entry
            assert entry["Put"]["TableName"] == "MusicKPIs"
            assert "Item" in entry["Put"]

    def test_put_not_update(self):
        items = self._items(1)
        batch = build_transact_batch(items, "MusicKPIs")
        assert "Put" in batch[0]
        assert "Update" not in batch[0]

    def test_item_values_preserved(self):
        items = self._items(1)
        batch = build_transact_batch(items, "MusicKPIs")
        assert batch[0]["Put"]["Item"]["genre"] == {"S": "genre_0"}

    def test_empty_input_returns_empty(self):
        batch = build_transact_batch([], "MusicKPIs")
        assert batch == []

    def test_over_100_records_raises(self):
        rows = [dict(SAMPLE_ROW, genre=f"genre_{i}") for i in range(101)]
        items = parquet_rows_to_ddb_items(rows)
        with pytest.raises(ValueError, match="101"):
            build_transact_batch(items, "MusicKPIs")

    def test_exactly_100_records_ok(self):
        rows = [dict(SAMPLE_ROW, genre=f"genre_{i}") for i in range(100)]
        items = parquet_rows_to_ddb_items(rows)
        batch = build_transact_batch(items, "MusicKPIs")
        assert len(batch) == 100
