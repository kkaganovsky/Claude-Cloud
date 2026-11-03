#!/usr/bin/env python3
"""
prepare_pa_data.py
==================
Converts PA_XX .nrrd / .seg.nrrd files to nnU-Net NIfTI format and merges
them with the original Dataset001 (Assembloid/PCDH) cases into a new
Dataset002_GraftSeg.

Usage
-----
python prepare_pa_data.py \
    --pa_root "/Extreme SSD/MRI NeuralNetwork/PA_MRI_root" \
    --orig_raw "/Extreme SSD/MRI NeuralNetwork/nnUNet_raw/Dataset001_GraftSeg" \
    --out_raw  "/Extreme SSD/MRI NeuralNetwork/nnUNet_raw/Dataset002_GraftSeg"

The --pa_root folder must contain the two timepoint subfolders:
    PA_MRI_2m Slicer final segmented data/
    PA_MRI_4m Slicer segmented data/

Each timepoint subfolder contains one subfolder per animal, e.g.:
    PA_01_L1F/
        230001 T2_Anatomy_PA01_L1F.nrrd      <- image
        230001 T2_Anatomy_PA01_L1F.seg.nrrd  <- label

Dependencies
------------
    pip install SimpleITK nibabel numpy
"""

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

import numpy as np
import SimpleITK as sitk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TIMEPOINT_MAP = {
    "2m": "2mo",
    "4m": "4mo",
    "6m": "6mo",
    "2mo": "2mo",
    "4mo": "4mo",
    "6mo": "6mo",
}


def detect_timepoint(folder_name: str) -> str:
    """Extract timepoint label from a parent folder name."""
    folder_lower = folder_name.lower()
    for key, val in TIMEPOINT_MAP.items():
        # match e.g. "2m " or "_2m_" or "2mo"
        if re.search(rf"\b{re.escape(key)}\b", folder_lower):
            return val
    raise ValueError(f"Cannot detect timepoint from folder name: '{folder_name}'")


def find_image_and_label(animal_dir: Path):
    """
    Return (image_path, label_path) from an animal folder.
    Label = file ending in .seg.nrrd
    Image = any .nrrd that is NOT a .seg.nrrd
    """
    nrrd_files = list(animal_dir.glob("*.nrrd"))
    labels = [f for f in nrrd_files if f.name.endswith(".seg.nrrd")]
    images = [f for f in nrrd_files if not f.name.endswith(".seg.nrrd")]

    if len(labels) == 0:
        return None, None  # no label — skip
    if len(images) == 0:
        return None, None  # no image — skip
    if len(labels) > 1:
        print(f"  WARNING: multiple .seg.nrrd files in {animal_dir}, using first: {labels[0].name}")
    if len(images) > 1:
        print(f"  WARNING: multiple image .nrrd files in {animal_dir}, using first: {images[0].name}")

    return images[0], labels[0]


def nrrd_to_nifti(src: Path, dst: Path, binarize: bool = False):
    """Load a .nrrd file with SimpleITK and save as .nii.gz."""
    img = sitk.ReadImage(str(src))

    if binarize:
        # Convert any multi-label seg to binary (graft=1, background=0)
        arr = sitk.GetArrayFromImage(img)
        arr_bin = (arr > 0).astype(np.uint8)
        img_out = sitk.GetImageFromArray(arr_bin)
        img_out.CopyInformation(img)
    else:
        img_out = img

    sitk.WriteImage(img_out, str(dst))


def sanitize_case_id(animal_id: str, timepoint: str) -> str:
    """Build a clean nnU-Net case ID from animal ID and timepoint."""
    # Replace spaces and special chars with underscores
    safe = re.sub(r"[^A-Za-z0-9_]", "_", animal_id)
    return f"{safe}_{timepoint}"


# ---------------------------------------------------------------------------
# Main ingestion
# ---------------------------------------------------------------------------

def ingest_pa_cases(pa_root: Path, out_images: Path, out_labels: Path) -> list:
    """
    Walk PA_MRI_2m and PA_MRI_4m subfolders, convert each animal's
    .nrrd/.seg.nrrd pair to NIfTI, return list of case IDs added.
    """
    timepoint_folders = [d for d in pa_root.iterdir() if d.is_dir()]
    if not timepoint_folders:
        print(f"ERROR: No subfolders found in {pa_root}")
        sys.exit(1)

    added = []
    skipped = []

    for tp_folder in sorted(timepoint_folders):
        try:
            timepoint = detect_timepoint(tp_folder.name)
        except ValueError as e:
            print(f"  SKIP folder (cannot parse timepoint): {e}")
            continue

        print(f"\nProcessing timepoint folder: {tp_folder.name}  →  {timepoint}")

        animal_dirs = sorted([d for d in tp_folder.iterdir() if d.is_dir()])
        print(f"  Found {len(animal_dirs)} animal subfolders")

        for animal_dir in animal_dirs:
            animal_id = animal_dir.name
            image_path, label_path = find_image_and_label(animal_dir)

            if image_path is None:
                print(f"  SKIP {animal_id}: missing image or label")
                skipped.append(f"{animal_id}_{timepoint}")
                continue

            case_id = sanitize_case_id(animal_id, timepoint)

            dst_image = out_images / f"{case_id}_0000.nii.gz"
            dst_label = out_labels / f"{case_id}.nii.gz"

            if dst_image.exists() and dst_label.exists():
                print(f"  SKIP {case_id}: already converted")
                added.append(case_id)
                continue

            try:
                nrrd_to_nifti(image_path, dst_image, binarize=False)
                nrrd_to_nifti(label_path, dst_label, binarize=True)
                print(f"  OK   {case_id}  ({image_path.name})")
                added.append(case_id)
            except Exception as e:
                print(f"  ERROR {case_id}: {e}")
                skipped.append(case_id)

    print(f"\nPA_XX ingestion: {len(added)} converted, {len(skipped)} skipped")
    if skipped:
        print(f"  Skipped: {skipped}")

    return added


def copy_original_cases(orig_raw: Path, out_images: Path, out_labels: Path) -> list:
    """
    Copy all imagesTr/*.nii.gz and labelsTr/*.nii.gz from Dataset001
    into Dataset002, skipping any that already exist.
    """
    orig_images = orig_raw / "imagesTr"
    orig_labels = orig_raw / "labelsTr"

    if not orig_images.exists():
        print(f"WARNING: Original imagesTr not found at {orig_images} — skipping original cases")
        return []

    added = []
    image_files = sorted(orig_images.glob("*_0000.nii.gz"))
    print(f"\nCopying {len(image_files)} original cases from Dataset001...")

    for img_file in image_files:
        # Derive case ID: strip _0000.nii.gz suffix
        case_id = img_file.name.replace("_0000.nii.gz", "")
        lbl_file = orig_labels / f"{case_id}.nii.gz"

        if not lbl_file.exists():
            print(f"  SKIP {case_id}: label not found in Dataset001")
            continue

        dst_img = out_images / img_file.name
        dst_lbl = out_labels / lbl_file.name

        if dst_img.exists() and dst_lbl.exists():
            added.append(case_id)
            continue

        shutil.copy2(img_file, dst_img)
        shutil.copy2(lbl_file, dst_lbl)
        added.append(case_id)

    print(f"  Copied {len(added)} original cases")
    return added


def write_dataset_json(out_raw: Path, all_case_ids: list):
    """Write nnU-Net dataset.json for Dataset002."""
    dataset = {
        "channel_names": {"0": "MRI"},
        "labels": {"background": 0, "graft": 1},
        "numTraining": len(all_case_ids),
        "file_ending": ".nii.gz",
        "name": "Dataset002_GraftSeg",
        "description": "Combined Assembloid/PCDH + PA_XX graft segmentation dataset",
        "reference": "",
        "licence": "",
        "release": "1.0",
        "overwrite_image_reader_writer": "SimpleITKIO"
    }

    out_path = out_raw / "dataset.json"
    with open(out_path, "w") as f:
        json.dump(dataset, f, indent=2)
    print(f"\nWrote dataset.json ({len(all_case_ids)} training cases) → {out_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Prepare PA_XX data for nnU-Net Dataset002")
    parser.add_argument("--pa_root", required=True,
                        help="Root folder containing 'PA_MRI_2m ...' and 'PA_MRI_4m ...' subfolders")
    parser.add_argument("--orig_raw", required=True,
                        help="Path to original Dataset001_GraftSeg nnUNet_raw folder")
    parser.add_argument("--out_raw", required=True,
                        help="Output path for Dataset002_GraftSeg nnUNet_raw folder")
    args = parser.parse_args()

    pa_root = Path(args.pa_root)
    orig_raw = Path(args.orig_raw)
    out_raw = Path(args.out_raw)

    # Create output folder structure
    out_images = out_raw / "imagesTr"
    out_labels = out_raw / "labelsTr"
    out_images.mkdir(parents=True, exist_ok=True)
    out_labels.mkdir(parents=True, exist_ok=True)
    (out_raw / "imagesTs").mkdir(exist_ok=True)
    (out_raw / "labelsTs").mkdir(exist_ok=True)

    print("=" * 60)
    print("Step 1: Ingest PA_XX cases")
    print("=" * 60)
    pa_cases = ingest_pa_cases(pa_root, out_images, out_labels)

    print("\n" + "=" * 60)
    print("Step 2: Copy original Dataset001 cases")
    print("=" * 60)
    orig_cases = copy_original_cases(orig_raw, out_images, out_labels)

    all_cases = pa_cases + orig_cases
    print(f"\nTotal cases in Dataset002: {len(all_cases)}")
    print(f"  PA_XX cases:    {len(pa_cases)}")
    print(f"  Original cases: {len(orig_cases)}")

    print("\n" + "=" * 60)
    print("Step 3: Write dataset.json")
    print("=" * 60)
    write_dataset_json(out_raw, all_cases)

    print("\nDone. Next steps:")
    print("  1. Run generate_splits.py to create animal-level 5-fold splits")
    print("  2. Run: nnUNetv2_plan_and_preprocess -d 2 -c 3d_fullres --verify_dataset_integrity")
    print("  3. Copy splits_final.json to $nnUNet_preprocessed/Dataset002_GraftSeg/")
    print("  4. Run: bash run_training.sh")


if __name__ == "__main__":
    main()
