#!/usr/bin/env python3
"""Assemble dataset-3class/yolo_dataset/ for esc-finetune.

Reads dataset-3class/{images,labels}/, splits into train/val, copies the
files into dataset-3class/yolo_dataset/{train,val}/{images,labels}/, and
writes data.yaml with nc=3 and names=[person, backpack, drone].

Skips frames whose label files are empty (no detections found by the
auto-labelers — training on those poisons the model).

Usage:
    uv run scripts/export_3class.py
    uv run scripts/export_3class.py --val-split 0.15
"""

from __future__ import annotations

import argparse
import random
import shutil
import sys
from collections import Counter
from pathlib import Path


DATASET = Path("dataset-3class")
IMAGES = DATASET / "images"
LABELS = DATASET / "labels"

# Match the index → name mapping used by ingest_3class.py.
CLASS_NAMES_ORDERED = ["person", "backpack", "drone"]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--val-split", type=float, default=0.2,
                   help="Fraction of usable frames held out for validation (def 0.2)")
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    images = sorted(IMAGES.glob("*.jpg"))
    labels = sorted(LABELS.glob("*.txt"))
    if not images:
        print(f"no images in {IMAGES}", file=sys.stderr)
        return 1

    label_stems = {l.stem for l in labels}
    pairs: list[tuple[Path, Path]] = []
    skipped_no_label = 0
    skipped_empty = 0
    class_counts: Counter[int] = Counter()

    for img in images:
        if img.stem not in label_stems:
            skipped_no_label += 1
            continue
        lbl = LABELS / f"{img.stem}.txt"
        text = lbl.read_text().strip()
        if not text:
            skipped_empty += 1
            continue
        pairs.append((img, lbl))
        for line in text.splitlines():
            try:
                cls = int(line.split()[0])
                class_counts[cls] += 1
            except (ValueError, IndexError):
                pass

    if not pairs:
        print(f"no usable image+label pairs (no_label={skipped_no_label}, empty={skipped_empty})",
              file=sys.stderr)
        return 2

    print(f"{len(pairs)} usable pairs (no_label={skipped_no_label}, empty={skipped_empty})")
    print("class instance counts:")
    for idx, name in enumerate(CLASS_NAMES_ORDERED):
        print(f"  {idx}={name:<10} {class_counts.get(idx, 0)} instances")

    if not class_counts.get(2):
        print("WARN: zero drone labels — model will not learn drone class. "
              "Re-ingest the drone clip or check YOLO-World confidence threshold.",
              file=sys.stderr)

    rnd = random.Random(args.seed)
    rnd.shuffle(pairs)
    split = int(len(pairs) * (1 - args.val_split))
    train = pairs[:split]
    val = pairs[split:]

    out = DATASET / "yolo_dataset"
    if out.exists():
        shutil.rmtree(out)
    for sub in ("train", "val"):
        (out / sub / "images").mkdir(parents=True, exist_ok=True)
        (out / sub / "labels").mkdir(parents=True, exist_ok=True)
    for which, group in (("train", train), ("val", val)):
        for img, lbl in group:
            shutil.copy2(img, out / which / "images" / img.name)
            shutil.copy2(lbl, out / which / "labels" / lbl.name)

    data_yaml = out / "data.yaml"
    data_yaml.write_text(
        f"path: {out.resolve()}\n"
        f"train: train/images\n"
        f"val: val/images\n"
        f"\n"
        f"nc: {len(CLASS_NAMES_ORDERED)}\n"
        f"names: {CLASS_NAMES_ORDERED}\n"
    )

    print(f"\nExported to {out}/")
    print(f"  train: {len(train)} images")
    print(f"  val:   {len(val)} images")
    print(f"\nNext:")
    print(f"  uv run esc-finetune {data_yaml} --base yolov8n.pt --epochs 60")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
