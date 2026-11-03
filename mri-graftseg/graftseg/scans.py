"""Find unlabeled scans to segment: Bruker/enhanced multi-frame DICOM (``EnIm1.dcm``), classic DICOM series,
or ``.nrrd`` volumes. When a folder holds several MR series (T2 anatomy, localizer...), the T2 anatomy wins.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import SimpleITK as sitk

from .naming import output_name
from .slicer_seg import is_seg_file

ENHANCED_MR = "1.2.840.10008.5.1.4.1.1.4.1"
CLASSIC_MR = "1.2.840.10008.5.1.4.1.1.4"


@dataclass
class Scan:
    path: Path                     # the DICOM file / first DICOM of a series / the .nrrd
    kind: str                      # 'dicom-enhanced' | 'dicom-series' | 'nrrd'
    folder: Path
    series_description: str = ""
    n_frames: int = 0
    files: list = field(default_factory=list)
    name: str = ""
    name_source: str = ""
    note: str = ""

    def load(self) -> sitk.Image:
        if self.kind == "nrrd":
            return sitk.ReadImage(str(self.path))
        if self.kind == "dicom-enhanced":
            return read_enhanced_dicom(self.path)
        r = sitk.ImageSeriesReader()
        r.SetFileNames([str(f) for f in self.files])
        return r.Execute()


def _score(desc: str, n_frames: int, sop: str) -> tuple:
    d = desc.lower()
    return ("t2" in d and "anat" in d, "t2" in d, "loc" not in d and "scout" not in d and "tripilot" not in d,
            sop == ENHANCED_MR, n_frames)


def read_enhanced_dicom(path: Path) -> sitk.Image:
    """Enhanced (multi-frame) MR DICOM -> SimpleITK image with geometry from the per-frame functional groups.

    Frames are sorted along the slice normal. DICOM patient coordinates are LPS, which is ITK's convention.
    """
    import pydicom
    ds = pydicom.dcmread(str(path))
    arr = ds.pixel_array.astype(np.float32)
    if arr.ndim == 2:
        arr = arr[None]
    shared = ds.SharedFunctionalGroupsSequence[0] if "SharedFunctionalGroupsSequence" in ds else None
    per = ds.PerFrameFunctionalGroupsSequence

    def grp(frame, name):
        if name in frame:
            return getattr(frame, name)[0]
        if shared is not None and name in shared:
            return getattr(shared, name)[0]
        raise KeyError(name)

    ori = np.array(grp(per[0], "PlaneOrientationSequence").ImageOrientationPatient, float)
    row, col = ori[:3], ori[3:]
    normal = np.cross(row, col)
    pix = grp(per[0], "PixelMeasuresSequence")
    dr, dc = (float(v) for v in pix.PixelSpacing)  # row spacing (between rows), column spacing
    pos = np.array([np.array(grp(f, "PlanePositionSequence").ImagePositionPatient, float) for f in per])
    # Rescale (Bruker stores per-frame scaling; apply it so frames are comparable)
    slopes, inters = [], []
    for f in per:
        try:
            t = grp(f, "PixelValueTransformationSequence")
            slopes.append(float(t.RescaleSlope)); inters.append(float(t.RescaleIntercept))
        except KeyError:
            slopes.append(float(getattr(ds, "RescaleSlope", 1))); inters.append(float(getattr(ds, "RescaleIntercept", 0)))
    arr = arr * np.array(slopes, np.float32)[:, None, None] + np.array(inters, np.float32)[:, None, None]
    # Multi-orientation / multi-echo files: keep frames sharing the first frame's orientation
    d = pos @ normal
    if len(np.unique(np.round(d, 4))) != len(d):
        raise ValueError(f"{path.name}: {len(d)} frames but fewer distinct slice positions "
                         "(multi-echo or repeated slices); not supported")
    # Same frame order as the original predict_grafts.py (InStackPositionNumber), so the network sees the
    # array exactly as it did when the model was checked; fall back to position along the slice normal.
    try:
        stack = [int(f.FrameContentSequence[0].InStackPositionNumber) for f in per]
        order = np.argsort(stack, kind="stable")
    except (AttributeError, IndexError, KeyError):
        order = np.argsort(d, kind="stable")
    arr = arr[order]
    pos = pos[order]
    if len(pos) > 1:
        step = (pos[-1] - pos[0]) / (len(pos) - 1)
        dz = float(np.linalg.norm(step))
        slice_dir = step / dz
    else:
        dz = float(getattr(grp(per[0], "PixelMeasuresSequence"), "SliceThickness", 1.0))
        slice_dir = normal
    img = sitk.GetImageFromArray(arr)  # (z, y, x): x along rows' direction (columns index), y down
    img.SetSpacing((dc, dr, dz))
    direction = np.column_stack([row, col, slice_dir])
    img.SetDirection([float(v) for v in direction.flatten()])
    img.SetOrigin([float(v) for v in pos[0]])
    return img


def _dicom_info(p: Path) -> Optional[dict]:
    import pydicom
    try:
        ds = pydicom.dcmread(str(p), stop_before_pixels=True)
    except Exception:
        return None
    sop = str(getattr(ds, "SOPClassUID", ""))
    if sop not in (ENHANCED_MR, CLASSIC_MR):
        return None
    return {"sop": sop, "desc": str(getattr(ds, "SeriesDescription", "") or getattr(ds, "ProtocolName", "")),
            "frames": int(getattr(ds, "NumberOfFrames", 1) or 1), "series": str(getattr(ds, "SeriesInstanceUID", p)),
            "patient": str(getattr(ds, "PatientName", "") or "")}


def find_scans(inputs: list[Path]) -> list[Scan]:
    """One Scan per folder with DICOM (best MR series chosen), plus one per .nrrd volume."""
    scans: list[Scan] = []
    for inp in inputs:
        inp = Path(inp)
        files = [inp] if inp.is_file() else sorted(p for p in inp.rglob("*") if p.is_file()
                                                   and "__MACOSX" not in p.parts and not p.name.startswith("._"))
        dirs: dict[Path, list[Path]] = {}
        for f in files:
            low = f.name.lower()
            if low.endswith(".nrrd"):
                if is_seg_file(f):
                    continue
                s = Scan(path=f, kind="nrrd", folder=f.parent)
                s.name, s.name_source = output_name([f.name, f.parent.name, f.parent.parent.name], f.parent)
                scans.append(s)
            elif low.endswith(".dcm") or low.endswith(".ima") or "." not in f.name:
                dirs.setdefault(f.parent, []).append(f)
        for folder, dfiles in sorted(dirs.items()):
            series: dict[str, dict] = {}
            for f in dfiles:
                info = _dicom_info(f)
                if info is None:
                    continue
                key = info["series"] if info["sop"] == CLASSIC_MR else str(f)
                e = series.setdefault(key, {**info, "files": []})
                e["files"].append(f)
            if not series:
                continue
            for e in series.values():
                if e["sop"] == CLASSIC_MR:
                    e["frames"] = len(e["files"])
            ranked = sorted(series.values(), key=lambda e: _score(e["desc"], e["frames"], e["sop"]), reverse=True)
            best = ranked[0]
            s = Scan(path=best["files"][0], kind="dicom-enhanced" if best["sop"] == ENHANCED_MR else "dicom-series",
                     folder=folder, series_description=best["desc"], n_frames=best["frames"], files=best["files"])
            s.name, s.name_source = output_name(
                [best["desc"], best["patient"], folder.name, folder.parent.name], folder)
            if len(ranked) > 1:
                s.note = f"{len(ranked)} MR series in folder; chose '{best['desc']}' ({best['frames']} frames)"
            scans.append(s)
    # Drop the .nrrd copies this tool wrote next to a DICOM (they would be segmented twice).
    dicom_dirs = {s.folder for s in scans if s.kind != "nrrd"}
    kept = []
    for s in scans:
        if s.kind == "nrrd" and s.folder in dicom_dirs:
            try:
                import nrrd
                if "GraftSeg_Source" in nrrd.read_header(str(s.path)):
                    continue
            except Exception:
                pass
        kept.append(s)
    return kept
