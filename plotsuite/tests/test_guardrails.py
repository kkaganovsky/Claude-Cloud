"""The app must refuse invalid analyses with a clear reason, and warn on assumption violations."""
import numpy as np
import pandas as pd
import pytest

from plotsuite import stats as S
from plotsuite.data import Dataset
from plotsuite.stats.advisor import recommend


def errors(key, ds, opts=None):
    return [i.message for i in S.evaluate(key, ds, opts) if i.level == "error"]


def warnings_(key, ds, opts=None):
    return [i.message for i in S.evaluate(key, ds, opts) if i.level == "warning"]


def test_t_test_on_three_groups_is_blocked_until_two_are_chosen(col3):
    for k in ("unpaired_t", "welch_t", "mann_whitney", "ks_two_sample"):
        assert any("exactly two" in m for m in errors(k, col3))
    assert not errors("unpaired_t", col3, {"group_a": "A", "group_b": "B"})
    assert errors("unpaired_t", col3, {"group_a": "A", "group_b": "A"})
    with pytest.raises(S.BlockedError):
        S.run("unpaired_t", col3)


def test_paired_tests_require_matched_rows_flag(col3, col3p):
    o = {"group_a": "A", "group_b": "B"}
    for k in ("paired_t", "wilcoxon_paired", "ratio_paired_t"):
        assert any("matched" in m for m in errors(k, col3, o))
        assert not errors(k, col3p, o)
    assert any("matched" in m for m in errors("rm_anova", col3))
    assert warnings_("unpaired_t", col3p, o)  # unpaired test on matched data: warn


def test_rm_anova_blocks_incomplete_subjects(col3p):
    ds = col3p.copy(); ds.df.loc[2, "B"] = np.nan
    assert any("missing" in m for m in errors("rm_anova", ds))
    assert any("missing" in m.lower() for m in errors("friedman", ds))
    assert warnings_("paired_t", ds, {"group_a": "A", "group_b": "B"})  # paired t drops incomplete pairs with a warning


def test_non_numeric_cells_are_never_silently_dropped():
    df = pd.DataFrame({"A": [1, 2, "oops", 4], "B": [1, 2, 3, 4]})
    ds = Dataset("x", "column", df)
    assert ds.bad_cells() == [("A", 3, "oops")]
    for k in ("descriptive", "unpaired_t", "oneway_anova"):
        assert any("non-numeric" in m for m in errors(k, ds, {"group_a": "A", "group_b": "B"}))


def test_layout_mismatch_blocks(col3, surv):
    assert any("needs a" in m for m in errors("survival_km", col3))
    assert any("needs a" in m for m in errors("oneway_anova", surv))


def test_group_count_and_n_requirements():
    tiny = Dataset("t", "column", pd.DataFrame({"A": [1.0, 2, 3], "B": [2.0, np.nan, np.nan]}))
    assert errors("unpaired_t", tiny)  # B has n=1
    assert errors("oneway_anova", Dataset("one", "column", pd.DataFrame({"A": [1.0, 2, 3]})))
    assert errors("normality", Dataset("two", "column", pd.DataFrame({"A": [1.0, 2]})))
    assert errors("wilcoxon_paired", Dataset("w", "column", pd.DataFrame({"A": [1.0, 2, 3, 4], "B": [2.0, 3, 4, 5]}), paired=True), {"group_a": "A", "group_b": "B"})


def test_dunnett_needs_control(col3):
    assert any("control" in m.lower() for m in errors("oneway_anova", col3, {"posthoc": "dunnett"}))
    assert not errors("oneway_anova", col3, {"posthoc": "dunnett", "control": "A"})
    assert errors("oneway_anova", col3, {"posthoc": "dunnett", "control": "Z"})
    assert errors("oneway_anova", col3, {"posthoc": "trend", "groups": ["A", "B"]})


def test_assumption_violations_warn_and_are_attached_to_results(rng):
    df = pd.DataFrame({"A": rng.lognormal(0, 1.2, 30), "B": rng.lognormal(1.5, .2, 30)})
    ds = Dataset("ln", "column", df)
    o = {"group_a": "A", "group_b": "B"}
    w = warnings_("unpaired_t", ds, o)
    assert any("normality" in m for m in w) and any("Variances differ" in m for m in w)
    res = S.run("unpaired_t", ds, o)
    assert any("normality" in m for m in res.warnings)
    assert not warnings_("mann_whitney", ds, o)  # rank test: no normality warning
    rec = recommend(ds)
    assert rec[0]["key"] in ("mann_whitney",)


def test_advisor_picks_standard_tests(col3, col3p, rng):
    assert recommend(col3)[0]["key"] == "oneway_anova"
    assert recommend(col3p)[0]["key"] == "rm_anova"
    two = Dataset("2", "column", col3.df[["A", "B"]])
    assert recommend(two)[0]["key"] in ("unpaired_t", "welch_t")
    assert recommend(Dataset("2p", "column", col3.df[["A", "B"]], paired=True))[0]["key"] == "paired_t"
    for d in (col3, col3p, two):
        assert all(not r["blocked"] for r in recommend(d))


def test_two_way_design_checks(grouped):
    ds = grouped.copy(); ds.df = ds.df[~((ds.df.geno == "KO") & (ds.df.time == "t1"))]
    assert any("no data" in m for m in errors("two_way_anova", ds))
    one = grouped.copy(); one.df = one.df.groupby(["geno", "time"]).head(1)
    assert any("single observation" in m for m in errors("two_way_anova", one))
    assert not errors("two_way_anova", one, {"interaction": False})
    nov = grouped.copy(); nov.roles = {**nov.roles, "value": None}
    assert any("Assign" in m for m in errors("two_way_anova", nov))
    dup = grouped.copy(); dup.roles = {**dup.roles, "b": "geno"}
    assert errors("two_way_anova", dup)
    assert any("Tukey" in m for m in errors("two_way_rm", grouped, {"posthoc": "A within each B", "adjust": "tukey"}))


def test_rm_design_checks(grouped):
    ds = grouped.copy(); ds.df = ds.df.drop(index=[2])
    assert any("lack" in m or "missing" in m for m in errors("two_way_rm", ds, {"design": "Both within-subject"}))
    bad = grouped.copy(); bad.df = bad.df.assign(id="s1")
    assert errors("two_way_rm", bad)


def test_multiple_t_needs_two_levels(grouped):
    assert any("exactly two" in m for m in errors("multiple_t", grouped))


def test_transform_domain_and_normalize():
    neg = Dataset("n", "column", pd.DataFrame({"x": [1.0, -2, 3]}))
    assert errors("transform", neg, {"fn": "log10(Y)"}) and not errors("transform", neg, {"fn": "Y squared"})
    assert errors("transform", Dataset("p", "column", pd.DataFrame({"x": [0.2, 1.5]})), {"fn": "arcsine sqrt (proportions 0-1)"})
    assert errors("normalize", neg, {"how": "% of control mean"})
    assert errors("pvalue_stack", Dataset("p", "column", pd.DataFrame({"p": [0.1, 1.5]})))


def test_contingency_requires_counts():
    frac = Dataset("f", "contingency", pd.DataFrame({"g": ["a", "b"], "y": [.5, .2], "n": [.5, .8]}))
    for k in ("fisher_exact", "chi_square", "rr_or", "mcnemar"):
        assert any("whole numbers" in m for m in errors(k, frac))
    big = Dataset("b", "contingency", pd.DataFrame({"g": list("abc"), "y": [3, 4, 5], "n": [6, 7, 8]}))
    assert errors("fisher_exact", big) and errors("mcnemar", big) and errors("rr_or", big)
    sparse = Dataset("s", "contingency", pd.DataFrame({"g": ["a", "b"], "y": [2, 1], "n": [3, 4]}))
    assert any("expected count" in m for m in warnings_("chi_square", sparse))
    zero = Dataset("z", "contingency", pd.DataFrame({"g": ["a", "b"], "y": [0, 0], "n": [3, 4]}))
    assert errors("chi_square", zero)
    assert errors("mcnemar", Dataset("m", "contingency", pd.DataFrame({"g": ["a", "b"], "y": [5, 0], "n": [0, 6]})))


def test_survival_input_checks(surv):
    bad = surv.copy(); bad.df.loc[0, "e"] = 2
    assert any("0 (censored)" in m for m in errors("survival_km", bad))
    neg = surv.copy(); neg.df.loc[0, "t"] = -1
    assert any("Negative" in m for m in errors("survival_km", neg))
    noroles = Dataset("s", "survival", surv.df, {})
    assert any("Assign" in m for m in errors("survival_km", noroles))
    few = Dataset("s", "survival", surv.df.head(8).assign(e=0), surv.roles)
    assert errors("survival_km", few)
    assert not errors("cox_ph", surv)  # the Group role is used as the default covariate
    nog = Dataset("s", "survival", surv.df, {"time": "t", "event": "e"})
    assert errors("cox_ph", nog)


def test_regression_and_fit_checks(xy, rng):
    few = Dataset("f", "xy", pd.DataFrame({"x": [1.0, 2], "y": [1.0, 2]}), {"x": "x", "ys": ["y"]})
    assert errors("linear_regression", few)
    const = Dataset("c", "xy", pd.DataFrame({"x": [1.0, 1, 1, 1], "y": [1.0, 2, 3, 4]}), {"x": "x", "ys": ["y"]})
    assert any("identical" in m for m in errors("linear_regression", const))
    three = Dataset("t", "xy", pd.DataFrame({"x": [1.0, 2, 3], "y": [1.0, 2, 2.5]}), {"x": "x", "ys": ["y"]})
    assert any("more points than parameters" in m for m in errors("nonlinear_fit", three, {"model": "Two-phase decay"}))
    assert any("log(" in m for m in errors("nonlinear_fit", Dataset("l", "xy", pd.DataFrame({"x": np.log10([.1, 1, 10, 100]) - 2, "y": [1, 2, 3, 4.0]}), {"x": "x", "ys": ["y"]}), {"model": "Hill equation"}))
    assert any("unknown parameter" in m for m in errors("nonlinear_fit", xy, {"constraints": "Bogus = 3"}))
    assert errors("nonlinear_fit", xy, {"constraints": "Top = = 4"})
    assert errors("nonlinear_fit", xy, {"custom": "x.__class__"})
    assert errors("nonlinear_fit", xy, {"custom": "__import__('os').system('ls')"})
    ws = xy.copy(); ws.df.loc[0, "resp"] = 0.0
    assert errors("nonlinear_fit", ws, {"weights": "1/Y^2"})
    assert errors("compare_models", xy, {"model_a": "Hill equation", "model_b": "Hill equation"})
    assert errors("compare_models", xy, {"model_a": "Two-phase decay", "model_b": "One-phase decay", "nested": True})
    multi = Dataset("m", "xy", pd.DataFrame({"x": [1.0, 2, 3, 4], "a": [1.0, 2, 3, 5], "b": [2.0, 3, 4, 7]}), {"x": "x", "ys": ["a", "b"]})
    assert any("several Y series" in m for m in errors("linear_regression", multi))
    assert not errors("linear_regression", multi, {"y": "a"})


def test_glm_checks(rng):
    d = pd.DataFrame({"y": rng.normal(size=40), "x": rng.normal(size=40), "k": np.r_[np.zeros(20), np.ones(20)]})
    ds = Dataset("g", "column", d)
    assert errors("logistic", ds, {"response": "y", "predictors": ["x"]})
    sep = Dataset("s", "column", pd.DataFrame({"y": [0, 0, 0, 1, 1, 1] * 3, "x": [1.0, 2, 3, 4, 5, 6] * 3, "z": rng.normal(size=18)}))
    assert any("separation" in m.lower() for m in errors("logistic", sep, {"response": "y", "predictors": ["x"]}))
    assert errors("poisson", ds, {"response": "y", "predictors": ["x"]})
    assert errors("multiple_linear", ds, {"response": "y", "predictors": ["y"]})
    col = Dataset("c", "column", pd.DataFrame({"y": rng.normal(size=40), "a": (a := rng.normal(size=40)), "b": a + rng.normal(size=40) * 1e-3}))
    assert any("multicollinearity" in m for m in warnings_("multiple_linear", col, {"response": "y", "predictors": ["a", "b"]}))


def test_every_registered_test_has_a_check_and_valid_defaults():
    for k, spec in S.REGISTRY.items():
        assert spec.check is not None, k
        assert spec.layouts and spec.family and spec.prism
        for o in spec.options:
            assert o.kind in {"select", "float", "int", "bool", "text", "group", "groups", "series", "series_multi", "column", "columns"}, (k, o.name)
            if o.kind == "select":
                assert o.default in o.choices, (k, o.name)


def test_prism_coverage_present():
    must = {  # Prism analyses -> keys
        "descriptive", "normality", "one_sample_t", "wilcoxon_one_sample", "unpaired_t", "welch_t", "paired_t", "ratio_paired_t", "mann_whitney", "wilcoxon_paired",
        "ks_two_sample", "oneway_anova", "welch_anova", "bf_anova", "kruskal", "rm_anova", "friedman", "mixed_effects", "two_way_anova", "two_way_rm", "three_way_anova",
        "multiple_t", "linear_regression", "nonlinear_fit", "compare_models", "deming", "multiple_linear", "logistic", "poisson", "correlation_xy", "correlation_matrix",
        "survival_km", "cox_ph", "fisher_exact", "chi_square", "chi_square_trend", "mcnemar", "rr_or", "chi_square_gof", "binomial_test", "grubbs", "rout", "pvalue_stack",
        "bland_altman", "roc", "pca", "cluster", "transform", "normalize", "fraction_of_total", "frequency_distribution", "auc", "variance_tests", "compare_lines", "row_stats"}
    assert must <= set(S.REGISTRY)


def test_listing_marks_status(col3):
    rows = {r["key"]: r for r in S.listing(col3)}
    assert rows["oneway_anova"]["status"] in ("ok", "warn") and rows["unpaired_t"]["status"] == "blocked"
    assert "survival_km" not in rows


def test_custom_equation_parser_is_sandboxed():
    from plotsuite.stats.xy import parse_custom
    fn, names = parse_custom("A*exp(-k*x)+c")
    assert names == ["A", "k", "c"] and fn(np.array([0.0]), 2, 1, 3)[0] == pytest.approx(5)
    for evil in ["__import__('os').system('echo hi')", "x.__class__", "(lambda: 1)()", "[1,2][0]", "open('f')", "x if x else 1", "'a'*3", "abs(x, key=1)"]:
        with pytest.raises(Exception):
            parse_custom(evil)
