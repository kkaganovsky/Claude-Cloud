"""Tests for column tables: descriptive, normality, t-tests, rank tests, one-way ANOVA families."""
from __future__ import annotations

import numpy as np
import pandas as pd
import scipy.stats as st
from statsmodels.stats.oneway import anova_oneway

from ..data import Dataset
from . import checks as C
from . import posthoc as PH
from .core import (Comparison, Option, Result, err, fmt_P, fmt_p, info, register, table, warn)

ALT = Option("alternative", "Tails", "select", "two-sided", ["two-sided", "less", "greater"],
             "One-tailed only if the direction was predicted before seeing the data. 'less' means first group < second group.")
ALPHA = Option("alpha", "Significance level (alpha)", "float", 0.05)
GA = Option("group_a", "First group", "group", None)
GB = Option("group_b", "Second group", "group", None)
GROUPS = Option("groups", "Groups to include", "groups", None)
CONTROL = Option("control", "Control group (if comparing to a control)", "group", None)


def selected(ds: Dataset, opts: dict) -> dict:
    return C.groups_of(ds, opts.get("groups") or None)


def two(ds: Dataset, opts: dict):
    a, b = opts.get("group_a"), opts.get("group_b")
    g = ds.group_arrays()
    if a and b:
        if a == b:
            raise ValueError("Choose two different groups.")
        return a, b, g[a], g[b]
    if len(g) == 2:
        (a, x), (b, y) = g.items()
        return a, b, x, y
    raise ValueError(f"This table has {len(g)} groups: pick exactly two (First/Second group). "
                     "For 3+ groups use ANOVA / Kruskal-Wallis with multiple comparisons - repeated t-tests inflate false positives.")


def _two_check(ds, o, need_n=2, paired=False, parametric=True, equal_var=False):
    try:
        a, b, x, y = two(ds, o)
    except Exception as e:
        return [err(str(e))]
    g = {a: x, b: y}
    out = C.min_n(g, need_n)
    if paired:
        if not ds.paired:
            out.append(err("This test needs matched rows. Tick 'Rows are matched subjects' for this table (Data tab) if each row is the same subject/pair."))
        out += C.paired_rows(ds, [a, b], allow_missing=True)
    elif ds.paired:
        out.append(warn("Table is marked as matched but an unpaired test ignores the pairing; use the paired test unless groups are truly independent."))
    if o.get("alternative", "two-sided") != "two-sided":
        out.append(info("One-tailed test: valid only if the direction was fixed before looking at the data."))
    if parametric:
        out += C.normality(g, o.get("alpha", .05)) if not paired else []
        out += C.nonconstant(g)
        if equal_var:
            out += C.equal_variance(g, o.get("alpha", .05))
    return out


# ------------------------------------------------------------------ descriptive
@register("descriptive", "Column statistics (descriptive)", "Descriptive", ["column"],
          [Option("ci", "Confidence level (%)", "float", 95.0)], prism="Column statistics")
def descriptive(ds, ci=95.0):
    rows = {}
    for n, v in ds.group_arrays().items():
        k = len(v)
        sd = v.std(ddof=1) if k > 1 else np.nan
        sem = sd / np.sqrt(k) if k > 1 else np.nan
        tc = st.t.ppf(1 - (1 - ci / 100) / 2, k - 1) if k > 1 else np.nan
        rows[n] = {
            "N": k, "Mean": v.mean(), "SD": sd, "SEM": sem,
            f"{ci:g}% CI low": v.mean() - tc * sem, f"{ci:g}% CI high": v.mean() + tc * sem,
            "Median": np.median(v), "25th pct": np.percentile(v, 25), "75th pct": np.percentile(v, 75),
            "Min": v.min(), "Max": v.max(), "Sum": v.sum(),
            "CV %": sd / v.mean() * 100 if k > 1 and v.mean() != 0 else np.nan,
            "Skewness": st.skew(v, bias=False) if k > 2 else np.nan,
            "Kurtosis (excess)": st.kurtosis(v, bias=False) if k > 3 else np.nan,
            "Geometric mean": st.gmean(v) if (v > 0).all() else np.nan,
        }
    t = pd.DataFrame(rows)
    return Result("Column statistics", f"Descriptive statistics for {len(rows)} column(s).", {"Statistics": t},
                  methods="numpy/scipy.stats descriptive statistics; CI of mean uses the t distribution.")


@register("normality", "Normality tests", "Descriptive", ["column"],
          [ALPHA], "Shapiro-Wilk, D'Agostino-Pearson, Anderson-Darling and Lilliefors (K-S) for each column.",
          prism="Normality and lognormality tests")
def normality(ds, alpha=0.05):
    from statsmodels.stats.diagnostic import lilliefors
    rows = []
    for n, v in ds.group_arrays().items():
        r = {"Group": n, "N": len(v)}
        const = np.ptp(v) == 0
        def put(name, f, minn):
            if len(v) < minn or const:
                r[name + " P"] = np.nan
                r[name + " normal?"] = f"n/a (need n>={minn})" if len(v) < minn else "n/a"
                return
            p = float(f())
            r[name + " P"] = p
            r[name + " normal?"] = "Yes" if p > alpha else "No"
        put("Shapiro-Wilk", lambda: st.shapiro(v).pvalue, 3)
        put("D'Agostino-Pearson", lambda: st.normaltest(v).pvalue, 8)
        put("Anderson-Darling", lambda: st.anderson(v, "norm", method="interpolate").pvalue, 8)
        put("Lilliefors (KS)", lambda: lilliefors(v, dist="norm", pvalmethod="approx")[1], 5)
        rows.append(r)
    return Result("Normality tests", "P > alpha means no evidence against normality; it does not prove normality, especially for small n.",
                  {"Normality": pd.DataFrame(rows).set_index("Group")},
                  methods="scipy.stats.shapiro / normaltest / anderson(method='interpolate'); statsmodels lilliefors.")


@register("variance_tests", "Compare variances (Bartlett / Brown-Forsythe / F)", "Descriptive", ["column"], [GROUPS],
          prism="Test for equal variances")
def variance_tests(ds, groups=None):
    g = C.groups_of(ds, groups)
    arrs = list(g.values())
    rows = [("Brown-Forsythe (Levene, median)", *st.levene(*arrs, center="median")),
            ("Levene (mean)", *st.levene(*arrs, center="mean")), ("Bartlett (assumes normality)", *st.bartlett(*arrs))]
    t = pd.DataFrame(rows, columns=["Test", "Statistic", "P"]).set_index("Test")
    tables = {"Equality of variances": t}
    if len(arrs) == 2:
        x, y = arrs
        F = x.var(ddof=1) / y.var(ddof=1)
        dfn, dfd = len(x) - 1, len(y) - 1
        p = min(1.0, 2 * min(st.f.sf(F, dfn, dfd), st.f.cdf(F, dfn, dfd)))
        tables["F test"] = pd.DataFrame({"F": [F], "DFn": [dfn], "DFd": [dfd], "P": [p]})
    return Result("Compare variances", "P < alpha suggests unequal variances.", tables,
                  methods="scipy.stats.levene / bartlett; F test from scipy.stats.f.")


from .core import checker  # noqa: E402


@checker("variance_tests")
def _(ds, o):
    g = C.groups_of(ds, o.get("groups") or None)
    return C.n_groups(g, 2) + C.min_n(g, 2) + C.nonconstant(g)


# ------------------------------------------------------------------ one-sample
@register("one_sample_t", "One-sample t test", "t tests / rank tests", ["column"],
          [GA, Option("mu", "Hypothetical value", "float", 0.0), ALT, ALPHA], prism="One sample t test")
def one_sample_t(ds, group_a=None, mu=0.0, alternative="two-sided", alpha=0.05):
    n, v = _one(ds, group_a)
    r = st.ttest_1samp(v, mu, alternative=alternative)
    ci = r.confidence_interval(1 - alpha)
    d = v.mean() - mu
    t = pd.DataFrame({"Value": {"Column": n, "N": len(v), "Mean": v.mean(), "SD": v.std(ddof=1), "Hypothetical value": mu,
                                "Difference (mean - hyp.)": d, f"{(1 - alpha) * 100:g}% CI of mean low": ci.low,
                                f"{(1 - alpha) * 100:g}% CI of mean high": ci.high, "t": r.statistic, "df": r.df, "P": r.pvalue,
                                "Cohen's d": d / v.std(ddof=1)}})
    return Result("One-sample t test", f"{n}: mean {v.mean():.4g} vs {mu:g}; t({r.df:.0f})={r.statistic:.3f}, {fmt_P(r.pvalue)} ({alternative}).",
                  {"One-sample t test": t}, p=float(r.pvalue), methods="scipy.stats.ttest_1samp.")


def _one(ds, name):
    g = ds.group_arrays()
    if name:
        return name, g[name]
    if len(g) == 1:
        return next(iter(g.items()))
    raise ValueError("pick the column to test")


def _one_check(ds, o, parametric=True, nmin=2):
    g = ds.group_arrays()
    name = o.get("group_a")
    if not name and len(g) != 1:
        return [err(f"This table has {len(g)} columns; choose which one to test against the hypothetical value.")]
    if name and name not in g:
        return [err(f"Column '{name}' not found or empty.")]
    sub = {name: g[name]} if name else g
    out = C.min_n(sub, nmin)
    if parametric:
        out += C.normality(sub, o.get("alpha", .05)) + C.nonconstant(sub)
    return out


checker("one_sample_t")(lambda ds, o: _one_check(ds, o))


@register("wilcoxon_one_sample", "One-sample Wilcoxon signed-rank test", "t tests / rank tests", ["column"],
          [GA, Option("mu", "Hypothetical median", "float", 0.0), ALT, ALPHA], prism="Wilcoxon signed rank test (one sample)")
def wilcoxon_one(ds, group_a=None, mu=0.0, alternative="two-sided", alpha=0.05):
    n, v = _one(ds, group_a)
    d = v - mu
    r = st.wilcoxon(d[d != 0], alternative=alternative, method="auto")
    t = pd.DataFrame({"Value": {"Column": n, "N": len(v), "N used (nonzero diffs)": int((d != 0).sum()), "Median": np.median(v),
                                "Hypothetical median": mu, "W (signed-rank statistic)": r.statistic, "P": r.pvalue}})
    return Result("Wilcoxon signed-rank (one sample)", f"{n}: median {np.median(v):.4g} vs {mu:g}; {fmt_P(r.pvalue)} ({alternative}).",
                  {"Wilcoxon": t}, p=float(r.pvalue), methods="scipy.stats.wilcoxon on (x - hypothetical median); exact p for small n without ties/zeros, normal approximation otherwise.")


checker("wilcoxon_one_sample")(lambda ds, o: _one_check(ds, o, False, 6) +
                               [info("Zero differences are dropped; with n<6 a two-sided P<0.05 is impossible.")])


# ------------------------------------------------------------------ two-sample
def _unpaired(equal_var):
    def run(ds, group_a=None, group_b=None, alternative="two-sided", alpha=0.05, groups=None):
        a, b, x, y = two(ds, dict(group_a=group_a, group_b=group_b))
        r = st.ttest_ind(x, y, equal_var=equal_var, alternative=alternative)
        ci = r.confidence_interval(1 - alpha)
        d = x.mean() - y.mean()
        sp = np.sqrt(((len(x) - 1) * x.var(ddof=1) + (len(y) - 1) * y.var(ddof=1)) / (len(x) + len(y) - 2))
        t = pd.DataFrame({"Value": {f"Mean {a}": x.mean(), f"Mean {b}": y.mean(), f"Difference ({a} - {b})": d,
                                    f"{(1 - alpha) * 100:g}% CI low": ci.low, f"{(1 - alpha) * 100:g}% CI high": ci.high,
                                    "t": r.statistic, "df": r.df, "P": r.pvalue, "Cohen's d": d / sp, f"n {a}": len(x), f"n {b}": len(y)}})
        name = "Unpaired t test" + ("" if equal_var else " with Welch's correction")
        return Result(name, f"{a} vs {b}: difference {d:.4g}; t({r.df:.2f})={r.statistic:.3f}, {fmt_P(r.pvalue)} ({alternative}).",
                      {name: t}, [Comparison(a, b, float(r.pvalue), d)], p=float(r.pvalue),
                      methods=f"scipy.stats.ttest_ind(equal_var={equal_var}).")
    return run


register("unpaired_t", "Unpaired t test (equal variances)", "t tests / rank tests", ["column"], [GA, GB, ALT, ALPHA], prism="Unpaired t test")(_unpaired(True))
register("welch_t", "Unpaired t test with Welch's correction", "t tests / rank tests", ["column"], [GA, GB, ALT, ALPHA], prism="Welch's t test")(_unpaired(False))
checker("unpaired_t")(lambda ds, o: _two_check(ds, o, 2, False, True, True))
checker("welch_t")(lambda ds, o: _two_check(ds, o, 2, False, True, False))


def _paired_xy(ds, o):
    a, b, _, _ = two(ds, o)
    m = ds.numeric([a, b]).dropna()
    return a, b, m[a].to_numpy(), m[b].to_numpy()


@register("paired_t", "Paired t test", "t tests / rank tests", ["column"], [GA, GB, ALT, ALPHA], prism="Paired t test")
def paired_t(ds, group_a=None, group_b=None, alternative="two-sided", alpha=0.05):
    a, b, x, y = _paired_xy(ds, dict(group_a=group_a, group_b=group_b))
    r = st.ttest_rel(x, y, alternative=alternative)
    ci = r.confidence_interval(1 - alpha)
    d = x - y
    rr = st.pearsonr(x, y)
    t = pd.DataFrame({"Value": {"Pairs": len(x), f"Mean difference ({a} - {b})": d.mean(), "SD of differences": d.std(ddof=1),
                                f"{(1 - alpha) * 100:g}% CI low": ci.low, f"{(1 - alpha) * 100:g}% CI high": ci.high, "t": r.statistic,
                                "df": r.df, "P": r.pvalue, "Cohen's dz": d.mean() / d.std(ddof=1),
                                "Pearson r (pairing effectiveness)": rr.statistic, "P of r": rr.pvalue}})
    return Result("Paired t test", f"{a} - {b}: mean difference {d.mean():.4g}; t({r.df:.0f})={r.statistic:.3f}, {fmt_P(r.pvalue)} ({alternative}).",
                  {"Paired t test": t}, [Comparison(a, b, float(r.pvalue), d.mean())], p=float(r.pvalue), methods="scipy.stats.ttest_rel.")


@checker("paired_t")
def _(ds, o):
    out = _two_check(ds, o, 2, True, False)
    try:
        a, b, x, y = _paired_xy(ds, o)
        if len(x) < 2:
            out.append(err("Fewer than 2 complete pairs."))
        elif len(x) > 3 and np.ptp(x - y) > 0:
            p = st.shapiro(x - y).pvalue
            if p < o.get("alpha", .05):
                out.append(warn(f"The paired differences are not normal (Shapiro-Wilk p={p:.3g}); consider the Wilcoxon matched-pairs test."))
    except Exception:
        pass
    return out


@register("ratio_paired_t", "Ratio paired t test", "t tests / rank tests", ["column"], [GA, GB, ALT, ALPHA], prism="Ratio paired t test")
def ratio_paired_t(ds, group_a=None, group_b=None, alternative="two-sided", alpha=0.05):
    a, b, x, y = _paired_xy(ds, dict(group_a=group_a, group_b=group_b))
    lx, ly = np.log(x), np.log(y)
    r = st.ttest_rel(lx, ly, alternative=alternative)
    ci = r.confidence_interval(1 - alpha)
    t = pd.DataFrame({"Value": {"Pairs": len(x), f"Geometric mean ratio ({a}/{b})": np.exp((lx - ly).mean()),
                                f"{(1 - alpha) * 100:g}% CI of ratio low": np.exp(ci.low), f"{(1 - alpha) * 100:g}% CI of ratio high": np.exp(ci.high),
                                "t": r.statistic, "df": r.df, "P": r.pvalue}})
    return Result("Ratio paired t test", f"Geometric mean ratio {np.exp((lx - ly).mean()):.4g}; {fmt_P(r.pvalue)}.", {"Ratio paired t": t},
                  [Comparison(a, b, float(r.pvalue), float((lx - ly).mean()))], p=float(r.pvalue), methods="paired t test on log-transformed values (scipy.stats.ttest_rel).")


@checker("ratio_paired_t")
def _(ds, o):
    out = _two_check(ds, o, 2, True, False)
    try:
        a, b, x, y = _paired_xy(ds, o)
        out += C.positive({a: x, b: y}, "The ratio paired t test")
    except Exception:
        pass
    return out


@register("mann_whitney", "Mann-Whitney U test", "t tests / rank tests", ["column"], [GA, GB, ALT, ALPHA], prism="Mann-Whitney test")
def mann_whitney(ds, group_a=None, group_b=None, alternative="two-sided", alpha=0.05):
    a, b, x, y = two(ds, dict(group_a=group_a, group_b=group_b))
    r = st.mannwhitneyu(x, y, alternative=alternative, method="auto")
    t = pd.DataFrame({"Value": {f"Median {a}": np.median(x), f"Median {b}": np.median(y), "Difference of medians": np.median(x) - np.median(y),
                                f"U ({a})": r.statistic, "P": r.pvalue, f"n {a}": len(x), f"n {b}": len(y)}})
    return Result("Mann-Whitney test", f"{a} vs {b}: U={r.statistic:.4g}, {fmt_P(r.pvalue)} ({alternative}).", {"Mann-Whitney": t},
                  [Comparison(a, b, float(r.pvalue), float(np.median(x) - np.median(y)))], p=float(r.pvalue),
                  methods="scipy.stats.mannwhitneyu(method='auto'): exact when no ties and n<=8, else normal approximation with tie and continuity correction.")


@checker("mann_whitney")
def _(ds, o):
    out = _two_check(ds, o, 1, False, False)
    if not any(i.level == "error" for i in out):
        _, _, x, y = two(ds, o)
        out += C.ties_note({"a": x, "b": y})
    return out


@register("wilcoxon_paired", "Wilcoxon matched-pairs signed-rank test", "t tests / rank tests", ["column"], [GA, GB, ALT, ALPHA],
          prism="Wilcoxon matched-pairs signed rank test")
def wilcoxon_paired(ds, group_a=None, group_b=None, alternative="two-sided", alpha=0.05):
    a, b, x, y = _paired_xy(ds, dict(group_a=group_a, group_b=group_b))
    d = x - y
    r = st.wilcoxon(d[d != 0], alternative=alternative, method="auto")
    t = pd.DataFrame({"Value": {"Pairs": len(x), "Pairs with nonzero difference": int((d != 0).sum()), "Median difference": np.median(d),
                                "W": r.statistic, "P": r.pvalue}})
    return Result("Wilcoxon matched-pairs test", f"{a} vs {b}: median difference {np.median(d):.4g}, {fmt_P(r.pvalue)} ({alternative}).",
                  {"Wilcoxon": t}, [Comparison(a, b, float(r.pvalue), float(np.median(d)))], p=float(r.pvalue),
                  methods="scipy.stats.wilcoxon on paired differences (zero differences dropped).")


@checker("wilcoxon_paired")
def _(ds, o):
    out = _two_check(ds, o, 1, True, False)
    try:
        a, b, x, y = _paired_xy(ds, o)
        nz = int((x - y != 0).sum())
        if nz < 6:
            out.append(err(f"Only {nz} nonzero paired difference(s); the Wilcoxon test cannot reach P<0.05 with fewer than 6."))
    except Exception:
        pass
    return out


@register("ks_two_sample", "Kolmogorov-Smirnov test (two samples)", "t tests / rank tests", ["column"], [GA, GB, ALT, ALPHA],
          prism="Kolmogorov-Smirnov test")
def ks2(ds, group_a=None, group_b=None, alternative="two-sided", alpha=0.05):
    a, b, x, y = two(ds, dict(group_a=group_a, group_b=group_b))
    r = st.ks_2samp(x, y, alternative=alternative)
    return Result("Kolmogorov-Smirnov test", f"D={r.statistic:.4g}, {fmt_P(r.pvalue)}.",
                  {"KS": pd.DataFrame({"Value": {"D": r.statistic, "P": r.pvalue, "n1": len(x), "n2": len(y)}})},
                  [Comparison(a, b, float(r.pvalue))], p=float(r.pvalue), methods="scipy.stats.ks_2samp.")


checker("ks_two_sample")(lambda ds, o: _two_check(ds, o, 2, False, False))


# ------------------------------------------------------------------ one-way ANOVA
def _anova_check(parametric=True, equal_var=False, nmin=2, posthoc_key="posthoc", require_ge3=True):
    def chk(ds, o):
        try:
            g = selected(ds, o)
        except KeyError as e:
            return [err(str(e))]
        out = C.n_groups(g, 2, hint="Select the groups to compare.") + C.min_n(g, nmin)
        if len(g) == 2:
            out.append(warn("Only 2 groups: this is equivalent to a t test. A t test is the standard choice."))
        if ds.paired and "rm" not in posthoc_key:
            out.append(warn("Table is marked as matched; use the repeated-measures / Friedman version."))
        ph = o.get("posthoc", "none")
        ctl = o.get("control")
        if ph == "dunnett" and (not ctl or ctl not in g):
            out.append(err("Dunnett's test compares each group to a control: choose the control group."))
        if ctl and ctl not in g:
            out.append(err(f"Control group '{ctl}' is not among the selected groups."))
        if ph == "trend" and len(g) < 3:
            out.append(err("Test for linear trend needs at least 3 ordered groups."))
        if ph == "trend":
            out.append(info("Trend test assumes the columns are in a meaningful order (left to right) and roughly equally spaced."))
        if parametric:
            out += C.normality(g, o.get("alpha", .05)) + C.nonconstant(g)
            if equal_var:
                out += C.equal_variance(g, o.get("alpha", .05))
        else:
            out += C.ties_note(g)
        return out
    return chk


def _posthoc_opts(choices, extra_adjust=None):
    opts = [Option("posthoc", "Multiple comparisons", "select", "none", choices)]
    if extra_adjust:
        opts.append(Option("adjust", "Adjustment", "select", extra_adjust[0], extra_adjust))
    opts += [CONTROL, Option("compare", "Compare", "select", "all pairs", ["all pairs", "each vs control"],
                             "Bonferroni / Sidak / Holm-Sidak / Dunn: all pairs or only each vs the control group."), ALPHA]
    return opts


def _ctl(o):
    return o.get("control") if (o.get("posthoc") == "dunnett" or o.get("compare") == "each vs control") else None


@register("oneway_anova", "One-way ANOVA (ordinary)", "ANOVA", ["column"], [GROUPS] + _posthoc_opts(PH.PARAMETRIC), prism="Ordinary one-way ANOVA")
def oneway(ds, groups=None, posthoc="none", control=None, compare="all pairs", alpha=0.05):
    g = C.groups_of(ds, groups)
    arrs = list(g.values())
    r = st.f_oneway(*arrs)
    N = sum(map(len, arrs)); k = len(arrs)
    gm = np.concatenate(arrs).mean()
    ssb = sum(len(v) * (v.mean() - gm) ** 2 for v in arrs)
    ssw = sum(((v - v.mean()) ** 2).sum() for v in arrs)
    tab = pd.DataFrame({"SS": [ssb, ssw, ssb + ssw], "DF": [k - 1, N - k, N - 1], "MS": [ssb / (k - 1), ssw / (N - k), np.nan],
                        "F": [r.statistic, np.nan, np.nan], "P": [r.pvalue, np.nan, np.nan]}, index=["Between groups (treatment)", "Within groups (residual)", "Total"])
    tables = {"ANOVA table": tab, "R squared": pd.DataFrame({"R2": [ssb / (ssb + ssw)]})}
    cm = []
    if posthoc != "none":
        t, cm = PH.parametric(g, posthoc, _ctl({"posthoc": posthoc, "control": control, "compare": compare}), alpha)
        tables["Multiple comparisons: " + posthoc] = t.set_index(t.columns[0])
    return Result("One-way ANOVA", f"F({k - 1}, {N - k}) = {r.statistic:.3f}, {fmt_P(r.pvalue)}.", tables, cm, p=float(r.pvalue),
                  methods="scipy.stats.f_oneway; post-tests use the pooled within-group MSE (scipy.stats.tukey_hsd / dunnett, statsmodels.multipletests, studentized_range).")


checker("oneway_anova")(_anova_check(True, True))


def _anova_oneway_sm(use_var, title, key, label):
    @register(key, label, "ANOVA", ["column"], [GROUPS] + _posthoc_opts(["none", "games-howell", "tamhane-t2"])[:1] + [ALPHA], prism=title)
    def run(ds, groups=None, posthoc="none", alpha=0.05):
        g = C.groups_of(ds, groups)
        r = anova_oneway(list(g.values()), use_var=use_var, welch_correction=True)
        tab = pd.DataFrame({"F*" if use_var == "bf" else "W": [r.statistic], "DFn": [r.df_num], "DFd": [r.df_denom], "P": [r.pvalue]})
        tables = {title: tab}
        cm = []
        if posthoc != "none":
            t, cm = PH.unequal_var(g, posthoc, alpha)
            tables["Multiple comparisons: " + posthoc] = t.set_index(t.columns[0])
        return Result(title, f"F({r.df_num:.0f}, {r.df_denom:.2f}) = {r.statistic:.3f}, {fmt_P(r.pvalue)}.", tables, cm, p=float(r.pvalue),
                      methods=f"statsmodels.stats.oneway.anova_oneway(use_var='{use_var}'); post-tests: pingouin.pairwise_gameshowell / scikit-posthocs.posthoc_tamhane.")
    checker(key)(_anova_check(True, False))


_anova_oneway_sm("unequal", "Welch's ANOVA", "welch_anova", "One-way ANOVA - Welch (unequal variances)")
_anova_oneway_sm("bf", "Brown-Forsythe ANOVA", "bf_anova", "One-way ANOVA - Brown-Forsythe (unequal variances)")


@register("kruskal", "Kruskal-Wallis test", "ANOVA", ["column"],
          [GROUPS, Option("posthoc", "Multiple comparisons", "select", "none", ["none", "dunn"]),
           Option("adjust", "Adjustment", "select", "bonferroni", ["bonferroni", "holm", "sidak", "none"]), CONTROL,
           Option("compare", "Compare", "select", "all pairs", ["all pairs", "each vs control"])], prism="Kruskal-Wallis test")
def kruskal(ds, groups=None, posthoc="none", adjust="bonferroni", control=None, compare="all pairs"):
    g = C.groups_of(ds, groups)
    r = st.kruskal(*g.values())
    tables = {"Kruskal-Wallis": pd.DataFrame({"H (tie-corrected)": [r.statistic], "DF": [len(g) - 1], "P": [r.pvalue]})}
    cm = []
    if posthoc == "dunn":
        t, cm = PH.dunn(g, adjust, control if compare == "each vs control" else None)
        tables["Dunn's multiple comparisons"] = t.set_index("Comparison")
    return Result("Kruskal-Wallis test", f"H({len(g) - 1}) = {r.statistic:.3f}, {fmt_P(r.pvalue)}.", tables, cm, p=float(r.pvalue),
                  methods="scipy.stats.kruskal; scikit-posthocs.posthoc_dunn.")


@checker("kruskal")
def _(ds, o):
    out = _anova_check(False, False, 1)(ds, o)
    if o.get("compare") == "each vs control" and not o.get("control"):
        out.append(err("Choose the control group."))
    return out


# ---- repeated measures
def _rm_matrix(ds, groups):
    names = groups or list(ds.group_arrays())
    return ds.numeric(names).dropna()


@register("rm_anova", "Repeated-measures one-way ANOVA", "ANOVA", ["column"],
          [GROUPS, Option("posthoc", "Multiple comparisons (paired t, corrected)", "select", "none", ["none", "bonferroni", "sidak", "holm-sidak"]), CONTROL,
           Option("compare", "Compare", "select", "all pairs", ["all pairs", "each vs control"])], prism="Repeated measures one-way ANOVA")
def rm_anova(ds, groups=None, posthoc="none", control=None, compare="all pairs"):
    import pingouin as pg
    m = _rm_matrix(ds, groups)
    long = m.reset_index(names="subject").melt("subject", var_name="cond", value_name="v")
    a = pg.rm_anova(data=long, dv="v", within="cond", subject="subject", correction=True, detailed=True)
    tables = {"RM ANOVA (pingouin)": a.set_index("Source")}
    col = lambda *names: next(n for n in names if n in a.columns)  # pingouin renamed these columns between versions
    p_unc = a.loc[0, col("p_unc", "p-unc")]
    p_gg = a.loc[0, col("p_GG_corr", "p-GG-corr", "p_unc", "p-unc")]
    cm = []
    if posthoc != "none":
        t, cm = PH.paired_pairwise(m, posthoc, control if compare == "each vs control" else None)
        tables["Multiple comparisons (paired t)"] = t.set_index("Comparison")
    return Result("Repeated-measures ANOVA", f"F({a.loc[0, 'DF']:.0f}, {a.loc[1, 'DF']:.0f}) = {a.loc[0, 'F']:.3f}, uncorrected {fmt_P(p_unc)}, "
                  f"Greenhouse-Geisser-corrected {fmt_P(p_gg)}.", tables, cm, p=float(p_gg),
                  methods="pingouin.rm_anova (Greenhouse-Geisser sphericity correction); post-tests = paired t tests with multiplicity correction (note: Prism instead uses the RM-ANOVA residual error term).")


@checker("rm_anova")
def _(ds, o):
    names = o.get("groups") or None
    g = C.groups_of(ds, names)
    out = C.n_groups(g, 3, hint="With 2 conditions use the paired t test.")
    if not ds.paired:
        out.append(err("Repeated-measures ANOVA needs matched rows. Tick 'Rows are matched subjects' (Data tab) if each row is one subject."))
    m = _rm_matrix(ds, names) if g else None
    if m is not None:
        full = ds.numeric(list(g)).dropna(how="all")
        if len(m) < len(full):
            out.append(err(f"{len(full) - len(m)} subject(s) have missing values; RM ANOVA cannot use incomplete subjects. Remove them or use the mixed-effects model."))
        elif len(m) < 3:
            out.append(err("Need at least 3 complete subjects."))
    if o.get("compare") == "each vs control" and not o.get("control"):
        out.append(err("Choose the control group."))
    out.append(info("Sphericity is not assumed: the Greenhouse-Geisser-corrected P is reported."))
    return out


@register("friedman", "Friedman test", "ANOVA", ["column"],
          [GROUPS, Option("posthoc", "Multiple comparisons", "select", "none", ["none", "dunn"]),
           Option("adjust", "Adjustment", "select", "bonferroni", ["bonferroni", "holm", "sidak", "none"]), CONTROL,
           Option("compare", "Compare", "select", "all pairs", ["all pairs", "each vs control"])], prism="Friedman test")
def friedman(ds, groups=None, posthoc="none", adjust="bonferroni", control=None, compare="all pairs"):
    m = _rm_matrix(ds, groups)
    r = st.friedmanchisquare(*[m[c] for c in m.columns])
    tables = {"Friedman": pd.DataFrame({"Chi-square": [r.statistic], "DF": [m.shape[1] - 1], "P": [r.pvalue], "Subjects": [len(m)]})}
    cm = []
    if posthoc == "dunn":
        t, cm = PH.dunn_friedman(m, adjust, control if compare == "each vs control" else None)
        tables["Dunn's multiple comparisons"] = t.set_index("Comparison")
    return Result("Friedman test", f"Chi-square({m.shape[1] - 1}) = {r.statistic:.3f}, {fmt_P(r.pvalue)}.", tables, cm, p=float(r.pvalue),
                  methods="scipy.stats.friedmanchisquare; Dunn's test on rank sums (in-house, z = rank-sum difference / sqrt(n k (k+1)/6)).")


checker("friedman")(lambda ds, o: [i for i in REGISTRY_CHECK("rm_anova", ds, o)])


def REGISTRY_CHECK(key, ds, o):
    from .core import REGISTRY
    return REGISTRY[key].check(ds, o)


checker("descriptive")(lambda ds, o: [])
checker("normality")(lambda ds, o: [i for g in [ds.group_arrays()] for i in C.min_n(g, 3, " for any normality test")]
                     + [info("Normality tests have low power for n<8 and flag trivial deviations for very large n; inspect a histogram/QQ plot too.")])
