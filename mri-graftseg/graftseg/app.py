"""GraftSeg desktop app (PySide6 + pyqtgraph).

Pages: Home · Segment · Review · Models · Train · Settings.
Long jobs (prediction, dataset building, training) run as ``python -m graftseg ...`` child processes, so the window
never freezes and every job can also be repeated from a terminal with the command shown at the top of its log.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import signal
import sys
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from . import config as C
from . import models as M

Qt = QtCore.Qt
pg.setConfigOptions(imageAxisOrder="row-major", antialias=True)

from .ui_kit import BAD, GOOD, INFO, WARN, FlowLayout, ResponsiveGrid, T, Toast, apply_theme, fit_to_screen, icon


def muted() -> str:
    return T["muted"]


def card(title: str = "") -> tuple[QtWidgets.QFrame, QtWidgets.QVBoxLayout]:
    f = QtWidgets.QFrame(); f.setObjectName("card")
    lay = QtWidgets.QVBoxLayout(f); lay.setContentsMargins(18, 16, 18, 16); lay.setSpacing(10)
    if title:
        t = QtWidgets.QLabel(title.upper()); t.setObjectName("cardtitle"); lay.addWidget(t)
    sh = QtWidgets.QGraphicsDropShadowEffect(blurRadius=18, xOffset=0, yOffset=2)
    sh.setColor(QtGui.QColor(0, 0, 0, T.get("shadow", 18))); f.setGraphicsEffect(sh)
    return f, lay


def button(text: str, role: str = "", tip: str = "") -> QtWidgets.QPushButton:
    b = QtWidgets.QPushButton(text)
    if role:
        b.setProperty("role", role)
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def header(title: str, sub: str = "") -> QtWidgets.QWidget:
    w = QtWidgets.QWidget(); lay = QtWidgets.QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 6); lay.setSpacing(2)
    t = QtWidgets.QLabel(title); t.setObjectName("h1"); lay.addWidget(t)
    if sub:
        s = QtWidgets.QLabel(sub); s.setObjectName("sub"); s.setWordWrap(True); lay.addWidget(s)
    return w


def chip(text: str, color: str) -> str:
    return f'<span style="color:{color};font-weight:600;">● {text}</span>'


def settings() -> QtCore.QSettings:
    return QtCore.QSettings("graftseg", "graftseg")


def remember_dir(key: str, path: str) -> None:
    settings().setValue(f"dir/{key}", str(Path(path) if Path(path).is_dir() else Path(path).parent))


def last_dir(key: str) -> str:
    return str(settings().value(f"dir/{key}", str(Path.home())))


class PathList(QtWidgets.QWidget):
    """A list of folders/files with Add / Remove buttons and drag-and-drop."""
    changed = QtCore.Signal()

    def __init__(self, key: str, files: bool = False, placeholder: str = "Drop folders here or click Add folder"):
        super().__init__()
        self.key = key
        lay = QtWidgets.QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self.list = QtWidgets.QListWidget(); self.list.setFixedHeight(96); self.list.setAcceptDrops(True)
        self.list.setToolTip(placeholder)
        self.hint = QtWidgets.QLabel(placeholder, self.list.viewport()); self.hint.setObjectName("sub")
        self.hint.move(12, 10); self.hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.list.model().rowsInserted.connect(self._hint); self.list.model().rowsRemoved.connect(self._hint)
        self.list.setDragDropMode(QtWidgets.QAbstractItemView.DropOnly)
        self.setAcceptDrops(True)
        lay.addWidget(self.list)
        row = QtWidgets.QHBoxLayout(); lay.addLayout(row)
        add = button("Add folder…"); add.clicked.connect(self.add_folder); row.addWidget(add)
        if files:
            addf = button("Add files…"); addf.clicked.connect(self.add_files); row.addWidget(addf)
        rm = button("Remove"); rm.clicked.connect(self.remove); row.addWidget(rm)
        row.addStretch()

    def _hint(self, *a):
        self.hint.setVisible(self.list.count() == 0)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            self.add(u.toLocalFile())

    def add(self, p: str):
        if p and p not in self.paths():
            self.list.addItem(p); remember_dir(self.key, p); self.changed.emit()

    def add_folder(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose folder", last_dir(self.key))
        self.add(d)

    def add_files(self):
        fs, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Choose scans", last_dir(self.key),
                                                       "Scans (*.dcm *.nrrd *.seg.nrrd);;All files (*)")
        for f in fs:
            self.add(f)

    def remove(self):
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))
        self.changed.emit()

    def paths(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]

    def set_paths(self, ps):
        self.list.clear()
        for p in ps:
            if p:
                self.list.addItem(p)


# ---------------------------------------------------------------------------------------------------------------
class Runner(QtWidgets.QWidget):
    """Runs ``python -m graftseg <args>`` and streams its output."""
    finished = QtCore.Signal(int, str)   # exit code, full output
    line = QtCore.Signal(str)

    def __init__(self):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        top = QtWidgets.QHBoxLayout(); lay.addLayout(top)
        self.state = QtWidgets.QLabel("Idle"); self.state.setObjectName("sub"); top.addWidget(self.state, 1)
        self.bar = QtWidgets.QProgressBar(); self.bar.setMaximumWidth(220); self.bar.setVisible(False); top.addWidget(self.bar)
        self.stop_btn = button("Stop", "danger", "Stop cleanly (like Ctrl+C)"); self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop); top.addWidget(self.stop_btn)
        self.log = QtWidgets.QPlainTextEdit(); self.log.setObjectName("log"); self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(20000); lay.addWidget(self.log, 1)
        self.proc: Optional[QtCore.QProcess] = None
        self.out: list[str] = []
        self._partial = ""

    @property
    def busy(self) -> bool:
        return self.proc is not None and self.proc.state() != QtCore.QProcess.NotRunning

    def start(self, args: list[str], label: str):
        if self.busy:
            QtWidgets.QMessageBox.information(self, "Busy", "A job is still running here.")
            return
        self.out, self._partial = [], ""
        self.log.clear()
        shown = " ".join(f'"{a}"' if " " in a else a for a in args)
        self.log.appendPlainText(f"$ graftseg {shown}\n")
        self.proc = QtCore.QProcess(self)
        env = QtCore.QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1"); env.insert("PYTHONIOENCODING", "utf-8")
        fd, self.stop_file = tempfile.mkstemp(prefix="graftseg_stop_"); os.close(fd); os.remove(self.stop_file)
        env.insert("GRAFTSEG_STOP_FILE", self.stop_file)
        self.proc.setProcessEnvironment(env)
        self.proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._done)
        self.state.setText(f"{label}…"); self.stop_btn.setEnabled(True)
        self.bar.setVisible(True); self.bar.setRange(0, 0)
        self.proc.start(sys.executable, ["-m", "graftseg", *args])

    def _read(self):
        text = self._partial + bytes(self.proc.readAllStandardOutput()).decode(errors="replace")
        text = text.replace("\r", "\n")
        lines = text.split("\n")
        self._partial = lines.pop()
        for ln in lines:
            if not ln.strip():
                continue
            self.out.append(ln)
            self.log.appendPlainText(ln)
            self.line.emit(ln)
            if ln.startswith("[") and "/" in ln.split("]")[0]:
                try:
                    i, n = ln[1:].split("]")[0].split("/")
                    self.bar.setRange(0, int(n)); self.bar.setValue(int(i))
                except ValueError:
                    pass

    def _done(self, code, _status):
        try:
            os.remove(self.stop_file)
        except OSError:
            pass
        if self._partial.strip():
            self.out.append(self._partial); self.log.appendPlainText(self._partial)
        self.stop_btn.setEnabled(False); self.bar.setVisible(False)
        self.state.setText("Finished" if code == 0 else f"Stopped / failed (exit {code})")
        self.finished.emit(code, "\n".join(self.out))

    def stop(self):
        """Stop cleanly on every OS: a stop file the job checks (between scans / every second during training),
        plus Ctrl+C on Linux/macOS."""
        if not self.busy:
            return
        Path(self.stop_file).touch()
        if os.name != "nt":
            try:
                os.kill(int(self.proc.processId()), signal.SIGINT)
            except OSError:
                pass
        self.state.setText("Stopping…")
        QtCore.QTimer.singleShot(180000, lambda: self.proc.kill() if self.busy else None)  # last resort


class Worker(QtCore.QThread):
    done = QtCore.Signal(object, object)  # result, error

    def __init__(self, fn, *a, **k):
        super().__init__(); self.fn, self.a, self.k = fn, a, k

    def run(self):
        try:
            self.done.emit(self.fn(*self.a, **self.k), None)
        except Exception as e:  # noqa: BLE001
            self.done.emit(None, e)


def gpu_info() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            ps = [torch.cuda.get_device_properties(i) for i in range(torch.cuda.device_count())]
            return " · ".join(f"{p.name} ({p.total_memory / 2**30:.0f} GB)" for p in ps) + f"  — CUDA {torch.version.cuda}"
        return f"No CUDA GPU visible (torch {torch.__version__}). Segmenting works on CPU, slower; training needs a GPU."
    except Exception as e:  # noqa: BLE001
        return f"PyTorch not available: {e}"


# ---------------------------------------------------------------------------------------------------------------
class Page(QtWidgets.QScrollArea):
    def __init__(self, app: "MainWindow"):
        super().__init__()
        self.app = app
        self.setWidgetResizable(True)
        inner = QtWidgets.QWidget(); inner.setObjectName("content")
        self.lay = QtWidgets.QVBoxLayout(inner); self.lay.setContentsMargins(28, 24, 28, 24); self.lay.setSpacing(16)
        self.lay.setAlignment(Qt.AlignTop)
        self.setWidget(inner)

    def refresh(self):
        pass


class HomePage(Page):
    def __init__(self, app):
        super().__init__(app)
        self.lay.addWidget(header("Welcome", "Segment organoid grafts on T2 MRI, check them, and keep the model up to date."))
        # first-run checklist
        self.setup_card, sl = card("Get started")
        self.steps = []
        for key, title, desc, page, btn in [
                ("ws", "Choose the workspace", "The folder that holds nnUNet_raw / nnUNet_results (e.g. …/MRI NeuralNetwork).", "Settings", "Open Settings"),
                ("model", "Add your trained weights", "Point to checkpoint_best.pth, give it a name. Copied into the models folder.", "Models", "Add weights"),
                ("go", "Segment a folder", "Pick a folder of DICOM / NRRD scans and review the results.", "Segment", "Segment")]:
            row = QtWidgets.QHBoxLayout(); row.setSpacing(12)
            badge = QtWidgets.QLabel(); badge.setFixedSize(26, 26); badge.setAlignment(Qt.AlignCenter)
            txt = QtWidgets.QLabel(); txt.setWordWrap(True)
            b = button(btn); b.clicked.connect(lambda _=0, p=page, k=key: (self.app.go(p), k == "model" and self.app.models.add()))
            row.addWidget(badge); row.addWidget(txt, 1); row.addWidget(b)
            sl.addLayout(row)
            self.steps.append((key, badge, txt, title, desc, b))
        self.lay.addWidget(self.setup_card)
        grid = ResponsiveGrid(280)
        c1, l1 = card("Workspace"); self.ws = QtWidgets.QLabel(); self.ws.setWordWrap(True); l1.addWidget(self.ws)
        c2, l2 = card("Compute"); self.gpu = QtWidgets.QLabel("Checking…"); self.gpu.setWordWrap(True); l2.addWidget(self.gpu)
        c3, l3 = card("Default model"); self.model = QtWidgets.QLabel(); self.model.setWordWrap(True); l3.addWidget(self.model)
        mb = button("Manage models"); mb.clicked.connect(lambda: app.go("Models")); l3.addWidget(mb, 0, Qt.AlignLeft)
        for c, l in ((c1, l1), (c2, l2), (c3, l3)):
            l.setAlignment(Qt.AlignTop); grid.add(c)
        self.lay.addWidget(grid)
        grid2 = ResponsiveGrid(260)
        for ic, t, d, page, role in [("segment", "Segment new scans", "Each scan gets <b>PA_01_R2M.seg.nrrd</b> next to it.", "Segment", "primary"),
                                     ("review", "Review and correct", "Paint / erase, adjust contrast, save as reviewed.", "Review", ""),
                                     ("train", "Retrain", "Turn reviewed folders into a dataset and train on the GPU.", "Train", "")]:
            c, l = card()
            head = QtWidgets.QHBoxLayout(); lab = QtWidgets.QLabel(); lab.setPixmap(icon(ic, T["accent"], 28).pixmap(28, 28))
            head.addWidget(lab); tt = QtWidgets.QLabel(f"<b style='font-size:12pt'>{t}</b>"); head.addWidget(tt, 1); l.addLayout(head)
            dd = QtWidgets.QLabel(d); dd.setObjectName("sub"); dd.setWordWrap(True); l.addWidget(dd)
            b = button(f"Open {page}", role); b.clicked.connect(lambda _=0, p=page: app.go(p)); l.addWidget(b, 0, Qt.AlignLeft)
            grid2.add(c)
        self.lay.addWidget(grid2)
        self.lay.addStretch()
        self._w = Worker(gpu_info); self._w.done.connect(lambda r, e: self.gpu.setText(r or str(e))); self._w.start()

    def refresh(self):
        cfg = self.app.cfg
        ws_ok = C.CONFIG_PATH.exists() and bool(cfg.workspace or cfg.nnunet_results)
        try:
            self.ws.setText(f"<b>{cfg.workspace or cfg.results.parent}</b><br><span style='color:{muted()}'>models: {cfg.models}</span>")
        except RuntimeError:
            self.ws.setText(f"<span style='color:{BAD}'>Not set yet</span>")
        model_ok = False
        try:
            d, cd = M.resolve(cfg, None)
            model_ok = True
            self.model.setText(f"<span style='font-size:13pt;font-weight:600'>{cd.get('name', d.name)}</span><br>"
                               f"<span style='color:{muted()}'>epoch {cd.get('epoch')} · EMA Dice {cd.get('best_ema_pseudo_dice')} · "
                               f"{cd.get('checkpoint')} · folds {cd.get('folds')}</span>")
        except Exception:
            self.model.setText(f"<span style='color:{WARN}'>No model yet.</span> Add your trained weights on the Models page.")
        done = {"ws": ws_ok, "model": model_ok, "go": bool(settings().value("segment/ran", False, type=bool))}
        for key, badge, txt, title, desc, b in self.steps:
            ok = done[key]
            if ok:
                badge.setPixmap(icon("check", "white", 18).pixmap(18, 18))
                badge.setStyleSheet(f"background:{GOOD}; border-radius:13px;")
            else:
                badge.setPixmap(QtGui.QPixmap()); badge.setText(str([s[0] for s in self.steps].index(key) + 1))
                badge.setStyleSheet(f"background:{T['accent_soft']}; color:{T['accent']}; border-radius:13px; font-weight:700;")
            txt.setText(f"<b>{title}</b><br><span style='color:{muted()}'>{desc}</span>")
            b.setProperty("role", "" if ok else "primary"); b.style().unpolish(b); b.style().polish(b)
        self.setup_card.setVisible(not all(done.values()))


class SegmentPage(Page):
    def __init__(self, app):
        super().__init__(app)
        self.lay.addWidget(header("Segment new scans",
                                  "Finds Bruker/enhanced DICOM (EnIm1.dcm) or .nrrd scans in the folders, picks the T2 "
                                  "anatomy series, and writes <Cage>_<EarTag>.seg.nrrd (plus the MRI as .nrrd) next to each scan."))
        c, l = card("Scans")
        self.inputs = PathList("segment", files=True); l.addWidget(self.inputs)
        self.lay.addWidget(c)
        c, l = card("Options")
        form = QtWidgets.QFormLayout(); form.setHorizontalSpacing(18); l.addLayout(form)
        self.model = QtWidgets.QComboBox(); form.addRow("Model", self.model)
        self.device = QtWidgets.QComboBox(); self.device.addItems(["auto", "cuda", "mps", "cpu"]); self.device.setFixedWidth(160)
        self.device.setToolTip("auto = NVIDIA GPU if present, else Apple-silicon GPU (mps), else CPU")
        form.addRow("Device", self.device)
        self.fast = QtWidgets.QCheckBox("Fast mode (no mirroring augmentation, ~8× faster, slightly less accurate)")
        self.overwrite = QtWidgets.QCheckBox("Redo scans that already have a prediction (reviewed files are kept unless this is ticked)")
        self.review_after = QtWidgets.QCheckBox("Open Review when finished"); self.review_after.setChecked(True)
        for w in (self.fast, self.overwrite, self.review_after):
            form.addRow("", w)
        row = QtWidgets.QHBoxLayout(); l.addLayout(row)
        self.preview = button("Preview names", tip="Show which scans are found and how each will be named; writes nothing")
        self.run = button("Run segmentation", "primary")
        row.addWidget(self.preview); row.addWidget(self.run); row.addStretch()
        self.lay.addWidget(c)
        c, l = card("Progress")
        self.runner = Runner(); self.runner.setMinimumHeight(220); l.addWidget(self.runner)
        self.lay.addWidget(c, 1)
        self.res_card, l = card("Results")
        self.results = QtWidgets.QTableWidget(0, 6)
        self.results.setHorizontalHeaderLabels(["Animal", "Graft (mm³)", "Pieces", "Check", "Status", "File"])
        self.results.verticalHeader().setVisible(False); self.results.setAlternatingRowColors(True)
        self.results.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.results.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.results.horizontalHeader().setStretchLastSection(True); self.results.setMinimumHeight(180)
        self.results.doubleClicked.connect(self._open_row)
        hint = QtWidgets.QLabel("Double-click a row to open it in Review."); hint.setObjectName("sub")
        l.addWidget(self.results); l.addWidget(hint)
        self.res_card.setVisible(False)
        self.lay.addWidget(self.res_card)
        self.preview.clicked.connect(lambda: self._go(dry=True))
        self.run.clicked.connect(lambda: self._go(dry=False))
        self.runner.finished.connect(self._finished)
        self.inputs.set_paths(settings().value("segment/inputs", []) or [])
        self._dry = False

    def refresh(self):
        cur = self.model.currentData()
        self.model.clear()
        for m in M.list_models(self.app.cfg):
            self.model.addItem(f"{m['name']}{'  (default)' if m['default'] else ''}  — epoch {m.get('epoch')}, "
                               f"Dice {m.get('best_ema_pseudo_dice')}", m["name"])
        if self.model.count() == 0:
            self.model.addItem("No model registered — add one on the Models page", None)
        i = self.model.findData(cur or self.app.cfg.default_model)
        self.model.setCurrentIndex(max(i, 0))
        self.device.setCurrentText(self.app.cfg.device or "auto")

    def _go(self, dry: bool):
        paths = self.inputs.paths()
        if not paths:
            QtWidgets.QMessageBox.information(self, "No scans", "Add a folder with scans first.")
            return
        if not dry and not self.model.currentData():
            QtWidgets.QMessageBox.warning(self, "No model", "Register a model on the Models page first.")
            return
        settings().setValue("segment/inputs", paths)
        args = ["predict", *paths]
        if self.model.currentData():
            args += ["--model", self.model.currentData()]
        args += ["--device", self.device.currentText()]
        if self.fast.isChecked():
            args.append("--fast")
        if self.overwrite.isChecked():
            args.append("--overwrite")
        if dry:
            args.append("--dry-run")
        self._dry = dry
        self.runner.start(args, "Finding scans" if dry else "Segmenting")

    def _finished(self, code: int, out: str):
        if self._dry:
            return
        rows = []
        for ln in out.splitlines():
            if ln.startswith("Summary: "):
                try:
                    with open(ln[len("Summary: "):].strip(), encoding="utf-8") as f:
                        rows = list(csv.DictReader(f))
                except OSError:
                    pass
        if code != 0 and not rows:
            self.app.status("Segmentation stopped or failed — see the log.", "error")
            return
        settings().setValue("segment/ran", True)
        self._rows = rows
        self.results.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [r["name"], r["graft_mm3"], r["components"], r["qc"] or ("ok" if r["status"] == "predicted" else ""),
                    r["status"], r["seg_nrrd"] or r["input"]]
            for j, v in enumerate(vals):
                it = QtWidgets.QTableWidgetItem(str(v))
                if j == 3 and r["qc"]:
                    it.setForeground(QtGui.QColor(WARN))
                if j == 4 and r["status"].startswith("failed"):
                    it.setForeground(QtGui.QColor(BAD))
                self.results.setItem(i, j, it)
        self.results.resizeColumnsToContents()
        self.res_card.setVisible(bool(rows))
        n_ok = sum(r["status"] == "predicted" for r in rows)
        flagged = sum(bool(r["qc"]) for r in rows)
        self.app.status(f"Segmented {n_ok} scan(s)" + (f", {flagged} flagged for a closer look" if flagged else ""),
                        "warn" if flagged else "ok")
        self.app.home.refresh()
        segs = [r["seg_nrrd"] for r in rows if r.get("seg_nrrd")]
        if segs and self.review_after.isChecked():
            self.app.review.load_paths(segs)
            self.app.go("Review")

    def _open_row(self, index):
        r = self._rows[index.row()] if index.row() < len(getattr(self, "_rows", [])) else None
        if r and r.get("seg_nrrd"):
            segs = [x["seg_nrrd"] for x in self._rows if x.get("seg_nrrd")]
            self.app.review.load_paths(segs)
            self.app.review.select_path(r["seg_nrrd"])
            self.app.go("Review")


# ---------------------------------------------------------------------------------------------------------------
class SliceView(pg.ViewBox):
    """ViewBox where left/right drag paints/erases, wheel changes slice, Ctrl+wheel / middle-drag zoom and pan."""

    def __init__(self, editor: "ReviewPage"):
        super().__init__(lockAspect=True, invertY=True, enableMenu=False)
        self.editor = editor

    def mouseDragEvent(self, ev, axis=None):
        if ev.button() in (Qt.LeftButton, Qt.RightButton) and self.editor.case is not None:
            ev.accept()
            self.editor.stroke(ev.scenePos(), ev.button() == Qt.LeftButton, ev.isStart(), ev.isFinish())
        else:
            super().mouseDragEvent(ev, axis)

    def mouseClickEvent(self, ev):
        if ev.button() in (Qt.LeftButton, Qt.RightButton) and self.editor.case is not None:
            ev.accept()
            self.editor.stroke(ev.scenePos(), ev.button() == Qt.LeftButton, True, True)
        else:
            super().mouseClickEvent(ev)

    def wheelEvent(self, ev, axis=None):
        if ev.modifiers() & Qt.ControlModifier:
            super().wheelEvent(ev, axis)
        else:
            ev.accept()
            self.editor.step_slice(1 if ev.delta() > 0 else -1)


class ReviewPage(QtWidgets.QWidget):
    LUT = np.array([[0, 0, 0, 0], [52, 211, 120, 255]], np.uint8)

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setObjectName("content")
        self.case = None
        self.paths: list[str] = []
        self.idx = -1
        self.axis = 0
        self.slice = 0
        self.paint_mode = True
        self._last = None
        outer = QtWidgets.QHBoxLayout(self); outer.setContentsMargins(12, 12, 12, 12); outer.setSpacing(0)
        self.split = QtWidgets.QSplitter(Qt.Horizontal); self.split.setHandleWidth(10); outer.addWidget(self.split)

        # left: case list
        left, ll = card("Cases")
        left.setMinimumWidth(190); self.left = left
        ob = button("Folder…", tip="Load every .seg.nrrd under a folder"); ob.clicked.connect(self.open_folder)
        of = button("Files…"); of.clicked.connect(self.open_files)
        r = QtWidgets.QHBoxLayout(); r.addWidget(ob); r.addWidget(of); ll.addLayout(r)
        self.only_pred = QtWidgets.QCheckBox("Only not yet reviewed"); self.only_pred.toggled.connect(self._fill_list)
        ll.addWidget(self.only_pred)
        self.list = QtWidgets.QListWidget(); self.list.setObjectName("cases")
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.list.setTextElideMode(Qt.ElideMiddle)
        self.list.currentRowChanged.connect(self._row_changed); ll.addWidget(self.list, 1)
        self.split.addWidget(left)

        # center: viewer
        center = QtWidgets.QWidget(); mid = QtWidgets.QVBoxLayout(center); mid.setContentsMargins(6, 0, 6, 0); mid.setSpacing(8)
        self.split.addWidget(center)
        toolw = QtWidgets.QWidget(); tools = FlowLayout(toolw, spacing=8); mid.addWidget(toolw)
        self.b_cases = button("Cases", "ghost", "Show / hide the case list"); self.b_cases.setCheckable(True); self.b_cases.setChecked(True)
        self.b_cases.toggled.connect(lambda on: self.left.setVisible(on)); tools.addWidget(self.b_cases)
        self.btn_paint = button("Paint  (P)"); self.btn_erase = button("Erase  (E)")
        for b in (self.btn_paint, self.btn_erase):
            b.setCheckable(True)
        self.btn_paint.setChecked(True)
        grp = QtWidgets.QButtonGroup(self); grp.setExclusive(True); grp.addButton(self.btn_paint); grp.addButton(self.btn_erase)
        self.btn_paint.toggled.connect(lambda on: setattr(self, "paint_mode", on))
        tools.addWidget(self.btn_paint); tools.addWidget(self.btn_erase)
        bw = QtWidgets.QWidget(); bl = QtWidgets.QHBoxLayout(bw); bl.setContentsMargins(4, 0, 4, 0)
        bl.addWidget(QtWidgets.QLabel("Brush"))
        self.brush = QtWidgets.QSlider(Qt.Horizontal); self.brush.setRange(1, 30); self.brush.setValue(4); self.brush.setFixedWidth(110)
        self.brush_lbl = QtWidgets.QLabel("4 px"); self.brush_lbl.setMinimumWidth(36)
        self.brush.valueChanged.connect(lambda v: (self.brush_lbl.setText(f"{v} px"), self._cursor_update()))
        bl.addWidget(self.brush); bl.addWidget(self.brush_lbl); tools.addWidget(bw)
        self.ball = QtWidgets.QCheckBox("3D brush"); self.ball.setToolTip("Paint a ball that reaches the neighbouring slices (key 3)")
        tools.addWidget(self.ball)
        ow = QtWidgets.QWidget(); ol = QtWidgets.QHBoxLayout(ow); ol.setContentsMargins(4, 0, 4, 0)
        ol.addWidget(QtWidgets.QLabel("Overlay"))
        self.opacity = QtWidgets.QSlider(Qt.Horizontal); self.opacity.setRange(0, 100); self.opacity.setValue(45); self.opacity.setFixedWidth(90)
        self.opacity.valueChanged.connect(lambda v: self.ov.setOpacity(v / 100)); ol.addWidget(self.opacity); tools.addWidget(ow)
        self.view_btns = []
        for k, name in enumerate(("Acquired plane", "Plane 2", "Plane 3")):
            b = button(name); b.setCheckable(True); b.clicked.connect(lambda _=0, k=k: self.set_axis_order(k))
            self.view_btns.append(b); tools.addWidget(b)

        self.glw = pg.GraphicsLayoutWidget(); self.glw.setBackground(T["viewer"]); self.glw.setMinimumSize(320, 260)
        self.vb = SliceView(self); self.glw.addItem(self.vb, 0, 0)
        self.img = pg.ImageItem(); self.ov = pg.ImageItem(); self.ov.setOpacity(0.45)
        self.vb.addItem(self.img); self.vb.addItem(self.ov)
        self.cursor = QtWidgets.QGraphicsEllipseItem(); self.cursor.setPen(pg.mkPen("#fbbf24", width=1.5, cosmetic=True))
        self.cursor.setParentItem(self.img); self.cursor.setVisible(False)
        self.hist = pg.HistogramLUTItem(image=self.img, orientation="vertical"); self.hist.setFixedWidth(110)
        self.glw.addItem(self.hist, 0, 1)
        self.glw.scene().sigMouseMoved.connect(self._mouse_moved)
        mid.addWidget(self.glw, 1)

        srow = QtWidgets.QHBoxLayout(); mid.addLayout(srow)
        self.slider = QtWidgets.QSlider(Qt.Horizontal); self.slider.valueChanged.connect(self.set_slice)
        self.slice_lbl = QtWidgets.QLabel("–"); self.slice_lbl.setMinimumWidth(90)
        srow.addWidget(QtWidgets.QLabel("Slice")); srow.addWidget(self.slider, 1); srow.addWidget(self.slice_lbl)
        auto = button("Auto contrast", tip="Reset window/level (drag the histogram on the right to window smoothly)")
        auto.clicked.connect(self.auto_levels); srow.addWidget(auto)

        # right: info + actions
        right, rl = card("This case")
        self.info = QtWidgets.QLabel("Open a folder of segmentations, or run Segment first."); self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.RichText); rl.addWidget(self.info)
        self.b_save = button("Save  (Ctrl+S)", "primary"); self.b_next = button("Save + next  (N)", "primary")
        self.b_prev = button("◀  Previous  (B)"); self.b_undo = button("Undo  (Ctrl+Z)")
        self.b_fill = button("Fill holes in slice  (F)"); self.b_jump = button("Go to graft  (J)")
        self.b_revert = button("Revert to saved", "danger")
        for b in (self.b_save, self.b_next, self.b_prev, self.b_undo, self.b_fill, self.b_jump, self.b_revert):
            rl.addWidget(b)
        help_ = QtWidgets.QLabel(
            f"<span style='color:{muted()}'><b>Mouse</b><br>left-drag: paint (or erase in Erase mode)<br>right-drag: erase<br>"
            "wheel: slice · Ctrl+wheel: zoom<br>middle-drag: pan<br><b>Keys</b> ↑/↓ slice · [ ] brush · 3 3D brush<br>"
            "Contrast: drag the histogram bar.</span>")
        help_.setWordWrap(True); rl.addWidget(help_); rl.addStretch()
        rs = QtWidgets.QScrollArea(); rs.setWidgetResizable(True); rs.setWidget(right); rs.setMinimumWidth(220)
        rs.setFrameShape(QtWidgets.QFrame.NoFrame); self.split.addWidget(rs)
        self.split.setStretchFactor(0, 0); self.split.setStretchFactor(1, 1); self.split.setStretchFactor(2, 0)
        self.split.setSizes([250, 900, 270])
        st = settings().value("review/split")
        if st is not None:
            self.split.restoreState(st)
        self.split.splitterMoved.connect(lambda *_: settings().setValue("review/split", self.split.saveState()))
        self._auto_hidden = False
        self.b_save.clicked.connect(self.save); self.b_next.clicked.connect(self.save_next)
        self.b_prev.clicked.connect(lambda: self.goto(self.idx - 1)); self.b_undo.clicked.connect(self.undo)
        self.b_fill.clicked.connect(self.fill); self.b_jump.clicked.connect(self.jump); self.b_revert.clicked.connect(self.revert)
        for key, fn in [("Ctrl+S", self.save), ("N", self.save_next), ("B", lambda: self.goto(self.idx - 1)),
                        ("Ctrl+Z", self.undo), ("Z", self.undo), ("F", self.fill), ("J", self.jump),
                        ("P", lambda: self.btn_paint.setChecked(True)), ("E", lambda: self.btn_erase.setChecked(True)),
                        ("Up", lambda: self.step_slice(1)), ("Down", lambda: self.step_slice(-1)),
                        ("]", lambda: self.brush.setValue(self.brush.value() + 1)),
                        ("[", lambda: self.brush.setValue(self.brush.value() - 1)),
                        ("3", lambda: self.ball.toggle())]:
            sc = QtGui.QShortcut(QtGui.QKeySequence(key), self); sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        self._statuses: dict[str, str] = {}
        self._set_enabled(False)

    def refresh(self):
        pass

    # -- list ----------------------------------------------------------------------------------------
    def open_folder(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Folder with .seg.nrrd files", last_dir("review"))
        if d:
            remember_dir("review", d)
            from .review import collect
            self.load_paths([str(p) for p in collect([Path(d)])])

    def open_files(self):
        fs, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Segmentations", last_dir("review"), "Slicer segmentation (*.seg.nrrd)")
        if fs:
            remember_dir("review", fs[0]); self.load_paths(fs)

    def load_paths(self, paths: list[str]):
        if not self.confirm_leave():
            return
        self.paths = [str(p) for p in paths]
        import nrrd
        self._statuses = {}
        for p in self.paths:
            try:
                self._statuses[p] = nrrd.read_header(p).get("GraftSeg_Status", "human")
            except Exception:
                self._statuses[p] = "unreadable"
        self.case, self.idx = None, -1
        self._fill_list()

    def _fill_list(self):
        self.list.blockSignals(True)
        self.list.clear()
        self._shown = [p for p in self.paths if not self.only_pred.isChecked() or self._statuses.get(p) == "predicted"]
        colors = {"predicted": WARN, "reviewed": GOOD, "human": INFO}
        for p in self._shown:
            st = self._statuses.get(p, "?")
            it = QtWidgets.QListWidgetItem(f"{Path(p).name[:-9]}\n{st}")
            it.setForeground(QtGui.QColor(T["text"])); it.setToolTip(p)
            it.setData(Qt.UserRole, p)
            it.setIcon(self._dot(colors.get(st, muted())))
            self.list.addItem(it)
        self.list.blockSignals(False)
        if self._shown:
            self.list.setCurrentRow(0)

    @staticmethod
    def _dot(color: str) -> QtGui.QIcon:
        pm = QtGui.QPixmap(12, 12); pm.fill(Qt.transparent)
        p = QtGui.QPainter(pm); p.setRenderHint(QtGui.QPainter.Antialiasing); p.setBrush(QtGui.QColor(color)); p.setPen(Qt.NoPen)
        p.drawEllipse(1, 1, 10, 10); p.end()
        return QtGui.QIcon(pm)

    def _row_changed(self, row: int):
        if row < 0 or row >= len(self._shown):
            return
        path = self._shown[row]
        if self.case is not None and str(self.case.seg_path) == path:
            return
        if not self.confirm_leave():
            self.list.blockSignals(True); self.list.setCurrentRow(self._shown.index(str(self.case.seg_path))); self.list.blockSignals(False)
            return
        self.open_case(path)

    def select_path(self, path: str):
        if path in getattr(self, "_shown", []):
            self.list.setCurrentRow(self._shown.index(path))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        narrow = self.width() < 1050
        if narrow and not self._auto_hidden and self.b_cases.isChecked():
            self._auto_hidden = True; self.b_cases.setChecked(False)
        elif not narrow and self._auto_hidden:
            self._auto_hidden = False; self.b_cases.setChecked(True)

    def goto(self, i: int):
        if 0 <= i < len(self._shown):
            self.list.setCurrentRow(i)
        elif i >= len(self._shown) and self._shown:
            self.app.status("Last case reached.")

    def confirm_leave(self) -> bool:
        if self.case is None or not self.case.edited:
            return True
        r = QtWidgets.QMessageBox.question(self, "Unsaved edits", f"Save changes to {self.case.seg_path.name}?",
                                           QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel)
        if r == QtWidgets.QMessageBox.Save:
            self.save(); return True
        return r == QtWidgets.QMessageBox.Discard

    # -- case ----------------------------------------------------------------------------------------
    def open_case(self, path: str):
        from .review import Case
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.case = Case(Path(path))
        except Exception as e:  # noqa: BLE001
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, "Cannot open", str(e)); self.case = None; self._set_enabled(False)
            return
        QtWidgets.QApplication.restoreOverrideCursor()
        self.idx = self._shown.index(path)
        sp = self.case.spacing_zyx
        self.acq_axis = int(np.argmax(sp)) if np.ptp(sp) > 1e-6 else 0
        self._set_enabled(True)
        self.auto_levels(redraw=False)
        self.set_axis_order(0)

    def _set_enabled(self, on: bool):
        for b in (self.b_save, self.b_next, self.b_undo, self.b_fill, self.b_jump, self.b_revert, self.slider):
            b.setEnabled(on)
        self.b_prev.setEnabled(on)

    def set_axis_order(self, k: int):
        if self.case is None:
            return
        axes = [self.acq_axis] + [a for a in range(3) if a != self.acq_axis]
        self.axis = axes[k]
        for i, b in enumerate(self.view_btns):
            b.setChecked(i == k)
        sp = np.delete(self.case.spacing_zyx, self.axis)  # (row spacing, col spacing) of the 2D view
        tr = QtGui.QTransform(); tr.scale(sp[1], sp[0])
        self.img.setTransform(tr); self.ov.setTransform(tr)
        n = self.case.mask.shape[self.axis]
        self.slider.blockSignals(True); self.slider.setRange(0, n - 1); self.slider.blockSignals(False)
        self.slice = -1
        self.set_slice(self.case.best_slice(self.axis), force=True)
        self.vb.autoRange(padding=0.02)

    def auto_levels(self, redraw: bool = True):
        if self.case is None:
            return
        v = self.case.vol
        lo, hi = np.percentile(v, [0.5, 99.5])
        self.levels = (float(lo), float(hi))
        self.hist.setLevels(*self.levels)
        if redraw:
            self.redraw()

    def set_slice(self, s: int, force: bool = False):
        if self.case is None:
            return
        s = int(np.clip(s, 0, self.case.mask.shape[self.axis] - 1))
        if s == self.slice and not force:
            return
        self.slice = s
        if self.slider.value() != s:
            self.slider.blockSignals(True); self.slider.setValue(s); self.slider.blockSignals(False)
        self.redraw(image=True)

    def step_slice(self, d: int):
        self.set_slice(self.slice + d)

    def redraw(self, image: bool = True):
        c = self.case
        if c is None:
            return
        if image:
            lv = self.hist.getLevels() if self.img.image is not None else self.levels
            self.img.setImage(np.take(c.vol, self.slice, axis=self.axis), autoLevels=False, levels=lv)
        self.ov.setImage(np.take(c.mask, self.slice, axis=self.axis).astype(np.uint8), lut=self.LUT, levels=(0, 1),
                         autoLevels=False)
        n = c.mask.shape[self.axis]
        self.slice_lbl.setText(f"{self.slice + 1} / {n}")
        st = c.header.get("GraftSeg_Status", "human")
        color = {"predicted": WARN, "reviewed": GOOD}.get(st, INFO)
        self.info.setText(
            f"<div style='font-size:13pt;font-weight:600'>{c.name}</div>"
            f"<div style='color:{muted()}'>case {self.idx + 1} of {len(self._shown)}</div><br>"
            f"{chip(st, color)} {chip('edited – not saved', BAD) if c.edited else ''}<br><br>"
            f"<b>Graft</b> {c.volume_mm3():.2f} mm³<br>"
            f"<span style='color:{muted()}'>{int(c.mask.sum())} voxels · voxel {' × '.join(f'{s:.3f}' for s in c.spacing_zyx[::-1])} mm<br>"
            f"MRI: {c.image_path.name}<br>model: {c.header.get('GraftSeg_Model', '–')}</span>")

    # -- painting ------------------------------------------------------------------------------------
    def _rc(self, scene_pos) -> tuple[float, float]:
        p = self.img.mapFromScene(scene_pos)
        return p.y(), p.x()

    def stroke(self, scene_pos, left: bool, start: bool, finish: bool):
        paint = left and self.paint_mode
        r, cc = self._rc(scene_pos)
        if start:
            self.case.push_undo(self.axis, self.slice, self.ball.isChecked())
            self._last = (r, cc)
        lr, lc = self._last or (r, cc)
        dist = float(np.hypot(r - lr, cc - lc))
        steps = max(1, int(dist / max(1.0, self.brush.value() / 2)))
        for t in np.linspace(0, 1, steps + 1)[1:] if dist > 0 else [1.0]:
            self.case.brush(self.axis, self.slice, lr + (r - lr) * t, lc + (cc - lc) * t, self.brush.value(),
                            paint=paint, ball=self.ball.isChecked())
        self._last = None if finish else (r, cc)
        self.redraw(image=False)

    def _mouse_moved(self, scene_pos):
        if self.case is None or not self.vb.sceneBoundingRect().contains(scene_pos):
            self.cursor.setVisible(False); return
        self._cursor_pos = self._rc(scene_pos)
        self._cursor_update()

    def _cursor_update(self):
        if self.case is None or not hasattr(self, "_cursor_pos"):
            return
        r, c = self._cursor_pos
        sp = np.delete(self.case.spacing_zyx, self.axis)
        rad_mm = self.brush.value() * sp.min()
        ry, rx = rad_mm / sp[0], rad_mm / sp[1]
        self.cursor.setRect(c - rx, r - ry, 2 * rx, 2 * ry); self.cursor.setVisible(True)

    def undo(self):
        if self.case and self.case.do_undo():
            self.redraw(image=False)

    def fill(self):
        if self.case:
            self.case.push_undo(self.axis, self.slice, False); self.case.fill_holes(self.axis, self.slice); self.redraw(image=False)

    def jump(self):
        if self.case:
            self.set_slice(self.case.best_slice(self.axis))

    def revert(self):
        if self.case and self.case.edited:
            self.case.mask[...] = self.case.original; self.case.undo.clear(); self.redraw(image=False)

    def save(self):
        if self.case is None:
            return
        p = self.case.save()
        self.case.header["GraftSeg_Status"] = "reviewed"
        self._statuses[str(p)] = "reviewed"
        it = self.list.item(self.idx)
        if it:
            it.setText(f"{Path(p).name[:-9]}\nreviewed"); it.setIcon(self._dot(GOOD))
        self.redraw(image=False)
        self.app.status(f"Saved {Path(p).name} (reviewed)", "ok")

    def save_next(self):
        if self.case is None:
            return
        self.save()
        self.goto(self.idx + 1)


# ---------------------------------------------------------------------------------------------------------------
class AddModelDialog(QtWidgets.QDialog):
    def __init__(self, parent, cfg: C.Config):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Add model weights"); self.setMinimumWidth(620)
        lay = QtWidgets.QVBoxLayout(self); lay.setSpacing(12)
        lay.addWidget(header("Add model weights", "Point to a checkpoint file (e.g. checkpoint_best.pth, even one copied "
                                                  "into a folder like “saved NN weights”), a fold_N folder, or a whole "
                                                  "nnU-Net training folder (nnUNetTrainer__nnUNetPlans__3d_fullres)."))
        form = QtWidgets.QFormLayout(); form.setHorizontalSpacing(14); lay.addLayout(form)
        src_row = QtWidgets.QHBoxLayout()
        self.src = QtWidgets.QLineEdit(); self.src.setPlaceholderText("/path/to/checkpoint_best.pth  or  training folder")
        bf = button("File…"); bd = button("Folder…")
        bf.clicked.connect(self.pick_file); bd.clicked.connect(self.pick_dir)
        src_row.addWidget(self.src, 1); src_row.addWidget(bf); src_row.addWidget(bd)
        form.addRow("Weights", src_row)
        self.name = QtWidgets.QLineEdit(); self.name.setPlaceholderText("e.g. graft_v1_2026-04-23")
        form.addRow("Name", self.name)
        self.ckpt = QtWidgets.QComboBox(); self.ckpt.setEditable(True); self.ckpt.addItems(["checkpoint_best.pth", "checkpoint_final.pth"])
        form.addRow("Checkpoint", self.ckpt)
        loc_row = QtWidgets.QHBoxLayout()
        self.loc = QtWidgets.QLineEdit(str(cfg.models)); bl = button("Change…")
        bl.clicked.connect(self.pick_loc); loc_row.addWidget(self.loc, 1); loc_row.addWidget(bl)
        form.addRow("Save in", loc_row)
        self.link = QtWidgets.QCheckBox("Link to the original file instead of copying (saves disk; breaks if it moves)")
        self.default = QtWidgets.QCheckBox("Use as default model"); self.default.setChecked(True)
        self.notes = QtWidgets.QLineEdit(); self.notes.setPlaceholderText("optional: what is special about this model")
        form.addRow("", self.link); form.addRow("", self.default); form.addRow("Notes", self.notes)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.button(QtWidgets.QDialogButtonBox.Ok).setText("Add model"); bb.button(QtWidgets.QDialogButtonBox.Ok).setProperty("role", "primary")
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)

    def _suggest(self, p: Path):
        self.src.setText(str(p)); remember_dir("weights", str(p))
        if not self.name.text():
            base = p.parent.name if p.is_file() else p.name
            self.name.setText("".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in base)[:40])
        if p.is_dir():
            names = sorted({c.name for c in p.rglob("fold_*/*.pth")} | {c.name for c in p.glob("*.pth")})
            if names:
                self.ckpt.clear(); self.ckpt.addItems(names)
                if "checkpoint_best.pth" in names:
                    self.ckpt.setCurrentText("checkpoint_best.pth")
        else:
            self.ckpt.clear(); self.ckpt.addItem(p.name)

    def pick_file(self):
        f, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Checkpoint", last_dir("weights"), "PyTorch checkpoint (*.pth);;All files (*)")
        if f:
            self._suggest(Path(f))

    def pick_dir(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Training folder", last_dir("weights"))
        if d:
            self._suggest(Path(d))

    def pick_loc(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Where to keep models", self.loc.text())
        if d:
            self.loc.setText(d)


class ModelsPage(Page):
    COLS = ["", "Name", "Epoch", "Best EMA Dice", "Folds", "Checkpoint", "Normalization", "Added", "Notes"]

    def __init__(self, app):
        super().__init__(app)
        self.lay.addWidget(header("Models", "Every model is one folder with its weights, plans and a model card. "
                                            "Prediction only needs that folder — no nnU-Net paths or flags."))
        c, l = card()
        row = QtWidgets.QHBoxLayout(); l.addLayout(row)
        self.loc = QtWidgets.QLabel(); self.loc.setObjectName("sub"); row.addWidget(self.loc, 1)
        b_open_loc = button("Open models folder"); b_open_loc.clicked.connect(lambda: self._open(self.app.cfg.models)); row.addWidget(b_open_loc)
        self.table = QtWidgets.QTableWidget(0, len(self.COLS)); self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows); self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers); self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True); self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(260); self.table.doubleClicked.connect(lambda _: self.set_default())
        l.addWidget(self.table)
        row = QtWidgets.QHBoxLayout(); l.addLayout(row)
        self.b_add = button("＋ Add weights…", "primary"); self.b_add.clicked.connect(self.add)
        b_def = button("★ Set as default"); b_def.clicked.connect(self.set_default)
        b_exp = button("Save a copy as…", tip="Copy this model (weights + card) to any folder under any name")
        b_exp.clicked.connect(self.export)
        b_ren = button("Rename…"); b_ren.clicked.connect(self.rename)
        b_open = button("Open folder"); b_open.clicked.connect(lambda: self._open(self._selected_path()))
        b_del = button("Delete", "danger"); b_del.clicked.connect(self.delete)
        for b in (self.b_add, b_def, b_exp, b_ren, b_open):
            row.addWidget(b)
        row.addStretch(); row.addWidget(b_del)
        self.lay.addWidget(c)
        c, l = card("Details")
        self.details = QtWidgets.QPlainTextEdit(); self.details.setReadOnly(True); self.details.setObjectName("log")
        self.details.setMinimumHeight(180); l.addWidget(self.details)
        self.lay.addWidget(c)
        self.lay.addStretch()
        self.table.itemSelectionChanged.connect(self._show_details)
        self._models = []

    def refresh(self):
        cfg = self.app.cfg
        try:
            self.loc.setText(f"Stored in  {cfg.models}")
        except RuntimeError:
            self.loc.setText("Workspace not set (Settings)")
            return
        self._models = M.list_models(cfg)
        self.table.setRowCount(len(self._models))
        for i, m in enumerate(self._models):
            vals = ["★" if m["default"] else "", m["name"], m.get("epoch"), m.get("best_ema_pseudo_dice"),
                    ",".join(str(f) for f in m.get("folds", [])), m.get("checkpoint"),
                    ",".join(m.get("normalization") or []) if m.get("normalization") else "",
                    (m.get("registered") or "")[:16].replace("T", " "), m.get("notes", "")]
            for j, v in enumerate(vals):
                it = QtWidgets.QTableWidgetItem("" if v is None else str(v))
                if j == 0:
                    it.setForeground(QtGui.QColor("#f59e0b"))
                if j == 1:
                    f = it.font(); f.setBold(True); it.setFont(f)
                self.table.setItem(i, j, it)
        self.table.resizeColumnsToContents()

    def _selected(self) -> Optional[dict]:
        r = self.table.currentRow()
        return self._models[r] if 0 <= r < len(self._models) else None

    def _selected_path(self):
        m = self._selected()
        return Path(m["path"]) if m else None

    def _show_details(self):
        m = self._selected()
        self.details.setPlainText(json.dumps({k: v for k, v in m.items() if k != "default"}, indent=2) if m else "")

    @staticmethod
    def _open(path):
        if path:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

    def add(self):
        dlg = AddModelDialog(self, self.app.cfg)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        src, name = dlg.src.text().strip(), dlg.name.text().strip()
        if not src or not name:
            QtWidgets.QMessageBox.warning(self, "Missing", "Choose the weights and give the model a name."); return
        cfg = self.app.cfg
        loc = Path(dlg.loc.text().strip())
        if loc != cfg.models:
            cfg.models_dir = str(loc); cfg.save()
        self.b_add.setEnabled(False); self.app.status("Adding model (copying weights)…")
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        self._w = Worker(M.register, cfg, Path(src), name, checkpoint=dlg.ckpt.currentText(), notes=dlg.notes.text(),
                         link=dlg.link.isChecked(), make_default=dlg.default.isChecked(), log=lambda s: None)
        self._w.done.connect(self._added); self._w.start()

    def _added(self, res, err):
        QtWidgets.QApplication.restoreOverrideCursor(); self.b_add.setEnabled(True)
        if err:
            QtWidgets.QMessageBox.warning(self, "Could not add model", str(err)); self.app.status("")
            return
        self.app.cfg = C.load(); self.app.status(f"Model saved in {res}", "ok"); self.app.refresh_all()

    def set_default(self):
        m = self._selected()
        if m:
            self.app.cfg.default_model = m["name"]; self.app.cfg.save(); self.app.refresh_all()

    def export(self):
        m = self._selected()
        if not m:
            return
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Save a copy of the model into…", last_dir("export"))
        if not d:
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "Name", "Folder name for the copy:", text=m["name"])
        if not ok or not name.strip():
            return
        dest = Path(d) / name.strip()
        if dest.exists():
            QtWidgets.QMessageBox.warning(self, "Exists", f"{dest} already exists."); return
        remember_dir("export", d)
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            shutil.copytree(m["path"], dest, symlinks=False)
            cardp = dest / "MODEL_CARD.json"
            cd = json.loads(cardp.read_text(encoding="utf-8")); cd["name"] = name.strip(); cardp.write_text(json.dumps(cd, indent=2))
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.app.status(f"Copied to {dest} — on another computer use Add weights… → Folder…", "ok")

    def rename(self):
        m = self._selected()
        if not m:
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "Rename model", "New name:", text=m["name"])
        name = name.strip()
        if not ok or not name or name == m["name"]:
            return
        new = Path(m["path"]).parent / name
        if new.exists():
            QtWidgets.QMessageBox.warning(self, "Exists", f"{new} already exists."); return
        Path(m["path"]).rename(new)
        cd = json.loads((new / "MODEL_CARD.json").read_text(encoding="utf-8")); cd["name"] = name
        (new / "MODEL_CARD.json").write_text(json.dumps(cd, indent=2))
        if self.app.cfg.default_model == m["name"]:
            self.app.cfg.default_model = name; self.app.cfg.save()
        self.app.refresh_all()

    def delete(self):
        m = self._selected()
        if not m:
            return
        r = QtWidgets.QMessageBox.warning(self, "Delete model", f"Delete the model folder\n{m['path']}?\n\n"
                                          "This removes its copy of the weights (linked originals are not touched).",
                                          QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
        if r != QtWidgets.QMessageBox.Yes:
            return
        shutil.rmtree(m["path"])
        if self.app.cfg.default_model == m["name"]:
            self.app.cfg.default_model = ""; self.app.cfg.save()
        self.app.refresh_all()


# ---------------------------------------------------------------------------------------------------------------
class TrainPage(Page):
    def __init__(self, app):
        super().__init__(app)
        self.lay.addWidget(header("Train", "Step 1 turns labeled folders (<animal>/<MRI>.nrrd + .seg.nrrd) into a training "
                                           "dataset. Step 2 trains nnU-Net on it. All scans of one animal stay in the same fold."))
        c, l = card("Step 1 · Training data")
        l.addWidget(QtWidgets.QLabel("Folders with labeled scans (searched recursively; irrelevant files are ignored):"))
        self.roots = PathList("train_roots"); l.addWidget(self.roots)
        l.addWidget(QtWidgets.QLabel("Optional: existing nnU-Net datasets to include as-is (e.g. Dataset001_GraftSeg):"))
        self.extra = PathList("train_nnunet", placeholder="e.g. …/nnUNet_raw/Dataset001_GraftSeg"); l.addWidget(self.extra)
        form = QtWidgets.QFormLayout(); l.addLayout(form)
        idrow = QtWidgets.QHBoxLayout()
        self.ds_id = QtWidgets.QSpinBox(); self.ds_id.setRange(1, 999); self.ds_id.setFixedWidth(70)
        self.ds_name = QtWidgets.QLineEdit("GraftSeg")
        idrow.addWidget(QtWidgets.QLabel("Dataset")); idrow.addWidget(self.ds_id); idrow.addWidget(self.ds_name, 1)
        form.addRow("New dataset", idrow)
        self.fluid = QtWidgets.QCheckBox("Also learn fluid (Fluid / Ventricle / CSF segments as a 2nd class)")
        self.nfolds = QtWidgets.QSpinBox(); self.nfolds.setRange(2, 10); self.nfolds.setValue(5); self.nfolds.setFixedWidth(70)
        form.addRow("", self.fluid); form.addRow("Folds", self.nfolds)
        row = QtWidgets.QHBoxLayout(); l.addLayout(row)
        b_check = button("Check data", tip="List every segmentation found and which segment counts as graft; writes nothing")
        b_build = button("Build dataset", "primary")
        b_check.clicked.connect(self.check); b_build.clicked.connect(self.build)
        row.addWidget(b_check); row.addWidget(b_build); row.addStretch()
        self.table = QtWidgets.QTableWidget(0, 6); self.table.setHorizontalHeaderLabels(["Case", "Status", "Graft segment(s)", "Ignored", "Not recognised", "Note / file"])
        self.table.verticalHeader().setVisible(False); self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers); self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(200); self.table.setVisible(False); l.addWidget(self.table)
        self.lay.addWidget(c)

        c, l = card("Step 2 · Train")
        form = QtWidgets.QFormLayout(); l.addLayout(form)
        self.ds_pick = QtWidgets.QComboBox(); form.addRow("Dataset", self.ds_pick)
        self.epochs = QtWidgets.QComboBox()
        for e in (250, 500, 750, 1000, 2000):
            self.epochs.addItem(f"{e} epochs" + ("  (nnU-Net default)" if e == 1000 else ""), e)
        self.epochs.setCurrentIndex(3); form.addRow("Length", self.epochs)
        self.planner = QtWidgets.QComboBox()
        for label, key in [("Default U-Net (what the current model uses)", "default"), ("ResEnc M (~9 GB VRAM)", "resenc-m"),
                           ("ResEnc L (~24 GB VRAM, nnU-Net's recommendation)", "resenc-l"), ("ResEnc XL (~40 GB VRAM)", "resenc-xl")]:
            self.planner.addItem(label, key)
        form.addRow("Network", self.planner)
        frow = QtWidgets.QHBoxLayout()
        self.fold_boxes = [QtWidgets.QCheckBox(str(i)) for i in range(5)]
        self.fold_boxes[0].setChecked(True)
        for b in self.fold_boxes:
            frow.addWidget(b)
        frow.addStretch(); form.addRow("Folds", frow)
        self.tdev = QtWidgets.QComboBox(); self.tdev.setFixedWidth(220)
        for label, key in [("NVIDIA GPU (cuda)", "cuda"), ("Apple silicon GPU (mps, slow)", "mps"), ("CPU (very slow, testing only)", "cpu")]:
            self.tdev.addItem(label, key)
        if sys.platform == "darwin":
            self.tdev.setCurrentIndex(1)
        form.addRow("Device", self.tdev)
        self.reg_name = QtWidgets.QLineEdit(); self.reg_name.setPlaceholderText("e.g. graft_v2 — leave empty to register later on the Models page")
        form.addRow("Save model as", self.reg_name)
        self.restart = QtWidgets.QCheckBox("Start over (ignore existing checkpoints). Off = continue where it stopped.")
        form.addRow("", self.restart)
        row = QtWidgets.QHBoxLayout(); l.addLayout(row)
        b_train = button("Start training", "primary"); b_train.clicked.connect(self.train); row.addWidget(b_train)
        b_stat = button("Refresh progress"); b_stat.clicked.connect(self.refresh_status); row.addWidget(b_stat)
        row.addStretch()
        note = QtWidgets.QLabel(f"<span style='color:{muted()}'>Stop is safe: nnU-Net saves a checkpoint every 50 epochs and "
                                "checkpoint_best.pth whenever validation improves; Start again continues from there.</span>")
        note.setWordWrap(True); l.addWidget(note)
        self.status_tbl = QtWidgets.QTableWidget(0, 6)
        self.status_tbl.setHorizontalHeaderLabels(["Run", "Fold", "Epoch", "Best EMA Dice", "At epoch", "Checkpoints"])
        self.status_tbl.verticalHeader().setVisible(False); self.status_tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.status_tbl.horizontalHeader().setStretchLastSection(True); self.status_tbl.setMinimumHeight(120)
        l.addWidget(self.status_tbl)
        self.lay.addWidget(c)
        c, l = card("Log")
        self.runner = Runner(); self.runner.setMinimumHeight(260); l.addWidget(self.runner)
        self.runner.finished.connect(self._finished)
        self.lay.addWidget(c, 1)
        self.roots.set_paths(settings().value("train/roots", []) or [])
        self.extra.set_paths(settings().value("train/extra", []) or [])
        self._mode = ""
        self._timer = QtCore.QTimer(self); self._timer.timeout.connect(self.refresh_status); self._timer.start(60000)

    def refresh(self):
        cfg = self.app.cfg
        self.ds_pick.clear()
        try:
            dss = sorted(cfg.raw.glob("Dataset*_*")) if cfg.raw.exists() else []
        except RuntimeError:
            return
        ids = []
        for d in dss:
            try:
                i = int(d.name[7:10]); ids.append(i)
                n = json.loads((d / "dataset.json").read_text(encoding="utf-8")).get("numTraining", "?") if (d / "dataset.json").exists() else "?"
                self.ds_pick.addItem(f"{d.name}  ({n} cases)", i)
            except ValueError:
                pass
        self.ds_id.setValue((max(ids) + 1) if ids else 1)
        if self.ds_pick.count():
            self.ds_pick.setCurrentIndex(self.ds_pick.count() - 1)
        self.refresh_status()

    def check(self):
        if not self.roots.paths():
            QtWidgets.QMessageBox.information(self, "No folders", "Add folders with labeled scans first."); return
        self._save()
        self._report = Path(tempfile.gettempdir()) / "graftseg_scan_report.csv"
        args = ["scan", *self.roots.paths(), "--report", str(self._report)]
        if self.fluid.isChecked():
            args.append("--with-fluid")
        self._mode = "check"
        self.runner.start(args, "Checking data")

    def build(self):
        if not self.roots.paths() and not self.extra.paths():
            QtWidgets.QMessageBox.information(self, "No folders", "Add folders with labeled scans first."); return
        self._save()
        args = ["build", *self.roots.paths(), "--id", str(self.ds_id.value()), "--name", self.ds_name.text().strip() or "GraftSeg",
                "--folds", str(self.nfolds.value())]
        for p in self.extra.paths():
            args += ["--include-nnunet", p]
        if self.fluid.isChecked():
            args.append("--with-fluid")
        self._mode = "build"
        self.runner.start(args, "Building dataset")

    def train(self):
        if self.ds_pick.currentData() is None:
            QtWidgets.QMessageBox.information(self, "No dataset", "Build a dataset first (step 1)."); return
        folds = [str(i) for i, b in enumerate(self.fold_boxes) if b.isChecked()]
        if not folds:
            QtWidgets.QMessageBox.information(self, "No folds", "Tick at least one fold."); return
        args = ["train", "--dataset", str(self.ds_pick.currentData()), "--folds", ",".join(folds),
                "--epochs", str(self.epochs.currentData()), "--planner", self.planner.currentData(),
                "--device", self.tdev.currentData()]
        if self.reg_name.text().strip():
            args += ["--register-as", self.reg_name.text().strip()]
        if self.restart.isChecked():
            args.append("--restart")
        self._mode = "train"
        self.runner.start(args, "Training")

    def _save(self):
        settings().setValue("train/roots", self.roots.paths()); settings().setValue("train/extra", self.extra.paths())

    def _finished(self, code, _out):
        if self._mode == "check" and code == 0 and self._report.exists():
            with open(self._report, encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.table.setVisible(True); self.table.setRowCount(len(rows))
            for i, r in enumerate(rows):
                vals = [r["case_id"] or r["animal"], r["status"], r["graft_segments"], r["ignored_segments"],
                        r["unknown_segments"], (r["reason"] + "  " if r["reason"] else "") + r["seg_path"]]
                for j, v in enumerate(vals):
                    it = QtWidgets.QTableWidgetItem(v)
                    if j == 1:
                        it.setForeground(QtGui.QColor(GOOD if v == "ok" else BAD))
                    if j == 4 and v:
                        it.setForeground(QtGui.QColor(WARN))
                    self.table.setItem(i, j, it)
            self.table.resizeColumnsToContents()
        if self._mode in ("build", "train"):
            self.app.refresh_all()

    def refresh_status(self):
        if self.ds_pick.currentData() is None:
            return
        from .train import status
        try:
            rows = status(self.app.cfg, int(self.ds_pick.currentData()))
        except Exception:
            rows = []
        self.status_tbl.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for j, k in enumerate(["run", "fold", "last_epoch", "best_ema_dice", "best_epoch", "checkpoints"]):
                self.status_tbl.setItem(i, j, QtWidgets.QTableWidgetItem("" if r[k] is None else str(r[k])))
        self.status_tbl.resizeColumnsToContents()


# ---------------------------------------------------------------------------------------------------------------
class SettingsPage(Page):
    def __init__(self, app):
        super().__init__(app)
        self.lay.addWidget(header("Settings", f"Saved in {C.CONFIG_PATH}. The nnUNet_* environment variables are set "
                                              "automatically from here — no exports needed."))
        c, l = card("Workspace")
        l.addWidget(QtWidgets.QLabel("One folder that holds everything (nnUNet_raw, nnUNet_preprocessed, nnUNet_results, models):"))
        row = QtWidgets.QHBoxLayout(); l.addLayout(row)
        self.ws = QtWidgets.QLineEdit(); self.ws.setPlaceholderText("/media/pasca/Extreme SSD/MRI NeuralNetwork")
        b = button("Browse…"); b.clicked.connect(lambda: self._browse(self.ws)); row.addWidget(self.ws, 1); row.addWidget(b)
        self.adv = QtWidgets.QCheckBox("My nnU-Net folders are somewhere else (set each one)"); l.addWidget(self.adv)
        self.adv_box = QtWidgets.QWidget(); form = QtWidgets.QFormLayout(self.adv_box); form.setContentsMargins(0, 0, 0, 0)
        self.fields = {}
        for key, label in [("nnunet_raw", "nnUNet_raw"), ("nnunet_preprocessed", "nnUNet_preprocessed"),
                           ("nnunet_results", "nnUNet_results"), ("models_dir", "Models folder")]:
            e = QtWidgets.QLineEdit(); e.setPlaceholderText("(inside the workspace)")
            bb = button("…"); bb.clicked.connect(lambda _=0, e=e: self._browse(e))
            r = QtWidgets.QHBoxLayout(); r.addWidget(e, 1); r.addWidget(bb); form.addRow(label, r); self.fields[key] = e
        l.addWidget(self.adv_box); self.adv.toggled.connect(self.adv_box.setVisible); self.adv_box.setVisible(False)
        form2 = QtWidgets.QFormLayout(); l.addLayout(form2)
        self.device = QtWidgets.QComboBox(); self.device.addItems(["auto", "cuda", "mps", "cpu"]); self.device.setFixedWidth(160)
        self.device.setToolTip("cuda = NVIDIA GPU · mps = Apple-silicon GPU · auto picks the best available")
        form2.addRow("Device", self.device)
        sv = button("Save settings", "primary"); sv.clicked.connect(self.save); l.addWidget(sv, 0, Qt.AlignLeft)
        self.lay.addWidget(c)
        c, l = card("Appearance")
        form3 = QtWidgets.QFormLayout(); l.addLayout(form3)
        self.theme = QtWidgets.QComboBox(); self.theme.setFixedWidth(220)
        for label, key in [("Follow system", "system"), ("Light", "light"), ("Dark", "dark")]:
            self.theme.addItem(label, key)
        self.theme.setCurrentIndex(max(0, self.theme.findData(str(settings().value("ui/theme", "system")))))
        self.theme.currentIndexChanged.connect(lambda _: (settings().setValue("ui/theme", self.theme.currentData()),
                                                          self.app.set_theme(self.theme.currentData())))
        form3.addRow("Theme", self.theme)
        srow = QtWidgets.QHBoxLayout()
        self.scale = QtWidgets.QComboBox(); self.scale.setFixedWidth(220)
        for label, v in [("Automatic (from the monitor)", ""), ("Compact 85 %", "0.85"), ("100 %", "1"), ("115 %", "1.15"),
                         ("125 %", "1.25"), ("150 %", "1.5"), ("175 %", "1.75"), ("200 %", "2")]:
            self.scale.addItem(label, v)
        self.scale.setCurrentIndex(max(0, self.scale.findData(str(settings().value("ui/scale", "")))))
        self.scale.currentIndexChanged.connect(self._scale_changed)
        self.restart_btn = button("Restart now", "primary"); self.restart_btn.setVisible(False)
        self.restart_btn.clicked.connect(self._restart)
        srow.addWidget(self.scale); srow.addWidget(self.restart_btn); srow.addStretch()
        form3.addRow("Interface size", srow)
        note = QtWidgets.QLabel("High-resolution monitors are scaled automatically. Use Interface size if text looks too "
                                "small or too large (applies after a restart).")
        note.setObjectName("sub"); note.setWordWrap(True); l.addWidget(note)
        self.lay.addWidget(c)
        c, l = card("Using nnU-Net commands in a terminal")
        self.env = QtWidgets.QPlainTextEdit(); self.env.setObjectName("log"); self.env.setReadOnly(True); self.env.setMaximumHeight(110)
        l.addWidget(self.env)
        cp = button("Copy"); cp.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(self.env.toPlainText()))
        l.addWidget(cp, 0, Qt.AlignLeft)
        self.lay.addWidget(c)
        self.lay.addStretch()

    def _scale_changed(self, _i):
        settings().setValue("ui/scale", self.scale.currentData())
        self.restart_btn.setVisible(True)

    def _restart(self):
        if not self.app.close():
            return
        QtCore.QProcess.startDetached(sys.executable, ["-m", "graftseg"])

    def _browse(self, edit: QtWidgets.QLineEdit):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose folder", edit.text() or last_dir("settings"))
        if d:
            edit.setText(d)

    def refresh(self):
        cfg = self.app.cfg
        self.ws.setText(cfg.workspace)
        for k, e in self.fields.items():
            e.setText(getattr(cfg, k))
        self.adv.setChecked(any(getattr(cfg, k) for k in self.fields))
        self.device.setCurrentText(cfg.device or "auto")
        try:
            self.env.setPlainText("\n".join(f'export {k}="{v}"' for k, v in cfg.nnunet_env().items()))
        except RuntimeError:
            self.env.setPlainText("(set the workspace first)")

    def save(self):
        cfg = self.app.cfg
        cfg.workspace = self.ws.text().strip()
        for k, e in self.fields.items():
            setattr(cfg, k, e.text().strip() if self.adv.isChecked() else "")
        cfg.device = self.device.currentText()
        try:
            for p in (cfg.raw, cfg.preprocessed, cfg.results, cfg.models):
                p.mkdir(parents=True, exist_ok=True)
        except (RuntimeError, OSError) as e:
            QtWidgets.QMessageBox.warning(self, "Cannot use that folder", str(e)); return
        cfg.save()
        self.app.status("Settings saved.", "ok"); self.app.refresh_all()


# ---------------------------------------------------------------------------------------------------------------
class MainWindow(QtWidgets.QMainWindow):
    PAGES = ["Home", "Segment", "Review", "Models", "Train", "Settings"]
    ICONS = {"Home": "home", "Segment": "segment", "Review": "review", "Models": "models", "Train": "train",
             "Settings": "settings"}
    COMPACT_BELOW = 1180  # window width (logical px) below which the sidebar shows icons only

    def __init__(self):
        super().__init__()
        self.cfg = C.load()
        self.setWindowTitle("GraftSeg")
        self.setMinimumSize(900, 580)
        central = QtWidgets.QWidget(); self.setCentralWidget(central)
        h = QtWidgets.QHBoxLayout(central); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)
        self.side = QtWidgets.QWidget(); self.side.setObjectName("sidebar"); self.side.setAttribute(Qt.WA_StyledBackground, True)
        sl = QtWidgets.QVBoxLayout(self.side); sl.setContentsMargins(0, 14, 0, 10); sl.setSpacing(0)
        self.brand = QtWidgets.QLabel("GraftSeg"); self.brand.setObjectName("brand"); self.brand.setContentsMargins(18, 4, 18, 0)
        self.brand_sub = QtWidgets.QLabel("MRI graft segmentation"); self.brand_sub.setObjectName("brandsub")
        self.brand_sub.setContentsMargins(18, 0, 18, 12)
        sl.addWidget(self.brand); sl.addWidget(self.brand_sub)
        self.nav = QtWidgets.QListWidget(); self.nav.setObjectName("nav"); self.nav.setIconSize(QtCore.QSize(20, 20))
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for p in self.PAGES:
            it = QtWidgets.QListWidgetItem(icon(self.ICONS[p], "#cbd5e1"), f"  {p}"); it.setToolTip(p)
            self.nav.addItem(it)
        sl.addWidget(self.nav, 1)
        self.ver = QtWidgets.QLabel("v0.2"); self.ver.setStyleSheet("color:#475569;padding:6px 18px;"); sl.addWidget(self.ver)
        h.addWidget(self.side)
        self.stack = QtWidgets.QStackedWidget(); h.addWidget(self.stack, 1)
        self.home, self.segment, self.review = HomePage(self), SegmentPage(self), ReviewPage(self)
        self.models, self.trainp, self.settings_page = ModelsPage(self), TrainPage(self), SettingsPage(self)
        self.pages = [self.home, self.segment, self.review, self.models, self.trainp, self.settings_page]
        for p in self.pages:
            self.stack.addWidget(p)
        self.nav.currentRowChanged.connect(self._nav)
        self.toast = Toast(self)
        self._compact = None
        self._set_compact(False)
        fit_to_screen(self, settings().value("window/geometry"))
        self.refresh_all()
        self.nav.setCurrentRow(0)
        try:  # follow the OS light/dark switch while running
            QtGui.QGuiApplication.styleHints().colorSchemeChanged.connect(lambda *_: self.set_theme(None))
        except AttributeError:
            pass

    # -- layout --------------------------------------------------------------------------------------
    def _set_compact(self, compact: bool):
        if compact == self._compact:
            return
        self._compact = compact
        self.side.setFixedWidth(72 if compact else 220)
        self.brand.setText("GS" if compact else "GraftSeg")
        self.brand_sub.setVisible(not compact)
        for i, p in enumerate(self.PAGES):
            self.nav.item(i).setText("" if compact else f"  {p}")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._set_compact(self.width() < self.COMPACT_BELOW)
        if self.toast.isVisible():
            self.toast.reposition()

    def set_theme(self, mode: Optional[str]):
        mode = mode or str(settings().value("ui/theme", "system"))
        apply_theme(QtWidgets.QApplication.instance(), mode)
        self.review.glw.setBackground(T["viewer"])
        self.refresh_all()

    # -- navigation ----------------------------------------------------------------------------------
    def _nav(self, row: int):
        if self.stack.currentWidget() is self.review and row != 2 and not self.review.confirm_leave():
            self.nav.blockSignals(True); self.nav.setCurrentRow(2); self.nav.blockSignals(False); return
        self.stack.setCurrentIndex(row)
        self.pages[row].refresh()

    def go(self, page: str):
        self.nav.setCurrentRow(self.PAGES.index(page))

    def status(self, text: str, kind: str = "info"):
        if text:
            self.toast.show_message(text, kind)

    def refresh_all(self):
        self.cfg = C.load()
        for p in self.pages:
            try:
                p.refresh()
            except Exception as e:  # noqa: BLE001
                self.status(f"{type(p).__name__}: {e}", "error")

    def closeEvent(self, e):
        if not self.review.confirm_leave():
            e.ignore(); return
        busy = [p for p in (self.segment, self.trainp) if p.runner.busy]
        if busy and QtWidgets.QMessageBox.question(self, "Job running", "A job is still running. Stop it and quit?") \
                != QtWidgets.QMessageBox.Yes:
            e.ignore(); return
        for p in busy:
            p.runner.stop()
            p.runner.proc.waitForFinished(15000)
        self.home._w.wait(10000)
        settings().setValue("window/geometry", self.saveGeometry())
        e.accept()


def make_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        scale = settings().value("ui/scale", "")
        if scale and "QT_SCALE_FACTOR" not in os.environ:   # "Interface size" setting (applies at start-up)
            os.environ["QT_SCALE_FACTOR"] = str(scale)
        QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("GraftSeg")
    app.setApplicationDisplayName("GraftSeg")
    app.setDesktopFileName("graftseg")
    app.setWindowIcon(icon("segment", "#6366f1", 64))
    app.setStyle("Fusion")
    apply_theme(app, str(settings().value("ui/theme", "system")))
    return app


def launch(page: str = "home", review_paths: Optional[list[str]] = None):
    app = make_app()
    w = MainWindow()
    if not C.CONFIG_PATH.exists():
        w.go("Home")
    if review_paths:
        w.review.load_paths(review_paths)
        w.go("Review")
    elif page.lower() == "review":
        w.go("Review")
    w.show()
    return app.exec()
