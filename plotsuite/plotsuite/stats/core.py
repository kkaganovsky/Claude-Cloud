"""Test registry, result container and the guardrail (check) machinery.

Every test is a :class:`TestSpec` with
  * ``layouts``  - which table kinds it can run on
  * ``options``  - declarative option schema (the GUI renders controls from it)
  * ``check``    - returns a list of :class:`Issue`; any ``error`` blocks the run
  * ``run``      - does the computation using scipy / statsmodels / pingouin / scikit-*
"""
from __future__ import annotations

import dataclasses as dc
import importlib.metadata as md
from typing import Callable

import numpy as np
import pandas as pd

from ..data import Dataset


class BlockedError(Exception):
    def __init__(self, issues: list["Issue"]):
        self.issues = issues
        super().__init__("; ".join(i.message for i in issues if i.level == "error"))


@dc.dataclass
class Issue:
    level: str  # "error" blocks the run, "warning" runs but is reported, "info"
    message: str


def err(m): return Issue("error", m)
def warn(m): return Issue("warning", m)
def info(m): return Issue("info", m)


@dc.dataclass
class Option:
    name: str
    label: str
    kind: str = "select"  # select | float | int | bool | text | column | columns
    default: object = None
    choices: list | None = None
    help: str = ""
    source: str = ""  # for column/columns: "numeric" | "all" | "binary"


@dc.dataclass
class Comparison:
    a: str
    b: str
    p: float  # adjusted where applicable
    diff: float | None = None
    label: str = ""


@dc.dataclass
class Result:
    test: str
    summary: str = ""
    tables: dict[str, pd.DataFrame] = dc.field(default_factory=dict)
    comparisons: list[Comparison] = dc.field(default_factory=list)
    warnings: list[str] = dc.field(default_factory=list)
    methods: str = ""
    curves: dict = dc.field(default_factory=dict)  # overlay data for graphs (fits, KM, ROC...)
    new_dataset: Dataset | None = None
    p: float | None = None  # headline p-value when there is one

    def text(self) -> str:
        out = [f"== {self.test} ==", self.summary]
        for k, t in self.tables.items():
            out.append(f"\n-- {k} --\n{t.to_string()}")
        if self.warnings:
            out.append("\nWarnings:\n" + "\n".join(f"  ! {w}" for w in self.warnings))
        out.append(f"\nMethods: {self.methods}")
        return "\n".join(out)


@dc.dataclass
class TestSpec:
    __test__ = False  # not a pytest class
    key: str
    name: str
    family: str
    layouts: tuple
    run: Callable
    check: Callable | None = None
    options: list = dc.field(default_factory=list)
    description: str = ""
    prism: str = ""  # Prism analysis it corresponds to


REGISTRY: dict[str, TestSpec] = {}


def register(key, name, family, layouts, options=None, description="", prism="", check=None):
    def deco(fn):
        REGISTRY[key] = TestSpec(key, name, family, tuple(layouts), fn, check, options or [], description, prism or name)
        return fn
    return deco


def checker(key):
    def deco(fn):
        REGISTRY[key].check = fn
        return fn
    return deco


def defaults(spec: TestSpec) -> dict:
    return {o.name: o.default for o in spec.options}


def evaluate(key: str, ds: Dataset, opts: dict | None = None) -> list[Issue]:
    spec = REGISTRY[key]
    opts = {**defaults(spec), **(opts or {})}
    if ds.kind not in spec.layouts:
        return [err(f"'{spec.name}' needs a {'/'.join(spec.layouts)} table; this table is '{ds.kind}'.")]
    issues = generic_checks(ds)
    if any(i.level == "error" for i in issues):
        return issues
    try:
        if spec.check:
            issues += spec.check(ds, opts) or []
    except Exception as e:  # a failing check must never let an unvalidated run through
        issues.append(err(f"Could not validate data for this test: {e}"))
    return issues


def generic_checks(ds: Dataset) -> list[Issue]:
    out = []
    if ds.df.empty:
        return [err("The table is empty.")]
    cols = [str(c) for c in ds.df.columns]
    dup = sorted({c for c in cols if cols.count(c) > 1})
    if dup:
        out.append(err(f"Duplicate column names: {', '.join(dup)}. Rename them so each is unique."))
    if ds.kind == "column":
        bad = ds.bad_cells()
        if bad:
            ex = ", ".join(f"{c}[row {r}]='{v}'" for c, r, v in bad[:4])
            out.append(err(f"{len(bad)} non-numeric cell(s) in the data ({ex}...). Fix or clear them; nothing is silently dropped."))
        m = ds.numeric()
        if not np.isfinite(m.to_numpy()).any():
            out.append(err("No numeric data found."))
    return out


def run(key: str, ds: Dataset, opts: dict | None = None, acknowledge_warnings: bool = True) -> Result:
    """Validate then run. Errors raise BlockedError. Warnings are attached to the Result."""
    spec = REGISTRY[key]
    opts = {**defaults(spec), **(opts or {})}
    issues = evaluate(key, ds, opts)
    errors = [i for i in issues if i.level == "error"]
    if errors:
        raise BlockedError(errors)
    res = spec.run(ds, **opts)
    res.test = res.test or spec.name
    res.warnings = [i.message for i in issues if i.level == "warning"] + res.warnings
    res.methods = (res.methods + " " if res.methods else "") + versions()
    return res


def versions() -> str:
    def v(p):
        try:
            return md.version(p)
        except Exception:
            return "?"
    return f"[scipy {v('scipy')}, statsmodels {v('statsmodels')}, pingouin {v('pingouin')}, scikit-posthocs {v('scikit-posthocs')}, scikit-learn {v('scikit-learn')}]"


def listing(ds: Dataset) -> list[dict]:
    """All tests with status for this table: ok / warn / blocked (+ reasons). Drives the GUI menu."""
    rows = []
    for k, spec in REGISTRY.items():
        if ds.kind not in spec.layouts:
            continue
        iss = evaluate(k, ds)
        status = "blocked" if any(i.level == "error" for i in iss) else ("warn" if any(i.level == "warning" for i in iss) else "ok")
        rows.append({"key": k, "name": spec.name, "family": spec.family, "status": status, "issues": iss})
    return rows


# ---- formatting ---------------------------------------------------------------
def fmt_p(p, style="GP") -> str:
    if p is None or (isinstance(p, float) and np.isnan(p)):
        return "n/a"
    if p < 0.0001:
        return "<0.0001"
    return f"{p:.4f}" if style == "GP" else f"{p:.3g}"


def fmt_P(p) -> str:
    """'P < 0.0001' or 'P = 0.0123' (use inside sentences instead of 'P = <0.0001')."""
    t = fmt_p(p)
    return "P " + t.replace("<", "< ") if t.startswith("<") else f"P = {t}"


def stars(p) -> str:
    if p is None or np.isnan(p):
        return "n/a"
    return "ns" if p >= 0.05 else "*" if p >= 0.01 else "**" if p >= 0.001 else "***" if p >= 0.0001 else "****"


def table(rows, cols=None, index=None) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=cols, index=index)
