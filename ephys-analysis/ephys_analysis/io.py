"""Loading .abf files into a unit-normalised Recording (time in ms, Vm in mV, Im in pA)."""
import os
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

_V_SCALE = {"mV": 1.0, "V": 1e3, "uV": 1e-3}
_I_SCALE = {"pA": 1.0, "nA": 1e3, "uA": 1e6, "fA": 1e-3}


@dataclass
class Recording:
    path: str
    t: np.ndarray                 # ms, shape (N,)
    v: np.ndarray                 # mV, shape (n_sweeps, N)
    i: np.ndarray                 # pA, shape (n_sweeps, N)
    i_source: str                 # description of where Im came from
    channel_names: list


def _unit(u: str) -> str:
    return (u or "").replace("µ", "u").strip()


def load_abf(path: str, v_channel: Optional[int] = None,
             i_channel: Optional[int] = None) -> Recording:
    """Load an ABF. Vm = first channel in V units (or `v_channel`).

    Im = a recorded channel in A units if present (or `i_channel`), otherwise the
    command waveform stored in the file header (`sweepC`) of the Vm channel.
    """
    import pyabf

    abf = pyabf.ABF(path)
    units = [_unit(u) for u in abf.adcUnits]
    if v_channel is None:
        v_channel = next((c for c, u in enumerate(units) if u in _V_SCALE), 0)
    if i_channel is None:
        i_channel = next((c for c, u in enumerate(units)
                          if u in _I_SCALE and c != v_channel), None)

    vs, is_ = [], []
    for s in range(abf.sweepCount):
        abf.setSweep(s, channel=v_channel)
        vs.append(np.asarray(abf.sweepY, float) * _V_SCALE.get(units[v_channel], 1.0))
        if i_channel is not None:
            abf.setSweep(s, channel=i_channel)
            is_.append(np.asarray(abf.sweepY, float) * _I_SCALE[units[i_channel]])
        else:
            abf.setSweep(s, channel=v_channel)
            cu = _unit(abf.sweepUnitsC)
            is_.append(np.asarray(abf.sweepC, float) * _I_SCALE.get(cu, 1.0))
    abf.setSweep(0, channel=v_channel)
    t = np.asarray(abf.sweepX, float) * 1e3
    src = (f"recorded channel {i_channel} ({abf.adcNames[i_channel]})"
           if i_channel is not None else "command waveform from file header")
    return Recording(path, t, np.vstack(vs), np.vstack(is_), src, list(abf.adcNames))


def sibling_abfs(path: str) -> List[str]:
    """All .abf files in the folder of `path`, in natural name order (2 before 10)."""
    import re
    d = os.path.dirname(os.path.abspath(path))
    key = lambda n: [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", n)]
    names = sorted((n for n in os.listdir(d) if n.lower().endswith(".abf") and not n.startswith(".")), key=key)
    return [os.path.join(d, n) for n in names]
