"""Run a trained sEPSC classifier bundle on a recording and write a candidates CSV for the Events tab review workflow.

Bundle (pickle, produced by the training code that lives next to the weights): dict with
    rf            fitted sklearn classifier on the tabular features (optional)
    cnn_cfg       {"width", "drop", ...} CNN hyper-parameters
    prep_kw       kwargs for cnn.prep (e.g. {"pre": 100, "post": 400, "ms": True})
    cnn_states    list of CNN state_dicts (averaged)
    mu, sd        feature standardisation for the CNN's feature head
    threshold     score at or above which a candidate is accepted
    cin           number of CNN input channels (2 single scale, 4 multiscale)

Candidates come from the matched-filter + derivative detectors on a 10 kHz, 3 kHz low-passed copy of the trace; events within the first
30 ms or the last 120 ms of a sweep (no room for the wide window) are not scored. Test pulses are not masked here.
"""
import csv
import os
import pickle

import numpy as np

from . import core as C

COLUMNS = ["sweep", "time_in_sweep_ms", "score", "accepted", "amplitude_pA"]
MIN_KEEP = 0.25           # candidates below this score are not written (the review tool ignores them anyway)


def load_bundle(path):
    with open(path, "rb") as fh:
        return pickle.load(fh)


def _logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _nets(bundle):
    from . import cnn as N
    cfg = bundle["cnn_cfg"]
    nets = []
    for st in bundle["cnn_states"]:
        net = N.Net(len(C.FEAT_NAMES), cfg["width"], cfg["drop"], cin=bundle.get("cin", 2))
        net.load_state_dict(st)
        nets.append(net.to(N.DEV).eval())
    return nets


def score(bundle, X, Fm, nets=None):
    """X: (n, WPRE+WPOST) wide windows; Fm: (n, len(FEAT_NAMES)). Returns ensemble probability per candidate."""
    from . import cnn as N
    nets = nets or _nets(bundle)
    Fm = np.nan_to_num(Fm, nan=0.0, posinf=1e3, neginf=-1e3).astype(np.float32)
    Xp = N.prep(X, Fm, **bundle.get("prep_kw", {}))
    Fs = np.clip((Fm - bundle["mu"]) / bundle["sd"], -8, 8).astype(np.float32)
    idx = np.arange(len(Fm))
    z = [_logit(np.mean([N.predict(n, Xp, Fs, idx) for n in nets], 0))]
    if bundle.get("rf") is not None:
        z.append(_logit(bundle["rf"].predict_proba(Fm)[:, 1]))
    return 1 / (1 + np.exp(-np.mean(z, 0)))


def candidates(y_pa, fs):
    """Detect candidates in one sweep. Returns (peak indices at 10 kHz, wide windows, features, noise sd)."""
    y = C.prep_trace(y_pa, fs)
    pk, _ = C.detect_all(y, thr=2.5, k=2.5)
    pk = np.asarray(pk, int)
    sdv = C.noise_sd(y - np.convolve(y, np.ones(1000) / 1000, mode="same"))
    keep, X, Fl = [], [], []
    for j, c in enumerate(pk):
        wv, ww = C.window(y, int(c)), C.window_wide(y, int(c))
        if wv is None or ww is None:
            continue
        dprev = (c - pk[j - 1]) / 10.0 if j > 0 else 1000.0
        dnext = (pk[j + 1] - c) / 10.0 if j + 1 < len(pk) else 1000.0
        n_near = int(np.sum(np.abs(pk - c) <= 300)) - 1
        Fl.append(C.feats(wv, sdv, dprev, dnext, n_near))
        X.append(ww.astype(np.float32))
        keep.append(int(c))
    if not keep:
        return np.array([], int), np.zeros((0, C.WPRE + C.WPOST), np.float32), np.zeros((0, len(C.FEAT_NAMES)), np.float32), sdv
    return np.array(keep, int), np.stack(X), np.array(Fl, np.float32), sdv


def suppress_duplicates(sweep, idx, sc, tol=40):
    """Within 4 ms in the same sweep only the best-scoring candidate may be accepted (others get score 0)."""
    out = sc.copy()
    taken = {}
    for k in np.argsort(-sc):
        s, c = sweep[k], idx[k]
        if any(abs(c - q) <= tol for q in taken.get(s, ())):
            out[k] = 0.0
            continue
        taken.setdefault(s, []).append(c)
    return out


def classify_recording(rec, bundle, threshold=None, nets=None):
    """rec: ephys_suite.io.Recording. Returns rows (dicts with COLUMNS) for candidates scoring >= MIN_KEEP."""
    im = rec.im()
    if im is None:
        raise ValueError("recording has no current channel (pA)")
    thr = float(threshold if threshold is not None else bundle["threshold"])
    sw, ix, Xs, Fs = [], [], [], []
    for s in range(im.shape[0]):
        idx, X, Fm, _ = candidates(im[s], rec.fs)
        sw += [s] * len(idx); ix.append(idx); Xs.append(X); Fs.append(Fm)
    if not sw:
        return []
    idx, X, Fm, sweep = np.concatenate(ix), np.concatenate(Xs), np.concatenate(Fs), np.array(sw)
    sc = suppress_duplicates(sweep, idx, score(bundle, X, Fm, nets))
    rows = []
    for k in np.where(sc >= MIN_KEEP)[0]:
        rows.append(dict(sweep=int(sweep[k]) + 1, time_in_sweep_ms=round(idx[k] / C.FS * 1000.0, 2), score=round(float(sc[k]), 4),
                         accepted=int(sc[k] >= thr), amplitude_pA=round(float(-Fm[k, 0]), 2)))
    return rows


def write_candidates(rows, path):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


def main(argv=None):
    import argparse
    from ..io import load_abf
    ap = argparse.ArgumentParser(prog="ephys-suite-classify", description="Classify sEPSC candidates with a trained model bundle.")
    ap.add_argument("abf", nargs="+")
    ap.add_argument("--model", required=True, help="model bundle (.pkl)")
    ap.add_argument("--out", default=".", help="output folder for <name>.candidates.csv")
    ap.add_argument("--threshold", type=float, default=None, help="override the bundle's acceptance threshold")
    a = ap.parse_args(argv)
    bundle = load_bundle(a.model)
    nets = _nets(bundle)
    os.makedirs(a.out, exist_ok=True)
    for p in a.abf:
        rows = classify_recording(load_abf(p), bundle, a.threshold, nets)
        out = os.path.join(a.out, os.path.splitext(os.path.basename(p))[0] + ".candidates.csv")
        write_candidates(rows, out)
        print("%s: %d accepted of %d candidates -> %s" % (os.path.basename(p), sum(r["accepted"] for r in rows), len(rows), out))


if __name__ == "__main__":
    main()
