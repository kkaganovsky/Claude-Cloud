"""Layout helpers: wrapping toolbars, screen-aware window sizing, and show / hide panels that are remembered.

Qt-only, no analysis code. The pure parts (`window_rect`, `parse_rect`) are tested without a display.
"""
from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import QPoint, QRect, QSize, Qt

MIN_CONTENT = (1000, 620)           # below this the tabs scroll instead of squeezing controls out of view
WANT = (1600, 950)                  # preferred window size on a big screen
SCREEN_FRACTION = 0.94              # never ask for more than this share of the available screen


# ------------------------------------------------------------------------------------------------ wrapping toolbar
class FlowLayout(QtWidgets.QLayout):
    """Left-to-right layout that wraps onto further rows when the window is narrow, so no control is cut off."""

    def __init__(self, parent=None, hspace=6, vspace=4):
        super().__init__(parent)
        self._items, self._h, self._v = [], hspace, vspace
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QRect(0, 0, w, 0), False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do(self, rect, apply):
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = r.x(), r.y(), 0
        for it in self._items:
            if it.isEmpty():
                continue
            sh = it.sizeHint()
            if x + sh.width() > r.right() + 1 and line_h > 0:
                x, y, line_h = r.x(), y + line_h + self._v, 0
            if apply:
                it.setGeometry(QRect(QPoint(x, y), sh))
            x += sh.width() + self._h
            line_h = max(line_h, sh.height())
        return y + line_h - rect.y() + m.bottom()


class FlowRow(QtWidgets.QWidget):
    """Holds a FlowLayout and keeps its own minimum height equal to the wrapped height, so the layout above never squeezes
    a wrapped toolbar into the widgets below it (Qt only knows the wrapped height once the width is fixed)."""

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        lay = self.layout()
        if lay is not None:
            h = lay.heightForWidth(self.width())
            if h != self.minimumHeight():
                self.setMinimumHeight(h)


def flow_row(*items):
    """A QWidget holding the given widgets in a wrapping row. Plain strings become labels."""
    w = FlowRow()
    lay = FlowLayout(w)
    for it in items:
        lay.addWidget(QtWidgets.QLabel(it) if isinstance(it, str) else it)
    w.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Minimum)
    return w


def labeled(text, widget):
    """Label + control that wrap together (a label never ends a row on its own)."""
    w = QtWidgets.QWidget()
    h = QtWidgets.QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(3)
    h.addWidget(QtWidgets.QLabel(text))
    h.addWidget(widget)
    return w


# ------------------------------------------------------------------------------------------------ window size
def window_rect(avail, want=WANT, saved=None):
    """Window rectangle for a screen whose usable area is `avail` (x, y, w, h).

    The saved rectangle (same format) is used when it still overlaps the screen, but its size is clamped to the screen;
    otherwise the window is the wanted size, shrunk to SCREEN_FRACTION of the screen, and centred.
    """
    ax, ay, aw, ah = avail
    mw, mh = int(aw * SCREEN_FRACTION), int(ah * SCREEN_FRACTION)
    if saved:
        sx, sy, sw, sh = saved
        w, h = max(300, min(sw, mw)), max(300, min(sh, mh))
        ox = min(sx + w, ax + aw) - max(sx, ax)
        oy = min(sy + h, ay + ah) - max(sy, ay)
        if ox >= 200 and oy >= 100:                                  # still reachable on this screen
            x = min(max(sx, ax), ax + aw - w)
            y = min(max(sy, ay), ay + ah - h)
            return (x, y, w, h)
    w, h = min(want[0], mw), min(want[1], mh)
    return (ax + (aw - w) // 2, ay + (ah - h) // 2, w, h)


def parse_rect(s):
    try:
        v = tuple(int(float(p)) for p in str(s).split(","))
        return v if len(v) == 4 and v[2] > 0 and v[3] > 0 else None
    except (TypeError, ValueError):
        return None


def fit_window(win, settings):
    """Size and place `win` for the screen it is on (restoring last time's size if it still fits)."""
    scr = win.screen() or QtWidgets.QApplication.primaryScreen()
    a = scr.availableGeometry()
    x, y, w, h = window_rect((a.x(), a.y(), a.width(), a.height()), saved=parse_rect(settings.value("window/rect", "")))
    win.setGeometry(x, y, w, h)
    win.setMinimumSize(min(MIN_CONTENT[0] // 2, a.width()), min(MIN_CONTENT[1] // 2, a.height()))


def remember_window(win, settings):
    g = win.normalGeometry() if win.isMaximized() or win.isFullScreen() else win.geometry()
    settings.setValue("window/rect", f"{g.x()},{g.y()},{g.width()},{g.height()}")
    settings.setValue("window/maximized", bool(win.isMaximized()))


def scrolling_central(content, min_size=MIN_CONTENT):
    """Wrap the tab widget so a small screen gets scroll bars instead of clipped controls."""
    content.setMinimumWidth(min_size[0])      # the height comes from the layouts themselves (never forced below what they need)
    s = QtWidgets.QScrollArea()
    s.setWidgetResizable(True)
    s.setFrameShape(QtWidgets.QFrame.NoFrame)
    s.setWidget(content)
    return s


def side_split(left_scroll, right, hint_extra=28, max_w=460):
    """Settings column | main area as a draggable splitter. The column opens wide enough for its widest control."""
    sp = QtWidgets.QSplitter(Qt.Horizontal)
    sp.setChildrenCollapsible(False)
    sp.addWidget(left_scroll)
    sp.addWidget(right)
    sp.setStretchFactor(0, 0)
    sp.setStretchFactor(1, 1)
    left_scroll.setMinimumWidth(240)

    def fit():                                           # after styling, when the controls' real widths are known
        inner = left_scroll.widget()
        if inner is None:
            return
        inner.ensurePolished()
        need = inner.sizeHint().width() + hint_extra
        want = max(300, min(max_w, need))
        left_scroll.setMinimumWidth(min(want, max(240, need)))
        if left_scroll.width() < want:
            sp.setSizes([want, max(400, sp.width() - want)])
    QtCore.QTimer.singleShot(0, fit)
    return sp


# ------------------------------------------------------------------------------------------------ show / hide panels
class PanelManager(QtCore.QObject):
    """Registry of the panels each tab offers. Visibility is remembered; the View menu and presets drive it."""

    changed = QtCore.Signal()

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.tabs = {}                       # tab key -> {"title": str, "panels": {key: dict}}
        self.groups = []                     # (container, [child widgets]): container hides when all its children are hidden

    def add(self, tab, key, label, widgets, default=True, title=None):
        t = self.tabs.setdefault(tab, {"title": title or tab, "panels": {}})
        if title:
            t["title"] = title
        ws = [w for w in (widgets if isinstance(widgets, (list, tuple)) else [widgets]) if w is not None]
        saved = self.settings.value(f"panels/{tab}/{key}", None)
        on = default if saved is None else str(saved).lower() in ("true", "1")
        t["panels"][key] = dict(label=label, widgets=ws, default=default, on=on)
        for w in ws:
            w.setVisible(on)

    def add_group(self, container, children):
        self.groups.append((container, list(children)))
        self._refresh_groups()

    def _refresh_groups(self):
        for c, kids in self.groups:
            c.setVisible(not all(k.isHidden() for k in kids))

    def is_on(self, tab, key):
        return self.tabs[tab]["panels"][key]["on"]

    def set_on(self, tab, key, on, remember=True):
        p = self.tabs[tab]["panels"][key]
        p["on"] = bool(on)
        for w in p["widgets"]:
            w.setVisible(bool(on))
        self._refresh_groups()
        if remember:
            self.settings.setValue(f"panels/{tab}/{key}", bool(on))
        self.changed.emit()

    def reset(self, tab=None):
        for tk, t in self.tabs.items():
            if tab in (None, tk):
                for k, p in t["panels"].items():
                    self.set_on(tk, k, p["default"])

    def apply_preset(self, tab, hidden):
        """Show everything on `tab` except the panel keys in `hidden`."""
        for k in self.tabs[tab]["panels"]:
            self.set_on(tab, k, k not in hidden)

    def populate_menu(self, menu, presets=None):
        """Fill the View menu: one submenu of check boxes per tab, presets, reset."""
        menu.clear()
        self._actions = {}
        for tk, t in self.tabs.items():
            sub = menu.addMenu(t["title"])
            for k, p in t["panels"].items():
                a = sub.addAction(p["label"])
                a.setCheckable(True)
                a.setChecked(p["on"])
                a.toggled.connect(lambda on, tk=tk, k=k: self.set_on(tk, k, on))
                self._actions[(tk, k)] = a
            sub.addSeparator()
            sub.addAction("Show all").triggered.connect(lambda _=False, tk=tk: self._show_all(tk))
        menu.addSeparator()
        for tab, name, hidden in (presets or []):
            menu.addAction(name).triggered.connect(lambda _=False, tab=tab, hidden=hidden: self.apply_preset(tab, hidden))
        menu.addSeparator()
        menu.addAction("Reset layout (all tabs)").triggered.connect(lambda: self.reset())
        self.changed.connect(self._sync)

    def _show_all(self, tab):
        self.apply_preset(tab, ())

    def _sync(self):
        for (tk, k), a in getattr(self, "_actions", {}).items():
            on = self.tabs[tk]["panels"][k]["on"]
            if a.isChecked() != on:
                a.blockSignals(True)
                a.setChecked(on)
                a.blockSignals(False)


# ------------------------------------------------------------------------------------------------ splitter memory
def _splitters(widget):
    return widget.findChildren(QtWidgets.QSplitter)


def save_splitters(tabs, settings):
    for key, w in tabs.items():
        sps = _splitters(w)
        settings.setValue(f"splitters/{key}/n", len(sps))
        for i, s in enumerate(sps):
            settings.setValue(f"splitters/{key}/{i}", s.saveState())


def restore_splitters(tabs, settings):
    for key, w in tabs.items():
        sps = _splitters(w)
        try:
            if int(settings.value(f"splitters/{key}/n", -1)) != len(sps):
                continue                                              # layout changed since it was saved
        except (TypeError, ValueError):
            continue
        for i, s in enumerate(sps):
            st = settings.value(f"splitters/{key}/{i}")
            if st:
                s.restoreState(st)


def reset_splitter_memory(settings):
    settings.remove("splitters")
