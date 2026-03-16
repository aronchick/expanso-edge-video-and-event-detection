"""Dataset builder for fine-tuning YOLO on your actual camera images.

Three-step workflow:
1. CAPTURE:  Grab frames from cameras at intervals
2. LABEL:    Auto-label with YOLO-World, save in YOLO format for correction
3. EXPORT:   Generate YOLO-format dataset ready for fine-tuning

Run with:
    uv run esc-dataset capture config.yaml          # grab 50 frames per camera
    uv run esc-dataset capture config.yaml --count 200  # grab 200 frames
    uv run esc-dataset label                        # auto-label with YOLO-World
    uv run esc-dataset export                       # create train/val split

After step 2, manually review labels in dataset/labels/ and fix any that are
wrong. Then run step 3 to create the final dataset.
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
REVIEW_DIR = DATASET_DIR / "review"  # Annotated images for visual review

# Single class for fine-tuning: 0 = box
CLASS_NAMES = ["box"]


def capture_frames(config_path: str, count: int = 50, interval: float = 2.0) -> None:
    """Capture frames from cameras at regular intervals."""
    from expanso_security_camera.config import DemoConfig

    config = DemoConfig.from_yaml(config_path)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Capturing {count} frames per camera (interval={interval}s)")
    print(f"Output → {IMAGES_DIR}/")

    for cam in config.cameras:
        print(f"\nCamera: {cam.camera_id} ({cam.url})")
        cap = cv2.VideoCapture(cam.url)
        if not cap.isOpened():
            print("  FAILED: Cannot connect")
            continue

        # Flush buffer
        for _ in range(5):
            cap.read()

        captured = 0
        for i in range(count):
            ret, frame = cap.read()
            if not ret:
                # Try reconnecting
                cap.release()
                time.sleep(1)
                cap = cv2.VideoCapture(cam.url)
                if not cap.isOpened():
                    print(f"  Lost connection at frame {i}")
                    break
                continue

            fname = f"{cam.camera_id}_{i:04d}.jpg"
            cv2.imwrite(str(IMAGES_DIR / fname), frame)
            captured += 1

            if (i + 1) % 10 == 0:
                print(f"  {i + 1}/{count} frames captured")

            time.sleep(interval)

        cap.release()
        print(f"  Captured {captured} frames for {cam.camera_id}")

    total = len(list(IMAGES_DIR.glob("*.jpg")))
    print(f"\nTotal frames captured: {total}")
    print("Next step: uv run esc-dataset label")


def auto_label(conf: float = 0.08, imgsz: int = 640) -> None:
    """Auto-label captured images using YOLO-World.

    Generates YOLO-format .txt files alongside each image.
    Also creates annotated review images for visual verification.
    """
    from ultralytics import YOLO

    os.environ["YOLO_VERBOSE"] = "false"

    images = sorted(IMAGES_DIR.glob("*.jpg"))
    if not images:
        print(f"No images found in {IMAGES_DIR}/. Run 'capture' first.")
        return

    print(f"Auto-labeling {len(images)} images with YOLO-World...")
    print(f"  Confidence threshold: {conf}")
    print(f"  Image size: {imgsz}")

    LABELS_DIR.mkdir(parents=True, exist_ok=True)
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)

    model = YOLO("yolov8s-worldv2.pt")
    model.set_classes(
        [
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
    )

    stats = {"total_images": 0, "total_boxes": 0, "images_with_boxes": 0}

    for img_path in images:
        frame = cv2.imread(str(img_path))
        if frame is None:
            continue

        h, w = frame.shape[:2]
        stats["total_images"] += 1

        # Run detection at multiple scales and merge
        all_dets = []
        for sz in (imgsz, imgsz + 320):
            results = model(frame, verbose=False, conf=conf, imgsz=sz, iou=0.3)
            if results and results[0].boxes is not None:
                for box in results[0].boxes:
                    x1, y1, x2, y2 = map(float, box.xyxy[0].tolist())
                    c = float(box.conf[0])
                    all_dets.append({"bbox": [x1, y1, x2, y2], "conf": c})

        # NMS merge
        dets = _nms_merge_simple(all_dets, iou_threshold=0.4)

        # Write YOLO-format label (class x_center y_center width height — normalized)
        label_path = LABELS_DIR / img_path.with_suffix(".txt").name
        lines = []
        for d in dets:
            x1, y1, x2, y2 = d["bbox"]
            xc = (x1 + x2) / 2 / w
            yc = (y1 + y2) / 2 / h
            bw = (x2 - x1) / w
            bh = (y2 - y1) / h
            lines.append(f"0 {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}")

        label_path.write_text("\n".join(lines))

        if dets:
            stats["images_with_boxes"] += 1
            stats["total_boxes"] += len(dets)

        # Draw review image
        review_img = frame.copy()
        for i, d in enumerate(dets):
            x1, y1, x2, y2 = map(int, d["bbox"])
            cv2.rectangle(review_img, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(
                review_img,
                f"box {d['conf']:.2f}",
                (x1, y1 - 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                1,
            )
        cv2.putText(
            review_img,
            f"{len(dets)} boxes",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 255, 255),
            2,
        )
        cv2.imwrite(str(REVIEW_DIR / img_path.name), review_img)

    print("\nLabeling complete:")
    print(f"  Images: {stats['total_images']}")
    print(f"  Images with boxes: {stats['images_with_boxes']}")
    print(f"  Total boxes labeled: {stats['total_boxes']}")
    print(f"  Labels → {LABELS_DIR}/")
    print(f"  Review images → {REVIEW_DIR}/")
    print(f"\n*** IMPORTANT: Review the images in {REVIEW_DIR}/ ***")
    print(f"*** Fix any wrong labels in {LABELS_DIR}/ before exporting ***")
    print("*** Each .txt file has one line per box: class x_center y_center w h ***")
    print("\nNext step: uv run esc-dataset export")


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
        print("No images found. Run 'capture' and 'label' first.")
        return

    # Match images to labels
    label_stems = {lbl.stem for lbl in labels}
    paired = [(img, LABELS_DIR / f"{img.stem}.txt") for img in images if img.stem in label_stems]

    if not paired:
        print("No matched image/label pairs found.")
        return

    print(f"Found {len(paired)} image/label pairs")

    # Shuffle and split
    random.shuffle(paired)
    split_idx = int(len(paired) * (1 - val_split))
    train_pairs = paired[:split_idx]
    val_pairs = paired[split_idx:]

    # Create output structure
    out_dir = DATASET_DIR / "yolo_dataset"
    for split in ("train", "val"):
        (out_dir / split / "images").mkdir(parents=True, exist_ok=True)
        (out_dir / split / "labels").mkdir(parents=True, exist_ok=True)

    for pairs, split in [(train_pairs, "train"), (val_pairs, "val")]:
        for img_path, lbl_path in pairs:
            shutil.copy2(img_path, out_dir / split / "images" / img_path.name)
            shutil.copy2(lbl_path, out_dir / split / "labels" / lbl_path.name)

    # Write data.yaml for YOLO training
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


def main() -> None:
    """Entry point for esc-dataset command."""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  uv run esc-dataset capture config.yaml [--count 50]")
        print("  uv run esc-dataset label [--conf 0.08]")
        print("  uv run esc-dataset export [--val-split 0.2]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "capture":
        config_path = sys.argv[2] if len(sys.argv) > 2 else "config.yaml"
        count = 50
        for i, arg in enumerate(sys.argv):
            if arg == "--count" and i + 1 < len(sys.argv):
                count = int(sys.argv[i + 1])
        capture_frames(config_path, count=count)

    elif cmd == "label":
        conf = 0.08
        for i, arg in enumerate(sys.argv):
            if arg == "--conf" and i + 1 < len(sys.argv):
                conf = float(sys.argv[i + 1])
        auto_label(conf=conf)

    elif cmd == "export":
        val_split = 0.2
        for i, arg in enumerate(sys.argv):
            if arg == "--val-split" and i + 1 < len(sys.argv):
                val_split = float(sys.argv[i + 1])
        export_dataset(val_split=val_split)

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
