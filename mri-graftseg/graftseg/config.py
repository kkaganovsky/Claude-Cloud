"""One settings file so nobody has to remember paths or export nnUNet_* variables.

Location: ``~/.config/graftseg/config.json`` on Linux, ``~/Library/Application Support/graftseg`` on macOS,
``%APPDATA%\\graftseg`` on Windows (override with the GRAFTSEG_CONFIG environment variable).
Everything lives under one *workspace* folder:

    <workspace>/nnUNet_raw            training datasets (images + labels, NIfTI)
    <workspace>/nnUNet_preprocessed   nnU-Net's preprocessed copies
    <workspace>/nnUNet_results        raw training output (all checkpoints, logs)
    <workspace>/models                registered models, one folder each, ready for prediction
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

def _default_config_path() -> Path:
    """Per-OS settings location: Linux ~/.config/graftseg, macOS ~/Library/Application Support/graftseg,
    Windows %APPDATA%\\graftseg."""
    import sys
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "graftseg" / "config.json"


CONFIG_PATH = Path(os.environ.get("GRAFTSEG_CONFIG", _default_config_path()))


@dataclass
class Config:
    workspace: str = ""
    nnunet_raw: str = ""
    nnunet_preprocessed: str = ""
    nnunet_results: str = ""
    models_dir: str = ""
    default_model: str = ""
    device: str = "auto"          # auto | cuda | mps (Apple silicon) | cpu
    extra: dict = field(default_factory=dict)

    # -- derived paths -------------------------------------------------------------------------------
    def _p(self, value: str, sub: str) -> Path:
        if value:
            return Path(value).expanduser()
        if not self.workspace:
            raise RuntimeError("No workspace set. Run:  graftseg setup --workspace \"/path/to/MRI NeuralNetwork\"")
        return Path(self.workspace).expanduser() / sub

    @property
    def raw(self) -> Path:
        return self._p(self.nnunet_raw, "nnUNet_raw")

    @property
    def preprocessed(self) -> Path:
        return self._p(self.nnunet_preprocessed, "nnUNet_preprocessed")

    @property
    def results(self) -> Path:
        return self._p(self.nnunet_results, "nnUNet_results")

    @property
    def models(self) -> Path:
        return self._p(self.models_dir, "models")

    def nnunet_env(self) -> dict[str, str]:
        return {"nnUNet_raw": str(self.raw), "nnUNet_preprocessed": str(self.preprocessed),
                "nnUNet_results": str(self.results)}

    def apply_env(self) -> None:
        """Set nnUNet_* for this process (and its child processes). Call before importing nnunetv2."""
        for k, v in self.nnunet_env().items():
            Path(v).mkdir(parents=True, exist_ok=True)
            os.environ[k] = v

    def save(self, path: Path = CONFIG_PATH) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))
        return path


def load(path: Path = CONFIG_PATH) -> Config:
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {k: v for k, v in data.items() if k in Config.__dataclass_fields__}
        return Config(**known)
    cfg = Config()
    # Fall back to nnUNet_* environment variables if the user already exported them.
    if os.environ.get("nnUNet_results"):
        cfg.nnunet_raw = os.environ.get("nnUNet_raw", "")
        cfg.nnunet_preprocessed = os.environ.get("nnUNet_preprocessed", "")
        cfg.nnunet_results = os.environ["nnUNet_results"]
        cfg.workspace = str(Path(cfg.nnunet_results).parent)
    return cfg


def resolve_device(cfg: Optional[Config] = None, requested: Optional[str] = None) -> str:
    want = (requested or (cfg.device if cfg else "auto") or "auto").lower()
    if want != "auto":
        return want
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
            return "mps"
        return "cpu"
    except Exception:
        return "cpu"
