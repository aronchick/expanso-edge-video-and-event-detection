"""Quick camera test script.

Connects to both cameras, grabs frames, runs YOLO detection,
and saves annotated images. Use this to verify the hardware
works before running the full pipeline.

Usage:
    uv run esc-test-cameras
    uv run esc-test-cameras config.yaml
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2


def log(msg: str) -> None:
    print(msg, flush=True)


def test_camera(name: str, url: str, output_dir: Path) -> bool:
    """Test a single camera: connect, grab frames, optionally run YOLO."""
    log(f"\n{'=' * 50}")
    log(f"  Testing: {name}")
    log(f"  URL: {url}")
    log(f"{'=' * 50}")

    # Connect
    log("Connecting...")
    cap = cv2.VideoCapture(url)
    if not cap.isOpened():
        log(f"FAILED: Cannot open {url}")
        return False

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    log(f"Connected: {w}x{h} @ {fps:.0f}fps")

    # Grab 10 frames, save first and last
    log("Grabbing frames...")
    frames_ok = 0
    first_frame = None
    last_frame = None

    for i in range(10):
        ret, frame = cap.read()
        if ret:
            frames_ok += 1
            if first_frame is None:
                first_frame = frame
            last_frame = frame
        else:
            log(f"  Frame {i}: FAILED")

    cap.release()
    log(f"Frames captured: {frames_ok}/10")

    if first_frame is None:
        log("FAILED: No frames captured")
        return False

    # Save frame
    output_dir.mkdir(parents=True, exist_ok=True)
    frame_path = output_dir / f"{name}.jpg"
    cv2.imwrite(str(frame_path), last_frame)
    log(f"Frame saved: {frame_path}")

    # Try YOLO detection
    try:
        from ultralytics import YOLO

        log("Running YOLO detection...")
        model = YOLO("yolov8s.pt")
        results = model(last_frame, verbose=False, conf=0.3)

        detections = []
        if results and results[0].boxes is not None:
            for box in results[0].boxes:
                cls_id = int(box.cls[0])
                conf = float(box.conf[0])
                cls_name = model.names[cls_id]
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                detections.append(f"  {cls_name}: {conf:.2f} [{x1},{y1},{x2},{y2}]")

                # Draw on frame
                color = (0, 255, 0)
                cv2.rectangle(last_frame, (x1, y1), (x2, y2), color, 2)
                label = f"{cls_name} {conf:.2f}"
                cv2.putText(
                    last_frame,
                    label,
                    (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    color,
                    2,
                )

        if detections:
            log(f"Detections ({len(detections)}):")
            for d in detections:
                log(d)
        else:
            log("No detections (scene may be empty)")

        # Save annotated frame
        annotated_path = output_dir / f"{name}_annotated.jpg"
        cv2.imwrite(str(annotated_path), last_frame)
        log(f"Annotated frame saved: {annotated_path}")

    except ImportError:
        log("YOLO not available, skipping detection test")
    except Exception as e:
        log(f"YOLO error (non-fatal): {e}")

    log("Result: PASS")
    return True


def main() -> None:
    """Entry point for esc-test-cameras."""
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    output_dir = Path("/tmp/camera-test")

    log("=" * 50)
    log("  Security Camera Test")
    log("=" * 50)

    # Load config
    if Path(config_path).exists():
        from expanso_security_camera.config import DemoConfig

        config = DemoConfig.from_yaml(config_path)
        cameras = [(c.camera_id, c.url) for c in config.cameras]
    else:
        log(f"No config at {config_path}, using defaults")
        cameras = [
            (
                "cam-outside",
                "rtsp://admin:securePass@192.168.1.172:554/h264Preview_01_sub",
            ),
            (
                "cam-inside",
                "rtsp://admin:securePass@192.168.1.15:554/h264Preview_01_sub",
            ),
        ]

    results = {}
    for name, url in cameras:
        results[name] = test_camera(name, url, output_dir)

    # Summary
    log(f"\n{'=' * 50}")
    log("  SUMMARY")
    log(f"{'=' * 50}")
    for name, ok in results.items():
        status = "PASS" if ok else "FAIL"
        log(f"  {name}: {status}")

    all_ok = all(results.values())
    log(f"\n  Overall: {'ALL PASS' if all_ok else 'SOME FAILED'}")
    log(f"  Test frames: {output_dir}/")
    log(f"{'=' * 50}")

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
