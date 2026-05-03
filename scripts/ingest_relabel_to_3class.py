#!/usr/bin/env python3
"""Ingest a (MP4, normalized relabel NDJSON, class_id) pair into dataset-3class/.

Bridges scripts/relabel_video.py + normalize_relabel_labels.py output into
the dataset-3class/{images,labels}/ shape that scripts/export_3class.py and
esc-finetune already understand.

For each frame referenced in the NDJSON, we:
  - Decode that frame from the MP4 (same fps stride that produced the NDJSON).
  - Pick the highest-confidence kept hit (top-1 per frame per class).
  - Write image to dataset-3class/images/<subject>_<stem>_<NNNNN>.jpg.
  - Write label to dataset-3class/labels/<subject>_<stem>_<NNNNN>.txt
    with one line: '<class_id> <cx> <cy> <w> <h>' normalized to the frame size.

Frames with zero kept hits are skipped (training on empty labels poisons
the recall floor; export_3class.py also filters them).

Usage:
    python3 scripts/ingest_relabel_to_3class.py \\
        --video VIDEO.mp4 --ndjson NDJSON.normalized.ndjson \\
        --class-id 2 --subject drone --fps 5 \\
        [--dataset-dir dataset-3class]

Class id convention (matches scripts/ingest_3class.py):
    0 = person
    1 = backpack
    2 = drone
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image


def iter_frames(video: Path, fps: float):
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(video),
        "-vf", f"fps={fps}",
        "-c:v", "mjpeg", "-q:v", "3",
        "-f", "image2pipe", "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1 << 22)
    assert proc.stdout is not None and proc.stderr is not None
    SOI, EOI = b"\xff\xd8\xff", b"\xff\xd9"
    buf = bytearray()
    idx = 0
    try:
        while True:
            chunk = proc.stdout.read(1 << 16)
            if not chunk:
                break
            buf.extend(chunk)
            while True:
                soi = buf.find(SOI)
                if soi < 0:
                    break
                eoi = buf.find(EOI, soi)
                if eoi < 0:
                    if soi > 0:
                        del buf[:soi]
                    break
                jpeg = bytes(buf[soi : eoi + 2])
                del buf[: eoi + 2]
                yield idx, jpeg
                idx += 1
    finally:
        proc.stdout.close()
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        rc = proc.wait()
        proc.stderr.close()
        if rc != 0 and idx == 0:
            raise RuntimeError(f"ffmpeg exited {rc}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--video", type=Path, required=True)
    p.add_argument("--ndjson", type=Path, required=True)
    p.add_argument("--class-id", type=int, required=True)
    p.add_argument("--subject", required=True,
                   help="Tag used in output filenames, e.g. 'drone' or 'backpack'")
    p.add_argument("--fps", type=float, default=5.0)
    p.add_argument("--dataset-dir", type=Path, default=Path("dataset-3class"))
    p.add_argument("--min-conf", type=float, default=0.25)
    args = p.parse_args()

    if not args.video.is_file():
        print(f"video not found: {args.video}", file=sys.stderr)
        return 2
    if not args.ndjson.is_file():
        print(f"ndjson not found: {args.ndjson}", file=sys.stderr)
        return 2

    images_dir = args.dataset_dir / "images"
    labels_dir = args.dataset_dir / "labels"
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)

    # Map frame_idx -> top-1 hit from the NDJSON.
    top_per_frame: dict[int, dict] = {}
    with args.ndjson.open() as f:
        for line in f:
            event = json.loads(line)
            idx = event.get("frame_idx")
            if idx is None:
                continue
            best = None
            for h in event.get("yolo_hits") or []:
                if h.get("confidence", 0.0) < args.min_conf:
                    continue
                if best is None or h["confidence"] > best["confidence"]:
                    best = h
            if best is not None:
                top_per_frame[int(idx)] = best

    print(f"[setup] frames with kept hit: {len(top_per_frame)}", flush=True)
    if not top_per_frame:
        print("no hits to ingest; aborting.", file=sys.stderr)
        return 1

    stem = args.video.stem
    n_written = 0
    n_skipped = 0
    for idx, jpeg in iter_frames(args.video, args.fps):
        hit = top_per_frame.get(idx)
        if hit is None:
            n_skipped += 1
            continue

        # Figure out the image size from the JPEG so we can normalize.
        with Image.open(BytesIO(jpeg)) as im:
            W, H = im.size

        x1, y1, x2, y2 = hit["bbox"]
        # Clamp into the frame, just in case.
        x1 = max(0.0, min(float(x1), W))
        x2 = max(0.0, min(float(x2), W))
        y1 = max(0.0, min(float(y1), H))
        y2 = max(0.0, min(float(y2), H))
        if x2 <= x1 or y2 <= y1:
            n_skipped += 1
            continue
        cx = ((x1 + x2) / 2.0) / W
        cy = ((y1 + y2) / 2.0) / H
        w = (x2 - x1) / W
        h = (y2 - y1) / H

        base = f"{args.subject}_{stem}_{idx:05d}"
        (images_dir / f"{base}.jpg").write_bytes(jpeg)
        (labels_dir / f"{base}.txt").write_text(
            f"{args.class_id} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n"
        )
        n_written += 1

        if n_written and n_written % 200 == 0:
            print(f"[progress] written={n_written} skipped={n_skipped}", flush=True)

    print(f"[done] subject={args.subject} class_id={args.class_id} "
          f"written={n_written} skipped={n_skipped} → {args.dataset_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
