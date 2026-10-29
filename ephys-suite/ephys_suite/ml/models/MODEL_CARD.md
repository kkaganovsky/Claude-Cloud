# sEPSC classifier v3 (`sepsc_v3.pkl.xz`)

Default model for `ephys-suite-classify` / `ephys_suite.ml.infer.load_bundle()`. xz-compressed pickle of the bundle described in
`ephys_suite/ml/infer.py` (load only bundles you trust: pickles run code on load).

- **Ensemble:** random forest (500 trees, 21 tabular features) + 3 multiscale CNN seeds (width 32, dropout 0.1), logits averaged.
- **Input:** 3 kHz low-pass, 10 kHz working rate, wide 1500-sample windows (peak at sample 300).
- **Training data:** Easy Electrophysiology sEPSC annotations (lab recordings) + Ding-lab mEPSC/EPSC recordings (Mini Analysis labels),
  quiet-baseline windows as extra negatives. Technician-labelled HSO-HCO in vitro cells (batch2) excluded.
- **Held-out performance** (grouped 5-fold CV by cell, candidates): ensemble AP 0.932, precision 0.896, recall 0.841 at threshold 0.866;
  EE-annotated cells AP 0.883, Ding cells AP 0.965; false alarms on quiet baseline 0.05%. Against an outside reviewer's yes/no on
  3,647 hFA candidates: AP 0.926.
- **Threshold:** bundle threshold 0.866; recordings with median pre-event noise SD >= 1.86 pA use 0.90.
- **Known weak spot:** very noisy, low-rate cells (e.g. AP ~0.19 against a manual review of one such cell). Review those by hand.
- **Environment:** pickled with scikit-learn 1.9.1, torch 2.14.1, numpy 2.4.6. Older scikit-learn may warn or fail to unpickle the forest.
- **SHA-256:** uncompressed `c9ce74e39cafa71b9dcd1d54a27b5550ec23778737e4bc0ed623a190d1e8f5c3`,
  compressed `a5f38ece8051e8be83d4620712f58241b00ea699e21b610a7768c79042737b13`.

A v4 retrain (2026-10-28: adds manually reviewed hFA cells, annotation-note cleaning, loss-based epoch selection) performed the same
(AP 0.924 vs 0.926 against the outside reviewer) and is not shipped.
