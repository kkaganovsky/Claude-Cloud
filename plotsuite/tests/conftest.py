import numpy as np
import pandas as pd
import pytest

from plotsuite.data import Dataset


@pytest.fixture
def rng():
    return np.random.default_rng(12345)


@pytest.fixture
def col3(rng):
    return Dataset("c3", "column", pd.DataFrame({"A": rng.normal(10, 2, 12), "B": rng.normal(12, 2, 12), "C": rng.normal(15, 3, 12)}))


@pytest.fixture
def col3p(col3):
    return Dataset("c3p", "column", col3.df.copy(), paired=True)


@pytest.fixture
def grouped(rng):
    rows = [(g, t, f"{g}{s}", rng.normal(10 + (g == "KO") * 2 + i * 1.5, 1.5)) for g in ["WT", "KO"] for i, t in enumerate(["t0", "t1", "t2"]) for s in range(6)]
    return Dataset("g", "grouped", pd.DataFrame(rows, columns=["geno", "time", "id", "y"]), {"value": "y", "a": "geno", "b": "time", "subject": "id"})


@pytest.fixture
def xy(rng):
    x = np.tile([0.1, 0.3, 1, 3, 10, 30, 100, 300.0], 3)
    y = 5 + 95 / (1 + (30 / x) ** 1.2) + rng.normal(0, 2, len(x))
    return Dataset("xy", "xy", pd.DataFrame({"conc": x, "resp": y}), {"x": "conc", "ys": ["resp"]})


@pytest.fixture
def surv(rng):
    n = 40
    df = pd.DataFrame({"t": np.r_[rng.exponential(10, n), rng.exponential(18, n), rng.exponential(30, n)], "e": rng.binomial(1, .8, 3 * n), "g": np.repeat(["a", "b", "c"], n)})
    return Dataset("s", "survival", df, {"time": "t", "event": "e", "group": "g"})
