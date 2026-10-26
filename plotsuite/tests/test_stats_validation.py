"""Numerical validation of results against independent reference implementations / hand formulas."""
import itertools

import numpy as np
import pandas as pd
import pytest
import scipy.stats as st
import statsmodels.api as sm
import statsmodels.formula.api as smf
from statsmodels.duration.survfunc import survdiff
from statsmodels.stats.anova import anova_lm
from statsmodels.stats.multicomp import pairwise_tukeyhsd

from plotsuite import stats as S
from plotsuite.data import Dataset


def pair_p(res, a, b, key="Adjusted P"):
    t = list(res.tables.values())[-1]
    for lab in (f"{a} vs. {b}", f"{b} vs. {a}"):
        if lab in t.index:
            return t.loc[lab, key]
    raise KeyError((a, b))


def test_unpaired_and_welch_t_match_hand_formula(col3):
    x, y = col3.df.A.to_numpy(), col3.df.B.to_numpy()
    r = S.run("unpaired_t", col3, {"group_a": "A", "group_b": "B"})
    sp2 = ((len(x) - 1) * x.var(ddof=1) + (len(y) - 1) * y.var(ddof=1)) / (len(x) + len(y) - 2)
    t = (x.mean() - y.mean()) / np.sqrt(sp2 * (1 / len(x) + 1 / len(y)))
    assert r.p == pytest.approx(2 * st.t.sf(abs(t), len(x) + len(y) - 2))
    w = S.run("welch_t", col3, {"group_a": "A", "group_b": "B"})
    se2 = x.var(ddof=1) / len(x) + y.var(ddof=1) / len(y)
    df = se2 ** 2 / ((x.var(ddof=1) / len(x)) ** 2 / (len(x) - 1) + (y.var(ddof=1) / len(y)) ** 2 / (len(y) - 1))
    assert w.p == pytest.approx(2 * st.t.sf(abs((x.mean() - y.mean()) / np.sqrt(se2)), df))
    ci = r.tables["Unpaired t test"].loc[["95% CI low", "95% CI high"], "Value"].to_numpy()
    se = np.sqrt(sp2 * (1 / len(x) + 1 / len(y)))
    tc = st.t.ppf(.975, len(x) + len(y) - 2)
    assert ci == pytest.approx([x.mean() - y.mean() - tc * se, x.mean() - y.mean() + tc * se])


def test_one_way_anova_matches_statsmodels_and_tukey(col3):
    r = S.run("oneway_anova", col3, {"posthoc": "tukey"})
    long = col3.long().rename(columns={"a": "g", "value": "v"})
    a = anova_lm(smf.ols("v ~ C(g)", long).fit())
    assert r.tables["ANOVA table"].loc["Between groups (treatment)", "F"] == pytest.approx(a.loc["C(g)", "F"])
    assert r.p == pytest.approx(a.loc["C(g)", "PR(>F)"])
    tk = pairwise_tukeyhsd(long.v, long.g)
    sm_p = dict(zip(zip(tk.groupsunique[tk._multicomp.pairindices[0]], tk.groupsunique[tk._multicomp.pairindices[1]]), tk.pvalues))
    for (a_, b_), p in sm_p.items():
        assert pair_p(r, a_, b_) == pytest.approx(p, rel=1e-3, abs=1e-8)


def test_bonferroni_sidak_holm_manual(col3):
    g = col3.group_arrays()
    mse = sum(((v - v.mean()) ** 2).sum() for v in g.values()) / (sum(map(len, g.values())) - 3)
    df = sum(map(len, g.values())) - 3
    raw = {}
    for a, b in itertools.combinations(g, 2):
        se = np.sqrt(mse * (1 / len(g[a]) + 1 / len(g[b])))
        raw[(a, b)] = 2 * st.t.sf(abs((g[a].mean() - g[b].mean()) / se), df)
    for method, f in [("bonferroni", lambda p: min(1, p * 3)), ("sidak", lambda p: 1 - (1 - p) ** 3)]:
        r = S.run("oneway_anova", col3, {"posthoc": method})
        for (a, b), p in raw.items():
            assert pair_p(r, a, b) == pytest.approx(f(p))
    r = S.run("oneway_anova", col3, {"posthoc": "holm-sidak"})
    order = sorted(raw, key=raw.get)
    prev = 0
    for i, k in enumerate(order):
        adj = max(prev, 1 - (1 - raw[k]) ** (3 - i)); prev = adj
        assert pair_p(r, *k) == pytest.approx(adj)


def test_snk_is_never_more_conservative_than_tukey(col3):
    t = S.run("oneway_anova", col3, {"posthoc": "tukey"}); s = S.run("oneway_anova", col3, {"posthoc": "snk"})
    for a, b in itertools.combinations("ABC", 2):
        assert pair_p(s, a, b) <= pair_p(t, a, b) + 1e-12
    # the widest pair (r = k) uses the same studentized-range critical value as Tukey
    means = col3.df.mean().sort_values()
    lo, hi = means.index[0], means.index[-1]
    assert pair_p(s, lo, hi) == pytest.approx(pair_p(t, lo, hi), rel=1e-6)


def test_trend_posthoc_manual(col3):
    r = S.run("oneway_anova", col3, {"posthoc": "trend"})
    g = col3.group_arrays(); m = np.array([g[k].mean() for k in "ABC"]); n = np.array([len(g[k]) for k in "ABC"])
    w = np.array([-1, 0, 1.0]); mse = sum(((v - v.mean()) ** 2).sum() for v in g.values()) / (n.sum() - 3)
    t = (w @ m) / np.sqrt(mse * (w ** 2 / n).sum())
    row = r.tables["Multiple comparisons: trend"].iloc[0]
    assert row["t"] == pytest.approx(t) and row["P"] == pytest.approx(2 * st.t.sf(abs(t), n.sum() - 3))


def test_welch_anova_and_kruskal_against_scipy(col3):
    r = S.run("kruskal", col3)
    assert r.p == pytest.approx(st.kruskal(*col3.group_arrays().values()).pvalue)
    w = S.run("welch_anova", col3)
    from statsmodels.stats.oneway import anova_oneway
    assert w.p == pytest.approx(anova_oneway(list(col3.group_arrays().values()), use_var="unequal").pvalue)


def test_friedman_and_dunn_friedman_manual(col3p):
    r = S.run("friedman", col3p, {"posthoc": "dunn", "adjust": "none"})
    m = col3p.df
    assert r.p == pytest.approx(st.friedmanchisquare(m.A, m.B, m.C).pvalue)
    R = m.rank(axis=1).sum(); n, k = m.shape
    z = (R["A"] - R["B"]) / np.sqrt(n * k * (k + 1) / 6)
    assert pair_p(r, "A", "B") == pytest.approx(2 * st.norm.sf(abs(z)))


def test_rm_anova_matches_manual_decomposition(col3p):
    m = col3p.df.to_numpy(); n, k = m.shape
    ss_c = n * ((m.mean(0) - m.mean()) ** 2).sum(); ss_s = k * ((m.mean(1) - m.mean()) ** 2).sum()
    ss_e = ((m - m.mean()) ** 2).sum() - ss_c - ss_s
    F = (ss_c / (k - 1)) / (ss_e / ((n - 1) * (k - 1)))
    r = S.run("rm_anova", col3p)
    assert r.tables["RM ANOVA (pingouin)"].loc["cond", "F"] == pytest.approx(F)


def test_two_way_anova_matches_statsmodels_type3(grouped):
    r = S.run("two_way_anova", grouped)
    m = smf.ols("y ~ C(geno, Sum) * C(time, Sum)", grouped.df).fit()
    a = anova_lm(m, typ=3)
    tab = r.tables["ANOVA table (Type III)"]
    assert tab.loc["geno", "F"] == pytest.approx(a.loc["C(geno, Sum)", "F"])
    assert tab.loc["geno x time", "P"] == pytest.approx(a.loc["C(geno, Sum):C(time, Sum)", "PR(>F)"])


def test_two_way_simple_effects_manual(grouped):
    r = S.run("two_way_anova", grouped, {"posthoc": "A within each B", "adjust": "none"})
    m = smf.ols("y ~ C(geno, Sum) * C(time, Sum)", grouped.df).fit()
    cell = grouped.df.groupby(["geno", "time"]).y.agg(["mean", "count"])
    se = np.sqrt(m.mse_resid * (1 / 6 + 1 / 6))
    d = cell.loc[("KO", "t0"), "mean"] - cell.loc[("WT", "t0"), "mean"]
    row = r.tables["Multiple comparisons"].loc["time=t0"]
    assert row["Adjusted P"] == pytest.approx(2 * st.t.sf(abs(d / se), m.df_resid))


def test_logrank_family_matches_statsmodels(surv):
    d = surv.df
    for name, wt in [("Log-rank (Mantel-Cox)", None), ("Gehan-Breslow-Wilcoxon", "gb"), ("Tarone-Ware", "tw")]:
        chi, p = survdiff(d.t, d.e, d.g, weight_type=wt)
        r = S.run("survival_km", surv, {"weighting": name}).tables[f"{name} comparison"]
        assert r["Chi-square"].iloc[0] == pytest.approx(chi) and r["P"].iloc[0] == pytest.approx(p)


def test_logrank_trend_equals_logrank_for_two_groups(surv):
    two = Dataset("s2", "survival", surv.df[surv.df.g != "c"], surv.roles)
    r = S.run("survival_km", two, {"trend": True})
    assert r.tables["Test for trend"]["Chi-square"].iloc[0] == pytest.approx(r.tables["Log-rank (Mantel-Cox) comparison"]["Chi-square"].iloc[0])


def test_km_median_and_curve(surv):
    from plotsuite.stats.survival import km_table
    t = surv.survival(); sub = t[t.group == "a"]
    c = km_table(sub)
    assert c["s"][0] == 1 and np.all(np.diff(c["s"]) <= 1e-12)
    last_is_event = bool(sub.sort_values("time").iloc[-1].event == 1)
    assert (c["s"][-1] == 0) == last_is_event  # KM falls to 0 only if the longest-observed subject had the event
    first = c["times"][np.argmax(c["s"] <= .5)]
    assert c["median"] == first


def test_cox_matches_two_group_hr_direction(surv):
    two = Dataset("s2", "survival", surv.df[surv.df.g != "c"], surv.roles)
    hr = S.run("cox_ph", two).tables["Cox PH"]["HR"].iloc[0]  # b vs a
    km = S.run("survival_km", two).tables["Hazard ratio (a vs b)"]["HR"]
    assert hr == pytest.approx(1 / km.iloc[1], rel=0.1)  # Peto one-step approximates the Cox HR


def test_grubbs_nist_example():
    # Classic example data (NIST e-Handbook 1.3.5.17): n=8, suspect 245.57
    v = [199.31, 199.53, 200.19, 200.82, 201.92, 201.95, 202.18, 245.57]
    ds = Dataset("o", "column", pd.DataFrame({"x": v}))
    r = S.run("grubbs", ds).tables["Grubbs"].iloc[0]
    n = 8
    G = (245.57 - np.mean(v)) / np.std(v, ddof=1)
    assert r["G"] == pytest.approx(G)
    tc = st.t.isf(.05 / (2 * n), n - 2)
    assert r["G critical"] == pytest.approx((n - 1) / np.sqrt(n) * np.sqrt(tc ** 2 / (n - 2 + tc ** 2)))
    assert r["G critical"] == pytest.approx(2.1266, abs=1e-3)  # tabulated two-sided critical value for n=8, alpha=0.05
    assert r["Suspected value"] == 245.57


def test_deming_delta1_equals_orthogonal_regression(rng):
    x = np.linspace(0, 10, 30); y = 2 * x + 1 + rng.normal(0, 1, 30); x = x + rng.normal(0, 1, 30)
    ds = Dataset("d", "xy", pd.DataFrame({"x": x, "y": y}), {"x": "x", "ys": ["y"]})
    b = S.run("deming", ds, {"delta": 1.0}).tables["Deming regression"].loc["Slope", "Best-fit value"]
    c = np.cov(x, y); w, v = np.linalg.eigh(c); pc = v[:, -1]
    assert b == pytest.approx(pc[1] / pc[0])


def test_linear_regression_matches_scipy(xy):
    t = xy.xy_series()["resp"]
    r = S.run("linear_regression", xy)
    lr = st.linregress(t.x, t.y)
    bf = r.tables["Best-fit values"]
    assert bf.loc["Slope", "Best-fit value"] == pytest.approx(lr.slope) and bf.loc["Slope", "SE"] == pytest.approx(lr.stderr)
    assert r.p == pytest.approx(lr.pvalue)


def test_compare_lines_detects_different_slopes(rng):
    x = np.tile(np.arange(10.0), 2)
    df = pd.DataFrame({"x": x, "A": x * 1 + rng.normal(0, .3, 20), "B": x * 3 + rng.normal(0, .3, 20)})
    ds = Dataset("cl", "xy", df, {"x": "x", "ys": ["A", "B"]})
    r = S.run("compare_lines", ds)
    assert r.p < 1e-6


def test_nonlinear_recovers_exact_parameters():
    x = np.array([0.1, 0.3, 1, 3, 10, 30, 100, 300.0])
    y = 5 + 95 / (1 + (30 / x) ** 1.2)
    ds = Dataset("n", "xy", pd.DataFrame({"x": x, "y": y}), {"x": "x", "ys": ["y"]})
    bf = S.run("nonlinear_fit", ds, {"model": "[Agonist] vs response (variable slope)"}).tables["Best-fit values"]["Best-fit value"]
    assert bf.loc["Bottom"] == pytest.approx(5, abs=1e-4) and bf.loc["Top"] == pytest.approx(100, abs=1e-3)
    assert bf.loc["EC50"] == pytest.approx(30, rel=1e-4) and bf.loc["HillSlope"] == pytest.approx(1.2, rel=1e-4)


def test_nonlinear_aicc_and_se_formula(xy):
    r = S.run("nonlinear_fit", xy, {"model": "[Agonist] vs response (variable slope)"})
    g = r.tables["Goodness of fit"]["Value"]
    n, k = 24, 4; sse = g["Sum of squares (weighted)"]; K = k + 1
    assert g["AICc"] == pytest.approx(n * np.log(sse / n) + 2 * K + 2 * K * (K + 1) / (n - K - 1))
    assert g["Sy.x"] == pytest.approx(np.sqrt(sse / (n - k)))
    # scipy cross-check of the fitted values
    from scipy.optimize import curve_fit
    t = xy.xy_series()["resp"]
    f = lambda x, b, tp, e, h: b + (tp - b) * x ** h / (e ** h + x ** h)
    p, _ = curve_fit(f, t.x, t.y, p0=[5, 100, 30, 1])
    assert r.tables["Best-fit values"]["Best-fit value"].to_numpy() == pytest.approx(p, rel=1e-4)


def test_nonlinear_constraints_and_interpolation(xy):
    r = S.run("nonlinear_fit", xy, {"model": "[Agonist] vs response (variable slope)", "constraints": "Bottom = 0\nTop <= 100", "interpolate_y": "50"})
    bf = r.tables["Best-fit values"]
    assert bf.loc["Bottom", "Best-fit value"] == 0 and bf.loc["Bottom", "Note"] == "Constant"
    assert bf.loc["Top", "Best-fit value"] <= 100 + 1e-9
    xs = float(r.tables["Interpolated X"].loc[50.0, "X"])
    p = bf["Best-fit value"]
    yhat = p["Bottom"] + (p["Top"] - p["Bottom"]) * xs ** p["HillSlope"] / (p["EC50"] ** p["HillSlope"] + xs ** p["HillSlope"])
    assert yhat == pytest.approx(50, abs=1e-3)


def test_compare_models_f_test_manual(xy):
    r = S.run("compare_models", xy, {"model_a": "[Agonist] vs response (3 param)", "model_b": "[Agonist] vs response (variable slope)", "nested": True})
    f = r.tables["Fits"]
    a, b = f.iloc[:, 0], f.iloc[:, 1]
    F = ((a["Sum of squares"] - b["Sum of squares"]) / (a["df"] - b["df"])) / (b["Sum of squares"] / b["df"])
    assert r.p == pytest.approx(st.f.sf(F, a["df"] - b["df"], b["df"]))
    w = r.tables["AICc comparison"]["Probability model is correct"]
    assert w.sum() == pytest.approx(1)
    assert (w.iloc[0] > w.iloc[1]) == (a["AICc"] < b["AICc"])


def test_contingency_against_scipy_statsmodels():
    c = Dataset("c", "contingency", pd.DataFrame({"g": ["tx", "ctl"], "y": [12, 5], "n": [8, 17]}))
    arr = np.array([[12, 8], [5, 17]])
    assert S.run("fisher_exact", c).p == pytest.approx(st.fisher_exact(arr)[1])
    assert S.run("chi_square", c).p == pytest.approx(st.chi2_contingency(arr, correction=False)[1])
    assert S.run("chi_square", c, {"correction": True}).p == pytest.approx(st.chi2_contingency(arr, correction=True)[1])
    rr = S.run("rr_or", c).tables["Ratios"]
    assert rr["Estimate"].iloc[0] == pytest.approx((12 / 20) / (5 / 22)) and rr["Estimate"].iloc[1] == pytest.approx(12 * 17 / (8 * 5))
    sens = S.run("rr_or", c).tables["Diagnostic measures (rows = test result, cols = truth)"].loc["Sensitivity", "Estimate"]
    assert sens == pytest.approx(12 / 20)


def test_chi_square_trend_matches_cochran_armitage():
    c = Dataset("t", "contingency", pd.DataFrame({"dose": ["0", "1", "2", "3"], "resp": [5, 9, 14, 20], "none": [45, 41, 36, 30]}))
    r = S.run("chi_square_trend", c)
    t = c.counts().to_numpy(float); n_i = t.sum(1); r_i = t[:, 0]; N = t.sum(); pbar = r_i.sum() / N; sc = np.arange(4.0)
    T = (sc * (r_i - n_i * pbar)).sum()
    var = pbar * (1 - pbar) * ((n_i * sc ** 2).sum() - (n_i * sc).sum() ** 2 / N)
    ca = T ** 2 / var
    assert 2 * st.norm.sf(abs(r.tables["Trend"].loc["z", "Value"])) == pytest.approx(st.chi2.sf(ca * (N - 1) / N, 1), rel=1e-6)


def test_bland_altman_limits(col3p):
    r = S.run("bland_altman", col3p, {"group_a": "A", "group_b": "B"}).tables["Bland-Altman"]
    d = col3p.df.A - col3p.df.B
    assert r.loc["Bias (mean difference)", "Estimate"] == pytest.approx(d.mean())
    assert r.loc["Upper limit of agreement", "Estimate"] == pytest.approx(d.mean() + 1.96 * d.std(ddof=1))


def test_roc_auc_equals_mann_whitney(col3):
    r = S.run("roc", col3, {"group_a": "A", "group_b": "C"})
    u = st.mannwhitneyu(col3.df.C, col3.df.A).statistic
    assert r.tables["AUC"].loc["AUC", "Value"] == pytest.approx(u / 144)


def test_pvalue_stack_matches_statsmodels():
    from statsmodels.stats.multitest import multipletests
    p = [.001, .01, .03, .04, .2, .6]
    r = S.run("pvalue_stack", Dataset("p", "column", pd.DataFrame({"p": p})), {"method": "fdr_bh"})
    assert r.tables["Adjusted P values"]["Adjusted P / q"].to_numpy() == pytest.approx(multipletests(p, method="fdr_bh")[1])


def test_pca_variance_sums_to_100(col3):
    r = S.run("pca", col3)
    assert r.tables["Variance explained"]["% variance"].sum() == pytest.approx(100)
    assert np.allclose(r.tables["Loadings"].T @ r.tables["Loadings"], np.eye(3), atol=1e-8)


def test_logistic_matches_statsmodels_and_mlr(rng):
    x = rng.normal(0, 1, 120); y = (rng.random(120) < 1 / (1 + np.exp(-(0.5 + 1.2 * x)))).astype(int)
    ds = Dataset("l", "column", pd.DataFrame({"x": x, "y": y, "z": rng.normal(size=120)}))
    r = S.run("logistic", ds, {"response": "y", "predictors": ["x"]}).tables["Coefficients"]
    m = sm.Logit(y, sm.add_constant(x)).fit(disp=0)
    assert r["Coefficient"].to_numpy() == pytest.approx(m.params)
    r2 = S.run("multiple_linear", ds, {"response": "x", "predictors": ["z"]}).tables["Coefficients"]
    assert r2["Coefficient"].iloc[1] == pytest.approx(np.polyfit(ds.df.z, ds.df.x, 1)[0])


def test_multiple_t_corrections(grouped):
    rows = grouped.df[grouped.df.time != "t2"].assign(row=lambda d: d.id.str[-1])
    ds = Dataset("m", "grouped", rows, {"value": "y", "a": "row", "b": "geno"})
    r = S.run("multiple_t", ds, {"correction": "bonferroni", "test": "Welch t"}).tables["Multiple t tests"]
    assert (r["Adjusted P / q"] >= r["P (unadj.)"] - 1e-12).all()


def test_mixed_effects_runs_with_missing(grouped):
    ds = Dataset("g2", "grouped", grouped.df.drop(index=[3, 11, 20]), grouped.roles)
    r = S.run("mixed_effects", ds)
    assert "C(a)" in r.tables["Fixed effects (Wald tests)"].index
