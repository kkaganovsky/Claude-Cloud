"""Fast tests (no nnU-Net, no GPU): naming, label rules, Slicer I/O, dataset building, splits, DICOM geometry."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import nrrd
import numpy as np
import pytest
import SimpleITK as sitk

sys.path.insert(0, str(Path(__file__).parent))
import synth  # noqa: E402

from graftseg import labels as L  # noqa: E402
from graftseg.dataset import build, make_splits, scan  # noqa: E402
from graftseg.naming import parse_animal, parse_timepoint  # noqa: E402
from graftseg.slicer_seg import masks_on_reference, read_seg, repair_transposed, write_seg  # noqa: E402


@pytest.mark.parametrize("text,name", [
    ("T2_Anatomy__PA05_R2M(E7)", "PA_05_R2M"), ("T2_Anatomy__PA05-R2M(E7)", "PA_05_R2M"),
    ("T2_Anatomy__PA05 R2M(E7)", "PA_05_R2M"), ("T2_Anatomy__PA_01_R2M(E7)", "PA_01_R2M"),
    ("T2_Anatomy__PA01_NmF(E9)", "PA_01_NmF"), ("T2_Anatomy__PA01_R1L1F(E11)", "PA_01_R1L1F"),
    ("T2_Anatomy__PA05_R2M", "PA_05_R2M"), ("Assembloid03_L1F_2mo12192022", "Assembloid_03_L1F"),
    ("Assembloid_41_L1M_2mo", "Assembloid_41_L1M"), ("PCDH_14_R1F_2mo", "PCDH_14_R1F"),
    ("230001 T2_Anatomy_PA01_L1F", "PA_01_L1F"),
])
def test_parse_animal(text, name):
    assert parse_animal(text).name == name


def test_unparseable_and_timepoints():
    assert parse_animal("Pasca_FebMar26_Pasca_03-13-2026__E7_P1") is None
    assert parse_timepoint("PA_MRI_2m Slicer final segmented data") == "2mo"
    assert parse_timepoint("PA_MRI_4m Slicer segmented data") == "4mo"
    assert parse_timepoint("Assembloid03_L1F_2mo12192022") == "2mo"
    assert parse_timepoint("T2_Anatomy") is None


@pytest.mark.parametrize("name,pos,expected", [
    ("Organoid", 0, L.GRAFT), ("Orgnaoid", 0, L.GRAFT), ("organiod", 1, L.GRAFT), ("Assembloid", 0, L.GRAFT),
    ("Assembloid_23_R1M_6mo", 0, L.GRAFT), ("PCDH_14_R1F_2mo", 0, L.GRAFT), ("Segment_1", 0, L.GRAFT),
    ("Segment_1", 1, L.IGNORE), ("Segment_2", 1, L.IGNORE), ("Black spot", 1, L.IGNORE),
    ("Black point", 2, L.IGNORE), ("Blackspot", 2, L.IGNORE), ("Balck spot", 2, L.IGNORE),
    ("background", 1, L.IGNORE), ("Brain", 1, L.IGNORE), ("Fluid", 1, L.FLUID), ("Ventricle?", 1, L.FLUID),
    ("CSF", 2, L.FLUID), ("Graft", 0, L.GRAFT), ("Tumor", 1, L.UNKNOWN),
])
def test_classify(name, pos, expected):
    assert L.classify_segment(name, pos) == expected


def test_excluded_unnamed_graft():
    a = parse_animal("Assembloid_39_R2M")
    assert L.classify_segment("Segment_1", 0, a) == L.IGNORE
    assert L.classify_segment("Organoid", 0, a) == L.GRAFT


def test_seg_roundtrip_and_cropped_resample(tmp_path):
    g = synth.write_case(tmp_path / "PA_01_R1M", "img", seed=1, crop=True, graft_center=(20, 22, 8))
    ref = sitk.ReadImage(str(tmp_path / "PA_01_R1M" / "img.nrrd"))
    seg = read_seg(tmp_path / "PA_01_R1M" / "img.seg.nrrd")
    assert [s.name for s in seg.segments] == ["Organoid", "Black spot"]
    m = masks_on_reference(seg, seg.segments[:1], ref).transpose(2, 1, 0)
    assert np.array_equal(m, g)
    out = write_seg(tmp_path / "out.seg.nrrd", m.transpose(2, 1, 0).astype(np.uint8), ref, {1: "Graft"})
    back = read_seg(out)
    assert back.status == "predicted" and back.segments[0].name == "Graft"
    assert np.array_equal(masks_on_reference(back, back.segments, ref).transpose(2, 1, 0), g)


def test_4d_layered_seg(tmp_path):
    g = synth.write_case(tmp_path / "a", "img", seed=2, four_d=True, graft_center=(20, 22, 8))
    ref = sitk.ReadImage(str(tmp_path / "a" / "img.nrrd"))
    seg = read_seg(tmp_path / "a" / "img.seg.nrrd")
    assert seg.data.ndim == 4
    assert np.array_equal(masks_on_reference(seg, seg.segments[:1], ref).transpose(2, 1, 0), g)


def test_repair_old_predict_grafts_export(tmp_path):
    """The old exporter wrote an (x,y,z) array with index_order='C'; repair must restore the true placement."""
    g = synth.volume(3)[1].astype(np.uint8)
    h = synth.header(); h.update({"type": "unsigned char", "Segment0_Name": "PA_01_R2M", "Segment0_LabelValue": "1",
                                  "Segment0_Tags": "ParentCage:PA_01|EarTag:R2M|"})
    p = tmp_path / "old.seg.nrrd"
    nrrd.write(str(p), g, h, index_order="C")      # reproduces the bug
    assert list(nrrd.read_header(str(p))["sizes"]) == list(g.shape[::-1])
    assert repair_transposed(p) == p
    data, hh = nrrd.read(str(p), index_order="F")
    assert np.array_equal(data, g) and list(hh["sizes"]) == list(g.shape)
    assert repair_transposed(p) is None             # second run: not touched again


def test_scan_build_and_animal_level_splits(tmp_path):
    root = synth.write_tree(tmp_path / "src", 6)
    # duplicate copy of one animal folder (same MRI) -> must be de-duplicated
    a = root / "PA_MRI_2m Slicer final segmented data" / "PA_01_R1M"
    shutil.copytree(a, root / "copies" / "PA_01_R1M")
    cases = scan([root])
    ok = [c for c in cases if c.status == "ok"]
    assert len(ok) == 12
    assert any("duplicate" in c.reason for c in cases)
    assert all(c.graft_segments and not c.unknown_segments for c in ok)
    assert {c.animal for c in ok} == {"PA_01_R1M", "PA_02_L1F", "PA_03_NmF", "PA_04_R2M", "PA_05_R1L1F", "PA_06_L2M"}
    ds = build([root], tmp_path / "raw", 7, n_folds=3, log=lambda s: None)
    assert len(list((ds / "imagesTr").glob("*_0000.nii.gz"))) == 12
    splits = json.loads((ds / "splits_final.json").read_text())
    for f in splits:
        tr = {parse_animal(c).key for c in f["train"]}
        va = {parse_animal(c).key for c in f["val"]}
        assert not tr & va, "an animal is in both train and val"
        assert len(f["train"]) + len(f["val"]) == 12
    lab = sitk.GetArrayFromImage(sitk.ReadImage(str(ds / "labelsTr" / "PA_03_NmF_2mo.nii.gz")))
    assert set(np.unique(lab)) == {0, 1}           # black spot / CSF removed


def test_unreviewed_predictions_are_not_training_data(tmp_path):
    synth.write_case(tmp_path / "PA_01_R1M", "img", seed=1)
    h = nrrd.read_header(str(tmp_path / "PA_01_R1M" / "img.seg.nrrd"))
    d, _ = nrrd.read(str(tmp_path / "PA_01_R1M" / "img.seg.nrrd"), index_order="F")
    h["GraftSeg_Status"] = "predicted"
    nrrd.write(str(tmp_path / "PA_01_R1M" / "img.seg.nrrd"), d, h, index_order="F")
    assert scan([tmp_path])[0].status == "skip"
    assert scan([tmp_path], include_unreviewed=True)[0].status == "ok"


def test_make_splits_keeps_timepoints_together():
    from graftseg.dataset import Case
    cases = [Case(seg_path="", case_id=f"PA_{a:02d}_R1M_{t}", animal_key=f"pa_{a:02d}_r1m") for a in range(10) for t in ("2mo", "4mo")]
    for f in make_splits(cases, 5):
        assert len(f["val"]) == 4
        assert {c[:9] for c in f["val"]}.isdisjoint({c[:9] for c in f["train"]})


def _old_affine_lps(ds):
    """Geometry exactly as the old predict_grafts.dicom_to_nifti computed it (converted back to LPS)."""
    sf = ds.SharedFunctionalGroupsSequence[0]
    iop = np.array(sf.PlaneOrientationSequence[0].ImageOrientationPatient, float)
    ps = sf.PixelMeasuresSequence[0].PixelSpacing
    fi = sorted([(int(f.FrameContentSequence[0].InStackPositionNumber), np.array(f.PlanePositionSequence[0].ImagePositionPatient, float))
                 for f in ds.PerFrameFunctionalGroupsSequence], key=lambda t: t[0])
    v = fi[1][1] - fi[0][1]
    A = np.eye(4)
    A[:3, 0] = iop[:3] * float(ps[1]); A[:3, 1] = iop[3:] * float(ps[0]); A[:3, 2] = v; A[:3, 3] = fi[0][1]
    return A


def test_enhanced_dicom_matches_old_converter_oblique(tmp_path):
    import pydicom
    from graftseg.scans import find_scans, read_enhanced_dicom
    p = tmp_path / "Pasca_X__E7_P1" / "EnIm1.dcm"
    synth.write_enhanced_dicom(p)
    ds = pydicom.dcmread(str(p))
    # make it oblique and store frames in reverse stack order
    th = np.deg2rad(17)
    row = np.array([np.cos(th), np.sin(th), 0.0]); col = np.array([-np.sin(th) * 0.2, np.cos(th) * 0.2, np.sqrt(1 - 0.04)])
    col /= np.linalg.norm(col); col -= row * (row @ col); col /= np.linalg.norm(col)
    ds.SharedFunctionalGroupsSequence[0].PlaneOrientationSequence[0].ImageOrientationPatient = list(np.r_[row, col])
    n = np.cross(row, col)
    for k, f in enumerate(ds.PerFrameFunctionalGroupsSequence):
        f.PlanePositionSequence[0].ImagePositionPatient = list(np.array([1.0, 2.0, 3.0]) + k * 0.5 * n)
    ds.save_as(str(p))
    img = read_enhanced_dicom(p)
    A = _old_affine_lps(ds)
    for idx in [(0, 0, 0), (5, 7, 3), (39, 43, 15)]:
        assert np.allclose(img.TransformIndexToPhysicalPoint(idx), (A @ np.r_[idx, 1])[:3], atol=1e-4)
    arr = sitk.GetArrayFromImage(img)
    assert np.array_equal(arr, ds.pixel_array.astype(np.float32))  # same frame order as the old script
    sc = find_scans([tmp_path])
    assert len(sc) == 1 and sc[0].kind == "dicom-enhanced" and sc[0].name == "PA_05_R2M"


def test_folder_fallback_and_series_choice(tmp_path):
    from graftseg.scans import find_scans
    d = tmp_path / "Pasca_FebMar26_Pasca_03-13-2026__E7_P1"
    synth.write_enhanced_dicom(d / "EnIm1.dcm", desc="T2_Anatomy__garbled")
    synth.write_enhanced_dicom(d / "Loc.dcm", desc="Localizer_TriPilot_PA05_R2M")
    sc = find_scans([tmp_path])
    assert len(sc) == 1 and sc[0].path.name == "EnIm1.dcm"
    assert sc[0].name_source == "folder-fallback" and sc[0].name == "Pasca_FebMar26_Pasca_03-13-2026_E7_P1"


def test_stop_file_stops_a_running_job(tmp_path, monkeypatch):
    """The app's Stop button works on every OS by creating GRAFTSEG_STOP_FILE; the job must end within seconds."""
    import threading
    import time
    from graftseg import train as TR
    stop = tmp_path / "stop"
    monkeypatch.setenv("GRAFTSEG_STOP_FILE", str(stop))
    cmd = [sys.executable, "-c", "import time\nfor i in range(600):\n    print(i, flush=True); time.sleep(0.1)"]
    threading.Timer(1.0, stop.touch).start()
    t0 = time.time()
    with pytest.raises(KeyboardInterrupt):
        TR._run(cmd, dict(__import__("os").environ), None, log=lambda s: None)
    assert time.time() - t0 < 10


def test_nnunet_entry_points_resolve():
    import importlib
    from graftseg.train import _ENTRY
    for mod, fn in _ENTRY.values():
        assert callable(getattr(importlib.import_module(mod), fn))
