"""ABF loading into a unit-normalised Recording: time ms, voltage mV, current pA."""
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

_SCALE = {"mV": 1.0, "V": 1e3, "uV": 1e-3, "pA": 1.0, "nA": 1e3, "uA": 1e6, "fA": 1e-3}


def _u(x: str) -> str:
    return (x or "").replace("µ", "u").strip()


@dataclass
class Recording:
    path: str
    t: np.ndarray                  # ms, shape (N,), time within a sweep
    ch: List[np.ndarray]           # per channel (n_sweeps, N), normalised units
    units: List[str]               # per channel, after normalisation
    names: List[str]
    sweep_start_ms: np.ndarray     # (n_sweeps,) start of each sweep on the recording clock
    command: Optional[np.ndarray] = None   # (n_sweeps, N) header command waveform of channel 0
    command_units: str = ""
    fs: float = 20000.0

    @property
    def n_sweeps(self):
        return self.ch[0].shape[0]

    def channel_by_units(self, units: str, default=None):
        for k, u in enumerate(self.units):
            if u == units:
                return k
        return default

    def vm(self):
        k = self.channel_by_units("mV")
        return None if k is None else self.ch[k]

    def im(self):
        """Recorded current channel if present, otherwise the header command waveform (pA)."""
        k = self.channel_by_units("pA")
        if k is not None:
            return self.ch[k]
        if self.command is not None and self.command_units == "pA":
            return self.command
        return None


def load_abf(path: str) -> Recording:
    import pyabf

    abf = pyabf.ABF(path)
    units = [_u(u) for u in abf.adcUnits]
    chans, outu = [], []
    for c in range(abf.channelCount):
        rows = []
        for s in range(abf.sweepCount):
            abf.setSweep(s, channel=c)
            rows.append(np.asarray(abf.sweepY, float) * _SCALE.get(units[c], 1.0))
        chans.append(np.vstack(rows))
        u = units[c]
        outu.append("mV" if u in ("mV", "V", "uV") else "pA" if u in ("pA", "nA", "uA", "fA") else u)
    abf.setSweep(0, channel=0)
    t = np.asarray(abf.sweepX, float) * 1e3
    cmd, cu = None, ""
    try:
        rows = []
        for s in range(abf.sweepCount):
            abf.setSweep(s, channel=0)
            rows.append(np.asarray(abf.sweepC, float))
        cu = _u(abf.sweepUnitsC)
        cmd = np.vstack(rows) * _SCALE.get(cu, 1.0)
        cu = "pA" if cu in ("pA", "nA", "uA", "fA") else "mV" if cu in ("mV", "V", "uV") else cu
    except Exception:
        cmd = None
    starts = np.array([s * abf.sweepIntervalSec * 1e3 for s in range(abf.sweepCount)])
    return Recording(path, t, chans, outu, list(abf.adcNames), starts, cmd, cu, float(abf.dataRate))
