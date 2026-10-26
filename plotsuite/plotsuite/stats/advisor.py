"""Suggest a suitable test from the *data* (table layout, group count, pairing, normality, variances)."""
from __future__ import annotations

import numpy as np
import scipy.stats as st

from ..data import Dataset
from .core import evaluate


def _normal(g: dict) -> tuple[bool | None, str]:
    small = [n for n, v in g.items() if len(v) < 8]
    bad = [n for n, v in g.items() if 3 <= len(v) <= 5000 and np.ptp(v) > 0 and st.shapiro(v).pvalue < .05]
    if bad:
        return False, f"{', '.join(bad)} fail(s) the Shapiro-Wilk normality test"
    if small:
        return None, f"n<8 in {', '.join(small)}: normality can't be judged from the data"
    return True, "all groups pass the Shapiro-Wilk normality test"


def recommend(ds: Dataset) -> list[dict]:
    out = []

    def add(key, why, opts=None, main=True):
        iss = evaluate(key, ds, opts or {})
        out.append({"key": key, "opts": opts or {}, "why": why, "main": main, "blocked": any(i.level == "error" for i in iss),
                    "issues": [i.message for i in iss if i.level == "error"]})

    k = ds.kind
    if k == "column":
        g = ds.group_arrays(); names = list(g)
        n = len(names)
        if n == 0:
            return []
        norm, why = _normal(g)
        par = norm is not False
        caveat = "" if norm else " (" + why + ")"
        if n == 1:
            add("one_sample_t" if par else "wilcoxon_one_sample", f"One group vs a hypothetical value; {why}.", {"group_a": names[0]})
        elif n == 2:
            a, b = names
            if ds.paired:
                add("paired_t" if par else "wilcoxon_paired", f"Two matched groups; {why}.", {"group_a": a, "group_b": b})
                add("wilcoxon_paired" if par else "paired_t", "Alternative.", {"group_a": a, "group_b": b}, False)
            else:
                eq = st.levene(*g.values(), center="median").pvalue > .05 if all(len(v) > 1 and np.ptp(v) > 0 for v in g.values()) else True
                if par and eq:
                    add("unpaired_t", f"Two independent groups; {why}; variances similar.", {"group_a": a, "group_b": b})
                elif par:
                    add("welch_t", f"Two independent groups; variances differ (Brown-Forsythe P<0.05).", {"group_a": a, "group_b": b})
                else:
                    add("mann_whitney", f"Two independent groups; {why}.", {"group_a": a, "group_b": b})
                add("mann_whitney" if par else "welch_t", "Alternative.", {"group_a": a, "group_b": b}, False)
        else:
            if ds.paired:
                if par:
                    add("rm_anova", f"{n} matched groups; {why}.", {"posthoc": "holm-sidak"})
                    add("friedman", "Non-parametric alternative.", {"posthoc": "dunn"}, False)
                else:
                    add("friedman", f"{n} matched groups; {why}.", {"posthoc": "dunn"})
            else:
                eq = st.levene(*g.values(), center="median").pvalue > .05 if all(len(v) > 1 and np.ptp(v) > 0 for v in g.values()) else True
                if par and eq:
                    add("oneway_anova", f"{n} independent groups; {why}; variances similar. Tukey compares every pair.", {"posthoc": "tukey"})
                    add("kruskal", "Non-parametric alternative.", {"posthoc": "dunn"}, False)
                elif par:
                    add("welch_anova", f"{n} independent groups; variances differ.", {"posthoc": "games-howell"})
                else:
                    add("kruskal", f"{n} independent groups; {why}.", {"posthoc": "dunn"})
                    add("welch_anova", "Parametric alternative if you can justify normality or transform the data.", {"posthoc": "games-howell"}, False)
        add("descriptive", "Summary statistics.", None, False)
    elif k == "grouped":
        if ds.roles.get("subject"):
            add("two_way_rm", "Two factors with repeated measures on subjects.", {"posthoc": "A within each B"})
            add("mixed_effects", "Use instead if some subjects have missing values.", None, False)
        else:
            add("two_way_anova", "Two factors, independent subjects.", {"posthoc": "A within each B"})
        add("multiple_t", "Alternative: one t test per row when factor B has exactly two levels.", None, False)
    elif k == "xy":
        add("nonlinear_fit", "Fit a curve (choose the equation).", None)
        add("linear_regression", "Straight-line fit.", None, False)
        add("correlation_xy", "If neither variable is controlled.", None, False)
    elif k == "survival":
        add("survival_km", "Kaplan-Meier curves and log-rank comparison.", None)
        add("cox_ph", "Adjust for covariates.", None, False)
    elif k == "contingency":
        c = ds.counts().to_numpy(dtype=float)
        if c.shape == (2, 2):
            exp_min = (np.outer(c.sum(1), c.sum(0)) / c.sum()).min() if c.sum() else 0
            add("fisher_exact" if exp_min < 5 else "chi_square", f"2x2 table; smallest expected count {exp_min:.1f}.")
            add("rr_or", "Effect sizes.", None, False)
        else:
            add("chi_square", "r x c table.")
            add("chi_square_trend", "If categories are ordered.", None, False)
    return out
