#!/usr/bin/env bash
# Launch wrapper for edge-sensor on the Jetson with CUDA torch + TRT runtime.
#
# The venv was originally CPU-only; we reinstalled torch from the Jetson AI Lab
# CUDA wheel which links against:
#   - /usr/local/cuda/lib64           — system cuBLAS 12.6 (must come FIRST,
#                                        the wheel-installed cuBLAS 12.9
#                                        triggers CUBLAS_STATUS_ALLOC_FAILED
#                                        on Orin)
#   - .venv/.../nvidia/cu12/lib       — pip-installed libcudss.so.0 needed
#                                        by torch._C
#
# Without these on LD_LIBRARY_PATH the sensor either crashes at import (no
# cuDSS) or fails the first matmul (cuBLAS handle alloc).
#
# Usage:
#   scripts/run_sensor.sh --node-id sensor-north --rtsp-url ... --yolo-model yolov8s.engine ...
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${REPO_ROOT}/.venv"
NVIDIA_CU12="${VENV}/lib/python3.10/site-packages/nvidia/cu12/lib"

if [ ! -d "${NVIDIA_CU12}" ]; then
  echo "ERROR: missing ${NVIDIA_CU12}. Run: uv pip install nvidia-cudss-cu12" >&2
  exit 1
fi

export LD_LIBRARY_PATH="/usr/local/cuda/lib64:${NVIDIA_CU12}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "${VENV}/bin/edge-sensor" "$@"
