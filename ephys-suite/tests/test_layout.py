"""Window sizing, wrapping toolbars, show / hide panels, shortcuts."""
import os

import pytest
from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt

from ephys_suite import layout as LY
from ephys_suite.gui import EventsTab, MainWindow


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# ---------------------------------------------------------------- pure geometry
def test_window_fits_small_and_large_screens():
    for avail in [(0, 0, 1366, 728), (0, 0, 1920, 1040), (0, 0, 2560, 1400), (0, 0, 1280, 700)]:
        x, y, w, h = LY.window_rect(avail)
        assert 0 <= x and 0 <= y and x + w <= avail[2] and y + h <= avail[3], avail
        assert w <= LY.WANT[0] and h <= LY.WANT[1]
    assert LY.window_rect((0, 0, 2560, 1400))[2:] == LY.WANT        # a big screen gets the preferred size, not more


def test_saved_window_is_restored_but_clamped_and_never_off_screen():
    avail = (0, 0, 1920, 1040)
    assert LY.window_rect(avail, saved=(100, 80, 1200, 800)) == (100, 80, 1200, 800)
    x, y, w, h = LY.window_rect((0, 0, 1366, 728), saved=(100, 80, 1900, 1000))          # saved on a bigger monitor
    assert w <= 1366 and h <= 728 and x + w <= 1366 and y + h <= 728
    x, y, w, h = LY.window_rect(avail, saved=(5000, 3000, 1200, 800))                   # monitor was unplugged
    assert 0 <= x and x + w <= 1920 and 0 <= y and y + h <= 1040
    assert LY.parse_rect("1,2,3,4") == (1, 2, 3, 4) and LY.parse_rect("junk") is None and LY.parse_rect("1,2,0,4") is None


# ---------------------------------------------------------------- wrapping toolbar
def test_flow_row_wraps_instead_of_cutting_off(app):
    btns = [QtWidgets.QPushButton(f"button number {i}") for i in range(10)]
    row = LY.flow_row(*btns)
    host = QtWidgets.QWidget()
    v = QtWidgets.QVBoxLayout(host)
    v.addWidget(row)
    host.resize(1400, 200)
    host.show()
    wide_h = row.height()
    host.resize(420, 400)
    app.processEvents()
    assert row.height() > wide_h                                                      # taller = wrapped onto more rows
    assert all(row.rect().contains(b.geometry()) for b in btns)                      # every button fully inside the row
    host.close()


# ---------------------------------------------------------------- panels
def test_panels_can_be_hidden_and_are_remembered(app):
    w = MainWindow()
    w.show()
    w.tabs.setCurrentWidget(w.events)
    pm = w.panels
    assert pm.is_on("events", "table") and w.events.tbl.isVisible()
    pm.set_on("events", "table", False)
    assert not w.events.tbl.isVisible()
    pm.set_on("events", "plots", False)
    pm.set_on("events", "measure", False)
    w.close()
    w2 = MainWindow()                                                                # a new session starts the way you left it
    w2.show()
    w2.tabs.setCurrentWidget(w2.events)
    assert not w2.events.tbl.isVisible() and not w2.events.bot.isVisible()
    assert w2.events.pd.isVisible()
    w2.panels.reset()
    assert w2.events.tbl.isVisible() and w2.events.bot.isVisible()
    w2.close()


def test_group_container_hides_when_all_children_hidden(app):
    w = MainWindow()
    w.show()
    pm = w.panels
    top = w.passive.pv.parentWidget()
    pm.set_on("passive", "vm", False)
    assert top.isVisible()
    pm.set_on("passive", "rin", False)
    assert not top.isVisible()                                                       # no empty gap left behind
    pm.set_on("passive", "vm", True)
    assert top.isVisible()
    w.close()


def test_view_menu_lists_every_panel_and_presets_work(app):
    w = MainWindow()
    w.show()
    menu = [m for m in w.menuBar().findChildren(QtWidgets.QMenu) if m.title() == "&View"][0]
    titles = [a.text() for a in menu.actions()]
    assert any("Events tab" in t for t in titles) and any("Reset layout" in t for t in titles)
    w.tabs.setCurrentWidget(w.events)
    w.panels.apply_preset("events", {"settings", "measure", "plots", "table"})
    assert not w.events.side.isVisible() and w.events.pd.isVisible() and w.events.kit.card.isVisible()
    w.close()


def test_every_tab_still_works_in_a_small_window(app):
    w = MainWindow()
    w.show()
    w.resize(900, 560)
    app.processEvents()
    for t in (w.passive, w.spikes, w.events, w.summary, w.qc):
        w.tabs.setCurrentWidget(t)
        app.processEvents()
        assert t.width() >= LY.MIN_CONTENT[0] - 5                                   # tabs keep a usable size (outer area scrolls)
    w.close()


# ---------------------------------------------------------------- keyboard
def _key_targets(ev):
    from PySide6.QtGui import QShortcut
    return {s.key().toString(): s for s in ev.findChildren(QShortcut)}


def test_page_keys_go_the_obvious_way(app):
    w = MainWindow()
    ev = w.events
    seen = []
    ev.page = lambda d: seen.append(d)
    from PySide6.QtGui import QKeySequence, QShortcut
    shortcuts = ev.findChildren(QShortcut)

    def find(name):
        return [q for q in shortcuts if q.key() == QKeySequence(name)]
    for name, want in (("X", +1), ("PgDown", +1), ("Z", -1), ("PgUp", -1)):
        assert find(name), name
        seen.clear()
        find(name)[0].activated.emit()
        assert seen == [want], (name, seen)
    keys = {q.key().toString() for q in shortcuts}
    assert "Space" not in keys                                                       # Space no longer deletes events
    w.close()
