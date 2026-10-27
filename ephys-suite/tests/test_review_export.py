"""HTML event-review package: export, snippet round trip, import of the reviewer's CSV, and a headless-browser smoke test."""
import csv
import json
import os
import re
import shutil
import sys

import numpy as np
import pytest

from ephys_suite import model_review as MR
from ephys_suite import review_export as RX
from ephys_suite import review_import as RI
from ephys_suite import verify as V
from ephys_suite.io import load_abf
from ephys_suite.ml.core import prep_trace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))


@pytest.fixture(scope="module")
def data(cell7_dir, tmp_path_factory):
    """The example recording copied next to a practice candidates file (made by the ordinary detector, not a model)."""
    from make_demo_candidates import make_candidates
    d = tmp_path_factory.mktemp("rxdata")
    abf = str(d / "sEPSC.abf")
    shutil.copy(os.path.join(cell7_dir, "sEPSC.abf"), abf)
    rows = make_candidates(abf, ((0.0, 400.0),))
    cpath = str(d / "sEPSC.candidates.csv")
    with open(cpath, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sweep", "time_in_sweep_ms", "score", "accepted", "amplitude_pA"])
        w.writerows([(a, b, c, int(c >= 0.7), e) for a, b, c, _, e in rows])      # model accepts >= 0.7; 0.5-0.7 are borderline
    return abf, cpath, rows


def read_cell(out, cid):
    txt = open(os.path.join(out, "data", f"cell{cid}.js"), encoding="utf-8").read()
    meta = json.loads(re.search(r"CELLDATA\['%s'\]=(\{.*?\});window" % cid, txt).group(1))
    events = json.loads(re.search(r"\.events=(\[.*?\]);window", txt).group(1))
    b64 = re.search(r"\.b64='([^']*)'", txt).group(1)
    return meta, events, b64


# ---------------------------------------------------------------------------------------------- selection
def test_select_events_tiers_exclusions_and_skips():
    rows = [dict(sweep=1, t=1000.0, score=0.95, accepted=1), dict(sweep=1, t=2000.0, score=0.6, accepted=0),
            dict(sweep=1, t=3000.0, score=0.3, accepted=0),                      # below min score: not sent
            dict(sweep=1, t=110000.0, score=0.99, accepted=1),                   # inside the excluded 100-130 s window
            dict(sweep=2, t=500.0, score=0.97, accepted=1), dict(sweep=1, t=500.0, score=0.92, accepted=1)]
    ev, info = RX.select_events(rows, 0.5, 0.9, exclude=[(100, 130)], skip=lambda s, t: (s, t) == (2, 500.0))
    assert [(e["sweep"], e["t"], e["tier"]) for e in ev] == [(1, 500.0, "detected"), (1, 1000.0, "detected"), (1, 2000.0, "maybe")]
    assert info == dict(low_score=1, excluded=1, skipped=1)
    assert RX.parse_windows(["13:100-130"]) == {"13": [(100.0, 130.0)]}
    with pytest.raises(SystemExit):
        RX.parse_windows(["13:130-100"])


def test_per_file_options_resolve_by_cell_number_stem_or_name():
    assert RX.lookup({"13": 0.99}, "13", "/x/cell.abf") == 0.99
    assert RX.lookup({"3": 0.99}, "03", "/x/cell.abf") == 0.99
    assert RX.lookup({"cell": 0.99}, "01", "/x/cell.abf") == 0.99
    assert RX.lookup({"cell.abf": 0.99}, "01", "/x/cell.abf") == 0.99
    assert RX.lookup({"other": 0.99}, "01", "/x/cell.abf", "dflt") == "dflt"


# ---------------------------------------------------------------------------------------------- the package
def test_snippets_round_trip_to_prep_trace_within_quantisation(data, tmp_path):
    abf, cpath, _ = data
    out = str(tmp_path / "pkg")
    man = RX.build_package([(abf, cpath)], out, min_score=0.5, thr=0.9)
    meta, events, b64 = read_cell(out, "01")
    assert meta["n"] == len(events) > 20 and meta["recording"] == "sEPSC.abf"
    snips = RX.decode_snippets(b64, len(events), 3000)
    rec = load_abf(abf)
    ch = rec.channel_by_units("pA", 0)
    cache = {}
    for i in list(range(0, len(events), max(1, len(events) // 12)))[:12]:
        sw, t = events[i][0], events[i][1]
        cache.setdefault(sw, prep_trace(rec.ch[ch][sw - 1], rec.fs))
        y = cache[sw]
        i0 = int(round((t - 100) * 10))
        seg = y[i0:i0 + 3000]
        assert len(seg) == 3000
        assert np.max(np.abs(snips[i] - (seg - np.median(seg)))) <= 0.05, i          # int16 / 20 = 0.05 pA steps
    assert man["package"]["id"] and os.path.exists(os.path.join(out, "START_HERE.html"))
    assert open(os.path.join(out, "data", "cells.js")).read().count("window.SNIP={fs:10000,pre:100,post:200,scale:20}") == 1


def test_zero_event_cell_and_event_at_the_sweep_edge(data, tmp_path):
    abf, cpath, _ = data
    quiet = tmp_path / "quiet.candidates.csv"                                     # only sub-threshold candidates: nothing to review
    quiet.write_text("sweep,time_in_sweep_ms,score,accepted\n1,1000,0.3,0\n1,2000,0.4,0\n")
    edge = tmp_path / "edge.candidates.csv"                                       # events 5 ms from the start and end of the sweep
    n_ms = float(load_abf(abf).t[-1])
    edge.write_text(f"sweep,time_in_sweep_ms,score,accepted\n1,5,0.95,1\n1,{n_ms - 5},0.96,1\n")
    shutil.copy(abf, tmp_path / "quiet.abf")
    shutil.copy(abf, tmp_path / "edge.abf")
    out = str(tmp_path / "pkg")
    RX.build_package([(str(tmp_path / "quiet.abf"), str(quiet)), (str(tmp_path / "edge.abf"), str(edge))], out)
    m1, e1, b1 = read_cell(out, "01")
    assert m1["n"] == 0 and e1 == [] and b1 == ""
    assert "nothing to review" in open(os.path.join(out, "README.txt")).read()
    m2, e2, b2 = read_cell(out, "02")
    s = RX.decode_snippets(b2, 2, 3000)
    assert np.all(s[0][:950] == 0) and np.any(s[0][1000:1200] != 0)                # 95 ms of nothing before t = 5 ms -> padded with 0
    assert np.all(s[1][-1950:] == 0) and np.any(s[1][:200] != 0)                   # nothing after the end of the sweep


def test_polarity_exclusion_note_and_title_flow_into_the_package(data, tmp_path):
    abf, cpath, rows = data
    out = str(tmp_path / "pkg")
    RX.build_package([(abf, cpath)], out, exclude={"1": [(2.0, 4.0)]}, polarity="outward", title="My IPSC review")
    js = open(os.path.join(out, "data", "cells.js"), encoding="utf-8").read()
    assert '"polarity": "outward"' in js and '"ylim": [-15, 45]' in js and "My IPSC review" in js
    meta, events, _ = read_cell(out, "01")
    assert "2-4 s skipped" in meta["note"] and all(not (2000 <= e[1] < 4000) for e in events)
    assert "upward" in open(os.path.join(out, "README.txt")).read()


def test_cli_exports_pairs_beside_recordings_and_makes_a_zip(data, tmp_path, capsys):
    abf, cpath, _ = data
    d = tmp_path / "in"
    d.mkdir()
    shutil.copy(abf, d / "sEPSC.abf")
    shutil.copy(cpath, d / "sEPSC.candidates.csv")
    out = tmp_path / "pkg"
    RX.main([str(d / "sEPSC.abf"), "--out", str(out), "--zip", "--thr-per-file", "1=0.99", "--exclude", "sEPSC:2-4"])
    assert (tmp_path / "pkg.zip").exists() and (out / "manifest.json").exists()
    assert not (out / "recordings").exists()                                      # raw recordings are opt-in
    meta, _, _ = read_cell(str(out), "01")
    assert meta["thr"] == 0.99
    with pytest.raises(SystemExit):                                               # a recording without candidates is reported, not guessed
        RX.main([str(abf) + ".nope.abf", "--out", str(tmp_path / "x")])


# ---------------------------------------------------------------------------------------------- import
def _returned(out, answers, path, pkg_id=None):
    """Write a reviewer CSV like the page's download: answers = {event_index: 'yes'|'no'|'unsure'}."""
    meta, events, _ = read_cell(out, "01")
    pkg = pkg_id or json.load(open(os.path.join(out, "manifest.json")))["package"]["id"]
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["cell", "label", "recording", "sweep", "time_in_sweep_ms", "time_s", "model_score", "model_tier", "decision", "package_id"])
        for i, e in enumerate(events):
            w.writerow(["01", meta["label"], meta["recording"], e[0], e[1], e[1] / 1000, e[2], e[3], answers.get(i, ""), pkg])
    return events


def test_import_matches_by_sweep_and_time_counts_answers_and_writes_resume_file(data, tmp_path):
    abf, cpath, _ = data
    out = str(tmp_path / "pkg")
    RX.build_package([(abf, cpath)], out, thr=0.9)
    meta, events, _ = read_cell(out, "01")
    det = [i for i, e in enumerate(events) if e[3] == "detected"]
    maybe = [i for i, e in enumerate(events) if e[3] == "maybe" and e[2] < 0.7]                       # not accepted by the model
    assert len(det) >= 5 and len(maybe) >= 3
    answers = {det[0]: "yes", det[1]: "no", det[2]: "unsure", maybe[0]: "yes", maybe[1]: "no", maybe[2]: "unsure"}   # det[3:], maybe[3:] blank
    ret = str(tmp_path / "returned.csv")
    _returned(out, answers, ret)
    lines = open(ret).read().splitlines()                                          # shuffle rows: matching must not depend on order
    body = lines[1:]
    np.random.default_rng(0).shuffle(body)
    open(ret, "w").write("\n".join([lines[0]] + body) + "\n")
    msgs = []
    rep = RI.import_returned(ret, package=out, out=str(tmp_path / "merged"), log=msgs.append)
    assert (rep["yes"], rep["no"], rep["unsure"]) == (2, 2, 2) and rep["blank"] == len(events) - 6 and rep["unmatched"] == 0
    assert any("2 yes, 2 no, 2 unsure" in m for m in msgs)                         # the counts are printed
    rows = list(csv.DictReader(open(tmp_path / "merged" / "sEPSC_review.csv")))
    by = {(int(r["sweep"]), round(float(r["time_in_sweep_ms"]), 0)): r for r in rows}
    def row_of(i):
        e = events[i]
        return min((r for r in rows if int(r["sweep"]) == e[0]), key=lambda r: abs(float(r["time_in_sweep_ms"]) - e[1]))
    assert row_of(det[0])["decision"] == "confirmed" and row_of(det[0])["final_event"] == "1"
    assert row_of(det[1])["decision"] == "rejected" and row_of(det[1])["final_event"] == "0"
    assert row_of(maybe[0])["decision"] == "confirmed" and row_of(maybe[0])["final_event"] == "1"      # a 'maybe' the reviewer accepted
    assert row_of(maybe[1])["decision"] == "dismissed" and row_of(maybe[1])["final_event"] == "0"
    assert row_of(det[2])["decision"] == "" and row_of(det[2])["final_event"] == "0"                   # unsure -> not an event, undecided
    assert row_of(det[5])["final_event"] == "0"                                                        # blank -> not an event (default)
    prog = V.load_progress(str(tmp_path / "merged" / "sEPSC.candidates.review_progress.json"))
    assert prog is not None and len(prog["log"]["confirmed"]) >= 1 and len(prog["log"]["rejected"]) == 1
    assert len(prog["log"]["dismissed"]) == 1


def test_import_policy_flags_and_unmatched_rows(data, tmp_path):
    abf, cpath, _ = data
    out = str(tmp_path / "pkg")
    RX.build_package([(abf, cpath)], out, thr=0.9)
    _, events, _ = read_cell(out, "01")
    det = [i for i, e in enumerate(events) if e[3] == "detected"]
    ret = str(tmp_path / "ret.csv")
    _returned(out, {det[0]: "unsure"}, ret)
    with open(ret, "a", newline="") as fh:                                         # a row the candidates never had
        csv.writer(fh).writerow(["01", "x", "sEPSC.abf", 1, 123456.78, 123.4, 0.9, "detected", "yes", "pkg"])
    for pol, want in (("event", "1"), ("model", "1"), ("not-event", "0")):
        rep = RI.import_returned(ret, package=out, out=str(tmp_path / pol), unsure_as=pol, blank_as=pol, log=lambda m: None)
        assert rep["unmatched"] == 1
        rows = list(csv.DictReader(open(tmp_path / pol / "sEPSC_review.csv")))
        e = events[det[0]]
        r = min((r for r in rows if int(r["sweep"]) == e[0]), key=lambda r: abs(float(r["time_in_sweep_ms"]) - e[1]))
        assert r["final_event"] == want and r["decision"] == ("unreviewed" if want == "1" else ""), pol
    with pytest.raises(ValueError):
        RI.read_returned(str(tmp_path / "pkg" / "manifest.json"))                  # not a result file


def test_import_without_the_recording_still_writes_the_review_csv(data, tmp_path):
    abf, cpath, _ = data
    out = str(tmp_path / "pkg")
    RX.build_package([(abf, cpath)], out, thr=0.9)
    _, events, _ = read_cell(out, "01")
    ret = str(tmp_path / "ret.csv")
    _returned(out, {0: "yes", 1: "no"}, ret)
    lone = tmp_path / "lone"
    lone.mkdir()
    shutil.copy(cpath, lone / "sEPSC.candidates.csv")                              # no package, no recording anywhere near
    msgs = []
    RI.import_returned(ret, candidates=str(lone), out=str(tmp_path / "m"), log=msgs.append)
    assert any("recording not found" in m for m in msgs)
    rows = list(csv.DictReader(open(tmp_path / "m" / "sEPSC_review.csv")))
    assert rows and not (tmp_path / "m" / "sEPSC.candidates.review_progress.json").exists()
    ds = {r["decision"] for r in rows}
    assert "confirmed" in ds and ds & {"rejected", "dismissed"}


# ---------------------------------------------------------------------------------------------- real browser
def test_page_in_headless_chrome_from_file_url(data, tmp_path):
    pw = pytest.importorskip("playwright.sync_api")
    abf, cpath, _ = data
    out = str(tmp_path / "pkg")
    RX.build_package([(abf, cpath)], out, thr=0.9)
    meta, events, _ = read_cell(out, "01")
    pkg_id = json.load(open(os.path.join(out, "manifest.json")))["package"]["id"]
    exe = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers") + "/chromium"
    try:
        p = pw.sync_playwright().start()
        browser = p.chromium.launch(executable_path=exe if os.path.exists(exe) else None)
    except Exception as e:                                                         # pragma: no cover
        pytest.skip(f"no headless Chrome available: {e}")
    try:
        page = browser.new_context(accept_downloads=True).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto("file://" + os.path.join(out, "START_HERE.html"))
        page.click("text=Cell 01")
        page.wait_for_selector("#evtitle:has-text('event 1 of')")
        assert page.inner_text("#pkgtitle") == "Event review"
        assert "score" not in page.inner_text("#review").lower()                   # the model score is not shown
        page.keyboard.press("y")
        page.keyboard.press("n")
        page.keyboard.press("u")
        st = json.loads(page.evaluate(f"localStorage.getItem('ephysrev_{pkg_id}_01')"))
        assert st["d"] == {"0": "Y", "1": "N", "2": "U"}
        with page.expect_download() as dl:
            page.click("#saveall")
        path = tmp_path / "dl.csv"
        dl.value.save_as(str(path))
        rows = list(csv.DictReader(open(path)))
        assert [r["decision"] for r in rows[:4]] == ["yes", "no", "unsure", ""] and rows[0]["package_id"] == pkg_id
        assert len(rows) == meta["n"] and not errors
        # what the browser wrote is exactly what the importer reads
        assert RI.read_returned(str(path))[0][0][3] == "yes"
    finally:
        browser.close()
        p.stop()


# ---------------------------------------------------------------------------------------------- GUI
@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_gui_export_skips_my_decisions_and_import_resumes_in_the_app(qapp, data, tmp_path, monkeypatch):
    from PySide6 import QtWidgets
    from ephys_suite.gui import MainWindow
    from ephys_suite.gui_export import ExportDialog, import_flow
    abf, cpath, _ = data
    d = tmp_path / "cell"
    d.mkdir()
    shutil.copy(abf, d / "sEPSC.abf")
    shutil.copy(cpath, d / "sEPSC.candidates.csv")
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes))
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", staticmethod(lambda *a, **k: None))
    w = MainWindow()
    w.events.settings.clear()
    w.load(str(d / "sEPSC.abf"))
    ev = w.events
    assert ev.load_candidates_file(str(d / "sEPSC.candidates.csv"))
    # my own decisions in the app: confirm two events, reject one
    es = ev.session.events
    mine = [(es[0].record, es[0].peak_idx), (es[1].record, es[1].peak_idx), (es[2].record, es[2].peak_idx)]
    ev.session.confirm(es[0])
    ev.session.confirm(es[1])
    ev.session.reject(es[2])
    dlg = ExportDialog(ev)
    assert [os.path.basename(a) for a, _ in dlg.chosen()] == ["sEPSC.abf"]
    dlg.ed_out.setText(str(tmp_path / "out_pkg"))
    dlg.chk_zip.setChecked(False)
    man = dlg.run()
    n_with_skip = man["cells"][0]["n"]
    assert man["cells"][0]["skipped"] == 3 and "already-decided" in man["cells"][0]["note"]
    dlg.chk_skip.setChecked(False)
    dlg.ed_out.setText(str(tmp_path / "out_pkg2"))
    assert dlg.run()["cells"][0]["n"] == n_with_skip + 3                       # without the switch they would be sent again
    # a reviewer answers the first few events; merge the file back through the GUI path
    _, events, _ = read_cell(str(tmp_path / "out_pkg"), "01")
    ret = str(tmp_path / "back.csv")
    _returned(str(tmp_path / "out_pkg"), {0: "yes", 1: "no", 2: "unsure"}, ret)
    ev.kit.autosave()                                                          # my 3 decisions are in the progress file
    assert os.path.exists(d / "sEPSC.candidates.review_progress.json")
    seen = []
    monkeypatch.setattr(ev, "load_candidates_file", lambda p, thr=None: seen.append(p) or True)
    rep = import_flow(ev, returned=ret, package=str(tmp_path / "out_pkg"))
    assert (rep["yes"], rep["no"], rep["unsure"]) == (1, 1, 1)
    assert os.path.exists(d / "sEPSC.candidates.review_progress.json") and os.path.exists(d / "sEPSC_review.csv")
    assert seen and seen[0].endswith("sEPSC.candidates.csv")                    # the open recording's result is loaded straight away
    prog = V.load_progress(str(d / "sEPSC.candidates.review_progress.json"))
    assert len(prog["log"]["confirmed"]) == 3 and len(prog["log"]["rejected"]) + len(prog["log"]["dismissed"]) == 2   # mine + theirs
    assert any(f.startswith("sEPSC.candidates.review_progress.json.bak-") for f in os.listdir(d))                  # old file kept
    w.close()
