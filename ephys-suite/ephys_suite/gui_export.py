"""Events tab: hand the review of model-proposed events to an outside reviewer (HTML package), and merge their answers back."""
import os

from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt

from . import model_review as MR
from . import review_export as RX
from . import review_import as RI
from . import verify as V


def pairs_in_folder(folder):
    """[(abf, candidates_csv)] for every recording in `folder` that has a candidates file (GUI auto-load rule)."""
    out = []
    for f in sorted(os.listdir(folder)):
        if f.lower().endswith(".abf"):
            p = os.path.join(folder, f)
            c = RX.find_candidates_for(p)
            if c:
                out.append((p, c))
    return out


class ExportDialog(QtWidgets.QDialog):
    """Options for the export. `run()` does the work (so tests can call it without the dialog being shown)."""

    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.setWindowTitle("Export for outside review (HTML)")
        self.result = None
        rec = tab.main.rec
        self.folder = os.path.dirname(os.path.abspath(rec.path))
        self.cur_abf = os.path.abspath(rec.path)
        self.settings = tab.settings
        v = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel("Writes a folder anyone can review in a web browser (double-click START_HERE.html): no install, no Python, "
                                 "no model. The reviewer answers Yes / No / Not sure for each event and sends back one CSV, which you "
                                 "merge back with <i>Import reviewer's results…</i>.")
        intro.setWordWrap(True)
        v.addWidget(intro)
        self.lst = QtWidgets.QListWidget()
        v.addWidget(QtWidgets.QLabel("Recordings to send (each needs a candidates file beside it):"))
        v.addWidget(self.lst, 1)
        self.pairs = pairs_in_folder(self.folder)
        if not any(os.path.abspath(a) == self.cur_abf for a, _ in self.pairs):
            c = (tab.kit.cand_path if getattr(tab, 'kit', None) is not None else None) or RX.find_candidates_for(self.cur_abf)
            if c:
                self.pairs.insert(0, (self.cur_abf, c))
        for abf, c in self.pairs:
            reviewed = os.path.exists(V.progress_path(abf, c))
            it = QtWidgets.QListWidgetItem(os.path.basename(abf) + ("   (you already have a saved review of this one)" if reviewed and abs_ne(abf, self.cur_abf) else ""))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked if reviewed and abs_ne(abf, self.cur_abf) else Qt.Checked)
            it.setData(Qt.UserRole, (abf, c))
            self.lst.addItem(it)
        form = QtWidgets.QFormLayout()
        self.sp_min = QtWidgets.QDoubleSpinBox()
        self.sp_min.setRange(0, 1)
        self.sp_min.setDecimals(2)
        self.sp_min.setSingleStep(0.05)
        self.sp_min.setValue(RX.DEFAULT_MIN_SCORE)
        self.sp_min.setToolTip("Candidates scored below this are not sent. (The Events tab itself shows candidates down to 0.25.)")
        self.sp_thr = QtWidgets.QDoubleSpinBox()
        self.sp_thr.setRange(0, 1)
        self.sp_thr.setDecimals(2)
        self.sp_thr.setSingleStep(0.05)
        self.sp_thr.setValue(round(tab.review.thr, 2) if tab.review is not None else RX.FALLBACK_THR)
        self.sp_thr.setToolTip("At or above this score the reviewer sees 'computer: detected', below it 'computer: maybe'. "
                               "Both are judged the same way; the score itself is never shown.")
        form.addRow("Send candidates scored at least", self.sp_min)
        form.addRow("'Detected' from score", self.sp_thr)
        self.ed_title = QtWidgets.QLineEdit("sEPSC event review")
        form.addRow("Title", self.ed_title)
        self.pol = "inward" if int(tab.p.direction) < 0 else "outward"
        form.addRow("Event direction", QtWidgets.QLabel(f"{self.pol} (from the Detection settings)"))
        v.addLayout(form)
        self.chk_skip = QtWidgets.QCheckBox("Leave out events I have already confirmed / rejected in this review (open recording)")
        self.chk_skip.setChecked(tab.review is not None)
        self.chk_skip.setEnabled(tab.review is not None)
        self.chk_raw = QtWidgets.QCheckBox("Also copy the .abf recordings into the package (large; not needed for the review)")
        self.chk_zip = QtWidgets.QCheckBox("Also make a .zip ready to email")
        self.chk_zip.setChecked(True)
        for c in (self.chk_skip, self.chk_raw, self.chk_zip):
            v.addWidget(c)
        row = QtWidgets.QHBoxLayout()
        self.ed_out = QtWidgets.QLineEdit(os.path.join(self.folder, os.path.splitext(os.path.basename(rec.path))[0] + "_review_package"))
        b = QtWidgets.QPushButton("Browse…")
        b.clicked.connect(self._browse)
        row.addWidget(QtWidgets.QLabel("Save into"))
        row.addWidget(self.ed_out, 1)
        row.addWidget(b)
        v.addLayout(row)
        self.lbl = QtWidgets.QLabel("")
        self.lbl.setWordWrap(True)
        v.addWidget(self.lbl)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        self.btn_go = QtWidgets.QPushButton("Export")
        self.btn_go.setProperty("role", "primary")
        bb.addButton(self.btn_go, QtWidgets.QDialogButtonBox.AcceptRole)
        bb.rejected.connect(self.reject)
        self.btn_go.clicked.connect(self._go)
        v.addWidget(bb)
        self.resize(640, 560)

    def _browse(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Save the package into", self.folder)
        if d:
            self.ed_out.setText(os.path.join(d, os.path.basename(self.ed_out.text())))

    def chosen(self):
        return [self.lst.item(i).data(Qt.UserRole) for i in range(self.lst.count()) if self.lst.item(i).checkState() == Qt.Checked]

    def skip_for(self):
        """callable(abf) -> predicate dropping events already decided for the OPEN recording."""
        tab = self.tab
        if not self.chk_skip.isChecked() or tab.review is None or tab.session is None:
            return None
        s = tab.session

        def for_abf(abf):
            if os.path.abspath(abf) != self.cur_abf:
                return None

            def pred(sweep, t_ms):
                r = sweep - 1
                if not 0 <= r < s.Y.shape[0]:
                    return False
                return s.decision(r, int(s.snap_peak(r, t_ms, None, half_ms=1.0))) is not None
            return pred
        return for_abf

    def run(self):
        pairs = self.chosen()
        if not pairs:
            raise ValueError("Tick at least one recording.")
        log = []

        def say(msg):
            log.append(msg)
            self.lbl.setText(msg)
            QtWidgets.QApplication.processEvents()
        self.result = RX.build_package(pairs, self.ed_out.text().strip(), min_score=self.sp_min.value(), thr=self.sp_thr.value(),
                                       include_raw=self.chk_raw.isChecked(), title=self.ed_title.text().strip() or "Event review",
                                       polarity=self.pol, skip=self.skip_for(), make_zip=self.chk_zip.isChecked(), log=say)
        self.settings.setValue("last_package", os.path.abspath(self.ed_out.text().strip()))
        return self.result

    def _go(self):
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            man = self.run()
        except Exception as e:                                     # shown to the user, not swallowed
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, "Could not export", str(e))
            return
        QtWidgets.QApplication.restoreOverrideCursor()
        n = sum(c["n"] for c in man["cells"])
        where = man.get("zip") or os.path.abspath(self.ed_out.text().strip())
        box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Information, "Package ready",
                                    f"{n} events in {len(man['cells'])} recording(s).\n\nSend this to your reviewer:\n{where}\n\n"
                                    "When they send back their CSV, use Import reviewer's results… in the same panel.", QtWidgets.QMessageBox.Ok, self)
        open_btn = box.addButton("Open folder", QtWidgets.QMessageBox.ActionRole)
        box.exec()
        if box.clickedButton() is open_btn:
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(os.path.dirname(where)))
        self.accept()


def abs_ne(a, b):
    return os.path.abspath(a) != os.path.abspath(b)


def import_flow(tab, returned=None, package=None):
    """Pick the reviewer's CSV, merge it, and (if it is the open recording) load the result for a second look."""
    rec = tab.main.rec
    start = os.path.dirname(os.path.abspath(rec.path)) if rec is not None else ""
    if returned is None:
        returned, _ = QtWidgets.QFileDialog.getOpenFileName(tab, "Reviewer's results (CSV)", start, "CSV (*.csv)")
    if not returned:
        return None
    try:
        rows, pkgs = RI.read_returned(returned)
    except (ValueError, OSError) as e:
        QtWidgets.QMessageBox.warning(tab, "Not a review result file", str(e))
        return None
    package = package or tab.settings.value("last_package", "")
    if package and not os.path.isfile(os.path.join(package, "manifest.json")):
        package = None
    msgs = []
    try:
        names = sorted({r[0] for r in rows})
        cand_dir = None
        if not package:
            cand_dir = start                                        # candidates and recordings beside the open recording
        rep = RI.import_returned(returned, package=package, candidates=cand_dir, recordings=start or None,
                                 out=start or os.path.dirname(returned), log=msgs.append)
    except Exception as e:
        QtWidgets.QMessageBox.warning(tab, "Could not import", str(e))
        return None
    summary = (f"{rep['yes']} yes, {rep['no']} no, {rep['unsure']} unsure, {rep['blank']} unanswered"
               f"{', ' + str(rep['unmatched']) + ' could not be matched' if rep['unmatched'] else ''}.\n"
               "Unsure and unanswered events are counted as NOT events and stay open for a second look.\n\n" + "\n".join(msgs[:-1]))
    tab.say("Imported the reviewer's answers: " + summary.splitlines()[0], "add")
    QtWidgets.QMessageBox.information(tab, "Reviewer's answers merged", summary)
    if rec is not None and os.path.basename(rec.path) in rep["recordings"]:      # the open recording: show the result straight away
        cand = next((f for f in rep["recordings"][os.path.basename(rec.path)]["files"] if f.endswith(".candidates.csv")), None)
        if cand:
            tab.load_candidates_file(cand)
    return rep
