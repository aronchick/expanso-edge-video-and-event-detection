#!/usr/bin/env -S uv run -s
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6,<7"]
# ///
"""Build every pipeline fixture by running the real pipeline on real input.

For each job in PIPELINES this replays the recorded producer output
(fixtures/capture/*.jsonl) through a local Expanso Edge agent, once for the
whole pipeline and once per processor stage, and writes:

  fixtures/<id>/input.jsonl             what the pipeline reads
  fixtures/<id>/expected.jsonl          what it produced with its output swapped
                                        for a file (the public-bar golden)
  fixtures/<id>/stages/NN-<label>.input.jsonl
  fixtures/<id>/stages/NN-<label>.output.jsonl
                                        one pair per stage; the last stage,
                                        `deliver`, is what the job's own output
                                        wrote (archive time and ingest time are
                                        stamped there, at delivery)
  fixtures/pipelines.json               the index the guide page is built from

Nothing here is hand-written: every record is an engine's output. Re-run it
after changing a job and commit the diff.

  uv run -s scripts/build-fixtures.py               # all pipelines
  uv run -s scripts/build-fixtures.py fuse          # one pipeline
  uv run -s scripts/build-fixtures.py --runner host # use expanso-edge on PATH
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

# id, job, recorded input, the file the job's own output writes (or None to use
# the previous pipeline's delivered output), outputs to leave out of a local run.
PIPELINES = [
    {
        "id": "sensor-north",
        "job": "jobs/sensor-north-job.yaml",
        "input": "fixtures/capture/sensor-north.jsonl",
        "deliver": "sensor-north.events.ndjson",
        "example": "edge-isr",
    },
    {
        "id": "sensor-south",
        "job": "jobs/sensor-south-job.yaml",
        "input": "fixtures/capture/sensor-south.jsonl",
        "deliver": "sensor-south.events.ndjson",
        "example": "edge-isr",
    },
    {
        "id": "fusion-node",
        "job": "jobs/orchestrator-job.yaml",
        "input": "fixtures/capture/fusion-node.jsonl",
        "deliver": "tally.ndjson",
        "example": "edge-isr",
    },
    {
        "id": "fuse",
        "job": "jobs/fuse-job.yaml",
        "input": "fixtures/capture/fuse.jsonl",
        "deliver": "fused-alerts.ndjson",
        "example": "edge-isr",
    },
    {
        "id": "event-archive",
        "job": "jobs/event-archive-job.yaml",
        "input": "fixtures/capture/event-archive.jsonl",
        "deliver": "events-*.ndjson",
        "without": ("s3",),
        "example": "edge-isr",
    },
    {
        "id": "yolo-detector",
        "job": "jobs/yolo-detector-job.yaml",
        "input": "fixtures/capture/yolo-detector.jsonl",
        "deliver": "detection-events.ndjson",
        "example": "box-counting",
    },
    {
        "id": "security-camera-events",
        "job": "jobs/security-camera-events-job.yaml",
        "input": "yolo-detector",  # the scans yolo-detector delivered
        "deliver": "enriched-events.ndjson",
        "example": "box-counting",
    },
    {
        "id": "box-crossing",
        "job": "jobs/box-crossing-job.yaml",
        "input": "fixtures/capture/box-crossing.jsonl",
        "deliver": "crossing-events.ndjson",
        "example": "box-counting",
    },
    {
        "id": "security-camera-recorder",
        "job": "jobs/recorder-job.yaml",
        "input": "fixtures/capture/recorder.jsonl",
        "deliver": "recording-catalog.ndjson",
        "example": "box-counting",
    },
]


def load_replay():
    spec = importlib.util.spec_from_file_location(
        "edge_replay", ROOT / "scripts" / "edge-replay.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["edge_replay"] = module
    spec.loader.exec_module(module)

    return module


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in records),
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def processor_kind(processor: dict) -> str:
    return next(key for key in processor if key != "label")


def edge_version(runner: str) -> str:
    if runner == "docker":
        command = ["docker", "run", "--rm", "--entrypoint", "expanso-edge", "edge-isr-proof:local"]
    else:
        command = ["expanso-edge"]

    return subprocess.run([*command, "version"], capture_output=True, text=True).stdout.strip()


def build(entry: dict, replay_module, runner: str, delivered: dict[str, Path]) -> dict:
    job_path = ROOT / entry["job"]
    job = yaml.safe_load(job_path.read_text(encoding="utf-8"))
    processors = job["config"]["pipeline"]["processors"]
    base = ROOT / "fixtures" / entry["id"]
    source = delivered[entry["input"]] if entry["input"] in delivered else ROOT / entry["input"]
    records = read_jsonl(source)
    write_jsonl(base / "input.jsonl", records)
    replay = replay_module.replay
    stage_files: list[dict] = []
    previous = base / "input.jsonl"
    index = 0

    for position, processor in enumerate(processors, start=1):
        if processor_kind(processor) == "log":
            continue

        index += 1
        label = processor.get("label", f"stage-{position}")
        produced = replay(job_path, base / "input.jsonl", upto=position, runner=runner)["actual"]
        stem = f"{index:02d}-{label}"
        write_jsonl(base / "stages" / f"{stem}.input.jsonl", read_jsonl(previous))
        write_jsonl(base / "stages" / f"{stem}.output.jsonl", produced)
        previous = base / "stages" / f"{stem}.output.jsonl"
        stage_files.append(
            {"id": f"{entry['id']}-{label}", "label": label, "kind": processor_kind(processor)}
        )

    expected = replay(job_path, base / "input.jsonl", runner=runner)["actual"]
    write_jsonl(base / "expected.jsonl", expected)

    if read_jsonl(previous) != expected:
        raise SystemExit(f"{entry['id']}: last stage differs from the full replay")

    full = replay(
        job_path,
        base / "input.jsonl",
        runner=runner,
        full=True,
        without=tuple(entry.get("without", ())),
    )
    pattern = entry["deliver"]
    matches = [name for name in full if Path(name).match(pattern)]

    if len(matches) != 1:
        raise SystemExit(f"{entry['id']}: expected one output matching {pattern}, got {list(full)}")

    index += 1
    stem = f"{index:02d}-deliver"
    write_jsonl(base / "stages" / f"{stem}.input.jsonl", expected)
    write_jsonl(base / "stages" / f"{stem}.output.jsonl", full[matches[0]])
    stage_files.append({"id": f"{entry['id']}-deliver", "label": "deliver", "kind": "output"})
    delivered[entry["id"]] = base / "stages" / f"{stem}.output.jsonl"

    return {
        "id": entry["id"],
        "example": entry["example"],
        "job": entry["job"],
        "name": job["name"],
        "records_in": len(records),
        "records_out": len(expected),
        "stages": stage_files,
        "delivered_to": matches[0],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("only", nargs="*", help="pipeline ids to build (default all)")
    parser.add_argument("--runner", choices=("docker", "host"), default="docker")
    args = parser.parse_args()
    replay_module = load_replay()
    delivered: dict[str, Path] = {}
    index_path = ROOT / "fixtures" / "pipelines.json"
    index = json.loads(index_path.read_text()) if index_path.is_file() else {"pipelines": []}
    known = {p["id"]: p for p in index["pipelines"]}

    for entry in PIPELINES:
        if args.only and entry["id"] not in args.only:
            existing = ROOT / "fixtures" / entry["id"] / "stages"

            if existing.is_dir():
                delivered[entry["id"]] = sorted(existing.glob("*-deliver.output.jsonl"))[0]

            continue

        print(f"== {entry['id']}", file=sys.stderr, flush=True)
        known[entry["id"]] = build(entry, replay_module, args.runner, delivered)

    ordered = [known[e["id"]] for e in PIPELINES if e["id"] in known]
    index_path.write_text(
        json.dumps(
            {
                "built": dt.datetime.now().astimezone().strftime("%Y-%m-%d"),
                "engine": edge_version(args.runner),
                "pipelines": ordered,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
