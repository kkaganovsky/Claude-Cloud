"""Look and feel shared by the app: light/dark themes, vector icons, toasts, flow layout, responsive grid.

Everything is drawn in logical pixels / points, so Qt scales it on high-DPI monitors (Windows, macOS Retina,
Linux with fractional scaling). The optional "Interface size" setting multiplies that (QT_SCALE_FACTOR).
"""
from __future__ import annotations

import math
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

Qt = QtCore.Qt

GOOD, WARN, BAD, INFO = "#16a34a", "#d97706", "#dc2626", "#2563eb"
LIGHT = dict(accent="#4f46e5", accent_dark="#4338ca", accent_soft="#eef2ff", accent_line="#c7d2fe",
             bg="#f5f7fb", card="#ffffff", line="#e4e7ee", text="#111827", muted="#6b7280", field="#ffffff",
             alt="#f9fafb", head="#f9fafb", disabled="#f3f4f6", side="#0f172a", side_text="#cbd5e1",
             side_hover="#1e293b", log="#0b1020", log_text="#d1d5db", shadow=18, viewer="#05070d")
DARK = dict(accent="#6366f1", accent_dark="#4f46e5", accent_soft="#1e1b4b", accent_line="#3730a3",
            bg="#0b1120", card="#111827", line="#1f2937", text="#e5e7eb", muted="#9ca3af", field="#0f172a",
            alt="#0f172a", head="#0f172a", disabled="#1f2937", side="#020617", side_text="#94a3b8",
            side_hover="#111827", log="#020617", log_text="#cbd5e1", shadow=60, viewer="#000000")
T: dict = dict(LIGHT)   # current theme tokens (read at render time)

FONT = '"Inter", "Segoe UI", ".AppleSystemUIFont", "Helvetica Neue", "Noto Sans", "Ubuntu", "Cantarell", Arial, sans-serif'


def qss(t: dict) -> str:
    return f"""
* {{ font-family: {FONT}; font-size: 10pt; }}
QMainWindow, QDialog {{ background: {t['bg']}; }}
QWidget {{ color: {t['text']}; }}
QWidget#content {{ background: {t['bg']}; }}
QToolTip {{ background: #111827; color: white; border: 1px solid #374151; padding: 6px 8px; border-radius: 6px; }}
QWidget#sidebar {{ background: {t['side']}; }}
QListWidget#nav {{ background: {t['side']}; border: none; padding: 8px 8px; outline: 0; }}
QListWidget#nav::item {{ color: {t['side_text']}; padding: 10px 10px; margin: 2px 0; border-radius: 10px; }}
QListWidget#nav::item:hover {{ background: {t['side_hover']}; color: white; }}
QListWidget#nav::item:selected {{ background: {t['accent']}; color: white; font-weight: 600; }}
QLabel#brand {{ color: white; font-size: 15pt; font-weight: 700; }}
QLabel#brandsub {{ color: #64748b; font-size: 9pt; }}
QLabel#h1 {{ font-size: 19pt; font-weight: 700; }}
QLabel#sub, QLabel#muted {{ color: {t['muted']}; }}
QLabel#cardtitle {{ color: {t['muted']}; font-size: 8.5pt; font-weight: 700; letter-spacing: 0.6px; }}
QFrame#card {{ background: {t['card']}; border: 1px solid {t['line']}; border-radius: 16px; }}
QFrame#banner {{ background: {t['accent_soft']}; border: 1px solid {t['accent_line']}; border-radius: 14px; }}
QFrame#toast {{ background: #111827; border: 1px solid #374151; border-radius: 12px; }}
QFrame#toast QLabel {{ color: #f9fafb; }}
QPushButton {{ background: {t['card']}; border: 1px solid {t['line']}; border-radius: 10px; padding: 7px 14px; }}
QPushButton:hover {{ border-color: {t['accent']}; background: {t['accent_soft']}; }}
QPushButton:pressed {{ background: {t['accent_line']}; }}
QPushButton:disabled {{ color: {t['muted']}; background: {t['disabled']}; border-color: {t['line']}; }}
QPushButton:checked {{ background: {t['accent']}; color: white; border-color: {t['accent_dark']}; }}
QPushButton[role="primary"] {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {t['accent']}, stop:1 #7c3aed);
    color: white; border: 1px solid {t['accent_dark']}; font-weight: 600; }}
QPushButton[role="primary"]:hover {{ background: {t['accent_dark']}; }}
QPushButton[role="primary"]:disabled {{ background: {t['accent_line']}; color: {t['muted']}; }}
QPushButton[role="danger"] {{ color: {BAD}; }}
QPushButton[role="danger"]:hover {{ background: rgba(220, 38, 38, 0.08); border-color: {BAD}; }}
QPushButton[role="ghost"] {{ background: transparent; border: 1px solid transparent; }}
QPushButton[role="ghost"]:hover {{ background: {t['accent_soft']}; border-color: {t['accent_line']}; }}
QPushButton[role="ghost"]:checked {{ background: {t['accent_soft']}; color: {t['accent']}; border-color: {t['accent_line']}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox, QComboBox {{
    background: {t['field']}; border: 1px solid {t['line']}; border-radius: 9px; padding: 6px 9px; selection-background-color: {t['accent']}; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0; border: none; }}
QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {t['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {t['card']}; border: 1px solid {t['line']}; selection-background-color: {t['accent_soft']}; selection-color: {t['text']}; }}
QPlainTextEdit#log {{ background: {t['log']}; color: {t['log_text']}; font-family: "JetBrains Mono", "Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono", monospace;
    font-size: 9pt; border-radius: 12px; border: none; padding: 10px; }}
QTableWidget, QListWidget {{ background: {t['card']}; alternate-background-color: {t['alt']}; border: 1px solid {t['line']}; border-radius: 12px;
    gridline-color: {t['line']}; selection-background-color: {t['accent_soft']}; selection-color: {t['text']}; outline: 0; }}
QListWidget#cases::item {{ padding: 8px 8px; border-radius: 8px; }}
QListWidget#cases::item:selected {{ background: {t['accent_soft']}; }}
QHeaderView::section {{ background: {t['head']}; color: {t['muted']}; border: none; border-bottom: 1px solid {t['line']}; padding: 7px 9px; font-weight: 600; }}
QTableCornerButton::section {{ background: {t['head']}; border: none; }}
QProgressBar {{ background: {t['line']}; border: none; border-radius: 5px; max-height: 10px; text-align: center; font-size: 7pt; color: transparent; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t['accent']}, stop:1 #7c3aed); border-radius: 5px; }}
QSlider::groove:horizontal {{ height: 5px; background: {t['line']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {t['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {t['card']}; border: 2px solid {t['accent']}; width: 14px; height: 14px; margin: -6px 0; border-radius: 9px; }}
QCheckBox {{ spacing: 8px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['line']}; border-radius: 4px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t['line']}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:hover {{ background: {t['accent_line']}; }}
QStatusBar {{ background: {t['card']}; color: {t['muted']}; border-top: 1px solid {t['line']}; }}
"""


def system_is_dark() -> bool:
    try:
        return QtGui.QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:  # Qt < 6.5
        pal = QtGui.QGuiApplication.palette()
        return pal.color(QtGui.QPalette.Window).lightness() < 128


def apply_theme(app: QtWidgets.QApplication, mode: str) -> None:
    """mode: 'system' | 'light' | 'dark'."""
    dark = system_is_dark() if mode == "system" else mode == "dark"
    T.clear(); T.update(DARK if dark else LIGHT)
    T["is_dark"] = dark
    pal = QtGui.QPalette()
    for role, key in [(QtGui.QPalette.Window, "bg"), (QtGui.QPalette.Base, "field"), (QtGui.QPalette.Text, "text"),
                      (QtGui.QPalette.WindowText, "text"), (QtGui.QPalette.Button, "card"),
                      (QtGui.QPalette.ButtonText, "text"), (QtGui.QPalette.Highlight, "accent"),
                      (QtGui.QPalette.AlternateBase, "alt"), (QtGui.QPalette.PlaceholderText, "muted")]:
        pal.setColor(role, QtGui.QColor(T[key]))
    pal.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("white"))
    app.setPalette(pal)
    app.setStyleSheet(qss(T))


# ---------------------------------------------------------------------------------------------------------------
# Vector icons (drawn, so they are sharp at any DPI and look the same on every OS)
def icon(name: str, color: str = "#cbd5e1", size: int = 20) -> QtGui.QIcon:
    ic = QtGui.QIcon()
    for scale in (1, 2, 3):
        pm = QtGui.QPixmap(size * scale, size * scale)
        pm.fill(Qt.transparent)
        pm.setDevicePixelRatio(scale)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        pen = QtGui.QPen(QtGui.QColor(color), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        s = size
        r = QtCore.QRectF
        if name == "home":
            path = QtGui.QPainterPath()
            path.moveTo(s * .18, s * .48); path.lineTo(s * .5, s * .2); path.lineTo(s * .82, s * .48)
            p.drawPath(path)
            p.drawRoundedRect(r(s * .28, s * .45, s * .44, s * .37), 2, 2)
        elif name == "segment":
            p.drawEllipse(QtCore.QPointF(s * .5, s * .5), s * .3, s * .3)
            p.drawEllipse(QtCore.QPointF(s * .5, s * .5), s * .1, s * .1)
            for a in range(4):
                ang = a * math.pi / 2
                p.drawLine(QtCore.QPointF(s * .5 + math.cos(ang) * s * .32, s * .5 + math.sin(ang) * s * .32),
                           QtCore.QPointF(s * .5 + math.cos(ang) * s * .44, s * .5 + math.sin(ang) * s * .44))
        elif name == "review":
            path = QtGui.QPainterPath()
            path.moveTo(s * .25, s * .75); path.lineTo(s * .3, s * .58); path.lineTo(s * .66, s * .22)
            path.lineTo(s * .78, s * .34); path.lineTo(s * .42, s * .7); path.closeSubpath()
            p.drawPath(path)
            p.drawLine(QtCore.QPointF(s * .2, s * .82), QtCore.QPointF(s * .8, s * .82))
        elif name == "models":
            p.drawRoundedRect(r(s * .2, s * .2, s * .6, s * .6), 3, 3)
            p.drawLine(QtCore.QPointF(s * .2, s * .43), QtCore.QPointF(s * .8, s * .43))
            p.drawLine(QtCore.QPointF(s * .43, s * .43), QtCore.QPointF(s * .43, s * .8))
        elif name == "train":
            p.drawArc(r(s * .2, s * .2, s * .6, s * .6), 40 * 16, 280 * 16)
            path = QtGui.QPainterPath()
            path.moveTo(s * .74, s * .18); path.lineTo(s * .76, s * .34); path.lineTo(s * .6, s * .34)
            p.drawPath(path)
        elif name == "settings":
            p.drawEllipse(QtCore.QPointF(s * .5, s * .5), s * .12, s * .12)
            for a in range(8):
                ang = a * math.pi / 4
                p.drawLine(QtCore.QPointF(s * .5 + math.cos(ang) * s * .22, s * .5 + math.sin(ang) * s * .22),
                           QtCore.QPointF(s * .5 + math.cos(ang) * s * .34, s * .5 + math.sin(ang) * s * .34))
            p.drawEllipse(QtCore.QPointF(s * .5, s * .5), s * .23, s * .23)
        elif name == "check":
            pen.setWidthF(2.4); p.setPen(pen)
            path = QtGui.QPainterPath()
            path.moveTo(s * .25, s * .52); path.lineTo(s * .43, s * .7); path.lineTo(s * .76, s * .3)
            p.drawPath(path)
        elif name == "dot":
            p.setPen(Qt.NoPen); p.setBrush(QtGui.QColor(color))
            p.drawEllipse(QtCore.QPointF(s * .5, s * .5), s * .28, s * .28)
        elif name == "folder":
            path = QtGui.QPainterPath()
            path.moveTo(s * .15, s * .3); path.lineTo(s * .4, s * .3); path.lineTo(s * .48, s * .38)
            path.lineTo(s * .85, s * .38); path.lineTo(s * .85, s * .75); path.lineTo(s * .15, s * .75); path.closeSubpath()
            p.drawPath(path)
        p.end()
        ic.addPixmap(pm)
    return ic


# ---------------------------------------------------------------------------------------------------------------
class Toast(QtWidgets.QFrame):
    """Small message that slides in at the bottom-right of the window and fades out."""

    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setObjectName("toast")
        lay = QtWidgets.QHBoxLayout(self); lay.setContentsMargins(14, 10, 14, 10); lay.setSpacing(10)
        self.dot = QtWidgets.QLabel(); lay.addWidget(self.dot)
        self.label = QtWidgets.QLabel(); self.label.setWordWrap(True); self.label.setMaximumWidth(460)
        lay.addWidget(self.label)
        self.fx = QtWidgets.QGraphicsOpacityEffect(self); self.setGraphicsEffect(self.fx)
        self.anim = QtCore.QPropertyAnimation(self.fx, b"opacity", self)
        self.timer = QtCore.QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self.fade)
        self.hide()

    def show_message(self, text: str, kind: str = "info", ms: int = 4500):
        color = {"ok": GOOD, "warn": WARN, "error": BAD}.get(kind, "#818cf8")
        self.dot.setPixmap(icon("dot", color, 14).pixmap(14, 14))
        self.label.setText(text)
        self.adjustSize()
        self.reposition()
        self.anim.stop(); self.fx.setOpacity(0.0); self.show(); self.raise_()
        self.anim.setDuration(180); self.anim.setStartValue(0.0); self.anim.setEndValue(1.0); self.anim.start()
        self.timer.start(ms)

    def fade(self):
        self.anim.stop(); self.anim.setDuration(400); self.anim.setStartValue(1.0); self.anim.setEndValue(0.0)
        self.anim.finished.connect(self._hide_once)
        self.anim.start()

    def _hide_once(self):
        try:
            self.anim.finished.disconnect(self._hide_once)
        except (RuntimeError, TypeError):
            pass
        if self.fx.opacity() < 0.05:
            self.hide()

    def reposition(self):
        par = self.parentWidget()
        if par is not None:
            self.move(par.width() - self.width() - 24, par.height() - self.height() - 36)


class FlowLayout(QtWidgets.QLayout):
    """Lays widgets out left to right and wraps to the next line when the window is narrow."""

    def __init__(self, parent=None, spacing: int = 8):
        super().__init__(parent)
        self._items: list = []
        self._sp = spacing
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
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QtCore.QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QtCore.QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return size + QtCore.QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do(self, rect, test):
        """Two passes: break items into lines, then centre each item vertically within its line."""
        lines, cur, x = [], [], rect.x()
        for it in self._items:
            if it.widget() is not None and it.widget().isHidden():
                continue
            w = it.sizeHint().width()
            if cur and x + w > rect.right():
                lines.append(cur); cur, x = [], rect.x()
            cur.append(it); x += w + self._sp
        if cur:
            lines.append(cur)
        y = rect.y()
        for line in lines:
            lh = max(it.sizeHint().height() for it in line)
            x = rect.x()
            for it in line:
                hint = it.sizeHint()
                if not test:
                    it.setGeometry(QtCore.QRect(QtCore.QPoint(x, y + (lh - hint.height()) // 2), hint))
                x += hint.width() + self._sp
            y += lh + self._sp
        return max(0, y - self._sp - rect.y())


class ResponsiveGrid(QtWidgets.QWidget):
    """Cards side by side on wide windows, stacked on narrow ones."""

    def __init__(self, min_card_width: int = 300, spacing: int = 16):
        super().__init__()
        self.min_w = min_card_width
        self.grid = QtWidgets.QGridLayout(self); self.grid.setContentsMargins(0, 0, 0, 0); self.grid.setSpacing(spacing)
        self.widgets: list[QtWidgets.QWidget] = []
        self._cols = 0
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)

    def add(self, w: QtWidgets.QWidget):
        self.widgets.append(w); self._relayout(force=True)

    def resizeEvent(self, e):
        super().resizeEvent(e); self._relayout()

    def _relayout(self, force=False):
        cols = max(1, min(len(self.widgets), self.width() // self.min_w)) if self.width() > 0 else len(self.widgets)
        if cols == self._cols and not force:
            return
        self._cols = cols
        for w in self.widgets:
            self.grid.removeWidget(w)
        for c in range(self.grid.columnCount()):
            self.grid.setColumnStretch(c, 0)
        for i, w in enumerate(self.widgets):
            self.grid.addWidget(w, i // cols, i % cols)
        for c in range(cols):
            self.grid.setColumnStretch(c, 1)


def fit_to_screen(win: QtWidgets.QWidget, geometry: Optional[QtCore.QByteArray]) -> None:
    """Restore the saved window geometry if it is still visible (monitors change), else size to the current screen."""
    if geometry is not None and win.restoreGeometry(geometry):
        frame = win.frameGeometry()
        for scr in QtGui.QGuiApplication.screens():
            inter = scr.availableGeometry().intersected(frame)
            if inter.width() * inter.height() > 0.5 * frame.width() * frame.height():
                return
    scr = (win.screen() or QtGui.QGuiApplication.primaryScreen()).availableGeometry()
    w = min(1500, max(960, int(scr.width() * 0.88)))
    h = min(960, max(600, int(scr.height() * 0.88)))
    w, h = min(w, scr.width()), min(h, scr.height())
    win.resize(w, h)
    win.move(scr.center() - QtCore.QPoint(w // 2, h // 2))
