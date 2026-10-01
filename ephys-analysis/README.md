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
mean(baseline), ΔIm likewise from the Im trace; hyperpolarizing sweeps only; Rin = slope of an OLS
line (`scipy.stats.linregress`, intercept modelled) of ΔVm (mV) vs ΔIm (nA) → MΩ. The fit is shown
with its equation. One sweep → V/I. Sweeps with spikes are excluded by default.

**Steady-state estimator** (for large sag): mean, median, last sample(s), or an OLS line fitted
over the measure region and evaluated at its end.

**τ0**: per sweep, `b0 + Σ b_k·exp(−t/τ_k)` with 1 (default), 2 or 3 terms (scipy `least_squares`,
TRF, τ > 0); τ0 is the slowest term. Fit start = step onset (+offset), the sag minimum, or a custom
time; fit end editable; b0 free or fixed to the steady-state estimate. By default τ0 is taken from
only the **smallest hyperpolarizing sweep** (the one just before injected current = 0; "τ taken
from: smallest"); Rin still uses all analysed sweeps. "all" uses the median (or mean) τ0 across
analysed hyperpolarizing sweeps. Sag peak, sag and sag ratio (sag / baseline-to-peak deflection) are tabulated.

**Cm = τ0 / Rin** (ms / MΩ = nF, reported in pF). This is the headline value and matches how the rest
of the project's data were analysed.

Reference only: Golowasch et al. 2009 (J Neurophysiol 102:2161, in the repo root) show that for
non-isopotential cells τ/Rin is not correct and recommend a 2–3 exponential fit with Cm = τ0/R0,
R0 = V0/Iext (amplitude of the slowest term). With >1 exponential terms the GUI also prints this
as a muted reference line; it never replaces the headline Cm.

## Known limitations
- Verified on synthetic passive-membrane data (`tests/`) and loaded/analysed on
  `cell 7 DLX kglu next to DLX cells FS?/IO.abf` (step auto-detected at 578-1578 ms).
- Step auto-detection assumes one step per sweep; with multi-epoch protocols set times manually.
- Exponential start values use my own estimates (b0 from the end of the window); the triexponential
  start taus differ from the manual's identical tau/3 (degenerate Jacobian).
- A single τ fit is a poor model of a cell with strong sag or multiple compartments; check the
  displayed fits and R² per sweep.
