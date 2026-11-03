#!/usr/bin/env bash
# GraftSeg installer for Linux (NVIDIA GPU optional).
#
#   bash install.sh            # install into the Python you run it with if nnU-Net already works there,
#                              # otherwise into a new environment at ~/graftseg-env
#   bash install.sh --new-env  # always make ~/graftseg-env
#
# Afterwards: run `graftseg` (or pick GraftSeg from the app menu).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ENV_DIR="${GRAFTSEG_ENV:-$HOME/graftseg-env}"
PY="${PYTHON:-python3}"

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

if [[ "${1:-}" != "--new-env" ]] && "$PY" -c "import nnunetv2, torch" 2>/dev/null; then
    say "nnU-Net and PyTorch already installed in $("$PY" -c 'import sys; print(sys.prefix)') - installing GraftSeg there (your torch is kept)"
    "$PY" -m pip install -e "$HERE"
else
    say "Creating environment $ENV_DIR"
    "$PY" -m venv "$ENV_DIR"
    PY="$ENV_DIR/bin/python"
    "$PY" -m pip install --upgrade pip wheel
    # Pick a PyTorch build that the installed NVIDIA driver supports (driver's max CUDA version from nvidia-smi).
    TORCH_INDEX=""
    if command -v nvidia-smi >/dev/null 2>&1; then
        CUDA_MAX=$(nvidia-smi | sed -n 's/.*CUDA Version: *\([0-9]*\)\.\([0-9]*\).*/\1\2/p' | head -1)
        say "NVIDIA driver supports CUDA up to ${CUDA_MAX:0:2}.${CUDA_MAX:2}"
        for v in 128 126 124 121 118; do
            if [[ -n "$CUDA_MAX" && "$CUDA_MAX" -ge "$v" ]]; then TORCH_INDEX="https://download.pytorch.org/whl/cu$v"; break; fi
        done
    else
        say "No NVIDIA GPU found: installing CPU PyTorch (segmenting works, slower; training needs a GPU)"
        TORCH_INDEX="https://download.pytorch.org/whl/cpu"
    fi
    if [[ -n "$TORCH_INDEX" ]]; then
        "$PY" -m pip install torch --index-url "$TORCH_INDEX" || {
            say "Could not install from $TORCH_INDEX; falling back to the default PyPI build"; "$PY" -m pip install torch; }
    fi
    "$PY" -m pip install -e "$HERE"
    mkdir -p "$HOME/.local/bin"
    ln -sf "$ENV_DIR/bin/graftseg" "$HOME/.local/bin/graftseg"
    say "Linked graftseg into ~/.local/bin (make sure it is on your PATH)"
fi

# Qt needs a few system libraries on minimal installs; tell the user if they are missing.
"$PY" -c "import PySide6.QtWidgets" 2>/dev/null || cat <<'EOF'

Qt could not start. Install its system libraries, then run graftseg again:
    sudo apt install libegl1 libgl1 libxkbcommon-x11-0 libxcb-cursor0 libfontconfig1 libdbus-1-3
EOF

"$PY" -m graftseg install-desktop || true
say "Checking the installation"
"$PY" -m graftseg doctor || true
say "Done. Start with:  graftseg      (first run: choose your workspace folder in Settings)"
