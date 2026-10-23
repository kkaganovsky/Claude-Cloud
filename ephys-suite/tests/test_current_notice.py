"""Notice when no recorded current channel exists and the header command is used as the current step (offscreen)."""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6 import QtWidgets
from ephys_suite.gui import MainWindow
from ephys_suite.io import Recording, current_source


def rec(units, command=None, cu=""):
    t = np.arange(0, 100, 0.05)
    ch = [np.zeros((2, len(t))) for _ in units]
    return Recording("x.abf", t, ch, list(units), [f"IN {i}" for i in range(len(units))], np.array([0.0, 1000.0]), command, cu, 20000.0)


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_current_source_classification():
    cmd = np.zeros((2, 2000))
    assert current_source(rec(["mV", "pA"])) == "recorded"
    assert current_source(rec(["mV", "mV"], cmd, "pA")) == "header"
    assert current_source(rec(["mV"], cmd, "mV")) == "none"
    assert current_source(rec(["mV"])) == "none"


def test_popup_only_when_the_header_is_used(app):
    w = MainWindow()
    try:
        w.notify_current_source(rec(["mV", "pA"]))
        assert w.last_notice is None and w._notice_box is None
        w.notify_current_source(rec(["mV", "mV"], np.zeros((2, 2000)), "pA"))
        assert w.last_notice[0] == "Current channel not found" and "header" in w.last_notice[1]
        assert w._notice_box is not None and "command waveform from the file header" in w._notice_box.text()
        w._notice_box.close()
        w.notify_current_source(rec(["mV"]))
        assert "not available" in w.last_notice[1]
    finally:
        w.close()
