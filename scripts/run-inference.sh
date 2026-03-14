#!/bin/bash
# Wrapper script for Expanso subprocess input.
# Runs esc-infer via uv, redirecting uv's own build output to stderr
# so only JSON events from the inference engine reach stdout.
set -euo pipefail
cd /home/daaronch/security-cameras
exec /home/daaronch/.local/bin/uv run --quiet esc-infer config.yaml
