# ephys-suite

**New here? Follow the [tutorial](docs/TUTORIAL.md)** (worked example on one cell, with screenshots).

Patch-clamp analysis suite modelled on [Easy Electrophysiology](https://www.easyelectrophysiology.com) (manual v2.6.3 and
its open-source analysis code), for Axon `.abf` files. Independent re-implementation, GPL-3.0-or-later (the algorithms follow
GPL-3.0 code; see *Licensing* below).

```
pip install -e .[test]
ephys-suite path/to/IO.abf        # or: python -m ephys_suite
pytest
```

| Tab | What it does |
|---|---|
| Passive | Rin (EE OLS; Scala RANSAC / median), sag (EE ratio and Scala "Tolias" ratio), tau, Cm = tau/Rin. Draggable step window; every window is a spin box. |
| AP counting and kinetics | EE auto-threshold record / spike / manual detection, rheobase (record / exact / Scala), FS latency, mean ISI, SFA (divisor, local variance), adaptation index, f-I plot. AP kinetics: threshold (first/third derivative, Sekerli Methods I/II, leading inflection, max curvature), amplitude, rise, decay, FWHM, fAHP, mAHP, optional 200 kHz interpolation, max slopes, phase plot. |
| Events | Template (biexponential, rise 0.5 / decay 5 ms default) detection by **deconvolution** (Pernia-Andrade 2012; the "convolution method"), correlation and detection criterion (Clements & Bekkers 1997 sliding window), or thresholding. Lower threshold (linear / RMS / polynomial curve), upper threshold, amplitude threshold, local-maximum period, omit period, peak smoothing; per-event baseline + foot, amplitude, 10-90 rise, decay % (37), mono-exponential tau, FWHM, AUC, inter-event interval; average event overlay, cumulative probability, KS test; CSV export. |
| Cell summary | One spreadsheet row in the project's column layout (`report.COLUMNS`) plus a printed list of every decision made. |

## Spike counting rule
Threshold-line crossings closer than 3 ms are one AP, and an AP counts only if its peak is at least **20 mV above the
lowest Vm since the previous AP** (lab rule, adjustable in the AP tab: "Min. rise above preceding trough"). This
rejects noise on depolarisation-block plateaus. It also rejects real APs that have shrunk below 20 mV late in strong
steps (e.g. 26507006 at +140 to +170 pA), so cells like that are undercounted at high current.

## Access-resistance QC (sEPSC cells)

Checks that every cell's access (series) resistance is **below 25 MOhm**, replacing `RaRm.m` / `test_pulsePasca.m`.

```
ephys-suite-qc "/Users/kkaganovsky/Downloads/hFA in vitro 10162026"        # or: python -m ephys_suite.qc "<folder>"
```
It prints one line per recording, a verdict per cell, and writes `Ra_QC_<date>.xlsx` (sheets Cells / Files / Sweeps / Settings) into
the folder (`--out`, `--csv`, `--limit 25`, `--vstep -10`, `--drift 0.2`). Exit code 0 = every cell passes. The same scan is in the GUI
tab **Access resistance QC** (colored table; click a row to see the detected pulse, the baseline / peak / steady-state windows and Ra
per sweep, so each detection can be checked by eye).

- **Which files:** EPSC recordings = `.abf` in a folder whose name contains `EPSC` (or with EPSC in the file name); test-pulse
  recordings = `.abf` in a folder named `CM` (or named like `membrane` / `test pulse`). A file's cell is the folder above its `EPSC` /
  `CM` folder; the first folder under the chosen root is the line (e.g. `line 14`, `line Q2`). Other `.abf` files (IO, spontaneous...)
  and Mac `._` / `__MACOSX` files are ignored. Layout problems (an EPSC file with no CM file, several EPSC and CM files in one cell
  folder, ...) are listed after the table.
- **Detected, not hard-coded:** the pulse onset / offset come from the recorded command-voltage channel when there is one, otherwise from
  the two opposite capacitive transients in the current trace (any sampling rate, sweep length, pulse position / duration; sweeps may
  differ in length). The step size is the measured command step; with no command channel the assumed step (default -10 mV) is used and the
  report says so (its sign is taken from the direction of the onset transient). If a sweep has no pulse it is reported, not guessed.
- **Ra, Rm (as in the MATLAB script):** Ra = dV / (I_peak - I_baseline) x 1000, Rm = dV / (I_steady - I_baseline) x 1000 - Ra. Baseline =
  25 ms before the onset; peak = extreme current within 5 ms after the onset; steady state = 80-90 % into the pulse (the script's
  20-10 ms before the end of a 100 ms pulse).
- **Verdict (per recording, then per cell = worst recording):** PASS = Ra mean and every sweep below the limit and a pulse found in every
  sweep; WARN = some sweeps at / above the limit (mean below) or a pulse missing in some sweeps; FAIL = mean Ra at or above the limit;
  NO PULSE = nothing measurable. The script's **RaExclude drift rule** (any sweep's Ra >= 120 % or <= 80 % of the first sweep's) is a
  separate flag (column and line in the summary); it does not change the verdict.
- **Checked against the MATLAB script** on the example cell's embedded pulse: per-sweep Ra within 2.5 % (10.73 / 12.89 / 13.50 /
  13.94 vs the script's 10.61 / 12.71 / 13.29 / 13.80 MOhm - the difference is the measured step of -10.10 mV instead of -10 mV; with the step
  forced to -10 mV the agreement is within 1 %). The same cell's `membrane.abf` (a 10 kHz test-pulse file with no command channel) gives
  10.4 MOhm.

## Moving through a folder and switching the AP threshold method
- **Previous / Next file** (toolbar, or Ctrl+[ / Ctrl+]) opens the neighbouring `.abf` in the same folder (natural name
  order); the toolbar shows "3 / 7: name". The Open dialog starts in the last folder used.
- **AP counting and kinetics tab**: buttons **1 Method II** / **2 Leading inflection** (keys 1 / 2) switch the threshold
  method. Both candidate thresholds are always drawn on the first-AP plot (square = Method II, diamond = leading
  inflection; the active one is outlined) and listed with their amplitude and half-width, so the two can be compared
  at a glance. Every new file starts on Method II. **F** jumps to the first spiking sweep (opened by default);
  **Copy** (key C) copies `file, sweep, threshold, amplitude, half-width, method` (tab-separated) for a spreadsheet.

## Editing events (Events tab)

The tab shows an edit toolbar, a view toolbar and a one-line hint that always tells you the next step.

| Action | How |
|---|---|
| Add an event | **A** (Add mode), then click on it - or drag across it. The peak snaps to the extreme within the snap window (± 2 ms, adjustable). |
| **Fit kinetics** toggle (**K**) | **ON** (default): the event is measured fully and must pass the thresholds, as in Easy Electrophysiology; if it fails, the hint says why and suggests turning Fit off. **OFF**: any event can be added regardless of the threshold; only peak, baseline and amplitude are measured (`Fit = no` in the table). |
| Baseline | The baseline preceding the event is found automatically. If none can be found, the tab **prompts you to click the baseline level** (orange peak marker). **Manual baseline (B)** arms the same prompt in advance. After you click, the button switches itself off and you are still in Add mode, so you can keep adding. With an event selected (Add off), **B** moves *that* event's baseline and re-measures it. The baseline must be left of the peak (otherwise it asks again). |
| Remove an event | Normal mode: click its marker (blue ring), click again to delete (as in EE); or select it and press **Delete / Space**. In Add mode a click on a marker only selects it, so nothing is deleted by accident. |
| Undo / redo | **Ctrl+Z / Ctrl+Y** (every add, delete and baseline move). |
| Navigate | **←/→** previous / next event (as in EE); **PageDown / PageUp** (or **Z / X**) next / previous window - running past the end of a record continues in the next one; clicking a table row jumps to the event. |
| View | X start, width, Y min, Y max boxes (also follow mouse zoom). **Lock Y** keeps the limits while paging / changing record, otherwise Y is fitted to the visible data. **Fit Y** fits once. **Remember view** stores width and Y limits and re-applies them whenever a file is opened. |
| Re-running detection | **Keep manual edits when re-running** (default on) layers your additions, deletions and baselines over the new detection; **Discard all manual edits** resets. |

Marker legend: red = detected, green diamond = added by hand, white = added without kinetics, blue dot = baseline,
purple dot = decay endpoint, dashed red line = lower threshold.

## Step ordering

Steps are ordered by their injected current **relative to zero**, never by file position or by one assumed step size:
`lowest` = most negative step, `smallest` = the last hyperpolarising step before zero (used for tau by default), and
rheobase / first-spike latency / AP kinetics use the **smallest depolarising step with an AP**. Currents are only rounded
to a protocol step when the measured spacing is uniform (within 20 %); with uneven steps the measured currents are used.

## Reproducing the reference cell

`scripts/validate_cell7.py <dir>/` runs the example cell and prints a comparison with the reference spreadsheet row. With the
defaults (current commit):

| Column | Result vs reference |
|---|---|
| Max AP, Adaptation, Rheobase, Amplitude, Threshold, Corr Threshold, Rise, Decay, Half-width, fAHP, mAHP | **exact** (to the printed precision) |
| RMP original / corrected | within 0.005 mV |
| steady-state dV, Rin, Sag mV, Sag ratio, Tolias ratio, tau, Cm | close, not exact (see below) |
| FS latency | **differs by design** (see below) |

## DECISIONS to check

*Recovered from the reference values (each reproduces your number exactly):*
1. **AP kinetics = Easy Electrophysiology, first AP of the first spiking sweep (sweep 9), threshold = Sekerli Method II with
   dV/dt lower bound 1 mV/ms.** Method II gives -47.503662 mV for every search window from 2 to 50 ms; first-derivative cutoffs,
   Method I, maximum curvature etc. do not.
2. Rise 10-90 %, decay 10-90 % of peak -> fAHP, FWHM, all with 200 kHz linear interpolation (without interpolation the values would
   be 0.30 / 0.85 / 0.70 ms).
3. fAHP = minimum within 0-5 ms after the peak, mAHP = minimum within 10-50 ms, both **relative to the threshold**
   (the fAHP minimum is at 2.15 ms; the mAHP window must start between ~7 and 10 ms and end after ~36 ms).
4. **Max AP = 80** with the EE auto-threshold detection (also 80 with the record threshold).
5. **Adaptation = EE "divisor" SFA (first ISI / last ISI) of the sweep with the most APs** (sweep 37: 9.30 / 15.70 = 0.592357 ms).
   It is *not* the Scala ISI2/ISI1 index (that gives 0.92 for the same sweep; the Scala median over the five lowest sweeps gives
   1.67). Both are available (`adaptation_mode`).
6. **Rheobase = EE "Exact"**: Im at the sample of the first AP peak minus the Im baseline = 401.5500 pA (baseline = start of sweep to
   step start). The Scala RANSAC rheobase falls back to the first spiking current (402.5 pA) here because the first three spiking
   steps all have one AP.
7. **RMP = mean Vm of sweep 0 of `spontanous?.abf`** (-73.561 vs -73.566 reference; likely a slightly different window);
   liquid-junction correction 9.68 mV (RMP corrected = RMP - 9.68, Corr Threshold = Threshold - 9.68).
8. **Sag mV / Sag ratio / Tolias ratio use the raw minimum sample as the peak** (not Allen's 5 ms mean) and the lowest step.
   Sag mV = Vpeak - Vss, Sag ratio = sag / (Vpeak - Vbaseline) (identical in EE and Allen), Tolias ratio =
   (Vpeak - Vbaseline) / (Vss - Vbaseline) = 1 + sag/dV (Scala's "sag ratio"). The reference's four numbers are mutually consistent
   under these definitions.

*Defaults I chose (not documented by EE or recovered from your numbers) - please check:*
- AP detection: amplitude 30 mV, +dV/dt 20 mV/ms, -dV/dt -10 mV/ms, width 2 ms; amplitude = peak minus Vm at the +dV/dt crossing.
  Threshold search 10 ms before the peak (matches the lab's Easy Electrophysiology setting; e.g. 26424012 threshold -35.1257 mV, amplitude 46.5088 mV, identical to EE. With 5 ms some cells differ by 0.1-8 mV).
- Passive windows: baseline = 100 ms before the step, steady state = last 100 ms of the step (Scala et al. 2019 Methods).
- **Rin also uses the 0 pA sweep** (|dI| <= 1 pA, dV ~ 0) by default ("Also use the 0 pA sweep"); it is never used for tau or
  sag. With two-step protocols (-40 / -20 pA) the OLS line otherwise passes exactly through two points. The cell-7 reproduction
  test switches it off, because the reference Rin was made from the hyperpolarising steps only (with it on: Rin 29.45 MOhm).
- Currents for Rin are measured from the Im channel and **rounded to the protocol step** (estimated as 100 pA), emulating EE's
  "Round Im injections"; with rounded currents *some* region choices give exactly 28.3092 MOhm, with measured currents none did.
- tau: mono-exponential, step onset -> step end (whole current injection), b0 free, **smallest** hyperpolarising step only
  (`tau_source`), Cm = tau / Rin (your specification; Golowasch et al. 2009 note this assumes an isopotential cell).
- Events: template width 25 ms, lower threshold linear -5 pA, amplitude threshold 5 pA, local-maximum period 5 ms, baseline search
  10 ms, average baseline 1 ms, decay search 30 ms, deconvolution band 1-200 Hz (the manual says 0.1 Hz in the background section and
  1 Hz in the options section), cutoff = 3.5 x sigma of the Gaussian fitted to the all-points histogram (pooled across records).
  Omit periods apply to every record's own time axis (the example sEPSC file has a test pulse at 0.1-0.3 s in each sweep).
- **Lower threshold default = RMS** (record mean -/+ 2 x RMS; 2x is the manual's example multiple; omit periods are excluded from
  the RMS so a test pulse does not inflate it). On the example sEPSC file this finds 160 events (4 Hz) vs 100 at 3 x RMS and 56
  with a fixed -5 pA level - raise the multiple if it is too sensitive.

*Differences from your reference that I could not resolve:*
- **FS latency: 593.1 ms in the reference = 682.2 ms (first AP peak, rheobase sweep) - 89.1 ms.** 89.1 ms is the Im start of the 2.5 s
  protocol (`23713046/47.abf`); in `IO.abf` the current starts at 578.15 ms, so the latency measured from the actual step start is
  **104.05 ms**. This looks like a wrong injection start entered in EE; please check.
- **steady-state dV / Rin / sag / tau / Cm**: these depend on the baseline and steady-state regions you dragged in EE. I back-solved
  the voltages they imply (steady state -88.4346 mV, baseline -73.8010 mV for the -500 pA sweep) but many window pairs fit, so the
  windows are not identifiable. Current suite values: dV -14.586, Rin 28.67, sag -1.404, sag ratio 0.0878, tolias 1.0962,
  tau 21.158, Cm 738.1 (reference -14.634, 28.309, -1.397, 0.0871, 1.0955, 21.259, 750.9). Set the windows explicitly in the Passive
  tab if you want to match them.

## Licensing
The suite follows algorithms from Easy Electrophysiology (GPL-3.0-or-later) and the Scala et al. 2019 / Allen Institute feature code
(Allen Institute non-commercial licence). Nothing was copied verbatim; the suite is released as GPL-3.0-or-later. The Allen-derived
definitions (sag, adaptation index, RANSAC rheobase) are re-implemented from the paper and notebook descriptions. Please confirm the
licensing is acceptable for your use before redistributing.

## Reviewing classifier-proposed events
The Events tab can load a candidates CSV from the sEPSC classifier (columns `sweep, time_in_sweep_ms, score, accepted`): accepted candidates become the event set, borderline ones are orange rings you click to accept, **R** steps through the most uncertain candidates, **M** marks a record fully reviewed, and *Save review…* writes the final decisions for retraining (see `model_review.py`).

## Outside review: HTML package for a collaborator
Hand the yes / no decisions on model-proposed events to someone who has **no install, no Python and no model**.

1. **Export:** Events tab > *Review model events* > **Export for outside review (HTML)…** (or `ephys-suite-review-export`). It writes a
   folder (and optionally a `.zip`): `START_HERE.html`, `README.txt` for the reviewer, `data/`, `manifest.json`. Tick which recordings
   to send; tick *leave out events I have already confirmed / rejected* to keep your own decisions out of it.
2. **Review (collaborator):** double-click `START_HERE.html` (Chrome works best), pick a cell, press **Y** (yes), **N** (no), **U** (not
   sure), arrows to go back / forward. Answers save in that browser; **Download results** writes one CSV to send back.
   The model score is never shown. They can only confirm or reject: *counts from this review can fall below, never above, the model's;*
   finding missed events still needs the full app.
3. **Import:** **Import reviewer's results…** (or `ephys-suite-review-import returned.csv --package <folder> --out <folder>`).
   Matches on `(recording, sweep, round(time_in_sweep_ms, 2))`, never on row order, prints the counts, and writes the GUI's own
   `<name>_review.csv` plus a `.review_progress.json`; open the recording and use *Resume saved review* to look at what is left.
   Your own earlier decisions are kept; old output files are renamed `*.bak-<time>`, never overwritten.
   **Unsure and unanswered events count as NOT events** by default and stay open for a second look
   (`--unsure-as` / `--blank-as` `not-event | model | event`).

```
ephys-suite-review-export a.abf b.abf --candidates cands/ --out pkg --zip --min-score 0.5 \
        --thr-per-file 2=0.99 --exclude 2:100-130 --polarity inward --title "hFA sEPSC review"
```
Snippets use the classifier's own preprocessing (`ml.core.prep_trace`: 10 kHz, 4th-order zero-phase 3 kHz low-pass) so the reviewer sees what the
model saw; each is -100 to +200 ms around the event, median-subtracted (judge SHAPE, not amplitude). Default minimum score 0.5
(the app shows candidates down to 0.25, so 0.25-0.5 is never sent unless you lower `--min-score`). `--thr` defaults to the lowest score the
model accepted in that file, else 0.9. Answers are kept per package and cell in the reviewer's browser (`localStorage`), so two packages never share
answers. Design notes and the original prototype: `docs/handoff/html_event_review/`.

## Display filter (Events tab)
*Filter view* (on by default) low-passes what you **see** (trace, verify-card zoom, gallery) with a 4th-order zero-phase Butterworth at the
cutoff box (default **3 kHz**, the filter type and cutoff the classifier applies; buttons for 3 kHz (CNN), 1 kHz, 500 Hz). The raw trace is drawn
faintly underneath. It is display only: detection, thresholds, measurements, markers and the saved review all use the raw trace
(markers sit at raw peak heights, so they can float slightly off a heavily filtered peak). The classifier also resamples to 10 kHz, which is not reproduced.

## Associated events file
Opening a recording looks beside it (and in a `candidates/` or `review/` subfolder) for `<name>.candidates.csv`, `<name>_review.csv` or
`<name>.review.csv` with the candidate columns, and **asks** whether to load the newest one (never loads silently; a saved
`.review_progress.json` is then offered for resume as usual). Tick *Don't ask again* in the question to switch it off.

## Clicking markers (fixed)
Clicks on the trace markers (orange rings, events, possible misses) were swallowed by the marker layer, so **clicking an orange ring did nothing**
in the real window (the tests called the handler directly and missed it). Fixed and covered by tests that send real mouse clicks.
A click on a ring now accepts it *and* counts as a confirmed decision; the ring's click area is larger. The event the verify card
shows is selected but not armed: one click on it selects, a second click removes it.

## Window layout and keys
The window opens at up to 94 % of the screen and remembers its size, splitter positions and hidden panels; toolbars wrap instead of
being cut off and the page scrolls below 1000 x 620 px. **View** menu: show / hide any panel per tab, presets, *Reset layout*.
**Help > Keyboard shortcuts (F1)** lists every key. Paging in the Events tab: **X / PageDown** next, **Z / PageUp** previous
(PageUp / PageDown did not work before: Qt needs the name `PgDown`). **Space no longer deletes** the selected event (Delete / Backspace do).

## Verifying events and finding missed ones (Events tab)

*Skip borderline within N ms* (Review model events, default 10 ms): a not-accepted candidate this soon *after* an accepted, confirmed or added event is not offered as borderline (it is usually on the previous event's decay). Whether the model scored it low *because* of the overlap is not established; set 0 to see everything. Filtered candidates stay in the exported file.

A **Verify card** (right of the trace) and an **overview strip** (under it) turn review into a short loop. Everything uses the same
event set, table and Ctrl+Z as before.

| Goal | How |
|---|---|
| **Verify predicted events** | An event stays *to verify* until you confirm it. The card shows the current item zoomed (-25 / +60 ms) with the baseline, the peak, the model score, SNR and the gap to the previous event, plus a green line showing the *typical event scaled to this amplitude* so a wrong shape is obvious. **Y / Enter** confirms and moves on, **N** rejects (event removed) and moves on, **. / ,** skip / go back. Queues (drop-down): *Uncertain: events and candidates, least sure first* (default after loading candidates: ONE list of accepted events and borderline candidates, ordered by how close the model's score is to its threshold; Y confirms an event or accepts a candidate, N rejects an event or dismisses a candidate), model events *least sure first*, *all events in time order* (also for events from the normal detector), *borderline candidates*, *possible missed events*. |
| **Check many at once** | **G** opens the **gallery**: a contact sheet (5 x 4 by default, same Y scale, shape overlay). Click a thumbnail to flip it (event queues: reject / bring back; candidate queues: add / remove); **Enter** confirms (or, for candidate queues, dismisses) everything you left alone and turns the page; *Next page with undecided* skips finished pages. |
| **Find missed events** | The **overview strip** shows every record side by side: signal envelope, event ticks (red = to verify, green = confirmed, blue = added by hand, orange = borderline, purple = possible miss), a **green/grey strip of which parts you have actually looked at**, and the current window (click or drag to jump). **V** jumps to the next unviewed stretch, so paging through the whole trace is a loop of *look - add what you see (A) - V*. A window only counts as viewed when it is <= 2.5 s wide (adjustable) and stays on screen for 0.35 s, so zooming out or holding PageDown does not count. When a record is fully viewed and fully decided the card says so and **M** marks it reviewed. |
| **Second opinion** | **F** (or the *Possible miss* button) jumps to places that a separate, permissive deconvolution detector (cutoff 2.5 SD, amplitude >= 3 x the record's noise) flags but that are not events, not model candidates and not already decided - strongest first. **Y** adds one (kinetics measured, thresholds off; if no baseline can be found you get the same click-the-baseline prompt as for hand-added events), **N** dismisses it for good. Click a purple diamond on the trace to focus it, click it again to add it. This is a *second detector*, not proof: events that look like noise to both the model and a deconvolution detector will not be flagged - the coverage strip is what shows you looked everywhere. |
| **Resume later** | Decisions, hand edits (adds, baselines), dismissed candidates, coverage and records marked reviewed autosave 1.5 s after each change to `<candidates>.review_progress.json` (or next to the recording if no candidates are loaded). Loading the same candidates again offers to resume; *Resume saved review* restores it later. |
| **Save** | *Save review...* writes the existing CSV plus two appended columns, `decision` (confirmed / rejected / dismissed / unreviewed / added) and `record_viewed_pct`; decided second-opinion places are listed as `source = suspect`. The `.json` next to it gains `viewed_fraction_by_record`, `viewed_fraction_total` and `viewed_intervals_ms`, so labels can be limited to stretches that were really reviewed. |

Keys: **Y/Enter** confirm, **N** reject, **. ,** next / previous, **E** next event to verify, **F** / **Shift+F** next / previous possible miss,
**V** next unviewed stretch, **G** gallery, plus the existing **R** (next uncertain candidate), **M**, **A**, **B**, **K**, Delete, Ctrl+Z / Ctrl+Y.
Keys are ignored while you are typing in a box. **Delete** on a selected event now also records it as *rejected*.

## sEPSC classifier inference (optional)

`ephys_suite/ml/` runs a trained CNN (+ random-forest) sEPSC classifier. Only inference is in the repository; training code and model weights live outside it and are updated separately.

```bash
pip install -e ".[ml]"          # torch + scikit-learn
ephys-suite-classify cell.abf --model final_models_v2.pkl --out candidates/
```

Writes `candidates/cell.candidates.csv` (`sweep, time_in_sweep_ms, score, accepted, amplitude_pA`), which loads in the Events tab ("Review model events") for accept / remove review. The model bundle is a pickle (load only bundles you trust); its format is documented in `ephys_suite/ml/infer.py`. Candidates come from the matched-filter and derivative detectors on a 10 kHz, 3 kHz low-passed copy of the trace; events in the first 30 ms or last 120 ms of a sweep are not scored, and test pulses are not masked, so exclude them before counting frequency. The CSV has a `flag` column: `large_slow` marks events outside the envelope of annotated sEPSCs (deeper than 100 pA, or slow to recover; failed spikes, escape currents, slow steps) - they are not accepted so they stay out of EPSC counts but remain visible for review (`--accept-flagged` overrides); `near_large` marks candidates within -20..+150 ms of one (amplitude/baseline unreliable, acceptance unchanged). Recordings whose median pre-event noise SD is >= 1.86 pA are treated as noisy and use a stricter threshold (0.90). Scores are calibrated against reviewed labels only if the weights were; check quiet cells (low event rate, small events) by hand.
