"""Access (series) resistance quality control from voltage-clamp test pulses.

Replaces the MATLAB workflow RaRm.m / test_pulsePasca.m (hard-coded sample indices, a fixed -10 mV step, a 100 ms pulse
at 20 kHz) with detection from the data:

  * the pulse onset / offset are found from the recorded command-voltage channel when there is one, otherwise from the two
    opposite capacitive transients in the current trace (so any sampling rate, sweep length, pulse position or duration);
  * the step size is the measured command step when a command channel exists, otherwise an assumed value (default -10 mV;
    its sign is taken from the direction of the onset transient) - the report says which.

Per sweep (formulas of test_pulsePasca.m):
    Ra  = dV / (I_peak - I_baseline) x 1000            [MOhm]  peak of the onset capacitive transient
    Rt  = dV / (I_steady - I_baseline) x 1000          [MOhm]  mean current late in the pulse
    Rm  = Rt - Ra
(Ra is the peak-based value, as in the lab's MATLAB script. The peak method over-estimates Ra when the transient is only
a few samples wide; the 'peak at the edge of the window' note flags unresolved transients.)

Per recording (RaRm.m): mean Ra / Rm over sweeps, and the exclusion rule 'any sweep's Ra >= 120 % or <= 80 % of the first
sweep's Ra'. The verdict adds the limit (default 25 MOhm) on the mean Ra.

Folders: EPSC recordings are .abf files in a folder whose name contains 'EPSC' (or with EPSC in the file name); separate
test-pulse recordings are .abf files in a folder named 'CM' (or named like membrane / test pulse). Files are grouped into
cells by their folder.
"""
import csv
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
from scipy import signal

from .common import ts_ms

_SCALE = {"mV": 1.0, "V": 1e3, "uV": 1e-3, "pA": 1.0, "nA": 1e3, "uA": 1e6, "fA": 1e-3}


@dataclass
class QCParams:
    limit_mohm: float = 25.0            # access resistance must be below this
    drift: float = 0.20                 # exclude when any sweep's Ra is >= (1+drift) or <= (1-drift) x the first sweep's
    v_step: float = -10.0               # mV; used only when no command channel is found (sign inferred from the data)
    search_ms: float = 1000.0           # where to look for the pulse (the whole sweep if nothing is found there)
    min_dur_ms: float = 5.0             # plausible pulse duration range
    max_dur_ms: float = 500.0
    baseline_ms: float = 25.0           # current baseline: this long before the onset (user's script: 25 ms)
    peak_ms: float = 5.0                # the capacitive peak is searched this long after the onset
    ss_frac: Tuple[float, float] = (0.80, 0.90)   # steady-state window as fractions of the pulse (script: 20-10 ms before
                                                  # the end of a 100 ms pulse)
    min_cmd_mV: float = 2.0             # smallest command step accepted as a pulse


@dataclass
class SweepRa:
    sweep: int
    ok: bool = False
    onset_ms: float = np.nan
    offset_ms: float = np.nan
    dv: float = np.nan
    dv_source: str = ""                 # 'command channel' | 'assumed'
    timing_source: str = ""             # 'command channel' | 'current transients'
    baseline: float = np.nan
    i_peak: float = np.nan
    i_ss: float = np.nan
    ra: float = np.nan
    rm: float = np.nan
    note: str = ""
    # for plotting
    base_win: Tuple[float, float] = (np.nan, np.nan)
    peak_t: float = np.nan
    ss_win: Tuple[float, float] = (np.nan, np.nan)


@dataclass
class FileQC:
    path: str
    role: str                            # 'EPSC' | 'CM'
    source: str                          # 'embedded pulse' | 'CM file'
    cell: str = ""
    line: str = ""
    sweeps: List[SweepRa] = field(default_factory=list)
    n_sweeps: int = 0
    n_ok: int = 0
    ra_mean: float = np.nan
    ra_max: float = np.nan
    ra_first: float = np.nan
    rm_mean: float = np.nan
    dv_source: str = ""
    drift_exclude: bool = False
    status: str = "NO PULSE"             # PASS | WARN | FAIL | NO PULSE | ERROR
    reasons: List[str] = field(default_factory=list)
    error: str = ""

    @property
    def name(self):
        return os.path.basename(self.path)


@dataclass
class CellQC:
    cell: str
    line: str
    files: List[FileQC]
    status: str = "NO PULSE"
    ra_mean: float = np.nan              # worst (highest) per-file mean Ra
    ra_max: float = np.nan
    drift: bool = False                  # any file's Ra drifts beyond the limit set by QCParams.drift
    reasons: List[str] = field(default_factory=list)


# ------------------------------------------------------------------------------------------------- reading
def read_sweeps(path: str):
    """(sweeps, info): sweeps = list of (t_ms, I_pA, V_mV or None); sweeps may differ in length."""
    import pyabf

    abf = pyabf.ABF(path)
    units = [(u or "").replace("µ", "u").strip() for u in abf.adcUnits]
    ci = next((c for c, u in enumerate(units) if u in ("pA", "nA", "uA", "fA")), None)
    vi = next((c for c, u in enumerate(units) if u in ("mV", "V", "uV")), None)
    cu = (getattr(abf, "sweepUnitsC", "") or "").replace("µ", "u").strip()
    info = dict(units=units, names=list(abf.adcNames), rate=float(abf.dataRate), current_channel=ci, command_channel=vi,
                header_command=vi is None and cu in ("mV", "V", "uV"))
    if ci is None:
        return [], info
    out = []
    for s in range(abf.sweepCount):
        abf.setSweep(s, channel=ci)
        t = np.asarray(abf.sweepX, float) * 1e3
        I = np.asarray(abf.sweepY, float) * _SCALE[units[ci]]
        V = None
        if vi is not None:
            abf.setSweep(s, channel=vi)
            V = np.asarray(abf.sweepY, float) * _SCALE[units[vi]]
        elif cu in ("mV", "V", "uV"):                 # no recorded voltage channel: the protocol's command waveform is in the header
            abf.setSweep(s, channel=ci)
            V = np.asarray(abf.sweepC, float) * _SCALE[cu]
        out.append((t, I, V))
    return out, info


# ------------------------------------------------------------------------------------------------ detection
def _hold(x):
    k = max(1, len(x) // 50)
    return float(np.median(np.concatenate([x[:k], x[-k:]])))


def _run_around(mask, k):
    a = k
    while a > 0 and mask[a - 1]:
        a -= 1
    b = k
    while b + 1 < len(mask) and mask[b + 1]:
        b += 1
    return a, b


def pulse_from_command(t, V, p: QCParams, lim: int):
    """(onset_idx, offset_idx, dV) from the command channel, or None."""
    if V is None or len(V) < 10:
        return None
    Vs = signal.medfilt(V, 3)
    dev = Vs - _hold(Vs)
    k = int(np.argmax(np.abs(dev[:lim])))
    peak = dev[k]
    if abs(peak) < p.min_cmd_mV:
        return None
    mask = dev * np.sign(peak) > 0.5 * abs(peak)
    a, b = _run_around(mask, k)
    dt = ts_ms(t)
    dur = (b + 1 - a) * dt
    if not (p.min_dur_ms <= dur <= p.max_dur_ms):
        return None
    n = max(2, int(0.1 * (b - a)))
    return a, b + 1, float(np.median(dev[a + n:b + 1 - n]))


def pulse_from_current(t, I, p: QCParams, lim: int):
    """(onset_idx, offset_idx, sign_of_dV) from the two opposite capacitive transients, or None."""
    if len(I) < 20:
        return None
    dt = ts_ms(t)
    Is = np.convolve(I, np.ones(3) / 3.0, mode="same")
    d = np.diff(Is)
    ad = np.abs(d[:lim])
    if ad.size < 5:
        return None
    noise = 1.4826 * float(np.median(np.abs(d - np.median(d))))
    mx = float(ad.max())
    if mx < max(12 * noise, 1e-9):
        return None
    peaks, _ = signal.find_peaks(ad, height=0.3 * mx, distance=max(1, int(round(1.0 / dt))))
    best = None
    for i, a in enumerate(peaks):
        for b in peaks[i + 1:]:
            dur = (b - a) * dt
            if dur < p.min_dur_ms:
                continue
            if dur > p.max_dur_ms:
                break
            if d[a] * d[b] >= 0:                      # must be opposite polarity
                continue
            score = min(abs(d[a]), abs(d[b]))
            if best is None or score > best[0] * 1.1:
                best = (score, int(a), int(b))
    if best is None:
        return None
    _, a, b = best
    # the first transient is the onset: a hyperpolarising step gives a negative onset transient
    return a + 1, b + 1, float(np.sign(d[a]))


def find_pulse(t, I, V, p: QCParams):
    """Locate the test pulse in one sweep. Returns dict(onset, offset, dv, dv_source, timing_source, notes) or None."""
    n = len(t)
    for lim in (min(n - 1, int(round(p.search_ms / ts_ms(t)))), n - 1):
        cmd = pulse_from_command(t, V, p, lim)
        cur = pulse_from_current(t, I, p, lim)
        notes = []
        if cmd is not None:
            on, off, dv = cmd
            if cur is not None and abs(cur[0] - on) * ts_ms(t) > 2.0:
                notes.append(f"current transient and command onset differ by {abs(cur[0] - on) * ts_ms(t):.1f} ms")
            return dict(onset=on, offset=off, dv=dv, dv_source="command channel", timing_source="command channel", notes=notes)
        if cur is not None:
            on, off, sign = cur
            sign = sign if sign != 0 else -1.0
            return dict(onset=on, offset=off, dv=abs(p.v_step) * sign, dv_source="assumed",
                        timing_source="current transients", notes=[])
        if lim >= n - 1:
            break
    return None


# -------------------------------------------------------------------------------------------- measurement
def measure_sweep(sweep: int, t, I, V, p: QCParams) -> SweepRa:
    r = SweepRa(sweep)
    f = find_pulse(t, I, V, p)
    if f is None:
        r.note = "no test pulse found"
        return r
    dt = ts_ms(t)
    on, off, dv = f["onset"], f["offset"], f["dv"]
    r.onset_ms, r.offset_ms, r.dv = float(t[on]), float(t[min(off, len(t) - 1)]), float(dv)
    r.dv_source, r.timing_source = f["dv_source"], f["timing_source"]
    notes = list(f["notes"])
    nb = max(3, int(round(p.baseline_ms / dt)))
    b0 = max(0, on - nb)
    if on - b0 < 3:
        r.note = "no baseline before the pulse"
        return r
    r.baseline = float(np.mean(I[b0:on]))
    r.base_win = (float(t[b0]), float(t[on - 1]))
    dur = (off - on) * dt
    npk = max(2, int(round(min(p.peak_ms, 0.25 * dur) / dt)))
    seg = I[on:on + npk + 1] - r.baseline
    k = int(np.argmin(seg)) if dv < 0 else int(np.argmax(seg))
    r.i_peak = float(seg[k])
    r.peak_t = float(t[on + k])
    if k == len(seg) - 1:
        notes.append("peak at the edge of the search window (transient not resolved)")
    a0 = on + int(round(p.ss_frac[0] * (off - on)))
    a1 = on + int(round(p.ss_frac[1] * (off - on)))
    a1 = max(a1, a0 + 3)
    r.ss_win = (float(t[a0]), float(t[min(a1, len(t) - 1)]))
    r.i_ss = float(np.mean(I[a0:a1 + 1]) - r.baseline)
    if r.i_peak == 0 or r.i_ss == 0:
        r.note = "zero current"
        return r
    r.ra = dv / r.i_peak * 1000.0
    rt = dv / r.i_ss * 1000.0
    r.rm = rt - r.ra
    if r.ra <= 0:
        notes.append("Ra <= 0 (peak current has the wrong sign)")
    r.ok = r.ra > 0
    r.note = "; ".join(notes)
    return r


def analyse_file(path: str, role: str, source: str, p: QCParams, cell: str = "", line: str = "") -> FileQC:
    fq = FileQC(path, role, source, cell, line)
    try:
        sweeps, info = read_sweeps(path)
    except Exception as e:                                           # unreadable file
        fq.status, fq.error = "ERROR", f"{type(e).__name__}: {e}"
        fq.reasons.append(f"could not read: {e}")
        return fq
    if not sweeps:
        fq.status = "NO PULSE"
        fq.reasons.append("no current channel (not a voltage-clamp recording?)")
        return fq
    fq.sweeps = [measure_sweep(i, t, I, V, p) for i, (t, I, V) in enumerate(sweeps)]
    _summarise(fq, p)
    return fq


def _summarise(fq: FileQC, p: QCParams):
    fq.n_sweeps = len(fq.sweeps)
    ok = [s for s in fq.sweeps if s.ok]
    fq.n_ok = len(ok)
    fq.reasons = []
    if not ok:
        fq.status = "NO PULSE"
        fq.reasons.append(fq.sweeps[0].note if fq.sweeps and fq.sweeps[0].note else "no test pulse found")
        return
    ra = np.array([s.ra for s in ok])
    fq.ra_mean, fq.ra_max, fq.ra_first = float(ra.mean()), float(ra.max()), float(ra[0])
    fq.rm_mean = float(np.mean([s.rm for s in ok]))
    fq.dv_source = ok[0].dv_source
    fq.drift_exclude = bool(np.any(ra >= ra[0] * (1 + p.drift)) or np.any(ra <= ra[0] * (1 - p.drift)))
    status = "PASS"                       # the verdict is about the Ra limit and pulse detection only; drift is a separate flag
    if fq.ra_mean >= p.limit_mohm:
        status = "FAIL"
        fq.reasons.append(f"mean Ra {fq.ra_mean:.1f} >= {p.limit_mohm:g} MOhm")
    elif fq.ra_max >= p.limit_mohm:
        status = "WARN"
        fq.reasons.append(f"{int(np.sum(ra >= p.limit_mohm))} sweep(s) with Ra >= {p.limit_mohm:g} (max {fq.ra_max:.1f})")
    if fq.drift_exclude:
        fq.reasons.append(f"drift flag: Ra moves >{p.drift * 100:.0f} % from the first sweep ({ra.min():.1f} to {ra.max():.1f})")
    if fq.n_ok < fq.n_sweeps:
        status = "WARN" if status == "PASS" else status
        fq.reasons.append(f"pulse not found in {fq.n_sweeps - fq.n_ok} of {fq.n_sweeps} sweeps")
    if fq.dv_source == "assumed":
        fq.reasons.append(f"step size assumed ({p.v_step:g} mV; no command channel)")
    fq.status = status


# --------------------------------------------------------------------------------------------- discovery
_EPSC = re.compile(r"epsc", re.I)
_CM_DIR = re.compile(r"(?<![a-z])cm(?![a-z])", re.I)
_CM_FILE = re.compile(r"membrane|test.?pulse|seal.?test|(?<![a-z])cm(?![a-z])", re.I)
_SKIP_DIR = re.compile(r"^(\.|__macosx)", re.I)


def _natural(s):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", s)]


def classify(root: str, path: str):
    """(role, source, cell, line) of an .abf under root, or None when it is neither an EPSC nor a test-pulse recording."""
    rel = os.path.relpath(path, root)
    parts = rel.split(os.sep)
    dirs, name = parts[:-1], parts[-1]
    idx, dir_role = None, None
    for i in range(len(dirs) - 1, -1, -1):                   # nearest role folder (EPSC / CM) above the file
        if _EPSC.search(dirs[i]):
            idx, dir_role = i, "EPSC"
            break
        if _CM_DIR.search(dirs[i]):
            idx, dir_role = i, "CM"
            break
    if _EPSC.search(name):
        role = "EPSC"
    elif dir_role is not None:
        role = dir_role
    elif _CM_FILE.search(name):
        role = "CM"
    else:
        return None
    cell_parts = dirs[:idx] if idx is not None else dirs     # the cell is the folder above the EPSC / CM folder
    cell = "/".join(cell_parts) if cell_parts else "(root)"
    line = dirs[0] if dirs else ""
    return role, ("embedded pulse" if role == "EPSC" else "CM file"), cell, line


def discover(root: str):
    """All classified .abf files under root: list of (path, role, source, cell, line), plus skipped file count."""
    found, skipped = [], 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted((d for d in dirnames if not _SKIP_DIR.match(d)), key=_natural)
        for fn in sorted(filenames, key=_natural):
            if not fn.lower().endswith(".abf") or fn.startswith("."):
                continue
            path = os.path.join(dirpath, fn)
            c = classify(root, path)
            if c is None:
                skipped += 1
                continue
            found.append((path,) + c)
    return found, skipped


@dataclass
class QCReport:
    root: str
    params: QCParams
    files: List[FileQC]
    cells: List[CellQC]
    skipped: int = 0
    warnings: List[str] = field(default_factory=list)


_RANK = {"ERROR": 4, "FAIL": 3, "WARN": 2, "NO PULSE": 1, "PASS": 0}


def scan(root: str, p: Optional[QCParams] = None, progress: Optional[Callable[[int, int, str], None]] = None) -> QCReport:
    p = p or QCParams()
    found, skipped = discover(root)
    files = []
    for i, (path, role, source, cell, line) in enumerate(found):
        if progress:
            progress(i, len(found), path)
        files.append(analyse_file(path, role, source, p, cell, line))
    if progress:
        progress(len(found), len(found), "")
    return QCReport(root, p, files, group_cells(files, p), skipped, layout_warnings(files))


def group_cells(files: List[FileQC], p: QCParams) -> List[CellQC]:
    cells: Dict[Tuple[str, str], CellQC] = {}
    for f in files:
        c = cells.setdefault((f.line, f.cell), CellQC(f.cell, f.line, []))
        c.files.append(f)
    out = []
    for c in cells.values():
        measured = [f for f in c.files if f.status in ("PASS", "WARN", "FAIL")]
        if not measured:
            c.status = "ERROR" if any(f.status == "ERROR" for f in c.files) else "NO PULSE"
            c.reasons.append("no test pulse could be measured in any file of this cell")
        else:
            c.status = max((f.status for f in measured), key=lambda s: _RANK[s])
            c.ra_mean = max(f.ra_mean for f in measured)
            c.ra_max = max(f.ra_max for f in measured)
            c.drift = any(f.drift_exclude for f in measured)
            for f in c.files:
                for r in f.reasons:
                    c.reasons.append(f"{f.role}/{f.name}: {r}")
        out.append(c)
    return sorted(out, key=lambda c: (_natural(c.line), _natural(c.cell)))


def layout_warnings(files: List[FileQC]):
    w = []
    by = {}
    for f in files:
        by.setdefault((f.line, f.cell), []).append(f)
    for (line, cell), fs in by.items():
        ne = sum(f.role == "EPSC" for f in fs)
        nc = sum(f.role == "CM" for f in fs)
        if ne and not nc:
            w.append(f"{cell}: {ne} EPSC file(s), no CM test-pulse file (Ra from the embedded pulse only)")
        if nc and not ne:
            w.append(f"{cell}: {nc} CM file(s) but no EPSC file")
        if ne > 1 and nc > 1:
            w.append(f"{cell}: {ne} EPSC and {nc} CM files share one cell folder - check that they belong to the same cell")
    return w


# ---------------------------------------------------------------------------------------------- reporting
FILE_COLUMNS = ["Line", "Cell", "Role", "Source", "File", "Sweeps", "Pulse found", "Ra mean (MOhm)", "Ra max (MOhm)",
                "Ra first (MOhm)", "Rm mean (MOhm)", "RaExclude (drift)", "Step size", "Status", "Notes",
                "Path"]
SWEEP_COLUMNS = ["Line", "Cell", "Role", "File", "Sweep", "Onset (ms)", "Offset (ms)", "dV (mV)", "dV source",
                 "Timing source", "I baseline (pA)", "I peak (pA)", "I steady (pA)", "Ra (MOhm)", "Rm (MOhm)", "Note"]
CELL_COLUMNS = ["Line", "Cell", "Files", "Ra mean, worst file (MOhm)", "Ra max (MOhm)", "Status", "RaExclude (drift)", "Notes"]


def _f(x, nd=2):
    return "" if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), nd)


def file_rows(rep: QCReport):
    return [[f.line, f.cell, f.role, f.source, f.name, f.n_sweeps, f.n_ok, _f(f.ra_mean), _f(f.ra_max), _f(f.ra_first),
             _f(f.rm_mean, 1), int(f.drift_exclude), f.dv_source, f.status, "; ".join(f.reasons), f.path]
            for f in rep.files]


def sweep_rows(rep: QCReport):
    out = []
    for f in rep.files:
        for s in f.sweeps:
            out.append([f.line, f.cell, f.role, f.name, s.sweep, _f(s.onset_ms), _f(s.offset_ms), _f(s.dv, 3), s.dv_source,
                        s.timing_source, _f(s.baseline), _f(s.i_peak), _f(s.i_ss), _f(s.ra), _f(s.rm, 1), s.note])
    return out


def cell_rows(rep: QCReport):
    return [[c.line, c.cell, len(c.files), _f(c.ra_mean), _f(c.ra_max), c.status, int(c.drift), "; ".join(c.reasons)] for c in rep.cells]


def summary_text(rep: QCReport) -> str:
    lim = rep.params.limit_mohm
    lines = [f"Access resistance QC  (limit: Ra < {lim:g} MOhm)  root: {rep.root}",
             f"{len(rep.files)} recordings in {len(rep.cells)} cells ({rep.skipped} .abf files ignored: not EPSC / CM)", ""]
    fmt = "%-12s %-26s %-5s %-14s %-24s %4s %8s %8s %8s  %s"
    lines.append(fmt % ("line", "cell", "role", "source", "file", "n", "Ra mean", "Ra max", "Rm mean", "status"))
    for f in rep.files:
        lines.append(fmt % (f.line[:12], f.cell[:26], f.role, f.source, f.name[:24], f"{f.n_ok}/{f.n_sweeps}",
                            "-" if not np.isfinite(f.ra_mean) else f"{f.ra_mean:.1f}",
                            "-" if not np.isfinite(f.ra_max) else f"{f.ra_max:.1f}",
                            "-" if not np.isfinite(f.rm_mean) else f"{f.rm_mean:.0f}", f.status))
    lines.append("")
    for line in sorted({c.line for c in rep.cells}, key=_natural):
        cs = [c for c in rep.cells if c.line == line]
        cnt = {k: sum(c.status == k for c in cs) for k in ("PASS", "WARN", "FAIL", "NO PULSE", "ERROR")}
        lines.append(f"{line or '(root)'}: {len(cs)} cells - " + ", ".join(f"{v} {k}" for k, v in cnt.items() if v))
    bad = [c for c in rep.cells if c.status != "PASS"]
    drift = [c for c in rep.cells if c.drift]
    lines.append("")
    lines.append(f"Ra below {lim:g} MOhm (every sweep, pulse found in every sweep): {len(rep.cells) - len(bad)} of {len(rep.cells)} cells.")
    for c in bad:
        lines.append(f"  {c.status:8s} {c.cell}: " + "; ".join(c.reasons))
    lines.append(f"Ra drift flag (>{rep.params.drift * 100:.0f} % from the first sweep, your RaExclude rule): {len(drift)} of {len(rep.cells)} cells.")
    for c in drift:
        lines.append(f"  drift    {c.cell}: " + "; ".join(r for r in c.reasons if "drift flag" in r))
    for w in rep.warnings:
        lines.append(f"  layout: {w}")
    return "\n".join(lines)


def _color(status):
    return {"PASS": "C6EFCE", "WARN": "FFEB9C", "FAIL": "FFC7CE", "NO PULSE": "D9D9D9", "ERROR": "FFC7CE"}.get(status)


def write_xlsx(rep: QCReport, path: str):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    for title, cols, rows, scol in (("Cells", CELL_COLUMNS, cell_rows(rep), 5), ("Files", FILE_COLUMNS, file_rows(rep), 13),
                                    ("Sweeps", SWEEP_COLUMNS, sweep_rows(rep), None)):
        ws = wb.active if title == "Cells" else wb.create_sheet(title)
        ws.title = title
        ws.append(cols)
        for c in ws[1]:
            c.font = Font(bold=True)
        for r in rows:
            ws.append(r)
            if scol is not None and _color(r[scol]):
                ws.cell(row=ws.max_row, column=scol + 1).fill = PatternFill("solid", fgColor=_color(r[scol]))
        for i, col in enumerate(cols, 1):
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = max(10, min(40, len(col) + 2))
        ws.freeze_panes = "A2"
    ws = wb.create_sheet("Settings")
    for k, v in asdict(rep.params).items():
        ws.append([k, str(v)])
    ws.append(["root", rep.root])
    wb.save(path)


def write_csv(rep: QCReport, stem: str):
    """Writes <stem>_cells.csv, <stem>_files.csv and <stem>_sweeps.csv; returns the paths."""
    out = []
    for suffix, cols, rows in (("cells", CELL_COLUMNS, cell_rows(rep)), ("files", FILE_COLUMNS, file_rows(rep)),
                               ("sweeps", SWEEP_COLUMNS, sweep_rows(rep))):
        p = f"{stem}_{suffix}.csv"
        with open(p, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            w.writerows(rows)
        out.append(p)
    return out


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(prog="ephys-suite-qc", description="Check access resistance (Ra) of every EPSC cell from its test pulses.")
    ap.add_argument("root", help="folder containing the cells (e.g. 'hFA in vitro 10162026')")
    ap.add_argument("--limit", type=float, default=25.0, help="Ra must be below this, MOhm (default 25)")
    ap.add_argument("--vstep", type=float, default=-10.0, help="test-pulse step in mV, used only when no command channel is recorded (default -10)")
    ap.add_argument("--drift", type=float, default=0.20, help="exclude when Ra moves more than this fraction from the first sweep (default 0.20)")
    ap.add_argument("--out", help="write an .xlsx report to this path (default: <root>/Ra_QC_<date>.xlsx)")
    ap.add_argument("--csv", action="store_true", help="also write CSV files next to the .xlsx")
    ap.add_argument("--no-xlsx", action="store_true", help="print only")
    a = ap.parse_args(argv)
    p = QCParams(limit_mohm=a.limit, v_step=a.vstep, drift=a.drift)
    rep = scan(a.root, p, progress=lambda i, n, f: print(f"\r[{i}/{n}] {os.path.basename(f):40s}", end="", flush=True) if n else None)
    print("\r" + " " * 60 + "\r", end="")
    print(summary_text(rep))
    if not a.no_xlsx:
        import datetime
        out = a.out or os.path.join(a.root, f"Ra_QC_{datetime.date.today():%Y-%m-%d}.xlsx")
        try:
            write_xlsx(rep, out)
            print(f"\nReport: {out}")
        except ImportError:
            print("\nopenpyxl not installed: writing CSV instead (pip install openpyxl for .xlsx)")
            a.csv = True
            out = os.path.splitext(out)[0] + ".xlsx"
        if a.csv:
            for pth in write_csv(rep, os.path.splitext(out)[0]):
                print("Report:", pth)
    return 0 if all(c.status == "PASS" for c in rep.cells) else 1


if __name__ == "__main__":
    raise SystemExit(main())
