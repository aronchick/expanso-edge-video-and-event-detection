"""Create the bucket on the proof S3 service with the setup identity.
Runs in the client container on the proof network."""

import json

import boto3
from botocore.config import Config

keys = json.load(open("/cfg/keys.json", encoding="utf-8"))["setup"]
client = boto3.client(
    "s3",
    endpoint_url="https://s3.test:9000",
    region_name="us-west-2",
    aws_access_key_id=keys["id"],
    aws_secret_access_key=keys["secret"],
    config=Config(s3={"addressing_style": "path"}),
)
try:
    client.create_bucket(
        Bucket="edge-isr-events", CreateBucketConfiguration={"LocationConstraint": "us-west-2"}
    )
except client.exceptions.BucketAlreadyOwnedByYou:
    pass

# The service answers before its storage is ready; only a real write proves it is.
client.put_object(Bucket="edge-isr-events", Key="ready.txt", Body=b"ready")
client.delete_object(Bucket="edge-isr-events", Key="ready.txt")
print("bucket ready")
