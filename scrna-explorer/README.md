# snRNA gene explorer

Question it answers: *is gene X expressed in this dataset, in which cell type, and is it higher in condition A than B?*

**Design:** each dataset is reduced once to a small gene x group table (n cells, % expressing, mean expression).
The GUI only reads those tables (a few MB each), so the multi-GB raw objects don't need to be kept.

## Status
- `prep/summarize.py` (h5ad / 10x MTX): unit-tested against a dense reference; GUI smoke-tested on **synthetic** data only.
- `prep/export_seurat.R` (Seurat `.rds`): **never run** (no R, and no access to GEO, where this was written). Check it on one object first.
- No real dataset (GSE301194, GSE190815) has been processed yet; no biological result has been produced.

## Use
```
pip install -r requirements.txt
# 1. download (GEO FTP) and summarise each dataset into data/summaries/
Rscript prep/export_seurat.R GSE301194_XCX_processed_seuratObject.rds XCX data/summaries/XCX.tsv <celltype_col>
#    GSE190815 (Revah 2022): inspect what the supplementary files are, then use export_seurat.R or summarize.py with
#    --group-cols <condition_col> <celltype_col>; for transplanted samples keep human cells only, e.g. --filter species=human
#    (column names are unknown until the objects are opened; the script prints the metadata columns)
# 2. GUI
streamlit run app/app.py
```
Rows in the heatmap are chosen per dataset in the sidebar ("all" = whole dataset, or any metadata column).
Genes absent from a dataset show `n/a`, not 0.

## Validation against your slide
Your slide gives % cells expressing for GRIN1, GRIN2A/B, GRIA1/2, CAMK2A/B, STX3, TSNARE?, STX12, CACNA1C/D, SLC12A2/5/6/4
in XCX (Day168+), Revah tx and Revah in vitro (human cells). e.g. SLC12A2: 25% / 20% / 11%. Once the real tables exist,
enter those genes and compare; agreement (given the same cell filter) validates the pipeline. Note the slide's
"TSNARE" label is cut off in the image, so I can't tell which gene it is.
