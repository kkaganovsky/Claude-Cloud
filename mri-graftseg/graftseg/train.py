"""Train nnU-Net on a dataset built by ``graftseg build``, without hand-set environment variables.

Steps (each skipped if already done, so the same command also resumes):
  1. nnUNetv2_plan_and_preprocess (single process: the multi-process blosc2 writer crashed on this data before)
  2. copy the animal-level splits_final.json into nnUNet_preprocessed (nnU-Net would otherwise make random
     case-level folds, which can put one animal's 2mo scan in train and its 4mo scan in validation)
  3. nnUNetv2_train for each fold (parallel on several GPUs, one fold per GPU; sequential otherwise)
  4. optionally register the result as a named model
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

from .config import Config

# nnU-Net ships trainers only for these lengths; nnUNetv2_train has no "number of epochs" flag.
EPOCH_TRAINERS = {1: "nnUNetTrainer_1epoch", 5: "nnUNetTrainer_5epochs", 10: "nnUNetTrainer_10epochs",
                  20: "nnUNetTrainer_20epochs", 50: "nnUNetTrainer_50epochs", 100: "nnUNetTrainer_100epochs",
                  250: "nnUNetTrainer_250epochs", 500: "nnUNetTrainer_500epochs", 750: "nnUNetTrainer_750epochs",
                  1000: "nnUNetTrainer", 2000: "nnUNetTrainer_2000epochs", 4000: "nnUNetTrainer_4000epochs"}
PLANNERS = {"default": ("ExperimentPlanner", "nnUNetPlans"),
            "resenc-m": ("nnUNetPlannerResEncM", "nnUNetResEncUNetMPlans"),
            "resenc-l": ("nnUNetPlannerResEncL", "nnUNetResEncUNetLPlans"),
            "resenc-xl": ("nnUNetPlannerResEncXL", "nnUNetResEncUNetXLPlans")}
CONFIG = "3d_fullres"


def dataset_dir(cfg: Config, dataset_id: int, where: str = "raw") -> Path:
    base = {"raw": cfg.raw, "pre": cfg.preprocessed, "results": cfg.results}[where]
    hits = sorted(base.glob(f"Dataset{dataset_id:03d}_*")) if base.exists() else []
    if not hits:
        raise FileNotFoundError(f"No Dataset{dataset_id:03d}_* in {base}")
    return hits[0]


def trainer_for(epochs: int) -> str:
    if epochs not in EPOCH_TRAINERS:
        raise ValueError(f"nnU-Net has trainers for {sorted(EPOCH_TRAINERS)} epochs only (asked for {epochs}).")
    return EPOCH_TRAINERS[epochs]


def gpu_count() -> int:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True,
                             text=True, timeout=20)
        return len([l for l in out.stdout.splitlines() if l.strip()]) if out.returncode == 0 else 0
    except Exception:
        return 0


def _bin(name: str) -> str:
    cand = Path(sys.executable).parent / name
    return str(cand) if cand.exists() else name


def _run(cmd: list[str], env: dict, log_path: Optional[Path], log=print) -> int:
    log("$ " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    fh = open(log_path, "a") if log_path else None
    p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    try:
        for line in p.stdout:
            if fh:
                fh.write(line); fh.flush()
            log(line.rstrip("\n"))
        p.wait()
    except KeyboardInterrupt:  # Stop button / Ctrl+C: let nnU-Net stop cleanly, never kill -9 mid-checkpoint
        import signal
        p.send_signal(signal.SIGINT)
        try:
            p.wait(timeout=120)
        except subprocess.TimeoutExpired:
            p.terminate()
        log("Stopped. Resume later with the same command (continues from the last checkpoint, saved every 50 epochs).")
        raise
    if fh:
        fh.close()
    return p.returncode


def results_folder(cfg: Config, dataset_id: int, epochs: int, planner: str) -> Path:
    name = dataset_dir(cfg, dataset_id).name
    return cfg.results / name / f"{trainer_for(epochs)}__{PLANNERS[planner][1]}__{CONFIG}"


def train(cfg: Config, dataset_id: int, folds: list[int], epochs: int = 1000, planner: str = "default",
          resume: bool = True, replan: bool = False, gpus: Optional[int] = None, device: str = "cuda",
          register_as: Optional[str] = None, register_checkpoint: str = "checkpoint_best.pth", log=print) -> Path:
    cfg.apply_env()
    env = os.environ.copy()
    raw = dataset_dir(cfg, dataset_id)
    trainer = trainer_for(epochs)
    pl_class, plans = PLANNERS[planner]

    # 1. plan + preprocess
    pre = cfg.preprocessed / raw.name
    if replan or not (pre / f"{plans}.json").exists() or not (pre / f"{plans}_{CONFIG}").exists():
        cmd = [_bin("nnUNetv2_plan_and_preprocess"), "-d", str(dataset_id), "-c", CONFIG, "-pl", pl_class,
               "--verify_dataset_integrity", "-npfp", "1", "-np", "1"]
        if _run(cmd, env, None, log) != 0:
            raise RuntimeError("nnUNetv2_plan_and_preprocess failed (see output above)")
    else:
        log(f"Preprocessed data found in {pre} (use --replan to redo).")

    # 2. animal-level splits
    src_split = raw / "splits_final.json"
    if src_split.exists():
        dst = pre / "splits_final.json"
        if dst.exists() and json.loads(dst.read_text()) != json.loads(src_split.read_text()):
            log(f"WARNING: {dst} differs from the dataset's animal-level split; replacing it.")
        shutil.copy2(src_split, dst)
        n = len(json.loads(src_split.read_text()))
        bad = [f for f in folds if f >= n]
        if bad:
            raise ValueError(f"Folds {bad} requested but the split has {n} folds")
    else:
        log("WARNING: no splits_final.json in the dataset; nnU-Net will make random case-level folds.")

    out = cfg.results / raw.name / f"{trainer}__{plans}__{CONFIG}"
    out.mkdir(parents=True, exist_ok=True)
    if (raw / "manifest.csv").exists():
        shutil.copy2(raw / "manifest.csv", out / "training_manifest.csv")
    logs = out / "graftseg_logs"
    logs.mkdir(exist_ok=True)

    def fold_cmd(f: int) -> list[str]:
        cmd = [_bin("nnUNetv2_train"), str(dataset_id), CONFIG, str(f), "-p", plans, "-tr", trainer, "--npz",
               "-device", device]
        if resume and any((out / f"fold_{f}" / c).exists() for c in ("checkpoint_latest.pth", "checkpoint_final.pth",
                                                                    "checkpoint_best.pth")):
            cmd.append("--c")
        return cmd

    todo = [f for f in folds if not (out / f"fold_{f}" / "checkpoint_final.pth").exists() or not resume]
    for f in sorted(set(folds) - set(todo)):
        log(f"fold {f}: already finished (checkpoint_final.pth exists)")
    n_gpu = gpus if gpus is not None else (gpu_count() if device == "cuda" else 0)
    failures = []
    if n_gpu > 1 and len(todo) > 1:
        log(f"{n_gpu} GPUs: training up to {n_gpu} folds at once. Per-fold output: {logs}/fold_N.log")
        queue = list(todo)
        lock = threading.Lock()

        def worker(gpu: int):
            while True:
                with lock:
                    if not queue:
                        return
                    f = queue.pop(0)
                e = dict(env, CUDA_VISIBLE_DEVICES=str(gpu))
                rc = _run(fold_cmd(f), e, logs / f"fold_{f}.log", log=lambda s: None)
                log(f"fold {f} on GPU {gpu}: {'done' if rc == 0 else f'FAILED (exit {rc})'}")
                if rc:
                    failures.append(f)
        threads = [threading.Thread(target=worker, args=(g,)) for g in range(min(n_gpu, len(todo)))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    else:
        for f in todo:
            log(f"=== fold {f} ({trainer}, {plans}) ===")
            if _run(fold_cmd(f), env, logs / f"fold_{f}.log", log) != 0:
                failures.append(f)
    if failures:
        raise RuntimeError(f"Training failed for folds {failures}; logs in {logs}")
    log(f"Training output: {out}")
    if register_as:
        from .models import register
        register(cfg, out, register_as, checkpoint=register_checkpoint, folds=folds, log=log,
                 notes=f"Trained by graftseg on {raw.name}, {trainer}, {plans}")
    return out


_EPOCH = re.compile(r"Epoch (\d+)\s*$")
_BEST = re.compile(r"New best EMA pseudo Dice: ([0-9.]+)")
_EMA = re.compile(r"Pseudo dice \[(.*)\]")


def status(cfg: Config, dataset_id: int) -> list[dict]:
    """Per fold: last epoch, best EMA pseudo-Dice and the epoch it was reached, checkpoints on disk."""
    rows = []
    base = dataset_dir(cfg, dataset_id, "results")
    for tdir in sorted(p for p in base.iterdir() if p.is_dir() and "__" in p.name):
        for fdir in sorted(tdir.glob("fold_*")):
            last_epoch, best, best_epoch, cur = None, None, None, None
            for logf in sorted(fdir.glob("training_log_*.txt")):
                for line in logf.read_text(errors="ignore").splitlines():
                    m = _EPOCH.search(line)
                    if m:
                        cur = int(m.group(1)); last_epoch = cur
                    m = _BEST.search(line)
                    if m:
                        best, best_epoch = round(float(m.group(1)), 4), cur
            rows.append({"run": tdir.name, "fold": fdir.name, "last_epoch": last_epoch, "best_ema_dice": best,
                         "best_epoch": best_epoch, "since_best": (last_epoch - best_epoch)
                         if last_epoch is not None and best_epoch is not None else None,
                         "checkpoints": ", ".join(sorted(p.name for p in fdir.glob("checkpoint_*.pth"))),
                         "path": str(fdir)})
    return rows
