"""Dataset builder for fine-tuning YOLO on your actual camera images.

Primary workflow — known-count capture:
    # 8 boxes in frame, capture 2 min at 5fps on cam-inside
    uv run esc-dataset collect config.yaml --boxes 8 --camera cam-inside
    # Remove a box, repeat
    uv run esc-dataset collect config.yaml --boxes 7 --camera cam-inside
    # ... down to 1, then:
    uv run esc-dataset export
    uv run esc-finetune dataset/yolo_dataset/data.yaml
"""

from __future__ import annotations

import os
import random
import shutil
import sys
import time
from pathlib import Path

import cv2
import numpy as np

DATASET_DIR = Path("dataset")
IMAGES_DIR = DATASET_DIR / "images"
LABELS_DIR = DATASET_DIR / "labels"
REVIEW_DIR = DATASET_DIR / "review"

CLASS_NAMES = ["box"]

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


def collect(
    config_path: str,
    expected_boxes: int,
    camera_id: str = "cam-inside",
    duration: int = 120,
    fps: float = 5.0,
    conf: float = 0.03,
) -> None:
    """Capture frames and auto-label with known box count.

    There are always exactly `expected_boxes` boxes in the frame. YOLO-World
    runs at very low confidence to find all candidates. We take the top N
    detections by confidence as labels. Frames where YOLO finds fewer than
    N are kept too — we just use what it found (the model will learn from
    the partial labels). Frames where detections are clearly garbage
    (zero detections, or huge bbox covering >50% of frame) are discarded.

    Args:
        config_path: Path to config.yaml
        expected_boxes: Known number of boxes in the scene
        camera_id: Which camera to capture from
        duration: Capture duration in seconds
        fps: Frames per second to capture
        conf: YOLO confidence threshold (very low to catch everything)
    """
    from ultralytics import YOLO

    from expanso_security_camera.config import DemoConfig

    os.environ["YOLO_VERBOSE"] = "false"
    config = DemoConfig.from_yaml(config_path)

    # Find the camera URL
    cam_url = None
    for cam in config.cameras:
        if cam.camera_id == camera_id:
            cam_url = cam.url
            break
    if cam_url is None:
        print(f"Camera '{camera_id}' not found in config. Available:")
        for cam in config.cameras:
            print(f"  {cam.camera_id}")
        sys.exit(1)

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    LABELS_DIR.mkdir(parents=True, exist_ok=True)
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)

    model = YOLO("yolov8s-worldv2.pt")
    model.set_classes(BOX_CLASSES)

    interval = 1.0 / fps
    total_frames = int(duration * fps)

    print(f"Collecting {camera_id}: {expected_boxes} boxes")
    print(f"  {total_frames} frames over {duration}s ({fps} fps)")
    print(f"  YOLO conf={conf} (low — we pick top {expected_boxes} by confidence)")
    print()

    # Connect to camera with persistent connection
    cap = cv2.VideoCapture(cam_url)
    if not cap.isOpened():
        print(f"Cannot connect to {camera_id}")
        sys.exit(1)

    # Flush stale buffer
    for _ in range(5):
        cap.read()

    kept = 0
    discarded = 0
    existing = len(list(IMAGES_DIR.glob("*.jpg")))
    frame_idx = existing  # Continue numbering from previous runs

    start = time.time()
    for i in range(total_frames):
        frame_start = time.time()

        ret, frame = cap.read()
        if not ret:
            # Reconnect
            cap.release()
            time.sleep(0.5)
            cap = cv2.VideoCapture(cam_url)
            if not cap.isOpened():
                print(f"  Lost connection at frame {i}")
                break
            continue

        h, w = frame.shape[:2]
        frame_area = h * w

        # Run YOLO at very low confidence to get all candidates
        results = model(frame, verbose=False, conf=conf, imgsz=640, iou=0.5)
        raw_dets = []
        if results and results[0].boxes is not None:
            for box in results[0].boxes:
                x1, y1, x2, y2 = map(float, box.xyxy[0].tolist())
                c = float(box.conf[0])
                bbox_area = (x2 - x1) * (y2 - y1)
                # Skip garbage: bbox covering >40% of frame
                if bbox_area > frame_area * 0.4:
                    continue
                # Skip tiny noise: bbox < 0.5% of frame
                if bbox_area < frame_area * 0.005:
                    continue
                raw_dets.append({"bbox": [x1, y1, x2, y2], "conf": c})

        # Discard frame if zero detections
        if len(raw_dets) == 0:
            discarded += 1
            if i % 50 == 0:
                print(f"  [{i}/{total_frames}] 0 detections — skip")
            elapsed = time.time() - frame_start
            if elapsed < interval:
                time.sleep(interval - elapsed)
            continue

        # Take top N by confidence (N = expected_boxes)
        raw_dets.sort(key=lambda d: d["conf"], reverse=True)
        dets = raw_dets[:expected_boxes]

        # Save image
        fname = f"{camera_id}_{frame_idx:05d}"
        cv2.imwrite(str(IMAGES_DIR / f"{fname}.jpg"), frame)

        # Write YOLO-format labels
        lines = []
        for d in dets:
            x1, y1, x2, y2 = d["bbox"]
            xc = (x1 + x2) / 2 / w
            yc = (y1 + y2) / 2 / h
            bw = (x2 - x1) / w
            bh = (y2 - y1) / h
            lines.append(f"0 {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}")
        (LABELS_DIR / f"{fname}.txt").write_text("\n".join(lines))

        # Save review image
        review = frame.copy()
        for d in dets:
            bx1, by1, bx2, by2 = map(int, d["bbox"])
            cv2.rectangle(review, (bx1, by1), (bx2, by2), (0, 255, 0), 2)
            cv2.putText(
                review,
                f"{d['conf']:.2f}",
                (bx1, by1 - 4),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                (0, 255, 0),
                1,
            )
        cv2.putText(
            review,
            f"{len(dets)}/{expected_boxes} boxes (frame {frame_idx})",
            (10, 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 255),
            2,
        )
        cv2.imwrite(str(REVIEW_DIR / f"{fname}.jpg"), review)

        kept += 1
        frame_idx += 1

        if (i + 1) % 50 == 0:
            found = len(raw_dets)
            used = len(dets)
            print(f"  [{i + 1}/{total_frames}] found={found} used={used} kept={kept}")

        elapsed = time.time() - frame_start
        if elapsed < interval:
            time.sleep(interval - elapsed)

    cap.release()
    total_time = time.time() - start

    print(f"\nDone! {kept} frames kept, {discarded} discarded ({total_time:.0f}s)")
    print(f"  Total dataset: {len(list(IMAGES_DIR.glob('*.jpg')))} images")
    print(f"  Images → {IMAGES_DIR}/")
    print(f"  Labels → {LABELS_DIR}/")
    print(f"  Review → {REVIEW_DIR}/")


def _nms_merge_simple(dets: list[dict], iou_threshold: float = 0.4) -> list[dict]:
    """Simple NMS merge."""
    if not dets:
        return []

    boxes = np.array([d["bbox"] for d in dets], dtype=np.float32)
    scores = np.array([d["conf"] for d in dets], dtype=np.float32)

    indices = cv2.dnn.NMSBoxes(
        bboxes=[(int(b[0]), int(b[1]), int(b[2] - b[0]), int(b[3] - b[1])) for b in boxes],
        scores=scores.tolist(),
        score_threshold=0.01,
        nms_threshold=iou_threshold,
    )

    if len(indices) == 0:
        return []

    return [dets[i] for i in indices.flatten()]


def export_dataset(val_split: float = 0.2) -> None:
    """Create YOLO-format dataset with train/val split."""
    images = sorted(IMAGES_DIR.glob("*.jpg"))
    labels = sorted(LABELS_DIR.glob("*.txt"))

    if not images:
        print("No images found. Run 'collect' first.")
        return

    label_stems = {lbl.stem for lbl in labels}
    paired = [(img, LABELS_DIR / f"{img.stem}.txt") for img in images if img.stem in label_stems]

    if not paired:
        print("No matched image/label pairs found.")
        return

    print(f"Found {len(paired)} image/label pairs")

    random.shuffle(paired)
    split_idx = int(len(paired) * (1 - val_split))
    train_pairs = paired[:split_idx]
    val_pairs = paired[split_idx:]

    out_dir = DATASET_DIR / "yolo_dataset"
    for split in ("train", "val"):
        (out_dir / split / "images").mkdir(parents=True, exist_ok=True)
        (out_dir / split / "labels").mkdir(parents=True, exist_ok=True)

    for pairs, split in [(train_pairs, "train"), (val_pairs, "val")]:
        for img_path, lbl_path in pairs:
            shutil.copy2(img_path, out_dir / split / "images" / img_path.name)
            shutil.copy2(lbl_path, out_dir / split / "labels" / lbl_path.name)

    data_yaml = out_dir / "data.yaml"
    data_yaml.write_text(
        f"path: {out_dir.resolve()}\n"
        f"train: train/images\n"
        f"val: val/images\n"
        f"\n"
        f"nc: {len(CLASS_NAMES)}\n"
        f"names: {CLASS_NAMES}\n"
    )

    print(f"\nDataset exported to {out_dir}/")
    print(f"  Train: {len(train_pairs)} images")
    print(f"  Val:   {len(val_pairs)} images")
    print(f"  data.yaml: {data_yaml}")
    print(f"\nNext step: uv run esc-finetune {data_yaml}")


def _parse_arg(flag: str, default, cast=str):
    """Parse a --flag value from sys.argv."""
    for i, arg in enumerate(sys.argv):
        if arg == flag and i + 1 < len(sys.argv):
            return cast(sys.argv[i + 1])
    return default


def main() -> None:
    """Entry point for esc-dataset command."""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  uv run esc-dataset collect config.yaml --boxes 8 [--camera cam-inside]")
        print("  uv run esc-dataset export [--val-split 0.2]")
        print()
        print("Collect workflow (repeat for 8, 7, 6, ... 1 boxes):")
        print("  1. Set up N boxes in front of camera")
        print("  2. uv run esc-dataset collect config.yaml --boxes N")
        print("  3. Remove a box, repeat")
        print("  4. uv run esc-dataset export")
        print("  5. uv run esc-finetune dataset/yolo_dataset/data.yaml")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "collect":
        config_path = sys.argv[2] if len(sys.argv) > 2 else "config.yaml"
        collect(
            config_path=config_path,
            expected_boxes=_parse_arg("--boxes", 8, int),
            camera_id=_parse_arg("--camera", "cam-inside", str),
            duration=_parse_arg("--duration", 120, int),
            fps=_parse_arg("--fps", 5.0, float),
            conf=_parse_arg("--conf", 0.03, float),
        )

    elif cmd == "export":
        export_dataset(val_split=_parse_arg("--val-split", 0.2, float))

    else:
        print(f"Unknown command: {cmd}")
        print("Available: collect, export")
        sys.exit(1)


if __name__ == "__main__":
    main()
