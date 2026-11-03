"""Find labeled scans, decide which segment is the graft, and build an nnU-Net training dataset.

Layout expected (any depth, irrelevant files are ignored):

    <source root>/.../<animal folder>/<MRI>.nrrd            the image (any .nrrd that is not .seg.nrrd)
    <source root>/.../<animal folder>/<name>.seg.nrrd       the Slicer segmentation

The animal is parsed from the folder names (deepest first), the timepoint (2mo, 4mo...) likewise.
Every decision is written to ``manifest.csv`` / ``label_audit.csv`` inside the built dataset, so later anyone can
see which source file each training case came from and why a file was skipped.
"""
from __future__ import annotations

import csv
import hashlib
import json
import random
import shutil
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import nrrd
import SimpleITK as sitk

from . import labels as L
from .naming import animal_from_path, parse_animal, safe_name, timepoint_from_path
from .slicer_seg import TOOL_FIELD, is_seg_file, masks_on_reference, read_seg

SKIP_DIRS = {"__MACOSX", ".git", ".Trash"}


@dataclass
class Case:
    seg_path: str
    image_path: str = ""
    animal: str = ""
    animal_key: str = ""
    timepoint: str = ""
    case_id: str = ""
    graft_segments: list = field(default_factory=list)
    fluid_segments: list = field(default_factory=list)
    ignored_segments: list = field(default_factory=list)
    unknown_segments: list = field(default_factory=list)
    status: str = "ok"
    reason: str = ""
    graft_voxels: int = 0
    graft_mm3: float = 0.0
    fold: int = -1
    source: str = "slicer"


def _iter_files(root: Path):
    for p in sorted(Path(root).rglob("*")):
        if any(part in SKIP_DIRS or part.startswith("._") for part in p.parts):
            continue
        if p.is_file():
            yield p


def _bbox_lps(header: dict) -> tuple[np.ndarray, np.ndarray]:
    from .slicer_seg import _space_sign, _spatial_directions
    sign = _space_sign(header)
    dirs = _spatial_directions(header) * sign[None, :]
    origin = np.asarray(header.get("space origin", [0, 0, 0]), float) * sign
    sizes = np.asarray(header["sizes"][-3:])
    corners = [origin + (np.array([i, j, k]) * (sizes - 1)) @ dirs for i in (0, 1) for j in (0, 1) for k in (0, 1)]
    c = np.array(corners)
    return c.min(0), c.max(0)


def _pick_image(seg: Path, seg_header: dict, candidates: list[Path]) -> tuple[Optional[Path], str]:
    if not candidates:
        return None, "no MRI .nrrd next to the segmentation"
    if len(candidates) == 1:
        return candidates[0], ""
    stem = seg.name[: -len(".seg.nrrd")].lower()
    same = [c for c in candidates if c.name[: -len(".nrrd")].lower() == stem]
    if len(same) == 1:
        return same[0], ""
    lo, hi = _bbox_lps(seg_header)
    center = (lo + hi) / 2
    scored = []
    for c in candidates:
        try:
            h = nrrd.read_header(str(c))
            if int(h.get("dimension", 3)) != 3:
                continue
            clo, chi = _bbox_lps(h)
            inside = bool(np.all(center >= clo - 1e-3) and np.all(center <= chi + 1e-3))
            scored.append((inside, int(np.prod(h["sizes"])), c))
        except Exception:
            continue
    if not scored:
        return None, "none of the .nrrd files next to it is a 3D image"
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    note = f"{len(candidates)} images in folder; chose {scored[0][2].name} (contains the segmentation, largest)"
    return scored[0][2], note


def scan(roots: Iterable[Path], with_fluid: bool = False, include_unreviewed: bool = False) -> list[Case]:
    """Find every .seg.nrrd under ``roots`` and decide how to use it. Reads headers only (fast)."""
    cases: list[Case] = []
    for root in roots:
        root = Path(root)
        files = list(_iter_files(root))
        by_dir: dict[Path, list[Path]] = defaultdict(list)
        for f in files:
            if f.name.lower().endswith(".nrrd"):
                by_dir[f.parent].append(f)
        for d, fs in sorted(by_dir.items()):
            segs = [f for f in fs if is_seg_file(f)]
            images = [f for f in fs if not is_seg_file(f)]
            for s in segs:
                c = Case(seg_path=str(s))
                a = animal_from_path(s.parent, root.parent) or parse_animal(s.name)
                c.animal = a.name if a else safe_name(s.parent.name)
                c.animal_key = a.key if a else c.animal.lower()
                c.timepoint = timepoint_from_path(s, root.parent) or ""
                try:
                    h = nrrd.read_header(str(s))
                except Exception as e:  # noqa: BLE001
                    c.status, c.reason = "skip", f"unreadable segmentation: {e}"
                    cases.append(c)
                    continue
                if h.get(TOOL_FIELD) == "predicted" and not include_unreviewed:
                    c.status, c.reason = "skip", "model prediction that was never reviewed"
                    cases.append(c)
                    continue
                img, note = _pick_image(s, h, images)
                if img is None:
                    c.status, c.reason = "skip", note
                    cases.append(c)
                    continue
                c.image_path, c.reason = str(img), note
                names = []
                k = 0
                while f"Segment{k}_Name" in h or f"Segment{k}_ID" in h:
                    names.append(str(h.get(f"Segment{k}_Name", f"Segment_{k + 1}")))
                    k += 1
                for pos, name in enumerate(names):
                    cls = L.classify_segment(name, pos, a)
                    if cls == L.FLUID and not with_fluid:
                        cls = L.IGNORE
                    {L.GRAFT: c.graft_segments, L.FLUID: c.fluid_segments, L.IGNORE: c.ignored_segments,
                     L.UNKNOWN: c.unknown_segments}[cls].append(name)
                if not names:
                    c.status, c.reason = "skip", "segmentation has no Slicer segment names"
                elif not c.graft_segments:
                    c.status, c.reason = "skip", "no segment recognised as graft"
                cases.append(c)
    _dedupe(cases)
    _assign_case_ids(cases)
    return cases


def _file_hash(p: str) -> str:
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _dedupe(cases: list[Case]) -> None:
    """Same MRI labeled twice (e.g. copied folders, two saved segmentations): keep the newest segmentation."""
    groups: dict[str, list[Case]] = defaultdict(list)
    for c in cases:
        if c.status == "ok":
            groups[_file_hash(c.image_path)].append(c)
    for group in groups.values():
        if len(group) > 1:
            group.sort(key=lambda c: Path(c.seg_path).stat().st_mtime, reverse=True)
            for c in group[1:]:
                c.status, c.reason = "skip", f"duplicate of {group[0].seg_path} (same MRI file; newer segmentation kept)"


def _assign_case_ids(cases: list[Case]) -> None:
    used: dict[str, int] = defaultdict(int)
    for c in cases:
        if c.status != "ok":
            continue
        base = safe_name(f"{c.animal}_{c.timepoint}" if c.timepoint else c.animal)
        used[base] += 1
        c.case_id = base if used[base] == 1 else f"{base}_scan{used[base]}"


# ----------------------------------------------------------------------------------------------------------------
def make_splits(cases: list[Case], n_folds: int = 5, seed: int = 12345) -> list[dict]:
    """Animal-level folds: every scan of an animal (all timepoints) lands in the same fold."""
    groups: dict[str, list[Case]] = defaultdict(list)
    for c in cases:
        groups[c.animal_key].append(c)
    keys = sorted(groups)
    random.Random(seed).shuffle(keys)
    keys.sort(key=lambda k: len(groups[k]), reverse=True)  # big groups first, then fill the emptiest fold
    load = [0] * n_folds
    for k in keys:
        f = min(range(n_folds), key=lambda i: (load[i], i))
        for c in groups[k]:
            c.fold = f
        load[f] += len(groups[k])
    ids = [c.case_id for c in cases]
    splits = []
    for f in range(n_folds):
        val = sorted(c.case_id for c in cases if c.fold == f)
        splits.append({"train": sorted(i for i in ids if i not in set(val)), "val": val})
    return splits


def _import_nnunet_dataset(src: Path, with_fluid: bool) -> tuple[list[Case], dict[int, int]]:
    """Existing nnU-Net dataset (e.g. Dataset001_GraftSeg) -> cases + label value remap."""
    dj = json.loads((src / "dataset.json").read_text(encoding="utf-8"))
    remap = {0: 0}
    for name, value in dj.get("labels", {}).items():
        if isinstance(value, list):
            continue
        v = int(value)
        if v == 0:
            continue
        cls = L.classify_segment(name, 0 if v == 1 else v, None)
        remap[v] = 1 if cls == L.GRAFT else (2 if (cls == L.FLUID and with_fluid) else 0)
    ending = dj.get("file_ending", ".nii.gz")
    cases = []
    for lab in sorted((src / "labelsTr").glob(f"*{ending}")):
        cid = lab.name[: -len(ending)]
        img = src / "imagesTr" / f"{cid}_0000{ending}"
        a = parse_animal(cid)
        c = Case(seg_path=str(lab), image_path=str(img), source=f"nnunet:{src.name}",
                 animal=a.name if a else cid, animal_key=a.key if a else cid.lower(),
                 timepoint=timepoint_from_path(Path(cid)) or "", case_id=safe_name(cid))
        if not img.exists():
            c.status, c.reason = "skip", "image missing"
        cases.append(c)
    return cases, remap


def build(roots: list[Path], out_raw: Path, dataset_id: int, name: str = "GraftSeg",
          with_fluid: bool = False, n_folds: int = 5, import_nnunet: Optional[list[Path]] = None,
          overwrite: bool = False, dry_run: bool = False, log=print) -> Path:
    ds_dir = Path(out_raw) / f"Dataset{dataset_id:03d}_{name}"
    existing = [p for p in Path(out_raw).glob(f"Dataset{dataset_id:03d}_*")] if Path(out_raw).exists() else []
    if existing and not overwrite and not dry_run:
        raise FileExistsError(f"{existing[0]} already exists. Pick another --id or pass --overwrite.")

    cases = scan(roots, with_fluid=with_fluid) if roots else []
    remaps: dict[str, dict[int, int]] = {}
    for src in import_nnunet or []:
        imported, remap = _import_nnunet_dataset(Path(src), with_fluid)
        remaps[f"nnunet:{Path(src).name}"] = remap
        taken = {c.case_id for c in cases if c.status == "ok"}
        for c in imported:
            if c.status == "ok" and c.case_id in taken:
                c.case_id = f"{c.case_id}_{Path(src).name.split('_')[0].lower()}"
            taken.add(c.case_id)
        cases.extend(imported)

    ok = [c for c in cases if c.status == "ok"]
    log(f"Found {len(cases)} segmentation(s): {len(ok)} usable, {len(cases) - len(ok)} skipped.")
    for c in cases:
        if c.status != "ok":
            log(f"  [skip] {c.seg_path}: {c.reason}")
    if dry_run:
        _summarise(ok, log)
        return ds_dir

    if existing and overwrite:
        for e in existing:
            shutil.rmtree(e)
    (ds_dir / "imagesTr").mkdir(parents=True)
    (ds_dir / "labelsTr").mkdir(parents=True)

    for i, c in enumerate(ok, 1):
        try:
            if c.source == "slicer":
                img = sitk.ReadImage(c.image_path)
                if img.GetDimension() != 3 or img.GetNumberOfComponentsPerPixel() != 1:
                    raise ValueError("MRI is not a single-channel 3D image")
                seg = read_seg(Path(c.seg_path))
                by_name = {s.name: s for s in seg.segments}
                lab = np.zeros(sitk.GetArrayViewFromImage(img).shape, np.uint8)
                if c.fluid_segments:
                    lab[masks_on_reference(seg, [by_name[n] for n in c.fluid_segments if n in by_name], img)] = 2
                lab[masks_on_reference(seg, [by_name[n] for n in c.graft_segments if n in by_name], img)] = 1
                spacing = img.GetSpacing()
            else:
                img = sitk.ReadImage(c.image_path)
                lab_img = sitk.ReadImage(c.seg_path)
                arr = sitk.GetArrayFromImage(lab_img)
                remap = remaps[c.source]
                lab = np.zeros(arr.shape, np.uint8)
                for v, nv in remap.items():
                    lab[arr == v] = nv
                spacing = img.GetSpacing()
            c.graft_voxels = int((lab == 1).sum())
            c.graft_mm3 = round(c.graft_voxels * float(np.prod(spacing)), 3)
            if c.graft_voxels == 0:
                c.status, c.reason = "skip", "graft segment is empty on the MRI grid"
                log(f"  [skip] {c.case_id}: {c.reason}")
                continue
            sitk.WriteImage(img, str(ds_dir / "imagesTr" / f"{c.case_id}_0000.nii.gz"), useCompression=True)
            lab_img = sitk.GetImageFromArray(lab)
            lab_img.CopyInformation(img)
            sitk.WriteImage(lab_img, str(ds_dir / "labelsTr" / f"{c.case_id}.nii.gz"), useCompression=True)
            log(f"  [{i}/{len(ok)}] {c.case_id}: graft {c.graft_mm3} mm3")
        except Exception as e:  # noqa: BLE001
            c.status, c.reason = "skip", f"failed to convert: {e}"
            log(f"  [skip] {c.case_id}: {c.reason}")

    ok = [c for c in cases if c.status == "ok"]
    labels = {"background": 0, "graft": 1}
    if with_fluid:
        labels["fluid"] = 2
    (ds_dir / "dataset.json").write_text(json.dumps({
        "channel_names": {"0": "T2"}, "labels": labels, "numTraining": len(ok), "file_ending": ".nii.gz",
        "name": name, "description": "Organoid graft segmentation, T2w rat MRI. Built by graftseg.",
    }, indent=2))
    splits = make_splits(ok, n_folds)
    (ds_dir / "splits_final.json").write_text(json.dumps(splits, indent=2))
    _write_csv(ds_dir / "manifest.csv", cases)
    (ds_dir / "build_info.json").write_text(json.dumps({
        "built": datetime.now().isoformat(timespec="seconds"), "sources": [str(r) for r in roots],
        "imported_nnunet": [str(p) for p in import_nnunet or []], "with_fluid": with_fluid,
        "n_folds": n_folds, "n_cases": len(ok), "label_rules": "graftseg.labels (see docs/DATA_DECISIONS.md)",
    }, indent=2))
    _summarise(ok, log)
    log(f"Dataset written to {ds_dir}")
    return ds_dir


def _summarise(ok: list[Case], log) -> None:
    animals = {c.animal_key for c in ok}
    tps = defaultdict(int)
    for c in ok:
        tps[c.timepoint or "?"] += 1
    unknown = sorted({n for c in ok for n in c.unknown_segments})
    log(f"{len(ok)} cases from {len(animals)} animals; timepoints: " +
        ", ".join(f"{k}={v}" for k, v in sorted(tps.items())))
    if unknown:
        log("Segment names not covered by any rule (ignored): " + ", ".join(repr(u) for u in unknown))


def _write_csv(path: Path, cases: list[Case]) -> None:
    rows = [asdict(c) for c in cases]
    for r in rows:
        for k in ("graft_segments", "fluid_segments", "ignored_segments", "unknown_segments"):
            r[k] = " | ".join(r[k])
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["seg_path"])
        w.writeheader()
        w.writerows(rows)


def write_scan_report(cases: list[Case], path: Path) -> Path:
    _write_csv(Path(path), cases)
    return Path(path)
