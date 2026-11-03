"""Command line entry point.

    organoid-sizer                         # open the app, choose a folder in the browser
    organoid-sizer /path/to/images         # open with a folder
    organoid-sizer /path/to/images --batch --method cellpose --diameter-um 500
                                           # no GUI: detect + export CSVs for every image
"""
from __future__ import annotations

import argparse
import sys

from . import __version__



def main(argv=None):
    ap = argparse.ArgumentParser(prog="organoid-sizer", description="Organoid size quantification (Cellpose).")
    ap.add_argument("folder", nargs="?", help="folder with images")
    ap.add_argument("--out", help="output folder (default: <folder>/organoid_results)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to allow other computers on the network")
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    ap.add_argument("--recursive", action="store_true", help="include images in sub-folders")
    ap.add_argument("--batch", action="store_true", help="no GUI: detect organoids in every image and export")
    ap.add_argument("--method", choices=["cellpose", "threshold"], help="detection method for --batch")
    ap.add_argument("--diameter-um", type=float, help="expected organoid diameter in µm")
    ap.add_argument("--diameter-px", type=float, help="expected organoid diameter in pixels")
    ap.add_argument("--um-per-px", type=float, help="pixel size to use for images without metadata")
    ap.add_argument("--invert", action="store_true", help="invert images before detection")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args(argv)
    try:
        import cv2  # noqa: F401
    except ImportError:
        sys.exit("OpenCV is missing. Start organoid-sizer from your Cellpose environment, or run:\n"
                 "    pip install opencv-python-headless")

    from .project import Project
    proj = Project()
    if a.folder:
        proj.open(a.folder, a.out, a.recursive)
    if a.um_per_px:
        from .calibration import from_pixel_size
        proj.default_calibration = from_pixel_size(a.um_per_px, "um/px")
        for r in proj.images:
            r.ensure_loaded()
            if not r.calibration.get("um_per_px"):
                r.set_calibration(dict(proj.default_calibration))

    if a.batch:
        if not a.folder:
            ap.error("--batch needs a folder")
        s = dict(proj.settings)
        if a.method:
            s["method"] = a.method
        if a.diameter_um:
            s["diameter_um"] = a.diameter_um
        if a.diameter_px:
            s["diameter_px"] = a.diameter_px
        if a.invert:
            s["invert"] = True
        for r in proj.images:
            print(f"{r.path.name}: ", end="", flush=True)
            try:
                log = r.run_segmentation(s)
                print(log[-1])
            except Exception as e:
                print(f"FAILED: {e}")
        res = proj.export()
        print(f"Wrote {len(res['files'])} files to {res['folder']}")
        return 0

    from .server import serve
    serve(proj, a.host, a.port, not a.no_browser)
    return 0


if __name__ == "__main__":
    sys.exit(main())
