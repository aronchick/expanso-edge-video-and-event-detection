#!/usr/bin/env -S uv run -s
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6,<7"]
# ///
"""Build the guide page from the real fixtures.

public/guide/index.html is generated, never hand-edited: the explanation, the
step explorer, and the run and deploy instructions. Every stage panel carries
the exact processor configuration from the job file and the real records that
went into and came out of it (fixtures/<pipeline>/stages/*), so the explorer
cannot drift from what the pipelines do. The same files are copied to
public/guide/data/ for the download buttons.

  uv run -s scripts/build-guide.py            # write public/guide
  uv run -s scripts/build-guide.py --check    # fail if it is out of date
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "public" / "guide"
SHOWN = 3  # records shown per side; the download has all of them

EXAMPLES = {
    "edge-isr": {
        "title": "Edge ISR cascade",
        "blurb": (
            "Two cameras, two zones. Each sensor detects people locally and prints a signed "
            "event, the fusion node merges the counts and raises the crowd flag, the fuse "
            "pipeline signs the alert, and the archive keeps every event."
        ),
    },
    "box-counting": {
        "title": "Box counting",
        "blurb": (
            "The original warehouse demo. A detector counts boxes at a bay door and in the "
            "room beyond it, esc-infer turns line crossings into events, and the pipelines "
            "validate, enrich and catalogue them."
        ),
    },
}

PIPELINES = {
    "sensor-north": (
        "Sensor north",
        "edge-sensor on the north camera. Prints one signed JSON event per frame.",
    ),
    "sensor-south": (
        "Sensor south",
        "edge-sensor on the south camera. Same pipeline, its own node identity.",
    ),
    "fusion-node": (
        "Fusion node",
        "The orchestrator. Prints a tally line for every event and an alert line when the "
        "merged count flags.",
    ),
    "fuse": (
        "Fuse",
        "The cross-zone merge as its own pipeline: signs each crowd alert.",
    ),
    "event-archive": (
        "Event archive",
        "Follows the event log, checks signatures, and stores every event.",
    ),
    "yolo-detector": (
        "YOLO detector",
        "The GPU loop on the Jetson. Prints one scan of per-camera box counts.",
    ),
    "security-camera-events": (
        "Scan enrichment",
        "Validates the detection scans and stamps where they came from.",
    ),
    "box-crossing": (
        "Box crossing",
        "esc-infer events: line crossings and the arrivals-versus-departures reconciliation.",
    ),
    "security-camera-recorder": (
        "Recorder",
        "Segmented video evidence. Catalogues each finished segment.",
    ),
}

PRODUCERS = {
    "sensor-north": "edge-sensor, replaying the recorded north scene",
    "sensor-south": "edge-sensor, replaying the recorded south scene",
    "fusion-node": "edge-orchestrator, receiving both sensors",
    "fuse": "scripts/fuse-correlator.py, following the event log",
    "event-archive": "scripts/tail-ndjson.py, following the event log",
    "yolo-detector": "scripts/detect_loop.py on the recorded footage",
    "security-camera-events": "scripts/tail-ndjson.py, following detection-events.ndjson",
    "box-crossing": "esc-infer on the recorded footage",
    "security-camera-recorder": "esc-record on the recorded footage",
}

STAGES = {
    "parse-event": "Turns the line the producer printed into a structured event. A line that is "
    "not JSON is dropped, never forwarded.",
    "parse-line": "Turns the orchestrator's stdout line into a structured record.",
    "parse-alert": "Turns the correlator's stdout line into a structured alert.",
    "parse-scan": "Turns the detector's stdout line into a structured scan.",
    "parse-segment": "Turns the recorder's stdout line into a structured segment record.",
    "validate-event": "Drops an event that lacks a node, a timestamp or its detections.",
    "keep-fused-alerts": "Keeps only real fused alerts, so a stray line never reaches the audit file.",
    "keep-sensor-events": "Archives sensor events only. Alerts from the fusion node are archived by "
    "the fuse pipeline.",
    "require-schema": "Drops a record that does not carry a schema_version.",
    "check-signature": "Records whether the DBOM signature is present and well formed. An unsigned "
    "event is kept and flagged, never dropped.",
    "classify": "Labels each line CLEAR, FLAG or ALERT from the merged tally.",
    "normalise-class": "Maps the raw detector class (suitcase, cardboard box, person) to the label "
    "the business counts. Edit this stage to change what is counted.",
    "add-source": "Stamps the device and site the record came from.",
    "add-lineage": "Stamps which pipeline and node handled the record.",
    "sign-record": "Signs the whole record with SHA-256 so a later change is detectable.",
    "deliver": "The job's own outputs. The delivery time is stamped here, at the moment of writing.",
}

SAMPLE_DATA = [
    (
        "fixtures/scenes/north.jsonl",
        "YOLO yolov8s detections measured on a viewing window panned down the Ultralytics "
        "sample photograph bus.jpg (41 frames).",
    ),
    (
        "fixtures/scenes/south.jsonl",
        "The same weights on a window panned across zidane.jpg (41 frames).",
    ),
    (
        "fixtures/capture/",
        "The output of the real producers on those scenes: edge-sensor, edge-orchestrator and "
        "the fuse correlator, with the analyst answers replayed from fixtures/model/.",
    ),
    (
        "recordings (not committed)",
        "Two cameras filmed for two minutes while boxes were carried through a door. "
        "esc-infer, detect_loop.py and esc-record ran on that footage with the "
        "yolov8s-worldv2 weights; their output is in fixtures/capture/.",
    ),
]


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def pretty(record: dict) -> str:
    return json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False)


def focus_index(pipeline_id: str, final: list[dict]) -> int:
    """The first record worth looking at: one with detections or an alert."""
    for index, record in enumerate(final):
        if record.get("yolo_hits") or record.get("type") == "alert" or record.get("fused_alert"):
            return index

        if record.get("event_type") == "crossing" or record.get("detections"):
            return index

    return 0


def records_html(records: list[dict], start: int, label: str) -> str:
    shown = records[start : start + SHOWN] if records else []

    if not shown:
        return '<p class="none">No records. This stage dropped everything it received.</p>'

    blocks = "".join(
        f'<pre class="json" data-public-json tabindex="0">{esc(pretty(r))}</pre>' for r in shown
    )
    first = start + 1
    last = start + len(shown)
    note = f"records {first} to {last} of {len(records)}" if len(records) > SHOWN else ""

    return f'{blocks}<p class="note">{esc(note)}</p>' if note else blocks


def processor_yaml(job: dict, label: str) -> str:
    if label == "deliver":
        return yaml.safe_dump(job["config"]["output"], sort_keys=False, width=88).rstrip()

    for processor in job["config"]["pipeline"]["processors"]:
        if processor.get("label") == label:
            return yaml.safe_dump(processor, sort_keys=False, width=88).rstrip()

    return ""


def build() -> dict[Path, str | bytes]:
    index = json.loads((ROOT / "fixtures" / "pipelines.json").read_text(encoding="utf-8"))
    files: dict[Path, str | bytes] = {}
    sections: list[str] = []
    example_tabs: list[str] = []
    first_example = True

    for example_id, example in EXAMPLES.items():
        pipelines = [p for p in index["pipelines"] if p["example"] == example_id]
        pressed = "true" if first_example else "false"
        example_tabs.append(
            f'<button type="button" class="tab" data-example-tab="{example_id}"'
            f' aria-pressed="{pressed}" aria-controls="example-{example_id}"'
            f' id="tab-{example_id}">{esc(example["title"])}</button>'
        )
        pipeline_tabs: list[str] = []
        pipeline_blocks: list[str] = []

        for p_position, pipeline in enumerate(pipelines):
            pid = pipeline["id"]
            title, summary = PIPELINES[pid]
            job = yaml.safe_load((ROOT / pipeline["job"]).read_text(encoding="utf-8"))
            base = ROOT / "fixtures" / pid
            final = read_jsonl(base / "expected.jsonl")
            start = focus_index(pid, final)
            files[Path("data/jobs") / Path(pipeline["job"]).name] = (
                ROOT / pipeline["job"]
            ).read_text(encoding="utf-8")
            is_first = first_example and p_position == 0
            pipeline_tabs.append(
                f'<button type="button" class="tab sub" data-pipeline-tab="{pid}"'
                f' aria-pressed="{"true" if p_position == 0 else "false"}">{esc(title)}</button>'
            )
            stepper: list[str] = []
            panels: list[str] = []
            total = len(pipeline["stages"])

            for s_position, stage in enumerate(pipeline["stages"]):
                label = stage["label"]
                stem = f"{s_position + 1:02d}-{label}"
                stage_in = read_jsonl(base / "stages" / f"{stem}.input.jsonl")
                stage_out = read_jsonl(base / "stages" / f"{stem}.output.jsonl")
                kept = len(stage_in) == len(stage_out)
                offset = start if kept and start < len(stage_out) else 0

                for side in ("input", "output"):
                    source = base / "stages" / f"{stem}.{side}.jsonl"
                    files[Path("data/stages") / pid / source.name] = source.read_text(
                        encoding="utf-8"
                    )

                code = processor_yaml(job, label)
                stepper.append(
                    f'<li><button type="button" class="step" data-go="{esc(stage["id"])}">'
                    f'<span class="n">{s_position + 1}</span>{esc(label)}</button></li>'
                )
                producer = PRODUCERS[pid] if s_position == 0 else "the previous stage"
                dropped = len(stage_in) - len(stage_out)
                counts = f"{len(stage_in)} in, {len(stage_out)} out" + (
                    f", {dropped} dropped" if dropped > 0 else ""
                )
                panels.append(
                    f'<article class="stage" data-stage-id="{esc(stage["id"])}"'
                    f' data-pipeline="{pid}" data-index="{s_position}" hidden'
                    f' data-input-file="data/stages/{pid}/{stem}.input.jsonl"'
                    f' data-output-file="data/stages/{pid}/{stem}.output.jsonl">'
                    f'<h4 class="stage-h">Stage {s_position + 1} of {total}: {esc(label)}</h4>'
                    f'<p class="what">{esc(STAGES.get(label, ""))}</p>'
                    f'<p class="counts">{esc(counts)}</p>'
                    f'<h5 class="io-h">Configuration</h5>'
                    f'<pre class="code" data-yaml tabindex="0">{esc(code)}</pre>'
                    f'<div class="io"><div class="io-col"><h5 class="io-h">Input from {esc(producer)}</h5>'
                    f"<div data-input>{records_html(stage_in, offset, 'input')}</div></div>"
                    f'<div class="io-col"><h5 class="io-h">Output</h5>'
                    f"<div data-output>{records_html(stage_out, offset, 'output')}</div></div></div>"
                    "</article>"
                )

            pipeline_blocks.append(
                f'<div class="pipeline" data-pipeline="{pid}"'
                f' data-job-file="data/jobs/{esc(Path(pipeline["job"]).name)}"'
                f"{'' if p_position == 0 else ' hidden'}>"
                f'<h3 class="pl-h">{esc(title)}</h3><p class="pl-sum">{esc(summary)}</p>'
                f'<p class="pl-job">Job file: <code>{esc(pipeline["job"])}</code>. Runs '
                f"{esc(pipeline['records_in'])} recorded "
                f"records through {total - 1} processors and delivers to "
                f"<code>{esc(pipeline['delivered_to'])}</code>.</p>"
                f'<ol class="stepper" aria-label="Stages of {esc(title)}">{"".join(stepper)}</ol>'
                f"{''.join(panels)}</div>"
            )
            del is_first

        sections.append(
            f'<div class="example" id="example-{example_id}" data-example="{example_id}"'
            f"{'' if first_example else ' hidden'}>"
            f'<p class="ex-blurb">{esc(example["blurb"])}</p>'
            f'<div class="tabs sub" role="group" aria-label="Pipelines in {esc(example["title"])}">'
            f"{''.join(pipeline_tabs)}</div>{''.join(pipeline_blocks)}</div>"
        )
        first_example = False

    page = PAGE.format(
        example_tabs="".join(example_tabs),
        examples="".join(sections),
        built=esc(index["built"]),
        engine=esc(index["engine"]),
        sample_data="".join(
            f"<li><code>{esc(path)}</code>: {esc(text)}</li>" for path, text in SAMPLE_DATA
        ),
        run=RUN,
        deploy=DEPLOY,
    )
    files[Path("index.html")] = page

    return files


def env_list(rows: list[tuple[str, str, str]]) -> str:
    items = "".join(
        f'<li><code>{esc(name)}</code> <span class="def">default <code>{esc(default)}</code></span>'
        f'<span class="mean">{esc(meaning)}</span></li>'
        for name, default, meaning in rows
    )

    return f'<ul class="env">{items}</ul>'


def command(identifier: str, text: str, label: str) -> str:
    return (
        f'<div class="cmd"><pre class="code" id="{identifier}" tabindex="0">{esc(text)}</pre>'
        f'<div class="actions"><button type="button" class="btn" id="copy-{identifier}"'
        f' data-copy-from="{identifier}" data-label="{esc(label)}">Copy command</button>'
        f'<span class="feedback" id="feedback-{identifier}" role="status"'
        f' aria-live="polite"></span></div></div>'
    )


RUN = (
    '<ol class="steps">'
    "<li><h3>Install</h3><p>Python 3.11 or newer and uv. No GPU, camera or model weights are "
    "needed to replay the recorded scenes.</p>"
    + command("run-install", "uv sync", "install")
    + "</li><li><h3>Start the fusion node</h3><p>The dashboard and this guide are served from it.</p>"
    + command("run-orchestrator", "uv run edge-orchestrator --port 8080", "orchestrator")
    + "</li><li><h3>Replay both recorded scenes</h3><p>Each command plays 41 frames of real "
    "detections through the sensor's trigger, threshold, analyst, signing and emit path. "
    "Run them in two terminals.</p>"
    + command(
        "run-north",
        "uv run edge-sensor --replay fixtures/scenes/north.jsonl \\\n"
        "  --node-id sensor-north --orchestrator http://localhost:8080 \\\n"
        "  --speed 2",
        "north",
    )
    + command(
        "run-south",
        "uv run edge-sensor --replay fixtures/scenes/south.jsonl \\\n"
        "  --node-id sensor-south --orchestrator http://localhost:8080 \\\n"
        "  --speed 2",
        "south",
    )
    + '</li><li><h3>Watch the merge</h3><p>Open <a href="/">the dashboard</a>. The flag '
    "trips when the combined count reaches 3 with at least one person in each zone and two "
    "in the busier one.</p>"
    + "</li><li><h3>Run a pipeline on its fixture</h3><p>This deploys the real job to a "
    "throwaway Expanso Edge container with its input and output swapped for files, the same "
    "method the proof uses. It needs Docker.</p>"
    + command(
        "run-replay",
        "uv run -s scripts/edge-replay.py jobs/fuse-job.yaml \\\n  fixtures/fuse/input.jsonl",
        "replay",
    )
    + "</li><li><h3>Rebuild every fixture and this page</h3>"
    + command(
        "run-rebuild",
        "uv run -s scripts/build-fixtures.py\nuv run -s scripts/build-guide.py",
        "rebuild",
    )
    + "</li><li><h3>Live cameras</h3><p>Two USB webcams on a laptop, no Expanso job.</p>"
    + command(
        "run-live",
        "uv run --extra vision edge-sensor --cameras 0,1 \\\n  --orchestrator http://localhost:8080",
        "live",
    )
    + "</li></ol>"
)

DEPLOY = (
    '<ol class="steps">'
    "<li><h3>Prepare the node</h3><p>Install Expanso Edge and bootstrap it into your "
    "cluster. Label it so the jobs find it, put this repository on it, and give it the "
    "YOLO weights.</p>"
    + command(
        "dep-node",
        "sudo mkdir -p /opt/edge-isr/state /opt/edge-isr/archive /opt/edge-isr/models\n"
        "git clone https://github.com/aronchick/expanso-edge-video-and-event-detection \\\n"
        "  /opt/edge-isr/app\n"
        "cd /opt/edge-isr/app && uv sync --extra vision",
        "node",
    )
    + "<p>Node settings, read by the jobs from the agent's environment:</p>"
    + env_list(
        [
            ("EDGE_ISR_HOME", "/opt/edge-isr/app", "This repository."),
            ("EDGE_ISR_STATE", "/opt/edge-isr/state", "Databases, the event log, audit files."),
            ("EDGE_ISR_ARCHIVE", "/opt/edge-isr/archive", "Daily archive files."),
            ("EDGE_ISR_ORCHESTRATOR", "http://localhost:8080", "Where sensors send events."),
            (
                "EDGE_ISR_NORTH_SOURCE, EDGE_ISR_SOUTH_SOURCE",
                "local go2rtc RTSP",
                "Camera RTSP URL or webcam index.",
            ),
            (
                "EDGE_ISR_YOLO_MODEL",
                "/opt/edge-isr/models/yolov8s.pt",
                "YOLO weights or a TensorRT engine.",
            ),
            ("EDGE_CROWD_THRESHOLD", "3", "Combined people needed to flag."),
            (
                "EDGE_ISR_BIND",
                "127.0.0.1",
                "Fusion node listen address. The API has no login; use 0.0.0.0 only on an "
                "isolated LAN.",
            ),
        ]
    )
    + "</li>"
    "<li><h3>Deploy the jobs</h3><p>Fusion node first, then the sensors, the fuse pipeline and "
    "the archive.</p>"
    + command(
        "dep-jobs",
        "for job in orchestrator sensor-north sensor-south fuse event-archive; do\n"
        "  expanso-cli job deploy jobs/$job-job.yaml\n"
        "done\n"
        "expanso-cli job list",
        "jobs",
    )
    + "<p>Each job's page in Expanso Cloud now shows real logs (one line per event or alert) and "
    "real input and output rates.</p></li>"
    "<li><h3>Give the archive somewhere to write</h3><p>The archive job writes to S3 with the "
    "standard AWS credential chain over HTTPS (S3 encrypts the objects at rest by default), and needs only "
    "permission to put objects under <code>events/</code>.</p>"
    + command(
        "dep-iam",
        '{\n  "Version": "2012-10-17",\n  "Statement": [{\n    "Effect": "Allow",\n'
        '    "Action": "s3:PutObject",\n'
        '    "Resource": "arn:aws:s3:::edge-isr-events/events/*"\n  }]\n}',
        "policy",
    )
    + "<p>The policy above is for a bucket named <code>edge-isr-events</code>; use your bucket's name. Set <code>EDGE_ISR_S3_BUCKET</code> and <code>AWS_REGION</code> in the agent's "
    "environment. For a private S3-compatible endpoint also set "
    "<code>EDGE_ISR_S3_ENDPOINT</code> and <code>AWS_CA_BUNDLE</code>. When the WAN drops the "
    "sensors and the dashboard keep running and Expanso Edge buffers the archive writes until "
    "the link returns.</p></li>"
    "<li><h3>Jetson</h3><p>Export the TensorRT engine on the device, set up its camera LAN, and "
    "install the nightly updater.</p>"
    + command(
        "dep-jetson",
        "python scripts/export_tensorrt.py\n"
        "sudo scripts/setup_jetson_lan.sh\n"
        "scripts/install_jetson_update_timer.sh --enable",
        "jetson",
    )
    + "</li><li><h3>Box counting</h3><p>The GPU detector runs in the pinned Ultralytics Jetson "
    "image with the NVIDIA runtime. Put <code>.env</code> with the camera credentials and the "
    "weights in <code>BOX_DATA_DIR</code>.</p>"
    + command(
        "dep-box",
        "for job in yolo-detector security-camera-events box-crossing recorder; do\n"
        "  expanso-cli job deploy jobs/$job-job.yaml\n"
        "done",
        "box",
    )
    + "</li></ol>"
)

PAGE = """<!doctype html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Edge video and event detection</title>
<meta name="description" content="Detect and merge people counts at the edge, with a stage-by-stage look at every Expanso pipeline.">
<link rel="stylesheet" href="../fonts/fonts.css">
<link rel="stylesheet" href="guide.css">
<script src="theme.js"></script>
</head>
<body>
<a class="skip" href="#explanation">Skip to the guide</a>
<header class="top">
  <p class="brand">Edge video and event detection</p>
  <nav aria-label="Sections">
    <a href="#explanation">Overview</a>
    <a href="#explorer">Step explorer</a>
    <a href="#run">Run it</a>
    <a href="#deploy">Deploy it</a>
    <a href="../">Dashboard</a>
  </nav>
  <button type="button" id="theme-toggle" class="btn" aria-pressed="false">Dark theme</button>
</header>

<main>
<section id="explanation" aria-labelledby="explanation-h">
  <h1 id="explanation-h">Count people at the edge, merge them, keep the proof</h1>
  <p class="lead">Cameras produce far more video than anyone can watch. This example keeps the watching at the edge. A detector counts people in each camera, a fusion node merges the counts, and only the events that matter move on, signed and archived.</p>

  <h2>What it does</h2>
  <ul class="plain">
    <li><strong>Detection stays local.</strong> YOLO runs on the node. Nothing leaves it for the node to know what it is looking at.</li>
    <li><strong>The merge happens at the edge.</strong> The fusion node adds the per-zone people counts into one combined count, on the same node, with no round trip to a control room.</li>
    <li><strong>An analyst summary is optional.</strong> When a detection warrants it the sensor asks the demo model gateway for one sentence. Rehearsals replay recorded answers; no model is called.</li>
    <li><strong>The mission updates live.</strong> Arm a new class once and every sensor picks it up within about a second, with no restart.</li>
    <li><strong>The link can drop.</strong> Sensors keep detecting and the dashboard keeps painting. Events queue on disk and the archive drains when the link returns.</li>
  </ul>

  <h2>The merge rule</h2>
  <p>The crowd flag trips when the combined count across the cameras is at least 3 (<code>EDGE_CROWD_THRESHOLD</code>), every camera sees at least one person, and the busiest sees at least two. It fires once per crossing and then waits for the count to drop.</p>
  <div class="cases">
    <p><strong>Flag.</strong> North 2 and south 1 make 3, both zones occupied, one with two.</p>
    <p><strong>No flag.</strong> North 4 and south 0 make 4, but the crowd is in one camera only.</p>
  </div>

  <h2>Two examples on one engine</h2>
  <p>The Edge ISR cascade is the headline: five pipelines. Box counting is the original warehouse demo: four pipelines. Both run as Expanso Edge jobs from <code>jobs/</code>, and the explorer below walks through every one of them.</p>

  <h2>Where the sample data comes from</h2>
  <p>Every record in the explorer is real output, never typed in. The stage inputs and outputs were produced by Expanso Edge {engine} on {built}.</p>
  <ul class="plain">{sample_data}</ul>
</section>

<section id="explorer" aria-labelledby="explorer-h">
  <h2 id="explorer-h">Step explorer</h2>
  <p>Pick an example and a pipeline, then step through its stages. Each stage shows its exact configuration and the real records going in and coming out. The Left and Right arrow keys page the stages; the page does not scroll when they do.</p>
  <div class="tabs" role="group" aria-label="Example">{example_tabs}</div>
  {examples}
  <div class="toolbar" role="group" aria-label="Stage controls">
    <div class="pager">
      <button type="button" id="stage-prev" class="btn">Previous stage</button>
      <span id="stage-position" class="position" aria-live="polite">Stage 1</span>
      <button type="button" id="stage-next" class="btn">Next stage</button>
    </div>
    <div class="actions">
      <button type="button" id="copy-stage-yaml" class="btn">Copy configuration</button>
      <span id="copy-yaml-status" class="feedback" role="status" aria-live="polite"></span>
    </div>
    <div class="actions">
      <button type="button" id="copy-stage-output" class="btn">Copy output</button>
      <span id="copy-output-status" class="feedback" role="status" aria-live="polite"></span>
    </div>
    <div class="actions">
      <a id="download-stage-output" class="btn" href="data/" download>Download output</a>
      <span id="download-output-status" class="feedback" role="status" aria-live="polite"></span>
    </div>
    <div class="actions">
      <a id="download-job" class="btn" href="data/" data-download download>Download job file</a>
      <span id="download-job-status" class="feedback" role="status" aria-live="polite"></span>
    </div>
  </div>
</section>

<section id="run" aria-labelledby="run-h">
  <h2 id="run-h">Run it</h2>
  {run}
</section>

<section id="deploy" aria-labelledby="deploy-h">
  <h2 id="deploy-h">Deploy it</h2>
  {deploy}
</section>
</main>

<footer class="foot">
  <p>This page is generated by <code>scripts/build-guide.py</code> from the fixtures in <code>fixtures/</code>. Proof report: <a href="https://github.com/aronchick/expanso-edge-video-and-event-detection/blob/main/docs/PROOF.md">docs/PROOF.md</a>.</p>
</footer>
<script src="guide.js"></script>
</body>
</html>
"""


def write(files: dict[Path, str | bytes]) -> None:
    if (OUT / "data").exists():
        shutil.rmtree(OUT / "data")

    for relative, content in files.items():
        target = OUT / relative
        target.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")


def stale(files: dict[Path, str | bytes]) -> list[str]:
    problems = []

    for relative, content in files.items():
        target = OUT / relative
        expected = content if isinstance(content, bytes) else content.encode("utf-8")

        if not target.is_file() or target.read_bytes() != expected:
            problems.append(str(target.relative_to(ROOT)))

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="fail if the page is out of date")
    args = parser.parse_args()
    files = build()

    if args.check:
        problems = stale(files)

        if problems:
            print(
                "out of date, run scripts/build-guide.py:", *problems, sep="\n  ", file=sys.stderr
            )

            return 1

        return 0

    write(files)
    print(f"wrote {len(files)} files under {OUT.relative_to(ROOT)}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
