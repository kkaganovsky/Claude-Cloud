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
class EventViewBox(pg.ViewBox):
    """ViewBox that hands clicks / drags to the Events tab first (add, select, delete, baseline), else behaves normally."""

    def __init__(self, tab):
        super().__init__()
        self.tab = tab

    def mouseClickEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            pt = self.mapToView(ev.pos())
            if self.tab.on_click(pt.x(), pt.y(), self):
                ev.accept()
                return
        super().mouseClickEvent(ev)

    def mouseDragEvent(self, ev, axis=None):
        if ev.button() == Qt.LeftButton and self.tab.drag_enabled():
            ev.accept()
            x0 = self.mapToView(ev.buttonDownPos()).x()
            x1 = self.mapToView(ev.pos()).x()
            self.tab.drag(x0, x1, ev.isFinish())
            return
        super().mouseDragEvent(ev, axis)


class EventsTab(QtWidgets.QWidget):
    HINT_IDLE = ("Click an event marker to select it, click it again (or press Delete) to remove it.  "
                 "<b>A</b> = add events,  <b>PageDown / PageUp</b> = next / previous window,  <b>←/→</b> = previous / next event.")

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.p = E.EventParams()
        self.session = None
        self.sel = None                 # selected (primed) event
        self.pending = None             # (record, peak_idx) waiting for a baseline click, or ('edit', event)
        self.settings = QtCore.QSettings("ephys-suite", "events")
        self._busy = False
        self.omit_start, self.omit_stop = QtWidgets.QDoubleSpinBox(), QtWidgets.QDoubleSpinBox()
        lay = QtWidgets.QHBoxLayout(self)
        lay.addWidget(self._build_side())
        lay.addWidget(self._build_main(), 1)
        self._shortcuts()
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._refresh_summary)

    # ---------------------------------------------------------------------------------------- layout
    def _build_side(self):
        side = QtWidgets.QWidget()
        sv = QtWidgets.QVBoxLayout(side)
        self.chan = QtWidgets.QComboBox()
        self.chan.currentIndexChanged.connect(lambda *_: self._new_session())
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
            ("lower_mode", "Lower threshold", "choice", dict(items=["rms", "linear", "curve", "none"],
                                                           tip="rms = record mean -/+ n x RMS (default; omit periods excluded)")),
            ("rms_multiple", "  RMS multiple", "float", dict(dec=2, step=0.5)),
            ("lower_value", "  linear level / curve offset", "float", dict(suffix="pA")),
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
        self.chk_keep = QtWidgets.QCheckBox("Keep manual edits when re-running")
        self.chk_keep.setChecked(True)
        sv.addWidget(self.chk_keep)
        b2 = QtWidgets.QPushButton("Discard all manual edits")
        b2.clicked.connect(self.discard_edits)
        sv.addWidget(b2)
        b3 = QtWidgets.QPushButton("Export events CSV…")
        b3.clicked.connect(self.export_csv)
        sv.addWidget(b3)
        sv.addStretch(1)
        return scroll(side)

    def _tool(self, text, tip, checkable=True, checked=False):
        b = QtWidgets.QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.setChecked(checked)
        b.setToolButtonStyle(Qt.ToolButtonTextOnly)
        b.setMinimumHeight(26)
        b.setStyleSheet("QToolButton{padding:2px 8px;border:1px solid #b5b5b5;border-radius:3px;background:#f4f4f4;}"
                        "QToolButton:checked{background:#2e9e57;color:white;font-weight:bold;border-color:#1f7a41;}"
                        "QToolButton:pressed{background:#d5d5d5;}")
        return b

    def _build_main(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        # --- edit toolbar
        row = QtWidgets.QHBoxLayout()
        self.btn_add = self._tool("Add events (A)", "Click an event (or drag across it) to add it. The peak snaps to the extreme "
                                                    "within the snap window.")
        self.btn_fit = self._tool("Fit kinetics (K)", "ON: added events are measured fully and must pass the thresholds "
                                                      "(like Easy Electrophysiology).\nOFF: any event can be added regardless of "
                                                      "thresholds; only peak, baseline and amplitude are measured.", checked=True)
        self.btn_bl = self._tool("Manual baseline (B)", "Armed: after you pick a peak you click its baseline level, then this "
                                                        "switches itself off.\nWith an event selected (and Add off): moves that "
                                                        "event's baseline. Also switches on by itself when no baseline is found.")
        self.btn_del = self._tool("Delete selected (Del)", "Remove the selected event", checkable=False)
        self.btn_undo = self._tool("Undo (Ctrl+Z)", "Undo the last edit", checkable=False)
        self.btn_redo = self._tool("Redo (Ctrl+Y)", "Redo", checkable=False)
        self.snap = QtWidgets.QDoubleSpinBox()
        self.snap.setRange(0.1, 50)
        self.snap.setValue(2.0)
        self.snap.setSuffix(" ms")
        self.snap.setToolTip("Snap window: a click finds the peak within ± this many ms")
        for b in (self.btn_add, self.btn_fit, self.btn_bl, self.btn_del, self.btn_undo, self.btn_redo):
            row.addWidget(b)
        row.addWidget(QtWidgets.QLabel("snap ±"))
        row.addWidget(self.snap)
        row.addStretch(1)
        v.addLayout(row)
        self.hint = QtWidgets.QLabel(self.HINT_IDLE)
        self.hint.setWordWrap(True)
        self._hint_style(None)
        v.addWidget(self.hint)
        # --- view toolbar
        vr = QtWidgets.QHBoxLayout()
        self.spin = QtWidgets.QSpinBox()
        self.spin.valueChanged.connect(self._record_changed)
        vr.addWidget(QtWidgets.QLabel("Record"))
        vr.addWidget(self.spin)
        self.b_prev = self._tool("◀ page", "Previous window (PageUp / X)", checkable=False)
        self.b_next = self._tool("page ▶", "Next window (PageDown / Z)", checkable=False)
        self.b_pe = self._tool("◀ event", "Previous event (←)", checkable=False)
        self.b_ne = self._tool("event ▶", "Next event (→)", checkable=False)
        for b in (self.b_prev, self.b_next, self.b_pe, self.b_ne):
            vr.addWidget(b)
        self.vx, self.vw, self.vy0, self.vy1 = (self._vspin(-1e9, 1e9), self._vspin(1, 1e9), self._vspin(-1e9, 1e9), self._vspin(-1e9, 1e9))
        for lbl, sp in (("X start ms", self.vx), ("width ms", self.vw), ("Y min", self.vy0), ("Y max", self.vy1)):
            vr.addWidget(QtWidgets.QLabel(lbl))
            vr.addWidget(sp)
            sp.valueChanged.connect(self._view_edited)
        self.chk_locky = QtWidgets.QCheckBox("Lock Y")
        self.chk_locky.setToolTip("ON: keep the Y limits above while paging / changing record.\nOFF: fit Y to the visible data each time.")
        self.chk_locky.setChecked(True)
        vr.addWidget(self.chk_locky)
        self.b_fity = self._tool("Fit Y", "Fit Y to the visible data", checkable=False)
        self.b_save = self._tool("Remember view", "Save the current window width and Y limits; they are re-applied whenever a file is opened.", checkable=False)
        vr.addWidget(self.b_fity)
        vr.addWidget(self.b_save)
        vr.addStretch(1)
        v.addLayout(vr)
        # --- plots
        right = QtWidgets.QSplitter(Qt.Vertical)
        top = QtWidgets.QWidget()
        tv = QtWidgets.QVBoxLayout(top)
        tv.setContentsMargins(0, 0, 0, 0)
        self.lbl = QtWidgets.QLabel()
        self.lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        tv.addWidget(self.lbl)
        self.vb = EventViewBox(self)
        self.pd = pg.PlotWidget(viewBox=self.vb)
        self.pd.showGrid(x=True, y=True, alpha=0.2)
        self.pd.setLabel("bottom", "ms")
        self.pd.setLabel("left", "pA")
        self.pd.setFocusPolicy(Qt.StrongFocus)
        for ax in ("left", "bottom"):
            self.pd.getAxis(ax).enableAutoSIPrefix(False)
        self.curve = self.pd.plot([], [], pen=pg.mkPen((40, 40, 40), width=1))
        self.curve.setClipToView(True)
        self.curve.setDownsampling(auto=True, method="peak")
        self.vb.disableAutoRange()                      # the window you set (or remembered) must not be reset by the data
        self.thr_line = pg.InfiniteLine(angle=0, pen=pg.mkPen(RED, style=Qt.DashLine))
        self.pd.addItem(self.thr_line)
        self.sc_peak = pg.ScatterPlotItem(size=8, pen=pg.mkPen("k"))
        self.sc_bl = pg.ScatterPlotItem(size=6, brush=pg.mkBrush(*BLUE), pen=pg.mkPen("k"))
        self.sc_end = pg.ScatterPlotItem(size=5, brush=pg.mkBrush(150, 0, 200), pen=pg.mkPen("k"))
        self.sc_sel = pg.ScatterPlotItem(size=16, brush=pg.mkBrush(None), pen=pg.mkPen((0, 90, 255), width=3))
        self.sc_pend = pg.ScatterPlotItem(size=14, brush=pg.mkBrush(255, 150, 0, 160), pen=pg.mkPen("k"))
        for it in (self.sc_end, self.sc_bl, self.sc_peak, self.sc_sel, self.sc_pend):
            it.setZValue(10)
            self.pd.addItem(it)
        self.band = pg.LinearRegionItem(movable=False, brush=pg.mkBrush(255, 150, 0, 50), pen=pg.mkPen((255, 150, 0)))
        self.pm = plot("Detection measure", "ms")
        self.pm.setXLink(self.pd)
        self.pm.setMaximumHeight(130)
        tv.addWidget(self.pd, 4)
        tv.addWidget(self.pm, 1)
        right.addWidget(top)
        bot = QtWidgets.QSplitter()
        self.pavg = plot("Events aligned on half-rise", "ms", "pA")
        self.pcdf = plot("Cumulative probability: amplitude", "pA", "P")
        self.pint = plot("Cumulative probability: inter-event interval", "ms", "P")
        for pw in (self.pavg, self.pcdf, self.pint):
            bot.addWidget(pw)
        right.addWidget(bot)
        self.tbl = table(["#", "Rec", "Time ms", "Peak pA", "Baseline pA", "Amp pA", "Rise ms", "Decay% ms", "Tau ms", "FWHM ms",
                          "AUC", "Interval ms", "Fit", "Manual"])
        self.tbl.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl.itemSelectionChanged.connect(self._table_selected)
        right.addWidget(self.tbl)
        right.setSizes([480, 170, 170])
        v.addWidget(right, 1)
        # wiring
        self.btn_add.toggled.connect(self._mode_changed)
        self.btn_bl.toggled.connect(self._bl_toggled)
        self.btn_del.clicked.connect(self.delete_selected)
        self.btn_undo.clicked.connect(lambda: self._undo(True))
        self.btn_redo.clicked.connect(lambda: self._undo(False))
        self.b_prev.clicked.connect(lambda: self.page(-1))
        self.b_next.clicked.connect(lambda: self.page(+1))
        self.b_pe.clicked.connect(lambda: self.goto_event(-1))
        self.b_ne.clicked.connect(lambda: self.goto_event(+1))
        self.b_fity.clicked.connect(self.fit_y)
        self.b_save.clicked.connect(self.remember_view)
        self.vb.sigRangeChanged.connect(self._range_changed)
        return w

    @staticmethod
    def _vspin(lo, hi):
        sp = QtWidgets.QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setDecimals(1)
        sp.setKeyboardTracking(False)
        sp.setMaximumWidth(90)
        return sp

    def _shortcuts(self):
        from PySide6.QtGui import QKeySequence, QShortcut
        def sc(key, fn):
            q = QShortcut(QKeySequence(key), self)
            q.setContext(Qt.WidgetWithChildrenShortcut)
            q.activated.connect(lambda: self._typing() or fn())
        sc("A", lambda: self.btn_add.toggle())
        sc("K", lambda: self.btn_fit.toggle())
        sc("B", lambda: self.btn_bl.toggle())
        for k in ("Delete", "Backspace", "Space"):
            sc(k, self.delete_selected)
        sc("Right", lambda: self.goto_event(+1))
        sc("Left", lambda: self.goto_event(-1))
        sc("PageDown", lambda: self.page(+1))
        sc("Z", lambda: self.page(+1))
        sc("PageUp", lambda: self.page(-1))
        sc("X", lambda: self.page(-1))
        sc("Ctrl+Z", lambda: self._undo(True))
        sc("Ctrl+Y", lambda: self._undo(False))
        sc("Esc", self.cancel)

    @staticmethod
    def _typing():
        w = QtWidgets.QApplication.focusWidget()
        return isinstance(w, (QtWidgets.QAbstractSpinBox, QtWidgets.QLineEdit, QtWidgets.QTextEdit))

    def _hint_style(self, kind):
        col = {None: "#eef3fb", "add": "#e8f7ec", "pending": "#fff1d6", "warn": "#fde8e8"}[kind]
        self.hint.setStyleSheet(f"background:{col}; padding:4px; border:1px solid #c8c8c8; border-radius:3px;")

    def say(self, text, kind=None):
        self.hint.setText(text)
        self._hint_style(kind)

    # ------------------------------------------------------------------------------------- data / session
    @property
    def rec_i(self):
        return self.spin.value()

    def set_recording(self, rec):
        self._busy = True
        self.chan.clear()
        for k, (n, u) in enumerate(zip(rec.names, rec.units)):
            self.chan.addItem(f"{k}: {n} ({u})")
        self.chan.setCurrentIndex(rec.channel_by_units("pA", 0))
        self.spin.setRange(0, rec.n_sweeps - 1)
        self.spin.setValue(0)
        self._busy = False
        self._new_session()
        self._apply_saved_view()

    def _new_session(self):
        rec = self.main.rec
        if rec is None or self._busy:
            return
        self.sel, self.pending = None, None
        self.p.direction = int(self.p.direction)
        self.session = E.EventSession(rec.t, rec.ch[self.chan.currentIndex()], self.p, rec.sweep_start_ms)
        self.pd.setLabel("left", rec.units[self.chan.currentIndex()])
        self._load_record(reset_x=True)
        self._refresh_all()

    def run(self, *_):
        if self.session is None:
            return
        self.p.direction = int(self.p.direction)
        self.p.omit = ((self.omit_start.value(), self.omit_stop.value()),) if self.omit_stop.value() > self.omit_start.value() else ()
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.session.detect(keep_edits=self.chk_keep.isChecked())
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.sel = None
        self._load_record(reset_x=False)
        self._refresh_all()
        self.say(f"Detected {len(self.session.events)} events. Press <b>A</b> to add missed events, click a marker twice to remove one.")

    def discard_edits(self):
        if self.session is None or self.session.log.is_empty():
            return
        if QtWidgets.QMessageBox.question(self, "Discard manual edits", "Remove all manually added / deleted events and baselines?") \
                == QtWidgets.QMessageBox.Yes:
            self.session.log = E.EditLog()
            self.session._undo.clear()
            self.session._redo.clear()
            self.session._rebuild()
            self.sel = None
            self._refresh_all()

    # ---------------------------------------------------------------------------------------- plotting
    def _load_record(self, reset_x):
        rec = self.main.rec
        if rec is None or self.session is None:
            return
        y = self.session.Y[self.rec_i]
        self.curve.setData(rec.t, y)
        self.vb.disableAutoRange()
        line = E._lower_threshold_line(rec.t, y, self.p)
        if line is not None and self.p.lower_mode != "none":
            self.thr_line.setValue(float(line[0]))
            self.thr_line.show()
        else:
            self.thr_line.hide()
        self.pm.clear()
        m = self.session.auto.measure[self.rec_i] if self.session.auto and self.session.auto.measure else None
        if m is not None:
            self.pm.plot(rec.t, m, pen=pg.mkPen((230, 130, 0)))
            self.pm.addItem(pg.InfiniteLine(self.session.auto.threshold, angle=0, pen=pg.mkPen(RED)))
        if reset_x:
            w = self.vw.value() if self.vw.value() > 1 else 1000.0
            self._set_view(0.0, w)

    def _record_events(self):
        return [e for e in (self.session.events if self.session else []) if e.record == self.rec_i]

    def _markers(self):
        ev = self._record_events()
        rec = self.main.rec
        y = self.session.Y[self.rec_i]
        spots = []
        for e in ev:
            if e.manual:
                brush, sym = pg.mkBrush(40, 170, 70), "d"
            elif not e.fitted:
                brush, sym = pg.mkBrush(255, 255, 255), "o"
            else:
                brush, sym = pg.mkBrush(*RED), "o"
            spots.append(dict(pos=(e.peak_t, e.peak_im), brush=brush, symbol=sym, data=e))
        self.sc_peak.setData(spots)
        self.sc_bl.setData([e.bl_t for e in ev], [e.bl_im for e in ev])
        fe = [e for e in ev if e.fitted and e.end_idx >= 0]
        self.sc_end.setData([rec.t[e.end_idx] for e in fe], [y[e.end_idx] for e in fe])
        sel = self.sel if (self.sel is not None and self.sel.record == self.rec_i) else None
        self.sc_sel.setData([sel.peak_t] if sel else [], [sel.peak_im] if sel else [])
        if self.pending and self.pending[0] != "edit" and self.pending[0] == self.rec_i:
            k = self.pending[1]
            self.sc_pend.setData([rec.t[k]], [y[k]])
        elif self.pending and self.pending[0] == "edit":
            e = self.pending[1]
            self.sc_pend.setData([e.bl_t], [e.bl_im])
        else:
            self.sc_pend.setData([], [])

    def _refresh_all(self):
        self._markers()
        self._fill_table()
        self._timer.start(120)
        self._summary_label()

    def _summary_label(self):
        if self.session is None:
            return
        S_ = E.summarize(self.session.results())
        n_man = sum(1 for e in self.session.events if e.manual)
        self.lbl.setText(f"  {S_['n']} events ({n_man} added by hand), {S_['frequency_hz']:.3f} Hz; amp median "
                         f"{S_.get('amplitude_median', np.nan):.1f}; rise {S_.get('rise_ms_median', np.nan):.2f} ms, decay% "
                         f"{S_.get('decay_pct_ms_median', np.nan):.2f} ms, FWHM {S_.get('half_width_ms_median', np.nan):.2f} ms")

    def _refresh_summary(self):
        rec = self.main.rec
        if rec is None or self.session is None:
            return
        ev = self.session.events
        self.pcdf.clear()
        self.pint.clear()
        self.pavg.clear()
        fitted = [e for e in ev if e.fitted]
        if ev:
            x, y = E.cumulative_probability([abs(e.amplitude) for e in ev])
            self.pcdf.plot(x, y, pen=pg.mkPen(BLUE, width=2))
            iv = [e.interval_ms for e in ev if np.isfinite(e.interval_ms)]
            if iv:
                x, y = E.cumulative_probability(iv)
                self.pint.plot(x, y, pen=pg.mkPen(BLUE, width=2))
        if fitted:
            tx, M, mean = E.average_event(rec.t, self.session.Y, fitted, 5.0, 30.0, "rise_half")
            for row in M[:100]:
                self.pavg.plot(tx, row, pen=pg.mkPen((190, 190, 190, 120)))
            if mean.size:
                self.pavg.plot(tx, mean, pen=pg.mkPen(RED, width=2))

    def _fill_table(self):
        ev = self.session.events if self.session else []
        self._rows = list(ev)
        self.tbl.blockSignals(True)
        fill(self.tbl, [(i + 1, e.record, e.time, e.peak_im, e.bl_im, e.amplitude, e.rise_ms, e.decay_pct_ms, e.tau_ms,
                         e.half_width_ms, e.auc, e.interval_ms, "yes" if e.fitted else "no", "yes" if e.manual else "")
                        for i, e in enumerate(ev)])
        if self.sel in self._rows:
            self.tbl.selectRow(self._rows.index(self.sel))
        self.tbl.blockSignals(False)

    # ------------------------------------------------------------------------------------------- view
    def _set_view(self, x0, width, y=None):
        self._busy = True
        self.vb.setXRange(x0, x0 + width, padding=0)
        if y is not None:
            self.vb.setYRange(y[0], y[1], padding=0)
        self._busy = False
        self._range_changed()

    def _range_changed(self, *_):
        if self._busy:
            return
        (x0, x1), (y0, y1) = self.vb.viewRange()
        for sp, v in ((self.vx, x0), (self.vw, x1 - x0), (self.vy0, y0), (self.vy1, y1)):
            sp.blockSignals(True)
            sp.setValue(v)
            sp.blockSignals(False)

    def _view_edited(self, *_):
        self._busy = True
        self.vb.setXRange(self.vx.value(), self.vx.value() + self.vw.value(), padding=0)
        if self.vy1.value() > self.vy0.value():
            self.vb.setYRange(self.vy0.value(), self.vy1.value(), padding=0)
        self._busy = False

    def fit_y(self):
        if self.session is None:
            return
        t = self.main.rec.t
        (x0, x1), _ = self.vb.viewRange()
        i, j = idx_at(t, x0), idx_at(t, x1)
        seg = self.session.Y[self.rec_i][i:j + 1]
        if seg.size:
            pad = 0.08 * max(float(np.ptp(seg)), 1.0)
            self._busy = True
            self.vb.setYRange(float(seg.min()) - pad, float(seg.max()) + pad, padding=0)
            self._busy = False
            self._range_changed()

    def page(self, d):
        if self.session is None:
            return
        t = self.main.rec.t
        (x0, x1), _ = self.vb.viewRange()
        w = x1 - x0
        nx = x0 + d * 0.9 * w
        rec = self.rec_i
        if nx + w > t[-1] + 1e-9 and d > 0:
            if rec + 1 < self.spin.maximum() + 1:
                self.spin.setValue(rec + 1)          # triggers _record_changed (keeps width, starts at 0)
                return
            nx = max(0.0, t[-1] - w)
        elif nx < t[0] and d < 0:
            if rec > 0:
                self.spin.blockSignals(True)
                self.spin.setValue(rec - 1)
                self.spin.blockSignals(False)
                self._load_record(False)
                self._markers()
                nx = max(0.0, t[-1] - w)
            else:
                nx = 0.0
        self._set_view(nx, w)
        if not self.chk_locky.isChecked():
            self.fit_y()

    def _record_changed(self, *_):
        if self._busy or self.session is None:
            return
        w = self.vw.value()
        self.sel = None if (self.sel is not None and self.sel.record != self.rec_i) else self.sel
        self._load_record(False)
        self._set_view(0.0, w)
        if not self.chk_locky.isChecked():
            self.fit_y()
        self._markers()

    def remember_view(self):
        self.settings.setValue("width", self.vw.value())
        self.settings.setValue("y0", self.vy0.value())
        self.settings.setValue("y1", self.vy1.value())
        self.say("View remembered: window width and Y limits will be re-applied when a file is opened.")

    def _apply_saved_view(self):
        w = self.settings.value("width", None)
        y0, y1 = self.settings.value("y0", None), self.settings.value("y1", None)
        if w is None:
            return
        try:
            self._set_view(0.0, float(w), (float(y0), float(y1)) if y0 is not None and y1 is not None else None)
        except (TypeError, ValueError):
            pass

    # ------------------------------------------------------------------------------------ interaction
    def _px(self, vb):
        dx, dy = vb.viewPixelSize()
        return max(dx, 1e-12), max(dy, 1e-12)

    def _hit(self, x, y, vb, radius=12):
        dx, dy = self._px(vb)
        best, bd = None, radius
        for e in self._record_events():
            d = np.hypot((e.peak_t - x) / dx, (e.peak_im - y) / dy)
            if d < bd:
                best, bd = e, d
        return best

    def drag_enabled(self):
        return self.btn_add.isChecked() and self.pending is None and self.session is not None

    def drag(self, x0, x1, finish):
        if not self.band.scene():
            self.pd.addItem(self.band)
        self.band.setRegion((min(x0, x1), max(x0, x1)))
        if finish:
            self.pd.removeItem(self.band)
            if abs(x1 - x0) > 4 * self._px(self.vb)[0]:
                self.add_at(min(x0, x1), max(x0, x1))
            else:
                self.add_at(x0, None)

    def on_click(self, x, y, vb):
        if self.session is None:
            return False
        self.pd.setFocus()
        if self.pending:
            self.complete_pending(x, y)
            return True
        hit = self._hit(x, y, vb)
        if hit is not None:                              # EE: first click primes (blue), second click deletes
            if self.btn_add.isChecked():                 # in Add mode a click on a marker only selects it (no accidental delete)
                self.select(hit)
                self.say("That event is already there (selected). Press <b>Delete</b> to remove it, or leave Add mode (A) and "
                         "click it twice.", "warn")
            elif hit is self.sel:
                self.delete_selected()
            else:
                self.select(hit)
                self.say("Event selected (blue ring). Click it again or press <b>Delete</b> to remove it; <b>B</b> moves its "
                         "baseline; ←/→ steps between events.")
            return True
        if self.btn_add.isChecked():
            self.add_at(x, None)
            return True
        if self.sel is not None:
            self.select(None)
            return True
        return False

    def select(self, ev):
        self.sel = ev
        self._markers()
        if ev is not None and ev in getattr(self, "_rows", []):
            self.tbl.blockSignals(True)
            self.tbl.selectRow(self._rows.index(ev))
            self.tbl.blockSignals(False)

    def _table_selected(self):
        r = self.tbl.currentRow()
        if 0 <= r < len(self._rows):
            self.focus_event(self._rows[r])

    def focus_event(self, ev, recenter=True):
        if ev.record != self.rec_i:
            self.spin.setValue(ev.record)
        self.select(ev)
        (x0, x1), _ = self.vb.viewRange()
        if recenter and not (x0 < ev.peak_t < x1):
            self._set_view(max(0.0, ev.peak_t - (x1 - x0) / 2), x1 - x0)
            if not self.chk_locky.isChecked():
                self.fit_y()

    def goto_event(self, d):
        evs = self.session.events if self.session else []
        if not evs:
            return
        if self.sel in evs:
            k = evs.index(self.sel) + d
        else:                                            # nearest event to the current view centre
            (x0, x1), _ = self.vb.viewRange()
            cur = [e for e in evs if e.record == self.rec_i]
            c = (x0 + x1) / 2
            k = (evs.index(min(cur, key=lambda e: abs(e.peak_t - c))) if cur else 0)
        self.focus_event(evs[int(np.clip(k, 0, len(evs) - 1))])

    def add_at(self, x0, x1):
        S_ = self.session
        rec = self.rec_i
        pk = S_.snap_peak(rec, x0, x1, half_ms=self.snap.value())
        if self.btn_bl.isChecked():
            return self.begin_pending(rec, pk)
        ev, why = S_.add(rec, pk, fit=self.btn_fit.isChecked())
        self._after_add(ev, why, rec, pk)

    def _after_add(self, ev, why, rec, pk):
        if why == "ok":
            self.sel = ev
            self._refresh_all()
            self.say(f"Added event at {ev.peak_t:.1f} ms (amplitude {ev.amplitude:.1f}"
                     + ("" if ev.fitted else ", kinetics not fitted") + "). Keep clicking events, press <b>PageDown</b> to "
                     "advance, <b>Ctrl+Z</b> to undo.", "add")
        elif why == "duplicate":
            self.select(ev)
            self.say("An event is already there - it is selected now (click it again to delete).", "warn")
        elif why == "no_baseline":
            self.begin_pending(rec, pk)
        else:
            hint = ""
            if self.btn_fit.isChecked() and why in ("lower_threshold", "amplitude_threshold", "upper_threshold"):
                hint = " Turn <b>Fit kinetics</b> off (K) to add it anyway."
            self.say(f"Not added: {E.REASONS.get(why, why)}.{hint}", "warn")

    def begin_pending(self, rec, pk):
        self.pending = (rec, pk)
        self.btn_bl.blockSignals(True)
        self.btn_bl.setChecked(True)
        self.btn_bl.blockSignals(False)
        self._markers()
        self.say("<b>Click the baseline level for this event</b> (to the left of the orange peak). Esc cancels.", "pending")

    def complete_pending(self, x, y):
        t = self.main.rec.t
        idx = idx_at(t, x)
        if self.pending[0] == "edit":
            ev = self.pending[1]
            new, why = self.session.set_baseline(ev, idx, y)
            if new is None:
                self.say(f"Baseline not set: {E.REASONS.get(why, why)}. Click to the left of the peak.", "pending")
                return
            self.sel = new
            msg = "Baseline moved and the event re-measured."
        else:
            rec, pk = self.pending
            new, why = self.session.add(rec, pk, fit=self.btn_fit.isChecked(), baseline=(idx, float(y)))
            if new is None:
                self.say(f"Not added: {E.REASONS.get(why, why)}." + (" Click to the left of the peak." if why == "bad_baseline" else ""),
                         "pending" if why in ("bad_baseline", "wrong_direction") else "warn")
                if why not in ("bad_baseline", "wrong_direction"):
                    self.cancel(quiet=True)
                return
            self.sel = new
            msg = f"Added event at {new.peak_t:.1f} ms with your baseline (amplitude {new.amplitude:.1f})."
        self.pending = None
        self.btn_bl.blockSignals(True)
        self.btn_bl.setChecked(False)
        self.btn_bl.blockSignals(False)
        self._refresh_all()
        self.say(msg + " Manual baseline is off again - keep adding events or press <b>PageDown</b> to advance.", "add")

    def cancel(self, quiet=False):
        self.pending = None
        self.btn_bl.blockSignals(True)
        self.btn_bl.setChecked(False)
        self.btn_bl.blockSignals(False)
        if self.sel is not None:
            self.sel = None
        self._markers()
        if not quiet:
            self.say(self.HINT_IDLE)

    def _mode_changed(self, on):
        self.pd.setCursor(Qt.CrossCursor if on else Qt.ArrowCursor)
        if on:
            self.say("<b>Add mode</b>: click an event (or drag across it). <b>Fit kinetics ON</b> = thresholds apply; <b>OFF</b> = "
                     "any event can be added. Toggle <b>Manual baseline</b> (B) to set the baseline yourself.", "add")
        else:
            self.say(self.HINT_IDLE)

    def _bl_toggled(self, on):
        if on and self.pending is None:
            if self.sel is not None and not self.btn_add.isChecked():
                self.pending = ("edit", self.sel)
                self._markers()
                self.say("<b>Click the new baseline</b> for the selected event (left of its peak). Esc cancels.", "pending")
            else:
                self.say("Manual baseline <b>armed</b>: pick an event's peak, then click its baseline level. It switches off by itself.", "pending")
        elif not on:
            self.pending = None
            self._markers()
            self.say(self.HINT_IDLE)

    def delete_selected(self):
        if self.session is None or self.sel is None:
            return
        ev = self.sel
        self.session.delete(ev)
        k = max(0, self._rows.index(ev) - 1) if ev in self._rows else 0
        self.sel = None
        self._refresh_all()
        self.say(f"Event at {ev.peak_t:.1f} ms removed. Ctrl+Z restores it.")

    def _undo(self, undo):
        if self.session is None:
            return
        ok = self.session.undo() if undo else self.session.redo()
        if ok:
            self.sel = None
            self._refresh_all()
            self.say("Undone." if undo else "Redone.")

    def export_csv(self):
        if not self.session or not self.session.events:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export events", "events.csv", "CSV (*.csv)")
        if path:
            with open(path, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow([f.name for f in dataclasses.fields(E.Event)])
                for e in self.session.events:
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
