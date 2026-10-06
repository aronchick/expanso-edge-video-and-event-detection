#!/usr/bin/env python3
"""Follow a growing JSON Lines file and print each new line to stdout.

The `file` input of an Expanso pipeline reads a file once and exits, and a
piped `tail -F` is block-buffered on some platforms. This is the small,
stdlib-only follower the archive pipeline uses as its subprocess input.

  tail-ndjson.py events.ndjson                       # start at the beginning
  tail-ndjson.py events.ndjson --checkpoint .offset  # resume after a restart
  tail-ndjson.py events.ndjson --from-end            # only lines written from now on

With --checkpoint the byte offset of the last printed line is saved after each
line, so a restart continues where it stopped instead of re-sending history.
A file that shrinks (rotated or truncated) is read again from the top.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


def read_offset(checkpoint: Path | None) -> int:
    if checkpoint is None or not checkpoint.is_file():
        return 0

    try:
        return int(checkpoint.read_text(encoding="utf-8").strip() or "0")
    except ValueError:
        return 0


def write_offset(checkpoint: Path | None, offset: int) -> None:
    if checkpoint is None:
        return

    temporary = checkpoint.with_suffix(checkpoint.suffix + ".tmp")
    temporary.write_text(str(offset), encoding="utf-8")
    os.replace(temporary, checkpoint)


def follow(path: Path, checkpoint: Path | None, from_end: bool, poll: float) -> None:
    while not path.exists():
        time.sleep(poll)

    offset = read_offset(checkpoint)

    if from_end and offset == 0:
        offset = path.stat().st_size

    pending = b""

    while True:
        size = path.stat().st_size if path.exists() else 0

        if size < offset:
            offset, pending = 0, b""

        if size == offset:
            time.sleep(poll)
            continue

        with path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read()

        data = pending + chunk
        lines = data.split(b"\n")
        pending = lines.pop()
        offset += len(chunk)
        consumed = offset - len(pending)

        for line in lines:
            if line.strip():
                sys.stdout.write(line.decode("utf-8", errors="replace") + "\n")

        sys.stdout.flush()
        write_offset(checkpoint, consumed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("path", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--from-end", action="store_true")
    parser.add_argument("--poll", type=float, default=0.2, help="seconds between checks")
    args = parser.parse_args()

    try:
        follow(args.path, args.checkpoint, args.from_end, args.poll)
    except KeyboardInterrupt:
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
