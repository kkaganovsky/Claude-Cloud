# PlotSuite

An open-source, GUI-driven graphing and statistics suite modelled on GraphPad Prism's workflow — built entirely from standard
Python packages (matplotlib, scipy, statsmodels, pingouin, scikit-posthocs, scikit-learn) with a Streamlit interface.

```bash
cd plotsuite
pip install -e ".[test]"        # numpy pandas scipy statsmodels pingouin scikit-posthocs scikit-learn matplotlib streamlit openpyxl
plotsuite                        # opens the GUI in your browser   (same as: streamlit run plotsuite/app.py)
pytest                           # 78 tests, ~1.5 min
```

Click **Load example** in the sidebar to get one table of every layout, saved analyses, and graphs of every type.

## Workflow (five tabs)

| Tab | What it does |
|---|---|
| **① Data** | Paste from Excel/Prism, upload CSV/XLSX, or type into an editable grid. Five layouts like Prism: *Column*, *Grouped/long* (factor roles), *XY*, *Contingency*, *Survival*. "Rows are matched subjects" flag for paired designs. |
| **② Analyze** | A **test advisor** proposes a test from your data (layout, #groups, pairing, normality, variance). A menu lists every test valid for the table, marked ✅ ready / ⚠️ runs with warnings / 🔧 needs your choices / ⛔ not valid. Controls are generated from each test's option schema. Results: summary sentence, tables, warnings, exact methods + package versions, Excel export. Save analyses into the project. |
| **③ Graph** | Column (bar / box / violin / points), grouped, XY with fitted curves + confidence bands, Kaplan-Meier, histogram, heat map, stacked bars, Bland-Altman, ROC. ~80 formatting settings in a generated panel (fonts, axes, ticks, scales, limits, colours, markers, hatches, error bars, jitter/swarm, legends, …). Significance brackets and fitted curves are drawn from saved analyses, so they update when the data change. Export PNG/PDF/SVG/TIFF (text stays editable). |
| **④ Consistency** | Prism's "consistency" idea, made explicit — see below. |
| **⑤ Multi-panel layout** | Compose several graphs into one figure with panel letters A, B, C … |

## Consistency (keeping figures uniform)

* **Project theme** — one shared `Style`. Each graph stores only its *overrides*. In the Graph tab pick **"all graphs (project theme)"** and any change restyles every graph (graph-specific overrides of that field are removed); pick **"this graph only"** for a one-off.
* **Group styles** — colour / marker / hatch are tied to a group *name*, so "Vehicle" is the same colour in every graph and every table. Colours follow the palette automatically until you pick one explicitly.
* **Make this graph the project look**, **copy format** between graphs, **match axes** (y-limits, scale, ticks, size), **reset to theme**.
* **Named formats** (built-ins: single column, wide, presentation, poster, grayscale) saved in the project file.

## Guardrails ("it won't let you do something dumb")

Every test has a validator that runs *before* the Run button is enabled. **Errors block** (with the reason and the fix); **warnings run** and are copied into the result.

| Blocks | Warns |
|---|---|
| t test / Mann-Whitney on ≠2 groups (points you to ANOVA) | non-normal data for parametric tests (Shapiro-Wilk) |
| paired / RM tests on tables not flagged as matched, or with missing subjects | unequal variances for equal-variance tests (Brown-Forsythe) |
| non-numeric cells (never silently dropped) | unpaired test on matched data |
| n too small, constant X, too few points for a model, df ≤ 0 | zero-variance groups, ties, tiny n |
| Dunnett without a control, trend test with <3 groups | χ² with expected counts <5 (suggests Fisher) |
| log/sqrt/logit of out-of-domain values, P-values outside 0–1 | multicollinearity (VIF>10), low events-per-variable |
| non-integer counts in contingency tests, 2×2-only tests on r×c | many covariates for few events (Cox) |
| perfect separation in logistic regression | residuals non-normal / runs test on regression |
| survival event not 0/1, negative times | overdispersion in Poisson regression |
| two-way designs with empty cells, or no replicates + interaction | iterative Grubbs, one-tailed tests |
| nonlinear: unknown/contradictory constraints, X<0 for concentration models, bad weights, unsafe equations | |

## Statistics included

*Descriptive:* column statistics, normality battery (Shapiro-Wilk, D'Agostino-Pearson, Anderson-Darling, Lilliefors), variance tests (Brown-Forsythe, Bartlett, F).
*t / rank tests:* one-sample t, one-sample Wilcoxon, unpaired t, Welch, paired t, ratio-paired t, Mann-Whitney, Wilcoxon matched-pairs, Kolmogorov-Smirnov, multiple t tests per row (FDR / Holm-Šídák / Bonferroni).
*ANOVA:* one-way ordinary / Welch / Brown-Forsythe / Kruskal-Wallis / repeated-measures / Friedman / mixed-effects; two-way ordinary and RM/mixed; three-way. Post-tests: Tukey, Dunnett, Bonferroni, Šídák, Holm-Šídák, Fisher LSD, Student-Newman-Keuls, linear trend, Games-Howell, Tamhane T2, Dunn (Kruskal & Friedman); simple-effects for two-way designs.
*Regression/correlation:* linear (with runs test), compare lines, Deming, nonlinear (23 built-in equations + safe custom equations, constraints, weights, interpolation, confidence band, AICc), compare models (F test / AICc), multiple linear, logistic, Poisson, Pearson/Spearman/Kendall, correlation matrix.
*Survival:* Kaplan-Meier with log-rank, Gehan-Breslow-Wilcoxon, Tarone-Ware, trend test, hazard ratio; Cox PH.
*Contingency:* Fisher, χ², χ² trend, McNemar, relative risk / odds ratio / sensitivity-specificity, χ² goodness of fit, binomial.
*Other:* Grubbs & ROUT outliers, P-value stack correction, Bland-Altman, ROC, PCA, hierarchical & k-means clustering, transform, normalize, fraction of total, frequency distribution, area under curve, row statistics.

## What is validated, and where it differs from Prism

`tests/test_stats_validation.py` checks results against independent references (hand formulas, `statsmodels`, `scipy`):
t tests, one-way ANOVA, Tukey vs `pairwise_tukeyhsd`, Bonferroni/Šídák/Holm-Šídák, SNK ordering properties, trend contrast, Friedman+Dunn, RM-ANOVA decomposition,
two-way Type III vs `anova_lm`, simple effects, log-rank/Gehan/Tarone-Ware vs `statsmodels.survdiff`, Grubbs critical value (tabulated), Deming = orthogonal regression,
nonlinear fits vs `scipy.curve_fit` and exact parameter recovery, AICc/F-test formulas, Fisher/χ²/RR/OR, Cochran-Armitage trend, Bland-Altman, ROC AUC = Mann-Whitney U, FDR, PCA, logistic.
`tests/test_smoke_all.py` runs every test with every option choice.

**Known differences / limits — please read before comparing numbers with Prism:**
* **Not validated against GraphPad Prism itself** (no Prism available here). Matching Prism to the last digit is *not* guaranteed.
* Post-tests after RM ANOVA are paired t tests with correction (Prism uses the ANOVA error term).
* Mixed-effects model: random-intercept REML with asymptotic Wald tests (Prism uses a different small-sample approach).
* Dunnett's T3 is not offered (use Games-Howell / Tamhane T2).
* **ROUT** is experimental: it follows the published outline but the exact merit function / FDR step-up were not checked against Prism. Prefer Grubbs.
* Anderson-Darling P values come from scipy's interpolation tables (clipped to the range 0.01-0.15, so "P = 0.15" means "P > 0.15").
* Chi-square default is *without* Yates' correction (tick the box for 2×2 tables if you want it).
* Implemented in-house (and tested as above): SNK, trend post-test, Dunn after Friedman, log-rank family, Grubbs, Deming (jackknife SE), ROUT.
* Not implemented: Dunnett T3, nested/ANCOVA tests beyond line comparison, multiple logistic with interactions, Kaplan-Meier at-risk table, replicates-test lack-of-fit, axis breaks.

## Layout

```
plotsuite/
  data.py        Dataset (5 layouts) + import helpers
  style.py       Style dataclass (all graph settings, GUI metadata), palettes, presets
  project.py     Project = datasets + analyses + graphs + theme; consistency operations; JSON save/load
  plots.py       matplotlib drawers, significance brackets, export, multi-panel layout
  stats/         registry + guardrails (core, checks) and tests (column, grouped, xy, survival, contingency, misc), advisor
  app.py         Streamlit GUI
tests/           78 tests (stats validation, guardrails, plots/consistency, smoke-all, headless GUI)
```

Adding a test = one function with `@register(key, name, family, layouts, options)` and a `@checker(key)` that returns `err()/warn()/info()` issues; the menu, option widgets, guardrail UI, results display and Excel export come for free.
