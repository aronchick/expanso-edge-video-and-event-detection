"""Record a scene: what the real YOLO weights measure, frame by frame.

The output is the input of `edge-sensor --replay`. It holds detections only
(labels, confidences, boxes), never pixels, so a scene can be committed and
replayed anywhere without weights, a GPU or a camera.

Two sources:

  --image PHOTO   pan a fixed-size viewing window across a still photograph,
                  the way a camera on a pole sees a scene change as people
                  walk into view. The window moves --step pixels per frame.
  --video CLIP    sample a video every --every seconds.

Run it from the project environment (it needs the vision extra):

  uv run --extra vision python scripts/record-scene.py \\
      --weights yolov8s.pt --image bus.jpg --window 810x600 --axis y \\
      --step 24 --frames 21 --out fixtures/scenes/north.jsonl

The sample photographs `bus.jpg` and `zidane.jpg` ship inside the ultralytics
package: `python -c "import ultralytics,os;print(os.path.dirname(ultralytics.__file__))"`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

WANTED = {"person", "backpack", "airplane", "drone"}
CONF_FLOOR = 0.10  # the live detector's predict-side floor; thresholds apply at replay


def detections(model, frame) -> list[dict]:
    result = model(frame, verbose=False, conf=CONF_FLOOR)[0]
    out = []

    for cls_idx, conf, box in zip(result.boxes.cls, result.boxes.conf, result.boxes.xyxy):
        label = model.names[int(cls_idx)]

        if label not in WANTED:
            continue

        out.append(
            {
                "label": label,
                "confidence": round(float(conf), 3),
                "bbox": [round(float(v), 1) for v in box],
            }
        )

    return sorted(out, key=lambda d: (-d["confidence"], d["label"]))


def image_frames(path: Path, window: tuple[int, int], axis: str, step: int, count: int):
    image = cv2.imread(str(path))

    if image is None:
        raise SystemExit(f"cannot read image {path}")

    height, width = image.shape[:2]
    win_w, win_h = min(window[0], width), min(window[1], height)

    for i in range(count):
        offset = i * step
        x = min(offset, width - win_w) if axis == "x" else 0
        y = min(offset, height - win_h) if axis == "y" else 0

        yield i, image[y : y + win_h, x : x + win_w]


def video_frames(path: Path, every: float, limit: float):
    capture = cv2.VideoCapture(str(path))

    if not capture.isOpened():
        raise SystemExit(f"cannot open video {path}")

    fps = capture.get(cv2.CAP_PROP_FPS) or 15.0
    index = 0
    next_at = 0.0

    while True:
        ok, frame = capture.read()

        if not ok:
            break

        seconds = index / fps
        index += 1

        if seconds > limit:
            break

        if seconds + 1e-9 >= next_at:
            next_at += every

            yield int(round(seconds / every)), frame


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--weights", required=True)
    parser.add_argument("--image")
    parser.add_argument("--video")
    parser.add_argument("--window", default="640x480", help="WxH of the viewing window")
    parser.add_argument("--axis", choices=("x", "y"), default="x")
    parser.add_argument("--step", type=int, default=32, help="pixels per frame (image)")
    parser.add_argument("--frames", type=int, default=20, help="frame count (image)")
    parser.add_argument("--every", type=float, default=0.5, help="seconds between frames")
    parser.add_argument("--limit", type=float, default=30.0, help="max seconds (video)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    if bool(args.image) == bool(args.video):
        parser.error("give exactly one of --image or --video")

    from ultralytics import YOLO

    model = YOLO(args.weights)
    window = tuple(int(v) for v in args.window.lower().split("x"))

    if args.image:
        source = Path(args.image)
        frames = image_frames(source, window, args.axis, args.step, args.frames)
    else:
        source = Path(args.video)
        frames = video_frames(source, args.every, args.limit)

    lines = []

    for index, frame in frames:
        lines.append(
            {
                "offset_s": round(index * args.every, 3),
                "frame": {"index": index, "width": frame.shape[1], "height": frame.shape[0]},
                "detections": detections(model, frame),
            }
        )

    header = {
        "scene": {
            "model": Path(args.weights).stem,
            "source": source.name,
            "frames": len(lines),
            "recorded_with": "scripts/record-scene.py",
        }
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "\n".join(json.dumps(r, separators=(",", ":")) for r in [header, *lines]) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out} ({len(lines)} frames)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
