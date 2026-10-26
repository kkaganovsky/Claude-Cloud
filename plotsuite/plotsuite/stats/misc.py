"""Outliers, P-value families, transforms, multivariate, ROC, Bland-Altman, GLMs."""
from __future__ import annotations

import numpy as np
import pandas as pd
import scipy.stats as st
import statsmodels.api as sm
from scipy.optimize import minimize_scalar
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.outliers_influence import variance_inflation_factor

from ..data import Dataset
from . import checks as CK
from .core import Option, Result, checker, err, fmt_P, fmt_p, info, register, stars, warn

GROUP = Option("group_a", "Column", "group", None)
GROUPS = Option("groups", "Columns to include", "groups", None)
ALPHA = Option("alpha", "Significance level (alpha)", "float", 0.05)


def _one(ds, name):
    g = ds.group_arrays()
    if name:
        return name, g[name]
    if len(g) == 1:
        return next(iter(g.items()))
    raise ValueError("choose a column")


def _one_chk(ds, o, nmin):
    g = ds.group_arrays()
    if not o.get("group_a") and len(g) != 1:
        return [err("Choose which column to analyse.")]
    n, v = _one(ds, o.get("group_a"))
    return CK.min_n({n: v}, nmin)


# ------------------------------------------------------------------ outliers
@register("grubbs", "Outlier test: Grubbs", "Outliers", ["column"],
          [GROUP, Option("iterate", "Repeat after removing an outlier", "bool", False), ALPHA], prism="Identify outliers (Grubbs)")
def grubbs(ds, group_a=None, iterate=False, alpha=0.05):
    name, v = _one(ds, group_a)
    cur = v.copy(); found = []
    while len(cur) >= 3 and np.ptp(cur) > 0:
        n = len(cur); m, s = cur.mean(), cur.std(ddof=1)
        i = int(np.argmax(np.abs(cur - m))); G = abs(cur[i] - m) / s
        tc = st.t.isf(alpha / (2 * n), n - 2)
        Gc = (n - 1) / np.sqrt(n) * np.sqrt(tc ** 2 / (n - 2 + tc ** 2))
        denom = (n - 1) ** 2 - n * G ** 2
        p = min(1.0, 2 * n * st.t.sf(G * np.sqrt(n * (n - 2) / denom), n - 2)) if denom > 0 else 0.0
        if G > Gc:
            found.append((cur[i], G, Gc, p)); cur = np.delete(cur, i)
            if not iterate:
                break
        else:
            if not found:
                found.append((cur[i], G, Gc, p)); found[-1] = ("none significant (most extreme: %.5g)" % cur[i], G, Gc, p)
            break
    t = pd.DataFrame(found, columns=["Suspected value", "G", "G critical", "P"])
    sig = [f for f in found if not isinstance(f[0], str)]
    return Result("Grubbs' outlier test", f"{name}: {len(sig)} outlier(s) detected at alpha={alpha:g}" + (f": {', '.join(f'{f[0]:.5g}' for f in sig)}." if sig else "."),
                  {"Grubbs": t}, methods="In-house Grubbs' test (critical value from scipy.stats.t.isf); Prism-equivalent formulation. Removing outliers needs a justification beyond the test.")


@checker("grubbs")
def _(ds, o):
    out = _one_chk(ds, o, 3)
    if not out:
        n, v = _one(ds, o.get("group_a"))
        out += CK.normality({n: v})
        if o.get("iterate"):
            out.append(warn("Iterative Grubbs' removal inflates the false-positive rate; prefer one pass unless you pre-specified this."))
    return out


@register("rout", "Outlier test: ROUT (experimental)", "Outliers", ["column"],
          [GROUP, Option("q", "Q (max. false discovery rate, %)", "float", 1.0)], prism="Identify outliers (ROUT)")
def rout(ds, group_a=None, q=1.0):
    name, v = _one(ds, group_a)
    N = len(v); K = 1
    mu = np.median(v)
    rsdr = np.percentile(np.abs(v - mu), 68.27) * N / (N - K)
    lo, hi = v.min(), v.max()
    for _ in range(100):
        f = lambda m, s=rsdr: np.log1p(((v - m) / s) ** 2 / 2).sum()  # noqa: E731
        mu_new = minimize_scalar(f, bounds=(lo, hi), method="bounded", options={"xatol": 1e-10}).x
        rs_new = np.percentile(np.abs(v - mu_new), 68.27) * N / (N - K)
        done = abs(mu_new - mu) < 1e-10 * (abs(mu) + 1) and abs(rs_new - rsdr) < 1e-10 * (rsdr + 1)
        mu, rsdr = mu_new, rs_new
        if done:
            break
    tstat = np.abs(v - mu) / rsdr
    p = 2 * st.t.sf(tstat, N - K)
    rej = multipletests(p, alpha=q / 100, method="fdr_bh")[0]
    t = pd.DataFrame({"Value": v, "t ratio": tstat, "P (unadj.)": p, "Outlier": np.where(rej, "Yes", "no")})
    return Result("ROUT outlier detection", f"{name}: robust centre {mu:.5g}, RSDR {rsdr:.5g}; {int(rej.sum())} outlier(s) at Q={q:g}%.", {"ROUT": t},
                  warnings=["EXPERIMENTAL: follows the published ROUT outline (robust fit -> RSDR -> t ratios -> FDR) but the exact merit function and FDR step-up were not verified against GraphPad Prism. Cross-check with Grubbs'."],
                  methods="In-house: Lorentzian robust location (scipy.optimize.minimize_scalar), RSDR from 68.27th percentile, Benjamini-Hochberg via statsmodels.")


checker("rout")(lambda ds, o: _one_chk(ds, o, 8) + ([err("Q must be between 0 and 100.")] if not 0 < (o.get("q") or 1) < 100 else []))


# ------------------------------------------------------------------ P-value family
@register("pvalue_stack", "Analyze a stack of P values (multiplicity correction)", "Multiple comparisons", ["column"],
          [GROUP, Option("method", "Method", "select", "fdr_bh",
                         ["bonferroni", "sidak", "holm", "holm-sidak", "fdr_bh", "fdr_by", "fdr_tsbky"]), ALPHA], prism="Analyze a stack of P values")
def pstack(ds, group_a=None, method="fdr_bh", alpha=0.05):
    name, v = _one(ds, group_a)
    rej, adj, _, _ = multipletests(v, alpha=alpha, method=method)
    t = pd.DataFrame({"P (input)": v, "Adjusted P / q": adj, "Significant / discovery": np.where(rej, "Yes", "No"), "Summary": [stars(x) for x in adj]})
    return Result("P-value stack", f"{int(rej.sum())} of {len(v)} significant using {method} (alpha={alpha:g}).", {"Adjusted P values": t},
                  methods="statsmodels.stats.multitest.multipletests.")


@checker("pvalue_stack")
def _(ds, o):
    out = _one_chk(ds, o, 2)
    if not out:
        _, v = _one(ds, o.get("group_a"))
        if ((v < 0) | (v > 1)).any():
            out.append(err("All values must be P values between 0 and 1."))
    return out


# ------------------------------------------------------------------ transforms etc.
TRANS = {"log10(Y)": (np.log10, True), "ln(Y)": (np.log, True), "log2(Y)": (np.log2, True), "sqrt(Y)": (np.sqrt, "nonneg"),
         "1/Y": (lambda y: 1 / y, "nonzero"), "Y squared": (np.square, False), "exp(Y)": (np.exp, False),
         "arcsine sqrt (proportions 0-1)": (lambda y: np.arcsin(np.sqrt(y)), "prop"), "logit (proportions 0-1)": (lambda y: np.log(y / (1 - y)), "open_prop")}


@register("transform", "Transform data (Y = f(Y))", "Data processing", ["column"],
          [GROUPS, Option("fn", "Transformation", "select", "log10(Y)", list(TRANS)),
           Option("k", "Optional constant added before transforming (Y + k)", "float", 0.0)], prism="Transform")
def transform(ds, groups=None, fn="log10(Y)", k=0.0):
    names = groups or list(ds.group_arrays())
    f, _ = TRANS[fn]
    out = ds.numeric(names) + k
    out = out.apply(lambda c: pd.Series(f(c.to_numpy()), index=c.index))
    out.columns = [f"{fn.replace('Y', c)}" for c in names]
    return Result("Transform", f"Applied {fn} to {len(names)} column(s); result is a new table.", {"Transformed": out},
                  new_dataset=Dataset(f"{ds.name} [{fn}]", "column", out, paired=ds.paired), methods="numpy.")


@checker("transform")
def _(ds, o):
    names = o.get("groups") or list(ds.group_arrays())
    m = ds.numeric(names) + (o.get("k") or 0)
    rule = TRANS[o.get("fn", "log10(Y)")][1]
    v = m.to_numpy(); v = v[~np.isnan(v)]
    bad = {True: v <= 0, "nonneg": v < 0, "nonzero": v == 0, "prop": (v < 0) | (v > 1), "open_prop": (v <= 0) | (v >= 1), False: np.zeros_like(v, bool)}[rule]
    if bad.any():
        return [err(f"{int(bad.sum())} value(s) are outside the domain of '{o.get('fn')}' (e.g. {v[bad][0]:.4g}). Fix the data or add a constant.")]
    return []


@register("normalize", "Normalize", "Data processing", ["column"],
          [GROUPS, Option("how", "Method", "select", "0-100% (min to max)", ["0-100% (min to max)", "% of control mean", "z-score", "% of column maximum", "% of column sum"]),
           Option("control", "Control column (for % of control)", "group", None)], prism="Normalize")
def normalize(ds, groups=None, how="0-100% (min to max)", control=None):
    names = groups or list(ds.group_arrays())
    m = ds.numeric(names)
    if how.startswith("0-100"):
        lo, hi = m.min().min(), m.max().max()
        out = (m - lo) / (hi - lo) * 100
    elif how == "% of control mean":
        out = m / ds.numeric([control])[control].mean() * 100
    elif how == "z-score":
        out = (m - m.mean()) / m.std(ddof=1)
    elif how == "% of column maximum":
        out = m / m.max() * 100
    else:
        out = m / m.sum() * 100
    return Result("Normalize", f"{how} applied to {len(names)} column(s); result is a new table.", {"Normalized": out},
                  new_dataset=Dataset(f"{ds.name} [normalized]", "column", out, paired=ds.paired), methods="pandas/numpy.")


@checker("normalize")
def _(ds, o):
    out = []
    names = o.get("groups") or list(ds.group_arrays())
    if o.get("how") == "% of control mean":
        c = o.get("control")
        if not c or c not in ds.group_arrays():
            out.append(err("Choose the control column."))
        elif ds.numeric([c])[c].mean() == 0:
            out.append(err("The control mean is zero."))
    if o.get("how", "").startswith("0-100") and ds.numeric(names).max().max() == ds.numeric(names).min().min():
        out.append(err("All values are identical; min-max normalisation is undefined."))
    return out


@register("fraction_of_total", "Fraction of total", "Data processing", ["column"], [GROUPS, Option("pct", "As percent", "bool", True)], prism="Fraction of total")
def frac(ds, groups=None, pct=True):
    names = groups or list(ds.group_arrays())
    m = ds.numeric(names)
    out = m / m.sum() * (100 if pct else 1)
    return Result("Fraction of total", "Each value divided by its column total.", {"Fraction of total": out},
                  new_dataset=Dataset(f"{ds.name} [fraction]", "column", out), methods="pandas.")


checker("fraction_of_total")(lambda ds, o: [err("Column total is zero or negative values present.")] if ((ds.numeric(o.get("groups") or None).sum() <= 0).any() or (ds.numeric(o.get("groups") or None) < 0).any().any()) else [])


@register("frequency_distribution", "Frequency distribution (histogram)", "Data processing", ["column"],
          [GROUP, Option("bin_width", "Bin width (0 = automatic)", "float", 0.0), Option("cumulative", "Cumulative", "bool", False),
           Option("relative", "Relative frequency (%)", "bool", False)], prism="Frequency distribution")
def freqdist(ds, group_a=None, bin_width=0.0, cumulative=False, relative=False):
    name, v = _one(ds, group_a)
    if bin_width > 0:
        edges = np.arange(np.floor(v.min() / bin_width) * bin_width, v.max() + bin_width, bin_width)
    else:
        edges = np.histogram_bin_edges(v, bins="auto")
    h, e = np.histogram(v, bins=edges)
    if cumulative:
        h = np.cumsum(h)
    y = h / len(v) * 100 if relative else h
    c = (e[:-1] + e[1:]) / 2
    df = pd.DataFrame({"Bin center": c, "Bin start": e[:-1], "Bin end": e[1:], name: y})
    ds_new = Dataset(f"{ds.name} [{name} histogram]", "xy", df[["Bin center", name]], {"x": "Bin center", "ys": [name]})
    return Result("Frequency distribution", f"{len(c)} bins.", {"Frequency distribution": df}, new_dataset=ds_new, methods="numpy.histogram_bin_edges('auto') / numpy.histogram.")


checker("frequency_distribution")(lambda ds, o: _one_chk(ds, o, 3))


@register("row_stats", "Row statistics (mean, SD, SEM per row)", "Data processing", ["column"], [GROUPS], prism="Row means with SD or SEM")
def rowstats(ds, groups=None):
    m = ds.numeric(groups or list(ds.group_arrays()))
    out = pd.DataFrame({"Mean": m.mean(axis=1), "SD": m.std(axis=1, ddof=1), "SEM": m.std(axis=1, ddof=1) / np.sqrt(m.notna().sum(axis=1)), "N": m.notna().sum(axis=1)})
    return Result("Row statistics", "Per-row summary statistics.", {"Row statistics": out}, methods="pandas.")


checker("row_stats")(lambda ds, o: [])


# ------------------------------------------------------------------ multivariate
@register("correlation_matrix", "Correlation matrix", "Correlation", ["column"],
          [GROUPS, Option("method", "Method", "select", "pearson", ["pearson", "spearman"]),
           Option("adjust", "Adjust P values", "select", "holm", ["none", "bonferroni", "holm", "fdr_bh"])], prism="Correlation matrix")
def corrmat(ds, groups=None, method="pearson", adjust="holm"):
    names = groups or list(ds.group_arrays())
    m = ds.numeric(names)
    k = len(names)
    R = pd.DataFrame(np.eye(k), index=names, columns=names); P = pd.DataFrame(np.nan, index=names, columns=names); Nn = P.copy()
    pairs = []
    for i in range(k):
        for j in range(i + 1, k):
            d = m[[names[i], names[j]]].dropna()
            r = (st.pearsonr if method == "pearson" else st.spearmanr)(d.iloc[:, 0], d.iloc[:, 1])
            R.iloc[i, j] = R.iloc[j, i] = r.statistic
            pairs.append((i, j, r.pvalue, len(d)))
    ps = [p[2] for p in pairs]
    adj = ps if adjust == "none" else multipletests(ps, method=adjust)[1]
    for (i, j, _, n), a in zip(pairs, adj):
        P.iloc[i, j] = P.iloc[j, i] = a; Nn.iloc[i, j] = Nn.iloc[j, i] = n
    return Result("Correlation matrix", f"{method} correlations among {k} variables ({len(pairs)} pairs, P adjusted: {adjust}).",
                  {"r": R, "Adjusted P": P, "n (pairs)": Nn},
                  new_dataset=Dataset(f"{ds.name} [r matrix]", "column", R.reset_index(drop=True)),
                  methods="scipy.stats.pearsonr / spearmanr, pairwise-complete; statsmodels multipletests.")


@checker("correlation_matrix")
def _(ds, o):
    g = CK.groups_of(ds, o.get("groups") or None)
    out = CK.n_groups(g, 3, hint="For two variables use the XY correlation.") + CK.min_n(g, 4)
    return out + [info("Many pairwise tests: P values are adjusted for multiplicity.")]


def _complete(ds, names):
    return ds.numeric(names).dropna()


@register("pca", "Principal component analysis", "Multivariate", ["column"],
          [GROUPS, Option("scale", "Preprocessing", "select", "standardize (correlation matrix)", ["standardize (correlation matrix)", "center only (covariance matrix)"]),
           Option("ncomp", "Components to keep (0 = all)", "int", 0)], prism="Principal component analysis (PCA)")
def pca(ds, groups=None, scale="standardize (correlation matrix)", ncomp=0):
    from sklearn.decomposition import PCA
    names = groups or list(ds.group_arrays())
    X = _complete(ds, names)
    Z = (X - X.mean()) / (X.std(ddof=1) if scale.startswith("standardize") else 1)
    k = ncomp or min(Z.shape)
    p = PCA(n_components=k).fit(Z.to_numpy())
    cols = [f"PC{i + 1}" for i in range(p.n_components_)]
    ev = pd.DataFrame({"Eigenvalue": p.explained_variance_, "% variance": p.explained_variance_ratio_ * 100,
                       "Cumulative %": np.cumsum(p.explained_variance_ratio_) * 100}, index=cols)
    load = pd.DataFrame(p.components_.T, index=names, columns=cols)
    scores = pd.DataFrame(p.transform(Z.to_numpy()), index=X.index, columns=cols)
    return Result("PCA", f"PC1 explains {ev.iloc[0, 1]:.1f}% of variance.", {"Variance explained": ev, "Loadings": load, "Scores": scores},
                  new_dataset=Dataset(f"{ds.name} [PCA scores]", "column", scores.reset_index(drop=True)),
                  methods="sklearn.decomposition.PCA (SVD). Rows = observations, columns = variables.")


@checker("pca")
def _(ds, o):
    g = CK.groups_of(ds, o.get("groups") or None)
    out = CK.n_groups(g, 2, hint="PCA needs at least two variables.")
    if out:
        return out
    X = _complete(ds, list(g))
    if len(X) < 3:
        return [err("Fewer than 3 complete rows (rows with a missing value are dropped).")]
    dropped = len(ds.numeric(list(g)).dropna(how="all")) - len(X)
    if dropped:
        out.append(warn(f"{dropped} row(s) with missing values are excluded."))
    if len(X) < 3 * len(g):
        out.append(warn("Fewer than 3 observations per variable: components will be unstable."))
    if (X.std() == 0).any():
        out.append(err("A variable is constant."))
    return out


@register("cluster", "Cluster analysis (hierarchical / k-means)", "Multivariate", ["column"],
          [GROUPS, Option("method", "Method", "select", "hierarchical: ward", ["hierarchical: ward", "hierarchical: average", "hierarchical: complete", "hierarchical: single", "k-means"]),
           Option("k", "Number of clusters", "int", 2), Option("standardize", "Standardize variables", "bool", True)], prism="Hierarchical clustering / k-means")
def cluster(ds, groups=None, method="hierarchical: ward", k=2, standardize=True):
    from scipy.cluster import hierarchy as H
    from sklearn.cluster import KMeans
    names = groups or list(ds.group_arrays())
    X = _complete(ds, names)
    Z = ((X - X.mean()) / X.std(ddof=1)) if standardize else X
    curves = {}
    if method == "k-means":
        lab = KMeans(n_clusters=k, n_init=20, random_state=0).fit_predict(Z.to_numpy()) + 1
    else:
        L = H.linkage(Z.to_numpy(), method=method.split(": ")[1])
        lab = H.fcluster(L, k, criterion="maxclust")
        curves = {"linkage": L, "labels": [str(i + 1) for i in X.index]}
    out = pd.DataFrame({"Row": X.index + 1, "Cluster": lab}).set_index("Row")
    return Result("Cluster analysis", f"{k} clusters; sizes: {dict(pd.Series(lab).value_counts().sort_index())}.", {"Cluster assignments": out}, curves=curves,
                  methods="scipy.cluster.hierarchy.linkage/fcluster or sklearn.cluster.KMeans(n_init=20). Cluster number is chosen by you, not tested.")


@checker("cluster")
def _(ds, o):
    g = CK.groups_of(ds, o.get("groups") or None)
    out = CK.n_groups(g, 1)
    X = _complete(ds, list(g)) if g else None
    if X is not None:
        if not 2 <= (o.get("k") or 2) < len(X):
            out.append(err("Number of clusters must be at least 2 and less than the number of rows."))
        if o.get("method", "").endswith("ward") and not o.get("standardize", True) and len(g) > 1:
            out.append(info("Ward linkage on unstandardized variables is dominated by the largest-scale variable."))
    return out


# ------------------------------------------------------------------ agreement / diagnostics
@register("bland_altman", "Bland-Altman method comparison", "Agreement", ["column"],
          [Option("group_a", "Method A", "group", None), Option("group_b", "Method B", "group", None),
           Option("pct", "Difference as % of mean", "bool", False)], prism="Bland-Altman")
def bland_altman(ds, group_a=None, group_b=None, pct=False):
    from .column import two
    a, b, _, _ = two(ds, dict(group_a=group_a, group_b=group_b))
    m = ds.numeric([a, b]).dropna()
    mean = m.mean(axis=1).to_numpy(); diff = (m[a] - m[b]).to_numpy()
    if pct:
        diff = diff / mean * 100
    n = len(diff); bias, sd = diff.mean(), diff.std(ddof=1)
    tc = st.t.ppf(.975, n - 1)
    se_b = sd / np.sqrt(n); se_l = sd * np.sqrt(1 / n + 1.96 ** 2 / (2 * (n - 1)))
    rows = pd.DataFrame({"Estimate": [bias, bias - 1.96 * sd, bias + 1.96 * sd],
                         "95% CI low": [bias - tc * se_b, bias - 1.96 * sd - tc * se_l, bias + 1.96 * sd - tc * se_l],
                         "95% CI high": [bias + tc * se_b, bias - 1.96 * sd + tc * se_l, bias + 1.96 * sd + tc * se_l]},
                        index=["Bias (mean difference)", "Lower limit of agreement", "Upper limit of agreement"])
    pb = st.ttest_1samp(diff, 0).pvalue
    return Result("Bland-Altman", f"Bias {bias:.4g}, 95% limits of agreement {bias - 1.96 * sd:.4g} to {bias + 1.96 * sd:.4g}; bias != 0: {fmt_P(pb)}.",
                  {"Bland-Altman": rows, "Summary": pd.DataFrame({"Value": {"n": n, "SD of differences": sd, "P (bias != 0)": pb}})},
                  curves={"ba": {"mean": mean, "diff": diff, "bias": bias, "lo": bias - 1.96 * sd, "hi": bias + 1.96 * sd}},
                  methods="Bland & Altman limits (mean +/- 1.96 SD) with CIs from the t distribution; one-sample t test for bias.")


@checker("bland_altman")
def _(ds, o):
    from .column import two
    try:
        a, b, _, _ = two(ds, o)
    except Exception as e:
        return [err(str(e))]
    m = ds.numeric([a, b]).dropna()
    out = []
    if len(m) < 10:
        out.append(warn("Fewer than ~10 paired measurements: limits of agreement are imprecise."))
    if o.get("pct") and (m.mean(axis=1) == 0).any():
        out.append(err("A mean of zero makes percent difference undefined."))
    if not ds.paired:
        out.append(err("Methods must be measured on the same subjects/samples. Tick 'Rows are matched subjects' (Data tab) to confirm."))
    elif len(m) >= 8 and np.ptp((m[a] - m[b]).to_numpy()) > 0 and st.shapiro((m[a] - m[b]).to_numpy()).pvalue < .05:
        out.append(warn("Differences are not normal; limits of agreement are approximate. Consider log-transforming."))
    return out


@register("roc", "ROC curve", "Agreement", ["column"],
          [Option("group_a", "Controls (negatives)", "group", None), Option("group_b", "Patients (positives)", "group", None),
           Option("direction", "Positives have", "select", "higher values", ["higher values", "lower values"])], prism="ROC curve")
def roc(ds, group_a=None, group_b=None, direction="higher values"):
    from sklearn.metrics import roc_auc_score, roc_curve
    from .column import two
    a, b, x, y = two(ds, dict(group_a=group_a, group_b=group_b))
    score = np.r_[x, y]; truth = np.r_[np.zeros(len(x)), np.ones(len(y))]
    if direction == "lower values":
        score = -score
    auc = roc_auc_score(truth, score)
    n1, n2 = len(y), len(x)
    q1, q2 = auc / (2 - auc), 2 * auc ** 2 / (1 + auc)
    se = np.sqrt((auc * (1 - auc) + (n1 - 1) * (q1 - auc ** 2) + (n2 - 1) * (q2 - auc ** 2)) / (n1 * n2))
    p = st.mannwhitneyu(y, x, alternative="two-sided").pvalue
    fpr, tpr, thr = roc_curve(truth, score)
    thr = thr if direction == "higher values" else -thr
    tab = pd.DataFrame({"Threshold": thr, "Sensitivity": tpr, "Specificity": 1 - fpr, "Youden J": tpr - fpr})
    best = tab.loc[tab["Youden J"].idxmax()]
    return Result("ROC curve", f"AUC = {auc:.3f} (SE {se:.3f}), {fmt_P(p)} vs 0.5; best Youden threshold {best['Threshold']:.4g}.",
                  {"AUC": pd.DataFrame({"Value": {"AUC": auc, "SE (Hanley-McNeil)": se, "95% CI low": auc - 1.96 * se, "95% CI high": min(1, auc + 1.96 * se), "P (AUC=0.5; Mann-Whitney)": p}}),
                   "Thresholds": tab}, curves={"roc": {"fpr": fpr, "tpr": tpr}}, p=float(p),
                  methods="sklearn.metrics.roc_curve / roc_auc_score; Hanley-McNeil SE; P from scipy.stats.mannwhitneyu (equivalent to AUC=0.5).")


@checker("roc")
def _(ds, o):
    from .column import two
    try:
        a, b, x, y = two(ds, o)
    except Exception as e:
        return [err(str(e))]
    out = CK.min_n({a: x, b: y}, 3)
    if (len(x) < 10 or len(y) < 10):
        out.append(warn("Fewer than 10 per group: the ROC curve and AUC are very imprecise."))
    return out


# ------------------------------------------------------------------ GLMs
RESP = Option("response", "Response (Y) column", "group", None)
PRED = Option("predictors", "Predictor (X) columns", "groups", None)


def _glm_data(ds, response, predictors):
    names = [response] + list(predictors)
    return ds.numeric(names).dropna()


def _glm_check(ds, o, kind):
    r, p = o.get("response"), o.get("predictors")
    g = ds.group_arrays()
    if not r or r not in g:
        return [err("Choose the response (Y) column.")]
    if not p:
        return [err("Choose at least one predictor column.")]
    if r in p:
        return [err("The response cannot also be a predictor.")]
    d = _glm_data(ds, r, p)
    out = []
    n, k = len(d), len(p)
    if n < k + 3:
        return [err(f"{n} complete rows for {k} predictor(s): need at least {k + 3}.")]
    if len(ds.numeric([r] + list(p)).dropna(how="all")) > n:
        out.append(warn(f"{len(ds.numeric([r] + list(p)).dropna(how='all')) - n} row(s) with missing values are excluded."))
    if (d[list(p)].std() == 0).any():
        out.append(err("A predictor is constant."))
    y = d[r]
    if kind == "logistic":
        if not set(y.unique()) <= {0.0, 1.0} or y.nunique() < 2:
            return out + [err("Logistic regression needs a response coded exactly 0/1 with both values present.")]
        ev = min(y.sum(), n - y.sum())
        if ev < 10 * k:
            out.append(warn(f"Only {int(ev)} events in the smaller outcome class for {k} predictor(s) (<10 per predictor): the model may overfit."))
    elif kind == "poisson":
        if (y < 0).any() or not np.allclose(y, np.round(y)):
            return out + [err("Poisson regression needs a non-negative whole-number response (counts).")]
    else:
        if y.nunique() < 3:
            out.append(warn("The response has <3 distinct values; consider logistic regression."))
    if k > 1:
        X = sm.add_constant(d[list(p)])
        vif = [variance_inflation_factor(X.to_numpy(), i) for i in range(1, X.shape[1])]
        if max(vif) > 10:
            out.append(warn(f"Severe multicollinearity (VIF up to {max(vif):.1f}); coefficients are unstable."))
    return out


def _fit_logit(y, X):
    """Logit fit that refuses perfect / quasi-complete separation instead of returning meaningless huge coefficients."""
    import warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        try:
            m = sm.Logit(y, X).fit(disp=0, maxiter=100)
        except Exception as e:
            raise RuntimeError(f"Logistic fit failed ({e}); this often means perfect separation.") from e
    p = m.predict()
    sep = any("separation" in str(x.message).lower() for x in w) or not m.mle_retvals.get("converged", True) \
        or bool(np.all(np.where(np.asarray(y) == 1, p > 1 - 1e-6, p < 1e-6)))
    if sep:
        raise RuntimeError("Perfect or quasi-complete separation: a predictor (or combination) predicts the outcome exactly, so the maximum-likelihood "
                           "coefficients do not exist. Collect more data, drop/merge predictors, or use penalised (Firth) regression.")
    return m


def _glm_table(m, link_exp, ci):
    cf = m.conf_int(1 - ci / 100)
    t = pd.DataFrame({"Coefficient": m.params, "SE": m.bse, "CI low": cf[0], "CI high": cf[1], "P": m.pvalues})
    if link_exp:
        t[link_exp] = np.exp(m.params); t[link_exp + " CI low"] = np.exp(cf[0]); t[link_exp + " CI high"] = np.exp(cf[1])
    return t


@register("multiple_linear", "Multiple linear regression", "Regression", ["column"], [RESP, PRED, Option("ci", "Confidence level (%)", "float", 95.0)],
          prism="Multiple linear regression")
def mlr(ds, response=None, predictors=None, ci=95.0):
    d = _glm_data(ds, response, predictors)
    m = sm.OLS(d[response], sm.add_constant(d[list(predictors)])).fit()
    return Result("Multiple linear regression", f"R2 = {m.rsquared:.4f}, adjusted R2 = {m.rsquared_adj:.4f}; model F {fmt_P(m.f_pvalue)}.",
                  {"Coefficients": _glm_table(m, None, ci), "Fit": pd.DataFrame({"Value": {"n": int(m.nobs), "R squared": m.rsquared, "Adj. R squared": m.rsquared_adj,
                                                                                               "F": m.fvalue, "P": m.f_pvalue, "AIC": m.aic}})}, p=float(m.f_pvalue),
                  methods="statsmodels.api.OLS.")


checker("multiple_linear")(lambda ds, o: _glm_check(ds, o, "linear"))


@register("logistic", "Logistic regression", "Regression", ["column"], [RESP, PRED, Option("ci", "Confidence level (%)", "float", 95.0)],
          prism="Simple / multiple logistic regression")
def logistic(ds, response=None, predictors=None, ci=95.0):
    d = _glm_data(ds, response, predictors)
    m = _fit_logit(d[response], sm.add_constant(d[list(predictors)]))
    from sklearn.metrics import roc_auc_score
    auc = roc_auc_score(d[response], m.predict())
    return Result("Logistic regression", f"McFadden pseudo-R2 = {m.prsquared:.3f}; likelihood-ratio {fmt_P(m.llr_pvalue)}; AUC = {auc:.3f}.",
                  {"Coefficients": _glm_table(m, "Odds ratio", ci), "Fit": pd.DataFrame({"Value": {"n": int(m.nobs), "Pseudo R2": m.prsquared, "LR chi-square": m.llr, "LR P": m.llr_pvalue, "AIC": m.aic, "AUC": auc}})},
                  p=float(m.llr_pvalue), methods="statsmodels.api.Logit (maximum likelihood); sklearn roc_auc_score.")


@checker("logistic")
def _(ds, o):
    out = _glm_check(ds, o, "logistic")
    if not any(i.level == "error" for i in out):
        d = _glm_data(ds, o["response"], o["predictors"])
        try:
            _fit_logit(d[o["response"]], sm.add_constant(d[list(o["predictors"])]))
        except Exception as e:
            out.append(err(f"The model cannot be fitted: {e}"))
    return out


@register("poisson", "Poisson regression", "Regression", ["column"], [RESP, PRED, Option("ci", "Confidence level (%)", "float", 95.0)], prism="Poisson regression")
def poisson(ds, response=None, predictors=None, ci=95.0):
    d = _glm_data(ds, response, predictors)
    m = sm.GLM(d[response], sm.add_constant(d[list(predictors)]), family=sm.families.Poisson()).fit()
    disp = m.pearson_chi2 / m.df_resid
    w = [f"Overdispersion (Pearson chi2/df = {disp:.2f}): standard errors are too small; consider negative-binomial regression."] if disp > 1.5 else []
    return Result("Poisson regression", f"Deviance {m.deviance:.3f} on {int(m.df_resid)} df; dispersion {disp:.2f}.", {"Coefficients": _glm_table(m, "Rate ratio", ci)},
                  warnings=w, methods="statsmodels.api.GLM (Poisson, log link).")


checker("poisson")(lambda ds, o: _glm_check(ds, o, "poisson"))
