# Panel Editor

A free, open-source, **browser-based** editor for Adobe Illustrator `.ai` files, built so collaborators
without Illustrator can open a figure, add or adjust panels, and send the file back.
Files are processed entirely in the browser — nothing is uploaded.

> **Read "What it is and isn't" before relying on it.** It edits the *vector content* of an `.ai` file.
> It is not a clone of Illustrator, and it cannot write Illustrator's proprietary native data.

## Use it

```bash
npm install
npm run dev          # http://localhost:5173
npm run build        # static site in dist/ — host anywhere (GitHub Pages, Netlify, an intranet)
```

Collaborators only need a URL. (`dist/` must be served over http(s); browsers will not run it from `file://`.)

**Workflow:** *Open…* (or drag a file in) → edit → *Save .ai*. *Ctrl/⌘+S* saves; in Chrome/Edge
re-saving overwrites the same file, other browsers download a new copy.

## What you can do

| | |
|---|---|
| Open | PDF-compatible `.ai`, plain `.pdf`. Each PDF page = one artboard. PDF layers (optional content groups) = layers. |
| Select / transform | click, shift-click, marquee, move, scale (handles, Shift = proportional), rotate (Shift = 15° steps), arrow-key nudge, numeric X/Y/W/H |
| Edit points | Direct-select tool (A): drag anchors and Bézier handles of any path |
| Draw | rectangle, ellipse, line, pencil (auto-smoothed), pen (click = corner, drag = curve, click first point to close) |
| Type | click to add text, double-click to edit; size, Sans/Serif/Mono, bold/italic, fill, stroke |
| Style | solid fill/stroke, none, stroke weight, opacity; imported gradients are displayed and kept |
| Images | *Place image…* (PNG/JPG) or drop onto the window |
| Arrange | to front / forward / backward / to back, duplicate, copy/paste, layers (show/hide, lock, reorder, rename, add/delete), multiple artboards |
| Safety | 60-step undo/redo |
| Export | `.ai` (see below), PDF (lean copy), SVG and PNG of the current artboard |

Shortcuts: V select · A direct select · P pen · N pencil · M rect · L ellipse · \\ line · T type · H hand (or hold Space) · Z zoom · Ctrl/⌘ + Z / Shift+Z / C / V / D / A / S / O / 0 / + / −.

## What it is and isn't

An Illustrator `.ai` saved with *Create PDF Compatible File* (the default) contains a complete PDF
page description plus Illustrator's private native data. Panel Editor reads the PDF half, turns it into editable
objects, and writes a new PDF.

* **Saved `.ai` files are PDF files with an `.ai` extension.** They do **not** contain Illustrator's native
  private data. Illustrator will open them through its PDF path; expect paths, fills, strokes, images and
  layers to arrive as editable vectors. **This has not been tested in Adobe Illustrator** (none was available
  when this was written) — try a real round trip with one of your files before depending on it.
  Verified so far: pdf.js (the reader this app uses) and poppler render saved files identically to the originals
  on the test files (see Tests).
* **Fonts are not preserved.** Original fonts are not embedded in saved files. Text is written with a
  Liberation font (metric-compatible with Arial / Times / Courier) chosen from the original font's name and style,
  squeezed to the original width if you did not touch the text. Greek letters, µ, ±, ° etc. work.
  If exact typography matters, have text finished in Illustrator.
* **Re-opening in Panel Editor is lossless.** Saved files also carry the editor's own state, so a file that
  goes Panel Editor → collaborator → Panel Editor keeps editable text and layers exactly. If Illustrator re-saves the
  file that extra data is dropped and the file is read from its vector content again.
  (This roughly doubles file size; *Export → PDF (lean copy)* omits it.)
* **Colour is RGB.** CMYK/spot colours are converted to RGB on import (pdf.js does the conversion) and written as RGB.
  Do not use this for print-critical colour work.
* **Not supported (shown flat, approximated or dropped):** mesh gradients and tiling patterns (flat grey),
  blend modes, opacity/soft masks, live effects, symbols, brushes, text-on-path/area-type reflow (each line is its own
  text object), grouping (objects are flat; clipping masks are kept as per-object clips and move with the object),
  overprint, artboard names, `.ai` files without PDF compatibility (an error explains this).
  Import notes appear in the status bar (⚠) when a file uses any of these.
* Old PostScript-based `.ai` (Illustrator 8 and earlier) and SVG import are not supported.

If you need full Illustrator-grade fidelity, this is the wrong tool; it is meant for "add a panel, move things,
fix a label" collaboration.

## Figure workflow: linked plots, automatic panel letters, lab grid

Built around `plotsuite` (the figure generator) and `ai-figures-pdf` (file naming), but works with any PDF plot.

**An artboard is a figure.** Set each artboard to *Main*, *Supplementary* or *Extended Data*; names are computed
(`Fig. 2`, `Supplementary Fig. 1`). A **panel** is a tagged set of objects (plot artwork + an invisible frame +
the letter + your callouts). Clicking any part selects the whole panel (Alt-click = just that object).

* **Letters are computed, never typed.** Default is reading order (rows top→bottom, left→right, tolerance 30 pt);
  or switch to manual order (▲▼). Drag a panel and every letter shifts. Move a panel to another figure from the
  sidebar (*→ fig…*) and it renumbers, e.g. `Fig. 2c` → `Supplementary Fig. 1a`. A single-panel figure gets no letter (switchable).
* **References follow.** In any text, *Insert panel reference…* adds `{{ref:ID}}` (also `{{fig:ID}}`, `{{letter:ID}}`,
  `{{short:ID}}`); it displays "Fig. 2b" and updates when panels move. The **Change log** lists every relabel
  and the net change since the file was opened, i.e. what to fix in manuscript text outside the file.
  *Panel map (CSV)* exports label, name, source PDF, data file and data hash for every panel.
* **Linked plots.** *Place plot(s)…* takes plotsuite PDFs (plus their `.json` sidecars) and imports them as real,
  editable vector panels. *↻* (or the folder watcher in Chrome/Edge) swaps in a newer PDF: only the plot artwork is
  replaced; letter, callouts, position and scale stay, and (if *auto-arrange* is ticked) panels re-flow when the new plot is wider.
  Each panel stores its provenance: command, data file + sha256, statistics output.
* **Rebuilding when data change:** `tools/panel_build.py panels.json [--watch|--check|--force]` runs `plotsuite-plot`
  only for plots whose data/command hash changed and writes `plots/<id>.pdf` + `<id>.json`. If plotsuite's guardrails block an
  analysis, the old PDF is kept untouched, the error is reported and the exit code is non-zero.
* **Snapping.** Smart guides snap panel frames to neighbours' edges/centres (threshold 6 px); *Grid* shows and snaps to
  the column grid; Align/Distribute work on whole panels; **Auto-arrange** flows panels in reading order, top-aligned
  rows, left edges on column starts, letters above frames. Layout (columns, gutter, margin, row gap, letter offset) is editable in the sidebar.
* **Export figures** writes `Figure 1.ai`, `Supplementary Figure 1.ai`, … plus `panel-map.csv` in one zip. These names
  are classified correctly by `ai-figures-pdf` (checked against its `classify()`). The multi-artboard project `.ai`
  remains the master; per-figure files are deliverables.

**Assumptions to check against your lab's actual conventions** (all are presets/settings, not hard-coded): the
"Nature" column widths (89 / 183 mm) and 12-column grid, 8 pt bold lowercase letters 10 pt above the top-left of the
frame, 8 pt gutters. The lab's real rules (letter size/offset, margins, panel spacing) should replace these.
The "wiki" mentioned for the figure generator was not found in this repository's branches, so the integration targets
`plotsuite-plot`'s CLI/PDF output and its `--save-project` is not used yet.

## How it works

`src/import.js` walks pdf.js's operator list (paths, colours, gradients, clips, images, text matrices, marked
content → layers) and builds the model in `src/model.js`. `src/render.js` draws the model as SVG. `src/export.js` writes
PDF content streams with pdf-lib (shading patterns for gradients, optional-content groups for layers,
subsetted Liberation fonts). `src/main.js` is the editor UI.

## Tests

```bash
npm run build
npm i -D playwright && npx playwright install chromium   # once
npm test          # unit (12) + UI e2e (37) + figure-workflow e2e (42, uses real plotsuite output fixtures)
```

`test/make_fixture.py` (reportlab) generates an independent `.ai`-style fixture with gradients, a clip, dashes,
an image and text. The e2e test drives the real UI (selecting, dragging, resizing, drawing, direct-select, text,
layers, undo/redo, save/reopen) and, if `pdftocairo` and ImageMagick are installed, renders the saved file with
poppler and compares it to the original.

## Licence

MIT. The bundled Liberation fonts (`public/fonts`) are under their own licence (SIL OFL / see `public/fonts/LICENSE-Liberation.txt`);
pdf.js (Apache-2.0) and pdf-lib (MIT) are dependencies.
