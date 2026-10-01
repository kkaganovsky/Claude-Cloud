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

**τ**: per sweep, `b0 + b1·exp(−t/τ)` (scipy `least_squares`, TRF, τ > 0), fit start = step onset
(+offset), the sag minimum, or a custom time; fit end editable; b0 free or fixed to the steady-state
estimate. τ is the median (or mean) across analysed sweeps. Sag peak, sag and sag ratio
(sag / baseline-to-peak deflection) are tabulated.

**Cm = τ / Rin** (ms / MΩ = nF, reported in pF), as specified by the project owner, citing
PMC2775376. That paper could not be opened from the build environment (blocked by the network
proxy) and this formula is not in the Easy Electrophysiology manual, so it is unverified against
the paper.

## Known limitations
- Developed without access to the target recording (`slice 3/cell 2 DLX+/IO.abf`); verified with
  synthetic passive-membrane data (`tests/`) and one public ABF for loading only.
- Step auto-detection assumes one step per sweep; with multi-epoch protocols set times manually.
- Exponential start values use my own estimates (b0 from the end of the window), not the manual's
  exact recipe; mono-exponential only.
- A single τ fit is a poor model of a cell with strong sag or multiple compartments; check the
  displayed fits and R² per sweep.
