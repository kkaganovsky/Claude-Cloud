"""Export model candidates to a standalone HTML review package for an outside reviewer, and (review_import.py) merge the answers back.

The package is a folder: START_HERE.html (plain JS + canvas, no libraries), data/*.js (event snippets) and README.txt. The reviewer
needs no install, Python or model weights. Snippets are prepared exactly as the classifier prepares its input (ml.core.prep_trace:
10 kHz, 4th-order zero-phase 3 kHz low-pass), so the reviewer sees what the model saw.

Page data is stored in .js files (not .json) because browsers block fetch() of local files opened from file://; a <script src> works.
Snippet format: int16, units of 1/SCALE pA, little-endian, base64; one snippet = (pre + post) ms at 10 kHz, median-subtracted.
Median subtraction means the snippet is for judging SHAPE, not for measuring amplitude.
"""
import argparse
import base64
import csv
import json
import os
import re
import secrets
import shutil
import sys
import time

import numpy as np

from . import model_review as MR
from .io import load_abf

FS_OUT = 10000
SCALE = 20                                  # int16 counts per pA (0.05 pA)
DEFAULT_PRE, DEFAULT_POST = 100.0, 200.0    # ms shown before / after the event
DEFAULT_MIN_SCORE = 0.5                     # lower scores are not sent out (the GUI's own MIN_SHOW is 0.25)
FALLBACK_THR = 0.9


# ------------------------------------------------------------------------------------------------ small helpers
def read_candidates(path):
    """Rows of a candidates CSV: dicts with sweep (1-based int), t (ms), score, accepted (0/1)."""
    rows = []
    with open(path, newline="") as fh:
        rd = csv.DictReader(fh)
        for need in MR.REQUIRED_COLS:
            if need not in (rd.fieldnames or []):
                raise ValueError(f"{os.path.basename(path)}: needs a '{need}' column")
        for r in rd:
            rows.append(dict(sweep=int(float(r["sweep"])), t=float(r["time_in_sweep_ms"]), score=float(r["score"]),
                             accepted=int(float(r["accepted"]))))
    return rows


def parse_kv(items, what):
    """['13=0.99', ...] -> {'13': '0.99'}"""
    out = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"{what}: expected KEY=VALUE, got '{it}'")
        k, v = it.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def parse_windows(items):
    """['13:100-130', '13:200-210'] -> {'13': [(100.0, 130.0), (200.0, 210.0)]} (seconds, on time_in_sweep_ms)."""
    out = {}
    for it in items or []:
        m = re.fullmatch(r"\s*(.+?):\s*([\d.]+)\s*-\s*([\d.]+)\s*", it)
        if not m or float(m.group(3)) <= float(m.group(2)):
            raise SystemExit(f"--exclude: expected KEY:START-STOP in seconds (e.g. 13:100-130), got '{it}'")
        out.setdefault(m.group(1), []).append((float(m.group(2)), float(m.group(3))))
    return out


def _keys(cell_id, abf):
    """Names a per-file option may use for a recording: its cell number (3, 03), file stem, or file name."""
    stem = os.path.splitext(os.path.basename(abf))[0]
    ks = {str(cell_id), str(cell_id).lstrip("0") or "0", stem, os.path.basename(abf)}
    return ks


def lookup(table, cell_id, abf, default=None):
    for k, v in table.items():
        if k in _keys(cell_id, abf):
            return v
    return default


def find_candidates_for(abf, candidates=None):
    """The candidates CSV that belongs to a recording: `candidates` may be a file (single recording) or a folder
    (`<stem>.candidates.csv` inside it); otherwise the same rule the GUI's auto-load uses (beside the recording / candidates/)."""
    stem = os.path.splitext(os.path.basename(abf))[0]
    if candidates and os.path.isfile(candidates):
        return candidates
    if candidates and os.path.isdir(candidates):
        for d in (candidates, os.path.join(candidates, "candidates")):
            f = os.path.join(d, stem + ".candidates.csv")
            if os.path.isfile(f):
                return f
        return None
    for f in MR.find_associated(abf):
        if f.endswith(".candidates.csv"):
            return f
    return None


# ------------------------------------------------------------------------------------------------ selection and snippets
def select_events(rows, min_score=DEFAULT_MIN_SCORE, thr=FALLBACK_THR, exclude=(), skip=None):
    """Events to send out, sorted by (sweep, time). Returns (events, info) with info counts of what was left out.

    Each event: dict(sweep, t, score, tier) where tier is 'detected' (score >= thr) or 'maybe'. `exclude` = [(start_s, stop_s)]
    windows on time_in_sweep_ms; `skip(sweep, t_ms) -> bool` drops events already decided elsewhere (e.g. in the GUI)."""
    keep, info = [], dict(low_score=0, excluded=0, skipped=0)
    for r in rows:
        if r["score"] < min_score:
            info["low_score"] += 1
            continue
        if any(a <= r["t"] / 1000.0 < b for a, b in exclude):
            info["excluded"] += 1
            continue
        if skip is not None and skip(r["sweep"], r["t"]):
            info["skipped"] += 1
            continue
        keep.append(dict(sweep=r["sweep"], t=round(r["t"], 2), score=round(r["score"], 4),
                         tier="detected" if r["score"] >= thr else "maybe"))
    keep.sort(key=lambda e: (e["sweep"], e["t"]))
    return keep, info


def make_snippets(rec, events, pre=DEFAULT_PRE, post=DEFAULT_POST, channel=None):
    """int16 array (n_events, L): classifier-prepared trace around each event, median-subtracted, 1/SCALE pA units."""
    from .ml.core import prep_trace
    ch = channel if channel is not None else rec.channel_by_units("pA", 0)
    L = int(round((pre + post) * FS_OUT / 1000))
    buf = np.zeros((len(events), L), np.int16)
    cache = {}
    for i, e in enumerate(events):
        sw = e["sweep"] - 1
        if not 0 <= sw < rec.n_sweeps:
            continue                                               # candidate in a sweep this recording does not have: blank
        if sw not in cache:
            cache = {sw: prep_trace(rec.ch[ch][sw], rec.fs)}      # one sweep at a time (sorted by sweep): bounded memory
        y = cache[sw]
        i0 = int(round((e["t"] - pre) * FS_OUT / 1000))
        seg = np.full(L, np.nan)
        a0, a1 = max(i0, 0), min(i0 + L, len(y))
        if a1 > a0:
            seg[a0 - i0:a1 - i0] = y[a0:a1]
        med = np.nanmedian(seg) if np.isfinite(seg).any() else 0.0
        seg = np.nan_to_num(seg - med, nan=0.0)                    # past the sweep edges: 0 (no data)
        buf[i] = np.clip(np.round(seg * SCALE), -32767, 32767).astype(np.int16)
    return buf


def decode_snippets(b64, n, L):
    """Inverse of the page's decoding: base64 -> (n, L) pA array (for tests / checking)."""
    return np.frombuffer(base64.b64decode(b64), dtype="<i2").reshape(n, L).astype(float) / SCALE


# ------------------------------------------------------------------------------------------------ the package
def _write_js(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _readme(pkg, cells, title):
    notes = []
    empty = [c["id"] for c in cells if c["n"] == 0]
    if empty:
        notes.append("Cell%s %s: nothing to review." % ("s" if len(empty) > 1 else "", ", ".join(empty)))
    for c in cells:
        if c.get("note"):
            notes.append(f"Cell {c['id']}: {c['note']}.")
    sizes = sorted((c["n"] for c in cells), reverse=True)
    if len(cells) > 3 and sizes[0] > 0:
        big = [c for c in cells if c["n"] >= max(sizes[min(1, len(sizes) - 1)], 1.5 * float(np.median(sizes)))]
        big = sorted(big, key=lambda c: -c["n"])[:3]
        if big and big[0]["n"] > 1.5 * float(np.median(sizes)):
            notes.append("Largest: " + ", ".join(f"cell {c['id']} ({c['n']} events)" for c in big) + ". Do those last.")
    event_word = "downward" if pkg["polarity"] == "inward" else "upward"
    first, last = cells[0]["id"], cells[-1]["id"]
    return f"""{title} - how to use
{'=' * (len(title) + 13)}

Nothing to install.

1. Unzip this folder anywhere (for example your Desktop). Keep the files together.
2. Double-click START_HERE.html. It opens in your web browser (Chrome works best).
3. Click a cell on the left (Cell {first} to Cell {last}).
4. For each event, look at the trace and press:
      Y  (or the green button)   = yes, it is a real event
      N  (or the red button)     = no, it is noise
      U  (or the purple button)  = not sure
      Left / Right arrow         = go back / forward
   The next event appears automatically.
5. Your answers save automatically in the browser, so you can close it and continue later.
   Use the SAME browser on the SAME computer each time.
6. When you finish, or at the end of each day, click "Download results for ALL cells"
   and send the CSV file (it goes to your Downloads folder) back by email.

What a real event looks like
----------------------------
- A fast {event_word} deflection, then a slower, smooth return to the baseline.
- NOT an event: a single very thin spike, or a wiggle no bigger than the surrounding noise.
- The dashed line marks where the computer found the event.
- "Detected" (green) = the computer thinks it is an event. "Maybe" (orange) = the computer is unsure.
  Judge both the same way.
- Tick "Smoother view" if the trace is too noisy to judge, and "Zoom in" to see the event's shape.
- You can only confirm or reject what is shown. Events the computer missed are not part of this review.

Notes
-----
{chr(10).join('- ' + n for n in notes) if notes else '- (none)'}

What is in the folder
---------------------
START_HERE.html   the review page
data/             the event snippets the page shows (do not edit)
manifest.json     what was exported (used when the answers are merged back)
recordings/       the original .abf recordings (only if they were included; not needed for the review)
"""


def build_package(pairs, out, *, min_score=DEFAULT_MIN_SCORE, thr=None, thr_per_file=None, exclude=None, pre=DEFAULT_PRE,
                  post=DEFAULT_POST, include_raw=False, title="Event review", polarity="inward", skip=None, make_zip=False,
                  log=print):
    """Write the review package into `out` (replaced if it exists). pairs: [(abf_path, candidates_csv_path), ...].

    thr: tier cut-off for all files (default: per file, the lowest score the model accepted, else 0.9). thr_per_file / exclude:
    {key: value} with key = cell number, file stem or file name. skip: callable(abf_path) -> None or a predicate
    (sweep, t_ms) -> bool that drops events already decided elsewhere. Returns the manifest dict."""
    if polarity not in ("inward", "outward"):
        raise ValueError("polarity must be 'inward' or 'outward'")
    thr_per_file, exclude = thr_per_file or {}, exclude or {}
    out = os.path.abspath(out)
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, "data"))
    width = max(2, len(str(len(pairs))))
    pkg_id = secrets.token_hex(4)
    cells, manifest_cells = [], []
    for n, (abf, cpath) in enumerate(pairs, 1):
        cid = str(n).zfill(width)
        rows = read_candidates(cpath)
        acc = [r["score"] for r in rows if r["accepted"]]
        t_file = float(lookup(thr_per_file, cid, abf, thr if thr is not None else (min(acc) if acc else FALLBACK_THR)))
        win = lookup(exclude, cid, abf, [])
        sk = skip(abf) if callable(skip) else skip
        events, info = select_events(rows, min_score, t_file, win, sk)
        rec = load_abf(abf)
        buf = make_snippets(rec, events, pre, post)
        b64 = base64.b64encode(buf.astype("<i2").tobytes()).decode()
        stem = os.path.splitext(os.path.basename(abf))[0]
        notes = []
        if win:
            notes.append(", ".join(f"{a:g}-{b:g} s" for a, b in win) + " skipped on purpose")
        if info["skipped"]:
            notes.append(f"{info['skipped']} already-decided event(s) left out")
        meta = dict(id=cid, label=stem, recording=os.path.basename(abf), file=os.path.basename(abf), n=len(events),
                    n_detected=sum(e["tier"] == "detected" for e in events), n_maybe=sum(e["tier"] == "maybe" for e in events),
                    thr=round(t_file, 4), note="; ".join(notes))
        ev = [[e["sweep"], e["t"], e["score"], e["tier"]] for e in events]
        _write_js(os.path.join(out, "data", f"cell{cid}.js"),
                  "window.CELLDATA=window.CELLDATA||{};window.CELLDATA['%s']=%s;window.CELLDATA['%s'].events=%s;"
                  "window.CELLDATA['%s'].b64='%s';" % (cid, json.dumps(meta), cid, json.dumps(ev), cid, b64))
        cells.append(meta)
        manifest_cells.append(dict(meta, candidates=os.path.basename(cpath), candidates_path=os.path.abspath(cpath),
                                   recording_path=os.path.abspath(abf), excluded_windows_s=win, **info))
        if include_raw:
            os.makedirs(os.path.join(out, "recordings"), exist_ok=True)
            shutil.copy2(abf, os.path.join(out, "recordings", os.path.basename(abf)))
        log(f"{cid} {stem}: {meta['n_detected']} detected, {meta['n_maybe']} maybe"
            f"{' (' + meta['note'] + ')' if meta['note'] else ''}; {info['low_score']} below {min_score:g}")
    ylim = [-45, 15] if polarity == "inward" else [-15, 45]
    pkg = dict(id=pkg_id, title=title, polarity=polarity, ylim=ylim, unit="pA", created=time.strftime("%Y-%m-%d %H:%M"))
    _write_js(os.path.join(out, "data", "cells.js"), "window.PKG=%s;window.CELLS=%s;window.SNIP={fs:%d,pre:%g,post:%g,scale:%d};"
              % (json.dumps(pkg), json.dumps(cells), FS_OUT, pre, post, SCALE))
    page = os.path.join(os.path.dirname(os.path.abspath(__file__)), "review_page.html")
    shutil.copy2(page, os.path.join(out, "START_HERE.html"))
    with open(os.path.join(out, "README.txt"), "w", encoding="utf-8") as fh:
        fh.write(_readme(pkg, cells, title))
    manifest = dict(package=pkg, snip=dict(fs=FS_OUT, pre=pre, post=post, scale=SCALE), min_score=min_score, cells=manifest_cells)
    with open(os.path.join(out, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)
    if make_zip:
        manifest["zip"] = shutil.make_archive(out, "zip", os.path.dirname(out), os.path.basename(out))
    log(f"package {pkg_id}: {sum(c['n'] for c in cells)} events in {len(cells)} recording(s) -> {out}")
    return manifest


# ------------------------------------------------------------------------------------------------ CLI
def main(argv=None):
    ap = argparse.ArgumentParser(prog="ephys-suite-review-export",
                                 description="Write a folder an outside reviewer can open in a browser (double-click START_HERE.html) "
                                             "to answer Yes / No / Not sure for each model-proposed event.")
    ap.add_argument("abf", nargs="+", help="recording(s)")
    ap.add_argument("--candidates", help="candidates CSV (one recording) or a folder with <name>.candidates.csv; "
                                         "default: the file beside each recording (the GUI's auto-load rule)")
    ap.add_argument("--out", required=True, help="output folder (replaced if it exists)")
    ap.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE, help="do not send candidates scored below this (default 0.5)")
    ap.add_argument("--thr", type=float, default=None, help="score at or above which an event is tiered 'detected' rather than 'maybe' "
                                                            "(default: per file, the lowest score the model accepted)")
    ap.add_argument("--thr-per-file", action="append", metavar="KEY=VALUE", help="threshold for one recording (KEY = cell number, file stem or name), e.g. 13=0.99")
    ap.add_argument("--exclude", action="append", metavar="KEY:START-STOP", help="leave out events in this time window of the sweep, in seconds, e.g. 13:100-130")
    ap.add_argument("--pre", type=float, default=DEFAULT_PRE, help="ms shown before the event (default 100)")
    ap.add_argument("--post", type=float, default=DEFAULT_POST, help="ms shown after the event (default 200)")
    ap.add_argument("--polarity", choices=("inward", "outward"), default="inward", help="inward events point down (sEPSC), outward up")
    ap.add_argument("--include-raw", action="store_true", help="also copy the .abf recordings into the package (large; not needed for review)")
    ap.add_argument("--title", default="Event review")
    ap.add_argument("--zip", action="store_true", help="also write <out>.zip, ready to email")
    a = ap.parse_args(argv)
    pairs, missing = [], []
    for abf in a.abf:
        c = find_candidates_for(abf, a.candidates)
        (pairs if c else missing).append((abf, c) if c else abf)
    for m in missing:
        print(f"no candidates file found for {m} (looked beside it and in --candidates); skipped", file=sys.stderr)
    if not pairs:
        raise SystemExit("nothing to export")
    build_package(pairs, a.out, min_score=a.min_score, thr=a.thr, thr_per_file=parse_kv(a.thr_per_file, "--thr-per-file"),
                  exclude=parse_windows(a.exclude), pre=a.pre, post=a.post, include_raw=a.include_raw, title=a.title,
                  polarity=a.polarity, make_zip=a.zip)
    return 0


if __name__ == "__main__":
    sys.exit(main())
