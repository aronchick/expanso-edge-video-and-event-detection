"""Dataset builder for fine-tuning YOLO on your actual camera images.

Three-step workflow:
    1. CAPTURE: Raw frames only — fast, no YOLO (on Jetson)
    2. LABEL:   Batch local YOLO labeling + optional human review (offline)
    3. EXPORT + FINETUNE

    # Capture (fast — just grabs frames, no inference)
    uv run esc-dataset capture config.yaml --boxes 1 --camera cam-inside --countdown 10 &
    uv run esc-dataset capture config.yaml --boxes 2 --camera cam-outside --countdown 10 &
    wait

    # Label all frames in batch (runs YOLO once on everything)
    uv run esc-dataset label

    # Optional: render numbered candidates for human review
    uv run esc-dataset validate

    # Export + fine-tune
    uv run esc-dataset export
    uv run esc-finetune dataset/yolo_dataset/data.yaml
"""

from __future__ import annotations

import json
import random
import shutil
import sys
import time
from pathlib import Path

import cv2
import numpy as np

# Target-specific config. Default "box" preserves the original box-counting
# workflow; "drone" was added for the Edge ISR demo. Each target writes to
# its own dataset dir so they don't clobber each other and a box training
# session can coexist with a drone training session on the same Jetson.
TARGETS: dict[str, dict] = {
    "box": {
        "dataset_dir": Path("dataset"),
        "class_names": ["box"],
        "subject_singular": "cardboard box",
        "subject_plural": "cardboard boxes",
        "subject_for_count": "cardboard box(es)",
    },
    "drone": {
        "dataset_dir": Path("dataset-drone"),
        "class_names": ["drone"],
        # Phrasing is deliberate: civilian quadcopter shape, not "airplane"
        # (the COCO airplane class is full-size aircraft and confuses the
        # vision model when we want a small UAV).
        "subject_singular": "small civilian quadcopter drone",
        "subject_plural": "small civilian quadcopter drones",
        "subject_for_count": "small civilian quadcopter drone(s)",
    },
}


def _target_config(target: str) -> dict:
    if target not in TARGETS:
        raise SystemExit(f"Unknown --target {target!r}; valid: {', '.join(TARGETS.keys())}")
    return TARGETS[target]


def _dirs(target: str) -> tuple[Path, Path, Path, Path, Path]:
    """Return (dataset, images, labels, review, meta) paths for this target."""
    base = _target_config(target)["dataset_dir"]
    return base, base / "images", base / "labels", base / "review", base / "meta"


# Back-compat: original module-level constants remain for any external
# importer; default target is "box" so behavior is identical.
DATASET_DIR = TARGETS["box"]["dataset_dir"]
IMAGES_DIR = DATASET_DIR / "images"
LABELS_DIR = DATASET_DIR / "labels"
REVIEW_DIR = DATASET_DIR / "review"
META_DIR = DATASET_DIR / "meta"

CLASS_NAMES = TARGETS["box"]["class_names"]

BOX_CLASSES = [
    "cardboard box",
    "shipping box",
    "package",
    "carton",
    "box",
    "parcel",
    "crate",
    "container",
    "brown box",
    "sealed box",
    "stacked boxes",
    "rectangular object",
    "delivery package",
    "moving box",
]


# ── Step 1: Capture (raw frames only, no YOLO) ─────────────────────────


def capture(
    config_path: str,
    expected_boxes: int,
    camera_id: str = "cam-inside",
    duration: int = 30,
    fps: float = 5.0,
    countdown: int = 0,
    target: str = "box",
) -> None:
    """Capture raw frames from camera. No YOLO — just fast frame grabs.

    Saves frames as JPEGs and a manifest with expected subject count per frame.
    Labeling happens in a separate offline batch step.

    `target` selects the dataset directory + the subject vocabulary used by
    the label/validate/export steps. Default "box" preserves the original
    box-counting workflow; "drone" was added for the Edge ISR demo.
    """
    from expanso_security_camera.config import DemoConfig

    config = DemoConfig.from_yaml(config_path)

    _, images_dir, _, _, meta_dir = _dirs(target)

    cam_url = None
    for cam in config.cameras:
        if cam.camera_id == camera_id:
            cam_url = cam.url
            break
    if cam_url is None:
        print(f"Camera '{camera_id}' not found. Available:")
        for cam in config.cameras:
            print(f"  {cam.camera_id}")
        sys.exit(1)

    for d in (images_dir, meta_dir):
        d.mkdir(parents=True, exist_ok=True)

    interval = 1.0 / fps
    total_frames = int(duration * fps)
    frame_idx = len(list(images_dir.glob("*.jpg")))

    cfg = _target_config(target)
    print(
        f"Capturing {camera_id} → {cfg['dataset_dir']}: "
        f"{expected_boxes} {cfg['subject_singular']}(s) expected"
    )
    print(f"  {total_frames} frames over {duration}s ({fps} fps)")

    if countdown > 0:
        for sec in range(countdown, 0, -1):
            print(f"  Starting in {sec}...", flush=True)
            time.sleep(1)

    print("  GO!", flush=True)

    cap = cv2.VideoCapture(cam_url)
    if not cap.isOpened():
        print(f"Cannot connect to {camera_id}")
        sys.exit(1)
    for _ in range(5):
        cap.read()

    kept = 0
    start = time.time()

    for i in range(total_frames):
        frame_start = time.time()

        ret, frame = cap.read()
        if not ret:
            cap.release()
            time.sleep(0.5)
            cap = cv2.VideoCapture(cam_url)
            if not cap.isOpened():
                print(f"  Lost connection at frame {i}")
                break
            continue

        fname = f"{camera_id}_{frame_idx:05d}"
        cv2.imwrite(str(images_dir / f"{fname}.jpg"), frame)

        # Save expected count + target so the label step knows which subject
        # to ask Gemini about. (Frames captured under one target should not
        # be relabeled as a different target without re-capture.)
        meta = {"expected_boxes": expected_boxes, "camera_id": camera_id, "target": target}
        (meta_dir / f"{fname}.json").write_text(json.dumps(meta))

        kept += 1
        frame_idx += 1

        if (i + 1) % 50 == 0:
            elapsed = int(time.time() - start)
            print(f"  [{elapsed}s] {i + 1}/{total_frames} captured")

        elapsed = time.time() - frame_start
        if elapsed < interval:
            time.sleep(interval - elapsed)

    cap.release()
    total_time = time.time() - start
    total_dataset = len(list(images_dir.glob("*.jpg")))

    print(f"\nDone! {kept} frames captured ({total_time:.0f}s)")
    print(f"  Total dataset: {total_dataset} images at {images_dir}")


# ── Step 2: Label (local YOLO on all captured frames) ──────────────────


def _call_local_yolo_for_boxes(
    image_path: str, expected_boxes: int, target: str = "box"
) -> list[dict]:
    """Run local open-vocabulary YOLO; no provider or network call is made."""
    from ultralytics import YOLO

    cfg = _target_config(target)
    model = YOLO("yolov8s-worldv2.pt")
    model.set_classes([cfg["subject_singular"]])
    result = model(image_path, verbose=False, conf=0.08)[0]
    ranked = sorted(
        zip(result.boxes.conf, result.boxes.xyxy),
        key=lambda item: float(item[0]),
        reverse=True,
    )
    return [{"bbox": [int(value) for value in box]} for _, box in ranked[:expected_boxes]]


def _label_one(img_path: Path, target: str = "box") -> tuple[str, int, str | None]:
    """Label a single image. Returns (stem, num_boxes, error_or_none)."""
    cfg = _target_config(target)
    _, _, labels_dir, review_dir, meta_dir = _dirs(target)

    frame = cv2.imread(str(img_path))
    if frame is None:
        return (img_path.stem, 0, "cannot read")

    h, w = frame.shape[:2]

    meta_path = meta_dir / f"{img_path.stem}.json"
    expected = 8
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        expected = meta.get("expected_boxes", 8)

    try:
        dets = _call_local_yolo_for_boxes(str(img_path), expected, target=target)

        lines = []
        for d in dets:
            x1, y1, x2, y2 = d["bbox"]
            xc = (x1 + x2) / 2 / w
            yc = (y1 + y2) / 2 / h
            bw = (x2 - x1) / w
            bh = (y2 - y1) / h
            lines.append(f"0 {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}")
        (labels_dir / f"{img_path.stem}.txt").write_text("\n".join(lines))

        review = frame.copy()
        for d in dets:
            bx1, by1, bx2, by2 = map(int, d["bbox"])
            cv2.rectangle(review, (bx1, by1), (bx2, by2), (0, 255, 0), 2)
        cv2.putText(
            review,
            f"{len(dets)}/{expected} {cfg['class_names'][0]} [local YOLO]",
            (10, 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0),
            2,
        )
        cv2.imwrite(str(review_dir / f"{img_path.stem}.jpg"), review)
        return (img_path.stem, len(dets), None)

    except Exception as e:
        return (img_path.stem, 0, str(e))


def label(sample_every: int = 1, workers: int = 10, target: str = "box") -> None:
    """Batch-label captured frames using local YOLO.

    Args:
        sample_every: Label every Nth frame (1 = all, 5 = every 5th)
        workers: Number of concurrent API requests (default 10)
        target:  Which dataset (and subject vocab) to label.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    _, images_dir, labels_dir, review_dir, _ = _dirs(target)

    images = sorted(images_dir.glob("*.jpg"))
    if not images:
        print(f"No images found in {images_dir}. Run 'capture' first.")
        return

    existing_labels = {p.stem for p in labels_dir.glob("*.txt")}
    to_label = [img for img in images if img.stem not in existing_labels]

    if not to_label:
        print(f"All {len(images)} images already labeled. Nothing to do.")
        return

    to_label = to_label[::sample_every]

    for d in (labels_dir, review_dir):
        d.mkdir(parents=True, exist_ok=True)

    print(f"Labeling {len(to_label)} images for target={target!r} via local YOLO")

    labeled = 0
    errors = 0
    start = time.time()

    # Process in batches to control rate
    batch_size = 1
    for batch_start in range(0, len(to_label), batch_size):
        batch = to_label[batch_start : batch_start + batch_size]

        with ThreadPoolExecutor(max_workers=batch_size) as pool:
            futures = {pool.submit(_label_one, img, target): img for img in batch}
            for future in as_completed(futures):
                stem, n_boxes, err = future.result()
                if err:
                    errors += 1
                    if errors <= 5 or errors % 20 == 0:
                        print(f"  ERROR {stem}: {err}")
                else:
                    labeled += 1

        done = labeled + errors
        if done % 10 == 0 or done == len(to_label):
            elapsed = time.time() - start
            rate = labeled / elapsed if elapsed > 0 else 0
            print(f"  [{done}/{len(to_label)}] {labeled} ok, {errors} err ({rate:.1f}/s)")

        # Small pause between batches
        time.sleep(0.5)

    elapsed = time.time() - start
    print(f"\nDone! {labeled} labeled, {errors} errors ({elapsed:.0f}s)")
    print(f"  Labels → {LABELS_DIR}/")
    print(f"  Review → {REVIEW_DIR}/")


# ── Step 3: Review (optional, local rendering) ─────────────────────────


def _draw_numbered_candidates(frame, dets: list[dict]) -> np.ndarray:
    img = frame.copy()
    for i, d in enumerate(dets):
        x1, y1, x2, y2 = map(int, d["bbox"])
        color = (0, 255, 0)
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        label = f"#{i + 1}"
        cv2.putText(img, label, (x1 + 2, y1 + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3)
        cv2.putText(img, label, (x1 + 2, y1 + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    return img


def _parse_indices(text: str) -> list[int]:
    """Parse a one-based JSON array into zero-based candidate indexes."""
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        indices = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, TypeError):
        return []
    return [index - 1 for index in indices if isinstance(index, int) and index >= 1]


def validate(sample_every: int = 10, target: str = "box") -> None:
    """Render numbered local candidates for a bounded human review."""

    cfg = _target_config(target)
    _, images_dir, labels_dir, review_dir, meta_dir = _dirs(target)

    images = sorted(images_dir.glob("*.jpg"))
    metas = sorted(meta_dir.glob("*.json"))

    if not images:
        print(f"No images found in {images_dir}. Run 'capture' then 'label' first.")
        return

    meta_stems = {m.stem for m in metas}
    paired = [(img, meta_dir / f"{img.stem}.json") for img in images if img.stem in meta_stems]

    to_validate = paired[::sample_every]
    print(f"Rendering {len(to_validate)} of {len(paired)} frames for review")
    print()

    validated = 0
    for idx, (img_path, meta_path) in enumerate(to_validate):
        meta = json.loads(meta_path.read_text())
        expected = meta["expected_boxes"]
        all_candidates = meta.get("all_candidates", [])

        if not all_candidates:
            continue

        frame = cv2.imread(str(img_path))
        if frame is None:
            continue

        annotated = _draw_numbered_candidates(frame, all_candidates)
        cv2.putText(
            annotated,
            f"expected {expected} {cfg['class_names'][0]}",
            (10, 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0),
            2,
        )
        cv2.imwrite(str(review_dir / f"{img_path.stem}-review.jpg"), annotated)
        validated += 1
        print(f"  [{idx + 1}/{len(to_validate)}] {img_path.stem}")

    print(f"\nDone! {validated} review frames written; labels were not changed")


# ── Step 4: Export ──────────────────────────────────────────────────────


def export_dataset(val_split: float = 0.2, target: str = "box") -> None:
    """Create YOLO-format dataset with train/val split."""
    cfg = _target_config(target)
    dataset_dir, images_dir, labels_dir, _, _ = _dirs(target)

    images = sorted(images_dir.glob("*.jpg"))
    labels = sorted(labels_dir.glob("*.txt"))

    if not images:
        print(f"No images found in {images_dir}. Run 'capture' then 'label' first.")
        return

    label_stems = {lbl.stem for lbl in labels}
    paired = [(img, labels_dir / f"{img.stem}.txt") for img in images if img.stem in label_stems]

    if not paired:
        print("No matched image/label pairs. Run 'label' first.")
        return

    # Filter out empty label files (local YOLO found 0 detections — bad
    # training signal). Drop instead of poison the dataset.
    nonempty = []
    empty = 0
    for img_path, lbl_path in paired:
        if lbl_path.stat().st_size > 0:
            nonempty.append((img_path, lbl_path))
        else:
            empty += 1
    if empty:
        print(f"Skipped {empty} frames with empty label files")
    paired = nonempty

    print(f"Found {len(paired)} usable image/label pairs")

    random.shuffle(paired)
    split_idx = int(len(paired) * (1 - val_split))
    train_pairs = paired[:split_idx]
    val_pairs = paired[split_idx:]

    out_dir = dataset_dir / "yolo_dataset"
    for split in ("train", "val"):
        (out_dir / split / "images").mkdir(parents=True, exist_ok=True)
        (out_dir / split / "labels").mkdir(parents=True, exist_ok=True)

    for pairs, split in [(train_pairs, "train"), (val_pairs, "val")]:
        for img_path, lbl_path in pairs:
            shutil.copy2(img_path, out_dir / split / "images" / img_path.name)
            shutil.copy2(lbl_path, out_dir / split / "labels" / lbl_path.name)

    class_names = cfg["class_names"]
    data_yaml = out_dir / "data.yaml"
    data_yaml.write_text(
        f"path: {out_dir.resolve()}\n"
        f"train: train/images\n"
        f"val: val/images\n"
        f"\n"
        f"nc: {len(class_names)}\n"
        f"names: {class_names}\n"
    )

    print(f"\nDataset (target={target!r}) exported to {out_dir}/")
    print(f"  Train: {len(train_pairs)} images")
    print(f"  Val:   {len(val_pairs)} images")
    print(f"\nNext: uv run esc-finetune {data_yaml}")


# ── CLI ─────────────────────────────────────────────────────────────────


def _parse_arg(flag: str, default, cast=str):
    for i, arg in enumerate(sys.argv):
        if arg == flag and i + 1 < len(sys.argv):
            return cast(sys.argv[i + 1])
    return default


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage:")
        print("  esc-dataset capture config.yaml --target drone --boxes 1 --camera cam-outside")
        print("  esc-dataset label   --target drone   # batch Gemini labeling")
        print("  esc-dataset export  --target drone   # train/val split")
        print()
        print("Common options (all subcommands):")
        print("  --target NAME  Subject class set: 'box' (default) | 'drone'")
        print()
        print("Capture options:")
        print("  --boxes N      Expected subject count per frame (required, default 8)")
        print("  --camera ID    Camera id from config.yaml (default: cam-inside)")
        print("  --duration S   Seconds to capture (default: 30)")
        print("  --fps N        Frames/sec (default: 5)")
        print("  --countdown S  Countdown before starting (default: 0)")
        print()
        print("Label options:")
        print("  --sample-every N   Label every Nth frame (default 1)")
        print()
        print("Validate options:")
        print("  --sample-every N   Validate every Nth frame (default 10)")
        print()
        print("Export options:")
        print("  --val-split F   Fraction held out for validation (default 0.2)")
        sys.exit(1)

    cmd = sys.argv[1]
    target = _parse_arg("--target", "box", str)

    if cmd == "capture":
        config_path = sys.argv[2] if len(sys.argv) > 2 else "config.yaml"
        capture(
            config_path=config_path,
            expected_boxes=_parse_arg("--boxes", 8, int),
            camera_id=_parse_arg("--camera", "cam-inside", str),
            duration=_parse_arg("--duration", 30, int),
            fps=_parse_arg("--fps", 5.0, float),
            countdown=_parse_arg("--countdown", 0, int),
            target=target,
        )

    elif cmd == "label":
        label(sample_every=_parse_arg("--sample-every", 1, int), target=target)

    elif cmd == "validate":
        validate(sample_every=_parse_arg("--sample-every", 10, int), target=target)

    elif cmd == "export":
        export_dataset(val_split=_parse_arg("--val-split", 0.2, float), target=target)

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
