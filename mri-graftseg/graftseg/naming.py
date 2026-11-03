"""Animal / case naming.

Rule agreed for this project: every output is named "<Parent Cage>_<EarTag>", e.g. ``PA_01_R2M``
(parent cage ``PA_01``, ear tag ``R2M``). The cage and tag are read from, in order:

1. the DICOM SeriesDescription (e.g. ``T2_Anatomy__PA05_R2M(E7)``), when the input is a DICOM;
2. the folder names / file name, deepest first (the folder a scan sits in is the animal name);
3. if nothing parses: the name of the folder the scan sits in (never skipped).

Delimiters between cage letters, cage number and tag may be ``_``, ``-``, ``.``, space or nothing
(``PA05_R2M``, ``PA_05-R2M``, ``PA05 R2M`` all give ``PA_05_R2M``). ``Nm`` = "no mark" ear tag.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

KNOWN_PREFIXES = ("PA", "Assembloid", "PCDH")

# ear tag: R2M, L1F, R1L1M, NmF, Nm, R2 ...
_TAG = r"(?P<tag>(?:(?:[RL]\d+){1,2}|Nm)[MF]?)"
_ANIMAL_RE = re.compile(
    r"(?<![A-Za-z])(?P<prefix>[A-Za-z]{2,})[ _\-.]*(?P<num>\d{1,3})[ _\-.]*" + _TAG + r"(?![A-Za-z0-9])"
)
_TIMEPOINT_RE = re.compile(r"(?<![A-Za-z0-9])(?P<n>\d{1,2})[ _\-]?(?:mo|months?|m)(?![A-Za-z])", re.I)
_SAFE_RE = re.compile(r"[^A-Za-z0-9_\-]+")


@dataclass(frozen=True)
class AnimalId:
    prefix: str
    number: int
    tag: str

    @property
    def cage(self) -> str:
        return f"{self.prefix}_{self.number:02d}"

    @property
    def name(self) -> str:
        return f"{self.cage}_{self.tag}"

    @property
    def key(self) -> str:
        """Grouping key (case-insensitive) used to keep all scans of one animal in one fold."""
        return self.name.lower()


def parse_animal(text: str) -> Optional[AnimalId]:
    """Return the animal in ``text`` or None. A known prefix (PA, Assembloid, PCDH) wins over others."""
    if not text:
        return None
    matches = list(_ANIMAL_RE.finditer(text))
    if not matches:
        return None
    known = [m for m in matches if m.group("prefix").lower() in {p.lower() for p in KNOWN_PREFIXES}]
    m = (known or matches)[-1]
    prefix = m.group("prefix")
    for p in KNOWN_PREFIXES:  # canonical capitalisation
        if prefix.lower() == p.lower():
            prefix = p
    return AnimalId(prefix, int(m.group("num")), m.group("tag"))


def parse_timepoint(text: str) -> Optional[str]:
    """'PA_MRI_2m Slicer final' -> '2mo', 'Assembloid_41_L1M_6mo' -> '6mo'."""
    m = None
    for m in _TIMEPOINT_RE.finditer(text or ""):
        pass
    return f"{int(m.group('n'))}mo" if m else None


def animal_from_path(path: Path, root: Optional[Path] = None) -> Optional[AnimalId]:
    """Search file name, then parent folders (deepest first), stopping at ``root``."""
    path = Path(path)
    parts = [path.name] + [p.name for p in path.parents]
    if root is not None:
        root = Path(root).resolve()
        parts = [path.name] + [p.name for p in path.parents if root in p.resolve().parents or p.resolve() == root]
    for part in parts:
        a = parse_animal(part)
        if a:
            return a
    return None


def timepoint_from_path(path: Path, root: Optional[Path] = None) -> Optional[str]:
    path = Path(path)
    parts = [path.name] + [p.name for p in path.parents]
    if root is not None:
        root = Path(root).resolve()
        parts = [path.name] + [p.name for p in path.parents if root in p.resolve().parents or p.resolve() == root]
    for part in parts:
        t = parse_timepoint(part)
        if t:
            return t
    return None


def safe_name(text: str) -> str:
    s = _SAFE_RE.sub("_", text).strip("_")
    return re.sub(r"_+", "_", s) or "case"


def output_name(candidates: Iterable[str], fallback_folder: Path) -> tuple[str, str]:
    """Pick the output name from candidate strings (SeriesDescription, file name, folders...).

    Returns (name, how) where how is 'parsed' or 'folder-fallback'.
    """
    for c in candidates:
        a = parse_animal(c)
        if a:
            return a.name, "parsed"
    return safe_name(Path(fallback_folder).name), "folder-fallback"
