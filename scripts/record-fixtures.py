#!/usr/bin/env -S uv run
"""Record the input fixtures of the Edge ISR pipelines from the real producers.

Starts the orchestrator, replays both recorded scenes through `edge-sensor
--replay`, and runs the fuse correlator against the orchestrator's event log.
What each of them wrote to stdout is exactly what its Expanso pipeline reads
in production, so it is committed as that pipeline's input fixture:

  fixtures/capture/sensor-north.jsonl   edge-sensor stdout, one event per line
  fixtures/capture/sensor-south.jsonl
  fixtures/capture/fusion-node.jsonl    orchestrator stdout: tally and alert lines
  fixtures/capture/fuse.jsonl           fuse-correlator stdout: crowd alerts
  fixtures/capture/event-archive.jsonl  the orchestrator's events.ndjson tail

If the model gateway is available (--gateway-kit), it runs in fixture mode so
the analyst descriptions are the recorded answers; no model is ever called.

  uv run python scripts/record-fixtures.py --gateway-kit ../_demo-kit
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEED = 4.0


def free_port() -> int:
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))

        return sock.getsockname()[1]


def wait_for(url: str, seconds: float = 30.0) -> None:
    deadline = time.monotonic() + seconds

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except OSError:
            time.sleep(0.2)

    raise SystemExit(f"timed out waiting for {url}")


def stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()

        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--gateway-kit", help="path to the demo kit (for fixture-mode replay)")
    parser.add_argument("--out", default=str(ROOT / "fixtures" / "capture"))
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    processes: list[subprocess.Popen] = []
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "MODEL_GATEWAY_URL": "http://127.0.0.1:1"}

    with tempfile.TemporaryDirectory(prefix="edge-isr-record-") as raw:
        work = Path(raw)
        events = work / "events.ndjson"
        shutil.copy(ROOT / "triggers.yaml", work / "triggers.yaml")

        def start(name: str, command: list[str]) -> Path:
            stdout = work / f"{name}.stdout"
            stderr = work / f"{name}.err"
            processes.append(
                subprocess.Popen(
                    command,
                    cwd=ROOT,
                    env=env,
                    stdout=stdout.open("w"),
                    stderr=stderr.open("w"),
                )
            )

            return stdout

        try:
            if args.gateway_kit:
                gateway_port = free_port()
                config = work / "gateway.toml"
                config.write_text(
                    f'port = {gateway_port}\nfixtures = "{ROOT / "fixtures" / "model"}"\n'
                    f'state = "{work / "gateway"}"\n'
                    "[caps]\nper_run = 1\nper_minute = 1\nper_day = 1\n",
                    encoding="utf-8",
                )
                start(
                    "gateway",
                    [
                        "uv", "run", "-s", str(Path(args.gateway_kit) / "model-gateway.py"),
                        "serve", "--config", str(config),
                    ],
                )  # fmt: skip
                wait_for(f"http://127.0.0.1:{gateway_port}/status")
                env["MODEL_GATEWAY_URL"] = f"http://127.0.0.1:{gateway_port}"

            fusion = start(
                "fusion-node",
                [
                    sys.executable, "-m", "expanso_security_camera.orchestrator.api",
                    "--host", "127.0.0.1", "--port", str(port),
                    "--db", str(work / "orchestrator.db"),
                    "--triggers", str(work / "triggers.yaml"),
                    "--snapshots", str(work / "snapshots"),
                    "--ndjson", str(events),
                ],
            )  # fmt: skip
            wait_for(f"{url}/metrics")
            fuse = start(
                "fuse", [sys.executable, str(ROOT / "scripts" / "fuse-correlator.py"), str(events)]
            )
            time.sleep(1.0)
            sensors = {}

            for zone in ("north", "south"):
                sensors[zone] = start(
                    f"sensor-{zone}",
                    [
                        sys.executable, "-m", "expanso_security_camera.sensor.main",
                        "--replay", str(ROOT / "fixtures" / "scenes" / f"{zone}.jsonl"),
                        "--node-id", f"sensor-{zone}", "--orchestrator", url,
                        "--db", str(work / f"sensor-{zone}.db"), "--speed", str(SPEED),
                    ],
                )  # fmt: skip

            sensor_processes = processes[-2:]

            for process in sensor_processes:
                process.wait(timeout=120)

            time.sleep(2.0)
        finally:
            for process in reversed(processes):
                stop(process)

        for name, source in (
            ("sensor-north", sensors["north"]),
            ("sensor-south", sensors["south"]),
            ("fusion-node", fusion),
            ("fuse", fuse),
            ("event-archive", events),
        ):
            lines = [ln for ln in source.read_text(encoding="utf-8").splitlines() if ln.strip()]

            for ln in lines:
                json.loads(ln)

            (out / f"{name}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"{name}: {len(lines)} lines", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
