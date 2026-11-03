# Organoid Sizer

Semi-automatic measurement of organoid size from brightfield / plate / scanner images.
Cellpose finds the organoids, you check and correct them in a browser window, and every image gets a labelled CSV.

* **Detection:** Cellpose (versions 2, 3 and 4; cpsam, cyto3, cyto2 or your own trained model) or a classical
  threshold method that needs no AI and no GPU.
* **Correction:** click to select; delete, exclude (with a reason: touching, fused, out of focus, debris, edge, other),
  include again, split a fused organoid in 2 or 3 automatically or with a drawn cut line, merge, add a missed organoid
  with a circle or a traced outline, brush / eraser to fix an outline, undo / redo for everything. Zoom with the wheel,
  pan with Space-drag or right-drag, minimap for large images.
* **Scale:** read from microscope metadata, or typed in (µm/px, nm/px, px/µm, px/mm, DPI), or from a line drawn on a
  ruler / scale bar / object of known size, or from automatic scale-bar detection, or from the tick spacing of a
  graduated ruler; save scales as presets; apply to one image or all.
* **Output:** one CSV per image (`<image>_organoids.csv`), one CSV with all organoids, a per-image summary, an annotated
  overlay PNG whose numbers match the CSV, a 16-bit label mask (opens in Fiji with the scale), and a JSON record of the
  settings and calibration used.
* Runs locally (nothing is uploaded anywhere); works on Ubuntu, Windows and macOS in Chrome, Firefox, Edge or Safari;
  layout adapts from laptop to 4K screens (Settings ⚙ → Interface size).

## Install (Ubuntu, Cellpose already installed)

```bash
conda activate cellpose            # or however you normally start Cellpose
cd /path/to/organoid-sizer
pip install -e . --no-deps         # --no-deps: uses the numpy / scipy / OpenCV that Cellpose brought
organoid-sizer                     # opens the app in your browser
```

Without Cellpose (threshold method only, any OS): `pip install -e ".[standalone]"`.
Optional readers for vendor files: `pip install nd2 readlif czifile` (or `bioio` / `aicsimageio` for other formats).

Start options:

```bash
organoid-sizer /data/plate_scans                  # open a folder directly
organoid-sizer /data/plate_scans --out /data/res  # choose where results go (default: <folder>/organoid_results)
organoid-sizer --host 0.0.0.0 --port 8765         # let other computers on the lab network use it
organoid-sizer /data/scans --batch --um-per-px 10.58 --diameter-um 400   # no GUI: detect + export everything
```

Double-click launchers are in `launchers/` (`.sh` for Linux/macOS, `.bat` for Windows, `.desktop` for the Ubuntu menu).
They activate a conda environment called `cellpose` if it exists.

## Workflow

1. **Open folder** (top bar) and pick the image folder in the dialog, or drag image files onto the window.
2. **Scale tab.** Microscope files with metadata are calibrated automatically (green dot). Otherwise:
   * *I know the pixel size* – type it (e.g. `1.6 µm per pixel`, or `1200 DPI` for a scanner) → **Apply to all images**.
   * *Ruler / scale bar* – press **R**, drag along the ruler (zoom in for the ends, drag the round handles to adjust),
     type the real length → Apply. **Find scale bars** locates printed scale bars and gives their exact pixel length.
   * *Graduated ruler* – draw a line across many tick marks; the tick period is measured from the whole profile
     (more precise than clicking two marks). Type the tick spacing (e.g. 1 mm) → Apply.
   * **Save current** keeps a scale (e.g. “Epson 1200 dpi”, “EVOS 4×”) for one-click use later.
3. **Detect tab → Detect in this image.** If organoids are missed or cut into pieces, set *Expected organoid size*
   and try again; *Invert image* sometimes helps for dark organoids. *Only inside wells* or **Draw analysis region**
   (key **O**) ignores labels, text and debris outside the well. **Detect in all images** runs the batch.
4. **Correct.** Click an organoid and press **Del** (not an organoid), **X** (exclude – it stays in the CSV with the
   reason), **2** / **3** (split fused organoids), **M** (merge selected). Add a missed one with **C** (drag a box
   around it) or **P** (trace the outline). **S** cuts along a line you draw. **N** jumps to the next organoid flagged
   “?” (possibly fused: low solidity, or elongated and not convex). Right-click gives the same actions.
5. Tick **Reviewed**, **PgDn** for the next image.
6. **Export all.** Everything is also saved automatically after each change (`<output>/.organoid_sizer/`), so you can
   close the browser and continue later.

Press **?** in the app for all keyboard shortcuts.

## CSV columns

| column | meaning |
|---|---|
| `organoid_number` | number shown on the overlay; reading order (top-to-bottom rows, left-to-right) |
| `status`, `exclusion_reason` | `included` / `excluded` and why |
| `flags` | `possibly fused / irregular`, `touches image edge` (hints, not decisions) |
| `source` | `cellpose`, `threshold`, `manual`, `split`, `merged` |
| `area_px`, `area_um2`, `area_mm2` | projected area (holes filled) |
| `equivalent_diameter_um` | diameter of a circle with the same area: 2·√(area/π) |
| `feret_max_um`, `feret_min_um` | largest / smallest caliper width |
| `ellipse_major_um`, `ellipse_minor_um` | axes of the fitted ellipse (OpenCV `fitEllipse`) |
| `circularity` | 4π·area/perimeter² (1 = circle) |
| `solidity` | area / convex-hull area |
| `volume_sphere_mm3` | π/6·d³ from the equivalent diameter |
| `volume_ellipsoid_mm3` | 4/3·π·a·b² (prolate spheroid from the ellipse axes) |
| `um_per_px`, `calibration_source` | the scale used for this image and where it came from |

`summary_per_image.csv` uses **included** organoids only. Without a scale, sizes are in pixels and µm columns are empty.

## Where the pixel size comes from

| source | trusted automatically? |
|---|---|
| OME-TIFF (`PhysicalSizeX`), ImageJ TIFF (calibration + unit), Zeiss LSM, MetaMorph STK / MetaSeries, Olympus FluoView | yes |
| Zeiss CZI (`czifile`), Nikon ND2 (`nd2`), Leica LIF (`readlif`), other formats via `bioio` / `aicsimageio` | yes, if the reader is installed |
| DPI / resolution tag in TIFF, PNG, JPEG | **no** – shown as a suggestion. Flatbed scanners write a real DPI, but cameras, screenshots and figures usually write a placeholder (72, 96, 144, 300). Enable *Trust DPI tags* in the Scale tab for scanner batches. |

## Tests

```bash
pip install pytest && pytest
```

## Known limits

* Measurements are 2-D projections; volumes assume a sphere or prolate spheroid.
* The “possibly fused” flag is a geometric hint (solidity < 0.90, or aspect ratio > 1.5 with solidity < 0.97).
* The threshold method works for evenly lit, good-contrast images (e.g. brightfield wells; *Only inside wells* is on by default and does nothing when no well is found);
  low-contrast photos with text over them need Cellpose and/or an analysis region.
* Multi-channel / z-stack / time-lapse files: the first channel, middle z-plane and first time point are used
  (noted in the detection log).
