"""One modern, flat look for the whole app: Fusion base + a single stylesheet (cards, soft borders, accent colour, hover states)."""
from PySide6 import QtGui, QtWidgets

ACCENT, ACCENT_DARK, ACCENT_SOFT = "#3b6ef5", "#2c55c9", "#e9effe"
GOOD, BAD = "#2e9e57", "#d64545"
BG, CARD, LINE, TEXT, MUTED = "#f4f6fa", "#ffffff", "#dfe3eb", "#1f2430", "#667085"

QSS = f"""
* {{ font-family: "Inter", "Segoe UI", "SF Pro Text", "Helvetica Neue", "Noto Sans", Arial, sans-serif; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget {{ color: {TEXT}; }}
QToolTip {{ background: #1f2430; color: white; border: none; padding: 5px 8px; border-radius: 5px; }}
QMenuBar {{ background: {CARD}; border-bottom: 1px solid {LINE}; }}
QMenuBar::item {{ padding: 5px 10px; border-radius: 5px; }}
QMenuBar::item:selected {{ background: {ACCENT_SOFT}; }}
QMenu {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 8px; padding: 5px; }}
QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 5px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT_DARK}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 5px 6px; }}
QToolBar {{ background: {CARD}; border: none; border-bottom: 1px solid {LINE}; padding: 3px 6px; spacing: 6px; }}
QToolBar QToolButton {{ padding: 4px 10px; border-radius: 6px; border: 1px solid transparent; }}
QToolBar QToolButton:hover {{ background: {ACCENT_SOFT}; }}
QToolBar QToolButton:disabled {{ color: #b3b8c4; }}
QStatusBar {{ background: {CARD}; color: {MUTED}; border-top: 1px solid {LINE}; }}

QTabWidget::pane {{ border: none; background: {BG}; top: -1px; }}
QTabBar {{ background: {CARD}; }}
QTabBar::tab {{ background: transparent; padding: 9px 16px; margin-right: 2px; color: {MUTED}; border-bottom: 3px solid transparent; }}
QTabBar::tab:hover {{ color: {TEXT}; background: {ACCENT_SOFT}; }}
QTabBar::tab:selected {{ color: {ACCENT_DARK}; font-weight: 600; border-bottom: 3px solid {ACCENT}; }}

QGroupBox {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 10px; margin-top: 14px; padding: 10px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 12px; padding: 0 6px; color: {MUTED}; font-weight: 600; }}

QPushButton {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 7px; padding: 6px 12px; }}
QPushButton:hover {{ border-color: {ACCENT}; background: {ACCENT_SOFT}; }}
QPushButton:pressed {{ background: #d7e1fd; }}
QPushButton:disabled {{ color: #aab0be; background: #f0f2f6; }}
QPushButton[role="primary"] {{ background: {ACCENT}; color: white; border-color: {ACCENT_DARK}; font-weight: 600; }}
QPushButton[role="primary"]:hover {{ background: {ACCENT_DARK}; }}

QToolButton {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 7px; padding: 4px 10px; }}
QToolButton:hover {{ border-color: {ACCENT}; background: {ACCENT_SOFT}; }}
QToolButton:checked {{ background: {GOOD}; color: white; border-color: #23803f; font-weight: 600; }}
QToolButton:pressed {{ background: #d7e1fd; }}

QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox, QComboBox {{
    background: {CARD}; border: 1px solid {LINE}; border-radius: 6px; padding: 3px 6px; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border: 1px solid {ACCENT}; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0; border: none; }}
QLineEdit:read-only {{ background: #f0f2f6; }}
QComboBox QAbstractItemView {{ background: {CARD}; border: 1px solid {LINE}; selection-background-color: {ACCENT_SOFT}; selection-color: {ACCENT_DARK}; }}
QCheckBox {{ spacing: 7px; }}

QTableWidget, QTableView {{ background: {CARD}; alternate-background-color: #f8f9fc; border: 1px solid {LINE}; border-radius: 8px;
    gridline-color: #eceff5; selection-background-color: {ACCENT_SOFT}; selection-color: {ACCENT_DARK}; }}
QHeaderView::section {{ background: #f0f2f7; color: {MUTED}; border: none; border-right: 1px solid {LINE}; padding: 5px 8px; font-weight: 600; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle {{ background: #c4cad8; border-radius: 4px; min-height: 28px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: #9aa3b8; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 7px; }}
QSplitter::handle:vertical {{ height: 7px; }}
QSplitter::handle:hover {{ background: {ACCENT_SOFT}; }}
QProgressBar {{ background: #e6e9f0; border: none; border-radius: 5px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
"""


def apply(app):
    """Fusion + light palette + the stylesheet. Call once, before the first window is created."""
    app.setStyle("Fusion")
    pal = QtGui.QPalette()
    pal.setColor(QtGui.QPalette.Window, QtGui.QColor(BG))
    pal.setColor(QtGui.QPalette.Base, QtGui.QColor(CARD))
    pal.setColor(QtGui.QPalette.Text, QtGui.QColor(TEXT))
    pal.setColor(QtGui.QPalette.WindowText, QtGui.QColor(TEXT))
    pal.setColor(QtGui.QPalette.ButtonText, QtGui.QColor(TEXT))
    pal.setColor(QtGui.QPalette.Highlight, QtGui.QColor(ACCENT))
    pal.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("white"))
    app.setPalette(pal)
    f = app.font()
    f.setPointSizeF(max(f.pointSizeF(), 10.0))
    app.setFont(f)
    app.setStyleSheet(QSS)


def primary(button):
    """Mark a QPushButton as the main action of its panel."""
    button.setProperty("role", "primary")
    return button
