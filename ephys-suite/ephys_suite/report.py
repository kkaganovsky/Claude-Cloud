"""Assemble one spreadsheet row per cell (the user's column layout) and a log of every analysis decision."""
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import kinetics as K
from . import passive as P
from . import spikes as S
from .common import idx_at, window_mean
from .io import Recording, load_abf

COLUMNS = ["Differentiation date", "Days post diff", "Date Recorded", "Rat", "Slice number", "Cell number", "Dlx",
           "Max AP", "Adaptation", "Rheobase (pA)", "FS Latency (ms)", "steady state change in Voltage (sag)",
           "Input Resistance", "Sag mV", "Sag Ratio", "Tolias ratio", "Amplitude (mV)", "Threshold (mV)",
           "Corr Threshold", "Rise Time (ms)", "Decay Time (ms)", "Half-Width (ms)", "fAHP (mV)", "mAHP (mV)",
           "Cm (pF)", "RMP original", "RMP corrected", "tau (ms)", "notes"]


@dataclass
class CellConfig:
    ljp_mV: float = 9.68                         # liquid junction correction subtracted from RMP / threshold
    spike: S.SpikeParams = field(default_factory=S.SpikeParams)
    kin: K.KineticsParams = field(default_factory=K.KineticsParams)
    passive: P.PassiveParams = field(default_factory=P.PassiveParams)
    rheobase_mode: str = "exact"                 # 'exact' (EE) | 'record' (EE) | 'scala' (RANSAC)
    adaptation_mode: str = "sfa_divisor_maxap"   # EE divisor (first/last ISI) of the sweep with most APs | 'scala_ai'
    latency_mode: str = "peak"                   # 'peak' (EE) | 'threshold' (Allen / Scala)
    latency_origin: Optional[float] = None       # ms; None = current-step start
    ai_n_sweeps: int = 5                         # Scala: median over the five lowest spiking steps with >= 3 spikes
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass
class CellReport:
    row: Dict[str, object]
    decisions: List[Tuple[str, str]]
    detail: Dict[str, object]


def detect_step(t, I):
    """Step start/stop (ms): half-maximum crossing of |Im - hold| on the sweep with the largest deviation; hold =
    median of the first and last 2 % of each sweep."""
    I = np.atleast_2d(I)
    k2 = max(1, I.shape[1] // 50)
    hold = np.median(np.concatenate([I[:, :k2], I[:, -k2:]], axis=1), axis=1, keepdims=True)
    dev = I - hold
    k = int(np.argmax(np.abs(dev).max(axis=1)))
    d = np.abs(dev[k])
    if d.max() < 1.0:
        return None
    idx = np.flatnonzero(d > 0.5 * d.max())
    return float(t[idx[0]]), float(t[idx[-1]] + (t[1] - t[0]))


def estimate_step(dI) -> float:
    """Protocol step (pA) from the measured step currents: median spacing of the sorted values, to the nearest 5 pA.
    Returns 0 (= do not round) when the spacing is not uniform (within 20 %), because one step size cannot describe an
    uneven protocol; the measured currents are then used as they are."""
    d = np.diff(np.sort(np.asarray(dI, float)))
    d = d[d > 1.0]
    if not d.size:
        return 0.0
    med = float(np.median(d))
    if (d.max() - d.min()) > 0.2 * med:
        return 0.0
    return float(5 * round(med / 5))


def make_config(rec: Recording, **overrides) -> CellConfig:
    t, I = rec.t, rec.im()
    st = detect_step(t, I) or (0.1 * t[-1], 0.9 * t[-1])
    cfg = CellConfig()
    cfg.passive.step = st
    cfg.spike.window = st
    base, meas = cfg.passive.regions()
    dI = [window_mean(t, I[s], *meas) - window_mean(t, I[s], *base) for s in range(I.shape[0])]
    cfg.passive.round_step = estimate_step(dI)       # EE 'Round Im Injections'
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def build_cell_report(path: str, cfg: Optional[CellConfig] = None, spont_path: Optional[str] = None) -> CellReport:
    rec = load_abf(path)
    V, I, t = rec.vm(), rec.im(), rec.t
    cfg = cfg or make_config(rec)
    dec: List[Tuple[str, str]] = []
    a, b = cfg.passive.step
    dec.append(("Current step", f"{a:.2f}-{b:.2f} ms, detected from the Im trace (half-maximum crossing); Im = "
                                f"{'recorded channel' if rec.channel_by_units('pA') is not None else 'header command'}"))
    row = {c: "" for c in COLUMNS}
    row.update(cfg.metadata)

    # ------------------------------------------------------------------------------------------ passive
    pas = P.analyse_passive(t, V, I, cfg.passive)
    low = pas.lowest
    row["steady state change in Voltage (sag)"] = low.dV if low else np.nan
    row["Input Resistance"] = pas.rin
    row["Sag mV"] = low.sag_mV if low else np.nan
    row["Sag Ratio"] = low.sag_ratio if low else np.nan
    row["Tolias ratio"] = low.tolias if low else np.nan
    row["Cm (pF)"] = pas.cm_pF
    row["tau (ms)"] = pas.tau
    base, meas = cfg.passive.regions()
    pp = cfg.passive
    dec += [
        ("Passive windows", f"baseline {base[0]:.1f}-{base[1]:.1f} ms; steady state {meas[0]:.1f}-{meas[1]:.1f} ms (mean)"),
        ("Step ordering", "steps are ordered by their injected current relative to zero (not by file position): 'lowest' = most "
                          "negative, 'smallest' = the last hyperpolarising step before zero, rheobase / latency / kinetics use the "
                          "smallest depolarising step with an AP"),
        ("Steps used for Rin", f"{pas.rin_n} hyperpolarising steps (dI < -{pp.min_abs_dI} pA, spiking steps "
                               f"{'excluded' if pp.exclude_spikes else 'kept'}); dI measured from the Im channel"
                               + (f", rounded to the {pp.round_step:.0f} pA protocol step" if pp.round_step else ", not rounded (uneven or unknown step size)")),
        ("Rin method", {"ols": "OLS line of dV on dI (EE manual s.9)", "ransac": "RANSAC line (Scala 2019)",
                        "median_ratio": "median of per-step dV/dI (Scala patch-seq)"}[pp.ri_method]),
        ("Sag / ratios", f"from the lowest step (dI={low.dI:.0f} pA): peak = "
                         f"{'raw minimum sample' if pp.peak_avg_ms == 0 else f'{pp.peak_avg_ms} ms mean'}; "
                         "Sag mV = Vpeak - Vss; Sag Ratio = sag/(Vpeak - Vbase) (EE); Tolias ratio = "
                         "(Vpeak - Vbase)/(Vss - Vbase) (Scala 'sag ratio')") if low else ("Sag", "no usable step"),
        ("tau", f"mono-exponential on the {'smallest' if pp.tau_source == 'smallest' else 'every'} hyperpolarising step"
                f" (dI={pas.smallest.dI:.0f} pA), fit {pas.smallest.fit_window[0]:.1f}-{pas.smallest.fit_window[1]:.1f} ms, "
                f"b0 {pp.b0_mode}" if pas.smallest else "no usable step"),
        ("Cm", "tau / Rin x 1000 (user-specified; assumes an isopotential cell, cf. Golowasch et al. 2009)"),
    ]

    # ------------------------------------------------------------------------------------------- spikes
    sp = cfg.spike
    per, thr_line = S.detect_all(t, V, sp)
    counts = np.array([len(x) for x in per])
    row["Max AP"] = int(counts.max()) if counts.size else 0
    dec.append(("AP detection", f"{sp.method}; amplitude >= {sp.amplitude} mV, +dV/dt >= {sp.pos_dvdt} mV/ms, "
                                f"-dV/dt <= {sp.neg_dvdt} mV/ms within {sp.width} ms; window {sp.window[0]:.1f}-"
                                f"{sp.window[1]:.1f} ms"
                                + (f"; record threshold {thr_line:.2f} mV" if thr_line is not None else "")
                                + " (numeric defaults are this suite's own, not documented by EE)"))
    dec.append(("Max AP", "largest spike count in any sweep"))
    # per-sweep dI over the step (relative to baseline) for all sweeps
    dI_all = np.array([window_mean(t, I[s], *meas) - window_mean(t, I[s], *base) for s in range(V.shape[0])])
    # latency
    order = S.order_from_zero(dI_all)                    # depolarising steps, smallest current first
    first = next((s for s in order if per[s]), None)
    thr_times = None
    origin = cfg.latency_origin if cfg.latency_origin is not None else a
    if first is not None:
        if cfg.latency_mode == "threshold":
            k = K.analyse_ap(t, V[first], per[first][0].peak_idx, cfg.kin)
            thr_times = [k.thr_t] if k.ok else None
        row["FS Latency (ms)"] = S.first_spike_latency(per[first], origin, thr_times)
    dec.append(("FS latency", f"smallest depolarising step with an AP (sweep {first}, dI {dI_all[first] if first is not None else float('nan'):.0f} pA); {'peak' if cfg.latency_mode=='peak' else 'threshold'}"
                              f" time minus origin {origin:.2f} ms"))
    # adaptation
    if cfg.adaptation_mode == "scala_ai":
        ai = [(dI_all[s], S.adaptation_index_scala(per[s])) for s in range(len(per)) if per[s] and dI_all[s] > 0]
        ai = [x for x in sorted(ai) if np.isfinite(x[1])]
        sel = ai[:cfg.ai_n_sweeps]
        row["Adaptation"] = float(np.median([x[1] for x in sel])) if sel else 0.0
        dec.append(("Adaptation", f"Scala 2019: ISI2/ISI1 per sweep (>= 3 spikes), median over the {cfg.ai_n_sweeps} lowest "
                                  f"spiking depolarising sweeps ({len(sel)} available); ratio, not percent"))
    else:
        mx = counts.max()
        k = min((s_ for s_ in range(len(counts)) if counts[s_] == mx), key=lambda s_: (dI_all[s_] <= 0, dI_all[s_])) if mx > 0 else None
        row["Adaptation"] = S.sfa_divisor(per[k]) if k is not None else np.nan
        dec.append(("Adaptation", f"EE spike-frequency adaptation, divisor method (first ISI / last ISI) on the smallest-current sweep with "
                                  f"the maximum AP count (sweep {k}, {int(counts.max())} APs); ISI between AP peaks. Matches "
                                  "the reference value exactly; the Scala ISI2/ISI1 index is available as 'scala_ai'"))
    # rheobase
    rheo_info = {}
    if cfg.rheobase_mode == "scala":
        dur_s = (b - a) / 1000.0
        rheo, rheo_info = S.rheobase_scala(counts, dI_all, dur_s)
        dec.append(("Rheobase", f"Scala 2019: deterministic RANSAC of rate vs dI over the 5 lowest spiking steps, "
                                f"x-intercept clipped to the sub/supra-threshold interval; mode = {rheo_info.get('mode')}"))
    elif cfg.rheobase_mode == "record":
        rheo, _ = S.rheobase_record(per, dI_all)
        dec.append(("Rheobase", "EE 'Record': dI of the first sweep with an AP"))
    else:
        bl = np.array([window_mean(t, I[s], t[0], a) for s in range(V.shape[0])])
        rheo, _ = S.rheobase_exact(per, dI_all, I, bl)
        dec.append(("Rheobase", "EE 'Exact': Im at the sample of the first AP peak minus the Im baseline (mean from the start "
                                "of the sweep to the step start)"))
    row["Rheobase (pA)"] = rheo

    # --------------------------------------------------------------------------------------- kinetics
    kin = None
    if first_spiking := next((s for s in order if per[s]), None):
        kin = K.analyse_ap(t, V[first_spiking], per[first_spiking][0].peak_idx, cfg.kin)
    if kin and kin.ok:
        row["Amplitude (mV)"], row["Threshold (mV)"] = kin.amplitude, kin.thr_v
        row["Corr Threshold"] = kin.thr_v - cfg.ljp_mV
        row["Rise Time (ms)"], row["Decay Time (ms)"], row["Half-Width (ms)"] = kin.rise_ms, kin.decay_ms, kin.half_width_ms
        row["fAHP (mV)"], row["mAHP (mV)"] = kin.fahp, kin.mahp
    kp = cfg.kin
    dec.append(("AP kinetics", f"first AP of the smallest depolarising step with an AP (sweep {first_spiking}); threshold method {kp.thr_method} "
                               f"(lower bound {kp.method_II_lower if kp.thr_method=='method_II' else kp.method_I_lower} mV/ms), "
                               f"search {kp.thr_search} ms before peak; rise {kp.rise_pct}, decay {kp.decay_pct} of "
                               f"peak->{'threshold' if kp.decay_to_thr else 'fAHP'}; 200 kHz interpolation "
                               f"{'on' if kp.interp else 'off'}; fAHP window {kp.fahp} ms, mAHP window {kp.mahp} ms after "
                               f"the peak, both relative to threshold"))
    # ------------------------------------------------------------------------------------------- RMP
    if spont_path:
        r2 = load_abf(spont_path)
        v0 = r2.vm()[0]
        row["RMP original"] = float(v0.mean())
        dec.append(("RMP", f"mean Vm of sweep 0 of {spont_path.split('/')[-1]} (whole sweep)"))
    else:
        row["RMP original"] = pas.sweeps[int(np.argmin(np.abs(dI_all)))].v_base
        dec.append(("RMP", "baseline Vm of the sweep with the smallest |dI| in the step family (no spontaneous file given)"))
    row["RMP corrected"] = row["RMP original"] - cfg.ljp_mV
    dec.append(("Liquid junction", f"{cfg.ljp_mV} mV subtracted from RMP and threshold (user setting)"))
    detail = dict(passive=pas, spikes=per, counts=counts, dI=dI_all, kinetics=kin, rheobase=rheo_info, thr_line=thr_line)
    return CellReport(row, dec, detail)


def compare(row: Dict[str, object], ref: Dict[str, float]):
    out = []
    for k, rv in ref.items():
        v = row.get(k, np.nan)
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = np.nan
        d = v - rv
        out.append((k, v, rv, d, "OK" if abs(d) <= max(1e-3 * abs(rv), 5e-4) else ("close" if abs(d) <= 0.02 * abs(rv) else "DIFF")))
    return out
