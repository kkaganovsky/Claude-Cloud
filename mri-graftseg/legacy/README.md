# Legacy scripts (from Biomni, unchanged)

Kept for reference only. GraftSeg replaces them:

| Script | Replaced by | Known problems (see ../README.md) |
|---|---|---|
| `predict_grafts.py` | Segment + Review pages, `graftseg predict` / `review` | `.seg.nrrd` written with axes reversed (fix: `graftseg repair-old`) |
| `prepare_pa_data.py` | `graftseg build` | every non-zero segment became graft; 4D Slicer files fail; cropped segs give a label of the wrong size |
| `generate_splits.py` | `graftseg build` (writes `splits_final.json`) | drops cases when an animal has two scans at one timepoint; `Assembloid03` ≠ `Assembloid_03` |
| `run_training.sh` | Train page, `graftseg train` | `-num_epochs` is not an nnUNetv2_train option, so training never starts |
