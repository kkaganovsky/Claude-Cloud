# ephys-suite

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
  Threshold search 5 ms before the peak.
- Passive windows: baseline = 100 ms before the step, steady state = last 100 ms of the step (Scala et al. 2019 Methods).
- Currents for Rin are measured from the Im channel and **rounded to the protocol step** (estimated as 100 pA), emulating EE's
  "Round Im injections"; with rounded currents *some* region choices give exactly 28.3092 MOhm, with measured currents none did.
- tau: mono-exponential, step onset -> step end (whole current injection), b0 free, **smallest** hyperpolarising step only
  (`tau_source`), Cm = tau / Rin (your specification; Golowasch et al. 2009 note this assumes an isopotential cell).
- Events: template width 25 ms, lower threshold linear -5 pA, amplitude threshold 5 pA, local-maximum period 5 ms, baseline search
  10 ms, average baseline 1 ms, decay search 30 ms, deconvolution band 1-200 Hz (the manual says 0.1 Hz in the background section and
  1 Hz in the options section), cutoff = 3.5 x sigma of the Gaussian fitted to the all-points histogram (pooled across records).
  Omit periods apply to every record's own time axis (the example sEPSC file has a test pulse at 0.1-0.3 s in each sweep).

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
