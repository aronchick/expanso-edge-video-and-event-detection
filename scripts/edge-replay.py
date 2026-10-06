#!/usr/bin/env -S uv run -s
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6,<7"]
# ///
"""Replay a recorded input through a real Expanso Edge pipeline.

The same method the public-bar check uses: take a job, swap its input for the
fixture file and its output for a file, deploy it to a local Expanso Edge
agent, and read what comes out. `--upto N` keeps only the first N processors,
which is how the per-stage inputs and outputs in the explorer are produced.

  uv run -s scripts/edge-replay.py jobs/event-archive-job.yaml \
      fixtures/capture/event-archive.jsonl --out /tmp/archive.jsonl

By default the agent runs in a throwaway container (proof/Dockerfile), so no
host Expanso configuration is read or written. --runner host uses the
`expanso-edge` and `expanso-cli` on PATH instead, for CI runners.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
IMAGE = "edge-isr-proof:local"


def render(
    job: dict, source: Path, sink: Path | None, upto: int | None, without: tuple[str, ...] = ()
) -> dict:
    """The job with its input replaced by a file, and its output too unless
    `sink` is None (full mode keeps the job's own outputs)."""
    rendered = copy.deepcopy(job)
    config = rendered.get("config", rendered)
    config["input"] = {"file": {"paths": [str(source)], "codec": "lines"}}

    if sink is not None:
        config["output"] = {"file": {"path": str(sink), "codec": "lines"}}
    elif without:
        outputs = config["output"]["broker"]["outputs"]
        config["output"]["broker"]["outputs"] = [
            o for o in outputs if o.get("label") not in without
        ]

    if upto is not None:
        config["pipeline"]["processors"] = config["pipeline"]["processors"][:upto]

    rendered["name"] = f"replay-{job.get('name', 'job')}"[:60]
    rendered["type"] = "pipeline"
    rendered.pop("selector", None)
    rendered["config"] = config

    return rendered


def run_docker(work: Path, rebuild: bool, env: dict[str, str], docker_args: list[str]) -> None:
    have = (
        subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode == 0
    )

    if rebuild or not have:
        subprocess.run(["docker", "build", "-q", "-t", IMAGE, str(ROOT / "proof")], check=True)

    flags = [item for key, value in env.items() for item in ("-e", f"{key}={value}")]
    subprocess.run(
        ["docker", "run", "--rm", "-v", f"{work}:/work", *flags, *docker_args, IMAGE], check=True
    )


def run_host(work: Path, env: dict[str, str]) -> None:
    subprocess.run(
        ["bash", str(ROOT / "proof" / "replay-entrypoint.sh")],
        check=True,
        env={**os.environ, "REPLAY_TIMEOUT": "60", **env},
        cwd=work,
    )


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def replay(
    job_path: Path,
    source: Path,
    upto: int | None = None,
    runner: str = "docker",
    rebuild: bool = False,
    full: bool = False,
    without: tuple[str, ...] = (),
    docker_args: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict[str, list[dict]]:
    """Run the job over `source`. Returns {"actual": records} normally; in full
    mode the job keeps its own outputs and the result maps each written
    *.ndjson file name to its records."""
    job = yaml.safe_load(job_path.read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory(prefix="edge-replay-") as raw:
        work = Path(raw).resolve()
        (work / "in.jsonl").write_bytes(source.read_bytes())
        container = runner == "docker"
        prefix = Path("/work") if container else work
        sink = None if full else prefix / "actual.jsonl"
        env = dict(extra_env or {})

        if full:
            (work / "out").mkdir()
            env |= {
                "EDGE_ISR_STATE": str(prefix / "out"),
                "EDGE_ISR_ARCHIVE": str(prefix / "out"),
                "BOX_DATA_DIR": str(prefix / "out"),
                "RECORDING_DIR": str(prefix / "out" / "recordings"),
            }

        (work / "job.yaml").write_text(
            yaml.safe_dump(render(job, prefix / "in.jsonl", sink, upto, without), sort_keys=False),
            encoding="utf-8",
        )

        if container:
            run_docker(work, rebuild, env, docker_args or [])
        else:
            run_host(work, env)

        if full:
            return {f.name: read_jsonl(f) for f in sorted((work / "out").glob("*.ndjson"))}

        actual = work / "actual.jsonl"

        return {"actual": read_jsonl(actual) if actual.is_file() else []}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("job", type=Path)
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--upto", type=int, help="keep only the first N processors")
    parser.add_argument("--runner", choices=("docker", "host"), default="docker")
    parser.add_argument("--rebuild", action="store_true", help="rebuild the runner image")
    args = parser.parse_args()
    records = replay(args.job, args.input, args.upto, args.runner, args.rebuild)["actual"]
    text = "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in records)

    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)

    print(f"{len(records)} records", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
