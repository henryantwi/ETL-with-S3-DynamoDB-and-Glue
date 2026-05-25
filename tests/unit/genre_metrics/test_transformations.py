import datetime
import pytest
from pyspark.sql import Row
from pyspark.sql.types import (
    StructType, StructField, StringType, DoubleType, TimestampType, LongType, DateType
)

from glue_jobs.genre_metrics.transformations import (
    join_activity_to_catalog,
    compute_genre_metrics,
    compute_top_songs,
    compute_top_genres_per_day,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_activity(spark, rows):
    schema = StructType([
        StructField("user_id", StringType(), False),
        StructField("track_id", StringType(), False),
        StructField("listened_at", TimestampType(), False),
    ])
    return spark.createDataFrame(rows, schema)


def make_catalog(spark, rows):
    schema = StructType([
        StructField("track_id", StringType(), False),
        StructField("song_name", StringType(), False),
        StructField("artist_name", StringType(), True),
        StructField("genre", StringType(), False),
        StructField("duration_seconds", DoubleType(), True),
    ])
    return spark.createDataFrame(rows, schema)


TS = datetime.datetime(2026, 5, 25, 12, 0, 0)
TS2 = datetime.datetime(2026, 5, 26, 12, 0, 0)


# ---------------------------------------------------------------------------
# T013: join + aggregation tests
# ---------------------------------------------------------------------------

class TestJoinActivityToCatalog:
    def test_join_produces_enriched_events(self, spark):
        activity = make_activity(spark, [
            Row(user_id="u1", track_id="t1", listened_at=TS),
            Row(user_id="u2", track_id="t2", listened_at=TS),
        ])
        catalog = make_catalog(spark, [
            Row(track_id="t1", song_name="Song A", artist_name="Art1", genre="Pop", duration_seconds=180.0),
            Row(track_id="t2", song_name="Song B", artist_name="Art2", genre="Rock", duration_seconds=200.0),
        ])
        result = join_activity_to_catalog(activity, catalog)
        rows = {r.user_id: r for r in result.collect()}
        assert len(rows) == 2
        assert rows["u1"].genre == "Pop"
        assert rows["u2"].genre == "Rock"

    def test_unmatched_rows_excluded(self, spark):
        activity = make_activity(spark, [
            Row(user_id="u1", track_id="t1", listened_at=TS),
            Row(user_id="u2", track_id="t_unknown", listened_at=TS),
        ])
        catalog = make_catalog(spark, [
            Row(track_id="t1", song_name="Song A", artist_name="Art1", genre="Pop", duration_seconds=180.0),
        ])
        result = join_activity_to_catalog(activity, catalog)
        assert result.count() == 1

    def test_null_duration_contributes_zero(self, spark):
        activity = make_activity(spark, [
            Row(user_id="u1", track_id="t1", listened_at=TS),
        ])
        catalog = make_catalog(spark, [
            Row(track_id="t1", song_name="Song A", artist_name="Art1", genre="Pop", duration_seconds=None),
        ])
        result = join_activity_to_catalog(activity, catalog)
        row = result.first()
        assert row.duration_seconds == 0.0


class TestComputeGenreMetrics:
    def _enriched(self, spark):
        activity = make_activity(spark, [
            Row(user_id="u1", track_id="t1", listened_at=TS),
            Row(user_id="u2", track_id="t1", listened_at=TS),
            Row(user_id="u1", track_id="t2", listened_at=TS),
            Row(user_id="u1", track_id="t3", listened_at=TS2),
            Row(user_id="u2", track_id="t4", listened_at=TS2),
        ])
        catalog = make_catalog(spark, [
            Row(track_id="t1", song_name="Song A", artist_name="A1", genre="Pop", duration_seconds=60.0),
            Row(track_id="t2", song_name="Song B", artist_name="A2", genre="Rock", duration_seconds=120.0),
            Row(track_id="t3", song_name="Song C", artist_name="A3", genre="Pop", duration_seconds=90.0),
            Row(track_id="t4", song_name="Song D", artist_name="A4", genre="Rock", duration_seconds=150.0),
        ])
        return join_activity_to_catalog(activity, catalog)

    def test_one_record_per_pair(self, spark):
        result = compute_genre_metrics(self._enriched(spark))
        assert result.count() == 4

    def test_aggregate_values_correct(self, spark):
        activity = make_activity(spark, [
            Row(user_id="u1", track_id="t1", listened_at=TS),
            Row(user_id="u1", track_id="t1", listened_at=TS),
            Row(user_id="u2", track_id="t1", listened_at=TS),
        ])
        catalog = make_catalog(spark, [
            Row(track_id="t1", song_name="Song A", artist_name="A1", genre="Pop", duration_seconds=60.0),
        ])
        enriched = join_activity_to_catalog(activity, catalog)
        result = compute_genre_metrics(enriched)
        row = result.first()
        assert row.total_plays == 3
        assert row.distinct_users == 2
        assert abs(row.total_listening_time_seconds - 180.0) < 0.001
        assert abs(row.avg_listening_time_per_user_seconds - 90.0) < 0.001

    def test_null_duration_contributes_zero(self, spark):
        activity = make_activity(spark, [
            Row(user_id="u1", track_id="t1", listened_at=TS),
        ])
        catalog = make_catalog(spark, [
            Row(track_id="t1", song_name="Song A", artist_name="A1", genre="Pop", duration_seconds=None),
        ])
        enriched = join_activity_to_catalog(activity, catalog)
        result = compute_genre_metrics(enriched)
        row = result.first()
        assert row.total_listening_time_seconds == 0.0
        assert row.avg_listening_time_per_user_seconds == 0.0


# ---------------------------------------------------------------------------
# T023/T024: top-N tests
# ---------------------------------------------------------------------------

class TestComputeTopSongs:
    def _enriched(self, spark, rows_catalog=None, rows_activity=None):
        activity = make_activity(spark, rows_activity)
        catalog = make_catalog(spark, rows_catalog)
        return join_activity_to_catalog(activity, catalog)

    def test_top_3_songs_truncated(self, spark):
        catalog_rows = [
            Row(track_id=f"t{i}", song_name=f"Song{i}", artist_name="A", genre="Pop", duration_seconds=10.0)
            for i in range(1, 6)
        ]
        # play counts: t1=5, t2=4, t3=3, t4=2, t5=1
        activity_rows = (
            [Row(user_id="u1", track_id="t1", listened_at=TS)] * 5 +
            [Row(user_id="u1", track_id="t2", listened_at=TS)] * 4 +
            [Row(user_id="u1", track_id="t3", listened_at=TS)] * 3 +
            [Row(user_id="u1", track_id="t4", listened_at=TS)] * 2 +
            [Row(user_id="u1", track_id="t5", listened_at=TS)] * 1
        )
        enriched = self._enriched(spark, catalog_rows, activity_rows)
        result = compute_top_songs(enriched)
        row = result.first()
        assert len(row.top_3_songs) == 3

    def test_top_3_songs_tiebreak_alphabetical(self, spark):
        catalog_rows = [
            Row(track_id="t1", song_name="Zebra", artist_name="A", genre="Pop", duration_seconds=10.0),
            Row(track_id="t2", song_name="Apple", artist_name="A", genre="Pop", duration_seconds=10.0),
            Row(track_id="t3", song_name="Mango", artist_name="A", genre="Pop", duration_seconds=10.0),
            Row(track_id="t4", song_name="Banana", artist_name="A", genre="Pop", duration_seconds=10.0),
        ]
        # all 4 songs have play_count=1 — alphabetical tiebreak
        activity_rows = [
            Row(user_id="u1", track_id="t1", listened_at=TS),
            Row(user_id="u1", track_id="t2", listened_at=TS),
            Row(user_id="u1", track_id="t3", listened_at=TS),
            Row(user_id="u1", track_id="t4", listened_at=TS),
        ]
        enriched = self._enriched(spark, catalog_rows, activity_rows)
        result = compute_top_songs(enriched)
        row = result.first()
        names = [s.song_name for s in row.top_3_songs]
        assert names == sorted(names)
        assert len(names) == 3
        assert "Apple" in names
        assert "Banana" in names
        assert "Mango" in names

    def test_fewer_than_3_songs_preserved(self, spark):
        catalog_rows = [
            Row(track_id="t1", song_name="Song1", artist_name="A", genre="Pop", duration_seconds=10.0),
            Row(track_id="t2", song_name="Song2", artist_name="A", genre="Pop", duration_seconds=10.0),
        ]
        activity_rows = [
            Row(user_id="u1", track_id="t1", listened_at=TS),
            Row(user_id="u1", track_id="t2", listened_at=TS),
        ]
        enriched = self._enriched(spark, catalog_rows, activity_rows)
        result = compute_top_songs(enriched)
        row = result.first()
        assert len(row.top_3_songs) == 2


class TestComputeTopGenresPerDay:
    def _make_metrics(self, spark, genre_plays_by_date):
        from pyspark.sql.types import IntegerType
        rows = []
        for date_str, genre_data in genre_plays_by_date.items():
            for genre, plays in genre_data.items():
                rows.append(Row(
                    date=date_str,
                    genre=genre,
                    total_plays=plays,
                    distinct_users=1,
                    total_listening_time_seconds=float(plays * 60),
                    avg_listening_time_per_user_seconds=float(plays * 60),
                ))
        schema = StructType([
            StructField("date", StringType(), False),
            StructField("genre", StringType(), False),
            StructField("total_plays", LongType(), False),
            StructField("distinct_users", LongType(), False),
            StructField("total_listening_time_seconds", DoubleType(), False),
            StructField("avg_listening_time_per_user_seconds", DoubleType(), False),
        ])
        return spark.createDataFrame(rows, schema)

    def test_top_5_genres_truncated(self, spark):
        genre_data = {f"Genre{i}": 10 - i for i in range(8)}
        metrics = self._make_metrics(spark, {"2026-05-25": genre_data})
        result = compute_top_genres_per_day(metrics)
        row = result.first()
        assert len(row.top_5_genres_of_day) == 5

    def test_top_5_genres_tiebreak(self, spark):
        genre_data = {f"Genre{chr(ord('A') + i)}": 10 for i in range(6)}
        metrics = self._make_metrics(spark, {"2026-05-25": genre_data})
        result = compute_top_genres_per_day(metrics)
        row = result.first()
        names = [g.genre for g in row.top_5_genres_of_day]
        assert names == sorted(names)
        assert len(names) == 5

    def test_top_5_genres_same_per_date(self, spark):
        genre_data = {f"Genre{i}": 10 - i for i in range(8)}
        metrics = self._make_metrics(spark, {"2026-05-25": genre_data})
        result = compute_top_genres_per_day(metrics)
        rows = result.collect()
        first_list = rows[0].top_5_genres_of_day
        for row in rows[1:]:
            assert row.top_5_genres_of_day == first_list, "SC-006: all rows on same date must have identical top_5_genres_of_day"

    def test_fewer_than_5_genres_preserved(self, spark):
        genre_data = {f"Genre{i}": 10 - i for i in range(3)}
        metrics = self._make_metrics(spark, {"2026-05-25": genre_data})
        result = compute_top_genres_per_day(metrics)
        row = result.first()
        assert len(row.top_5_genres_of_day) == 3
