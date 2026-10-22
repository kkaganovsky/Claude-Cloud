"""Verify workspace for the Events tab: overview strip (minimap), verify card, gallery, coverage and autosave.

Attached to an EventsTab (see VerifyKit). Everything that changes events goes through the tab's EventSession, so the tab's own
undo (Ctrl+Z), table and markers stay consistent.

Keys (when the Events tab has focus and no box is being edited)
  Y / Enter   confirm the current item and go to the next      N   reject / dismiss it and go to the next
  . / ,       skip to the next / previous item                 F   next possible missed event (second opinion)
  V           jump to the next part of the trace not yet viewed   G   gallery (contact sheet)
  E           next event to verify (least sure first)
"""
import os

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

from . import events as E
from . import model_review as MR
from . import verify as V

GREEN, RED, BLUE, ORANGE, PURPLE, GREY = (40, 160, 70), (220, 30, 30), (50, 90, 220), (255, 140, 0), (150, 60, 190), (150, 150, 150)
VIEW_MAX_MS = 2500.0          # a window wider than this does not count as "viewed" (events are not visible at that scale)
DWELL_MS = 350                # ... and it must stay on screen this long


def _hide_axes(plot):
    for ax in ("left", "bottom", "top", "right"):
        plot.hideAxis(ax)


class Minimap(pg.PlotWidget):
    """The whole recording (all records side by side): signal envelope, event ticks, viewed / unviewed strip, current window."""

    def __init__(self, kit):
        super().__init__()
        self.kit = kit
        self.setFixedHeight(92)
        self.setMouseEnabled(False, False)
        self.setMenuEnabled(False)
        self.hideButtons()
        _hide_axes(self.getPlotItem())
        self.setBackground((250, 250, 250))
        self.offsets, self.lengths, self.ylim = [], [], (0.0, 1.0)
        self.env = pg.PlotCurveItem(pen=pg.mkPen((90, 90, 90), width=1), connect="pairs")
        self.unseen = pg.PlotCurveItem(pen=pg.mkPen(205, 205, 205, width=5))
        self.seen = pg.PlotCurveItem(pen=pg.mkPen((90, 200, 120), width=5), connect="pairs")
        self.ticks = pg.ScatterPlotItem(symbol="|", size=14, pen=pg.mkPen(None))
        self.cands = pg.ScatterPlotItem(symbol="|", size=10, pen=pg.mkPen(None))
        self.region = pg.LinearRegionItem(values=(0, 1), movable=True, brush=pg.mkBrush(50, 90, 220, 40), pen=pg.mkPen(BLUE, width=2))
        self.region.setZValue(20)
        for it in (self.unseen, self.seen, self.env, self.cands, self.ticks, self.region):
            self.addItem(it)
        self.labels = []
        self.region.sigRegionChanged.connect(self._dragged)
        self.scene().sigMouseClicked.connect(self._clicked)
        self._busy = False

    # ------------------------------------------------------------------------------------------ geometry
    def rebuild(self, session):
        for lb in self.labels:
            self.removeItem(lb)
        self.labels = []
        n, N, ts = session.Y.shape[0], session.Y.shape[1], session.ts
        length = N * ts
        gap = max(length * 0.02, 1.0)
        self.offsets = [r * (length + gap) for r in range(n)]
        self.lengths = [length] * n
        allv = session.Y.ravel()
        lo, hi = np.percentile(allv[::max(1, allv.size // 200000)], [0.2, 99.8])
        if hi - lo <= 0:
            lo, hi = float(allv.min()), float(allv.max()) + 1.0
        pad = 0.12 * (hi - lo)
        self.ylim = (lo - pad, hi + pad)
        nb = 900
        xs, ys = [], []
        edges = np.linspace(0, N, nb + 1).astype(int)
        for r in range(n):
            y = np.clip(session.Y[r], lo, hi)
            for a, b in zip(edges[:-1], edges[1:]):
                if b <= a:
                    continue
                seg = y[a:b]
                x = self.offsets[r] + (a + 0.5 * (b - a)) * ts
                xs += [x, x]
                ys += [float(seg.min()), float(seg.max())]
            lb = pg.TextItem(f"{r + 1}", color=(120, 120, 120), anchor=(0, 0))
            lb.setPos(self.offsets[r], self.ylim[1])
            self.addItem(lb)
            self.labels.append(lb)
        self.env.setData(np.array(xs), np.array(ys))
        ux = []
        for r in range(n):
            ux += [self.offsets[r], self.offsets[r] + length]
        self.unseen.setData(np.array(ux), np.full(len(ux), self.ylim[0] + 0.03 * (hi - lo)), connect="pairs")
        self.setXRange(0, self.offsets[-1] + length, padding=0.005)
        self.setYRange(*self.ylim, padding=0)

    def to_x(self, record, t):
        return self.offsets[record] + t

    def from_x(self, x):
        """(record, t_ms) for an x in minimap coordinates."""
        r = max(i for i, o in enumerate(self.offsets) if o <= x + 1e-9) if self.offsets else 0
        return r, min(max(x - self.offsets[r], 0.0), self.lengths[r])

    # --------------------------------------------------------------------------------------------- drawing
    def refresh(self, session, cov, review, suspects, current=None):
        if not self.offsets:
            return
        ytop = self.ylim[1] - 0.06 * (self.ylim[1] - self.ylim[0])
        spots = []
        for e in session.events:
            d = session.decision(e.record, e.peak_idx)
            col = GREEN if d == "confirmed" else (BLUE if e.manual else RED)
            spots.append(dict(pos=(self.to_x(e.record, e.peak_t), ytop), brush=pg.mkBrush(*col), pen=pg.mkPen(*col, width=2)))
        self.ticks.setData(spots)
        ytick = self.ylim[1] - 0.22 * (self.ylim[1] - self.ylim[0])
        cs = []
        tol = max(1, int(round(4.0 / session.ts)))
        if review is not None:
            for c in review.cands:
                if c.score >= MR.MIN_SHOW and session.find_event(c.record, c.peak_idx, tol) is None \
                        and session.decision(c.record, c.peak_idx) != "dismissed":
                    cs.append(dict(pos=(self.to_x(c.record, c.peak_idx * session.ts), ytick), pen=pg.mkPen(*ORANGE, width=2)))
        for q in V.build_queue("suspects", session, review, suspects) if suspects else []:
            cs.append(dict(pos=(self.to_x(q.record, q.peak_idx * session.ts), ytick), pen=pg.mkPen(*PURPLE, width=2)))
        self.cands.setData(cs)
        sx, sy = [], []
        y0 = self.ylim[0] + 0.03 * (self.ylim[1] - self.ylim[0])
        if cov is not None:
            for r, ivs in enumerate(cov.iv):
                for a, b in ivs:
                    sx += [self.to_x(r, a), self.to_x(r, b)]
                    sy += [y0, y0]
        self.seen.setData(np.array(sx), np.array(sy), connect="pairs") if sx else self.seen.setData([], [])

    def set_window(self, record, x0, x1):
        if not self.offsets:
            return
        self._busy = True
        self.region.setRegion((self.to_x(record, x0), self.to_x(record, x1)))
        self._busy = False

    # ----------------------------------------------------------------------------------------- interaction
    def _dragged(self):
        if self._busy:
            return
        a, b = self.region.getRegion()
        r, t0 = self.from_x(a)
        self.kit.jump_window(r, t0, t0 + (b - a))

    def _clicked(self, ev):
        if not self.offsets or ev.button() != Qt.LeftButton or ev.isAccepted():
            return
        vb = self.getPlotItem().vb
        if not vb.sceneBoundingRect().contains(ev.scenePos()):
            return
        x = vb.mapSceneToView(ev.scenePos()).x()
        a, b = self.region.getRegion()
        r, t = self.from_x(x)
        self.kit.jump_window(r, t - (b - a) / 2.0, t + (b - a) / 2.0)


class ZoomCard(QtWidgets.QWidget):
    pass


class GalleryDialog(QtWidgets.QDialog):
    """Contact sheet of the current queue: click a thumbnail to flip its decision, Enter confirms the rest and turns the page."""

    def __init__(self, kit):
        super().__init__(kit.tab)
        self.kit = kit
        self.setWindowTitle("Gallery")
        self.resize(1250, 800)
        self.setWindowFlag(Qt.Window, True)
        lay = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        self.cb = QtWidgets.QComboBox()
        for k, v in V.MODES.items():
            self.cb.addItem(v, k)
        self.cols = QtWidgets.QSpinBox()
        self.cols.setRange(2, 10)
        self.cols.setValue(5)
        self.rows = QtWidgets.QSpinBox()
        self.rows.setRange(1, 8)
        self.rows.setValue(4)
        self.pre = QtWidgets.QDoubleSpinBox()
        self.pre.setRange(5, 200)
        self.pre.setValue(20)
        self.pre.setSuffix(" ms before")
        self.post = QtWidgets.QDoubleSpinBox()
        self.post.setRange(10, 400)
        self.post.setValue(50)
        self.post.setSuffix(" ms after")
        self.common = QtWidgets.QCheckBox("same Y scale")
        self.common.setChecked(True)
        self.common.setToolTip("ON: every thumbnail uses the same Y range, so amplitudes can be compared.")
        for lbl, w in (("Queue", self.cb), ("columns", self.cols), ("rows", self.rows), ("", self.pre), ("", self.post)):
            if lbl:
                top.addWidget(QtWidgets.QLabel(lbl))
            top.addWidget(w)
        top.addWidget(self.common)
        top.addStretch(1)
        lay.addLayout(top)
        self.hint = QtWidgets.QLabel()
        self.hint.setStyleSheet("padding:4px;background:#eef3fb;border:1px solid #c8c8c8;border-radius:3px;")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        self.glw = pg.GraphicsLayoutWidget()
        lay.addWidget(self.glw, 1)
        bot = QtWidgets.QHBoxLayout()
        self.b_prev = QtWidgets.QPushButton("◀ Previous page")
        self.b_next = QtWidgets.QPushButton("Next page ▶")
        self.b_rest = QtWidgets.QPushButton()
        self.b_undec = QtWidgets.QPushButton("Next page with undecided")
        self.lbl = QtWidgets.QLabel()
        for w in (self.b_prev, self.b_next, self.b_undec, self.b_rest):
            bot.addWidget(w)
        bot.addWidget(self.lbl, 1)
        lay.addLayout(bot)
        self.items, self.page, self.plots, self.cells = [], 0, [], []
        self.cb.currentIndexChanged.connect(lambda *_: self.reload())
        for w in (self.cols, self.rows, self.pre, self.post, self.common):
            (w.toggled if isinstance(w, QtWidgets.QCheckBox) else w.valueChanged).connect(lambda *_: self.draw())
        self.b_prev.clicked.connect(lambda: self.turn(-1))
        self.b_next.clicked.connect(lambda: self.turn(+1))
        self.b_undec.clicked.connect(self.next_undecided)
        self.b_rest.clicked.connect(self.decide_rest)
        self.glw.scene().sigMouseClicked.connect(self._clicked)
        for key, fn in (("Return", self.decide_rest), ("Enter", self.decide_rest), ("Right", lambda: self.turn(+1)),
                        ("Left", lambda: self.turn(-1)), ("PageDown", lambda: self.turn(+1)), ("PageUp", lambda: self.turn(-1))):
            QtGui.QShortcut(QtGui.QKeySequence(key), self, activated=fn)

    # -------------------------------------------------------------------------------------------------- data
    def mode(self):
        return self.cb.currentData()

    def per_page(self):
        return self.cols.value() * self.rows.value()

    def open_with(self, mode):
        self.cb.blockSignals(True)
        self.cb.setCurrentIndex(max(0, self.cb.findData(mode)))
        self.cb.blockSignals(False)
        self.reload()
        self.show()
        self.raise_()
        self.activateWindow()

    def reload(self):
        k = self.kit
        if self.mode() == "suspects":
            k.ensure_suspects()
        self.items = V.build_queue(self.mode(), k.session, k.review, k.suspects, include_done=True)
        self.page = 0
        self.next_undecided(first=True)

    def state(self, it):
        """'event' | 'confirmed' | 'rejected' | 'dismissed' | 'open' for a queue item."""
        s = self.kit.session
        tol = max(1, int(round(4.0 / s.ts)))
        ev = s.find_event(it.record, it.peak_idx, tol)
        d = s.decision(it.record, it.peak_idx)
        if ev is not None:
            return "confirmed" if (d == "confirmed" or ev.manual) else "event"
        return d if d in ("rejected", "dismissed") else "open"

    def undecided(self, it):
        return self.state(it) in ("event", "open")

    def next_undecided(self, first=False):
        n = max(1, self.per_page())
        pages = max(1, -(-len(self.items) // n))
        start = 0 if first else self.page + 1
        for k in range(pages):
            pg_ = (start + k) % pages
            if any(self.undecided(it) for it in self.items[pg_ * n:(pg_ + 1) * n]):
                self.page = pg_
                break
        self.draw()

    def turn(self, d):
        n = max(1, self.per_page())
        pages = max(1, -(-len(self.items) // n))
        self.page = int(np.clip(self.page + d, 0, pages - 1))
        self.draw()

    # --------------------------------------------------------------------------------------------- drawing
    def draw(self):
        k = self.kit
        s = k.session
        self.glw.clear()
        self.plots, self.cells = [], []
        n = self.per_page()
        mode = self.mode()
        events_mode = mode in ("lowest_score", "time")
        self.b_rest.setText("Confirm the rest and next page (Enter)" if events_mode else "Dismiss the rest and next page (Enter)")
        self.hint.setText(("Click a thumbnail to <b>reject</b> that event (click again to bring it back). Everything you leave alone is "
                           "confirmed when you press Enter." if events_mode else
                           "Click a thumbnail to <b>add</b> it as an event (click again to remove it). Everything you leave alone is "
                           "dismissed as 'not an event' when you press Enter."))
        chunk = self.items[self.page * n:(self.page + 1) * n]
        pre, post = self.pre.value(), self.post.value()
        snips = [V.snippet(s, it.record, it.peak_idx, pre, post) for it in chunk]
        ghost = V.ghost_template(s, pre_ms=pre, post_ms=post)
        lo = min((float(y.min()) for _, y, _ in snips), default=0.0)
        hi = max((float(y.max()) for _, y, _ in snips), default=1.0)
        cols = self.cols.value()
        for i, (it, (tr, y, i0)) in enumerate(zip(chunk, snips)):
            p = self.glw.addPlot(row=i // cols, col=i % cols)
            _hide_axes(p)
            p.setMouseEnabled(False, False)
            p.setMenuEnabled(False)
            st = self.state(it)
            col = {"event": (255, 255, 255), "confirmed": (222, 245, 228), "rejected": (253, 220, 220), "dismissed": (230, 230, 230),
                   "open": (255, 255, 255)}[st]
            p.vb.setBackgroundColor(col)
            p.vb.setBorder(pg.mkPen(RED if st == "rejected" else GREEN if st == "confirmed" else (190, 190, 190), width=2))
            p.plot(tr, y, pen=pg.mkPen((30, 30, 30), width=1))
            if ghost is not None:
                bl = V.local_baseline(s, it.record, it.peak_idx)
                gt, gy = ghost
                p.plot(gt, gy * float(y[i0] - bl) + bl, pen=pg.mkPen(46, 158, 87, 150, width=2))
            p.plot([0], [y[i0]], pen=None, symbol="o", symbolBrush=pg.mkBrush(*(ORANGE if it.kind == "candidate" else PURPLE if it.kind == "suspect" else RED)),
                   symbolSize=7)
            if self.common.isChecked():
                p.setYRange(lo - 0.05 * (hi - lo), hi + 0.05 * (hi - lo), padding=0)
            p.setXRange(tr[0], tr[-1], padding=0)
            sc = f"{it.score:.2f}" if np.isfinite(it.score) else (f"SNR {it.snr:.1f}" if np.isfinite(it.snr) else "")
            p.setTitle(f"{self.page * n + i + 1}. rec {it.record + 1}  {it.peak_idx * s.ts:.0f} ms  {sc}", size="8pt")
            self.plots.append(p)
            self.cells.append(it)
        pages = max(1, -(-len(self.items) // n))
        dec = sum(not self.undecided(it) for it in self.items)
        self.lbl.setText(f"  page {self.page + 1}/{pages} · {len(self.items)} items · {dec} decided")

    # ----------------------------------------------------------------------------------------- interaction
    def _clicked(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        for p, it in zip(self.plots, self.cells):
            if p.vb.sceneBoundingRect().contains(ev.scenePos()):
                self.kit.flip(it, self.mode())
                self.draw()
                return

    def decide_rest(self):
        n = self.per_page()
        chunk = self.items[self.page * n:(self.page + 1) * n]
        self.kit.decide_rest(chunk, self.mode())
        pages = max(1, -(-len(self.items) // n))
        if self.page + 1 < pages:
            self.page += 1
        self.draw()


class VerifyKit(QtCore.QObject):
    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.cov = None
        self.suspects = None
        self.mode = "time"
        self.queue = []
        self.pos = -1
        self.hist = []                      # keys decided here, for "undo jumps back"
        self.cand_path = None
        self._announced_done = set()
        self.gallery = None
        self.dwell = QtCore.QTimer(self)
        self.dwell.setSingleShot(True)
        self.dwell.timeout.connect(self._count_view)
        self.save_timer = QtCore.QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.autosave)
        self._install()
        self._shortcuts()
        tab.vb.sigRangeChanged.connect(self._view_changed)

    # ---------------------------------------------------------------------------------------------- properties
    @property
    def session(self):
        return self.tab.session

    @property
    def review(self):
        return self.tab.review

    # --------------------------------------------------------------------------------------------------- UI
    def _install(self):
        tab = self.tab
        host = tab.pd.parentWidget().layout()
        i = host.indexOf(tab.pd)
        host.removeWidget(tab.pd)
        self.split = QtWidgets.QSplitter(Qt.Horizontal)
        self.split.addWidget(tab.pd)
        self.card = self._build_card()
        self.split.addWidget(self.card)
        self.split.setStretchFactor(0, 4)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([900, 330])
        host.insertWidget(i, self.split, 4)
        self.mm = Minimap(self)
        self.lbl_cov = QtWidgets.QLabel()
        self.lbl_cov.setTextFormat(Qt.RichText)
        host.insertWidget(i + 1, self.mm)
        host.insertWidget(i + 2, self.lbl_cov)
        # markers drawn on the main plot
        self.sc_sus = pg.ScatterPlotItem(size=12, symbol="d", brush=pg.mkBrush(None), pen=pg.mkPen(*PURPLE, width=2))
        self.sc_cur = pg.ScatterPlotItem(size=28, brush=pg.mkBrush(None), pen=pg.mkPen(*ORANGE, width=3, style=Qt.DashLine))
        for it in (self.sc_sus, self.sc_cur):
            it.setZValue(9)
            tab.pd.addItem(it)

    def _build_card(self):
        w = QtWidgets.QWidget()
        w.setMinimumWidth(260)
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(4, 0, 0, 0)
        head = QtWidgets.QLabel("<b>Verify</b>")
        v.addWidget(head)
        self.cb_mode = QtWidgets.QComboBox()
        for k, t in V.MODES.items():
            self.cb_mode.addItem(t, k)
        self.cb_mode.setCurrentIndex(self.cb_mode.findData("time"))
        self.cb_mode.currentIndexChanged.connect(self._mode_changed)
        v.addWidget(self.cb_mode)
        self.lbl_title = QtWidgets.QLabel()
        self.lbl_title.setWordWrap(True)
        v.addWidget(self.lbl_title)
        self.zoom = pg.PlotWidget()
        self.zoom.showGrid(x=True, y=True, alpha=0.2)
        self.zoom.setMinimumHeight(190)
        self.zoom.setMenuEnabled(False)
        for ax in ("left", "bottom"):
            self.zoom.getAxis(ax).enableAutoSIPrefix(False)
        v.addWidget(self.zoom, 1)
        self.lbl_info = QtWidgets.QLabel()
        self.lbl_info.setWordWrap(True)
        self.lbl_info.setTextFormat(Qt.RichText)
        v.addWidget(self.lbl_info)
        row = QtWidgets.QHBoxLayout()
        self.b_yes = QtWidgets.QPushButton("✓ Confirm (Y)")
        self.b_no = QtWidgets.QPushButton("✗ Reject (N)")
        self.b_yes.setStyleSheet("QPushButton{background:#2e9e57;color:white;font-weight:bold;padding:6px;}")
        self.b_no.setStyleSheet("QPushButton{background:#c0392b;color:white;font-weight:bold;padding:6px;}")
        self.b_yes.clicked.connect(self.confirm_current)
        self.b_no.clicked.connect(self.reject_current)
        row.addWidget(self.b_yes)
        row.addWidget(self.b_no)
        v.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        b = QtWidgets.QPushButton("◀ ,")
        b.setToolTip("Previous item (,)")
        b.clicked.connect(lambda: self.step(-1))
        row.addWidget(b)
        b = QtWidgets.QPushButton("Skip .  ▶")
        b.setToolTip("Skip to the next item (.)")
        b.clicked.connect(lambda: self.step(+1))
        row.addWidget(b)
        v.addLayout(row)
        self.lbl_stats = QtWidgets.QLabel()
        self.lbl_stats.setWordWrap(True)
        self.lbl_stats.setTextFormat(Qt.RichText)
        self.lbl_stats.setStyleSheet("padding:4px;background:#f4f4f4;border:1px solid #d0d0d0;border-radius:3px;")
        v.addWidget(self.lbl_stats)
        self.lbl_saved = QtWidgets.QLabel("progress is saved automatically")
        self.lbl_saved.setStyleSheet("color:#777;font-size:11px;")
        v.addWidget(self.lbl_saved)
        row = QtWidgets.QHBoxLayout()
        for text, tip, fn in (("Next unviewed (V)", "Jump to the next part of the trace you have not looked at yet", self.next_unviewed),
                              ("Possible miss (F)", "Jump to the next place the second-opinion detector flags as a possible missed event",
                               lambda: self.next_suspect(+1))):
            b = QtWidgets.QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        v.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        b = QtWidgets.QPushButton("Gallery… (G)")
        b.setToolTip("Contact sheet: check many events at a glance")
        b.clicked.connect(lambda: self.open_gallery())
        row.addWidget(b)
        self.b_resume = QtWidgets.QPushButton("Resume saved review")
        self.b_resume.setToolTip("Restore decisions, hand edits and coverage from the autosaved progress file")
        self.b_resume.clicked.connect(self.resume)
        row.addWidget(self.b_resume)
        v.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("counts as viewed if window ≤"))
        self.sp_view = QtWidgets.QDoubleSpinBox()
        self.sp_view.setRange(0.2, 60)
        self.sp_view.setValue(VIEW_MAX_MS / 1000.0)
        self.sp_view.setSuffix(" s")
        self.sp_view.setToolTip("Only windows at least this narrow count towards 'trace viewed' (after a short dwell time).")
        row.addWidget(self.sp_view)
        v.addLayout(row)
        return w

    def _shortcuts(self):
        def sc(key, fn):
            q = QtGui.QShortcut(QtGui.QKeySequence(key), self.tab)
            q.setContext(Qt.WidgetWithChildrenShortcut)
            q.activated.connect(lambda: self.tab._typing() or fn())
        sc("Y", self.confirm_current)
        sc("Return", self.confirm_current)
        sc("Enter", self.confirm_current)
        sc("N", self.reject_current)
        sc(".", lambda: self.step(+1))
        sc(",", lambda: self.step(-1))
        sc("F", lambda: self.next_suspect(+1))
        sc("Shift+F", lambda: self.next_suspect(-1))
        sc("V", self.next_unviewed)
        sc("G", self.open_gallery)
        sc("E", self.next_to_verify)

    # ------------------------------------------------------------------------------------ hooks from the tab
    def on_session_reset(self):
        s = self.session
        self.cov = V.Coverage([s.Y.shape[1] * s.ts] * s.Y.shape[0]) if s is not None else None
        self.suspects, self.queue, self.pos, self.hist = None, [], -1, []
        self.cand_path = None
        self.lbl_saved.setText("progress is saved automatically")
        self._announced_done.clear()
        if s is not None:
            self.mm.rebuild(s)
        self.mode = "time"
        self.cb_mode.blockSignals(True)
        self.cb_mode.setCurrentIndex(self.cb_mode.findData("time"))
        self.cb_mode.blockSignals(False)
        self._after_change()

    def on_candidates_loaded(self, path):
        self.cand_path = path
        self.suspects = None
        self._announced_done.clear()
        self.hist = []
        self.cb_mode.blockSignals(True)
        self.mode = "lowest_score"
        self.cb_mode.setCurrentIndex(self.cb_mode.findData(self.mode))
        self.cb_mode.blockSignals(False)
        d = self.saved()
        if d is not None and self.fits(d):
            ans = QtWidgets.QMessageBox.question(self.tab, "Resume review?", "A saved review exists for these candidates:\n\n"
                                                 + V.progress_summary(d) + "\n\nResume it?")
            if ans == QtWidgets.QMessageBox.Yes:
                self.resume(d)
                return
        self._rebuild_queue()
        self.pos = 0 if self.queue else -1
        self._after_change()
        self.focus(self.current(), recenter=True)

    def on_events_changed(self):
        self._rebuild_queue()
        self._after_change()
        self.save_timer.start(1500)

    def on_markers(self):
        """Called at the end of EventsTab._markers: draw the suspects / current item of the visible record."""
        s = self.session
        if s is None:
            return
        rec, t = self.tab.rec_i, self.tab.main.rec.t
        y = s.Y[rec]
        q = [q for q in V.build_queue("suspects", s, self.review, self.suspects) if q.record == rec] if self.suspects else []
        self.sc_sus.setData([t[x.peak_idx] for x in q], [y[x.peak_idx] for x in q])
        cur = self.current()
        self.sc_cur.setData([t[cur.peak_idx]] if cur and cur.record == rec else [], [y[cur.peak_idx]] if cur and cur.record == rec else [])

    def on_undo(self):
        """The tab's undo was used: jump back to the item that was just un-decided."""
        if self.hist:
            key = self.hist.pop()
            self._rebuild_queue(prefer=key)
            self.focus(self.current(), recenter=True)

    def on_click(self, x, y, vb):
        """Clicks on suspect markers: first click focuses (card shows the zoom), second click adds it."""
        s = self.session
        if s is None or not self.suspects:
            return False
        rec, t = self.tab.rec_i, self.tab.main.rec.t
        dx, dy = self.tab._px(vb)
        best, bd = None, 12
        for q in V.build_queue("suspects", s, self.review, self.suspects):
            if q.record != rec:
                continue
            d = np.hypot((t[q.peak_idx] - x) / dx, (s.Y[rec][q.peak_idx] - y) / dy)
            if d < bd:
                best, bd = q, d
        if best is None:
            return False
        was = self.current().key if (self.mode == "suspects" and self.current() is not None) else None   # focused BEFORE this click?
        if self.mode != "suspects":
            self._set_mode("suspects")
        if was == best.key:
            self.confirm_current()                                   # second click on the focused suspect adds it
        else:
            self.pos = next((i for i, q in enumerate(self.queue) if q.key == best.key), self.pos)
            self.focus(self.current(), recenter=False)
            self._after_change()
        return True

    # ------------------------------------------------------------------------------------------------ queue
    def _set_mode(self, mode):
        if mode == "suspects":
            self.ensure_suspects()
        self.mode = mode
        self.cb_mode.blockSignals(True)
        self.cb_mode.setCurrentIndex(self.cb_mode.findData(mode))
        self.cb_mode.blockSignals(False)
        self.pos = -1
        self._rebuild_queue()

    def _mode_changed(self, *_):
        self._set_mode(self.cb_mode.currentData())
        self._after_change()
        if self.queue:
            self.pos = 0
            self.focus(self.current(), recenter=True)

    def ensure_suspects(self, force=False):
        s = self.session
        if s is None or (self.suspects is not None and not force):
            return
        tol = max(1, int(round(4.0 / s.ts)))
        cands = [] if self.review is None else [c for c in self.review.cands if c.score >= MR.MIN_SHOW]

        def skip(r, k):
            return (s.find_event(r, k, tol) is not None or s.decision(r, k) is not None
                    or any(c.record == r and abs(c.peak_idx - k) <= tol for c in cands))
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.suspects = V.find_suspects(s, skip)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def _rebuild_queue(self, prefer=None):
        s = self.session
        if s is None:
            self.queue = []
            return
        old = self.current()
        key = prefer or (old.key if old else None)
        old_pos = self.pos
        self.queue = V.build_queue(self.mode, s, self.review, self.suspects)
        if key is not None and any(q.key == key for q in self.queue):
            self.pos = next(i for i, q in enumerate(self.queue) if q.key == key)
        elif self.queue:
            self.pos = min(max(old_pos, 0), len(self.queue) - 1)      # always a current item when there is work to do
        else:
            self.pos = -1

    def current(self):
        return self.queue[self.pos] if 0 <= self.pos < len(self.queue) else None

    def step(self, d):
        if not self.queue:
            self.tab.say("Nothing left in this queue.", "add")
            return
        self.pos = (self.pos + d) % len(self.queue) if self.pos >= 0 else (0 if d > 0 else len(self.queue) - 1)
        self.focus(self.current(), recenter=True)

    def next_to_verify(self):
        self._set_mode("lowest_score" if self.review is not None else "time")
        self._after_change()
        self.pos = 0 if self.queue else -1
        self.focus(self.current(), recenter=True)

    def next_suspect(self, d):
        if self.session is None:
            return
        if self.mode != "suspects":
            self._set_mode("suspects")
            self._after_change()
            self.pos = 0 if d > 0 else len(self.queue) - 1
            if not self.queue:
                self.tab.say("No possible missed events found by the second-opinion detector. Keep scanning the trace (V).", "add")
                return
            self.focus(self.current(), recenter=True)
        else:
            self.step(d)

    # ----------------------------------------------------------------------------------------------- focus
    def focus(self, item, recenter=True):
        tab, s = self.tab, self.session
        if item is None:
            self._draw_card(None)
            tab._markers()
            return
        if item.record != tab.rec_i:
            tab.spin.setValue(item.record)
        ev = s.find_event(item.record, item.peak_idx, max(1, int(round(4.0 / s.ts)))) if item.kind == "event" else None
        if ev is not None:
            tab.select(ev)
        else:
            tab.sel = None
        (x0, x1), _ = tab.vb.viewRange()
        t = tab.main.rec.t[item.peak_idx]
        if recenter and not (x0 + 0.1 * (x1 - x0) < t < x1 - 0.1 * (x1 - x0)):
            tab._set_view(max(0.0, t - (x1 - x0) / 2), x1 - x0)
            if not tab.chk_locky.isChecked():
                tab.fit_y()
        tab._markers()
        self._draw_card(item)

    # -------------------------------------------------------------------------------------------- decisions
    def _decide(self, fn_event, fn_cand, fn_sus, say):
        it = self.current()
        if it is None:
            self.tab.say("Nothing to decide: pick a queue (or load model candidates), or press <b>V</b> to start scanning the trace.", "warn")
            return
        s = self.session
        tol = max(1, int(round(4.0 / s.ts)))
        key = it.key
        if it.kind == "event":
            ev = s.find_event(it.record, it.peak_idx, tol)
            if ev is not None:
                fn_event(ev)
        elif it.kind == "candidate":
            fn_cand(it)
        else:
            fn_sus(it)
        self.hist.append(key)
        self.tab.sel = None
        self.tab._refresh_all()                       # -> on_events_changed -> queue rebuilt without the decided item
        nxt = self.current()
        if nxt is None:
            self.tab.say(f"{say} Nothing left in this queue ✓ - switch queue, or press <b>V</b> to continue scanning for missed events.", "add")
            self._draw_card(None)
        else:
            self.focus(nxt, recenter=True)

    def _accept(self, it):
        s = self.session
        ev, why = s.add(it.record, it.peak_idx, fit=True, confirm=True, thresholds=False)
        if ev is None and why == "no_baseline":
            self.tab.begin_pending(it.record, it.peak_idx)        # same smooth manual-baseline prompt as hand-added events
            self.tab.say("No baseline could be found before this event. <b>Click the baseline level</b> (left of the orange peak), "
                         "Esc cancels.", "pending")
            self._pending_accept = True
            return
        if ev is None:
            ev, why = s.add(it.record, it.peak_idx, fit=False, confirm=True, thresholds=False)
        if ev is None:
            self.tab.say(f"Could not add this event: {E.REASONS.get(why, why)}.", "warn")

    def confirm_current(self):
        self._decide(self.session.confirm, self._accept, self._accept, "Confirmed.")

    def reject_current(self):
        s = self.session
        self._decide(s.reject, lambda it: s.dismiss(it.record, it.peak_idx), lambda it: s.dismiss(it.record, it.peak_idx), "Rejected.")

    # gallery actions -------------------------------------------------------------------------------------
    def flip(self, it, mode):
        s = self.session
        tol = max(1, int(round(4.0 / s.ts)))
        ev = s.find_event(it.record, it.peak_idx, tol)
        if it.kind == "event":
            if ev is not None:
                s.reject(ev)
            else:
                s.unreject(it.record, it.peak_idx)
        else:                                          # candidate / suspect: click = add, click again = remove
            if ev is None:
                self._accept(it)
            else:
                s.reject(ev)
        self.tab._refresh_all()

    def decide_rest(self, items, mode):
        s = self.session
        tol = max(1, int(round(4.0 / s.ts)))
        n = 0
        for it in items:
            ev = s.find_event(it.record, it.peak_idx, tol)
            if it.kind == "event":
                if ev is not None and not s.is_verified(ev):
                    s.confirm(ev)
                    n += 1
            elif ev is None and s.decision(it.record, it.peak_idx) is None:
                s.dismiss(it.record, it.peak_idx)
                n += 1
        self.tab._refresh_all()
        self.tab.say(f"Gallery page: {n} item(s) decided.", "add")

    def open_gallery(self):
        if self.session is None:
            return
        if self.gallery is None:
            self.gallery = GalleryDialog(self)
        self.gallery.open_with(self.mode if self.mode in V.MODES else "time")

    # ------------------------------------------------------------------------------------------ coverage
    def jump_window(self, record, x0, x1):
        """Move the main view (used by the minimap)."""
        tab = self.tab
        if self.session is None:
            return
        if record != tab.rec_i:
            tab.spin.blockSignals(True)
            tab.spin.setValue(record)
            tab.spin.blockSignals(False)
            tab._load_record(False)
            tab._markers()
        L = self.session.Y.shape[1] * self.session.ts
        w = x1 - x0
        x0 = min(max(0.0, x0), max(0.0, L - w))
        tab._set_view(x0, w)
        if not tab.chk_locky.isChecked():
            tab.fit_y()

    def _view_changed(self, *_):
        tab = self.tab
        if self.session is None:
            return
        (x0, x1), _ = tab.vb.viewRange()
        self.mm.set_window(tab.rec_i, x0, x1)
        self.dwell.start(DWELL_MS)

    def _count_view(self):
        if self.cov is None or self.session is None or not self.tab.isVisible():
            return                                       # only count what the reviewer can actually see (tab on screen)
        (x0, x1), _ = self.tab.vb.viewRange()
        if (x1 - x0) > self.sp_view.value() * 1000.0:
            return
        self.cov.add(self.tab.rec_i, x0, x1)
        self._after_change(light=True)
        self.save_timer.start(1500)

    def next_unviewed(self):
        if self.cov is None:
            return
        (x0, x1), _ = self.tab.vb.viewRange()
        g = self.cov.next_gap(self.tab.rec_i, x1)
        if g is None:
            self.tab.say("The whole trace has been viewed ✓. Use <b>F</b> to check the second-opinion candidates, then save the review.", "add")
            return
        r, a, b = g
        w = x1 - x0
        if r != self.tab.rec_i:
            self.tab.spin.setValue(r)
        self.tab._set_view(max(0.0, a), w)
        if not self.tab.chk_locky.isChecked():
            self.tab.fit_y()
        self.tab.say(f"Unviewed part: record {r + 1}, {a / 1000:.1f}–{min(b, a + w) / 1000:.1f} s. Look for events the model missed "
                     "(<b>A</b> adds one, <b>V</b> continues).")

    # ------------------------------------------------------------------------------------------------ view
    def _after_change(self, light=False):
        s = self.session
        if s is None:
            return
        self.mm.refresh(s, self.cov, self.review, self.suspects)
        st = V.stats(s, self.review, self.suspects, self.cov)
        rec = self.tab.rec_i
        cov_r = self.cov.fraction(rec) if self.cov else float("nan")
        self.lbl_cov.setText(
            f"<span style='color:#555'>Trace viewed <b>{st['coverage'] * 100:.0f}%</b> (this record {cov_r * 100:.0f}%) · green strip = viewed, grey = "
            f"not yet · ticks: <span style='color:#dc1e1e'>■ to verify</span> <span style='color:#28a046'>■ confirmed</span> "
            f"<span style='color:#325adc'>■ added</span> <span style='color:#ff8c00'>■ borderline</span> <span style='color:#963cbe'>■ possible miss</span>"
            f" · click or drag to jump</span>")
        todo = st["unverified"]
        ready = V.record_ready_to_mark(s, rec, self.cov, self.review, self.suspects) if self.review is not None else False
        parts = [f"Events <b>{st['events']}</b>: ✓ <b>{st['events'] - todo}</b> verified · <b>{todo}</b> to verify",
                 f"✗ {st['rejected']} rejected · + {st['added']} added by hand"]
        if self.review is not None:
            parts.append(f"borderline left <b>{st['borderline_open']}</b>")
        parts.append("possible misses left <b>" + (str(st['suspects_open']) if self.suspects is not None else "?") + "</b> (F)")
        if ready and self.review is not None and rec not in self.review.reviewed:
            parts.append(f"<span style='color:#1e7a3c'>Record {rec + 1} is fully reviewed - press <b>M</b> to mark it.</span>")
        self.lbl_stats.setText("<br>".join(parts))
        self.b_resume.setEnabled(self.saved() is not None)
        if not light:
            self._draw_card(self.current())
        # finishing messages
        if self.cov is not None and st["coverage"] >= 0.995 and "cov" not in self._announced_done:
            self._announced_done.add("cov")
            self.tab.say("Whole trace viewed ✓. Check the possible misses (<b>F</b>) and save the review when you are happy.", "add")

    def _draw_card(self, item):
        s = self.session
        self.zoom.clear()
        titles = {"lowest_score": "least sure first", "time": "time order", "borderline": "closest to the threshold first",
                  "suspects": "strongest first"}
        qn = len(self.queue)
        if item is None:
            self.lbl_title.setText(f"<b>{V.MODES[self.mode]}</b><br>{'Nothing left in this queue ✓' if not qn else 'Press . to start'}")
            self.lbl_info.setText("")
            self._buttons(None)
            return
        self.lbl_title.setText(f"<b>{V.MODES[self.mode]}</b> ({titles[self.mode]})<br>item {self.pos + 1} of {qn} still open")
        tr, y, i0 = V.snippet(s, item.record, item.peak_idx)
        tol = max(1, int(round(4.0 / s.ts)))
        ev = s.find_event(item.record, item.peak_idx, tol)
        base = ev.bl_im if ev is not None else V.local_baseline(s, item.record, item.peak_idx)
        amp = (ev.amplitude if ev is not None else float(y[i0] - base))
        g = V.ghost_template(s)
        self.zoom.plot(tr, y, pen=pg.mkPen((30, 30, 30), width=1.5))
        if g is not None and amp:
            self.zoom.plot(g[0], g[1] * amp + base, pen=pg.mkPen(46, 158, 87, 170, width=3))
        self.zoom.addItem(pg.InfiniteLine(base, angle=0, pen=pg.mkPen(BLUE, style=Qt.DotLine)))
        col = ORANGE if item.kind == "candidate" else PURPLE if item.kind == "suspect" else RED
        self.zoom.plot([0], [y[i0]], pen=None, symbol="o", symbolSize=9, symbolBrush=pg.mkBrush(*col))
        if ev is not None and ev.fitted and ev.end_idx >= 0:
            self.zoom.plot([(ev.end_idx - item.peak_idx) * s.ts], [s.Y[item.record][ev.end_idx]], pen=None, symbol="o", symbolSize=6,
                           symbolBrush=pg.mkBrush(150, 0, 200))
        noise = V.robust_noise(s.Y[item.record], s.ts)
        prev = [e for e in s.events if e.record == item.record and e.peak_idx < item.peak_idx]
        gap = (item.peak_idx - prev[-1].peak_idx) * s.ts if prev else float("nan")
        bits = [f"rec {item.record + 1} · {item.peak_idx * s.ts:.1f} ms"]
        if np.isfinite(item.score):
            bits.append(f"model score <b>{item.score:.2f}</b>")
        bits.append(f"amplitude <b>{amp:.1f}</b>")
        bits.append(f"SNR <b>{abs(amp) / noise:.1f}</b>")
        if np.isfinite(gap):
            bits.append(f"{gap:.0f} ms after the previous event")
        bits.append("green line = typical event scaled to this amplitude" if g is not None else "")
        self.lbl_info.setText("<br>".join(b for b in bits if b))
        self._buttons(item)

    def _buttons(self, item):
        if item is None:
            self.b_yes.setEnabled(False)
            self.b_no.setEnabled(False)
            return
        self.b_yes.setEnabled(True)
        self.b_no.setEnabled(True)
        if item.kind == "event":
            self.b_yes.setText("✓ Confirm (Y)")
            self.b_no.setText("✗ Reject (N)")
        elif item.kind == "candidate":
            self.b_yes.setText("✓ Accept (Y)")
            self.b_no.setText("✗ Not an event (N)")
        else:
            self.b_yes.setText("✓ Add as event (Y)")
            self.b_no.setText("✗ Not an event (N)")

    # ------------------------------------------------------------------------------------------ progress
    def path(self):
        return V.progress_path(self.tab.main.rec.path, self.cand_path)

    def saved(self):
        if self.session is None or self.tab.main.rec is None:
            return None
        return V.load_progress(self.path())

    def fits(self, d):
        s = self.session
        return d.get("n_records") == s.Y.shape[0] and d.get("n_samples") == s.Y.shape[1]

    def autosave(self):
        if self.session is None or self.tab.main.rec is None or (self.session.log.is_empty() and not self.session.log.confirmed
                                                                 and not self.session.log.dismissed and (self.cov is None or self.cov.viewed_ms() == 0)):
            return
        try:
            p = V.save_progress(self.path(), self.session, self.review, self.cov, self.tab.main.rec.path,
                                os.path.basename(self.cand_path) if self.cand_path else "", self.mode, self.pos)
            self.lbl_saved.setText(f"autosaved {QtCore.QTime.currentTime().toString('HH:mm:ss')} → {os.path.basename(p)}")
        except OSError as e:
            self.lbl_saved.setText(f"could not autosave: {e}")

    def resume(self, d=None):
        d = d or self.saved()
        if d is None or not self.fits(d):
            self.tab.say("No matching saved review to resume.", "warn")
            return
        V.apply_progress(d, self.session, self.review, self.cov)
        if d.get("mode") in V.MODES and d["mode"] != "suspects":
            self.mode = d["mode"]
            self.cb_mode.blockSignals(True)
            self.cb_mode.setCurrentIndex(self.cb_mode.findData(self.mode))
            self.cb_mode.blockSignals(False)
        self.tab.sel = None
        self.tab._refresh_all()
        self.tab.say("Resumed the saved review: " + V.progress_summary(d), "add")
