# Data and labeling decisions

Taken from the Biomni project "Xenograft MRI Intensity Segmentation" (April 2026). They are coded in
`graftseg/labels.py` and `graftseg/naming.py`; change them there.

## Task
T2-weighted MRI of rat brain with human organoid / assembloid grafts in the cortex. Intensity classes: host tissue
(darkest), graft (bright), ventricle fluid (brightest). Only the graft is segmented. The graft is one continuous piece
but can touch the ventricle; in rare cases it is in the contralateral hemisphere. So prediction does **not** keep only
the largest piece. Pieces are counted and flagged in the summary CSV instead.

## Which Slicer segment is the graft
Label audit of 312 Assembloid / PCDH files:

| Segment name | Decision |
|---|---|
| "Organoid" (and typos), "Assembloid", the animal's own ID (e.g. "Assembloid_23_R1M_6mo", "PCDH_14_R1F_2mo") | graft |
| unnamed "Segment_1" when it is the first segment | graft, after visual review of the 9 such cases, **except Assembloid_39_R2M** (row 3 of that review; excluded) |
| "Fluid", "Ventricle?" (also "CSF") | fluid: used only when a dataset is built with "Also learn fluid", otherwise ignored |
| "Black spot" / "Black point" (and typos) | ignored |
| "background" / "Brain" | ignored (whole-brain box ROIs drawn as a Slicer workflow step, not anatomy) |
| other unnamed "Segment_N" | ignored (the small ones looked like un-renamed black spots) |
| anything else | ignored and listed as "not recognised" by *Check data*, so a rule can be added |

"Nm" in an ear tag means "no mark" (ear marking), **not** "no graft". These animals have grafts and are included.
Nested parent-level folders (e.g. `Assembloid08_R1F/` next to its timepoint subfolders) hold independent scans and
are kept. The same MRI file appearing twice is de-duplicated automatically (the newer segmentation is kept).

Not settled: early on, CSF was listed among the segments to drop, and later "fluid" was to be kept. GraftSeg treats
CSF as fluid. This only matters when fluid is trained as a second class.

## Normalization
The model in use was trained with nnU-Net's default **ZScoreNormalization** (per-case mean/std). CTNormalization was
discussed as a fix for very bright (saturated) graft voxels being missed, but no model was ever trained with it.
For now, the Review page's window/level and brush cover those misses.

## Training history
- Dataset001_GraftSeg: 272 Assembloid/PCDH cases (205 train / 67 validation), 3d_fullres, fold 0, 1000 epochs on an
  RTX A6000. Best EMA pseudo-Dice was reached at epoch 251. Baselines on the same validation set: classical
  threshold + region growing, mean Dice 0.41; zero-shot Efficient MedSAM2 (CPU), 0.05.
- Dataset002 (Assembloid/PCDH + PA_XX 2mo/4mo) was planned with the legacy scripts; see the README for the problems
  found in them.

## Splits
Folds are made per animal: every scan of an animal (all timepoints) lands in the same fold, so the 2mo scan is never in
training while the 4mo scan is in validation. Animals are identified case-insensitively after normalizing
`Assembloid03_L1F` / `Assembloid_03_L1F` to the same ID.
