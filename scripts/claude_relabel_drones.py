#!/usr/bin/env python3
"""Recover drone training frames missed by YOLO-World using the Claude CLI.

Pre-condition:
    scripts/ingest_3class.py has been run on the drone clip with subject=drone.
    Some frames have populated label files (YOLO-World hits, class 2);
    others have empty label files because YOLO-World had nothing above
    its confidence floor.

What this script does:
    For each empty drone-subject label file, ask `claude` (the CLI, OAuth
    against your subscription — no per-call cost) whether the image
    contains a small civilian quadcopter drone. If yes, parse a bbox out
    of the response and write it as class 2 in YOLO format. If no, leave
    the label empty (export_3class.py will skip empty files).

Why Claude instead of Gemini Flash:
    Gemini Flash with a "this image contains exactly N drones" prompt
    confidently boxes ceiling fixtures and bystanders when no drone is
    visible. Claude is structured to refuse — "I don't see a drone" —
    which keeps false positives out of the training set.

Usage on Jetson:
    uv run scripts/claude_relabel_drones.py
    uv run scripts/claude_relabel_drones.py --max-frames 50  # quick try
    uv run scripts/claude_relabel_drones.py --workers 3      # parallel CLI

Each CLI call takes 5-10s, so 340 sequential = ~30-55 min. Default
workers=3 cuts that to ~15 min while staying under Claude's rate limit.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import cv2


DATASET = Path("dataset-3class")
IMAGES = DATASET / "images"
LABELS = DATASET / "labels"
META = DATASET / "meta"
REVIEW = DATASET / "review"

DRONE_CLASS_INDEX = 2  # matches CLASS_INDICES in ingest_3class.py


PROMPT_TEMPLATE = (
    "Look at the image at {image_path}.\n\n"
    "Examine it for a small civilian/hobbyist quadcopter drone. A drone here:\n"
    "  - has 4 visible rotors/arms\n"
    "  - is roughly palm-sized to shoebox-sized\n"
    "  - may be in mid-air (hovering), on the floor, on a surface, or near "
    "the ceiling\n"
    "  - often has bright/colorful top shell against the dark/white "
    "background of the room\n\n"
    "Output rules:\n"
    "  - If you see a drone, respond with EXACTLY one JSON object on a "
    'single line: {{"x1":INT,"y1":INT,"x2":INT,"y2":INT}} — tight pixel-'
    "coordinate bounding box.\n"
    "  - If NO drone is visible, respond with exactly: NO_DRONE\n"
    "  - No explanation, no markdown fences, no extra text — just the "
    "JSON or the literal NO_DRONE."
)


def _claude_bin() -> str:
    bin_path = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")
    if not Path(bin_path).exists():
        raise SystemExit(f"claude CLI not found at {bin_path}")
    return bin_path


def _is_drone_subject(stem: str) -> bool:
    """Match the prefix scheme set by ingest_3class.py."""
    return stem.startswith("drone_") or stem.startswith("drone-")


def _ask_claude(claude_bin: str, image_path: Path, timeout: float = 60.0) -> Optional[tuple[int, int, int, int]]:
    """Returns (x1,y1,x2,y2) ints if Claude found a drone, else None.

    The CLI needs `--add-dir` to grant filesystem read access to the image,
    AND the path has to appear in the prompt text (Claude reads files
    referenced in prompts that are inside an --add-dir directory). Passing
    the path as a positional arg is treated as additional prompt text and
    Claude never actually opens the image — it returned NO_DRONE on every
    frame because it was responding to "is there a drone in this filename?"
    """
    full_prompt = PROMPT_TEMPLATE.format(image_path=image_path)
    result = subprocess.run(
        [
            claude_bin,
            "--add-dir", str(image_path.parent),
            "-p", full_prompt,
            "--output-format", "text",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {result.stderr.strip()[:200]}")

    text = result.stdout.strip()
    if not text or "NO_DRONE" in text.upper():
        return None

    # Strip code fences if present.
    text = text.replace("```json", "").replace("```", "").strip()

    # Find a single JSON object — Claude sometimes prefixes with whitespace.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None

    if not all(k in obj for k in ("x1", "y1", "x2", "y2")):
        return None
    try:
        return (int(obj["x1"]), int(obj["y1"]), int(obj["x2"]), int(obj["y2"]))
    except (TypeError, ValueError):
        return None


def _yolo_line(cls_idx: int, x1: int, y1: int, x2: int, y2: int, w: int, h: int) -> str:
    xc = (x1 + x2) / 2 / w
    yc = (y1 + y2) / 2 / h
    bw = (x2 - x1) / w
    bh = (y2 - y1) / h
    return f"{cls_idx} {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}"


def _process_one(claude_bin: str, image_path: Path) -> tuple[str, str, Optional[str]]:
    """Returns (stem, status, error_or_None)."""
    label_path = LABELS / f"{image_path.stem}.txt"
    review_path = REVIEW / f"{image_path.stem}.jpg"

    try:
        bbox = _ask_claude(claude_bin, image_path)
    except subprocess.TimeoutExpired:
        return (image_path.stem, "timeout", None)
    except Exception as e:
        return (image_path.stem, "error", str(e))

    if bbox is None:
        return (image_path.stem, "no_drone", None)

    frame = cv2.imread(str(image_path))
    if frame is None:
        return (image_path.stem, "read_fail", None)
    h, w = frame.shape[:2]

    x1, y1, x2, y2 = bbox
    # Defensive clamp — Claude occasionally returns coords slightly out of frame
    x1, x2 = max(0, min(w - 1, x1)), max(0, min(w, x2))
    y1, y2 = max(0, min(h - 1, y1)), max(0, min(h, y2))
    if x2 <= x1 or y2 <= y1:
        return (image_path.stem, "invalid_bbox", None)

    label_path.write_text(_yolo_line(DRONE_CLASS_INDEX, x1, y1, x2, y2, w, h))

    # Update review/ overlay so we can spot-check Claude's work alongside
    # the YOLO-World hits.
    review = frame.copy()
    cv2.rectangle(review, (x1, y1), (x2, y2), (0, 200, 255), 2)
    cv2.putText(review, "drone [Claude]", (x1, max(y1 - 4, 14)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1, cv2.LINE_AA)
    cv2.imwrite(str(review_path), review, [cv2.IMWRITE_JPEG_QUALITY, 80])

    return (image_path.stem, "labeled", None)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--max-frames", type=int, default=None,
                   help="Cap how many frames to process (def: all empties)")
    p.add_argument("--workers", type=int, default=3,
                   help="Parallel claude CLI calls (def 3 — keeps under rate limits)")
    args = p.parse_args()

    claude_bin = _claude_bin()

    # Find drone-subject frames whose label is empty.
    candidates: list[Path] = []
    for img in sorted(IMAGES.glob("*.jpg")):
        if not _is_drone_subject(img.stem):
            continue
        lbl = LABELS / f"{img.stem}.txt"
        if not lbl.exists():
            continue
        if lbl.stat().st_size > 0:
            continue  # already has a label (YOLO-World hit)
        candidates.append(img)

    if args.max_frames:
        candidates = candidates[: args.max_frames]

    print(f"claude bin       : {claude_bin}")
    print(f"empty drone frames: {len(candidates)}")
    print(f"workers          : {args.workers}")
    print(f"prompt          : refusal-aware (NO_DRONE | bbox json)")
    print()

    if not candidates:
        print("nothing to do — no empty drone frames found")
        return 0

    counts = {"labeled": 0, "no_drone": 0, "timeout": 0, "error": 0,
              "invalid_bbox": 0, "read_fail": 0}
    started = time.time()
    done = 0

    # Sequential vs threaded — workers > 1 uses threads; each call shells
    # out to `claude` so the GIL is released during subprocess.run.
    if args.workers <= 1:
        for img in candidates:
            stem, status, err = _process_one(claude_bin, img)
            counts[status] += 1
            done += 1
            if err:
                print(f"  [{done}/{len(candidates)}] {stem}: {status}: {err}")
            elif done <= 3 or done % 25 == 0 or done == len(candidates):
                elapsed = time.time() - started
                rate = done / elapsed if elapsed > 0 else 0
                eta = (len(candidates) - done) / rate if rate else 0
                print(f"  [{done}/{len(candidates)}] {stem}: {status}  "
                      f"({rate:.2f}/s, ETA {eta/60:.1f} min) "
                      f"labeled={counts['labeled']} no_drone={counts['no_drone']}")
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(_process_one, claude_bin, img): img for img in candidates}
            for fut in as_completed(futures):
                stem, status, err = fut.result()
                counts[status] += 1
                done += 1
                if err and counts["error"] <= 5:
                    print(f"  [{done}/{len(candidates)}] {stem}: {status}: {err}")
                elif done <= 3 or done % 25 == 0 or done == len(candidates):
                    elapsed = time.time() - started
                    rate = done / elapsed if elapsed > 0 else 0
                    eta = (len(candidates) - done) / rate if rate else 0
                    print(f"  [{done}/{len(candidates)}] {stem}: {status}  "
                          f"({rate:.2f}/s, ETA {eta/60:.1f} min) "
                          f"labeled={counts['labeled']} no_drone={counts['no_drone']}")

    elapsed = time.time() - started
    print()
    print(f"Done in {elapsed/60:.1f} min")
    for k, v in counts.items():
        print(f"  {k:15s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
