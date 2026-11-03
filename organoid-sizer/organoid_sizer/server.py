"""Local web server (Python standard library only) that serves the browser interface and a small JSON API.
Nothing leaves the computer: the server listens on 127.0.0.1 unless --host is given."""
from __future__ import annotations

import json
import mimetypes
import os
import string
import sys
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, calibration, project, segment
from .imageio import SUPPORTED_EXT, calibration_dict

STATIC = Path(__file__).parent / "static"


class App:
    def __init__(self, proj: project.Project):
        self.proj = proj

    # ---------------------------------------------------------------- helpers
    def rec(self, key):
        return self.proj.get(key)

    def image_payload(self, r):
        r.ensure_loaded()
        objs = r.objects()
        return {"key": r.key, "name": r.path.name, "path": str(r.path), "width": r.info["width"],
                "height": r.info["height"], "calibration": r.calibration, "info": r.info, "roi": r.roi,
                "reviewed": r.reviewed, "log": r.log, "objects": objs, "can_undo": bool(r.undo),
                "can_redo": bool(r.redo), "segmented": r.labels is not None}

    def state(self):
        p = self.proj
        return {"version": __version__, "input_dir": str(p.input_dir) if p.input_dir else None,
                "output_dir": str(p.output_dir) if p.output_dir else None,
                "images": [r.summary() for r in p.images], "settings": p.settings,
                "default_calibration": p.default_calibration, "trust_dpi": p.trust_dpi,
                "cellpose": segment.cellpose_info(), "job": p.job, "presets": project.load_presets(),
                "extensions": sorted(SUPPORTED_EXT)}

    # ---------------------------------------------------------------- API
    def get(self, path, q):
        p = self.proj
        if path == "/api/state":
            return self.state()
        if path == "/api/job":
            return p.job
        if path == "/api/browse":
            return browse(q.get("path", [""])[0])
        parts = path.strip("/").split("/")
        if len(parts) >= 4 and parts[:2] == ["api", "image"]:
            r = self.rec(urllib.parse.unquote(parts[2]))
            what = parts[3]
            if what == "data":
                return self.image_payload(r)
            if what == "display.png":
                return ("image/png", project.display_png(r.rgb))
            if what == "thumb.jpg":
                data = project.thumbnail_jpeg(r.rgb)
                if len(p.images) > 30:
                    r.unload()
                return ("image/jpeg", data)
            if what == "overlay.png":
                return ("image/png", r.overlay_png())
            if what == "objects.csv":
                import csv
                import io
                b = io.StringIO()
                cols = [c for c, _ in project.measure.CSV_COLUMNS]
                w = csv.DictWriter(b, fieldnames=cols)
                w.writeheader()
                w.writerows(r.csv_rows())
                return ("text/csv; charset=utf-8", b.getvalue().encode(), f"{r.key}_organoids.csv")
        raise KeyError(path)

    def post(self, path, body, raw=None, q=None):
        p = self.proj
        if path == "/api/open":
            p.open(body["input_dir"], body.get("output_dir") or None, body.get("recursive", False))
            p.save_project()
            return self.state()
        if path == "/api/upload":
            # raw file bytes; ?name=
            name = os.path.basename(q.get("name", ["upload.png"])[0])
            if p.input_dir is None:
                d = Path.home() / "organoid_sizer_uploads"
                d.mkdir(exist_ok=True)
                p.open(d)
            dest = p.input_dir / name
            if not dest.exists():
                dest.write_bytes(raw)
                p.add_file(dest)
            return self.state()
        if path == "/api/settings":
            p.settings.update(body)
            p.save_project()
            return {"settings": p.settings}
        if path == "/api/segment_all":
            settings = {**p.settings, **body.get("settings", {})}
            p.settings = settings
            p.save_project()
            keys = body.get("keys") or [r.key for r in p.images]
            if body.get("only_new"):
                keys = [k for k in keys if p.get(k).labels is None]
            p.segment_all(keys, settings)
            return p.job
        if path == "/api/export":
            return p.export(body.get("keys"), body.get("overlays", True), body.get("masks", True))
        if path == "/api/presets":
            project.save_presets(body["presets"])
            return {"presets": body["presets"]}
        if path == "/api/default_calibration":
            cal = body.get("calibration")
            p.default_calibration = cal
            p.trust_dpi = body.get("trust_dpi", p.trust_dpi)
            n = 0
            for r in p.images:
                mode = body.get("apply", "uncalibrated")  # "uncalibrated" | "all" | "none"
                if mode == "all" or (mode == "uncalibrated" and not (r.calibration or {}).get("um_per_px")):
                    if cal:
                        r.set_calibration(dict(cal))
                        n += 1
            p.save_project()
            return {"applied_to": n, **self.state()}
        if path == "/api/reveal":
            return {"ok": reveal(p.output_dir)}

        parts = path.strip("/").split("/")
        if len(parts) >= 4 and parts[:2] == ["api", "image"]:
            r = self.rec(urllib.parse.unquote(parts[2]))
            what = parts[3]
            if what == "segment":
                settings = {**p.settings, **body.get("settings", {})}
                p.settings = settings
                p.save_project()
                r.run_segmentation(settings)
            elif what == "edit":
                r.apply_edit(body["op"], body)
            elif what == "undo":
                r.do_undo()
            elif what == "redo":
                r.do_redo()
            elif what == "reviewed":
                r.reviewed = bool(body.get("reviewed", True))
                r.save()
            elif what == "roi":
                r.roi = body.get("roi")
                r.save()
            elif what == "calibration":
                r.set_calibration(make_calibration(r, body))
            elif what == "detect_scalebar":
                return {"candidates": calibration.detect_scale_bars(r.gray)}
            elif what == "ruler_period":
                return calibration.ruler_period(r.gray, body["p0"], body["p1"])
            else:
                raise KeyError(path)
            return self.image_payload(r)
        raise KeyError(path)


def make_calibration(r, b: dict) -> dict:
    m = b.get("method")
    if m == "pixel_size":
        return calibration.from_pixel_size(b["value"], b["unit"])
    if m == "line":
        return calibration.from_line(b["p0"], b["p1"], b["length"], b["unit"], b.get("what", "line on known object"))
    if m == "period":
        return calibration.from_period(b["period_px"], b["spacing"], b["unit"], b.get("n_ticks"))
    if m == "metadata":
        return r.info.get("metadata_calibration") or calibration_dict()
    if m == "dpi":
        c = dict(r.info.get("dpi_suggestion") or calibration_dict())
        c["trusted"] = True
        return c
    if m == "preset":
        return dict(b["calibration"])
    if m == "none":
        return calibration_dict()
    raise ValueError(f"unknown calibration method {m}")


def browse(path: str):
    """List folders (and count images) so the user can pick a folder from the browser."""
    if not path:
        path = str(Path.home())
    p = Path(path).expanduser()
    if os.name == "nt" and path in ("", "/", "\\"):
        drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
        return {"path": "", "parent": None, "dirs": [{"name": d, "path": d} for d in drives], "n_images": 0}
    p = p.resolve()
    dirs, n_img, imgs = [], 0, []
    try:
        for c in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            if c.name.startswith("."):
                continue
            if c.is_dir():
                dirs.append({"name": c.name, "path": str(c)})
            elif c.suffix.lower() in SUPPORTED_EXT:
                n_img += 1
                if len(imgs) < 8:
                    imgs.append(c.name)
    except PermissionError:
        pass
    parent = str(p.parent) if p.parent != p else ("" if os.name == "nt" else None)
    return {"path": str(p), "parent": parent, "dirs": dirs, "n_images": n_img, "examples": imgs,
            "home": str(Path.home())}


def reveal(folder):
    if not folder:
        return False
    import subprocess
    try:
        if sys.platform.startswith("darwin"):
            subprocess.Popen(["open", str(folder)])
        elif os.name == "nt":
            os.startfile(str(folder))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(folder)])
        return True
    except Exception:
        return False


def make_handler(app: App):
    class H(BaseHTTPRequestHandler):
        server_version = "organoid-sizer"

        def log_message(self, fmt, *args):  # quiet
            if os.environ.get("ORGANOID_SIZER_DEBUG"):
                sys.stderr.write(fmt % args + "\n")

        def _send(self, code, ctype, data: bytes, filename=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj, code=200):
            self._send(code, "application/json", json.dumps(obj, default=_jsonable).encode())

        def _handle(self, fn):
            try:
                res = fn()
                if isinstance(res, tuple):
                    self._send(200, res[0], res[1], res[2] if len(res) > 2 else None)
                else:
                    self._json(res)
            except KeyError as e:
                self._json({"error": f"Not found: {e}"}, 404)
            except Exception as e:
                if os.environ.get("ORGANOID_SIZER_DEBUG"):
                    traceback.print_exc()
                self._json({"error": str(e), "trace": traceback.format_exc(limit=3)}, 400)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            if not u.path.startswith("/api/"):
                rel = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
                f = (STATIC / rel).resolve()
                if STATIC.resolve() not in f.parents or not f.is_file():
                    return self._send(404, "text/plain", b"not found")
                ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
                return self._send(200, ctype, f.read_bytes())
            self._handle(lambda: app.get(u.path, q))

        def do_POST(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            if u.path == "/api/upload":
                return self._handle(lambda: app.post(u.path, {}, raw, q))
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._json({"error": "bad JSON"}, 400)
            self._handle(lambda: app.post(u.path, body, None, q))

    return H


def _jsonable(o):
    import numpy as np
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    raise TypeError(type(o))


def serve(proj: project.Project, host="127.0.0.1", port=8765, open_browser=True):
    app = App(proj)
    httpd = None
    for p in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer((host, p), make_handler(app))
            port = p
            break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit(f"No free port between {port} and {port + 19}")
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}/"
    print(f"Organoid Sizer {__version__} running at {url}  (Ctrl+C to stop)")
    if open_browser:
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return httpd
