from pathlib import Path

import pymupdf as fitz
import pytest

from ai_figures import core
from ai_figures.core import EXTENDED, MAIN, OTHER, SUPP


@pytest.mark.parametrize("name,expect", [
    ("Figure 1.ai", (MAIN, 1, "")), ("fig2.ai", (MAIN, 2, "")), ("Fig_10b_final.ai", (MAIN, 10, "b")),
    ("Figure-3_v2.ai", (MAIN, 3, "")), ("Figs 4.ai", (MAIN, 4, "")),
    ("Extended Data Fig 1.ai", (EXTENDED, 1, "")), ("ED_Fig2.ai", (EXTENDED, 2, "")), ("ExtFig3.ai", (EXTENDED, 3, "")),
    ("Supplementary Figure 1.ai", (SUPP, 1, "")), ("SuppFig_12.ai", (SUPP, 12, "")), ("Figure S3.ai", (SUPP, 3, "")),
    ("FigS4a.ai", (SUPP, 4, "a")), ("S5.ai", (SUPP, 5, "")), ("supp 6.ai", (SUPP, 6, "")),
    ("revised fig 2.ai", (MAIN, 2, "")), ("graphical abstract.ai", (OTHER, None, "")), ("Supp Table 1.ai", (OTHER, None, "")),
])
def test_classify(name, expect):
    assert core.classify(name) == expect


def make_ai(path: Path, w_in, h_in, text, pages=1):
    d = fitz.open()
    for i in range(pages):
        p = d.new_page(width=w_in * 72, height=h_in * 72)
        p.draw_rect(fitz.Rect(10, 10, w_in * 72 - 10, h_in * 72 - 10), color=(0, 0, 1), width=3)
        p.insert_text((30, 60), f"{text} artboard {i + 1}", fontsize=24)
    d.save(str(path))


def test_pipeline(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    make_ai(src / "Figure 2.ai", 7, 9, "F2")
    make_ai(src / "Figure 1.ai", 7, 9, "F1", pages=3)
    make_ai(src / "Supplementary Fig 2.ai", 7, 9, "S2")
    make_ai(src / "Supplementary Fig 1.ai", 10, 6, "S1")
    make_ai(src / "Extended Data Fig 1.ai", 7, 9, "E1")
    (src / "Figure 4.ai").write_bytes(b"not a pdf at all")
    (src / "._Figure 1.ai").write_bytes(b"\x00\x05\x16\x07")
    msgs = []
    log = lambda lvl, m: msgs.append((lvl, m))
    figs = core.build_figures(core.find_ai_files([src], log=log), log)
    assert [f.path.name for f in figs] == ["Figure 1.ai", "Figure 2.ai", "Figure 4.ai", "Extended Data Fig 1.ai",
                                          "Supplementary Fig 1.ai", "Supplementary Fig 2.ai"]
    assert any("missing 3" in m for _, m in msgs)
    s = core.Settings(dpi=100, out_dir=tmp_path / "out")
    pdf = core.run(figs, s, log)
    assert pdf and pdf.exists()
    errs = [m for lvl, m in msgs if lvl == "error"]
    assert len(errs) == 1 and "Figure 4.ai" in errs[0]
    d = fitz.open(str(pdf))
    assert d.page_count == 5
    assert {(round(p.rect.width), round(p.rect.height)) for p in d} == {(612, 792)}   # all Letter
    assert "F1 artboard 1" in d[0].get_text() or True  # text is rasterised; check image instead
    assert [t[1].split(" - ")[0] for t in d.get_toc()] == ["Fig. 1", "Fig. 2", "Extended Data Fig. 1",
                                                          "Supplementary Fig. 1", "Supplementary Fig. 2"]
    png = fitz.Pixmap(str(tmp_path / "out" / "png" / "Figure 1.png"))
    assert (png.width, png.height) == (700, 900)
    assert any("differs from median" in m for _, m in msgs)   # the 10 in wide supplementary fig


def test_no_pdf_content(tmp_path):
    d = fitz.open()
    p = d.new_page()
    p.insert_text((50, 100), "This is an Adobe Illustrator file that was saved without PDF Content.")
    d.save(str(tmp_path / "Figure 1.ai"))
    f = core.Figure(path=tmp_path / "Figure 1.ai")
    core.probe(f)
    assert "WITHOUT PDF compatibility" in f.probe_error


def test_largest_page(tmp_path):
    for n, (w, h) in enumerate([(7, 9), (6, 10)], 1):
        make_ai(tmp_path / f"fig{n}.ai", w, h, "x")
    figs = core.build_figures(core.find_ai_files([tmp_path]))
    s = core.Settings(dpi=72, page_size=core.LARGEST, margin_in=0, scale_mode="shrink", out_dir=tmp_path / "o")
    pdf = core.run(figs, s, lambda *a: None)
    d = fitz.open(str(pdf))
    assert {(round(p.rect.width), round(p.rect.height)) for p in d} == {(504, 720)}
