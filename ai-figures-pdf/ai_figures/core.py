"""Core logic (no GUI): find .ai files, order them as figures, render artboard 1 to PNG, compile one PDF.

How an .ai file is read
-----------------------
An Illustrator file saved with "Create PDF Compatible File" (the default) contains a full PDF stream. PyMuPDF opens
that stream; page 1 of it is taken as artboard 1, cropped to the page's CropBox. Files saved *without* PDF
compatibility hold only a placeholder page ("This is an Adobe Illustrator file that was saved without PDF
Content"); those are detected and reported as errors, since they cannot be rendered without Illustrator.
"""
from __future__ import annotations

import dataclasses
import re
import statistics
from pathlib import Path
from typing import Callable, List, Optional

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # older PyMuPDF
    import fitz

# ---------------------------------------------------------------------------------------------------------------
# Figure-name recognition and ordering
# ---------------------------------------------------------------------------------------------------------------
MAIN, EXTENDED, SUPP, OTHER = 0, 1, 2, 3
CATEGORY_LABEL = {MAIN: "Fig.", EXTENDED: "Extended Data Fig.", SUPP: "Supplementary Fig.", OTHER: "Unrecognized"}

_NUM = r"(\d+)([a-z]?)(?![a-z])"          # figure number, optional panel letter ("1", "1b"); "1v2" is not a panel
_FIG = r"fig(?:ure)?s?\s*"
# Checked in this order; the first match wins.  Names are lower-cased and _ - . , ( ) [ ] + become spaces first.
_PATTERNS = [
    (EXTENDED, re.compile(r"(?<![a-z])(?:extended|ext|ed)\s*(?:data\s*)?" + _FIG + r"s?" + _NUM)),
    (SUPP, re.compile(r"(?<![a-z])(?:supplementary|supplemental|suppl|supp|sup|si)\s*(?:info(?:rmation)?\s*)?"
                      r"(?:" + _FIG + r")?s?" + _NUM)),
    (SUPP, re.compile(_FIG + r"s" + _NUM)),                      # "Figure S1", "FigS2"
    (MAIN, re.compile(_FIG + _NUM)),                             # "Figure 1", "Fig1", "fig_2b"
    (SUPP, re.compile(r"^s\s*" + _NUM + r"$")),                  # a file called just "S3"
]


def _normalise(stem: str) -> str:
    s = re.sub(r"[_\-.,()\[\]+]+", " ", stem.lower())
    return re.sub(r"\s+", " ", s).strip()


def _natural_key(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s.lower())]


@dataclasses.dataclass
class Figure:
    path: Path
    category: int = OTHER
    number: Optional[int] = None
    panel: str = ""
    include: bool = True
    # filled by probe()
    width_pt: Optional[float] = None
    height_pt: Optional[float] = None
    n_pages: Optional[int] = None
    probe_error: str = ""
    # filled by export
    png_path: Optional[Path] = None

    @property
    def label(self) -> str:
        if self.category == OTHER:
            return "Unrecognized"
        return f"{CATEGORY_LABEL[self.category]} {self.number}{self.panel}"

    @property
    def sort_key(self):
        n = self.number if self.number is not None else 10 ** 9
        return (self.category, n, self.panel, _natural_key(self.path.name))

    @property
    def size_text(self) -> str:
        if self.width_pt is None:
            return ""
        return f"{self.width_pt / 72:.2f} x {self.height_pt / 72:.2f} in"


def classify(filename: str):
    """Return (category, number, panel) for a file name. Unmatched names give (OTHER, None, "")."""
    s = _normalise(Path(filename).stem)
    for cat, rx in _PATTERNS:
        m = rx.search(s)
        if m:
            return cat, int(m.group(1)), m.group(2)
    return OTHER, None, ""


def find_ai_files(paths, recursive: bool = False, log: Callable[[str, str], None] = None) -> List[Path]:
    """Collect .ai files from dropped folders/files. Skips macOS '._' resource-fork files (they are not artwork)."""
    log = log or (lambda lvl, msg: None)
    out, seen = [], set()
    for p in map(Path, paths):
        if p.is_dir():
            cands = p.rglob("*") if recursive else p.iterdir()
        elif p.is_file():
            cands = [p]
        else:
            log("error", f"Not found: {p}")
            continue
        for f in cands:
            if not f.is_file() or f.suffix.lower() != ".ai":
                continue
            if f.name.startswith("._"):
                log("info", f"Skipped macOS metadata file {f.name}")
                continue
            r = f.resolve()
            if r not in seen:
                seen.add(r)
                out.append(f)
    return out


def build_figures(files: List[Path], log: Callable[[str, str], None] = None) -> List[Figure]:
    """Classify, sort, and report duplicates, gaps and unrecognized names."""
    log = log or (lambda lvl, msg: None)
    figs = []
    for f in files:
        cat, num, panel = classify(f.name)
        figs.append(Figure(path=f, category=cat, number=num, panel=panel))
    figs.sort(key=lambda g: g.sort_key)

    for g in figs:
        if g.category == OTHER:
            log("warn", f"{g.path.name}: no figure number recognised in the name; placed at the end (reorder by hand if needed)")
    by_key = {}
    for g in figs:
        if g.category != OTHER:
            by_key.setdefault((g.category, g.number, g.panel), []).append(g)
    for (cat, num, panel), gs in by_key.items():
        if len(gs) > 1:
            names = ", ".join(x.path.name for x in gs)
            log("warn", f"{CATEGORY_LABEL[cat]} {num}{panel} appears {len(gs)} times ({names}); all are included - "
                        f"untick the ones you don't want")
    for cat in (MAIN, EXTENDED, SUPP):
        nums = sorted({g.number for g in figs if g.category == cat})
        if nums:
            missing = sorted(set(range(1, nums[-1] + 1)) - set(nums))
            if missing:
                log("warn", f"{CATEGORY_LABEL[cat]} numbering has gaps: missing {', '.join(map(str, missing))}")
    return figs


# ---------------------------------------------------------------------------------------------------------------
# Opening / rendering
# ---------------------------------------------------------------------------------------------------------------
_NO_PDF_MARKERS = ("saved without pdf content", "without pdf compatib")


class AiError(Exception):
    pass


def _open_artboard1(path: Path):
    try:
        doc = fitz.open(str(path), filetype="pdf")
    except Exception as e:  # noqa: BLE001 - report whatever the library says
        raise AiError(f"cannot be opened as PDF-compatible Illustrator file ({e}). Re-save it in Illustrator with "
                      f"'Create PDF Compatible File' ticked.") from e
    if doc.page_count == 0:
        doc.close()
        raise AiError("contains no pages/artboards")
    if doc.needs_pass:
        doc.close()
        raise AiError("is password protected")
    page = doc[0]
    try:
        txt = page.get_text().lower()
    except Exception:  # noqa: BLE001
        txt = ""
    if any(m in txt for m in _NO_PDF_MARKERS):
        doc.close()
        raise AiError("was saved WITHOUT PDF compatibility, so artboard 1 cannot be rendered outside Illustrator. "
                      "Re-save it with 'Create PDF Compatible File' ticked.")
    return doc, page


def probe(fig: Figure) -> None:
    """Read artboard-1 size and page count, without rendering. Errors go into fig.probe_error."""
    try:
        doc, page = _open_artboard1(fig.path)
    except AiError as e:
        fig.probe_error = str(e)
        return
    r = page.rect
    fig.width_pt, fig.height_pt, fig.n_pages = r.width, r.height, doc.page_count
    doc.close()


def render_png(fig: Figure, out_dir: Path, dpi: int, transparent: bool, log) -> Path:
    doc, page = _open_artboard1(fig.path)
    try:
        r = page.rect
        w_px, h_px = round(r.width / 72 * dpi), round(r.height / 72 * dpi)
        mp = w_px * h_px / 1e6
        if mp > 150:
            log("warn", f"{fig.path.name}: {w_px} x {h_px} px ({mp:.0f} MP) at {dpi} dpi - large, may be slow / use a lot of memory")
        rot = page.rotation
        if rot:
            log("info", f"{fig.path.name}: artboard page has /Rotate {rot}; rendered as displayed")
        try:
            pix = page.get_pixmap(dpi=dpi, alpha=transparent, annots=False)
        except MemoryError as e:
            raise AiError(f"out of memory rendering {w_px} x {h_px} px; lower the DPI") from e
        out_dir.mkdir(parents=True, exist_ok=True)
        png = out_dir / (fig.path.stem + ".png")
        pix.save(str(png))
        fig.png_path = png
        return png
    finally:
        doc.close()


# ---------------------------------------------------------------------------------------------------------------
# PDF assembly
# ---------------------------------------------------------------------------------------------------------------
PAGE_SIZES_IN = {"US Letter (8.5 x 11 in)": (8.5, 11.0), "A4 (8.27 x 11.69 in)": (8.27, 11.69),
                 "Tabloid (11 x 17 in)": (11.0, 17.0)}
LARGEST = "Largest artboard (all pages same size)"
CUSTOM = "Custom"


@dataclasses.dataclass
class Settings:
    dpi: int = 300
    transparent: bool = False
    page_size: str = "US Letter (8.5 x 11 in)"
    custom_w_in: float = 8.5
    custom_h_in: float = 11.0
    margin_in: float = 0.5
    scale_mode: str = "fit"          # "fit": every figure scaled to fill the printable area; "shrink": only shrink oversize ones
    align: str = "top"               # "top" or "center" (vertical); always centred horizontally
    out_dir: Optional[Path] = None
    pdf_name: str = "figures.pdf"
    bookmarks: bool = True


def page_size_pt(figs: List[Figure], s: Settings):
    if s.page_size == LARGEST:
        w = max(f.width_pt for f in figs) + 2 * s.margin_in * 72
        h = max(f.height_pt for f in figs) + 2 * s.margin_in * 72
        return w, h
    if s.page_size == CUSTOM:
        return s.custom_w_in * 72, s.custom_h_in * 72
    w, h = PAGE_SIZES_IN[s.page_size]
    return w * 72, h * 72


def report_size_consistency(figs: List[Figure], log) -> None:
    """Flag artboards whose size differs a lot from the rest (text size will then differ page to page)."""
    ok = [f for f in figs if f.width_pt]
    if len(ok) < 2:
        return
    mw = statistics.median(f.width_pt for f in ok)
    mh = statistics.median(f.height_pt for f in ok)
    log("info", f"Median artboard 1 size: {mw / 72:.2f} x {mh / 72:.2f} in")
    for f in ok:
        dw, dh = f.width_pt / mw - 1, f.height_pt / mh - 1
        if abs(dw) > 0.10:
            log("warn", f"{f.path.name} ({f.label}): artboard width {f.width_pt / 72:.2f} in differs from median "
                        f"{mw / 72:.2f} in by {dw:+.0%} - it will be scaled differently, so its text size will not match")
        elif abs(dh) > 0.25:
            log("info", f"{f.path.name}: artboard height {f.height_pt / 72:.2f} in vs median {mh / 72:.2f} in")
        if f.width_pt > f.height_pt * 1.05:
            log("info", f"{f.path.name}: artboard is landscape ({f.size_text}); it is fitted on a portrait page")


def compile_pdf(figs: List[Figure], s: Settings, pdf_path: Path, log) -> Path:
    pw, ph = page_size_pt(figs, s)
    m = s.margin_in * 72
    aw, ah = pw - 2 * m, ph - 2 * m
    if aw <= 0 or ah <= 0:
        raise AiError("margins are larger than the page")
    log("info", f"PDF page size {pw / 72:.2f} x {ph / 72:.2f} in, margin {s.margin_in:g} in, scaling: "
                f"{'fit every figure to the page' if s.scale_mode == 'fit' else 'native size (shrink oversize only)'}")
    out = fitz.open()
    toc = []
    scales = []
    for f in figs:
        w, h = f.width_pt, f.height_pt
        k = min(aw / w, ah / h)
        if s.scale_mode == "shrink":
            k = min(k, 1.0)
        scales.append(k)
        dw, dh = w * k, h * k
        x0 = m + (aw - dw) / 2
        y0 = m if s.align == "top" else m + (ah - dh) / 2
        page = out.new_page(width=pw, height=ph)
        page.insert_image(fitz.Rect(x0, y0, x0 + dw, y0 + dh), filename=str(f.png_path), keep_proportion=True)
        eff = s.dpi / k
        note = f"scale {k:.0%}, effective {eff:.0f} dpi"
        if eff < 150:
            log("warn", f"Page {len(toc) + 1} {f.label} ({f.path.name}): enlarged to {k:.0%}, effective resolution only "
                        f"{eff:.0f} dpi - raise the DPI")
        else:
            log("info", f"Page {len(toc) + 1}: {f.label} - {f.path.name} ({note})")
        toc.append([1, f"{f.label} - {f.path.stem}" if f.category != OTHER else f.path.stem, len(toc) + 1])
    if s.bookmarks and toc:
        out.set_toc(toc)
    if len(set(round(k, 3) for k in scales)) > 1:
        lo, hi = min(scales), max(scales)
        if hi / lo > 1.15:
            log("warn", f"Figures are placed at different scales ({lo:.0%} to {hi:.0%}) because their artboards differ in "
                        f"size; page size is uniform, but text/line sizes will vary between pages. Using the same "
                        f"artboard width in Illustrator avoids this.")
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    out.save(str(pdf_path), garbage=3, deflate=True)
    out.close()
    return pdf_path


# ---------------------------------------------------------------------------------------------------------------
# Whole pipeline
# ---------------------------------------------------------------------------------------------------------------
def run(figs: List[Figure], s: Settings, log, progress=None, cancelled=lambda: False) -> Optional[Path]:
    """Render each included figure, then compile. Per-file errors are logged and that file is skipped."""
    progress = progress or (lambda i, n: None)
    todo = [f for f in figs if f.include]
    for f in figs:
        if not f.include:
            log("info", f"Skipped (unticked): {f.path.name}")
    if not todo:
        log("error", "No figures selected")
        return None
    out_dir = Path(s.out_dir)
    png_dir = out_dir / "png"
    log("info", f"Exporting {len(todo)} figure(s) at {s.dpi} dpi to {png_dir}")
    done, n_err = [], 0
    for i, f in enumerate(todo):
        if cancelled():
            log("warn", "Cancelled")
            return None
        progress(i, len(todo) + 1)
        try:
            if f.width_pt is None and not f.probe_error:
                probe(f)
            if f.probe_error:
                raise AiError(f.probe_error)
            if f.n_pages and f.n_pages > 1:
                log("info", f"{f.path.name}: file has {f.n_pages} pages/artboards; using artboard 1 only")
            render_png(f, png_dir, s.dpi, s.transparent, log)
            done.append(f)
            log("ok", f"{f.label}: {f.path.name} -> {f.png_path.name}")
        except AiError as e:
            n_err += 1
            log("error", f"{f.path.name}: {e}")
        except Exception as e:  # noqa: BLE001 - keep going with the other files
            n_err += 1
            log("error", f"{f.path.name}: unexpected error {type(e).__name__}: {e}")
    if not done:
        log("error", "No figure could be rendered; no PDF written")
        return None
    report_size_consistency(done, log)
    progress(len(todo), len(todo) + 1)
    pdf = out_dir / (s.pdf_name if s.pdf_name.lower().endswith(".pdf") else s.pdf_name + ".pdf")
    try:
        compile_pdf(done, s, pdf, log)
    except Exception as e:  # noqa: BLE001
        log("error", f"Could not write PDF: {type(e).__name__}: {e}")
        return None
    progress(len(todo) + 1, len(todo) + 1)
    lvl = "ok" if n_err == 0 else "warn"
    log(lvl, f"Wrote {pdf} ({len(done)} page(s); {n_err} file(s) failed)")
    return pdf


def write_log(out_dir: Path, lines: List[str]) -> Path:
    p = Path(out_dir) / "export_log.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


__all__ = ["Figure", "Settings", "classify", "find_ai_files", "build_figures", "probe", "run", "AiError",
           "PAGE_SIZES_IN", "LARGEST", "CUSTOM", "CATEGORY_LABEL", "write_log"]
