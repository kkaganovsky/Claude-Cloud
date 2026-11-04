"""Command-line version of the GUI:  ai-figures-pdf-cli FOLDER [--dpi 300] [--page letter|a4|tabloid|largest] ..."""
import argparse
import sys
from pathlib import Path

from . import core

_PAGES = {"letter": "US Letter (8.5 x 11 in)", "a4": "A4 (8.27 x 11.69 in)", "tabloid": "Tabloid (11 x 17 in)",
          "largest": core.LARGEST}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("inputs", nargs="+", help="folder(s) and/or .ai files")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--page", choices=list(_PAGES), default="letter")
    ap.add_argument("--margin", type=float, default=0.5, help="inches")
    ap.add_argument("--scale", choices=["fit", "shrink"], default="fit")
    ap.add_argument("--align", choices=["top", "center"], default="top")
    ap.add_argument("--transparent", action="store_true")
    ap.add_argument("--recursive", action="store_true")
    ap.add_argument("--out", help="output folder (default: <first input>/figures_export)")
    ap.add_argument("--name", default="figures.pdf")
    a = ap.parse_args(argv)

    lines = []

    def log(lvl, msg):
        line = f"[{lvl.upper():5}] {msg}"
        lines.append(line)
        print(line, file=sys.stderr if lvl in ("warn", "error") else sys.stdout, flush=True)

    first = Path(a.inputs[0])
    out = Path(a.out) if a.out else (first if first.is_dir() else first.parent) / "figures_export"
    files = core.find_ai_files(a.inputs, a.recursive, log)
    if not files:
        log("error", "No .ai files found")
        return 2
    figs = core.build_figures(files, log)
    for i, f in enumerate(figs, 1):
        log("info", f"{i:3d}. {f.label:28s} {f.path.name}")
    s = core.Settings(dpi=a.dpi, transparent=a.transparent, page_size=_PAGES[a.page], margin_in=a.margin,
                      scale_mode=a.scale, align=a.align, out_dir=out, pdf_name=a.name)
    pdf = core.run(figs, s, log)
    core.write_log(out, lines)
    return 0 if pdf and not any(l.startswith("[ERROR") for l in lines) else 1


if __name__ == "__main__":
    sys.exit(main())
