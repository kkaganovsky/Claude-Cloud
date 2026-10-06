# ephys-analysis

GUI for current-clamp `.abf` recordings with a family of current steps (first negative, constant
increment). Measures **input resistance**, **membrane time constant (τ)** and **capacitance (Cm)**.

```
pip install -e .[test]
ephys-analysis path/to/IO.abf      # or: python -m ephys_analysis
pytest
```

## What it does

Vm is the first voltage channel; Im is a recorded current channel if present, otherwise the command
waveform stored in the ABF header (like Easy Electrophysiology's "Generate Axon Im protocol from
header"). Step start/stop are auto-detected from Im (half-maximum crossing) and the baseline,
measure and fit regions are placed from them. Every value can be edited in the spin boxes **or** by
dragging the shaded regions; everything recomputes live. Sweeps can be unchecked individually.

**Input resistance** (follows Easy Electrophysiology manual v2.6.3, §9): ΔVm = mean(measure) −
mean(baseline), ΔIm likewise from the Im trace; sweeps chosen by "Sweeps for Rin" (below); Rin = slope of an OLS
line (`scipy.stats.linregress`, intercept modelled) of ΔVm (mV) vs ΔIm (nA) → MΩ. The fit is shown
with its equation. One sweep → V/I. Sweeps with spikes are excluded by default.

**Sweeps for Rin** (one setting, "Sweeps for Rin"). The 0 pA sweep (|ΔIm| ≤ 1 pA, ΔVm ~ 0) is always
used: with only two hyperpolarizing steps (e.g. −40/−20 pA) the line would otherwise pass exactly through
two points (R² = 1, no check on the fit). It is never used for τ or sag.
- **Negative + 0 pA** (default): hyperpolarizing steps and the 0 pA sweep.
- **Negative + 0 pA + 2 smallest positive (no spikes)**: also the two smallest depolarizing steps whose Vm
  stays below the spike threshold during the step; spiking steps are skipped (the table says why each
  depolarizing sweep was or was not used). If fewer than two qualify, the ones that do are used.

On the 08/27/2026 cells (default windows): 26827002 / 006 / 010 give Rin 539.0 / 1600.8 / 615.3 MΩ
(R² 0.966 / 0.988 / 0.870) with negative + 0 pA, against 354.0 / 1873.6 / 205.1 MΩ from the two steps
alone; adding positive steps gives 593.9 (+20 pA only; +40 spikes) / 1012.5 (+20, +40; R² 0.897) / 615.3
(all positive steps spike).

**ΔIm and sweep labels**: ΔIm = mean Im (measure) − mean Im (baseline), so a constant holding-current
offset on the Im channel cancels. When the steps are evenly spaced ΔIm is rounded to the protocol step
(EE "Round Im injections"; "Round ΔIm to step", 0 = off; the measured values are ~1 % larger than the
command on the 2026 rigs, e.g. +202.7 for +200 pA). The sweep list shows the same ΔIm. (Earlier
versions labelled sweeps from the single most extreme Im sample, which includes noise and read a few pA
too large, e.g. −45 / −24 / −4 / +24 pA for −40 / −20 / 0 / +20.)

**Steady-state estimator** (for large sag): mean, median, last sample(s), or an OLS line fitted
over the measure region and evaluated at its end.

**τ0**: per sweep, `b0 + Σ b_k·exp(−t/τ_k)` with 1 (default), 2 or 3 terms (scipy `least_squares`,
TRF, τ > 0); τ0 is the slowest term. Fit start = step onset (+offset), the sag minimum, or a custom
time; fit end editable via spin box or by dragging the blue region's end handle (default: step onset + 500 ms); b0 free or fixed to the steady-state estimate. By default τ0 is taken from
only the **smallest hyperpolarizing sweep** (the one just before injected current = 0; "τ taken
from: smallest"); Rin still uses all analysed sweeps. "sweep" takes τ from one sweep you choose (spin box, or double-click its row
in the results table), e.g. sweep 0 when its fit is best; the reason is shown if that sweep cannot be used
(unchecked, the 0 pA sweep, or a failed fit). "all" uses the median (or mean) τ0 across
analysed hyperpolarizing sweeps. Sag peak, sag and sag ratio (sag / baseline-to-peak deflection) are tabulated.

**Cm [pF] = τ0 [ms] / Rin [MΩ] × 1000** (ms / MΩ = nF, reported in pF). This is the headline value and matches how the rest
of the project's data were analysed.

Reference only: Golowasch et al. 2009 (J Neurophysiol 102:2161, in the repo root) show that for
non-isopotential cells τ/Rin is not correct and recommend a 2–3 exponential fit with Cm = τ0/R0,
R0 = V0/Iext (amplitude of the slowest term). With >1 exponential terms the GUI also prints this
as a muted reference line; it never replaces the headline Cm.

## Opening files and copying results
- **Previous / Next file** (toolbar, or Ctrl+[ / Ctrl+]) opens the neighbouring `.abf` in the same folder; a saved
  analysis for that file is offered for restore as usual.
- Open with the button / Ctrl+O (the dialog starts in the folder of the last opened file), or drag an `.abf`
  onto the window.
- Under the results table, one tab-separated line `file  Rin (MΩ)  Cm (pF)` updates live; **Copy** puts it on
  the clipboard so it pastes into three spreadsheet cells (**Copy with header** adds `File  Rin_MOhm  Cm_pF`).

**Default windows**: baseline = sweep start to 2 % of the step length before onset; measure (steady state) =
100 ms ending **1 ms before** the step offset (clear of the off-transient); exponential fit = step onset to
onset + **500 ms**. All are editable / draggable.

## Saving an analysis (reproducible)
**Save analysis** (button or Ctrl+S) writes `<name>.ephys-analysis.json` next to the `.abf`: every parameter,
the checked sweeps, the `.abf` (relative + absolute path and SHA-256 of its bytes), software versions and the
results (Rin, τ, Cm, …). Opening that `.abf` again offers to restore it; you can also open or drag the `.json`
itself. On restore the analysis is re-run from the saved settings and compared with the saved results; any
difference, or a data file that is not byte-identical, is shown as a warning.

## Known limitations
- Verified on synthetic passive-membrane data (`tests/`) and loaded/analysed on
  `cell 7 DLX kglu next to DLX cells FS?/IO.abf` (step auto-detected at 578-1578 ms).
- Step auto-detection assumes one step per sweep; with multi-epoch protocols set times manually.
- Exponential start values use my own estimates (b0 from the end of the window); the triexponential
  start taus differ from the manual's identical tau/3 (degenerate Jacobian).
- A single τ fit is a poor model of a cell with strong sag or multiple compartments; check the
  displayed fits and R² per sweep.
