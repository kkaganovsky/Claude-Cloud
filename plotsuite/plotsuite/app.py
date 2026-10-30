"""PlotSuite GUI (Streamlit). Run with:  plotsuite   or   streamlit run plotsuite/app.py"""
from __future__ import annotations

import dataclasses as dc
import io
import re

import numpy as np
import pandas as pd
import streamlit as st

from plotsuite import plots, stats as S
from plotsuite.data import KIND_LABELS, KINDS, Dataset, read_table
from plotsuite.examples import example_project
from plotsuite.project import GRAPH_KINDS, AnalysisSpec, GraphSpec, Project
from plotsuite.stats.advisor import recommend
from plotsuite.style import HATCHES, MARKERS, PALETTES, PRESETS, Style, diff_from, field_meta

st.set_page_config(page_title="PlotSuite", page_icon="📈", layout="wide")

SETUP_WORDS = re.compile(r"choose|pick|assign|select|Tick|tick", re.I)


# ------------------------------------------------------------------ state
def init():
    ss = st.session_state
    ss.setdefault("project", Project())
    ss.setdefault("ver", 0)  # bumps whenever the theme changes -> format widgets re-initialise
    ss.setdefault("ds_ver", {})
    ss.setdefault("last", None)
    ss.setdefault("png", {})
    for k, v in ss.pop("_pending", {}).items():  # widget keys may only be set before their widget exists
        ss[k] = v
    names = list(ss.project.datasets)
    if names and ss.get("cur_ds") not in names:
        ss["cur_ds"] = names[0]


def set_later(**kw):
    st.session_state.setdefault("_pending", {}).update(kw)


def P() -> Project:
    return st.session_state.project


def bump():
    st.session_state.ver += 1


def current_ds() -> Dataset | None:
    p = P()
    return p.datasets.get(st.session_state.get("cur_ds"))


# ------------------------------------------------------------------ sidebar
def sidebar():
    p = P()
    with st.sidebar:
        st.title("📈 PlotSuite")
        p.name = st.text_input("Project name", p.name)
        c1, c2 = st.columns(2)
        if c1.button("Load example", help="Loads example tables, analyses and graphs of every type", width="stretch"):
            st.session_state.project = example_project(); st.session_state.last = None; bump(); st.rerun()
        if c2.button("New project", width="stretch"):
            st.session_state.project = Project(); st.session_state.last = None; bump(); st.rerun()
        up = st.file_uploader("Open project (.json)", type=["json"], key="open_proj")
        if up is not None and st.session_state.get("opened") != up.file_id:
            try:
                st.session_state.project = Project.from_json(up.read().decode()); st.session_state["opened"] = up.file_id; bump(); st.rerun()
            except Exception as e:
                st.error(f"Could not open: {e}")
        st.download_button("💾 Save project", P().to_json(), file_name=f"{re.sub(r'[^A-Za-z0-9_-]+', '_', P().name)}.plotsuite.json", mime="application/json", width="stretch")
        st.divider()
        names = list(P().datasets)
        if names:
            st.selectbox("Current data table", names, key="cur_ds")
        st.caption("Stats come from scipy, statsmodels, pingouin, scikit-posthocs and scikit-learn; "
                   "every result lists the exact function used. See the *About* tab for validated vs. in-house methods.")


# ------------------------------------------------------------------ data tab
def tab_data():
    p = P()
    with st.expander("➕ Add a data table", expanded=not p.datasets):
        c1, c2, c3 = st.columns([2, 2, 3])
        name = c1.text_input("Name", f"Table {len(p.datasets) + 1}", key="new_name")
        kind = c2.selectbox("Layout", KINDS, format_func=lambda k: KIND_LABELS[k], key="new_kind")
        src = c3.radio("Start from", ["Empty grid", "Paste", "Upload CSV/XLSX"], horizontal=True, key="new_src")
        df = None
        if src == "Paste":
            txt = st.text_area("Paste cells copied from Excel/Prism (first row = column names)", height=140, key="paste_txt")
            if txt.strip():
                df = read_table(txt)
        elif src == "Upload CSV/XLSX":
            f = st.file_uploader("File", type=["csv", "tsv", "txt", "xlsx"], key="new_file")
            if f is not None:
                df = read_table(f, f.name)
        else:
            tmpl = {"column": ["Group 1", "Group 2", "Group 3"], "grouped": ["Factor A", "Factor B", "Value", "Subject"], "xy": ["X", "Y1"],
                    "contingency": ["Row", "Outcome 1", "Outcome 2"], "survival": ["Time", "Event", "Group"]}[kind]
            df = pd.DataFrame({c: pd.Series([np.nan] * 8, dtype=float) if kind != "grouped" or c == "Value" else pd.Series([""] * 8) for c in tmpl})
            if kind == "contingency":
                df["Row"] = ["Row 1", "Row 2"] + [""] * 6
        if st.button("Create table", disabled=df is None or not name.strip() or name in p.datasets):
            ds = Dataset(name.strip(), kind, df)
            guess_roles(ds)
            p.datasets[ds.name] = ds; set_later(cur_ds=ds.name); st.rerun()
        if name in p.datasets:
            st.caption("A table with this name already exists.")
    ds = current_ds()
    if ds is None:
        st.info("Add a table, or click **Load example** in the sidebar.")
        return
    st.subheader(f"{ds.name}  ·  {KIND_LABELS[ds.kind]}")
    roles_ui(ds)
    ver = st.session_state.ds_ver.get(ds.name, 0)
    edited = st.data_editor(ds.df, num_rows="dynamic", key=f"ed:{ds.name}:{ver}", width="stretch", height=min(500, 60 + 35 * (len(ds.df) + 1)))
    ds.df = edited.reset_index(drop=True)
    with st.expander("Columns: add / rename / delete / delete table"):
        c1, c2, c3 = st.columns(3)
        nc = c1.text_input("New column name", key=f"nc:{ds.name}")
        if c1.button("Add column", disabled=not nc.strip() or nc in ds.df.columns):
            ds.df[nc] = np.nan; st.session_state.ds_ver[ds.name] = ver + 1; st.rerun()
        old = c2.selectbox("Rename column", list(ds.df.columns), key=f"rn_old:{ds.name}")
        new = c2.text_input("to", key=f"rn_new:{ds.name}")
        if c2.button("Rename", disabled=not new.strip() or new in ds.df.columns):
            ds.df = ds.df.rename(columns={old: new})
            ds.roles = {k: (new if v == old else [new if x == old else x for x in v] if isinstance(v, list) else v) for k, v in ds.roles.items()}
            st.session_state.ds_ver[ds.name] = ver + 1; st.rerun()
        dl = c3.selectbox("Delete column", list(ds.df.columns), key=f"dl:{ds.name}")
        if c3.button("Delete column", disabled=len(ds.df.columns) < 2):
            ds.df = ds.df.drop(columns=[dl]); st.session_state.ds_ver[ds.name] = ver + 1; st.rerun()
        st.divider()
        if st.button(f"🗑 Delete table '{ds.name}'", type="secondary"):
            for g in list(p.graphs):
                if g.dataset == ds.name:
                    p.graphs.remove(g)
            p.analyses = [a for a in p.analyses if a.dataset != ds.name]
            del p.datasets[ds.name]; st.rerun()
    issues = S.core.generic_checks(ds)
    for i in issues:
        (st.error if i.level == "error" else st.warning)(i.message)
    if ds.kind == "column":
        m = ds.numeric()
        st.caption(f"{m.shape[1]} columns · {int(m.notna().sum().sum())} numeric values · {int(m.isna().sum().sum())} empty cells")


def guess_roles(ds: Dataset):
    cols = [str(c) for c in ds.df.columns]
    if ds.kind == "grouped":
        ds.roles = {"a": cols[0], "b": cols[1] if len(cols) > 1 else None, "value": cols[2] if len(cols) > 2 else cols[-1], "subject": cols[3] if len(cols) > 3 else None}
    elif ds.kind == "xy":
        ds.roles = {"x": cols[0], "ys": cols[1:]}
    elif ds.kind == "survival":
        ds.roles = {"time": cols[0], "event": cols[1] if len(cols) > 1 else cols[0], "group": cols[2] if len(cols) > 2 else None}


def roles_ui(ds: Dataset):
    cols = [str(c) for c in ds.df.columns]
    none = ["(none)"] + cols

    def pick(label, role, optional=False, key=None):
        opts = none if optional else cols
        cur = ds.roles.get(role)
        idx = opts.index(cur) if cur in opts else 0
        v = st.selectbox(label, opts, index=idx, key=f"role:{ds.name}:{role}")
        ds.roles[role] = None if v == "(none)" else v
    if ds.kind == "column":
        ds.paired = st.checkbox("Rows are matched subjects (paired / repeated measures: one row = one subject measured in every column)", value=ds.paired, key=f"paired:{ds.name}")
    elif ds.kind == "grouped":
        c = st.columns(5)
        with c[0]: pick("Value (outcome)", "value")
        with c[1]: pick("Factor A", "a")
        with c[2]: pick("Factor B", "b", True)
        with c[3]: pick("Factor C (3-way)", "c", True)
        with c[4]: pick("Subject ID (repeated measures)", "subject", True)
    elif ds.kind == "xy":
        c = st.columns(2)
        with c[0]: pick("X column", "x")
        with c[1]:
            ys = st.multiselect("Y series", [x for x in cols if x != ds.roles.get("x")], default=[y for y in ds.roles.get("ys", []) if y in cols and y != ds.roles.get("x")], key=f"ys:{ds.name}")
            ds.roles["ys"] = ys
    elif ds.kind == "survival":
        c = st.columns(3)
        with c[0]: pick("Time", "time")
        with c[1]: pick("Event (1 = event, 0 = censored)", "event")
        with c[2]: pick("Group", "group", True)
    elif ds.kind == "contingency":
        st.caption("First column = row labels; the other columns = whole-number counts.")


# ------------------------------------------------------------------ analyze tab
def option_controls(spec, ds: Dataset, key: str) -> dict:
    vals = {}
    groups = ds.group_names() if ds.kind == "column" else []
    ys = list(ds.xy_series()) if ds.kind == "xy" else []
    cols = [str(c) for c in ds.df.columns]
    for o in spec.options:
        k = f"{key}:{o.name}"
        h = o.help or None
        if o.kind == "select":
            ch = list(o.choices)
            vals[o.name] = st.selectbox(o.label, ch, index=ch.index(o.default) if o.default in ch else 0, key=k, help=h)
        elif o.kind == "float":
            vals[o.name] = st.number_input(o.label, value=float(o.default), format="%g", key=k, help=h)
        elif o.kind == "int":
            vals[o.name] = st.number_input(o.label, value=int(o.default), step=1, key=k, help=h)
        elif o.kind == "bool":
            vals[o.name] = st.checkbox(o.label, value=bool(o.default), key=k, help=h)
        elif o.kind == "text":
            vals[o.name] = st.text_input(o.label, value=o.default or "", key=k, help=h)
        elif o.kind == "group":
            v = st.selectbox(o.label, ["(auto)"] + groups, key=k, help=h)
            vals[o.name] = None if v == "(auto)" else v
        elif o.kind == "groups":
            sel = st.multiselect(o.label, groups, default=groups, key=k, help=h)
            vals[o.name] = None if set(sel) == set(groups) else sel
        elif o.kind == "series":
            v = st.selectbox(o.label, ["(auto)"] + ys, key=k, help=h)
            vals[o.name] = None if v == "(auto)" else v
        elif o.kind == "series_multi":
            sel = st.multiselect(o.label, ys, default=ys, key=k, help=h)
            vals[o.name] = sel or None
        elif o.kind == "columns":
            vals[o.name] = st.multiselect(o.label, cols, key=k, help=h) or None
    return vals


def show_result(res: S.Result, key: str):
    st.success(res.summary or res.test)
    for w in res.warnings:
        st.warning(w)
    for name, t in res.tables.items():
        st.markdown(f"**{name}**")
        st.dataframe(t, width="stretch")
    st.caption("Methods: " + res.methods)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame({"Test": [res.test], "Summary": [res.summary], "Methods": [res.methods]}).to_excel(xw, sheet_name="Summary", index=False)
        for i, (name, t) in enumerate(res.tables.items()):
            t.to_excel(xw, sheet_name=re.sub(r"[\[\]:*?/\\]", "", f"{i + 1} {name}")[:31])
    st.download_button("⬇ Results (.xlsx)", buf.getvalue(), file_name=f"{re.sub(r'\\W+', '_', res.test)}.xlsx", key=f"dlx:{key}")


def tab_analyze():
    p = P()
    ds = current_ds()
    if ds is None:
        st.info("Add or load a data table first."); return
    st.subheader(f"Analyze: {ds.name}")
    recs = recommend(ds)
    with st.expander("🧭 Which test should I use? (suggestions from your data)", expanded=True):
        for r in recs:
            spec = S.REGISTRY[r["key"]]
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"{'**' if r['main'] else ''}{spec.name}{'**' if r['main'] else ''} — {r['why']}" + (f"  \n:red[blocked: {r['issues'][0]}]" if r["blocked"] else ""))
            c2.button("Use", key=f"use:{ds.name}:{r['key']}", on_click=use_rec, args=(ds.name, r), disabled=r["blocked"])
    rows = S.listing(ds)
    fam = sorted({r["family"] for r in rows})
    c1, c2 = st.columns(2)
    family = c1.selectbox("Analysis family", fam, key="an_family")
    cand = [r for r in rows if r["family"] == family]

    def label(r):
        errs = [i for i in r["issues"] if i.level == "error"]
        icon = "✅" if r["status"] == "ok" else "⚠️" if r["status"] == "warn" else ("🔧" if all(SETUP_WORDS.search(i.message) for i in errs) else "⛔")
        return f"{icon} {r['name']}"
    keys = [r["key"] for r in cand]
    if st.session_state.get("an_test") not in keys:
        st.session_state["an_test"] = keys[0]
    key = c2.selectbox("Test", keys, format_func=lambda k: label(next(r for r in cand if r["key"] == k)), key="an_test")
    spec = S.REGISTRY[key]
    st.caption(f"✅ ready  ·  ⚠️ runs with warnings  ·  🔧 needs your choices  ·  ⛔ not valid for this table.   Prism equivalent: *{spec.prism}*. {spec.description}")
    opts = option_controls(spec, ds, f"opt:{ds.name}:{key}")
    issues = S.evaluate(key, ds, opts)
    errs = [i for i in issues if i.level == "error"]
    for i in issues:
        {"error": st.error, "warning": st.warning, "info": st.info}[i.level](("⛔ " if i.level == "error" else "") + i.message)
    if st.button("▶ Run analysis", type="primary", disabled=bool(errs)):
        try:
            st.session_state.last = dict(res=S.run(key, ds, opts), ds=ds.name, key=key, opts=opts)
        except S.BlockedError as e:
            st.error(str(e))
        except Exception as e:
            st.error(f"The calculation failed: {e}")
            st.session_state.last = None
    last = st.session_state.last
    if last and last["ds"] == ds.name and last["key"] == key:
        st.divider(); show_result(last["res"], key)
        c1, c2, c3 = st.columns([3, 1, 2])
        nm = c1.text_input("Save as", f"{spec.name}", key="save_name")
        if c2.button("Save to project"):
            p.analyses.append(AnalysisSpec(nm, ds.name, key, last["opts"])); st.toast("Saved. Attach it to a graph in the Graph tab to draw its brackets/fit.")
        if last["res"].new_dataset is not None and c3.button("Use result as new data table"):
            nd = last["res"].new_dataset; nd.name = unique_name(nd.name); guess_roles(nd); p.datasets[nd.name] = nd; set_later(cur_ds=nd.name); st.rerun()
    saved = [a for a in p.analyses if a.dataset == ds.name]
    if saved:
        with st.expander(f"Saved analyses for this table ({len(saved)})"):
            for a in saved:
                c1, c2 = st.columns([5, 1])
                c1.write(f"**{a.name}** — {S.REGISTRY[a.test].name}")
                if c2.button("Delete", key=f"da:{a.id}"):
                    p.analyses.remove(a)
                    for g in p.graphs:
                        if a.id in g.analysis_ids:
                            g.analysis_ids.remove(a.id)
                    st.rerun()


def use_rec(ds_name, r):
    key = r["key"]; spec = S.REGISTRY[key]
    st.session_state["an_family"] = spec.family; st.session_state["an_test"] = key
    for o in spec.options:
        if o.name in r["opts"] and o.kind in ("select", "group", "float", "int", "bool", "text"):
            st.session_state[f"opt:{ds_name}:{key}:{o.name}"] = r["opts"][o.name]


def unique_name(n):
    base, i = n, 2
    while n in P().datasets:
        n = f"{base} ({i})"; i += 1
    return n


# ------------------------------------------------------------------ graph tab
def field_widget(name, meta, value, key):
    w, label, help_ = meta["widget"], meta["label"], meta.get("help") or None
    if w == "bool":
        return st.checkbox(label, value=bool(value), key=key, help=help_)
    if w == "select":
        ch = meta["choices"]
        return st.selectbox(label, ch, index=ch.index(value) if value in ch else 0, key=key, help=help_)
    if w == "int":
        return int(st.number_input(label, value=int(value), min_value=int(meta["lo"]), max_value=int(meta["hi"]), step=int(meta["step"] or 1), key=key, help=help_))
    if w == "float":
        return float(st.number_input(label, value=float(value), min_value=float(meta["lo"]), max_value=float(meta["hi"]), step=float(meta["step"] or .1), format="%g", key=key, help=help_))
    if w == "opt_float":
        t = st.text_input(label, value="" if value is None else f"{value:g}", key=key, help=help_ or "Leave blank for automatic")
        if t.strip() == "":
            return None
        try:
            return float(t)
        except ValueError:
            st.caption(f":red[“{t}” is not a number]"); return value
    return st.text_input(label, value=value or "", key=key, help=help_)


def format_panel(g: GraphSpec, scope: str):
    p = P()
    eff = p.effective_style(g)
    changed = {}
    groups = {}
    for name, meta in field_meta():
        groups.setdefault(meta["group"], []).append((name, meta))
    for grp, items in groups.items():
        n_over = sum(1 for n, _ in items if n in g.overrides)
        with st.expander(grp + (f"  ·  {n_over} graph-specific" if n_over else ""), expanded=grp in ("Figure",) and False):
            for name, meta in items:
                key = f"fmt:{g.id}:{name}:{st.session_state.ver}"
                v = field_widget(name, meta, getattr(eff, name), key)
                if v != getattr(eff, name):
                    changed[name] = v
    if changed:
        if scope == "all graphs (project theme)":
            p.set_theme(**changed); bump()
        else:
            g.overrides.update(changed)
            for k in [k for k in changed if changed[k] == getattr(p.theme, k)]:
                g.overrides.pop(k, None)  # back to theme value -> not an override any more


def graph_params(g: GraphSpec, ds: Dataset):
    pr = g.params
    k = g.kind
    if k in ("column", "histogram"):
        names = ds.group_names()
        sel = st.multiselect("Groups to show (order = left to right)", names, default=[n for n in pr.get("groups", names) if n in names], key=f"gp:{g.id}:groups")
        pr["groups"] = sel or None
        if k == "histogram":
            pr["bins"] = st.number_input("Bins (0 = auto)", 0, 200, int(pr.get("bins", 0)), key=f"gp:{g.id}:bins")
            pr["stat"] = st.selectbox("Y axis", ["count", "percent", "density"], index=["count", "percent", "density"].index(pr.get("stat", "count")), key=f"gp:{g.id}:stat")
    elif k == "grouped":
        pr["x_factor"] = st.radio("Factor on the X axis", ["a", "b"], index=["a", "b"].index(pr.get("x_factor", "a")), horizontal=True,
                                  format_func=lambda x: ds.roles.get(x) or x, key=f"gp:{g.id}:xf")
    elif k == "xy":
        ys = list(ds.xy_series())
        pr["series"] = st.multiselect("Series", ys, default=[s for s in pr.get("series", ys) if s in ys], key=f"gp:{g.id}:series") or None
    elif k == "heatmap":
        pr["cmap"] = st.selectbox("Colour map", ["viridis", "magma", "cividis", "RdBu_r", "coolwarm", "Greys", "YlGnBu"], key=f"gp:{g.id}:cmap")
        pr["annotate"] = st.checkbox("Print values in cells", pr.get("annotate", False), key=f"gp:{g.id}:ann")
        pr["center_zero"] = st.checkbox("Centre colour scale at 0", pr.get("center_zero", False), key=f"gp:{g.id}:cz")
    elif k == "stacked":
        pr["percent"] = st.checkbox("Show as percent of row", pr.get("percent", True), key=f"gp:{g.id}:pct")
    elif k in ("bland_altman", "roc"):
        names = ds.group_names()
        c = st.columns(2)
        pr["group_a"] = c[0].selectbox("Method A / controls", names, index=names.index(pr["group_a"]) if pr.get("group_a") in names else 0, key=f"gp:{g.id}:ga")
        pr["group_b"] = c[1].selectbox("Method B / patients", names, index=names.index(pr["group_b"]) if pr.get("group_b") in names else min(1, len(names) - 1), key=f"gp:{g.id}:gb")
        if k == "roc":
            pr["direction"] = st.selectbox("Positives have", ["higher values", "lower values"], key=f"gp:{g.id}:dir")


def tab_graph():
    p = P()
    if not p.datasets:
        st.info("Add or load a data table first."); return
    with st.expander("➕ New graph", expanded=not p.graphs):
        c1, c2, c3, c4 = st.columns([2, 2, 3, 1])
        dsn = c1.selectbox("Data table", list(p.datasets), index=list(p.datasets).index(st.session_state["cur_ds"]) if st.session_state.get("cur_ds") in p.datasets else 0, key="ng_ds")
        ds = p.datasets[dsn]
        kinds = [k for k, (lay, _) in GRAPH_KINDS.items() if lay == ds.kind]
        kind = c2.selectbox("Graph type", kinds, format_func=lambda k: GRAPH_KINDS[k][1], key="ng_kind")
        nm = c3.text_input("Graph name", f"{dsn} graph", key="ng_name")
        if c4.button("Create", disabled=not kinds):
            g = GraphSpec(nm, dsn, kind); p.graphs.append(g); set_later(cur_graph=g.id); st.rerun()
    if not p.graphs:
        return
    ids = [g.id for g in p.graphs]
    if st.session_state.get("cur_graph") not in ids:
        st.session_state["cur_graph"] = ids[0]  # safe: the "Graph" selectbox below is created after this line
    gid = st.selectbox("Graph", ids, format_func=lambda i: f"{p.graph(i).name}  ({GRAPH_KINDS[p.graph(i).kind][1]})", key="cur_graph")
    g = p.graph(gid)
    ds = p.datasets.get(g.dataset)
    if ds is None:
        st.error("This graph's data table was deleted."); return
    left, right = st.columns([2, 3], gap="large")
    with left:
        scope = st.radio("Apply format changes to", ["this graph only", "all graphs (project theme)"], key="scope", horizontal=True,
                         help="'All graphs' edits the shared project theme, so every graph follows (graph-specific overrides of that setting are removed).")
        with st.expander("Data & content", expanded=True):
            g.name = st.text_input("Graph name", g.name, key=f"gn:{g.id}")
            graph_params(g, ds)
            cand = [a for a in p.analyses if a.dataset == g.dataset]
            if cand and g.kind in ("column", "grouped", "xy"):
                g.analysis_ids = st.multiselect("Overlay saved analyses (significance brackets / fitted curves)", [a.id for a in cand],
                                                default=[i for i in g.analysis_ids if i in {a.id for a in cand}], format_func=lambda i: p.analysis(i).name, key=f"an:{g.id}")
            elif g.kind in ("column", "grouped", "xy"):
                st.caption("Run and save an analysis in the Analyze tab to draw significance brackets or fitted curves.")
            if st.button("🗑 Delete graph", key=f"delg:{g.id}"):
                p.graphs.remove(g); st.rerun()
        format_panel(g, scope)
    with right:
        style = p.effective_style(g)
        comps, results = plots.analyses_for(p, g)
        fig = plots.render(g, ds, style, p.gs(style), comps, results)
        if getattr(fig, "_ps_clipped", None):
            st.warning(fig._ps_clipped)
        st.image(plots.export(fig, "png", 150), caption=None)
        if g.overrides:
            st.caption("Graph-specific settings: " + ", ".join(sorted(g.overrides)))
        c1, c2, c3 = st.columns([2, 2, 2])
        fmt = c1.selectbox("Export format", ["png", "pdf", "svg", "tiff"], key=f"fmt_sel:{g.id}")
        dpi = c2.number_input("DPI", 72, 1200, int(style.dpi), 50, key=f"dpi:{g.id}")
        if c3.button("Prepare download", key=f"prep:{g.id}"):
            st.session_state.png[g.id] = (fmt, plots.export(fig, fmt, int(dpi), style.background == "transparent"))
        if g.id in st.session_state.png:
            f_, b_ = st.session_state.png[g.id]
            st.download_button(f"⬇ {g.name}.{f_}", b_, file_name=f"{re.sub(r'[^A-Za-z0-9_-]+', '_', g.name)}.{f_}", mime={"png": "image/png", "pdf": "application/pdf", "svg": "image/svg+xml", "tiff": "image/tiff"}[f_], key=f"dlf:{g.id}")
            st.caption("PDF/SVG keep text editable (Illustrator / Inkscape).")


# ------------------------------------------------------------------ consistency tab
def tab_consistency():
    p = P()
    if not p.graphs:
        st.info("Create graphs first."); return
    st.markdown("Everything here keeps graphs looking like they belong together. "
                "**Project theme** = shared format; each graph only stores what differs from it. **Group styles** = colour/marker/hatch tied to a group *name*, identical in every graph.")
    st.subheader("Group colours & markers (same name → same look, everywhere)")
    names = set()
    for g in p.graphs:
        ds = p.datasets.get(g.dataset)
        if ds is None:
            continue
        if ds.kind == "column":
            names |= set(ds.group_names())
        elif ds.kind == "grouped":
            t = ds.long(); names |= set(map(str, t["b"].unique() if "b" in t else t["a"].unique()))
        elif ds.kind == "xy":
            names |= set(ds.xy_series())
        elif ds.kind == "survival":
            names |= set(map(str, ds.survival().group.unique()))
        elif ds.kind == "contingency":
            names |= set(map(str, ds.counts().columns))
    names |= {"Bland-Altman", "ROC"} & set()
    for n in sorted(names):
        p.style_for_group(n)
    if p.group_styles:
        c0, c1 = st.columns([3, 2])
        pal = c1.selectbox("Re-assign colours from palette", list(PALETTES), key="repal")
        if c1.button("Apply palette (clears custom colours)"):
            p.recolor_all(pal); bump(); st.rerun()
        with c0:
            for n, s in list(p.group_styles.items()):
                cc = st.columns([3, 1, 1, 1])
                cc[0].markdown(f"**{n}**")
                auto = p.style_for_group(n)["color"]
                pick_c = cc[1].color_picker("Colour", auto, key=f"gc:{n}:{st.session_state.ver}", label_visibility="collapsed")
                if pick_c.lower() != auto.lower():
                    s["color"] = pick_c  # explicit choice; stops following the palette
                mk = cc[2].selectbox("Marker", ["(theme)"] + MARKERS, index=(MARKERS.index(s["marker"]) + 1) if s.get("marker") in MARKERS else 0, key=f"gm:{n}", label_visibility="collapsed")
                s["marker"] = None if mk == "(theme)" else mk
                h = cc[3].selectbox("Hatch", ["(theme)"] + [x for x in HATCHES if x], index=(HATCHES.index(s["hatch"]) if s.get("hatch") in HATCHES and s["hatch"] else 0), key=f"gh:{n}", label_visibility="collapsed")
                s["hatch"] = None if h == "(theme)" else h
    st.divider()
    st.subheader("Project theme")
    rows = [{"Graph": g.name, "Graph-specific settings": ", ".join(sorted(g.overrides)) or "— follows theme —"} for g in p.graphs]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    gids = {g.id: g.name for g in p.graphs}
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Make one graph the project look**")
        src = st.selectbox("Source graph", list(gids), format_func=gids.get, key="cs_src")
        if st.button("Use as project theme for ALL graphs"):
            p.promote_to_theme(src); bump(); st.rerun()
        st.markdown("**Copy graph-specific format**")
        dst = st.multiselect("Copy this graph's settings to", [i for i in gids if i != src], format_func=gids.get, key="cs_dst")
        if st.button("Copy format", disabled=not dst):
            p.copy_format(src, dst); bump(); st.rerun()
        st.markdown("**Match axes / size**")
        flds = st.multiselect("Fields to match", ["y_min", "y_max", "y_scale", "y_major_step", "width", "height", "spine_width", "font_family", "label_size", "tick_size"],
                              default=["y_min", "y_max", "y_scale", "y_major_step", "width", "height"], key="cs_flds")
        dst2 = st.multiselect("Apply to", [i for i in gids if i != src], format_func=gids.get, key="cs_dst2")
        if st.button("Match", disabled=not dst2):
            p.match_axes(src, dst2, tuple(flds)); bump(); st.rerun()
    with c2:
        st.markdown("**Reset**")
        rg = st.multiselect("Remove graph-specific settings from", list(gids), format_func=gids.get, key="cs_rst")
        if st.button("Reset to theme", disabled=not rg):
            for i in rg:
                p.reset_overrides(i)
            bump(); st.rerun()
        st.markdown("**Named formats (saved in the project file)**")
        pn = st.text_input("Preset name", key="pn")
        if st.button("Save theme as preset", disabled=not pn.strip()):
            p.save_preset(pn.strip()); st.toast("Saved")
        built = {f"built-in: {k}": v for k, v in PRESETS.items()}
        allp = {**built, **{f"saved: {k}": v for k, v in p.presets.items()}}
        sel = st.selectbox("Apply a format to the whole project", list(allp), key="pr_sel")
        if st.button("Apply preset to ALL graphs"):
            p.theme = Style.from_dict({**Style().to_dict(), **(allp[sel] if sel.startswith("built-in") else allp[sel])}); [g.overrides.clear() for g in p.graphs]; bump(); st.rerun()


# ------------------------------------------------------------------ layout tab
def tab_layout():
    p = P()
    if not p.graphs:
        st.info("Create graphs first."); return
    st.markdown("Arrange several graphs into one multi-panel figure with automatic panel letters. Each panel keeps its own effective format.")
    c1, c2, c3, c4 = st.columns(4)
    rows = c1.number_input("Rows", 1, 6, 1); cols = c2.number_input("Columns", 1, 6, 2)
    letters = c3.checkbox("Panel letters (A, B, C…)", True); w = c4.number_input("Figure width (in)", 2.0, 20.0, 7.0, .5)
    ids = [None] + [g.id for g in p.graphs]
    panels = []
    cc = st.columns(int(cols))
    for i in range(int(rows * cols)):
        with cc[i % int(cols)]:
            panels.append(st.selectbox(f"Panel {chr(65 + i)}", ids, format_func=lambda x: "(empty)" if x is None else p.graph(x).name, key=f"lay:{i}"))
    if any(panels):
        lay = {"rows": int(rows), "cols": int(cols), "panels": panels, "letters": letters, "width": float(w), "height": float(w) * rows / cols * .85}
        fig = plots.render_layout(lay, p)
        st.image(plots.export(fig, "png", 140))
        fmt = st.selectbox("Format", ["pdf", "svg", "png", "tiff"], key="lay_fmt")
        if st.button("Prepare multi-panel download"):
            st.session_state["lay_bytes"] = (fmt, plots.export(fig, fmt, 300))
        if "lay_bytes" in st.session_state:
            f_, b_ = st.session_state["lay_bytes"]
            st.download_button(f"⬇ figure.{f_}", b_, file_name=f"figure.{f_}", key="lay_dl")


# ------------------------------------------------------------------ about
def tab_about():
    import plotsuite
    st.markdown(f"""
### PlotSuite {plotsuite.__version__}
Open-source graphing + statistics, modelled on GraphPad Prism's workflow. All calculations come from standard packages
(scipy, statsmodels, pingouin, scikit-posthocs, scikit-learn); each result prints the exact function it used.

**Guardrails.** Before any test runs, the data are checked: wrong table layout, wrong number of groups, unmatched pairs,
non-numeric cells, impossible values (log of ≤0, non-integer counts, P outside 0-1), too few points for a model, perfect separation, and so on.
Hard problems **block** the run and tell you why; assumption problems (non-normal data, unequal variances, small expected counts) show as **warnings** and are copied into the results.

**Where this deliberately differs from Prism** (so you can reconcile numbers):
* Post-tests after repeated-measures ANOVA are paired t tests with a multiplicity correction (Prism uses the ANOVA error term).
* The mixed-effects model is a random-intercept REML model with asymptotic Wald tests (Prism uses a different small-sample approach).
* Dunnett's T3 is not available; use Games-Howell or Tamhane T2 for unequal variances.
* *ROUT* outlier detection is flagged **experimental** (not validated against Prism).
* Methods implemented in-house (checked against reference implementations in the test-suite): Student-Newman-Keuls, trend test post-test, Dunn after Friedman, log-rank family and trend, Grubbs, Deming regression.
""")


def main():
    init()
    sidebar()
    t = st.tabs(["① Data", "② Analyze", "③ Graph", "④ Consistency", "⑤ Multi-panel layout", "About"])
    with t[0]:
        tab_data()
    with t[1]:
        tab_analyze()
    with t[2]:
        tab_graph()
    with t[3]:
        tab_consistency()
    with t[4]:
        tab_layout()
    with t[5]:
        tab_about()


main()
