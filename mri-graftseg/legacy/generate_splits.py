#!/usr/bin/env python3
"""
generate_splits.py
==================
Generates an animal-level 5-fold cross-validation split for nnU-Net,
ensuring:
  1. All scans from one animal are in the same fold (no animal leakage)
  2. An animal's 2mo scan never trains while its 4mo scan validates
     (no temporal leakage)
  3. Animals with both timepoints are distributed evenly across folds

Output: splits_final.json in nnU-Net format, to be placed at:
    $nnUNet_preprocessed/Dataset002_GraftSeg/splits_final.json

Usage
-----
python generate_splits.py \
    --imagesTr "/Extreme SSD/MRI NeuralNetwork/nnUNet_raw/Dataset002_GraftSeg/imagesTr" \
    --out      "/Extreme SSD/MRI NeuralNetwork/nnUNet_preprocessed/Dataset002_GraftSeg/splits_final.json" \
    --n_folds  5 \
    --seed     42
"""

import argparse
import json
import re
import random
from collections import defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TIMEPOINT_PATTERN = re.compile(r"_(2mo|4mo|6mo)$")


def parse_case_id(filename: str) -> tuple:
    """
    Parse a case filename into (animal_id, timepoint).

    Examples:
        PA_01_L1F_2mo_0000.nii.gz  ->  ('PA_01_L1F', '2mo')
        Assembloid03_L1F_2mo12192022_0000.nii.gz  ->  ('Assembloid03_L1F', '2mo12192022')

    Strategy:
        - Strip _0000.nii.gz suffix to get case_id
        - Try to match known timepoint suffixes (_2mo, _4mo, _6mo) at end
        - If matched: animal = everything before, timepoint = matched group
        - If not matched: animal = case_id, timepoint = 'unknown'
    """
    # Strip channel suffix and extension
    case_id = filename.replace("_0000.nii.gz", "").replace(".nii.gz", "")

    m = TIMEPOINT_PATTERN.search(case_id)
    if m:
        timepoint = m.group(1)
        animal_id = case_id[:m.start()]
    else:
        # Fall back: try to find embedded timepoint like "2mo" anywhere in name
        embedded = re.search(r"(2mo|4mo|6mo)", case_id)
        if embedded:
            timepoint = embedded.group(1)
            # Use everything up to the timepoint as animal ID
            animal_id = case_id[:embedded.start()].rstrip("_")
        else:
            timepoint = "unknown"
            animal_id = case_id

    return case_id, animal_id, timepoint


def assign_folds_stratified(animals: list, n_folds: int, rng: random.Random) -> dict:
    """
    Assign animals to folds with stratification by timepoint availability.

    animals: list of (animal_id, timepoints_set) tuples
    Returns: dict {animal_id: fold_index}
    """
    # Separate animals by whether they have multiple timepoints
    multi_tp = [(a, tp) for a, tp in animals if len(tp) > 1]
    single_tp = [(a, tp) for a, tp in animals if len(tp) == 1]

    # Shuffle within each stratum
    rng.shuffle(multi_tp)
    rng.shuffle(single_tp)

    assignments = {}
    fold_counts = [0] * n_folds

    def assign_next(animal_id):
        # Assign to the fold with fewest animals (round-robin with balance)
        fold = fold_counts.index(min(fold_counts))
        assignments[animal_id] = fold
        fold_counts[fold] += 1

    # Distribute multi-timepoint animals first (more constrained)
    for animal_id, _ in multi_tp:
        assign_next(animal_id)

    # Then single-timepoint animals
    for animal_id, _ in single_tp:
        assign_next(animal_id)

    return assignments


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate animal-level 5-fold splits for nnU-Net")
    parser.add_argument("--imagesTr", required=True,
                        help="Path to Dataset002_GraftSeg/imagesTr folder")
    parser.add_argument("--out", required=True,
                        help="Output path for splits_final.json")
    parser.add_argument("--n_folds", type=int, default=5,
                        help="Number of folds (default: 5)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducibility (default: 42)")
    args = parser.parse_args()

    imagesTr = Path(args.imagesTr)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------------
    # 1. Parse all case IDs
    # -----------------------------------------------------------------------
    image_files = sorted(imagesTr.glob("*_0000.nii.gz"))
    if not image_files:
        print(f"ERROR: No *_0000.nii.gz files found in {imagesTr}")
        return

    print(f"Found {len(image_files)} cases in imagesTr")

    # Map: animal_id -> {timepoint -> case_id}
    animal_to_cases = defaultdict(dict)
    all_case_ids = []

    for f in image_files:
        case_id, animal_id, timepoint = parse_case_id(f.name)
        animal_to_cases[animal_id][timepoint] = case_id
        all_case_ids.append(case_id)

    print(f"Found {len(animal_to_cases)} unique animals")

    # -----------------------------------------------------------------------
    # 2. Report timepoint distribution
    # -----------------------------------------------------------------------
    tp_counts = defaultdict(int)
    for animal_id, tp_dict in animal_to_cases.items():
        key = "+".join(sorted(tp_dict.keys()))
        tp_counts[key] += 1

    print("\nTimepoint availability per animal:")
    for key, count in sorted(tp_counts.items()):
        print(f"  {key}: {count} animals")

    # -----------------------------------------------------------------------
    # 3. Assign animals to folds
    # -----------------------------------------------------------------------
    rng = random.Random(args.seed)
    animals_list = [(a, set(tp.keys())) for a, tp in animal_to_cases.items()]
    fold_assignments = assign_folds_stratified(animals_list, args.n_folds, rng)

    # -----------------------------------------------------------------------
    # 4. Build splits
    # -----------------------------------------------------------------------
    splits = []
    for fold_idx in range(args.n_folds):
        val_cases = []
        train_cases = []

        for animal_id, tp_dict in animal_to_cases.items():
            assigned_fold = fold_assignments[animal_id]
            for case_id in tp_dict.values():
                if assigned_fold == fold_idx:
                    val_cases.append(case_id)
                else:
                    train_cases.append(case_id)

        splits.append({"train": sorted(train_cases), "val": sorted(val_cases)})

    # -----------------------------------------------------------------------
    # 5. Verify no temporal leakage
    # -----------------------------------------------------------------------
    print("\nVerifying temporal leakage (2mo in train / 4mo in val for same animal)...")
    leakage_found = False
    for fold_idx, split in enumerate(splits):
        train_set = set(split["train"])
        val_set = set(split["val"])

        for animal_id, tp_dict in animal_to_cases.items():
            cases_in_train = [c for c in tp_dict.values() if c in train_set]
            cases_in_val   = [c for c in tp_dict.values() if c in val_set]

            if cases_in_train and cases_in_val:
                print(f"  LEAKAGE DETECTED fold {fold_idx}: animal {animal_id}")
                print(f"    Train: {cases_in_train}")
                print(f"    Val:   {cases_in_val}")
                leakage_found = True

    if not leakage_found:
        print("  No temporal leakage detected. All animals are fully in train OR val per fold.")

    # -----------------------------------------------------------------------
    # 6. Print fold summary
    # -----------------------------------------------------------------------
    print(f"\n{'Fold':<6} {'Train cases':<14} {'Val cases':<12} {'Val animals':<14} {'Val 2mo':<10} {'Val 4mo'}")
    print("-" * 70)

    for fold_idx, split in enumerate(splits):
        val_set = set(split["val"])
        val_animals = set()
        val_2mo = 0
        val_4mo = 0

        for animal_id, tp_dict in animal_to_cases.items():
            for tp, case_id in tp_dict.items():
                if case_id in val_set:
                    val_animals.add(animal_id)
                    if tp == "2mo":
                        val_2mo += 1
                    elif tp == "4mo":
                        val_4mo += 1

        print(f"{fold_idx:<6} {len(split['train']):<14} {len(split['val']):<12} "
              f"{len(val_animals):<14} {val_2mo:<10} {val_4mo}")

    # -----------------------------------------------------------------------
    # 7. Write splits_final.json
    # -----------------------------------------------------------------------
    with open(out_path, "w") as f:
        json.dump(splits, f, indent=2)

    print(f"\nWrote splits_final.json → {out_path}")
    print("\nNext step:")
    print(f"  Copy to: $nnUNet_preprocessed/Dataset002_GraftSeg/splits_final.json")
    print(f"  (nnU-Net reads this file at training time, not preprocessing time)")


if __name__ == "__main__":
    main()
