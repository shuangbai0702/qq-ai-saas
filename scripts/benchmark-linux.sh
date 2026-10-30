#!/usr/bin/env bash
set -euo pipefail
# Run on a real Linux host from the product root. Does not install services or
# change cgroup controllers; use an already delegated group if available.
cd "$(dirname "$0")/.."
label="${1:-linux-$(date +%Y%m%d-%H%M%S)}"
python_bin="${ASTRBOT_PYTHON:-references/astrbot/.venv/bin/python}"
node scripts/production-build.mjs
"$python_bin" scripts/benchmark-platform.py --profiles-only --seconds 60 --label "$label"
sample_args=(--seconds 120 --output "experiments/round2-platform/results/$label-live.json")
if [[ -n "${FISHBOT_CGROUP_ROOT:-}" ]]; then sample_args+=(--cgroup-root "$FISHBOT_CGROUP_ROOT"); fi
"$python_bin" scripts/sample_resources.py "${sample_args[@]}"
