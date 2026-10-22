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
    def __init__(self, cands, thr, source=""):
        self.cands = cands
        self.thr = float(thr)
        self.source = source
        self.reviewed = set()                 # sweeps (0-based) marked fully reviewed
        self.visited = set()                  # (record, peak_idx) of queue items stepped over
        self.pos = -1
        shown = [c for c in cands if c.score >= MIN_SHOW]
        shown.sort(key=lambda c: abs(_logit(c.score) - _logit(self.thr)))
        self.queue = shown[:QUEUE_MAX]

    def candidates_in(self, record, accepted):
        return [c for c in self.cands if c.record == record and c.accepted == accepted and c.score >= MIN_SHOW]

    def step(self, d):
        if not self.queue:
            return None
        self.pos = (self.pos + d) % len(self.queue) if self.pos >= 0 else (0 if d > 0 else len(self.queue) - 1)
        c = self.queue[self.pos]
        self.visited.add((c.record, c.peak_idx))
        return c


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
    return ReviewState(cands, thr, os.path.basename(path))


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
