"""Tests for XY tables: regression, correlation, Deming, nonlinear curve fitting, AUC."""
from __future__ import annotations

import ast
import re

import numpy as np
import pandas as pd
import scipy.stats as st
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy.optimize import brentq, curve_fit
from statsmodels.sandbox.stats.runs import runstest_1samp
from statsmodels.stats.anova import anova_lm

from ..data import Dataset
from .core import Comparison, Option, Result, checker, err, fmt_P, fmt_p, info, register, warn

YSEL = Option("y", "Y series", "series", None)
ALPHA = Option("alpha", "Significance level (alpha)", "float", 0.05)


def series(ds: Dataset, y: str | None):
    s = ds.xy_series()
    if y is None:
        if len(s) == 1:
            y = next(iter(s))
        else:
            raise ValueError("choose which Y series to analyse")
    return y, s[y]


def _xy_check(ds, o, nmin=3, need_varied_x=True):
    out = []
    r = ds.roles
    if not r.get("x") or r["x"] not in ds.df.columns:
        return [err("Assign the X column (Data tab > Roles).")]
    s = ds.xy_series()
    if not s:
        return [err("No Y series with numeric (x, y) pairs.")]
    bad = ds.bad_cells([r["x"]] + list(r.get("ys") or [c for c in ds.df.columns if c != r["x"]]))
    if bad:
        out.append(err(f"{len(bad)} non-numeric cell(s), e.g. {bad[0][0]} row {bad[0][1]}: '{bad[0][2]}'."))
    y = o.get("y")
    if y is None and len(s) > 1:
        out.append(err("This table has several Y series: choose which one to analyse."))
        return out
    if y is not None and y not in s:
        return out + [err(f"Series '{y}' has no valid data.")]
    _, t = series(ds, y)
    if len(t) < nmin:
        out.append(err(f"Need at least {nmin} points; found {len(t)}."))
    if need_varied_x and t["x"].nunique() < 2:
        out.append(err("All X values are identical."))
    return out


# ------------------------------------------------------------------ linear regression
@register("linear_regression", "Simple linear regression", "Regression", ["xy"],
          [YSEL, Option("through_origin", "Force line through the origin", "bool", False), ALPHA], prism="Simple linear regression")
def linreg(ds, y=None, through_origin=False, alpha=0.05):
    name, t = series(ds, y)
    X = sm.add_constant(t["x"]) if not through_origin else t[["x"]]
    m = sm.OLS(t["y"], X).fit()
    ci = m.conf_int(alpha)
    slope = m.params["x"]
    icpt = m.params.get("const", 0.0)
    xs = np.linspace(t.x.min(), t.x.max(), 100)
    pr = m.get_prediction(sm.add_constant(xs) if not through_origin else xs.reshape(-1, 1)).summary_frame(alpha=alpha)
    rows = {"Slope": (slope, m.bse["x"], ci.loc["x", 0], ci.loc["x", 1])}
    if not through_origin:
        rows["Y-intercept"] = (icpt, m.bse["const"], ci.loc["const", 0], ci.loc["const", 1])
        rows["X-intercept"] = (-icpt / slope if slope else np.nan, np.nan, np.nan, np.nan)
    bf = pd.DataFrame(rows, index=["Best-fit value", "SE", f"{(1 - alpha) * 100:g}% CI low", f"{(1 - alpha) * 100:g}% CI high"]).T
    gof = pd.DataFrame({"Value": {"R squared": m.rsquared, "Adjusted R squared": m.rsquared_adj, "Sy.x": np.sqrt(m.mse_resid), "n": int(m.nobs),
                                  "F": m.fvalue, "DFn": 1, "DFd": m.df_resid, "P (slope != 0)": m.f_pvalue}})
    tabs = {"Best-fit values": bf, "Goodness of fit / slope test": gof}
    warns = []
    resid = m.resid.to_numpy()
    if len(resid) >= 8 and np.ptp(resid) > 0:
        ps = st.shapiro(resid).pvalue
        tabs["Residual diagnostics"] = pd.DataFrame({"Value": {"Shapiro-Wilk P (residuals normal?)": ps}})
        if ps < alpha:
            warns.append(f"Residuals are not normally distributed (Shapiro-Wilk P={ps:.3g}).")
        if (resid > 0).any() and (resid < 0).any():
            z, pr_ = runstest_1samp(resid, cutoff=0, correction=False)
            tabs["Residual diagnostics"].loc["Runs test P (deviation from linearity)"] = pr_
            if pr_ < alpha:
                warns.append(f"Runs test suggests the data deviate from a straight line (P={pr_:.3g}).")
    return Result("Simple linear regression", f"{name}: Y = {slope:.4g}*X + {icpt:.4g}; R2 = {m.rsquared:.4f}; slope != 0: {fmt_P(m.f_pvalue)}.", tabs,
                  warnings=warns, p=float(m.f_pvalue),
                  curves={"fit": {"x": xs, "y": pr["mean"].to_numpy(), "lo": pr["mean_ci_lower"].to_numpy(), "hi": pr["mean_ci_upper"].to_numpy(), "series": name}},
                  methods="statsmodels.api.OLS; runs test: statsmodels.sandbox.stats.runs.runstest_1samp; Shapiro-Wilk: scipy.stats.shapiro.")


@checker("linear_regression")
def _(ds, o):
    out = _xy_check(ds, o)
    if not out or all(i.level != "error" for i in out):
        _, t = series(ds, o.get("y"))
        if np.ptp(t.y) == 0:
            out.append(warn("All Y values are identical; R squared is undefined."))
        if t.x.nunique() == len(t) and len(t) < 5:
            out.append(info("Very few points: the line has little statistical power."))
    return out


@register("compare_lines", "Compare regression lines (slopes / intercepts)", "Regression", ["xy"],
          [Option("series_sel", "Series to compare", "series_multi", None), ALPHA], prism="Compare slopes and intercepts")
def compare_lines(ds, series_sel=None, alpha=0.05):
    s = ds.xy_series()
    names = series_sel or list(s)
    t = pd.concat([s[n].assign(series=n) for n in names])
    full = smf.ols("y ~ x * C(series)", t).fit()
    par = smf.ols("y ~ x + C(series)", t).fit()
    one = smf.ols("y ~ x", t).fit()
    a1 = anova_lm(par, full); a2 = anova_lm(one, par)
    rows = [("Slopes equal?", a1.loc[1, "F"], a1.loc[1, "df_diff"], a1.loc[1, "df_resid"], a1.loc[1, "Pr(>F)"]),
            ("Elevations (intercepts) equal, assuming common slope?", a2.loc[1, "F"], a2.loc[1, "df_diff"], a2.loc[1, "df_resid"], a2.loc[1, "Pr(>F)"])]
    fits = pd.DataFrame({n: np.polyfit(s[n].x, s[n].y, 1) for n in names}, index=["Slope", "Intercept"]).T
    return Result("Compare regression lines", f"Slopes: {fmt_P(rows[0][4])}; elevations: {fmt_P(rows[1][4])}.",
                  {"Comparison (extra sum-of-squares F tests)": pd.DataFrame(rows, columns=["Hypothesis", "F", "DFn", "DFd", "P"]).set_index("Hypothesis"),
                   "Individual fits": fits}, p=float(rows[0][4]),
                  methods="statsmodels OLS nested models compared with anova_lm (ANCOVA).")


@checker("compare_lines")
def _(ds, o):
    out = _xy_check(ds, {**o, "y": next(iter(ds.xy_series()), None)}, 3)
    s = ds.xy_series()
    names = o.get("series_sel") or list(s)
    if len(names) < 2:
        out.append(err("Need at least two Y series to compare lines."))
    for n in names:
        if n not in s or len(s[n]) < 3:
            out.append(err(f"Series '{n}' needs at least 3 points."))
    return [i for i in out if "several Y series" not in i.message]


# ------------------------------------------------------------------ correlation
@register("correlation_xy", "Correlation (X vs Y)", "Correlation", ["xy"],
          [YSEL, Option("method", "Method", "select", "pearson", ["pearson", "spearman", "kendall"]),
           Option("alternative", "Tails", "select", "two-sided", ["two-sided", "less", "greater"]), ALPHA], prism="Correlation")
def corr_xy(ds, y=None, method="pearson", alternative="two-sided", alpha=0.05):
    name, t = series(ds, y)
    fn = {"pearson": st.pearsonr, "spearman": st.spearmanr, "kendall": st.kendalltau}[method]
    r = fn(t.x, t.y, alternative=alternative)
    rows = {"r" if method == "pearson" else ("rho" if method == "spearman" else "tau"): r.statistic, "P": r.pvalue, "n": len(t)}
    if method == "pearson":
        ci = r.confidence_interval(1 - alpha)
        rows.update({f"{(1 - alpha) * 100:g}% CI low": ci.low, f"{(1 - alpha) * 100:g}% CI high": ci.high, "R squared": r.statistic ** 2})
    return Result(f"{method.title()} correlation", f"{name}: {method} correlation = {r.statistic:.4f}, {fmt_P(r.pvalue)}.",
                  {"Correlation": pd.DataFrame({"Value": rows})}, p=float(r.pvalue),
                  methods=f"scipy.stats.{fn.__name__}. Correlation is symmetric and does not imply causation or a fitted line.")


@checker("correlation_xy")
def _(ds, o):
    out = _xy_check(ds, o, 3)
    if not any(i.level == "error" for i in out):
        _, t = series(ds, o.get("y"))
        if np.ptp(t.y) == 0:
            out.append(err("Y is constant: correlation is undefined."))
        if o.get("method") == "pearson" and len(t) >= 8:
            for lab, v in (("X", t.x), ("Y", t.y)):
                if np.ptp(v) > 0 and st.shapiro(v).pvalue < .05:
                    out.append(warn(f"{lab} fails normality; Pearson assumes both are Gaussian - consider Spearman."))
        if o.get("method") == "pearson":
            out.append(info("Use correlation only when both X and Y are measured (not set by the experimenter); otherwise use regression."))
    return out


# ------------------------------------------------------------------ Deming
@register("deming", "Deming (errors-in-both-variables) regression", "Regression", ["xy"],
          [YSEL, Option("delta", "Error-variance ratio (var Y error / var X error)", "float", 1.0,
                        help="1 = orthogonal regression; use the ratio of the measurement-error variances of the two methods."), ALPHA],
          prism="Deming regression")
def deming(ds, y=None, delta=1.0, alpha=0.05):
    name, t = series(ds, y)

    def fit(x, yv):
        mx, my = x.mean(), yv.mean()
        sxx, syy = ((x - mx) ** 2).sum() / (len(x) - 1), ((yv - my) ** 2).sum() / (len(x) - 1)
        sxy = ((x - mx) * (yv - my)).sum() / (len(x) - 1)
        b = (syy - delta * sxx + np.sqrt((syy - delta * sxx) ** 2 + 4 * delta * sxy ** 2)) / (2 * sxy)
        return b, my - b * mx
    x, yv = t.x.to_numpy(), t.y.to_numpy()
    b, a = fit(x, yv)
    n = len(x)
    jb, ja = [], []
    for i in range(n):
        keep = np.arange(n) != i
        bi, ai = fit(x[keep], yv[keep]); jb.append(bi); ja.append(ai)
    jb, ja = np.array(jb), np.array(ja)
    pb = n * b - (n - 1) * jb; pa = n * a - (n - 1) * ja
    seb, sea = pb.std(ddof=1) / np.sqrt(n), pa.std(ddof=1) / np.sqrt(n)
    tc = st.t.ppf(1 - alpha / 2, n - 2)
    tab = pd.DataFrame({"Best-fit value": [b, a], "SE (jackknife)": [seb, sea], "CI low": [b - tc * seb, a - tc * sea], "CI high": [b + tc * seb, a + tc * sea]},
                       index=["Slope", "Intercept"])
    xs = np.linspace(x.min(), x.max(), 100)
    return Result("Deming regression", f"{name}: Y = {b:.4g}*X + {a:.4g} (delta = {delta:g}).", {"Deming regression": tab},
                  curves={"fit": {"x": xs, "y": a + b * xs, "series": name}},
                  methods="In-house closed-form Deming estimator (Linnet) with jackknife standard errors; verify delta from replicate measurements.")


checker("deming")(lambda ds, o: _xy_check(ds, o, 4) + ([err("The error-variance ratio must be > 0.")] if (o.get("delta") or 0) <= 0 else []))


# ------------------------------------------------------------------ nonlinear
def _poly(*names):
    return lambda x, *p: sum(pi * x ** i for i, pi in enumerate(p))


def _g(x, y):
    return dict(xr=np.ptp(x) or 1.0, ymax=y.max(), ymin=y.min(), yr=np.ptp(y) or 1.0, y0=y[np.argmin(x)], y1=y[np.argmax(x)],
                xmid=float(np.interp(y.min() + np.ptp(y) / 2, y[np.argsort(x)], np.sort(x))) if np.ptp(y) else x.mean(),
                xpk=x[np.argmax(y)])


MODELS: dict[str, dict] = {}


def model(name, names, fn, guess, lower=None, upper=None, needs_pos_x=False, needs_pos_y=False):
    MODELS[name] = dict(fn=fn, names=names, guess=guess, lower=lower, upper=upper, pos_x=needs_pos_x, pos_y=needs_pos_y)


model("Straight line", ["Slope", "Intercept"], lambda x, m, b: m * x + b, lambda x, y: [np.polyfit(x, y, 1)[0], np.polyfit(x, y, 1)[1]])
model("Polynomial: quadratic", ["B0", "B1", "B2"], _poly(), lambda x, y: list(np.polyfit(x, y, 2)[::-1]))
model("Polynomial: cubic", ["B0", "B1", "B2", "B3"], _poly(), lambda x, y: list(np.polyfit(x, y, 3)[::-1]))
model("Exponential growth", ["Y0", "K"], lambda x, y0, k: y0 * np.exp(k * x),
      lambda x, y: [max(y[np.argmin(x)], 1e-6), np.log(max(y.max(), 1e-6) / max(y.min(), 1e-6)) / (np.ptp(x) or 1)])
model("One-phase decay", ["Y0", "Plateau", "K"], lambda x, y0, pl, k: pl + (y0 - pl) * np.exp(-k * x),
      lambda x, y: [(_g(x, y)["y0"]), (_g(x, y)["y1"]), 3 / _g(x, y)["xr"]], lower=[-np.inf, -np.inf, 0])
model("Two-phase decay", ["Plateau", "SpanFast", "SpanSlow", "KFast", "KSlow"],
      lambda x, pl, sf, ss, kf, ks: pl + sf * np.exp(-kf * x) + ss * np.exp(-ks * x),
      lambda x, y: [_g(x, y)["y1"], _g(x, y)["yr"] * .5, _g(x, y)["yr"] * .5, 10 / _g(x, y)["xr"], 1 / _g(x, y)["xr"]],
      lower=[-np.inf, -np.inf, -np.inf, 0, 0])
model("One-phase association", ["Y0", "Plateau", "K"], lambda x, y0, pl, k: y0 + (pl - y0) * (1 - np.exp(-k * x)),
      lambda x, y: [_g(x, y)["y0"], _g(x, y)["y1"], 3 / _g(x, y)["xr"]], lower=[-np.inf, -np.inf, 0])
model("[Agonist] vs response (3 param)", ["Bottom", "Top", "EC50"], lambda x, b, t, e: b + (t - b) * x / (e + x),
      lambda x, y: [y.min(), y.max(), _g(x, y)["xmid"] or 1], needs_pos_x=True, lower=[-np.inf, -np.inf, 0])
model("[Agonist] vs response (variable slope)", ["Bottom", "Top", "EC50", "HillSlope"],
      lambda x, b, t, e, h: b + (t - b) * x ** h / (e ** h + x ** h),
      lambda x, y: [y.min(), y.max(), _g(x, y)["xmid"] or 1, 1.0], needs_pos_x=True, lower=[-np.inf, -np.inf, 1e-12, -np.inf])
model("log(agonist) vs response (variable slope)", ["Bottom", "Top", "LogEC50", "HillSlope"],
      lambda x, b, t, e, h: b + (t - b) / (1 + 10 ** ((e - x) * h)), lambda x, y: [y.min(), y.max(), _g(x, y)["xmid"], 1.0])
model("log(agonist) vs response (3 param)", ["Bottom", "Top", "LogEC50"], lambda x, b, t, e: b + (t - b) / (1 + 10 ** (e - x)),
      lambda x, y: [y.min(), y.max(), _g(x, y)["xmid"]])
model("log(inhibitor) vs response (variable slope)", ["Bottom", "Top", "LogIC50", "HillSlope"],
      lambda x, b, t, e, h: b + (t - b) / (1 + 10 ** ((x - e) * h)), lambda x, y: [y.min(), y.max(), _g(x, y)["xmid"], -1.0])
model("log(inhibitor) vs response (3 param)", ["Bottom", "Top", "LogIC50"], lambda x, b, t, e: b + (t - b) / (1 + 10 ** (x - e)),
      lambda x, y: [y.min(), y.max(), _g(x, y)["xmid"]])
model("Michaelis-Menten / one-site specific binding", ["Vmax", "Km"], lambda x, v, k: v * x / (k + x),
      lambda x, y: [y.max() * 1.2, _g(x, y)["xmid"] or np.median(x)], lower=[0, 0], needs_pos_x=True)
model("One-site total binding", ["Bmax", "Kd", "NS", "Background"], lambda x, bm, kd, ns, bg: bm * x / (kd + x) + ns * x + bg,
      lambda x, y: [y.max(), np.median(x), 0.0, y.min()], lower=[0, 0, 0, -np.inf], needs_pos_x=True)
model("Two-site specific binding", ["Bmax1", "Kd1", "Bmax2", "Kd2"], lambda x, b1, k1, b2, k2: b1 * x / (k1 + x) + b2 * x / (k2 + x),
      lambda x, y: [y.max() * .5, np.percentile(x, 25) or 1, y.max() * .5, np.percentile(x, 75) or 1], lower=[0, 0, 0, 0], needs_pos_x=True)
model("Hill equation", ["Vmax", "K", "n"], lambda x, v, k, n: v * x ** n / (k ** n + x ** n),
      lambda x, y: [y.max(), _g(x, y)["xmid"] or 1, 1.0], lower=[0, 1e-12, 0], needs_pos_x=True)
model("Gaussian", ["Amplitude", "Mean", "SD"], lambda x, a, m, s: a * np.exp(-0.5 * ((x - m) / s) ** 2),
      lambda x, y: [y.max(), _g(x, y)["xpk"], _g(x, y)["xr"] / 6], lower=[-np.inf, -np.inf, 1e-12])
model("Lorentzian", ["Amplitude", "Center", "HalfWidth"], lambda x, a, c, w: a / (1 + ((x - c) / w) ** 2),
      lambda x, y: [y.max(), _g(x, y)["xpk"], _g(x, y)["xr"] / 6], lower=[-np.inf, -np.inf, 1e-12])
model("Boltzmann sigmoid", ["Bottom", "Top", "V50", "Slope"], lambda x, b, t, v, s: b + (t - b) / (1 + np.exp((v - x) / s)),
      lambda x, y: [y.min(), y.max(), _g(x, y)["xmid"], _g(x, y)["xr"] / 10])
model("Sine wave", ["Amplitude", "Wavelength", "Phase", "Offset"], lambda x, a, w, p, o: a * np.sin(2 * np.pi * x / w + p) + o,
      lambda x, y: [_g(x, y)["yr"] / 2, _g(x, y)["xr"] / 2, 0.0, y.mean()], lower=[-np.inf, 1e-12, -np.inf, -np.inf])
model("Power law", ["A", "B"], lambda x, a, b: a * x ** b, lambda x, y: [max(y.mean(), 1e-6), 1.0], needs_pos_x=True)
model("Logistic growth", ["K", "N0", "r"], lambda x, k, n0, r: k / (1 + ((k - n0) / n0) * np.exp(-r * x)),
      lambda x, y: [y.max() * 1.1, max(y.min(), 1e-3), 3 / _g(x, y)["xr"]], lower=[0, 1e-12, 0])

_ALLOWED_FUNCS = {"exp": np.exp, "log": np.log, "ln": np.log, "log10": np.log10, "sqrt": np.sqrt, "sin": np.sin, "cos": np.cos,
                  "tan": np.tan, "tanh": np.tanh, "abs": np.abs, "arctan": np.arctan, "sinh": np.sinh, "cosh": np.cosh}
_CONSTS = {"pi": np.pi, "e": np.e}


def parse_custom(expr: str):
    """Safely turn e.g. 'A*exp(-k*x)+c' into (fn, param_names). Only arithmetic and whitelisted functions."""
    tree = ast.parse(expr.replace("^", "**"), mode="eval")
    names = []
    seen = sorted((n for n in ast.walk(tree) if isinstance(n, ast.Name)), key=lambda n: (n.lineno, n.col_offset))  # source order
    for n in seen:
        if n.id != "x" and n.id not in _ALLOWED_FUNCS and n.id not in _CONSTS and n.id not in names:
            names.append(n.id)
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            continue
        if isinstance(n, ast.Call):
            if not (isinstance(n.func, ast.Name) and n.func.id in _ALLOWED_FUNCS) or n.keywords:
                raise ValueError("only these functions are allowed: " + ", ".join(_ALLOWED_FUNCS))
        elif not isinstance(n, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Load, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow,
                                ast.USub, ast.UAdd, ast.Mod, ast.Call)):
            raise ValueError(f"unsupported syntax in equation: {type(n).__name__}")
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and not isinstance(n.value, (int, float)):
            raise ValueError("only numeric constants are allowed")
    code = compile(tree, "<equation>", "eval")

    def fn(x, *p):
        env = {**_ALLOWED_FUNCS, **_CONSTS, "x": x, **dict(zip(names, p))}
        return eval(code, {"__builtins__": {}}, env)  # noqa: S307 - AST-whitelisted above
    return fn, names


def parse_constraints(text: str, names):
    fixed, lo, hi = {}, {}, {}
    for line in re.split(r"[\n;,]+", text or ""):
        line = line.strip()
        if not line:
            continue
        m = re.fullmatch(r"([A-Za-z_]\w*)\s*(>=|<=|=|>|<)\s*(-?[\d.]+(?:[eE][-+]?\d+)?)", line)
        if not m:
            raise ValueError(f"cannot parse constraint '{line}' (use e.g. Bottom = 0, Top <= 100, K > 0)")
        nm, op, v = m.group(1), m.group(2), float(m.group(3))
        if nm not in names:
            raise ValueError(f"constraint refers to unknown parameter '{nm}' (parameters: {', '.join(names)})")
        if op == "=":
            fixed[nm] = v
        elif op in (">=", ">"):
            lo[nm] = v
        else:
            hi[nm] = v
    return fixed, lo, hi


def parse_initial(text: str):
    d = {}
    for part in re.split(r"[\n;,]+", text or ""):
        if part.strip():
            k, _, v = part.partition("=")
            d[k.strip()] = float(v)
    return d


def _setup(ds, o):
    name, t = series(ds, o.get("y"))
    if o.get("custom"):
        fn, names = parse_custom(o["custom"])
        guess = lambda x, y: [1.0] * len(names)  # noqa: E731
        spec = dict(fn=fn, names=names, guess=guess, lower=None, upper=None, pos_x=False, pos_y=False)
        mname = "Custom: " + o["custom"]
    else:
        spec = MODELS[o["model"]]; mname = o["model"]
    return name, t, spec, mname


def fit_model(x, y, spec, constraints="", initial="", weights="none", n_starts=8, seed=0):
    names = spec["names"]
    fixed, lo_c, hi_c = parse_constraints(constraints, names)
    free = [n for n in names if n not in fixed]
    lo = np.array([spec["lower"][i] if spec["lower"] is not None else -np.inf for i, n in enumerate(names)], float)
    hi = np.array([spec["upper"][i] if spec["upper"] is not None else np.inf for i, n in enumerate(names)], float)
    for i, n in enumerate(names):
        lo[i] = max(lo[i], lo_c.get(n, -np.inf)); hi[i] = min(hi[i], hi_c.get(n, np.inf))
    fi = [names.index(n) for n in free]

    def full(p):
        v = np.empty(len(names))
        v[fi] = p
        for n, val in fixed.items():
            v[names.index(n)] = val
        return v

    def f(xx, *p):
        return spec["fn"](xx, *full(p))
    w = {"none": np.ones_like(y), "1/Y^2": 1 / np.where(y == 0, np.nan, y) ** 2, "1/Y": 1 / np.abs(np.where(y == 0, np.nan, y)),
         "1/X": 1 / np.abs(np.where(x == 0, np.nan, x)), "1/X^2": 1 / np.where(x == 0, np.nan, x) ** 2}[weights]
    sigma = 1 / np.sqrt(w)
    g0 = np.array(spec["guess"](x, y), float)
    ini = parse_initial(initial)
    for n, v in ini.items():
        if n in names:
            g0[names.index(n)] = v
    g0 = np.clip(g0, lo, hi)
    rng = np.random.default_rng(seed)
    best = None
    for k in range(n_starts):
        start = g0 if k == 0 else np.clip(g0 * np.exp(rng.normal(0, .7, len(g0))) + rng.normal(0, .1 * np.abs(g0) + 1e-3), lo, hi)
        try:
            p, cov = curve_fit(f, x, y, p0=start[fi], sigma=sigma, bounds=(lo[fi], hi[fi]), maxfev=20000)
        except Exception:
            continue
        sse = float((((y - f(x, *p)) / sigma) ** 2).sum())
        if np.isfinite(sse) and (best is None or sse < best[0] - 1e-12 * abs(best[0])):
            best = (sse, p, cov, start)
    if best is None:
        raise RuntimeError("The fit did not converge from any starting point. Try giving initial values or constraints.")
    sse, p, cov, _ = best
    return dict(names=names, free=free, fixed=fixed, vals=full(p), pfree=p, cov=cov, sse=sse, sigma=sigma, lo=lo, hi=hi, fi=fi, f=f, w=w, full=full)


@register("nonlinear_fit", "Nonlinear regression (curve fit)", "Regression", ["xy"],
          [YSEL, Option("model", "Equation", "select", "One-phase decay", list(MODELS)),
           Option("custom", "...or custom equation in x (overrides the choice above), e.g. A*exp(-k*x)+c", "text", ""),
           Option("constraints", "Constraints, one per line (Bottom = 0, Top <= 100, K > 0)", "text", ""),
           Option("initial", "Initial values (K = 0.5, Top = 100)", "text", ""),
           Option("weights", "Weighting", "select", "none", ["none", "1/Y^2", "1/Y", "1/X", "1/X^2"]),
           Option("interpolate_y", "Interpolate X for these Y values (comma separated)", "text", ""), ALPHA],
          prism="Nonlinear regression (curve fit)")
def nonlinear(ds, y=None, model="One-phase decay", custom="", constraints="", initial="", weights="none", interpolate_y="", alpha=0.05):
    name, t, spec, mname = _setup(ds, dict(y=y, model=model, custom=custom))
    x, yv = t.x.to_numpy(), t.y.to_numpy()
    r = fit_model(x, yv, spec, constraints, initial, weights)
    n, k = len(x), len(r["pfree"])
    dof = n - k
    se_free = np.sqrt(np.diag(r["cov"]))
    se = {nm: s for nm, s in zip(r["free"], se_free)}
    tc = st.t.ppf(1 - alpha / 2, dof)
    rows = []
    notes = []
    for i, nm in enumerate(r["names"]):
        v = r["vals"][i]
        if nm in r["fixed"]:
            rows.append((nm, v, np.nan, np.nan, np.nan, "Constant"))
        else:
            s = se[nm]
            at = "At bound" if (np.isclose(v, r["lo"][i]) or np.isclose(v, r["hi"][i])) else ""
            rows.append((nm, v, s, v - tc * s if np.isfinite(s) else np.nan, v + tc * s if np.isfinite(s) else np.nan, at))
            if at:
                notes.append(f"Parameter '{nm}' hit its constraint ({v:.4g}); the fit may be poorly determined.")
            if not np.isfinite(s):
                notes.append(f"Parameter '{nm}' has an undefined standard error (ill-defined / over-parameterised model).")
    bf = pd.DataFrame(rows, columns=["Parameter", "Best-fit value", "SE", f"{(1 - alpha) * 100:g}% CI low", f"{(1 - alpha) * 100:g}% CI high", "Note"]).set_index("Parameter")
    wy = np.average(yv, weights=r["w"])
    sst = float((r["w"] * (yv - wy) ** 2).sum())
    wsse = float((r["w"] * (yv - r["f"](x, *r["pfree"])) ** 2).sum())
    r2 = 1 - wsse / sst if sst > 0 else np.nan
    K = k + 1
    aicc = n * np.log(wsse / n) + 2 * K + 2 * K * (K + 1) / (n - K - 1) if n - K - 1 > 0 and wsse > 0 else np.nan
    gof = pd.DataFrame({"Value": {"R squared": r2, "Sy.x": np.sqrt(wsse / dof), "Sum of squares (weighted)": wsse, "Degrees of freedom": dof,
                                  "n": n, "AICc": aicc, "Weighting": weights}})
    tabs = {"Best-fit values": bf, "Goodness of fit": gof}
    if k > 1:
        sd = np.sqrt(np.diag(r["cov"]))
        tabs["Parameter correlation"] = pd.DataFrame(r["cov"] / np.outer(sd, sd), index=r["free"], columns=r["free"])
        if np.nanmax(np.abs(np.triu(tabs["Parameter correlation"].to_numpy(), 1))) > 0.99:
            notes.append("Two parameters are almost perfectly correlated (|r|>0.99): the data cannot define them separately.")
    xs = np.linspace(x.min(), x.max(), 300)
    ys = r["f"](xs, *r["pfree"])
    band = None
    if dof > 0 and np.all(np.isfinite(r["cov"])):  # delta-method confidence band of the fitted curve
        J = np.empty((len(xs), k))
        for j in range(k):
            h = 1e-6 * max(abs(r["pfree"][j]), 1e-3)
            pp, pm = r["pfree"].copy(), r["pfree"].copy(); pp[j] += h; pm[j] -= h
            J[:, j] = (r["f"](xs, *pp) - r["f"](xs, *pm)) / (2 * h)
        se_curve = np.sqrt(np.clip(np.einsum("ij,jk,ik->i", J, r["cov"], J), 0, None))
        band = (ys - tc * se_curve, ys + tc * se_curve)
    resid = yv - r["f"](x, *r["pfree"])
    tabs["Residuals"] = pd.DataFrame({"X": x, "Y": yv, "Fitted": yv - resid, "Residual": resid})
    if len(resid) >= 8 and (resid > 0).any() and (resid < 0).any():
        z, pr_ = runstest_1samp(resid, cutoff=0, correction=False)
        gof.loc["Runs test P (systematic deviation)"] = pr_
        if pr_ < alpha:
            notes.append(f"Runs test P={pr_:.3g}: residuals are not randomly scattered - the equation may not describe the data.")
    if dof <= 0:
        notes.append("No degrees of freedom left.")
    if r2 < 0:
        notes.append("R squared is negative: the model fits worse than a horizontal line.")
    if interpolate_y.strip():
        out = []
        for s in re.split(r"[,;\s]+", interpolate_y.strip()):
            y0 = float(s)
            dense = np.linspace(x.min(), x.max(), 5000)
            d = r["f"](dense, *r["pfree"]) - y0
            idx = np.where(np.sign(d[:-1]) * np.sign(d[1:]) <= 0)[0]
            if not len(idx):
                out.append((y0, "outside the fitted curve's range (no extrapolation done)"))
            else:
                roots = [brentq(lambda z: r["f"](z, *r["pfree"]) - y0, dense[i], dense[i + 1]) for i in idx]
                out.append((y0, ", ".join(f"{z:.5g}" for z in roots) + (" (multiple solutions)" if len(roots) > 1 else "")))
        tabs["Interpolated X"] = pd.DataFrame(out, columns=["Y", "X"]).set_index("Y")
    s = "; ".join(f"{a} = {b:.4g}" for a, b in zip(r["names"], r["vals"]))
    return Result(f"Nonlinear regression: {mname}", f"{name}: {s}; R2 = {r2:.4f}.", tabs, warnings=notes,
                  curves={"fit": {"x": xs, "y": ys, "series": name, **({"lo": band[0], "hi": band[1]} if band else {})}},
                  methods="scipy.optimize.curve_fit (trust-region reflective, multi-start); SEs and the curve's confidence band from the asymptotic covariance (delta method); AICc with K = parameters + 1. Custom equations are parsed with a whitelist.")


@checker("nonlinear_fit")
def _(ds, o):
    out = _xy_check(ds, o, 3)
    if any(i.level == "error" for i in out):
        return out
    try:
        name, t, spec, mname = _setup(ds, o)
        fixed, lo, hi = parse_constraints(o.get("constraints", ""), spec["names"])
        parse_initial(o.get("initial", ""))
    except Exception as e:
        return out + [err(str(e))]
    k = len(spec["names"]) - len(fixed)
    n = len(t)
    if n <= k:
        out.append(err(f"'{mname}' has {k} free parameter(s) but only {n} data point(s); need more points than parameters."))
    elif n < k + 3:
        out.append(warn(f"Only {n - k} degrees of freedom: parameter estimates will be very uncertain."))
    if spec.get("pos_x") and (t.x < 0).any():
        out.append(err("This equation needs X >= 0 (concentration, not log concentration). Use the log(...) version for log-scaled X."))
    if o.get("weights") in ("1/Y", "1/Y^2") and (t.y == 0).any():
        out.append(err("Weighting by 1/Y fails when any Y = 0."))
    if o.get("weights") in ("1/X", "1/X^2") and (t.x == 0).any():
        out.append(err("Weighting by 1/X fails when any X = 0."))
    if lo and hi and any(lo.get(n_, -np.inf) > hi.get(n_, np.inf) for n_ in spec["names"]):
        out.append(err("A parameter's lower constraint is above its upper constraint."))
    if o.get("interpolate_y"):
        try:
            [float(s) for s in re.split(r"[,;\s]+", o["interpolate_y"].strip())]
        except ValueError:
            out.append(err("Interpolation values must be numbers separated by commas."))
    return out


@register("compare_models", "Compare two nonlinear models", "Regression", ["xy"],
          [YSEL, Option("model_a", "Simpler model", "select", "One-phase decay", list(MODELS)),
           Option("model_b", "More complex model", "select", "Two-phase decay", list(MODELS)),
           Option("nested", "The simpler model is a special case of the complex one (nested)", "bool", False,
                  "Required for the extra-sum-of-squares F test; AICc works for any pair."), ALPHA], prism="Compare models (extra sum-of-squares F test / AICc)")
def compare_models(ds, y=None, model_a="One-phase decay", model_b="Two-phase decay", nested=False, alpha=0.05):
    name, t = series(ds, y)
    x, yv = t.x.to_numpy(), t.y.to_numpy()
    fa = fit_model(x, yv, MODELS[model_a]); fb = fit_model(x, yv, MODELS[model_b])
    n = len(x)
    def stats_(f):
        k = len(f["pfree"]); K = k + 1
        return k, f["sse"], n - k, n * np.log(f["sse"] / n) + 2 * K + 2 * K * (K + 1) / (n - K - 1)
    ka, ssa, dfa, aa = stats_(fa); kb, ssb, dfb, ab = stats_(fb)
    rows = pd.DataFrame({model_a: [ka, ssa, dfa, aa], model_b: [kb, ssb, dfb, ab]}, index=["Parameters", "Sum of squares", "df", "AICc"])
    d = ab - aa
    pa = 1 / (1 + np.exp(-0.5 * d))  # Akaike weight of model A
    pb = 1 - pa
    tabs = {"Fits": rows, "AICc comparison": pd.DataFrame({"Probability model is correct": {model_a: pa, model_b: pb}})}
    msg = f"AICc prefers '{model_a if aa < ab else model_b}' (probability {max(pa, pb):.3f})."
    p = None
    if nested:
        F = ((ssa - ssb) / (dfa - dfb)) / (ssb / dfb)
        p = float(st.f.sf(F, dfa - dfb, dfb))
        tabs["Extra sum-of-squares F test"] = pd.DataFrame({"Value": {"F": F, "DFn": dfa - dfb, "DFd": dfb, "P": p}})
        msg += f" F test: F({dfa - dfb},{dfb}) = {F:.3f}, {fmt_P(p)} ({'complex' if p < alpha else 'simpler'} model preferred)."
    return Result("Compare nonlinear models", msg, tabs, p=p, methods="Two scipy.optimize.curve_fit fits; AICc weights; extra sum-of-squares F test via scipy.stats.f.")


@checker("compare_models")
def _(ds, o):
    out = _xy_check(ds, o, 4)
    if any(i.level == "error" for i in out):
        return out
    a, b = o.get("model_a"), o.get("model_b")
    if a == b:
        return out + [err("Choose two different models.")]
    _, t = series(ds, o.get("y"))
    ka, kb = len(MODELS[a]["names"]), len(MODELS[b]["names"])
    if len(t) <= max(ka, kb) + 1:
        out.append(err("Not enough data points to fit and compare these models (need more points than the larger model's parameters + 1)."))
    if o.get("nested") and ka >= kb:
        out.append(err("For the F test the simpler model must have fewer parameters than the complex one."))
    if not o.get("nested"):
        out.append(info("F test is skipped (models not declared nested); AICc is reported."))
    return out


@register("auc", "Area under the curve", "Data analysis", ["xy"],
          [YSEL, Option("baseline", "Baseline Y", "float", 0.0)], prism="Area under the curve")
def auc(ds, y=None, baseline=0.0):
    name, t = series(ds, y)
    t = t.sort_values("x")
    x, yv = t.x.to_numpy(), t.y.to_numpy() - baseline
    total = np.trapezoid(yv, x)
    pos = np.trapezoid(np.clip(yv, 0, None), x)
    neg = np.trapezoid(np.clip(yv, None, 0), x)
    return Result("Area under the curve", f"{name}: total area {total:.5g} (baseline {baseline:g}).",
                  {"AUC": pd.DataFrame({"Value": {"Total area": total, "Area above baseline": pos, "Area below baseline": neg, "Peak Y": yv.max() + baseline, "X at peak": x[np.argmax(yv)]}})},
                  methods="numpy.trapezoid (trapezoidal rule).")


checker("auc")(lambda ds, o: _xy_check(ds, o, 2))
