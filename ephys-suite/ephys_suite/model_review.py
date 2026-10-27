"""Review events proposed by a classifier inside the Events tab (add / remove them like in Easy Electrophysiology).

Input: a candidates CSV with one row per candidate (columns: sweep [1-based], time_in_sweep_ms, score, accepted [0/1]; the
file written by the ml_results/code/apply_new.py pipeline). Candidates with accepted=1 become the event set; the others (score
>= MIN_SHOW) are drawn as orange rings that can be accepted with one click. A review queue lists the most uncertain candidates
(score closest to the model's threshold) first.

Output: a review CSV with the final decision for every candidate plus the events added by hand, and a sidecar .json with the
sweeps you marked as fully reviewed. Labels from reviewed sweeps are trustworthy; the rest are only as good as the model.
"""
import csv
import json
import os
from dataclasses import dataclass, field

import numpy as np

MIN_SHOW = 0.25          # candidates below this score are neither shown nor queued
SHADOW_MS = 10.0         # a not-accepted candidate this soon after an accepted event is not offered as borderline
QUEUE_MAX = 600


@dataclass
class Cand:
    record: int
    peak_idx: int
    score: float
    accepted: bool


def _logit(p):
    p = min(max(float(p), 1e-6), 1 - 1e-6)
    return float(np.log(p / (1 - p)))


class ReviewState:
    def __init__(self, cands, thr, source="", ts_ms=None, shadow_ms=SHADOW_MS):
        self.cands = cands
        self.thr = float(thr)
        self.source = source
        self.ts_ms = ts_ms                    # ms per sample (needed to turn shadow_ms into samples)
        self.shadow_ms = float(shadow_ms)
        self.reviewed = set()                 # sweeps (0-based) marked fully reviewed
        self.visited = set()                  # (record, peak_idx) of queue items stepped over
        self.pos = -1
        self._rebuild()

    def set_shadow(self, ms):
        """Change the 'too soon after an accepted event' window (0 = off) and rebuild the borderline queue."""
        self.shadow_ms = max(0.0, float(ms))
        self._rebuild()

    def _rebuild(self):
        self.shadowed = set()                 # (record, peak_idx) of not-accepted candidates within shadow_ms AFTER an accepted one
        if self.ts_ms and self.shadow_ms > 0:
            win = self.shadow_ms / self.ts_ms
            acc = {}
            for c in self.cands:
                if c.accepted:
                    acc.setdefault(c.record, []).append(c.peak_idx)
            for c in self.cands:
                if not c.accepted and any(0 < c.peak_idx - a <= win for a in acc.get(c.record, ())):
                    self.shadowed.add((c.record, c.peak_idx))
        shown = [c for c in self.cands if c.score >= MIN_SHOW and (c.record, c.peak_idx) not in self.shadowed]
        shown.sort(key=lambda c: abs(_logit(c.score) - _logit(self.thr)))
        self.queue = shown[:QUEUE_MAX]
        self.pos = -1

    def is_shadowed(self, record, peak_idx):
        return (record, peak_idx) in self.shadowed

    def candidates_in(self, record, accepted):
        return [c for c in self.cands if c.record == record and c.accepted == accepted and c.score >= MIN_SHOW
                and (accepted or (c.record, c.peak_idx) not in self.shadowed)]

    def step(self, d):
        if not self.queue:
            return None
        self.pos = (self.pos + d) % len(self.queue) if self.pos >= 0 else (0 if d > 0 else len(self.queue) - 1)
        c = self.queue[self.pos]
        self.visited.add((c.record, c.peak_idx))
        return c


REQUIRED_COLS = ("sweep", "time_in_sweep_ms", "score", "accepted")


def find_associated(rec_path):
    """Candidate / review CSVs that belong to this recording, newest first.

    Looked for next to the recording and in a `candidates/` (or `review/`) subfolder:
    `<name>.candidates.csv` (ephys-suite-classify output), `<name>_review.csv` (Save review), `<name>.review.csv`.
    A file only counts if its header has the candidate columns. Which one is 'right' is not decided here: the newest is listed first.
    """
    base = os.path.splitext(os.path.basename(rec_path))[0]
    folder = os.path.dirname(os.path.abspath(rec_path))
    names = (f"{base}.candidates.csv", f"{base}_review.csv", f"{base}.review.csv")
    found = []
    for d in (folder, os.path.join(folder, "candidates"), os.path.join(folder, "review")):
        for n in names:
            f = os.path.join(d, n)
            if os.path.isfile(f) and _has_columns(f):
                found.append(f)
    return sorted(found, key=os.path.getmtime, reverse=True)


def _has_columns(path):
    try:
        with open(path, newline="") as fh:
            head = next(csv.reader(fh), [])
        return all(c in head for c in REQUIRED_COLS)
    except (OSError, UnicodeDecodeError, csv.Error):
        return False


def load_candidates(path, session, thr=None):
    """Read the CSV, snap every candidate to the real peak in the loaded trace, drop duplicates (keep the higher score)."""
    rows = list(csv.DictReader(open(path, newline="")))
    if not rows:
        raise ValueError("empty candidates file")
    for need in ("sweep", "time_in_sweep_ms", "score", "accepted"):
        if need not in rows[0]:
            raise ValueError(f"candidates CSV needs a '{need}' column")
    best = {}
    for r in rows:
        rec = int(float(r["sweep"])) - 1
        if not 0 <= rec < session.Y.shape[0]:
            continue
        k = session.snap_peak(rec, float(r["time_in_sweep_ms"]), None, half_ms=1.0)
        c = Cand(rec, int(k), float(r["score"]), bool(int(float(r["accepted"]))))
        key = (rec, c.peak_idx)
        if key not in best or c.score > best[key].score:
            best[key] = c
    cands = sorted(best.values(), key=lambda c: (c.record, c.peak_idx))
    if thr is None:
        acc = [c.score for c in cands if c.accepted]
        thr = min(acc) if acc else 0.5
    return ReviewState(cands, thr, os.path.basename(path), ts_ms=float(session.ts))


def export_review(path, session, state, recording="", recording_path="", coverage=None, suspects=None):
    """Write the final decision for every model candidate plus manual additions; returns (n_rows, n_changed).

    Columns after `visited` (appended, so older readers are unaffected): decision (confirmed / rejected / dismissed /
    unreviewed / added), record_viewed_pct (share of the record the reviewer looked at). Suspects (second-opinion places)
    that were decided are listed as source 'suspect'."""
    ts = session.ts
    tol = max(1, int(round(4.0 / ts)))
    rows, changed, near = [], 0, set()

    def decision(rec, idx, final):
        d = session.decision(rec, idx)
        if d:
            return d
        return "unreviewed" if final else ""

    def viewed(rec):
        return round(100.0 * coverage.fraction(rec), 1) if coverage is not None else ""
    for c in state.cands:
        if c.score < MIN_SHOW and not c.accepted:
            continue
        ev = session.find_event(c.record, c.peak_idx, tol)
        final = ev is not None
        if ev is not None:
            near.add((ev.record, ev.peak_idx))
        changed += int(final != c.accepted)
        rows.append([recording, c.record + 1, round(c.peak_idx * ts, 3), c.peak_idx, round(c.score, 4), int(c.accepted), int(final),
                     "model", int(c.record in state.reviewed), int((c.record, c.peak_idx) in state.visited),
                     decision(c.record, c.peak_idx, final), viewed(c.record)])
    for e in session.events:
        if (e.record, e.peak_idx) not in near:
            rows.append([recording, e.record + 1, round(e.peak_idx * ts, 3), e.peak_idx, "", 0, 1, "manual_add",
                         int(e.record in state.reviewed), 1, "added", viewed(e.record)])
            changed += 1
    for sp in suspects or []:
        d = session.decision(sp.record, sp.peak_idx)
        ev = session.find_event(sp.record, sp.peak_idx, tol)
        if d is None and ev is None:
            continue
        if ev is not None and (ev.record, ev.peak_idx) in near:
            continue
        rows.append([recording, sp.record + 1, round(sp.peak_idx * ts, 3), sp.peak_idx, "", 0, int(ev is not None), "suspect",
                     int(sp.record in state.reviewed), 1, d or "added", viewed(sp.record)])
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["recording", "sweep", "time_in_sweep_ms", "peak_idx", "model_score", "model_accepted", "final_event", "source",
                    "sweep_marked_reviewed", "visited", "decision", "record_viewed_pct"])
        w.writerows(rows)
    meta = dict(recording=recording, recording_path=recording_path, source=state.source, threshold=state.thr,
                reviewed_sweeps=sorted(int(r) + 1 for r in state.reviewed), n_changed=changed)
    if coverage is not None:
        meta["viewed_fraction_by_record"] = {str(r + 1): round(coverage.fraction(r), 4) for r in range(len(coverage.len))}
        meta["viewed_fraction_total"] = round(coverage.fraction(), 4)
        meta["viewed_intervals_ms"] = {str(int(r) + 1): v for r, v in coverage.to_json().items()}
    json.dump(meta, open(os.path.splitext(path)[0] + ".json", "w"), indent=1)
    return len(rows), changed
