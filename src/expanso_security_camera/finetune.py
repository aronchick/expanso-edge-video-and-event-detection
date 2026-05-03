"""Fine-tune YOLOv8 on your camera-specific box images.

Takes the dataset created by esc-dataset and fine-tunes a YOLOv8s model
to detect boxes in your specific environment. The resulting model will
be far more accurate than generic YOLO or YOLO-World for your cameras.

Run with:
    uv run esc-finetune dataset/yolo_dataset/data.yaml
    uv run esc-finetune dataset/yolo_dataset/data.yaml --epochs 100
    uv run esc-finetune dataset/yolo_dataset/data.yaml --base yolov8s.pt

Output: runs/detect/box-finetune/weights/best.pt
Copy that to your config as model_name and you're done.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def finetune(
    data_yaml: str,
    base_model: str = "yolov8s.pt",
    epochs: int = 50,
    imgsz: int = 640,
    batch: int = 8,
    patience: int = 15,
    freeze: int = 10,
    device: str = "0",
    name: str = "box-finetune",
    cls_loss: float = 0.5,
    output_name: str | None = None,
) -> str:
    """Fine-tune YOLOv8 on box detection dataset.

    Uses transfer learning: freezes the backbone for stability,
    trains the detection head on your specific box images.

    Args:
        data_yaml: Path to data.yaml from esc-dataset export
        base_model: Base model to fine-tune from (yolov8n.pt for speed)
        epochs: Number of training epochs
        imgsz: Training image size
        batch: Batch size (reduce to 4 for Jetson, 16+ for big GPU)
        patience: Early stopping patience
        freeze: Number of backbone layers to freeze (10 = freeze backbone)
        device: CUDA device ("0" for first GPU, "cpu" for CPU)
        name: Experiment name

    Returns:
        Path to best.pt model weights
    """
    from ultralytics import YOLO

    os.environ["YOLO_VERBOSE"] = "true"

    data_path = Path(data_yaml)
    if not data_path.exists():
        print(f"ERROR: {data_yaml} not found. Run 'esc-dataset export' first.")
        sys.exit(1)

    print("Fine-tuning Configuration:")
    print(f"  Base model:    {base_model}")
    print(f"  Dataset:       {data_yaml}")
    print(f"  Epochs:        {epochs}")
    print(f"  Image size:    {imgsz}")
    print(f"  Batch size:    {batch}")
    print(f"  Freeze layers: {freeze}")
    print(f"  Device:        {device}")
    print(f"  Patience:      {patience}")
    print()

    model = YOLO(base_model)

    model.train(
        data=str(data_path.resolve()),
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        patience=patience,
        freeze=freeze,
        device=device,
        name=name,
        # Augmentation tuned for security camera footage
        hsv_h=0.01,  # Minimal hue variation (consistent lighting)
        hsv_s=0.3,  # Some saturation variation (day/night)
        hsv_v=0.4,  # Value/brightness variation (shadows, lighting)
        degrees=5.0,  # Slight rotation (camera vibration)
        translate=0.1,  # Small translation
        scale=0.3,  # Scale variation (boxes at different distances)
        shear=2.0,  # Minimal shear
        perspective=0.0005,  # Slight perspective (camera angle)
        flipud=0.0,  # No vertical flip (gravity is real)
        fliplr=0.5,  # Horizontal flip OK
        mosaic=0.8,  # Mosaic augmentation
        mixup=0.1,  # Light mixup
        # Training hyperparameters
        lr0=0.001,  # Lower initial LR for fine-tuning
        lrf=0.01,  # Final LR = lr0 * lrf
        warmup_epochs=3,
        warmup_bias_lr=0.01,
        cos_lr=True,  # Cosine annealing
        # Loss weights
        box=7.5,  # Box loss weight (higher = more precise boxes)
        cls=cls_loss,  # Classification loss. Default 0.5 was tuned for the
        # 1-class box-counting case where the head only needs to localize.
        # For multi-class fine-tunes (e.g. person/backpack/drone), 1.0 is
        # a more reasonable starting point so the head learns to
        # discriminate between classes.
        dfl=1.5,  # Distribution focal loss
        # Other
        plots=True,
        save=True,
        exist_ok=True,
        verbose=True,
    )

    # Find best weights
    best_pt = Path(f"runs/detect/{name}/weights/best.pt")
    if best_pt.exists():
        # Also copy to project root for easy use. The legacy default
        # `box-detector-finetuned.pt` is preserved for the original box
        # workflow; pass --output-name to override (e.g. drone-3class.pt).
        out_name = output_name or "box-detector-finetuned.pt"
        if not out_name.endswith(".pt"):
            out_name = f"{out_name}.pt"
        output_path = Path(out_name)
        shutil.copy2(best_pt, output_path)
        print(f"\n{'=' * 60}")
        print("Fine-tuning complete!")
        print(f"  Best model: {best_pt}")
        print(f"  Copied to:  {output_path}")
        print("\nTo use in your pipeline:")
        print(f"  python scripts/export_tensorrt.py --model {output_path}")
        print(f"  Then point your YAML --yolo-model at {output_path.stem}.engine")
        print(f"{'=' * 60}")
        return str(output_path)
    else:
        print(f"\nWARNING: best.pt not found at {best_pt}")
        print("Check runs/detect/ for training output")
        return ""


def validate_model(model_path: str, data_yaml: str) -> None:
    """Run validation on the fine-tuned model."""
    from ultralytics import YOLO

    print(f"Validating {model_path} on {data_yaml}...")
    model = YOLO(model_path)
    metrics = model.val(data=data_yaml)

    print("\nValidation Results:")
    print(f"  mAP50:    {metrics.box.map50:.3f}")
    print(f"  mAP50-95: {metrics.box.map:.3f}")
    print(f"  Precision: {metrics.box.mp:.3f}")
    print(f"  Recall:    {metrics.box.mr:.3f}")


def main() -> None:
    """Entry point for esc-finetune command."""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  uv run esc-finetune <data.yaml> [options]")
        print()
        print("Options:")
        print("  --epochs N       Training epochs (default: 50)")
        print("  --batch N        Batch size (default: 8)")
        print("  --base MODEL     Base model (default: yolov8n.pt)")
        print("  --freeze N       Layers to freeze (default: 10)")
        print("  --device DEV     CUDA device (default: 0)")
        print("  --name NAME      runs/detect/<name>/ output dir (default: box-finetune)")
        print("  --cls F          Classification loss weight (default: 0.5)")
        print("  --output-name N  Copy best.pt as <N>.pt at project root")
        print("                   (default: box-detector-finetuned.pt)")
        print("  --validate       Run validation only (needs trained model)")
        sys.exit(1)

    data_yaml = sys.argv[1]

    # Parse args
    epochs = 50
    batch = 8
    base_model = "yolov8n.pt"
    freeze = 10
    device = "0"
    validate_only = False
    name = "box-finetune"
    cls_loss = 0.5
    output_name: str | None = None

    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--epochs" and i + 1 < len(sys.argv):
            epochs = int(sys.argv[i + 1])
            i += 2
        elif arg == "--batch" and i + 1 < len(sys.argv):
            batch = int(sys.argv[i + 1])
            i += 2
        elif arg == "--base" and i + 1 < len(sys.argv):
            base_model = sys.argv[i + 1]
            i += 2
        elif arg == "--freeze" and i + 1 < len(sys.argv):
            freeze = int(sys.argv[i + 1])
            i += 2
        elif arg == "--device" and i + 1 < len(sys.argv):
            device = sys.argv[i + 1]
            i += 2
        elif arg == "--name" and i + 1 < len(sys.argv):
            name = sys.argv[i + 1]
            i += 2
        elif arg == "--cls" and i + 1 < len(sys.argv):
            cls_loss = float(sys.argv[i + 1])
            i += 2
        elif arg == "--output-name" and i + 1 < len(sys.argv):
            output_name = sys.argv[i + 1]
            i += 2
        elif arg == "--validate":
            validate_only = True
            i += 1
        else:
            i += 1

    if validate_only:
        model_path = "box-detector-finetuned.pt"
        if not Path(model_path).exists():
            print(f"No fine-tuned model found at {model_path}. Train first.")
            sys.exit(1)
        validate_model(model_path, data_yaml)
    else:
        finetune(
            data_yaml=data_yaml,
            base_model=base_model,
            epochs=epochs,
            batch=batch,
            freeze=freeze,
            device=device,
            name=name,
            cls_loss=cls_loss,
            output_name=output_name,
        )


if __name__ == "__main__":
    main()
