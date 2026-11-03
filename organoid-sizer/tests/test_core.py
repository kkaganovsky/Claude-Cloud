import csv
import math

import numpy as np
import pytest
import tifffile
from PIL import Image

from organoid_sizer import calibration, edit, imageio, measure, segment
from organoid_sizer.project import Project


def disk(shape, cx, cy, r, val=1):
    yy, xx = np.mgrid[:shape[0], :shape[1]]
    return ((xx - cx) ** 2 + (yy - cy) ** 2 <= r * r) * val


def synthetic(shape=(300, 400)):
    """light background, dark organoids (brightfield-like)."""
    img = np.full(shape, 0.8, np.float32)
    img -= 0.5 * disk(shape, 100, 150, 40)
    img -= 0.5 * disk(shape, 260, 120, 25)
    # two fused spheres
    img -= 0.5 * np.clip(disk(shape, 300, 230, 22) + disk(shape, 338, 230, 22), 0, 1)
    rng = np.random.default_rng(0)
    return np.clip(img + rng.normal(0, 0.01, shape), 0, 1).astype(np.float32)


# ---------------------------------------------------------------- metadata
def test_ome_tiff_pixel_size(tmp_path):
    p = tmp_path / "a.ome.tif"
    tifffile.imwrite(p, (synthetic() * 65535).astype(np.uint16), ome=True,
                     metadata={"PhysicalSizeX": 0.65, "PhysicalSizeXUnit": "µm", "PhysicalSizeY": 0.65})
    _, _, info = imageio.load_image(p)
    assert info["calibration"]["trusted"]
    assert info["calibration"]["um_per_px"] == pytest.approx(0.65)
    assert "OME" in info["calibration"]["source"]


def test_ome_unit_mm(tmp_path):
    p = tmp_path / "b.ome.tif"
    tifffile.imwrite(p, np.zeros((20, 20), np.uint8), ome=True,
                     metadata={"PhysicalSizeX": 0.002, "PhysicalSizeXUnit": "mm"})
    _, _, info = imageio.load_image(p)
    assert info["calibration"]["um_per_px"] == pytest.approx(2.0)


def test_imagej_tiff_pixel_size(tmp_path):
    p = tmp_path / "ij.tif"
    tifffile.imwrite(p, np.zeros((50, 60), np.uint8), imagej=True, resolution=(1 / 3.2, 1 / 3.2),
                     metadata={"unit": "um"})
    _, _, info = imageio.load_image(p)
    assert info["calibration"]["um_per_px"] == pytest.approx(3.2, rel=1e-4)
    assert "ImageJ" in info["calibration"]["source"]


def test_dpi_is_only_a_suggestion(tmp_path):
    p = tmp_path / "scan.png"
    Image.fromarray(np.zeros((30, 30), np.uint8)).save(p, dpi=(1200, 1200))
    _, _, info = imageio.load_image(p)
    assert info["calibration"]["um_per_px"] is None
    assert info["dpi_suggestion"]["um_per_px"] == pytest.approx(25400 / 1200, rel=1e-3)
    _, _, info2 = imageio.load_image(p, trust_dpi=True)
    assert info2["calibration"]["um_per_px"] == pytest.approx(25400 / 1200, rel=1e-3)


def test_tiff_dpi_cm(tmp_path):
    p = tmp_path / "scan.tif"
    tifffile.imwrite(p, np.zeros((30, 30), np.uint8), resolution=(400, 400), resolutionunit=3)  # px per cm
    _, _, info = imageio.load_image(p)
    assert info["dpi_suggestion"]["um_per_px"] == pytest.approx(25.0)


def test_16bit_and_rgb_load(tmp_path):
    p = tmp_path / "rgb.jpg"
    Image.fromarray((np.random.rand(40, 50, 3) * 255).astype(np.uint8)).save(p)
    rgb, gray, info = imageio.load_image(p)
    assert rgb.shape == (40, 50, 3) and gray.shape == (40, 50) and 0 <= gray.min() <= gray.max() <= 1


# ---------------------------------------------------------------- calibration
@pytest.mark.parametrize("value,unit,expected", [
    (1.6, "um/px", 1.6), (650, "nm/px", 0.65), (2, "px/um", 0.5), (100, "px/mm", 10.0), (2400, "dpi", 25400 / 2400)])
def test_pixel_size_units(value, unit, expected):
    assert calibration.from_pixel_size(value, unit)["um_per_px"] == pytest.approx(expected)


def test_line_calibration():
    c = calibration.from_line((10, 10), (10 + 300, 10 + 400), 5, "mm")  # 500 px = 5 mm
    assert c["um_per_px"] == pytest.approx(10.0)


def test_scalebar_detection():
    g = np.full((300, 500), 0.2, np.float32)
    g[260:266, 380:480] = 1.0  # 100 px white bar
    cands = calibration.detect_scale_bars(g)
    assert cands and cands[0]["length_px"] == pytest.approx(100, abs=1)


def test_ruler_period():
    x = np.arange(800)
    g = np.tile((0.5 + 0.4 * (np.mod(x, 23.6) < 3)).astype(np.float32), (40, 1))  # ticks every 23.6 px
    r = calibration.ruler_period(g, (5, 20), (790, 20))
    assert r["period_px"] == pytest.approx(23.6, rel=0.01)


# ---------------------------------------------------------------- detection / editing / measurement
def test_threshold_segmentation_finds_organoids():
    lab, exc, log = segment.segment(synthetic(), {"method": "threshold", "split_touching": False})
    assert lab.max() == 3  # two singles + one fused pair
    objs = measure.measure_all(lab, None, 10.0, {})
    big = max(objs, key=lambda o: o["area_px"])
    assert big["eq_diam_px"] == pytest.approx(80, rel=0.06)
    assert big["eq_diam_um"] == pytest.approx(big["eq_diam_px"] * 10)


def test_fused_pair_flag_and_auto_split():
    lab, _, _ = segment.segment(synthetic(), {"method": "threshold", "split_touching": False})
    objs = measure.measure_all(lab, None, None, {})
    fused = [o for o in objs if "possibly fused / irregular" in o["flags"]]
    assert len(fused) == 1
    out, created = edit.split_auto(lab, fused[0]["label"], 2)
    assert out.max() == 4 and len(created) == 1
    parts = [o for o in measure.measure_all(out, None, None, {}) if o["cx"] > 280]
    assert len(parts) == 2
    assert parts[0]["area_px"] == pytest.approx(parts[1]["area_px"], rel=0.15)
    assert (out > 0).sum() == (lab > 0).sum()  # no area lost


def test_split_line_and_merge():
    lab = np.zeros((100, 100), np.int32)
    lab[20:80, 20:80] = 1
    out, created = edit.split_line(lab, [(50, 0), (50, 99)])
    assert out.max() == 2 and (out > 0).sum() == 3600
    merged, _ = edit.merge(out, [1, 2])
    assert set(np.unique(merged)) == {0, 1}


def test_add_delete_brush():
    lab = np.zeros((100, 100), np.int32)
    lab, ids = edit.add_polygon(lab, edit.ellipse_points(50, 50, 20, 20))
    assert ids == [1] and abs((lab == 1).sum() - math.pi * 400) < 60
    lab2, _ = edit.brush(lab, [(70, 50), (85, 50)], 4, target=1)
    assert (lab2 == 1).sum() > (lab == 1).sum()
    lab3, _ = edit.brush(lab2, [(50, 50)], 5, target=1, erase=True)
    assert (lab3 == 1).sum() < (lab2 == 1).sum()
    assert edit.delete(lab3, [1]).max() == 0


def test_size_filter_and_roi():
    lab, _, log = segment.segment(synthetic(), {"method": "threshold", "split_touching": False,
                                                "min_diameter_px": 70})
    assert lab.max() == 1
    roi = {"points": [[200, 50], [400, 50], [400, 300], [200, 300]]}
    lab2, _, _ = segment.segment(synthetic(), {"method": "threshold", "split_touching": False}, roi=roi)
    assert lab2.max() == 2


# ---------------------------------------------------------------- project / export
def test_project_export_and_undo(tmp_path):
    d = tmp_path / "imgs"
    d.mkdir()
    Image.fromarray((synthetic() * 255).astype(np.uint8)).save(d / "plate 1.png")
    Image.fromarray((synthetic() * 255).astype(np.uint8)).save(d / "plate 2.png")
    p = Project(d)
    assert [r.path.name for r in p.images] == ["plate 1.png", "plate 2.png"]
    r = p.images[0]
    r.run_segmentation({"method": "threshold", "split_touching": False})
    r.set_calibration(calibration.from_pixel_size(5, "um/px"))
    first = r.objects()[0]["label"]
    r.apply_edit("exclude", {"ids": [first], "reason": "Touching a neighbour"})
    n = len(r.objects())
    r.apply_edit("delete", {"ids": [r.objects()[1]["label"]]})
    assert len(r.objects()) == n - 1
    r.do_undo()
    assert len(r.objects()) == n
    res = p.export()
    assert "plate_1_organoids.csv" in res["files"]
    rows = list(csv.DictReader(open(p.output_dir / "plate_1_organoids.csv")))
    assert len(rows) == n
    assert {row["status"] for row in rows} == {"included", "excluded"}
    assert any(row["exclusion_reason"] == "Touching a neighbour" for row in rows)
    assert float(rows[0]["um_per_px"]) == 5
    assert rows[0]["calibration_source"] == "entered pixel size"
    assert (p.output_dir / "plate_1_overlay.png").exists()
    assert (p.output_dir / "plate_1_labels.tif").exists()
    summ = list(csv.DictReader(open(p.output_dir / "summary_per_image.csv")))
    assert summ[0]["n_excluded"] == "1"
    # resume: a new Project on the same folder sees the saved work
    p2 = Project(d)
    assert len(p2.images[0].objects()) == n


def test_threshold_auto_split_touching():
    lab, _, _ = segment.segment(synthetic(), {"method": "threshold", "split_touching": True})
    assert lab.max() == 4  # the fused pair becomes two
