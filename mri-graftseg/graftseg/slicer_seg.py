"""Read and write 3D Slicer segmentation files (``*.seg.nrrd``).

A Slicer ``.seg.nrrd`` is a NRRD labelmap plus ``SegmentN_*`` key/value fields that give each segment its name,
label value, layer, color and extent. It is 3D (one layer) or 4D (first axis = layers, used when segments overlap,
and by older Slicer versions with one layer per segment). Its voxel grid is often a cropped sub-box of the MRI, so
masks are always resampled onto the MRI grid in physical (LPS) space before use.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
import nrrd
import SimpleITK as sitk

# Colors used for the segments this tool writes (r g b in 0..1)
COLORS = {"Graft": (0.2, 0.85, 0.3), "Fluid": (0.3, 0.5, 1.0)}
TOOL_FIELD = "GraftSeg_Status"  # 'predicted' (model output, not reviewed) or 'reviewed'
TOOL_MODEL_FIELD = "GraftSeg_Model"


@dataclass
class Segment:
    index: int          # N in SegmentN_*
    id: str
    name: str
    label_value: int
    layer: int


@dataclass
class SlicerSeg:
    path: Path
    header: dict
    data: np.ndarray            # (x, y, z) or (layers, x, y, z), index_order 'F'
    segments: list[Segment]

    @property
    def status(self) -> Optional[str]:
        return self.header.get(TOOL_FIELD)

    def segment_mask(self, seg: Segment) -> np.ndarray:
        """Boolean (x, y, z) mask of one segment on the seg's own grid."""
        d = self.data
        if d.ndim == 4:
            layer = d[seg.layer] if seg.layer < d.shape[0] else np.zeros(d.shape[1:], d.dtype)
        else:
            layer = d
        return layer == seg.label_value

    def geometry_image(self, array_xyz: np.ndarray) -> sitk.Image:
        """Wrap an (x, y, z) array of the seg grid into a SimpleITK image with the seg's LPS geometry."""
        return _array_to_sitk(array_xyz, self.header)


def _space_sign(header: dict) -> np.ndarray:
    space = str(header.get("space", "left-posterior-superior")).lower()
    if space in ("right-anterior-superior", "ras"):
        return np.array([-1.0, -1.0, 1.0])  # RAS -> LPS
    return np.array([1.0, 1.0, 1.0])


def _spatial_directions(header: dict) -> np.ndarray:
    sd = np.asarray(header["space directions"], dtype=float)
    rows = [r for r in sd if not np.any(np.isnan(r))]
    return np.asarray(rows[-3:], dtype=float)  # 3 x 3, row i = physical step of voxel axis i


def _array_to_sitk(array_xyz: np.ndarray, header: dict) -> sitk.Image:
    sign = _space_sign(header)
    dirs = _spatial_directions(header) * sign[None, :]
    spacing = np.linalg.norm(dirs, axis=1)
    direction = (dirs / spacing[:, None]).T  # column j = unit direction of voxel axis j
    origin = np.asarray(header.get("space origin", [0, 0, 0]), dtype=float) * sign
    img = sitk.GetImageFromArray(np.ascontiguousarray(np.transpose(array_xyz, (2, 1, 0))))
    img.SetSpacing([float(s) for s in spacing])
    img.SetDirection([float(v) for v in direction.flatten()])
    img.SetOrigin([float(v) for v in origin])
    return img


def read_seg(path: Path) -> SlicerSeg:
    data, header = nrrd.read(str(path), index_order="F")
    idx = sorted({int(m.group(1)) for k in header for m in [re.match(r"Segment(\d+)_", k)] if m})
    segments = []
    four_d = data.ndim == 4
    for i in idx:
        name = str(header.get(f"Segment{i}_Name", f"Segment_{i + 1}"))
        sid = str(header.get(f"Segment{i}_ID", f"Segment_{i + 1}"))
        if f"Segment{i}_LabelValue" in header:
            lv = int(header[f"Segment{i}_LabelValue"])
            layer = int(header.get(f"Segment{i}_Layer", 0))
        elif four_d:  # old format: one layer per segment, any nonzero voxel belongs to it
            lv, layer = 1, i
        else:
            lv, layer = i + 1, 0
        segments.append(Segment(i, sid, name, lv, layer))
    if four_d and segments and all(f"Segment{s.index}_LabelValue" not in header for s in segments):
        data = (data != 0).astype(np.uint8)
    if not segments:  # plain labelmap without Slicer fields: one segment per label value
        vals = [int(v) for v in np.unique(data) if v != 0]
        segments = [Segment(k, f"Label_{v}", f"Label_{v}", v, 0) for k, v in enumerate(vals)]
    return SlicerSeg(Path(path), header, data, segments)


def masks_on_reference(seg: SlicerSeg, segments: Sequence[Segment], reference: sitk.Image) -> np.ndarray:
    """Union of ``segments`` resampled (nearest neighbour) onto ``reference``. Returns bool array (z, y, x)."""
    if not segments:
        return np.zeros(sitk.GetArrayViewFromImage(reference).shape, bool)
    union = np.zeros(seg.data.shape[-3:], np.uint8)
    for s in segments:
        union |= seg.segment_mask(s).astype(np.uint8)
    src = seg.geometry_image(union)
    out = sitk.Resample(src, reference, sitk.Transform(), sitk.sitkNearestNeighbor, 0, sitk.sitkUInt8)
    return sitk.GetArrayFromImage(out).astype(bool)


def _extent(mask_xyz: np.ndarray) -> str:
    if not mask_xyz.any():
        return "0 -1 0 -1 0 -1"
    out = []
    for ax in range(3):
        other = tuple(a for a in range(3) if a != ax)
        nz = np.where(mask_xyz.any(axis=other))[0]
        out += [int(nz[0]), int(nz[-1])]
    return " ".join(str(v) for v in out)


def write_seg(path: Path, labelmap_zyx: np.ndarray, reference: sitk.Image,
              names: dict[int, str], status: str = "predicted", model: str = "",
              extra: Optional[dict] = None) -> Path:
    """Write a Slicer-compatible ``.seg.nrrd`` on the grid of ``reference`` (the MRI).

    labelmap_zyx: integer array shaped like ``sitk.GetArrayFromImage(reference)``; names: {label value: segment name}.
    """
    path = Path(path)
    lab = np.asarray(labelmap_zyx).astype(np.uint8)
    if lab.shape != sitk.GetArrayViewFromImage(reference).shape:
        raise ValueError(f"labelmap shape {lab.shape} != reference {sitk.GetArrayViewFromImage(reference).shape}")
    data_xyz = np.transpose(lab, (2, 1, 0))
    spacing = np.asarray(reference.GetSpacing(), float)
    direction = np.asarray(reference.GetDirection(), float).reshape(3, 3)
    dirs = (direction * spacing[None, :]).T  # row i = physical step of axis i (LPS)
    header = {
        "type": "unsigned char",
        "dimension": 3,
        "space": "left-posterior-superior",
        "sizes": list(data_xyz.shape),
        "space directions": dirs,
        "kinds": ["domain", "domain", "domain"],
        "encoding": "gzip",
        "space origin": np.asarray(reference.GetOrigin(), float),
        "Segmentation_MasterRepresentation": "Binary labelmap",
        "Segmentation_ContainedRepresentationNames": "Binary labelmap|",
        "Segmentation_ReferenceImageExtentOffset": "0 0 0",
    }
    for i, (value, name) in enumerate(sorted(names.items())):
        color = COLORS.get(name, (0.9, 0.6, 0.2))
        header.update({
            f"Segment{i}_Color": " ".join(f"{c:g}" for c in color),
            f"Segment{i}_ColorAutoGenerated": "0",
            f"Segment{i}_Extent": _extent(data_xyz == value),
            f"Segment{i}_ID": f"Segment_{value}",
            f"Segment{i}_LabelValue": str(int(value)),
            f"Segment{i}_Layer": "0",
            f"Segment{i}_Name": name,
            f"Segment{i}_NameAutoGenerated": "0",
            f"Segment{i}_Tags": "Segmentation.Status:inprogress|",
        })
    header[TOOL_FIELD] = status
    if model:
        header[TOOL_MODEL_FIELD] = model
    for k, v in (extra or {}).items():
        header[k] = str(v)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    nrrd.write(str(tmp), data_xyz, header, index_order="F")
    tmp.replace(path)
    return path


def _lps_header(reference: sitk.Image, shape_xyz) -> dict:
    spacing = np.asarray(reference.GetSpacing(), float)
    direction = np.asarray(reference.GetDirection(), float).reshape(3, 3)
    return {"dimension": 3, "space": "left-posterior-superior", "sizes": list(shape_xyz),
            "space directions": (direction * spacing[None, :]).T, "kinds": ["domain"] * 3,
            "encoding": "gzip", "space origin": np.asarray(reference.GetOrigin(), float)}


def write_volume(path: Path, image: sitk.Image, extra: Optional[dict] = None) -> Path:
    """Write an MRI volume as .nrrd (opens in Slicer; pairs with the .seg.nrrd for later retraining)."""
    arr = sitk.GetArrayFromImage(image)
    if np.issubdtype(arr.dtype, np.floating) and np.allclose(arr, np.round(arr)) and arr.min() >= -32768 \
            and arr.max() <= 32767:
        arr = arr.astype(np.int16)
    data_xyz = np.transpose(arr, (2, 1, 0))
    header = _lps_header(image, data_xyz.shape)
    for k, v in (extra or {}).items():
        header[k] = str(v)
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    nrrd.write(str(tmp), data_xyz, header, index_order="F")
    tmp.replace(path)
    return path


def repair_transposed(path: Path, out: Optional[Path] = None, status: str = "predicted") -> Optional[Path]:
    """Fix a .seg.nrrd written by the old predict_grafts.py (array written with index_order='C' while it was in
    x,y,z order, so 'sizes' are reversed relative to 'space directions'). Returns the written path or None if the
    file does not look like one of those."""
    data, header = nrrd.read(str(path), index_order="F")
    tags = str(header.get("Segment0_Tags", ""))
    if "ParentCage:" not in tags or TOOL_FIELD in header or data.ndim != 3:
        return None
    fixed = np.transpose(data, (2, 1, 0))
    header["sizes"] = list(fixed.shape)
    header["Segment0_Extent"] = _extent(fixed == int(header.get("Segment0_LabelValue", 1)))
    header[TOOL_FIELD] = status
    header["GraftSeg_Repaired"] = "axes reordered from old predict_grafts.py export"
    out = Path(out or path)
    nrrd.write(str(out), np.ascontiguousarray(fixed), header, index_order="F")
    return out


def is_seg_file(p: Path) -> bool:
    return p.name.lower().endswith(".seg.nrrd")
