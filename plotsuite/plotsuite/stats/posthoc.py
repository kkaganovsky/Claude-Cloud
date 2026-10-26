"""Multiple-comparison procedures built on scipy / statsmodels / scikit-posthocs / pingouin."""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd
import scipy.stats as st
from statsmodels.stats.multitest import multipletests

from .core import Comparison, stars

PARAMETRIC = ["none", "tukey", "dunnett", "bonferroni", "sidak", "holm-sidak", "lsd", "snk", "trend"]
UNEQUAL_VAR = ["none", "games-howell", "tamhane-t2"]


def _pairs(names, control=None):
    if control:
        return [(control, n) for n in names if n != control]
    return list(itertools.combinations(names, 2))


def _frame(rows):
    cols = ["Comparison", "Mean diff", "CI low", "CI high", "Adjusted P", "Summary"]
    return pd.DataFrame(rows, columns=cols)


def _row(a, b, d, lo, hi, p):
    return [f"{a} vs. {b}", d, lo, hi, p, stars(p)]


def pooled(groups: dict):
    k = len(groups)
    N = sum(len(v) for v in groups.values())
    sse = sum(((v - v.mean()) ** 2).sum() for v in groups.values())
    df = N - k
    return sse / df, df


def parametric(groups: dict, method: str, control=None, alpha=0.05):
    """Post-tests after an ordinary one-way ANOVA (pooled error term). Returns (table, [Comparison])."""
    names = list(groups)
    means = {n: groups[n].mean() for n in names}
    mse, df = pooled(groups)
    cmps, rows = [], []
    if method == "tukey":
        r = st.tukey_hsd(*[groups[n] for n in names])
        ci = r.confidence_interval(1 - alpha)
        for i, j in itertools.combinations(range(len(names)), 2):
            p = float(r.pvalue[i, j])
            rows.append(_row(names[i], names[j], means[names[i]] - means[names[j]], ci.low[i, j], ci.high[i, j], p))
            cmps.append(Comparison(names[i], names[j], p, means[names[i]] - means[names[j]]))
    elif method == "dunnett":
        others = [n for n in names if n != control]
        r = st.dunnett(*[groups[n] for n in others], control=groups[control])
        ci = r.confidence_interval(1 - alpha)
        for k, n in enumerate(others):
            p = float(r.pvalue[k])
            d = means[n] - means[control]
            rows.append(_row(n, control, d, ci.low[k], ci.high[k], p))
            cmps.append(Comparison(n, control, p, d))
    elif method in ("bonferroni", "sidak", "holm-sidak", "lsd"):
        prs = _pairs(names, control)
        raw, ds_, ses = [], [], []
        for a, b in prs:
            d = means[a] - means[b]
            se = np.sqrt(mse * (1 / len(groups[a]) + 1 / len(groups[b])))
            raw.append(2 * st.t.sf(abs(d / se), df))
            ds_.append(d); ses.append(se)
        m = len(prs)
        meth = {"bonferroni": "bonferroni", "sidak": "sidak", "holm-sidak": "holm-sidak", "lsd": None}[method]
        adj = np.array(raw) if meth is None else multipletests(raw, alpha=alpha, method=meth)[1]
        if method == "bonferroni":
            a_ci = alpha / m
        elif method == "sidak":
            a_ci = 1 - (1 - alpha) ** (1 / m)
        elif method == "lsd":
            a_ci = alpha
        else:
            a_ci = None
        for (a, b), d, se, p in zip(prs, ds_, ses, adj):
            if a_ci is None:
                lo = hi = np.nan
            else:
                tc = st.t.ppf(1 - a_ci / 2, df)
                lo, hi = d - tc * se, d + tc * se
            rows.append(_row(a, b, d, lo, hi, float(p)))
            cmps.append(Comparison(a, b, float(p), d))
    elif method == "snk":
        order = sorted(names, key=lambda n: means[n])
        pos = {n: i for i, n in enumerate(order)}
        res = {}
        for a, b in itertools.combinations(names, 2):
            r = abs(pos[a] - pos[b]) + 1
            se = np.sqrt(mse / 2 * (1 / len(groups[a]) + 1 / len(groups[b])))
            q = abs(means[a] - means[b]) / se
            res[(a, b)] = (st.studentized_range.sf(q, r, df), r)
        # step-down: a pair can't be more significant than a pair that spans it
        for (a, b), (p, r) in list(res.items()):
            for (c, d), (p2, r2) in res.items():
                lo_, hi_ = sorted((pos[a], pos[b])); lo2, hi2 = sorted((pos[c], pos[d]))
                if lo2 <= lo_ and hi_ <= hi2 and r2 > r:
                    p = max(p, p2)
            res[(a, b)] = (p, r)
        for (a, b), (p, r) in res.items():
            d = means[a] - means[b]
            rows.append(_row(a, b, d, np.nan, np.nan, float(p)))
            cmps.append(Comparison(a, b, float(p), d))
    elif method == "trend":
        k = len(names)
        w = np.arange(k) - (k - 1) / 2
        L = sum(wi * means[n] for wi, n in zip(w, names))
        se = np.sqrt(mse * sum(wi ** 2 / len(groups[n]) for wi, n in zip(w, names)))
        t = L / se
        p = 2 * st.t.sf(abs(t), df)
        slope = L / np.sum(w ** 2)
        tc = st.t.ppf(1 - alpha / 2, df)
        return pd.DataFrame([{"Test": "Linear trend across the column order", "Slope per column step": slope,
                              "CI low": (L - tc * se) / np.sum(w ** 2), "CI high": (L + tc * se) / np.sum(w ** 2),
                              "t": t, "df": df, "P": p}]), []
    else:
        raise ValueError(method)
    return _frame(rows), cmps


def unequal_var(groups: dict, method: str, alpha=0.05):
    import pingouin as pg
    import scikit_posthocs as sp
    names = list(groups)
    cmps, rows = [], []
    if method == "games-howell":
        long = pd.DataFrame({"v": np.concatenate([groups[n] for n in names]),
                             "g": np.concatenate([[n] * len(groups[n]) for n in names])})
        gh = pg.pairwise_gameshowell(data=long, dv="v", between="g")
        for _, r in gh.iterrows():
            p = float(r["pval"]); d = float(r["diff"])
            rows.append(_row(r["A"], r["B"], d, np.nan, np.nan, p))
            cmps.append(Comparison(r["A"], r["B"], p, d))
    elif method == "tamhane-t2":
        P = sp.posthoc_tamhane([groups[n] for n in names])
        for i, j in itertools.combinations(range(len(names)), 2):
            p = float(P.iloc[i, j]); d = groups[names[i]].mean() - groups[names[j]].mean()
            rows.append(_row(names[i], names[j], d, np.nan, np.nan, p))
            cmps.append(Comparison(names[i], names[j], p, d))
    else:
        raise ValueError(method)
    return _frame(rows), cmps


def dunn(groups: dict, adjust="bonferroni", control=None):
    """Dunn's test after Kruskal-Wallis (tie-corrected z, scikit-posthocs)."""
    import scikit_posthocs as sp
    names = list(groups)
    P = sp.posthoc_dunn([groups[n] for n in names], p_adjust=adjust)
    allv = np.concatenate([groups[n] for n in names])
    ranks = st.rankdata(allv)
    mr, i0 = {}, 0
    for n in names:
        mr[n] = ranks[i0:i0 + len(groups[n])].mean(); i0 += len(groups[n])
    rows, cmps = [], []
    prs = _pairs(names, control)
    if control and adjust != "none":  # re-adjust for the smaller family when comparing to control
        raw = sp.posthoc_dunn([groups[n] for n in names], p_adjust=None)
        pv = {(a, b): float(raw.iloc[names.index(a), names.index(b)]) for a, b in prs}
        adj = multipletests(list(pv.values()), method={"bonferroni": "bonferroni", "holm": "holm", "sidak": "sidak"}.get(adjust, "bonferroni"))[1]
        pmap = dict(zip(pv, adj))
    else:
        pmap = {(a, b): float(P.iloc[names.index(a), names.index(b)]) for a, b in prs}
    for a, b in prs:
        p = pmap[(a, b)]
        rows.append([f"{a} vs. {b}", mr[a] - mr[b], p, stars(p)])
        cmps.append(Comparison(a, b, p, mr[a] - mr[b]))
    return pd.DataFrame(rows, columns=["Comparison", "Rank sum diff (mean ranks)", "Adjusted P", "Summary"]), cmps


def paired_pairwise(matrix: pd.DataFrame, adjust="holm-sidak", control=None, alpha=0.05):
    """Paired t-tests on every pair of RM columns with multiplicity correction."""
    names = list(matrix.columns)
    prs = _pairs(names, control)
    raw, dif = [], []
    for a, b in prs:
        d = matrix[a] - matrix[b]
        dif.append(d.mean())
        raw.append(st.ttest_rel(matrix[a], matrix[b]).pvalue)
    meth = {"none": None, "bonferroni": "bonferroni", "sidak": "sidak", "holm-sidak": "holm-sidak"}[adjust]
    adj = np.array(raw) if meth is None else multipletests(raw, method=meth)[1]
    rows, cmps = [], []
    for (a, b), d, p in zip(prs, dif, adj):
        rows.append(_row(a, b, d, np.nan, np.nan, float(p)))
        cmps.append(Comparison(a, b, float(p), d))
    return _frame(rows), cmps


def dunn_friedman(matrix: pd.DataFrame, adjust="bonferroni", control=None):
    """Dunn's test on Friedman rank sums."""
    names = list(matrix.columns)
    n, k = matrix.shape
    R = matrix.rank(axis=1).sum()
    prs = _pairs(names, control)
    raw = [2 * st.norm.sf(abs(R[a] - R[b]) / np.sqrt(n * k * (k + 1) / 6)) for a, b in prs]
    meth = {"none": None, "bonferroni": "bonferroni", "holm": "holm", "sidak": "sidak"}[adjust]
    adj = np.array(raw) if meth is None else multipletests(raw, method=meth)[1]
    rows, cmps = [], []
    for (a, b), p in zip(prs, adj):
        rows.append([f"{a} vs. {b}", R[a] - R[b], float(p), stars(p)])
        cmps.append(Comparison(a, b, float(p), float(R[a] - R[b])))
    return pd.DataFrame(rows, columns=["Comparison", "Rank sum diff", "Adjusted P", "Summary"]), cmps
