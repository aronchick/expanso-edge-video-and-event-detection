"""scripts/tail-ndjson.py: the follower the archive pipeline reads from."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "tail-ndjson.py"


def _run(args, tmp_path, seconds=1.5):
    process = subprocess.Popen(
        [sys.executable, str(SCRIPT), *args],
        stdout=subprocess.PIPE,
        text=True,
        cwd=tmp_path,
    )
    time.sleep(seconds)
    process.terminate()
    out, _ = process.communicate(timeout=5)

    return out.splitlines()


def test_reads_existing_lines_then_follows_appends(tmp_path):
    log = tmp_path / "events.ndjson"
    log.write_text('{"a":1}\n{"a":2}\n')
    process = subprocess.Popen(
        [sys.executable, str(SCRIPT), str(log), "--poll", "0.05"],
        stdout=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.6)

    with log.open("a") as handle:
        handle.write('{"a":3}\n{"a":4')  # the last line is still being written

    time.sleep(0.6)
    process.terminate()
    out, _ = process.communicate(timeout=5)

    assert out.splitlines() == ['{"a":1}', '{"a":2}', '{"a":3}']


def test_checkpoint_resumes_after_restart(tmp_path):
    log = tmp_path / "events.ndjson"
    checkpoint = tmp_path / "offset"
    log.write_text('{"a":1}\n{"a":2}\n')

    first = _run([str(log), "--checkpoint", str(checkpoint), "--poll", "0.05"], tmp_path)
    log.write_text(log.read_text() + '{"a":3}\n')
    second = _run([str(log), "--checkpoint", str(checkpoint), "--poll", "0.05"], tmp_path)

    assert first == ['{"a":1}', '{"a":2}']
    assert second == ['{"a":3}']
