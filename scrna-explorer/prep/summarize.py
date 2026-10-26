"""Collapse a single-cell expression dataset into a small gene x group summary table.

The GUI never needs the full matrix, only, for every gene and every group
(cell type, condition, ...): how many cells express it and how strongly.
That table is a few MB per dataset instead of GBs.

Inputs: an .h5ad file, or a 10x-style MTX directory plus a metadata CSV.
For Seurat .rds objects use prep/export_seurat.R (same output schema).

Output columns: dataset, group_col, group, gene, n_cells, pct_expr,
mean_expr, mean_expr_pos
  pct_expr      % of cells in the group with expression > 0
  mean_expr     mean of log-normalised expression over ALL cells in the group
  mean_expr_pos same, over expressing cells only (NaN if none)
"""
import argparse
import gzip
import os
import sys

import numpy as np
import pandas as pd
import scipy.io
import scipy.sparse as sp

SCHEMA = ["dataset", "group_col", "group", "gene", "n_cells",
          "pct_expr", "mean_expr", "mean_expr_pos"]


def log_normalize(counts, scale=1e4):
    """counts: cells x genes CSR. Seurat-style LogNormalize (log1p of CP10k)."""
    counts = sp.csr_matrix(counts, dtype=np.float64)
    totals = np.asarray(counts.sum(axis=1)).ravel()
    totals[totals == 0] = 1.0
    norm = sp.diags(scale / totals) @ counts
    norm.data = np.log1p(norm.data)
    return norm.tocsr()


def summarize(expr, genes, meta, group_cols, dataset):
    """expr: cells x genes sparse (already log-normalised); meta: DataFrame aligned to rows."""
    expr = sp.csr_matrix(expr)
    present = (expr > 0).astype(np.float64).tocsr()
    frames = []
    meta = meta.copy()
    meta["__all__"] = "all cells"
    for col in ["__all__"] + list(group_cols):
        labels = meta[col].astype("object").where(meta[col].notna(), "NA").astype(str)
        levels = sorted(labels.unique())
        idx = pd.Categorical(labels, categories=levels).codes
        ind = sp.csr_matrix((np.ones(len(idx)), (idx, np.arange(len(idx)))),
                            shape=(len(levels), len(idx)))
        n = np.asarray(ind.sum(axis=1)).ravel()
        n_pos = (ind @ present).toarray()
        sums = (ind @ expr).toarray()
        for i, level in enumerate(levels):
            with np.errstate(invalid="ignore", divide="ignore"):
                mean_pos = np.where(n_pos[i] > 0, sums[i] / n_pos[i], np.nan)
            frames.append(pd.DataFrame({
                "dataset": dataset,
                "group_col": "all" if col == "__all__" else col,
                "group": level,
                "gene": genes,
                "n_cells": int(n[i]),
                "pct_expr": 100.0 * n_pos[i] / n[i],
                "mean_expr": sums[i] / n[i],
                "mean_expr_pos": mean_pos,
            }))
    return pd.concat(frames, ignore_index=True)[SCHEMA]


def _read_lines(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt") as f:
        return [l.rstrip("\n").split("\t") for l in f]


def load_mtx_dir(d):
    def find(*names):
        for n in names:
            for suffix in ("", ".gz"):
                p = os.path.join(d, n + suffix)
                if os.path.exists(p):
                    return p
        sys.exit(f"none of {names} found in {d}")
    mtx = scipy.io.mmread(find("matrix.mtx")).tocsr()   # genes x cells in 10x layout
    feats = _read_lines(find("features.tsv", "genes.tsv"))
    genes = [f[1] if len(f) > 1 else f[0] for f in feats]
    barcodes = [b[0] for b in _read_lines(find("barcodes.tsv"))]
    return mtx.T.tocsr(), genes, barcodes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True, help="short name, e.g. XCX")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--h5ad")
    src.add_argument("--mtx-dir")
    ap.add_argument("--meta", help="metadata CSV/TSV (first column = barcode); required with --mtx-dir")
    ap.add_argument("--group-cols", nargs="+", default=[], help="metadata columns to summarise by")
    ap.add_argument("--filter", action="append", default=[], metavar="COL=VAL[,VAL]",
                    help="keep only cells where COL is one of VALs (repeatable), e.g. species=human")
    ap.add_argument("--already-normalized", action="store_true",
                    help="matrix is already log-normalised; otherwise raw counts are assumed")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    if a.h5ad:
        import anndata
        ad = anndata.read_h5ad(a.h5ad)
        expr, genes, meta = ad.X, list(ad.var_names), ad.obs.copy()
    else:
        if not a.meta:
            sys.exit("--meta is required with --mtx-dir")
        expr, genes, barcodes = load_mtx_dir(a.mtx_dir)
        sep = "\t" if a.meta.endswith((".tsv", ".tsv.gz", ".txt")) else ","
        meta = pd.read_csv(a.meta, sep=sep, index_col=0).reindex(barcodes)
        if meta.isna().all(axis=1).any():
            print("warning: some barcodes have no metadata row", file=sys.stderr)

    keep = np.ones(len(meta), dtype=bool)
    for f in a.filter:
        col, vals = f.split("=", 1)
        keep &= meta[col].astype(str).isin(vals.split(",")).to_numpy()
    expr, meta = sp.csr_matrix(expr)[keep], meta[keep]
    if not a.already_normalized:
        expr = log_normalize(expr)
    for c in a.group_cols:
        if c not in meta.columns:
            sys.exit(f"group column {c!r} not in metadata; available: {list(meta.columns)}")

    out = summarize(expr, genes, meta, a.group_cols, a.dataset)
    out.to_csv(a.out, sep="\t", index=False, float_format="%.5g")
    print(f"{a.dataset}: {keep.sum()} cells, {len(genes)} genes -> {a.out}")


if __name__ == "__main__":
    main()
