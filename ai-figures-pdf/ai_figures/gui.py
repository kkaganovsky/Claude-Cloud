"""Drag-and-drop GUI: drop a folder of .ai files, check the order, set DPI / page size, export PNGs + one PDF."""
from __future__ import annotations

import html
import sys
import time
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from . import core

COLORS = {"info": "#475467", "ok": "#2e9e57", "warn": "#b7791f", "error": "#d64545"}
QSS = """
QMainWindow { background: #f4f6fa; }
QGroupBox { background: white; border: 1px solid #dfe3eb; border-radius: 9px; margin-top: 14px; padding: 10px 8px 8px 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #667085; font-weight: 600; }
QPushButton { background: white; border: 1px solid #dfe3eb; border-radius: 7px; padding: 6px 12px; }
QPushButton:hover { border-color: #3b6ef5; background: #e9effe; }
QPushButton:disabled { color: #aab0be; background: #f0f2f6; }
QPushButton[role="primary"] { background: #3b6ef5; color: white; border-color: #2c55c9; font-weight: 600; }
QPushButton[role="primary"]:disabled { background: #a9bdf7; }
QTableWidget { background: white; border: 1px solid #dfe3eb; border-radius: 7px; gridline-color: #eceff5;
               selection-background-color: #e9effe; selection-color: #1f2430; }
QHeaderView::section { background: #f0f2f7; color: #667085; border: none; padding: 5px 8px; font-weight: 600; }
QPlainTextEdit, QTextEdit { background: white; border: 1px solid #dfe3eb; border-radius: 7px; }
"""
COLS = ["Use", "#", "Detected as", "File", "Artboard 1", "Status"]


class DropZone(QtWidgets.QLabel):
    dropped = QtCore.Signal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setMinimumHeight(90)
        self._set(False)

    def _set(self, hover):
        self.setText("<b>Drop a folder of .ai files here</b><br><span style='color:#667085'>"
                     "(or individual .ai files, or click to browse)</span>")
        self.setStyleSheet(f"QLabel {{ border: 2px dashed {'#3b6ef5' if hover else '#b8c0d0'}; border-radius: 12px;"
                           f" background: {'#e9effe' if hover else 'white'}; font-size: 14px; }}")

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._set(True)

    def dragLeaveEvent(self, e):
        self._set(False)

    def dropEvent(self, e):
        self._set(False)
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.dropped.emit(paths)

    def mousePressEvent(self, e):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Folder of .ai files")
        if d:
            self.dropped.emit([d])


class Worker(QtCore.QObject):
    log = QtCore.Signal(str, str)
    progress = QtCore.Signal(int, int)
    finished = QtCore.Signal(object)

    def __init__(self, figs, settings):
        super().__init__()
        self.figs, self.settings, self._cancel = figs, settings, False

    def cancel(self):
        self._cancel = True

    @QtCore.Slot()
    def go(self):
        try:
            pdf = core.run(self.figs, self.settings, self.log.emit, self.progress.emit, lambda: self._cancel)
        except Exception as e:  # noqa: BLE001
            self.log.emit("error", f"Unexpected failure: {type(e).__name__}: {e}")
            pdf = None
        self.finished.emit(pdf)


class Main(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI figures -> PDF")
        self.resize(1100, 820)
        self.figs: list[core.Figure] = []
        self.inputs: list[str] = []
        self.log_lines: list[str] = []
        self.thread = None
        self.worker = None

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        lay = QtWidgets.QVBoxLayout(central)

        self.drop = DropZone()
        self.drop.dropped.connect(self.load)
        lay.addWidget(self.drop)

        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        lay.addWidget(split, 1)

        # ---- file list ----
        top = QtWidgets.QWidget()
        tl = QtWidgets.QHBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        box = QtWidgets.QGroupBox("Figures (PDF page order)")
        bl = QtWidgets.QVBoxLayout(box)
        self.table = QtWidgets.QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        h = self.table.horizontalHeader()
        RM = QtWidgets.QHeaderView.ResizeMode
        for i, mode in enumerate([RM.ResizeToContents, RM.ResizeToContents, RM.ResizeToContents, RM.Stretch,
                                  RM.ResizeToContents, RM.ResizeToContents]):
            h.setSectionResizeMode(i, mode)
        self.table.itemChanged.connect(self._check_changed)
        bl.addWidget(self.table)
        row = QtWidgets.QHBoxLayout()
        self.b_up, self.b_down = QtWidgets.QPushButton("Move up"), QtWidgets.QPushButton("Move down")
        self.b_sort = QtWidgets.QPushButton("Re-sort by figure number")
        self.b_up.clicked.connect(lambda: self.move(-1))
        self.b_down.clicked.connect(lambda: self.move(1))
        self.b_sort.clicked.connect(self.resort)
        self.recursive = QtWidgets.QCheckBox("Include subfolders")
        self.recursive.toggled.connect(lambda: self.inputs and self.load(self.inputs))
        for w in (self.b_up, self.b_down, self.b_sort):
            row.addWidget(w)
        row.addStretch()
        row.addWidget(self.recursive)
        bl.addLayout(row)
        tl.addWidget(box, 1)

        # ---- settings ----
        sbox = QtWidgets.QGroupBox("Settings")
        form = QtWidgets.QFormLayout(sbox)
        self.dpi = QtWidgets.QSpinBox()
        self.dpi.setRange(36, 2400)
        self.dpi.setValue(300)
        self.dpi.setSuffix(" dpi")
        self.dpi.setSingleStep(50)
        self.transparent = QtWidgets.QCheckBox("Transparent PNG background")
        self.page = QtWidgets.QComboBox()
        self.page.addItems(list(core.PAGE_SIZES_IN) + [core.LARGEST, core.CUSTOM])
        self.cw, self.ch = QtWidgets.QDoubleSpinBox(), QtWidgets.QDoubleSpinBox()
        for sp, v in ((self.cw, 8.5), (self.ch, 11.0)):
            sp.setRange(1, 100)
            sp.setDecimals(2)
            sp.setValue(v)
            sp.setSuffix(" in")
        cust = QtWidgets.QHBoxLayout()
        cust.addWidget(self.cw)
        cust.addWidget(QtWidgets.QLabel("x"))
        cust.addWidget(self.ch)
        self.page.currentTextChanged.connect(lambda t: (self.cw.setEnabled(t == core.CUSTOM), self.ch.setEnabled(t == core.CUSTOM)))
        self.cw.setEnabled(False)
        self.ch.setEnabled(False)
        self.margin = QtWidgets.QDoubleSpinBox()
        self.margin.setRange(0, 5)
        self.margin.setSingleStep(0.25)
        self.margin.setValue(0.5)
        self.margin.setSuffix(" in")
        self.scale = QtWidgets.QComboBox()
        self.scale.addItem("Fit every figure to the page", "fit")
        self.scale.addItem("Native size (shrink only if too big)", "shrink")
        self.scale.setToolTip("Fit: every figure fills the printable area (same visual size page to page).\n"
                              "Native: figures keep their Illustrator size, so text sizes match if artboards match.")
        self.align = QtWidgets.QComboBox()
        self.align.addItem("Top", "top")
        self.align.addItem("Center", "center")
        self.bookmarks = QtWidgets.QCheckBox("PDF bookmarks per figure")
        self.bookmarks.setChecked(True)
        self.out = QtWidgets.QLineEdit()
        self.out.setPlaceholderText("default: <input folder>/figures_export")
        ob = QtWidgets.QPushButton("...")
        ob.setFixedWidth(34)
        ob.clicked.connect(self._pick_out)
        orow = QtWidgets.QHBoxLayout()
        orow.addWidget(self.out)
        orow.addWidget(ob)
        self.name = QtWidgets.QLineEdit("figures.pdf")
        form.addRow("PNG resolution", self.dpi)
        form.addRow("", self.transparent)
        form.addRow("PDF page size", self.page)
        form.addRow("Custom size", cust)
        form.addRow("Margin", self.margin)
        form.addRow("Scaling", self.scale)
        form.addRow("Vertical align", self.align)
        form.addRow("", self.bookmarks)
        form.addRow("Output folder", orow)
        form.addRow("PDF name", self.name)
        sbox.setFixedWidth(380)
        tl.addWidget(sbox)
        split.addWidget(top)

        # ---- log ----
        lbox = QtWidgets.QGroupBox("Log (errors and issues)")
        ll = QtWidgets.QVBoxLayout(lbox)
        self.logview = QtWidgets.QTextEdit()
        self.logview.setReadOnly(True)
        self.logview.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        ll.addWidget(self.logview)
        self.counts = QtWidgets.QLabel("")
        ll.addWidget(self.counts)
        split.addWidget(lbox)
        split.setSizes([480, 260])

        bottom = QtWidgets.QHBoxLayout()
        self.bar = QtWidgets.QProgressBar()
        self.bar.setTextVisible(True)
        self.b_cancel = QtWidgets.QPushButton("Cancel")
        self.b_cancel.setEnabled(False)
        self.b_open = QtWidgets.QPushButton("Open output folder")
        self.b_open.setEnabled(False)
        self.b_go = QtWidgets.QPushButton("Export PNGs + PDF")
        self.b_go.setProperty("role", "primary")
        self.b_go.setEnabled(False)
        self.b_go.clicked.connect(self.export)
        self.b_cancel.clicked.connect(self._cancel)
        self.b_open.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self._out_dir()))))
        bottom.addWidget(self.bar, 1)
        bottom.addWidget(self.b_cancel)
        bottom.addWidget(self.b_open)
        bottom.addWidget(self.b_go)
        lay.addLayout(bottom)
        self.n = {"warn": 0, "error": 0}

    # ------------------------------------------------------------------ logging
    @QtCore.Slot(str, str)
    def log(self, lvl, msg):
        stamp = time.strftime("%H:%M:%S")
        self.log_lines.append(f"{stamp} [{lvl.upper():5}] {msg}")
        tag = {"info": "", "ok": "OK  ", "warn": "WARN", "error": "ERR "}[lvl]
        self.logview.append(f"<span style='color:#98a2b3'>{stamp}</span> <span style='color:{COLORS[lvl]};"
                            f"{'font-weight:600' if lvl in ('warn', 'error') else ''}'>{tag} {html.escape(msg)}</span>")
        if lvl in self.n:
            self.n[lvl] += 1
            self.counts.setText(f"<span style='color:{COLORS['error']}'>{self.n['error']} error(s)</span> &nbsp; "
                                f"<span style='color:{COLORS['warn']}'>{self.n['warn']} warning(s)</span>")

    # ------------------------------------------------------------------ loading
    def load(self, paths):
        self.inputs = list(paths)
        self.logview.clear()
        self.log_lines.clear()
        self.n = {"warn": 0, "error": 0}
        self.counts.setText("")
        self.log("info", "Input: " + "; ".join(paths))
        files = core.find_ai_files(paths, self.recursive.isChecked(), self.log)
        if not files:
            self.log("error", "No .ai files found" + ("" if self.recursive.isChecked() else " (try 'Include subfolders')"))
            self.figs = []
            self.refresh()
            return
        self.figs = core.build_figures(files, self.log)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            for f in self.figs:
                core.probe(f)
                if f.probe_error:
                    f.include = False
                    self.log("error", f"{f.path.name}: {f.probe_error} (unticked; it will be skipped)")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        ok = sum(1 for f in self.figs if not f.probe_error)
        self.log("info", f"Found {len(self.figs)} .ai file(s); {ok} readable. Check the order below, then Export.")
        self.refresh()

    def refresh(self, select=None):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.figs))
        for r, f in enumerate(self.figs):
            chk = QtWidgets.QTableWidgetItem()
            chk.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            chk.setCheckState(QtCore.Qt.Checked if f.include else QtCore.Qt.Unchecked)
            self.table.setItem(r, 0, chk)
            vals = [str(r + 1), f.label, f.path.name, f.size_text,
                    "error" if f.probe_error else (f"{f.n_pages} artboards/pages" if (f.n_pages or 0) > 1 else "ok")]
            for c, v in enumerate(vals, 1):
                it = QtWidgets.QTableWidgetItem(v)
                if f.probe_error:
                    it.setForeground(QtGui.QColor(COLORS["error"]))
                    it.setToolTip(f.probe_error)
                elif f.category == core.OTHER and c == 2:
                    it.setForeground(QtGui.QColor(COLORS["warn"]))
                self.table.setItem(r, c, it)
        self.table.blockSignals(False)
        if select is not None:
            self.table.selectRow(select)
        self.b_go.setEnabled(any(f.include and not f.probe_error for f in self.figs) and self.thread is None)

    def _check_changed(self, item):
        if item.column() == 0:
            self.figs[item.row()].include = item.checkState() == QtCore.Qt.Checked
            self.b_go.setEnabled(any(f.include and not f.probe_error for f in self.figs))

    def move(self, d):
        r = self.table.currentRow()
        j = r + d
        if r < 0 or not (0 <= j < len(self.figs)):
            return
        self.figs[r], self.figs[j] = self.figs[j], self.figs[r]
        self.refresh(select=j)

    def resort(self):
        self.figs.sort(key=lambda g: g.sort_key)
        self.refresh()

    # ------------------------------------------------------------------ export
    def _pick_out(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Output folder")
        if d:
            self.out.setText(d)

    def _out_dir(self) -> Path:
        if self.out.text().strip():
            return Path(self.out.text().strip())
        first = Path(self.inputs[0]) if self.inputs else Path.home()
        return (first if first.is_dir() else first.parent) / "figures_export"

    def settings(self) -> core.Settings:
        return core.Settings(dpi=self.dpi.value(), transparent=self.transparent.isChecked(),
                             page_size=self.page.currentText(), custom_w_in=self.cw.value(), custom_h_in=self.ch.value(),
                             margin_in=self.margin.value(), scale_mode=self.scale.currentData(),
                             align=self.align.currentData(), out_dir=self._out_dir(),
                             pdf_name=self.name.text().strip() or "figures.pdf", bookmarks=self.bookmarks.isChecked())

    def export(self):
        s = self.settings()
        pdf = Path(s.out_dir) / s.pdf_name
        if pdf.exists() and QtWidgets.QMessageBox.question(self, "Overwrite?", f"{pdf} exists. Overwrite it?") \
                != QtWidgets.QMessageBox.Yes:
            return
        self.log("info", "-" * 60)
        self.n = {"warn": 0, "error": 0}
        self.thread = QtCore.QThread()
        self.worker = Worker(self.figs, s)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.go)
        self.worker.log.connect(self.log)
        self.worker.progress.connect(lambda i, n: (self.bar.setMaximum(n), self.bar.setValue(i)))
        self.worker.finished.connect(self._done)
        self.b_go.setEnabled(False)
        self.b_cancel.setEnabled(True)
        self.drop.setEnabled(False)
        self.thread.start()

    def _cancel(self):
        if self.worker:
            self.worker.cancel()

    def _done(self, pdf):
        self.thread.quit()
        self.thread.wait()
        self.thread = None
        self.b_cancel.setEnabled(False)
        self.drop.setEnabled(True)
        self.refresh()
        out = self._out_dir()
        try:
            p = core.write_log(out, self.log_lines)
            self.log("info", f"Log saved to {p}")
        except OSError as e:
            self.log("error", f"Could not save log: {e}")
        self.b_open.setEnabled(out.exists())
        if pdf:
            self.bar.setValue(self.bar.maximum())


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
    w = Main()
    w.show()
    if len(sys.argv) > 1:
        w.load(sys.argv[1:])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
