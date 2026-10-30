"""Every visual setting of a graph lives in :class:`Style`.

Fields carry GUI metadata (label, group, widget, limits) so the GUI is generated from the
dataclass - no setting can exist without a control. A project has one *theme* (a Style) and each
graph stores only its *overrides*; changing the theme restyles every graph (Prism-like consistency).
"""
from __future__ import annotations

import dataclasses as dc
import json
from typing import Any

PALETTES = {
    "Okabe-Ito (colour-blind safe)": ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#56B4E9", "#CC79A7", "#999999", "#000000"],
    "Tableau 10": ["#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F", "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC"],
    "Bold": ["#1B1B1B", "#E41A1C", "#377EB8", "#4DAF4A", "#984EA3", "#FF7F00", "#A65628", "#F781BF"],
    "Pastel": ["#A6CEE3", "#B2DF8A", "#FB9A99", "#FDBF6F", "#CAB2D6", "#FFFF99", "#B15928", "#CCCCCC"],
    "Grayscale": ["#000000", "#555555", "#8C8C8C", "#B8B8B8", "#DDDDDD"],
    "Viridis-like": ["#440154", "#3B528B", "#21908C", "#5DC863", "#FDE725"],
}
MARKERS = ["o", "s", "^", "v", "D", "P", "X", "*", "<", ">", "h", "+", "x"]
HATCHES = ["", "//", "\\\\", "xx", "..", "++", "--", "oo"]
_FONT_CANDIDATES = ["DejaVu Sans", "Helvetica", "Arial", "Liberation Sans", "Calibri", "Times New Roman", "Liberation Serif", "DejaVu Serif", "Courier New", "DejaVu Sans Mono"]


def register_fonts():
    """Make fonts in plotsuite/fonts/ (bundled Nimbus Sans) and in $PLOTSUITE_FONTS (e.g. your own licensed Helvetica .otf/.ttf) available."""
    import os
    import pathlib
    from matplotlib import font_manager
    dirs = [pathlib.Path(__file__).parent / "fonts"] + [pathlib.Path(d) for d in os.environ.get("PLOTSUITE_FONTS", "").split(os.pathsep) if d]
    for d in dirs:
        for f in sorted(d.glob("*")) if d.is_dir() else []:
            if f.suffix.lower() in (".otf", ".ttf"):
                try:
                    font_manager.fontManager.addfont(str(f))
                except Exception:
                    pass


register_fonts()

# Helvetica is commercial and often missing; these are metric-compatible stand-ins (same glyph widths, so layouts don't shift).
FONT_ALIASES = {"Helvetica": ["Helvetica", "Helvetica Neue", "Nimbus Sans", "Arial", "Liberation Sans", "Nimbus Sans L"]}


def resolve_font(name: str) -> str | None:
    """First installed font for `name` (or one of its aliases); None if nothing is installed."""
    from matplotlib import font_manager
    for f in FONT_ALIASES.get(name, [name]):
        try:
            font_manager.findfont(f, fallback_to_default=False)
            return f
        except Exception:
            pass
    return None


def _installed_fonts() -> list[str]:
    """Only offer fonts that matplotlib can actually find (otherwise choosing one silently does nothing)."""
    out = [f for f in _FONT_CANDIDATES if resolve_font(f)]
    return out or ["DejaVu Sans"]


FONTS = _installed_fonts()
LEGEND_LOCS = ["none", "best", "upper right", "upper left", "lower right", "lower left", "center right", "outside right"]


def F(default, label, group, widget=None, choices=None, lo=None, hi=None, step=None, help=""):
    if widget is None:
        widget = {bool: "bool", int: "int", float: "float", str: "text"}.get(type(default), "text")
    return dc.field(default=default, metadata=dict(label=label, group=group, widget=widget, choices=choices, lo=lo, hi=hi, step=step, help=help))


@dc.dataclass
class Style:
    # ---- figure ---------------------------------------------------------------
    width: float = F(3.4, "Width (inches)", "Figure", lo=1.0, hi=20.0, step=0.1)
    height: float = F(3.0, "Height (inches)", "Figure", lo=1.0, hi=20.0, step=0.1)
    dpi: int = F(300, "Export DPI", "Figure", lo=72, hi=1200, step=50)
    auto_width: bool = F(False, "Fixed height, width set by number of bars", "Figure", help="Height = 'Height' below; the plot area is (bars x slot width) wide and the figure width follows. Gives identical bar widths and heights across graphs.")
    bar_slot: float = F(0.2, "Width per bar slot (inches)", "Figure", lo=0.05, hi=2.0, step=0.01)
    margin_left: float = F(0.55, "Left margin (inches)", "Figure", lo=0.0, hi=3.0, step=0.05)
    margin_right: float = F(0.1, "Right margin (inches)", "Figure", lo=0.0, hi=3.0, step=0.05)
    margin_bottom: float = F(0.45, "Bottom margin (inches)", "Figure", lo=0.0, hi=3.0, step=0.05)
    margin_top: float = F(0.1, "Top margin (inches)", "Figure", lo=0.0, hi=3.0, step=0.05)
    background: str = F("white", "Background", "Figure", "select", ["white", "transparent"])
    font_family: str = F("DejaVu Sans", "Font", "Figure", "select", FONTS)
    title_size: float = F(10.0, "Title size (pt)", "Figure", lo=4.0, hi=40.0, step=0.5)
    label_size: float = F(9.0, "Axis label size (pt)", "Figure", lo=4.0, hi=40.0, step=0.5)
    tick_size: float = F(8.0, "Tick label size (pt)", "Figure", lo=4.0, hi=40.0, step=0.5)
    legend_size: float = F(8.0, "Legend size (pt)", "Figure", lo=4.0, hi=40.0, step=0.5)
    bold_labels: bool = F(False, "Bold axis labels", "Figure")
    title: str = F("", "Title", "Figure")
    # ---- axes -----------------------------------------------------------------
    x_label: str = F("", "X axis title", "Axes")
    y_label: str = F("", "Y axis title", "Axes")
    y_min: Any = F(None, "Y minimum (blank = auto)", "Axes", "opt_float")
    y_max: Any = F(None, "Y maximum (blank = auto)", "Axes", "opt_float")
    y_scale: str = F("linear", "Y scale", "Axes", "select", ["linear", "log", "symlog"])
    y_major_step: Any = F(None, "Y major tick interval (blank = auto)", "Axes", "opt_float")
    y_decimals: Any = F(None, "Y tick decimals (blank = auto)", "Axes", "opt_float")
    x_min: Any = F(None, "X minimum (XY graphs)", "Axes", "opt_float")
    x_max: Any = F(None, "X maximum (XY graphs)", "Axes", "opt_float")
    x_scale: str = F("linear", "X scale (XY graphs)", "Axes", "select", ["linear", "log", "symlog"])
    x_major_step: Any = F(None, "X major tick interval (XY graphs)", "Axes", "opt_float")
    x_tick_rotation: float = F(0.0, "X tick label rotation", "Axes", lo=0.0, hi=90.0, step=5.0)
    spine_width: float = F(1.0, "Axis line width", "Axes", lo=0.0, hi=5.0, step=0.1)
    hide_top_right: bool = F(True, "Hide top/right axes", "Axes")
    spine_offset: float = F(0.0, "Detach axes (points)", "Axes", lo=0.0, hi=30.0, step=1.0)
    tick_dir: str = F("out", "Tick direction", "Axes", "select", ["out", "in", "inout"])
    tick_pad: float = F(3.5, "Gap between tick and tick label (pt)", "Axes", lo=0.0, hi=15.0, step=0.5)
    label_pad: float = F(4.0, "Gap between tick labels and axis title (pt)", "Axes", lo=0.0, hi=20.0, step=0.5)
    rot_label_gap: float = F(1.5, "Clear gap between tick end and rotated label (pt; 1.5 pt = 2 px at 96 dpi)", "Axes", lo=0.0, hi=10.0, step=0.25)
    tick_len: float = F(3.5, "Tick length", "Axes", lo=0.0, hi=15.0, step=0.5)
    tick_width: float = F(1.0, "Tick width", "Axes", lo=0.0, hi=5.0, step=0.1)
    minor_ticks: bool = F(False, "Minor ticks", "Axes")
    grid: str = F("none", "Grid lines", "Axes", "select", ["none", "y", "x", "both"])
    grid_alpha: float = F(0.25, "Grid opacity", "Axes", lo=0.0, hi=1.0, step=0.05)
    # ---- colours --------------------------------------------------------------
    palette: str = F("Okabe-Ito (colour-blind safe)", "Palette (used for groups without a fixed colour)", "Colours", "select", list(PALETTES))
    fill_alpha: float = F(0.85, "Fill opacity", "Colours", lo=0.0, hi=1.0, step=0.05)
    bar_filled: bool = F(True, "Filled bars/boxes (off = outlines)", "Colours")
    bar_color: str = F("group", "Column bar/box colour: 'group' or a hex like #CCCCCC", "Colours", help="'group' = each group's own colour; a hex colour fills every column bar/box the same (points keep group colours).")
    error_color: str = F("auto", "Error-bar colour: 'auto' or a hex like #333333", "Colours")
    # ---- column graphs --------------------------------------------------------
    column_mode: str = F("bar", "Column graph style", "Column graphs", "select", ["bar", "box", "violin", "points only"])
    bar_width: float = F(0.6, "Bar/box width", "Column graphs", lo=0.1, hi=1.0, step=0.05)
    bar_edge_width: float = F(1.0, "Bar edge width", "Column graphs", lo=0.0, hi=5.0, step=0.1)
    bar_hatch: str = F("", "Bar hatch pattern", "Column graphs", "select", HATCHES)
    show_points: bool = F(True, "Show individual data points", "Column graphs")
    point_layout: str = F("jitter", "Point layout", "Column graphs", "select", ["jitter", "swarm", "aligned"])
    jitter_width: float = F(0.18, "Jitter / swarm spread", "Column graphs", lo=0.0, hi=0.5, step=0.01)
    point_size: float = F(18.0, "Point size", "Column graphs", lo=1.0, hi=200.0, step=1.0)
    point_marker: str = F("o", "Point shape", "Column graphs", "select", MARKERS)
    point_alpha: float = F(0.9, "Point opacity", "Column graphs", lo=0.0, hi=1.0, step=0.05)
    point_edge_width: float = F(0.6, "Point edge width", "Column graphs", lo=0.0, hi=3.0, step=0.1)
    point_edge: str = F("black", "Point edge colour", "Column graphs", "select", ["black", "match", "white", "darker"])
    zero_line: bool = F(False, "Draw a line at Y = 0", "Column graphs")
    paired_lines: bool = F(True, "Connect matched points (paired tables)", "Column graphs")
    center: str = F("mean", "Central line", "Column graphs", "select", ["mean", "median", "none"])
    center_width: float = F(0.6, "Central line width (fraction of bar)", "Column graphs", lo=0.1, hi=1.5, step=0.05)
    center_lw: float = F(1.6, "Central line thickness", "Column graphs", lo=0.0, hi=6.0, step=0.1)
    error: str = F("SD", "Error bars", "Column graphs", "select", ["SD", "SEM", "CI95", "range", "IQR", "none"])
    cap_size: float = F(3.0, "Error-bar cap size", "Column graphs", lo=0.0, hi=15.0, step=0.5)
    error_lw: float = F(1.2, "Error-bar thickness", "Column graphs", lo=0.0, hi=5.0, step=0.1)
    whisker_rule: str = F("1.5 IQR (Tukey)", "Box whiskers", "Column graphs", "select", ["1.5 IQR (Tukey)", "min to max", "5th to 95th percentile"])
    show_fliers: bool = F(True, "Show box outliers", "Column graphs")
    box_notch: bool = F(False, "Notched boxes", "Column graphs")
    violin_bw: float = F(0.5, "Violin smoothing (bandwidth factor)", "Column graphs", lo=0.1, hi=2.0, step=0.05)
    violin_inner: str = F("quartiles", "Violin inner marks", "Column graphs", "select", ["quartiles", "box", "none"])
    # ---- grouped / XY ---------------------------------------------------------
    grouped_mode: str = F("bars", "Grouped graph style", "Grouped & XY graphs", "select", ["bars", "lines"])
    group_gap: float = F(0.15, "Gap between bar clusters", "Grouped & XY graphs", lo=0.0, hi=0.8, step=0.05)
    line_width: float = F(1.5, "Line width", "Grouped & XY graphs", lo=0.0, hi=6.0, step=0.1)
    line_style: str = F("-", "Line style", "Grouped & XY graphs", "select", ["-", "--", ":", "-."])
    connect_points: bool = F(False, "Connect data points", "Grouped & XY graphs")
    marker_size: float = F(5.0, "Marker size", "Grouped & XY graphs", lo=0.0, hi=20.0, step=0.5)
    show_replicate_points: bool = F(False, "Show replicate points (XY)", "Grouped & XY graphs")
    xy_error: str = F("SD", "Error bars for replicate Y (XY)", "Grouped & XY graphs", "select", ["SD", "SEM", "CI95", "none"])
    fit_line_width: float = F(1.8, "Fitted-curve width", "Grouped & XY graphs", lo=0.0, hi=6.0, step=0.1)
    show_fit_ci: bool = F(True, "Show regression confidence band", "Grouped & XY graphs")
    show_ci_band: bool = F(False, "Show survival confidence bands", "Grouped & XY graphs")
    show_censor: bool = F(True, "Show censor ticks (survival)", "Grouped & XY graphs")
    # ---- legend ---------------------------------------------------------------
    legend_loc: str = F("none", "Legend", "Legend", "select", LEGEND_LOCS)
    legend_frame: bool = F(False, "Legend frame", "Legend")
    legend_spacing: float = F(1.0, "Legend tightness (1 = normal, smaller = tighter)", "Legend", lo=0.1, hi=2.0, step=0.1)
    # ---- significance ---------------------------------------------------------
    sig_label: str = F("stars", "Significance label", "Significance brackets", "select", ["stars", "p-value", "both"])
    sig_only_significant: bool = F(False, "Only show significant comparisons", "Significance brackets")
    sig_size: float = F(9.0, "Label size", "Significance brackets", lo=4.0, hi=30.0, step=0.5)
    sig_lw: float = F(1.0, "Bracket line width", "Significance brackets", lo=0.0, hi=4.0, step=0.1)
    sig_step: float = F(0.09, "Vertical spacing (fraction of axis)", "Significance brackets", lo=0.02, hi=0.4, step=0.01)
    sig_ns: bool = F(True, "Label non-significant comparisons", "Significance brackets")
    sig_ns_label: str = F("ns", "Text for non-significant (ns / NS / n.s.)", "Significance brackets")

    def merged(self, overrides: dict | None) -> "Style":
        valid = {f.name for f in dc.fields(self)}
        return dc.replace(self, **{k: v for k, v in (overrides or {}).items() if k in valid})

    def to_dict(self) -> dict:
        return dc.asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Style":
        valid = {f.name for f in dc.fields(Style)}
        return Style(**{k: v for k, v in (d or {}).items() if k in valid})


def field_meta():
    return [(f.name, f.metadata) for f in dc.fields(Style)]


def diff_from(a: "Style", b: "Style") -> dict:
    """Fields where a differs from b."""
    return {f.name: getattr(a, f.name) for f in dc.fields(a) if getattr(a, f.name) != getattr(b, f.name)}


# Sensible starting points; saved presets in a project use the same JSON format.
PRESETS = {
    "Default (single column, 3.4 in)": {},
    "Wide (double column, 7 in)": {"width": 7.0, "height": 3.2},
    "Presentation": {"width": 6.0, "height": 4.5, "title_size": 18, "label_size": 16, "tick_size": 14, "legend_size": 14, "spine_width": 2.0,
                     "tick_width": 2.0, "tick_len": 6, "line_width": 2.5, "error_lw": 2.0, "bar_edge_width": 2.0, "point_size": 45, "sig_size": 16, "dpi": 200},
    "Poster": {"width": 8.0, "height": 6.0, "title_size": 26, "label_size": 22, "tick_size": 20, "legend_size": 20, "spine_width": 3.0, "tick_width": 3.0,
               "tick_len": 9, "line_width": 3.5, "error_lw": 3.0, "bar_edge_width": 3.0, "point_size": 80, "sig_size": 22, "dpi": 150},
    # Measured from Fig. 5 of "Developmental xenocortication using human-derived organoids in mice" (Nature 658, 2026) plus your specs
    # (Helvetica; 6 pt numbers, 7 pt other text; tight spacing; #CCCCCC bars, #333333 error bars, short thin ticks/axes). Bar width, point size, error-bar weight were estimated by eye.
    "Xenocortical Nature style": {"font_family": "Helvetica", "title_size": 7, "label_size": 7, "tick_size": 6, "legend_size": 7, "sig_size": 7, "tick_pad": 1.0, "label_pad": 1.5, "rot_label_gap": 1.5, "legend_spacing": 0.35,
                                  "auto_width": True, "height": 1.3, "bar_slot": 0.14, "margin_left": 0.45, "margin_bottom": 0.4, "margin_top": 0.1, "margin_right": 0.08, "dpi": 600, "column_mode": "bar", "bar_color": "#CCCCCC", "error_color": "#333333",
                                  "bar_edge_width": 0.0, "bar_width": 0.65, "bar_filled": True, "fill_alpha": 1.0, "error": "SEM", "cap_size": 1.5,
                                  "error_lw": 0.75, "spine_width": 0.5, "tick_width": 0.5, "tick_len": 2.0, "tick_dir": "out", "x_tick_rotation": 45.0,
                                  "hide_top_right": True, "show_points": True, "point_layout": "jitter", "jitter_width": 0.12, "point_size": 5.0,
                                  "point_alpha": 1.0, "point_edge": "match", "point_edge_width": 0.0, "zero_line": True, "sig_lw": 0.5,
                                  "sig_ns_label": "NS", "sig_step": 0.1, "palette": "Bold"},
    "Minimal grayscale": {"palette": "Grayscale", "bar_filled": True, "fill_alpha": 1.0, "point_edge": "black"},
}


def dumps(s: Style) -> str:
    return json.dumps(s.to_dict(), indent=2)
