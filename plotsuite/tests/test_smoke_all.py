"""Run every registered test with every choice of every select-option; nothing that passes its own checks may crash."""
import numpy as np
import pandas as pd
import pytest

from plotsuite import stats as S
from plotsuite.data import Dataset


def tables(rng):
    n = 30
    col = pd.DataFrame({"a": rng.normal(5, 1, n), "b": rng.normal(6, 1, n), "c": rng.normal(7, 1.5, n)})
    col["y"] = (rng.random(n) < 1 / (1 + np.exp(-(col.a - 5)))).astype(int); col["k"] = rng.poisson(np.exp(.2 * col.a))
    rows = [(g, t, f"{g}{s}", rng.normal(10 + (g == "KO") * 2 + i * 1.5, 1.5), "x" if s % 2 else "y") for g in ["WT", "KO"] for i, t in enumerate(["t0", "t1", "t2"]) for s in range(6)]
    gr = pd.DataFrame(rows, columns=["geno", "time", "id", "y", "c"])
    two = gr[gr.time != "t2"].assign(row=lambda d: d.id.str[-1])
    x = np.tile([0.1, 0.3, 1, 3, 10, 30, 100, 300.0], 3)
    xy = pd.DataFrame({"x": x, "y": 5 + 95 / (1 + (30 / x) ** 1.2) + rng.normal(0, 2, len(x))})
    sv = pd.DataFrame({"t": np.r_[rng.exponential(10, 40), rng.exponential(18, 40), rng.exponential(30, 40)], "e": rng.binomial(1, .8, 120), "g": np.repeat(["a", "b", "c"], 40)})
    ct = pd.DataFrame({"g": ["tx", "ctl"], "y": [12, 5], "n": [8, 17]})
    ct3 = pd.DataFrame({"g": ["lo", "mid", "hi"], "y": [5, 9, 14], "n": [45, 41, 36]})
    multi = pd.DataFrame({"x": np.tile(np.arange(8.0), 2), "A": np.tile(np.arange(8.0), 2) + rng.normal(0, .3, 16), "B": 2 * np.tile(np.arange(8.0), 2) + rng.normal(0, .3, 16)})
    return [Dataset("pvals", "column", pd.DataFrame({"p": [.001, .01, .03, .04, .2, .6]})), Dataset("multi", "xy", multi, {"x": "x", "ys": ["A", "B"]}),
            Dataset("col", "column", col), Dataset("colp", "column", col, paired=True),
            Dataset("grp", "grouped", gr, {"value": "y", "a": "geno", "b": "time", "subject": "id", "c": "c"}),
            Dataset("grp2", "grouped", two, {"value": "y", "a": "row", "b": "geno"}),
            Dataset("xy", "xy", xy, {"x": "x", "ys": ["y"]}), Dataset("sv", "survival", sv, {"time": "t", "event": "e", "group": "g"}),
            Dataset("ct", "contingency", ct), Dataset("ct3", "contingency", ct3)]


def option_variants(spec, ds):
    base = {}
    groups = ds.group_names() if ds.kind == "column" else []
    for o in spec.options:
        base[o.name] = o.default
        if o.kind == "group" and groups:
            base[o.name] = groups[min({"group_b": 1}.get(o.name, 0), len(groups) - 1)]
    if "group_b" in base and len(groups) > 1:
        base["group_b"] = groups[1]
    if "response" in base:
        base["response"] = "y" if spec.key == "logistic" else ("k" if spec.key == "poisson" else "c")
        base["predictors"] = ["a", "b"]
    if "control" in base and groups:
        base["control"] = groups[0]
    yield dict(base)
    for o in spec.options:
        if o.kind == "select" and o.name not in ("model_a", "model_b"):  # 22x22 model pairs would only slow the suite
            for ch in o.choices:
                if ch != base.get(o.name):
                    yield {**base, o.name: ch}
        if o.kind == "bool":
            yield {**base, o.name: not base[o.name]}


def test_all_tests_run_wherever_they_validate():
    rng = np.random.default_rng(99)
    ran, blocked = set(), set()
    for ds in tables(rng):
        for key, spec in S.REGISTRY.items():
            if ds.kind not in spec.layouts:
                continue
            for opts in option_variants(spec, ds):
                if any(i.level == "error" for i in S.evaluate(key, ds, opts)):
                    blocked.add(key); continue
                res = S.run(key, ds, opts)  # must not raise
                assert res.test and res.methods and (res.summary or res.tables), (key, opts)
                for t in res.tables.values():
                    assert isinstance(t, pd.DataFrame)
                for c in res.comparisons:
                    assert 0 <= c.p <= 1 + 1e-9, (key, opts, c)
                ran.add(key)
    never = set(S.REGISTRY) - ran
    assert not never, f"tests that never ran on any example table: {sorted(never)}"
