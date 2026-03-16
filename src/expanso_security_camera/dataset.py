"""Dataset builder for fine-tuning YOLO on your actual camera images.

Three-step workflow:
    1. CAPTURE: Raw frames only — fast, no YOLO (on Jetson)
    2. LABEL:   Batch YOLO labeling + optional Claude validation (offline)
    3. EXPORT + FINETUNE

    # Capture (fast — just grabs frames, no inference)
    uv run esc-dataset capture config.yaml --boxes 1 --camera cam-inside --countdown 10 &
    uv run esc-dataset capture config.yaml --boxes 2 --camera cam-outside --countdown 10 &
    wait

    # Label all frames in batch (runs YOLO once on everything)
    uv run esc-dataset label

    # Optional: validate with Claude CLI
    uv run esc-dataset validate

    # Export + fine-tune
    uv run esc-dataset export
    uv run esc-finetune dataset/yolo_dataset/data.yaml
"""

from __future__ import annotations

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
META_DIR = DATASET_DIR / "meta"

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


# ── Step 1: Capture (raw frames only, no YOLO) ─────────────────────────


def capture(
    config_path: str,
    expected_boxes: int,
    camera_id: str = "cam-inside",
    duration: int = 30,
    fps: float = 5.0,
    countdown: int = 0,
) -> None:
    """Capture raw frames from camera. No YOLO — just fast frame grabs.

    Saves frames as JPEGs and a manifest with expected box count per frame.
    Labeling happens in a separate offline batch step.
    """
    from expanso_security_camera.config import DemoConfig

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

    for d in (IMAGES_DIR, META_DIR):
        d.mkdir(parents=True, exist_ok=True)

    interval = 1.0 / fps
    total_frames = int(duration * fps)
    frame_idx = len(list(IMAGES_DIR.glob("*.jpg")))

    print(f"Capturing {camera_id}: {expected_boxes} boxes expected")
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
        cv2.imwrite(str(IMAGES_DIR / f"{fname}.jpg"), frame)

        # Save expected box count for this frame (used by label step)
        meta = {"expected_boxes": expected_boxes, "camera_id": camera_id}
        (META_DIR / f"{fname}.json").write_text(json.dumps(meta))

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
    total_dataset = len(list(IMAGES_DIR.glob("*.jpg")))

    print(f"\nDone! {kept} frames captured ({total_time:.0f}s)")
    print(f"  Total dataset: {total_dataset} images")


# ── Step 2: Label (batch Claude vision on all captured frames) ──────────


def _call_gemini_for_boxes(image_path: str, expected_boxes: int) -> list[dict]:
    """Ask Gemini Flash to identify bounding boxes around cardboard boxes.

    Returns list of {"bbox": [x1, y1, x2, y2]} dicts in pixel coords.
    Uses the REST API directly — no SDK needed.
    """
    import base64
    import urllib.request

    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set")

    # Read and encode image
    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")

    # Get image dimensions for the response
    frame = cv2.imread(image_path)
    h, w = frame.shape[:2]

    prompt = (
        f"This security camera image ({w}x{h} pixels) contains exactly "
        f"{expected_boxes} cardboard box(es). For each box, return its "
        f"bounding box as pixel coordinates. Return ONLY a JSON array of "
        f"objects with x1, y1, x2, y2 integer keys (pixel values). "
        f'Example: [{{"x1":10,"y1":20,"x2":100,"y2":200}}]. No explanation.'
    )

    payload = json.dumps(
        {
            "contents": [
                {
                    "parts": [
                        {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}},
                        {"text": prompt},
                    ]
                }
            ],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1024},
        }
    ).encode("utf-8")

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={api_key}"
    )
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})

    # Retry with backoff on rate limit
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 4:
                wait = 2 ** attempt  # 1, 2, 4, 8s
                time.sleep(wait)
                continue
            raise

    text = data["candidates"][0]["content"]["parts"][0]["text"]

    # Parse JSON array of bbox objects
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        boxes = json.loads(text[start : end + 1])
        return [
            {"bbox": [int(b["x1"]), int(b["y1"]), int(b["x2"]), int(b["y2"])]}
            for b in boxes
            if all(k in b for k in ("x1", "y1", "x2", "y2"))
        ]
    except (json.JSONDecodeError, TypeError, KeyError):
        return []


def label(sample_every: int = 1) -> None:
    """Batch-label captured frames using Claude vision.

    Sends every Nth frame to Claude CLI, asks it to identify bounding
    boxes around the expected number of cardboard boxes. Skips already-
    labeled frames. Uses OAuth login — no API key needed.

    Args:
        sample_every: Label every Nth frame (1 = all, 5 = every 5th)
    """
    images = sorted(IMAGES_DIR.glob("*.jpg"))
    if not images:
        print("No images found. Run 'capture' first.")
        return

    existing_labels = {p.stem for p in LABELS_DIR.glob("*.txt")}
    to_label = [img for img in images if img.stem not in existing_labels]

    if not to_label:
        print(f"All {len(images)} images already labeled. Nothing to do.")
        return

    # Sample if requested
    to_label = to_label[::sample_every]

    for d in (LABELS_DIR, REVIEW_DIR):
        d.mkdir(parents=True, exist_ok=True)

    print(f"Labeling {len(to_label)} images via Gemini Flash")

    labeled = 0
    errors = 0
    for idx, img_path in enumerate(to_label):
        frame = cv2.imread(str(img_path))
        if frame is None:
            continue

        h, w = frame.shape[:2]

        meta_path = META_DIR / f"{img_path.stem}.json"
        expected = 8
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            expected = meta.get("expected_boxes", 8)

        try:
            dets = _call_gemini_for_boxes(str(img_path), expected)

            # Write YOLO-format labels
            lines = []
            for d in dets:
                x1, y1, x2, y2 = d["bbox"]
                xc = (x1 + x2) / 2 / w
                yc = (y1 + y2) / 2 / h
                bw = (x2 - x1) / w
                bh = (y2 - y1) / h
                lines.append(f"0 {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}")
            (LABELS_DIR / f"{img_path.stem}.txt").write_text("\n".join(lines))

            # Review image
            review = frame.copy()
            for d in dets:
                bx1, by1, bx2, by2 = map(int, d["bbox"])
                cv2.rectangle(review, (bx1, by1), (bx2, by2), (0, 255, 0), 2)
            cv2.putText(
                review,
                f"{len(dets)}/{expected} boxes [Gemini]",
                (10, 25),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 0),
                2,
            )
            cv2.imwrite(str(REVIEW_DIR / f"{img_path.stem}.jpg"), review)

            labeled += 1
            if (idx + 1) % 5 == 0 or idx == 0:
                print(f"  [{idx + 1}/{len(to_label)}] {img_path.stem}: {len(dets)} boxes")

        except Exception as e:
            errors += 1
            print(f"  [{idx + 1}/{len(to_label)}] {img_path.stem}: ERROR {e}")

        # Gemini free tier: 10 RPM, need 6s between requests
        time.sleep(6.0)

    print(f"\nDone! {labeled} labeled, {errors} errors")
    print(f"  Labels → {LABELS_DIR}/")
    print(f"  Review → {REVIEW_DIR}/")


# ── Step 3: Validate (optional, Claude CLI) ─────────────────────────────


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


def _call_claude_cli(image_path: str, prompt: str) -> list[int]:
    """Call Claude via CLI — uses OAuth login, no API key needed."""
    import shutil
    import subprocess

    claude_bin = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")
    result = subprocess.run(
        [claude_bin, "-p", prompt, image_path, "--output-format", "text"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {result.stderr.strip()}")
    return _parse_indices(result.stdout)


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


def validate(sample_every: int = 10) -> None:
    """Offline validation: send sampled frames to Claude CLI.

    For every Nth image, draws all YOLO candidates on the frame,
    sends to `claude -p`, asks which are real boxes.
    Rewrites the label file with the validated detections.
    """
    import tempfile

    images = sorted(IMAGES_DIR.glob("*.jpg"))
    metas = sorted(META_DIR.glob("*.json"))

    if not images:
        print("No images found. Run 'capture' then 'label' first.")
        return

    meta_stems = {m.stem for m in metas}
    paired = [(img, META_DIR / f"{img.stem}.json") for img in images if img.stem in meta_stems]

    to_validate = paired[::sample_every]
    print(f"Validating {len(to_validate)} of {len(paired)} frames via claude CLI")
    print()

    validated = 0
    errors = 0

    for idx, (img_path, meta_path) in enumerate(to_validate):
        meta = json.loads(meta_path.read_text())
        expected = meta["expected_boxes"]
        all_candidates = meta.get("all_candidates", [])

        if not all_candidates:
            continue

        frame = cv2.imread(str(img_path))
        if frame is None:
            continue

        h, w = frame.shape[:2]

        annotated = _draw_numbered_candidates(frame, all_candidates)

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            tmp_path = f.name
            cv2.imwrite(tmp_path, annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])

        n = len(all_candidates)
        prompt = (
            f"This security camera image has exactly {expected} cardboard boxes "
            f"(some may be partially occluded). I've drawn {n} numbered candidate "
            f"bounding boxes (#1-#{n}). Which candidates are correctly on real "
            f"cardboard boxes? Return ONLY a JSON array of candidate numbers. "
            f"Example: [1, 3, 5]. No explanation."
        )

        try:
            valid_indices = _call_claude_cli(tmp_path, prompt)
            dets = [all_candidates[j] for j in valid_indices if j < len(all_candidates)]

            lines = []
            for d in dets:
                x1, y1, x2, y2 = d["bbox"]
                xc = (x1 + x2) / 2 / w
                yc = (y1 + y2) / 2 / h
                bw = (x2 - x1) / w
                bh = (y2 - y1) / h
                lines.append(f"0 {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}")
            (LABELS_DIR / f"{img_path.stem}.txt").write_text("\n".join(lines))

            review = frame.copy()
            for d in dets:
                bx1, by1, bx2, by2 = map(int, d["bbox"])
                cv2.rectangle(review, (bx1, by1), (bx2, by2), (0, 255, 0), 2)
            cv2.putText(
                review,
                f"{len(dets)}/{expected} boxes [Gemini]",
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
        finally:
            os.unlink(tmp_path)

    print(f"\nDone! {validated} validated, {errors} errors")


# ── Step 4: Export ──────────────────────────────────────────────────────


def export_dataset(val_split: float = 0.2) -> None:
    """Create YOLO-format dataset with train/val split."""
    images = sorted(IMAGES_DIR.glob("*.jpg"))
    labels = sorted(LABELS_DIR.glob("*.txt"))

    if not images:
        print("No images found. Run 'capture' then 'label' first.")
        return

    label_stems = {lbl.stem for lbl in labels}
    paired = [(img, LABELS_DIR / f"{img.stem}.txt") for img in images if img.stem in label_stems]

    if not paired:
        print("No matched image/label pairs. Run 'label' first.")
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
        print("  esc-dataset capture config.yaml --boxes 4 --camera cam-inside")
        print("  esc-dataset label              # batch Claude vision labeling")
        print("  esc-dataset label --sample-every 5   # every 5th frame")
        print("  esc-dataset export              # train/val split")
        print()
        print("Capture options:")
        print("  --boxes N      Expected box count (required)")
        print("  --camera ID    Camera (default: cam-inside)")
        print("  --duration S   Seconds (default: 30)")
        print("  --fps N        Frames/sec (default: 5)")
        print("  --countdown S  Countdown before starting (default: 0)")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "capture":
        config_path = sys.argv[2] if len(sys.argv) > 2 else "config.yaml"
        capture(
            config_path=config_path,
            expected_boxes=_parse_arg("--boxes", 8, int),
            camera_id=_parse_arg("--camera", "cam-inside", str),
            duration=_parse_arg("--duration", 30, int),
            fps=_parse_arg("--fps", 5.0, float),
            countdown=_parse_arg("--countdown", 0, int),
        )

    elif cmd == "models":
        import urllib.request

        api_key = os.environ.get("GOOGLE_API_KEY", "")
        if not api_key:
            print("Set GOOGLE_API_KEY first")
            sys.exit(1)
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for m in data.get("models", []):
            name = m["name"].replace("models/", "")
            methods = ", ".join(m.get("supportedGenerationMethods", []))
            if "generateContent" in methods:
                print(f"  {name}")

    elif cmd == "label":
        label(sample_every=_parse_arg("--sample-every", 1, int))

    elif cmd == "validate":
        validate(sample_every=_parse_arg("--sample-every", 10, int))

    elif cmd == "export":
        export_dataset(val_split=_parse_arg("--val-split", 0.2, float))

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
