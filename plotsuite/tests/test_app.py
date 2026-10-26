"""Drive the real Streamlit script headlessly (streamlit.testing.AppTest)."""
import pathlib

import pytest
from streamlit.testing.v1 import AppTest

APP = str(pathlib.Path(__file__).parent.parent / "plotsuite" / "app.py")


def fresh(example=True):
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    if example:
        next(b for b in at.button if b.label == "Load example").click().run()
        assert not at.exception, [e.value for e in at.exception]
    return at


def button(at, prefix):
    return next(b for b in at.button if b.label.startswith(prefix))


def test_empty_app_and_example_load():
    at = fresh(False)
    assert any("Add a table" in i.value for i in at.info) or at.expander
    at = fresh(True)
    P = at.session_state.project
    assert len(P.datasets) == 6 and len(P.graphs) == 5 and len(P.analyses) == 4
    assert len(at.tabs) == 6 and not at.exception


def test_invalid_test_is_blocked_then_allowed_after_choosing_groups():
    at = fresh()
    at.selectbox(key="an_family").set_value("t tests / rank tests").run()
    at.selectbox(key="an_test").set_value("unpaired_t").run()
    run = button(at, "▶")
    assert run.disabled and any("exactly two" in e.value for e in at.error)
    at.selectbox(key="opt:Dose groups:unpaired_t:group_a").set_value("Vehicle").run()
    at.selectbox(key="opt:Dose groups:unpaired_t:group_b").set_value("High dose").run()
    run = button(at, "▶")
    assert not run.disabled and not at.exception
    run.click().run()
    assert not at.exception and any("difference" in s.value for s in at.success)
    assert at.session_state.last["res"].p < 0.05


def test_menu_flags_status_icons_and_advisor_present():
    at = fresh()
    at.selectbox(key="an_family").set_value("t tests / rank tests").run()
    labels = at.selectbox(key="an_test").options
    assert any(l.startswith("⛔") or l.startswith("🔧") or "t test" in l for l in labels)
    assert any("Which test should I use" in e.label for e in at.expander)


def test_advisor_use_button_selects_the_test():
    at = fresh()
    use = [b for b in at.button if b.label == "Use" and not b.disabled]
    assert use
    use[0].click().run()
    assert not at.exception
    assert at.selectbox(key="an_test").value in ("oneway_anova", "kruskal", "welch_anova")


def test_run_save_and_attach_analysis():
    at = fresh()
    button(at, "▶").click().run()
    n0 = len(at.session_state.project.analyses)
    button(at, "Save to project").click().run()
    assert len(at.session_state.project.analyses) == n0 + 1 and not at.exception


def test_format_scope_this_graph_vs_all_graphs():
    at = fresh()
    P = lambda: at.session_state.project  # noqa: E731
    gid = at.session_state["cur_graph"]
    at.radio(key="scope").set_value("this graph only").run()
    next(n for n in at.number_input if n.label == "Width (inches)").set_value(6.5).run()
    g = P().graph(gid)
    assert g.overrides.get("width") == 6.5 and P().theme.width == 3.4
    others = [x for x in P().graphs if x.id != gid]
    assert all(P().effective_style(x).width == 3.4 for x in others)
    at.radio(key="scope").set_value("all graphs (project theme)").run()
    next(n for n in at.number_input if n.label == "Tick label size (pt)").set_value(12.0).run()
    assert P().theme.tick_size == 12.0 and all(P().effective_style(x).tick_size == 12.0 for x in P().graphs)
    assert not at.exception


def test_theme_change_removes_graph_specific_override_of_same_field():
    at = fresh()
    P = lambda: at.session_state.project  # noqa: E731
    gid = at.session_state["cur_graph"]
    at.radio(key="scope").set_value("this graph only").run()
    next(n for n in at.number_input if n.label == "Axis line width").set_value(2.0).run()
    assert P().graph(gid).overrides["spine_width"] == 2.0
    at.radio(key="scope").set_value("all graphs (project theme)").run()
    next(n for n in at.number_input if n.label == "Axis line width").set_value(1.5).run()
    assert P().theme.spine_width == 1.5 and "spine_width" not in P().graph(gid).overrides


def test_new_table_from_paste_and_roles():
    at = fresh(False)
    at.text_input(key="new_name").set_value("Pasted").run()
    at.radio(key="new_src").set_value("Paste").run()
    at.text_area(key="paste_txt").set_value("ctrl\ttreated\n1.2\t2.1\n1.4\t2.5\n1.1\t2.2\n1.3\t2.8").run()
    button(at, "Create table").click().run()
    assert not at.exception
    P = at.session_state.project
    assert "Pasted" in P.datasets and list(P.datasets["Pasted"].df.columns) == ["ctrl", "treated"]
    assert P.datasets["Pasted"].df.shape == (4, 2)


def test_new_graph_and_consistency_tools():
    at = fresh()
    P = lambda: at.session_state.project  # noqa: E731
    n = len(P().graphs)
    at.selectbox(key="ng_ds").set_value("Dose groups").run()
    at.text_input(key="ng_name").set_value("Second").run()
    next(b for b in at.button if b.label == "Create").click().run()
    assert len(P().graphs) == n + 1 and P().graph(at.session_state["cur_graph"]).name == "Second"
    P().graphs[0].overrides["y_max"] = 40.0
    at.run()
    src = P().graphs[0].id
    at.selectbox(key="cs_src").set_value(src).run()
    button(at, "Use as project theme").click().run()
    assert P().theme.y_max == 40.0 and all(g.overrides == {} for g in P().graphs) and not at.exception


def test_layout_tab_and_about_render():
    at = fresh()
    at.selectbox(key="lay:0").set_value(at.session_state.project.graphs[0].id).run()
    at.selectbox(key="lay:1").set_value(at.session_state.project.graphs[2].id).run()
    assert not at.exception
    assert len(at.get("image")) >= 2  # graph preview + layout preview


def test_project_download_is_valid_json():
    from plotsuite.project import Project
    at = fresh()
    Project.from_json(at.session_state.project.to_json())
