"""Make a PRACTICE candidates file for the review workflow without a trained classifier.

    python scripts/make_demo_candidates.py recording.abf [--out cands.csv] [--omit 0 400]

The candidates come from the ordinary deconvolution detector, not from the CNN: the score is a simple function of each
event's amplitude / noise ratio (score 0.5 at SNR 4.5), `accepted` = score >= 0.5. Weak detections (SNR 3.7 - 4.5) become
borderline candidates, so every queue in the Events tab (least sure first, borderline, possible misses) has something in it.
Use it to learn the workflow; use `ephys-suite-classify` for real model output.
"""
import argparse
import csv
import dataclasses
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from ephys_suite import events as E                      # noqa: E402
from ephys_suite import verify as V                      # noqa: E402
from ephys_suite.io import load_abf                      # noqa: E402

SNR_MID, SNR_SLOPE, SNR_FLOOR = 4.5, 1.2, 3.7


def score_from_snr(snr: float) -> float:
    return 1.0 / (1.0 + math.exp(-(snr - SNR_MID) / SNR_SLOPE))


def make_candidates(path: str, omit=((0.0, 0.0),), channel=None):
    """Rows (sweep[1-based], time_in_sweep_ms, score, accepted, amplitude_pA) for every detection with SNR >= 3.7."""
    rec = load_abf(path)
    ch = channel if channel is not None else rec.channel_by_units("pA", 0)
    omit = tuple((a, b) for a, b in omit if b > a)
    p = dataclasses.replace(E.EventParams(), method="deconvolution", deconv_sd=2.5, lower_mode="none", amplitude_threshold=0.0,
                            fit_decay=False, omit=omit)
    res = E.analyse_events(rec.t, rec.ch[ch], p, rec.sweep_start_ms)
    noise = [V.robust_noise(rec.ch[ch][r], rec.t[1] - rec.t[0]) for r in range(rec.ch[ch].shape[0])]
    rows = []
    for e in res.events:
        snr = abs(e.amplitude) / noise[e.record]
        if snr < SNR_FLOOR:
            continue
        s = score_from_snr(snr)
        rows.append((e.record + 1, round(e.peak_t, 3), round(s, 4), int(s >= 0.5), round(e.amplitude, 2)))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("abf")
    ap.add_argument("--out", help="output CSV (default: <recording>.candidates.csv next to the recording)")
    ap.add_argument("--omit", nargs=2, type=float, metavar=("START_MS", "STOP_MS"), default=[0.0, 0.0],
                    help="ignore this time range in every sweep (e.g. the test pulse: 0 400)")
    a = ap.parse_args(argv)
    rows = make_candidates(a.abf, (tuple(a.omit),))
    out = a.out or os.path.splitext(a.abf)[0] + ".candidates.csv"
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sweep", "time_in_sweep_ms", "score", "accepted", "amplitude_pA"])
        w.writerows(rows)
    acc = sum(r[3] for r in rows)
    print(f"{len(rows)} candidates ({acc} accepted, {len(rows) - acc} borderline) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
