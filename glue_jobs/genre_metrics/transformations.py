import pyspark.sql.functions as F
from pyspark.sql import DataFrame, Window


def join_activity_to_catalog(activity_df: DataFrame, catalog_df: DataFrame) -> DataFrame:
    enriched = activity_df.join(catalog_df, on="track_id", how="inner")
    enriched = enriched.withColumn("date", F.col("listen_time").cast("date").cast("string"))
    # duration_ms (int) → duration_seconds (float)
    enriched = enriched.withColumn(
        "duration_seconds", F.coalesce(F.col("duration_ms").cast("double") / 1000.0, F.lit(0.0))
    )
    # actual song name column is track_name
    enriched = (
        enriched.select("user_id", "track_id", "date", "track_genre", "track_name", "duration_seconds")
        .withColumnRenamed("track_genre", "genre")
        .withColumnRenamed("track_name", "song_name")
    )
    # Schema guard: a valid genre always contains a letter. Drop null/blank/
    # purely-numeric genres so any column-shifted source row (e.g. genre="60.015")
    # never reaches the metrics or DynamoDB. Defense-in-depth behind the escape fix.
    return enriched.filter(F.col("genre").isNotNull() & (F.col("genre").rlike("[A-Za-z]")))


def compute_genre_metrics(enriched_df: DataFrame) -> DataFrame:
    metrics = enriched_df.groupBy("date", "genre").agg(
        F.count("*").alias("listen_count"),
        F.countDistinct("user_id").alias("unique_listener_count"),
        F.sum("duration_seconds").alias("total_listening_time"),
    )
    metrics = metrics.withColumn(
        "avg_listening_time_per_user",
        F.when(
            F.col("unique_listener_count") > 0,
            F.col("total_listening_time") / F.col("unique_listener_count"),
        ).otherwise(F.lit(0.0)),
    )
    return metrics


def compute_top_songs(enriched_df: DataFrame) -> DataFrame:
    song_counts = enriched_df.groupBy("date", "genre", "track_id", "song_name").agg(
        F.count("*").cast("long").alias("play_count")
    )
    # rank 1 = highest play_count; tiebreak asc song_name
    window = Window.partitionBy("date", "genre").orderBy(F.desc("play_count"), F.asc("song_name"))
    ranked = song_counts.withColumn("rn", F.row_number().over(window)).filter(F.col("rn") <= 3)
    # Collect with rn first; sort_array ascending by rn preserves correct rank order
    top_songs = ranked.groupBy("date", "genre").agg(
        F.sort_array(
            F.collect_list(
                F.struct(
                    F.col("rn").alias("rn"),
                    F.col("track_id").alias("track_id"),
                    F.col("song_name").alias("song_name"),
                    F.col("play_count").alias("play_count"),
                )
            )
        ).alias("_sorted")
    )
    # Strip rn; output {song_id, song_name, listen_count} to match Phase 4 contract
    top_songs = top_songs.withColumn(
        "top_3_songs",
        F.expr(
            "transform(_sorted, x -> struct(x.track_id as song_id, x.song_name as song_name, x.play_count as listen_count))"
        ),
    ).drop("_sorted")
    return top_songs


def compute_top_genres_per_day(metrics_df: DataFrame) -> DataFrame:
    daily_totals = metrics_df.groupBy("date", "genre").agg(F.sum("listen_count").alias("genre_total_plays"))
    # rank 1 = most-played genre per date; tiebreak asc genre
    window = Window.partitionBy("date").orderBy(F.desc("genre_total_plays"), F.asc("genre"))
    top5 = (
        daily_totals.withColumn("rn", F.row_number().over(window))
        .filter(F.col("rn") <= 5)
        .groupBy("date")
        .agg(
            F.sort_array(
                F.collect_list(
                    F.struct(
                        F.col("rn").alias("rn"),
                        F.col("genre").alias("genre"),
                        F.col("genre_total_plays").cast("long").alias("genre_total_plays"),
                    )
                )
            ).alias("_sorted")
        )
    )
    # Output {genre_id, genre_name, listen_count} to match Phase 4 DynamoDB contract
    top5 = top5.withColumn(
        "top_5_genres",
        F.expr(
            "transform(_sorted, x -> struct(x.genre as genre_id, x.genre as genre_name, x.genre_total_plays as listen_count))"
        ),
    ).drop("_sorted")
    # Broadcast-join: every record on the same date gets the same top_5_genres list
    return metrics_df.join(F.broadcast(top5), on="date", how="left")
