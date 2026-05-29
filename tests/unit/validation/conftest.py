import pytest
import boto3
from moto import mock_aws

REGION = "eu-west-1"
BUCKET = "raw-data-test"


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
        yield client
