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
        StructField("listen_time", TimestampType(), False),
    ])
    return spark.createDataFrame(rows, schema)


def make_catalog(spark, rows):
    schema = StructType([
        StructField("track_id", StringType(), False),
        StructField("track_name", StringType(), False),
        StructField("artists", StringType(), True),
        StructField("track_genre", StringType(), False),
        StructField("duration_ms", DoubleType(), True),
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
            Row(user_id="u1", track_id="t1", listen_time=TS),
            Row(user_id="u2", track_id="t2", listen_time=TS),
        ])
        catalog = make_catalog(spark, [
            Row(track_id="t1", track_name="Song A", artists="Art1", track_genre="Pop", duration_ms=180000.0),
            Row(track_id="t2", track_name="Song B", artists="Art2", track_genre="Rock", duration_ms=200000.0),
        ])
        result = join_activity_to_catalog(activity, catalog)
        rows = {r.user_id: r for r in result.collect()}
        assert len(rows) == 2
        assert rows["u1"].genre == "Pop"
        assert rows["u2"].genre == "Rock"

    def test_unmatched_rows_excluded(self, spark):
        activity = make_activity(spark, [
            Row(user_id="u1", track_id="t1", listen_time=TS),
            Row(user_id="u2", track_id="t_unknown", listen_time=TS),
        ])
        catalog = make_catalog(spark, [
            Row(track_id="t1", track_name="Song A", artists="Art1", track_genre="Pop", duration_ms=180000.0),
        ])
        result = join_activity_to_catalog(activity, catalog)
        assert result.count() == 1

    def test_null_duration_contributes_zero(self, spark):
        activity = make_activity(spark, [
            Row(user_id="u1", track_id="t1", listen_time=TS),
        ])
        catalog = make_catalog(spark, [
            Row(track_id="t1", track_name="Song A", artists="Art1", track_genre="Pop", duration_ms=None),
        ])
        result = join_activity_to_catalog(activity, catalog)
        row = result.first()
        assert row.duration_seconds == 0.0

    def test_numeric_genre_rows_dropped(self, spark):
        # Column-shifted source rows surface as numeric/blank genres (e.g. tempo
        # "60.015", key "3"). Guard must drop them so they never reach metrics.
        activity = make_activity(spark, [
            Row(user_id="u1", track_id="t1", listen_time=TS),
            Row(user_id="u2", track_id="t2", listen_time=TS),
            Row(user_id="u3", track_id="t3", listen_time=TS),
            Row(user_id="u4", track_id="t4", listen_time=TS),
        ])
        catalog = make_catalog(spark, [
            Row(track_id="t1", track_name="Song A", artists="A", track_genre="Pop", duration_ms=60000.0),
            Row(track_id="t2", track_name="Song B", artists="A", track_genre="60.015", duration_ms=60000.0),
            Row(track_id="t3", track_name="Song C", artists="A", track_genre="3", duration_ms=60000.0),
            Row(track_id="t4", track_name="Song D", artists="A", track_genre="", duration_ms=60000.0),
        ])
        result = join_activity_to_catalog(activity, catalog)
        genres = {r.genre for r in result.collect()}
        assert genres == {"Pop"}

    def test_genre_with_letters_and_digits_kept(self, spark):
        # Legit genres can contain digits (e.g. "trip-hop", "j-pop", "80s").
        activity = make_activity(spark, [
            Row(user_id="u1", track_id="t1", listen_time=TS),
        ])
        catalog = make_catalog(spark, [
            Row(track_id="t1", track_name="Song A", artists="A", track_genre="80s-pop", duration_ms=60000.0),
        ])
        result = join_activity_to_catalog(activity, catalog)
        assert result.first().genre == "80s-pop"


class TestComputeGenreMetrics:
    def _enriched(self, spark):
        activity = make_activity(spark, [
            Row(user_id="u1", track_id="t1", listen_time=TS),
            Row(user_id="u2", track_id="t1", listen_time=TS),
            Row(user_id="u1", track_id="t2", listen_time=TS),
            Row(user_id="u1", track_id="t3", listen_time=TS2),
            Row(user_id="u2", track_id="t4", listen_time=TS2),
        ])
        catalog = make_catalog(spark, [
            Row(track_id="t1", track_name="Song A", artists="A1", track_genre="Pop", duration_ms=60000.0),
            Row(track_id="t2", track_name="Song B", artists="A2", track_genre="Rock", duration_ms=120000.0),
            Row(track_id="t3", track_name="Song C", artists="A3", track_genre="Pop", duration_ms=90000.0),
            Row(track_id="t4", track_name="Song D", artists="A4", track_genre="Rock", duration_ms=150000.0),
        ])
        return join_activity_to_catalog(activity, catalog)

    def test_one_record_per_pair(self, spark):
        result = compute_genre_metrics(self._enriched(spark))
        assert result.count() == 4

    def test_aggregate_values_correct(self, spark):
        activity = make_activity(spark, [
            Row(user_id="u1", track_id="t1", listen_time=TS),
            Row(user_id="u1", track_id="t1", listen_time=TS),
            Row(user_id="u2", track_id="t1", listen_time=TS),
        ])
        catalog = make_catalog(spark, [
            Row(track_id="t1", track_name="Song A", artists="A1", track_genre="Pop", duration_ms=60000.0),
        ])
        enriched = join_activity_to_catalog(activity, catalog)
        result = compute_genre_metrics(enriched)
        row = result.first()
        assert row.listen_count == 3
        assert row.unique_listener_count == 2
        assert abs(row.total_listening_time - 180.0) < 0.001
        assert abs(row.avg_listening_time_per_user - 90.0) < 0.001

    def test_null_duration_contributes_zero(self, spark):
        activity = make_activity(spark, [
            Row(user_id="u1", track_id="t1", listen_time=TS),
        ])
        catalog = make_catalog(spark, [
            Row(track_id="t1", track_name="Song A", artists="A1", track_genre="Pop", duration_ms=None),
        ])
        enriched = join_activity_to_catalog(activity, catalog)
        result = compute_genre_metrics(enriched)
        row = result.first()
        assert row.total_listening_time == 0.0
        assert row.avg_listening_time_per_user == 0.0


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
            Row(track_id=f"t{i}", track_name=f"Song{i}", artists="A", track_genre="Pop", duration_ms=10000.0)
            for i in range(1, 6)
        ]
        # play counts: t1=5, t2=4, t3=3, t4=2, t5=1
        activity_rows = (
            [Row(user_id="u1", track_id="t1", listen_time=TS)] * 5 +
            [Row(user_id="u1", track_id="t2", listen_time=TS)] * 4 +
            [Row(user_id="u1", track_id="t3", listen_time=TS)] * 3 +
            [Row(user_id="u1", track_id="t4", listen_time=TS)] * 2 +
            [Row(user_id="u1", track_id="t5", listen_time=TS)] * 1
        )
        enriched = self._enriched(spark, catalog_rows, activity_rows)
        result = compute_top_songs(enriched)
        row = result.first()
        assert len(row.top_3_songs) == 3

    def test_top_3_songs_tiebreak_alphabetical(self, spark):
        catalog_rows = [
            Row(track_id="t1", track_name="Zebra", artists="A", track_genre="Pop", duration_ms=10000.0),
            Row(track_id="t2", track_name="Apple", artists="A", track_genre="Pop", duration_ms=10000.0),
            Row(track_id="t3", track_name="Mango", artists="A", track_genre="Pop", duration_ms=10000.0),
            Row(track_id="t4", track_name="Banana", artists="A", track_genre="Pop", duration_ms=10000.0),
        ]
        # all 4 songs have listen_count=1 — alphabetical tiebreak
        activity_rows = [
            Row(user_id="u1", track_id="t1", listen_time=TS),
            Row(user_id="u1", track_id="t2", listen_time=TS),
            Row(user_id="u1", track_id="t3", listen_time=TS),
            Row(user_id="u1", track_id="t4", listen_time=TS),
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
            Row(track_id="t1", track_name="Song1", artists="A", track_genre="Pop", duration_ms=10000.0),
            Row(track_id="t2", track_name="Song2", artists="A", track_genre="Pop", duration_ms=10000.0),
        ]
        activity_rows = [
            Row(user_id="u1", track_id="t1", listen_time=TS),
            Row(user_id="u1", track_id="t2", listen_time=TS),
        ]
        enriched = self._enriched(spark, catalog_rows, activity_rows)
        result = compute_top_songs(enriched)
        row = result.first()
        assert len(row.top_3_songs) == 2


class TestComputeTopGenresPerDay:
    def _make_metrics(self, spark, genre_plays_by_date):
        rows = []
        for date_str, genre_data in genre_plays_by_date.items():
            for genre, plays in genre_data.items():
                rows.append(Row(
                    date=date_str,
                    genre=genre,
                    listen_count=plays,
                    unique_listener_count=1,
                    total_listening_time=float(plays * 60),
                    avg_listening_time_per_user=float(plays * 60),
                ))
        schema = StructType([
            StructField("date", StringType(), False),
            StructField("genre", StringType(), False),
            StructField("listen_count", LongType(), False),
            StructField("unique_listener_count", LongType(), False),
            StructField("total_listening_time", DoubleType(), False),
            StructField("avg_listening_time_per_user", DoubleType(), False),
        ])
        return spark.createDataFrame(rows, schema)

    def test_top_5_genres_truncated(self, spark):
        genre_data = {f"Genre{i}": 10 - i for i in range(8)}
        metrics = self._make_metrics(spark, {"2026-05-25": genre_data})
        result = compute_top_genres_per_day(metrics)
        row = result.first()
        assert len(row.top_5_genres) == 5

    def test_top_5_genres_tiebreak(self, spark):
        genre_data = {f"Genre{chr(ord('A') + i)}": 10 for i in range(6)}
        metrics = self._make_metrics(spark, {"2026-05-25": genre_data})
        result = compute_top_genres_per_day(metrics)
        row = result.first()
        names = [g.genre_name for g in row.top_5_genres]
        assert names == sorted(names)
        assert len(names) == 5

    def test_top_5_genres_same_per_date(self, spark):
        genre_data = {f"Genre{i}": 10 - i for i in range(8)}
        metrics = self._make_metrics(spark, {"2026-05-25": genre_data})
        result = compute_top_genres_per_day(metrics)
        rows = result.collect()
        first_list = rows[0].top_5_genres
        for row in rows[1:]:
            assert row.top_5_genres == first_list, "SC-006: all rows on same date must have identical top_5_genres"

    def test_fewer_than_5_genres_preserved(self, spark):
        genre_data = {f"Genre{i}": 10 - i for i in range(3)}
        metrics = self._make_metrics(spark, {"2026-05-25": genre_data})
        result = compute_top_genres_per_day(metrics)
        row = result.first()
        assert len(row.top_5_genres) == 3
