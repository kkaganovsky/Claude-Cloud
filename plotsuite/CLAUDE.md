# PlotSuite - guide for an agent using this package

Open-source Prism-style graphing + statistics (matplotlib, scipy, statsmodels, pingouin, scikit-posthocs, scikit-learn). Install: `pip install -e .` from this folder.

## Plot data (headless)
```bash
plotsuite-plot data.csv -o fig.pdf --preset "Xenocortical Nature style" --groups Control,Treated \
    --test unpaired_t --opt group_a=Control group_b=Treated --set y_label="Cells per mm2" y_min=0 --results stats.xlsx
```
* `--kind column|grouped|xy|survival|contingency` (+ `--roles ...`, `--paired`) chooses the table layout. Column tables: one column per group.
* `--list-tests` shows which tests are valid for the table (ok / warn / blocked + reason). `--list-fields` lists every `--set` style field.
* `--test KEY --opt k=v` runs a stats test; brackets/stars and fit curves are drawn automatically. Output is text + optional xlsx.

## Rules the guardrails enforce (do not work around them)
* Exit code 2 + `BLOCKED by guardrails` means the analysis is invalid for this data (wrong group count, unmatched pairs, non-numeric cells, bad counts...).
  Report the reason to the user; do not switch to a different test just to make it run. Warnings are printed and must be reported with the result.
* Never edit numbers to make a test pass. Paired tests need `--paired` only when each row is truly the same subject.

## Python API
```python
from plotsuite import stats as S, plots
from plotsuite.data import Dataset, read_table
from plotsuite.project import Project, GraphSpec, AnalysisSpec
from plotsuite.style import PRESETS, Style
res = S.run("oneway_anova", ds, {"posthoc": "tukey"})   # raises S.BlockedError with .issues if invalid
fig = plots.render(graph, ds, style, project.gs(style), res.comparisons, [res]); open("f.pdf","wb").write(plots.export(fig, "pdf", 600))
```

## House style ("Xenocortical Nature style" preset)
Helvetica (Nimbus Sans clone bundled in `plotsuite/fonts`; set `PLOTSUITE_FONTS` for real Helvetica), 6 pt numbers / 7 pt other text, #CCCCCC bars, #333333 s.e.m.
error bars, short thin ticks, tight tick/label/legend spacing, rotated labels end on their tick with a 1.5 pt gap, **height always 1.3 in, width from the number of bars**.
Bar width, point size and error-bar weight were estimated by eye from the Nature figure. If a warning says content is cut off, increase `margin_*` or shorten labels - do not resize the figure.

## Limits to remember
Not validated against GraphPad Prism itself. See README "What is validated, and where it differs from Prism" before comparing numbers.
Run `pytest` (82 tests, ~1.5 min) after any change.
