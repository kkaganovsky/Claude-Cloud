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

# ---------------------------------------------------------------------------------------------------------------
# Look
ACCENT, ACCENT_DARK, ACCENT_SOFT = "#4f46e5", "#4338ca", "#eef2ff"
GOOD, WARN, BAD = "#16a34a", "#d97706", "#dc2626"
BG, CARD, LINE, TEXT, MUTED = "#f5f7fb", "#ffffff", "#e4e7ee", "#111827", "#6b7280"
SIDEBAR, SIDEBAR_TEXT = "#0f172a", "#cbd5e1"

QSS = f"""
* {{ font-family: "Inter", "Segoe UI", "Noto Sans", "Ubuntu", "Helvetica Neue", Arial, sans-serif; font-size: 10pt; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget {{ color: {TEXT}; }}
QWidget#content {{ background: {BG}; }}
QToolTip {{ background: #111827; color: white; border: none; padding: 6px 8px; border-radius: 6px; }}
QListWidget#nav {{ background: {SIDEBAR}; border: none; padding: 10px 8px; outline: 0; }}
QListWidget#nav::item {{ color: {SIDEBAR_TEXT}; padding: 11px 14px; margin: 2px 0; border-radius: 9px; }}
QListWidget#nav::item:hover {{ background: #1e293b; color: white; }}
QListWidget#nav::item:selected {{ background: {ACCENT}; color: white; font-weight: 600; }}
QLabel#brand {{ background: {SIDEBAR}; color: white; font-size: 15pt; font-weight: 700; padding: 18px 18px 6px 18px; }}
QLabel#brandsub {{ background: {SIDEBAR}; color: #64748b; padding: 0 18px 14px 18px; font-size: 9pt; }}
QLabel#h1 {{ font-size: 18pt; font-weight: 700; color: {TEXT}; }}
QLabel#sub {{ color: {MUTED}; }}
QLabel#cardtitle {{ color: {MUTED}; font-size: 9pt; font-weight: 600; letter-spacing: 0.5px; }}
QLabel#big {{ font-size: 14pt; font-weight: 600; }}
QFrame#card {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 14px; }}
QFrame#banner {{ background: {ACCENT_SOFT}; border: 1px solid #c7d2fe; border-radius: 12px; }}
QPushButton {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 9px; padding: 8px 14px; }}
QPushButton:hover {{ border-color: {ACCENT}; background: {ACCENT_SOFT}; }}
QPushButton:pressed {{ background: #e0e7ff; }}
QPushButton:disabled {{ color: #9ca3af; background: #f3f4f6; }}
QPushButton:checked {{ background: {ACCENT}; color: white; border-color: {ACCENT_DARK}; }}
QPushButton[role="primary"] {{ background: {ACCENT}; color: white; border: 1px solid {ACCENT_DARK}; font-weight: 600; }}
QPushButton[role="primary"]:hover {{ background: {ACCENT_DARK}; }}
QPushButton[role="primary"]:disabled {{ background: #a5b4fc; border-color: #a5b4fc; }}
QPushButton[role="danger"] {{ color: {BAD}; }}
QPushButton[role="danger"]:hover {{ background: #fef2f2; border-color: {BAD}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox, QComboBox {{
    background: {CARD}; border: 1px solid {LINE}; border-radius: 8px; padding: 6px 8px; selection-background-color: {ACCENT}; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0; border: none; }}
QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {CARD}; border: 1px solid {LINE}; selection-background-color: {ACCENT_SOFT}; selection-color: {ACCENT_DARK}; }}
QPlainTextEdit#log {{ background: #0b1020; color: #d1d5db; font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; font-size: 9pt; border-radius: 10px; border: none; padding: 8px; }}
QTableWidget, QListWidget {{ background: {CARD}; alternate-background-color: #f9fafb; border: 1px solid {LINE}; border-radius: 10px;
    gridline-color: #f1f2f6; selection-background-color: {ACCENT_SOFT}; selection-color: {ACCENT_DARK}; outline: 0; }}
QListWidget#cases::item {{ padding: 7px 8px; border-radius: 6px; }}
QHeaderView::section {{ background: #f9fafb; color: {MUTED}; border: none; border-bottom: 1px solid {LINE}; padding: 7px 8px; font-weight: 600; }}
QProgressBar {{ background: #e5e7eb; border: none; border-radius: 5px; height: 10px; text-align: center; font-size: 8pt; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
QSlider::groove:horizontal {{ height: 5px; background: #e5e7eb; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: white; border: 2px solid {ACCENT}; width: 14px; height: 14px; margin: -6px 0; border-radius: 9px; }}
QCheckBox {{ spacing: 8px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #cbd5e1; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QStatusBar {{ background: {CARD}; color: {MUTED}; border-top: 1px solid {LINE}; }}
"""


def card(title: str = "") -> tuple[QtWidgets.QFrame, QtWidgets.QVBoxLayout]:
    f = QtWidgets.QFrame(); f.setObjectName("card")
    lay = QtWidgets.QVBoxLayout(f); lay.setContentsMargins(18, 16, 18, 16); lay.setSpacing(10)
    if title:
        t = QtWidgets.QLabel(title.upper()); t.setObjectName("cardtitle"); lay.addWidget(t)
    sh = QtWidgets.QGraphicsDropShadowEffect(blurRadius=18, xOffset=0, yOffset=2)
    sh.setColor(QtGui.QColor(15, 23, 42, 18)); f.setGraphicsEffect(sh)
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
        env.insert("PYTHONUNBUFFERED", "1")
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
        if self._partial.strip():
            self.out.append(self._partial); self.log.appendPlainText(self._partial)
        self.stop_btn.setEnabled(False); self.bar.setVisible(False)
        self.state.setText("Finished" if code == 0 else f"Stopped / failed (exit {code})")
        self.finished.emit(code, "\n".join(self.out))

    def stop(self):
        if self.busy:
            os.kill(int(self.proc.processId()), signal.SIGINT)
            self.state.setText("Stopping (finishing the current step)…")


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
        self.setWidget(inner)

    def refresh(self):
        pass


class HomePage(Page):
    def __init__(self, app):
        super().__init__(app)
        self.lay.addWidget(header("Welcome", "Segment organoid grafts on T2 MRI, check them, and keep the model up to date."))
        self.banner = QtWidgets.QFrame(); self.banner.setObjectName("banner")
        bl = QtWidgets.QHBoxLayout(self.banner); bl.setContentsMargins(16, 12, 16, 12)
        self.banner_text = QtWidgets.QLabel(); self.banner_text.setWordWrap(True); bl.addWidget(self.banner_text, 1)
        self.banner_btn = button("Open Settings", "primary"); bl.addWidget(self.banner_btn)
        self.banner_btn.clicked.connect(lambda: app.go("Settings"))
        self.lay.addWidget(self.banner)
        grid = QtWidgets.QGridLayout(); grid.setSpacing(16); self.lay.addLayout(grid)
        c1, l1 = card("Workspace"); self.ws = QtWidgets.QLabel(); self.ws.setWordWrap(True); l1.addWidget(self.ws)
        c2, l2 = card("GPU"); self.gpu = QtWidgets.QLabel("Checking…"); self.gpu.setWordWrap(True); l2.addWidget(self.gpu)
        c3, l3 = card("Default model"); self.model = QtWidgets.QLabel(); self.model.setWordWrap(True); l3.addWidget(self.model)
        mb = button("Manage models"); mb.clicked.connect(lambda: app.go("Models")); l3.addWidget(mb, 0, Qt.AlignLeft)
        for i, (c, l) in enumerate(((c1, l1), (c2, l2), (c3, l3))):
            l.addStretch(); grid.addWidget(c, 0, i)
        c4, l4 = card("What do you want to do?")
        steps = [("1  Segment new scans", "Pick a folder of DICOM/NRRD scans. Each gets PA_01_R2M.seg.nrrd next to it.", "Segment"),
                 ("2  Review and correct", "Paint / erase the graft, save. Reviewed files can be used for training.", "Review"),
                 ("3  Retrain", "Build a dataset from labeled folders and train on the GPU.", "Train")]
        for t, d, page in steps:
            row = QtWidgets.QHBoxLayout()
            txt = QtWidgets.QLabel(f"<b>{t}</b><br><span style='color:{MUTED}'>{d}</span>"); txt.setWordWrap(True)
            row.addWidget(txt, 1)
            b = button(f"Go to {page}", "primary" if page == "Segment" else ""); b.clicked.connect(lambda _=0, p=page: app.go(p))
            row.addWidget(b); l4.addLayout(row)
        self.lay.addWidget(c4)
        self.lay.addStretch()
        self._w = Worker(gpu_info); self._w.done.connect(lambda r, e: self.gpu.setText(r or str(e))); self._w.start()

    def refresh(self):
        cfg = self.app.cfg
        configured = C.CONFIG_PATH.exists()
        self.banner.setVisible(not configured)
        self.banner_text.setText("<b>First time here?</b> Choose the workspace folder (where nnUNet_raw / "
                                 "nnUNet_results live, e.g. <i>/media/pasca/Extreme SSD/MRI NeuralNetwork</i>).")
        try:
            self.ws.setText(f"<b>{cfg.workspace or cfg.results.parent}</b><br><span style='color:{MUTED}'>models: {cfg.models}</span>")
        except RuntimeError:
            self.ws.setText("<span style='color:#dc2626'>Not set</span>")
        try:
            d, cd = M.resolve(cfg, None)
            dice = cd.get("best_ema_pseudo_dice")
            self.model.setText(f"<span style='font-size:13pt;font-weight:600'>{cd.get('name', d.name)}</span><br>"
                               f"<span style='color:{MUTED}'>epoch {cd.get('epoch')} · EMA Dice {dice} · "
                               f"{cd.get('checkpoint')} · folds {cd.get('folds')}</span>")
        except Exception:
            self.model.setText(f"<span style='color:{WARN}'>No model yet.</span> Add your trained weights on the Models page.")


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
        self.device = QtWidgets.QComboBox(); self.device.addItems(["auto", "cuda", "cpu"]); self.device.setFixedWidth(160)
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
        self.runner = Runner(); self.runner.setMinimumHeight(260); l.addWidget(self.runner)
        self.lay.addWidget(c, 1)
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
        if self._dry or code != 0:
            return
        segs = []
        for ln in out.splitlines():
            if ln.startswith("Summary: "):
                try:
                    with open(ln[len("Summary: "):].strip()) as f:
                        segs = [r["seg_nrrd"] for r in csv.DictReader(f) if r.get("seg_nrrd")]
                except OSError:
                    pass
        if segs and self.review_after.isChecked():
            self.app.review.load_paths(segs)
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
        outer = QtWidgets.QHBoxLayout(self); outer.setContentsMargins(16, 16, 16, 16); outer.setSpacing(12)

        # left: case list
        left, ll = card("Cases")
        left.setMaximumWidth(300); left.setMinimumWidth(230)
        ob = button("Folder…", tip="Load every .seg.nrrd under a folder"); ob.clicked.connect(self.open_folder)
        of = button("Files…"); of.clicked.connect(self.open_files)
        r = QtWidgets.QHBoxLayout(); r.addWidget(ob); r.addWidget(of); ll.addLayout(r)
        self.only_pred = QtWidgets.QCheckBox("Only not yet reviewed"); self.only_pred.toggled.connect(self._fill_list)
        ll.addWidget(self.only_pred)
        self.list = QtWidgets.QListWidget(); self.list.setObjectName("cases")
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.list.setTextElideMode(Qt.ElideMiddle)
        self.list.currentRowChanged.connect(self._row_changed); ll.addWidget(self.list, 1)
        outer.addWidget(left)

        # center: viewer
        mid = QtWidgets.QVBoxLayout(); outer.addLayout(mid, 1)
        tools = QtWidgets.QHBoxLayout(); mid.addLayout(tools)
        self.btn_paint = button("Paint  (P)"); self.btn_erase = button("Erase  (E)")
        for b in (self.btn_paint, self.btn_erase):
            b.setCheckable(True)
        self.btn_paint.setChecked(True)
        grp = QtWidgets.QButtonGroup(self); grp.setExclusive(True); grp.addButton(self.btn_paint); grp.addButton(self.btn_erase)
        self.btn_paint.toggled.connect(lambda on: setattr(self, "paint_mode", on))
        tools.addWidget(self.btn_paint); tools.addWidget(self.btn_erase)
        tools.addWidget(QtWidgets.QLabel("  Brush"))
        self.brush = QtWidgets.QSlider(Qt.Horizontal); self.brush.setRange(1, 30); self.brush.setValue(4); self.brush.setFixedWidth(120)
        self.brush_lbl = QtWidgets.QLabel("4 px"); self.brush.valueChanged.connect(lambda v: (self.brush_lbl.setText(f"{v} px"), self._cursor_update()))
        tools.addWidget(self.brush); tools.addWidget(self.brush_lbl)
        self.ball = QtWidgets.QCheckBox("3D brush"); self.ball.setToolTip("Paint a ball that reaches the neighbouring slices (key 3)")
        tools.addWidget(self.ball)
        tools.addWidget(QtWidgets.QLabel("  Overlay"))
        self.opacity = QtWidgets.QSlider(Qt.Horizontal); self.opacity.setRange(0, 100); self.opacity.setValue(45); self.opacity.setFixedWidth(100)
        self.opacity.valueChanged.connect(lambda v: self.ov.setOpacity(v / 100)); tools.addWidget(self.opacity)
        tools.addStretch()
        self.view_btns = []
        for k, name in enumerate(("Acquired plane", "Plane 2", "Plane 3")):
            b = button(name); b.setCheckable(True); b.clicked.connect(lambda _=0, k=k: self.set_axis_order(k))
            self.view_btns.append(b); tools.addWidget(b)

        self.glw = pg.GraphicsLayoutWidget(); self.glw.setBackground("#05070d")
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
        right.setMaximumWidth(270); right.setMinimumWidth(230)
        self.info = QtWidgets.QLabel("Open a folder of segmentations, or run Segment first."); self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.RichText); rl.addWidget(self.info)
        self.b_save = button("Save  (Ctrl+S)", "primary"); self.b_next = button("Save + next  (N)", "primary")
        self.b_prev = button("◀  Previous  (B)"); self.b_undo = button("Undo  (Ctrl+Z)")
        self.b_fill = button("Fill holes in slice  (F)"); self.b_jump = button("Go to graft  (J)")
        self.b_revert = button("Revert to saved", "danger")
        for b in (self.b_save, self.b_next, self.b_prev, self.b_undo, self.b_fill, self.b_jump, self.b_revert):
            rl.addWidget(b)
        help_ = QtWidgets.QLabel(
            f"<span style='color:{MUTED}'><b>Mouse</b><br>left-drag: paint (or erase in Erase mode)<br>right-drag: erase<br>"
            "wheel: slice · Ctrl+wheel: zoom<br>middle-drag: pan<br><b>Keys</b> ↑/↓ slice · [ ] brush · 3 3D brush<br>"
            "Contrast: drag the histogram bar.</span>")
        help_.setWordWrap(True); rl.addWidget(help_); rl.addStretch()
        outer.addWidget(right)
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
        colors = {"predicted": WARN, "reviewed": GOOD, "human": "#2563eb"}
        for p in self._shown:
            st = self._statuses.get(p, "?")
            it = QtWidgets.QListWidgetItem(f"{Path(p).name[:-9]}\n{st}")
            it.setForeground(QtGui.QColor(TEXT)); it.setToolTip(p)
            it.setData(Qt.UserRole, p)
            it.setIcon(self._dot(colors.get(st, MUTED)))
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
        color = {"predicted": WARN, "reviewed": GOOD}.get(st, "#2563eb")
        self.info.setText(
            f"<div style='font-size:13pt;font-weight:600'>{c.name}</div>"
            f"<div style='color:{MUTED}'>case {self.idx + 1} of {len(self._shown)}</div><br>"
            f"{chip(st, color)} {chip('edited – not saved', BAD) if c.edited else ''}<br><br>"
            f"<b>Graft</b> {c.volume_mm3():.2f} mm³<br>"
            f"<span style='color:{MUTED}'>{int(c.mask.sum())} voxels · voxel {' × '.join(f'{s:.3f}' for s in c.spacing_zyx[::-1])} mm<br>"
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
        self.app.status(f"Saved {p}")

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
        self.app.cfg = C.load(); self.app.status(f"Model saved in {res}"); self.app.refresh_all()

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
            cd = json.loads(cardp.read_text()); cd["name"] = name.strip(); cardp.write_text(json.dumps(cd, indent=2))
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.app.status(f"Copied to {dest}  (add it on another computer with “Add weights… → Folder…”)")

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
        cd = json.loads((new / "MODEL_CARD.json").read_text()); cd["name"] = name
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
        self.reg_name = QtWidgets.QLineEdit(); self.reg_name.setPlaceholderText("e.g. graft_v2 — leave empty to register later on the Models page")
        form.addRow("Save model as", self.reg_name)
        self.restart = QtWidgets.QCheckBox("Start over (ignore existing checkpoints). Off = continue where it stopped.")
        form.addRow("", self.restart)
        row = QtWidgets.QHBoxLayout(); l.addLayout(row)
        b_train = button("Start training", "primary"); b_train.clicked.connect(self.train); row.addWidget(b_train)
        b_stat = button("Refresh progress"); b_stat.clicked.connect(self.refresh_status); row.addWidget(b_stat)
        row.addStretch()
        note = QtWidgets.QLabel(f"<span style='color:{MUTED}'>Stop is safe: nnU-Net saves a checkpoint every 50 epochs and "
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
                n = json.loads((d / "dataset.json").read_text()).get("numTraining", "?") if (d / "dataset.json").exists() else "?"
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
                "--epochs", str(self.epochs.currentData()), "--planner", self.planner.currentData()]
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
            with open(self._report) as f:
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
        self.device = QtWidgets.QComboBox(); self.device.addItems(["auto", "cuda", "cpu"]); self.device.setFixedWidth(160)
        form2.addRow("Device", self.device)
        sv = button("Save settings", "primary"); sv.clicked.connect(self.save); l.addWidget(sv, 0, Qt.AlignLeft)
        self.lay.addWidget(c)
        c, l = card("Using nnU-Net commands in a terminal")
        self.env = QtWidgets.QPlainTextEdit(); self.env.setObjectName("log"); self.env.setReadOnly(True); self.env.setMaximumHeight(110)
        l.addWidget(self.env)
        cp = button("Copy"); cp.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(self.env.toPlainText()))
        l.addWidget(cp, 0, Qt.AlignLeft)
        self.lay.addWidget(c)
        self.lay.addStretch()

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
        self.app.status("Settings saved."); self.app.refresh_all()


# ---------------------------------------------------------------------------------------------------------------
class MainWindow(QtWidgets.QMainWindow):
    PAGES = ["Home", "Segment", "Review", "Models", "Train", "Settings"]

    def __init__(self):
        super().__init__()
        self.cfg = C.load()
        self.setWindowTitle("GraftSeg")
        central = QtWidgets.QWidget(); self.setCentralWidget(central)
        h = QtWidgets.QHBoxLayout(central); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)
        side = QtWidgets.QWidget(); side.setFixedWidth(210); side.setStyleSheet(f"background:{SIDEBAR};")
        sl = QtWidgets.QVBoxLayout(side); sl.setContentsMargins(0, 0, 0, 0); sl.setSpacing(0)
        b = QtWidgets.QLabel("GraftSeg"); b.setObjectName("brand"); sl.addWidget(b)
        s = QtWidgets.QLabel("MRI graft segmentation"); s.setObjectName("brandsub"); sl.addWidget(s)
        self.nav = QtWidgets.QListWidget(); self.nav.setObjectName("nav")
        icons = {"Home": "⌂", "Segment": "◎", "Review": "✎", "Models": "▣", "Train": "↻", "Settings": "⚙"}
        for p in self.PAGES:
            self.nav.addItem(f"{icons[p]}   {p}")
        sl.addWidget(self.nav, 1)
        ver = QtWidgets.QLabel("v0.1"); ver.setStyleSheet("color:#475569;padding:12px 18px;"); sl.addWidget(ver)
        h.addWidget(side)
        self.stack = QtWidgets.QStackedWidget(); h.addWidget(self.stack, 1)
        self.home, self.segment, self.review = HomePage(self), SegmentPage(self), ReviewPage(self)
        self.models, self.trainp, self.settings_page = ModelsPage(self), TrainPage(self), SettingsPage(self)
        self.pages = [self.home, self.segment, self.review, self.models, self.trainp, self.settings_page]
        for p in self.pages:
            self.stack.addWidget(p)
        self.nav.currentRowChanged.connect(self._nav)
        self.setStatusBar(QtWidgets.QStatusBar())
        st = settings()
        geo = st.value("window/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            scr = QtGui.QGuiApplication.primaryScreen().availableGeometry()
            self.resize(min(1500, int(scr.width() * 0.9)), min(950, int(scr.height() * 0.9)))
        self.refresh_all()
        self.nav.setCurrentRow(0 if C.CONFIG_PATH.exists() else self.PAGES.index("Settings"))

    def _nav(self, row: int):
        if self.stack.currentWidget() is self.review and row != 2 and not self.review.confirm_leave():
            self.nav.blockSignals(True); self.nav.setCurrentRow(2); self.nav.blockSignals(False); return
        self.stack.setCurrentIndex(row)
        self.pages[row].refresh()

    def go(self, page: str):
        self.nav.setCurrentRow(self.PAGES.index(page))

    def status(self, text: str):
        self.statusBar().showMessage(text, 15000)

    def refresh_all(self):
        self.cfg = C.load()
        for p in self.pages:
            try:
                p.refresh()
            except Exception as e:  # noqa: BLE001
                self.status(f"{type(p).__name__}: {e}")

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
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("GraftSeg")
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
    return app


def launch(page: str = "home", review_paths: Optional[list[str]] = None):
    app = make_app()
    w = MainWindow()
    if review_paths:
        w.review.load_paths(review_paths)
        w.go("Review")
    elif page.lower() == "review":
        w.go("Review")
    w.show()
    return app.exec()
