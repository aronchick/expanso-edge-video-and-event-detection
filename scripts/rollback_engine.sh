#!/usr/bin/env bash
# Roll the live demo back from drone-3class.engine → yolov8s.engine.
#
# Use this if the fine-tuned 3-class model misbehaves on stage (weak drone
# detection, false positives, whatever) and you need the legacy COCO path
# back in front of the audience.
#
# What it does:
#   1. Rewrites jobs/sensor-{north,south}-job.yaml on disk so --yolo-model
#      points at yolov8s.engine again. (Detector.py still works because it
#      derives class indices from model.names — see detector.py docstring.)
#   2. Commits + pushes (so Jetson's `git pull` picks it up).
#   3. SSHes to Jetson, pulls, redeploys both sensor jobs.
#
# To re-deploy the fine-tuned engine after a rollback, just edit
# jobs/sensor-*-job.yaml back to drone-3class.engine and run
# `expanso-cli job deploy` again.
set -euo pipefail

cd "$(dirname "$0")/.."

if ! grep -q 'drone-3class.engine' jobs/sensor-north-job.yaml; then
  echo "Already on the legacy engine — no rollback needed."
  exit 0
fi

echo "→ rewriting YAMLs to point at yolov8s.engine"
sed -i.bak \
  's|--yolo-model=/home/daaronch/security-cameras/drone-3class.engine|--yolo-model=/home/daaronch/security-cameras/yolov8s.engine|g' \
  jobs/sensor-north-job.yaml jobs/sensor-south-job.yaml
rm -f jobs/sensor-north-job.yaml.bak jobs/sensor-south-job.yaml.bak

echo "→ committing + pushing"
git add jobs/sensor-north-job.yaml jobs/sensor-south-job.yaml
git commit -m "Rollback: revert sensor pipelines to legacy yolov8s.engine"
git push

echo "→ pulling + redeploying on Jetson"
ssh jetson "cd ~/security-cameras && git pull --rebase && \
  expanso-cli job deploy jobs/sensor-north-job.yaml && \
  expanso-cli job deploy jobs/sensor-south-job.yaml"

echo
echo "✅ Rollback complete. Live demo is back on yolov8s.engine."
echo "   Verify: open http://192.168.2.1:8090"
