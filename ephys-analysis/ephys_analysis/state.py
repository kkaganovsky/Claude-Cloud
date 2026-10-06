"""Save / restore the full state of an analysis as a small JSON file next to the .abf.

The file records everything that determines the numbers: every analysis parameter (regions, estimator,
fit settings, rounding, 0 pA option, ...), which sweeps were checked, the .abf it belongs to (name, path
relative to the JSON, absolute path and a SHA-256 of its bytes), the software versions, and the results
(Rin, tau, Cm, ...). On restore the analysis is re-run from the parameters and the new results are compared
with the saved ones, so a mismatch (different data file or different code) is reported, never hidden.
"""
import dataclasses
import datetime as _dt
import hashlib
import json
import os
from typing import List, Optional

import numpy as np

from . import __version__
from .analysis import Params, Summary

FORMAT = "ephys-analysis-state"
FORMAT_VERSION = 1
SUFFIX = ".ephys-analysis.json"
RESULT_KEYS = ("n", "rin", "intercept", "r", "stderr", "tau", "tau_sd", "n_tau", "cm", "r0", "cm_r0")


def sidecar_path(abf_path: str) -> str:
    """'.../26827002.abf' -> '.../26827002.ephys-analysis.json'."""
    return os.path.splitext(abf_path)[0] + SUFFIX


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def params_to_dict(p: Params) -> dict:
    return {f.name: (list(v) if isinstance(v := getattr(p, f.name), tuple) else v)
            for f in dataclasses.fields(Params)}


def params_from_dict(d: dict) -> Params:
    """Unknown keys are ignored and missing keys keep their defaults (older / newer state files)."""
    p = Params()
    for f in dataclasses.fields(Params):
        if f.name in d:
            v = d[f.name]
            default = getattr(p, f.name)
            if isinstance(default, tuple):
                v = tuple(float(x) for x in v)
            elif isinstance(default, bool):
                v = bool(v)
            elif isinstance(default, int):
                v = int(v)
            elif isinstance(default, float):
                v = float(v)
            setattr(p, f.name, v)
    return p


def _num(x):
    x = float(x)
    return x if np.isfinite(x) else None          # JSON has no NaN


def summary_to_dict(S: Summary) -> dict:
    return {k: _num(getattr(S, k)) for k in RESULT_KEYS}


def build_state(abf_path: str, p: Params, sweeps: List[int], S: Summary, json_path: str,
                sha256: Optional[str] = None) -> dict:
    import scipy
    abf_abs = os.path.abspath(abf_path)
    return {
        "format": FORMAT, "format_version": FORMAT_VERSION,
        "saved": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "software": {"ephys-analysis": __version__, "numpy": np.__version__, "scipy": scipy.__version__},
        "abf": {"name": os.path.basename(abf_abs),
                "relative_path": os.path.relpath(abf_abs, os.path.dirname(os.path.abspath(json_path))),
                "absolute_path": abf_abs,
                "sha256": sha256 or file_sha256(abf_abs)},
        "sweeps_analysed": [int(s) for s in sweeps],
        "params": params_to_dict(p),
        "results": summary_to_dict(S),
    }


def save_state(json_path: str, abf_path: str, p: Params, sweeps: List[int], S: Summary) -> dict:
    st = build_state(abf_path, p, sweeps, S, json_path)
    tmp = json_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)
    os.replace(tmp, json_path)
    return st


def read_state(json_path: str) -> dict:
    with open(json_path, encoding="utf-8") as f:
        st = json.load(f)
    if st.get("format") != FORMAT:
        raise ValueError(f"{json_path} is not an ephys-analysis state file")
    return st


def resolve_abf(st: dict, json_path: str) -> Optional[str]:
    """The .abf a state file belongs to: path relative to the JSON first (survives moving the folder),
    then the absolute path, then the same file name next to the JSON."""
    a = st["abf"]
    here = os.path.dirname(os.path.abspath(json_path))
    for cand in (os.path.join(here, a.get("relative_path", "")), a.get("absolute_path", ""),
                 os.path.join(here, a.get("name", ""))):
        if cand and os.path.isfile(cand):
            return os.path.normpath(cand)
    return None


def compare_results(saved: dict, S: Summary, rel: float = 1e-6) -> List[str]:
    """Names of results that differ between a state file and a re-run (empty list = reproduced)."""
    now = summary_to_dict(S)
    bad = []
    for k in RESULT_KEYS:
        a, b = saved.get(k), now.get(k)
        if a is None and b is None:
            continue
        if a is None or b is None or abs(a - b) > rel * max(abs(a), abs(b), 1e-12):
            bad.append(f"{k}: saved {a}, now {b}")
    return bad
