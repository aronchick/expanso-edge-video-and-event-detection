#!/usr/bin/env -S uv run -s
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6,<7"]
# ///
"""Prove the archive job's S3 output against a TLS, authenticated S3 service.

Starts a throwaway S3-compatible service (SeaweedFS, with TLS and per-identity
permissions) on a private Docker network, issues a write-only key for the
archive job and a read key for the auditor, runs the real jobs/event-archive-job.yaml over
fixtures/event-archive/input.jsonl in the pinned Expanso Edge container with
the standard AWS environment, and then checks:

  - every event arrived as one object under events/<node>/
  - each object carries the archive time and a signature audit
  - a wrong secret key is refused
  - the archive key cannot list the bucket or write outside events/
  - plaintext HTTP to the TLS port is refused

Everything it starts is removed afterwards, even on failure. Needs Docker.

  uv run -s scripts/prove-s3.py --report docs/s3-proof.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUCKET = "edge-isr-events"
SERVICE_IMAGE = (
    "chrislusf/seaweedfs:4.48@"
    "sha256:4e61d15fd35994cb1e43e1e553dff106794841fd9a99ade2fc8c8bfce4d7872d"
)
REGION = "us-west-2"


def docker(*args: str, check: bool = True, capture: bool = False, env: dict | None = None):
    return subprocess.run(
        ["docker", *args],
        check=check,
        capture_output=capture,
        text=True,
        env={**os.environ, **(env or {})},
    )


def load_replay():
    spec = importlib.util.spec_from_file_location(
        "edge_replay", ROOT / "scripts" / "edge-replay.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["edge_replay"] = module
    spec.loader.exec_module(module)

    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--report", type=Path, help="write the verification JSON here")
    parser.add_argument(
        "--require", action="store_true", help="fail, instead of skipping, when Docker is missing"
    )
    args = parser.parse_args()

    if shutil.which("docker") is None:
        print("skipping the S3 proof: docker is not installed here", file=sys.stderr)

        return 1 if args.require else 0
    suffix = str(os.getpid())
    network = f"edge-isr-s3-{suffix}"
    service = f"edge-isr-s3-service-{suffix}"
    replay = load_replay()
    records = [
        json.loads(line)
        for line in (ROOT / "fixtures/event-archive/input.jsonl").read_text().splitlines()
        if line
    ]
    docker(
        "build",
        "-q",
        "-t",
        "edge-isr-s3-client:local",
        "-f",
        str(ROOT / "proof/Dockerfile.s3-client"),
        "proof",
    )
    docker("build", "-q", "-t", replay.IMAGE, str(ROOT / "proof"))
    result = 1

    with tempfile.TemporaryDirectory(prefix="edge-isr-s3-") as raw:
        base = Path(raw).resolve()
        certs, cfg = base / "certs", base / "cfg"
        certs.mkdir()
        cfg.mkdir()
        # Fresh credentials for every run; they exist only in this temp directory.
        keys = {
            name: {"id": "AKIA" + secrets.token_hex(8).upper(), "secret": secrets.token_urlsafe(30)}
            for name in ("setup", "archive-writer", "auditor")
        }
        (cfg / "keys.json").write_text(json.dumps(keys), encoding="utf-8")

        def identity(name: str, actions: list[str]) -> dict:
            return {
                "name": name,
                "credentials": [{"accessKey": keys[name]["id"], "secretKey": keys[name]["secret"]}],
                "actions": actions,
            }

        (cfg / "s3.json").write_text(
            json.dumps(
                {
                    "identities": [
                        identity("setup", ["Admin", "Read", "Write", "List", "Tagging"]),
                        identity("archive-writer", [f"Write:{BUCKET}/events/*"]),
                        identity("auditor", [f"Read:{BUCKET}", f"List:{BUCKET}"]),
                    ]
                }
            ),
            encoding="utf-8",
        )
        docker(
            "run", "--rm", "-v", f"{certs}:/certs", "--entrypoint", "sh", replay.IMAGE, "-c",
            "openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj /CN=s3.test "
            "-keyout /certs/server.key -out /certs/server.crt "
            "-addext subjectAltName=DNS:s3.test,DNS:*.s3.test >/dev/null 2>&1 "
            "&& chmod 0644 /certs/server.key",
        )  # fmt: skip
        docker("network", "create", network, capture=True)
        client = [
            "run", "--rm", "--network", network,
            "-e", "AWS_CA_BUNDLE=/certs/server.crt",
            "-v", f"{certs}:/certs:ro", "-v", f"{cfg}:/cfg:ro",
            "-v", f"{ROOT / 'proof'}:/proof:ro", "edge-isr-s3-client:local",
        ]  # fmt: skip

        try:
            docker(
                "run", "-d", "--name", service, "--network", network,
                "--network-alias", "s3.test", "--network-alias", f"{BUCKET}.s3.test",
                "-v", f"{certs}:/certs:ro", "-v", f"{cfg}:/cfg:ro",
                SERVICE_IMAGE, "server", "-dir=/data", "-master.volumeSizeLimitMB=64",
                "-filer", "-s3", "-s3.port=9000", "-s3.config=/cfg/s3.json",
                "-s3.domainName=s3.test",
                "-s3.cert.file=/certs/server.crt", "-s3.key.file=/certs/server.key",
                capture=True,
            )  # fmt: skip
            deadline = time.monotonic() + 90

            while time.monotonic() < deadline:
                made = docker(*client, "python", "/proof/s3-setup.py", check=False, capture=True)

                if made.returncode == 0:
                    break

                time.sleep(2)
            else:
                print(made.stdout, made.stderr, file=sys.stderr)
                raise SystemExit("the S3 service did not come up")

            writer = keys["archive-writer"]
            replay.replay(
                ROOT / "jobs/event-archive-job.yaml",
                ROOT / "fixtures/event-archive/input.jsonl",
                full=True,
                docker_args=["--network", network, "-v", f"{certs}:/certs:ro"],
                extra_env={
                    "EDGE_ISR_S3_BUCKET": BUCKET,
                    "AWS_REGION": REGION,
                    "EDGE_ISR_S3_ENDPOINT": "https://s3.test:9000",
                    "AWS_ACCESS_KEY_ID": writer["id"],
                    "AWS_SECRET_ACCESS_KEY": writer["secret"],
                    "AWS_CA_BUNDLE": "/certs/server.crt",
                },
            )
            time.sleep(1.0)
            verify = docker(
                *client, "python", "/proof/s3-verify.py", str(len(records)),
                check=False, capture=True,
            )  # fmt: skip
            print(verify.stdout)
            print(verify.stderr, file=sys.stderr)
            result = verify.returncode

            if args.report and verify.stdout:
                args.report.write_text(verify.stdout, encoding="utf-8")
        finally:
            docker("rm", "-f", service, check=False, capture=True)
            docker("network", "rm", network, check=False, capture=True)

    return result


if __name__ == "__main__":
    raise SystemExit(main())
