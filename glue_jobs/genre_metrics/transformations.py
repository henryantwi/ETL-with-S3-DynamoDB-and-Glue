from pyspark.sql import DataFrame
import pyspark.sql.functions as F
from pyspark.sql import Window


def join_activity_to_catalog(activity_df: DataFrame, catalog_df: DataFrame) -> DataFrame:
    enriched = activity_df.join(catalog_df, on="track_id", how="inner")
    enriched = enriched.withColumn("date", F.col("listen_time").cast("date").cast("string"))
    # duration_ms (int) → duration_seconds (float)
    enriched = enriched.withColumn(
        "duration_seconds",
        F.coalesce(F.col("duration_ms").cast("double") / 1000.0, F.lit(0.0))
    )
    # actual song name column is track_name
    return enriched.select("user_id", "track_id", "date", "track_genre", "track_name", "duration_seconds") \
                   .withColumnRenamed("track_genre", "genre") \
                   .withColumnRenamed("track_name", "song_name")


def compute_genre_metrics(enriched_df: DataFrame) -> DataFrame:
    metrics = enriched_df.groupBy("date", "genre").agg(
        F.count("*").alias("total_plays"),
        F.countDistinct("user_id").alias("distinct_users"),
        F.sum("duration_seconds").alias("total_listening_time_seconds"),
    )
    metrics = metrics.withColumn(
        "avg_listening_time_per_user_seconds",
        F.when(
            F.col("distinct_users") > 0,
            F.col("total_listening_time_seconds") / F.col("distinct_users"),
        ).otherwise(F.lit(0.0)),
    )
    return metrics


def compute_top_songs(enriched_df: DataFrame) -> DataFrame:
    song_counts = enriched_df.groupBy("date", "genre", "song_name").agg(
        F.count("*").cast("long").alias("play_count")
    )
    # rank 1 = highest play_count; tiebreak asc song_name
    window = Window.partitionBy("date", "genre").orderBy(F.desc("play_count"), F.asc("song_name"))
    ranked = (
        song_counts
        .withColumn("rn", F.row_number().over(window))
        .filter(F.col("rn") <= 3)
    )
    # Collect with rn first; sort_array ascending by rn preserves correct rank order
    top_songs = ranked.groupBy("date", "genre").agg(
        F.sort_array(
            F.collect_list(F.struct(
                F.col("rn").alias("rn"),
                F.col("song_name").alias("song_name"),
                F.col("play_count").alias("play_count"),
            ))
        ).alias("_sorted")
    )
    # Strip rn using SQL-style transform (supported since Spark 3.0)
    top_songs = top_songs.withColumn(
        "top_3_songs",
        F.expr("transform(_sorted, x -> struct(x.song_name as song_name, x.play_count as play_count))"),
    ).drop("_sorted")
    return top_songs


def compute_top_genres_per_day(metrics_df: DataFrame) -> DataFrame:
    daily_totals = metrics_df.groupBy("date", "genre").agg(
        F.sum("total_plays").alias("genre_total_plays")
    )
    # rank 1 = most-played genre per date; tiebreak asc genre
    window = Window.partitionBy("date").orderBy(F.desc("genre_total_plays"), F.asc("genre"))
    top5 = (
        daily_totals
        .withColumn("rn", F.row_number().over(window))
        .filter(F.col("rn") <= 5)
        .groupBy("date")
        .agg(
            F.sort_array(
                F.collect_list(F.struct(
                    F.col("rn").alias("rn"),
                    F.col("genre").alias("genre"),
                    F.col("genre_total_plays").cast("long").alias("play_count"),
                ))
            ).alias("_sorted")
        )
    )
    top5 = top5.withColumn(
        "top_5_genres_of_day",
        F.expr("transform(_sorted, x -> struct(x.genre as genre, x.play_count as play_count))"),
    ).drop("_sorted")
    # Broadcast-join: every record on the same date gets the same top_5_genres_of_day list
    return metrics_df.join(F.broadcast(top5), on="date", how="left")
