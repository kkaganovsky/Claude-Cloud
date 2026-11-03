"""Segment new scans with a registered model and write Slicer files next to each scan.

For a scan in folder F named PA_01_R2M (see naming.py) the outputs are:

    F/PA_01_R2M.seg.nrrd     graft segmentation ("Graft" segment), Slicer-compatible
    F/PA_01_R2M.nrrd         the MRI itself, written only when the input was DICOM (so Slicer opens the pair and
                             the folder can be used for retraining once the segmentation has been reviewed)

A human segmentation that already sits there is never overwritten: the prediction goes to
``PA_01_R2M_pred.seg.nrrd`` instead and its Dice against the human one is reported.
"""
from __future__ import annotations

import csv
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import nrrd
import numpy as np
import SimpleITK as sitk

from . import labels as L
from .config import Config, resolve_device
from .models import resolve
from .scans import Scan, find_scans
from .slicer_seg import TOOL_FIELD, masks_on_reference, read_seg, write_seg, write_volume


def dice(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a.astype(bool), b.astype(bool)
    s = a.sum() + b.sum()
    return float(2 * (a & b).sum() / s) if s else 1.0


def remove_small_components(mask: np.ndarray, spacing, min_mm3: float) -> np.ndarray:
    if min_mm3 <= 0 or not mask.any():
        return mask
    from scipy import ndimage
    lab, n = ndimage.label(mask, structure=np.ones((3, 3, 3)))
    vox = float(np.prod(spacing))
    sizes = ndimage.sum(mask, lab, range(1, n + 1)) * vox
    keep = np.isin(lab, [i + 1 for i, s in enumerate(sizes) if s >= min_mm3])
    return keep


def n_components(mask: np.ndarray) -> int:
    if not mask.any():
        return 0
    from scipy import ndimage
    return int(ndimage.label(mask, structure=np.ones((3, 3, 3)))[1])


class Segmenter:
    def __init__(self, cfg: Config, model: Optional[str] = None, device: Optional[str] = None,
                 fast: bool = False, checkpoint: Optional[str] = None, folds: Optional[list[int]] = None):
        self.model_dir, self.card = resolve(cfg, model)
        self.name = self.card.get("name", self.model_dir.name)
        for k, v in cfg.nnunet_env().items():  # nnU-Net imports want these set, prediction does not use them
            os.environ.setdefault(k, v)
        import torch
        from nnunetv2.inference.predict_from_raw_data import nnUNetPredictor
        dev = resolve_device(cfg, device)
        if dev == "cpu":
            torch.set_num_threads(max(1, os.cpu_count() or 1))
        self.predictor = nnUNetPredictor(tile_step_size=0.5, use_gaussian=True, use_mirroring=not fast,
                                         device=torch.device(dev), verbose=False, allow_tqdm=False)
        self.checkpoint = checkpoint or self.card["checkpoint"]
        self.folds = tuple(folds or self.card["folds"])
        self.predictor.initialize_from_trained_model_folder(str(self.model_dir), use_folds=self.folds,
                                                            checkpoint_name=self.checkpoint)
        labels = self.predictor.dataset_json["labels"]
        self.label_names = {int(v): k for k, v in labels.items() if not isinstance(v, list)}
        self.graft_value = next((v for v, n in self.label_names.items() if v and L.classify_segment(n, v - 1)
                                 == L.GRAFT), 1)
        self.fluid_value = next((v for v, n in self.label_names.items() if v and L.classify_segment(n, 1)
                                 == L.FLUID), None)
        self.device = dev

    def __call__(self, image: sitk.Image) -> np.ndarray:
        """MRI -> label array (z, y, x) on the MRI grid."""
        with tempfile.TemporaryDirectory(prefix="graftseg_") as td:
            inp = Path(td) / "case_0000.nii.gz"
            sitk.WriteImage(sitk.Cast(image, sitk.sitkFloat32), str(inp))
            self.predictor.predict_from_files_sequential([[str(inp)]], [str(Path(td) / "pred")],
                                                         save_probabilities=False, overwrite=True)
            return sitk.GetArrayFromImage(sitk.ReadImage(str(Path(td) / "pred.nii.gz"))).astype(np.uint8)


def _existing_human_graft(seg_path: Path, image: sitk.Image) -> Optional[np.ndarray]:
    seg = read_seg(seg_path)
    graft = [s for i, s in enumerate(seg.segments) if L.classify_segment(s.name, i) == L.GRAFT]
    return masks_on_reference(seg, graft, image) if graft else None


def output_paths(scan: Scan) -> dict:
    base = scan.folder / scan.name
    return {"seg": Path(f"{base}.seg.nrrd"), "pred": Path(f"{base}_pred.seg.nrrd"), "image": Path(f"{base}.nrrd")}


def run(cfg: Config, inputs: list[Path], model: Optional[str] = None, device: Optional[str] = None,
        overwrite: bool = False, fast: bool = False, with_fluid: bool = False, min_component_mm3: float = 0.0,
        summary_csv: Optional[Path] = None, checkpoint: Optional[str] = None, dry_run: bool = False,
        log=print) -> tuple[list[dict], Optional[Path]]:
    scans = find_scans(inputs)
    log(f"Found {len(scans)} scan(s).")
    names_seen: dict[str, int] = {}
    for s in scans:
        names_seen[str(s.folder / s.name)] = names_seen.get(str(s.folder / s.name), 0) + 1
    for s in scans:
        if names_seen[str(s.folder / s.name)] > 1 and s.kind == "nrrd":
            s.name = f"{s.name}_{s.path.name[:-5]}"  # two volumes in one folder parse to the same animal
    if dry_run:
        for s in scans:
            log(f"  {s.name:24s} [{s.name_source}] {s.kind:15s} {s.path}" + (f"  ({s.note})" if s.note else ""))
        return [], None
    seg_model = Segmenter(cfg, model, device, fast, checkpoint)
    log(f"Model '{seg_model.name}' ({seg_model.checkpoint}, folds {list(seg_model.folds)}) on {seg_model.device}")
    rows = []
    for i, s in enumerate(scans, 1):
        out = output_paths(s)
        row = {"name": s.name, "name_from": s.name_source, "input": str(s.path), "kind": s.kind,
               "series_description": s.series_description, "note": s.note, "seg_nrrd": "", "graft_voxels": "",
               "graft_mm3": "", "components": "", "dice_vs_existing": "", "qc": "", "status": "",
               "model": seg_model.name, "checkpoint": seg_model.checkpoint}
        target = out["seg"]
        human = None
        if s.kind == "nrrd":  # a person's segmentation saved next to this MRI under the MRI's own name
            own = s.path.with_name(s.path.name[:-5] + ".seg.nrrd")
            if own.exists() and own != out["seg"] and nrrd.read_header(str(own)).get(TOOL_FIELD) is None:
                human, target = own, out["pred"]
        if target == out["seg"] and target.exists():
            status = nrrd.read_header(str(target)).get(TOOL_FIELD)
            if status is None:      # made by a person (Slicer): keep it, predict next to it
                human, target = out["seg"], out["pred"]
            elif status == "reviewed" and not overwrite:
                row["status"], row["seg_nrrd"] = "skipped (already reviewed)", str(target)
                rows.append(row); log(f"[{i}/{len(scans)}] {s.name}: already reviewed, skipped"); continue
            elif not overwrite:
                row["status"], row["seg_nrrd"] = "skipped (exists)", str(target)
                rows.append(row); log(f"[{i}/{len(scans)}] {s.name}: prediction exists, skipped"); continue
        if target == out["pred"] and target.exists() and not overwrite:
            row["status"], row["seg_nrrd"] = "skipped (exists)", str(target)
            rows.append(row); log(f"[{i}/{len(scans)}] {s.name}: prediction exists, skipped"); continue
        try:
            img = s.load()
            lab = seg_model(img)
            graft = lab == seg_model.graft_value
            graft = remove_small_components(graft, img.GetSpacing(), min_component_mm3)
            out_lab = graft.astype(np.uint8)
            names = {1: "Graft"}
            if with_fluid and seg_model.fluid_value is not None:
                out_lab[(lab == seg_model.fluid_value) & ~graft] = 2
                names[2] = "Fluid"
            if s.kind != "nrrd" and not out["image"].exists():
                write_volume(out["image"], img, {"GraftSeg_Source": f"converted from {s.path.name}"})
            write_seg(target, out_lab, img, names, status="predicted", model=seg_model.name,
                      extra={"GraftSeg_Input": s.path.name, "GraftSeg_Checkpoint": seg_model.checkpoint})
            vox = int(graft.sum())
            mm3 = vox * float(np.prod(img.GetSpacing()))
            row.update(seg_nrrd=str(target), graft_voxels=vox, graft_mm3=round(mm3, 3),
                       components=n_components(graft), status="predicted")
            qc = []
            if vox == 0:
                qc.append("EMPTY")
            elif mm3 < 1.0:
                qc.append("tiny (<1 mm3)")
            if row["components"] and row["components"] > 1:
                qc.append(f"{row['components']} separate pieces")
            if human is not None:
                h = _existing_human_graft(human, img)
                if h is not None:
                    row["dice_vs_existing"] = round(dice(graft, h), 4)
            row["qc"] = "; ".join(qc)
            log(f"[{i}/{len(scans)}] {s.name}: graft {mm3:.2f} mm3 -> {target.name}"
                + (f"  Dice vs existing {row['dice_vs_existing']}" if row["dice_vs_existing"] != "" else "")
                + (f"  [{row['qc']}]" if row["qc"] else ""))
        except Exception as e:  # noqa: BLE001
            row["status"] = f"failed: {e}"
            log(f"[{i}/{len(scans)}] {s.name}: FAILED {e}")
        rows.append(row)
    if summary_csv is None and inputs:
        root = Path(inputs[0]) if Path(inputs[0]).is_dir() else Path(inputs[0]).parent
        summary_csv = root / f"graftseg_predictions_{datetime.now():%Y%m%d_%H%M%S}.csv"
    if rows and summary_csv:
        with open(summary_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        log(f"Summary: {summary_csv}")
    return rows, summary_csv
