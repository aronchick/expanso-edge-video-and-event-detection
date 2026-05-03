#!/usr/bin/env python3
"""Collapse GroundingDINO subword-token labels back to a canonical class
and IoU-dedupe hits within each frame.

GroundingDINO's open-vocabulary head returns BERT subword pieces as labels:
a prompt of "drone . quadcopter . uav ." produces hits labeled
'drone' / 'uav' / 'quadcopter' but also '##v' / 'ua' / 'dronecopter' /
'##cop' etc., depending on which subword tokens activate. For training
data we want a single canonical class; for evaluation we want one hit
per physical object per frame.

Usage:
    python3 scripts/normalize_relabel_labels.py IN.ndjson \\
        [--out OUT.ndjson] [--class drone] [--iou 0.5] \\
        [--min-conf 0.25] [--min-area-px 64]

Stdlib-only, no venv needed.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def iou(a: list[float], b: list[float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    aa = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    bb = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = aa + bb - inter
    return inter / union if union > 0 else 0.0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("input", type=Path)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--class", dest="canonical_class", default="drone",
                   help="Canonical class name to assign to all kept hits.")
    p.add_argument("--iou", type=float, default=0.5,
                   help="IoU threshold above which two hits in the same frame "
                        "are considered duplicates (keep higher confidence).")
    p.add_argument("--min-conf", type=float, default=0.25)
    p.add_argument("--min-area-px", type=float, default=64.0,
                   help="Discard boxes with area smaller than this (px²) — "
                        "filters tokenizer noise that picks up on tiny patches.")
    args = p.parse_args()

    if not args.input.is_file():
        print(f"input not found: {args.input}", file=sys.stderr)
        return 2
    out_path = args.out or args.input.with_suffix(".normalized.ndjson")

    raw_labels = Counter()
    kept_per_frame = Counter()
    total_in = 0
    total_kept = 0
    n_frames = 0
    n_frames_with_hits = 0

    with args.input.open() as fin, out_path.open("w") as fout:
        for line in fin:
            event = json.loads(line)
            n_frames += 1
            kept: list[dict] = []
            for hit in event.get("yolo_hits") or []:
                total_in += 1
                raw_labels[hit.get("label", "")] += 1
                if hit.get("confidence", 0.0) < args.min_conf:
                    continue
                bbox = hit.get("bbox") or [0, 0, 0, 0]
                x1, y1, x2, y2 = bbox
                area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
                if area < args.min_area_px:
                    continue
                # Drop into the canonical class regardless of which subword
                # token activated; the prompt-class set is fixed by the user.
                normalized = {
                    "label": args.canonical_class,
                    "confidence": hit["confidence"],
                    "bbox": bbox,
                    "raw_label": hit.get("label", ""),
                }
                merged = False
                for k in kept:
                    if iou(k["bbox"], bbox) >= args.iou:
                        if normalized["confidence"] > k["confidence"]:
                            k.update(normalized)
                        merged = True
                        break
                if not merged:
                    kept.append(normalized)

            event["yolo_hits"] = kept
            event["normalized"] = {
                "canonical_class": args.canonical_class,
                "iou_threshold": args.iou,
                "min_conf": args.min_conf,
                "min_area_px": args.min_area_px,
            }
            fout.write(json.dumps(event) + "\n")
            total_kept += len(kept)
            kept_per_frame[len(kept)] += 1
            if kept:
                n_frames_with_hits += 1

    print(f"input:  {args.input}")
    print(f"output: {out_path}")
    print(f"frames: {n_frames}  with-hits: {n_frames_with_hits} "
          f"({100*n_frames_with_hits/n_frames if n_frames else 0:.1f}%)")
    print(f"hits in: {total_in}  hits kept: {total_kept}  "
          f"compression: {100*(1 - total_kept/total_in) if total_in else 0:.1f}%")
    print(f"hits-per-frame distribution: {dict(sorted(kept_per_frame.items()))}")
    print(f"top-15 raw labels (input): "
          f"{dict(raw_labels.most_common(15))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
