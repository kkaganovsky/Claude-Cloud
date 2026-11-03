#!/usr/bin/env python3
"""
predict_grafts.py — Graft segmentation inference script with interactive editor
================================================================================
Walks a root folder of Bruker multi-frame DICOM files, runs nnU-Net
(or classical fallback) segmentation, optionally opens an interactive
slice editor for manual correction, then saves a 3D Slicer-compatible
.seg.nrrd file next to each DICOM.

Multi-series handling
---------------------
Each case folder may contain multiple DICOM files (T2 anatomy, localizer,
scout, etc.).  The script scores every .dcm in a folder and picks the best
one using this priority order:
  1. SeriesDescription contains "T2_Anatomy" (case-insensitive)
  2. SeriesDescription contains "T2" and has the most slices
  3. Largest file by byte size (last resort)
Only one .seg.nrrd is written per case folder.

Output naming
-------------
Derived from SeriesDescription: "T2_Anatomy__PA05_R2M(E7)" → PA05_R2M
Fallback: parent folder name if PA/EarTag cannot be parsed.
Saved as: <dicom_dir>/PA05_R2M/PA05_R2M.seg.nrrd

Summary CSV
-----------
A run-level summary is written to <input_root>/segmentation_summary.csv
with columns: case_name, parent_cage, ear_tag, dicom_file, series_description,
              n_slices, voxel_spacing_mm, graft_voxels, graft_volume_mm3,
              dice (if GT label found alongside DICOM), status, nrrd_path,
              edited (whether the user manually edited the segmentation)

Usage
-----
  # nnU-Net with best checkpoint (interim or final):
  python predict_grafts.py --input /path/to/Pasca_2026 \\
                           --model nnunet \\
                           --nnunet_results /path/to/nnUNet_results \\
                           --nnunet_checkpoint checkpoint_best.pth

  # nnU-Net with final trained model:
  python predict_grafts.py --input /path/to/Pasca_2026 \\
                           --model nnunet \\
                           --nnunet_results /path/to/nnUNet_results \\
                           --nnunet_checkpoint checkpoint_final.pth

  # With interactive review/editing GUI after each prediction:
  python predict_grafts.py --input /path/to/Pasca_2026 \\
                           --model nnunet \\
                           --nnunet_results /path/to/nnUNet_results \\
                           --review

  # Re-edit existing .seg.nrrd files without re-running segmentation:
  python predict_grafts.py --input /path/to/Pasca_2026 --review_only

  # Classical fallback (CPU, no GPU needed):
  python predict_grafts.py --input /path/to/Pasca_2026 --model classical

Checkpoint argument
-------------------
  --nnunet_checkpoint   Name of the .pth checkpoint file inside the fold folder.
                        Default: checkpoint_best.pth
                        During training:  checkpoint_best.pth  = best epoch so far
                        After training:   checkpoint_final.pth = fully trained model
                        Custom snapshot:  checkpoint_epoch_200_snapshot.pth
                          (copy the file manually: cp checkpoint_latest.pth checkpoint_epoch_200_snapshot.pth)

Dependencies
------------
  pip install pydicom nibabel numpy scipy pynrrd
  (nnU-Net must be installed separately for --model nnunet)
  tkinter is part of the Python standard library (install python3-tk on Linux if missing)
"""

import argparse
import copy
import csv
import os
import re
import sys
import shutil
import subprocess
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pydicom
import nibabel as nib
import nrrd
from scipy import ndimage

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Naming helpers
# ─────────────────────────────────────────────────────────────────────────────

def parse_series_description(series_desc: str):
    """
    Extract (parent_cage, ear_tag) from SeriesDescription.

    Handles all observed delimiter variants:
      T2_Anatomy__PA05_R2M(E7)    standard underscore
      T2_Anatomy__PA05-R2M(E7)    dash delimiter
      T2_Anatomy__PA05 R2M(E7)    space delimiter
      T2_Anatomy__PA05_R2M (E7)   space before (E#)
      T2_Anatomy_PA05_R2M(E7)     single underscore prefix
      T2_Anatomy__PA_05_R2M(E7)   underscore inside PA number
      T2_Anatomy__PA_01_R2M(E7)   PA_01 style
      T2_Anatomy__PA05_R2M        no (E#) suffix
      SomePrefix_PA05_R2M(E7)     arbitrary prefix

    Returns (parent_cage, ear_tag) e.g. ("PA05", "R2M")
    Raises ValueError if pattern not found.
    """
    s = series_desc.strip()
    patterns = [
        # With (E#) suffix — most specific
        r'(PA[\d_]+)[_\-\s]+([A-Za-z][A-Za-z0-9]+)\s*\(E\d+\)',
        # Without (E#) suffix
        r'(PA[\d_]+)[_\-\s]+([A-Za-z][A-Za-z0-9]+)',
    ]
    for pat in patterns:
        m = re.search(pat, s)
        if m:
            parent_cage = m.group(1).rstrip('_')
            ear_tag     = m.group(2)
            if re.match(r'^[A-Za-z]', ear_tag) and not re.match(r'^\d{4}', ear_tag):
                return parent_cage, ear_tag
    raise ValueError(f"Cannot parse PA/EarTag from: '{series_desc}'")


def fallback_name_from_folder(dcm_path: Path) -> str:
    """
    Last-resort naming from the parent folder name.
    Strips the first two underscore-delimited tokens (batch prefix) and
    collapses multiple underscores.
    """
    folder = dcm_path.parent.name
    parts  = re.split(r'_', folder, maxsplit=2)
    name   = parts[-1] if len(parts) >= 2 else folder
    name   = re.sub(r'_+', '_', name).strip('_')
    return name


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Multi-series selection
# ─────────────────────────────────────────────────────────────────────────────

ENHANCED_MR_UID = "1.2.840.10008.5.1.4.1.1.4.1"
CLASSIC_MR_UID  = "1.2.840.10008.5.1.4.1.1.4"


def _score_dicom(dcm_path: Path) -> tuple:
    try:
        ds = pydicom.dcmread(str(dcm_path), stop_before_pixels=True)
    except Exception:
        return None

    modality = getattr(ds, 'Modality', '')
    if modality != 'MR':
        return None

    sop_uid     = getattr(ds, 'SOPClassUID', '')
    series_desc = getattr(ds, 'SeriesDescription', '').lower()
    n_frames    = int(getattr(ds, 'NumberOfFrames', 1))
    file_size   = dcm_path.stat().st_size

    if 't2_anatomy' in series_desc or 't2anatomy' in series_desc:
        priority = 0
    elif 't2' in series_desc:
        priority = 1
    elif sop_uid == ENHANCED_MR_UID:
        priority = 2
    elif sop_uid == CLASSIC_MR_UID:
        priority = 3
    else:
        priority = 4

    return (priority, -n_frames, -file_size)


def select_best_dicom(folder: Path) -> Path | None:
    dcm_files = sorted(folder.glob("*.dcm"))
    if not dcm_files:
        dcm_files = [p for p in sorted(folder.iterdir())
                     if p.is_file() and p.suffix == '' and p.name.startswith('EnIm')]
    if not dcm_files:
        return None
    if len(dcm_files) == 1:
        return dcm_files[0]

    scored = []
    for p in dcm_files:
        score = _score_dicom(p)
        if score is not None:
            scored.append((score, p))
    if not scored:
        return None

    scored.sort(key=lambda x: x[0])
    best_path = scored[0][1]

    if len(scored) > 1:
        print(f"  [MULTI-SERIES] {len(scored)} MR series found in {folder.name}:")
        for score, p in scored:
            ds   = pydicom.dcmread(str(p), stop_before_pixels=True)
            desc = getattr(ds, 'SeriesDescription', '(no description)')
            nf   = getattr(ds, 'NumberOfFrames', 1)
            tag  = " ← SELECTED" if p == best_path else ""
            print(f"      {p.name:20s}  [{desc}]  {nf} frames{tag}")

    return best_path


def find_case_folders(root: Path) -> list[Path]:
    seen_folders = set()
    for dcm in sorted(root.rglob("*.dcm")):
        seen_folders.add(dcm.parent)
    for p in sorted(root.rglob("EnIm1")):
        if p.is_file():
            seen_folders.add(p.parent)
    return sorted(seen_folders)


# ─────────────────────────────────────────────────────────────────────────────
# 3.  DICOM → NIfTI
# ─────────────────────────────────────────────────────────────────────────────

def dicom_to_nifti(dcm_path: Path, out_nii_path: Path) -> nib.Nifti1Image:
    ds  = pydicom.dcmread(str(dcm_path))
    arr = ds.pixel_array.astype(np.float32)

    sfgs = ds.SharedFunctionalGroupsSequence[0]
    try:
        pvt = sfgs.PixelValueTransformationSequence[0]
        arr = arr * float(pvt.RescaleSlope) + float(pvt.RescaleIntercept)
    except (AttributeError, IndexError):
        pass

    pfgs       = ds.PerFrameFunctionalGroupsSequence
    frame_info = []
    for i, frame in enumerate(pfgs):
        sp  = int(frame.FrameContentSequence[0].InStackPositionNumber)
        ipp = [float(x) for x in frame.PlanePositionSequence[0].ImagePositionPatient]
        frame_info.append((sp, i, ipp))
    frame_info.sort(key=lambda x: x[0])
    arr = arr[[fi[1] for fi in frame_info]]

    iop         = [float(x) for x in sfgs.PlaneOrientationSequence[0].ImageOrientationPatient]
    row_cos     = np.array(iop[:3])
    col_cos     = np.array(iop[3:])
    ps          = sfgs.PixelMeasuresSequence[0].PixelSpacing
    col_spacing = float(ps[0])
    row_spacing = float(ps[1])

    ipp0      = np.array(frame_info[0][2])
    ipp1      = np.array(frame_info[1][2])
    slice_vec = ipp1 - ipp0
    slice_sp  = np.linalg.norm(slice_vec)
    slice_dir = slice_vec / slice_sp if slice_sp > 0 else np.cross(row_cos, col_cos)

    F      = np.diag([-1., -1., 1.])
    affine = np.eye(4)
    affine[:3, 0] = F @ (row_cos   * row_spacing)
    affine[:3, 1] = F @ (col_cos   * col_spacing)
    affine[:3, 2] = F @ (slice_dir * slice_sp)
    affine[:3, 3] = F @ ipp0

    img = nib.Nifti1Image(arr.transpose(2, 1, 0), affine)
    img.header.set_xyzt_units('mm')
    nib.save(img, str(out_nii_path))
    return img


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Segmentation models
# ─────────────────────────────────────────────────────────────────────────────

def run_nnunet(nii_path: Path, out_dir: Path,
               dataset_id: int, fold: int,
               plans: str, results_dir: str,
               checkpoint: str = "checkpoint_best.pth") -> np.ndarray:
    """
    Run nnUNetv2_predict on a single NIfTI. Returns binary mask array.

    checkpoint: name of the .pth file inside the fold folder.
      checkpoint_best.pth   — best epoch seen so far (use during/after training)
      checkpoint_final.pth  — final epoch (use after training completes)
      checkpoint_latest.pth — most recent periodic save (every 50 epochs)
      checkpoint_epoch_200_snapshot.pth — manually copied snapshot
    """
    tmp_in  = out_dir / "tmp_input"
    tmp_out = out_dir / "tmp_output"
    tmp_in.mkdir(exist_ok=True)
    tmp_out.mkdir(exist_ok=True)

    in_file = tmp_in / "case_0000_0000.nii.gz"
    shutil.copy(str(nii_path), str(in_file))

    env = os.environ.copy()
    if results_dir:
        env["nnUNet_results"] = results_dir

    cmd = [
        "nnUNetv2_predict",
        "-i",   str(tmp_in),
        "-o",   str(tmp_out),
        "-d",   str(dataset_id),
        "-c",   "3d_fullres",
        "-f",   str(fold),
        "-p",   plans,
        "-chk", checkpoint,
        "--disable_tta",
    ]
    print(f"    Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, env=env)
    if result.returncode != 0:
        raise RuntimeError("nnUNetv2_predict failed.")

    pred_file = tmp_out / "case_0000.nii.gz"
    if not pred_file.exists():
        raise FileNotFoundError(f"Prediction not found: {pred_file}")

    mask = nib.load(str(pred_file)).get_fdata().astype(np.uint8)
    shutil.rmtree(str(tmp_in))
    shutil.rmtree(str(tmp_out))
    return mask


def run_classical(nii_path: Path,
                  seed_pct: float = 99.2,
                  grow_pct: float = 98.0,
                  pad_mm:   float = 2.0) -> np.ndarray:
    """
    Optimized threshold + region-grow pipeline.
    Best params from grid search on 205 training cases:
      seed=99.2%, grow=98.0%, pad=2mm  →  mean Dice 0.382.
    """
    img_nib = nib.load(str(nii_path))
    img     = img_nib.get_fdata().astype(np.float32)
    zooms   = img_nib.header.get_zooms()[:3]
    dz, dy, dx = float(zooms[2]), float(zooms[1]), float(zooms[0])

    thresh_seed = np.percentile(img, seed_pct)
    labeled, n  = ndimage.label(img >= thresh_seed)
    if n == 0:
        return np.zeros_like(img, dtype=np.uint8)

    comp_sizes = np.bincount(labeled.ravel())[1:]
    top_idx    = np.argsort(comp_sizes)[-3:]
    thresh_grow = np.percentile(img, grow_pct)
    grow_mask   = img >= thresh_grow

    pz = max(1, int(np.ceil(pad_mm / dz)))
    py = max(1, int(np.ceil(pad_mm / dy)))
    px = max(1, int(np.ceil(pad_mm / dx)))
    D, H, W = img.shape
    best_pred, best_size = None, 0

    for comp_id in (top_idx + 1):
        seed   = (labeled == comp_id)
        coords = np.array(np.where(seed))
        z0 = max(0, coords[0].min()-pz); z1 = min(D, coords[0].max()+pz+1)
        y0 = max(0, coords[1].min()-py); y1 = min(H, coords[1].max()+py+1)
        x0 = max(0, coords[2].min()-px); x1 = min(W, coords[2].max()+px+1)

        gc    = grow_mask[z0:z1, y0:y1, x0:x1]
        sc    = seed[z0:z1, y0:y1, x0:x1]
        gl, _ = ndimage.label(gc)
        sls   = np.unique(gl[sc]); sls = sls[sls > 0]
        if len(sls) == 0:
            continue

        fc = np.zeros_like(gc, dtype=bool)
        for sl in sls:
            fc |= (gl == sl)
        if fc.sum() > best_size:
            best_size = fc.sum()
            best_pred = np.zeros((D, H, W), dtype=bool)
            best_pred[z0:z1, y0:y1, x0:x1] = fc

    return best_pred.astype(np.uint8) if best_pred is not None else np.zeros_like(img, dtype=np.uint8)


# ─────────────────────────────────────────────────────────────────────────────
# 5.  Interactive segmentation editor GUI
# ─────────────────────────────────────────────────────────────────────────────

def launch_editor(vol: np.ndarray, mask: np.ndarray,
                  case_name: str, zooms: tuple) -> tuple[np.ndarray, bool]:
    """
    Open an interactive tkinter editor for reviewing and correcting a
    3-D segmentation mask.

    Parameters
    ----------
    vol       : (D, H, W) float array — MRI volume
    mask      : (D, H, W) uint8 array — predicted segmentation (modified in-place copy)
    case_name : string shown in the window title
    zooms     : (dz, dy, dx) voxel spacing in mm

    Returns
    -------
    edited_mask : (D, H, W) uint8 — final mask after user edits
    saved       : bool — True if user clicked Save, False if Skip
    """
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        print("  [WARN] tkinter not available — skipping GUI review.")
        return mask, False

    # ── normalise volume to uint8 for display ────────────────────────────────
    p1, p99 = np.percentile(vol, 1), np.percentile(vol, 99)
    vol_u8  = np.clip((vol - p1) / (p99 - p1 + 1e-6) * 255, 0, 255).astype(np.uint8)

    D, H, W = vol.shape
    edited  = mask.copy().astype(np.uint8)
    undo_stack: list[np.ndarray] = []   # stores full mask snapshots (up to 20)
    result  = {"saved": False}

    # ── helper: push undo snapshot ───────────────────────────────────────────
    def push_undo():
        if len(undo_stack) >= 20:
            undo_stack.pop(0)
        undo_stack.append(edited.copy())

    # ── helper: render one view ──────────────────────────────────────────────
    def render_view(axis: int, idx: int, brush_r: int, cursor_pos,
                    opacity: float) -> "np.ndarray":
        """
        Return an RGBA (H, W, 4) uint8 image for the given axis/slice.
        axis: 0=axial (z), 1=coronal (y), 2=sagittal (x)
        """
        if axis == 0:   # axial — slice along z
            img_sl  = vol_u8[idx, :, :]          # (H, W)
            seg_sl  = edited[idx, :, :]
        elif axis == 1: # coronal — slice along y
            img_sl  = vol_u8[:, idx, :]          # (D, W)
            seg_sl  = edited[:, idx, :]
        else:           # sagittal — slice along x
            img_sl  = vol_u8[:, :, idx]          # (D, H)
            seg_sl  = edited[:, :, idx]

        # Build RGBA
        rgb  = np.stack([img_sl, img_sl, img_sl], axis=-1)
        rgba = np.concatenate([rgb, np.full((*img_sl.shape, 1), 255, dtype=np.uint8)], axis=-1)

        # Overlay mask in green
        green_mask = seg_sl > 0
        alpha_val  = int(opacity * 255)
        rgba[green_mask, 0] = np.clip(rgba[green_mask, 0].astype(int) * (1 - opacity * 0.5), 0, 255).astype(np.uint8)
        rgba[green_mask, 1] = np.clip(rgba[green_mask, 1].astype(int) * (1 - opacity * 0.5) + 180 * opacity, 0, 255).astype(np.uint8)
        rgba[green_mask, 2] = np.clip(rgba[green_mask, 2].astype(int) * (1 - opacity * 0.5), 0, 255).astype(np.uint8)

        # Draw brush cursor circle
        if cursor_pos is not None:
            cy, cx = cursor_pos
            rr, cc = _circle_coords(cy, cx, brush_r, img_sl.shape)
            rgba[rr, cc, 0] = 255
            rgba[rr, cc, 1] = 255
            rgba[rr, cc, 2] = 0
            rgba[rr, cc, 3] = 200

        return rgba

    def _circle_coords(cy, cx, r, shape):
        """Return (rows, cols) of pixels on the perimeter of a circle."""
        H, W = shape
        angles = np.linspace(0, 2 * np.pi, max(8, r * 8))
        rr = np.clip((cy + r * np.sin(angles)).astype(int), 0, H - 1)
        cc = np.clip((cx + r * np.cos(angles)).astype(int), 0, W - 1)
        return rr, cc

    def _disk_coords(cy, cx, r, shape):
        """Return (rows, cols) of all pixels inside a filled circle."""
        H, W = shape
        y, x = np.ogrid[:H, :W]
        dist  = (y - cy) ** 2 + (x - cx) ** 2
        mask2 = dist <= r ** 2
        return np.where(mask2)

    # ── build the window ─────────────────────────────────────────────────────
    root = tk.Tk()
    root.title(f"Segmentation Editor — {case_name}")
    root.configure(bg="#1e1e1e")

    # State variables
    brush_r_var  = tk.IntVar(value=5)
    opacity_var  = tk.DoubleVar(value=0.55)
    mode_var     = tk.StringVar(value="paint")   # "paint" or "erase"
    slice_vars   = [tk.IntVar(value=D // 2),
                    tk.IntVar(value=H // 2),
                    tk.IntVar(value=W // 2)]
    axis_labels  = ["Axial (Z)", "Coronal (Y)", "Sagittal (X)"]
    axis_maxes   = [D - 1, H - 1, W - 1]

    # Canvas size — scale so longest dim = 320 px
    CANVAS_PX = 320

    def canvas_size(axis):
        if axis == 0:   return H, W
        elif axis == 1: return D, W
        else:           return D, H

    def scale_factors(axis):
        sh, sw = canvas_size(axis)
        sy = CANVAS_PX / sh
        sx = CANVAS_PX / sw
        s  = min(sy, sx)
        return s, s

    # ── PhotoImage cache ─────────────────────────────────────────────────────
    photo_images = [None, None, None]   # one per axis
    cursor_pos   = [None, None, None]

    def refresh_canvas(axis):
        from PIL import Image as PILImage, ImageTk
        idx    = slice_vars[axis].get()
        br     = brush_r_var.get()
        op     = opacity_var.get()
        rgba   = render_view(axis, idx, br, cursor_pos[axis], op)
        sh, sw = canvas_size(axis)
        sy, sx = scale_factors(axis)
        new_h  = int(sh * sy)
        new_w  = int(sw * sx)
        pil_img = PILImage.fromarray(rgba, 'RGBA').resize((new_w, new_h), PILImage.NEAREST)
        photo   = ImageTk.PhotoImage(pil_img)
        photo_images[axis] = photo   # keep reference
        canvases[axis].config(width=new_w, height=new_h)
        canvases[axis].delete("all")
        canvases[axis].create_image(0, 0, anchor="nw", image=photo)

    def refresh_all():
        for ax in range(3):
            refresh_canvas(ax)
        # Update voxel count label
        n_vox = int((edited > 0).sum())
        vox_label.config(text=f"Graft voxels: {n_vox:,}")

    # ── layout ───────────────────────────────────────────────────────────────
    top_frame    = tk.Frame(root, bg="#1e1e1e")
    top_frame.pack(side="top", fill="x", padx=8, pady=4)

    canvas_frame = tk.Frame(root, bg="#1e1e1e")
    canvas_frame.pack(side="top", fill="both", expand=True, padx=8, pady=4)

    ctrl_frame   = tk.Frame(root, bg="#1e1e1e")
    ctrl_frame.pack(side="top", fill="x", padx=8, pady=4)

    btn_frame    = tk.Frame(root, bg="#1e1e1e")
    btn_frame.pack(side="top", fill="x", padx=8, pady=8)

    # Title
    tk.Label(top_frame, text=f"Case: {case_name}",
             bg="#1e1e1e", fg="#e0e0e0", font=("Helvetica", 13, "bold")).pack(side="left")
    vox_label = tk.Label(top_frame, text="", bg="#1e1e1e", fg="#80c080",
                         font=("Helvetica", 11))
    vox_label.pack(side="right")

    # Three canvases
    canvases = []
    for ax in range(3):
        col_frame = tk.Frame(canvas_frame, bg="#1e1e1e")
        col_frame.pack(side="left", padx=6)
        tk.Label(col_frame, text=axis_labels[ax],
                 bg="#1e1e1e", fg="#aaaaaa", font=("Helvetica", 10)).pack()
        c = tk.Canvas(col_frame, bg="#000000", cursor="crosshair",
                      highlightthickness=1, highlightbackground="#444444")
        c.pack()
        canvases.append(c)
        # Slice slider below canvas
        sl = tk.Scale(col_frame, from_=0, to=axis_maxes[ax],
                      orient="horizontal", variable=slice_vars[ax],
                      bg="#1e1e1e", fg="#cccccc", troughcolor="#333333",
                      highlightthickness=0, length=CANVAS_PX,
                      command=lambda v, a=ax: refresh_canvas(a))
        sl.pack()

    # Controls row
    tk.Label(ctrl_frame, text="Brush size:", bg="#1e1e1e", fg="#cccccc").pack(side="left")
    tk.Scale(ctrl_frame, from_=1, to=30, orient="horizontal",
             variable=brush_r_var, bg="#1e1e1e", fg="#cccccc",
             troughcolor="#333333", highlightthickness=0, length=120,
             command=lambda v: refresh_all()).pack(side="left", padx=(0, 16))

    tk.Label(ctrl_frame, text="Opacity:", bg="#1e1e1e", fg="#cccccc").pack(side="left")
    tk.Scale(ctrl_frame, from_=0.0, to=1.0, resolution=0.05, orient="horizontal",
             variable=opacity_var, bg="#1e1e1e", fg="#cccccc",
             troughcolor="#333333", highlightthickness=0, length=120,
             command=lambda v: refresh_all()).pack(side="left", padx=(0, 16))

    tk.Label(ctrl_frame, text="Mode:", bg="#1e1e1e", fg="#cccccc").pack(side="left")
    for label, val, color in [("Paint (L-click)", "paint", "#4caf50"),
                               ("Erase (R-click)", "erase", "#f44336")]:
        tk.Radiobutton(ctrl_frame, text=label, variable=mode_var, value=val,
                       bg="#1e1e1e", fg=color, selectcolor="#333333",
                       activebackground="#1e1e1e").pack(side="left", padx=4)

    # Hint label
    tk.Label(ctrl_frame,
             text="  Left-drag=paint  Right-drag=erase  Scroll=slice",
             bg="#1e1e1e", fg="#666666", font=("Helvetica", 9)).pack(side="left", padx=8)

    # Buttons
    btn_style = dict(font=("Helvetica", 11, "bold"), relief="flat",
                     padx=16, pady=6, cursor="hand2")

    def on_save():
        result["saved"] = True
        root.quit()

    def on_skip():
        result["saved"] = False
        root.quit()

    def on_undo():
        if undo_stack:
            prev = undo_stack.pop()
            edited[:] = prev
            refresh_all()

    tk.Button(btn_frame, text="Save & Next →", bg="#4caf50", fg="white",
              command=on_save, **btn_style).pack(side="left", padx=6)
    tk.Button(btn_frame, text="Undo", bg="#ff9800", fg="white",
              command=on_undo, **btn_style).pack(side="left", padx=6)
    tk.Button(btn_frame, text="Skip (no edit)", bg="#607d8b", fg="white",
              command=on_skip, **btn_style).pack(side="left", padx=6)

    # Keyboard shortcuts
    root.bind("<Return>", lambda e: on_save())
    root.bind("<Escape>", lambda e: on_skip())
    root.bind("<Control-z>", lambda e: on_undo())

    # ── mouse event handlers ─────────────────────────────────────────────────
    def _canvas_to_voxel(axis, cx_px, cy_px):
        """Convert canvas pixel coords → voxel (row, col) in the slice plane."""
        sh, sw = canvas_size(axis)
        sy, sx = scale_factors(axis)
        new_h  = int(sh * sy)
        new_w  = int(sw * sx)
        row = int(cy_px / new_h * sh)
        col = int(cx_px / new_w * sw)
        return (np.clip(row, 0, sh - 1), np.clip(col, 0, sw - 1))

    def _apply_brush(axis, row, col, paint: bool):
        """Paint or erase a disk of voxels at the given slice-plane position."""
        push_undo()
        idx = slice_vars[axis].get()
        sh, sw = canvas_size(axis)
        rr, cc = _disk_coords(row, col, brush_r_var.get(), (sh, sw))
        if axis == 0:
            edited[idx, rr, cc] = 1 if paint else 0
        elif axis == 1:
            edited[rr, idx, cc] = 1 if paint else 0
        else:
            edited[rr, cc, idx] = 1 if paint else 0

    def make_handlers(axis):
        def on_motion(event):
            row, col = _canvas_to_voxel(axis, event.x, event.y)
            cursor_pos[axis] = (row, col)
            refresh_canvas(axis)

        def on_leave(event):
            cursor_pos[axis] = None
            refresh_canvas(axis)

        def on_b1_press(event):
            row, col = _canvas_to_voxel(axis, event.x, event.y)
            _apply_brush(axis, row, col, paint=True)
            refresh_all()

        def on_b3_press(event):
            row, col = _canvas_to_voxel(axis, event.x, event.y)
            _apply_brush(axis, row, col, paint=False)
            refresh_all()

        def on_b1_drag(event):
            row, col = _canvas_to_voxel(axis, event.x, event.y)
            cursor_pos[axis] = (row, col)
            _apply_brush(axis, row, col, paint=True)
            refresh_all()

        def on_b3_drag(event):
            row, col = _canvas_to_voxel(axis, event.x, event.y)
            cursor_pos[axis] = (row, col)
            _apply_brush(axis, row, col, paint=False)
            refresh_all()

        def on_scroll(event):
            delta = -1 if (event.delta > 0 or event.num == 4) else 1
            cur   = slice_vars[axis].get()
            new   = np.clip(cur + delta, 0, axis_maxes[axis])
            slice_vars[axis].set(new)
            refresh_canvas(axis)

        canvases[axis].bind("<Motion>",         on_motion)
        canvases[axis].bind("<Leave>",          on_leave)
        canvases[axis].bind("<ButtonPress-1>",  on_b1_press)
        canvases[axis].bind("<ButtonPress-3>",  on_b3_press)
        canvases[axis].bind("<B1-Motion>",      on_b1_drag)
        canvases[axis].bind("<B3-Motion>",      on_b3_drag)
        canvases[axis].bind("<MouseWheel>",     on_scroll)
        canvases[axis].bind("<Button-4>",       on_scroll)   # Linux scroll up
        canvases[axis].bind("<Button-5>",       on_scroll)   # Linux scroll down

    for ax in range(3):
        make_handlers(ax)

    # ── initial render ───────────────────────────────────────────────────────
    # Jump to the slice with the most graft voxels
    counts = edited.sum(axis=(1, 2))
    if counts.max() > 0:
        slice_vars[0].set(int(np.argmax(counts)))
    counts_y = edited.sum(axis=(0, 2))
    if counts_y.max() > 0:
        slice_vars[1].set(int(np.argmax(counts_y)))
    counts_x = edited.sum(axis=(0, 1))
    if counts_x.max() > 0:
        slice_vars[2].set(int(np.argmax(counts_x)))

    refresh_all()
    root.mainloop()
    root.destroy()

    return edited, result["saved"]


# ─────────────────────────────────────────────────────────────────────────────
# 6.  Mask → .seg.nrrd
# ─────────────────────────────────────────────────────────────────────────────

def mask_to_seg_nrrd(mask: np.ndarray,
                     ref_nii: nib.Nifti1Image,
                     out_path: Path,
                     segment_name: str,
                     parent_cage: str,
                     ear_tag: str):
    """Write a 3D Slicer-compatible .seg.nrrd file."""
    affine     = ref_nii.affine
    vox2mm     = affine[:3, :3]
    origin_ras = affine[:3, 3]
    ras2lps    = np.diag([-1., -1., 1.])

    space_directions = (ras2lps @ vox2mm).T
    space_origin     = ras2lps @ origin_ras

    color_r, color_g, color_b = 0.501961, 0.682353, 0.501961

    header = {
        "type":             "unsigned char",
        "dimension":        3,
        "space":            "left-posterior-superior",
        "sizes":            list(mask.shape),
        "space directions": [list(row) for row in space_directions],
        "kinds":            ["domain", "domain", "domain"],
        "endian":           "little",
        "encoding":         "gzip",
        "space origin":     list(space_origin),
        "Segmentation_ContainedRepresentationNames": "Binary labelmap|Closed surface|",
        "Segmentation_ConversionParameters":         "Decimation factor:0.0|Smoothing factor:0.5|",
        "Segmentation_MasterRepresentation":         "Binary labelmap",
        "Segmentation_ReferenceImageExtentOffset":   "0 0 0",
        "Segment0_Color":              f"{color_r} {color_g} {color_b}",
        "Segment0_ColorAutoGenerated": "1",
        "Segment0_Extent":
            f"0 {mask.shape[0]-1} 0 {mask.shape[1]-1} 0 {mask.shape[2]-1}",
        "Segment0_ID":               "Segment_1",
        "Segment0_LabelValue":       "1",
        "Segment0_Layer":            "0",
        "Segment0_Name":             segment_name,
        "Segment0_NameAutoGenerated": "0",
        "Segment0_Tags":
            f"ParentCage:{parent_cage}|EarTag:{ear_tag}|Terminology:"
            "Segmentation category and type - 3D Slicer General Anatomy list"
            "~SRT^T-D0050^Tissue~SRT^T-D0050^Tissue~^^~"
            "Anatomic codes - DICOM master list~^^~^^|",
    }

    nrrd.write(str(out_path), mask.astype(np.uint8), header, index_order='C')
    print(f"    Saved: {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
# 7.  Dice helper
# ─────────────────────────────────────────────────────────────────────────────

def compute_dice(pred: np.ndarray, gt: np.ndarray) -> float:
    smooth = 1.0
    inter  = (pred.astype(bool) & gt.astype(bool)).sum()
    return (2.0 * inter + smooth) / (pred.sum() + gt.sum() + smooth)


def find_gt_label(dcm_path: Path) -> Path | None:
    folder = dcm_path.parent
    candidates = (
        list(folder.glob("*_seg.nii.gz")) +
        list(folder.glob("*_label.nii.gz")) +
        list(folder.glob("*_mask.nii.gz")) +
        [folder / "label.nii.gz",
         folder / "seg.nii.gz",
         folder / "mask.nii.gz",
         folder / "gt.nii.gz"]
    )
    for p in candidates:
        if p.exists():
            return p
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 8.  Per-case processing
# ─────────────────────────────────────────────────────────────────────────────

def process_folder(case_folder: Path, args) -> dict:
    """Process one case folder. Returns a summary dict for the CSV."""
    row = {
        "case_name":          "",
        "parent_cage":        "",
        "ear_tag":            "",
        "dicom_file":         "",
        "series_description": "",
        "n_slices":           "",
        "voxel_spacing_mm":   "",
        "graft_voxels":       "",
        "graft_volume_mm3":   "",
        "dice":               "",
        "edited":             "no",
        "status":             "failed",
        "nrrd_path":          "",
    }

    print(f"\n{'─'*60}")
    print(f"  Folder: {case_folder}")

    # ── pick best DICOM ───────────────────────────────────────────────────────
    dcm_path = select_best_dicom(case_folder)
    if dcm_path is None:
        print(f"  [SKIP] No valid MR DICOM found.")
        row["status"] = "skipped_no_dicom"
        return row

    row["dicom_file"] = str(dcm_path)
    print(f"  DICOM: {dcm_path.name}")

    # ── read metadata ─────────────────────────────────────────────────────────
    try:
        ds = pydicom.dcmread(str(dcm_path), stop_before_pixels=True)
    except Exception as e:
        print(f"  [SKIP] Cannot read DICOM: {e}")
        row["status"] = "skipped_unreadable"
        return row

    series_desc = getattr(ds, 'SeriesDescription', '')
    n_frames    = int(getattr(ds, 'NumberOfFrames', 1))
    row["series_description"] = series_desc
    row["n_slices"]           = n_frames

    # ── parse case name ───────────────────────────────────────────────────────
    parent_cage = ear_tag = None
    if series_desc:
        try:
            parent_cage, ear_tag = parse_series_description(series_desc)
        except ValueError:
            pass

    if parent_cage is None:
        case_name   = fallback_name_from_folder(dcm_path)
        parent_cage = case_name
        ear_tag     = "unknown"
        src = f"SeriesDescription='{series_desc}'" if series_desc else "no SeriesDescription"
        print(f"  [FALLBACK] Could not parse PA/EarTag from {src}")
        print(f"  Case: {case_name}  (named from folder)")
    else:
        case_name = f"{parent_cage}_{ear_tag}"
        print(f"  Case: {case_name}  (ParentCage={parent_cage}, EarTag={ear_tag})")

    row["case_name"]   = case_name
    row["parent_cage"] = parent_cage
    row["ear_tag"]     = ear_tag

    # ── output paths ──────────────────────────────────────────────────────────
    out_dir   = case_folder / case_name
    out_dir.mkdir(exist_ok=True)
    nii_path  = out_dir / f"{case_name}.nii.gz"
    nrrd_path = out_dir / f"{case_name}.seg.nrrd"
    row["nrrd_path"] = str(nrrd_path)

    # ── review_only mode: load existing .seg.nrrd and open editor ─────────────
    if args.review_only:
        if not nrrd_path.exists():
            print(f"  [SKIP] No existing .seg.nrrd found for review: {nrrd_path.name}")
            row["status"] = "skipped_no_nrrd"
            return row
        if not nii_path.exists():
            print(f"  [SKIP] NIfTI not found for review (need --keep_nifti on original run): {nii_path.name}")
            row["status"] = "skipped_no_nifti"
            return row
        print(f"  [REVIEW ONLY] Loading existing segmentation for editing ...")
        ref_nii = nib.load(str(nii_path))
        vol     = ref_nii.get_fdata().astype(np.float32)
        mask_data, _ = nrrd.read(str(nrrd_path))
        mask    = mask_data.astype(np.uint8)
        zooms   = ref_nii.header.get_zooms()[:3]
        edited_mask, saved = launch_editor(vol, mask, case_name, zooms)
        if saved:
            mask_to_seg_nrrd(edited_mask, ref_nii, nrrd_path,
                             segment_name=f"Graft {case_name}",
                             parent_cage=parent_cage, ear_tag=ear_tag)
            _fill_voxel_stats(row, edited_mask, ref_nii)
            row["edited"] = "yes"
            row["status"] = "reviewed"
        else:
            _fill_voxel_stats(row, mask, ref_nii)
            row["status"] = "review_skipped"
        return row

    # ── skip if already done ──────────────────────────────────────────────────
    if nrrd_path.exists() and not args.overwrite:
        print(f"  [SKIP] Already exists: {nrrd_path.name}  (use --overwrite to redo)")
        row["status"] = "skipped_exists"
        try:
            data, _ = nrrd.read(str(nrrd_path))
            ref_nii = nib.load(str(nii_path)) if nii_path.exists() else None
            _fill_voxel_stats(row, data, ref_nii)
        except Exception:
            pass
        return row

    # ── DICOM → NIfTI ─────────────────────────────────────────────────────────
    print(f"  Converting DICOM → NIfTI ...")
    try:
        ref_nii = dicom_to_nifti(dcm_path, nii_path)
    except Exception as e:
        print(f"  [ERROR] DICOM conversion failed: {e}")
        row["status"] = "failed_conversion"
        return row

    zooms = ref_nii.header.get_zooms()[:3]
    row["voxel_spacing_mm"] = f"{zooms[0]:.4f} x {zooms[1]:.4f} x {zooms[2]:.4f}"

    # ── segmentation ──────────────────────────────────────────────────────────
    print(f"  Running segmentation ({args.model}) ...")
    try:
        if args.model == "nnunet":
            mask = run_nnunet(
                nii_path, out_dir,
                dataset_id  = args.nnunet_dataset,
                fold        = args.nnunet_fold,
                plans       = args.nnunet_plans,
                results_dir = args.nnunet_results,
                checkpoint  = args.nnunet_checkpoint,
            )
        else:
            mask = run_classical(nii_path)
    except Exception as e:
        print(f"  [ERROR] Segmentation failed: {e}")
        row["status"] = "failed_segmentation"
        return row

    # ── save original prediction as audit trail ───────────────────────────────
    if args.keep_nifti:
        orig_pred_path = out_dir / f"{case_name}_pred_original.nii.gz"
        orig_nii = nib.Nifti1Image(mask.astype(np.uint8), ref_nii.affine)
        nib.save(orig_nii, str(orig_pred_path))

    # ── interactive review ────────────────────────────────────────────────────
    final_mask = mask
    if args.review:
        vol = ref_nii.get_fdata().astype(np.float32)
        print(f"  Opening editor for {case_name} ...")
        edited_mask, saved = launch_editor(vol, mask, case_name, zooms)
        if saved:
            final_mask = edited_mask
            row["edited"] = "yes"
            print(f"  Edits saved.")
        else:
            print(f"  Skipped editing — using original prediction.")

    # ── voxel stats ───────────────────────────────────────────────────────────
    _fill_voxel_stats(row, final_mask, ref_nii)

    # ── Dice vs GT (if available) ─────────────────────────────────────────────
    gt_path = find_gt_label(dcm_path)
    if gt_path is not None:
        try:
            gt_nib = nib.load(str(gt_path))
            gt     = (gt_nib.get_fdata() == 1).astype(np.uint8)
            if gt.shape == final_mask.shape:
                dice = compute_dice(final_mask, gt)
                row["dice"] = f"{dice:.4f}"
                print(f"  Dice vs GT: {dice:.4f}")
            else:
                print(f"  [WARN] GT shape {gt.shape} != pred shape {final_mask.shape}, skipping Dice")
        except Exception as e:
            print(f"  [WARN] Could not compute Dice: {e}")

    # ── write .seg.nrrd ───────────────────────────────────────────────────────
    print(f"  Writing .seg.nrrd ...")
    try:
        mask_to_seg_nrrd(
            final_mask, ref_nii, nrrd_path,
            segment_name = f"Graft {case_name}",
            parent_cage  = parent_cage,
            ear_tag      = ear_tag,
        )
    except Exception as e:
        print(f"  [ERROR] seg.nrrd write failed: {e}")
        row["status"] = "failed_nrrd"
        return row

    # ── cleanup ───────────────────────────────────────────────────────────────
    if not args.keep_nifti:
        nii_path.unlink(missing_ok=True)

    row["status"] = "success"
    return row


def _fill_voxel_stats(row: dict, mask: np.ndarray, ref_nii):
    n_vox = int((mask > 0).sum())
    row["graft_voxels"] = n_vox
    if ref_nii is not None:
        zooms   = ref_nii.header.get_zooms()[:3]
        vox_vol = float(zooms[0]) * float(zooms[1]) * float(zooms[2])
        row["graft_volume_mm3"] = f"{n_vox * vox_vol:.2f}"
        row["voxel_spacing_mm"] = f"{zooms[0]:.4f} x {zooms[1]:.4f} x {zooms[2]:.4f}"


# ─────────────────────────────────────────────────────────────────────────────
# 9.  Summary CSV
# ─────────────────────────────────────────────────────────────────────────────

CSV_FIELDS = [
    "case_name", "parent_cage", "ear_tag",
    "dicom_file", "series_description",
    "n_slices", "voxel_spacing_mm",
    "graft_voxels", "graft_volume_mm3",
    "dice", "edited", "status", "nrrd_path",
]


def write_summary_csv(rows: list[dict], out_path: Path):
    with open(str(out_path), "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in CSV_FIELDS})
    print(f"\n  Summary CSV: {out_path}")


def print_summary_table(rows: list[dict]):
    print(f"\n{'═'*90}")
    print(f"  {'CASE':<25} {'VOXELS':>8}  {'VOL(mm³)':>10}  {'DICE':>6}  {'EDITED':>6}  STATUS")
    print(f"  {'─'*25}  {'─'*8}  {'─'*10}  {'─'*6}  {'─'*6}  {'─'*15}")
    for r in rows:
        name   = r.get("case_name", "")[:25]
        vox    = str(r.get("graft_voxels", ""))
        vol    = str(r.get("graft_volume_mm3", ""))
        dice   = str(r.get("dice", ""))
        edited = str(r.get("edited", ""))
        stat   = r.get("status", "")
        print(f"  {name:<25}  {vox:>8}  {vol:>10}  {dice:>6}  {edited:>6}  {stat}")
    print(f"{'═'*90}")


# ─────────────────────────────────────────────────────────────────────────────
# 10.  Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Graft segmentation: DICOM folder → .seg.nrrd (3D Slicer compatible)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Checkpoint selection (--nnunet_checkpoint):
  checkpoint_best.pth    Best validation epoch so far — use during or after training (DEFAULT)
  checkpoint_final.pth   Final epoch — use after training fully completes
  checkpoint_latest.pth  Most recent periodic save (every 50 epochs)
  <custom>.pth           Any manually copied snapshot, e.g.:
                           cp $nnUNet_results/.../fold_0/checkpoint_latest.pth \\
                              $nnUNet_results/.../fold_0/checkpoint_epoch_200_snapshot.pth

Review / editing workflow:
  --review               After each prediction, open the interactive editor.
                         Left-drag = paint voxels, Right-drag = erase voxels.
                         Scroll wheel = change slice. Save & Next / Skip / Undo.
  --review_only          Skip segmentation; re-open existing .seg.nrrd files for editing.
                         Requires --keep_nifti to have been set on the original run.
        """
    )
    parser.add_argument("--input", "-i", required=True,
        help="Root folder containing case subfolders with DICOM files")
    parser.add_argument("--model", choices=["nnunet", "classical"], default="nnunet",
        help="Segmentation model (default: nnunet)")
    parser.add_argument("--overwrite", action="store_true",
        help="Re-run even if .seg.nrrd already exists")
    parser.add_argument("--keep_nifti", action="store_true",
        help="Keep intermediate .nii.gz files (required for --review_only later)")
    parser.add_argument("--summary_csv", type=str, default=None,
        help="Path for summary CSV (default: <input>/segmentation_summary.csv)")
    parser.add_argument("--review", action="store_true",
        help="Open interactive editor after each prediction for manual correction")
    parser.add_argument("--review_only", action="store_true",
        help="Skip segmentation; re-edit existing .seg.nrrd files only")

    nnunet = parser.add_argument_group("nnU-Net options (--model nnunet)")
    nnunet.add_argument("--nnunet_dataset",    type=int, default=1,
        help="Dataset ID (default: 1)")
    nnunet.add_argument("--nnunet_fold",       type=int, default=0,
        help="Fold to use for inference (default: 0)")
    nnunet.add_argument("--nnunet_plans",      type=str, default="nnUNetResEncUNetLPlans",
        help="Plans identifier (default: nnUNetResEncUNetLPlans)")
    nnunet.add_argument("--nnunet_results",    type=str, default=None,
        help="Path to nnUNet_results folder (overrides env var)")
    nnunet.add_argument("--nnunet_checkpoint", type=str, default="checkpoint_best.pth",
        help=(
            "Checkpoint filename inside the fold folder. "
            "Default: checkpoint_best.pth (best epoch so far — works during AND after training). "
            "Use checkpoint_final.pth once training fully completes."
        ))

    args = parser.parse_args()

    root = Path(args.input)
    if not root.exists():
        print(f"ERROR: Input folder not found: {root}")
        sys.exit(1)

    case_folders = find_case_folders(root)
    if not case_folders:
        print(f"ERROR: No DICOM files found under {root}")
        sys.exit(1)

    print(f"Found {len(case_folders)} case folder(s) under {root}")
    if not args.review_only:
        print(f"Model:      {args.model}")
        if args.model == "nnunet":
            print(f"Checkpoint: {args.nnunet_checkpoint}")
    if args.review or args.review_only:
        print(f"Review GUI: enabled")
    print(f"Timestamp:  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    rows = []
    for folder in case_folders:
        row = process_folder(folder, args)
        rows.append(row)

    print_summary_table(rows)

    csv_path = Path(args.summary_csv) if args.summary_csv else root / "segmentation_summary.csv"
    write_summary_csv(rows, csv_path)

    n_ok     = sum(1 for r in rows if r["status"] in ("success", "reviewed"))
    n_edited = sum(1 for r in rows if r.get("edited") == "yes")
    n_skip   = sum(1 for r in rows if r["status"].startswith("skipped"))
    n_fail   = sum(1 for r in rows if r["status"].startswith("failed"))
    print(f"\n  Done.  Success: {n_ok}  (manually edited: {n_edited})  "
          f"Skipped: {n_skip}  Failed: {n_fail}")
    print(f"  Summary: {csv_path}")


if __name__ == "__main__":
    main()
