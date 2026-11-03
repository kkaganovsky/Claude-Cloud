"""A project = a folder of images + an output folder. All work is saved automatically to
``<output>/.organoid_sizer/`` so a session can be closed and resumed."""
from __future__ import annotations

import csv
import io
import json
import threading
import zlib
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import __version__, edit, measure, segment
from .imageio import calibration_dict, is_supported, load_image

HISTORY = 40
PRESETS_FILE = Path.home() / ".organoid_sizer" / "presets.json"


class ImageRecord:
    def __init__(self, project: "Project", path: Path, key: str):
        self.project = project
        self.path = path
        self.key = key  # unique, file-name safe
        self.lock = threading.RLock()
        self._rgb = None
        self._gray = None
        self.info: dict | None = None
        self.labels: np.ndarray | None = None
        self.meta: dict[int, dict] = {}
        self.calibration = calibration_dict()
        self.roi: dict | None = None
        self.reviewed = False
        self.log: list[str] = []
        self.settings_used: dict | None = None
        self.undo: list = []
        self.redo: list = []
        self._objs_cache = None
        self._load_saved()

    # ------------------------------------------------------------------ loading / saving
    @property
    def state_dir(self) -> Path:
        return self.project.state_dir

    def _load_saved(self):
        j = self.state_dir / f"{self.key}.json"
        if j.exists():
            try:
                d = json.loads(j.read_text())
                self.meta = {int(k): v for k, v in d.get("meta", {}).items()}
                self.calibration = d.get("calibration") or calibration_dict()
                self.roi = d.get("roi")
                self.reviewed = d.get("reviewed", False)
                self.log = d.get("log", [])
                self.settings_used = d.get("settings_used")
                self.info = d.get("info")
            except Exception:
                pass
        z = self.state_dir / f"{self.key}_labels.npz"
        if z.exists():
            try:
                self.labels = np.load(z)["labels"].astype(np.int32)
            except Exception:
                self.labels = None

    def save(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        d = {"file": str(self.path), "meta": {str(k): v for k, v in self.meta.items()},
             "calibration": self.calibration, "roi": self.roi, "reviewed": self.reviewed, "log": self.log[-50:],
             "settings_used": self.settings_used, "info": self.info, "saved": datetime.now().isoformat()}
        (self.state_dir / f"{self.key}.json").write_text(json.dumps(d, indent=1))
        if self.labels is not None:
            np.savez_compressed(self.state_dir / f"{self.key}_labels.npz", labels=self.labels.astype(np.uint16)
                                if self.labels.max() < 65535 else self.labels)

    def ensure_loaded(self):
        with self.lock:
            if self._rgb is None:
                rgb, gray, info = load_image(self.path, trust_dpi=self.project.trust_dpi)
                self._rgb, self._gray = rgb, gray
                first_time = self.info is None or "metadata_calibration" not in (self.info or {})
                self.info = info
                if first_time and not self.calibration.get("um_per_px"):
                    self.calibration = info["calibration"]
                    if not self.calibration.get("um_per_px") and self.project.default_calibration:
                        self.calibration = dict(self.project.default_calibration)
                if self.labels is not None and self.labels.shape != gray.shape:
                    self.labels = None
                    self.meta = {}
        return self

    @property
    def rgb(self):
        return self.ensure_loaded()._rgb

    @property
    def gray(self):
        return self.ensure_loaded()._gray

    def unload(self):
        self._rgb = self._gray = None

    # ------------------------------------------------------------------ history
    def _snapshot(self):
        lab = self.labels
        packed = None if lab is None else (zlib.compress(lab.astype(np.int32).tobytes(), 1), lab.shape)
        return packed, json.dumps({str(k): v for k, v in self.meta.items()})

    def _restore(self, snap):
        packed, meta = snap
        if packed is None:
            self.labels = None
        else:
            buf, shape = packed
            self.labels = np.frombuffer(zlib.decompress(buf), np.int32).reshape(shape).copy()
        self.meta = {int(k): v for k, v in json.loads(meta).items()}
        self._objs_cache = None

    def push_history(self):
        self.undo.append(self._snapshot())
        if len(self.undo) > HISTORY:
            self.undo.pop(0)
        self.redo.clear()

    def do_undo(self):
        with self.lock:
            if not self.undo:
                return False
            self.redo.append(self._snapshot())
            self._restore(self.undo.pop())
            self.save()
            return True

    def do_redo(self):
        with self.lock:
            if not self.redo:
                return False
            self.undo.append(self._snapshot())
            self._restore(self.redo.pop())
            self.save()
            return True

    # ------------------------------------------------------------------ operations
    def um_per_px(self):
        return self.calibration.get("um_per_px") if self.calibration else None

    def run_segmentation(self, settings: dict):
        with self.lock:
            self.ensure_loaded()
            self.push_history()
            lab, excluded, log = segment.segment(self.gray, settings, self.um_per_px(), self.roi)
            src = "cellpose" if settings.get("method", "cellpose") == "cellpose" else "threshold"
            self.labels = lab
            self.meta = {i: {"status": "included", "reason": "", "source": src} for i in range(1, int(lab.max()) + 1)}
            for i, r in excluded.items():
                self.meta[i] = {"status": "excluded", "reason": r, "source": src}
            self.log = [f"{datetime.now():%Y-%m-%d %H:%M} detection"] + log
            self.settings_used = settings
            self.reviewed = False
            self._objs_cache = None
            self.save()
            return log

    def apply_edit(self, op: str, p: dict):
        with self.lock:
            self.ensure_loaded()
            if self.labels is None:
                self.labels = np.zeros(self.gray.shape, np.int32)
            self.push_history()
            lab = self.labels
            created, source = [], "manual"
            try:
                if op == "delete":
                    lab = edit.delete(lab, p["ids"])
                elif op == "clear":
                    lab = np.zeros_like(lab)
                elif op in ("exclude", "include"):
                    for i in p["ids"]:
                        m = self.meta.setdefault(int(i), {"source": "manual"})
                        m["status"] = "excluded" if op == "exclude" else "included"
                        m["reason"] = p.get("reason", "") if op == "exclude" else ""
                elif op == "polygon":
                    lab, created = edit.add_polygon(lab, p["points"], p.get("overwrite", True))
                elif op == "ellipse":
                    pts = edit.ellipse_points(p["cx"], p["cy"], p["rx"], p["ry"], p.get("angle", 0))
                    lab, created = edit.add_polygon(lab, pts, p.get("overwrite", True))
                elif op == "brush":
                    lab, created = edit.brush(lab, p["points"], p["radius"], p.get("target"), erase=False)
                elif op == "erase":
                    lab, created = edit.brush(lab, p["points"], p["radius"], p.get("target"), erase=True)
                elif op == "merge":
                    lab, created = edit.merge(lab, p["ids"])
                    source = "merged"
                elif op == "split_line":
                    lab, created = edit.split_line(lab, p["points"])
                    source = "split"
                elif op == "split_auto":
                    lab, created = edit.split_auto(lab, p["id"], p.get("n", 2))
                    created = created + [int(p["id"])]
                    source = "split"
                else:
                    raise ValueError(f"unknown operation {op}")
            except Exception:
                self.undo.pop()
                raise
            self.labels = lab
            present = set(int(v) for v in np.unique(lab) if v)
            self.meta = {k: v for k, v in self.meta.items() if k in present}
            for i in present:
                self.meta.setdefault(i, {"status": "included", "reason": "", "source": source})
            for i in created:
                if i in present:
                    st = self.meta.get(i, {})
                    self.meta[i] = {"status": st.get("status", "included") if op == "merge" else "included",
                                    "reason": "", "source": source}
            self._objs_cache = None
            self.save()
            return created

    def objects(self):
        with self.lock:
            if self.labels is None:
                return []
            if self._objs_cache is None:
                self._objs_cache = measure.measure_all(self.labels, self.gray, self.um_per_px(), self.meta)
            return self._objs_cache

    def set_calibration(self, cal: dict):
        with self.lock:
            self.calibration = cal
            self._objs_cache = None
            self.save()

    def summary(self) -> dict:
        objs = self.objects() if self.labels is not None else []
        inc = [o for o in objs if o["status"] == "included"]
        d = {"key": self.key, "name": self.path.name, "n_objects": len(objs), "n_included": len(inc),
             "n_excluded": len(objs) - len(inc), "segmented": self.labels is not None, "reviewed": self.reviewed,
             "calibration": self.calibration, "loaded": self._rgb is not None}
        return d

    # ------------------------------------------------------------------ export
    def csv_rows(self):
        objs = self.objects()
        return [measure.csv_row(o, self.path.name, self.calibration) for o in objs]

    def overlay_png(self) -> bytes:
        img = cv2.cvtColor(self.rgb, cv2.COLOR_RGB2BGR).copy()
        objs = self.objects()
        h, w = img.shape[:2]
        th = max(1, int(round(max(h, w) / 900)))
        fs = max(0.35, max(h, w) / 2200)
        for o in objs:
            c = np.asarray(o["contour"], np.int32).reshape(-1, 1, 2)
            col = (80, 220, 60) if o["status"] == "included" else (60, 60, 230)
            cv2.polylines(img, [c], True, col, th, cv2.LINE_AA)
            txt = str(o["number"]) + ("x" if o["status"] != "included" else "")
            org = (int(o["cx"]) - int(6 * fs * len(txt)), int(o["cy"]) + int(6 * fs))
            cv2.putText(img, txt, org, cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0), th + 2, cv2.LINE_AA)
            cv2.putText(img, txt, org, cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), th, cv2.LINE_AA)
        ok, buf = cv2.imencode(".png", img)
        return buf.tobytes()


class Project:
    def __init__(self, input_dir: str | Path | None = None, output_dir: str | Path | None = None):
        self.input_dir: Path | None = None
        self.output_dir: Path | None = None
        self.images: list[ImageRecord] = []
        self.trust_dpi = False
        self.default_calibration: dict | None = None
        self.settings = dict(segment.DEFAULTS)
        self.lock = threading.RLock()
        self.job = {"running": False, "done": 0, "total": 0, "message": "", "errors": []}
        if input_dir:
            self.open(input_dir, output_dir)

    @property
    def state_dir(self) -> Path:
        return (self.output_dir or Path.cwd()) / ".organoid_sizer"

    def open(self, input_dir, output_dir=None, recursive=False):
        d = Path(input_dir).expanduser().resolve()
        if not d.is_dir():
            raise ValueError(f"Not a folder: {d}")
        with self.lock:
            self.input_dir = d
            self.output_dir = Path(output_dir).expanduser().resolve() if output_dir else d / "organoid_results"
            self.output_dir.mkdir(parents=True, exist_ok=True)
            pj = self.state_dir / "project.json"
            if pj.exists():
                try:
                    pd = json.loads(pj.read_text())
                    self.settings.update(pd.get("settings", {}))
                    self.default_calibration = pd.get("default_calibration")
                    self.trust_dpi = pd.get("trust_dpi", False)
                except Exception:
                    pass
            files = sorted((p for p in (d.rglob("*") if recursive else d.iterdir())
                            if p.is_file() and is_supported(p) and self.output_dir not in p.parents),
                           key=lambda p: _natural(str(p.relative_to(d))))
            self.images = []
            used = set()
            for f in files:
                self.add_file(f, used)
        return self

    def add_file(self, f: Path, used: set | None = None):
        used = used if used is not None else {r.key for r in self.images}
        base = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in f.stem)
        key, k = base, 2
        while key in used:
            key, k = f"{base}_{k}", k + 1
        used.add(key)
        rec = ImageRecord(self, f, key)
        self.images.append(rec)
        return rec

    def save_project(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "project.json").write_text(json.dumps({
            "input_dir": str(self.input_dir), "settings": self.settings,
            "default_calibration": self.default_calibration, "trust_dpi": self.trust_dpi}, indent=1))

    def get(self, key: str) -> ImageRecord:
        for r in self.images:
            if r.key == key:
                return r
        raise KeyError(key)

    # ------------------------------------------------------------------ batch
    def segment_all(self, keys, settings):
        def work():
            self.job = {"running": True, "done": 0, "total": len(keys), "message": "", "errors": []}
            for k in keys:
                r = self.get(k)
                self.job["message"] = f"Detecting organoids in {r.path.name}"
                try:
                    r.run_segmentation(settings)
                except Exception as e:  # keep going with the other images
                    self.job["errors"].append(f"{r.path.name}: {e}")
                self.job["done"] += 1
                if len(self.images) > 30:
                    r.unload()
            self.job["running"] = False
            self.job["message"] = "Finished"
        threading.Thread(target=work, daemon=True).start()

    def export(self, keys=None, overlays=True, masks=True) -> dict:
        out = self.output_dir
        out.mkdir(parents=True, exist_ok=True)
        recs = [self.get(k) for k in keys] if keys else self.images
        cols = [c for c, _ in measure.CSV_COLUMNS]
        written, all_rows, per_image = [], [], []
        for r in recs:
            if r.labels is None:
                continue
            rows = r.csv_rows()
            p = out / f"{r.key}_organoids.csv"
            _write_csv(p, cols, rows)
            written.append(p.name)
            all_rows += rows
            per_image.append(_image_summary(r))
            if overlays:
                (out / f"{r.key}_overlay.png").write_bytes(r.overlay_png())
            if masks:
                _save_mask(out / f"{r.key}_labels.tif", r)
        if all_rows:
            _write_csv(out / "all_organoids.csv", cols, all_rows)
            _write_csv(out / "summary_per_image.csv", list(per_image[0].keys()), per_image)
            written += ["all_organoids.csv", "summary_per_image.csv"]
            (out / "export_info.json").write_text(json.dumps({
                "software": f"organoid-sizer {__version__}", "exported": datetime.now().isoformat(),
                "images": {r.path.name: {"calibration": r.calibration, "detection_settings": r.settings_used,
                                         "log": r.log} for r in recs if r.labels is not None}}, indent=1))
        return {"folder": str(out), "files": written}


def _image_summary(r: ImageRecord) -> dict:
    objs = [o for o in r.objects() if o["status"] == "included"]
    u = r.um_per_px()
    d = np.array([o["eq_diam_px"] for o in objs]) * (u or 1)
    a = np.array([o["area_px"] for o in objs]) * ((u * u / 1e6) if u else 1)
    unit_d, unit_a = ("um", "mm2") if u else ("px", "px")

    def f(x):
        return f"{x:.6g}" if x == x else ""
    return {"image": r.path.name, "n_included": len(objs), "n_excluded": len(r.objects()) - len(objs),
            f"mean_eq_diameter_{unit_d}": f(d.mean()) if len(d) else "",
            f"sd_eq_diameter_{unit_d}": f(d.std(ddof=1)) if len(d) > 1 else "",
            f"median_eq_diameter_{unit_d}": f(float(np.median(d))) if len(d) else "",
            f"mean_area_{unit_a}": f(a.mean()) if len(a) else "",
            f"total_area_{unit_a}": f(a.sum()) if len(a) else "",
            "um_per_px": f"{u:.6g}" if u else "", "calibration_source": r.calibration.get("source", "none"),
            "reviewed": "yes" if r.reviewed else "no"}


def _write_csv(path: Path, cols, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _save_mask(path: Path, r: ImageRecord):
    """Label image numbered like the CSV (organoid_number), 16-bit; opens in ImageJ/Fiji."""
    lab = r.labels
    lut = np.zeros(int(lab.max()) + 1, np.uint16)
    for o in r.objects():
        lut[o["label"]] = o["number"]
    out = lut[lab]
    try:
        import tifffile
        kw = {}
        u = r.um_per_px()
        if u:
            kw = dict(imagej=True, resolution=(1 / u, 1 / u), metadata={"unit": "um"})
        tifffile.imwrite(str(path), out, **kw)
    except ImportError:
        Image.fromarray(out).save(str(path.with_suffix(".png")))


def _natural(s: str):
    import re
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def load_presets() -> list:
    try:
        return json.loads(PRESETS_FILE.read_text())
    except Exception:
        return []


def save_presets(presets: list):
    PRESETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PRESETS_FILE.write_text(json.dumps(presets, indent=1))


def thumbnail_jpeg(rgb: np.ndarray, size=220) -> bytes:
    im = Image.fromarray(rgb)
    im.thumbnail((size, size))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=80)
    return b.getvalue()


def display_png(rgb: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_PNG_COMPRESSION, 1])
    return buf.tobytes()


