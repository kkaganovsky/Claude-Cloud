"""Model registry: every usable model is one self-contained folder under ``<workspace>/models/<name>/``:

    <name>/
      MODEL_CARD.json        what it is: source, checkpoint, epoch, EMA Dice, training dataset, date, notes
      dataset.json           nnU-Net labels / channels
      plans.json             nnU-Net network + preprocessing plan (normalization scheme lives here)
      fold_0/checkpoint_best.pth   (one folder per fold; several folds = ensemble)
      training_manifest.csv  copy of the dataset manifest (which scans it was trained on), when known

Prediction needs only this folder (no nnUNet_raw / nnUNet_preprocessed / -d / -p / -tr flags).
A lone ``checkpoint_*.pth`` can be registered too: nnU-Net stores the plans and dataset.json inside it.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import Config


def _torch_load(p: Path) -> dict:
    import torch
    return torch.load(str(p), map_location="cpu", weights_only=False)


def _card_from_checkpoint(ck: dict) -> dict:
    ia = ck.get("init_args", {})
    plans = ia.get("plans", {})
    conf = ia.get("configuration", "")
    norm = None
    try:
        norm = plans["configurations"][conf]["normalization_schemes"]
    except Exception:
        pass
    return {
        "epoch": ck.get("current_epoch"),
        "best_ema_pseudo_dice": round(float(ck["_best_ema"]), 4) if ck.get("_best_ema") is not None else None,
        "trainer": ck.get("trainer_name"),
        "configuration": conf,
        "plans_name": plans.get("plans_name"),
        "dataset_name": plans.get("dataset_name"),
        "normalization": norm,
        "labels": ia.get("dataset_json", {}).get("labels"),
    }


def register(cfg: Config, source: Path, name: str, checkpoint: str = "checkpoint_best.pth",
             folds: Optional[list[int]] = None, notes: str = "", link: bool = False,
             make_default: bool = False, log=print) -> Path:
    """Register a trainer output folder (``.../nnUNetTrainer__nnUNetPlans__3d_fullres``), one of its fold folders,
    or a single ``.pth`` file as a named model."""
    source = Path(source).expanduser().resolve()
    dest = cfg.models / name
    if dest.exists():
        raise FileExistsError(f"A model called '{name}' already exists at {dest}")
    ckpts: dict[int, Path] = {}
    plans_json = dataset_json = None
    manifest = None
    if source.is_file():
        ck = _torch_load(source)
        fold = ck.get("init_args", {}).get("fold", 0)
        ckpts[int(fold) if str(fold).isdigit() else 0] = source
        plans_json = ck["init_args"]["plans"]
        dataset_json = ck["init_args"]["dataset_json"]
        checkpoint = source.name
    else:
        base = source
        if source.name.startswith("fold_"):
            base = source.parent
            folds = folds or [int(source.name.split("_")[1])]
        avail = sorted(int(p.name.split("_")[1]) for p in base.glob("fold_*") if (p / checkpoint).exists())
        if not avail:
            raise FileNotFoundError(f"No fold_*/{checkpoint} under {base}")
        for f in folds or avail:
            if f not in avail:
                raise FileNotFoundError(f"{base}/fold_{f}/{checkpoint} not found (available folds: {avail})")
            ckpts[f] = base / f"fold_{f}" / checkpoint
        plans_json = json.loads((base / "plans.json").read_text(encoding="utf-8"))
        dataset_json = json.loads((base / "dataset.json").read_text(encoding="utf-8"))
        for cand in (base / "training_manifest.csv", cfg.raw / base.parent.name / "manifest.csv"):
            if cand.exists():
                manifest = cand
                break
    dest.mkdir(parents=True)
    (dest / "plans.json").write_text(json.dumps(plans_json, indent=2))
    (dest / "dataset.json").write_text(json.dumps(dataset_json, indent=2))
    info = {}
    for f, p in sorted(ckpts.items()):
        (dest / f"fold_{f}").mkdir()
        target = dest / f"fold_{f}" / checkpoint
        if link:
            target.symlink_to(p)
        else:
            shutil.copy2(p, target)
        info = info or _card_from_checkpoint(_torch_load(p))
    if manifest:
        shutil.copy2(manifest, dest / "training_manifest.csv")
    card = {
        "name": name, "registered": datetime.now().isoformat(timespec="seconds"), "source": str(source),
        "checkpoint": checkpoint, "folds": sorted(ckpts), "copied": not link, "notes": notes, **info,
    }
    (dest / "MODEL_CARD.json").write_text(json.dumps(card, indent=2))
    log(f"Registered model '{name}' -> {dest}")
    log(f"  epoch {card.get('epoch')}, best EMA pseudo-Dice {card.get('best_ema_pseudo_dice')}, "
        f"folds {card['folds']}, normalization {card.get('normalization')}")
    if make_default or not cfg.default_model:
        cfg.default_model = name
        cfg.save()
        log(f"  '{name}' is now the default model")
    return dest


def list_models(cfg: Config) -> list[dict]:
    out = []
    if not cfg.models.exists():
        return out
    for d in sorted(cfg.models.iterdir()):
        card = d / "MODEL_CARD.json"
        if card.exists():
            c = json.loads(card.read_text(encoding="utf-8"))
            c["path"] = str(d)
            c["default"] = d.name == cfg.default_model
            out.append(c)
    return out


def resolve(cfg: Config, name_or_path: Optional[str]) -> tuple[Path, dict]:
    """Model name, model folder, or None (= default model) -> (folder, card)."""
    if name_or_path and Path(name_or_path).expanduser().is_dir():
        d = Path(name_or_path).expanduser()
    else:
        name = name_or_path or cfg.default_model
        if not name:
            raise RuntimeError("No model chosen and no default model. Register one:\n"
                               "  graftseg model add \"/path/to/checkpoint_best.pth\" --name my_model")
        d = cfg.models / name
        if not d.exists():
            known = ", ".join(m["name"] for m in list_models(cfg)) or "none"
            raise FileNotFoundError(f"Model '{name}' not found in {cfg.models} (registered: {known})")
    card = json.loads((d / "MODEL_CARD.json").read_text(encoding="utf-8")) if (d / "MODEL_CARD.json").exists() else {}
    if "checkpoint" not in card:
        ck = sorted(d.glob("fold_*/*.pth"))
        if not ck:
            raise FileNotFoundError(f"No fold_*/*.pth in {d}")
        card["checkpoint"] = ck[0].name
        card["folds"] = sorted({int(p.parent.name.split("_")[1]) for p in ck if p.name == ck[0].name})
    return d, card
