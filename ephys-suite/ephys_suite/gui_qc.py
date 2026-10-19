"""Access-resistance QC tab: scan a folder tree of EPSC / CM recordings and check Ra against a limit."""
import os

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

from . import qc
from .gui import fill, plot, table

STATUS_COLORS = {"PASS": (198, 239, 206), "WARN": (255, 235, 156), "FAIL": (255, 199, 206), "NO PULSE": (217, 217, 217),
                 "ERROR": (255, 199, 206)}
BLUE, RED, GREEN, GREY = (50, 90, 220), (220, 30, 30), (40, 160, 70), (150, 150, 150)


class QCTab(QtWidgets.QWidget):
    COLS = ["Line", "Cell", "Role", "File", "Sweeps ok", "Ra mean", "Ra max", "Rm mean", "Drift", "Status"]

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.settings = QtCore.QSettings("ephys-suite", "qc")
        self.report = None
        self._cache = {}
        self._rows = []
        v = QtWidgets.QVBoxLayout(self)
        # ---- controls
        top = QtWidgets.QHBoxLayout()
        b = QtWidgets.QPushButton("Choose folder…")
        b.clicked.connect(self.choose_folder)
        self.lbl_root = QtWidgets.QLabel("no folder chosen")
        self.lbl_root.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(b)
        top.addWidget(self.lbl_root, 1)
        self.sp_limit = self._spin(25.0, 1, 1000, " MΩ", "Ra must be below this (every sweep)")
        self.sp_v = self._spin(-10.0, -200, 200, " mV", "Test-pulse step, used ONLY when the file has no command-voltage channel "
                                                      "(the sign comes from the data)")
        self.sp_drift = self._spin(20.0, 1, 200, " %", "Drift flag: any sweep's Ra this far from the first sweep's (your RaExclude rule)")
        for lbl, w in (("Ra limit", self.sp_limit), ("assumed step", self.sp_v), ("drift", self.sp_drift)):
            top.addWidget(QtWidgets.QLabel(lbl))
            top.addWidget(w)
        self.btn_scan = QtWidgets.QPushButton("Scan")
        self.btn_scan.clicked.connect(self.scan)
        self.btn_exp = QtWidgets.QPushButton("Export…")
        self.btn_exp.clicked.connect(self.export)
        self.chk_bad = QtWidgets.QCheckBox("only problems")
        self.chk_bad.toggled.connect(self._fill)
        for w in (self.btn_scan, self.btn_exp, self.chk_bad):
            top.addWidget(w)
        v.addLayout(top)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setMaximumHeight(10)
        self.bar.setTextVisible(False)
        v.addWidget(self.bar)
        self.lbl_sum = QtWidgets.QLabel("Choose the folder that holds the cells (e.g. 'hFA in vitro 10162026') and press Scan.")
        self.lbl_sum.setWordWrap(True)
        self.lbl_sum.setStyleSheet("padding:4px;")
        v.addWidget(self.lbl_sum)
        # ---- table | plots
        sp = QtWidgets.QSplitter()
        self.tbl = table(self.COLS)
        self.tbl.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl.itemSelectionChanged.connect(self._select)
        sp.addWidget(self.tbl)
        right = QtWidgets.QSplitter(Qt.Vertical)
        self.pp = plot("Test pulse: all sweeps (first sweep bold)", "ms", "pA")
        self.pr = plot("Ra per sweep", "sweep", "MΩ")
        right.addWidget(self.pp)
        right.addWidget(self.pr)
        self.txt = QtWidgets.QPlainTextEdit()
        self.txt.setReadOnly(True)
        self.txt.setPlaceholderText("Detailed report")
        right.addWidget(self.txt)
        right.setSizes([340, 220, 160])
        sp.addWidget(right)
        sp.setSizes([760, 760])
        v.addWidget(sp, 1)
        root = self.settings.value("root", "")
        if root and os.path.isdir(root):
            self.lbl_root.setText(root)

    @staticmethod
    def _spin(val, lo, hi, suffix, tip):
        s = QtWidgets.QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setValue(val)
        s.setSuffix(suffix)
        s.setToolTip(tip)
        s.setDecimals(1)
        return s

    # -------------------------------------------------------------------------------------------- actions
    def root(self):
        r = self.lbl_root.text()
        return r if os.path.isdir(r) else ""

    def choose_folder(self):
        start = self.root() or self.settings.value("root", "") or os.path.expanduser("~")
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Folder with the cells (EPSC / CM recordings)", start)
        if d:
            self.lbl_root.setText(d)
            self.settings.setValue("root", d)
            self.scan()

    def params(self):
        return qc.QCParams(limit_mohm=self.sp_limit.value(), v_step=self.sp_v.value(), drift=self.sp_drift.value() / 100.0)

    def scan(self):
        root = self.root()
        if not root:
            self.choose_folder()
            return
        self._cache.clear()
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            def prog(i, n, f):
                self.bar.setMaximum(max(n, 1))
                self.bar.setValue(i)
                self.lbl_sum.setText(f"Scanning {i}/{n}: {os.path.basename(f)}")
                QtWidgets.QApplication.processEvents()
            self.report = qc.scan(root, self.params(), prog)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.bar.setValue(self.bar.maximum())
        self._summary()
        self._fill()
        if self.tbl.rowCount():
            self.tbl.selectRow(0)

    def _summary(self):
        rep = self.report
        bad = [c for c in rep.cells if c.status != "PASS"]
        drift = [c for c in rep.cells if c.drift]
        if not rep.files:
            self.lbl_sum.setText("<b>No EPSC / CM recordings found</b> under this folder (folders or files with 'EPSC' in the name, "
                                 "test-pulse files in a folder called 'CM').")
            self.txt.setPlainText(qc.summary_text(rep))
            return
        lim = rep.params.limit_mohm
        col = "#1e7a3c" if not bad else "#b00020"
        self.lbl_sum.setText(
            f"<span style='color:{col}; font-size:14px'><b>{len(rep.cells) - len(bad)} of {len(rep.cells)} cells have Ra &lt; {lim:g} MΩ</b></span>"
            f" in every sweep ({len(rep.files)} recordings; {len(bad)} need attention).  Drift flag (&gt;{rep.params.drift * 100:.0f} %): "
            f"{len(drift)} cells.")
        self.txt.setPlainText(qc.summary_text(rep))

    def _fill(self, *_):
        if not self.report:
            return
        files = [f for f in self.report.files if not self.chk_bad.isChecked() or f.status != "PASS" or f.drift_exclude]
        self._rows = files
        rows = [(f.line, f.cell[len(f.line) + 1:] if f.cell.startswith(f.line + "/") else f.cell, f.role, f.name, f"{f.n_ok}/{f.n_sweeps}",
                 None if not np.isfinite(f.ra_mean) else round(f.ra_mean, 2), None if not np.isfinite(f.ra_max) else round(f.ra_max, 2),
                 None if not np.isfinite(f.rm_mean) else round(f.rm_mean, 1), "yes" if f.drift_exclude else "", f.status) for f in files]
        self.tbl.blockSignals(True)
        fill(self.tbl, rows)
        for i, f in enumerate(files):
            c = STATUS_COLORS.get(f.status)
            if c:
                self.tbl.item(i, len(self.COLS) - 1).setBackground(QtGui.QColor(*c))
                self.tbl.item(i, len(self.COLS) - 1).setForeground(QtGui.QColor(0, 0, 0))
        self.tbl.blockSignals(False)

    def _sweeps(self, path):
        if path not in self._cache:
            try:
                self._cache[path] = qc.read_sweeps(path)[0]
            except Exception:
                self._cache[path] = []
        return self._cache[path]

    def _select(self):
        r = self.tbl.currentRow()
        if not (0 <= r < len(self._rows)):
            return
        f = self._rows[r]
        sweeps = self._sweeps(f.path)
        lim = self.report.params.limit_mohm
        self.pp.clear()
        self.pr.clear()
        ok = [s for s in f.sweeps if s.ok]
        if ok and sweeps:
            s0 = ok[0]
            lo, hi = s0.onset_ms - 30, s0.offset_ms + 30
            for s in ok:
                t, I, _ = sweeps[s.sweep]
                m = (t >= lo) & (t <= hi)
                self.pp.plot(t[m], I[m], pen=pg.mkPen((60, 90, 200, 200 if s is s0 else 90), width=2 if s is s0 else 1))
            for rg, col in ((s0.base_win, GREY), (s0.ss_win, GREEN)):
                self.pp.addItem(pg.LinearRegionItem(rg, brush=pg.mkBrush(*col, 60), pen=pg.mkPen(col), movable=False))
            for x in (s0.onset_ms, s0.offset_ms):
                self.pp.addItem(pg.InfiniteLine(x, angle=90, pen=pg.mkPen(BLUE, style=Qt.DashLine)))
            self.pp.plot([s.peak_t for s in ok], [s.baseline + s.i_peak for s in ok], pen=None, symbol="o", symbolBrush=RED, symbolSize=7)
            self.pp.setTitle(f"{f.name}: onset {s0.onset_ms:.1f} ms, offset {s0.offset_ms:.1f} ms, step {s0.dv:+.2f} mV "
                             f"({s0.dv_source}); grey = baseline, green = steady state, red = peak", size="9pt")
            xs = np.array([s.sweep for s in ok], float)
            ys = np.array([s.ra for s in ok])
            self.pr.addItem(pg.LinearRegionItem((ys[0] * (1 - self.report.params.drift), ys[0] * (1 + self.report.params.drift)),
                                                orientation="horizontal", brush=pg.mkBrush(150, 150, 150, 40), pen=pg.mkPen(GREY), movable=False))
            self.pr.plot(xs, ys, pen=pg.mkPen(BLUE, width=2), symbol="o", symbolBrush=BLUE, symbolSize=7)
            self.pr.addItem(pg.InfiniteLine(lim, angle=0, pen=pg.mkPen(RED, width=2, style=Qt.DashLine)))
            self.pr.setYRange(0, max(lim * 1.15, float(ys.max()) * 1.1), padding=0)
            self.pr.setTitle(f"Ra per sweep (red = {lim:g} MΩ limit, grey band = ±{self.report.params.drift * 100:.0f} % of the first sweep)", size="9pt")
        elif sweeps:
            t, I, _ = sweeps[0]
            m = t <= 1000
            self.pp.plot(t[m], I[m], pen=pg.mkPen((60, 90, 200)))
            self.pp.setTitle(f"{f.name}: no test pulse detected - first sweep, first 1 s", size="9pt")
        lines = [f"{f.role} / {f.source}: {f.path}", f"status {f.status}" + (f" - {f.error}" if f.error else "")]
        lines += [f"  {x}" for x in f.reasons]
        for s in f.sweeps[:40]:
            lines.append(f"  sweep {s.sweep}: Ra {s.ra:.2f}  Rm {s.rm:.1f}  dV {s.dv:+.2f} ({s.dv_source}) onset {s.onset_ms:.1f} offset {s.offset_ms:.1f}"
                         + (f"  [{s.note}]" if s.note else "") if s.ok else f"  sweep {s.sweep}: {s.note}")
        self.txt.setPlainText("\n".join(lines))

    def export(self):
        if not self.report:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save report", os.path.join(self.root() or "", "Ra_QC.xlsx"),
                                                        "Excel (*.xlsx);;CSV (*.csv)")
        if not path:
            return
        if path.lower().endswith(".csv"):
            made = qc.write_csv(self.report, os.path.splitext(path)[0])
            self.main.statusBar().showMessage("Saved " + ", ".join(os.path.basename(p) for p in made))
        else:
            qc.write_xlsx(self.report, path if path.lower().endswith(".xlsx") else path + ".xlsx")
            self.main.statusBar().showMessage(f"Saved {path}")
