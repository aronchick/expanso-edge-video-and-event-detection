"""Check what the archive job wrote, and that the service refuses what it should.
Runs in the client container on the proof network. Prints one JSON document."""

import json
import sys
import urllib.error
import urllib.request

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

ENDPOINT = "https://s3.test:9000"
BUCKET = "edge-isr-events"
REGION = "us-west-2"
keys = json.load(open("/cfg/keys.json", encoding="utf-8"))
expected = int(sys.argv[1])


def client(key_id, secret):
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        config=Config(s3={"addressing_style": "path"}),
    )


def refused(call):
    try:
        call()
    except ClientError as error:
        return error.response["Error"]["Code"]

    return None


auditor = client(keys["auditor"]["id"], keys["auditor"]["secret"])
writer = client(keys["archive-writer"]["id"], keys["archive-writer"]["secret"])
listing = auditor.list_objects_v2(Bucket=BUCKET, Prefix="events/")
names = [item["Key"] for item in listing.get("Contents", [])]
records = [json.loads(auditor.get_object(Bucket=BUCKET, Key=name)["Body"].read()) for name in names]
bad_key = client(keys["archive-writer"]["id"], "not-the-secret")

try:
    urllib.request.urlopen("http://s3.test:9000/", timeout=3)  # plaintext to a TLS port
    plaintext = "accepted"
except (urllib.error.URLError, OSError) as error:
    plaintext = f"refused ({type(error).__name__})"

report = {
    "objects": len(names),
    "expected_objects": expected,
    "sample_key": names[0] if names else None,
    "all_have_archive_time": all("archived_at" in r.get("archive", {}) for r in records),
    "all_signed": all(r.get("audit", {}).get("signed") for r in records),
    "wrong_secret_refused": refused(
        lambda: bad_key.put_object(Bucket=BUCKET, Key="events/x.json", Body=b"{}")
    ),
    "writer_cannot_list": refused(lambda: writer.list_objects_v2(Bucket=BUCKET)),
    "writer_cannot_write_outside_events": refused(
        lambda: writer.put_object(Bucket=BUCKET, Key="other/x.json", Body=b"{}")
    ),
    "plaintext_http": plaintext,
}
print(json.dumps(report, indent=2))
ok = (
    report["objects"] == expected
    and report["all_have_archive_time"]
    and report["all_signed"]
    and report["wrong_secret_refused"]
    and report["writer_cannot_list"]
    and report["writer_cannot_write_outside_events"]
    and report["plaintext_http"].startswith("refused")
)
sys.exit(0 if ok else 1)
