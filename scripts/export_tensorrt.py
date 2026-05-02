#!/usr/bin/env python3
"""One-shot TensorRT engine export.

Per HACKATHON_SCRIPT.md §6.5. Run THIS ON THE JETSON, inside the L4T
container, before building Dockerfile.sensor. TensorRT engines are NOT
portable across hardware or JetPack versions; building on stage is the
most common Jetson demo failure.

    python scripts/export_tensorrt.py
    python scripts/export_tensorrt.py --model yolo11n.pt --imgsz 416  # for older Nano

Output: yolo11s.engine in the current directory. Copy that next to
Dockerfile.sensor before `docker build`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Export YOLO weights to TensorRT engine")
    parser.add_argument(
        "--model", default="yolo11s.pt", help="source weights (.pt). Downloads on first run."
    )
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument(
        "--workspace", type=int, default=4, help="GB of TRT workspace; reduce on Nano (try 2)"
    )
    parser.add_argument(
        "--no-half", action="store_true", help="disable FP16 (keeps FP32; ~2x slower on Orin)"
    )
    args = parser.parse_args()

    try:
        from ultralytics import YOLO
    except ImportError:
        print("ERROR: ultralytics not installed. Run inside the L4T container.", file=sys.stderr)
        sys.exit(1)

    print(f"Loading {args.model}...", flush=True)
    model = YOLO(args.model)
    print(
        f"Exporting to TensorRT engine (imgsz={args.imgsz}, fp16={not args.no_half}, "
        f"workspace={args.workspace}GB)...",
        flush=True,
    )
    model.export(
        format="engine",
        half=not args.no_half,
        imgsz=args.imgsz,
        device=0,
        workspace=args.workspace,
        batch=1,
    )

    base = Path(args.model).stem
    out = Path(f"{base}.engine")
    if out.exists():
        size_mb = out.stat().st_size / (1024 * 1024)
        print(f"OK: wrote {out} ({size_mb:.1f} MB)", flush=True)
    else:
        print(f"WARN: expected {out} but file not found; check ultralytics output", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
