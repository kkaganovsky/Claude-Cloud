"""Project = datasets + analyses + graphs + one shared theme (the consistency engine)."""
from __future__ import annotations

import dataclasses as dc
import json
import uuid

from . import stats as S
from .data import Dataset
from .style import PALETTES, MARKERS, Style, diff_from

GRAPH_KINDS = {
    "column": ("column", "Column graph (bars / box / violin / points)"),
    "grouped": ("grouped", "Grouped graph (two factors)"),
    "xy": ("xy", "XY graph (points, lines, fitted curves)"),
    "survival": ("survival", "Survival curves (Kaplan-Meier)"),
    "histogram": ("column", "Histogram / frequency distribution"),
    "heatmap": ("column", "Heat map"),
    "stacked": ("contingency", "Stacked bars (contingency)"),
    "bland_altman": ("column", "Bland-Altman plot"),
    "roc": ("column", "ROC curve"),
}


def uid() -> str:
    return uuid.uuid4().hex[:8]


@dc.dataclass
class GraphSpec:
    name: str
    dataset: str
    kind: str
    params: dict = dc.field(default_factory=dict)
    overrides: dict = dc.field(default_factory=dict)
    analysis_ids: list = dc.field(default_factory=list)  # analyses whose comparisons/fits are drawn
    id: str = dc.field(default_factory=uid)


@dc.dataclass
class AnalysisSpec:
    name: str
    dataset: str
    test: str
    options: dict = dc.field(default_factory=dict)
    id: str = dc.field(default_factory=uid)


@dc.dataclass
class Project:
    name: str = "Untitled project"
    datasets: dict = dc.field(default_factory=dict)
    graphs: list = dc.field(default_factory=list)
    analyses: list = dc.field(default_factory=list)
    theme: Style = dc.field(default_factory=Style)
    group_styles: dict = dc.field(default_factory=dict)  # group name -> {color, marker, hatch}
    presets: dict = dc.field(default_factory=dict)  # name -> Style dict (saved formats)
    layouts: list = dc.field(default_factory=list)

    # ---- lookups -------------------------------------------------------------
    def graph(self, gid) -> GraphSpec:
        return next(g for g in self.graphs if g.id == gid)

    def analysis(self, aid) -> AnalysisSpec:
        return next(a for a in self.analyses if a.id == aid)

    def effective_style(self, g: GraphSpec) -> Style:
        return self.theme.merged(g.overrides)

    # ---- consistency ---------------------------------------------------------
    def style_for_group(self, name: str, palette: str | None = None) -> dict:
        """Colour/marker/hatch for a group NAME - identical in every graph of the project.

        A group's colour is explicit only if the user picked one; otherwise it follows the palette by the
        group's first-seen order, so changing the palette restyles every automatically-coloured group.
        """
        st_ = self.group_styles.setdefault(name, {"color": None, "marker": None, "hatch": None, "order": len(self.group_styles)})
        if "order" not in st_:
            st_["order"] = len(self.group_styles) - 1
        cols = PALETTES[palette or self.theme.palette]
        return {**st_, "color": st_["color"] or cols[st_["order"] % len(cols)]}

    def gs(self, style: Style):
        """Group-style resolver bound to a graph's effective style (so a graph-specific palette is honoured)."""
        return lambda n: self.style_for_group(n, style.palette)

    def set_group_style(self, name: str, **kw):
        self.style_for_group(name)
        self.group_styles[name].update({k: v for k, v in kw.items() if v is not None or k in ("marker", "hatch", "color")})

    def recolor_all(self, palette: str):
        """Hand colours back to the palette: clears every explicit colour and makes `palette` the theme palette."""
        for v in self.group_styles.values():
            v["color"] = None
        self.theme = self.theme.merged({"palette": palette})
        for g in self.graphs:
            g.overrides.pop("palette", None)

    def set_theme(self, **kw):
        """Change a theme field; per-graph overrides of those fields are dropped so all graphs follow."""
        self.theme = self.theme.merged(kw)
        for g in self.graphs:
            for k in kw:
                g.overrides.pop(k, None)

    def copy_format(self, src_id: str, dst_ids: list[str]):
        src = self.graph(src_id)
        for d in dst_ids:
            self.graph(d).overrides = dict(src.overrides)

    def match_axes(self, src_id: str, dst_ids: list[str], fields=("y_min", "y_max", "y_scale", "y_major_step", "width", "height")):
        eff = self.effective_style(self.graph(src_id))
        for d in dst_ids:
            g = self.graph(d)
            for f in fields:
                g.overrides[f] = getattr(eff, f)

    def reset_overrides(self, gid: str, fields=None):
        g = self.graph(gid)
        if fields is None:
            g.overrides.clear()
        else:
            for f in fields:
                g.overrides.pop(f, None)

    def promote_to_theme(self, gid: str):
        """Make a graph's current look the project theme and drop all graph overrides (apply-to-all)."""
        g = self.graph(gid)
        self.theme = self.effective_style(g)
        for x in self.graphs:
            x.overrides.clear()

    def save_preset(self, name: str, gid: str | None = None):
        self.presets[name] = (self.effective_style(self.graph(gid)) if gid else self.theme).to_dict()

    def apply_preset(self, name: str, gid: str | None = None):
        s = Style.from_dict(self.presets[name])
        if gid is None:
            self.theme = s
            for g in self.graphs:
                g.overrides.clear()
        else:
            self.graph(gid).overrides = diff_from(s, self.theme)

    # ---- analyses ------------------------------------------------------------
    def run_analysis(self, a: AnalysisSpec) -> S.Result:
        return S.run(a.test, self.datasets[a.dataset], a.options)

    # ---- persistence ---------------------------------------------------------
    def to_json(self) -> str:
        return json.dumps({
            "format": "plotsuite-project", "version": 1, "name": self.name,
            "datasets": [d.to_dict() for d in self.datasets.values()],
            "graphs": [dc.asdict(g) for g in self.graphs], "analyses": [dc.asdict(a) for a in self.analyses],
            "theme": self.theme.to_dict(), "group_styles": self.group_styles, "presets": self.presets, "layouts": self.layouts,
        }, indent=1, default=_np_default)

    @staticmethod
    def from_json(text: str) -> "Project":
        d = json.loads(text)
        if d.get("format") != "plotsuite-project":
            raise ValueError("not a plotsuite project file")
        p = Project(name=d.get("name", "Project"))
        for dd in d["datasets"]:
            ds = Dataset.from_dict(dd)
            p.datasets[ds.name] = ds
        p.graphs = [GraphSpec(**g) for g in d.get("graphs", [])]
        p.analyses = [AnalysisSpec(**a) for a in d.get("analyses", [])]
        p.theme = Style.from_dict(d.get("theme", {}))
        p.group_styles = d.get("group_styles", {})
        p.presets = d.get("presets", {})
        p.layouts = d.get("layouts", [])
        return p


def _np_default(o):
    import numpy as np
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))
