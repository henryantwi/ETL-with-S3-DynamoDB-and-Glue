import os

import pytest
from pyspark.sql import SparkSession


@pytest.fixture(scope="module")
def spark():
    # Remove SPARK_HOME so pyspark uses its own bundled JARs, not a conflicting install
    os.environ.pop("SPARK_HOME", None)
    session = (
        SparkSession.builder.master("local[2]")
        .appName("genre-metrics-test")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.python.use.daemon", "false")
        .getOrCreate()
    )
    yield session
    session.stop()
