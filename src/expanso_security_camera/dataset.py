"""Dataset builder for fine-tuning YOLO on your actual camera images.

Three-step workflow:
    1. COLLECT: Capture frames + YOLO candidate labels (fast, on Jetson)
    2. VALIDATE: Send sampled frames to Claude/OpenAI to fix labels (offline)
    3. EXPORT + FINETUNE

    uv run esc-dataset collect config.yaml --boxes 8
    uv run esc-dataset collect config.yaml --boxes 7
    # ... down to 1 ...
    uv run esc-dataset validate            # offline, calls Claude/OpenAI
    uv run esc-dataset export
    uv run esc-finetune dataset/yolo_dataset/data.yaml
"""

from __future__ import annotations

import base64
import json
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
META_DIR = DATASET_DIR / "meta"  # Per-image metadata (expected count, etc.)

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


# ── Step 1: Collect ─────────────────────────────────────────────────────


def collect(
    config_path: str,
    expected_boxes: int,
    camera_id: str = "cam-inside",
    duration: int = 120,
    fps: float = 5.0,
    conf: float = 0.03,
) -> None:
    """Capture frames and auto-label with YOLO top-N candidates.

    Fast — runs entirely on Jetson, no API calls. Labels use top N
    detections by confidence. Validation pass fixes them later.
    """
    from ultralytics import YOLO

    from expanso_security_camera.config import DemoConfig

    os.environ["YOLO_VERBOSE"] = "false"
    config = DemoConfig.from_yaml(config_path)

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

    for d in (IMAGES_DIR, LABELS_DIR, REVIEW_DIR, META_DIR):
        d.mkdir(parents=True, exist_ok=True)

    model = YOLO("yolov8s-worldv2.pt")
    model.set_classes(BOX_CLASSES)

    interval = 1.0 / fps
    total_frames = int(duration * fps)
    existing = len(list(IMAGES_DIR.glob("*.jpg")))
    frame_idx = existing

    print(f"Collecting {camera_id}: {expected_boxes} boxes")
    print(f"  {total_frames} frames over {duration}s ({fps} fps)")
    print(f"  YOLO conf={conf}, taking top {expected_boxes} per frame")
    print()

    cap = cv2.VideoCapture(cam_url)
    if not cap.isOpened():
        print(f"Cannot connect to {camera_id}")
        sys.exit(1)
    for _ in range(5):
        cap.read()

    kept = 0
    discarded = 0
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

        h, w = frame.shape[:2]
        frame_area = h * w

        results = model(frame, verbose=False, conf=conf, imgsz=640, iou=0.5)
        raw_dets = []
        if results and results[0].boxes is not None:
            for box in results[0].boxes:
                x1, y1, x2, y2 = map(float, box.xyxy[0].tolist())
                c = float(box.conf[0])
                bbox_area = (x2 - x1) * (y2 - y1)
                if bbox_area > frame_area * 0.4:
                    continue
                if bbox_area < frame_area * 0.005:
                    continue
                raw_dets.append({"bbox": [x1, y1, x2, y2], "conf": c})

        if len(raw_dets) == 0:
            discarded += 1
            elapsed = time.time() - frame_start
            if elapsed < interval:
                time.sleep(interval - elapsed)
            continue

        raw_dets.sort(key=lambda d: d["conf"], reverse=True)
        dets = raw_dets[:expected_boxes]

        fname = f"{camera_id}_{frame_idx:05d}"

        # Save image
        cv2.imwrite(str(IMAGES_DIR / f"{fname}.jpg"), frame)

        # Save YOLO labels
        lines = []
        for d in dets:
            x1, y1, x2, y2 = d["bbox"]
            xc = (x1 + x2) / 2 / w
            yc = (y1 + y2) / 2 / h
            bw = (x2 - x1) / w
            bh = (y2 - y1) / h
            lines.append(f"0 {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}")
        (LABELS_DIR / f"{fname}.txt").write_text("\n".join(lines))

        # Save metadata (for validation pass)
        meta = {
            "expected_boxes": expected_boxes,
            "yolo_candidates": len(raw_dets),
            "yolo_used": len(dets),
            "all_candidates": [{"bbox": d["bbox"], "conf": d["conf"]} for d in raw_dets],
        }
        (META_DIR / f"{fname}.json").write_text(json.dumps(meta))

        # Review image
        review = frame.copy()
        for d in dets:
            bx1, by1, bx2, by2 = map(int, d["bbox"])
            cv2.rectangle(review, (bx1, by1), (bx2, by2), (255, 165, 0), 2)
        cv2.putText(
            review,
            f"{len(dets)}/{expected_boxes} boxes [YOLO]",
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
            elapsed_total = int(time.time() - start)
            print(f"  [{elapsed_total}s] {i + 1}/{total_frames} kept={kept}")

        elapsed = time.time() - frame_start
        if elapsed < interval:
            time.sleep(interval - elapsed)

    cap.release()
    total_time = time.time() - start
    total_dataset = len(list(IMAGES_DIR.glob("*.jpg")))

    print(f"\nDone! {kept} frames kept, {discarded} discarded ({total_time:.0f}s)")
    print(f"  Total dataset: {total_dataset} images")
    print(f"  Review → {REVIEW_DIR}/  (orange = YOLO labels, not yet validated)")


# ── Step 2: Validate ────────────────────────────────────────────────────


def _encode_frame_jpeg(frame, quality: int = 70) -> bytes:
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes()


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


def _call_anthropic(b64_image: str, prompt: str) -> list[int]:
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=256,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": b64_image,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    return _parse_indices(response.content[0].text)


def _call_openai(b64_image: str, prompt: str) -> list[int]:
    import openai

    client = openai.OpenAI()
    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=256,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    return _parse_indices(response.choices[0].message.content)


def _parse_indices(text: str) -> list[int]:
    text = text.strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        indices = json.loads(text[start : end + 1])
        return [i - 1 for i in indices if isinstance(i, int) and i >= 1]
    except (json.JSONDecodeError, TypeError):
        return []


def validate(
    sample_every: int = 10,
    provider: str = "anthropic",
) -> None:
    """Offline validation: send sampled frames to Claude/OpenAI.

    For every Nth image, sends the frame with ALL YOLO candidates
    (not just top-N) to the vision LLM and asks which are real boxes.
    Rewrites the label file with the validated detections.
    Non-sampled frames keep their YOLO top-N labels.
    """
    images = sorted(IMAGES_DIR.glob("*.jpg"))
    metas = sorted(META_DIR.glob("*.json"))

    if not images:
        print("No images found. Run 'collect' first.")
        return

    meta_stems = {m.stem for m in metas}
    paired = [(img, META_DIR / f"{img.stem}.json") for img in images if img.stem in meta_stems]

    to_validate = paired[::sample_every]
    print(f"Validating {len(to_validate)} of {len(paired)} frames with {provider}")
    print()

    validated = 0
    errors = 0

    for idx, (img_path, meta_path) in enumerate(to_validate):
        meta = json.loads(meta_path.read_text())
        expected = meta["expected_boxes"]
        all_candidates = meta["all_candidates"]

        if not all_candidates:
            continue

        frame = cv2.imread(str(img_path))
        if frame is None:
            continue

        h, w = frame.shape[:2]

        # Draw all candidates numbered for LLM
        annotated = _draw_numbered_candidates(frame, all_candidates)
        jpg_bytes = _encode_frame_jpeg(annotated, quality=80)
        b64 = base64.b64encode(jpg_bytes).decode("utf-8")

        n = len(all_candidates)
        prompt = (
            f"This security camera image has exactly {expected} cardboard boxes "
            f"(some may be partially occluded). I've drawn {n} numbered candidate "
            f"bounding boxes (#1-#{n}). Which candidates are correctly on real "
            f"cardboard boxes? Return ONLY a JSON array of candidate numbers. "
            f"Example: [1, 3, 5]. No explanation."
        )

        try:
            if provider == "anthropic":
                valid_indices = _call_anthropic(b64, prompt)
            else:
                valid_indices = _call_openai(b64, prompt)

            dets = [all_candidates[j] for j in valid_indices if j < len(all_candidates)]

            # Rewrite label file
            lines = []
            for d in dets:
                x1, y1, x2, y2 = d["bbox"]
                xc = (x1 + x2) / 2 / w
                yc = (y1 + y2) / 2 / h
                bw = (x2 - x1) / w
                bh = (y2 - y1) / h
                lines.append(f"0 {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}")
            (LABELS_DIR / f"{img_path.stem}.txt").write_text("\n".join(lines))

            # Update review image
            review = frame.copy()
            for d in dets:
                bx1, by1, bx2, by2 = map(int, d["bbox"])
                cv2.rectangle(review, (bx1, by1), (bx2, by2), (0, 255, 0), 2)
            cv2.putText(
                review,
                f"{len(dets)}/{expected} boxes [LLM]",
                (10, 25),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 0),
                2,
            )
            cv2.imwrite(str(REVIEW_DIR / f"{img_path.stem}.jpg"), review)

            validated += 1
            print(
                f"  [{idx + 1}/{len(to_validate)}] {img_path.stem}: "
                f"{len(dets)}/{expected} boxes validated"
            )

        except Exception as e:
            errors += 1
            print(f"  [{idx + 1}/{len(to_validate)}] {img_path.stem}: ERROR {e}")

    print(f"\nDone! {validated} validated, {errors} errors")
    print(f"  Green boxes in {REVIEW_DIR}/ = LLM-validated")
    print("  Orange boxes = YOLO-only (not sampled)")


# ── Step 3: Export ──────────────────────────────────────────────────────


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
        print("  esc-dataset collect config.yaml --boxes 8   # capture + YOLO label")
        print("  esc-dataset validate                        # offline LLM validation")
        print("  esc-dataset export                          # train/val split")
        print()
        print("Collect options:")
        print("  --boxes N      Expected box count (required)")
        print("  --camera ID    Camera (default: cam-inside)")
        print("  --duration S   Seconds (default: 120)")
        print("  --fps N        Frames/sec (default: 5)")
        print()
        print("Validate options:")
        print("  --sample-every N   Validate every Nth frame (default: 10)")
        print("  --provider X       anthropic or openai (default: anthropic)")
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

    elif cmd == "validate":
        validate(
            sample_every=_parse_arg("--sample-every", 10, int),
            provider=_parse_arg("--provider", "anthropic", str),
        )

    elif cmd == "export":
        export_dataset(val_split=_parse_arg("--val-split", 0.2, float))

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
