"""Logic behind the event-verification workflow (no GUI): coverage, possible-miss finder, queues, stats, progress files.

Two jobs
  1. Verify predicted events: every event is *unreviewed* until the reviewer confirms (or rejects) it; queues order the
     work (least sure first, time order, borderline candidates, possible misses).
  2. Find missed events: coverage records which parts of every record the reviewer has actually looked at (at a zoom level
     where events are visible), and a second-opinion detector (deconvolution at a permissive cutoff, independent of the
     classifier) proposes places that look like events but are neither events nor decided candidates.
Decisions live in events.EditLog (confirmed / rejected / dismissed) so one undo history covers everything.
"""
import dataclasses
import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import events as E
from . import model_review as MR

MODES = {
    "uncertain": "Uncertain: events and candidates, least sure first",
    "lowest_score": "Model events: least sure first",
    "time": "All events: in time order",
    "borderline": "Borderline model candidates",
    "suspects": "Possible missed events",
}
PROGRESS_VERSION = 1


# ------------------------------------------------------------------------------------------------ coverage
class Coverage:
    """Intervals (ms) of each record that the reviewer has viewed."""

    def __init__(self, lengths_ms: Sequence[float]):
        self.len = [float(x) for x in lengths_ms]
        self.iv: List[List[List[float]]] = [[] for _ in self.len]

    def add(self, record: int, t0: float, t1: float):
        lo, hi = max(0.0, min(t0, t1)), min(self.len[record], max(t0, t1))
        if hi - lo <= 0:
            return
        out, placed = [], False
        for a, b in self.iv[record]:
            if b < lo - 1e-9 or a > hi + 1e-9:
                out.append([a, b])
            else:
                lo, hi = min(lo, a), max(hi, b)
        out.append([lo, hi])
        out.sort()
        self.iv[record] = out

    def viewed_ms(self, record: Optional[int] = None) -> float:
        recs = range(len(self.len)) if record is None else [record]
        return float(sum(b - a for r in recs for a, b in self.iv[r]))

    def fraction(self, record: Optional[int] = None) -> float:
        recs = range(len(self.len)) if record is None else [record]
        tot = sum(self.len[r] for r in recs)
        return self.viewed_ms(record) / tot if tot else 0.0

    def gaps(self, record: int) -> List[Tuple[float, float]]:
        out, pos = [], 0.0
        for a, b in self.iv[record]:
            if a - pos > 1e-9:
                out.append((pos, a))
            pos = max(pos, b)
        if self.len[record] - pos > 1e-9:
            out.append((pos, self.len[record]))
        return out

    def next_gap(self, record: int, t: float, min_len: float = 20.0) -> Optional[Tuple[int, float, float]]:
        """First unviewed stretch (>= min_len ms) at or after (record, t), wrapping across records. None if everything is viewed."""
        n = len(self.len)
        for k in range(n + 1):
            r = (record + k) % n
            for a, b in self.gaps(r):
                if b - a < min_len:
                    continue
                if k == 0 and b <= t:
                    continue
                if k == n and a >= t:                                       # wrapped fully round to the start record
                    continue
                return r, max(a, t if k == 0 else a), b
        return None

    def to_json(self):
        return {str(r): [[round(a, 3), round(b, 3)] for a, b in v] for r, v in enumerate(self.iv) if v}

    def load_json(self, d):
        for r, v in (d or {}).items():
            r = int(r)
            if 0 <= r < len(self.iv):
                for a, b in v:
                    self.add(r, a, b)


# ------------------------------------------------------------------------------------- second opinion finder
def robust_noise(y, ts_ms: float = 0.05) -> float:
    """Noise SD at the time scale of events: the signal minus a 50 ms running mean (removes drift), robust (MAD) so events
    do not inflate it. First differences were used before, but they only see sample-to-sample noise and under-estimate the
    background fluctuation by ~3x on real sEPSC recordings, which makes every small deflection look like a strong event."""
    from scipy.ndimage import uniform_filter1d
    y = np.asarray(y, float)
    hp = y - uniform_filter1d(y, size=max(3, int(round(50.0 / ts_ms))), mode="nearest")
    return float(1.4826 * np.median(np.abs(hp - np.median(hp)))) or 1e-9


@dataclass
class Suspect:
    record: int
    peak_idx: int
    amp: float
    snr: float


def find_suspects(session: E.EventSession, skip: Callable[[int, int], bool], sd: float = 2.5, min_snr: float = 3.0,
                  cap_per_record: int = 400) -> List[Suspect]:
    """Places that look like events to a permissive deconvolution detector but are not (and are not decided candidates).

    skip(record, peak_idx) -> True for peaks that should not be proposed (existing events, model candidates, decided items).
    Ranked by amplitude / noise, strongest first."""
    p = dataclasses.replace(session.p, method="deconvolution", deconv_sd=sd, lower_mode="none", amplitude_threshold=0.0,
                            fit_decay=False)
    res = E.analyse_events(session.t, session.Y, p, session.starts)
    noise = [robust_noise(session.Y[r], session.ts) for r in range(session.Y.shape[0])]
    out = []
    for e in res.events:
        snr = abs(e.amplitude) / noise[e.record]
        if snr < min_snr or skip(e.record, e.peak_idx):
            continue
        out.append(Suspect(e.record, e.peak_idx, float(e.amplitude), float(snr)))
    out.sort(key=lambda s: -s.snr)
    per, keep = {}, []
    for s in out:
        per[s.record] = per.get(s.record, 0) + 1
        if per[s.record] <= cap_per_record:
            keep.append(s)
    return keep


# --------------------------------------------------------------------------------------------------- queues
@dataclass
class QItem:
    kind: str                 # 'event' | 'candidate' | 'suspect'
    record: int
    peak_idx: int
    score: float = float("nan")       # model score (candidates / model events)
    snr: float = float("nan")         # amplitude / noise (suspects)

    @property
    def key(self):
        return (self.record, self.peak_idx)


def _near(a, b, tol):
    return a[0] == b[0] and abs(a[1] - b[1]) <= tol


def model_score(review: Optional[MR.ReviewState], record: int, idx: int, tol: int) -> float:
    if review is None:
        return float("nan")
    best = [c.score for c in review.cands if c.record == record and abs(c.peak_idx - idx) <= tol]
    return max(best) if best else float("nan")


def build_queue(mode: str, session: E.EventSession, review: Optional[MR.ReviewState] = None,
                suspects: Optional[List[Suspect]] = None, include_done: bool = False) -> List[QItem]:
    tol = max(1, int(round(4.0 / session.ts)))
    items: List[QItem] = []
    if mode == "uncertain":
        # ONE list: accepted events and borderline candidates, ordered by how close the model's score is to its own threshold
        # (either side), so every decision that could go either way comes first. Events the model never scored come last.
        evs = build_queue("lowest_score", session, review, suspects, include_done)
        cands = build_queue("borderline", session, review, suspects, include_done)
        thr = MR._logit(review.thr) if review is not None else 0.0

        def dist(q):
            return (math.isnan(q.score), abs(MR._logit(q.score) - thr) if not math.isnan(q.score) else 0.0, q.record, q.peak_idx)
        return sorted(evs + cands, key=dist)
    if mode in ("lowest_score", "time"):
        for e in session.events:
            if not include_done and session.is_verified(e):
                continue
            items.append(QItem("event", e.record, e.peak_idx, model_score(review, e.record, e.peak_idx, tol)))
        if mode == "time" or review is None:
            items.sort(key=lambda q: (q.record, q.peak_idx))
        else:
            # model events by ascending score; events the model never scored (detected / added) after them, in time order
            items.sort(key=lambda q: (math.isnan(q.score), q.score if not math.isnan(q.score) else 0.0, q.record, q.peak_idx))
    elif mode == "borderline":
        if review is not None:
            thr = MR._logit(review.thr)
            win = review.shadow_ms / session.ts if review.shadow_ms > 0 else 0
            peaks = {}
            for e in session.events:                     # events you confirmed or added shadow a candidate too, not only accepted ones
                peaks.setdefault(e.record, []).append(e.peak_idx)
            for c in review.cands:
                if c.score < MR.MIN_SHOW or session.find_event(c.record, c.peak_idx, tol) is not None:
                    continue
                if review.is_shadowed(c.record, c.peak_idx) or any(0 < c.peak_idx - a <= win for a in peaks.get(c.record, ())):
                    continue
                if not include_done and session.decision(c.record, c.peak_idx) == "dismissed":
                    continue
                items.append(QItem("candidate", c.record, c.peak_idx, c.score))
            items.sort(key=lambda q: abs(MR._logit(q.score) - thr))
    elif mode == "suspects":
        for s in suspects or []:
            if session.find_event(s.record, s.peak_idx, tol) is not None:
                continue
            if not include_done and session.decision(s.record, s.peak_idx) is not None:
                continue
            items.append(QItem("suspect", s.record, s.peak_idx, snr=s.snr))
    else:
        raise ValueError(mode)
    return items


# ------------------------------------------------------------------------------------------------ the numbers
def stats(session: E.EventSession, review: Optional[MR.ReviewState], suspects: Optional[List[Suspect]], cov: Optional[Coverage]) -> Dict[str, float]:
    tol = max(1, int(round(4.0 / session.ts)))
    ev = session.events
    out = dict(
        events=len(ev),
        confirmed=sum(1 for e in ev if session.decision(e.record, e.peak_idx) == "confirmed"),
        added=sum(1 for e in ev if e.manual and session.decision(e.record, e.peak_idx) != "confirmed"),
        unverified=sum(1 for e in ev if not session.is_verified(e)),
        rejected=len(session.log.rejected),
        borderline_open=len(build_queue("borderline", session, review)) if review is not None else 0,
        suspects_open=len(build_queue("suspects", session, review, suspects)) if suspects is not None else 0,
        dismissed=len(session.log.dismissed),
        coverage=cov.fraction() if cov is not None else float("nan"),
    )
    return out


def record_ready_to_mark(session: E.EventSession, record: int, cov: Optional[Coverage], review: Optional[MR.ReviewState],
                         suspects: Optional[List[Suspect]]) -> bool:
    """A record can be marked fully reviewed when all of it was viewed, every event is verified and no candidate / suspect
    in it is still open."""
    if cov is None or cov.fraction(record) < 0.995:
        return False
    if any(not session.is_verified(e) for e in session.events if e.record == record):
        return False
    return not any(q.record == record for q in build_queue("borderline", session, review)) and \
        not any(q.record == record for q in build_queue("suspects", session, review, suspects))


# -------------------------------------------------------------------------------------------- zoom card data
def snippet(session: E.EventSession, record: int, peak_idx: int, pre_ms: float = 25.0, post_ms: float = 60.0):
    """(t_rel_ms, y, i_peak_in_window) around a peak, t_rel = 0 at the peak."""
    ts = session.ts
    a = max(0, peak_idx - int(round(pre_ms / ts)))
    b = min(session.Y.shape[1], peak_idx + int(round(post_ms / ts)) + 1)
    return (np.arange(a, b) - peak_idx) * ts, session.Y[record][a:b], peak_idx - a


def local_baseline(session: E.EventSession, record: int, peak_idx: int) -> float:
    """Baseline level just before a peak (median 12-4 ms before), for candidates that have no measured baseline."""
    ts = session.ts
    a, b = max(0, peak_idx - int(round(12.0 / ts))), max(1, peak_idx - int(round(4.0 / ts)))
    return float(np.median(session.Y[record][a:b])) if b > a else float(session.Y[record][peak_idx])


def ghost_template(session: E.EventSession, max_events: int = 300, pre_ms: float = 25.0, post_ms: float = 60.0):
    """Peak-aligned, baseline-subtracted average of the verified (else all) events, normalised to peak amplitude 1.
    Returns (t_rel_ms, template) or None. Drawn behind a candidate to judge its shape."""
    ts = session.ts
    verified = [e for e in session.events if session.is_verified(e) and e.fitted]
    ev = verified if len(verified) >= 3 else [e for e in session.events if e.fitted]      # fall back to all events until 3 are verified
    if len(ev) < 3:
        return None
    npre, npost = int(round(pre_ms / ts)), int(round(post_ms / ts))
    rows = []
    for e in ev[:max_events]:
        k = e.peak_idx
        if k - npre < 0 or k + npost >= session.Y.shape[1] or not np.isfinite(e.bl_im) or e.amplitude == 0:
            continue
        rows.append((session.Y[e.record][k - npre:k + npost + 1] - e.bl_im) / e.amplitude)
    if len(rows) < 3:
        return None
    return (np.arange(-npre, npost + 1) * ts), np.median(np.vstack(rows), axis=0)


# --------------------------------------------------------------------------------------------- progress file
def progress_path(recording_path: str, candidates_path: Optional[str] = None) -> str:
    base = candidates_path or recording_path
    return os.path.splitext(base)[0] + ".review_progress.json"


def log_to_dict(log: E.EditLog) -> dict:
    return dict(
        added=[[m.record, m.peak_idx, bool(m.fit), None if m.baseline is None else m.baseline[0],
                None if m.baseline is None else m.baseline[1]] for m in log.added],
        deleted=[list(k) for k in log.deleted],
        baselines=[[r, k, v[0], v[1]] for (r, k), v in log.baselines.items()],
        confirmed=sorted(map(list, log.confirmed)), rejected=sorted(map(list, log.rejected)), dismissed=sorted(map(list, log.dismissed)))


def log_from_dict(d: dict) -> E.EditLog:
    log = E.EditLog()
    for r, k, fit, bi, bl in d.get("added", []):
        log.added.append(E.ManualEvent(int(r), int(k), None if bi is None else (int(bi), float(bl)), bool(fit)))
    log.deleted = [(int(r), int(k)) for r, k in d.get("deleted", [])]
    log.baselines = {(int(r), int(k)): (int(i), float(v)) for r, k, i, v in d.get("baselines", [])}
    log.confirmed = {(int(r), int(k)) for r, k in d.get("confirmed", [])}
    log.rejected = {(int(r), int(k)) for r, k in d.get("rejected", [])}
    log.dismissed = {(int(r), int(k)) for r, k in d.get("dismissed", [])}
    return log


def save_progress(path: str, session: E.EventSession, review: Optional[MR.ReviewState], cov: Optional[Coverage],
                  recording_path: str, candidates_name: str = "", mode: str = "", pos: int = 0) -> str:
    d = dict(version=PROGRESS_VERSION, saved=time.strftime("%Y-%m-%d %H:%M:%S"), recording=os.path.basename(recording_path),
             n_records=int(session.Y.shape[0]), n_samples=int(session.Y.shape[1]), candidates=candidates_name,
             log=log_to_dict(session.log), reviewed_sweeps=sorted(review.reviewed) if review else [],
             visited=sorted(map(list, review.visited)) if review else [], coverage=cov.to_json() if cov else {}, mode=mode, pos=int(pos))
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(d, fh)
    os.replace(tmp, path)
    return path


def load_progress(path: str):
    """The saved progress dict, or None if missing / unreadable / from another version."""
    try:
        with open(path) as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    return d if d.get("version") == PROGRESS_VERSION else None


def progress_summary(d: dict) -> str:
    log = d.get("log", {})
    cov = d.get("coverage", {})
    return (f"saved {d.get('saved', '?')}: {len(log.get('confirmed', []))} confirmed, {len(log.get('rejected', []))} rejected, "
            f"{len(log.get('added', []))} added by hand, {len(log.get('dismissed', []))} dismissed, {len(d.get('reviewed_sweeps', []))} "
            f"records marked reviewed")


def apply_progress(d: dict, session: E.EventSession, review: Optional[MR.ReviewState], cov: Optional[Coverage]) -> bool:
    """Restore decisions / edits / coverage from a progress dict. False when it does not fit this recording."""
    if d.get("n_records") != session.Y.shape[0] or d.get("n_samples") != session.Y.shape[1]:
        return False
    session.log = log_from_dict(d.get("log", {}))
    session._undo.clear()
    session._redo.clear()
    session._rebuild()
    if review is not None:
        review.reviewed = {int(r) for r in d.get("reviewed_sweeps", [])}
        review.visited = {tuple(k) for k in d.get("visited", [])}
    if cov is not None:
        cov.load_json(d.get("coverage"))
    return True
