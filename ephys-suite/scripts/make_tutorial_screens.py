"""Regenerate the screenshots used in docs/TUTORIAL.md by driving the real GUI (offscreen) on the example cell.

    python scripts/make_tutorial_screens.py [--data FOLDER_WITH_THE_UNZIPPED_CELL] [--out docs/img]

Without --data the example cell is unzipped from the repository root into a temporary folder.
"""
import argparse
import glob
import os
import shutil
import sys
import tempfile
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

import numpy as np                                        # noqa: E402
from PySide6 import QtCore, QtWidgets                      # noqa: E402

from ephys_suite.gui import MainWindow                      # noqa: E402
import make_demo_candidates as MDC                          # noqa: E402


def find_cell(data):
    if data:
        hit = glob.glob(os.path.join(data, "cell*")) or [data]
        return hit[0]
    tmp = tempfile.mkdtemp(prefix="ephys-tutorial-")
    z = glob.glob(os.path.join(HERE, "..", "..", "cell 7 DLX kglu*.zip"))
    if not z:
        sys.exit("example cell zip not found next to the repository; pass --data")
    zipfile.ZipFile(z[0]).extractall(tmp)
    shutil.rmtree(os.path.join(tmp, "__MACOSX"), ignore_errors=True)
    return glob.glob(os.path.join(tmp, "cell*"))[0]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "docs", "img"))
    a = ap.parse_args(argv)
    cell = find_cell(a.data)
    os.makedirs(a.out, exist_ok=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    for k in ("ephys-suite", "qc", "events"):
        QtCore.QSettings("ephys-suite", k).clear()
    w = MainWindow()
    w.resize(1500, 920)
    w.show()

    def shot(name):
        app.processEvents()
        w.statusBar().clearMessage()
        w.grab().save(os.path.join(a.out, name))
        print("wrote", name)

    # 1-3 current clamp -------------------------------------------------------------------------------
    w.load(os.path.join(cell, "IO.abf"))
    w.tabs.setCurrentWidget(w.passive)
    shot("01_passive.png")
    w.tabs.setCurrentWidget(w.spikes)
    shot("02_ap.png")
    sm = w.summary
    for k, v in (("Date Recorded", "2026-10-16"), ("Cell number", "7"), ("Dlx", "yes")):
        sm.meta[k].setText(v)
    sm.spont = os.path.join(cell, "spontanous?.abf")
    sm.lbl_sp.setText("spontanous?.abf")
    w.tabs.setCurrentWidget(sm)
    sm.build()
    shot("03_summary.png")

    # 4 access resistance -------------------------------------------------------------------------------
    parent = os.path.dirname(cell)
    q = w.qc
    q.settings.clear()
    q.lbl_root.setText(parent)
    q.scan()
    w.tabs.setCurrentWidget(q)
    q.tbl.selectRow(0)
    shot("04_qc.png")

    # 5-6 sEPSC detection and hand editing --------------------------------------------------------------
    w.load(os.path.join(cell, "sEPSC.abf"))
    ev = w.events
    w.tabs.setCurrentWidget(ev)
    ev.omit_start.setValue(0)
    ev.omit_stop.setValue(400)
    ev.run()
    ev.vw.setValue(1500)
    ev.vy0.setValue(-90)
    ev.vy1.setValue(40)
    ev._set_view(700.0, 1500.0, (-90, 40))
    shot("05_events.png")
    s = ev.session
    t, y = s.t, s.Y[0]
    # a deflection the detector skipped: the biggest local minimum in 1.2-2.6 s that is not an event
    lo, hi = int(1200 / s.ts), int(2600 / s.ts)
    order = np.argsort(y[lo:hi])
    pk = None
    for j in order[:4000]:
        k = lo + int(j)
        if s.find_event(0, k, int(15 / s.ts)) is None and y[k] < np.median(y) - 7:
            pk = k
            break
    ev._set_view(max(0.0, t[pk] - 400), 1000.0, (-90, 40)) if pk is not None else None
    ev.btn_add.setChecked(True)
    ev.btn_fit.setChecked(False)
    ev.btn_bl.setChecked(True)                      # arm the manual baseline
    if pk is not None:
        ev.on_click(t[pk], y[pk], ev.vb)            # pick the peak -> waits for the baseline click
    shot("06_events_edit.png")
    ev.cancel()
    ev.btn_add.setChecked(False)
    ev.btn_fit.setChecked(True)

    # 7-9 verify workflow on a practice candidates file --------------------------------------------------
    rows = MDC.make_candidates(os.path.join(cell, "sEPSC.abf"), ((0.0, 400.0),))
    import csv
    cand = os.path.join(tempfile.mkdtemp(prefix="ephys-cands-"), "practice.candidates.csv")
    with open(cand, "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["sweep", "time_in_sweep_ms", "score", "accepted", "amplitude_pA"])
        wr.writerows(rows)
    ev.run()
    ev.load_candidates_file(cand)
    kit = ev.kit
    ev._set_view(700.0, 1500.0, (-90, 40))
    for _ in range(3):
        kit.confirm_current()
    kit._count_view()
    ev._set_view(700.0, 1500.0, (-90, 40))
    kit.focus(kit.current(), recenter=True)
    shot("07_verify.png")
    kit.open_gallery()
    kit.gallery.resize(1250, 760)
    kit.gallery.open_with("lowest_score")
    app.processEvents()
    kit.gallery.grab().save(os.path.join(a.out, "08_gallery.png"))
    print("wrote 08_gallery.png")
    kit.gallery.close()
    kit.next_suspect(+1)
    shot("09_possible_miss.png")
    w.close()


if __name__ == "__main__":
    main()
