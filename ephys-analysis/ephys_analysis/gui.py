"""PySide6 + pyqtgraph GUI. Spin boxes and draggable regions are two views of one Params."""
import csv
import os
import sys

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt

from . import analysis as A
from .io import load_abf

pg.setConfigOptions(antialias=False, background="w", foreground="k")

BASE_COL = (150, 150, 150, 70)
MEAS_COL = (40, 170, 70, 70)
FIT_COL = (50, 90, 220, 60)


def dspin(lo=-1e6, hi=1e6, dec=2, suffix="", step=1.0):
    w = QtWidgets.QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setDecimals(dec)
    w.setSingleStep(step)
    if suffix:
        w.setSuffix(suffix)
    w.setKeyboardTracking(False)
    return w


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ephys-analysis: input resistance / tau / capacitance")
        self.resize(1500, 900)
        self.rec = None
        self.p = A.Params()
        self.results, self.summary = [], A.Summary()
        self._busy = False
        self._build()

    # ------------------------------------------------------------------ layout
    def _build(self):
        split = QtWidgets.QSplitter()
        self.setCentralWidget(split)
        split.addWidget(self._build_controls())
        split.addWidget(self._build_plots())
        split.addWidget(self._build_results())
        split.setSizes([400, 680, 450])
        self.statusBar().showMessage("Open an .abf file (File > Open or Ctrl+O)")
        m = self.menuBar().addMenu("&File")
        a = m.addAction("&Open .abf…")
        a.setShortcut("Ctrl+O")
        a.triggered.connect(self.open_dialog)
        a = m.addAction("&Export results CSV…")
        a.triggered.connect(self.export_csv)

    def _build_controls(self):
        sa = QtWidgets.QScrollArea()
        sa.setWidgetResizable(True)
        host = QtWidgets.QWidget()
        sa.setWidget(host)
        lay = QtWidgets.QVBoxLayout(host)

        self.btn_open = QtWidgets.QPushButton("Open .abf…")
        self.btn_open.clicked.connect(self.open_dialog)
        self.lbl_file = QtWidgets.QLabel("no file")
        self.lbl_file.setWordWrap(True)
        lay.addWidget(self.btn_open)
        lay.addWidget(self.lbl_file)

        # sweeps
        g = QtWidgets.QGroupBox("Sweeps (checked = analysed)")
        gl = QtWidgets.QVBoxLayout(g)
        self.sweep_list = QtWidgets.QListWidget()
        self.sweep_list.setMaximumHeight(170)
        self.sweep_list.itemChanged.connect(lambda *_: self.recompute())
        self.sweep_list.currentRowChanged.connect(lambda *_: self.redraw())
        row = QtWidgets.QHBoxLayout()
        for txt, fn in (("All", lambda: self._check_all(True)), ("None", lambda: self._check_all(False))):
            b = QtWidgets.QPushButton(txt)
            b.clicked.connect(fn)
            row.addWidget(b)
        gl.addWidget(self.sweep_list)
        gl.addLayout(row)
        lay.addWidget(g)

        # regions
        g = QtWidgets.QGroupBox("Regions (ms) – also draggable on the plot")
        f = QtWidgets.QFormLayout(g)
        self.sp = {k: dspin(suffix=" ms", step=5) for k in
                   ("b0", "b1", "m0", "m1", "s0", "s1")}
        f.addRow("Baseline start / end", self._pair(self.sp["b0"], self.sp["b1"]))
        f.addRow("Measure start / end", self._pair(self.sp["m0"], self.sp["m1"]))
        f.addRow("Im step start / stop", self._pair(self.sp["s0"], self.sp["s1"]))
        self.btn_auto = QtWidgets.QPushButton("Auto-place from Im timing")
        self.btn_auto.clicked.connect(self.auto_regions)
        f.addRow(self.btn_auto)
        lay.addWidget(g)

        # Ri
        g = QtWidgets.QGroupBox("Input resistance (hyperpolarizing steps)")
        f = QtWidgets.QFormLayout(g)
        self.cb_est = QtWidgets.QComboBox()
        self.cb_est.addItems(["mean", "median", "last", "line"])
        self.cb_est.setToolTip("Steady-state Vm estimate in the measure region:\n"
                               "mean / median / last sample(s) / line = OLS line extrapolated to region end")
        self.sp_lastn = QtWidgets.QSpinBox()
        self.sp_lastn.setRange(1, 10000)
        self.chk_neg = QtWidgets.QCheckBox("Only negative ΔIm")
        self.chk_neg.setChecked(True)
        self.chk_spk = QtWidgets.QCheckBox("Exclude sweeps with spikes")
        self.chk_spk.setChecked(True)
        self.sp_spk = dspin(-100, 100, 1, " mV")
        f.addRow("Steady-state estimator", self.cb_est)
        f.addRow("'last': samples averaged", self.sp_lastn)
        f.addRow(self.chk_neg)
        f.addRow(self.chk_spk)
        f.addRow("Spike threshold", self.sp_spk)
        lay.addWidget(g)

        # tau
        g = QtWidgets.QGroupBox("Exponential fit  b0 + b1·exp(−t/τ)")
        f = QtWidgets.QFormLayout(g)
        self.cb_fstart = QtWidgets.QComboBox()
        self.cb_fstart.addItems(["onset", "sag", "custom"])
        self.cb_fstart.setToolTip("onset: step start + offset\nsag: start the fit at the Vm minimum "
                                  "(use when there is a large sag)\ncustom: fixed time")
        self.sp_foff = dspin(-1e4, 1e4, 1, " ms")
        self.sp_fcus = dspin(suffix=" ms", step=5)
        self.sp_sag = dspin(1, 1e5, 1, " ms")
        self.sp_fend = dspin(suffix=" ms", step=5)
        self.cb_nexp = QtWidgets.QComboBox()
        self.cb_nexp.addItems(["1", "2", "3"])
        self.cb_nexp.setToolTip("Exponential terms. τ reported = slowest term (τ0). 1 = default.")
        self.cb_b0 = QtWidgets.QComboBox()
        self.cb_b0.addItems(["free", "fixed"])
        self.cb_b0.setToolTip("free: b0 fitted\nfixed: b0 = steady-state estimate chosen above")
        self.cb_src = QtWidgets.QComboBox()
        self.cb_src.addItems(["smallest", "all"])
        self.cb_src.setToolTip("smallest: τ from only the smallest hyperpolarizing sweep (just before "
                               "injected current = 0). all: every analysed hyperpolarizing sweep.\n"
                               "Rin always uses all analysed sweeps.")
        self.cb_agg = QtWidgets.QComboBox()
        self.cb_agg.addItems(["median", "mean"])
        f.addRow("Fit start", self.cb_fstart)
        f.addRow("  onset offset", self.sp_foff)
        f.addRow("  custom start", self.sp_fcus)
        f.addRow("  sag search window", self.sp_sag)
        f.addRow("Fit end", self.sp_fend)
        f.addRow("Exponential terms", self.cb_nexp)
        f.addRow("b0", self.cb_b0)
        f.addRow("τ taken from", self.cb_src)
        f.addRow("τ aggregate (if >1 sweep)", self.cb_agg)
        lay.addWidget(g)

        self.chk_allfits = QtWidgets.QCheckBox("Show fits for all analysed sweeps")
        self.chk_allfits.setChecked(False)
        self.chk_allfits.toggled.connect(lambda *_: self.redraw())
        lay.addWidget(self.chk_allfits)
        lay.addStretch(1)

        for w in (self.sp_lastn, self.sp_spk, self.sp_foff, self.sp_fcus, self.sp_sag, self.sp_fend,
                  *self.sp.values()):
            w.valueChanged.connect(self._on_controls)
        for w in (self.cb_est, self.cb_fstart, self.cb_nexp, self.cb_b0, self.cb_src, self.cb_agg):
            w.currentIndexChanged.connect(self._on_controls)
        for w in (self.chk_neg, self.chk_spk):
            w.toggled.connect(self._on_controls)
        return sa

    @staticmethod
    def _pair(a, b):
        w = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(a)
        h.addWidget(b)
        return w

    def _build_plots(self):
        gl = pg.GraphicsLayoutWidget()
        self.pv = gl.addPlot(row=0, col=0)
        self.pi = gl.addPlot(row=1, col=0)
        gl.ci.layout.setRowStretchFactor(0, 3)
        gl.ci.layout.setRowStretchFactor(1, 1)
        self.pv.setLabel("left", "Vm", "mV")
        self.pi.setLabel("left", "Im", "pA")
        self.pi.setLabel("bottom", "Time", "ms")
        self.pi.setXLink(self.pv)
        for p in (self.pv, self.pi):
            p.showGrid(x=True, y=True, alpha=0.2)
            p.setDownsampling(auto=True, mode="peak")
            p.setClipToView(True)

        def reg(col, plot):
            r = pg.LinearRegionItem(brush=pg.mkBrush(col), pen=pg.mkPen(col[:3]))
            r.setZValue(-10)
            plot.addItem(r)
            return r

        self.rg = {
            "base_v": reg(BASE_COL, self.pv), "meas_v": reg(MEAS_COL, self.pv),
            "fit_v": reg(FIT_COL, self.pv),
            "base_i": reg(BASE_COL, self.pi), "meas_i": reg(MEAS_COL, self.pi),
        }
        for k, r in self.rg.items():
            r.sigRegionChangeFinished.connect(lambda _=None, k=k: self._on_region(k))
        self.rg["fit_v"].setToolTip("Fit window (end handle sets Fit end; start follows the 'Fit start' mode)")
        for p_ in (self.pv, self.pi):
            for ax in ("left", "bottom"):
                p_.getAxis(ax).enableAutoSIPrefix(False)
        self.curves = []
        return gl

    def _build_results(self):
        tabs = QtWidgets.QTabWidget()
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        self.pri = pg.PlotWidget()
        self.pri.setLabel("bottom", "ΔIm", "nA")
        self.pri.setLabel("left", "ΔVm", "mV")
        self.pri.showGrid(x=True, y=True, alpha=0.2)
        for ax in ("left", "bottom"):
            self.pri.getAxis(ax).enableAutoSIPrefix(False)
        v.addWidget(self.pri, 3)
        self.lbl_sum = QtWidgets.QLabel()
        self.lbl_sum.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_sum.setWordWrap(True)
        self.lbl_sum.setStyleSheet("font-size: 13px; padding: 4px;")
        v.addWidget(self.lbl_sum)
        self.table = QtWidgets.QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            ["Sweep", "ΔIm pA", "ΔVm mV", "Rin (ΔV/ΔI) MΩ", "τ ms", "fit R²", "Sag mV", "Sag ratio", "Used / note"])
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        v.addWidget(self.table, 4)
        tabs.addTab(w, "Results")
        return tabs

    # ------------------------------------------------------------------ data
    def open_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open ABF", "", "Axon ABF (*.abf);;All files (*)")
        if path:
            self.load(path)

    def load(self, path):
        try:
            rec = load_abf(path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Could not load file", f"{path}\n\n{e}")
            return
        self.rec = rec
        self.lbl_file.setText(f"{os.path.basename(path)}\n{rec.v.shape[0]} sweeps, "
                              f"{rec.t[1] - rec.t[0]:.4f} ms/sample\nIm: {rec.i_source}")
        self._busy = True
        self.sweep_list.clear()
        for s in range(rec.v.shape[0]):
            it = QtWidgets.QListWidgetItem(f"Sweep {s}  ({self._nominal(s)})")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            self.sweep_list.addItem(it)
        self._busy = False
        self.p = A.default_params(rec.t, rec.i)
        self._params_to_controls()
        self._plot_traces()
        self.recompute()
        self.statusBar().showMessage(f"Loaded {path}")

    def _nominal(self, s):
        i = self.rec.i[s]
        k2 = max(1, len(i) // 50)
        hold = np.median(np.r_[i[:k2], i[-k2:]])
        d = i[np.argmax(np.abs(i - hold))] - hold
        return f"{d:+.0f} pA"

    def _check_all(self, on):
        self._busy = True
        for r in range(self.sweep_list.count()):
            self.sweep_list.item(r).setCheckState(Qt.Checked if on else Qt.Unchecked)
        self._busy = False
        self.recompute()

    def _selected(self):
        return [r for r in range(self.sweep_list.count())
                if self.sweep_list.item(r).checkState() == Qt.Checked]

    # ------------------------------------------------------------ params sync
    def _params_to_controls(self):
        p = self.p
        self._busy = True
        for k, val in (("b0", p.baseline[0]), ("b1", p.baseline[1]), ("m0", p.measure[0]),
                       ("m1", p.measure[1]), ("s0", p.step[0]), ("s1", p.step[1])):
            self.sp[k].setValue(val)
        self.cb_est.setCurrentText(p.estimator)
        self.sp_lastn.setValue(p.last_n)
        self.chk_neg.setChecked(p.only_negative)
        self.chk_spk.setChecked(p.exclude_spikes)
        self.sp_spk.setValue(p.spike_threshold)
        self.cb_fstart.setCurrentText(p.fit_start_mode)
        self.sp_foff.setValue(p.fit_start_offset)
        self.sp_fcus.setValue(p.fit_start_custom)
        self.sp_sag.setValue(p.sag_search)
        self.sp_fend.setValue(p.fit_end)
        self.cb_nexp.setCurrentText(str(p.n_exp))
        self.cb_b0.setCurrentText(p.b0_mode)
        self.cb_src.setCurrentText(p.tau_source)
        self.cb_agg.setCurrentText(p.tau_agg)
        self._busy = False
        self._regions_from_params()

    def _controls_to_params(self):
        p, sp = self.p, self.sp
        p.baseline = (sp["b0"].value(), sp["b1"].value())
        p.measure = (sp["m0"].value(), sp["m1"].value())
        p.step = (sp["s0"].value(), sp["s1"].value())
        p.estimator = self.cb_est.currentText()
        p.last_n = self.sp_lastn.value()
        p.only_negative = self.chk_neg.isChecked()
        p.exclude_spikes = self.chk_spk.isChecked()
        p.spike_threshold = self.sp_spk.value()
        p.fit_start_mode = self.cb_fstart.currentText()
        p.fit_start_offset = self.sp_foff.value()
        p.fit_start_custom = self.sp_fcus.value()
        p.sag_search = self.sp_sag.value()
        p.fit_end = self.sp_fend.value()
        p.n_exp = int(self.cb_nexp.currentText())
        p.b0_mode = self.cb_b0.currentText()
        p.tau_source = self.cb_src.currentText()
        p.tau_agg = self.cb_agg.currentText()

    def _fit_region_start(self):
        p = self.p
        if p.fit_start_mode == "custom":
            return p.fit_start_custom
        return p.step[0] + p.fit_start_offset

    def _regions_from_params(self):
        self._busy = True
        p = self.p
        for k, v in (("base_v", p.baseline), ("base_i", p.baseline), ("meas_v", p.measure),
                     ("meas_i", p.measure), ("fit_v", (self._fit_region_start(), p.fit_end))):
            self.rg[k].setRegion(v)
        self._busy = False

    def _on_controls(self, *_):
        if self._busy:
            return
        self._controls_to_params()
        self._regions_from_params()
        self.recompute()

    def _on_region(self, key):
        if self._busy:
            return
        a, b = self.rg[key].getRegion()
        a, b = round(a, 2), round(b, 2)
        self._busy = True
        if key.startswith("base"):
            self.sp["b0"].setValue(a), self.sp["b1"].setValue(b)
        elif key.startswith("meas"):
            self.sp["m0"].setValue(a), self.sp["m1"].setValue(b)
        else:  # fit window: end handle -> fit end; start handle -> offset / custom start
            self.sp_fend.setValue(b)
            if self.cb_fstart.currentText() == "custom":
                self.sp_fcus.setValue(a)
            elif self.cb_fstart.currentText() == "onset":
                self.sp_foff.setValue(round(a - self.sp["s0"].value(), 2))
        self._busy = False
        self._on_controls()

    def auto_regions(self):
        if self.rec is None:
            return
        self.p = A.default_params(self.rec.t, self.rec.i)
        self._params_to_controls()
        self.recompute()

    # ------------------------------------------------------------- plotting
    def _plot_traces(self):
        self.pv.clear(), self.pi.clear()
        for r in self.rg.values():
            (self.pi if r in (self.rg["base_i"], self.rg["meas_i"]) else self.pv).addItem(r)
        self.curves = []
        self._regions_from_params()
        self.pv.enableAutoRange()
        self.pi.enableAutoRange()

    def redraw(self):
        if self.rec is None:
            return
        t = self.rec.t
        for plot, it in getattr(self, "_items", []):
            plot.removeItem(it)
        self._items = []
        self._extra = self._items
        sel = self.sweep_list.currentRow()
        used = {r.idx for r in self.results if r.used}
        for s in range(self.rec.v.shape[0]):
            on = s in used
            col = (60, 90, 200, 160) if on else (170, 170, 170, 110)
            width = 2.5 if s == sel else 1
            if s == sel:
                col = (220, 120, 0, 255)
            self._add(self.pv, self.pv.plot(t, self.rec.v[s], pen=pg.mkPen(col, width=width)), False)
            self._add(self.pi, self.pi.plot(t, self.rec.i[s], pen=pg.mkPen(col, width=width)), False)
        show = used if self.chk_allfits.isChecked() else (({sel} & used) | {r.idx for r in self.results if r.tau_used})
        for r in self.results:
            if r.idx in show and r.fit and r.fit.ok:
                c = self.pv.plot(r.fit.t, r.fit.y_fit, pen=pg.mkPen((220, 30, 30), width=2, style=Qt.DashLine))
                c.setZValue(5)
                self._items.append((self.pv, c))
            if r.used:
                mid = 0.5 * (self.p.measure[0] + self.p.measure[1])
                sc = pg.ScatterPlotItem([mid], [r.v_ss], size=8, brush=pg.mkBrush(40, 170, 70), pen=pg.mkPen("k"))
                sc.setZValue(6)
                self.pv.addItem(sc)
                self._items.append((self.pv, sc))
                if r.negative and np.isfinite(r.sag_t) and r.idx in show:
                    sg = pg.ScatterPlotItem([r.sag_t], [r.sag_v], size=7, symbol="o",
                                            brush=pg.mkBrush(230, 30, 30), pen=pg.mkPen("k"))
                    sg.setZValue(6)
                    self.pv.addItem(sg)
                    self._items.append((self.pv, sg))

    def _add(self, plot, item, _extra=True):
        self._items.append((plot, item))

    def recompute(self):
        if self.rec is None or self._busy:
            return
        self._controls_to_params()
        self.results, self.summary = A.analyze(self.rec, self.p, self._selected())
        self.redraw()
        self._update_ri_plot()
        self._update_table()

    def _update_ri_plot(self):
        self.pri.clear()
        used = [r for r in self.results if r.used]
        S = self.summary
        if not used:
            return
        x = np.array([r.dI for r in used]) / 1e3
        y = np.array([r.dV for r in used])
        self.pri.plot(x, y, pen=None, symbol="o", symbolSize=9, symbolBrush=(40, 90, 200))
        if np.isfinite(S.rin) and len(used) >= 2:
            xs = np.array([x.min(), max(x.max(), 0.0)])
            self.pri.plot(xs, S.rin * xs + S.intercept, pen=pg.mkPen((220, 30, 30), width=2))
            txt = f"y = {S.rin:.2f}·x {S.intercept:+.2f}   R² = {S.r ** 2:.4f}"
            ti = pg.TextItem(txt, color="k", anchor=(0, 0))
            self.pri.addItem(ti)
            ti.setPos(x.min(), y.max())

    def _update_table(self):
        S = self.summary
        agg = self.p.tau_agg
        ref = ""
        if self.p.n_exp > 1 and np.isfinite(S.cm_r0):
            ref = (f"<br><span style='color:#777'>Reference only (Golowasch 2009, non-isopotential): "
                   f"R0 = {S.r0:.1f} MΩ, Cm = τ0/R0 = {S.cm_r0:.1f} pF</span>")
        self.lbl_sum.setText(
            f"<b>Rin</b> = {S.rin:.2f} MΩ (n={S.n}, slope SE {S.stderr:.2f}, R²={S.r ** 2:.4f})<br>"
            f"<b>τ0</b> ({'smallest hyperpol. sweep' if self.p.tau_source == 'smallest' else agg}, n={S.n_tau}, {self.p.n_exp} exp) = {S.tau:.2f} ms (SD {S.tau_sd:.2f})<br>"
            f"<b>Cm = τ0 / Rin</b> = {S.cm:.1f} pF" + ref)
        self.table.setRowCount(len(self.results))
        for row, r in enumerate(self.results):
            f = r.fit
            rin = r.dV / (r.dI / 1e3) if r.dI else np.nan
            vals = [r.idx, f"{r.dI:.1f}", f"{r.dV:.2f}", f"{rin:.1f}",
                    f"{f.tau:.2f}" if f and f.ok else "", f"{f.r2:.4f}" if f and f.ok else "",
                    f"{r.sag:.2f}" if np.isfinite(r.sag) else "",
                    f"{r.sag_ratio:.3f}" if np.isfinite(r.sag_ratio) else "",
                    ("used, τ" if r.tau_used else "used") if r.used else (r.note or "unchecked")]
            for c, v in enumerate(vals):
                it = QtWidgets.QTableWidgetItem(str(v))
                if not r.used:
                    it.setForeground(Qt.gray)
                self.table.setItem(row, c, it)

    # --------------------------------------------------------------- export
    def export_csv(self):
        if not self.results:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export", "results.csv", "CSV (*.csv)")
        if not path:
            return
        S, p = self.summary, self.p
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["file", self.rec.path])
            w.writerow(["Rin_MOhm", S.rin, "tau_ms", S.tau, "tau_agg", p.tau_agg, "Cm_pF", S.cm])
            w.writerow(["baseline_ms", *p.baseline, "measure_ms", *p.measure, "estimator", p.estimator,
                        "fit_start_mode", p.fit_start_mode, "fit_end_ms", p.fit_end, "b0", p.b0_mode])
            w.writerow(["sweep", "dI_pA", "dV_mV", "used", "note", "tau_ms", "fit_b0", "fit_b1", "fit_r2",
                        "sag_mV", "sag_ratio"])
            for r in self.results:
                f = r.fit
                w.writerow([r.idx, r.dI, r.dV, int(r.used), r.note, f.tau if f else "", f.b0 if f else "",
                            f.b1 if f else "", f.r2 if f else "", r.sag, r.sag_ratio])
        self.statusBar().showMessage(f"Saved {path}")


def run(path=None):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    if path:
        w.load(path)
    app.exec()
