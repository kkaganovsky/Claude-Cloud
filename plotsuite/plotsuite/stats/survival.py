"""Survival analysis: Kaplan-Meier, log-rank family (incl. trend), Cox PH."""
from __future__ import annotations

import numpy as np
import pandas as pd
import scipy.stats as st
from statsmodels.duration.hazard_regression import PHReg
from statsmodels.duration.survfunc import SurvfuncRight

from ..data import Dataset
from .core import Comparison, Option, Result, checker, err, fmt_P, fmt_p, info, register, warn

WEIGHTS = {"Log-rank (Mantel-Cox)": lambda n: np.ones_like(n, dtype=float),
           "Gehan-Breslow-Wilcoxon": lambda n: n.astype(float),
           "Tarone-Ware": lambda n: np.sqrt(n.astype(float))}


def _surv_check(ds, o, need_groups=1, min_events=1):
    r = ds.roles
    if not r.get("time") or not r.get("event"):
        return [err("Assign the Time and Event columns (Data tab > Roles).")]
    bad = ds.bad_cells([r["time"], r["event"]])
    if bad:
        return [err(f"{len(bad)} non-numeric cell(s) in time/event columns (e.g. row {bad[0][1]}: '{bad[0][2]}').")]
    t = ds.survival()
    out = []
    if len(t) < len(ds.df):
        out.append(warn(f"{len(ds.df) - len(t)} row(s) with missing time/event are ignored."))
    if (t.time < 0).any():
        out.append(err("Negative survival times."))
    if not t.event.isin([0, 1]).all():
        out.append(err("The Event column must be 1 (event occurred) or 0 (censored) only."))
        return out
    g = t.groupby("group")
    if g.ngroups < need_groups:
        out.append(err(f"Need at least {need_groups} groups; found {g.ngroups}."))
    ev = g["event"].sum()
    if (ev == 0).any() and need_groups > 1:
        out.append(warn(f"Group(s) with no events: {', '.join(map(str, ev[ev == 0].index))}. Comparisons involving them are unreliable."))
    if ev.sum() < min_events:
        out.append(err(f"Only {int(ev.sum())} event(s); need at least {min_events}."))
    return out


def km_table(t):
    sf = SurvfuncRight(t.time.to_numpy(), t.event.to_numpy().astype(int))
    times, s, se = sf.surv_times, sf.surv_prob, sf.surv_prob_se
    with np.errstate(divide="ignore", invalid="ignore"):
        z = st.norm.ppf(0.975)
        ll = np.log(-np.log(s))
        sel = se / (s * np.abs(np.log(s)))
        lo = s ** np.exp(z * sel); hi = s ** np.exp(-z * sel)
    below = np.where(s <= 0.5)[0]
    med = float(times[below[0]]) if len(below) else np.nan
    return dict(times=np.r_[0, times], s=np.r_[1, s], lo=np.r_[1, np.nan_to_num(lo, nan=0)], hi=np.r_[1, np.nan_to_num(hi, nan=1)],
                median=med, cens=t[t.event == 0].time.to_numpy())


def at_risk(t, times):
    return [int((t.time >= x).sum()) for x in times]


def logrank_core(t: pd.DataFrame, wfun):
    """Observed, expected, covariance per group (hypergeometric variance), weighted."""
    gs = sorted(t.group.unique(), key=str)
    k = len(gs)
    et = np.unique(t.time[t.event == 1])
    O = np.zeros(k); E = np.zeros(k); V = np.zeros((k, k))
    gi = t.group.map({g: i for i, g in enumerate(gs)}).to_numpy()
    time, ev = t.time.to_numpy(), t.event.to_numpy()
    for x in et:
        at = time >= x
        n_i = np.bincount(gi[at], minlength=k).astype(float)
        n = n_i.sum()
        d_i = np.bincount(gi[(time == x) & (ev == 1)], minlength=k).astype(float)
        d = d_i.sum()
        w = float(wfun(np.array([n]))[0])
        O += w * d_i
        E += w * d * n_i / n
        if n > 1:
            c = w * w * d * (n - d) / (n - 1) / n
            V += c * (np.diag(n_i) - np.outer(n_i, n_i) / n)
    return gs, O, E, V


@register("survival_km", "Survival analysis (Kaplan-Meier + log-rank family)", "Survival", ["survival"],
          [Option("weighting", "Comparison test", "select", "Log-rank (Mantel-Cox)", list(WEIGHTS)),
           Option("trend", "Also test for trend across ordered groups", "bool", False,
                  "Only meaningful when the groups have a natural order (dose, stage ...) - order = alphabetical/entered order."),
           Option("ci", "Confidence level (%)", "float", 95.0)], prism="Survival analysis")
def survival_km(ds, weighting="Log-rank (Mantel-Cox)", trend=False, ci=95.0):
    t = ds.survival()
    gs = sorted(t.group.unique(), key=str)
    curves, summ = {}, {}
    for g in gs:
        sub = t[t.group == g]
        c = km_table(sub)
        curves[g] = c
        summ[g] = {"N": len(sub), "Events": int(sub.event.sum()), "Censored": int((sub.event == 0).sum()), "Median survival": c["median"]}
    tabs = {"Survival summary": pd.DataFrame(summ).T}
    cm, p, msg = [], None, f"{len(gs)} group(s)."
    if len(gs) > 1:
        wf = WEIGHTS[weighting]
        gl, O, E, V = logrank_core(t, wf)
        d = (O - E)[:-1]
        chi = float(d @ np.linalg.solve(V[:-1, :-1], d))
        df = len(gs) - 1
        p = float(st.chi2.sf(chi, df))
        tabs[f"{weighting} comparison"] = pd.DataFrame({"Chi-square": [chi], "DF": [df], "P": [p]})
        tabs["Observed vs expected events"] = pd.DataFrame({"Observed (weighted)": O, "Expected (weighted)": E}, index=gl)
        msg = f"{weighting}: chi-square({df}) = {chi:.3f}, {fmt_P(p)}."
        if trend:
            w = np.arange(len(gs), dtype=float)
            stat = float(w @ (O - E)) ** 2 / float(w @ V @ w)
            pt = float(st.chi2.sf(stat, 1))
            tabs["Test for trend"] = pd.DataFrame({"Chi-square": [stat], "DF": [1], "P": [pt]})
            msg += f" Trend: {fmt_P(pt)}."
        if len(gs) == 2:
            g0, g1, = gs
            gl2, O1, E1, V1 = logrank_core(t, WEIGHTS["Log-rank (Mantel-Cox)"])
            z = st.norm.ppf(1 - (1 - ci / 100) / 2)
            hr_mh = (O1[0] / E1[0]) / (O1[1] / E1[1]) if E1.min() > 0 and O1.min() > 0 else np.nan
            se_mh = np.sqrt(1 / E1[0] + 1 / E1[1])
            lnhr = (O1[0] - E1[0]) / V1[0, 0]; se_p = 1 / np.sqrt(V1[0, 0])
            tabs["Hazard ratio (" + f"{g0} vs {g1}" + ")"] = pd.DataFrame(
                {"HR": [hr_mh, np.exp(lnhr)], "CI low": [hr_mh * np.exp(-z * se_mh), np.exp(lnhr - z * se_p)],
                 "CI high": [hr_mh * np.exp(z * se_mh), np.exp(lnhr + z * se_p)]}, index=["Mantel-Haenszel (O/E ratio)", "Log-rank / Peto one-step"])
        else:
            pass
        cm = [Comparison(gs[0], gs[1], p)] if len(gs) == 2 else []
        # pairwise log-rank (unadjusted, for information) with Bonferroni
        if len(gs) > 2:
            import itertools
            from statsmodels.stats.multitest import multipletests
            rows = []
            for a, b in itertools.combinations(gs, 2):
                sub = t[t.group.isin([a, b])]
                _, O2, E2, V2 = logrank_core(sub, wf)
                dd = (O2 - E2)[:1]; ch = float(dd @ dd / V2[0, 0])
                rows.append((f"{a} vs. {b}", ch, st.chi2.sf(ch, 1)))
            adj = multipletests([r[2] for r in rows], method="holm")[1]
            tabs["Pairwise comparisons (Holm-adjusted)"] = pd.DataFrame({"Comparison": [r[0] for r in rows], "Chi-square": [r[1] for r in rows],
                                                                          "Adjusted P": adj}).set_index("Comparison")
            cm = [Comparison(*r[0].split(" vs. "), float(a)) for r, a in zip(rows, adj)]
    return Result("Survival analysis", msg, tabs, cm, p=p, curves={"km": curves, "t": t, "groups": gs},
                  methods="Kaplan-Meier: statsmodels.duration.survfunc.SurvfuncRight (CI: log-log Greenwood). Log-rank / Gehan-Breslow / Tarone-Ware / trend "
                          "implemented in-house (hypergeometric variance) and cross-checked against statsmodels.survdiff in the test-suite.")


checker("survival_km")(lambda ds, o: _surv_check(ds, o, 1, 1) + (
    [warn("Groups are compared in alphabetical order for the trend test; rename groups (e.g. 1_low, 2_mid, 3_high) if the order matters.")] if o.get("trend") else []))


@register("cox_ph", "Cox proportional-hazards regression", "Survival", ["survival"],
          [Option("covariates", "Covariates (columns)", "columns", None, source="all"), Option("ci", "Confidence level (%)", "float", 95.0)],
          prism="Cox proportional hazards regression")
def cox(ds, covariates=None, ci=95.0):
    r = ds.roles
    covs = covariates or ([r["group"]] if r.get("group") else [])
    d = pd.DataFrame({"time": pd.to_numeric(ds.df[r["time"]], errors="coerce"), "event": pd.to_numeric(ds.df[r["event"]], errors="coerce")})
    terms = []
    for i, c in enumerate(covs):
        col = ds.df[c]
        d[f"v{i}"] = pd.to_numeric(col, errors="coerce") if pd.api.types.is_numeric_dtype(col) else col.astype(str)
        terms.append(f"v{i}" if pd.api.types.is_numeric_dtype(col) else f"C(v{i})")
    d = d.dropna()
    m = PHReg.from_formula("time ~ " + " + ".join(terms), d, status=d.event.to_numpy(), ties="efron").fit()
    z = st.norm.ppf(1 - (1 - ci / 100) / 2)
    names = [str(n) for n in m.model.exog_names]
    for i, c in enumerate(covs):
        names = [n.replace(f"C(v{i})", c).replace(f"v{i}", c) for n in names]
    t = pd.DataFrame({"HR": np.exp(m.params), "CI low": np.exp(m.params - z * m.bse), "CI high": np.exp(m.params + z * m.bse), "coef": m.params,
                      "SE": m.bse, "z": m.tvalues, "P": m.pvalues}, index=names)
    return Result("Cox proportional hazards", f"{len(d)} subjects, {int(d.event.sum())} events.", {"Cox PH": t},
                  warnings=["The proportional-hazards assumption is not tested automatically; inspect log-log survival plots."],
                  methods="statsmodels.duration.hazard_regression.PHReg (Efron ties).")


@checker("cox_ph")
def _(ds, o):
    out = _surv_check(ds, o, 1, 5)
    if any(i.level == "error" for i in out):
        return out
    covs = o.get("covariates") or ([ds.roles["group"]] if ds.roles.get("group") else [])
    if not covs:
        out.append(err("Choose at least one covariate (or assign a Group role)."))
    t = ds.survival()
    ne = int(t.event.sum())
    if covs and ne < 10 * len(covs):
        out.append(warn(f"{ne} events for {len(covs)} covariate(s): fewer than ~10 events per covariate risks an overfit model."))
    for c in covs:
        if c in (ds.roles["time"], ds.roles["event"]):
            out.append(err("A covariate can't be the time or event column."))
    return out
