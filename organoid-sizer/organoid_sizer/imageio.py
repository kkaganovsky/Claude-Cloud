"""Image loading and physical pixel-size discovery.

Every reader returns ``(rgb, gray, info)``:

* ``rgb``  - uint8 H x W x 3 array used for display,
* ``gray`` - float32 H x W array (0..1) used for segmentation,
* ``info`` - dict with ``calibration`` (see :func:`calibration_dict`), ``notes`` and basic properties.

Pixel-size sources, in the order they are tried:

1. Microscope metadata that states a physical pixel size: OME-XML (OME-TIFF), ImageJ TIFF, Zeiss LSM,
   MetaMorph STK / MetaSeries, Olympus FluoView, Zeiss CZI (needs ``czifile`` or ``bioio``),
   Nikon ND2 (needs ``nd2``), Leica LIF (needs ``readlif``), anything else ``bioio`` / ``aicsimageio`` can read.
   These are marked ``trusted`` and applied automatically.
2. A resolution (DPI) tag in TIFF / PNG / JPEG. Flatbed scanners write a real DPI here, but screenshots, figures and many
   cameras write a placeholder (72, 96, 144, 300 ...). It is therefore offered as a *suggestion* and only applied
   when the user accepts it (or sets ``trust_dpi``).
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image

PLAIN_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
TIFF_EXT = {".tif", ".tiff", ".btf", ".lsm", ".stk"}
VENDOR_EXT = {".czi", ".nd2", ".lif", ".lei", ".oib", ".oif", ".vsi", ".ims", ".dv", ".zvi", ".scn", ".svs", ".ndpi"}
SUPPORTED_EXT = PLAIN_EXT | TIFF_EXT | VENDOR_EXT

_UNIT_TO_UM = {
    "m": 1e6, "meter": 1e6, "metre": 1e6,
    "cm": 1e4, "centimeter": 1e4, "centimetre": 1e4,
    "mm": 1e3, "millimeter": 1e3, "millimetre": 1e3,
    "um": 1.0, "µm": 1.0, "μm": 1.0, "micron": 1.0, "microns": 1.0, "micrometer": 1.0, "micrometre": 1.0,
    "\\u00b5m": 1.0, "u": 1.0,
    "nm": 1e-3, "nanometer": 1e-3, "nanometre": 1e-3,
    "in": 25400.0, "inch": 25400.0, "inches": 25400.0,
}


def unit_to_um(unit: str | None) -> float | None:
    if unit is None:
        return None
    u = str(unit).strip().lower().replace("µ", "u").replace("μ", "u")
    if u in ("um", "u", "micron", "microns", "micrometer", "micrometre"):
        return 1.0
    return _UNIT_TO_UM.get(u)


def calibration_dict(um_per_px=None, source="none", detail="", trusted=False):
    return {"um_per_px": (float(um_per_px) if um_per_px else None), "source": source, "detail": detail,
            "trusted": bool(trusted)}


def is_supported(path: str | os.PathLike) -> bool:
    p = Path(path)
    return p.suffix.lower() in SUPPORTED_EXT and not p.name.startswith("._")


# ----------------------------------------------------------------------------------------------- pixel conversions
def to_display(arr: np.ndarray) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Reduce any image array to (rgb uint8, gray float32 0..1). Multi-plane data: first plane is used."""
    notes: list[str] = []
    a = np.asarray(arr)
    a = np.squeeze(a)
    # colour last axis (RGB / RGBA)
    if a.ndim == 3 and a.shape[-1] in (3, 4) and a.shape[0] > 4:
        a = a[..., :3]
    elif a.ndim == 3 and a.shape[0] in (2, 3, 4) and a.shape[-1] > 4:
        # channel-first: show channel 0 as grey (brightfield is usually channel 0)
        notes.append(f"{a.shape[0]} channels found; channel 1 used.")
        a = a[0]
    while a.ndim > 3 or (a.ndim == 3 and a.shape[-1] not in (3,)):
        notes.append(f"Multi-plane data {a.shape}; first plane used.")
        a = a[0]
    a = a.astype(np.float32)
    lo, hi = np.percentile(a, 0.1), np.percentile(a, 99.9)
    if hi <= lo:
        lo, hi = float(a.min()), float(a.max()) if a.max() > a.min() else float(a.min()) + 1
    if arr.dtype == np.uint8:
        lo, hi = 0.0, 255.0  # keep 8-bit images exactly as they are
    norm = np.clip((a - lo) / (hi - lo), 0, 1)
    if norm.ndim == 2:
        gray = norm
        rgb = np.repeat((norm * 255).astype(np.uint8)[..., None], 3, axis=2)
    else:
        rgb = (norm * 255).astype(np.uint8)
        gray = (0.299 * norm[..., 0] + 0.587 * norm[..., 1] + 0.114 * norm[..., 2]).astype(np.float32)
    return rgb, gray.astype(np.float32), notes


# ----------------------------------------------------------------------------------------------- metadata parsers
def parse_ome_xml(xml: str) -> dict | None:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    for el in root.iter():
        if el.tag.endswith("Pixels") and "PhysicalSizeX" in el.attrib:
            x = float(el.attrib["PhysicalSizeX"])
            unit = el.attrib.get("PhysicalSizeXUnit", "µm")  # OME default unit is micrometre
            f = unit_to_um(unit)
            if f and x > 0:
                y = el.attrib.get("PhysicalSizeY")
                det = f"OME PhysicalSizeX = {x} {unit}"
                if y and abs(float(y) - x) / x > 0.01:
                    det += f" (Y = {y}; non-square pixels, X used)"
                return calibration_dict(x * f, "metadata: OME-XML", det, trusted=True)
    return None


def _dpi_suggestion(xres, unit_code, where: str) -> dict | None:
    """unit_code: 2 = inch, 3 = cm (TIFF ResolutionUnit)."""
    try:
        if isinstance(xres, tuple):
            xres = xres[0] / xres[1]
        xres = float(xres)
    except Exception:
        return None
    if not xres or xres <= 1:
        return None
    per = 25400.0 if unit_code in (2, None) else 10000.0
    um = per / xres
    unit_name = "inch" if unit_code in (2, None) else "cm"
    return calibration_dict(um, f"resolution tag ({where})",
                            f"{xres:g} px per {unit_name} = {um:.4g} µm/px. Real for flatbed scanners; often a "
                            f"placeholder for cameras, screenshots and figures - verify with a ruler.", trusted=False)


def _tiff_calibration(tf) -> tuple[dict | None, dict | None]:
    """Return (trusted calibration, dpi suggestion) from an open tifffile.TiffFile."""
    trusted = None
    if getattr(tf, "ome_metadata", None):
        trusted = parse_ome_xml(tf.ome_metadata)
    page = tf.pages[0]
    tags = page.tags
    if trusted is None and getattr(tf, "lsm_metadata", None):
        vx = tf.lsm_metadata.get("VoxelSizeX")
        if vx:
            trusted = calibration_dict(vx * 1e6, "metadata: Zeiss LSM", f"VoxelSizeX = {vx} m", True)
    if trusted is None and getattr(tf, "stk_metadata", None):
        st = tf.stk_metadata
        xc = st.get("XCalibration")
        if xc:
            trusted = calibration_dict(xc, "metadata: MetaMorph STK", f"XCalibration = {xc} (assumed µm)", True)
    if trusted is None and getattr(tf, "metaseries_metadata", None):
        try:
            ps = tf.metaseries_metadata["PlaneInfo"]["spatial-calibration-x"]
            unit = tf.metaseries_metadata["PlaneInfo"].get("spatial-calibration-units", "um")
            f = unit_to_um(unit) or 1.0
            trusted = calibration_dict(ps * f, "metadata: MetaSeries", f"spatial-calibration-x = {ps} {unit}", True)
        except Exception:
            pass
    if trusted is None and getattr(tf, "fluoview_metadata", None):
        try:
            dims = tf.fluoview_metadata["Dimensions"]
            for name, size, res, unit in dims:
                if str(name).upper().startswith("X") and res:
                    f = unit_to_um(unit) or 1.0
                    trusted = calibration_dict(res * f, "metadata: Olympus FluoView", f"X resolution {res} {unit}",
                                               True)
                    break
        except Exception:
            pass
    ij = getattr(tf, "imagej_metadata", None)
    xr = tags.get("XResolution")
    ru = tags.get("ResolutionUnit")
    if trusted is None and ij and xr is not None:
        unit = ij.get("unit")
        f = unit_to_um(unit)
        if f:
            v = xr.value
            ppu = v[0] / v[1] if isinstance(v, tuple) else float(v)
            if ppu > 0:
                trusted = calibration_dict(f / ppu, "metadata: ImageJ TIFF",
                                           f"{ppu:g} px per {unit} (ImageJ calibration)", True)
    # description written by some instruments: "PhysicalSizeX=..." or "pixel size: 1.23 um"
    if trusted is None:
        desc = page.description or ""
        m = re.search(r"(?:pixel\s*size|PixelSize|um/px|scale)\s*[:=]\s*([0-9.]+)\s*(µm|um|nm|mm)?", desc, re.I)
        if m:
            f = unit_to_um(m.group(2) or "um")
            trusted = calibration_dict(float(m.group(1)) * f, "metadata: TIFF description",
                                       f"'{m.group(0)}' in ImageDescription", True)
    dpi = None
    if xr is not None and trusted is None:
        code = int(ru.value) if ru is not None else 2
        if code in (2, 3):
            dpi = _dpi_suggestion(xr.value, code, "TIFF")
    return trusted, dpi


def _read_tiff(path: Path):
    import tifffile
    with tifffile.TiffFile(str(path)) as tf:
        trusted, dpi = _tiff_calibration(tf)
        series = tf.series[0]
        arr = series.asarray()
        axes = getattr(series, "axes", "")
    rgb, gray, notes = _tiff_axes_to_display(arr, axes)
    return rgb, gray, trusted, dpi, notes


def _tiff_axes_to_display(arr, axes: str):
    notes = []
    if axes and arr.ndim > 2:
        a = arr
        ax = axes
        keep = {"Y", "X", "S"}
        # take index 0 (or middle for Z) for every axis that is not Y/X/S
        idx = []
        for c, n in zip(ax, a.shape):
            if c in keep:
                idx.append(slice(None))
            else:
                k = n // 2 if c == "Z" else 0
                if n > 1:
                    notes.append(f"axis {c} has {n} planes; plane {k + 1} used.")
                idx.append(k)
        a = a[tuple(idx)]
        rgb, gray, n2 = to_display(a)
        return rgb, gray, notes + n2
    return to_display(arr)


def _read_plain(path: Path):
    im = Image.open(str(path))
    dpi = None
    d = im.info.get("dpi")
    if d and d[0] and float(d[0]) > 1:
        dpi = _dpi_suggestion(float(d[0]), 2, path.suffix.lower().lstrip(".").upper())
    if im.mode in ("I;16", "I;16B", "I", "F"):
        arr = np.asarray(im)
    else:
        arr = np.asarray(im.convert("RGB"))
    rgb, gray, notes = to_display(arr)
    return rgb, gray, None, dpi, notes


def _read_vendor(path: Path):
    """CZI / ND2 / LIF and other vendor formats through optional readers."""
    ext = path.suffix.lower()
    errors = []
    if ext == ".nd2":
        try:
            import nd2
            with nd2.ND2File(str(path)) as f:
                arr = f.asarray()
                vs = f.voxel_size()
                axes = "".join(f.sizes.keys())
            rgb, gray, notes = _tiff_axes_to_display(arr, axes.replace("C", "C"))
            cal = calibration_dict(vs.x, "metadata: Nikon ND2", f"voxel_size x = {vs.x} µm", True) if vs.x else None
            return rgb, gray, cal, None, notes
        except ImportError:
            errors.append("nd2 package not installed (pip install nd2)")
    if ext == ".lif":
        try:
            from readlif.reader import LifFile
            lif = LifFile(str(path))
            img = lif.get_image(0)
            arr = np.asarray(img.get_frame(z=0, t=0, c=0))
            scale = img.scale[0]  # px per µm
            rgb, gray, notes = to_display(arr)
            notes.append(f"LIF: first image '{img.name}', z=0 t=0 c=0 used.")
            cal = calibration_dict(1.0 / scale, "metadata: Leica LIF", f"{scale:g} px/µm", True) if scale else None
            return rgb, gray, cal, None, notes
        except ImportError:
            errors.append("readlif not installed (pip install readlif)")
    if ext == ".czi":
        try:
            import czifile
            with czifile.CziFile(str(path)) as cz:
                arr = cz.asarray()
                axes = cz.axes
                cal = None
                md = cz.metadata()
                m = re.search(r'<Distance Id="X">\s*<Value>([0-9.eE+-]+)</Value>', md)
                if m:
                    cal = calibration_dict(float(m.group(1)) * 1e6, "metadata: Zeiss CZI",
                                           f"Scaling X = {m.group(1)} m", True)
            arr = np.squeeze(arr)
            rgb, gray, notes = _tiff_axes_to_display(arr, axes.replace("0", "")[-arr.ndim:] if arr.ndim > 2 else "")
            return rgb, gray, cal, None, notes
        except ImportError:
            errors.append("czifile not installed (pip install czifile)")
    for modname in ("bioio", "aicsimageio"):
        try:
            mod = __import__(modname)
            Cls = getattr(mod, "BioImage", None) or getattr(mod, "AICSImage")
            img = Cls(str(path))
            arr = img.get_image_data("YX", T=0, C=0, Z=img.dims.Z // 2) if hasattr(img, "dims") else img.data
            px = img.physical_pixel_sizes.X
            rgb, gray, notes = to_display(arr)
            notes.append(f"Read with {modname}; T=0, C=0, middle Z used.")
            cal = calibration_dict(px, f"metadata: {modname}", f"physical_pixel_sizes.X = {px} µm", True) if px else None
            return rgb, gray, cal, None, notes
        except ImportError:
            errors.append(f"{modname} not installed")
        except Exception as e:  # reader present but failed
            errors.append(f"{modname}: {e}")
    raise RuntimeError(f"Cannot read {path.name}: " + "; ".join(errors))


def load_image(path: str | os.PathLike, trust_dpi: bool = False):
    p = Path(path)
    ext = p.suffix.lower()
    if ext in TIFF_EXT:
        try:
            rgb, gray, trusted, dpi, notes = _read_tiff(p)
        except ImportError:
            rgb, gray, trusted, dpi, notes = _read_plain(p)
    elif ext in VENDOR_EXT:
        rgb, gray, trusted, dpi, notes = _read_vendor(p)
    else:
        rgb, gray, trusted, dpi, notes = _read_plain(p)
    cal = trusted or (dpi if (trust_dpi and dpi) else calibration_dict())
    info = {
        "width": int(rgb.shape[1]), "height": int(rgb.shape[0]),
        "calibration": cal,
        "metadata_calibration": trusted,
        "dpi_suggestion": dpi,
        "notes": notes,
    }
    return rgb, gray, info
