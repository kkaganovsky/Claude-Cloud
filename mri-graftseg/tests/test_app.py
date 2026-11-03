"""GUI smoke test (offscreen): pages build, the editor paints / undoes / saves a reviewed Slicer file."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("pyqtgraph")
sys.path.insert(0, str(Path(__file__).parent))
import synth  # noqa: E402


@pytest.fixture()
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAFTSEG_CONFIG", str(tmp_path / "cfg.json"))
    import importlib
    from graftseg import config as C
    importlib.reload(C)
    from graftseg import app as A
    importlib.reload(A)
    cfg = C.Config(workspace=str(tmp_path / "ws")); cfg.save()
    qa = A.make_app()
    w = A.MainWindow()
    w.show()
    yield w, qa, tmp_path
    w.home._w.wait(10000)
    w.close()


def test_pages_and_editor(win):
    w, qa, tmp = win
    for p in w.PAGES:
        w.go(p); qa.processEvents()
    synth.write_case(tmp / "data" / "PA_01_R1M", "PA_01_R1M", seed=1, crop=True, graft_center=(20, 22, 8))
    seg = tmp / "data" / "PA_01_R1M" / "PA_01_R1M.seg.nrrd"
    w.review.load_paths([str(seg)]); w.go("Review"); qa.processEvents()
    rv = w.review
    assert rv.case is not None and rv.case.mask.any()
    v0 = rv.case.volume_mm3()
    from PySide6 import QtCore
    rv.stroke(rv.img.mapToScene(QtCore.QPointF(3, 3)), True, True, False)
    rv.stroke(rv.img.mapToScene(QtCore.QPointF(9, 6)), True, False, True)
    assert rv.case.volume_mm3() > v0 and rv.case.edited
    rv.undo()
    assert abs(rv.case.volume_mm3() - v0) < 1e-9
    rv.btn_erase.setChecked(True)
    rv.jump()
    rr, cc = (int(v) for v in rv.case.mask.take(rv.slice, axis=rv.axis).nonzero()[0][:1].tolist() +
              rv.case.mask.take(rv.slice, axis=rv.axis).nonzero()[1][:1].tolist())
    rv.stroke(rv.img.mapToScene(QtCore.QPointF(cc + 0.5, rr + 0.5)), True, True, True)
    assert rv.case.volume_mm3() < v0
    rv.save()
    import nrrd
    h = nrrd.read_header(str(seg))
    assert h["GraftSeg_Status"] == "reviewed" and h["Segment0_Name"] == "Graft"
    assert "Black spot" in [h[k] for k in h if k.endswith("_Name")]   # other segments kept
    for k in range(3):
        rv.set_axis_order(k); qa.processEvents()
