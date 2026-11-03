"""Command line: ``graftseg <command>``. Run ``graftseg`` with no command to open the app."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config as C


def _cfg() -> C.Config:
    return C.load()


def cmd_setup(a):
    cfg = _cfg()
    if a.workspace:
        cfg.workspace = str(Path(a.workspace).expanduser().resolve())
    for k in ("nnunet_raw", "nnunet_preprocessed", "nnunet_results", "models_dir", "device"):
        v = getattr(a, k)
        if v:
            setattr(cfg, k, str(Path(v).expanduser()) if k != "device" else v)
    if not cfg.workspace and not cfg.nnunet_results:
        sys.exit("Give the workspace folder, e.g.  graftseg setup --workspace \"/media/pasca/Extreme SSD/MRI NeuralNetwork\"")
    for p in (cfg.raw, cfg.preprocessed, cfg.results, cfg.models):
        p.mkdir(parents=True, exist_ok=True)
    path = cfg.save()
    print(f"Saved settings to {path}")
    for k, v in cfg.nnunet_env().items():
        print(f"  {k:20s} {v}")
    print(f"  {'models':20s} {cfg.models}")


def cmd_doctor(a):
    cfg = _cfg()
    ok = True
    print(f"settings file    {C.CONFIG_PATH} {'(found)' if C.CONFIG_PATH.exists() else '(missing: run graftseg setup)'}")
    try:
        for k, v in cfg.nnunet_env().items():
            print(f"{k:16s} {v} {'' if Path(v).exists() else '(missing)'}")
        print(f"models           {cfg.models}")
    except RuntimeError as e:
        print(e); ok = False
    try:
        import torch
        print(f"torch            {torch.__version__}, CUDA build {torch.version.cuda}")
        if torch.cuda.is_available():
            for i in range(torch.cuda.device_count()):
                p = torch.cuda.get_device_properties(i)
                print(f"GPU {i}            {p.name}, {p.total_memory / 2**30:.0f} GB")
        else:
            print("GPU              none visible to torch (prediction works on CPU, slowly; training needs a GPU)")
    except ImportError:
        print("torch            NOT INSTALLED"); ok = False
    try:
        import nnunetv2  # noqa: F401
        from importlib.metadata import version
        print(f"nnunetv2         {version('nnunetv2')}")
    except ImportError:
        print("nnunetv2         NOT INSTALLED"); ok = False
    try:
        from .models import list_models
        ms = list_models(cfg)
        print(f"models           {len(ms)} registered; default: {cfg.default_model or '(none)'}")
    except Exception:
        pass
    sys.exit(0 if ok else 1)


def cmd_scan(a):
    from .dataset import scan, write_scan_report
    cases = scan([Path(r) for r in a.roots], with_fluid=a.with_fluid, include_unreviewed=a.include_unreviewed)
    ok = [c for c in cases if c.status == "ok"]
    for c in cases:
        flag = "OK  " if c.status == "ok" else "SKIP"
        print(f"{flag} {c.case_id or c.animal:28s} graft={c.graft_segments} ignored={c.ignored_segments}"
              + (f" unknown={c.unknown_segments}" if c.unknown_segments else "")
              + (f"  ({c.reason})" if c.reason else "") + f"\n     {c.seg_path}")
    print(f"\n{len(ok)} usable / {len(cases)} segmentations, {len({c.animal_key for c in ok})} animals")
    if a.report:
        print(f"Report: {write_scan_report(cases, Path(a.report))}")


def cmd_build(a):
    from .dataset import build
    cfg = _cfg()
    out = build([Path(r) for r in a.roots], cfg.raw, a.id, a.name, with_fluid=a.with_fluid, n_folds=a.folds,
                import_nnunet=[Path(p) for p in a.include_nnunet or []], overwrite=a.overwrite, dry_run=a.dry_run)
    if not a.dry_run:
        print(f"\nNext:  graftseg train --dataset {a.id}")


def cmd_train(a):
    from .train import train
    folds = list(range(5)) if a.folds == "all" else [int(f) for f in a.folds.split(",")]
    train(_cfg(), a.dataset, folds, epochs=a.epochs, planner=a.planner, resume=not a.restart, replan=a.replan,
          gpus=a.gpus, device=a.device, register_as=a.register_as, register_checkpoint=a.register_checkpoint)


def cmd_train_status(a):
    from .train import status
    rows = status(_cfg(), a.dataset)
    for r in rows:
        print(f"{r['run']}/{r['fold']}: epoch {r['last_epoch']}, best EMA Dice {r['best_ema_dice']} at epoch "
              f"{r['best_epoch']} ({r['since_best']} epochs ago); {r['checkpoints']}")
    if not rows:
        print("No training runs found.")


def cmd_model(a):
    from . import models as M
    cfg = _cfg()
    if a.action == "list":
        ms = M.list_models(cfg)
        if not ms:
            print(f"No models in {cfg.models}. Add one:  graftseg model add <checkpoint.pth or trainer folder> --name NAME")
        for m in ms:
            print(f"{'*' if m['default'] else ' '} {m['name']:24s} epoch {m.get('epoch')}, EMA Dice "
                  f"{m.get('best_ema_pseudo_dice')}, folds {m.get('folds')}, {m.get('checkpoint')}, {m.get('registered')}")
            print(f"    {m['path']}")
    elif a.action == "add":
        M.register(cfg, Path(a.source), a.name, checkpoint=a.checkpoint,
                   folds=[int(f) for f in a.folds.split(",")] if a.folds else None, notes=a.notes or "",
                   link=a.link, make_default=a.default)
    elif a.action == "default":
        M.resolve(cfg, a.name)
        cfg.default_model = a.name
        cfg.save()
        print(f"Default model: {a.name}")
    elif a.action == "show":
        d, card = M.resolve(cfg, a.name)
        print(d)
        print(json.dumps(card, indent=2))


def cmd_predict(a):
    from .predict import run
    cfg = _cfg()
    rows, _ = run(cfg, [Path(p) for p in a.inputs], model=a.model, device=a.device, overwrite=a.overwrite,
                  fast=a.fast, with_fluid=a.with_fluid, min_component_mm3=a.min_component_mm3,
                  summary_csv=Path(a.summary) if a.summary else None, checkpoint=a.checkpoint, dry_run=a.dry_run)
    if a.review and rows:
        from .app import launch
        launch(page="review", review_paths=[r["seg_nrrd"] for r in rows if r.get("seg_nrrd")])


def cmd_review(a):
    from .app import launch
    from .review import collect
    paths = collect([Path(p) for p in a.inputs], include_reviewed=not a.only_unreviewed)
    launch(page="review", review_paths=[str(p) for p in paths])


def cmd_repair(a):
    from .slicer_seg import repair_transposed
    n = 0
    for inp in a.inputs:
        p = Path(inp)
        for f in ([p] if p.is_file() else sorted(p.rglob("*.seg.nrrd"))):
            out = repair_transposed(f, status="reviewed" if a.mark_reviewed else "predicted")
            if out:
                n += 1
                print(f"repaired {f}")
    print(f"{n} file(s) repaired (files not written by the old predict_grafts.py are left alone)")


def cmd_gui(a):
    from .app import launch
    launch()


def cmd_desktop(a):
    """Add a menu entry / desktop launcher (Linux)."""
    exe = Path(sys.executable)
    entry = ("[Desktop Entry]\nType=Application\nName=GraftSeg\nComment=MRI graft segmentation\n"
             f"Exec=\"{exe}\" -m graftseg\nTerminal=false\nCategories=Science;MedicalSoftware;\nIcon=applications-science\n")
    targets = [Path.home() / ".local/share/applications/graftseg.desktop"]
    desk = Path.home() / "Desktop"
    if desk.exists():
        targets.append(desk / "graftseg.desktop")
    for t in targets:
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text(entry)
        t.chmod(0o755)
        print(f"wrote {t}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="graftseg", description=__doc__)
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("setup", help="choose the workspace folder (once)")
    s.add_argument("--workspace"); s.add_argument("--nnunet-raw", dest="nnunet_raw")
    s.add_argument("--nnunet-preprocessed", dest="nnunet_preprocessed")
    s.add_argument("--nnunet-results", dest="nnunet_results"); s.add_argument("--models-dir", dest="models_dir")
    s.add_argument("--device", choices=["auto", "cuda", "cpu"])
    s.set_defaults(fn=cmd_setup)

    s = sub.add_parser("doctor", help="check paths, GPU, installed packages, models"); s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("scan", help="list labeled scans and which segment is used as graft (writes nothing)")
    s.add_argument("roots", nargs="+"); s.add_argument("--with-fluid", action="store_true")
    s.add_argument("--include-unreviewed", action="store_true", help="also use model predictions nobody reviewed")
    s.add_argument("--report", help="write the decisions to this CSV"); s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("build", help="build an nnU-Net training dataset from labeled folders")
    s.add_argument("roots", nargs="*", help="folders with <animal>/<MRI>.nrrd + .seg.nrrd")
    s.add_argument("--id", type=int, required=True, help="dataset number, e.g. 3 -> Dataset003_GraftSeg")
    s.add_argument("--name", default="GraftSeg"); s.add_argument("--with-fluid", action="store_true")
    s.add_argument("--folds", type=int, default=5)
    s.add_argument("--include-nnunet", action="append", help="also copy an existing nnU-Net dataset folder")
    s.add_argument("--overwrite", action="store_true"); s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_build)

    s = sub.add_parser("train", help="preprocess + train (resumes automatically)")
    s.add_argument("--dataset", type=int, required=True)
    s.add_argument("--folds", default="0", help="'0', '0,1,2' or 'all' (default 0)")
    s.add_argument("--epochs", type=int, default=1000, help="250, 500, 750, 1000, 2000 or 4000")
    s.add_argument("--planner", default="default", choices=["default", "resenc-m", "resenc-l", "resenc-xl"])
    s.add_argument("--restart", action="store_true", help="ignore existing checkpoints")
    s.add_argument("--replan", action="store_true"); s.add_argument("--gpus", type=int)
    s.add_argument("--device", default="cuda", choices=["cuda", "cpu"])
    s.add_argument("--register-as", help="register the finished model under this name")
    s.add_argument("--register-checkpoint", default="checkpoint_best.pth")
    s.set_defaults(fn=cmd_train)

    s = sub.add_parser("train-status", help="epoch / best Dice per fold")
    s.add_argument("--dataset", type=int, required=True); s.set_defaults(fn=cmd_train_status)

    s = sub.add_parser("model", help="list / add / default / show models")
    s.add_argument("action", choices=["list", "add", "default", "show"])
    s.add_argument("source", nargs="?", help="add: checkpoint .pth, fold_N folder or trainer output folder")
    s.add_argument("--name"); s.add_argument("--checkpoint", default="checkpoint_best.pth")
    s.add_argument("--folds"); s.add_argument("--notes"); s.add_argument("--link", action="store_true",
                                                                         help="link instead of copying weights")
    s.add_argument("--default", action="store_true")
    s.set_defaults(fn=cmd_model)

    s = sub.add_parser("predict", help="segment new scans (DICOM or .nrrd); writes .seg.nrrd next to each")
    s.add_argument("inputs", nargs="+"); s.add_argument("--model"); s.add_argument("--checkpoint")
    s.add_argument("--device", choices=["auto", "cuda", "cpu"]); s.add_argument("--overwrite", action="store_true")
    s.add_argument("--fast", action="store_true", help="no mirroring test-time augmentation (~8x faster)")
    s.add_argument("--with-fluid", action="store_true"); s.add_argument("--min-component-mm3", type=float, default=0)
    s.add_argument("--summary"); s.add_argument("--dry-run", action="store_true", help="only show names found")
    s.add_argument("--review", action="store_true", help="open the editor afterwards")
    s.set_defaults(fn=cmd_predict)

    s = sub.add_parser("review", help="open the editor on .seg.nrrd files / folders")
    s.add_argument("inputs", nargs="+"); s.add_argument("--only-unreviewed", action="store_true")
    s.set_defaults(fn=cmd_review)

    s = sub.add_parser("repair-old", help="fix axis order of .seg.nrrd written by the old predict_grafts.py")
    s.add_argument("inputs", nargs="+"); s.add_argument("--mark-reviewed", action="store_true")
    s.set_defaults(fn=cmd_repair)

    s = sub.add_parser("gui", help="open the app"); s.set_defaults(fn=cmd_gui)
    s = sub.add_parser("install-desktop", help="add GraftSeg to the Linux app menu"); s.set_defaults(fn=cmd_desktop)

    a = p.parse_args(argv)
    if a.cmd is None:
        return cmd_gui(a)
    if a.cmd == "model" and a.action in ("add",) and (not a.source or not a.name):
        p.error("model add needs SOURCE and --name")
    if a.cmd == "model" and a.action in ("default",) and not a.name:
        p.error("model default needs --name")
    try:
        a.fn(a)
    except (FileNotFoundError, FileExistsError, RuntimeError, ValueError) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
