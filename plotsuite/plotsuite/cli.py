"""Headless pipeline:  plotsuite-plot data.csv --preset "Xenocortical Nature style" --test oneway_anova --opt posthoc=tukey -o fig.pdf

Same engine, guardrails and presets as the GUI. Exit codes: 0 ok, 2 blocked by a guardrail / bad arguments, 3 graph could not be drawn.
"""
from __future__ import annotations

import argparse
import dataclasses as dc
import json
import sys

from . import plots, stats as S
from .data import KINDS, Dataset, read_table
from .project import AnalysisSpec, GraphSpec, Project
from .style import PRESETS, Style, field_meta


def _coerce(name: str, raw: str):
    meta = dict(field_meta())[name]
    w = meta["widget"]
    if w == "bool":
        return raw.lower() in ("1", "true", "yes", "on")
    if w == "int":
        return int(float(raw))
    if w in ("float",):
        return float(raw)
    if w == "opt_float":
        return None if raw.lower() in ("", "none", "auto") else float(raw)
    return raw


def _kv(items, what):
    out = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"{what} must look like key=value, got '{it}'")
        k, v = it.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _opt_value(spec, k, v):
    o = next((o for o in spec.options if o.name == k), None)
    if o is None:
        raise SystemExit(f"test '{spec.key}' has no option '{k}'. Options: {', '.join(x.name for x in spec.options)}")
    if o.kind == "float":
        return float(v)
    if o.kind == "int":
        return int(float(v))
    if o.kind == "bool":
        return v.lower() in ("1", "true", "yes", "on")
    if o.kind in ("groups", "series_multi", "columns"):
        return [x for x in v.split(",") if x]
    return v


def build_parser():
    ap = argparse.ArgumentParser(prog="plotsuite-plot", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data", nargs="?", help="CSV/TSV/XLSX file (first row = column names)")
    ap.add_argument("-o", "--out", help="figure file: .pdf .svg .png .tiff")
    ap.add_argument("--kind", default="column", choices=KINDS, help="table layout (default: column)")
    ap.add_argument("--graph", default=None, help="graph type: column|grouped|xy|survival|histogram|heatmap|stacked|bland_altman|roc (default from layout)")
    ap.add_argument("--roles", nargs="*", help="role=column pairs, e.g. value=Weight a=Genotype b=Time subject=Mouse | x=Dose ys=Resp | time=Days event=Died group=Arm")
    ap.add_argument("--paired", action="store_true", help="column table rows are matched subjects")
    ap.add_argument("--groups", help="comma-separated columns to plot, in order")
    ap.add_argument("--preset", default="Xenocortical Nature style", help=f"one of: {', '.join(PRESETS)} (or a saved preset in --project)")
    ap.add_argument("--set", nargs="*", metavar="FIELD=VALUE", help="override any Style field, e.g. y_label='Cells per mm2' y_min=0 error=SD")
    ap.add_argument("--test", help="stat test key to run and draw brackets/fit from (see --list-tests)")
    ap.add_argument("--opt", nargs="*", metavar="K=V", help="test options, e.g. posthoc=tukey group_a=Vehicle group_b=Drug")
    ap.add_argument("--dpi", type=int, help="export dpi (default: style dpi)")
    ap.add_argument("--results", help="write the statistics tables to this .xlsx")
    ap.add_argument("--save-project", help="also write a .json project the GUI can open")
    ap.add_argument("--list-tests", action="store_true", help="list tests valid for this table with status, then exit")
    ap.add_argument("--list-fields", action="store_true", help="list every --set field and its default, then exit")
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    if a.list_fields:
        d = Style()
        for n, m in field_meta():
            print(f"{n:22s} default={getattr(d, n)!r:22} [{m['group']}] {m['label']}" + (f"  choices={m['choices']}" if m["choices"] else ""))
        return 0
    if not a.data:
        print("error: data file required", file=sys.stderr); return 2
    try:
        with open(a.data, "rb") as fh:
            df = read_table(fh, a.data)
    except OSError as e:
        print(f"error: cannot read {a.data}: {e}", file=sys.stderr); return 2
    roles = {}
    for k, v in _kv(a.roles, "--roles").items():
        roles[k] = [x for x in v.split(",") if x] if k == "ys" else v
    ds = Dataset("data", a.kind, df, roles, paired=a.paired)
    if a.kind == "xy" and "ys" not in roles and "x" in roles:
        roles["ys"] = [c for c in df.columns if c != roles["x"]]
    P = Project("cli")
    P.datasets["data"] = ds
    if a.preset in PRESETS:
        P.theme = Style.from_dict({**Style().to_dict(), **PRESETS[a.preset]})
    else:
        print(f"error: unknown preset '{a.preset}'. Choose from: {', '.join(PRESETS)}", file=sys.stderr); return 2
    try:
        ov = {k: _coerce(k, v) for k, v in _kv(a.set, "--set").items()}
        bad = [k for k in ov if k not in {n for n, _ in field_meta()}]
        if bad:
            raise KeyError(f"unknown style field(s): {', '.join(bad)} (see --list-fields)")
    except (KeyError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr); return 2
    if a.list_tests:
        for r in S.listing(ds):
            why = "; ".join(i.message for i in r["issues"] if i.level == "error")[:110]
            print(f"{r['status']:8s} {r['key']:24s} {r['name']}" + (f"   <- {why}" if why else ""))
        return 0
    if not a.out:
        print("error: -o/--out required", file=sys.stderr); return 2
    gkind = a.graph or {"column": "column", "grouped": "grouped", "xy": "xy", "survival": "survival", "contingency": "stacked"}[a.kind]
    params = {"groups": a.groups.split(",")} if a.groups else {}
    g = GraphSpec("graph", "data", gkind, params, ov)
    P.graphs.append(g)
    if a.test:
        if a.test not in S.REGISTRY:
            print(f"error: unknown test '{a.test}'", file=sys.stderr); return 2
        spec = S.REGISTRY[a.test]
        try:
            opts = {k: _opt_value(spec, k, v) for k, v in _kv(a.opt, "--opt").items()}
        except (ValueError, SystemExit) as e:
            print(f"error: {e}", file=sys.stderr); return 2
        if a.groups and "groups" in {o.name for o in spec.options} and "groups" not in opts:
            opts["groups"] = a.groups.split(",")
        try:
            res = S.run(a.test, ds, opts)
        except S.BlockedError as e:
            print("BLOCKED by guardrails:", file=sys.stderr)
            for i in e.issues:
                print(f"  - {i.message}", file=sys.stderr)
            return 2
        an = AnalysisSpec(spec.name, "data", a.test, opts); P.analyses.append(an); g.analysis_ids.append(an.id)
        print(res.text())
        if a.results:
            import pandas as pd
            with pd.ExcelWriter(a.results, engine="openpyxl") as xw:
                pd.DataFrame({"Test": [res.test], "Summary": [res.summary], "Methods": [res.methods]}).to_excel(xw, sheet_name="Summary", index=False)
                for i, (n, t) in enumerate(res.tables.items()):
                    t.to_excel(xw, sheet_name=f"{i + 1} {n}"[:31].replace("/", "-").replace(":", ""))
    comps, results = plots.analyses_for(P, g)
    st = P.effective_style(g)
    fig = plots.render(g, ds, st, P.gs(st), comps, results)
    if getattr(fig, "_ps_error", None):
        print(f"error: cannot draw graph: {fig._ps_error}", file=sys.stderr); return 3
    if getattr(fig, "_ps_clipped", None):
        print(f"warning: {fig._ps_clipped}", file=sys.stderr)
    fmt = a.out.rsplit(".", 1)[-1].lower()
    with open(a.out, "wb") as f:
        f.write(plots.export(fig, fmt, a.dpi or st.dpi, st.background == "transparent"))
    w, h = fig.get_size_inches()
    print(f"wrote {a.out}  ({w:.2f} x {h:.2f} in, {a.dpi or st.dpi} dpi, font {plots.font_family(st.font_family)})")
    if a.save_project:
        open(a.save_project, "w").write(P.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
