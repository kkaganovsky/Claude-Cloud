#!/usr/bin/env bash
# Start Organoid Sizer on Linux / macOS. Uses the conda environment named in $OS_ENV (default: cellpose) if present.
ENV_NAME="${OS_ENV:-cellpose}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
if command -v conda >/dev/null 2>&1; then
  eval "$(conda shell.bash hook)"
  conda activate "$ENV_NAME" 2>/dev/null || echo "conda env '$ENV_NAME' not found - using the current Python"
fi
cd "$HERE" && exec python -m organoid_sizer "$@"
