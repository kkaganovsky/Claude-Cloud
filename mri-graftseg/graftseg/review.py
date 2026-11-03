"""Editing model for one segmentation (used by the Review page of the app, app.py).

Saving writes the segment "Graft" back to the same .seg.nrrd, marked as reviewed (so it can be used for training);
any other segments in the file (fluid, black spot...) are kept unchanged.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import SimpleITK as sitk

from . import labels as L
from .slicer_seg import TOOL_FIELD, TOOL_MODEL_FIELD, masks_on_reference, read_seg, write_seg


def find_image_for(seg_path: Path) -> Optional[Path]:
    seg_path = Path(seg_path)
    folder = seg_path.parent
    stem = seg_path.name[: -len(".seg.nrrd")]
    for s in (stem, stem[:-5] if stem.endswith("_pred") else None):
        if s and (folder / f"{s}.nrrd").exists():
            return folder / f"{s}.nrrd"
    import nrrd
    h = nrrd.read_header(str(seg_path))
    src = h.get("GraftSeg_Input")
    if src and (folder / src).exists():
        return folder / src
    vols = [p for p in folder.glob("*.nrrd") if not p.name.lower().endswith(".seg.nrrd")]
    return vols[0] if len(vols) == 1 else None


def load_image(path: Path) -> sitk.Image:
    if path.suffix.lower() == ".dcm":
        from .scans import read_enhanced_dicom
        return read_enhanced_dicom(path)
    return sitk.ReadImage(str(path))


class Case:
    """One image + editable graft mask. Arrays are numpy (z, y, x) like SimpleITK."""

    def __init__(self, seg_path: Path, image_path: Optional[Path] = None):
        self.seg_path = Path(seg_path)
        self.image_path = Path(image_path) if image_path else find_image_for(self.seg_path)
        if self.image_path is None:
            raise FileNotFoundError(f"Cannot find the MRI for {self.seg_path} (expected a .nrrd next to it)")
        self.image = load_image(self.image_path)
        self.vol = sitk.GetArrayFromImage(self.image).astype(np.float32)
        seg = read_seg(self.seg_path)
        self.header = seg.header
        graft = [s for i, s in enumerate(seg.segments) if L.classify_segment(s.name, i) == L.GRAFT]
        other = [s for s in seg.segments if s not in graft]
        self.mask = masks_on_reference(seg, graft, self.image)
        # keep the other segments (fluid, black spot...) unchanged on save
        self.others = [(s.name, masks_on_reference(seg, [s], self.image)) for s in other]
        self.original = self.mask.copy()
        self.spacing_zyx = np.array(self.image.GetSpacing()[::-1], float)
        self.undo: list[tuple] = []
        self.name = self.seg_path.name[: -len(".seg.nrrd")]

    @property
    def edited(self) -> bool:
        return not np.array_equal(self.mask, self.original)

    def volume_mm3(self) -> float:
        return float(self.mask.sum() * np.prod(self.spacing_zyx))

    def push_undo(self, axis: int, idx: int, ball: bool):
        if ball:
            self.undo.append(("all", self.mask.copy()))
        else:
            self.undo.append((axis, idx, np.take(self.mask, idx, axis=axis).copy()))
        del self.undo[:-30]

    def do_undo(self) -> bool:
        if not self.undo:
            return False
        item = self.undo.pop()
        if item[0] == "all":
            self.mask[...] = item[1]
        else:
            axis, idx, sl = item
            view = np.moveaxis(self.mask, axis, 0)
            view[idx] = sl
        return True

    def brush(self, axis: int, idx: int, r: float, c: float, radius_px: float, paint: bool, ball: bool = False):
        """Paint/erase a disc on slice ``idx`` of ``axis`` (row r, col c in that slice), or a ball (3D brush)."""
        sp = np.delete(self.spacing_zyx, axis)  # in-plane spacing (rows, cols)
        rad_mm = radius_px * sp.min()
        view = np.moveaxis(self.mask, axis, 0)
        thick = self.spacing_zyx[axis]
        n_sl = int(rad_mm // thick) if ball else 0
        for d in range(-n_sl, n_sl + 1):
            k = idx + d
            if not (0 <= k < view.shape[0]):
                continue
            rr_mm = np.sqrt(max(rad_mm ** 2 - (d * thick) ** 2, 0)) if ball else rad_mm
            h, w = view.shape[1:]
            y, x = np.ogrid[:h, :w]
            disc = ((y - r) * sp[0]) ** 2 + ((x - c) * sp[1]) ** 2 <= rr_mm ** 2 + 1e-9
            view[k][disc] = paint

    def fill_holes(self, axis: int, idx: int):
        from scipy import ndimage
        view = np.moveaxis(self.mask, axis, 0)
        view[idx] = ndimage.binary_fill_holes(view[idx])

    def best_slice(self, axis: int) -> int:
        other = tuple(a for a in range(3) if a != axis)
        counts = self.mask.sum(axis=other)
        return int(np.argmax(counts)) if counts.any() else self.mask.shape[axis] // 2

    def save(self) -> Path:
        lab = np.zeros(self.mask.shape, np.uint8)
        names = {}
        for v, (n, m) in enumerate(self.others, start=2):
            lab[m & ~self.mask] = v
            names[v] = n
        lab[self.mask] = 1
        names[1] = "Graft"
        write_seg(self.seg_path, lab, self.image, names, status="reviewed",
                  model=str(self.header.get(TOOL_MODEL_FIELD, "")),
                  extra={"GraftSeg_EditedByReviewer": "yes" if self.edited else "no",
                         "GraftSeg_PreviousStatus": self.header.get(TOOL_FIELD, "human")})
        self.original = self.mask.copy()
        return self.seg_path


def collect(inputs: list[Path], include_reviewed: bool = True) -> list[Path]:
    """All .seg.nrrd under the inputs (files or folders), predictions needing review first."""
    out = []
    for inp in inputs:
        inp = Path(inp)
        cands = [inp] if inp.is_file() else sorted(inp.rglob("*.seg.nrrd"))
        out += [p for p in cands if p.name.lower().endswith(".seg.nrrd") and not p.name.startswith("._")]
    import nrrd
    def status(p):
        try:
            return nrrd.read_header(str(p)).get(TOOL_FIELD, "human")
        except Exception:
            return "unreadable"
    st = {p: status(p) for p in out}
    out = [p for p in out if st[p] != "unreadable" and (include_reviewed or st[p] == "predicted")]
    return sorted(out, key=lambda p: (st[p] != "predicted", str(p)))
