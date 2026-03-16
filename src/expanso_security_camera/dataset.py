"""Dataset builder for fine-tuning YOLO on your actual camera images.

Uses YOLO-World for candidate bounding boxes + Claude/OpenAI vision to
validate which candidates are real boxes. YOLO is great at spatial coords,
vision LLMs are great at understanding what's actually a box.

Workflow:
    uv run esc-dataset collect config.yaml --boxes 8
    # remove a box, repeat for 7, 6, ... 1
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


def _encode_frame_jpeg(frame, quality: int = 70) -> bytes:
    """Encode frame as JPEG bytes."""
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes()


def _draw_numbered_candidates(frame, dets: list[dict]) -> np.ndarray:
    """Draw numbered candidate boxes on frame for LLM validation."""
    img = frame.copy()
    for i, d in enumerate(dets):
        x1, y1, x2, y2 = map(int, d["bbox"])
        color = (0, 255, 0)
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        label = f"#{i + 1}"
        cv2.putText(img, label, (x1 + 2, y1 + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3)
        cv2.putText(img, label, (x1 + 2, y1 + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    return img


def _validate_with_llm(
    frame,
    dets: list[dict],
    expected_boxes: int,
    provider: str = "anthropic",
) -> list[int]:
    """Send annotated frame to vision LLM, get back which candidates are real boxes.

    Returns list of 0-based indices of valid detections.
    """
    annotated = _draw_numbered_candidates(frame, dets)
    jpg_bytes = _encode_frame_jpeg(annotated, quality=80)
    b64 = base64.b64encode(jpg_bytes).decode("utf-8")

    n = len(dets)
    prompt = (
        f"This security camera image shows a scene with exactly {expected_boxes} "
        f"cardboard boxes (some may be partially occluded). "
        f"I've drawn {n} numbered candidate bounding boxes (#1 through #{n}). "
        f"Which candidates are correctly placed on real cardboard boxes? "
        f"Return ONLY a JSON array of the candidate numbers that are real boxes. "
        f"Example: [1, 3, 5, 7]. No explanation, just the JSON array."
    )

    if provider == "anthropic":
        return _call_anthropic(b64, prompt)
    elif provider == "openai":
        return _call_openai(b64, prompt)
    else:
        raise ValueError(f"Unknown provider: {provider}")


def _call_anthropic(b64_image: str, prompt: str) -> list[int]:
    """Call Claude vision API."""
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
    """Call OpenAI vision API."""
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
    """Parse JSON array of 1-based indices from LLM response, return 0-based."""
    text = text.strip()
    # Find JSON array in response
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        indices = json.loads(text[start : end + 1])
        # Convert 1-based to 0-based
        return [i - 1 for i in indices if isinstance(i, int) and i >= 1]
    except (json.JSONDecodeError, TypeError):
        return []


def collect(
    config_path: str,
    expected_boxes: int,
    camera_id: str = "cam-inside",
    duration: int = 120,
    fps: float = 5.0,
    conf: float = 0.03,
    llm_every: int = 10,
    provider: str = "anthropic",
) -> None:
    """Capture frames, label with YOLO + LLM validation.

    Every frame: YOLO-World at low conf → candidate boxes → top N kept.
    Every Nth frame: send to Claude/OpenAI to validate which candidates
    are real boxes. Validated frames get precise labels; in-between frames
    use pure YOLO top-N labels.

    Args:
        config_path: Path to config.yaml
        expected_boxes: Known number of boxes in the scene
        camera_id: Which camera to capture from
        duration: Capture duration in seconds
        fps: Frames per second to capture
        conf: YOLO confidence threshold (very low to catch everything)
        llm_every: Send every Nth frame to vision LLM for validation
        provider: "anthropic" or "openai"
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

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    LABELS_DIR.mkdir(parents=True, exist_ok=True)
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)

    model = YOLO("yolov8s-worldv2.pt")
    model.set_classes(BOX_CLASSES)

    interval = 1.0 / fps
    total_frames = int(duration * fps)

    print(f"Collecting {camera_id}: {expected_boxes} boxes, {provider} validation")
    print(f"  {total_frames} frames over {duration}s ({fps} fps)")
    print(f"  LLM validates every {llm_every}th frame")
    print(f"  YOLO conf={conf}")
    print()

    cap = cv2.VideoCapture(cam_url)
    if not cap.isOpened():
        print(f"Cannot connect to {camera_id}")
        sys.exit(1)
    for _ in range(5):
        cap.read()

    kept = 0
    validated = 0
    discarded = 0
    existing = len(list(IMAGES_DIR.glob("*.jpg")))
    frame_idx = existing

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

        # YOLO candidates at very low confidence
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

        # LLM validation on sampled frames
        use_llm = (i % llm_every == 0) and len(raw_dets) >= expected_boxes
        if use_llm:
            try:
                valid_indices = _validate_with_llm(frame, raw_dets, expected_boxes, provider)
                dets = [raw_dets[j] for j in valid_indices if j < len(raw_dets)]
                validated += 1
                tag = "LLM"
            except Exception as e:
                # Fallback to top-N if API fails
                dets = raw_dets[:expected_boxes]
                tag = f"YOLO(llm-err: {e})"
        else:
            # Non-validated: take top N by confidence
            dets = raw_dets[:expected_boxes]
            tag = "YOLO"

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

        # Review image
        review = frame.copy()
        for d in dets:
            bx1, by1, bx2, by2 = map(int, d["bbox"])
            color = (0, 255, 0) if tag == "LLM" else (255, 165, 0)
            cv2.rectangle(review, (bx1, by1), (bx2, by2), color, 2)
        cv2.putText(
            review,
            f"{len(dets)} boxes [{tag}]",
            (10, 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 255),
            2,
        )
        cv2.imwrite(str(REVIEW_DIR / f"{fname}.jpg"), review)

        kept += 1
        frame_idx += 1

        if (i + 1) % 25 == 0:
            elapsed_total = int(time.time() - start)
            print(
                f"  [{elapsed_total}s] {i + 1}/{total_frames} "
                f"kept={kept} validated={validated} discarded={discarded}"
            )

        elapsed = time.time() - frame_start
        if elapsed < interval:
            time.sleep(interval - elapsed)

    cap.release()
    total_time = time.time() - start

    total_dataset = len(list(IMAGES_DIR.glob("*.jpg")))
    print(f"\nDone! {kept} frames kept, {validated} LLM-validated, {discarded} discarded")
    print(f"  Total dataset so far: {total_dataset} images ({total_time:.0f}s)")
    print(f"  Review → {REVIEW_DIR}/  (green=LLM validated, orange=YOLO only)")


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
        print("  uv run esc-dataset collect config.yaml --boxes 8 [options]")
        print("  uv run esc-dataset export")
        print()
        print("Options:")
        print("  --boxes N        Expected box count (required)")
        print("  --camera ID      Camera ID (default: cam-inside)")
        print("  --duration S     Capture seconds (default: 120)")
        print("  --fps N          Frames per second (default: 5)")
        print("  --llm-every N    LLM-validate every Nth frame (default: 10)")
        print("  --provider X     anthropic or openai (default: anthropic)")
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
            llm_every=_parse_arg("--llm-every", 10, int),
            provider=_parse_arg("--provider", "anthropic", str),
        )

    elif cmd == "export":
        export_dataset(val_split=_parse_arg("--val-split", 0.2, float))

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
