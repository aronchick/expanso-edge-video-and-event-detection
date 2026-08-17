#!/usr/bin/env python3
"""Extract drone training frames from a phone video clip.

Reads an MP4/MOV file, samples frames at the requested rate, filters out
motion-blurred frames using Laplacian variance, and drops the keepers into
the dataset-drone/ tree where esc-dataset label can pick them up.

Why pre-filter blur:
    Gemini Flash mislabels motion-blurred frames — it either misses the
    drone entirely (empty label, useless for training) or boxes a smear
    that hurts training. The Laplacian-variance score is a cheap robust
    sharpness proxy; a threshold around 100 keeps in-focus frames and
    drops obvious smears.

Usage:
    uv run scripts/ingest_drone_video.py path/to/drone.mp4 \\
        --fps 10              # sample rate from source video (def 10)
        --keep-top 400        # max # of frames to keep after sharpness sort
        --min-sharpness 60    # absolute floor (drop anything below)

After this, run:
    uv run esc-dataset label   --target drone
    uv run esc-dataset export  --target drone
    uv run esc-finetune dataset-drone/yolo_dataset/data.yaml --base yolov8n.pt
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

DATASET_DIR = Path("dataset-drone")
IMAGES_DIR = DATASET_DIR / "images"
META_DIR = DATASET_DIR / "meta"


def _laplacian_variance(gray: np.ndarray) -> float:
    """Higher = sharper. Standard motion-blur detector."""
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("video", help="Path to drone video (mp4, mov, etc)")
    p.add_argument("--fps", type=float, default=10.0,
                   help="Frames per second to extract from the video (def 10)")
    p.add_argument("--keep-top", type=int, default=400,
                   help="Keep at most this many sharpest frames (def 400)")
    p.add_argument("--min-sharpness", type=float, default=60.0,
                   help="Drop any frame with Laplacian variance below this (def 60)")
    p.add_argument("--prefix", default=None,
                   help="Filename prefix for kept frames (def: video filename stem)")
    p.add_argument("--expected-boxes", type=int, default=1,
                   help="Number of drones expected per frame (used by labeler, def 1)")
    args = p.parse_args()

    video_path = Path(args.video).expanduser().resolve()
    if not video_path.exists():
        print(f"ERROR: {video_path} does not exist", file=sys.stderr)
        return 1

    prefix = args.prefix or video_path.stem.replace(" ", "_")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"ERROR: cannot open {video_path}", file=sys.stderr)
        return 1

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    src_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    src_duration_sec = src_total / src_fps if src_fps else 0
    sample_every = max(1, int(round(src_fps / args.fps)))

    print(f"Source : {video_path}")
    print(f"  src fps        : {src_fps:.1f}")
    print(f"  src total frames: {src_total} ({src_duration_sec:.0f}s)")
    print(f"  sampling every : {sample_every} frame(s) → ~{args.fps:.1f} fps")
    print(f"  expected output: ~{src_total // sample_every} candidate frames")
    print(f"  sharpness floor: {args.min_sharpness}")
    print(f"  keep top       : {args.keep_top} frames")
    print()

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    META_DIR.mkdir(parents=True, exist_ok=True)

    candidates: list[tuple[float, np.ndarray, int]] = []  # (sharpness, frame, src_index)
    src_idx = 0
    sampled = 0
    accepted = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if src_idx % sample_every == 0:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            sharpness = _laplacian_variance(gray)
            sampled += 1
            if sharpness >= args.min_sharpness:
                candidates.append((sharpness, frame, src_idx))
                accepted += 1
            if sampled % 100 == 0:
                print(f"  sampled {sampled} frames, {accepted} above sharpness floor")
        src_idx += 1

    cap.release()

    print(f"\nSampled {sampled}, kept {accepted} above sharpness {args.min_sharpness}")

    if not candidates:
        print("ERROR: no usable frames. Lower --min-sharpness or improve video.", file=sys.stderr)
        return 2

    # Keep the top-K sharpest. Sorting is descending; if we have fewer
    # than keep_top, we keep them all.
    candidates.sort(key=lambda c: c[0], reverse=True)
    keepers = candidates[: args.keep_top]
    print(f"Writing top {len(keepers)} sharpest frames\n")

    # Number them in time order so consecutive frame_NNNNN.jpg names ~match
    # capture order — easier to spot patterns when scrubbing the review/ dir.
    keepers.sort(key=lambda c: c[2])

    # Continue numbering from whatever's already in dataset-drone/images.
    existing = len(list(IMAGES_DIR.glob("*.jpg")))

    sharpness_summary = []
    for i, (sharpness, frame, src_index) in enumerate(keepers):
        idx = existing + i
        stem = f"{prefix}_{idx:05d}"
        out_img = IMAGES_DIR / f"{stem}.jpg"
        cv2.imwrite(str(out_img), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])

        meta = {
            "expected_boxes": args.expected_boxes,
            "camera_id": prefix,           # so esc-dataset's filenaming is happy
            "target": "drone",
            "source_video": str(video_path),
            "source_frame_index": src_index,
            "sharpness": sharpness,
        }
        (META_DIR / f"{stem}.json").write_text(json.dumps(meta))
        sharpness_summary.append(sharpness)

    print(f"OK   wrote {len(keepers)} frames to {IMAGES_DIR}")
    print(f"     sharpness range: {min(sharpness_summary):.1f} – {max(sharpness_summary):.1f}")
    print(f"     median:          {sorted(sharpness_summary)[len(sharpness_summary) // 2]:.1f}")
    print()
    print("Next:")
    print("  uv run esc-dataset label  --target drone")
    print("  uv run esc-dataset export --target drone")
    print("  uv run esc-finetune dataset-drone/yolo_dataset/data.yaml --base yolov8n.pt")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
