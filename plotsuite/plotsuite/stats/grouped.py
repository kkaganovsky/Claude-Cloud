"""Tests for grouped (long-format) tables: two/three-way ANOVA, RM/mixed designs, multiple t tests."""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd
import scipy.stats as st
import statsmodels.api as sm
import statsmodels.formula.api as smf
from statsmodels.stats.anova import anova_lm
from statsmodels.stats.multitest import multipletests

from ..data import Dataset
from .core import Comparison, Option, Result, checker, err, fmt_P, fmt_p, info, register, stars, warn

POST = Option("posthoc", "Simple-effects comparisons", "select", "none",
              ["none", "A within each B", "B within each A", "A main effect (pooled over B)", "B main effect (pooled over A)"],
              "Which levels to compare. 'A within each B' compares the levels of factor A separately at every level of B.")
ADJ = Option("adjust", "Correction", "select", "sidak", ["sidak", "bonferroni", "holm-sidak", "tukey", "none"])
INTER = Option("interaction", "Include A x B interaction", "bool", True)
ALPHA = Option("alpha", "Significance level (alpha)", "float", 0.05)


def _lg(ds):
    return ds.long()


def _roles_check(ds, need, o=None):
    out = []
    for r in need:
        if not ds.roles.get(r) or ds.roles[r] not in ds.df.columns:
            label = {"value": "Value (outcome)", "a": "Factor A", "b": "Factor B", "c": "Factor C", "subject": "Subject ID"}[r]
            out.append(err(f"Assign a column to the role '{label}' (Data tab > Roles)."))
    if len({ds.roles.get(r) for r in need if ds.roles.get(r)}) < len([r for r in need if ds.roles.get(r)]):
        out.append(err("The same column is assigned to two roles."))
    if not out:
        bad = ds.bad_cells([ds.roles["value"]])
        if bad:
            out.append(err(f"{len(bad)} non-numeric cell(s) in the value column (e.g. row {bad[0][1]}: '{bad[0][2]}')."))
    return out


def _design_check(ds, factors, interaction=True, alpha=0.05):
    out = _roles_check(ds, ["value"] + factors)
    if out:
        return out
    t = _lg(ds)
    for f in factors:
        k = t[f].nunique()
        if k < 2:
            out.append(err(f"Factor {f.upper()} has {k} level; needs at least 2."))
    cell = t.groupby(factors).size()
    full = int(np.prod([t[f].nunique() for f in factors]))
    if len(cell) < full:
        out.append(err(f"{full - len(cell)} combination(s) of factor levels have no data (empty cells). Fill them or remove those levels."))
    elif cell.min() < 2 and interaction:
        out.append(err("Some cells have a single observation, so the interaction cannot be tested. Add replicates or turn off the interaction (additive model)."))
    elif cell.min() < 2:
        out.append(info("No replicates: additive model only."))
    if cell.nunique() > 1:
        out.append(info("Unbalanced design: Type III sums of squares are reported (sum-to-zero coding)."))
    if len(t) - full <= 0 and interaction:
        out.append(err("No residual degrees of freedom."))
    return out


def _fit(t, factors, interaction):
    rhs = " + ".join(f"C({f}, Sum)" for f in factors)
    if interaction:
        rhs = " * ".join(f"C({f}, Sum)" for f in factors)
    return smf.ols(f"value ~ {rhs}", t).fit()


def _anova_table(model, factors, ds):
    a = anova_lm(model, typ=3).drop(index="Intercept")
    names = {}
    for idx in a.index:
        if idx == "Residual":
            names[idx] = "Residual"; continue
        parts = [p.strip() for p in idx.split(":")]
        lab = []
        for p in parts:
            f = p[p.index("(") + 1:].split(",")[0]
            lab.append(ds.roles.get(f, f.upper()))
        names[idx] = " x ".join(lab)
    a = a.rename(index=names)
    out = pd.DataFrame({"SS": a["sum_sq"], "DF": a["df"], "MS": a["sum_sq"] / a["df"], "F": a["F"], "P": a["PR(>F)"]})
    tot = model.centered_tss
    out.loc["Total"] = [tot, len(model.resid) - 1, np.nan, np.nan, np.nan]
    return out


def _resid_checks(model, t, factors):
    out = []
    if len(model.resid) >= 8 and np.ptp(model.resid) > 0 and len(model.resid) <= 5000:
        p = st.shapiro(model.resid).pvalue
        if p < 0.05:
            out.append(warn(f"Residuals are not normal (Shapiro-Wilk p={p:.3g}); consider transforming the data."))
    cells = [g["value"].to_numpy() for _, g in t.groupby(factors) if len(g) > 1]
    if len(cells) > 1 and all(np.ptp(c) > 0 for c in cells):
        p = st.levene(*cells, center="median").pvalue
        if p < 0.05:
            out.append(warn(f"Cell variances differ (Brown-Forsythe p={p:.3g}); ANOVA assumes homogeneous variances."))
    return out


def simple_effects(t, model, mode, adjust, alpha, ds):
    """Pairwise comparisons of cell means using the residual MSE / df of the fitted two-way model."""
    mse, df = model.mse_resid, model.df_resid
    A, B = "a", "b"
    nm = {"a": ds.roles.get("a", "A"), "b": ds.roles.get("b", "B")}
    cm = t.groupby([A, B])["value"].agg(["mean", "count"])
    rows = []
    if mode in ("A within each B", "B within each A"):
        vary, hold = (A, B) if mode == "A within each B" else (B, A)
        for h in sorted(t[hold].unique(), key=_key):
            lv = sorted(t[vary].unique(), key=_key)
            for x, y in itertools.combinations(lv, 2):
                gx = cm.loc[(x, h) if hold == B else (h, x)]; gy = cm.loc[(y, h) if hold == B else (h, y)]
                rows.append((f"{nm[hold]}={h}", x, y, gx["mean"] - gy["mean"], np.sqrt(mse * (1 / gx["count"] + 1 / gy["count"]))))
    else:
        vary = A if mode.startswith("A") else B
        hold = B if vary == A else A
        # marginal means: unweighted average of cell means over the other factor
        mm = t.groupby([vary, hold])["value"].agg(["mean", "count"]).reset_index()
        lv = sorted(t[vary].unique(), key=_key)
        nh = t[hold].nunique()
        marg = {l: mm[mm[vary] == l]["mean"].mean() for l in lv}
        inv = {l: (1 / mm[mm[vary] == l]["count"]).sum() / nh ** 2 for l in lv}
        for x, y in itertools.combinations(lv, 2):
            rows.append((f"{nm[vary]} (all {nm[hold]})", x, y, marg[x] - marg[y], np.sqrt(mse * (inv[x] + inv[y]))))
    raw = [2 * st.t.sf(abs(d / se), df) for *_, d, se in rows]
    fam = {}
    for r in rows:
        fam.setdefault(r[0], []).append(r)
    if adjust == "tukey":
        adj = []
        for r in rows:
            k = len(fam[r[0]]) and int(round((1 + np.sqrt(1 + 8 * len(fam[r[0]]))) / 2))
            adj.append(float(st.studentized_range.sf(abs(r[3] / r[4]) * np.sqrt(2), k, df)))
        adj = np.array(adj)
    elif adjust == "none":
        adj = np.array(raw)
    else:
        adj = multipletests(raw, method=adjust)[1]
    out, cmps = [], []
    for (grp, x, y, d, se), p in zip(rows, adj):
        tc = st.t.ppf(1 - alpha / 2, df)
        out.append([grp, f"{x} - {y}", d, d - tc * se, d + tc * se, float(p), stars(p)])
        cmps.append(Comparison(str(x), str(y), float(p), d, grp))
    return pd.DataFrame(out, columns=["Within", "Comparison", "Mean diff", "CI low (unadj.)", "CI high (unadj.)", "Adjusted P", "Summary"]), cmps


def _key(v):
    try:
        return (0, float(v))
    except (TypeError, ValueError):
        return (1, str(v))


@register("two_way_anova", "Two-way ANOVA (ordinary)", "ANOVA", ["grouped"], [INTER, POST, ADJ, ALPHA], prism="Two-way ANOVA")
def two_way(ds, interaction=True, posthoc="none", adjust="sidak", alpha=0.05):
    t = _lg(ds)
    m = _fit(t, ["a", "b"], interaction)
    tab = _anova_table(m, ["a", "b"], ds)
    tables = {"ANOVA table (Type III)": tab}
    cm = []
    if posthoc != "none":
        pt, cm = simple_effects(t, m, posthoc, adjust, alpha, ds)
        tables["Multiple comparisons"] = pt.set_index("Within")
    eff = tab.drop(index=["Residual", "Total"])
    s = "; ".join(f"{i}: F({r.DF:.0f},{tab.loc['Residual', 'DF']:.0f})={r.F:.3f}, {fmt_P(r.P)}" for i, r in eff.iterrows())
    return Result("Two-way ANOVA", s, tables, cm, warnings=_resid_checks(m, t, ["a", "b"]),
                  methods="statsmodels.formula.api.ols with sum-to-zero contrasts + anova_lm(typ=3); simple effects use the residual MSE and df; "
                          "corrections: statsmodels.multipletests / scipy.stats.studentized_range.")


@checker("two_way_anova")
def _(ds, o):
    return _design_check(ds, ["a", "b"], o.get("interaction", True)) + (
        [err("Tukey correction compares levels within a family; choose Sidak/Bonferroni/Holm-Sidak for main-effect comparisons.")] if False else [])


@register("three_way_anova", "Three-way ANOVA", "ANOVA", ["grouped"], [Option("interaction", "Include all interactions", "bool", True)], prism="Three-way ANOVA")
def three_way(ds, interaction=True):
    t = _lg(ds)
    m = _fit(t, ["a", "b", "c"], interaction)
    tab = _anova_table(m, ["a", "b", "c"], ds)
    eff = tab.drop(index=["Residual", "Total"])
    s = "; ".join(f"{i}: {fmt_P(r.P)}" for i, r in eff.iterrows())
    return Result("Three-way ANOVA", s, {"ANOVA table (Type III)": tab}, warnings=_resid_checks(m, t, ["a", "b", "c"]),
                  methods="statsmodels ols (sum-to-zero contrasts) + anova_lm(typ=3).")


checker("three_way_anova")(lambda ds, o: _design_check(ds, ["a", "b", "c"], o.get("interaction", True)))


# ---- repeated measures / mixed
RM_DESIGNS = ["A within-subject, B between-subject", "B within-subject, A between-subject", "Both within-subject"]


def _rm_prep(ds, design):
    t = _lg(ds)
    return t


def _rm_check(ds, o):
    out = _roles_check(ds, ["value", "a", "b", "subject"])
    if out:
        return out
    t = _lg(ds)
    design = o.get("design", RM_DESIGNS[0])
    if t.groupby(["subject", "a", "b"]).size().max() > 1:
        out.append(err("A subject has more than one value in the same A x B cell; average the replicates first."))
    within = {"A": ["a"], "B": ["b"]}
    if design.startswith("Both"):
        full = t["a"].nunique() * t["b"].nunique()
        cnt = t.groupby("subject").size()
        if (cnt != full).any():
            out.append(err(f"{int((cnt != full).sum())} subject(s) lack a value in some A x B cell. Both-within RM ANOVA needs complete subjects; use the mixed-effects model for missing data."))
    else:
        w = "a" if design.startswith("A within") else "b"
        bt = "b" if w == "a" else "a"
        nlev = t[w].nunique()
        cnt = t.groupby("subject").size()
        if (cnt != nlev).any():
            out.append(err(f"{int((cnt != nlev).sum())} subject(s) are missing a within-subject level. Use the mixed-effects model for missing data."))
        if (t.groupby("subject")[bt].nunique() > 1).any():
            out.append(err(f"Some subjects appear in more than one level of the between-subject factor ({bt.upper()}); the subject ID must be unique per group."))
    return out


@register("two_way_rm", "Two-way repeated-measures / mixed-design ANOVA", "ANOVA", ["grouped"],
          [Option("design", "Design", "select", RM_DESIGNS[0], RM_DESIGNS), POST, ADJ, ALPHA], prism="Two-way ANOVA with repeated measures")
def two_way_rm(ds, design=RM_DESIGNS[0], posthoc="none", adjust="sidak", alpha=0.05):
    import pingouin as pg
    t = _lg(ds)
    tables, cm = {}, []
    if design.startswith("Both"):
        a = pg.rm_anova(data=t, dv="value", within=["a", "b"], subject="subject", correction=True)
    else:
        w = "a" if design.startswith("A within") else "b"
        bt = "b" if w == "a" else "a"
        a = pg.mixed_anova(data=t, dv="value", within=w, between=bt, subject="subject", correction=True)
    a = a.copy()
    a["Source"] = a["Source"].replace({"a": ds.roles["a"], "b": ds.roles["b"], "a * b": f"{ds.roles['a']} x {ds.roles['b']}", "Interaction": "Interaction"})
    tables["RM ANOVA (pingouin)"] = a.set_index("Source")
    pcol = next(c for c in ["p_GG_corr", "p-GG-corr", "p_unc", "p-unc"] if c in a.columns)
    if posthoc != "none":
        cm_rows, cm = _rm_simple_effects(t, design, posthoc, adjust, ds)
        tables["Multiple comparisons"] = cm_rows.set_index("Within")
    pu = "p_unc" if "p_unc" in a.columns else "p-unc"
    s = "; ".join(f"{r['Source']}: {fmt_P(r[pcol] if pd.notna(r[pcol]) else r[pu])}" for _, r in a.iterrows())
    return Result("Two-way RM / mixed ANOVA", s, tables, cm,
                  methods="pingouin.rm_anova (two within factors) or pingouin.mixed_anova; simple effects = paired t tests (within-subject factor) or pooled unpaired t tests (between-subject factor) with correction. "
                          "Prism uses the ANOVA error terms for these post-tests, so adjusted P values can differ slightly.")


def _rm_simple_effects(t, design, mode, adjust, ds):
    if mode.startswith("A main") or mode.startswith("B main"):
        key = "a" if mode.startswith("A") else "b"
        m = t.groupby(["subject", key])["value"].mean().unstack()
        rows = []
        for x, y in itertools.combinations(m.columns, 2):
            r = st.ttest_rel(m[x], m[y], nan_policy="omit")
            rows.append((f"{ds.roles[key]} (all)", x, y, (m[x] - m[y]).mean(), r.pvalue))
    else:
        vary, hold = ("a", "b") if mode == "A within each B" else ("b", "a")
        within_vary = (design.startswith("Both") or (design.startswith("A within") and vary == "a") or (design.startswith("B within") and vary == "b"))
        rows = []
        for h, g in t.groupby(hold):
            m = g.pivot_table(index="subject", columns=vary, values="value")
            for x, y in itertools.combinations(sorted(m.columns, key=_key), 2):
                if within_vary:
                    r = st.ttest_rel(m[x], m[y], nan_policy="omit")
                else:
                    r = st.ttest_ind(m[x].dropna(), m[y].dropna())
                rows.append((f"{ds.roles[hold]}={h}", x, y, m[x].mean() - m[y].mean(), r.pvalue))
    raw = [r[-1] for r in rows]
    adj = np.array(raw) if adjust in ("none", "tukey") else multipletests(raw, method=adjust)[1]
    out = [[g, f"{x} - {y}", d, float(p), stars(p)] for (g, x, y, d, _), p in zip(rows, adj)]
    cmps = [Comparison(str(x), str(y), float(p), d, g) for (g, x, y, d, _), p in zip(rows, adj)]
    return pd.DataFrame(out, columns=["Within", "Comparison", "Mean diff", "Adjusted P", "Summary"]), cmps


checker("two_way_rm")(_rm_check)


@checker("two_way_rm")
def _(ds, o):
    out = _rm_check(ds, o)
    if o.get("adjust") == "tukey" and o.get("posthoc", "none") != "none":
        out.append(err("Tukey correction is not offered for repeated-measures post-tests; choose Sidak, Bonferroni or Holm-Sidak."))
    return out


@register("mixed_effects", "Mixed-effects model (random intercept per subject)", "ANOVA", ["grouped"],
          [Option("interaction", "Include A x B interaction", "bool", True)], prism="Mixed-effects model (REML)")
def mixed(ds, interaction=True):
    t = _lg(ds)
    f = "value ~ C(a) * C(b)" if interaction and "b" in t else ("value ~ C(a) + C(b)" if "b" in t else "value ~ C(a)")
    m = smf.mixedlm(f, t, groups=t["subject"]).fit(reml=True)
    wt = m.wald_test_terms(scalar=True).table.drop(index="Intercept", errors="ignore")
    tabs = {"Fixed effects (Wald tests)": wt, "Coefficients": pd.DataFrame({"coef": m.fe_params, "SE": m.bse_fe, "z": m.tvalues[m.fe_params.index], "P": m.pvalues[m.fe_params.index]}),
            "Variance components": pd.DataFrame({"Subject variance": [float(m.cov_re.iloc[0, 0])], "Residual variance": [m.scale]})}
    return Result("Mixed-effects model", "Wald chi-square tests of fixed effects from a REML random-intercept model.", tabs,
                  warnings=["P values are asymptotic (Wald z / chi-square) and are anti-conservative for small samples; Prism uses a different small-sample approach (compare cautiously)."],
                  methods="statsmodels.formula.api.mixedlm (REML, random intercept per subject).")


@checker("mixed_effects")
def _(ds, o):
    out = _roles_check(ds, ["value", "a", "subject"] + (["b"] if ds.roles.get("b") else []))
    if out:
        return out
    t = _lg(ds)
    if t["subject"].nunique() < 4:
        out.append(err("Need at least 4 subjects for a mixed-effects model."))
    if len(t) <= t["subject"].nunique() + 2:
        out.append(err("Too few observations for the model."))
    return out


# ---- multiple t tests (one per row)
@register("multiple_t", "Multiple t tests (one per row of factor A)", "t tests / rank tests", ["grouped"],
          [Option("test", "Test", "select", "Welch t", ["Welch t", "Student t", "Mann-Whitney"]),
           Option("correction", "Multiple-comparison correction", "select", "holm-sidak",
                  ["holm-sidak", "bonferroni", "sidak", "fdr_bh", "fdr_tsbky", "none"],
                  "fdr_bh = Benjamini-Hochberg FDR; fdr_tsbky = two-stage Benjamini-Krieger-Yekutieli."),
           ALPHA], prism="Multiple t tests (one per row)")
def multiple_t(ds, test="Welch t", correction="holm-sidak", alpha=0.05):
    t = _lg(ds)
    lv = sorted(t["b"].unique(), key=_key)
    l1, l2 = lv
    rows = []
    for r, g in t.groupby("a", sort=False):
        x = g[g.b == l1]["value"].to_numpy(); y = g[g.b == l2]["value"].to_numpy()
        if len(x) < 2 or len(y) < 2:
            continue  # reported by the check; never fabricate a P value
        if test == "Mann-Whitney":
            p = st.mannwhitneyu(x, y).pvalue; stat = np.nan
        else:
            res = st.ttest_ind(x, y, equal_var=(test == "Student t")); p, stat = res.pvalue, res.statistic
        rows.append([r, x.mean(), y.mean(), x.mean() - y.mean(), len(x), len(y), stat, p])
    df = pd.DataFrame(rows, columns=[ds.roles["a"], f"Mean {l1}", f"Mean {l2}", "Difference", f"n {l1}", f"n {l2}", "t", "P (unadj.)"])
    if correction == "none":
        adj, rej = df["P (unadj.)"].to_numpy(), df["P (unadj.)"].to_numpy() < alpha
    else:
        rej, adj = multipletests(df["P (unadj.)"], alpha=alpha, method=correction)[:2]
    df["Adjusted P / q"] = adj
    df["Discovery?" if correction.startswith("fdr") else "Significant?"] = np.where(rej, "Yes", "No")
    df["Summary"] = [stars(p) for p in adj]
    cm = [Comparison(str(l1), str(l2), float(p), float(d), str(r)) for r, p, d in zip(df.iloc[:, 0], adj, df["Difference"])]
    return Result("Multiple t tests", f"{len(df)} rows tested; {int(np.sum(rej))} significant after '{correction}' correction.",
                  {"Multiple t tests": df.set_index(df.columns[0])}, cm,
                  methods="scipy.stats.ttest_ind / mannwhitneyu per row; statsmodels.stats.multitest.multipletests.")


@checker("multiple_t")
def _(ds, o):
    out = _roles_check(ds, ["value", "a", "b"])
    if out:
        return out
    t = _lg(ds)
    if t["b"].nunique() != 2:
        return [err(f"Multiple t tests compare exactly two levels of Factor B; B has {t['b'].nunique()}. Use two-way ANOVA instead.")]
    n = t.groupby(["a", "b"]).size().unstack()
    bad = n[(n.fillna(0) < 2).any(axis=1)]
    if len(bad):
        out.append(warn(f"{len(bad)} row(s) have <2 values in a group and will fail: {', '.join(map(str, bad.index[:5]))}."))
        if len(bad) == len(n):
            out = [err("No row has at least 2 values per group.")]
    return out
