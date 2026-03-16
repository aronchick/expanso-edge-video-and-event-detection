"""Diagnostic tool: capture frames and analyze what YOLO sees vs what it misses.

Grabs snapshots from each camera, runs detection with multiple strategies
(standard YOLO, YOLO-World, different confidence thresholds, SAHI tiling),
and saves annotated images so you can see exactly what's being found.

Run with:
    uv run esc-diagnose config.yaml

Output goes to ./diagnostics/ with annotated images and a JSON report.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np


def grab_frame(url: str, retries: int = 3) -> np.ndarray | None:
    """Grab a single fresh frame from an RTSP stream."""
    for attempt in range(retries):
        cap = cv2.VideoCapture(url)
        if not cap.isOpened():
            time.sleep(1)
            continue
        # Flush stale buffer frames
        for _ in range(5):
            cap.read()
        ret, frame = cap.read()
        cap.release()
        if ret and frame is not None:
            return frame
        time.sleep(1)
    return None


def run_yoloworld_detection(
    frame: np.ndarray,
    classes: list[str],
    conf: float = 0.05,
    imgsz: int = 640,
    iou: float = 0.3,
) -> list[dict]:
    """Run YOLO-World open-vocabulary detection."""
    from ultralytics import YOLO

    os.environ["YOLO_VERBOSE"] = "false"
    model = YOLO("yolov8s-worldv2.pt")
    model.set_classes(classes)
    results = model(frame, verbose=False, conf=conf, imgsz=imgsz, iou=iou)
    return _extract_detections(results, model.names)


def run_standard_yolo(
    frame: np.ndarray,
    conf: float = 0.05,
    imgsz: int = 640,
) -> list[dict]:
    """Run standard YOLOv8 with all COCO classes."""
    from ultralytics import YOLO

    os.environ["YOLO_VERBOSE"] = "false"
    model = YOLO("yolov8s.pt")
    results = model(frame, verbose=False, conf=conf, imgsz=imgsz)
    return _extract_detections(results, model.names)


def run_sahi_detection(
    frame: np.ndarray,
    classes: list[str],
    conf: float = 0.05,
    slice_size: int = 320,
    overlap_ratio: float = 0.25,
) -> list[dict]:
    """Run tiled (SAHI-style) detection for small objects.

    Splits the image into overlapping tiles, runs detection on each,
    then merges results with NMS.
    """
    from ultralytics import YOLO

    os.environ["YOLO_VERBOSE"] = "false"
    model = YOLO("yolov8s-worldv2.pt")
    model.set_classes(classes)

    h, w = frame.shape[:2]
    stride = int(slice_size * (1 - overlap_ratio))
    all_dets: list[dict] = []

    # Run on full image first
    full_dets = _extract_detections(model(frame, verbose=False, conf=conf, imgsz=640), model.names)
    all_dets.extend(full_dets)

    # Run on tiles
    for y0 in range(0, h - slice_size // 2, stride):
        for x0 in range(0, w - slice_size // 2, stride):
            y1 = min(y0 + slice_size, h)
            x1 = min(x0 + slice_size, w)
            tile = frame[y0:y1, x0:x1]

            if tile.shape[0] < 32 or tile.shape[1] < 32:
                continue

            tile_dets = _extract_detections(
                model(tile, verbose=False, conf=conf, imgsz=slice_size), model.names
            )
            # Offset bboxes back to full-frame coordinates
            for d in tile_dets:
                d["bbox"] = [
                    d["bbox"][0] + x0,
                    d["bbox"][1] + y0,
                    d["bbox"][2] + x0,
                    d["bbox"][3] + y0,
                ]
                d["source"] = f"tile_{x0}_{y0}"
            all_dets.extend(tile_dets)

    # NMS to merge overlapping detections from different tiles
    return _nms_merge(all_dets, iou_threshold=0.4)


def run_multiscale_detection(
    frame: np.ndarray,
    classes: list[str],
    conf: float = 0.05,
    scales: tuple[int, ...] = (480, 640, 960, 1280),
) -> list[dict]:
    """Run detection at multiple image sizes and merge results."""
    from ultralytics import YOLO

    os.environ["YOLO_VERBOSE"] = "false"
    model = YOLO("yolov8s-worldv2.pt")
    model.set_classes(classes)

    all_dets: list[dict] = []
    for imgsz in scales:
        results = model(frame, verbose=False, conf=conf, imgsz=imgsz)
        dets = _extract_detections(results, model.names)
        for d in dets:
            d["source"] = f"scale_{imgsz}"
        all_dets.extend(dets)

    return _nms_merge(all_dets, iou_threshold=0.4)


def _extract_detections(results, names: dict) -> list[dict]:
    """Extract detection dicts from YOLO results."""
    dets = []
    if results and results[0].boxes is not None:
        for box in results[0].boxes:
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])
            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
            dets.append(
                {
                    "class": names.get(cls_id, f"class_{cls_id}"),
                    "confidence": round(conf, 3),
                    "bbox": [x1, y1, x2, y2],
                    "source": "full",
                }
            )
    return dets


def _nms_merge(dets: list[dict], iou_threshold: float = 0.4) -> list[dict]:
    """Non-maximum suppression to merge overlapping detections."""
    if not dets:
        return []

    boxes = np.array([d["bbox"] for d in dets], dtype=np.float32)
    scores = np.array([d["confidence"] for d in dets], dtype=np.float32)

    # Use OpenCV NMS
    indices = cv2.dnn.NMSBoxes(
        bboxes=[(int(b[0]), int(b[1]), int(b[2] - b[0]), int(b[3] - b[1])) for b in boxes],
        scores=scores.tolist(),
        score_threshold=0.01,
        nms_threshold=iou_threshold,
    )

    if len(indices) == 0:
        return []

    return [dets[i] for i in indices.flatten()]


def draw_detections(
    frame: np.ndarray, dets: list[dict], title: str, color: tuple = (0, 255, 0)
) -> np.ndarray:
    """Draw bounding boxes + labels on a frame copy."""
    img = frame.copy()
    for i, d in enumerate(dets):
        x1, y1, x2, y2 = d["bbox"]
        label = f"{d['class']} {d['confidence']:.2f}"
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(img, (x1, y1 - th - 6), (x1 + tw, y1), color, -1)
        cv2.putText(img, label, (x1, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

    # Title bar
    cv2.putText(
        img,
        f"{title} ({len(dets)} detections)",
        (10, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (255, 255, 255),
        3,
    )
    cv2.putText(
        img, f"{title} ({len(dets)} detections)", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2
    )
    return img


BOX_CLASSES_NARROW = ["cardboard box", "shipping box", "package", "carton"]
BOX_CLASSES_WIDE = [
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


def diagnose_camera(cam_id: str, url: str, out_dir: Path) -> dict:
    """Run full diagnostic on a single camera."""
    print(f"\n{'=' * 60}")
    print(f"Diagnosing {cam_id}: {url}")
    print(f"{'=' * 60}")

    frame = grab_frame(url)
    if frame is None:
        print(f"  FAILED: Cannot connect to {cam_id}")
        return {"camera_id": cam_id, "status": "offline"}

    h, w = frame.shape[:2]
    print(f"  Frame: {w}x{h}")

    cam_dir = out_dir / cam_id
    cam_dir.mkdir(parents=True, exist_ok=True)

    # Save raw frame
    cv2.imwrite(str(cam_dir / "raw.jpg"), frame)

    report = {
        "camera_id": cam_id,
        "frame_size": f"{w}x{h}",
        "status": "online",
        "strategies": {},
    }

    # Strategy 1: Standard YOLO (baseline)
    print("  [1/5] Standard YOLOv8s (all COCO classes, conf=0.05)...")
    dets = run_standard_yolo(frame, conf=0.05)
    report["strategies"]["standard_yolo_all"] = {"detections": len(dets), "details": dets}
    img = draw_detections(frame, dets, "Standard YOLO (all)", (0, 255, 0))
    cv2.imwrite(str(cam_dir / "01_standard_yolo_all.jpg"), img)
    print(f"    → {len(dets)} objects detected")

    # Strategy 2: YOLO-World narrow classes
    print("  [2/5] YOLO-World (narrow: cardboard box, shipping box, package, carton)...")
    dets = run_yoloworld_detection(frame, BOX_CLASSES_NARROW, conf=0.05)
    report["strategies"]["world_narrow"] = {"detections": len(dets), "details": dets}
    img = draw_detections(frame, dets, "World-Narrow", (0, 200, 255))
    cv2.imwrite(str(cam_dir / "02_world_narrow.jpg"), img)
    print(f"    → {len(dets)} boxes detected")

    # Strategy 3: YOLO-World wide classes
    print("  [3/5] YOLO-World (wide: 14 box synonyms)...")
    dets = run_yoloworld_detection(frame, BOX_CLASSES_WIDE, conf=0.05)
    report["strategies"]["world_wide"] = {"detections": len(dets), "details": dets}
    img = draw_detections(frame, dets, "World-Wide", (255, 100, 0))
    cv2.imwrite(str(cam_dir / "03_world_wide.jpg"), img)
    print(f"    → {len(dets)} boxes detected")

    # Strategy 4: Multi-scale detection
    print("  [4/5] Multi-scale YOLO-World (480, 640, 960, 1280)...")
    dets = run_multiscale_detection(frame, BOX_CLASSES_WIDE, conf=0.05)
    report["strategies"]["multiscale"] = {"detections": len(dets), "details": dets}
    img = draw_detections(frame, dets, "Multi-Scale", (200, 0, 255))
    cv2.imwrite(str(cam_dir / "04_multiscale.jpg"), img)
    print(f"    → {len(dets)} boxes detected")

    # Strategy 5: SAHI tiled detection
    print("  [5/5] SAHI tiled detection (320px tiles, 25% overlap)...")
    dets = run_sahi_detection(frame, BOX_CLASSES_WIDE, conf=0.05, slice_size=320)
    report["strategies"]["sahi_tiled"] = {"detections": len(dets), "details": dets}
    img = draw_detections(frame, dets, "SAHI-Tiled", (0, 255, 128))
    cv2.imwrite(str(cam_dir / "05_sahi_tiled.jpg"), img)
    print(f"    → {len(dets)} boxes detected")

    # Summary
    print(f"\n  Summary for {cam_id}:")
    for name, data in report["strategies"].items():
        print(f"    {name:25s} → {data['detections']} detections")

    return report


def main() -> None:
    """Entry point for esc-diagnose command."""
    from expanso_security_camera.config import DemoConfig

    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    config = DemoConfig.from_yaml(config_path)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path("diagnostics") / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Diagnostic output → {out_dir}/")
    print(f"Cameras: {len(config.cameras)}")

    all_reports = []
    for cam in config.cameras:
        report = diagnose_camera(cam.camera_id, cam.url, out_dir)
        all_reports.append(report)

    # Write JSON report
    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(all_reports, indent=2))
    print(f"\nFull report → {report_path}")
    print("\nOpen the diagnostics folder and compare the annotated images.")
    print("The strategy with the most correct detections is your best bet.")


if __name__ == "__main__":
    main()
