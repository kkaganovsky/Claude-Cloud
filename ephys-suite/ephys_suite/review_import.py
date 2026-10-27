"""Merge an outside reviewer's CSV (from the HTML review package) back into the GUI's own review format.

Your own earlier decisions in an existing progress file are kept; the reviewer's yes / no are added on top. Existing
outputs are renamed to *.bak-<time>, never overwritten. For every recording in the returned file this writes, into --out:
  <stem>_review.csv                       the format of the Events tab's 'Save review...' (decision, final_event, ...)
  <stem>.candidates.csv                   a copy of the candidates file
  <stem>.candidates.review_progress.json  loads with 'Resume saved review' (put the .abf in the same folder): the reviewer's
                                          yes / no are confirmed / rejected; unsure and blank stay open so you can check them.

Matching is on (recording, sweep, round(time_in_sweep_ms, 2)), never on row order: the returned CSV is the source of truth for
what was shown. Unsure and blank answers are counted as NOT events by default (flags below) and the counts are printed.
"""
import argparse
import csv
import json
import os
import shutil
import sys
import time

from . import events as E
from . import model_review as MR
from . import verify as V
from .io import load_abf
from .review_export import find_candidates_for, read_candidates

ANSWERS = {"yes": "yes", "no": "no", "unsure": "unsure", "": "blank"}
POLICIES = ("not-event", "model", "event")


def read_returned(path):
    """[(recording, sweep, time_ms, answer)] with answer in yes / no / unsure / blank; plus the package ids seen."""
    rows, pkgs = [], set()
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rd = csv.DictReader(fh)
        need = ("recording", "sweep", "time_in_sweep_ms", "decision")
        for n in need:
            if n not in (rd.fieldnames or []):
                raise ValueError(f"{os.path.basename(path)}: not a review result file (no '{n}' column)")
        for r in rd:
            ans = ANSWERS.get((r["decision"] or "").strip().lower())
            if ans is None:
                raise ValueError(f"unknown answer '{r['decision']}' (expected yes / no / unsure or blank)")
            rows.append((os.path.basename(r["recording"].strip()), int(float(r["sweep"])), float(r["time_in_sweep_ms"]), ans))
            if r.get("package_id"):
                pkgs.add(r["package_id"])
    return rows, pkgs


def _key(sweep, t):
    return (int(sweep), round(float(t), 2))


def _backup(path):
    """Never overwrite earlier work: an existing file is renamed to <name>.bak-<time> first."""
    if os.path.exists(path):
        shutil.move(path, f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}")


def _find_recording(name, search_dirs):
    for d in search_dirs:
        for sub in ("", "recordings"):
            f = os.path.join(d, sub, name)
            if os.path.isfile(f):
                return f
    return None


def import_returned(returned, *, candidates=None, package=None, recordings=None, out=".", unsure_as="not-event", blank_as="not-event",
                    log=print):
    """Merge `returned` (CSV path) into review files under `out`. Returns a report dict (counts per answer, per recording)."""
    for pol in (unsure_as, blank_as):
        if pol not in POLICIES:
            raise ValueError(f"policy must be one of {POLICIES}")
    rows, pkgs = read_returned(returned)
    manifest = None
    if package:
        with open(os.path.join(package, "manifest.json")) as fh:
            manifest = json.load(fh)
        known = manifest["package"]["id"]
        other = pkgs - {known}
        if other:
            log(f"WARNING: the file contains answers from another package ({', '.join(sorted(other))}); package is {known}")
    byrec = {}
    for rec_name, sw, t, ans in rows:
        byrec.setdefault(rec_name, []).append((sw, t, ans))
    os.makedirs(out, exist_ok=True)
    report = dict(total=len(rows), yes=0, no=0, unsure=0, blank=0, unmatched=0, recordings={}, files=[])
    for rec_name, items in sorted(byrec.items()):
        cpath = rpath = None
        if manifest:
            cell = next((c for c in manifest["cells"] if c["recording"] == rec_name), None)
            if cell:
                cpath = cell["candidates_path"] if os.path.isfile(cell["candidates_path"]) else None
                rpath = cell["recording_path"] if os.path.isfile(cell["recording_path"]) else None
        cpath = cpath or find_candidates_for(os.path.join(recordings or ".", rec_name), candidates) or find_candidates_for(rec_name, candidates)
        if cpath is None:
            log(f"{rec_name}: no candidates file found (use --candidates); skipped {len(items)} answers")
            report["unmatched"] += len(items)
            continue
        dirs = [d for d in (recordings, os.path.dirname(cpath), os.path.dirname(os.path.dirname(cpath)), package) if d]
        rpath = rpath or _find_recording(rec_name, dirs)
        rep = _merge_one(rec_name, items, cpath, rpath, out, unsure_as, blank_as, log)
        report["recordings"][rec_name] = rep
        for k in ("yes", "no", "unsure", "blank", "unmatched"):
            report[k] += rep[k]
        report["files"] += rep["files"]
    log(f"answers: {report['yes']} yes, {report['no']} no, {report['unsure']} unsure, {report['blank']} blank "
        f"({report['unmatched']} could not be matched); unsure counted as '{unsure_as}', blank as '{blank_as}'")
    return report


def _merge_one(rec_name, items, cpath, rpath, out, unsure_as, blank_as, log):
    stem = os.path.splitext(rec_name)[0]
    crow = read_candidates(cpath)
    answers, dup = {}, 0
    for sw, t, ans in items:
        if _key(sw, t) in answers:
            dup += 1
        answers[_key(sw, t)] = ans
    known = {_key(r["sweep"], r["t"]) for r in crow}
    rep = dict(yes=0, no=0, unsure=0, blank=0, unmatched=sum(k not in known for k in answers), duplicates=dup, files=[])
    for k, a in answers.items():
        if k in known:
            rep[a] += 1
    cout = os.path.join(out, os.path.basename(cpath))
    if os.path.abspath(cout) != os.path.abspath(cpath):
        shutil.copy2(cpath, cout)
    rep["files"].append(cout)
    if rpath is None:
        log(f"{rec_name}: recording not found, so the review CSV is written without peak positions and no resume file is made "
            f"(give --recordings <folder> to get both)")
        rep["files"] += [_csv_without_recording(rec_name, crow, answers, unsure_as, blank_as, out, stem)]
        return rep
    rec = load_abf(rpath)
    ch = rec.channel_by_units("pA", 0)
    sess = E.EventSession(rec.t, rec.ch[ch], E.EventParams(), rec.sweep_start_ms)
    state = MR.load_candidates(cout, sess)
    ts = sess.ts

    def snapped(sw, t):
        r = sw - 1
        return (r, int(sess.snap_peak(r, t, None, half_ms=1.0))) if 0 <= r < sess.Y.shape[0] else None
    sess.load_external([(c.record, c.peak_idx) for c in state.cands if c.accepted])
    prog = V.progress_path(rpath, cout)
    prev = V.load_progress(prog)                                 # your own earlier decisions (e.g. the cells you reviewed in the app)
    if prev is not None and V.apply_progress(prev, sess, state, None):
        log(f"{rec_name}: kept the decisions already saved in {os.path.basename(prog)}")
    tol = max(1, int(round(4.0 / ts)))
    for r in crow:
        k = _key(r["sweep"], r["t"])
        if k not in answers:
            continue
        pk = snapped(r["sweep"], r["t"])
        if pk is None:
            continue
        ans = answers[k]
        ev = sess.find_event(pk[0], pk[1], tol)
        if ans == "yes":
            if ev is not None:
                sess.confirm(ev)
            else:
                ev, why = sess.add(pk[0], pk[1], fit=True, confirm=True)
                if ev is None and why not in ("ok", "duplicate"):
                    sess.add(pk[0], pk[1], fit=False, confirm=True)
        elif ans == "no":
            if ev is not None:
                sess.reject(ev)
            else:
                sess.dismiss(pk[0], pk[1])
        else:                                                    # unsure / blank: stays OPEN for a second look in the GUI
            policy = unsure_as if ans == "unsure" else blank_as
            if policy == "not-event" and ev is not None:
                sess.delete(ev)                                  # no longer an event, but not decided: shows as a borderline ring
            elif policy == "event" and ev is None:
                sess.add(pk[0], pk[1], fit=False)
    review_csv = os.path.join(out, f"{stem}_review.csv")
    for f in (review_csv, os.path.splitext(review_csv)[0] + ".json", prog):
        _backup(f)
    MR.export_review(review_csv, sess, state, rec_name, rpath)
    V.save_progress(prog, sess, state, None, rpath, os.path.basename(cout), "uncertain", 0)
    rep["files"] += [review_csv, prog]
    log(f"{rec_name}: {rep['yes']} yes, {rep['no']} no, {rep['unsure']} unsure, {rep['blank']} blank -> {review_csv}")
    return rep


def _csv_without_recording(rec_name, crow, answers, unsure_as, blank_as, out, stem):
    path = os.path.join(out, f"{stem}_review.csv")
    _backup(path)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["recording", "sweep", "time_in_sweep_ms", "peak_idx", "model_score", "model_accepted", "final_event", "source",
                    "sweep_marked_reviewed", "visited", "decision", "record_viewed_pct"])
        for r in crow:
            if r["score"] < MR.MIN_SHOW and not r["accepted"]:
                continue
            a = answers.get(_key(r["sweep"], r["t"]))
            if a == "yes":
                final, dec = 1, "confirmed"
            elif a == "no":
                final, dec = 0, "rejected" if r["accepted"] else "dismissed"
            elif a in ("unsure", "blank"):
                pol = unsure_as if a == "unsure" else blank_as
                final, dec = {"not-event": 0, "event": 1, "model": r["accepted"]}[pol], "unreviewed"
            else:
                final, dec = r["accepted"], "unreviewed"
            w.writerow([rec_name, r["sweep"], r["t"], "", r["score"], r["accepted"], final, "model", 0, int(a is not None), dec, ""])
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ephys-suite-review-import", description=__doc__.split("\n\n")[0])
    ap.add_argument("returned", help="the CSV the reviewer sent back")
    ap.add_argument("--candidates", help="candidates CSV or folder (not needed with --package)")
    ap.add_argument("--package", help="the review package folder that was sent out (finds candidates and recordings itself)")
    ap.add_argument("--recordings", help="folder with the .abf files (needed for peak positions and the resume file)")
    ap.add_argument("--out", required=True, help="output folder")
    ap.add_argument("--unsure-as", choices=POLICIES, default="not-event", help="how 'unsure' answers count (default: not an event)")
    ap.add_argument("--blank-as", choices=POLICIES, default="not-event", help="how unanswered events count (default: not an event)")
    a = ap.parse_args(argv)
    if not (a.candidates or a.package):
        ap.error("give --candidates or --package")
    import_returned(a.returned, candidates=a.candidates, package=a.package, recordings=a.recordings, out=a.out,
                    unsure_as=a.unsure_as, blank_as=a.blank_as)
    return 0


if __name__ == "__main__":
    sys.exit(main())
