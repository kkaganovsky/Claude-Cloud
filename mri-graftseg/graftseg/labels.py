"""Which Slicer segment is the graft? Rules from the label-name audit of the Assembloid / PCDH data.

Decided with the annotator (see docs/DATA_DECISIONS.md):

* **Graft**: names like "Organoid" (and typos), "Assembloid", "Graft", or the animal's own ID pasted in as the name
  (e.g. "Assembloid_23_R1M_6mo", "PCDH_14_R1F_2mo"). An unnamed "Segment_1" counts as graft only when it is the
  first segment, except for Assembloid_39_R2M, whose unnamed Segment_1 was excluded after visual review.
* **Fluid** (ventricle / CSF): "Fluid", "Ventricle?", "CSF". Used only when the dataset is built with fluid as
  class 2; otherwise ignored.
* **Ignored**: "Black spot" / "Black point" (and typos), "background" / "Brain" (whole-brain box ROIs drawn as a
  Slicer workflow step), every other unnamed "Segment_N".
* Anything else is "unknown": ignored and listed in the audit so it can be added to a rule.
"""
from __future__ import annotations

import difflib
import re
from typing import Iterable, Optional

from .naming import AnimalId, parse_animal

GRAFT, FLUID, IGNORE, UNKNOWN = "graft", "fluid", "ignore", "unknown"

# Unnamed Segment_1 graft labels rejected after visual review (row 3 of the Segment_1 review figure).
EXCLUDE_UNNAMED_GRAFT = {"assembloid_39_r2m"}

_GRAFT_WORDS = ("organoid", "assembloid", "graft", "transplant")
_FLUID_WORDS = ("fluid", "ventricle", "ventrical", "csf")
_IGNORE_WORDS = ("black spot", "black point", "blackspot", "blackpoint", "background", "brain")
_GENERIC = re.compile(r"^\s*segment[ _-]*(\d+)\s*$", re.I)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z ]+", " ", name.lower()).strip()


def _close(word: str, choices: Iterable[str], cutoff: float = 0.8) -> bool:
    tokens = [t for t in _norm(word).split() if t] + [_norm(word).replace(" ", "")]
    for t in tokens:
        if difflib.get_close_matches(t, list(choices), n=1, cutoff=cutoff):
            return True
    return False


def classify_segment(name: str, position: int, animal: Optional[AnimalId] = None) -> str:
    """Return graft / fluid / ignore / unknown for one segment.

    position: 0-based order of the segment in the file (Segment0 = 0); used for unnamed "Segment_N".
    """
    raw = (name or "").strip()
    n = _norm(raw)
    m = _GENERIC.match(raw)
    if m:
        if position == 0 and int(m.group(1)) == 1:
            if animal is not None and animal.key in EXCLUDE_UNNAMED_GRAFT:
                return IGNORE
            return GRAFT
        return IGNORE
    squashed = n.replace(" ", "")
    if any(w.replace(" ", "") in squashed for w in _IGNORE_WORDS) or _close(raw, ["blackspot", "blackpoint"], 0.75):
        return IGNORE
    if any(w in n for w in _FLUID_WORDS) or _close(raw, _FLUID_WORDS):
        return FLUID
    if any(w in n for w in _GRAFT_WORDS) or _close(raw, _GRAFT_WORDS, 0.75):
        return GRAFT
    if parse_animal(raw) is not None:  # name is an animal / case ID
        return GRAFT
    return UNKNOWN
