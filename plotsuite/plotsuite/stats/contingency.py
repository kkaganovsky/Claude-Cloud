"""Contingency-table tests."""
from __future__ import annotations

import numpy as np
import pandas as pd
import scipy.stats as st
from statsmodels.stats.contingency_tables import Table, Table2x2, mcnemar

from ..data import Dataset
from . import checks as CK
from .core import Option, Result, checker, err, fmt_P, fmt_p, info, register, warn

ALT = Option("alternative", "Tails", "select", "two-sided", ["two-sided", "less", "greater"])
CI = Option("ci", "Confidence level (%)", "float", 95.0)


def _tab_check(ds, shape=None, min_rows=2, min_cols=2):
    if len(ds.df.columns) < 2:
        return [err("Needs a row-label column plus at least one count column.")]
    c = ds.counts()
    out = CK.integers(c.to_numpy(dtype=float), "counts")
    if out:
        return out
    if shape and c.shape != shape:
        return [err(f"This test needs a {shape[0]}x{shape[1]} table; the table is {c.shape[0]}x{c.shape[1]}.")]
    if c.shape[0] < min_rows or c.shape[1] < min_cols:
        return [err(f"Need at least {min_rows} rows and {min_cols} columns of counts.")]
    if (c.sum(axis=0) == 0).any() or (c.sum(axis=1) == 0).any():
        return [err("A row or column has a total of zero; remove it.")]
    return []


@register("fisher_exact", "Fisher's exact test (2x2)", "Contingency", ["contingency"], [ALT, CI], prism="Fisher's exact test")
def fisher(ds, alternative="two-sided", ci=95.0):
    c = ds.counts()
    r = st.fisher_exact(c.to_numpy(), alternative=alternative)
    tab = Table2x2(c.to_numpy() + 0.0)
    lo, hi = tab.oddsratio_confint(alpha=1 - ci / 100)
    return Result("Fisher's exact test", f"{fmt_P(r.pvalue)} ({alternative}); sample odds ratio {r.statistic:.4g}.",
                  {"Fisher": pd.DataFrame({"Value": {"P": r.pvalue, "Odds ratio (sample)": r.statistic, "OR CI low (Woolf)": lo, "OR CI high (Woolf)": hi}})},
                  p=float(r.pvalue), methods="scipy.stats.fisher_exact; OR CI via statsmodels Table2x2 (log-OR normal approximation).")


checker("fisher_exact")(lambda ds, o: _tab_check(ds, (2, 2)))


@register("chi_square", "Chi-square test of independence (r x c)", "Contingency", ["contingency"],
          [Option("correction", "Yates' continuity correction (2x2 only)", "bool", False)], prism="Chi-square test")
def chisq(ds, correction=False):
    c = ds.counts()
    chi, p, dof, exp = st.chi2_contingency(c.to_numpy(), correction=correction)
    cv = np.sqrt(chi / (c.to_numpy().sum() * (min(c.shape) - 1)))
    return Result("Chi-square test", f"Chi-square({dof}) = {chi:.3f}, {fmt_P(p)}.",
                  {"Chi-square": pd.DataFrame({"Value": {"Chi-square": chi, "DF": dof, "P": p, "Cramer's V": cv}}),
                   "Expected counts": pd.DataFrame(exp, index=c.index, columns=c.columns)}, p=float(p),
                  methods="scipy.stats.chi2_contingency.")


@checker("chi_square")
def _(ds, o):
    out = _tab_check(ds)
    if out:
        return out
    c = ds.counts().to_numpy(dtype=float)
    exp = np.outer(c.sum(1), c.sum(0)) / c.sum()
    frac = (exp < 5).mean()
    if exp.min() < 1 or frac > 0.2:
        out.append(warn(f"{frac:.0%} of cells have expected count < 5 (min {exp.min():.2f}); the chi-square approximation is unreliable. Use Fisher's exact test (2x2) or merge categories."))
    if c.sum() < 20:
        out.append(warn("Total count < 20: chi-square is unreliable."))
    return out


@register("chi_square_trend", "Chi-square test for trend", "Contingency", ["contingency"], [], prism="Chi-square test for trend")
def chisq_trend(ds):
    c = ds.counts()
    tb = Table(c.to_numpy(dtype=float))
    r = tb.test_ordinal_association(row_scores=np.arange(c.shape[0]), col_scores=np.arange(c.shape[1]))
    return Result("Chi-square test for trend", f"Linear-by-linear association: z = {r.zscore:.3f}, {fmt_P(r.pvalue)}.",
                  {"Trend": pd.DataFrame({"Value": {"z": r.zscore, "P": r.pvalue}})}, p=float(r.pvalue),
                  methods="statsmodels.stats.contingency_tables.Table.test_ordinal_association (rows and columns scored 0,1,2,...).")


@checker("chi_square_trend")
def _(ds, o):
    return _tab_check(ds) + [info("Rows (and columns) must be in a meaningful order; equally spaced scores 0,1,2,... are used.")]


@register("mcnemar", "McNemar's test (paired 2x2)", "Contingency", ["contingency"],
          [Option("exact", "Exact (binomial) test", "bool", True)], prism="McNemar's test")
def mc(ds, exact=True):
    r = mcnemar(ds.counts().to_numpy(dtype=float), exact=exact, correction=not exact)
    return Result("McNemar's test", f"{fmt_P(r.pvalue)}.", {"McNemar": pd.DataFrame({"Value": {"Statistic": r.statistic, "P": r.pvalue}})}, p=float(r.pvalue),
                  methods="statsmodels.stats.contingency_tables.mcnemar.")


@checker("mcnemar")
def _(ds, o):
    out = _tab_check(ds, (2, 2))
    if not out:
        c = ds.counts().to_numpy()
        if c[0, 1] + c[1, 0] == 0:
            out.append(err("No discordant pairs; McNemar's test is undefined."))
        out.append(info("Rows = outcome before, columns = outcome after, for the SAME subjects. Use Fisher/chi-square for independent groups."))
    return out


@register("rr_or", "Relative risk, odds ratio, sensitivity/specificity (2x2)", "Contingency", ["contingency"], [CI],
          prism="Relative risk and odds ratio")
def rr_or(ds, ci=95.0):
    c = ds.counts().to_numpy(dtype=float)
    a = 1 - ci / 100
    tb = Table2x2(c)
    rr_lo, rr_hi = tb.riskratio_confint(alpha=a); or_lo, or_hi = tb.oddsratio_confint(alpha=a)
    tabs = {"Ratios": pd.DataFrame({"Estimate": [tb.riskratio, tb.oddsratio], "CI low": [rr_lo, or_lo], "CI high": [rr_hi, or_hi]},
                                   index=["Relative risk (row 1 vs row 2, col 1 = outcome)", "Odds ratio"])}
    rows = {}
    for lab, k, n in [("Sensitivity", c[0, 0], c[0].sum()), ("Specificity", c[1, 1], c[1].sum()),
                      ("Positive predictive value", c[0, 0], c[:, 0].sum()), ("Negative predictive value", c[1, 1], c[:, 1].sum())]:
        b = st.binomtest(int(k), int(n)).proportion_ci(confidence_level=ci / 100, method="exact")
        rows[lab] = (k / n, b.low, b.high)
    tabs["Diagnostic measures (rows = test result, cols = truth)"] = pd.DataFrame(rows, index=["Estimate", "CI low (Clopper-Pearson)", "CI high"]).T
    return Result("Relative risk / odds ratio", f"RR = {tb.riskratio:.3f}, OR = {tb.oddsratio:.3f}.", tabs,
                  methods="statsmodels Table2x2 (log-scale normal CIs); scipy.stats.binomtest exact CIs. Sensitivity/specificity are only meaningful if rows = test result, columns = true status.")


checker("rr_or")(lambda ds, o: _tab_check(ds, (2, 2)) + [info("Zero cells make RR/OR undefined or unstable.")])


@register("chi_square_gof", "Chi-square goodness of fit", "Contingency", ["contingency"],
          [Option("expected", "Expected proportions, comma separated (blank = equal)", "text", "")], prism="Chi-square goodness of fit")
def gof(ds, expected=""):
    c = ds.counts().iloc[:, 0].to_numpy(dtype=float)
    p = np.array([float(x) for x in expected.split(",")]) if expected.strip() else np.ones(len(c))
    p = p / p.sum()
    r = st.chisquare(c, c.sum() * p)
    return Result("Chi-square goodness of fit", f"Chi-square({len(c) - 1}) = {r.statistic:.3f}, {fmt_P(r.pvalue)}.",
                  {"Goodness of fit": pd.DataFrame({"Observed": c, "Expected": c.sum() * p}, index=ds.counts().index)}, p=float(r.pvalue),
                  methods="scipy.stats.chisquare.")


@checker("chi_square_gof")
def _(ds, o):
    out = _tab_check(ds, None, 2, 1)
    if out:
        return out
    c = ds.counts().iloc[:, 0].to_numpy(dtype=float)
    if o.get("expected", "").strip():
        try:
            p = [float(x) for x in o["expected"].split(",")]
        except ValueError:
            return [err("Expected proportions must be numbers.")]
        if len(p) != len(c) or min(p) <= 0:
            return [err(f"Give {len(c)} positive expected proportions.")]
    if c.sum() * (1 / len(c)) < 5:
        out.append(warn("Expected counts are below 5 in some categories; use an exact multinomial test."))
    return out


@register("binomial_test", "Binomial test", "Contingency", ["contingency"],
          [Option("p0", "Hypothetical probability", "float", 0.5), ALT, CI], prism="Binomial test")
def binom(ds, p0=0.5, alternative="two-sided", ci=95.0):
    c = ds.counts().iloc[:, 0].to_numpy(dtype=float)
    k, n = int(c[0]), int(c.sum())
    r = st.binomtest(k, n, p0, alternative=alternative)
    b = r.proportion_ci(confidence_level=ci / 100, method="exact")
    return Result("Binomial test", f"{k}/{n} = {k / n:.4f} vs {p0:g}: {fmt_P(r.pvalue)}.",
                  {"Binomial": pd.DataFrame({"Value": {"Successes (row 1)": k, "Total": n, "Proportion": k / n, "CI low": b.low, "CI high": b.high, "P": r.pvalue}})},
                  p=float(r.pvalue), methods="scipy.stats.binomtest (exact CI).")


@checker("binomial_test")
def _(ds, o):
    out = _tab_check(ds, None, 2, 1)
    if not out and not 0 < (o.get("p0") or 0.5) < 1:
        out.append(err("Hypothetical probability must be between 0 and 1."))
    if not out:
        out.append(info("Row 1 = successes, remaining rows = failures (first count column)."))
    return out
