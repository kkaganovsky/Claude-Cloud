#!/usr/bin/env Rscript
# Summarise a Seurat .rds object into the same gene x group table as summarize.py.
# NOT TESTED: R was not available where this was written. Run it on one object
# and compare a few genes against FeaturePlot/DotPlot before trusting it.
#
# Usage: Rscript export_seurat.R <obj.rds> <dataset> <out.tsv> [group_cols,comma] [filter COL=VAL,VAL]
suppressPackageStartupMessages({ library(Seurat); library(Matrix) })
args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 3) stop("usage: export_seurat.R obj.rds dataset out.tsv [group_cols] [COL=VAL,VAL]")
obj <- readRDS(args[1]); dataset <- args[2]; out <- args[3]
group_cols <- if (length(args) >= 4 && nzchar(args[4])) strsplit(args[4], ",")[[1]] else character(0)
# Use the RNA assay: an "integrated" assay holds only ~2-3k variable genes with
# batch-corrected (often negative) values, so % expressing / means are not meaningful.
assay <- Sys.getenv("SEURAT_ASSAY", "RNA")
if (assay %in% Assays(obj)) DefaultAssay(obj) <- assay
meta <- obj@meta.data
cat("assays:", paste(Assays(obj), collapse = ", "), " default:", DefaultAssay(obj), "\n")
cat("metadata columns:", paste(colnames(meta), collapse = ", "), "\n")

if (length(args) >= 5 && nzchar(args[5])) {
  kv <- strsplit(args[5], "=", fixed = TRUE)[[1]]
  keep <- as.character(meta[[kv[1]]]) %in% strsplit(kv[2], ",")[[1]]
  obj <- obj[, keep]; meta <- obj@meta.data
}
get_layer <- function(o, what) {
  tryCatch(GetAssayData(o, layer = what), error = function(e) GetAssayData(o, slot = what))
}
expr <- get_layer(obj, "data")                   # genes x cells, log-normalised
if (max(expr) > 50) { # looks like raw counts, normalise ourselves
  expr <- LogNormalize(expr)
}
genes <- rownames(expr)
meta$`__all__` <- "all cells"
frames <- list()
for (col in c("__all__", group_cols)) {
  lab <- as.character(meta[[col]]); lab[is.na(lab)] <- "NA"
  for (lev in sort(unique(lab))) {
    sub <- expr[, lab == lev, drop = FALSE]
    n <- ncol(sub)
    npos <- Matrix::rowSums(sub > 0)
    s <- Matrix::rowSums(sub)
    frames[[length(frames) + 1]] <- data.frame(
      dataset = dataset, group_col = ifelse(col == "__all__", "all", col), group = lev,
      gene = genes, n_cells = n, pct_expr = 100 * npos / n, mean_expr = s / n,
      mean_expr_pos = ifelse(npos > 0, s / npos, NA_real_), stringsAsFactors = FALSE)
  }
}
write.table(do.call(rbind, frames), out, sep = "\t", quote = FALSE, row.names = FALSE)
cat("wrote", out, "\n")
