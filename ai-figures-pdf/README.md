# ai-figures-pdf

Drag and drop a folder of Illustrator `.ai` files. The tool exports **artboard 1** of each file as a PNG at a DPI you
choose, then puts all the PNGs into **one PDF** in figure order. Every page is the same size. Errors and issues are
listed in the log as it runs.

```
pip install -e .            # PyMuPDF + PySide6
ai-figures-pdf              # GUI   (or: python -m ai_figures)
ai-figures-pdf-cli "path/to/figures" --dpi 300 --page letter     # same thing without the GUI
```

## Order
Figures are read from the **file name** (case, `_ - .` and spaces are ignored):

1. Main figures: `Figure 1`, `Fig1`, `fig_2b`, `Figs 3`
2. Extended Data: `Extended Data Fig 1`, `ED_Fig2`, `ExtFig3`
3. Supplementary: `Supplementary Figure 1`, `SuppFig_2`, `Supp 3`, `Figure S4`, `FigS5a`, `S6`
4. Anything unrecognised (`cover art.ai`, `Supp Table 1.ai`) goes at the end, with a warning

Within a group, files are sorted by number, then panel letter (`1a`, `1b`), then name. The log warns about duplicate
numbers (`Fig1.ai` and `Fig1_v2.ai`, both kept) and gaps (`missing 3`). Before you export you can untick files or
move them up and down in the table.

## Consistent pages
- **Page size**: Letter, A4, Tabloid, custom, or "Largest artboard". All pages use this size.
- **Scaling**: *Fit* scales each figure to fill the printable area. *Native size* keeps the Illustrator size and only
  shrinks figures that don't fit. Figures are centred horizontally and aligned to the top (or to the centre).
- The log warns when an artboard's width is more than 10% off the median. If figures are placed at very different
  scales, text sizes won't match from page to page. It also warns when enlarging a figure drops its effective
  resolution below 150 dpi. Giving every figure the same artboard width in Illustrator is the real fix.

## Output
`<folder>/figures_export/` (you can change it): `png/<name>.png`, `figures.pdf` (one bookmark per figure), and
`export_log.txt`.

## Limitations (read these)
- The `.ai` file must have been saved with **"Create PDF Compatible File"** (Illustrator's default). The tool reads
  the PDF stream inside the file. Files saved without it are detected and reported, and they can't be rendered
  without Illustrator.
- Artboard 1 is taken to be **page 1** of that PDF stream, cropped to its page box. This was tested only on
  synthetic PDF-based files, not on files saved by Illustrator. Check the first export against Illustrator,
  especially files with several artboards, bleed, or linked images.
- The order comes only from file names. Text inside the artwork is not read.
- macOS `._*` files are skipped.

```
pip install -e .[test] && pytest
```
