"""PySide6 + pyqtgraph front-end: Passive | AP counting & kinetics | Events | Cell summary."""
import csv
import dataclasses
import os
import sys

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt

from . import events as E
from . import kinetics as K
from . import passive as P
from . import report as R
from . import spikes as S
from .common import idx_at, ts_ms, window_mean
from .io import load_abf

pg.setConfigOptions(antialias=False, background="w", foreground="k")
BLUE, RED, GREEN, GREY = (50, 90, 220), (220, 30, 30), (40, 160, 70), (170, 170, 170)


# ------------------------------------------------------------------------------------------- param forms
class ParamForm(QtWidgets.QGroupBox):
    """Edits fields of a dataclass instance in place. spec: (field, label, kind, opts)."""

    changed = QtCore.Signal()

    def __init__(self, title, obj, spec):
        super().__init__(title)
        self.obj, self.w = obj, {}
        self.spec = [tuple(x) + ({},) if len(x) == 3 else tuple(x) for x in spec]
        spec = self.spec
        f = QtWidgets.QFormLayout(self)
        for name, label, kind, o in spec:
            if kind in ("float", "int"):
                w = QtWidgets.QDoubleSpinBox() if kind == "float" else QtWidgets.QSpinBox()
                w.setRange(o.get("lo", -1e9), o.get("hi", 1e9))
                if kind == "float":
                    w.setDecimals(o.get("dec", 2))
                    w.setSingleStep(o.get("step", 1.0))
                if o.get("suffix"):
                    w.setSuffix(" " + o["suffix"])
                w.setKeyboardTracking(False)
                w.valueChanged.connect(self._edited)
            elif kind == "choice":
                w = QtWidgets.QComboBox()
                w.addItems(o["items"])
                w.currentIndexChanged.connect(self._edited)
            elif kind == "bool":
                w = QtWidgets.QCheckBox()
                w.toggled.connect(self._edited)
            elif kind == "pair":
                a, b = [self._mk_float(o) for _ in range(2)]
                w = QtWidgets.QWidget()
                h = QtWidgets.QHBoxLayout(w)
                h.setContentsMargins(0, 0, 0, 0)
                h.addWidget(a)
                h.addWidget(b)
                w.pair = (a, b)
                a.valueChanged.connect(self._edited)
                b.valueChanged.connect(self._edited)
            if o.get("tip"):
                w.setToolTip(o["tip"])
            self.w[name] = w
            f.addRow(label, w)
        self.load()

    @staticmethod
    def _mk_float(o):
        s = QtWidgets.QDoubleSpinBox()
        s.setRange(o.get("lo", -1e9), o.get("hi", 1e9))
        s.setDecimals(o.get("dec", 2))
        s.setKeyboardTracking(False)
        if o.get("suffix"):
            s.setSuffix(" " + o["suffix"])
        return s

    def load(self):
        """Push dataclass values into the widgets (no signals)."""
        for name, _, kind, o in self.spec:
            w, v = self.w[name], getattr(self.obj, name)
            w.blockSignals(True)
            if kind in ("float", "int"):
                w.setValue(0 if v is None else v)
            elif kind == "choice":
                w.setCurrentText(str(v))
            elif kind == "bool":
                w.setChecked(bool(v))
            elif kind == "pair":
                for sp, x in zip(w.pair, v):
                    sp.blockSignals(True)
                    sp.setValue(float(x))
                    sp.blockSignals(False)
            w.blockSignals(False)

    def _edited(self, *_):
        for name, _, kind, o in self.spec:
            w = self.w[name]
            if kind in ("float", "int"):
                v = w.value()
            elif kind == "choice":
                v = w.currentText()
                cur = getattr(self.obj, name)
                if isinstance(cur, (int, float)) and not isinstance(cur, bool):
                    v = type(cur)(v)
            elif kind == "bool":
                v = w.isChecked()
            else:
                v = tuple(s.value() for s in w.pair)
            setattr(self.obj, name, v)
        self.changed.emit()


def plot(title=None, xl=None, yl=None):
    p = pg.PlotWidget()
    p.showGrid(x=True, y=True, alpha=0.2)
    if xl:
        p.setLabel("bottom", xl)
    if yl:
        p.setLabel("left", yl)
    for ax in ("left", "bottom"):
        p.getAxis(ax).enableAutoSIPrefix(False)
    if title:
        p.setTitle(title, size="9pt")
    return p


def table(headers):
    t = QtWidgets.QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    t.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
    return t


def fill(t, rows, gray=None):
    t.setRowCount(len(rows))
    for i, r in enumerate(rows):
        for j, v in enumerate(r):
            it = QtWidgets.QTableWidgetItem("" if v is None else (f"{v:.4g}" if isinstance(v, (float, np.floating)) else str(v)))
            if gray and gray[i]:
                it.setForeground(Qt.gray)
            t.setItem(i, j, it)


def scroll(widget):
    s = QtWidgets.QScrollArea()
    s.setWidgetResizable(True)
    s.setWidget(widget)
    s.setMinimumWidth(300)
    return s


# ------------------------------------------------------------------------------------------------ Passive
class PassiveTab(QtWidgets.QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.p = P.PassiveParams()
        self.res = None
        lay = QtWidgets.QHBoxLayout(self)
        side = QtWidgets.QWidget()
        sv = QtWidgets.QVBoxLayout(side)
        self.form = ParamForm("Windows", self.p, [
            ("step", "Im step start / stop", "pair", dict(suffix="ms", lo=0)),
            ("baseline_ms", "Baseline (before step)", "float", dict(suffix="ms", lo=1)),
            ("measure_ms", "Steady state (ending at stop)", "float", dict(suffix="ms", lo=1)),
            ("peak_avg_ms", "Peak average (0 = raw min)", "float", dict(suffix="ms", lo=0)),
            ("round_step", "Round dI to step (0 = off)", "float", dict(suffix="pA", lo=0)),
            ("exclude_spikes", "Exclude sweeps with spikes", "bool", {}),
            ("spike_threshold", "Spike threshold", "float", dict(suffix="mV")),
        ])
        self.form2 = ParamForm("Input resistance", self.p, [
            ("ri_method", "Method", "choice", dict(items=["ols", "ransac", "median_ratio"],
                                                  tip="ols = EE manual; ransac / median_ratio = Scala et al. 2019")),
            ("ri_n", "Use n most negative steps (0 = all)", "int", dict(lo=0, hi=100)),
        ])
        self.form3 = ParamForm("tau / Cm (Cm = tau / Rin)", self.p, [
            ("tau_source", "tau from", "choice", dict(items=["smallest", "all"])),
            ("fit_start_mode", "Fit start", "choice", dict(items=["onset", "ten_percent", "custom"])),
            ("fit_start_offset", "  onset offset", "float", dict(suffix="ms")),
            ("fit_start_custom", "  custom start", "float", dict(suffix="ms")),
            ("fit_end_mode", "Fit end", "choice", dict(items=["step_end", "peak", "custom"])),
            ("fit_end_custom", "  custom end", "float", dict(suffix="ms")),
            ("b0_mode", "b0", "choice", dict(items=["free", "fixed"])),
        ])
        for f in (self.form, self.form2, self.form3):
            sv.addWidget(f)
            f.changed.connect(self.recompute)
        self.lbl = QtWidgets.QLabel()
        self.lbl.setWordWrap(True)
        self.lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        sv.addWidget(self.lbl)
        sv.addStretch(1)
        lay.addWidget(scroll(side))
        right = QtWidgets.QSplitter(Qt.Vertical)
        top = QtWidgets.QSplitter()
        self.pv = plot("Vm (steps)", "ms", "mV")
        self.pr = plot("Input resistance: dV vs dI", "dI (nA)", "dV (mV)")
        top.addWidget(self.pv)
        top.addWidget(self.pr)
        right.addWidget(top)
        self.tbl = table(["Sweep", "dI pA", "dV mV", "Vpeak", "Sag mV", "Sag ratio", "Tolias", "tau ms", "R²", "Status"])
        right.addWidget(self.tbl)
        right.setSizes([480, 220])
        lay.addWidget(right, 1)
        self.region = pg.LinearRegionItem(brush=pg.mkBrush(150, 150, 150, 40), pen=pg.mkPen(GREY))
        self.region.sigRegionChangeFinished.connect(self._region)

    def set_recording(self, rec):
        self.p.step = R.detect_step(rec.t, rec.im()) or (0.1 * rec.t[-1], 0.9 * rec.t[-1])
        base, meas = self.p.regions()
        dI = [window_mean(rec.t, rec.im()[s], *meas) - window_mean(rec.t, rec.im()[s], *base) for s in range(rec.n_sweeps)]
        self.p.round_step = R.estimate_step(dI)
        for f in (self.form, self.form2, self.form3):
            f.load()
        self.recompute()

    def _region(self):
        a, b = self.region.getRegion()
        self.p.step = (round(a, 2), round(b, 2))
        self.form.load()
        self.recompute()

    def recompute(self):
        rec = self.main.rec
        if rec is None or rec.vm() is None or rec.im() is None:
            return
        p = self.p
        if p.ri_n == 0:
            p.ri_n = None
        self.res = P.analyse_passive(rec.t, rec.vm(), rec.im(), p)
        self.main.step_changed()
        r = self.res
        self.pv.clear()
        self.region.blockSignals(True)
        self.region.setRegion(p.step)
        self.region.blockSignals(False)
        self.pv.addItem(self.region)
        base, meas = p.regions()
        for rg, col in ((base, GREY), (meas, GREEN)):
            self.pv.addItem(pg.LinearRegionItem(rg, brush=pg.mkBrush(*col, 60), pen=pg.mkPen(col), movable=False))
        for s in r.sweeps:
            col = (60, 90, 200, 150) if s.used else (190, 190, 190, 100)
            if r.lowest and s.idx == r.lowest.idx:
                col, w = (200, 90, 0), 2
            elif r.smallest and s.idx == r.smallest.idx:
                col, w = (0, 140, 60), 2
            else:
                w = 1
            self.pv.plot(rec.t, rec.vm()[s.idx], pen=pg.mkPen(col, width=w))
            if s.tau_used and s.fit and s.fit["ok"]:
                self.pv.plot(s.fit["t"], s.fit["y_fit"], pen=pg.mkPen(RED, width=2, style=Qt.DashLine))
        self.pr.clear()
        used = [s for s in r.sweeps if s.used]
        if used:
            x = np.array([s.dI for s in used]) / 1e3
            y = np.array([s.dV for s in used])
            self.pr.plot(x, y, pen=None, symbol="o", symbolBrush=BLUE, symbolSize=9)
            if np.isfinite(r.rin) and len(used) > 1:
                xs = np.array([x.min(), max(x.max(), 0)])
                c = r.rin_intercept if np.isfinite(r.rin_intercept) else 0.0
                self.pr.plot(xs, r.rin * xs + c, pen=pg.mkPen(RED, width=2))
        low, sm = r.lowest, r.smallest
        self.lbl.setText(
            f"<b>Rin</b> = {r.rin:.2f} MΩ (n={r.rin_n}, {p.ri_method})<br><b>tau</b> = {r.tau:.2f} ms"
            f" (sweep {sm.idx if sm else '-'}, dI {sm.dI if sm else float('nan'):.0f} pA)<br><b>Cm = tau/Rin</b> = {r.cm_pF:.1f} pF"
            + (f"<br>Lowest step (sweep {low.idx}, {low.dI:.0f} pA): dV {low.dV:.3f} mV, sag {low.sag_mV:.3f} mV, "
               f"sag ratio {low.sag_ratio:.4f}, Tolias ratio {low.tolias:.4f}" if low else "")
            + "<br><span style='color:#777'>Cm assumes an isopotential cell (Golowasch et al. 2009).</span>")
        rows = [(s.idx, s.dI, s.dV, s.v_peak, s.sag_mV, s.sag_ratio, s.tolias, s.fit["tau"] if s.fit and s.fit["ok"] else None,
                 s.fit["r2"] if s.fit and s.fit["ok"] else None,
                 ("used, tau" if s.tau_used else "used") if s.used else (s.note or "")) for s in r.sweeps]
        fill(self.tbl, rows, [not s.used for s in r.sweeps])


# ------------------------------------------------------------------------------------- AP counting / kinetics
class SpikesTab(QtWidgets.QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.sp = S.SpikeParams()
        self.kp = K.KineticsParams()
        self.cfg = R.CellConfig()
        self.per, self.thr_line = [], None
        lay = QtWidgets.QHBoxLayout(self)
        side = QtWidgets.QWidget()
        sv = QtWidgets.QVBoxLayout(side)
        self.f1 = ParamForm("AP detection (EE manual s.8)", self.sp, [
            ("method", "Method", "choice", dict(items=["auto_record", "auto_spike", "manual"])),
            ("amplitude", "Amplitude", "float", dict(suffix="mV")),
            ("pos_dvdt", "+dV/dt", "float", dict(suffix="mV/ms")),
            ("neg_dvdt", "-dV/dt", "float", dict(suffix="mV/ms")),
            ("width", "Width", "float", dict(suffix="ms", dec=2)),
            ("manual_threshold", "Manual threshold", "float", dict(suffix="mV")),
        ])
        self.f2 = ParamForm("AP kinetics (EE manual s.10)", self.kp, [
            ("thr_method", "Threshold method", "choice", dict(items=list(K.THRESHOLD_METHODS))),
            ("thr_search", "Threshold search", "float", dict(suffix="ms", lo=0.1)),
            ("first_deriv_cutoff_mode", "1st deriv: max / cutoff", "choice", dict(items=["max", "cutoff"])),
            ("first_deriv_cutoff", "1st deriv cutoff", "float", dict(suffix="mV/ms")),
            ("third_deriv_cutoff_mode", "3rd deriv: max / cutoff", "choice", dict(items=["max", "cutoff"])),
            ("third_deriv_cutoff", "3rd deriv cutoff", "float"),
            ("method_I_lower", "Method I lower bound", "float"),
            ("method_II_lower", "Method II lower bound", "float"),
            ("fahp", "fAHP window after peak (ms)", "pair", {}),
            ("mahp", "mAHP window after peak (ms)", "pair", {}),
            ("interp", "Interpolate to 200 kHz", "bool", {}),
            ("rise_pct", "Rise % cutoffs", "pair", {}),
            ("decay_pct", "Decay % cutoffs", "pair", {}),
            ("decay_to_thr", "Decay peak -> threshold (not fAHP)", "bool", {}),
            ("max_slope", "Max rise / decay slope", "bool", {}),
        ])
        self.f3 = ParamForm("Firing measures", self.cfg, [
            ("rheobase_mode", "Rheobase", "choice", dict(items=["exact", "record", "scala"])),
            ("adaptation_mode", "Adaptation", "choice", dict(items=["sfa_divisor_maxap", "scala_ai"])),
            ("latency_mode", "FS latency to", "choice", dict(items=["peak", "threshold"])),
            ("ljp_mV", "Liquid junction correction", "float", dict(suffix="mV")),
        ])
        for f in (self.f1, self.f2, self.f3):
            sv.addWidget(f)
            f.changed.connect(self.recompute)
        sv.addStretch(1)
        lay.addWidget(scroll(side))
        right = QtWidgets.QSplitter(Qt.Vertical)
        r1 = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(r1)
        h.setContentsMargins(0, 0, 0, 0)
        v = QtWidgets.QVBoxLayout()
        self.spin = QtWidgets.QSpinBox()
        self.spin.valueChanged.connect(self.redraw)
        v.addWidget(QtWidgets.QLabel("Sweep"))
        v.addWidget(self.spin)
        self.lbl = QtWidgets.QLabel()
        self.lbl.setWordWrap(True)
        self.lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.lbl, 1)
        h.addLayout(v, 1)
        self.pv = plot("Vm with detected APs", "ms", "mV")
        h.addWidget(self.pv, 4)
        right.addWidget(r1)
        r2 = QtWidgets.QSplitter()
        self.pap = plot("First AP of the sweep", "ms", "mV")
        self.pph = plot("Phase plot", "Vm (mV)", "dV/dt (mV/ms)")
        self.pfi = plot("Firing vs dI", "dI (pA)", "APs")
        for w in (self.pap, self.pph, self.pfi):
            r2.addWidget(w)
        right.addWidget(r2)
        self.tbl = table(["Sweep", "dI pA", "APs", "First AP (ms)", "Mean ISI ms", "SFA divisor", "SFA LV", "AI (ISI2/ISI1)"])
        right.addWidget(self.tbl)
        right.setSizes([260, 260, 200])
        lay.addWidget(right, 1)

    def set_recording(self, rec):
        self.spin.setRange(0, rec.n_sweeps - 1)
        self.recompute()

    def recompute(self):
        rec = self.main.rec
        if rec is None or rec.vm() is None:
            return
        self.sp.window = tuple(self.main.passive.p.step)
        self.per, self.thr_line = S.detect_all(rec.t, rec.vm(), self.sp)
        counts = [len(x) for x in self.per]
        base, meas = self.main.passive.p.regions()
        I = rec.im()
        self.dI = np.array([window_mean(rec.t, I[s], *meas) - window_mean(rec.t, I[s], *base) for s in range(rec.n_sweeps)])
        rows = []
        for s, x in enumerate(self.per):
            d = S.isis(x)
            rows.append((s, self.dI[s], len(x), x[0].peak_t if x else None, S.mean_isi(x), S.sfa_divisor(x),
                         S.sfa_local_variance(x), S.adaptation_index_scala(x)))
        fill(self.tbl, rows)
        self.pfi.clear()
        self.pfi.plot(self.dI, counts, pen=None, symbol="o", symbolBrush=BLUE, symbolSize=7)
        mx = int(np.argmax(counts)) if max(counts) else None
        txt = f"Max APs: {max(counts)}" + (f" (sweep {mx})" if mx is not None else "")
        if self.thr_line is not None:
            txt += f"<br>Record threshold {self.thr_line:.2f} mV"
        self.lbl.setText(txt)
        self.redraw()

    def redraw(self, *_):
        rec = self.main.rec
        if rec is None or not self.per:
            return
        s = self.spin.value()
        t, v = rec.t, rec.vm()[s]
        self.pv.clear()
        self.pv.plot(t, v, pen=pg.mkPen((40, 40, 40), width=1))
        sps = self.per[s]
        if sps:
            self.pv.plot([x.peak_t for x in sps], [x.peak_v for x in sps], pen=None, symbol="o", symbolBrush=RED, symbolSize=6)
        if self.thr_line is not None:
            self.pv.addItem(pg.InfiniteLine(self.thr_line, angle=0, pen=pg.mkPen(RED, style=Qt.DashLine)))
        self.pap.clear()
        self.pph.clear()
        if not sps:
            self.lbl.setText(self.lbl.text().split("<br>Sweep")[0] + f"<br>Sweep {s}: no APs")
            return
        k = K.analyse_ap(t, v, sps[0].peak_idx, self.kp)
        base = self.lbl.text().split("<br>Sweep")[0]
        if not k.ok:
            self.lbl.setText(base + f"<br>Sweep {s}: {len(sps)} APs; kinetics failed: {k.why}")
            return
        i0, i1 = max(0, k.peak_idx - int(5 / ts_ms(t))), min(len(t) - 1, k.peak_idx + int(15 / ts_ms(t)))
        self.pap.plot(t[i0:i1], v[i0:i1], pen=pg.mkPen((40, 40, 40)))
        for tt, vv, col in ((k.thr_t, k.thr_v, RED), (k.peak_t, k.peak_v, RED), (k.fahp_t, k.fahp_v, GREEN), (k.mahp_t, k.mahp_v, GREEN)):
            self.pap.plot([tt], [vv], pen=None, symbol="o", symbolBrush=col, symbolSize=8)
        m = k.marks
        self.pap.plot([m["rise"][0], m["rise"][1]], [m["rise"][2], m["rise"][3]], pen=None, symbol="+", symbolBrush=(150, 0, 200), symbolSize=12)
        self.pap.plot([m["decay"][0], m["decay"][1]], [m["decay"][2], m["decay"][3]], pen=None, symbol="+", symbolBrush=BLUE, symbolSize=12)
        self.pap.plot([m["half"][0], m["half"][1]], [m["half"][2]] * 2, pen=pg.mkPen((230, 170, 0), width=2))
        x, y = K.phase_plot(t, v, k.peak_idx)
        self.pph.plot(x, y, pen=pg.mkPen(BLUE))
        self.lbl.setText(base + f"<br><b>Sweep {s}</b>: {len(sps)} APs, first AP:<br>thr {k.thr_v:.3f} mV, amp {k.amplitude:.3f} mV,"
                         f"<br>rise {k.rise_ms:.3f}, decay {k.decay_ms:.3f}, FWHM {k.half_width_ms:.3f} ms,"
                         f"<br>fAHP {k.fahp:.3f}, mAHP {k.mahp:.3f} mV")


# --------------------------------------------------------------------------------------------------- Events
class EventsTab(QtWidgets.QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.p = E.EventParams()
        self.res = None
        self.omit_start, self.omit_stop = QtWidgets.QDoubleSpinBox(), QtWidgets.QDoubleSpinBox()
        lay = QtWidgets.QHBoxLayout(self)
        side = QtWidgets.QWidget()
        sv = QtWidgets.QVBoxLayout(side)
        self.chan = QtWidgets.QComboBox()
        self.chan.currentIndexChanged.connect(lambda *_: self.run())
        sv.addWidget(QtWidgets.QLabel("Data channel"))
        sv.addWidget(self.chan)
        self.f1 = ParamForm("Detection", self.p, [
            ("direction", "Direction (-1 inward / 1 outward)", "choice", dict(items=["-1", "1"])),
            ("method", "Method", "choice", dict(items=["deconvolution", "correlation", "detection_criterion", "threshold"],
                                               tip="deconvolution = Pernia-Andrade 2012 ('convolution method')")),
            ("rise_ms", "Template rise", "float", dict(suffix="ms", dec=3, lo=0.01)),
            ("decay_ms", "Template decay", "float", dict(suffix="ms", dec=3, lo=0.01)),
            ("width_ms", "Template width", "float", dict(suffix="ms", lo=1)),
            ("corr_cutoff", "Correlation cutoff", "float", dict(dec=2)),
            ("dc_cutoff", "Detection criterion", "float", dict(dec=2)),
            ("deconv_sd", "Deconvolution SD cutoff", "float", dict(dec=2)),
            ("deconv_low_hz", "Deconv low cutoff", "float", dict(suffix="Hz", dec=2)),
            ("deconv_high_hz", "Deconv high cutoff", "float", dict(suffix="Hz")),
        ])
        self.f2 = ParamForm("Thresholds / exclusion", self.p, [
            ("lower_mode", "Lower threshold", "choice", dict(items=["linear", "rms", "curve", "none"])),
            ("lower_value", "  linear level / curve offset", "float", dict(suffix="pA")),
            ("rms_multiple", "  RMS multiple", "float"),
            ("curve_order", "  curve polynomial order", "int", dict(lo=1, hi=10)),
            ("upper_value", "Upper threshold (0 = off)", "float", dict(suffix="pA")),
            ("amplitude_threshold", "Amplitude threshold", "float", dict(suffix="pA")),
            ("min_distance_ms", "Local maximum period", "float", dict(suffix="ms")),
            ("average_peak_ms", "Average peak (0 = off)", "float", dict(suffix="ms")),
        ])
        self.f3 = ParamForm("Kinetics", self.p, [
            ("baseline_search_ms", "Baseline search period", "float", dict(suffix="ms")),
            ("average_baseline_ms", "Average baseline", "float", dict(suffix="ms")),
            ("decay_search_ms", "Decay search period", "float", dict(suffix="ms")),
            ("decay_endpoint", "Decay endpoint", "choice", dict(items=["first_baseline_cross", "entire_search_region"])),
            ("decay_pct", "Decay %", "float", dict(suffix="%")),
            ("rise_pct", "Rise % cutoffs", "pair", {}),
            ("interp", "Interpolate to 200 kHz", "bool", {}),
            ("fit_decay", "Mono-exponential decay fit", "bool", {}),
        ])
        for f in (self.f1, self.f2, self.f3):
            sv.addWidget(f)
        g = QtWidgets.QGroupBox("Omit time period in every record (ms; e.g. test pulse)")
        gl = QtWidgets.QHBoxLayout(g)
        for w in (self.omit_start, self.omit_stop):
            w.setRange(0, 1e9)
            w.setDecimals(1)
            gl.addWidget(w)
        sv.addWidget(g)
        self.btn = QtWidgets.QPushButton("Run event detection")
        self.btn.clicked.connect(self.run)
        sv.addWidget(self.btn)
        self.btn_csv = QtWidgets.QPushButton("Export events CSV…")
        self.btn_csv.clicked.connect(self.export_csv)
        sv.addWidget(self.btn_csv)
        sv.addStretch(1)
        lay.addWidget(scroll(side))
        right = QtWidgets.QSplitter(Qt.Vertical)
        self.spin = QtWidgets.QSpinBox()
        self.spin.valueChanged.connect(self.redraw)
        top = QtWidgets.QWidget()
        tv = QtWidgets.QVBoxLayout(top)
        tv.setContentsMargins(0, 0, 0, 0)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Record"))
        row.addWidget(self.spin)
        self.lbl = QtWidgets.QLabel()
        self.lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self.lbl, 1)
        tv.addLayout(row)
        self.pd = plot("Data with detected events", "ms", "pA")
        self.pm = plot("Detection measure", "ms")
        self.pm.setXLink(self.pd)
        tv.addWidget(self.pd, 3)
        tv.addWidget(self.pm, 1)
        right.addWidget(top)
        bot = QtWidgets.QSplitter()
        self.pavg = plot("Events aligned on half-rise", "ms", "pA")
        self.pcdf = plot("Cumulative probability: amplitude", "pA", "P")
        self.pint = plot("Cumulative probability: inter-event interval", "ms", "P")
        for w in (self.pavg, self.pcdf, self.pint):
            bot.addWidget(w)
        right.addWidget(bot)
        self.tbl = table(["#", "Rec", "Time ms", "Peak pA", "Baseline pA", "Amp pA", "Rise ms", "Decay% ms", "Tau ms", "FWHM ms", "AUC", "Interval ms"])
        right.addWidget(self.tbl)
        right.setSizes([380, 220, 200])
        lay.addWidget(right, 1)

    def set_recording(self, rec):
        self.chan.blockSignals(True)
        self.chan.clear()
        for k, (n, u) in enumerate(zip(rec.names, rec.units)):
            self.chan.addItem(f"{k}: {n} ({u})")
        k = rec.channel_by_units("pA", 0)
        self.chan.setCurrentIndex(k)
        self.chan.blockSignals(False)
        self.spin.setRange(0, rec.n_sweeps - 1)
        self.res = None

    def run(self, *_):
        rec = self.main.rec
        if rec is None:
            return
        k = self.chan.currentIndex()
        Y = rec.ch[k]
        self.p.direction = int(self.p.direction)
        self.p.omit = ((self.omit_start.value(), self.omit_stop.value()),) if self.omit_stop.value() > self.omit_start.value() else ()
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.res = E.analyse_events(rec.t, Y, self.p, rec.sweep_start_ms)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        S_ = E.summarize(self.res)
        self.lbl.setText(f"  {S_['n']} events, {S_['frequency_hz']:.3f} Hz; amp median {S_.get('amplitude_median', np.nan):.1f} pA; "
                         f"rise {S_.get('rise_ms_median', np.nan):.2f} ms, decay% {S_.get('decay_pct_ms_median', np.nan):.2f} ms, "
                         f"FWHM {S_.get('half_width_ms_median', np.nan):.2f} ms")
        ev = self.res.events
        fill(self.tbl, [(i + 1, e.record, e.time, e.peak_im, e.bl_im, e.amplitude, e.rise_ms, e.decay_pct_ms, e.tau_ms,
                         e.half_width_ms, e.auc, e.interval_ms) for i, e in enumerate(ev)])
        self.redraw()
        self.pcdf.clear()
        self.pint.clear()
        if ev:
            x, y = E.cumulative_probability([abs(e.amplitude) for e in ev])
            self.pcdf.plot(x, y, pen=pg.mkPen(BLUE, width=2))
            iv = [e.interval_ms for e in ev if np.isfinite(e.interval_ms)]
            if iv:
                x, y = E.cumulative_probability(iv)
                self.pint.plot(x, y, pen=pg.mkPen(BLUE, width=2))
            tx, M, mean = E.average_event(rec.t, Y, ev, 5.0, 30.0, "rise_half")
            self.pavg.clear()
            for row in M[:100]:
                self.pavg.plot(tx, row, pen=pg.mkPen((190, 190, 190, 120)))
            if mean.size:
                self.pavg.plot(tx, mean, pen=pg.mkPen(RED, width=2))

    def redraw(self, *_):
        rec = self.main.rec
        if rec is None or self.res is None:
            return
        s = self.spin.value()
        Y = rec.ch[self.chan.currentIndex()]
        self.pd.clear()
        self.pm.clear()
        self.pd.plot(rec.t, Y[s], pen=pg.mkPen((40, 40, 40), width=1))
        ev = [e for e in self.res.events if e.record == s]
        if ev:
            self.pd.plot([e.peak_t for e in ev], [e.peak_im for e in ev], pen=None, symbol="o", symbolBrush=RED, symbolSize=7)
            self.pd.plot([e.bl_t for e in ev], [e.bl_im for e in ev], pen=None, symbol="o", symbolBrush=BLUE, symbolSize=5)
            self.pd.plot([rec.t[e.end_idx] for e in ev], [Y[s][e.end_idx] for e in ev], pen=None, symbol="o", symbolBrush=(150, 0, 200), symbolSize=5)
        m = self.res.measure[s]
        if m is not None:
            self.pm.plot(rec.t, m, pen=pg.mkPen((230, 130, 0)))
            self.pm.addItem(pg.InfiniteLine(self.res.threshold, angle=0, pen=pg.mkPen(RED)))

    def export_csv(self):
        if not self.res or not self.res.events:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export events", "events.csv", "CSV (*.csv)")
        if path:
            with open(path, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow([f.name for f in dataclasses.fields(E.Event)])
                for e in self.res.events:
                    w.writerow([getattr(e, f.name) for f in dataclasses.fields(E.Event)])


# ---------------------------------------------------------------------------------------------- Cell summary
class SummaryTab(QtWidgets.QWidget):
    META = ["Differentiation date", "Days post diff", "Date Recorded", "Rat", "Slice number", "Cell number", "Dlx", "notes"]

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.rep = None
        self.spont = None
        lay = QtWidgets.QHBoxLayout(self)
        side = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(side)
        self.meta = {}
        for k in self.META:
            self.meta[k] = QtWidgets.QLineEdit()
            f.addRow(k, self.meta[k])
        self.btn_sp = QtWidgets.QPushButton("RMP file (optional)…")
        self.btn_sp.clicked.connect(self.pick_spont)
        self.lbl_sp = QtWidgets.QLabel("none: RMP from the step baseline")
        self.lbl_sp.setWordWrap(True)
        f.addRow(self.btn_sp)
        f.addRow(self.lbl_sp)
        b = QtWidgets.QPushButton("Build cell row")
        b.clicked.connect(self.build)
        f.addRow(b)
        b2 = QtWidgets.QPushButton("Append / save row to CSV…")
        b2.clicked.connect(self.save)
        f.addRow(b2)
        lay.addWidget(scroll(side))
        right = QtWidgets.QSplitter(Qt.Vertical)
        self.tbl = table(["Column", "Value"])
        self.dec = QtWidgets.QTextEdit()
        self.dec.setReadOnly(True)
        right.addWidget(self.tbl)
        right.addWidget(self.dec)
        right.setSizes([420, 380])
        lay.addWidget(right, 1)

    def pick_spont(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Resting-potential recording", "", "Axon ABF (*.abf)")
        if path:
            self.spont = path
            self.lbl_sp.setText(os.path.basename(path))

    def build(self):
        m = self.main
        if m.rec is None:
            return
        cfg = R.CellConfig(ljp_mV=m.spikes.cfg.ljp_mV, spike=m.spikes.sp, kin=m.spikes.kp, passive=m.passive.p,
                           rheobase_mode=m.spikes.cfg.rheobase_mode, adaptation_mode=m.spikes.cfg.adaptation_mode,
                           latency_mode=m.spikes.cfg.latency_mode,
                           metadata={k: w.text() for k, w in self.meta.items() if w.text()})
        cfg.spike.window = tuple(cfg.passive.step)
        self.rep = R.build_cell_report(m.rec.path, cfg, self.spont)
        fill(self.tbl, [(k, self.rep.row.get(k, "")) for k in R.COLUMNS])
        self.dec.setPlainText("DECISIONS\n" + "\n".join(f"- {k}: {v}" for k, v in self.rep.decisions))

    def save(self):
        if self.rep is None:
            self.build()
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save row", "cells.csv", "CSV (*.csv)")
        if not path:
            return
        new = not os.path.exists(path)
        with open(path, "a", newline="") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(R.COLUMNS)
            w.writerow([self.rep.row.get(c, "") for c in R.COLUMNS])


# ----------------------------------------------------------------------------------------------- main window
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ephys-suite")
        self.resize(1600, 950)
        self.rec = None
        self.tabs = QtWidgets.QTabWidget()
        self.setCentralWidget(self.tabs)
        self.passive = PassiveTab(self)
        self.spikes = SpikesTab(self)
        self.events = EventsTab(self)
        self.summary = SummaryTab(self)
        for w, n in ((self.passive, "Passive (Rin / sag / tau / Cm)"), (self.spikes, "AP counting and kinetics"),
                     (self.events, "Events"), (self.summary, "Cell summary")):
            self.tabs.addTab(w, n)
        m = self.menuBar().addMenu("&File")
        a = m.addAction("&Open .abf…")
        a.setShortcut("Ctrl+O")
        a.triggered.connect(self.open_dialog)
        self.statusBar().showMessage("File > Open (Ctrl+O)")
        self._step_busy = False

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
        self.setWindowTitle(f"ephys-suite: {os.path.basename(path)}")
        has_cc = rec.vm() is not None and rec.im() is not None
        self.events.set_recording(rec)
        if has_cc:
            self.passive.set_recording(rec)
            self.spikes.set_recording(rec)
        self.statusBar().showMessage(f"{path}: {rec.n_sweeps} sweeps, channels {[f'{n} ({u})' for n, u in zip(rec.names, rec.units)]}")

    def step_changed(self):
        if self.rec is not None and self.spikes.per is not None and not self._step_busy and self.spikes.isVisible():
            self._step_busy = True
            self.spikes.recompute()
            self._step_busy = False


def run(path=None):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    if path:
        w.load(path)
    app.exec()
