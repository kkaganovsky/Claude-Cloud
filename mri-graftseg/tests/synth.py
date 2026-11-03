"""Synthetic T2-like volumes with Slicer-style segmentations, laid out like the real data folders."""
from __future__ import annotations

from pathlib import Path

import nrrd
import numpy as np

SHAPE = (40, 44, 16)            # x, y, z
SPACING = (0.15, 0.15, 0.5)


def volume(seed: int, graft_center=(12, 22, 8), radius_mm=1.2):
    rng = np.random.default_rng(seed)
    x, y, z = np.meshgrid(*[np.arange(n) for n in SHAPE], indexing="ij")
    pos = np.stack([x * SPACING[0], y * SPACING[1], z * SPACING[2]], -1)
    img = 300 + 30 * rng.standard_normal(SHAPE)
    c = np.array(graft_center) * SPACING
    graft = np.linalg.norm(pos - c, axis=-1) < radius_mm
    vent = np.linalg.norm(pos - np.array([30, 22, 8]) * SPACING, axis=-1) < 0.9
    img[graft] += 500
    img[vent] += 900
    return img.astype(np.int16), graft, vent


def header(origin=(-3.0, -3.3, -4.0), space="left-posterior-superior", sizes=SHAPE):
    return {"dimension": 3, "space": space, "sizes": list(sizes),
            "space directions": np.diag(SPACING).astype(float), "kinds": ["domain"] * 3, "encoding": "gzip",
            "space origin": np.array(origin, float)}


def seg_header(names, sizes, origin, four_d=False, layers=None):
    h = header(origin=origin, sizes=sizes)
    h["type"] = "unsigned char"
    if four_d:
        h["dimension"] = 4
        h["sizes"] = [len(names)] + list(sizes)
        h["space directions"] = np.vstack([np.full(3, np.nan), np.diag(SPACING)])
        h["kinds"] = ["list", "domain", "domain", "domain"]
    for i, n in enumerate(names):
        h[f"Segment{i}_Name"] = n
        h[f"Segment{i}_ID"] = f"Segment_{i + 1}"
        h[f"Segment{i}_LabelValue"] = str(1 if four_d else i + 1)
        h[f"Segment{i}_Layer"] = str(i if four_d else 0)
    return h


def write_case(folder: Path, stem: str, seed: int, names=("Organoid", "Black spot"), crop=True, four_d=False,
               graft_center=(12, 22, 8)):
    folder.mkdir(parents=True, exist_ok=True)
    img, graft, vent = volume(seed, graft_center)
    origin = np.array([-3.0, -3.3, -4.0])
    nrrd.write(str(folder / f"{stem}.nrrd"), img, header(origin), index_order="F")
    spot = np.zeros(SHAPE, bool)
    spot[20:22, 10:12, 5:7] = True
    masks = {"organoid": graft, "black spot": spot, "fluid": vent, "csf": vent}
    arrs = [masks.get(n.lower(), graft) for n in names]
    if crop:   # Slicer often saves the seg on a cropped box
        lo = np.array([4, 6, 2]); hi = np.array([38, 40, 14])
    else:
        lo = np.zeros(3, int); hi = np.array(SHAPE)
    sl = tuple(slice(a, b) for a, b in zip(lo, hi))
    seg_origin = origin + lo * np.array(SPACING)
    if four_d:
        data = np.stack([a[sl] for a in arrs]).astype(np.uint8)
    else:
        data = np.zeros(SHAPE, np.uint8)
        for i, a in enumerate(arrs):
            data[a & (data == 0)] = i + 1
        data = data[sl]
    nrrd.write(str(folder / f"{stem}.seg.nrrd"), data,
               seg_header(names, data.shape[-3:], seg_origin, four_d), index_order="F")
    return graft


def write_tree(root: Path, n_animals: int = 6) -> Path:
    tp_dirs = {"2mo": root / "PA_MRI_2m Slicer final segmented data", "4mo": root / "PA_MRI_4m Slicer segmented data"}
    tags = ["R1M", "L1F", "NmF", "R2M", "R1L1F", "L2M", "R2F", "NmM"]
    for a in range(n_animals):
        tag = tags[a % len(tags)]
        for k, (tp, d) in enumerate(tp_dirs.items()):
            names = ("Organoid", "Black spot") if a % 2 == 0 else (f"PA_{a + 1:02d}_{tag}", "CSF")
            write_case(d / f"PA_{a + 1:02d}_{tag}", f"23000{a} T2_Anatomy_PA{a + 1:02d}_{tag}", seed=a * 10 + k,
                       names=names, four_d=(a == 1), crop=(a != 2), graft_center=(10 + a, 20 + k, 8))
    # irrelevant files
    (root / "notes.txt").write_text("irrelevant")
    return root


def write_enhanced_dicom(path: Path, seed: int = 99, desc: str = "T2_Anatomy__PA05_R2M(E7)"):
    """Minimal Bruker-like enhanced multi-frame MR DICOM."""
    import pydicom
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.sequence import Sequence
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid
    img, graft, _ = volume(seed)
    frames = np.transpose(img, (2, 1, 0)).astype(np.uint16)  # (z, rows=y, cols=x)
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.4.1"
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = Dataset()
    ds.file_meta = meta
    ds.SOPClassUID = meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.SeriesInstanceUID = generate_uid()
    ds.Modality = "MR"
    ds.SeriesDescription = desc
    ds.PatientName = "test"
    ds.Rows, ds.Columns = frames.shape[1], frames.shape[2]
    ds.NumberOfFrames = frames.shape[0]
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16; ds.BitsStored = 16; ds.HighBit = 15; ds.PixelRepresentation = 0
    shared = Dataset()
    po = Dataset(); po.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    pm = Dataset(); pm.PixelSpacing = [SPACING[1], SPACING[0]]; pm.SliceThickness = SPACING[2]
    shared.PlaneOrientationSequence = Sequence([po])
    shared.PixelMeasuresSequence = Sequence([pm])
    ds.SharedFunctionalGroupsSequence = Sequence([shared])
    per = []
    for k in range(frames.shape[0]):
        f = Dataset()
        pp = Dataset(); pp.ImagePositionPatient = [-3.0, -3.3, -4.0 + k * SPACING[2]]
        fc = Dataset(); fc.InStackPositionNumber = k + 1
        f.PlanePositionSequence = Sequence([pp])
        f.FrameContentSequence = Sequence([fc])
        per.append(f)
    ds.PerFrameFunctionalGroupsSequence = Sequence(per)
    ds.PixelData = frames.tobytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.save_as(str(path), enforce_file_format=True)
    return graft
