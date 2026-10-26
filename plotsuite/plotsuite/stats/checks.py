"""Reusable guardrails. Each returns a list of Issue."""
from __future__ import annotations

import numpy as np
import scipy.stats as st

from ..data import Dataset
from .core import Issue, err, warn, info


def groups_of(ds: Dataset, names=None) -> dict:
    g = ds.group_arrays()
    if names:
        missing = [n for n in names if n not in g]
        if missing:
            raise KeyError(f"Group(s) not found or empty: {', '.join(missing)}")
        g = {n: g[n] for n in names}
    return g


def n_groups(g: dict, lo: int, hi: int | None = None, hint: str = "") -> list[Issue]:
    n = len(g)
    if n < lo:
        return [err(f"Needs at least {lo} group(s) with data; found {n}. {hint}".strip())]
    if hi is not None and n > hi:
        return [err(f"Needs at most {hi} group(s); you selected {n}. {hint}".strip())]
    return []


def min_n(g: dict, k: int, what: str = "") -> list[Issue]:
    small = [f"{n} (n={len(v)})" for n, v in g.items() if len(v) < k]
    return [err(f"Every group needs at least {k} values{what}: {', '.join(small)}.")] if small else []


def nonconstant(g: dict) -> list[Issue]:
    c = [n for n, v in g.items() if len(v) > 1 and np.ptp(v) == 0]
    return [warn(f"Group(s) with zero variance (all values identical): {', '.join(c)}; SD-based results are degenerate.")] if c else []


def finite(g: dict) -> list[Issue]:
    return []


def positive(g: dict, what="This test") -> list[Issue]:
    bad = [n for n, v in g.items() if np.any(v <= 0)]
    return [err(f"{what} requires all values > 0; check: {', '.join(bad)}.")] if bad else []


def normality(g: dict, alpha=0.05) -> list[Issue]:
    out = []
    for n, v in g.items():
        if len(v) < 8:
            out.append(info(f"'{n}': n={len(v)} is too small for a reliable normality test; judge normality from the biology/prior data."))
        elif len(v) <= 5000 and np.ptp(v) > 0:
            p = st.shapiro(v).pvalue
            if p < alpha:
                out.append(warn(f"'{n}' fails Shapiro-Wilk normality (p={p:.3g}). Consider a non-parametric test or a transform."))
    return out


def equal_variance(g: dict, alpha=0.05) -> list[Issue]:
    arrs = [v for v in g.values() if len(v) > 1]
    if len(arrs) < 2 or any(np.ptp(v) == 0 for v in arrs):
        return []
    p = st.levene(*arrs, center="median").pvalue
    return [warn(f"Variances differ between groups (Brown-Forsythe p={p:.3g}). Use a Welch / Brown-Forsythe test instead of the equal-variance version.")] if p < alpha else []


def paired_rows(ds: Dataset, names, allow_missing=False) -> list[Issue]:
    m = ds.numeric(names)
    out = []
    if len(m) < 2:
        return [err("Paired analysis needs at least 2 rows (subjects).")]
    na = m.isna()
    partial = na.any(axis=1) & ~na.all(axis=1)
    if partial.any():
        rows = ", ".join(str(i + 1) for i in np.where(partial)[0][:6])
        msg = f"Matched rows have missing values (row {rows}). In a paired design each row is one subject, so every group needs a value."
        out.append(warn(msg + " Incomplete rows will be dropped.") if allow_missing else err(msg + " Fill them in or remove those rows."))
    return out


def ties_note(g: dict) -> list[Issue]:
    allv = np.concatenate(list(g.values()))
    if len(np.unique(allv)) < len(allv):
        return [info("Tied values present; the tie-corrected version of the rank test is used.")]
    return []


def integers(arr, what="counts") -> list[Issue]:
    a = np.asarray(arr, dtype=float)
    if np.isnan(a).any():
        return [err(f"Missing/non-numeric {what}; every cell must be a number.")]
    if (a < 0).any() or not np.allclose(a, np.round(a)):
        return [err(f"{what.capitalize()} must be non-negative whole numbers (not percentages or means).")]
    return []
