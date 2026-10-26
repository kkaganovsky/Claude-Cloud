"""Gene explorer over pre-summarised single-cell datasets.  Run: streamlit run app/app.py"""
import glob
import os

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

SUMMARY_DIR = os.environ.get("SCRNA_SUMMARY_DIR",
                             os.path.join(os.path.dirname(__file__), "..", "data", "summaries"))

st.set_page_config(page_title="snRNA gene explorer", layout="wide")


@st.cache_data
def load_all(d):
    files = sorted(glob.glob(os.path.join(d, "*.tsv")) + glob.glob(os.path.join(d, "*.tsv.gz")))
    if not files:
        return None
    return pd.concat([pd.read_csv(f, sep="\t") for f in files], ignore_index=True)


df = load_all(os.path.abspath(SUMMARY_DIR))
st.title("Is my gene expressed here, and where?")
if df is None:
    st.error(f"No summary tables in {os.path.abspath(SUMMARY_DIR)}. See README.md (step 1).")
    st.stop()

datasets = sorted(df["dataset"].unique())
all_genes = sorted(df["gene"].unique())
upper = {g.upper(): g for g in all_genes}

with st.sidebar:
    st.header("Genes")
    raw = st.text_area("Gene symbols (space/comma/newline separated)", "SHISA9", height=100)
    wanted = [t for t in raw.replace(",", " ").split() if t]
    genes = [upper[t.upper()] for t in wanted if t.upper() in upper]
    missing = [t for t in wanted if t.upper() not in upper]
    if missing:
        st.warning("Not a feature in any loaded dataset: " + ", ".join(missing))
    st.header("Datasets")
    chosen = {}
    for ds in datasets:
        cols = sorted(df.loc[df.dataset == ds, "group_col"].unique(), key=lambda c: (c != "all", c))
        chosen[ds] = st.selectbox(f"{ds}: rows = ", cols, key=f"gc_{ds}",
                                  help="'all' = one row for the whole dataset")
    metric = st.radio("Metric", ["pct_expr", "mean_expr", "mean_expr_pos"],
                      format_func={"pct_expr": "% cells expressing (>0)",
                                   "mean_expr": "mean log-norm expr (all cells)",
                                   "mean_expr_pos": "mean log-norm expr (expressing cells)"}.get)
    min_cells = st.number_input("Hide groups with fewer cells than", 0, 100000, 20)

if not genes:
    st.info("Enter at least one gene symbol present in the data.")
    st.stop()

sel = pd.concat([df[(df.dataset == ds) & (df.group_col == gc)] for ds, gc in chosen.items()])
sel = sel[sel.gene.isin(genes) & (sel.n_cells >= min_cells)].copy()
sel["row"] = sel.dataset + " · " + sel.group + " (n=" + sel.n_cells.astype(str) + ")"
# genes absent from a dataset are 'not measured', not zero -> leave as NaN
metric_label = {"pct_expr": "% cells", "mean_expr": "mean expr", "mean_expr_pos": "mean expr (pos)"}[metric]

tab1, tab2, tab3 = st.tabs(["Compare groups / datasets", "Dot plot (one dataset)", "Table"])

with tab1:
    piv = sel.pivot_table(index="row", columns="gene", values=metric, sort=False).reindex(columns=genes)
    order = [r for r in sel["row"].drop_duplicates() if r in piv.index]
    piv = piv.loc[order]
    text = piv.map(lambda v: "n/a" if pd.isna(v) else (f"{v:.0f}%" if metric == "pct_expr" else f"{v:.2f}"))
    fig = go.Figure(go.Heatmap(z=piv.values, x=piv.columns, y=piv.index, text=text.values,
                               texttemplate="%{text}", colorscale="Blues",
                               zmin=0, zmax=100 if metric == "pct_expr" else None,
                               colorbar=dict(title=metric_label)))
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(height=max(250, 60 * len(piv) + 120), margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig, width="stretch")
    st.caption("n/a = gene not in that dataset's feature list (not the same as 0). "
               "Mean expression is only comparable within a dataset (different depth/normalisation); "
               "% expressing also depends on sequencing depth.")

with tab2:
    ds = st.selectbox("Dataset", datasets, key="dot_ds")
    gcs = sorted(df.loc[df.dataset == ds, "group_col"].unique(), key=lambda c: (c != "all", c))
    gc = st.selectbox("Group by", gcs, index=min(1, len(gcs) - 1), key="dot_gc")
    d = df[(df.dataset == ds) & (df.group_col == gc) & df.gene.isin(genes) & (df.n_cells >= min_cells)]
    if d.empty:
        st.info("No data for this selection.")
    else:
        d = d.assign(label=d.group + " (n=" + d.n_cells.astype(str) + ")")
        fig = px.scatter(d, x="gene", y="label", size="pct_expr", color="mean_expr_pos",
                         color_continuous_scale="Reds", size_max=28,
                         labels={"pct_expr": "% cells", "mean_expr_pos": "mean expr (pos)", "label": ""},
                         hover_data=["n_cells", "pct_expr", "mean_expr"])
        fig.update_layout(height=max(300, 40 * d["label"].nunique() + 150))
        st.plotly_chart(fig, width="stretch")
        st.caption("Dot size = % cells expressing; colour = mean expression in expressing cells.")

with tab3:
    out = sel[["dataset", "group_col", "group", "gene", "n_cells", "pct_expr", "mean_expr", "mean_expr_pos"]]
    st.dataframe(out, width="stretch", hide_index=True)
    st.download_button("Download CSV", out.to_csv(index=False), "gene_summary.csv", "text/csv")
