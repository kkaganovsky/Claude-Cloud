import dataclasses as dc

import numpy as np
import pandas as pd
import pytest

from plotsuite import plots, stats as S
from plotsuite.data import Dataset
from plotsuite.examples import example_project
from plotsuite.project import GraphSpec, Project
from plotsuite.style import PALETTES, PRESETS, Style, field_meta


def png(fig, dpi=60):
    return plots.export(fig, "png", dpi)


def render(p, g):
    comps, res = plots.analyses_for(p, g)
    st_ = p.effective_style(g)
    return plots.render(g, p.datasets[g.dataset], st_, p.gs(st_), comps, res)


def test_every_graph_kind_renders_without_error():
    p = example_project()
    rng = np.random.default_rng(0)
    p.datasets["m"] = Dataset("m", "column", pd.DataFrame({"a": rng.normal(5, 1, 30), "b": rng.normal(6, 1, 30), "c": rng.normal(7, 2, 30)}), paired=True)
    extra = [GraphSpec("hist", "m", "histogram", {"bins": 8, "stat": "percent"}), GraphSpec("heat", "m", "heatmap", {"annotate": False, "center_zero": True}),
             GraphSpec("ba", "m", "bland_altman", {"group_a": "a", "group_b": "b"}), GraphSpec("roc", "m", "roc", {"group_a": "a", "group_b": "b"}),
             GraphSpec("stk", "Responders", "stacked", {"percent": False})]
    for mode in ["bar", "box", "violin", "points only"]:
        extra.append(GraphSpec(f"col-{mode}", "m", "column", {}, {"column_mode": mode}))
    for g in p.graphs + extra:
        f = render(p, g)
        assert not hasattr(f, "_ps_error"), (g.name, getattr(f, "_ps_error", None))
        assert len(png(f)) > 2000


def test_bad_graph_shows_message_not_traceback():
    p = Project(); p.datasets["x"] = Dataset("x", "column", pd.DataFrame({"a": [1.0, 1.0, 1.0], "b": [2.0, 2.0, 2.0]}))
    g = GraphSpec("v", "x", "column", {}, {"column_mode": "violin"}); p.graphs.append(g)
    f = render(p, g)
    assert "violin" in f._ps_error.lower()


def test_export_formats():
    p = example_project(); f = render(p, p.graphs[0])
    assert plots.export(f, "png", 100)[:8] == b"\x89PNG\r\n\x1a\n"
    assert plots.export(f, "pdf")[:4] == b"%PDF"
    svg = plots.export(f, "svg").decode()
    assert "<svg" in svg and "<text" in svg  # text stays editable (svg.fonttype none)
    assert plots.export(f, "tiff", 80)[:2] in (b"II", b"MM")
    big = len(plots.export(f, "png", 300)); small = len(plots.export(f, "png", 72))
    assert big > small


def test_style_metadata_is_gui_safe():
    """Streamlit number_input raises if value is outside [min,max]; selects need default in choices."""
    s = Style()
    for name, m in field_meta():
        v = getattr(s, name)
        assert m["label"] and m["group"] and m["widget"] in ("bool", "int", "float", "select", "text", "opt_float"), name
        if m["widget"] == "select":
            assert v in m["choices"], name
        if m["widget"] in ("int", "float"):
            assert m["lo"] <= v <= m["hi"] and m["step"], name


def test_no_dead_settings_every_control_changes_some_graph():
    """Each Style field exposed in the GUI must visibly alter at least one graph type."""
    p = example_project()
    rng = np.random.default_rng(1)
    p.datasets["m"] = Dataset("m", "column", pd.DataFrame({"a": rng.normal(5, 1, 20), "b": rng.normal(6, 1, 20)}), paired=True)
    m2 = pd.DataFrame({"a": rng.normal(5, 1, 20), "b": rng.normal(6, 1, 20)}); m2.loc[0, "a"] = 12.0  # an outlier so whisker/flier settings matter
    p.datasets["m2"] = Dataset("m2", "column", m2)
    p.datasets["neg"] = Dataset("neg", "column", pd.DataFrame({"a": rng.normal(0, 1, 12), "b": rng.normal(-1, 1, 12)}))  # negatives, so a Y=0 line is visible
    graphs = [GraphSpec("negbars", "neg", "column", {}, {"column_mode": "bar"}), GraphSpec("c-bar", "m2", "column", {}, {"column_mode": "bar", "show_points": True, "legend_loc": "upper right", "title": "T", "grid": "both", "auto_width": True, "bar_color": "#CCCCCC", "error_color": "#333333", "zero_line": False}),
              GraphSpec("c-box", "m2", "column", {}, {"column_mode": "box"}), GraphSpec("c-vio", "m2", "column", {}, {"column_mode": "violin"}),
              p.graphs[0],
              GraphSpec("c-pts", "m2", "column", {}, {"column_mode": "points only"}),
              GraphSpec("paired", "m", "column", {}, {"column_mode": "points only"}),
              p.graphs[2], GraphSpec("grp-lines", "Genotype x time", "grouped", {"x_factor": "b"}, {"grouped_mode": "lines", "show_points": True}),
              p.graphs[3], p.graphs[4], GraphSpec("xyrep", "Dose-response", "xy", {}, {"connect_points": True}), GraphSpec("lines", "Genotype x time", "grouped", {}, {})]
    p.graphs += graphs

    def alt(name, meta, v):
        w = meta["widget"]
        if w == "bool":
            return not v
        if w == "select":
            return next(c for c in meta["choices"] if c != v and not (name == "legend_loc" and c == "none") and not (name == "y_scale" and c == "symlog"))
        if w in ("int", "float"):
            return type(v)(meta["lo"] + (meta["hi"] - meta["lo"]) * (0.2 if v > (meta["lo"] + meta["hi"]) / 2 else 0.8))
        if w == "opt_float":
            return {"y_min": -3.0, "y_max": 40.0, "y_major_step": 7.0, "y_decimals": 3.0, "x_min": 0.001, "x_max": 5.0, "x_major_step": 3.0}[name]
        return {"bar_color": "#FF0000", "error_color": "#00AA00", "sig_ns_label": "NS"}.get(name, "TEXT")
    baseline = {g.id: png(render(p, g)) for g in graphs}
    skip = {"dpi"}  # only used at export time
    dead = []
    for name, meta in field_meta():
        if name in skip:
            continue
        v = getattr(p.theme, name)
        changed = False
        for g in graphs:
            old = dict(g.overrides)
            ov = {name: alt(name, meta, g.overrides.get(name, v))}
            g.overrides = {**old, **ov}
            try:
                if png(render(p, g)) != baseline[g.id]:
                    changed = True
            finally:
                g.overrides = old
            if changed:
                break
        if not changed:
            dead.append(name)
    assert not dead, f"settings with no visible effect on any graph: {dead}"


def test_theme_change_restyles_all_graphs_and_clears_overrides():
    p = example_project()
    assert p.graphs[1].overrides.get("column_mode") == "points only"
    p.set_theme(column_mode="box", tick_size=11.0)
    assert all(p.effective_style(g).tick_size == 11.0 for g in p.graphs)
    assert p.graphs[1].overrides.get("column_mode") is None and p.effective_style(p.graphs[1]).column_mode == "box"
    a = png(render(p, p.graphs[0])); p.set_theme(tick_size=14.0); assert png(render(p, p.graphs[0])) != a


def test_graph_override_does_not_leak():
    p = example_project()
    p.graphs[0].overrides["width"] = 6.0
    assert p.effective_style(p.graphs[0]).width == 6.0 and p.effective_style(p.graphs[1]).width == p.theme.width


def test_group_colours_are_consistent_across_graphs_and_datasets():
    p = Project()
    p.datasets["one"] = Dataset("one", "column", pd.DataFrame({"Ctrl": [1.0, 2, 3], "Drug": [2.0, 3, 4]}))
    p.datasets["two"] = Dataset("two", "column", pd.DataFrame({"Drug": [5.0, 6, 7], "Ctrl": [1.0, 2, 3], "Extra": [3.0, 3.5, 4]}))
    g1, g2 = GraphSpec("a", "one", "column"), GraphSpec("b", "two", "column"); p.graphs += [g1, g2]
    for g in (g1, g2):
        render(p, g)
    assert list(p.group_styles) == ["Ctrl", "Drug", "Extra"]  # first-seen order, shared by both tables
    c_ctrl = p.style_for_group("Ctrl")["color"]
    assert c_ctrl == PALETTES[p.theme.palette][0] and p.style_for_group("Drug")["color"] == PALETTES[p.theme.palette][1]
    # same name -> same colour in both graphs, even though the column order differs between tables
    p.set_group_style("Drug", color="#123456", marker="s")
    assert p.style_for_group("Drug")["color"] == "#123456" and p.style_for_group("Drug")["marker"] == "s"
    # auto colours follow the palette; explicit ones do not
    p.set_theme(palette="Grayscale")
    assert p.style_for_group("Ctrl")["color"] == PALETTES["Grayscale"][0] and p.style_for_group("Drug")["color"] == "#123456"
    p.recolor_all("Tableau 10")
    assert p.style_for_group("Drug")["color"] == PALETTES["Tableau 10"][1] and p.theme.palette == "Tableau 10"
    assert len({p.style_for_group(k)["color"] for k in p.group_styles}) == 3
    # a graph-specific palette is honoured for auto colours
    p.graphs[0].overrides["palette"] = "Pastel"
    assert render(p, p.graphs[0]).axes[0].patches[0].get_facecolor()[:3] != render(p, g2).axes[0].patches[0].get_facecolor()[:3]


def test_copy_format_match_axes_promote_reset_presets():
    p = example_project()
    a, b, c = p.graphs[0], p.graphs[1], p.graphs[2]
    a.overrides.update({"y_min": 0.0, "y_max": 30.0, "y_scale": "linear", "width": 4.4, "tick_size": 12.0})
    p.match_axes(a.id, [b.id, c.id], ("y_min", "y_max", "width"))
    assert p.effective_style(b).y_max == 30.0 and p.effective_style(c).width == 4.4 and p.effective_style(c).tick_size == p.theme.tick_size
    p.copy_format(a.id, [c.id])
    assert c.overrides == a.overrides
    p.promote_to_theme(a.id)
    assert p.theme.tick_size == 12.0 and all(g.overrides == {} for g in p.graphs)
    assert all(p.effective_style(g).y_max == 30.0 for g in p.graphs)
    b.overrides["y_max"] = 99.0; p.reset_overrides(b.id, ["y_max"]); assert "y_max" not in b.overrides
    p.save_preset("lab style"); p.set_theme(tick_size=6.0); p.apply_preset("lab style"); assert p.theme.tick_size == 12.0
    p.apply_preset("lab style", gid=c.id); assert c.overrides == {}
    for name, ov in PRESETS.items():
        s = Style().merged(ov); assert s.width > 0


def test_project_roundtrip_preserves_everything_and_analyses_recompute():
    p = example_project()
    p.set_theme(label_size=11.0); p.graphs[0].overrides["y_max"] = 33.0; p.set_group_style("Vehicle", color="#abcdef"); p.save_preset("mine")
    p.layouts.append({"rows": 1, "cols": 2, "panels": [p.graphs[0].id, p.graphs[1].id]})
    q = Project.from_json(p.to_json())
    assert q.theme == p.theme and [g.overrides for g in q.graphs] == [g.overrides for g in p.graphs] and q.group_styles == p.group_styles
    assert q.presets == p.presets and q.layouts == p.layouts and list(q.datasets) == list(p.datasets)
    for n in p.datasets:
        pd.testing.assert_frame_equal(p.datasets[n].df.reset_index(drop=True), q.datasets[n].df.reset_index(drop=True), check_dtype=False)
        assert p.datasets[n].roles == q.datasets[n].roles and p.datasets[n].paired == q.datasets[n].paired
    for a, a2 in zip(p.analyses, q.analyses):
        r1, r2 = p.run_analysis(a), q.run_analysis(a2)
        assert r1.summary == r2.summary
    assert png(render(p, p.graphs[0])) == png(render(q, q.graphs[0]))
    with pytest.raises(ValueError):
        Project.from_json('{"format": "other"}')


def test_brackets_come_from_the_attached_analysis_and_follow_the_data():
    p = example_project()
    g = p.graphs[0]
    with_b = png(render(p, g)); g.analysis_ids = []; without = png(render(p, g))
    assert with_b != without
    g.analysis_ids = [p.analyses[0].id]
    p.datasets["Dose groups"].df["Vehicle"] = p.datasets["Dose groups"].df["High dose"] + 0.01  # now Vehicle ~ High dose: bracket text must change
    comps, _ = plots.analyses_for(p, g)
    lab = {(c.a, c.b): c.p for c in comps}
    assert lab[("Vehicle", "High dose")] > 0.5


def test_only_significant_option_hides_ns_brackets():
    p = example_project(); g = p.graphs[0]
    a = png(render(p, g)); g.overrides["sig_only_significant"] = True
    assert png(render(p, g)) != a


def test_layout_renders_panels_with_letters():
    p = example_project()
    lay = {"rows": 2, "cols": 2, "panels": [g.id for g in p.graphs[:3]] + [None], "letters": True, "width": 7.0, "height": 6.0}
    f = plots.render_layout(lay, p)
    assert len(f.axes) == 4
    texts = [t.get_text() for ax in f.axes for t in ax.texts]
    assert {"A", "B", "C"} <= set(texts)
    assert len(png(f)) > 5000
    lay2 = {**lay, "letters": False}
    assert not ({"A", "B", "C"} & {t.get_text() for ax in plots.render_layout(lay2, p).axes for t in ax.texts})


def test_axis_limits_and_scales_are_applied():
    p = example_project(); g = p.graphs[0]
    g.overrides.update({"y_min": 2.0, "y_max": 25.0, "y_major_step": 5.0, "x_label": "Treatment", "y_label": "Response (a.u.)", "title": "T", "y_scale": "linear"})
    f = render(p, g); ax = f.axes[0]
    assert ax.get_ylim() == (2.0, 25.0) and ax.get_xlabel() == "Treatment" and ax.get_ylabel() == "Response (a.u.)" and ax.get_title() == "T"
    assert list(ax.get_yticks())[:3] == [5.0, 10.0, 15.0] or 5.0 in ax.get_yticks()
    w, h = f.get_size_inches(); assert (w, h) == (p.theme.width, p.theme.height)
    g.overrides.update({"width": 5.0, "height": 2.5}); f = render(p, g); assert tuple(f.get_size_inches()) == (5.0, 2.5)
    xy = p.graphs[3]; xy.overrides["x_scale"] = "log"; assert render(p, xy).axes[0].get_xscale() == "log"


def test_xenocortical_preset_fixed_height_variable_width_and_clip_warning():
    from plotsuite.style import PRESETS, resolve_font
    p = example_project(); p.theme = Style.from_dict({**Style().to_dict(), **PRESETS["Xenocortical Nature style"]})
    for g in p.graphs:
        g.overrides = {}
    sizes = {}
    for g in p.graphs[:3]:
        f = render(p, g); sizes[g.name] = f.get_size_inches()
        assert f.get_size_inches()[1] == pytest.approx(1.3)
        assert plots.export(f, "png", 200)  # exact size kept on export
    from PIL import Image
    import io
    im = Image.open(io.BytesIO(plots.export(render(p, p.graphs[0]), "png", 200)))
    assert im.size[1] == 260  # 1.3 in x 200 dpi, no cropping
    pr = PRESETS["Xenocortical Nature style"]
    assert sizes["Dose groups"][0] == pytest.approx(pr["margin_left"] + 3 * pr["bar_slot"] + pr["margin_right"])  # 3 bars
    assert sizes["Before-after"][0] < sizes["Dose groups"][0] < sizes["Genotype x time"][0]
    st = p.effective_style(p.graphs[0])
    assert st.font_family == "Helvetica" and st.tick_size == 6 and st.label_size == 7 and st.tick_pad == 1.0 and st.legend_spacing < 0.5 and st.bar_color == "#CCCCCC" and st.error_color == "#333333"
    assert resolve_font("Helvetica") in ("Helvetica", "Nimbus Sans", "Arial", "Liberation Sans")
    f = render(p, p.graphs[0]); ax = f.axes[0]
    bars = ax.patches
    assert all(tuple(round(c, 3) for c in b.get_facecolor()[:3]) == (0.8, 0.8, 0.8) for b in bars[:3])
    assert not hasattr(f, "_ps_clipped")  # with tight spacing the 45-degree labels fit in 0.4 in
    p.theme = p.theme.merged({"margin_bottom": 0.15}); assert "bottom" in render(p, p.graphs[0])._ps_clipped  # too little room -> warned, not silently resized
    p.theme = p.theme.merged({"margin_bottom": 0.4}); assert not hasattr(render(p, p.graphs[0]), "_ps_clipped")


def test_grouped_levels_keep_data_order():
    p = example_project(); g = p.graphs[2]
    lab = [t.get_text() for t in render(p, g).axes[0].get_xticklabels()]
    assert lab == ["Day 0", "Day 7", "Day 14"]


def test_bracket_spacing_scales_with_label_size():
    p = example_project(); g = p.graphs[0]
    p.theme = p.theme.merged({"auto_width": True, "height": 1.3, "sig_size": 7.0})
    f = render(p, g); ax = f.axes[0]
    ys = sorted({round(l.get_ydata()[0], 3) for l in ax.lines if len(l.get_xdata()) == 4})
    h_in = ax.get_position().height * f.get_figheight(); rng = ax.get_ylim()[1] - ax.get_ylim()[0]
    gaps = [(b - a) / rng * h_in * 72 for a, b in zip(ys, ys[1:])]
    assert all(gp >= 7.0 for gp in gaps), gaps  # at least one text height (pt) between tiers


def test_diagonal_tick_labels_end_on_their_tick():
    """For rotated labels the letter nearest the axis must be centred on the tick (text ends at the tick, not hangs left of it)."""
    p = example_project(); g = p.graphs[0]
    p.theme = p.theme.merged({"auto_width": True, "height": 1.3, "x_tick_rotation": 45.0, "margin_bottom": 0.5})
    f = render(p, g); ax = f.axes[0]
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    c = FigureCanvasAgg(f); c.draw(); r = c.get_renderer()
    for lab, tick in zip(ax.get_xticklabels(), ax.get_xticks()):
        bb = lab.get_window_extent(r)
        x_tick = ax.transData.transform((tick, 0))[0]
        # the label's right end (nearest letter) sits at the tick, within half a letter; text extends to the LEFT
        assert abs(bb.x1 - x_tick) < 0.6 * p.theme.tick_size / 72 * f.dpi, (lab.get_text(), bb.x1, x_tick)
        assert bb.x0 < x_tick
