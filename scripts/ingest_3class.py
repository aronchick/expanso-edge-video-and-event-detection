#!/usr/bin/env python3
"""Ingest a phone clip for the 3-class fine-tune dataset.

Builds dataset-3class/ with images + YOLO label files containing class
indices [0=person, 1=backpack, 2=drone]. One invocation per (video,
subject) pair; run three times total for a clean dataset.

Subjects:
    drone             — single small civilian drone in frame.
                        Labels via yolov8s-worldv2.pt with custom text
                        prompts ("small civilian quadcopter drone", ...).
                        We tried Gemini Flash first; it forced "exactly 1
                        drone" and confidently mislabeled ceiling fixtures
                        and bystanders when no drone was visible. YOLO-
                        World is open-vocab AND threshold-respecting — it
                        emits no boxes when nothing in frame matches,
                        which is the right default for auto-labeling.
    person            — single person in frame, no backpack.
                        Labels via local COCO yolov8s.engine (already on
                        the Jetson, no API spend, ~30 fps).
    person+backpack   — single person carrying a backpack.
                        Labels via local COCO yolov8s.engine; both
                        person and backpack hits land in the same label.

Usage on the Jetson:
    LD_LIBRARY_PATH=/usr/local/cuda/lib64:.../nvidia/cu12/lib \\
      uv run scripts/ingest_3class.py /path/to/IMG_drone.MOV --subject drone
    uv run scripts/ingest_3class.py /path/to/IMG_person.MOV --subject person
    uv run scripts/ingest_3class.py /path/to/IMG_person_pack.MOV --subject person+backpack

After all three:
    uv run scripts/export_3class.py        # writes dataset-3class/yolo_dataset/data.yaml
    uv run esc-finetune dataset-3class/yolo_dataset/data.yaml --base yolov8n.pt
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np


DATASET = Path("dataset-3class")
IMAGES = DATASET / "images"
LABELS = DATASET / "labels"
META = DATASET / "meta"
REVIEW = DATASET / "review"

# Single source of truth for the 3-class index → name mapping. Used by
# both this script and export_3class.py.
CLASS_INDICES = {
    "person": 0,
    "backpack": 1,
    "drone": 2,
}
CLASS_NAMES_ORDERED = ["person", "backpack", "drone"]

VALID_SUBJECTS = {"drone", "person", "person+backpack"}


def _laplacian_variance(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _yolo_bbox_line(cls_idx: int, x1: float, y1: float, x2: float, y2: float, w: int, h: int) -> str:
    xc = (x1 + x2) / 2 / w
    yc = (y1 + y2) / 2 / h
    bw = (x2 - x1) / w
    bh = (y2 - y1) / h
    return f"{cls_idx} {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}"


# ──────────────────────────────────────────────────────────────────────────
# Frame sampling + sharpness filter
# ──────────────────────────────────────────────────────────────────────────


def sample_frames(video_path: Path, fps: float, min_sharpness: float, keep_top: int) -> list[tuple[float, np.ndarray, int]]:
    """Sample frames from video, filter by sharpness, return top-K sharpest."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise SystemExit(f"cannot open {video_path}")

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    src_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    sample_every = max(1, int(round(src_fps / fps)))

    print(f"  src fps={src_fps:.1f}, frames={src_total} ({src_total / src_fps:.0f}s)")
    print(f"  sampling every {sample_every} → ~{fps:.1f} fps")

    candidates: list[tuple[float, np.ndarray, int]] = []
    src_idx = 0
    sampled = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if src_idx % sample_every == 0:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            sharpness = _laplacian_variance(gray)
            sampled += 1
            if sharpness >= min_sharpness:
                candidates.append((sharpness, frame, src_idx))
        src_idx += 1
    cap.release()

    print(f"  sampled {sampled}, kept {len(candidates)} above sharpness {min_sharpness}")
    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates[:keep_top]


# ──────────────────────────────────────────────────────────────────────────
# Subject-specific labelers
# ──────────────────────────────────────────────────────────────────────────


_DRONE_WORLD_MODEL = None  # YOLO-World, lazy singleton


def _world_drone_model():
    """Load yolov8s-worldv2.pt with drone-related text prompts.

    Why YOLO-World over Gemini: Gemini Flash is forced by the
    "exactly 1 drone" prompt to pick the most-drone-shaped object even
    when no drone is visible (we've seen it box ceiling fixtures, people,
    and empty walls). YOLO-World is open-vocabulary AND respects a
    confidence threshold — it returns nothing when no drone-shaped
    object is in frame, which is the right default for auto-labeling.
    """
    global _DRONE_WORLD_MODEL
    if _DRONE_WORLD_MODEL is None:
        from ultralytics import YOLO
        # The repo ships yolov8s-worldv2.pt at the project root on the
        # Jetson. If it's missing, fall back to whatever is on PATH /
        # ultralytics will auto-download (it's on the Hub).
        path = Path("yolov8s-worldv2.pt")
        if not path.exists():
            path = Path("/home/daaronch/security-cameras/yolov8s-worldv2.pt")
        print(f"  loading YOLO-World drone labeler: {path}")
        m = YOLO(str(path))
        # set_classes installs custom-text class prompts. Keep the list
        # tight: prompts that refer to small civilian quadcopters, NOT
        # full-size aircraft (which yolov8s-world conflates).
        m.set_classes([
            "small civilian quadcopter drone",
            "consumer drone with rotors",
            "small UAV",
        ])
        _DRONE_WORLD_MODEL = m
    return _DRONE_WORLD_MODEL


# Confidence floor for drone detection. yolov8s-worldv2 with custom prompts
# tends to fire low-confidence false positives on dark blobs; 0.20 is a
# pragmatic floor that keeps real distant drone hits while dropping noise.
DRONE_WORLD_CONF = 0.20


def label_drone_via_world(image_path: Path) -> list[tuple[int, float, float, float, float]]:
    """Returns (class_idx, x1, y1, x2, y2) tuples for drones found by YOLO-World."""
    model = _world_drone_model()
    frame = cv2.imread(str(image_path))
    results = model(frame, verbose=False, conf=DRONE_WORLD_CONF)[0]
    out = []
    drone_idx = CLASS_INDICES["drone"]
    for conf, box in zip(results.boxes.conf, results.boxes.xyxy):
        x1, y1, x2, y2 = (float(v) for v in box)
        out.append((drone_idx, x1, y1, x2, y2))
    return out


_COCO_MODEL = None  # COCO yolov8s.engine, lazy singleton


def _coco_model():
    global _COCO_MODEL
    if _COCO_MODEL is None:
        from ultralytics import YOLO
        engine_path = "/home/daaronch/security-cameras/yolov8s.engine"
        if not Path(engine_path).exists():
            engine_path = "yolov8s.pt"
        print(f"  loading COCO model: {engine_path}")
        _COCO_MODEL = YOLO(engine_path)
    return _COCO_MODEL


def label_person_via_coco(image_path: Path) -> list[tuple[int, float, float, float, float]]:
    """Auto-label persons in a frame via the on-Jetson yolov8s.engine."""
    model = _coco_model()
    frame = cv2.imread(str(image_path))
    results = model(frame, verbose=False, classes=[0], conf=0.5)[0]
    out = []
    person_idx = CLASS_INDICES["person"]
    for conf, box in zip(results.boxes.conf, results.boxes.xyxy):
        x1, y1, x2, y2 = (float(v) for v in box)
        out.append((person_idx, x1, y1, x2, y2))
    return out


def label_person_backpack_via_coco(image_path: Path) -> list[tuple[int, float, float, float, float]]:
    """Auto-label persons + backpacks in one pass."""
    model = _coco_model()
    frame = cv2.imread(str(image_path))
    # COCO classes: 0=person, 24=backpack. Drop conf threshold for backpack
    # since backpacks at distance get confidences in the .35-.55 range; the
    # COCO ground truth at ~.4 is still better than "no label."
    results = model(frame, verbose=False, classes=[0, 24], conf=0.35)[0]
    out = []
    person_idx = CLASS_INDICES["person"]
    backpack_idx = CLASS_INDICES["backpack"]
    for cls, conf, box in zip(results.boxes.cls, results.boxes.conf, results.boxes.xyxy):
        cls_int = int(cls)
        x1, y1, x2, y2 = (float(v) for v in box)
        if cls_int == 0:
            out.append((person_idx, x1, y1, x2, y2))
        elif cls_int == 24:
            out.append((backpack_idx, x1, y1, x2, y2))
    return out


SUBJECT_LABELERS = {
    "drone": label_drone_via_world,
    "person": label_person_via_coco,
    "person+backpack": label_person_backpack_via_coco,
}


# ──────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("video", help="Path to the phone clip")
    p.add_argument("--subject", required=True, choices=sorted(VALID_SUBJECTS))
    p.add_argument("--fps", type=float, default=10.0)
    p.add_argument("--keep-top", type=int, default=400)
    p.add_argument("--min-sharpness", type=float, default=50.0)
    p.add_argument("--prefix", default=None,
                   help="Filename prefix (def: <subject>_<videostem>)")
    args = p.parse_args()

    video_path = Path(args.video).expanduser().resolve()
    if not video_path.exists():
        print(f"ERROR: {video_path} not found", file=sys.stderr)
        return 1

    prefix = args.prefix or f"{args.subject.replace('+', '-')}_{video_path.stem}"
    prefix = prefix.replace(" ", "_")
    print(f"\n=== ingest_3class subject={args.subject} ===")
    print(f"video : {video_path}")
    print(f"prefix: {prefix}")

    for d in (IMAGES, LABELS, META, REVIEW):
        d.mkdir(parents=True, exist_ok=True)

    keepers = sample_frames(video_path, args.fps, args.min_sharpness, args.keep_top)
    if not keepers:
        print("no keepers — lower --min-sharpness or improve video", file=sys.stderr)
        return 2

    # Time-order so review/ scrubs in capture order
    keepers.sort(key=lambda c: c[2])

    labeler = SUBJECT_LABELERS[args.subject]
    existing = len(list(IMAGES.glob("*.jpg")))

    written = 0
    empty = 0
    for i, (sharpness, frame, src_idx) in enumerate(keepers):
        idx = existing + i
        stem = f"{prefix}_{idx:05d}"
        img_path = IMAGES / f"{stem}.jpg"
        cv2.imwrite(str(img_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])

        h, w = frame.shape[:2]
        try:
            dets = labeler(img_path)
        except Exception as e:
            print(f"  [{i+1}/{len(keepers)}] {stem}: labeler error: {e}")
            dets = []

        if not dets:
            empty += 1
            # write empty label file so export step can skip it without
            # treating it as "not yet labeled"
            (LABELS / f"{stem}.txt").write_text("")
            continue

        lines = [_yolo_bbox_line(c, x1, y1, x2, y2, w, h) for c, x1, y1, x2, y2 in dets]
        (LABELS / f"{stem}.txt").write_text("\n".join(lines))

        # Sanity-check render: draw boxes + class names so user can scrub
        # the review/ dir and spot-check labels at a glance.
        review = frame.copy()
        for cls_idx, x1, y1, x2, y2 in dets:
            color = [(0, 255, 0), (0, 200, 255), (255, 80, 80)][cls_idx]  # B,G,R per class
            cv2.rectangle(review, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
            cv2.putText(review, CLASS_NAMES_ORDERED[cls_idx], (int(x1), int(y1) - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
        cv2.imwrite(str(REVIEW / f"{stem}.jpg"), review, [cv2.IMWRITE_JPEG_QUALITY, 80])

        meta = {
            "subject": args.subject,
            "source_video": str(video_path),
            "source_frame_index": src_idx,
            "sharpness": sharpness,
            "n_objects": len(dets),
        }
        (META / f"{stem}.json").write_text(json.dumps(meta))

        written += 1

        if (i + 1) % 25 == 0 or i + 1 == len(keepers):
            print(f"  [{i+1}/{len(keepers)}] {written} labeled, {empty} empty")

    print(f"\nDone. {written} usable frames, {empty} empty (skipped at export).")
    print(f"Total dataset-3class images: {len(list(IMAGES.glob('*.jpg')))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
